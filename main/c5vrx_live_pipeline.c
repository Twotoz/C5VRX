#include "c5vrx_live_pipeline.h"

#include <stdlib.h>
#include <string.h>

#include "c5vrx_adc_dump.h"
#include "c5vrx_wbfm_hw.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *TAG = "c5vrx_live";
#define FINITE_SOURCE_BUFFER_COUNT (C5VRX_RF_BLOCK_QUEUE_CAPACITY + 1u)

typedef struct {
    size_t words_per_block;
    uint64_t sequence;
    uint32_t *copy[FINITE_SOURCE_BUFFER_COUNT];
} finite_source_context_t;

typedef struct {
    TaskHandle_t task;
    TaskHandle_t source_task;
    volatile bool stop;
    c5vrx_live_pipeline_config_t config;
    c5vrx_stream_stats_t stats;
    c5vrx_cvbs_conditioner_t conditioner;
    uint8_t *wbfm;
    uint8_t *cvbs;
    c5vrx_rf_block_queue_t queue;
    uint8_t previous_wbfm;
    bool have_previous_wbfm;
} live_state_t;

static live_state_t s_live;

static void update_max_u64(uint64_t *maximum, uint64_t value)
{
    if (value > *maximum) *maximum = value;
}

static bool finite_acquire(c5vrx_rf_source_t *source,
                           c5vrx_rf_block_t *block,
                           uint32_t timeout_ms)
{
    (void)timeout_ms;
    finite_source_context_t *ctx = source ? source->context : NULL;
    if (!ctx || !block) return false;
    const int64_t begin = esp_timer_get_time();
    if (c5vrx_adc_dump_capture(ctx->words_per_block, false) != ESP_OK) {
        return false;
    }
    volatile const uint32_t *dump =
        (volatile const uint32_t *)(uintptr_t)C5VRX_ADC_DUMP_BASE_ADDR;
    for (size_t i = 0; i < ctx->words_per_block; ++i) {
        ctx->copy[ctx->sequence % FINITE_SOURCE_BUFFER_COUNT][i] = dump[i];
    }
    *block = (c5vrx_rf_block_t) {
        .words = ctx->copy[ctx->sequence % FINITE_SOURCE_BUFFER_COUNT],
        .word_count = ctx->words_per_block,
        .sequence = ctx->sequence++,
        .capture_time_us = (uint64_t)(esp_timer_get_time() - begin),
        .discontinuity_before = true,
        .owner = ctx,
    };
    return true;
}

static void finite_release(c5vrx_rf_source_t *source,
                           const c5vrx_rf_block_t *block)
{
    (void)source;
    (void)block;
}

esp_err_t c5vrx_finite_chain_source_create(c5vrx_rf_source_t *source,
                                           size_t words_per_block)
{
    if (!source || words_per_block < 8u ||
        words_per_block > C5VRX_ADC_DUMP_MAX_SAMPLES ||
        (words_per_block & 3u)) {
        return ESP_ERR_INVALID_ARG;
    }
    finite_source_context_t *ctx = calloc(1, sizeof(*ctx));
    if (!ctx) return ESP_ERR_NO_MEM;
    for (unsigned i = 0; i < FINITE_SOURCE_BUFFER_COUNT; ++i) {
        ctx->copy[i] = heap_caps_malloc(words_per_block * sizeof(uint32_t),
                                        MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
        if (!ctx->copy[i]) {
            while (i) free(ctx->copy[--i]);
            free(ctx);
            return ESP_ERR_NO_MEM;
        }
    }
    ctx->words_per_block = words_per_block;
    *source = (c5vrx_rf_source_t) {
        .name = "finite-vendor-dump-chain (NOT continuous RF)",
        .kind = C5VRX_RF_SOURCE_FINITE_CHAINED,
        .nominal_sample_rate_hz = 80000000u,
        .acquire = finite_acquire,
        .release = finite_release,
        .context = ctx,
    };
    return ESP_OK;
}

void c5vrx_finite_chain_source_destroy(c5vrx_rf_source_t *source)
{
    if (!source) return;
    finite_source_context_t *ctx = source->context;
    if (ctx) {
        for (unsigned i = 0; i < FINITE_SOURCE_BUFFER_COUNT; ++i)
            free(ctx->copy[i]);
        free(ctx);
    }
    memset(source, 0, sizeof(*source));
}

static void source_task(void *arg)
{
    (void)arg;
    while (!s_live.stop) {
        c5vrx_rf_block_t block = {0};
        const int64_t begin = esp_timer_get_time();
        if (!s_live.config.source->acquire(s_live.config.source, &block, 20u)) {
            ++s_live.stats.source_underruns;
            vTaskDelay(pdMS_TO_TICKS(1));
            continue;
        }
        const uint64_t duration = (uint64_t)(esp_timer_get_time() - begin);
        s_live.stats.source_time_us += duration;
        update_max_u64(&s_live.stats.source_time_max_us, duration);
        if (!c5vrx_rf_block_queue_push(&s_live.queue, &block)) {
            ++s_live.stats.dropped_rf_blocks;
            s_live.config.source->release(s_live.config.source, &block);
        }
        s_live.stats.queue_occupancy = s_live.queue.occupancy;
        s_live.stats.queue_high_water_mark = s_live.queue.high_water_mark;
    }
    s_live.source_task = NULL;
    vTaskDelete(NULL);
}

static void pipeline_task(void *arg)
{
    (void)arg;
    const int64_t start_us = esp_timer_get_time();
    ESP_LOGW(TAG, "pipeline source=%s kind=%s",
             s_live.config.source->name,
             s_live.config.source->kind == C5VRX_RF_SOURCE_CONTINUOUS
                 ? "continuous" : "FINITE/CHAINED EXPERIMENT");

    while (!s_live.stop) {
        c5vrx_rf_block_t block = {0};
        if (!c5vrx_rf_block_queue_pop(&s_live.queue, &block)) {
            vTaskDelay(pdMS_TO_TICKS(1));
            continue;
        }
        s_live.stats.queue_occupancy = s_live.queue.occupancy;
        if (block.discontinuity_before) ++s_live.stats.discontinuities;
        if (!block.words || block.word_count > s_live.config.maximum_input_words ||
            (block.word_count & 3u)) {
            ++s_live.stats.dropped_rf_blocks;
            s_live.config.source->release(s_live.config.source, &block);
            continue;
        }

        size_t written = 0;
        const int64_t wbfm_begin = esp_timer_get_time();
        esp_err_t err = c5vrx_wbfm_hw_transform(
            block.words, block.word_count, s_live.wbfm,
            s_live.config.maximum_input_words / 4u, &written);
        const uint64_t wbfm_duration =
            (uint64_t)(esp_timer_get_time() - wbfm_begin);
        s_live.stats.wbfm_time_us += wbfm_duration;
        update_max_u64(&s_live.stats.wbfm_time_max_us, wbfm_duration);
        s_live.config.source->release(s_live.config.source, &block);
        if (err != ESP_OK || written == 0u) {
            ++s_live.stats.dropped_rf_blocks;
            continue;
        }

        uint8_t wmin = 63u, wmax = 0u;
        for (size_t i = 0; i < written; ++i) {
            const uint8_t v = s_live.wbfm[i] & 0x3fu;
            if (v < wmin) wmin = v;
            if (v > wmax) wmax = v;
        }
        if (s_live.have_previous_wbfm) {
            const uint8_t first = s_live.wbfm[0] & 0x3fu;
            const uint32_t jump = s_live.previous_wbfm > first
                ? s_live.previous_wbfm - first : first - s_live.previous_wbfm;
            s_live.stats.boundary_jump_sum += jump;
            if (jump > s_live.stats.boundary_jump_max)
                s_live.stats.boundary_jump_max = jump;
        }
        s_live.previous_wbfm = s_live.wbfm[written - 1u] & 0x3fu;
        s_live.have_previous_wbfm = true;
        uint8_t cmin = 63u, cmax = 0u;
        const int64_t condition_begin = esp_timer_get_time();
        c5vrx_cvbs_condition(&s_live.conditioner, s_live.wbfm, s_live.cvbs,
                             written, &cmin, &cmax);
        const uint64_t conditioner_duration =
            (uint64_t)(esp_timer_get_time() - condition_begin);
        s_live.stats.conditioner_time_us += conditioner_duration;
        update_max_u64(&s_live.stats.conditioner_time_max_us,
                       conditioner_duration);

        const int64_t output_begin = esp_timer_get_time();
        const esp_err_t output_err = s_live.config.sink(
            s_live.cvbs, written, s_live.config.sink_context);
        const uint64_t output_duration =
            (uint64_t)(esp_timer_get_time() - output_begin);
        s_live.stats.output_time_us += output_duration;
        update_max_u64(&s_live.stats.output_time_max_us, output_duration);
        if (output_err != ESP_OK) {
            ++s_live.stats.output_underruns;
        }
        ++s_live.stats.blocks_processed;
        s_live.stats.input_samples += block.word_count;
        s_live.stats.output_samples += written;
        s_live.stats.wbfm_min = wmin;
        s_live.stats.wbfm_max = wmax;
        s_live.stats.cvbs_min = cmin;
        s_live.stats.cvbs_max = cmax;
        const uint64_t elapsed = (uint64_t)(esp_timer_get_time() - start_us);
        if (elapsed) {
            s_live.stats.achieved_input_rate_hz =
                (uint32_t)(s_live.stats.input_samples * 1000000u / elapsed);
            s_live.stats.achieved_output_rate_hz =
                (uint32_t)(s_live.stats.output_samples * 1000000u / elapsed);
        }
    }
    s_live.task = NULL;
    vTaskDelete(NULL);
}

esp_err_t c5vrx_live_pipeline_start(const c5vrx_live_pipeline_config_t *config)
{
    if (!config || !config->source || !config->source->acquire ||
        !config->source->release || !config->sink ||
        config->maximum_input_words < 8u ||
        (config->maximum_input_words & 3u) || s_live.task) {
        return ESP_ERR_INVALID_ARG;
    }
    memset(&s_live, 0, sizeof(s_live));
    s_live.config = *config;
    const size_t output_capacity = config->maximum_input_words / 4u;
    s_live.wbfm = heap_caps_malloc(output_capacity,
                                   MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
    s_live.cvbs = heap_caps_malloc(output_capacity,
                                   MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
    if (!s_live.wbfm || !s_live.cvbs) {
        free(s_live.wbfm);
        free(s_live.cvbs);
        memset(&s_live, 0, sizeof(s_live));
        return ESP_ERR_NO_MEM;
    }
    c5vrx_cvbs_conditioner_init(&s_live.conditioner, &config->conditioner);
    c5vrx_rf_block_queue_init(&s_live.queue);
    if (xTaskCreate(source_task, "c5vrx_source", 3072, NULL, 18,
                    &s_live.source_task) != pdPASS) {
        free(s_live.wbfm); free(s_live.cvbs);
        memset(&s_live, 0, sizeof(s_live));
        return ESP_ERR_NO_MEM;
    }
    if (xTaskCreate(pipeline_task, "c5vrx_live", 4096, NULL, 17,
                    &s_live.task) != pdPASS) {
        s_live.stop = true;
        while (s_live.source_task) vTaskDelay(pdMS_TO_TICKS(1));
        free(s_live.wbfm);
        free(s_live.cvbs);
        memset(&s_live, 0, sizeof(s_live));
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}

esp_err_t c5vrx_live_pipeline_stop(void)
{
    if (!s_live.task) return ESP_ERR_INVALID_STATE;
    s_live.stop = true;
    for (unsigned i = 0; i < 1000u && (s_live.task || s_live.source_task); ++i) {
        vTaskDelay(pdMS_TO_TICKS(1));
    }
    if (s_live.task || s_live.source_task) return ESP_ERR_TIMEOUT;
    free(s_live.wbfm);
    free(s_live.cvbs);
    s_live.wbfm = NULL;
    s_live.cvbs = NULL;
    return ESP_OK;
}

bool c5vrx_live_pipeline_running(void) { return s_live.task != NULL; }

void c5vrx_live_pipeline_get_stats(c5vrx_stream_stats_t *stats)
{
    if (stats) *stats = s_live.stats;
}

void c5vrx_live_pipeline_log_stats(void)
{
    c5vrx_stream_stats_t *s = &s_live.stats;
    ESP_LOGI(TAG,
             "blocks=%llu src_underrun=%llu out_underrun=%llu dropped=%llu discontinuities=%llu rate_iq=%u/s rate_cvbs=%u/s wbfm=%u..%u cvbs=%u..%u stage_us total/max source=%llu/%llu wbfm=%llu/%llu condition=%llu/%llu output=%llu/%llu boundary_jump avg/max=%llu/%u queue=%u high=%u",
             (unsigned long long)s->blocks_processed,
             (unsigned long long)s->source_underruns,
             (unsigned long long)s->output_underruns,
             (unsigned long long)s->dropped_rf_blocks,
             (unsigned long long)s->discontinuities,
             (unsigned)s->achieved_input_rate_hz,
             (unsigned)s->achieved_output_rate_hz,
             s->wbfm_min, s->wbfm_max, s->cvbs_min, s->cvbs_max,
             (unsigned long long)s->source_time_us,
             (unsigned long long)s->source_time_max_us,
             (unsigned long long)s->wbfm_time_us,
             (unsigned long long)s->wbfm_time_max_us,
             (unsigned long long)s->conditioner_time_us,
             (unsigned long long)s->conditioner_time_max_us,
             (unsigned long long)s->output_time_us,
             (unsigned long long)s->output_time_max_us,
             (unsigned long long)(s->blocks_processed > 1u
                 ? s->boundary_jump_sum / (s->blocks_processed - 1u) : 0u),
             (unsigned)s->boundary_jump_max,
             s->queue_occupancy, s->queue_high_water_mark);
}

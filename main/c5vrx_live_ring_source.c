#include "c5vrx_live_pipeline.h"

#include <stdatomic.h>
#include <stdlib.h>
#include <string.h>

#include "c5vrx_adc_dump.h"
#include "esp_cpu.h"
#include "esp_heap_caps.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "sdkconfig.h"

#define RING_WORDS C5VRX_ADC_DUMP_MAX_SAMPLES
#define RING_MASK  (RING_WORDS - 1u)
#define RING_BUFFER_COUNT (C5VRX_RF_BLOCK_QUEUE_CAPACITY + 1u)
#define RING_ABSOLUTE_MAX_HZ 320000000u

typedef struct {
    uint32_t *words;
    atomic_bool in_use;
} ring_slot_t;

typedef struct {
    size_t maximum_words;
    size_t guard_words;
    uint16_t reader;
    uint16_t last_writer;
    uint32_t available_words;
    bool reader_valid;
    bool discontinuity;
    bool fatal;
    uint32_t maximum_plausible_rate_hz;
    uint32_t last_observation_cycle;
    uint64_t sequence;
    ring_slot_t slots[RING_BUFFER_COUNT];
    c5vrx_live_ring_stats_t stats;
} ring_context_t;

static volatile const uint32_t *const s_ring =
    (volatile const uint32_t *)(uintptr_t)C5VRX_ADC_DUMP_BASE_ADDR;

static uint32_t cpu_hz(void)
{
    return (uint32_t)CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ * 1000000u;
}

static ring_slot_t *claim_slot(ring_context_t *ctx, uint32_t timeout_ms)
{
    const int64_t deadline = esp_timer_get_time() + (int64_t)timeout_ms * 1000;
    do {
        for (unsigned i = 0; i < RING_BUFFER_COUNT; ++i) {
            bool expected = false;
            if (atomic_compare_exchange_strong(&ctx->slots[i].in_use,
                                               &expected, true)) {
                return &ctx->slots[i];
            }
        }
        if (!timeout_ms) break;
        taskYIELD();
    } while (esp_timer_get_time() < deadline);
    return NULL;
}

static void resync_reader(ring_context_t *ctx, uint16_t writer)
{
    /* Half a ring maximizes bounded copy time in either direction. Alignment
     * preserves the current 4:1 transform contract. */
    ctx->reader = (uint16_t)((writer + RING_WORDS / 2u) & RING_MASK);
    ctx->reader &= (uint16_t)~7u;
    ctx->reader_valid = true;
    ctx->last_writer = writer;
    ctx->available_words = (writer - ctx->reader) & RING_MASK;
    ctx->discontinuity = true;
    ++ctx->stats.discontinuities;
}

static bool ring_acquire(c5vrx_rf_source_t *source,
                         c5vrx_rf_block_t *block,
                         uint32_t timeout_ms)
{
    ring_context_t *ctx = source ? source->context : NULL;
    if (!ctx || !block || ctx->fatal) return false;
    ring_slot_t *slot = claim_slot(ctx, timeout_ms);
    if (!slot) {
        ++ctx->stats.dropped_blocks;
        return false;
    }

    c5vrx_rf_dump_status_t before = {0};
    if (c5vrx_rf_dump_get_status(&before) != ESP_OK || !before.enabled) {
        atomic_store(&slot->in_use, false);
        ++ctx->stats.dropped_blocks;
        ctx->fatal = true;
        ++ctx->stats.fatal_stops;
        ctx->stats.fatal_reason = C5VRX_RING_FAILURE_PRODUCER_STOPPED;
        (void)c5vrx_rf_dump_stop();
        return false;
    }
    const uint32_t observation_cycle = (uint32_t)esp_cpu_get_cycle_count();
    if (ctx->reader_valid && ctx->last_observation_cycle) {
        const uint32_t interval_cycles =
            observation_cycle - ctx->last_observation_cycle;
        const uint64_t possible_interval_advance =
            (uint64_t)interval_cycles * ctx->maximum_plausible_rate_hz /
            cpu_hz();
        if (possible_interval_advance >= RING_WORDS) {
            ctx->stats.missed_words += possible_interval_advance;
            ++ctx->stats.overruns;
            ++ctx->stats.fatal_stops;
            ctx->stats.fatal_reason =
                C5VRX_RING_FAILURE_SERVICE_INTERVAL_AMBIGUOUS;
            ctx->fatal = true;
            atomic_store(&slot->in_use, false);
            (void)c5vrx_rf_dump_stop();
            return false;
        }
        const uint32_t produced =
            (before.pointer - ctx->last_writer) & RING_MASK;
        if (before.pointer < ctx->last_writer)
            ++ctx->stats.wraps_observed;
        ctx->available_words += produced;
        if (ctx->available_words >= RING_WORDS - ctx->guard_words) {
            ctx->stats.missed_words += ctx->available_words;
            ++ctx->stats.overruns;
            ++ctx->stats.fatal_stops;
            ctx->stats.fatal_reason = C5VRX_RING_FAILURE_READER_INSIDE_GUARD;
            ctx->fatal = true;
            atomic_store(&slot->in_use, false);
            (void)c5vrx_rf_dump_stop();
            return false;
        }
    }
    ctx->last_observation_cycle = observation_cycle;
    ctx->last_writer = before.pointer;
    if (!ctx->reader_valid) resync_reader(ctx, before.pointer);

    size_t count = ctx->maximum_words;
    const size_t contiguous = RING_WORDS - ctx->reader;
    if (count > contiguous) count = contiguous;
    count &= ~(size_t)3u;
    if (count < 8u) {
        ctx->reader = 0;
        count = ctx->maximum_words;
        if (count > RING_WORDS) count = RING_WORDS;
        count &= ~(size_t)3u;
        ctx->discontinuity = true;
        ++ctx->stats.discontinuities;
    }

    /* A fast consumer reaching the writer is normal backpressure, not an
     * overrun. Wait until this complete contiguous segment exists. */
    if (ctx->available_words < count) {
        atomic_store(&slot->in_use, false);
        return false;
    }

    const uint32_t first_cycle = (uint32_t)esp_cpu_get_cycle_count();
    for (size_t i = 0; i < count; ++i) slot->words[i] = s_ring[ctx->reader + i];
    __asm__ __volatile__("fence r, rw" ::: "memory");
    const uint32_t last_cycle = (uint32_t)esp_cpu_get_cycle_count();
    const uint32_t copy_cycles = last_cycle - first_cycle;
    if (copy_cycles > ctx->stats.maximum_copy_cycles)
        ctx->stats.maximum_copy_cycles = copy_cycles;

    c5vrx_rf_dump_status_t after = {0};
    if (c5vrx_rf_dump_get_status(&after) != ESP_OK || !after.enabled) {
        atomic_store(&slot->in_use, false);
        ++ctx->stats.dropped_blocks;
        ctx->fatal = true;
        ++ctx->stats.fatal_stops;
        ctx->stats.fatal_reason = C5VRX_RING_FAILURE_PRODUCER_STOPPED;
        (void)c5vrx_rf_dump_stop();
        return false;
    }
    const uint64_t possible_advance =
        (uint64_t)copy_cycles * ctx->maximum_plausible_rate_hz / cpu_hz();
    const uint32_t observed_advance =
        (after.pointer - before.pointer) & RING_MASK;
    if (possible_advance >= RING_WORDS ||
        ctx->available_words + observed_advance >=
            RING_WORDS - ctx->guard_words) {
        atomic_store(&slot->in_use, false);
        ctx->stats.missed_words += count;
        ++ctx->stats.overruns;
        ++ctx->stats.dropped_blocks;
        ++ctx->stats.fatal_stops;
        ctx->stats.fatal_reason = C5VRX_RING_FAILURE_COPY_AMBIGUOUS;
        ctx->fatal = true;
        (void)c5vrx_rf_dump_stop();
        return false;
    }

    if (after.pointer < before.pointer) ++ctx->stats.wraps_observed;
    ctx->available_words += observed_advance;
    ctx->last_writer = after.pointer;
    ctx->last_observation_cycle = last_cycle;

    const uint16_t start = ctx->reader;
    ctx->reader = (uint16_t)((ctx->reader + count) & RING_MASK);
    ctx->available_words -= count;
    ctx->stats.reader_pointer = ctx->reader;
    ctx->stats.writer_pointer = after.pointer;
    ++ctx->stats.blocks;
    ctx->stats.words += count;
    *block = (c5vrx_rf_block_t) {
        .words = slot->words,
        .word_count = count,
        .sequence = ctx->sequence++,
        .capture_time_us = (uint64_t)copy_cycles * 1000000u / cpu_hz(),
        .discontinuity_before = ctx->discontinuity,
        .owner = slot,
    };
    (void)start;
    ctx->discontinuity = false;
    return true;
}

static void ring_release(c5vrx_rf_source_t *source,
                         const c5vrx_rf_block_t *block)
{
    (void)source;
    ring_slot_t *slot = block ? block->owner : NULL;
    if (slot) atomic_store(&slot->in_use, false);
}

esp_err_t c5vrx_live_ring_source_create(c5vrx_rf_source_t *source,
                                        c5vrx_rf_dump_mode_t mode,
                                        size_t maximum_words_per_block,
                                        size_t guard_words,
                                        uint32_t maximum_plausible_rate_hz)
{
    if (!source || maximum_words_per_block < 8u ||
        maximum_words_per_block > RING_WORDS / 2u ||
        (maximum_words_per_block & 3u) || guard_words < 16u ||
        maximum_words_per_block + guard_words >= RING_WORDS / 2u ||
        !maximum_plausible_rate_hz ||
        maximum_plausible_rate_hz > RING_ABSOLUTE_MAX_HZ) {
        return ESP_ERR_INVALID_ARG;
    }
    ring_context_t *ctx = calloc(1, sizeof(*ctx));
    if (!ctx) return ESP_ERR_NO_MEM;
    ctx->maximum_words = maximum_words_per_block;
    ctx->guard_words = guard_words;
    ctx->maximum_plausible_rate_hz = maximum_plausible_rate_hz;
    ctx->stats.guard_words = (uint16_t)guard_words;
    for (unsigned i = 0; i < RING_BUFFER_COUNT; ++i) {
        ctx->slots[i].words = heap_caps_malloc(
            maximum_words_per_block * sizeof(uint32_t),
            MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
        if (!ctx->slots[i].words) {
            while (i) free(ctx->slots[--i].words);
            free(ctx);
            return ESP_ERR_NO_MEM;
        }
        atomic_init(&ctx->slots[i].in_use, false);
    }
    esp_err_t err = c5vrx_rf_dump_configure(RING_WORDS, mode);
    const bool configured = err == ESP_OK;
    if (configured) err = c5vrx_rf_dump_start();
    if (err == ESP_OK) {
        c5vrx_rf_dump_status_t status = {0};
        (void)c5vrx_rf_dump_get_status(&status);
        uint16_t previous = status.pointer;
        bool wrapped = false;
        const int64_t deadline = esp_timer_get_time() + 10000;
        while (esp_timer_get_time() < deadline && !wrapped) {
            (void)c5vrx_rf_dump_get_status(&status);
            wrapped = status.pointer < previous;
            previous = status.pointer;
        }
        if (!wrapped) err = ESP_ERR_TIMEOUT;
    }
    if (err != ESP_OK) {
        if (configured) (void)c5vrx_rf_dump_stop();
        for (unsigned i = 0; i < RING_BUFFER_COUNT; ++i)
            free(ctx->slots[i].words);
        free(ctx);
        return err;
    }
    *source = (c5vrx_rf_source_t) {
        .name = "EXPERIMENTAL_RING_SOURCE_UNPROVEN",
        .kind = C5VRX_RF_SOURCE_EXPERIMENTAL_RING_UNPROVEN,
        .nominal_sample_rate_hz = 0,
        .acquire = ring_acquire,
        .release = ring_release,
        .context = ctx,
    };
    return ESP_OK;
}

void c5vrx_live_ring_source_get_stats(const c5vrx_rf_source_t *source,
                                      c5vrx_live_ring_stats_t *stats)
{
    if (!stats) return;
    memset(stats, 0, sizeof(*stats));
    const ring_context_t *ctx = source ? source->context : NULL;
    if (ctx) *stats = ctx->stats;
}

const char *c5vrx_live_ring_failure_name(c5vrx_live_ring_failure_t failure)
{
    switch (failure) {
        case C5VRX_RING_FAILURE_PRODUCER_STOPPED: return "PRODUCER_STOPPED";
        case C5VRX_RING_FAILURE_SERVICE_INTERVAL_AMBIGUOUS:
            return "SERVICE_INTERVAL_AMBIGUOUS";
        case C5VRX_RING_FAILURE_READER_INSIDE_GUARD:
            return "READER_INSIDE_GUARD";
        case C5VRX_RING_FAILURE_COPY_AMBIGUOUS: return "COPY_AMBIGUOUS";
        default: return "NONE";
    }
}

void c5vrx_live_ring_source_destroy(c5vrx_rf_source_t *source)
{
    if (!source) return;
    ring_context_t *ctx = source->context;
    if (ctx) {
        (void)c5vrx_rf_dump_stop();
        for (unsigned i = 0; i < RING_BUFFER_COUNT; ++i)
            free(ctx->slots[i].words);
        free(ctx);
    }
    memset(source, 0, sizeof(*source));
}

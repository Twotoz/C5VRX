#include "c5vrx_adc_dump.h"

#include <inttypes.h>
#include <limits.h>
#include <stdio.h>
#include <string.h>
#include <math.h>

#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "c5vrx_rf_dump_producer.h"

static const char *TAG = "c5vrx_adc_dump";

/* Recovered from C5 v6.0.2 adctrig() disassembly. These are intentionally
 * private diagnostic constants, not presented as documented SoC registers. */
#define C5VRX_FE_DUMP_CONTROL_REG 0x600a9004u
#define C5VRX_FE_DUMP_POINTER_REG 0x600a9008u
#define C5VRX_FE_DUMP_DONE_BIT    (1u << 18)

/*
 * Prototype is independently documented by historical Espressif RF-test
 * tooling and matches the C5 v6.0.2 RISC-V argument usage, including the ninth
 * argument being loaded from the caller's stack.
 */
extern void adctrig(int32_t smp_num_aft_trig,
                    int32_t trigmode,
                    int32_t trigcase,
                    int32_t sample_80m,
                    int32_t dump_trig,
                    int32_t rx_gain_mode,
                    int32_t rx_gain,
                    int32_t rx_gain0,
                    int32_t rx_gain0_wait_us);
extern void set_dump_mode(int mode);

static int16_t sign_extend_10(uint32_t value)
{
    value &= 0x3ffu;
    return (value & 0x200u) ? (int16_t)(value - 0x400u) : (int16_t)value;
}

c5vrx_iq10_sample_t c5vrx_adc_decode_word(uint32_t raw)
{
    return (c5vrx_iq10_sample_t) {
        .i = sign_extend_10(raw >> 10),
        .q = sign_extend_10(raw),
        .raw = raw,
    };
}

static void trigger_dump(size_t sample_count)
{
    /* Historical tooling passes N-1; adctrig adds one internally. */
    adctrig((int32_t)sample_count - 1,
            0,  /* software trigger */
            0,  /* trigger case */
            1,  /* 80 MHz sample mode */
            0,  /* trigger then dump */
            0,  /* automatic RX gain */
            0,
            0,
            0);
    __asm__ __volatile__("fence iorw, iorw" ::: "memory");
}

static uint32_t fnv1a_dump_hash(volatile const uint32_t *words, size_t sample_count)
{
    uint32_t hash = 2166136261u;
    for (size_t i = 0; i < sample_count; ++i) {
        uint32_t v = words[i];
        for (unsigned b = 0; b < 4; ++b) {
            hash ^= v & 0xffu;
            hash *= 16777619u;
            v >>= 8;
        }
    }
    return hash;
}

esp_err_t c5vrx_adc_dump_capture(size_t sample_count, bool print_raw_words)
{
    if (sample_count == 0 || sample_count > C5VRX_ADC_DUMP_MAX_SAMPLES) {
        ESP_LOGE(TAG, "sample_count must be 1..%u", (unsigned)C5VRX_ADC_DUMP_MAX_SAMPLES);
        return ESP_ERR_INVALID_ARG;
    }

    ESP_LOGW(TAG,
             "EXPERIMENTAL finite IQ capture: %u samples, buffer=0x%08" PRIx32 ", historical rate argument=1 (physical rate UNKNOWN)",
             (unsigned)sample_count, (uint32_t)C5VRX_ADC_DUMP_BASE_ADDR);

    /* Mode 0 is the recovered normal packed 10-bit I/Q dump path. */
    set_dump_mode(0);
    trigger_dump(sample_count);

    volatile const uint32_t *const words =
        (volatile const uint32_t *)(uintptr_t)C5VRX_ADC_DUMP_BASE_ADDR;

    int64_t sum_i = 0;
    int64_t sum_q = 0;
    uint64_t sum_power = 0;
    int16_t min_i = INT16_MAX;
    int16_t max_i = INT16_MIN;
    int16_t min_q = INT16_MAX;
    int16_t max_q = INT16_MIN;

    if (print_raw_words) {
        printf("C5VRX_IQ_BEGIN samples=%u base=0x%08" PRIx32 "\n",
               (unsigned)sample_count, (uint32_t)C5VRX_ADC_DUMP_BASE_ADDR);
    }

    for (size_t i = 0; i < sample_count; ++i) {
        const uint32_t raw = words[i];
        const c5vrx_iq10_sample_t s = c5vrx_adc_decode_word(raw);

        sum_i += s.i;
        sum_q += s.q;
        sum_power += (uint64_t)((int32_t)s.i * s.i) + (uint64_t)((int32_t)s.q * s.q);
        if (s.i < min_i) min_i = s.i;
        if (s.i > max_i) max_i = s.i;
        if (s.q < min_q) min_q = s.q;
        if (s.q > max_q) max_q = s.q;

        if (print_raw_words) {
            printf("IQ:%08" PRIx32 "\n", raw);
        }
    }

    if (print_raw_words) {
        printf("C5VRX_IQ_END\n");
    }

    ESP_LOGI(TAG,
             "IQ summary: n=%u mean_i=%ld mean_q=%ld min/max_i=%d/%d min/max_q=%d/%d avg_power=%" PRIu64,
             (unsigned)sample_count,
             (long)(sum_i / (int64_t)sample_count),
             (long)(sum_q / (int64_t)sample_count),
             (int)min_i, (int)max_i, (int)min_q, (int)max_q,
             sum_power / sample_count);

    return ESP_OK;
}

typedef struct {
    volatile bool started;
    volatile bool finished;
    unsigned argument;
    unsigned mode;
    int64_t begin_us;
    int64_t end_us;
} vendor_probe_worker_t;

static vendor_probe_worker_t s_vendor_probe;

static void vendor_probe_task(void *arg)
{
    vendor_probe_worker_t *worker = arg;
    worker->begin_us = esp_timer_get_time();
    worker->started = true;
    adctrig((int32_t)C5VRX_ADC_DUMP_MAX_SAMPLES - 1,
            (int32_t)worker->mode, 1, (int32_t)worker->argument,
            0, 0, 0, 0, 0);
    worker->end_us = esp_timer_get_time();
    worker->finished = true;
    vTaskDelete(NULL);
}

static esp_err_t run_vendor_probe(unsigned argument, unsigned mode,
                                  c5vrx_adc_rate_probe_result_t *out)
{
    memset(out, 0, sizeof(*out));
    out->argument = argument;
    out->field = argument >> 1;
    out->pointer_in_range = true;
    memset(&s_vendor_probe, 0, sizeof(s_vendor_probe));
    s_vendor_probe.argument = argument;
    s_vendor_probe.mode = mode;
    set_dump_mode(0);
    if (xTaskCreate(vendor_probe_task, "c5vrx_rate_probe", 3072,
                    &s_vendor_probe, 4, NULL) != pdPASS)
        return ESP_ERR_NO_MEM;
    const int64_t deadline = esp_timer_get_time() + 1500000;
    while (!s_vendor_probe.started && esp_timer_get_time() < deadline) vTaskDelay(1);
    uint32_t previous = UINT32_MAX;
    uint32_t prior_word = 0;
    while (!s_vendor_probe.finished && esp_timer_get_time() < deadline) {
        const uint32_t value = *(volatile const uint32_t *)(uintptr_t)C5VRX_FE_DUMP_POINTER_REG;
        const uint32_t pointer = value & 0xffffu;
        if (pointer >= C5VRX_ADC_DUMP_MAX_SAMPLES) out->pointer_in_range = false;
        if (previous != UINT32_MAX && pointer != previous) {
            if (pointer < previous) ++out->wraps_lower_bound;
            out->pointer_delta_lower_bound +=
                (pointer - previous) & (C5VRX_ADC_DUMP_MAX_SAMPLES - 1u);
        }
        const uint32_t safe = (pointer - 128u) & (C5VRX_ADC_DUMP_MAX_SAMPLES - 1u);
        const uint32_t word = *(volatile const uint32_t *)(uintptr_t)
            (C5VRX_ADC_DUMP_BASE_ADDR + safe * 4u);
        if (out->observations && word != prior_word) ++out->content_changes;
        prior_word = word;
        previous = pointer;
        ++out->observations;
        vTaskDelay(1);
    }
    while (!s_vendor_probe.finished && esp_timer_get_time() < deadline + 500000) vTaskDelay(1);
    if (!s_vendor_probe.finished) return ESP_ERR_TIMEOUT;
    out->duration_us = (uint64_t)(s_vendor_probe.end_us - s_vendor_probe.begin_us);
    out->final_pointer_mode =
        *(volatile const uint32_t *)(uintptr_t)C5VRX_FE_DUMP_POINTER_REG;
    out->rate_field_survived_configuration =
        ((out->final_pointer_mode >> 21) & 7u) == out->field;
    if (out->duration_us)
        out->estimated_words_per_sec_lower_bound =
            out->pointer_delta_lower_bound * 1000000ull / out->duration_us;
    volatile const uint32_t *words =
        (volatile const uint32_t *)(uintptr_t)C5VRX_ADC_DUMP_BASE_ADDR;
    uint64_t power = 0;
    for (unsigned i = 0; i < 256; ++i) {
        c5vrx_iq10_sample_t s = c5vrx_adc_decode_word(words[i]);
        power += (uint64_t)((int32_t)s.i * s.i) + (uint64_t)((int32_t)s.q * s.q);
    }
    out->iq_power = power / 256u;
    return ESP_OK;
}

esp_err_t c5vrx_adc_rate_probe_all(void)
{
    for (unsigned field = 0; field < 8; ++field) {
        c5vrx_adc_rate_probe_result_t r;
        const esp_err_t err = run_vendor_probe(field * 2u, 7u, &r);
        printf("C5VRX_RATE_PROBE field=%u arg=%u duration_us=%llu pointer_delta_lower_bound=%u wraps_lower_bound=%u wrap_exact=0 estimated_words_per_sec_lower_bound=%llu estimated_complex_samples_per_sec_lower_bound=%llu content_changes=%u iq_power=%llu pointer_in_range=%u final_9008=%08x requested_field_survived=%u register_delta_ok=UNKNOWN classification=%s code=%d\n",
               field, field * 2u, (unsigned long long)r.duration_us,
               (unsigned)r.pointer_delta_lower_bound,
               (unsigned)r.wraps_lower_bound,
               (unsigned long long)r.estimated_words_per_sec_lower_bound,
               (unsigned long long)r.estimated_words_per_sec_lower_bound,
               (unsigned)r.content_changes, (unsigned long long)r.iq_power,
               r.pointer_in_range ? 1u : 0u, (unsigned)r.final_pointer_mode,
               r.rate_field_survived_configuration ? 1u : 0u,
               r.rate_field_survived_configuration
                   ? "MEASUREMENT_REQUIRED" : "OVERWRITTEN_BEFORE_ENABLE",
               (int)err);
        fflush(stdout);
        if (err != ESP_OK) return err;
    }
    return ESP_OK;
}

static uint32_t entropy_proxy(volatile const uint32_t *words, unsigned count)
{
    uint32_t changes = 0;
    for (unsigned i = 1; i < count; ++i) changes += words[i] != words[i - 1];
    return changes;
}

esp_err_t c5vrx_adc_dump_mode_probe(void)
{
    static const c5vrx_rf_dump_mode_t modes[] = {
        C5VRX_RF_DUMP_MODE_ORDINARY_RX, C5VRX_RF_DUMP_MODE_11,
        C5VRX_RF_DUMP_MODE_12,
    };
    if (!c5vrx_rf_dump_producer_available()) return ESP_ERR_NOT_SUPPORTED;
    volatile const uint32_t *words =
        (volatile const uint32_t *)(uintptr_t)C5VRX_ADC_DUMP_BASE_ADDR;
    for (unsigned n = 0; n < sizeof(modes) / sizeof(modes[0]); ++n) {
        esp_err_t err = c5vrx_rf_dump_configure(16384u, modes[n]);
        c5vrx_rf_dump_status_t before = {0}, during = {0}, after = {0};
        if (err == ESP_OK) err = c5vrx_rf_dump_get_status(&before);
        if (err == ESP_OK) err = c5vrx_rf_dump_start();
        const int64_t start = esp_timer_get_time();
        if (err == ESP_OK) {
            while (esp_timer_get_time() - start < 5000) {
                (void)c5vrx_rf_dump_get_status(&during);
            }
            err = c5vrx_rf_dump_stop();
        }
        if (err == ESP_OK) err = c5vrx_rf_dump_get_status(&after);
        uint64_t power = 0;
        for (unsigned i = 0; i < 256; ++i) {
            c5vrx_iq10_sample_t s = c5vrx_adc_decode_word(words[i]);
            power += (uint64_t)((int32_t)s.i * s.i) + (uint64_t)((int32_t)s.q * s.q);
        }
        printf("C5VRX_DUMP_MODE_PROBE mode=%u before_9008=%08x during_9008=%08x format=%08x fe_path=%08x fe_aux=%08x pointer=%u entropy_proxy=%u iq10_power=%llu iq10_format=UNPROVEN_AFTER_MODE_CHANGE stopped=%u code=%d\n",
               (unsigned)modes[n], (unsigned)before.pointer_mode,
               (unsigned)during.pointer_mode, (unsigned)during.format,
               (unsigned)during.fe_path, (unsigned)during.fe_aux,
               (unsigned)during.pointer,
               (unsigned)entropy_proxy(words, 256),
               (unsigned long long)(power / 256u),
               after.enabled ? 0u : 1u, (int)err);
        fflush(stdout);
        if (err != ESP_OK) return err;
    }
    return ESP_OK;
}

esp_err_t c5vrx_adc_phase_probe(unsigned field)
{
    if (field > 7u) return ESP_ERR_INVALID_ARG;
    c5vrx_adc_rate_probe_result_t r;
    esp_err_t err = run_vendor_probe(field * 2u, 7u, &r);
    if (err != ESP_OK) return err;
    volatile const uint32_t *words =
        (volatile const uint32_t *)(uintptr_t)C5VRX_ADC_DUMP_BASE_ADDR;
    /* Ring wrap is the fixed 16383 -> 0 boundary, not the final write pointer.
     * The vendor arm has completed before these reads, so the pair is stable. */
    const unsigned boundary = 0;
    float increments[9];
    for (int k = -4; k < 5; ++k) {
        const unsigned a = (boundary + k - 1) & 0x3fffu;
        const unsigned b = (boundary + k) & 0x3fffu;
        const c5vrx_iq10_sample_t x = c5vrx_adc_decode_word(words[a]);
        const c5vrx_iq10_sample_t y = c5vrx_adc_decode_word(words[b]);
        const float cross = (float)x.i * y.q - (float)x.q * y.i;
        const float dot = (float)x.i * y.i + (float)x.q * y.q;
        increments[k + 4] = atan2f(cross, dot);
    }
    float local = 0.0f;
    for (unsigned i = 0; i < 9; ++i) if (i != 4) local += increments[i];
    local /= 8.0f;
    printf("C5VRX_PHASE_PROBE field=%u arg=%u boundary=%u boundary_phase_jump=%g local_average_phase_increment=%g residual=%g wraps_lower_bound=%u wrap_observed=%u coherent_source_required=1 rate_field_survived=%u classification=%s\n",
           field, field * 2u, boundary, (double)increments[4], (double)local,
           (double)(increments[4] - local),
           (unsigned)r.wraps_lower_bound,
           r.wraps_lower_bound ? 1u : 0u,
           r.rate_field_survived_configuration ? 1u : 0u,
           r.rate_field_survived_configuration ? "MEASURED_NOT_INTERPRETED" :
                                                 "RATE_FIELD_OVERWRITTEN");
    fflush(stdout);
    return ESP_OK;
}

esp_err_t c5vrx_adc_dump_capture_chained(size_t block_count,
                                         size_t sample_count,
                                         c5vrx_adc_chain_stats_t *stats)
{
    if (block_count < 2 || block_count > 1024 ||
        sample_count == 0 || sample_count > C5VRX_ADC_DUMP_MAX_SAMPLES) {
        return ESP_ERR_INVALID_ARG;
    }

    c5vrx_adc_chain_stats_t local = {
        .blocks_completed = 0,
        .samples_per_block = sample_count,
        .total_samples = 0,
        .repeated_block_hashes = 0,
        .boundary_jump_power_sum = 0,
    };

    volatile const uint32_t *const words =
        (volatile const uint32_t *)(uintptr_t)C5VRX_ADC_DUMP_BASE_ADDR;

    uint32_t previous_hash = 0;
    c5vrx_iq10_sample_t previous_last = {0};
    bool have_previous = false;

    set_dump_mode(0);
    ESP_LOGW(TAG,
             "EXPERIMENTAL chained finite capture: blocks=%u samples/block=%u; this measures re-trigger continuity, not true streaming",
             (unsigned)block_count,
             (unsigned)sample_count);

    for (size_t block = 0; block < block_count; ++block) {
        trigger_dump(sample_count);

        const uint32_t hash = fnv1a_dump_hash(words, sample_count);
        const c5vrx_iq10_sample_t first = c5vrx_adc_decode_word(words[0]);
        const c5vrx_iq10_sample_t last = c5vrx_adc_decode_word(words[sample_count - 1]);

        if (have_previous) {
            if (hash == previous_hash) {
                ++local.repeated_block_hashes;
            }
            const int32_t di = (int32_t)first.i - previous_last.i;
            const int32_t dq = (int32_t)first.q - previous_last.q;
            local.boundary_jump_power_sum +=
                (uint64_t)(di * di) + (uint64_t)(dq * dq);
        }

        ESP_LOGI(TAG,
                 "chain block=%u hash=%08" PRIx32 " first=%d,%d last=%d,%d",
                 (unsigned)block,
                 hash,
                 (int)first.i,
                 (int)first.q,
                 (int)last.i,
                 (int)last.q);

        previous_hash = hash;
        previous_last = last;
        have_previous = true;
        ++local.blocks_completed;
        local.total_samples += sample_count;
    }

    const uint64_t boundaries = local.blocks_completed > 1
        ? (uint64_t)local.blocks_completed - 1u
        : 0u;
    ESP_LOGW(TAG,
             "chain summary: blocks=%u total=%" PRIu64 " repeated_hashes=%" PRIu32 " avg_boundary_jump_power=%" PRIu64,
             (unsigned)local.blocks_completed,
             local.total_samples,
             local.repeated_block_hashes,
             boundaries ? local.boundary_jump_power_sum / boundaries : 0u);

    if (stats) {
        *stats = local;
    }
    return ESP_OK;
}

typedef struct {
    volatile bool started;
    volatile bool finished;
    int64_t begin_us;
    int64_t end_us;
} ring_probe_worker_t;

static ring_probe_worker_t s_ring_probe_worker;
static volatile bool s_ring_probe_busy;

static void ring_probe_adctrig_task(void *arg)
{
    ring_probe_worker_t *worker = arg;
    worker->begin_us = esp_timer_get_time();
    worker->started = true;
    /* RX-error-1 is a known historical trigger. The vendor function owns all
     * setup, its one-second timeout, shutdown and RF-state restoration. */
    adctrig((int32_t)C5VRX_ADC_DUMP_MAX_SAMPLES - 1,
            7, 1, 1, 0, 0, 0, 0, 0);
    worker->end_us = esp_timer_get_time();
    worker->finished = true;
    vTaskDelete(NULL);
}

esp_err_t c5vrx_adc_dump_ring_probe(c5vrx_adc_ring_probe_stats_t *stats)
{
    if (!stats) return ESP_ERR_INVALID_ARG;
    if (s_ring_probe_busy) return ESP_ERR_INVALID_STATE;
    s_ring_probe_busy = true;
    memset(stats, 0, sizeof(*stats));
    stats->minimum_pointer = UINT32_MAX;

    set_dump_mode(0);
    ring_probe_worker_t *const worker = &s_ring_probe_worker;
    memset(worker, 0, sizeof(*worker));
    if (xTaskCreate(ring_probe_adctrig_task, "c5vrx_ring_probe", 3072,
                    worker, 4, NULL) != pdPASS) {
        s_ring_probe_busy = false;
        return ESP_ERR_NO_MEM;
    }

    const int64_t wait_deadline = esp_timer_get_time() + 1500000;
    while (!worker->started && esp_timer_get_time() < wait_deadline)
        vTaskDelay(1);
    if (!worker->started) {
        s_ring_probe_busy = false;
        return ESP_ERR_TIMEOUT;
    }

    uint32_t previous_pointer = UINT32_MAX;
    uint32_t previous_word = 0;
    bool have_word = false;
    while (!worker->finished && esp_timer_get_time() < wait_deadline) {
        const uint32_t pointer =
            (*(volatile const uint32_t *)(uintptr_t)C5VRX_FE_DUMP_POINTER_REG) & 0xffffu;
        ++stats->observations;
        if (previous_pointer != UINT32_MAX && pointer != previous_pointer)
            ++stats->pointer_changes;
        if (pointer < stats->minimum_pointer) stats->minimum_pointer = pointer;
        if (pointer > stats->maximum_pointer) stats->maximum_pointer = pointer;

        /* Historical tools define this pointer in words. Stay 256 words behind
         * the producer so the sampled location is not the current write word. */
        const uint32_t safe_index = (pointer - 256u) &
            (C5VRX_ADC_DUMP_MAX_SAMPLES - 1u);
        const uint32_t word = *(volatile const uint32_t *)(uintptr_t)
            (C5VRX_ADC_DUMP_BASE_ADDR + safe_index * sizeof(uint32_t));
        if (have_word && word != previous_word) ++stats->content_changes;
        previous_word = word;
        have_word = true;
        previous_pointer = pointer;
        vTaskDelay(1);
    }
    if (!worker->finished) {
        /* Keep the static state reserved until the vendor-owned call exits.
         * Its recovered timeout is one second, so this is only a safety net. */
        while (!worker->finished) vTaskDelay(1);
        s_ring_probe_busy = false;
        return ESP_ERR_TIMEOUT;
    }

    stats->active_time_us = (uint64_t)(worker->end_us - worker->begin_us);
    stats->final_status =
        *(volatile const uint32_t *)(uintptr_t)C5VRX_FE_DUMP_CONTROL_REG;
    stats->completion_bit_seen =
        (stats->final_status & C5VRX_FE_DUMP_DONE_BIT) != 0;
    stats->reached_vendor_timeout = stats->active_time_us >= 900000u;
    if (stats->minimum_pointer == UINT32_MAX) stats->minimum_pointer = 0;

    ESP_LOGW(TAG,
             "single-arm ring probe (NOT a stream): active_us=%llu observations=%u pointer_changes=%u range=%u..%u content_changes=%u done=%u vendor_timeout=%u status=%08" PRIx32,
             (unsigned long long)stats->active_time_us,
             (unsigned)stats->observations,
             (unsigned)stats->pointer_changes, (unsigned)stats->minimum_pointer,
             (unsigned)stats->maximum_pointer, (unsigned)stats->content_changes,
             stats->completion_bit_seen ? 1u : 0u,
             stats->reached_vendor_timeout ? 1u : 0u,
             (uint32_t)stats->final_status);
    s_ring_probe_busy = false;
    return ESP_OK;
}

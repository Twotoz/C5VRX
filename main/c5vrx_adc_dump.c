#include "c5vrx_adc_dump.h"

#include <inttypes.h>
#include <limits.h>
#include <stdio.h>
#include <string.h>

#include "esp_log.h"

static const char *TAG = "c5vrx_adc_dump";

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
             "EXPERIMENTAL finite IQ capture: %u samples, buffer=0x%08" PRIx32 ", 80 MHz mode",
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

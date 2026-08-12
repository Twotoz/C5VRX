#include "c5vrx_adc_dump.h"

#include <inttypes.h>
#include <limits.h>
#include <stdio.h>

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

esp_err_t c5vrx_adc_dump_capture(size_t sample_count, bool print_raw_words)
{
    if (sample_count == 0 || sample_count > C5VRX_ADC_DUMP_MAX_SAMPLES) {
        ESP_LOGE(TAG, "sample_count must be 1..%u", (unsigned)C5VRX_ADC_DUMP_MAX_SAMPLES);
        return ESP_ERR_INVALID_ARG;
    }

    ESP_LOGW(TAG,
             "EXPERIMENTAL finite IQ capture: %u samples, buffer=0x%08" PRIx32 ", 80 MHz mode",
             (unsigned)sample_count, (uint32_t)C5VRX_ADC_DUMP_BASE_ADDR);

    /*
     * C5 loop_dump_test() calls set_dump_mode() before adctrig(). Mode 0 is the
     * normal 10-bit path. Historical Espressif adc_dump.py uses the same mode.
     */
    set_dump_mode(0);

    /*
     * Software trigger = 0. Historical tooling passes N-1 for N samples; the
     * C5 function itself adds one before programming the sample-count field.
     */
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

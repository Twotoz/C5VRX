/* SPDX-License-Identifier: GPL-3.0-only */

#include "c5vrx_wbfm_hw.h"

#include <math.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

#include "c5vrx_adc_dump.h"
#include "driver/bitscrambler.h"
#include "driver/bitscrambler_loopback.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "soc/soc_caps.h"

static const char *TAG = "c5vrx_wbfm_hw";

BITSCRAMBLER_PROGRAM(c5vrx_wbfm_4to1_program, "c5vrx_wbfm_4to1");

#define C5VRX_WBFM_TEST_OUTPUT_SAMPLES 256u
#define C5VRX_WBFM_TEST_DECIMATION     4u
#define C5VRX_WBFM_TEST_INPUT_WORDS    (C5VRX_WBFM_TEST_OUTPUT_SAMPLES * C5VRX_WBFM_TEST_DECIMATION)
#define C5VRX_WBFM_PHASE_BIAS          32u
#define C5VRX_WBFM_LUT_WORDS           1024u
#define C5VRX_PI_F                     3.14159265358979323846f

struct c5vrx_wbfm_hw_context {
    bitscrambler_handle_t bs;
    size_t maximum_input_words;
};

static uint32_t pack_iq10(int16_t i, int16_t q)
{
    return (((uint32_t)i & 0x3ffu) << 10) | ((uint32_t)q & 0x3ffu);
}

static float coarse_signed_center(unsigned code5)
{
    const int signed5 = (code5 & 0x10u) ? (int)code5 - 32 : (int)code5;
    return (float)signed5 * 32.0f + 15.5f;
}

static uint8_t coarse_phase6_from_iq(int16_t i, int16_t q)
{
    const unsigned i5 = (((uint16_t)i & 0x3ffu) >> 5) & 0x1fu;
    const unsigned q5 = (((uint16_t)q & 0x3ffu) >> 5) & 0x1fu;
    float phase = atan2f(coarse_signed_center(q5), coarse_signed_center(i5));
    if (phase < 0.0f) {
        phase += 2.0f * C5VRX_PI_F;
    }
    return (uint8_t)lrintf(phase * (64.0f / (2.0f * C5VRX_PI_F))) & 0x3fu;
}

uint8_t c5vrx_wbfm_coarse_phase6(uint32_t packed_iq)
{
    const c5vrx_iq10_sample_t sample = c5vrx_adc_decode_word(packed_iq);
    return coarse_phase6_from_iq(sample.i, sample.q);
}

static void build_phase_lut(uint16_t lut[C5VRX_WBFM_LUT_WORDS])
{
    for (unsigned i5 = 0; i5 < 32u; ++i5) {
        for (unsigned q5 = 0; q5 < 32u; ++q5) {
            float phase = atan2f(coarse_signed_center(q5), coarse_signed_center(i5));
            if (phase < 0.0f) {
                phase += 2.0f * C5VRX_PI_F;
            }
            const uint8_t p =
                (uint8_t)lrintf(phase * (64.0f / (2.0f * C5VRX_PI_F))) & 0x3fu;
            const uint8_t bias_minus =
                (uint8_t)(C5VRX_WBFM_PHASE_BIAS - p) & 0x3fu;
            lut[(i5 << 5) | q5] =
                (uint16_t)p | ((uint16_t)bias_minus << 8);
        }
    }
}

esp_err_t c5vrx_wbfm_hw_create(size_t maximum_input_words,
                               c5vrx_wbfm_hw_context_t **context)
{
    if (!context || maximum_input_words < 8u ||
        (maximum_input_words & 3u)) {
        return ESP_ERR_INVALID_ARG;
    }
    *context = NULL;
    c5vrx_wbfm_hw_context_t *ctx = calloc(1, sizeof(*ctx));
    if (!ctx) return ESP_ERR_NO_MEM;

    uint16_t *lut = heap_caps_malloc(C5VRX_WBFM_LUT_WORDS * sizeof(uint16_t),
                                     MALLOC_CAP_INTERNAL);
    if (!lut) {
        free(ctx);
        return ESP_ERR_NO_MEM;
    }
    build_phase_lut(lut);

    const size_t max_transfer = maximum_input_words * sizeof(uint32_t);
    esp_err_t err = bitscrambler_loopback_create(
        &ctx->bs,
        SOC_BITSCRAMBLER_ATTACH_I2S0,
        max_transfer);
    if (err == ESP_OK) {
        err = bitscrambler_load_program(ctx->bs, c5vrx_wbfm_4to1_program);
    }
    if (err == ESP_OK) {
        err = bitscrambler_load_lut(ctx->bs, lut,
                                    C5VRX_WBFM_LUT_WORDS * sizeof(uint16_t));
    }
    free(lut);
    if (err != ESP_OK) {
        if (ctx->bs) bitscrambler_free(ctx->bs);
        free(ctx);
        return err;
    }
    ctx->maximum_input_words = maximum_input_words;
    *context = ctx;
    return ESP_OK;
}

void c5vrx_wbfm_hw_destroy(c5vrx_wbfm_hw_context_t *context)
{
    if (!context) return;
    if (context->bs) bitscrambler_free(context->bs);
    free(context);
}

esp_err_t c5vrx_wbfm_hw_transform_context(
    c5vrx_wbfm_hw_context_t *context,
    const uint32_t *packed_iq,
    size_t input_words,
    uint8_t *phase_delta,
    size_t output_capacity,
    size_t *output_written)
{
    if (output_written) *output_written = 0;
    if (!context || !packed_iq || !phase_delta || input_words < 8u ||
        (input_words & 3u) || input_words > context->maximum_input_words) {
        return ESP_ERR_INVALID_ARG;
    }
    const size_t expected_output = input_words / C5VRX_WBFM_TEST_DECIMATION;
    if (output_capacity < expected_output) return ESP_ERR_INVALID_SIZE;

    size_t written = 0;
    esp_err_t err = bitscrambler_loopback_run(
        context->bs, (void *)packed_iq, input_words * sizeof(uint32_t),
        phase_delta, output_capacity, &written);

    if (err == ESP_OK && output_written) *output_written = written;
    return err;
}

esp_err_t c5vrx_wbfm_hw_transform(const uint32_t *packed_iq,
                                   size_t input_words,
                                   uint8_t *phase_delta,
                                   size_t output_capacity,
                                   size_t *output_written)
{
    c5vrx_wbfm_hw_context_t *context = NULL;
    esp_err_t err = c5vrx_wbfm_hw_create(input_words, &context);
    if (err == ESP_OK) {
        err = c5vrx_wbfm_hw_transform_context(
            context, packed_iq, input_words, phase_delta,
            output_capacity, output_written);
    }
    c5vrx_wbfm_hw_destroy(context);
    return err;
}

esp_err_t c5vrx_wbfm_hw_probe_dump(size_t sample_count)
{
    if (sample_count < 8u || sample_count > C5VRX_ADC_DUMP_MAX_SAMPLES ||
        (sample_count & 3u) != 0u) {
        return ESP_ERR_INVALID_ARG;
    }

    esp_err_t err = c5vrx_adc_dump_capture(sample_count, false);
    if (err != ESP_OK) {
        return err;
    }

    const size_t output_capacity = sample_count / C5VRX_WBFM_TEST_DECIMATION;
    uint32_t *iq_copy = heap_caps_malloc(sample_count * sizeof(uint32_t),
                                         MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
    uint8_t *fm = heap_caps_calloc(output_capacity, 1,
                                   MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
    if (!iq_copy || !fm) {
        free(iq_copy);
        free(fm);
        return ESP_ERR_NO_MEM;
    }

    volatile const uint32_t *const dump =
        (volatile const uint32_t *)(uintptr_t)C5VRX_ADC_DUMP_BASE_ADDR;
    for (size_t i = 0; i < sample_count; ++i) {
        iq_copy[i] = dump[i];
    }

    size_t written = 0;
    err = c5vrx_wbfm_hw_transform(
        iq_copy,
        sample_count,
        fm,
        output_capacity,
        &written);

    if (err == ESP_OK && written > 1u) {
        unsigned min_code = 63u;
        unsigned max_code = 0u;
        uint64_t sum = 0;
        uint64_t abs_dev_sum = 0;
        for (size_t i = 1; i < written; ++i) {
            const unsigned code = fm[i] & 0x3fu;
            if (code < min_code) min_code = code;
            if (code > max_code) max_code = code;
            sum += code;
            abs_dev_sum += code >= C5VRX_WBFM_PHASE_BIAS
                ? code - C5VRX_WBFM_PHASE_BIAS
                : C5VRX_WBFM_PHASE_BIAS - code;
        }
        const size_t valid = written - 1u;
        ESP_LOGW(TAG,
                 "finite RF -> BitScrambler WBFM proof: iq=%u, fm=%u (4:1 retained-sample transform; physical rates UNKNOWN), code min/max=%u/%u mean=%u mean_abs_dev_from_bias=%u",
                 (unsigned)sample_count,
                 (unsigned)written,
                 min_code,
                 max_code,
                 (unsigned)(sum / valid),
                 (unsigned)(abs_dev_sum / valid));
        printf("C5VRX_WBFM_METRICS iq_samples=%u fm_samples=%u decimation=4 min=%u max=%u mean=%u mean_abs_dev_from_bias=%u physical_rates=UNKNOWN code=0\n",
               (unsigned)sample_count, (unsigned)written,
               min_code, max_code, (unsigned)(sum / valid),
               (unsigned)(abs_dev_sum / valid));
        fflush(stdout);
    }

    free(iq_copy);
    free(fm);
    return err;
}

esp_err_t c5vrx_wbfm_hw_self_test(void)
{
    const size_t input_bytes = C5VRX_WBFM_TEST_INPUT_WORDS * sizeof(uint32_t);
    const size_t output_bytes = C5VRX_WBFM_TEST_OUTPUT_SAMPLES;

    uint32_t *input = heap_caps_malloc(input_bytes,
                                       MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
    uint8_t *output = heap_caps_calloc(output_bytes, 1,
                                       MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
    if (!input || !output) {
        free(input);
        free(output);
        return ESP_ERR_NO_MEM;
    }

    const float kept_step = 5.0f * (2.0f * C5VRX_PI_F / 64.0f);
    const float input_step = kept_step / (float)C5VRX_WBFM_TEST_DECIMATION;

    uint8_t kept_phase[C5VRX_WBFM_TEST_OUTPUT_SAMPLES];
    for (unsigned n = 0; n < C5VRX_WBFM_TEST_INPUT_WORDS; ++n) {
        const float phase = input_step * (float)n;
        const int16_t i = (int16_t)lrintf(400.0f * cosf(phase));
        const int16_t q = (int16_t)lrintf(400.0f * sinf(phase));
        input[n] = pack_iq10(i, q);
        if ((n % C5VRX_WBFM_TEST_DECIMATION) == 0) {
            kept_phase[n / C5VRX_WBFM_TEST_DECIMATION] =
                coarse_phase6_from_iq(i, q);
        }
    }

    size_t written = 0;
    esp_err_t err = c5vrx_wbfm_hw_transform(
        input,
        C5VRX_WBFM_TEST_INPUT_WORDS,
        output,
        output_bytes,
        &written);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "BitScrambler self-test transform failed: %s",
                 esp_err_to_name(err));
        free(input);
        free(output);
        return err;
    }

    if (written < C5VRX_WBFM_TEST_OUTPUT_SAMPLES - 1u) {
        ESP_LOGE(TAG, "short BitScrambler output: %u bytes", (unsigned)written);
        free(input);
        free(output);
        return ESP_FAIL;
    }

    unsigned mismatches = 0;
    const unsigned compare_n =
        written < C5VRX_WBFM_TEST_OUTPUT_SAMPLES ? (unsigned)written
                                                 : C5VRX_WBFM_TEST_OUTPUT_SAMPLES;

    /* output[0] is the priming sample after counter reset. */
    for (unsigned k = 1; k < compare_n; ++k) {
        const uint8_t expected = (uint8_t)(
            C5VRX_WBFM_PHASE_BIAS +
            kept_phase[k] - kept_phase[k - 1]) & 0x3fu;
        const uint8_t actual = output[k] & 0x3fu;
        if (actual != expected) {
            if (mismatches < 8u) {
                ESP_LOGE(TAG,
                         "mismatch k=%u actual=%u expected=%u phase=%u prev=%u",
                         k, actual, expected,
                         (unsigned)kept_phase[k],
                         (unsigned)kept_phase[k - 1]);
            }
            ++mismatches;
        }
    }

    ESP_LOGI(TAG,
             "BitScrambler 4:1 WBFM proof: input=%u bytes output=%u bytes mismatches=%u (first byte ignored as priming)",
             (unsigned)input_bytes,
             (unsigned)written,
             mismatches);

    free(input);
    free(output);
    return mismatches == 0u ? ESP_OK : ESP_FAIL;
}

#include "c5vrx_wbfm_hw.h"

#include <math.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>

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

static uint32_t pack_iq10(int16_t i, int16_t q)
{
    return (((uint32_t)i & 0x3ffu) << 10) | ((uint32_t)q & 0x3ffu);
}

static int coarse_signed_center(unsigned code5)
{
    const int signed5 = (code5 & 0x10u) ? (int)code5 - 32 : (int)code5;
    /* Center of the corresponding 32-code-wide signed 10-bit bucket. */
    return signed5 * 32 + 15;
}

static uint8_t coarse_phase6_from_iq(int16_t i, int16_t q)
{
    const unsigned i5 = (((uint16_t)i & 0x3ffu) >> 5) & 0x1fu;
    const unsigned q5 = (((uint16_t)q & 0x3ffu) >> 5) & 0x1fu;
    const float phase = atan2f((float)coarse_signed_center(q5),
                               (float)coarse_signed_center(i5));
    float wrapped = phase;
    if (wrapped < 0.0f) {
        wrapped += 2.0f * C5VRX_PI_F;
    }
    return (uint8_t)lrintf(wrapped * (64.0f / (2.0f * C5VRX_PI_F))) & 0x3fu;
}

static void build_phase_lut(uint16_t lut[C5VRX_WBFM_LUT_WORDS])
{
    for (unsigned i5 = 0; i5 < 32u; ++i5) {
        for (unsigned q5 = 0; q5 < 32u; ++q5) {
            float phase = atan2f((float)coarse_signed_center(q5),
                                 (float)coarse_signed_center(i5));
            if (phase < 0.0f) {
                phase += 2.0f * C5VRX_PI_F;
            }
            const uint8_t p =
                (uint8_t)lrintf(phase * (64.0f / (2.0f * C5VRX_PI_F))) & 0x3fu;
            const uint8_t bias_minus = (uint8_t)(C5VRX_WBFM_PHASE_BIAS - p) & 0x3fu;
            lut[(i5 << 5) | q5] = (uint16_t)p | ((uint16_t)bias_minus << 8);
        }
    }
}

esp_err_t c5vrx_wbfm_hw_self_test(void)
{
    const size_t input_bytes = C5VRX_WBFM_TEST_INPUT_WORDS * sizeof(uint32_t);
    const size_t output_bytes = C5VRX_WBFM_TEST_OUTPUT_SAMPLES;

    uint32_t *input = heap_caps_malloc(input_bytes, MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
    uint8_t *output = heap_caps_calloc(output_bytes, 1, MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
    uint16_t *lut = heap_caps_malloc(C5VRX_WBFM_LUT_WORDS * sizeof(uint16_t),
                                     MALLOC_CAP_INTERNAL);
    if (!input || !output || !lut) {
        free(input);
        free(output);
        free(lut);
        return ESP_ERR_NO_MEM;
    }
    build_phase_lut(lut);

    /*
     * Choose a phase ramp whose phase advances by about five phase6 codes for
     * every fourth (kept) I/Q sample. This exercises wrap behavior without
     * approaching the +/-pi ambiguity of an FM discriminator.
     */
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

    bitscrambler_handle_t bs = NULL;
    esp_err_t err = bitscrambler_loopback_create(
        &bs,
        SOC_BITSCRAMBLER_ATTACH_I2S0,
        input_bytes);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "loopback create failed: %s", esp_err_to_name(err));
        free(input);
        free(output);
        free(lut);
        return err;
    }

    err = bitscrambler_load_program(bs, c5vrx_wbfm_4to1_program);
    if (err == ESP_OK) {
        err = bitscrambler_load_lut(bs, lut,
                                    C5VRX_WBFM_LUT_WORDS * sizeof(uint16_t));
    }
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "program/LUT load failed: %s", esp_err_to_name(err));
        bitscrambler_free(bs);
        free(input);
        free(output);
        free(lut);
        return err;
    }

    size_t written = 0;
    err = bitscrambler_loopback_run(
        bs,
        input,
        input_bytes,
        output,
        output_bytes,
        &written);
    bitscrambler_free(bs);

    if (err != ESP_OK) {
        ESP_LOGE(TAG, "loopback transform failed: %s", esp_err_to_name(err));
        free(input);
        free(output);
        free(lut);
        return err;
    }

    if (written < C5VRX_WBFM_TEST_OUTPUT_SAMPLES - 1u) {
        ESP_LOGE(TAG, "short BitScrambler output: %u bytes", (unsigned)written);
        free(input);
        free(output);
        free(lut);
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
    free(lut);
    return mismatches == 0u ? ESP_OK : ESP_FAIL;
}

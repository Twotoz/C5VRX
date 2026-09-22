#include "adjacent_m2m.h"
#include "adjacent_math.h"

#include <math.h>
#include <stdlib.h>
#include <string.h>

#include "driver/bitscrambler.h"
#include "driver/bitscrambler_loopback.h"
#include "esp_heap_caps.h"
#include "esp_timer.h"
#include "soc/soc_caps.h"

BITSCRAMBLER_PROGRAM(s_adjacent_m2m_program, "fm_adjacent_m2m");

#define ADJ_LUT_ITEMS 1024u
#define ADJ_LUT_BYTES (ADJ_LUT_ITEMS * sizeof(uint16_t))
#define ADJ_PI_F 3.14159265358979323846f

static bitscrambler_handle_t s_adj_bs;
static uint8_t s_phase7[256];
static uint8_t s_low_conf[256];
static adjacent_m2m_stats_t s_stats;

static int signed4(unsigned nibble)
{
    return (nibble & 8u) ? (int)nibble - 16 : (int)nibble;
}

static float wrap_pi(float x)
{
    while (x >= ADJ_PI_F) x -= 2.0f * ADJ_PI_F;
    while (x < -ADJ_PI_F) x += 2.0f * ADJ_PI_F;
    return x;
}

static uint8_t quantized_phase7(uint8_t packed)
{
    int qs = signed4(packed & 0x0fu);
    int is = signed4(packed >> 4u);
    float q = 64.0f * (float)qs + 31.5f;
    float i = 64.0f * (float)is + 31.5f;
    int phase = (int)lrintf(atan2f(q, i) * (128.0f / (2.0f * ADJ_PI_F)));
    return (uint8_t)phase & 0x7fu;
}

static bool quantized_low_confidence(uint8_t packed)
{
    int qs = signed4(packed & 0x0fu);
    int is = signed4(packed >> 4u);
    float q0 = 64.0f * (float)qs;
    float q1 = q0 + 63.0f;
    float i0 = 64.0f * (float)is;
    float i1 = i0 + 63.0f;
    float qc = (q0 + q1) * 0.5f;
    float ic = (i0 + i1) * 0.5f;
    float center = atan2f(qc, ic);
    float lo = 10.0f;
    float hi = -10.0f;
    const float q[2] = {q0, q1};
    const float i[2] = {i0, i1};

    for (unsigned qi = 0; qi < 2u; ++qi) {
        for (unsigned ii = 0; ii < 2u; ++ii) {
            float d = wrap_pi(atan2f(q[qi], i[ii]) - center);
            if (d < lo) lo = d;
            if (d > hi) hi = d;
        }
    }

    bool origin_cell = q0 <= 0.0f && q1 >= 0.0f &&
                       i0 <= 0.0f && i1 >= 0.0f;
    bool rail = qs == -8 || qs == 7 || is == -8 || is == 7;
    float spread = hi - lo;

    /* Static confidence is deliberately conservative only where Q4 phase is
     * intrinsically ill-conditioned. A large FM delta alone is never a reason
     * to hold video. */
    return origin_cell || spread > 0.60f || (rail && spread > 0.35f);
}

static void build_lut(uint16_t lut[ADJ_LUT_ITEMS])
{
    memset(lut, 0, ADJ_LUT_BYTES);

    for (unsigned packed = 0; packed < 256u; ++packed) {
        uint8_t phase = quantized_phase7((uint8_t)packed);
        bool low = quantized_low_confidence((uint8_t)packed);
        uint8_t negative = (uint8_t)(0u - phase) & 0x7fu;
        s_phase7[packed] = phase;
        s_low_conf[packed] = low ? 1u : 0u;
        lut[packed] = (uint16_t)phase |
                      ((uint16_t)(low ? 1u : 0u) << 7u) |
                      ((uint16_t)negative << 9u);
    }

    /* Pair-map bank. qsum is the arithmetic pair-sum /2. Because the full
     * phase7 turn is 128, qsum outside [-32,+31] proves a winding that an
     * endpoint discriminator would fold by one full turn. */
    for (unsigned state = 0; state < 256u; ++state) {
        int qsum = adjacent_signed7(state & 0x7fu);
        bool low_middle = (state & 0x80u) != 0u;
        int code = 20 + 3 * qsum; /* P20/G2, including real 2:1 boxcar. */
        uint8_t candidate = adjacent_clamp_code(code);
        bool winding = qsum < -32 || qsum >= 32;
        bool hold = low_middle && winding;
        lut[0x100u | state] = (uint16_t)(candidate >> 1u) |
                              ((uint16_t)(hold ? 1u : 0u) << 5u);
    }

    /* Final hold-map. Normal samples pass the candidate with only one DAC LSB
     * of quantization. A low-confidence winding event coasts for one output
     * from the previous three-bit CVBS bucket instead of creating a rail spike. */
    for (unsigned state = 0; state < 512u; ++state) {
        unsigned candidate5 = state & 0x1fu;
        unsigned previous3 = (state >> 5u) & 0x7u;
        bool hold = (state & 0x100u) != 0u;
        uint8_t code = hold ? (uint8_t)(previous3 * 8u + 4u)
                            : (uint8_t)(candidate5 << 1u);
        lut[0x200u | state] = code;
    }
}

esp_err_t adjacent_m2m_init(void)
{
    if (s_adj_bs) return ESP_OK;

    memset(&s_stats, 0, sizeof(s_stats));
    uint16_t *lut = heap_caps_malloc(ADJ_LUT_BYTES, MALLOC_CAP_INTERNAL);
    if (!lut) return ESP_ERR_NO_MEM;
    build_lut(lut);

    esp_err_t err = bitscrambler_loopback_create(
        &s_adj_bs, SOC_BITSCRAMBLER_ATTACH_I2S0, ADJACENT_M2M_BLOCK_BYTES);
    if (err == ESP_OK)
        err = bitscrambler_load_program(s_adj_bs, s_adjacent_m2m_program);
    if (err == ESP_OK)
        err = bitscrambler_load_lut(s_adj_bs, lut, ADJ_LUT_BYTES);
    free(lut);

    if (err != ESP_OK) adjacent_m2m_deinit();
    return err;
}

void adjacent_m2m_deinit(void)
{
    if (s_adj_bs) {
        bitscrambler_free(s_adj_bs);
        s_adj_bs = NULL;
    }
}

esp_err_t adjacent_m2m_transform(const uint8_t *input, size_t input_bytes,
                                 uint8_t *output, size_t output_bytes,
                                 uint32_t *elapsed_us)
{
    if (!s_adj_bs || !input || !output || input_bytes == 0u ||
        (input_bytes & 1u) != 0u || output_bytes != input_bytes ||
        input_bytes > ADJACENT_M2M_BLOCK_BYTES) {
        return ESP_ERR_INVALID_ARG;
    }

    size_t written = 0u;
    int64_t begin = esp_timer_get_time();
    esp_err_t err = bitscrambler_loopback_run(
        s_adj_bs, (void *)input, input_bytes, output, output_bytes, &written);
    uint32_t us = (uint32_t)(esp_timer_get_time() - begin);

    ++s_stats.transforms;
    s_stats.last_us = us;
    if (us > s_stats.max_us) s_stats.max_us = us;
    if (err != ESP_OK) ++s_stats.failures;
    if (err == ESP_OK && written != output_bytes) {
        ++s_stats.short_writes;
        err = ESP_ERR_INVALID_SIZE;
    }
    if (elapsed_us) *elapsed_us = us;
    return err;
}

uint8_t adjacent_m2m_reference_pair(uint8_t previous, uint8_t middle,
                                    uint8_t current, uint8_t previous_code,
                                    bool *held)
{
    int pair = adjacent_pair_sum7(
        s_phase7[previous], s_phase7[middle], s_phase7[current]);
    int qsum = adjacent_pair_qsum7(pair);
    if (qsum < -64) qsum = -64;
    if (qsum > 63) qsum = 63;

    bool hold = s_low_conf[middle] != 0u &&
                adjacent_pair_proves_winding(pair);
    uint8_t candidate_code = adjacent_map_qsum_to_cvbs(qsum);
    uint8_t code = hold ? (uint8_t)(((previous_code >> 3u) & 7u) * 8u + 4u)
                        : (uint8_t)(candidate_code & 0x3eu);
    if (held) *held = hold;
    return code;
}

const adjacent_m2m_stats_t *adjacent_m2m_stats(void)
{
    return &s_stats;
}

void adjacent_m2m_note_boundary_hold(void)
{
    ++s_stats.held_boundary_pairs;
}

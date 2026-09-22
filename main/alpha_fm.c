#include "alpha_fm.h"

#include <math.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "driver/bitscrambler.h"
#include "driver/bitscrambler_loopback.h"
#include "esp_cache.h"
#include "esp_heap_caps.h"
#include "esp_timer.h"
#include "hal/bitscrambler_peri_select.h"

BITSCRAMBLER_PROGRAM(s_alpha_fm_program, "fm_alpha");

#define ALPHA_LUT_ITEMS 1024u
#define ALPHA_LUT_BYTES (ALPHA_LUT_ITEMS * sizeof(uint16_t))
#define ALPHA_PEDESTAL 20
#define ALPHA_GAIN_SETTING 2
#define ALPHA_CONF_POWER_MIN 16
#define ALPHA_MILD_INNOVATION 8
#define ALPHA_MEDIUM_INNOVATION 28
#define ALPHA_MAX_LOWCONF_STEP 24
#define PI_F 3.14159265358979323846f

static bitscrambler_handle_t s_alpha_bs;
static uint16_t *s_alpha_lut;
static alpha_fm_stats_t s_stats;
static uint8_t s_boundary_state = ALPHA_INITIAL_STATE;

static int s4(unsigned value)
{
    value &= 0x0fu;
    return (value & 8u) ? (int)value - 16 : (int)value;
}

static float signed_bucket_center(unsigned code)
{
    float center = (float)(code * 64u) + 31.5f;
    if (center >= 512.0f) center -= 1024.0f;
    return center;
}

static uint8_t q4_phase5(uint8_t packed)
{
    const float q = signed_bucket_center(packed & 0x0fu);
    const float i = signed_bucket_center((packed >> 4u) & 0x0fu);
    const int phase = (int)lrintf(atan2f(q, i) *
                                  (32.0f / (2.0f * PI_F)));
    return (uint8_t)phase & 0x1fu;
}

static bool raw_high_confidence(uint8_t packed)
{
    const int q = s4(packed & 0x0fu);
    const int i = s4((packed >> 4u) & 0x0fu);
    const int power = i * i + q * q;
    const bool both_rails = (i == -8 || i == 7) && (q == -8 || q == 7);
    return power >= ALPHA_CONF_POWER_MIN && !both_rails;
}

static int signed_phase5_delta(uint8_t previous, uint8_t current)
{
    int delta = ((int)current - (int)previous) & 31;
    if (delta >= 16) delta -= 32;
    return delta;
}

static int scale_real_sum_phase5(int pair_steps)
{
    const int phase8_sum = pair_steps * 8;
    const int numerator = phase8_sum * (ALPHA_GAIN_SETTING + 1);
    return numerator < 0 ? -((-numerator + 2) / 4) :
                           (numerator + 2) / 4;
}

static uint8_t map_pair_steps(int pair_steps)
{
    int code = ALPHA_PEDESTAL + scale_real_sum_phase5(pair_steps);
    if (code < 0) code = 0;
    if (code > 63) code = 63;
    return (uint8_t)code;
}

static int half_round_away(int value)
{
    const int magnitude = value < 0 ? -value : value;
    const int half = (magnitude + 1) / 2;
    return value < 0 ? -half : half;
}

static uint8_t alpha_tracker_code(unsigned pair_bias,
                                  unsigned state,
                                  bool high_confidence)
{
    int pair_steps = (int)(pair_bias > 62u ? 62u : pair_bias) - 32;
    const int observation = map_pair_steps(pair_steps);
    if (high_confidence) return (uint8_t)observation;

    int prediction = (int)(state & 7u) * 8 + 4;
    if (prediction > 63) prediction = 63;
    const int innovation = observation - prediction;
    const int magnitude = innovation < 0 ? -innovation : innovation;
    int corrected;

    if (magnitude <= ALPHA_MILD_INNOVATION) {
        corrected = observation;
    } else if (magnitude <= ALPHA_MEDIUM_INNOVATION) {
        corrected = prediction + half_round_away(innovation);
    } else {
        int step = innovation;
        if (step > ALPHA_MAX_LOWCONF_STEP) step = ALPHA_MAX_LOWCONF_STEP;
        if (step < -ALPHA_MAX_LOWCONF_STEP) step = -ALPHA_MAX_LOWCONF_STEP;
        corrected = prediction + step;
    }

    if (corrected < 0) corrected = 0;
    if (corrected > 63) corrected = 63;
    return (uint8_t)corrected;
}

static void build_lut(uint16_t *lut)
{
    for (unsigned address = 0; address < ALPHA_LUT_ITEMS; ++address) {
        const uint8_t phase = q4_phase5((uint8_t)(address & 0xffu));

        const uint8_t previous = (uint8_t)(address & 31u);
        const uint8_t current = (uint8_t)((address >> 5u) & 31u);
        const uint8_t biased_delta =
            (uint8_t)(signed_phase5_delta(previous, current) + 16);

        const unsigned pair_bias = address & 63u;
        const unsigned state = (address >> 6u) & 7u;
        const bool high_confidence = ((address >> 9u) & 1u) != 0;
        uint8_t code = alpha_tracker_code(pair_bias, state, high_confidence);

        /*
         * Raw lookups need one confidence bit, while tracker lookups need all
         * six DAC bits. On the overlapping raw-address quarter of the LUT, use
         * tracker-code parity as that confidence bit. Toggling bit 0 changes
         * a low-confidence tracker result by only one DAC code and never
         * changes the 3-bit predictor state (code >> 3).
         */
        if (address < 256u) {
            const unsigned wanted = raw_high_confidence((uint8_t)address) ? 1u : 0u;
            if ((code & 1u) != wanted) code ^= 1u;
        }

        lut[address] = (uint16_t)phase |
                       ((uint16_t)biased_delta << 5u) |
                       ((uint16_t)code << 10u);
    }
}

static uint8_t alpha_code_from_phase(uint8_t previous_phase,
                                     uint8_t sample0,
                                     uint8_t sample1,
                                     uint8_t state,
                                     uint8_t *next_state)
{
    const uint8_t phase0 = (uint8_t)(s_alpha_lut[sample0] & 31u);
    const uint8_t phase1 = (uint8_t)(s_alpha_lut[sample1] & 31u);
    const unsigned d0_address = (unsigned)previous_phase |
                                ((unsigned)phase0 << 5u);
    const unsigned d1_address = (unsigned)phase0 |
                                ((unsigned)phase1 << 5u);
    const unsigned d0_bias = (s_alpha_lut[d0_address] >> 5u) & 31u;
    const unsigned d1_bias = (s_alpha_lut[d1_address] >> 5u) & 31u;
    const unsigned pair_bias = d0_bias + d1_bias;
    const unsigned confidence =
        ((s_alpha_lut[sample0] >> 10u) & 1u) &
        ((s_alpha_lut[sample1] >> 10u) & 1u);
    const unsigned tracker_address =
        pair_bias | (((unsigned)state & 7u) << 6u) | (confidence << 9u);
    const uint8_t code = (uint8_t)((s_alpha_lut[tracker_address] >> 10u) & 63u);
    if (next_state) *next_state = code >> 3u;
    return code;
}

uint8_t alpha_fm_reference_pair(uint8_t previous_raw,
                                uint8_t sample0,
                                uint8_t sample1,
                                uint8_t state,
                                uint8_t *next_state)
{
    return alpha_code_from_phase(q4_phase5(previous_raw),
                                 sample0, sample1, state, next_state);
}

esp_err_t alpha_fm_init(void)
{
    if (s_alpha_bs) return ESP_OK;
    memset(&s_stats, 0, sizeof(s_stats));
    s_boundary_state = ALPHA_INITIAL_STATE;

    s_alpha_lut = heap_caps_aligned_alloc(64u, ALPHA_LUT_BYTES,
                                          MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    if (!s_alpha_lut) return ESP_ERR_NO_MEM;
    build_lut(s_alpha_lut);

    esp_err_t err = bitscrambler_loopback_create(
        &s_alpha_bs, SOC_BITSCRAMBLER_ATTACH_I2S0, ALPHA_FM_BLOCK_BYTES);
    if (err != ESP_OK) goto fail;

    err = bitscrambler_load_program(s_alpha_bs, s_alpha_fm_program);
    if (err != ESP_OK) goto fail;
    err = bitscrambler_load_lut(s_alpha_bs, s_alpha_lut, ALPHA_LUT_BYTES);
    if (err != ESP_OK) goto fail;
    return ESP_OK;

fail:
    if (s_alpha_bs) {
        bitscrambler_free(s_alpha_bs);
        s_alpha_bs = NULL;
    }
    free(s_alpha_lut);
    s_alpha_lut = NULL;
    return err;
}

void alpha_fm_deinit(void)
{
    if (s_alpha_bs) bitscrambler_free(s_alpha_bs);
    s_alpha_bs = NULL;
    free(s_alpha_lut);
    s_alpha_lut = NULL;
    s_boundary_state = ALPHA_INITIAL_STATE;
}

static void sync_m2c(const void *addr, size_t size)
{
    if (!addr || !size || esp_cache_get_line_size_by_addr(addr) == 0) return;
    (void)esp_cache_msync((void *)addr, size,
                          ESP_CACHE_MSYNC_FLAG_DIR_M2C |
                          ESP_CACHE_MSYNC_FLAG_UNALIGNED);
}

static void sync_c2m(const void *addr, size_t size)
{
    if (!addr || !size || esp_cache_get_line_size_by_addr(addr) == 0) return;
    (void)esp_cache_msync((void *)addr, size,
                          ESP_CACHE_MSYNC_FLAG_DIR_C2M |
                          ESP_CACHE_MSYNC_FLAG_UNALIGNED);
}

esp_err_t alpha_fm_transform(const uint8_t *input, uint8_t *output,
                             size_t bytes, uint8_t previous_raw)
{
    if (!s_alpha_bs || !s_alpha_lut || !input || !output ||
        bytes != ALPHA_FM_BLOCK_BYTES)
        return ESP_ERR_INVALID_ARG;

    sync_m2c(input, bytes);

    size_t written = 0;
    const int64_t start = esp_timer_get_time();
    esp_err_t err = bitscrambler_loopback_run(
        s_alpha_bs, (void *)input, bytes, output, bytes, &written);
    const uint32_t m2m_us = (uint32_t)(esp_timer_get_time() - start);

    s_stats.runs++;
    s_stats.last_m2m_us = m2m_us;
    if (m2m_us > s_stats.max_m2m_us) s_stats.max_m2m_us = m2m_us;
    s_stats.last_bytes_written = (uint32_t)written;

    if (err != ESP_OK) {
        s_stats.failures++;
        return err;
    }
    if (written != bytes) {
        s_stats.short_writes++;
        return ESP_ERR_INVALID_SIZE;
    }

    /*
     * Every finite M2M run starts Alpha at phase=0/state=2. Carry the accepted
     * state across halves in software, but touch only the bounded prefix until
     * the reset hardware state converges. After pair zero, both paths have the
     * same phase; after their 3-bit states match, every later hardware output
     * is byte-identical to the persistent Alpha reference.
     */
    sync_m2c(output, bytes);

    uint8_t persistent_phase = q4_phase5(previous_raw);
    uint8_t persistent_state = s_boundary_state;
    uint8_t hardware_phase = 0u;
    uint8_t hardware_state = ALPHA_INITIAL_STATE;
    unsigned repaired_pairs = 0u;
    bool converged = false;

    const unsigned total_pairs = (unsigned)(bytes / 2u);
    const unsigned limit = total_pairs < ALPHA_BOUNDARY_REPAIR_MAX_PAIRS ?
                           total_pairs : ALPHA_BOUNDARY_REPAIR_MAX_PAIRS;

    for (unsigned pair = 0; pair < limit; ++pair) {
        const uint8_t sample0 = input[pair * 2u];
        const uint8_t sample1 = input[pair * 2u + 1u];

        uint8_t next_persistent = persistent_state;
        uint8_t next_hardware = hardware_state;
        const uint8_t accepted = alpha_code_from_phase(
            persistent_phase, sample0, sample1,
            persistent_state, &next_persistent);
        (void)alpha_code_from_phase(
            hardware_phase, sample0, sample1,
            hardware_state, &next_hardware);

        output[pair * 2u] = accepted;
        output[pair * 2u + 1u] = accepted;
        repaired_pairs = pair + 1u;

        persistent_phase = q4_phase5(sample1);
        hardware_phase = persistent_phase;
        persistent_state = next_persistent;
        hardware_state = next_hardware;

        if (hardware_state == persistent_state) {
            converged = true;
            break;
        }
    }

    s_stats.boundary_repair_pairs += repaired_pairs;
    if (repaired_pairs > s_stats.max_boundary_repair_pairs)
        s_stats.max_boundary_repair_pairs = repaired_pairs;

    if (!converged) {
        s_stats.state_convergence_misses++;
        s_stats.failures++;
        return ESP_ERR_INVALID_STATE;
    }

    sync_c2m(output, (size_t)repaired_pairs * 2u);

    /* From convergence onward the hardware path is authoritative. The final
     * duplicated code is therefore the exact state entering the next half. */
    s_boundary_state = output[bytes - 1u] >> 3u;

    const uint32_t total_us = (uint32_t)(esp_timer_get_time() - start);
    s_stats.last_us = total_us;
    if (total_us > s_stats.max_us) s_stats.max_us = total_us;
    if (total_us >= ALPHA_FM_HALF_PERIOD_US)
        s_stats.deadline_misses++;

    return ESP_OK;
}

const alpha_fm_stats_t *alpha_fm_stats(void)
{
    return &s_stats;
}

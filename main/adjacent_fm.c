#include "adjacent_fm.h"

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

BITSCRAMBLER_PROGRAM(s_adjacent_fm_program, "fm_adjacent");

#define ADJ_LUT_ITEMS 1024u
#define ADJ_LUT_BYTES (ADJ_LUT_ITEMS * sizeof(uint16_t))
#define ADJ_PEDESTAL 20
#define ADJ_GAIN_SETTING 2
#define PI_F 3.14159265358979323846f

static bitscrambler_handle_t s_adj_bs;
static uint16_t *s_adj_lut;
static adjacent_fm_stats_t s_stats;

static float signed_bucket_center(unsigned code)
{
    /* MODEM_DIAG exposes signed Q[9:6]/I[9:6]. Each nibble represents one
     * 64-code Q10/I10 bucket. */
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

static int signed_phase5_delta(uint8_t delta)
{
    int value = delta & 0x1fu;
    if (value >= 16) value -= 32;
    return value;
}

static int scale_real_sum_phase5(int pair_steps)
{
    /* One Phase5 step is 8/256 of the phase8 circle. G2 is the proven 1.5x
     * discriminator gain and the /2 belongs after the two adjacent deltas. */
    const int phase8_sum = pair_steps * 8;
    const int numerator = phase8_sum * (ADJ_GAIN_SETTING + 1);
    return numerator < 0 ? -((-numerator + 2) / 4) :
                           (numerator + 2) / 4;
}

static uint8_t map_delta_pair(uint8_t d0, uint8_t d1)
{
    const int pair = signed_phase5_delta(d0) + signed_phase5_delta(d1);
    int code = ADJ_PEDESTAL + scale_real_sum_phase5(pair);
    if (code < 0) code = 0;
    if (code > 63) code = 63;
    return (uint8_t)code;
}

static void build_lut(uint16_t *lut)
{
    /* Low six bits are valid for every possible (d0,d1) final address. */
    for (unsigned address = 0; address < ADJ_LUT_ITEMS; ++address) {
        const uint8_t d0 = (uint8_t)(address & 0x1fu);
        const uint8_t d1 = (uint8_t)((address >> 5u) & 0x1fu);
        lut[address] = map_delta_pair(d0, d1);
    }

    /* Raw-Q4 addresses 0..255 also carry the same Phase5 quantizer used by
     * Golden. The final-map code remains in low bits; phase state lives above
     * it, so one physical 1024x16 LUT safely serves both stages. */
    for (unsigned packed = 0; packed < 256u; ++packed) {
        const uint8_t phase = q4_phase5((uint8_t)packed);
        const uint8_t negative = (uint8_t)(0u - phase) & 0x1fu;
        lut[packed] |= (uint16_t)phase << 6u;
        lut[packed] |= (uint16_t)negative << 11u;
    }
}

uint8_t adjacent_fm_reference_pair(uint8_t previous_raw,
                                   uint8_t sample0,
                                   uint8_t sample1)
{
    const uint8_t p = q4_phase5(previous_raw);
    const uint8_t a = q4_phase5(sample0);
    const uint8_t b = q4_phase5(sample1);
    const uint8_t d0 = (uint8_t)(a - p) & 0x1fu;
    const uint8_t d1 = (uint8_t)(b - a) & 0x1fu;
    return map_delta_pair(d0, d1);
}

esp_err_t adjacent_fm_init(void)
{
    if (s_adj_bs) return ESP_OK;
    memset(&s_stats, 0, sizeof(s_stats));

    s_adj_lut = heap_caps_aligned_alloc(64u, ADJ_LUT_BYTES,
                                        MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    if (!s_adj_lut) return ESP_ERR_NO_MEM;
    build_lut(s_adj_lut);

    esp_err_t err = bitscrambler_loopback_create(
        &s_adj_bs, SOC_BITSCRAMBLER_ATTACH_I2S0, ADJACENT_FM_BLOCK_BYTES);
    if (err != ESP_OK) goto fail;

    err = bitscrambler_load_program(s_adj_bs, s_adjacent_fm_program);
    if (err != ESP_OK) goto fail;
    err = bitscrambler_load_lut(s_adj_bs, s_adj_lut, ADJ_LUT_BYTES);
    if (err != ESP_OK) goto fail;
    return ESP_OK;

fail:
    if (s_adj_bs) {
        bitscrambler_free(s_adj_bs);
        s_adj_bs = NULL;
    }
    free(s_adj_lut);
    s_adj_lut = NULL;
    return err;
}

void adjacent_fm_deinit(void)
{
    if (s_adj_bs) bitscrambler_free(s_adj_bs);
    s_adj_bs = NULL;
    free(s_adj_lut);
    s_adj_lut = NULL;
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

esp_err_t adjacent_fm_transform(const uint8_t *input, uint8_t *output,
                                size_t bytes, uint8_t previous_raw)
{
    if (!s_adj_bs || !input || !output || bytes != ADJACENT_FM_BLOCK_BYTES)
        return ESP_ERR_INVALID_ARG;

    /* RX GDMA produced input. Invalidate stale CPU cache before the loopback
     * helper performs its own C2M maintenance on that same buffer. */
    sync_m2c(input, bytes);

    size_t written = 0;
    const int64_t start = esp_timer_get_time();
    esp_err_t err = bitscrambler_loopback_run(
        s_adj_bs, (void *)input, bytes, output, bytes, &written);
    const uint32_t elapsed = (uint32_t)(esp_timer_get_time() - start);

    s_stats.runs++;
    s_stats.last_us = elapsed;
    if (elapsed > s_stats.max_us) s_stats.max_us = elapsed;
    s_stats.last_bytes_written = (uint32_t)written;
    if (elapsed >= ADJACENT_FM_HALF_PERIOD_US)
        s_stats.deadline_misses++;

    if (err != ESP_OK) {
        s_stats.failures++;
        return err;
    }
    if (written != bytes) {
        s_stats.short_writes++;
        return ESP_ERR_INVALID_SIZE;
    }

    /* A finite M2M run resets O26..O30, so only output pair zero lacks the
     * preceding Phase5 state. Repair exactly one [D,D] pair in CPU; every
     * following pair remains hardware adjacent-FM and state-continuous. */
    sync_m2c(output, bytes);
    const uint8_t first = adjacent_fm_reference_pair(
        previous_raw, input[0], input[1]);
    output[0] = first;
    output[1] = first;
    sync_c2m(output, 64u);
    return ESP_OK;
}

const adjacent_fm_stats_t *adjacent_fm_stats(void)
{
    return &s_stats;
}

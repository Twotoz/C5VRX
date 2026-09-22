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
#include "esp_memory_utils.h"
#include "esp_timer.h"
#include "hal/bitscrambler_peri_select.h"

BITSCRAMBLER_PROGRAM(s_adjacent_fm_program, "fm_adjacent");

#define ADJ_LUT_ITEMS 1024u
#define ADJ_LUT_BYTES (ADJ_LUT_ITEMS * sizeof(uint16_t))
#define ADJ_PHASE_BANK 0x000u
#define ADJ_MAP_BANK   0x200u
#define ADJ_PEDESTAL   20
#define ADJ_GAIN_SETTING 2
#define PI_F 3.14159265358979323846f

static bitscrambler_handle_t s_adj_bs;
static uint16_t *s_adj_lut;
static adjacent_fm_stats_t s_stats;

static float signed_bucket_center(unsigned code)
{
    /* MODEM_DIAG exposes signed Q[9:6]/I[9:6]. Each nibble represents one
     * 64-code Q10/I10 bucket. Use the bucket center exactly like the legacy
     * full-Q4 oracle. */
    float center = (float)(code * 64u) + 31.5f;
    if (center >= 512.0f) center -= 1024.0f;
    return center;
}

static uint8_t q4_phase8(uint8_t packed)
{
    const float q = signed_bucket_center(packed & 0x0fu);
    const float i = signed_bucket_center((packed >> 4u) & 0x0fu);
    int phase = (int)lrintf(atan2f(q, i) * (256.0f / (2.0f * PI_F)));
    if (phase < -128) phase = -128;
    if (phase > 127) phase = 127;
    return (uint8_t)phase;
}

static int scale_real_sum(int sum)
{
    /* G2 is the proven 1.5x discriminator gain. The division by two is the
     * real 40->20 MS/s boxcar after two adjacent discriminator intervals. */
    const int numerator = sum * (ADJ_GAIN_SETTING + 1);
    return numerator < 0 ? -((-numerator + 2) / 4) :
                           (numerator + 2) / 4;
}

static uint8_t map_sum9(unsigned sum_mod)
{
    int sum = (int)(sum_mod & 0x1ffu);
    if (sum >= 256) sum -= 512;
    int code = ADJ_PEDESTAL + scale_real_sum(sum);
    if (code < 0) code = 0;
    if (code > 63) code = 63;
    return (uint8_t)code;
}

static void build_lut(uint16_t *lut)
{
    memset(lut, 0, ADJ_LUT_BYTES);
    for (unsigned packed = 0; packed < 256u; ++packed) {
        uint8_t phase = q4_phase8((uint8_t)packed);
        uint8_t negative = (uint8_t)(0u - phase);
        lut[ADJ_PHASE_BANK | packed] =
            (uint16_t)phase | ((uint16_t)negative << 8u);
    }
    for (unsigned sum = 0; sum < 512u; ++sum)
        lut[ADJ_MAP_BANK | sum] = map_sum9(sum);
}

uint8_t adjacent_fm_reference_pair(uint8_t previous_raw,
                                   uint8_t sample0,
                                   uint8_t sample1)
{
    const uint8_t p = q4_phase8(previous_raw);
    const uint8_t a = q4_phase8(sample0);
    const uint8_t b = q4_phase8(sample1);
    const int8_t d0 = (int8_t)(uint8_t)(a - p);
    const int8_t d1 = (int8_t)(uint8_t)(b - a);
    const int pair = (int)d0 + (int)d1;
    return map_sum9((unsigned)pair & 0x1ffu);
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

    /* RX GDMA produced input. Invalidate any stale CPU cache before the public
     * loopback helper performs its own C2M maintenance. This prevents an old
     * CPU line from being written back over fresh MODEM_DIAG bytes. */
    sync_m2c(input, bytes);

    size_t written = 0;
    const int64_t start = esp_timer_get_time();
    esp_err_t err = bitscrambler_loopback_run(
        s_adj_bs, (void *)input, bytes, output, bytes, &written);
    const uint32_t elapsed =
        (uint32_t)(esp_timer_get_time() - start);

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

    /* The M2M driver resets BitScrambler transport state for every finite run.
     * Only the first 50 ns output depends on state from the previous block.
     * Repair that pair byte-exactly from the captured previous raw sample. */
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

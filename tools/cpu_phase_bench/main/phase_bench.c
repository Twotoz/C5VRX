/* Bounded C5 throughput probe. Does not start RF or modify C5VRX firmware. */
#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include "esp_attr.h"
#include "esp_cpu.h"
#include "esp_heap_caps.h"
#include "esp_partition.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#define BLOCK_BYTES 8192u
#define REPEATS 32u

static uint8_t source[BLOCK_BYTES] __attribute__((aligned(4)));
static uint8_t destination[BLOCK_BYTES] __attribute__((aligned(4)));
static uint8_t phase_lut[256] __attribute__((aligned(4)));
static uint8_t delta_lut[65536] __attribute__((aligned(64)));
extern const uint8_t pair_lut_bin_start[] asm("_binary_pair_lut_bin_start");
extern const uint8_t phase5_lut_bin_start[] asm("_binary_phase5_lut_bin_start");

typedef struct {
    uint32_t magic;
    uint32_t cycles_per_byte_x1000[8];
    uint32_t wrong[8];
    uint32_t free_internal;
} result_t;

static result_t result = {.magic = 0x50484153u};
static unsigned result_index;

static void fill_source(void)
{
    uint16_t *pairs = (uint16_t *)source;
    for (unsigned i = 0; i < BLOCK_BYTES / 2; ++i)
        pairs[i] = (uint16_t)(i * 40503u + 137u);
}

static uint32_t IRAM_ATTR transform_scalar(uint8_t *dst, const uint8_t *src,
                                            const uint8_t *lut, unsigned count)
{
    for (unsigned i = 0; i < count; ++i) dst[i] = lut[src[i]];
    return dst[count - 1];
}

static uint32_t IRAM_ATTR transform_pair(uint8_t *dst, const uint8_t *src,
                                          const uint8_t *lut, unsigned count)
{
    const uint16_t *in = (const uint16_t *)src;
    uint16_t *out = (uint16_t *)dst;
    for (unsigned i = 0; i < count / 2; ++i) {
        uint16_t raw = in[i];
        uint16_t low = lut[raw & 255u];
        uint16_t high = lut[raw >> 8];
        out[i] = low | (high << 8);
    }
    return dst[count - 1];
}

static uint32_t IRAM_ATTR transform_word(uint8_t *dst, const uint8_t *src,
                                          const uint8_t *lut, unsigned count)
{
    const uint32_t *in = (const uint32_t *)src;
    uint32_t *out = (uint32_t *)dst;
    for (unsigned i = 0; i < count / 4; ++i) {
        uint32_t raw = in[i];
        uint32_t value = lut[raw & 255u];
        value |= (uint32_t)lut[(raw >> 8) & 255u] << 8;
        value |= (uint32_t)lut[(raw >> 16) & 255u] << 16;
        value |= (uint32_t)lut[raw >> 24] << 24;
        out[i] = value;
    }
    return dst[count - 1];
}

/* Avoid six pack/shift instructions by writing the four phase bytes directly. */
static uint32_t IRAM_ATTR transform_word_stores(uint8_t *dst, const uint8_t *src,
                                                 const uint8_t *lut, unsigned count)
{
    const uint32_t *in = (const uint32_t *)src;
    for (unsigned i = 0; i < count / 4; ++i) {
        uint32_t raw = in[i];
        dst[4 * i] = lut[raw & 255u];
        dst[4 * i + 1] = lut[(raw >> 8) & 255u];
        dst[4 * i + 2] = lut[(raw >> 16) & 255u];
        dst[4 * i + 3] = lut[raw >> 24];
    }
    return dst[count - 1];
}

static uint32_t IRAM_ATTR transform_pair_table(uint8_t *dst, const uint8_t *src,
                                               const uint8_t *lookup, unsigned count)
{
    const uint16_t *table = (const uint16_t *)lookup;
    const uint16_t *in = (const uint16_t *)src;
    uint16_t *out = (uint16_t *)dst;
    const uint16_t *end = in + count / 2;
    while (in < end) *out++ = table[*in++];
    return out[-1];
}

static uint32_t IRAM_ATTR transform_pair_table_inplace(uint8_t *buffer,
                                                       const uint8_t *lookup,
                                                       unsigned count)
{
    const uint16_t *table = (const uint16_t *)lookup;
    uint16_t *ptr = (uint16_t *)buffer;
    uint16_t *end = ptr + count / 2;
    while (ptr < end) {
        *ptr = table[*ptr];
        ++ptr;
    }
    return ptr[-1];
}

/* Two pairs per branch. This lowers loop overhead without changing the
 * exact raw-pair table or the 16-bit in-place stream format. */
static uint32_t IRAM_ATTR transform_pair_table_inplace_unrolled(uint8_t *buffer,
                                                                const uint8_t *lookup,
                                                                unsigned count)
{
    const uint16_t *table = (const uint16_t *)lookup;
    uint16_t *ptr = (uint16_t *)buffer;
    uint16_t *end = ptr + count / 2;
    while (ptr + 1 < end) {
        uint16_t first = table[ptr[0]];
        uint16_t second = table[ptr[1]];
        ptr[0] = first;
        ptr[1] = second;
        ptr += 2;
    }
    if (ptr < end) *ptr = table[*ptr];
    return end[-1];
}

static void run_inplace(const uint8_t *lookup, bool unrolled)
{
    uint32_t cycles = 0, checksum = 0;
    for (unsigned pass = 0; pass < REPEATS; ++pass) {
        fill_source();
        uint32_t start = esp_cpu_get_cycle_count();
        checksum += unrolled
            ? transform_pair_table_inplace_unrolled(source, lookup, BLOCK_BYTES)
            : transform_pair_table_inplace(source, lookup, BLOCK_BYTES);
        cycles += esp_cpu_get_cycle_count() - start;
    }
    unsigned wrong = 0;
    const uint16_t *out = (const uint16_t *)source;
    for (unsigned i = 0; i < BLOCK_BYTES / 2; ++i) {
        unsigned raw_pair = (uint16_t)(i * 40503u + 137u);
        unsigned raw_m = raw_pair & 255u;
        unsigned raw_c = raw_pair >> 8;
        unsigned m = phase_lut[raw_m], c = phase_lut[raw_c];
        unsigned d1 = (c - m) & 31u;
        uint16_t expected = m | ((d1 & 7u) << 5) | (c << 8)
            | ((d1 >> 3) << 13);
        wrong += out[i] != expected;
    }
    uint32_t cpb = (uint32_t)((uint64_t)cycles * 1000u /
                              (BLOCK_BYTES * REPEATS));
    unsigned slot = unrolled ? 7u : 6u;
    printf("PHASE_BENCH inplace_internal%s cycles_per_byte_x1000=%lu wrong=%u checksum=%lu\n",
           unrolled ? "_unrolled" : "", (unsigned long)cpb, wrong,
           (unsigned long)checksum);
    result.cycles_per_byte_x1000[slot] = cpb;
    result.wrong[slot] = wrong;
}

/* The prospective Golden-compatible bridge only replaces the skipped middle
 * byte with one exact winding bit. The TX LUT still receives every raw endpoint. */
static uint32_t IRAM_ATTR transform_winding(uint8_t *dst, const uint8_t *src,
                                            const uint8_t *delta, unsigned count)
{
    uint8_t previous = src[0];
    const uint8_t *end = src + count - 1;
    ++src;
    ++dst;
    while (src < end) {
        uint8_t middle = src[0];
        uint8_t current = src[1];
        unsigned d0 = delta[previous | ((unsigned)middle << 8)];
        unsigned d1 = delta[middle | ((unsigned)current << 8)];
        dst[0] = (uint8_t)((d0 + d1 - 16u) >= 32u);
        previous = current;
        src += 2;
        dst += 2;
    }
    return previous;
}

static void run_winding(void)
{
    uint32_t checksum = 0;
    uint32_t start = esp_cpu_get_cycle_count();
    for (unsigned pass = 0; pass < REPEATS; ++pass)
        checksum += transform_winding(destination, source, delta_lut, BLOCK_BYTES);
    uint32_t cycles = esp_cpu_get_cycle_count() - start;
    unsigned wrong = 0;
    for (unsigned i = 1; i + 1 < BLOCK_BYTES; i += 2) {
        int p = phase_lut[source[i - 1]], m = phase_lut[source[i]];
        int c = phase_lut[source[i + 1]];
        int d0 = ((m - p + 16) & 31) - 16;
        int d1 = ((c - m + 16) & 31) - 16;
        wrong += destination[i] != (uint8_t)(d0 + d1 < -16 || d0 + d1 >= 16);
    }
    uint32_t cpb = (uint32_t)((uint64_t)cycles * 1000u /
                              (BLOCK_BYTES * REPEATS));
    printf("PHASE_BENCH winding cycles_per_byte_x1000=%lu wrong=%u checksum=%lu\n",
           (unsigned long)cpb, wrong, (unsigned long)checksum);
    /* This diagnostic does not use a result slot: the unrolled exact pair
     * transform is more important to preserve for offline readback. */
}

typedef uint32_t (*transform_t)(uint8_t *, const uint8_t *, const uint8_t *, unsigned);

static void run(const char *name, transform_t function, const uint8_t *lookup,
                bool packed_pair)
{
    uint32_t checksum = 0;
    uint32_t start = esp_cpu_get_cycle_count();
    for (unsigned pass = 0; pass < REPEATS; ++pass)
        checksum += function(destination, source, lookup, BLOCK_BYTES);
    uint32_t cycles = esp_cpu_get_cycle_count() - start;
    unsigned wrong = 0;
    if (packed_pair) {
        const uint16_t *out = (const uint16_t *)destination;
        for (unsigned i = 0; i < BLOCK_BYTES / 2; ++i) {
            unsigned m = phase_lut[source[2 * i]];
            unsigned c = phase_lut[source[2 * i + 1]];
            unsigned d1 = (c - m) & 31u;
            uint16_t expected = m | ((d1 & 7u) << 5) | (c << 8)
                | ((d1 >> 3) << 13);
            wrong += out[i] != expected;
        }
    } else {
        for (unsigned i = 0; i < BLOCK_BYTES; ++i)
            wrong += destination[i] != phase_lut[source[i]];
    }
    printf("PHASE_BENCH %s cycles=%lu bytes=%u cycles_per_byte_x1000=%lu wrong=%u checksum=%lu\n",
           name, (unsigned long)cycles, BLOCK_BYTES * REPEATS,
           (unsigned long)((uint64_t)cycles * 1000u / (BLOCK_BYTES * REPEATS)),
           wrong, (unsigned long)checksum);
    if (result_index < 6) {
        result.cycles_per_byte_x1000[result_index] =
            (uint32_t)((uint64_t)cycles * 1000u / (BLOCK_BYTES * REPEATS));
        result.wrong[result_index] = wrong;
        ++result_index;
    }
}

void app_main(void)
{
    memcpy(phase_lut, phase5_lut_bin_start, 256);
    fill_source();
    memset(destination, 0, sizeof destination);
    printf("PHASE_BENCH free_internal=%u threshold_cycles_per_byte=6\n",
           (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL));
    result.free_internal = heap_caps_get_free_size(MALLOC_CAP_INTERNAL);
    uint8_t *internal_table = heap_caps_aligned_alloc(
        64u, 131072u, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    if (internal_table) {
        memcpy(internal_table, pair_lut_bin_start, 131072u);
        run("pair_table_internal", transform_pair_table, internal_table, true);
        run_inplace(internal_table, false);
        run_inplace(internal_table, true);
    } else {
        printf("PHASE_BENCH pair_table_internal allocation_failed\n");
        result.wrong[5] = UINT32_MAX;
        result.wrong[6] = UINT32_MAX;
    }
    /* Print only. ESP-IDF deliberately aborts when asked to erase its running
     * factory app partition, even an unused page near the end. */
    printf("PHASE_BENCH result inplace=%lu unrolled=%lu wrong=%lu,%lu\n",
           (unsigned long)result.cycles_per_byte_x1000[6],
           (unsigned long)result.cycles_per_byte_x1000[7],
           (unsigned long)result.wrong[6], (unsigned long)result.wrong[7]);
    while (1) {
        vTaskDelay(pdMS_TO_TICKS(2000));
        printf("PHASE_BENCH alive\n");
    }
}

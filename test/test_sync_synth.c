/* C5VRX by Twotoz/contributors: compare the cheap kernel to the archived
 * phase-continuous reference, including wrapping and negative division. */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "sync_synth.h"
static uint32_t seed = 213;
static uint32_t next(void) { seed = seed * 1664525u + 1013904223u; return seed; }
int main(void)
{
    uint8_t cells[256], dst[2560];
    for (unsigned p = 0; p < 256; ++p) cells[p] = (uint8_t)p;
    uint64_t compared = 0;
    for (unsigned trial = 0; trial < 10000; ++trial) {
        uint32_t n = 1 + next() % sizeof(dst), n1 = next() % (n + 1);
        uint16_t initial = (uint16_t)next();
        int32_t residual = (int32_t)(next() & 65535u) - 32768;
        int32_t step1 = (int32_t)(next() & 65535u) - 32768;
        int32_t step2 = (int32_t)(next() & 65535u) - 32768;
        assert(sync_synth_generate(dst, sizeof(dst), cells, initial, residual, n1, step1, n - n1, step2));
        int64_t acc = initial;
        for (uint32_t k = 0; k < n; ++k) {
            acc += k < n1 ? step1 : step2;
            int64_t phase = acc + (int64_t)residual * (k + 1) / n;
            assert(dst[k] == (uint8_t)((uint64_t)(phase + 128) >> 8));
            ++compared;
        }
    }
    memset(dst, 0xa5, sizeof(dst));
    assert(!sync_synth_generate(dst, 10, cells, 0, 0, 11, 1, 0, 0));
    assert(!sync_synth_generate(dst, sizeof(dst), cells, 0, 0, 65536, 0, 0, 0));
    assert(!sync_synth_generate(dst, sizeof(dst), cells, 0, 32768, 1, 0, 0, 0));
    assert(!sync_synth_generate(dst, sizeof(dst), cells, 0, 0, 0, 0, 0, 0));
    for (unsigned k = 0; k < sizeof(dst); ++k) assert(dst[k] == 0xa5);
    printf("PASS: %llu phase codes match archived synthesis; invalid intervals write nothing\n",
           (unsigned long long)compared);
    return 0;
}

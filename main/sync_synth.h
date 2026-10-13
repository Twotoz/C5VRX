/* C5VRX by Twotoz/contributors: bounded phase-continuous sync synthesis.
 * Extends legacy/c5vrx4/sync_flywheel.c synth(). Its per-sample signed
 * 64-bit residual multiply/divide becomes an exact integer remainder walk.
 * This kernel does not select repair timing or authorize shared-DMA writes. */
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef struct {
    uint32_t phase, error, remainder, count;
    int32_t quotient, sign;
} sync_synth_t;

static inline bool sync_synth_init(sync_synth_t *s, uint16_t phase, int32_t residual, uint32_t count)
{
    if (!s || !count || count > 65535u || residual < -32768 || residual > 32767) return false;
    int32_t rem = residual % (int32_t)count;
    *s = (sync_synth_t){phase, 0, (uint32_t)(rem < 0 ? -rem : rem), count,
                        residual / (int32_t)count, rem < 0 ? -1 : 1};
    return true;
}

/* Exact modulo-65536 phase of acc + residual*(k+1)/count (C truncates toward
 * zero), including negative residuals and whole phase turns. No division or
 * 64-bit arithmetic per code; two divisions occur only at initialization. */
static inline uint8_t sync_synth_next(sync_synth_t *s, int32_t step)
{
    int32_t increment = step + s->quotient;
    s->error += s->remainder;
    if (s->error >= s->count) { s->error -= s->count; increment += s->sign; }
    s->phase = (s->phase + (uint32_t)increment) & 65535u;
    return (uint8_t)((s->phase + 128u) >> 8);
}

/* Emit into owned scratch storage. Shared RX/TX ring writes require a
 * separate lifetime/observer guard. Entire intervals are admitted or refused. */
static inline bool sync_synth_generate(uint8_t *dst, size_t capacity, const uint8_t cells[256],
    uint16_t phase, int32_t residual, uint32_t n1, int32_t step1, uint32_t n2, int32_t step2)
{
    if (!dst || !cells || n1 > 65535u || n2 > 65535u - n1 || n1 + n2 > capacity ||
        step1 < -32768 || step1 > 32767 || step2 < -32768 || step2 > 32767) return false;
    sync_synth_t s;
    if (!sync_synth_init(&s, phase, residual, n1 + n2)) return false;
    for (uint32_t k = 0; k < n1; ++k) dst[k] = cells[sync_synth_next(&s, step1)];
    for (uint32_t k = 0; k < n2; ++k) dst[n1 + k] = cells[sync_synth_next(&s, step2)];
    return true;
}

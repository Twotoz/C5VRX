/* SPDX-License-Identifier: GPL-3.0-only */

#include "c5vrx_clock_bridge.h"

#include <string.h>

/* All positions are free-running absolute sample counters (uint64); the
 * ring storage is addressed with & (capacity-1). phase_q32 carries the
 * fractional offset of the next output between read_pos and its
 * successor. The release pointer (tail) never passes the interpolation
 * history window, so cubic taps are always valid. */

#define PHASE_ONE (1ull << 32)

static uint32_t occupancy_of(const c5vrx_clock_bridge_t *b)
{
    return (uint32_t)(b->head - b->tail);
}

static void note_occupancy(c5vrx_clock_bridge_t *b)
{
    const uint32_t used = occupancy_of(b);
    if (!b->primed || used < b->occupancy_min) b->occupancy_min = used;
    if (!b->primed || used > b->occupancy_max) b->occupancy_max = used;
    b->primed = true;
}

static int tap(const c5vrx_clock_bridge_t *b, uint64_t index)
{
    return b->cfg.storage[index & (b->cfg.capacity - 1u)];
}

bool c5vrx_clock_bridge_init(c5vrx_clock_bridge_t *bridge,
                             const c5vrx_clock_bridge_config_t *config)
{
    if (!bridge || !config || !config->storage || !config->capacity ||
        config->capacity & (config->capacity - 1u) ||
        config->capacity < 8u ||
        !config->input_rate || !config->output_rate ||
        config->method > C5VRX_BRIDGE_CUBIC ||
        config->input_rate > 1000000000u ||
        config->output_rate > 1000000000u) {
        return false;
    }
    memset(bridge, 0, sizeof(*bridge));
    bridge->cfg = *config;
    return true;
}

void c5vrx_clock_bridge_push(c5vrx_clock_bridge_t *bridge,
                             const uint8_t *samples,
                             size_t count,
                             bool at_safe_boundary)
{
    if (!bridge || !bridge->cfg.storage || (!samples && count)) return;
    (void)at_safe_boundary; /* Policy note: overload always supersedes the
                             * oldest unread sample so latency stays
                             * bounded; discrete boundary-only correction
                             * remains gated on hardware measurement. */
    for (size_t i = 0; i < count; ++i) {
        if (occupancy_of(bridge) == bridge->cfg.capacity) {
            /* Full: supersede the oldest sample. If it was still
             * unread, the stream skips it - a counted, single-sample
             * discontinuity instead of unbounded latency growth. */
            ++bridge->dropped_samples;
            if ((uint64_t)bridge->tail <= bridge->read_pos - 1u &&
                bridge->read_pos > 0u) {
                ++bridge->read_pos;
                bridge->phase_q32 = 0u;
            }
            ++bridge->tail;
        }
        bridge->cfg.storage[bridge->head++ & (bridge->cfg.capacity - 1u)] =
            samples[i];
    }
    note_occupancy(bridge);
}

size_t c5vrx_clock_bridge_pull(c5vrx_clock_bridge_t *bridge,
                               uint8_t *out,
                               size_t limit)
{
    if (!bridge || !bridge->cfg.storage || !out || !limit) return 0u;
    const bool cubic = bridge->cfg.method == C5VRX_BRIDGE_CUBIC;
    const uint64_t step = ((uint64_t)bridge->cfg.input_rate << 32) /
                          bridge->cfg.output_rate;
    size_t produced = 0u;

    while (produced < limit) {
        const uint32_t int_part = (uint32_t)(bridge->phase_q32 >> 32);
        const uint32_t frac = (uint32_t)(bridge->phase_q32 & 0xFFFFFFFFu);
        const uint64_t p = bridge->read_pos + int_part;
        /* Samples required ahead of p: linear reads p, p+1; cubic also
         * p-1 (kept by the release policy) and p+2. */
        if (bridge->head < p + (cubic ? 3u : 2u)) break;
        if (p < bridge->tail) break;

        int value;
        if (cubic) {
            /* Catmull-Rom, coefficients in exact Q32 fixed point:
             * y = 0.5*[ 2s1 + (s2-s0)t + (2s0-5s1+4s2-s3)t^2
             *           + (3s1-s0-3s2+s3)t^3 ] */
            const int s1 = tap(bridge, p);
            /* History tap falls back to s1 before any history exists
             * (first outputs, or right after an overload skip). */
            const int s0 = p > (uint64_t)bridge->tail ?
                tap(bridge, p - 1) : s1;
            const int s2 = tap(bridge, p + 1);
            const int s3 = tap(bridge, p + 2);
            const uint64_t t2 = ((uint64_t)frac * frac) >> 32;
            const uint64_t t3 = (t2 * (uint64_t)frac) >> 32;
            const int64_t acc =
                (((int64_t)s1 << 33) +
                 (int64_t)(s2 - s0) * frac +
                 (int64_t)(2 * s0 - 5 * s1 + 4 * s2 - s3) * t2 +
                 (int64_t)(3 * s1 - s0 - 3 * s2 + s3) * t3 +
                 (1ll << 31)) >> 33;
            value = acc < 0 ? 0 : (acc > 63 ? 63 : (int)acc);
        } else {
            const int s1 = tap(bridge, p);
            const int s2 = tap(bridge, p + 1);
            value = s1 + (int)(((int64_t)(s2 - s1) * frac) >> 32);
        }
        out[produced++] = (uint8_t)value;

        bridge->phase_q32 += step;
        bridge->read_pos += bridge->phase_q32 >> 32;
        bridge->phase_q32 &= PHASE_ONE - 1u;
    }

    /* Release consumed history, retaining one cubic history tap. */
    const uint64_t releasable = bridge->read_pos -
        (produced ? (cubic ? 1u : 0u) : 0u);
    if ((uint64_t)bridge->tail < releasable) {
        bridge->tail = (uint32_t)releasable;
    }
    if (produced == 0u) ++bridge->underflows;
    note_occupancy(bridge);
    return produced;
}

uint32_t c5vrx_clock_bridge_occupancy(const c5vrx_clock_bridge_t *bridge)
{
    return bridge ? occupancy_of(bridge) : 0u;
}

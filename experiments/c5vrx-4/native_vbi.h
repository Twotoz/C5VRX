/* C5VRX by Twotoz and contributors. Analog-video scheduling for the opt-in
 * native AGC gate. Pure logic: no register, DMA or timer access.
 *
 * The C5 native AGC is an 802.11 packet AGC: on a continuous FPV carrier it
 * re-acquires from its start gain and traps a different gain each time
 * (docs/native-agc-v2.md). With the 1 ms periodic pace every release lands
 * at a random picture position: a 2-3 us saturated dash plus a new noise
 * texture every ~16 lines. This module lets releases follow the analog
 * signal instead:
 *  - locate the vertical broad-sync pulses in raw Q4/I4 windows and lock to
 *    the PAL (20.000 ms) or NTSC (16.683 ms) field period;
 *  - schedule a release only in the blank VBI lines after the vertical
 *    sync/equalizing pulses, never inside them;
 *  - release only when the held level left a hysteresis band around the
 *    level the hardware itself chose at its last acquisition, or the carrier
 *    was lost.
 * Hardware still selects every gain; firmware never computes or forces an
 * index. Without field lock the caller keeps the periodic pace fallback. */
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define NV_CHUNK_BYTES       40u    /* 1 us at 40 MS/s: 20 endpoint steps */
#define NV_MAX_CHUNKS        128u
#define NV_MIN_SPAN          128    /* chunk units (3.9 kHz): ~0.5 MHz sync-blank */
#define NV_BROAD_RUN         15u    /* us: broad pulse ~27 us; H/equalizing <=4.7 us */
#define NV_BROAD_MIN         20u    /* us of broad-pulse sync inside one window */
#define NV_MATCH_US          200u
#define NV_LOCK_HITS         3u
#define NV_LOCK_TIMEOUT_US   1000000u
/* Detected point (centroid of a broad-pulse window) ~= broad start + 85 us.
 * Release ~700 us after broad start: PAL blank lines 6-22 are 320..1408 us
 * after broad start, NTSC lines 10-21 are 381..1144 us after it. */
#define NV_RELEASE_AFTER_US  615u
#define NV_SETTLE_US         2000u

typedef struct {
    bool valid;      /* enough sync-to-blank structure for a decision */
    bool broad;      /* window dominated by vertical broad-sync pulses */
    unsigned low_pm; /* chunks inside broad-length sync runs, per mille */
    unsigned centroid_bytes; /* mean raw-byte offset of those chunks */
    int span;        /* p97 - p3 of 1-us mean frequency, chunk units */
} nv_window_t;

/* raw: consecutive 40 MS/s ring bytes; endpoints are the odd bytes. */
nv_window_t nv_window_analyze(const uint8_t *raw, size_t n, const uint8_t phase[256]);

enum { NV_PAL = 0, NV_NTSC = 1, NV_NONE = -1 };

typedef struct {
    int64_t ref_ns[2];
    uint64_t last_hit_us[2];
    uint8_t hits[2], misses[2];
    bool valid[2];
    uint32_t events, matches;
} nv_lock_t;

void nv_lock_reset(nv_lock_t *lock);
/* A broad-pulse detection at event_us (local monotonic microseconds). */
void nv_lock_feed(nv_lock_t *lock, uint64_t event_us);
/* NV_PAL, NV_NTSC or NV_NONE; expires hypotheses without recent hits. */
int nv_lock_standard(nv_lock_t *lock, uint64_t now_us);
/* Earliest VBI release time >= not_before_us. False without lock. */
bool nv_lock_next_release(nv_lock_t *lock, uint64_t now_us,
                          uint64_t not_before_us, uint64_t *release_us);

typedef struct {
    uint8_t p50, p95;
    uint16_t clip_pm, origin_pm;
    uint8_t coherence;
} nv_level_t;

typedef struct {
    uint64_t settle_until_us;
    uint32_t base_sum;
    uint16_t base_clip_pm;
    uint8_t base_p50, base_count, bad, severe;
    uint32_t demands;
} nv_demand_t;

void nv_demand_reset(nv_demand_t *demand);
/* Call when the gate opened at release_us: ignore the acquisition, then
 * learn the newly trapped hardware level as the hysteresis centre. */
void nv_demand_released(nv_demand_t *demand, uint64_t release_us);
enum { NV_HOLD = 0, NV_VBI = 1, NV_NOW = 2 };
/* NV_VBI: re-acquire in the next blank VBI lines (carrier lost, overload, or
 * the held level moved more than 3 dB from the learned hardware level), each
 * for two consecutive windows. NV_NOW: severe saturation; the held picture is
 * already destroyed, so do not wait for the VBI (PR154 overload rearm). */
int nv_demand_update(nv_demand_t *demand, const nv_level_t *level, uint64_t now_us);

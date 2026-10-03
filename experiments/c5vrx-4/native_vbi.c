/* C5VRX by Twotoz and contributors. See native_vbi.h. */
#include "native_vbi.h"
#include <string.h>

static const int64_t s_period_ns[2] = {20000000, 16683333};

nv_window_t nv_window_analyze(const uint8_t *raw, size_t n, const uint8_t phase[256])
{
    nv_window_t out;
    memset(&out, 0, sizeof(out));
    size_t chunks = n / NV_CHUNK_BYTES;
    if (!raw || !phase || chunks < 32u || chunks > NV_MAX_CHUNKS) return out;
    /* 1-us mean frequency: sum of 20 wrapped 50-ns Phase8 endpoint steps.
     * Negative sync is the lowest frequency (cvbs_monitor.c). */
    int16_t value[NV_MAX_CHUNKS], sorted[NV_MAX_CHUNKS];
    uint8_t previous = phase[raw[1]];
    for (size_t c = 0; c < chunks; ++c) {
        int sum = 0;
        for (size_t k = c * NV_CHUNK_BYTES + 1u; k < (c + 1u) * NV_CHUNK_BYTES; k += 2u) {
            uint8_t p = phase[raw[k]];
            sum += (int8_t)(uint8_t)(p - previous);
            previous = p;
        }
        value[c] = (int16_t)sum;
        size_t j = c;
        for (; j && sorted[j - 1u] > sum; --j) sorted[j] = sorted[j - 1u];
        sorted[j] = (int16_t)sum;
    }
    size_t tail = chunks * 3u / 100u;
    int low = sorted[tail], high = sorted[chunks - 1u - tail];
    out.span = high - low;
    if (out.span < NV_MIN_SPAN) return out;
    /* Narrow band at the sync tip: blank/black sits >=30 % of the
     * sync-to-white span above it, so neither a busy nor a dark picture
     * with a small bright object reaches it. Amplitude alone still cannot
     * tell vertical from horizontal sync; duration does. Ordinary and
     * equalizing pulses last 4.7/2.35 us, vertical broad pulses ~27 us. */
    int threshold = low + out.span / 6;
    unsigned count = 0;
    uint32_t centre = 0;
    for (size_t c = 0; c < chunks;) {
        if (value[c] >= threshold) { ++c; continue; }
        /* One noisy chunk inside a pulse does not split it. */
        size_t end = c + 1u, last = c;
        while (end < chunks && (value[end] < threshold ||
               (end + 1u < chunks && value[end + 1u] < threshold))) {
            if (value[end] < threshold) last = end;
            ++end;
        }
        size_t length = last + 1u - c;
        if (length >= NV_BROAD_RUN) {
            count += (unsigned)length;
            for (size_t j = c; j <= last; ++j)
                centre += (uint32_t)(j * NV_CHUNK_BYTES + NV_CHUNK_BYTES / 2u);
        }
        c = last + 1u;
    }
    out.valid = true;
    out.low_pm = (unsigned)(count * 1000u / chunks);
    out.centroid_bytes = count ? centre / count : 0u;
    out.broad = count >= NV_BROAD_MIN;
    return out;
}

void nv_lock_reset(nv_lock_t *lock)
{
    memset(lock, 0, sizeof(*lock));
}

void nv_lock_feed(nv_lock_t *lock, uint64_t event_us)
{
    int64_t t = (int64_t)event_us * 1000;
    ++lock->events;
    for (unsigned h = 0; h < 2u; ++h) {
        int64_t period = s_period_ns[h];
        if (lock->valid[h]) {
            int64_t d = t - lock->ref_ns[h];
            int64_t k = (d >= 0 ? d + period / 2 : d - period / 2) / period;
            int64_t r = d - k * period;
            if (k >= -100 && k <= 100 &&
                r >= -(int64_t)NV_MATCH_US * 1000 && r <= (int64_t)NV_MATCH_US * 1000) {
                /* Follow slow camera/VTX clock offsets; damp window jitter. */
                lock->ref_ns[h] += k * period + r / 4;
                if (lock->hits[h] < 255u) ++lock->hits[h];
                lock->misses[h] = 0;
                lock->last_hit_us[h] = event_us;
                ++lock->matches;
                continue;
            }
            /* A locked hypothesis tolerates isolated false detections. */
            if (lock->hits[h] >= NV_LOCK_HITS && ++lock->misses[h] < 3u) continue;
        }
        lock->valid[h] = true;
        lock->ref_ns[h] = t;
        lock->hits[h] = 1;
        lock->misses[h] = 0;
        lock->last_hit_us[h] = event_us;
    }
}

int nv_lock_standard(nv_lock_t *lock, uint64_t now_us)
{
    for (unsigned h = 0; h < 2u; ++h) {
        if (lock->valid[h] && now_us > lock->last_hit_us[h] &&
            now_us - lock->last_hit_us[h] > NV_LOCK_TIMEOUT_US) {
            lock->valid[h] = false;
            lock->hits[h] = 0;
            lock->misses[h] = 0;
        }
    }
    bool pal = lock->valid[NV_PAL] && lock->hits[NV_PAL] >= NV_LOCK_HITS;
    bool ntsc = lock->valid[NV_NTSC] && lock->hits[NV_NTSC] >= NV_LOCK_HITS;
    /* 5 PAL fields ~= 6 NTSC fields: the true standard keeps more hits. */
    if (pal && (!ntsc || lock->hits[NV_PAL] > lock->hits[NV_NTSC])) return NV_PAL;
    if (ntsc && (!pal || lock->hits[NV_NTSC] > lock->hits[NV_PAL])) return NV_NTSC;
    return NV_NONE;
}

bool nv_lock_next_release(nv_lock_t *lock, uint64_t now_us,
                          uint64_t not_before_us, uint64_t *release_us)
{
    int h = nv_lock_standard(lock, now_us);
    if (h == NV_NONE || !release_us) return false;
    int64_t period = s_period_ns[h];
    int64_t base = lock->ref_ns[h] + (int64_t)NV_RELEASE_AFTER_US * 1000;
    int64_t wanted = (int64_t)not_before_us * 1000;
    int64_t k = wanted > base ? (wanted - base + period - 1) / period : 0;
    int64_t release = base + k * period;
    if (release < 0) return false;
    *release_us = (uint64_t)(release / 1000);
    return true;
}

void nv_demand_reset(nv_demand_t *demand)
{
    uint32_t demands = demand->demands;
    memset(demand, 0, sizeof(*demand));
    demand->demands = demands;
}

void nv_demand_released(nv_demand_t *demand, uint64_t release_us)
{
    nv_demand_reset(demand);
    demand->settle_until_us = release_us + NV_SETTLE_US;
}

static bool vote(nv_demand_t *demand, bool bad)
{
    if (!bad) { demand->bad = 0; return false; }
    if (++demand->bad < 2u) return false;
    demand->bad = 0;
    ++demand->demands;
    return true;
}

int nv_demand_update(nv_demand_t *demand, const nv_level_t *level, uint64_t now_us)
{
    if (!level || now_us < demand->settle_until_us) return NV_HOLD;
    /* Direct Gain V3's saturation definition. */
    if (level->clip_pm >= 100u || level->p95 >= 95u) {
        if (++demand->severe >= 2u) {
            demand->severe = 0;
            demand->bad = 0;
            ++demand->demands;
            return NV_NOW;
        }
    } else {
        demand->severe = 0;
    }
    /* Same no-carrier definition as Direct Gain V3: the hardware must be
     * allowed to return to its high-gain idle state. */
    bool lost = (level->p50 <= 4u && level->origin_pm >= 650u) || level->coherence < 20u;
    if (lost) return vote(demand, true) ? NV_VBI : NV_HOLD;
    if (demand->base_count < 3u) {
        /* The hardware's own trapped level is the centre; firmware never
         * imposes a target level or a gain index. */
        demand->base_sum += level->p50;
        if (level->clip_pm > demand->base_clip_pm) demand->base_clip_pm = level->clip_pm;
        if (++demand->base_count == 3u)
            demand->base_p50 = (uint8_t)((demand->base_sum + 1u) / 3u);
        demand->bad = 0;
        return NV_HOLD;
    }
    /* p50 is power (r^2): x2 is +3 dB. */
    bool high = level->clip_pm >= demand->base_clip_pm + 20u || level->p95 >= 80u ||
                level->p50 > 2u * demand->base_p50;
    bool low = 2u * level->p50 < demand->base_p50;
    return vote(demand, high || low) ? NV_VBI : NV_HOLD;
}

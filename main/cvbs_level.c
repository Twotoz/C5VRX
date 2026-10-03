/* C5VRX by Twotoz and contributors. Voltage-ordered slew adapted from
 * experiments/c5vrx-4/cvbs_level.c at PR #162 (f96ad94).
 * Nominal loaded ladder from its cvbs_tables.h, not scope calibration. */
#include "cvbs_level.h"
#include <string.h>
const uint32_t cvbs_dac_uv[64] = {0,15183,31923,47106,62250,77433,94173,109356,124500,139683,156423,171606,186750,201933,218673,233856,264894,280077,296817,312000,327144,342327,359067,374250,389394,404577,421317,436500,451644,466827,483567,498750,518750,533933,550673,565856,581000,596183,612923,628106,643250,658433,675173,690356,705500,720683,737423,752606,783644,798827,815567,830750,845894,861077,877817,893000,908144,923327,940067,955250,970394,985577,1002317,1017500};
static int absolute(int x) { return x < 0 ? -x : x; }
static uint8_t nearest(int64_t uv)
{
    unsigned best = 0;
    int64_t distance = INT64_MAX;
    for (unsigned c = 0; c < 64; ++c) {
        int64_t e = (int64_t)cvbs_dac_uv[c] - uv;
        if (e < 0) e = -e;
        if (e < distance) { distance = e; best = c; }
    }
    return (uint8_t)best;
}
void cvbs_level_init(cvbs_level_t *s)
{
    memset(s, 0, sizeof(*s));
    for (unsigned c = 0; c < 64; ++c) s->codes[c] = (uint8_t)c;
}
uint8_t cvbs_level_slew(uint8_t current, uint8_t target, const uint32_t uv[64])
{
    if (uv[current] == uv[target]) return target;
    uint8_t next = target;
    for (unsigned c = 0; c < 64; ++c) {
        if (uv[target] > uv[current]) {
            if (uv[c] > uv[current] && uv[c] < uv[next]) next = (uint8_t)c;
        } else if (uv[c] < uv[current] && uv[c] > uv[next]) next = (uint8_t)c;
    }
    return next;
}
bool cvbs_level_observe(cvbs_level_t *s, const cvbs_level_stats_t *v,
                        bool fresh, uint32_t context, uint64_t now)
{
    if (!s->have_context || s->context != context) {
        s->have_context = true; s->context = context;
        s->good = 0; s->observed = false;
    }
    bool ordered = !s->observed || now > s->observed_us;
    if (s->observed && (!ordered || now - s->observed_us > 200000)) s->good = 0;
    s->observed = true; s->observed_us = now;
    int span = v ? v->blank_uv - v->sync_uv : 0;
    bool valid = ordered && fresh && v && v->valid && v->repeated &&
        v->period >= 1264 && v->period <= 1288 && span >= 100000 && span <= 600000 &&
        v->sync_mad_uv <= 35000 && v->blank_mad_uv <= 35000 &&
        v->origin_pm <= 350 && v->clip_pm <= 200;
    if (!valid) { s->good = 0; ++s->refusals; return false; }
    if (s->good && (absolute(v->blank_uv - s->blank) > 35000 ||
                   absolute(span - s->span) > 35000)) s->good = 0;
    s->blank = v->blank_uv; s->span = span;
    if (s->good < 3) ++s->good;
    if (s->good < 3 || (s->updates &&
        (now <= s->last_us || now - s->last_us < 100000))) return false;
    bool changed = false;
    for (unsigned c = 0; c < 64; ++c) {
        int64_t uv = 300000 + ((int64_t)cvbs_dac_uv[c] - s->blank) * 300000 / span;
        uint8_t next = cvbs_level_slew(s->codes[c], nearest(uv), cvbs_dac_uv);
        changed |= next != s->codes[c]; s->codes[c] = next;
    }
    if (changed) { ++s->updates; s->last_us = now; }
    return changed;
}
static int median(const int *v, size_t n)
{
    int a[64];
    if (!n || n > 64) return 0;
    for (size_t i = 0; i < n; ++i) {
        a[i] = v[i];
        for (size_t j = i; j && a[j] < a[j-1]; --j) {
            int t = a[j]; a[j] = a[j-1]; a[j-1] = t;
        }
    }
    return a[n/2];
}
static int mad(const int *v, size_t n, int centre)
{
    int a[64];
    for (size_t i = 0; i < n; ++i) a[i] = absolute(v[i] - centre);
    return median(a, n);
}
static bool low_at(const uint8_t *codes, size_t k, size_t n, int threshold)
{
    if (k < 2 || k + 2 >= n) return false;
    unsigned low = 0;
    for (size_t j = k-2; j <= k+2; ++j) low += (int)cvbs_dac_uv[codes[j]] < threshold;
    return low >= 3;
}
void cvbs_level_analyze(uint8_t *raw, size_t n, const uint16_t lut[1024],
                        cvbs_level_stats_t *out)
{
    memset(out, 0, sizeof(*out));
    if (!raw || !lut || n < 6000 || n > 8192 || (n & 1u)) return;
    unsigned hist[64] = {0}, origin = 0, clip = 0;
    unsigned prev = (lut[raw[1]] >> 8) & 31u;
    size_t count = 0;
    for (size_t k = 3; k < n; k += 2) {
        uint8_t r = raw[k];
        int i = r >> 4, q = r & 15;
        if (i >= 8) i -= 16;
        if (q >= 8) q -= 16;
        int ci = 2*i+1, cq = 2*q+1;
        origin += ci*ci+cq*cq <= 16;
        clip += i == -8 || i == 7 || q == -8 || q == 7;
        unsigned current = (lut[r] >> 8) & 31u;
        unsigned code = lut[(prev << 5) | current] & 63u;
        prev = current;
        raw[count++] = (uint8_t)code; ++hist[code];
    }
    out->origin_pm = origin*1000/(unsigned)count;
    out->clip_pm = clip*1000/(unsigned)count;
    unsigned cumulative = 0;
    int low = 0, centre = 0;
    bool have_low = false;
    for (unsigned c = 0; c < 64; ++c) {
        cumulative += hist[c];
        if (!have_low && cumulative >= count*3/100) { low = (int)cvbs_dac_uv[c]; have_low = true; }
        if (cumulative >= count/2) { centre = (int)cvbs_dac_uv[c]; break; }
    }
    if (centre - low < 100000) return;
    int threshold = (low + centre)/2;
    size_t previous = 0;
    bool have_previous = false;
    for (size_t k = 3; k+120 < count; ++k) {
        if (!low_at(raw, k, count, threshold) || low_at(raw, k-1, count, threshold)) continue;
        size_t end = k;
        while (end < count && low_at(raw, end, count, threshold)) ++end;
        size_t width = end-k;
        if (width < 78 || width > 114 || end+8 >= count) { k = end; continue; }
        int pulse[64], blank[4];
        size_t from = k+width/4, points = end-width/4-from;
        for (size_t j = 0; j < points; ++j) pulse[j] = (int)cvbs_dac_uv[raw[from+j]];
        /* 0.15..0.30 us after sync, before chroma burst. */
        for (size_t j = 0; j < 4; ++j) blank[j] = (int)cvbs_dac_uv[raw[end+3+j]];
        int s = median(pulse, points), b = median(blank, 4);
        int sm = mad(pulse, points, s), bm = mad(blank, 4, b);
        /* Refuse clipped sync: a transfer LUT cannot recover lost depth. */
        if (s <= 0 || b-s < 100000 || b-s > 600000 || sm > 35000 || bm > 35000) { k = end; continue; }
        if (have_previous && k-previous >= 1264 && k-previous <= 1288) {
            ++out->repeated; out->period = (unsigned)(k-previous);
            out->sync_uv = s; out->blank_uv = b;
            out->sync_mad_uv = sm; out->blank_mad_uv = bm; out->valid = true;
        }
        previous = k; have_previous = true; k = end;
    }
}

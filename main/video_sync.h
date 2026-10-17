/* C5VRX by Twotoz/contributors: sparse read-only H-sync presence watchdog.
 * Extends afc2's plateau/porch/line-period proof. No burst correlations or
 * frequency estimation. Average eight unwrapped 25 ns adjacent phase steps
 * into each 200 ns bin. An 8-sample endpoint would alias wide-deviation FM.
 * The caller serializes access; this is not a vertical-sync/luma detector. */
#pragma once
#include "afc.h"
#include <stdlib.h>
#define VIDEO_SYNC_BYTES 8190u
static inline bool video_sync_present(const uint8_t *raw, size_t bytes, const uint8_t lut[256])
{
    if (!raw || !lut || bytes != VIDEO_SYNC_BYTES) return false;
    static int8_t frequency[VIDEO_SYNC_BYTES / 8u];
    int steps[3] = {0}, means[2] = {0}, sum = 0, sum2 = 0, used = 0;
    bool valid[3] = {false};
    int lo = 127, hi = -127;
    unsigned count = (unsigned)((bytes - 1u) / 8u);
    for (unsigned k = 0; k < count; ++k) {
        unsigned slot = k % 3u;
        if (valid[slot]) { sum -= steps[slot]; --used; }
        int total = 0, good = 0;
        for (unsigned j = 0; j < 8u; ++j) {
            unsigned at = k * 8u + j;
            int delta = afc2_delta(raw[at], raw[at + 1u], lut);
            if (delta != INT16_MIN) { total += delta; ++good; }
        }
        int d = good >= 4 ? total / good : INT16_MIN;
        valid[slot] = d != INT16_MIN;
        steps[slot] = valid[slot] ? d : 0;
        if (valid[slot]) { sum += d; ++used; }
        int v = used ? sum * 2 / used : 0;
        sum2 += v - means[k & 1u]; means[k & 1u] = v;
        v = sum2 / 2;
        v = v > 127 ? 127 : v < -127 ? -127 : v;
        frequency[k] = (int8_t)v;
        if (k >= 8u) { if (v < lo) lo = v; if (v > hi) hi = v; }
    }
    if (hi - lo < 4) return false;
    for (unsigned p = 0; p < 2u; ++p) {
        int pol = p ? 1 : -1;
        unsigned length = 0, previous = 0;
        for (unsigned k = 8u; k < count; ++k) {
            if (afc2_in_sync_value(frequency[k], pol, lo, hi)) { ++length; continue; }
            unsigned start = k - length;
            if (length >= 17u && length <= 29u && start >= 12u && k + 3u < count) {
                int front = frequency[start - 4u], back = frequency[k + 3u];
                int tip = frequency[(start + k) / 2u];
                int sf = pol < 0 ? front - tip : tip - front;
                int sb = pol < 0 ? back - tip : tip - back;
                int depth = (sf + sb) / 2, mismatch = abs(front - back);
                if (sf > 0 && sb > 0 && depth * 8 >= hi - lo && mismatch * 2 <= depth) {
                    unsigned period = start - previous;
                    if (previous && period >= 316u && period <= 322u) return true;
                    previous = start;
                }
            }
            length = 0;
        }
    }
    return false;
}

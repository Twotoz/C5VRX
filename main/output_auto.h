/* C5VRX-5 by Twotoz and the C5VRX contributors: AUTO OUTPUT, the video level
 * a normal analog receiver delivers, set from the received signal.
 *
 * Extends issue #200 (manual OUTPUT) and its study research/output_level.py:
 * at OUTPUT 1.0x the DAC maps -6..+9 MHz onto its 64 codes, so a VTX of
 * deviation 0.69 uses ~19 of them and its sync is ~5.5 codes deep (model:
 * 0.060 V into 75 ohm where CVBS has 0.286 V; deviation 1.0: 0.089 V). A
 * goggle's sync slicer then works on a third of the level it was made for.
 *
 * Invariant (operator, PR #302 review): the output reference levels are the
 * same whatever the RF level, the gain row, the VTX deviation or the decoder.
 * The picture may get noisier; the levels must not follow the reception.
 *   sync tip   DAC code OUTPUT_AFFINE_TIP (20);
 *   blanking   OA_SYNC_X4 / 4 = 11.5 codes above it (code 31.5);
 *   white      what the VTX sends above blanking, at the same gain: code 60
 *              for the standard's 100 IRE (sync 40 of 140).
 * So the gain comes from the sync depth alone, not from the picture: scene
 * content cannot move the level. The DAC bounds it: a VTX with more picture
 * swing than the standard (issue #200 levels) would put its white past code
 * 63, and only then the observed peak white lowers the gain (OA_WHITE_OVER
 * codes of tolerance; the peak is kept while the sync depth stays the same,
 * so it never hunts with the scene).
 * Excursions past the DAC saturate, as in a video amplifier.
 *
 * Evidence per control window (control.c):
 *   tip_x4,         sync level and sync tip to back porch from the AFC's
 *   depth_x4        burst/timing-confirmed levels (afc2_measure) on the
 *                   decoders' frequency map (-6..+9 MHz on 63 codes), DAC
 *                   code x4. Host model: within 0.3 codes of the low-passed
 *                   decoded sync level for U80P64-R1 and U50P64-R1, and the
 *                   same from C/N 30 dB down to 4 dB. The 3rd percentile of
 *                   the decoded video (picture_sync.h tip_x4) sat ~1 code
 *                   under the sync level for U80P64-R1 and lower in noise;
 *   top_x4          peak white (99.5th percentile) of the unscaled live
 *                   decoder, DAC code x4 (picture_sync.h).
 * A window counts only with confirmed syncs, at least 80 % of its sync
 * pulses present and levels apart from the bulk; every other window is a
 * miss (output_auto_miss). OA_BATCH windows give medians; a batch whose depth
 * or tip scatter is rejected. A batch with at most OA_STRONG_MISSES misses
 * is strong, any other is weak: its peak white carries noise (host model:
 * up to ~15 % more span at C/N 4 dB), so it is kept apart and only counts
 * past OA_WEAK_OVER more codes, while no strong batch has given one.
 *
 * Changes cost one ~5 ms verified restart, so they are rare by rule:
 *   first setting   two agreeing strong batches, or four weak ones (a start
 *                   at the range edge still gets the level; it is then
 *                   provisional);
 *   hold            a weak batch never changes a setting: fading, noise, FM
 *                   clicks and a lost sync leave the last valid level alone;
 *   refine          a provisional setting (weak evidence, or kept across a
 *                   decoder change) is corrected by two agreeing strong
 *                   batches from one gain step or one tip code on;
 *   down / tip      two agreeing strong batches when the gain must fall by
 *                   >= 0.25x or the tip moved >= 3 codes (clipping is at
 *                   stake, or the AFC stepped);
 *   up              four agreeing strong batches, >= 0.25x more and 30 s
 *                   since the last change.
 * Between those thresholds nothing moves (hysteresis): no level pumping.
 * A channel change or a manual OUTPUT resets all of it and the caller
 * returns to the manual level; a decoder change keeps the level (the sync
 * depth is the AFC's, the same for every decoder) as provisional. Host
 * tests: test/test_output_auto.c and the acceptance run
 * research/output_consistency.py. No voltage is claimed: the DAC's loaded
 * span is not calibrated (research/output_level.py), only its use of the
 * range. */
#pragma once
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include "output_level.h"

#define OA_BATCH          16u
#define OA_SYNC_X4        46      /* sync depth target, 11.5 codes: 40/140 of tip 20 .. white 60 */
#define OA_WHITE_ROOM     (63 - OUTPUT_AFFINE_TIP)   /* codes above the tip the DAC has */
#define OA_WHITE_OVER     3       /* peak white this far past code 63 before the gain gives way */
#define OA_WEAK_OVER      5       /* and this much further for the noisy peak of a weak batch */
#define OA_GAIN_MIN_Q3    4       /* 0.5x: a wide VTX is brought down to the same levels */
#define OA_GAIN_MAX_Q3    32      /* 4.0x */
#define OA_STEP_Q3        2       /* 0.25x: hysteresis of a confirmed setting */
#define OA_TIP_X4         12      /* 3 source codes */
#define OA_UP_AFTER_US    30000000LL
#define OA_MIN_DEPTH_X4   12      /* 3 codes: under it no sync to reference */
#define OA_STRONG_MISSES  8u      /* a batch of 16 from at most 24 windows */
#define OA_NEED_STRONG    2u
#define OA_NEED_WEAK      4u
#define OA_NEED_UP        4u

typedef struct {
    uint16_t tip[OA_BATCH], top[OA_BATCH], depth[OA_BATCH];
    unsigned n;
    unsigned misses;              /* windows without evidence since this batch began */
    unsigned span_hold_x4;        /* largest tip-to-white span of this context, strong batches only; 0 none yet */
    unsigned span_weak_x4;        /* the same of the weak batches: used with OA_WEAK_OVER until a strong one came */
    unsigned span_depth_x4;       /* the sync depth those spans belong to */
    unsigned word;                /* applied affine word, 0 = the manual level */
    unsigned want;                /* candidate word, 0 none */
    unsigned agree;               /* consecutive batches agreeing with want */
    bool want_weak;               /* a weak batch is among them */
    bool provisional;             /* the applied word awaits strong evidence */
    int64_t applied_us;
    uint32_t batches, weak, rejected, applied;
    uint16_t last_tip_x4, last_depth_x4, last_span_x4;   /* telemetry: the last accepted batch */
} output_auto_t;

static inline void output_auto_reset(output_auto_t *a) { memset(a, 0, sizeof(*a)); }

/* A frequency (kHz from the receiver centre) as an unscaled DAC code x4. */
static inline unsigned output_auto_code_x4(int32_t khz)
{
    const int32_t x4 = (khz + 6000) * 252 / 15000;
    return x4 < 0 ? 0u : x4 > 252 ? 252u : (unsigned)x4;
}

/* Another decoder on the same channel: the level stays, the evidence starts
 * again and two strong batches may correct it. */
static inline void output_auto_requalify(output_auto_t *a)
{
    a->n = 0; a->misses = 0; a->want = 0; a->agree = 0; a->want_weak = false;
    a->span_hold_x4 = 0; a->span_weak_x4 = 0; a->span_depth_x4 = 0;
    if (a->word) a->provisional = true;
}

/* A control window that gave no evidence (weak, fading, no sync, a gain write). */
static inline void output_auto_miss(output_auto_t *a) { if (a->misses < 1000u) ++a->misses; }

static inline unsigned oa_median(uint16_t *v, unsigned n)
{
    for (unsigned i = 1; i < n; ++i)
        for (unsigned j = i; j > 0 && v[j - 1] > v[j]; --j) { const uint16_t t = v[j]; v[j] = v[j - 1]; v[j - 1] = t; }
    return n & 1u ? v[n / 2] : (unsigned)(v[n / 2 - 1] + v[n / 2]) / 2u;
}
static inline unsigned oa_mad(const uint16_t *v, unsigned n, unsigned med)
{
    uint16_t d[OA_BATCH];
    for (unsigned i = 0; i < n; ++i) d[i] = (uint16_t)(v[i] > med ? v[i] - med : med - v[i]);
    return oa_median(d, n);
}

/* The word for measured levels: the sync depth on its target, the tip on
 * OUTPUT_AFFINE_TIP; span_x4 (0: unknown) is the peak white above the tip. */
static inline unsigned output_auto_word_over(unsigned tip_x4, unsigned depth_x4, unsigned span_x4, unsigned over)
{
    if (!depth_x4) depth_x4 = 1u;
    unsigned g = (8u * OA_SYNC_X4 + depth_x4 / 2u) / depth_x4;
    if (g > OA_GAIN_MAX_Q3) g = OA_GAIN_MAX_Q3;
    if (span_x4 * g > 8u * 4u * (OA_WHITE_ROOM + over)) g = 8u * 4u * OA_WHITE_ROOM / span_x4;
    if (g < OA_GAIN_MIN_Q3) g = OA_GAIN_MIN_Q3;
    return output_affine_x4(g, tip_x4);
}
static inline unsigned output_auto_word(unsigned tip_x4, unsigned depth_x4, unsigned span_x4)
{
    return output_auto_word_over(tip_x4, depth_x4, span_x4, OA_WHITE_OVER);
}

static inline bool oa_near(unsigned a, unsigned b)
{
    const int dg = (int)output_affine_gain_q3(a) - (int)output_affine_gain_q3(b);
    const int dt = (int)output_affine_tip_x4(a) - (int)output_affine_tip_x4(b);
    return dg >= -1 && dg <= 1 && dt >= -4 && dt <= 4;
}

/* One qualified window. Returns true when `*word` should be applied now; the
 * caller reports the outcome with output_auto_applied() on success and simply
 * observes on (the candidate stays) when the transport refuses. */
static inline bool output_auto_observe(output_auto_t *a, unsigned tip_x4, unsigned top_x4, unsigned depth_x4,
                                       int64_t now_us, unsigned *word)
{
    if (depth_x4 < OA_MIN_DEPTH_X4 || top_x4 <= tip_x4) { output_auto_miss(a); return false; }
    a->tip[a->n] = (uint16_t)tip_x4; a->top[a->n] = (uint16_t)top_x4; a->depth[a->n] = (uint16_t)depth_x4;
    if (++a->n < OA_BATCH) return false;
    a->n = 0;
    const bool strong = a->misses <= OA_STRONG_MISSES;
    a->misses = 0;
    uint16_t v[OA_BATCH];
    memcpy(v, a->depth, sizeof(v));
    const unsigned depth = oa_median(v, OA_BATCH);
    memcpy(v, a->tip, sizeof(v));
    const unsigned tip = oa_median(v, OA_BATCH);
    memcpy(v, a->top, sizeof(v));
    const unsigned top = oa_median(v, OA_BATCH);
    /* Scatter: 15 % of the depth, 1.5 codes of tip. */
    if (oa_mad(a->depth, OA_BATCH, depth) * 100u > depth * 15u || oa_mad(a->tip, OA_BATCH, tip) > 6u ||
        depth < OA_MIN_DEPTH_X4 || top <= tip) {
        ++a->rejected;
        a->agree = 0; a->want = 0; a->want_weak = false;
        return false;
    }
    ++a->batches;
    if (!strong) ++a->weak;
    /* Peak white, strong batches only, kept for the context: a bright scene
     * returns, and noise is not white. Another sync depth (12 %) is another
     * VTX or another setting of it: its white is measured afresh. */
    if (strong) {
        const unsigned d0 = a->span_depth_x4;
        if (d0 && (depth > d0 ? depth - d0 : d0 - depth) * 8u > d0) { a->span_hold_x4 = 0; a->span_weak_x4 = 0; }
        if (!a->span_hold_x4) a->span_depth_x4 = depth;
    } else if (!a->span_depth_x4) a->span_depth_x4 = depth;
    if (strong && top - tip > a->span_hold_x4) a->span_hold_x4 = top - tip;
    if (!strong && top - tip > a->span_weak_x4) a->span_weak_x4 = top - tip;
    a->last_tip_x4 = (uint16_t)tip; a->last_depth_x4 = (uint16_t)depth; a->last_span_x4 = (uint16_t)a->span_hold_x4;
    /* A weak batch never changes a setting. */
    if (a->word && !strong) { a->agree = 0; a->want = 0; a->want_weak = false; return false; }
    const unsigned cand = strong || a->span_hold_x4 ? output_auto_word(tip, depth, a->span_hold_x4) :
                          output_auto_word_over(tip, depth, a->span_weak_x4, OA_WHITE_OVER + OA_WEAK_OVER);
    const int g = (int)output_affine_gain_q3(cand), t = (int)output_affine_tip_x4(cand);
    bool wanted;
    unsigned need = OA_NEED_STRONG;
    if (!a->word) {
        /* The manual 1.0x level is gain 8 with the tip where it is. */
        const int off = t - 4 * OUTPUT_AFFINE_TIP;
        wanted = g != 8 || off >= 4 || off <= -4;
    } else {
        const int cg = (int)output_affine_gain_q3(a->word), dt = t - (int)output_affine_tip_x4(a->word);
        if (a->provisional) {
            wanted = g != cg || dt >= 4 || dt <= -4;
            if (!wanted) a->provisional = false;          /* strong evidence agrees with it */
        } else {
            const bool down = g <= cg - OA_STEP_Q3, moved = dt >= OA_TIP_X4 || dt <= -OA_TIP_X4;
            const bool up = g >= cg + OA_STEP_Q3 && now_us - a->applied_us >= OA_UP_AFTER_US;
            wanted = down || moved || up;
            if (up && !down && !moved) need = OA_NEED_UP;
        }
    }
    if (!wanted) { a->agree = 0; a->want = 0; a->want_weak = false; return false; }
    if (a->want && oa_near(a->want, cand)) ++a->agree;
    else { a->want = cand; a->agree = 1; a->want_weak = false; }
    if (!strong) a->want_weak = true;
    if (a->want_weak && need < OA_NEED_WEAK) need = OA_NEED_WEAK;
    if (a->agree < need) return false;
    *word = a->want;
    return true;
}

static inline void output_auto_applied(output_auto_t *a, unsigned word, int64_t now_us)
{
    a->word = word;
    a->provisional = a->want_weak;
    a->applied_us = now_us;
    a->want = 0; a->agree = 0; a->want_weak = false;
    ++a->applied;
}

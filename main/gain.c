/* C5VRX-5 by Twotoz and the C5VRX contributors: Direct Gain V6 core.
 * Ported from legacy/c5vrx4/main/direct_gain_v3.c (PR #190) with the fixed-lane,
 * staged-recovery, boost-off configuration that ran on the board (V5), plus
 * the V6 carrier memory described in gain.h. */
#include "gain.h"
#include "direct_gain_v3_lut.h"
#include <string.h>

/* A Q4 cell represents the half-integer position n+0.5. The lookup is built
 * once; the observer only reads tables and counts a histogram. */
static uint8_t s_power[256];
static uint8_t s_flags[256];
static int8_t s_ci[256], s_cq[256];   /* 2*value+1 per axis */
static bool s_lut_ready;

/* The cell's I and Q value as one-hot bits (I + 8 in bits 0-15, Q + 8 in
 * 16-31): a signed nibble XOR 8 is its value + 8. OR-ing them gives every
 * value seen, so the span needs no per-sample sign extension or min/max. */
static inline uint32_t iq_bits(uint8_t raw)
{
    return 1u << ((raw >> 4) ^ 8u) | 1u << (((raw & 15u) ^ 8u) + 16u);
}

/* Direct mode: act on the first window outside the healthy band and jump
 * straight to the predicted destination. The healthy band stays a strict
 * zero-write zone, so a steady carrier gets no gain writes. */
#define DG_STABLE_WINDOWS 1u
#define DG_HIGH_WINDOWS   1u
#define DG_WEAK_WINDOWS   1u
/* Anti-hunt: two direction reversals within DG_REVERSAL_US arm a damped
 * period in which an out-of-band decision needs DG_DAMPED_WINDOWS windows.
 * Saturation is never damped. */
#define DG_REVERSAL_US     20000u
#define DG_REVERSALS_ARM   2u
#define DG_DAMP_US         200000u
#define DG_DAMPED_WINDOWS  8u
/* A moderate excursion must persist 20 ms (a third of a field) before a
 * write: ordinary FM envelope ripple crossed a band edge 30-100 times a
 * second, and every write is a short PHY transient (board, PR #177). */
#define DG_MODERATE_US     20000u
/* Wi-Fi bursts clip every sample for 0.1-0.5 ms (zerowidth/C5VRX PR #3);
 * saturation counts as overload only after it lasted this long. */
#define DG_BURST_US        1000u
/* Fade hold. A moving drone in an open field passes ground-reflection and
 * multipath nulls of a few milliseconds, after which the carrier is back at
 * its old level. Jumping to the table maximum on the first empty window
 * (+~22 dB from G60) made the returning carrier fold in the ultrafine
 * window: the 1 ms burst gate plus a staged recovery turned every null into
 * ~2 ms of extra loss and 4 writes, and a partial null into ~10 writes
 * (test/test_gain_fade.c, host model). Within DG_FADE_HOLD_US of the last
 * healthy window an empty or weak window does not raise the gain. Motion
 * nulls last ~lambda/2 / speed (2.6 cm: ~3 ms at 10 m/s); a longer hold made
 * a carrier that stays weaker (obstacle) wait: 81 ms instead of 1 ms for a
 * -12 dB step with 100 ms. With 10 ms the cost of a sustained drop is
 * bounded by the hold. A carrier that is really gone still reaches the
 * table maximum; distance fades are slower and tracked as before. */
#define DG_FADE_HOLD_US    10000u
/* V6 carrier memory. After the fade hold a lost carrier climbs from the last
 * healthy gain in steps of DG6_CLIMB_STEP indices (about one RF stage) every
 * DG6_CLIMB_US: a carrier back at its old level, or somewhat weaker, shows up
 * a few dB above it instead of 40 dB above it, and a carrier that is really
 * gone still reaches the table maximum within ~25 ms. Saturation above the
 * remembered gain is that carrier returning: one write back to it, without
 * the 1 ms burst gate and the staged walk (each stage a few ms of folded,
 * clipped picture). A burst there costs one write to a known-good gain. */
#define DG6_CLIMB_STEP     8u
#define DG6_CLIMB_US       3000u

static int clamp_i(int v, int lo, int hi) { return v < lo ? lo : v > hi ? hi : v; }
static int abs_i(int v) { return v < 0 ? -v : v; }

static void prepare_lut(void)
{
    if (s_lut_ready) return;
    for (unsigned raw = 0; raw < 256; ++raw) {
        int q = (int8_t)((raw & 15u) << 4) >> 4;
        int i = (int8_t)(raw & 240u) >> 4;
        int ci = 2 * i + 1, cq = 2 * q + 1;
        int power = (ci * ci + cq * cq + 2) / 4;
        s_power[raw] = (uint8_t)power;
        s_ci[raw] = (int8_t)ci;
        s_cq[raw] = (int8_t)cq;
        s_flags[raw] = (power <= 4 ? 1u : 0u) |
                       ((i == -8 || i == 7 || q == -8 || q == 7) ? 2u : 0u);
    }
    s_lut_ready = true;
}

/* Phase of (x, y) in 256 codes per turn, 0 along +I (c5vrx_phase8_gain_lut
 * on its own grid within one code; test/test_gain_measure.c). */
static uint8_t phase8(int x, int y)
{
    const unsigned ax = (unsigned)abs_i(x), ay = (unsigned)abs_i(y);
    const unsigned lo = ax < ay ? ax : ay, hi = ax < ay ? ay : ax;
    if (!hi) return 0;
    const unsigned t = (lo << 8) / hi;                                  /* tan, Q8 */
    unsigned a = (32u * t + ((11u * t * (256u - t)) >> 8) + 128u) >> 8; /* atan, first octant */
    if (ay > ax) a = 64u - a;
    if (x < 0) a = 128u - a;
    if (y < 0) a = 256u - a;
    return (uint8_t)a;
}

/* A window whose points sit around a DC point far from the origin read as a
 * strong, coherent carrier: power and phase are taken from the origin. On
 * the board (2026-10-19, weak VTX at a distance, after the live switch to
 * MATCHED) 3.5-4 cells of DC with 0.8 cells of carrier and noise around it
 * read P50 ~22 and coherence 96-100, inside the healthy band, so Direct Gain
 * held G71-G77 instead of climbing while the picture was gone. Such a window
 * is judged around its own DC, rounded to whole cells: only when the DC
 * dominates (|mean|^2 >= the spread around it, and >= 1.5 cells) in a probe
 * of >= 256 bytes. A carrier's circle has a small mean against a large
 * spread (research/data/afc_2026-10-18, 840 probes close in: at most 0.74
 * of it; the 2026-10-19 edge snapshots: 1-6 in ~78 % of the probes) and
 * keeps the origin-based result bit for bit; so do the 64-byte
 * sentinel and a fixed IQ point (dg_stuck, measured as before). Clipping,
 * repeats and the span stay raw. */
#define DG_CENTER_MIN_BYTES 256u
static bool dc_dominated(int64_t sx, int64_t sy, uint64_t s2, size_t bytes, int *di, int *dq)
{
    if (bytes < DG_CENTER_MIN_BYTES) return false;
    const int64_t n = (int64_t)bytes;
    const int64_t dc2 = sx * sx + sy * sy;            /* (n * mean)^2, dsp units (2 x cell) */
    const int64_t spread = n * (int64_t)s2 - dc2;     /* n^2 x variance */
    if (dc2 < spread || dc2 < 9 * n * n) return false;
    /* Whole cells: 2n per cell in sums of dsp units; round half away. */
    *di = (int)((sx >= 0 ? sx + n : sx - n) / (2 * n));
    *dq = (int)((sy >= 0 ? sy + n : sy - n) / (2 * n));
    return *di || *dq;
}

static dg_observation_t measure(const uint8_t *samples, size_t bytes,
                                const uint8_t phase8_lut[256], uint64_t observed_us, bool envelope)
{
    dg_observation_t r = {.observed_us = observed_us};
    if (!samples || !bytes || !phase8_lut) return r;
    prepare_lut();
    uint16_t hist[114] = {0};
    unsigned origin = 0, clip = 0, coherent = 0, repeats = 0;
    int32_t cx = s_ci[samples[0]], cy = s_cq[samples[0]];
    uint32_t cp = s_power[samples[0]];
    /* Every per-sample quantity depends only on the byte (tables) or on the
     * byte and its predecessor. The sentinel and the observer run this on
     * ~1.3 MB/s; the former per-sample sign extension, wrap and min/max cost
     * ~125 instructions a byte and with it ~55 % of the CPU, which starved
     * IDLE into the task watchdog (board 2026-10-18, docs/FINDINGS.md).
     * Results are identical (test/test_gain_measure.c). */
    uint32_t iq_seen = iq_bits(samples[0]);
    uint8_t prior_raw = samples[0], prior_phase = phase8_lut[prior_raw];
    ++hist[s_power[prior_raw]];
    origin += s_flags[prior_raw] & 1u;
    clip += s_flags[prior_raw] >> 1;
    for (size_t n = 1; n < bytes; ++n) {
        const uint8_t raw = samples[n], power = s_power[raw], phase = phase8_lut[raw];
        ++hist[power];
        origin += s_flags[raw] & 1u;
        clip += s_flags[raw] >> 1;
        iq_seen |= iq_bits(raw);
        cx += s_ci[raw];
        cy += s_cq[raw];
        cp += power;
        /* |wrap(phase - prior)| <= 32 codes (45 degrees) */
        coherent += power >= 8u && (uint8_t)(phase - prior_phase + 32u) <= 64u;
        repeats += raw == prior_raw;
        prior_phase = phase;
        prior_raw = raw;
    }
    /* Sum of x^2 + y^2 is 4 * power - 2 per sample (x, y odd). */
    const uint32_t i_seen = iq_seen & 0xffffu, q_seen = iq_seen >> 16;   /* never empty: bytes >= 1 */
    const int i_span = (31 - __builtin_clz(i_seen)) - __builtin_ctz(i_seen);
    const int q_span = (31 - __builtin_clz(q_seen)) - __builtin_ctz(q_seen);
    r.span = (uint8_t)(i_span > q_span ? i_span : q_span);
    r.repeat_pm = bytes > 1u ? (uint16_t)(repeats * 1000u / (bytes - 1u)) : 0;
    r.sum_i = cx;
    r.sum_q = cy;
    int di, dq;
    if (!dg_stuck(&r) && dc_dominated(cx, cy, 4ull * cp - 2ull * bytes, bytes, &di, &dq)) {
        memset(hist, 0, sizeof(hist));
        origin = coherent = 0;
        uint8_t prior = 0;
        for (size_t n = 0; n < bytes; ++n) {
            const int x = s_ci[samples[n]] - 2 * di, y = s_cq[samples[n]] - 2 * dq;
            const int p = (x * x + y * y + 2) / 4;
            const uint8_t power = (uint8_t)(p > 113 ? 113 : p), phase = phase8(x, y);
            ++hist[power];
            origin += power <= 4u;
            if (n) coherent += power >= 8u && (uint8_t)(phase - prior + 32u) <= 64u;
            prior = phase;
        }
        r.centered = 1;
    }
    int64_t sx = 0, sy = 0, sxx = 0, syy = 0, sxy = 0, sxp = 0, syp = 0;
    uint64_t spp = 0;
    if (envelope) {
        for (size_t n = 0; n < bytes; ++n) {
            const uint8_t raw = samples[n];
            const int x = 2 * ((int8_t)(raw & 240u) >> 4) + 1;
            const int y = 2 * ((int8_t)((raw & 15u) << 4) >> 4) + 1;
            const int p = x * x + y * y;
            sx += x; sy += y;
            sxx += x * x; syy += y * y; sxy += x * y;
            sxp += x * p; syp += y * p;
            spp += (unsigned)(p * p);
        }
    }
    unsigned cumulative = 0;
    bool p50 = false, p90 = false;
    unsigned r50 = (unsigned)((bytes + 1u) / 2u);
    unsigned r90 = (unsigned)((bytes * 90u + 99u) / 100u);
    unsigned r95 = (unsigned)((bytes * 95u + 99u) / 100u);
    for (unsigned p = 0; p < 114u; ++p) {
        cumulative += hist[p];
        if (!p50 && cumulative >= r50) { r.p50 = (uint8_t)p; p50 = true; }
        if (!p90 && cumulative >= r90) { r.p90 = (uint8_t)p; p90 = true; }
        if (cumulative >= r95) { r.p95 = (uint8_t)p; break; }
    }
    r.origin_pm = (uint16_t)(origin * 1000u / bytes);
    r.clip_pm = (uint16_t)(clip * 1000u / bytes);
    r.coherence = bytes > 1u ? (uint8_t)(coherent * 100u / (bytes - 1u)) : 0;
    /* Envelope C/N: integer raw moments in the existing sample loop, then
     * translate the moments to the window's DC centre once per probe.
     * No per-sample floating point or extra passes in the fast observer. */
    if (envelope) {
        const double inv = 1.0 / (double)bytes;
        const double mx = (double)sx * inv, my = (double)sy * inv;
        const double dc2 = mx * mx + my * my;
        const double mp_raw = (double)(sxx + syy) * inv;
        const double mp = mp_raw - dc2;
        const double mp2 = (double)spp * inv - 4.0 * (mx * (double)sxp + my * (double)syp) * inv
                    + 4.0 * (mx * mx * (double)sxx + 2.0 * mx * my * (double)sxy + my * my * (double)syy) * inv
                    + 2.0 * dc2 * mp_raw - 3.0 * dc2 * dc2;
        const double var = mp2 - mp * mp;
        const double ratio = mp <= 0.0 ? 0.0 : var > 0.0 ? 100.0 * mp * mp / var : 65535.0;
        r.env_ratio_x100 = (uint16_t)(ratio > 65535.0 ? 65535.0 : ratio + .5);
    }
    return r;
}

dg_observation_t dg_measure(const uint8_t *samples, size_t bytes,
                            const uint8_t phase8_lut[256], uint64_t observed_us)
{
    return measure(samples, bytes, phase8_lut, observed_us, true);
}

dg_observation_t dg_measure_gain(const uint8_t *samples, size_t bytes,
                                 const uint8_t phase8_lut[256], uint64_t observed_us)
{
    return measure(samples, bytes, phase8_lut, observed_us, false);
}

static dg_transition_t transition_kind(const direct_gain_t *g, uint8_t from, uint8_t to)
{
    const arc_gain_tuple_t *a = &g->tuple[from], *b = &g->tuple[to];
    if (a->rf_stage != b->rf_stage) return DG_RF;
    if (a->bb_code != b->bb_code) return DG_BB;
    return DG_FINE;
}

static bool carrier(const dg_observation_t *o)
{
    return o->coherence >= 55 && o->p50 >= 5 && o->origin_pm < 650;
}

/* Healthy band P50 13..32 (r ~3.5..5.6 cells), the zero-write zone. */
#define BAND_LO          13
#define BAND_HI          32
#define BAND_P95         65
#define BAND_CLIP_PM     20
#define WEAK_TARGET      17
#define HIGH_TARGET      27
#define HIGH_P90         53
#define HIGH_P95         72
#define SCHMITT_HI       30
#define SCHMITT_P90      47
#define SCHMITT_P95      65
#define SCHMITT_LO       14

static bool healthy(const dg_observation_t *o)
{
    /* Gain health is envelope placement, not FM phase-step speed. The
     * old coherence gate could leave a clean fast-FM carrier in ACQUIRE/
     * VERIFY forever and block profile/DC work that requires DG_HOLD.
     * Learning keeps its separate confirmed-carrier safeguards. */
    return o->p50 >= BAND_LO && o->p50 <= BAND_HI &&
           o->p95 <= BAND_P95 && o->clip_pm < BAND_CLIP_PM && o->origin_pm <= 250;
}

static bool valid_learning(const dg_observation_t *o)
{
    return carrier(o) && o->p50 >= 7 && o->p50 <= 45 &&
           o->p95 < 80 && o->clip_pm < 20 && o->origin_pm < 300;
}

static int power_db_q8(unsigned power)
{
    if (power < 1u) power = 1u;
    if (power > 113u) power = 113u;
    return s_dg3_power_db_q8[power];
}

/* Q10 power ratio to Q8 dB with an integer log2 iteration. */
static int ratio_db_q8(int ratio_q10)
{
    if (ratio_q10 < 1) ratio_q10 = 1;
    int64_t x = ((int64_t)ratio_q10 << 16) / 1024;
    int whole = 0;
    while (x < 65536) { x <<= 1; --whole; }
    while (x >= 131072) { x >>= 1; ++whole; }
    int fraction = 0;
    for (int bit = 7; bit >= 0; --bit) {
        x = (x * x) >> 16;
        if (x >= 131072) { x >>= 1; fraction |= 1 << bit; }
    }
    return ((whole * 256 + fraction) * 771 + 128) / 256;
}

#define DG_MAGIC 0x44473521u
static bool same_table(const arc_gain_table_t *a, const arc_gain_table_t *b)
{
    if (!a || !b || a->max_index != b->max_index || a->band5 != b->band5) return false;
    for (unsigned k = 0; k < ARC_RX_STAGE_COUNT; ++k)
        if (a->spans[k] != b->spans[k] || a->start[k] != b->start[k] || a->rf_code[k] != b->rf_code[k]) return false;
    return true;
}

bool dg_map_context_equal(const dg_map_context_t *a, const dg_map_context_t *b)
{
    return a && b && a->freq_mhz == b->freq_mhz && a->lane_mode == b->lane_mode &&
           a->iq_scale == b->iq_scale && a->filter_skirt == b->filter_skirt &&
           a->filter_bw20 == b->filter_bw20 && a->offset_khz == b->offset_khz && a->filter_code == b->filter_code;
}

bool dg_map_matches(const direct_gain_t *g, const arc_gain_table_t *table, const dg_map_context_t *context)
{
    return g && g->magic == DG_MAGIC && g->map_context_valid &&
           dg_map_context_equal(&g->map_context, context) && same_table(&g->table, table);
}

void dg_reset_context(direct_gain_t *g, const arc_gain_table_t *table, uint8_t current_gain,
                      const dg_map_context_t *context)
{
    if (!g) return;
    /* Keep the measured map across tracking resets on an unchanged table:
     * dropping it made V5 re-explore every step (a burst of corrective
     * writes, each a little grain; operator, 2026-10-06). */
    dg_map_blob_t kept;
    dg_map_context_t wanted = {0};
    if (context) wanted = *context; /* context may alias g */
    /* V6 carrier memory survives tracking resets on the same receiver: an
     * AFC step changes only the offset, a PHY lease nothing physical. */
    const bool same_receiver = g->magic == DG_MAGIC && table && same_table(&g->table, table) &&
        g->map_context_valid == (context != NULL) &&
        (!context || (g->map_context.freq_mhz == wanted.freq_mhz && g->map_context.lane_mode == wanted.lane_mode &&
                      g->map_context.iq_scale == wanted.iq_scale && g->map_context.filter_code == wanted.filter_code &&
                      g->map_context.filter_skirt == wanted.filter_skirt && g->map_context.filter_bw20 == wanted.filter_bw20));
    const bool anchor_valid = same_receiver && g->anchor_valid;
    const uint8_t anchor_gain = g->anchor_gain;
    const uint64_t anchor_us = g->anchor_us, carrier_us = g->carrier_us, last_healthy_us = g->last_healthy_us;
    const uint32_t anchor_returns = same_receiver ? g->anchor_returns : 0u, climbs = same_receiver ? g->climbs : 0u;
    bool keep = g->magic == DG_MAGIC && g->learned && table && same_table(&g->table, table) &&
                g->map_context_valid == (context != NULL) &&
                (!context || dg_map_context_equal(&g->map_context, &wanted)) &&
                dg_export_map(g, &kept) > 0u;
    memset(g, 0, sizeof(*g));
    prepare_lut();
    if (table && table->max_index >= 20u && table->max_index <= ARC_VENDOR_GAIN_MAX) g->table = *table;
    else arc_gain_table_from_bytes(&g->table, NULL, ARC_VENDOR_GAIN_MAX);
    for (unsigned k = 0; k <= g->table.max_index; ++k)
        (void)arc_gain_tuple_decode(&g->table, (uint8_t)k, &g->tuple[k]);
    g->current_gain = (uint8_t)clamp_i(current_gain, 20, g->table.max_index);
    g->target_gain = g->current_gain;
    g->relative_power_q10[g->current_gain] = 1024u;
    g->confidence[g->current_gain] = 1u;
    g->uncertainty_pm[g->current_gain] = 50u;
    g->state = DG_ACQUIRE;
    g->magic = DG_MAGIC;
    g->map_context_valid = context != NULL;
    g->map_context = wanted;
    if (keep) (void)dg_import_map(g, &kept);
    if (same_receiver) {
        g->anchor_valid = anchor_valid && anchor_gain >= 20u && anchor_gain <= g->table.max_index;
        g->anchor_gain = anchor_gain;
        g->anchor_us = anchor_us;
        g->carrier_us = carrier_us;
        g->last_healthy_us = last_healthy_us;
        g->anchor_returns = anchor_returns;
        g->climbs = climbs;
    }
}

void dg_reset(direct_gain_t *g, const arc_gain_table_t *table, uint8_t current_gain)
{
    /* Tracking/native handoff on the already-bound context. The observer
     * must use dg_reset_context when the physical transfer context changes. */
    dg_map_context_t context;
    if (g && g->magic == DG_MAGIC && g->map_context_valid) {
        context = g->map_context;
        dg_reset_context(g, table, current_gain, &context);
    } else dg_reset_context(g, table, current_gain, NULL);
}

unsigned dg_export_map(const direct_gain_t *g, dg_map_blob_t *blob)
{
    if (!g || !blob) return 0u;
    memset(blob, 0, sizeof(*blob));
    blob->version = DG_MAP_VERSION;
    blob->max_index = g->table.max_index;
    blob->band5 = g->table.band5;
    blob->context_valid = g->map_context_valid;
    blob->context = g->map_context;
    blob->freq_mhz = (uint16_t)g->map_context.freq_mhz;
    blob->lane_mode = (uint8_t)g->map_context.lane_mode;
    /* Canonical fields only: memset above keeps serialization padding zero. */
    memcpy(blob->table.spans, g->table.spans, sizeof(blob->table.spans));
    memcpy(blob->table.start, g->table.start, sizeof(blob->table.start));
    memcpy(blob->table.rf_code, g->table.rf_code, sizeof(blob->table.rf_code));
    blob->table.max_index = g->table.max_index;
    blob->table.band5 = g->table.band5;
    blob->table.runtime_spans_valid = g->table.runtime_spans_valid;
    unsigned confident = 0;
    for (unsigned k = 0; k <= g->table.max_index && k < DG_STATES; ++k) {
        if (!g->confidence[k] || !g->relative_power_q10[k]) continue;
        blob->confidence[k] = g->confidence[k];
        blob->power_q10[k] = g->relative_power_q10[k];
        blob->uncertainty_pm[k] = g->uncertainty_pm[k];
        ++confident;
    }
    return confident > 1u ? confident : 0u;   /* a lone anchor says nothing */
}

bool dg_import_map(direct_gain_t *g, const dg_map_blob_t *blob)
{
    if (!g || !blob || blob->version != DG_MAP_VERSION ||
        blob->max_index != g->table.max_index || blob->band5 != g->table.band5 ||
        !same_table(&blob->table, &g->table) || blob->context_valid != g->map_context_valid ||
        (g->map_context_valid && (!dg_map_context_equal(&blob->context, &g->map_context) ||
         blob->freq_mhz != g->map_context.freq_mhz || blob->lane_mode != g->map_context.lane_mode))) return false;
    const uint8_t anchor = g->current_gain;
    if (!blob->confidence[anchor] || !blob->power_q10[anchor]) return false;
    for (unsigned k = 0; k <= g->table.max_index && k < DG_STATES; ++k) {
        if (!blob->confidence[k] || !blob->power_q10[k]) continue;
        g->relative_power_q10[k] = blob->power_q10[k];
        g->uncertainty_pm[k] = blob->uncertainty_pm[k];
        g->confidence[k] = blob->confidence[k];
    }
    g->learned = 1u;
    return true;
}

bool dg_import_preset(direct_gain_t *g, const uint16_t *power_q10, unsigned count, uint8_t max_index, uint8_t band5)
{
    if (!g || !power_q10 || max_index != g->table.max_index || band5 != g->table.band5) return false;
    const uint8_t anchor = g->current_gain;
    if (anchor >= count || !power_q10[anchor]) return false;
    /* Rescale so the anchor keeps its own value: predict() only uses ratios. */
    const uint32_t base = g->relative_power_q10[anchor] ? g->relative_power_q10[anchor] : 1024u;
    unsigned used = 0;
    for (unsigned k = 0; k <= g->table.max_index && k < count; ++k) {
        if (!power_q10[k] || g->confidence[k] > 1u) continue;   /* own data wins */
        uint32_t v = (uint32_t)power_q10[k] * base / power_q10[anchor];
        g->relative_power_q10[k] = (uint16_t)(v < 1u ? 1u : v > 65535u ? 65535u : v);
        g->uncertainty_pm[k] = 600u;
        g->confidence[k] = 1u;
        ++used;
    }
    return used > 1u;
}

/* Measured responses predict any tuple; without them only a fine step in
 * the same BB bank gets a low-confidence local prior. */
static bool predict(const direct_gain_t *g, uint8_t candidate, int *ratio_q10, int *uncertainty_pm)
{
    uint8_t current = g->current_gain;
    if (g->confidence[current] && g->confidence[candidate]) {
        int base = g->relative_power_q10[current];
        if (!base) return false;
        *ratio_q10 = clamp_i((int)g->relative_power_q10[candidate] * 1024 / base, 1, 8192);
        *uncertainty_pm = clamp_i(g->uncertainty_pm[candidate] + g->uncertainty_pm[current], 80, 900);
        return true;
    }
    if (transition_kind(g, current, candidate) != DG_FINE) return false;
    int steps = (int)g->tuple[current].fine_code - (int)g->tuple[candidate].fine_code;
    if (abs_i(steps) > 2) return false;
    int ratio = 1024;
    while (steps > 0) { ratio = ratio * 121 / 100; --steps; }
    while (steps < 0) { ratio = ratio * 100 / 121; ++steps; }
    *ratio_q10 = ratio;
    *uncertainty_pm = 300;
    return true;
}

static uint8_t adjacent_physical(const direct_gain_t *g, bool up)
{
    const arc_gain_tuple_t *current = &g->tuple[g->current_gain];
    for (int distance = 1; distance <= 2; ++distance) {
        int wanted_fine = (int)current->fine_code + (up ? -distance : distance);
        for (unsigned k = 20u; k <= g->table.max_index; ++k) {
            const arc_gain_tuple_t *t = &g->tuple[k];
            if (g->confidence[k] && g->confidence[g->current_gain]) {
                bool measured_up = g->relative_power_q10[k] > g->relative_power_q10[g->current_gain];
                if (measured_up != up) continue;
            }
            if (t->rf_stage == current->rf_stage && t->bb_code == current->bb_code &&
                t->fine_code == wanted_fine) return (uint8_t)k;
        }
    }
    /* Fine exhausted: the adjacent BB bank at its least aggressive state. */
    int wanted_bb = up ? (int)current->bb_code * 2 + 1 : ((int)current->bb_code - 1) / 2;
    for (int distance = 0; distance < 6; ++distance) {
        int boundary_fine = up ? 5 - distance : distance;
        for (unsigned k = 20u; k <= g->table.max_index; ++k) {
            const arc_gain_tuple_t *t = &g->tuple[k];
            if (t->rf_stage == current->rf_stage && t->bb_code == wanted_bb &&
                t->fine_code == boundary_fine) return (uint8_t)k;
        }
    }
    /* Last resort: the adjacent RF stage's nearest entry in vendor order
     * (the exact 5 GHz table starts stages at counters 12..15). */
    int wanted_rf = (int)current->rf_stage + (up ? 1 : -1);
    if (up) {
        for (unsigned k = 20u; k <= g->table.max_index; ++k)
            if ((int)g->tuple[k].rf_stage == wanted_rf) return (uint8_t)k;
    } else {
        for (int k = (int)g->table.max_index; k >= 20; --k)
            if ((int)g->tuple[k].rf_stage == wanted_rf) return (uint8_t)k;
    }
    return g->current_gain;
}

/* Overload: remove late (BB) gain first; the RF stage sets the noise
 * figure. Still saturated a window later, the next drop takes RF. */
static uint8_t emergency_drop(const direct_gain_t *g)
{
    const arc_gain_tuple_t *current = &g->tuple[g->current_gain];
    if (current->bb_code > 1u) {
        unsigned lower_bb = (current->bb_code - 1u) / 2u;
        for (unsigned k = 20u; k < g->current_gain; ++k) {
            const arc_gain_tuple_t *t = &g->tuple[k];
            if (t->rf_stage == current->rf_stage && t->bb_code == lower_bb) return (uint8_t)k;
        }
    }
    if (current->rf_stage > 0u) {
        for (unsigned k = 20u; k < g->current_gain; ++k)
            if (g->tuple[k].rf_stage + 1u == current->rf_stage) return (uint8_t)k;
    }
    return adjacent_physical(g, false);
}

static uint8_t select_destination(const direct_gain_t *g, const dg_observation_t *o,
                                  bool up, int32_t desired_q8)
{
    uint8_t best = g->current_gain;
    int best_class = 4, best_error = 99999, best_uncertainty = 999, best_artifact = 999;
    for (unsigned k = 20u; k <= g->table.max_index; ++k) {
        if (k == g->current_gain) continue;
        int ratio, uncertainty;
        if (!predict(g, (uint8_t)k, &ratio, &uncertainty)) continue;
        int p50 = (int)o->p50 * ratio / 1024;
        int p95_worst = (int)o->p95 * ratio * (1000 + uncertainty) / 1024000;
        if (p50 < BAND_LO || p50 > BAND_HI || p95_worst > BAND_P95) continue;
        if (up && ratio <= 1024) continue;
        if (!up && ratio >= 1024) continue;
        int kind = transition_kind(g, g->current_gain, (uint8_t)k);
        int delta_q8 = ratio_db_q8(ratio);
        int hysteresis_q8 = kind == DG_FINE ? 90 : kind == DG_BB ? 192 : 384;
        if ((up && desired_q8 < delta_q8 + hysteresis_q8) ||
            (!up && desired_q8 > delta_q8 - hysteresis_q8)) continue;
        int error = abs_i((int)desired_q8 - delta_q8);
        int artifact = g->artifact_score[k];
        if (kind < best_class ||
            (kind == best_class && error < best_error) ||
            (kind == best_class && error == best_error && uncertainty < best_uncertainty) ||
            (kind == best_class && error == best_error && uncertainty == best_uncertainty &&
             artifact < best_artifact)) {
            best = (uint8_t)k;
            best_class = kind;
            best_error = error;
            best_uncertainty = uncertainty;
            best_artifact = artifact;
        }
    }
    return best == g->current_gain ? adjacent_physical(g, up) : best;
}

static void learn_transition(direct_gain_t *g, const dg_observation_t *after)
{
    if (!g->learn_enabled) { ++g->learn_refused; return; }
    if (!valid_learning(&g->before) || !valid_learning(after) ||
        abs_i((int)g->before.coherence - (int)after->coherence) > 15) return;
    const dg_observation_t *prior = &g->before_previous;
    if (!prior->observed_us || prior->observed_us >= g->before.observed_us ||
        g->before.observed_us - prior->observed_us > 10000u ||
        abs_i((int)prior->p50 - (int)g->before.p50) > clamp_i(g->before.p50 / 4, 2, 10) ||
        abs_i((int)prior->coherence - (int)g->before.coherence) > 12)
        return;
    uint8_t a = g->prior_gain, b = g->current_gain;
    uint32_t base = g->relative_power_q10[a];
    if (!base) return;
    /* A "fine" step the vendor table makes a BB jump is learned anyway, with
     * high uncertainty: rejecting it kept that index mis-predicted. */
    bool mismatch = false;
    if (g->transition == DG_FINE) {
        int steps = abs_i((int)g->tuple[a].fine_code - (int)g->tuple[b].fine_code);
        int high = 1024, low = 1024;
        while (steps-- > 0) { high = high * 18 / 10; low = low * 10 / 18; }
        int observed = (int)after->p50 * 1024 / g->before.p50;
        mismatch = observed < low || observed > high;
    }
    uint32_t estimate = (uint32_t)clamp_i((int)(base * after->p50 / g->before.p50), 1, 65535);
    if (mismatch) ++g->model_mismatches;
    if (!g->confidence[b]) {
        g->relative_power_q10[b] = (uint16_t)estimate;
        g->uncertainty_pm[b] = mismatch ? 600u : 350u;
    } else {
        uint32_t old = g->relative_power_q10[b];
        uint32_t residual_pm = old ? (uint32_t)(abs_i((int)old - (int)estimate) * 1000) / old : 1000u;
        g->uncertainty_pm[b] = (uint16_t)clamp_i((3 * (int)g->uncertainty_pm[b] + (int)residual_pm) / 4, 60, 900);
        if (residual_pm > 450u) {
            if (g->confidence[b]) --g->confidence[b];
            return;
        }
        g->relative_power_q10[b] = (uint16_t)((3u * old + estimate) / 4u);
    }
    if (g->confidence[b] < 15u) ++g->confidence[b];
    ++g->learned;
}

static void note_direction(direct_gain_t *g, int8_t dir, uint64_t now)
{
    if (g->last_write_dir && dir != g->last_write_dir &&
        now >= g->dir_write_us && now - g->dir_write_us < DG_REVERSAL_US) {
        if (++g->reversals >= DG_REVERSALS_ARM) {
            g->damp_until_us = now + DG_DAMP_US;
            g->reversals = 0;
            ++g->damp_events;
        }
    } else if (now < g->dir_write_us || now - g->dir_write_us >= DG_REVERSAL_US) {
        g->reversals = 0;
    }
    g->last_write_dir = dir;
    g->dir_write_us = now;
}

static uint8_t start_write(direct_gain_t *g, const dg_observation_t *o,
                           const dg_observation_t *prior, uint8_t target)
{
    if (target == g->current_gain) return target;
    note_direction(g, target > g->current_gain ? 1 : -1, o->observed_us);
    int ratio_q10 = 0, uncertainty_pm = 0;
    if (predict(g, target, &ratio_q10, &uncertainty_pm)) g->virtual_gain_q8 -= ratio_db_q8(ratio_q10);
    else g->virtual_gain_q8 = 0;
    g->virtual_gain_q8 = clamp_i(g->virtual_gain_q8, -12 * 256, 12 * 256);
    g->before = *o;
    g->before_previous = *prior;
    g->prior_gain = g->current_gain;
    g->target_gain = target;
    g->transition = transition_kind(g, g->current_gain, target);
    g->current_gain = target;
    g->state = DG_SETTLE;
    g->stable_windows = 0;
    g->write_us = o->observed_us;
    ++g->writes;
    return target;
}

static uint64_t settle_guard_us(const direct_gain_t *g)
{
    return g->settle_us[g->transition] > 400u ? (uint64_t)g->settle_us[g->transition] * 3u / 4u : 300u;
}

uint8_t dg_tick(direct_gain_t *g, const dg_observation_t *o)
{
    if (!g || !o) return 0u;
    dg_observation_t prior = g->last_tracking;
    g->last_tracking = *o;
    bool no_carrier = o->p50 <= 4 && o->origin_pm >= 650 && o->coherence < 20;
    bool saturated = o->clip_pm >= 100 || o->p95 >= 95;
    if (!no_carrier) g->carrier_us = o->observed_us;
    /* V6: a carrier clipping above its remembered gain returns to it at
     * once (see DG6_CLIMB_STEP). At or below it, V5's burst gate and
     * staged overload recovery still apply. */
    if (saturated && g->anchor_valid && g->current_gain > g->anchor_gain) {
        ++g->overloads;
        ++g->anchor_returns;
        g->saturated_run = false;
        g->overload_recovery = false;
        g->high_windows = g->weak_windows = 0;
        g->virtual_gain_q8 = 0;
        g->last_direction = 0;
        g->climb_us = 0;
        return start_write(g, o, &prior, g->anchor_gain);
    }
    if (!saturated) g->saturated_run = false;
    else if (!g->saturated_run) { g->saturated_run = true; g->saturated_since_us = o->observed_us; }
    if (saturated && o->observed_us - g->saturated_since_us < DG_BURST_US) {
        /* Possibly an interference burst: not an overload, not a prior. */
        ++g->bursts_ignored;
        g->last_tracking = prior;
        return g->current_gain;
    }
    bool at_max = g->current_gain == g->table.max_index;
    if (no_carrier) {
        /* Near-origin IQ during a settling write is not carrier loss yet. */
        if (g->state == DG_SETTLE &&
            (o->observed_us <= g->write_us || o->observed_us - g->write_us < settle_guard_us(g)))
            return g->current_gain;
        /* Fade hold: an empty window right after a healthy carrier is a
         * null, not a lost VTX; keep the gain the carrier will need again. */
        if (!g->overload_recovery && !at_max && g->last_healthy_us &&
            o->observed_us - g->last_healthy_us < DG_FADE_HOLD_US) {
            ++g->fade_holds;
            return g->current_gain;
        }
        /* V6: with a remembered carrier, climb from it in bounded steps. */
        if (!g->overload_recovery && !at_max && g->anchor_valid) {
            if (g->climb_us && o->observed_us >= g->climb_us &&
                o->observed_us - g->climb_us < DG6_CLIMB_US) return g->current_gain;
            unsigned next = (unsigned)g->current_gain + DG6_CLIMB_STEP;
            if (next > g->table.max_index) next = g->table.max_index;
            g->climb_us = o->observed_us;
            ++g->climbs;
            g->high_windows = g->weak_windows = 0;
            g->virtual_gain_q8 = 0;
            g->last_direction = 0;
            return start_write(g, o, &prior, (uint8_t)next);
        }
        /* After an overload a low radius is no proof the carrier vanished:
         * walk back through physical tuples instead of jumping to max. */
        if (g->overload_recovery && !at_max)
            return start_write(g, o, &prior, adjacent_physical(g, true));
        /* No carrier: listen at the table maximum, where a weak carrier
         * becomes visible; a strong one takes the saturation path next. */
        g->state = DG_ACQUIRE;
        g->stable_windows = 0;
        g->high_windows = g->weak_windows = 0;
        g->virtual_gain_q8 = 0;
        g->last_direction = 0;
        return start_write(g, o, &prior, g->table.max_index);
    }
    /* Saturation does not wait for a settling write once the window can no
     * longer hold pre-write samples; that write's learning is dropped. */
    if (saturated && g->state == DG_SETTLE &&
        (g->current_gain > g->prior_gain ||
         (o->observed_us >= g->write_us && o->observed_us - g->write_us >= 300u))) {
        g->before.p50 = 0u;
        g->state = DG_VERIFY;
    }
    if (g->state == DG_SETTLE) {
        if (o->observed_us <= g->write_us || o->observed_us - g->write_us < settle_guard_us(g))
            return g->current_gain;
        if (g->stable_windows &&
            abs_i((int)o->p50 - (int)g->previous.p50) <= 2 &&
            abs_i((int)o->p95 - (int)g->previous.p95) <= 4 &&
            abs_i((int)o->origin_pm - (int)g->previous.origin_pm) <= 60 &&
            abs_i((int)o->coherence - (int)g->previous.coherence) <= 12) {
            if (g->stable_windows < DG_STABLE_WINDOWS) ++g->stable_windows;
        } else g->stable_windows = 1u;
        g->previous = *o;
        if (g->stable_windows < DG_STABLE_WINDOWS) return g->current_gain;
        uint64_t elapsed = o->observed_us - g->write_us;
        if (elapsed < 65535u) {
            uint16_t *settle = &g->settle_us[g->transition];
            *settle = *settle ? (uint16_t)((3u * *settle + elapsed) / 4u) : (uint16_t)elapsed;
        }
        int quality_drop = (int)g->before.coherence - (int)o->coherence;
        int clipping_rise = (int)o->clip_pm - (int)g->before.clip_pm;
        int artifact = clamp_i((quality_drop > 0 ? quality_drop * 4 : 0) +
                               (clipping_rise > 0 ? clipping_rise / 4 : 0), 0, 1000);
        uint16_t *score = &g->artifact_score[g->current_gain];
        *score = *score ? (uint16_t)((3u * *score + artifact) / 4u) : (uint16_t)artifact;
        learn_transition(g, o);
        g->state = DG_VERIFY;
        ++g->verified;
    }
    if (saturated) {
        ++g->overloads;
        g->high_windows = g->weak_windows = 0;
        g->virtual_gain_q8 = 0;
        g->overload_recovery = true;
        return start_write(g, o, &prior, emergency_drop(g));
    }
    if (healthy(o)) {
        g->last_healthy_us = o->observed_us;
        /* A fixed IQ point (DC, no VTX) reads healthy but is no carrier. */
        if (!dg_stuck(o)) {
            g->anchor_valid = true;
            g->anchor_gain = g->current_gain;
            g->anchor_us = o->observed_us;
        }
        g->climb_us = 0;
        g->overload_recovery = false;
        g->state = DG_HOLD;
        g->corrections = 0;
        g->high_windows = g->weak_windows = 0;
        g->virtual_gain_q8 = 0;
        g->last_direction = 0;
        ++g->holds;
        return g->current_gain;
    }
    /* Act as soon as the envelope leaves the band, before it gets grainy. A
     * starved envelope climbs whether or not the carrier is recognised yet. */
    bool high = o->p50 > BAND_HI || o->p90 >= HIGH_P90 || o->p95 > HIGH_P95 || o->clip_pm >= BAND_CLIP_PM;
    bool weak = o->p50 < BAND_LO;
    /* Schmitt bands keep the direction through small fluctuations. */
    if (g->last_direction == 2 &&
        (o->p50 > SCHMITT_HI || o->p90 > SCHMITT_P90 || o->p95 > SCHMITT_P95)) high = true;
    if (g->last_direction == 1 && o->p50 < SCHMITT_LO) weak = true;
    if (high) {
        g->weak_windows = 0;
        g->last_direction = 2;
        if (!g->high_windows) g->excursion_since_us = o->observed_us;
        if (g->high_windows < 255u) ++g->high_windows;
    } else if (weak) {
        g->high_windows = 0;
        g->last_direction = 1;
        if (!g->weak_windows) g->excursion_since_us = o->observed_us;
        if (g->weak_windows < 255u) ++g->weak_windows;
    } else {
        g->high_windows = g->weak_windows = 0;
        g->last_direction = 0;
        return g->current_gain;
    }
    /* Weak at the table maximum: there is no higher state. Receiver noise
     * reads here (P50 ~9 between "carrier" and "no carrier"); every write
     * found by the search was a sideways step and V5 hunted ~20 times a
     * second at G83 with every VTX off (board, 2026-10-11). Hold. */
    if (weak && at_max) {
        g->state = DG_HOLD;
        return g->current_gain;
    }
    /* Severe excursions act on one window, moderate ones on two windows
     * and 20 ms; damping after reversals needs eight. */
    bool damped = o->observed_us < g->damp_until_us;
    /* Weak right after a healthy carrier is treated as moderate (it must
     * last 20 ms): a partial null would otherwise raise the gain on one
     * window and fold the returning carrier. Too strong always acts at once. */
    const bool fading = g->last_healthy_us && o->observed_us - g->last_healthy_us < DG_FADE_HOLD_US;
    bool severe = high ? ((int)o->clip_pm >= BAND_CLIP_PM || (int)o->p95 > HIGH_P95 ||
                          (int)o->p50 > BAND_HI + 8)
                       : ((int)o->p50 + 4 < BAND_LO && !fading);
    unsigned need_high = damped ? DG_DAMPED_WINDOWS : severe ? DG_HIGH_WINDOWS : 2u;
    unsigned need_weak = damped ? DG_DAMPED_WINDOWS : severe ? DG_WEAK_WINDOWS : 2u;
    if ((high && g->high_windows < need_high) || (weak && g->weak_windows < need_weak))
        return g->current_gain;
    if (!severe && o->observed_us - g->excursion_since_us < DG_MODERATE_US) return g->current_gain;
    int target_power = weak ? WEAK_TARGET : HIGH_TARGET;
    /* Direct: request the full relative correction in one step. */
    g->virtual_gain_q8 = clamp_i(power_db_q8((unsigned)target_power) - power_db_q8(o->p50),
                                 -12 * 256, 12 * 256);
    uint8_t target = select_destination(g, o, weak, g->virtual_gain_q8);
    if (target == g->current_gain) return target;
    if (g->state == DG_VERIFY) ++g->corrections;
    g->high_windows = g->weak_windows = 0;
    if (severe) ++g->writes_severe;
    else ++g->writes_moderate;
    return start_write(g, o, &prior, target);
}

void dg_sync_applied(direct_gain_t *g, uint8_t gain, uint64_t write_us)
{
    if (!g) return;
    if (gain != g->current_gain) {
        g->current_gain = gain;
        g->target_gain = gain;
        g->stable_windows = 0;
    }
    g->write_us = write_us;
    g->state = DG_SETTLE;
}

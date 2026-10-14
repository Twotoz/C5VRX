/* C5VRX by Twotoz and contributors: root sync-keeper candidate (see header).
 * Ported in idea from the C5VRX-3 flywheel on feat/sync-flywheel; root changes use unwrapped adjacent detection,
 * cheap exact synthesis and whole-interval admission. Host candidate only. */
#include "sync_flywheel.h"
#include "sync_synth.h"
#include <string.h>

#ifdef ESP_PLATFORM
#include "esp_attr.h"
#define SFW_HOT IRAM_ATTR
#else
#define SFW_HOT
#endif

/* ITU-R BT.470 geometry in 40 MS/s samples. */
#define HSYNC          188u   /* 4.7 us */
#define PORCH          48u    /* front porch part rebuilt with a pulse */
#define WIN_LOCKED     24
#define WIN_UNLOCKED   96
#define CLEAN_ERR      6
#define LOCK_CLEAN     32u
#define RELOCK_EVERY   8u
#define RELOCK_AFTER   16u
#define MAX_LAG        (4u * 2560u + 4096u)
#define NEED_LINE      420u   /* window + a normal pulse (field phase known) */
#define NEED_V_ACQ     2700u  /* + a broad pulse here or half a line later */
#define V_EDGE_GUARD   1u     /* fit multiple genuine edges, round inward */

static uint32_t s_evals;
static uint32_t s_lowm, s_steps;

static void store_evals(sync_flywheel_t *f)
{
    f->evals = s_evals;
    f->lowm_evals = s_lowm;
    f->phase_steps = s_steps;
}

static bool original(sync_flywheel_t *f, uint64_t begin, uint64_t end)
{
    bool ok = sync_repair_ledger_original(&f->repairs, begin, end, f->evidence_end);
    if (!ok && f->evidence_refused != UINT32_MAX) ++f->evidence_refused;
    return ok;
}

static inline uint8_t ph_at(const sfw_ring_t *r, uint64_t k)
{
    return r->phase[r->ring[(uint32_t)k & (r->ring_bytes - 1u)]];
}

static inline int wrap8(int d)
{
    return (int)(((unsigned)d + 128u) & 255u) - 128;
}

/* Per-25-ns unwrapped phase step. Summing adjacent steps avoids aliasing
 * wide-deviation picture into an apparent negative sync plateau. */
static inline int step3(const sfw_ring_t *r, uint64_t k)
{
    ++s_steps;
    return wrap8((int)ph_at(r, k) - (int)ph_at(r, k - 1u));
}

/* Preserve the old 225-ns averaging window, using nine unwrapped steps. */
static SFW_HOT bool lowm(const sync_flywheel_t *f, const sfw_ring_t *r, uint64_t k)
{
    ++s_evals;
    ++s_lowm;
    int total = 0;
    /* Center on the tested sample. The legacy [-3,+6] window made the
     * detected edge two samples early and could overwrite the last real
     * picture samples before the vertical interval. */
    int previous = ph_at(r, k - 5u);
#pragma GCC unroll 9
    for (unsigned j = 0; j < 9u; ++j) {
        int phase = ph_at(r, k - 4u + j);
        total += wrap8(phase - previous);
        previous = phase;
    }
    return total <= 9 * f->thr;
}

/* A raw-phase outlier at one 50-ns span is not a lost sync pulse: U85Q/LP
 * has state and can suppress it. Qualify the same 225-ns plateau used by
 * detection; actual DAC pulse validity remains a separate output test. */
static inline bool span_high(const sync_flywheel_t *f, const sfw_ring_t *r, uint64_t k)
{
    return !lowm(f, r, k);
}

/* A kept pulse must decode clean span by span, not only on average: n
 * single spans across [a, a + len) must all be at sync level. */
static bool clean_pulse(sync_flywheel_t *f, const sfw_ring_t *r, uint64_t a, uint32_t len,
                        unsigned n)
{
    uint32_t stride = (len - 12u) / n;
    if (!original(f, a + 4u, a + 14u + (n - 1u) * stride)) return false;
    for (unsigned i = 0; i < n; ++i)
        if (span_high(f, r, a + 9u + i * stride)) return false;
    return true;
}

/* ---- synthesis -------------------------------------------------------- */
static uint8_t s_cell[256];
static const uint8_t *s_cell_phase;
static int s_cell_mask = -1;

static int signed4(unsigned v) { return v < 8u ? (int)v : (int)v - 16; }

static uint16_t decoder_value(const uint16_t *lut, unsigned state, uint8_t a, uint8_t b)
{
    unsigned address = (a >> 4) | ((a & 15u) << 4) | ((b & 128u) << 1) | ((b & 8u) << 6);
    return lut[state * 32u + (lut[address] >> 11)];
}

/* A first V equalizing pulse has no spare received front porch. Recover
 * the exact surviving tracker-state set through a bounded transmitted IQ
 * prefix. If states have not merged, choose against their worst case rather
 * than inventing one exact phase. Modified bytes are appropriate
 * here: this predicts decoder state, and is never camera-sync evidence.
 * Both pair parities must admit the same generated waveform. */
typedef struct { unsigned phase, x, y; } decoder_bound_t;

static void decoder_advance(const uint16_t *lut, decoder_bound_t *bound, uint8_t raw, uint8_t mid)
{
    unsigned address = (raw >> 4) | ((raw & 15u) << 4) | ((mid & 128u) << 1) | ((mid & 8u) << 6);
    unsigned carry = ((address >> 8) + (bound->phase >> 3)) >> 2;
    bound->y = (bound->y >> 1) + (bound->x >> 1) + carry;
    unsigned value = decoder_value(lut, bound->phase, raw, mid);
    bound->phase = (value >> 6) & 31u;
    bound->x = value & 63u;
}

static bool decoder_prefix(sync_flywheel_t *f, const sfw_ring_t *r, uint64_t a,
                           decoder_bound_t states[2][32], unsigned counts[2], uint64_t last[2])
{
    const uint32_t mask = r->ring_bytes - 1u;
    if (!r->decoder_lut || a < 130u || a - 130u < f->repairs.oldest_readable) return false;
    for (unsigned parity = 0; parity < 2; ++parity) {
        last[parity] = a - 2u;
        if ((last[parity] & 1u) != parity) --last[parity];
        unsigned count = 32;
        for (unsigned j = 0; j < count; ++j) states[parity][j] = (decoder_bound_t){j, 63, 63};
        for (uint64_t k = last[parity] - 126u; k <= last[parity]; k += 2u) {
            for (unsigned j = 0; j < count; ++j)
                decoder_advance(r->decoder_lut, &states[parity][j],
                    r->ring[(uint32_t)k & mask], r->ring[(uint32_t)(k + 1u) & mask]);
            unsigned kept = 0;
            for (unsigned j = 0; j < count; ++j) {
                unsigned dst = 0;
                for (; dst < kept; ++dst)
                    if (states[parity][dst].phase == states[parity][j].phase &&
                        states[parity][dst].x == states[parity][j].x) break;
                if (dst == kept) states[parity][kept++] = states[parity][j];
                else if (states[parity][j].y > states[parity][dst].y)
                    states[parity][dst].y = states[parity][j].y;
            }
            count = kept;
        }
        counts[parity] = count;
        if (count != 1) ++f->bootstrap_unknown;
    }
    return true;
}

/* Only the first EQ has no preceding synthetic porch. Use the actual
 * worker table to make its tip directly, against every surviving decoder
 * state. This needs at most 28 regular observation tokens per byte, not a
 * 256-phase carrier search. The remaining slots use the ordinary DDA. */
static bool first_v_tip(sync_flywheel_t *f, const sfw_ring_t *r, uint64_t a,
                        unsigned low, uint8_t *tip)
{
    decoder_bound_t states[2][32];
    unsigned counts[2];
    uint64_t last[2];
    uint8_t cells[28];
    uint32_t available = 0;
    if (!decoder_prefix(f, r, a, states, counts, last)) return false;
    for (unsigned phase = 0; phase < 256u; ++phase) {
        uint8_t raw = s_cell[phase];
        unsigned address = (raw >> 4) | ((raw & 15u) << 4);
        unsigned token = r->decoder_lut[address] >> 11;
        if (token >= 28u) continue;
        /* Strong-cell tokens must be independent of the middle byte. */
        bool regular = true;
        for (unsigned quadrant = 1; quadrant < 4u; ++quadrant)
            if ((r->decoder_lut[address | (quadrant << 8)] >> 11) != token) regular = false;
        if (!regular) continue;
        if (!(available & (1u << token))) cells[token] = raw;
        available |= 1u << token;
    }
    unsigned previous_phase = ph_at(r, a - 1u);
    for (unsigned k = 0; k < low; ++k) {
        unsigned parity = (unsigned)((a + k) & 1u);
        unsigned best = UINT32_MAX, worst_best = UINT32_MAX;
        uint8_t chosen = 0;
        for (unsigned token = 0; token < 28u; ++token) if (available & (1u << token)) {
            unsigned worst = 0, sum = 0;
            for (unsigned j = 0; j < counts[parity]; ++j) {
                unsigned code = r->decoder_lut[states[parity][j].phase * 32u + token] & 63u;
                if (code > worst) worst = code;
                sum += code;
            }
            int target = (int)previous_phase + f->repair_step_q8 / 256;
            int error = wrap8((int)r->phase[cells[token]] - target);
            if (error < 0) error = -error;
            unsigned score = (worst << 20) + (sum << 8) + (unsigned)error;
            if (score < best) { best = score; worst_best = worst; chosen = cells[token]; }
        }
        if (worst_best > 8u) return false;
        tip[k] = chosen;
        unsigned other = parity ^ 1u;
        uint8_t endpoint = k ? tip[k - 1u] : r->ring[(uint32_t)(a - 1u) & (r->ring_bytes - 1u)];
        for (unsigned j = 0; j < counts[other]; ++j)
            decoder_advance(r->decoder_lut, &states[other][j], endpoint, chosen);
        previous_phase = r->phase[chosen];
    }
    return true;
}

/* Strong cells (radius 4..6.5 fine-lane steps) per target phase; with the
 * acquisition mask only bytes whose bit 0 is the unflagged polarity. */
static void build_cells(const sfw_ring_t *r)
{
    int key = r->mask_bit0 ? (int)(r->clear_bit0 & 1u) : 2;
    if (s_cell_phase == r->phase && s_cell_mask == key) return;
    int best[256];
    for (int t = 0; t < 256; ++t) best[t] = 1 << 30;
    for (int raw = 0; raw < 256; ++raw) {
        if (r->mask_bit0 && (unsigned)(raw & 1) != (r->clear_bit0 & 1u)) continue;
        int i = signed4((unsigned)raw >> 4), q = signed4((unsigned)raw & 15u);
        int r2x4 = (2 * i + 1) * (2 * i + 1) + (2 * q + 1) * (2 * q + 1);
        if (r2x4 < 64 || r2x4 > 169) continue;
        for (int t = 0; t < 256; ++t) {
            int e = wrap8((int)r->phase[raw] - t);
            e = e < 0 ? -e : e;
            int score = e * 1024 + (r2x4 > 100 ? r2x4 - 100 : 100 - r2x4);
            if (score < best[t]) { best[t] = score; s_cell[t] = (uint8_t)raw; }
        }
    }
    s_cell_phase = r->phase;
    s_cell_mask = key;
}

/* Write [a, a + n1 + n2): n1 samples rotating step1, then n2 at step2 (Q8
 * Phase8 bins per sample), from the real phase before a. When the next real
 * sample is available its phase is met (minus next_step), the residual
 * spread over the run, so neither edge carries a glitch. */
static void synth(sync_flywheel_t *f, const sfw_ring_t *r, uint64_t a, uint32_t n1,
                  int32_t step1, uint32_t n2, int32_t step2, int32_t next_step,
                  bool bootstrap, uint64_t floor, uint64_t avail_end)
{
    uint64_t b = a + n1 + n2;
    /* Refuse a whole pulse if any edge is already unsafe. Clipping a
     * repair at TX's frontier would generate a malformed sync width. */
    if (a < floor || b >= avail_end) { ++f->skipped_floor; return; }
    uint32_t n = n1 + n2;
    if (!n) return;
    if (bootstrap) {
        uint8_t tip[128];
        if (n1 <= sizeof(tip) && first_v_tip(f, r, a, n1, tip)) {
            int64_t p0 = (int64_t)r->phase[tip[n1 - 1u]] * 256;
            int64_t residual = ((int64_t)ph_at(r, b) * 256 - next_step -
                                (p0 + (int64_t)n2 * step2)) % 65536;
            if (residual >= 32768) residual -= 65536;
            if (residual < -32768) residual += 65536;
            sync_synth_t blank;
            if (!sync_synth_init(&blank, (uint16_t)p0, (int32_t)residual, n2) ||
                !sync_repair_ledger_record(&f->repairs, a, b)) return;
            uint32_t mask = r->ring_bytes - 1u;
            for (unsigned k = 0; k < n1; ++k) r->ring[(uint32_t)(a + k) & mask] = tip[k];
            for (unsigned k = 0; k < n2; ++k)
                r->ring[(uint32_t)(a + n1 + k) & mask] = s_cell[sync_synth_next(&blank, step2)];
            return;
        }
        if (f->bootstrap_refused != UINT32_MAX) ++f->bootstrap_refused;
    }
    int64_t p0 = (int64_t)ph_at(r, a - 1u) * 256;
    if (step1 == f->repair_step_q8 && f->repair_kp_x100) {
        /* Match the constant-frequency tracker's phase lag at the tip
         * transition. The raw first phase alone is behind the observation
         * needed for an immediate low-kp discriminator step. H repair has
         * an explicit settling porch; a V slot starts straight at the tip. */
        int32_t porch = (int32_t)f->blank_q4 * 16;
        p0 += (int64_t)2 * (step1 - porch) * (100 - f->repair_kp_x100) / f->repair_kp_x100;
    }
    int32_t tip_lead = 0;
    if (n1 == PORCH && step2 == f->repair_step_q8 && f->repair_kp_x100)
        tip_lead = 2 * (step2 - step1) * (100 - f->repair_kp_x100) / f->repair_kp_x100;
    int64_t rot = (int64_t)n1 * step1 + (int64_t)n2 * step2 + tip_lead;
    int64_t resid = 0;
    if (b < avail_end) {
        int64_t target = (int64_t)ph_at(r, b) * 256 - next_step;
        resid = (target - (p0 + rot)) % 65536;
        if (resid >= 32768) resid -= 65536;
        if (resid < -32768) resid += 65536;
    }
    const uint32_t mask = r->ring_bytes - 1u;
    sync_synth_t state;
    if (!sync_synth_init(&state, (uint16_t)p0, (int32_t)resid, n)) return;
    /* The record precedes every write. Overflow conservatively taints the
     * merged span; synthesized bytes never become camera-clock evidence. */
    if (!sync_repair_ledger_record(&f->repairs, a, b)) return;
    for (uint32_t k = 0; k < n1; ++k)
        r->ring[(uint32_t)(a + k) & mask] = s_cell[sync_synth_next(&state, step1)];
    state.phase = (state.phase + (uint32_t)tip_lead) & 65535u;
    for (uint32_t k = 0; k < n2; ++k)
        r->ring[(uint32_t)(a + n1 + k) & mask] = s_cell[sync_synth_next(&state, step2)];

}

static inline int32_t sync_step(const sync_flywheel_t *f)
{
    return f->repair_step_q8;
}
static int32_t verified_sync_step(const sfw_ring_t *r)
{
    if (!r->program_verified || !r->decoder_lut) return 0;
    /* Deepest digitally stable candidates in the actual root worker LUT.
     * -6 MHz aliases at low kp; this is not a claim of loaded DAC voltage. */
    switch (r->verified_kp_x100) {
        case 85: case 80: case 75: case 70: return -9830; /* -6 MHz */
        case 65: return -9011; /* -5.5 MHz: deeper tips fail offset-camera transitions */
        case 60: return -8602; /* -5.25 MHz: -5.5 fails endpoint-residual H transitions */
        case 55: return -7782; /* -4.75 MHz: -5 fails complete broad-pulse transitions */
        case 50: return -7373; /* -4.5 MHz */
        default: return 0;
    }
}

static bool native_sync_unsafe(const sync_flywheel_t *f)
{
    /* A clean RF plateau may still exceed the active tracker gain's
     * circular phase margin. Such output needs repair despite clean raw
     * sync. Level learning is independently original/variance qualified. */
    /* Reserve 15% of the circular phase range for real Q4/RF phase error.
     * A steady carrier sweep alone missed clean .60-kp pulse dropouts.
     * Synthetic strong cells have a separate exact-transition test. */
    int32_t raw_limit = -(139264 * (int32_t)f->repair_kp_x100 + 500) / 1000;
    return f->levels && f->repair_step_q8 && (int32_t)f->sync_q4 * 16 < raw_limit;
}
static inline int32_t blank_step(const sync_flywheel_t *f) { return (int32_t)f->blank_q4 * 16; }

/* ---- geometry ---------------------------------------------------------- */
static inline bool pal(const sync_flywheel_t *f) { return f->nominal_q16 == (int32_t)SFW_PAL_LINE_Q16; }
static inline unsigned npre(const sync_flywheel_t *f) { return pal(f) ? 5u : 6u; }
static inline unsigned nbroad(const sync_flywheel_t *f) { return pal(f) ? 5u : 6u; }
static inline unsigned npost(const sync_flywheel_t *f) { return pal(f) ? 5u : 6u; }
static inline uint32_t eq_low(const sync_flywheel_t *f) { return pal(f) ? 94u : 92u; }
static inline uint64_t half_q16(const sync_flywheel_t *f) { return (uint64_t)f->period_q16 / 2u; }
static inline uint32_t field_lines(const sync_flywheel_t *f, bool half)
{
    /* 312.5 / 262.5 lines: from a half-line anchor the next one is on a line
     * start 313 (263) lines on; from a line-start anchor half a line after
     * 312 (262) lines. */
    return pal(f) ? (half ? 313u : 312u) : (half ? 263u : 262u);
}
static inline uint64_t rnd(uint64_t q16) { return (q16 + 32768u) >> 16; }

void sfw_init(sync_flywheel_t *f)
{
    memset(f, 0, sizeof(*f));
    f->state = SFW_ACQUIRE;
}

bool sfw_locked(const sync_flywheel_t *f)
{
    return f->state == SFW_TRACK && f->clean_since_acq >= LOCK_CLEAN;
}

int sfw_standard(const sync_flywheel_t *f)
{
    if (f->state != SFW_TRACK) return 0;
    return pal(f) ? 1 : 2;
}

static void start_track(sync_flywheel_t *f, uint64_t start, uint32_t nominal)
{
    f->state = SFW_TRACK;
    f->nominal_q16 = f->period_q16 = (int32_t)nominal;
    f->coast_period_q16 = (int32_t)nominal;
    f->clock_known = false;
    f->next_q16 = (start << 16) + nominal;
    f->clean_since_acq = 0;
    f->lines_since_clean = 0;
    f->v_valid = false;
    f->v_observed = false;
    f->v_observed_n = 0;
    f->v_observed_sum = 0;
    f->v_end_q16 = 0;
    f->relock_have = false;
    f->rebuild_lines = 0;
    f->pristine_run = 0;
    f->acq_phase = 0;
    ++f->acquisitions;
}

/* Streaming acquisition, resumable under the budget: a step histogram over
 * ~2 lines for the threshold, then sync-width low runs one line apart. */
static void acquire(sync_flywheel_t *f, const sfw_ring_t *r, uint64_t avail_end, uint32_t budget)
{
    if (avail_end < 8192u) return;
    uint64_t end = avail_end - 16u;
    if (f->scan_pos + r->ring_bytes / 2u < avail_end || f->scan_pos < 8u) {
        f->scan_pos = avail_end - 6000u;
        f->acq_run = 0;
        f->acq_have_last = false;
    }
    while (f->scan_pos < end && s_evals < budget) {
        uint64_t k = f->scan_pos;
        f->scan_pos += 3u;
        if (!original(f, k - 5u, k + 5u)) {
            f->acq_run = 0;
            f->acq_have_last = false;
            continue;
        }
        if (f->acq_phase == 0) {
            ++s_evals;
            ++f->acq_hist[step3(r, k) + 128];
            if (++f->acq_n < 6800u) continue;      /* ~8 lines: never one V interval */
            unsigned c = 0, p3 = 0, p50 = 0;
            bool got = false;
            for (unsigned v = 0; v < 256u; ++v) {
                c += f->acq_hist[v];
                if (!got && c * 100u >= 3u * f->acq_n) { p3 = v; got = true; }
                if (c * 2u >= f->acq_n) { p50 = v; break; }
            }
            memset(f->acq_hist, 0, sizeof(f->acq_hist));
            f->acq_n = 0;
            if (p50 < p3 + 10u) continue;               /* no modulation */
            f->thr = (int16_t)((int)p3 - 128 + (int)(p50 - p3) / 3);
            f->acq_phase = 1;
            f->acq_run = 0;
            f->acq_have_last = false;
            f->acq_runs = 0;
            continue;
        }
        if (++f->acq_n > 6800u) { f->acq_phase = 0; f->acq_n = 0; continue; }   /* 8 lines, no match */
        if (lowm(f, r, k)) { f->acq_run += 3u; continue; }
        uint32_t run = f->acq_run;
        f->acq_run = 0;
        if (run < 150u || run > 230u) continue;
        uint64_t start = k - run;
        if (f->acq_have_last) {
            uint64_t d = start - f->acq_last_start;
            if (d >= 2552u && d <= 2568u) { start_track(f, start, SFW_PAL_LINE_Q16); return; }
            if (d >= 2534u && d <= 2550u) { start_track(f, start, SFW_NTSC_LINE_Q16); return; }
        }
        f->acq_last_start = start;
        f->acq_have_last = true;
        if (++f->acq_runs > 24u) { f->acq_phase = 0; f->acq_n = 0; }   /* re-estimate */
    }
}

/* Low-run length from a leading edge (stride 3, one high sample tolerated). */
static uint32_t pulse_width(const sync_flywheel_t *f, const sfw_ring_t *r, uint64_t k,
                            uint32_t max, uint32_t *inside)
{
    uint32_t w = 0, highs = 0, total = 0;
    while (w < max && highs < 2u) {
        if (lowm(f, r, k + w)) highs = 0;
        else { ++highs; ++total; }
        w += 3u;
    }
    *inside = total - highs;
    return w - 3u * highs;
}

typedef enum { P_NONE, P_NORMAL, P_EQ, P_BROAD } pulse_t;

static pulse_t classify(uint32_t w, uint32_t inside)
{
    uint32_t n = w / 3u;
    if (w >= 150u && w <= 230u && inside <= n / 8u) return P_NORMAL;
    if (w >= 900u && inside <= n / 8u) return P_BROAD;
    if (w >= 70u && w <= 120u && inside <= 1u) return P_EQ;
    return P_NONE;
}

/* Leading edge (high -> low) nearest the prediction within +-win, refined to
 * one sample; returns the classified pulse. */
static pulse_t find_pulse(sync_flywheel_t *f, const sfw_ring_t *r, uint64_t pred,
                          int win, uint64_t avail_end, int *found, uint32_t *width)
{
    if (pred + (uint64_t)win + 8u > avail_end) return P_NONE;
    /* Include the independent falling-edge refinement's +/-6-sample
     * search and lowm's phase predecessor/lookahead, not only width reads. */
    uint64_t end = pred + (uint64_t)win + 1310u;
    if (end > avail_end) end = avail_end;
    if (!original(f, pred - (uint64_t)win - 8u, end)) return P_NONE;
    bool prev = lowm(f, r, pred - (uint64_t)win - 3u);
    for (int e = -win; e <= win; e += 3) {
        uint64_t k = pred + (uint64_t)(int64_t)e;
        bool cur = lowm(f, r, k);
        if (cur && !prev && lowm(f, r, k + 3u)) {
            uint32_t inside;
            uint64_t room = avail_end > k + 12u ? avail_end - k - 12u : 0u;
            uint32_t w = pulse_width(f, r, k, room < 1300u ? (uint32_t)room : 1300u, &inside);
            pulse_t t = classify(w, inside);
            if (t != P_NONE) {
                int edge = e;
                if (lowm(f, r, k - 2u)) edge = e - 2;
                else if (lowm(f, r, k - 1u)) edge = e - 1;
                *found = edge;
                *width = w;
                return t;
            }
        }
        prev = cur;
    }
    return P_NONE;
}

static bool steady_tip(sync_flywheel_t *f, const sfw_ring_t *r, uint64_t start)
{
    if (!original(f, start + 59u, start + 92u)) return false;
    int sum = 0, squares = 0;
    for (unsigned k = 0; k < 32u; ++k) {
        int d = step3(r, start + 60u + k);
        sum += d; squares += d * d;
    }
    s_evals += 32u;
    return 32 * squares - sum * sum <= 32 * 32 * 64;
}

static void observe_clock(sync_flywheel_t *f, uint64_t start)
{
    /* Reuse the edge already detected on each clean fast-path line; no extra
     * phase reads are needed. Accumulate all edges but divide only every 32
     * lines. Sparse endpoint fitting biased the coast clock enough to move
     * a repaired field boundary into the last real picture samples.
     * Fit holdover frequency over many genuine clean lines. Freezing the
     * most recent bang-bang PLL period would freeze its quantization jitter
     * too, accumulating timing error across a faded field. */
    if (!f->clock_known) {
        f->clock_known = true;
        f->clock_sample = start;
        f->clock_line = f->grid_line;
        f->clock_n = 1;
        f->clock_x = f->clock_xx = 0;
        f->clock_y = f->clock_xy = 0;
    } else {
        uint32_t lines = f->grid_line - f->clock_line;
        if (lines && start >= f->clock_sample) {
            uint64_t measured = (start - f->clock_sample) * 65536u;
            uint64_t nominal = (uint64_t)lines * (uint32_t)f->nominal_q16;
            int64_t residual = (int64_t)measured - (int64_t)nominal;
            ++f->clock_n;
            f->clock_x += lines;
            f->clock_xx += (uint64_t)lines * lines;
            f->clock_y += residual;
            f->clock_xy += (int64_t)lines * residual;
            if (lines >= 128u && f->clock_n >= 5u && (lines & 31u) == 0u) {
                /* Fit all qualified edges, not just two quantized endpoints.
                 * This suppresses their noise without storing a history array. */
                int64_t numerator = (int64_t)f->clock_n * f->clock_xy - (int64_t)f->clock_x * f->clock_y;
                uint64_t denominator = f->clock_n * f->clock_xx - f->clock_x * f->clock_x;
                int64_t slope = numerator / (int64_t)denominator;
                int64_t uncertainty = 4u * 65536u / lines;
                f->coast_period_q16 = slope >= -uncertainty && slope <= uncertainty ?
                    f->nominal_q16 : f->nominal_q16 + (int32_t)slope;
            }
            if (lines >= 1024u) {
                f->clock_sample = start;
                f->clock_line = f->grid_line;
                f->clock_n = 1;
                f->clock_x = f->clock_xx = 0;
                f->clock_y = f->clock_xy = 0;
            }
        }
    }
}

static void learn_levels(sync_flywheel_t *f, const sfw_ring_t *r, uint64_t start)
{
    if (!original(f, start + 29u, start + 158u) ||
        !original(f, start - 61u, start - 28u) ||
        !original(f, start + 191u, start + 208u)) return;
    int s = 0, b = 0, ss = 0, bb = 0;
    for (unsigned k = 0; k < 128u; ++k) {
        int d = step3(r, start + 30u + k); s += d; ss += d * d;
    }
    for (unsigned k = 0; k < 48u; ++k) {
        uint64_t at = k < 32u ? start - 60u + k : start + 192u + (k - 32u);
        int d = step3(r, at); b += d; bb += d * d;
    }
    s_evals += 176u;
    /* At weak RF the circular phase slope is biased toward zero. A plausible
     * line width alone cannot qualify a new frequency/level: relearning it
     * there would weaken the generated sync precisely when it is needed.
     * Retain the last stable levels unless both plateaux have <=8 phase-bin
     * standard deviation. Quantized strong cells still pass this guard. */
    if (128 * ss - s * s > 128 * 128 * 64 || 48 * bb - b * b > 48 * 48 * 64) return;
    /* Consecutive differences telescope: quantized endpoint errors do not
     * accumulate as they did in the archived every-third-sample average. */
    int sq4 = s * 16 / 128, bq4 = b * 16 / 48;
    if (!f->levels && !f->sync_q4 && !f->blank_q4) { f->sync_q4 = (int16_t)sq4; f->blank_q4 = (int16_t)bq4; }
    else {
        f->sync_q4 = (int16_t)((7 * f->sync_q4 + sq4) / 8);
        f->blank_q4 = (int16_t)((7 * f->blank_q4 + bq4) / 8);
    }
    f->levels = f->blank_q4 - f->sync_q4 >= 12 * 16;
    if (f->levels) f->thr = (int16_t)((f->sync_q4 + f->blank_q4) / 32);
}

static void advance(sync_flywheel_t *f, uint64_t next_q16)
{
    f->next_q16 = next_q16;
    ++f->grid_line;
}

static inline uint64_t v_start_q16(const sync_flywheel_t *f)
{
    return f->v_next_q16 - (uint64_t)npre(f) * half_q16(f) - half_q16(f) / 2u;
}
static inline uint64_t v_stop_q16(const sync_flywheel_t *f)
{
    return f->v_next_q16 + (uint64_t)(nbroad(f) + npost(f)) * half_q16(f) - half_q16(f) / 4u;
}

static void field_done(sync_flywheel_t *f)
{
    f->v_end_q16 = v_stop_q16(f);
    /* Advance the measured field clock directly. Rebuilding it from each
     * instantaneous H prediction injected H PLL jitter at field boundaries. */
    if (f->v_observed) f->v_next_q16 = f->v_observed_q16 +
        (uint64_t)(f->v_observed_sum / f->v_observed_n) + (uint64_t)V_EDGE_GUARD * 65536u;
    f->v_next_q16 += (uint64_t)(pal(f) ? 625u : 525u) * (uint32_t)f->coast_period_q16 / 2u;
    f->v_observed = false;
    f->v_observed_n = 0;
    f->v_observed_sum = 0;
    f->v_a_line += field_lines(f, f->v_half);
    f->v_half = !f->v_half;
    f->v_armed = false;
    if (f->v_found) ++f->vsyncs;
    else ++f->v_coasted;
    f->v_found = false;
}

static bool broad_at(sync_flywheel_t *f, const sfw_ring_t *r, uint64_t p, uint32_t h2)
{
    if (!original(f, p + 4u, p + h2 - 105u)) return false;
    return clean_pulse(f, r, p, h2 - HSYNC, 16u) && !lowm(f, r, p + h2 - 110u);
}

static bool eq_at(sync_flywheel_t *f, const sfw_ring_t *r, uint64_t p)
{
    if (!original(f, p + 4u, p + 405u)) return false;
    return clean_pulse(f, r, p, eq_low(f), 6u) && !lowm(f, r, p + 130u) && !lowm(f, r, p + 400u);
}

/* One half-line slot of the vertical interval. Strict presence (every point
 * of the pulse low, the gap after it high): a noisy real pulse that fails is
 * simply rebuilt (harmless); a broken one that noise lets pass would stay
 * broken. A missing first broad pulse with real ones half a line away is a
 * wrong field parity: shift instead of adding a second V sync. */
static void v_slot(sync_flywheel_t *f, const sfw_ring_t *r, uint64_t avail_end,
                   uint64_t floor, bool repair)
{
    int j = f->v_slot;
    uint64_t h2q = half_q16(f);
    uint64_t pos_q16 = f->v_next_q16 + (uint64_t)((int64_t)j * (int64_t)h2q);
    uint64_t p = rnd(pos_q16);
    uint32_t h2 = (uint32_t)rnd(h2q);
    bool broad = j >= 0 && j < (int)nbroad(f);
    bool present = broad ? broad_at(f, r, p, h2) : eq_at(f, r, p);
    /* Wrong parity means an equalizing slot here: its middle is high. A real
     * broad pulse with only a broken start stays put and is rebuilt. */
    if (j == 0 && !present && p + 2u * h2 + 16u <= avail_end &&
        original(f, p + h2 / 4u - 5u, p + 3u * h2 / 4u + 5u) &&
        !lowm(f, r, p + h2 / 4u) && !lowm(f, r, p + h2 / 2u) && !lowm(f, r, p + 3u * h2 / 4u)) {
        if (broad_at(f, r, p + h2, h2)) {
            f->v_next_q16 += h2q;
            if (f->v_half) ++f->v_a_line;
            f->v_half = !f->v_half;
            ++f->v_parity;
            return;
        }
        if (broad_at(f, r, p - h2, h2)) {
            f->v_next_q16 -= h2q;
            if (!f->v_half) --f->v_a_line;
            f->v_half = !f->v_half;
            ++f->v_parity;
            return;
        }
    }
    if (present && broad && (j == 0 || j == (int)nbroad(f) - 1)) {
        if (j == 0) f->v_found = true;
        int edge = 0;
        uint32_t width = 0;
        if (find_pulse(f, r, p, 16, avail_end, &edge, &width) == P_BROAD &&
            steady_tip(f, r, p + (uint64_t)(int64_t)edge)) {
            /* Fit both edges. Leading-edge quantization alone can bias the
             * whole subsequent field; the broad pulse's known width gives
             * an independent falling edge and a centered time estimate. */
            uint64_t leading = p + (uint64_t)(int64_t)edge;
            uint64_t falling = leading + width;
            /* Classifier width is a stride-three estimate. Refine the
             * falling edge independently before using both edges as a
             * clock measurement, then average the first and last genuine
             * broad pulses. This retains a bounded clean-path budget. */
            uint64_t stop = falling + 6u;
            falling -= 6u;
            while (falling < stop && lowm(f, r, falling)) ++falling;
            int64_t measured = (int64_t)((leading + falling - (h2 - HSYNC)) * 32768u) -
                               (int64_t)j * (int64_t)h2q;
            if (!f->v_observed) {
                f->v_observed_q16 = (uint64_t)measured;
                f->v_observed = true;
            }
            f->v_observed_sum += measured - (int64_t)f->v_observed_q16;
            ++f->v_observed_n;
        }
    }
    if ((!present || f->rebuild_lines || native_sync_unsafe(f)) && repair) {
        uint32_t low = broad ? h2 - HSYNC : eq_low(f);
        bool first = j == -(int)npre(f);
        int32_t tip = sync_step(f);
        synth(f, r, p, low, tip, h2 - low, blank_step(f), blank_step(f),
              first, floor, avail_end);
        ++f->slots_repaired;
    }
    if (++f->v_slot >= (int16_t)(nbroad(f) + npost(f))) field_done(f);
}

/* Search only proved original gaps. Each gap is qualified once; neither
 * lowm nor the width walker performs a provenance lookup per phase sample.
 * Complete read footprints, including the previous phase and width tail,
 * stay inside the gap. A patched previous H pulse cannot self-reacquire. */
static bool find_relock(sync_flywheel_t *f, const sfw_ring_t *r,
                         uint64_t line0, uint32_t period, unsigned win, int32_t *offset)
{
    uint64_t begin = line0 + win;
    uint64_t limit = line0 + period - win;
    for (unsigned i = 0; i <= f->repairs.count && begin < limit; ++i) {
        uint64_t end = limit;
        if (i < f->repairs.count && f->repairs.ranges[i].begin < end)
            end = f->repairs.ranges[i].begin;
        if (end > begin + 286u && original(f, begin, end)) {
            uint64_t k = begin + 21u;
            bool previous = lowm(f, r, k - 16u);
            /* A normal sync is at least 150 samples wide. A 16-sample
             * coarse stride cannot skip it; only a detected transition
             * pays for one-sample edge refinement and the full width. */
            for (; k + 265u <= end; k += 16u) {
                bool current = lowm(f, r, k);
                if (current && !previous) {
                    uint64_t edge = k - 15u;
                    while (edge < k && !lowm(f, r, edge)) ++edge;
                    uint32_t inside;
                    uint32_t width = pulse_width(f, r, edge, 260u, &inside);
                    if (classify(width, inside) == P_NORMAL && inside == 0u) {
                        uint32_t e = (uint32_t)(edge - line0);
                        *offset = e > period / 2u ? (int32_t)e - (int32_t)period : (int32_t)e;
                        return true;
                    }
                }
                previous = current;
            }
        }
        if (i < f->repairs.count && f->repairs.ranges[i].end > begin)
            begin = f->repairs.ranges[i].end;
    }
    return false;
}

unsigned sfw_run(sync_flywheel_t *f, const sfw_ring_t *r, uint64_t avail_end,
                 uint64_t rx_complete, uint64_t write_floor, bool allow_repair, uint32_t budget_evals)
{
    if (!f || !r || !r->ring || !r->phase || !r->ring_bytes ||
        (r->ring_bytes & (r->ring_bytes - 1u))) return 0;
    if (rx_complete < avail_end ||
        !sync_repair_ledger_advance(&f->repairs, rx_complete > r->ring_bytes ?
                                    rx_complete - r->ring_bytes : 0)) return 0;
    f->evidence_end = avail_end;
    f->repair_step_q8 = verified_sync_step(r);
    f->repair_kp_x100 = f->repair_step_q8 ? r->verified_kp_x100 : 0;
    if (allow_repair && !f->repair_step_q8 && f->context_refused != UINT32_MAX)
        ++f->context_refused;
    build_cells(r);
    s_evals = 0;
    s_lowm = s_steps = 0;
    f->evals = 0;
    f->lowm_evals = f->phase_steps = 0;
    if (f->state == SFW_ACQUIRE) {
        acquire(f, r, avail_end, budget_evals);
        store_evals(f);
        if (f->state == SFW_ACQUIRE) return 0;
    }
    unsigned processed = 0;
    while (f->state == SFW_TRACK && s_evals < budget_evals) {
        bool locked = sfw_locked(f);
        bool repair = allow_repair && locked && f->levels && f->repair_step_q8;
        uint64_t h2q = half_q16(f);
        /* Arm the coming vertical interval 4 lines ahead, on the line grid. */
        if (f->v_valid && !f->v_armed) {
            int32_t ahead = (int32_t)(f->v_a_line - f->grid_line);
            if (ahead < 0) { field_done(f); continue; }        /* passed unseen */
            if (ahead <= 4) {
                /* The field clock was advanced from its last genuine edge;
                 * do not replace it with instantaneous H PLL jitter. */
                f->v_slot = (int16_t)-(int)npre(f);
                f->v_found = false;
                f->v_armed = true;
            }
        }
        if (f->v_armed) {
            uint64_t slot_q16 = f->v_next_q16 + (uint64_t)((int64_t)f->v_slot * (int64_t)h2q);
            if (slot_q16 <= f->next_q16 + h2q / 2u) {
                uint64_t p = rnd(slot_q16);
                if (p + 2u * rnd(h2q) + 16u > avail_end) break;
                if (avail_end - p > MAX_LAG) {                  /* too late to write */
                    ++f->skipped_lines;
                    if (++f->v_slot >= (int16_t)(nbroad(f) + npost(f))) field_done(f);
                    continue;
                }
                v_slot(f, r, avail_end, write_floor, repair);
                continue;
            }
        }
        uint64_t pred = rnd(f->next_q16);
        if (pred + (f->v_valid ? NEED_LINE : NEED_V_ACQ) > avail_end) break;
        if (pred + r->ring_bytes / 2u < avail_end) {
            f->state = SFW_ACQUIRE;          /* data gone: start over */
            f->scan_pos = 0;
            break;
        }
        uint64_t base = f->next_q16;
        /* Lines inside the vertical interval belong to the slots. */
        if ((f->v_armed && base >= v_start_q16(f) && base < v_stop_q16(f)) ||
            (f->v_valid && base < f->v_end_q16)) {
            {
                advance(f, base + (uint64_t)(int64_t)f->period_q16);
                ++f->lines;
                ++processed;
                continue;
            }
        }
        if (avail_end - pred > MAX_LAG) {
            advance(f, base + (uint64_t)(int64_t)f->period_q16);
            ++f->skipped_lines;
            if (f->lines_since_clean < 0xFFFFFFFFu) ++f->lines_since_clean;
            continue;
        }
        int win = WIN_UNLOCKED;
        if (locked) {
            win = WIN_LOCKED + (int)(f->lines_since_clean / 16u);
            if (win > WIN_UNLOCKED) win = WIN_UNLOCKED;
        }
        int found = 0;
        uint32_t width = 0;
        pulse_t type = P_NONE;
        bool fast = false;
        /* On a known bad carrier, a cheap original-source proof tests for
         * recovery every line. An unqualified pulse coasts directly; the
         * wider window runs only at the bounded reacquisition cadence. */
        bool coasting = f->rebuild_lines && f->v_valid;
        if (locked && (f->lines_since_clean == 0u || coasting) &&
            original(f, pred - 20u, pred + 210u) &&
            !lowm(f, r, pred - 15u) && lowm(f, r, pred + 3u) && lowm(f, r, pred + 60u) &&
            lowm(f, r, pred + 120u) && lowm(f, r, pred + 165u) && !lowm(f, r, pred + 205u)) {
            int e = -9;
            while (e < 6 && !lowm(f, r, pred + (uint64_t)(int64_t)e)) e += 3;
            if (e > -9 && lowm(f, r, pred + (uint64_t)(int64_t)(e - 2))) e -= 2;
            else if (e > -9 && lowm(f, r, pred + (uint64_t)(int64_t)(e - 1))) e -= 1;
            found = e;
            width = HSYNC;
            type = P_NORMAL;
            ++f->fast_lines;
            fast = true;
        } else if (!coasting || (f->lines % RELOCK_EVERY) == 0u) {
            type = find_pulse(f, r, pred, win, avail_end, &found, &width);
        }
        uint64_t start = pred + (uint64_t)(int64_t)found;
        bool normal = type == P_NORMAL, broad = type == P_BROAD;
        uint32_t line = f->grid_line;

        /* PLL: phase 1/4, frequency 1/64 (1/16, 1/256 while pulses are
         * noisy), period within 0.3 %. Only pulses
         * with both edges intact (width 188 +- 12) steer: a pulse whose start
         * the noise ate has a late leading edge. */
        bool steer = normal && width + 12u >= HSYNC && width <= HSYNC + 12u &&
                     (!f->rebuild_lines || steady_tip(f, r, start));
        bool clean = steer && (!locked || (found >= -CLEAN_ERR && found <= CLEAN_ERR));
        if (steer) {
            /* Noisy pulses (rebuild mode): 4x lower gains for a calm raster. */
            int32_t err = found * 65536;
            int32_t pg = f->rebuild_lines ? 16 : 4, fg = f->rebuild_lines ? 256 : 64;
            f->period_q16 += err / fg;
            int32_t lim = f->nominal_q16 / 333;
            if (f->period_q16 > f->nominal_q16 + lim) f->period_q16 = f->nominal_q16 + lim;
            if (f->period_q16 < f->nominal_q16 - lim) f->period_q16 = f->nominal_q16 - lim;
            advance(f, base + (uint64_t)(int64_t)(f->period_q16 + err / pg));
        } else {
            advance(f, base + (uint64_t)(int64_t)f->period_q16);
        }

        /* First vertical sync: a broad pulse at this line start or half a
         * line later fixes the field phase on the line grid. */
        if (!f->v_valid && locked && f->levels) {
            int half = -1;
            int v_edge = found;
            uint32_t h2 = (uint32_t)rnd(h2q);
            if (broad) half = 0;
            else if (!normal) {
                uint32_t v_width = 0;
                if (find_pulse(f, r, pred + h2, win, avail_end, &v_edge, &v_width) == P_BROAD)
                    half = 1;
            }
            if (half >= 0) {
                /* Arm this very interval from its anchor: its remaining
                 * slots and lines are vertical-interval, not missing H. */
                f->v_valid = true;
                f->v_a_line = line;
                f->v_half = half == 1;
                /* The observed V edge need not equal the predicted H grid.
                 * Discarding this offset could start the next field repair
                 * early, overwriting received picture before blanking. */
                f->v_phase_q16 = v_edge * 65536;
                f->v_next_q16 = base + (half ? h2q : 0u) + (uint64_t)(int64_t)f->v_phase_q16;
                f->v_slot = 0;
                f->v_found = false;
                f->v_armed = true;
            }
        }
        bool in_v = f->v_armed && base >= v_start_q16(f) && base < v_stop_q16(f);

        ++f->lines;
        ++processed;
        if (clean) {
            ++f->clean;
            if (f->clean_since_acq < 0xFFFFu) ++f->clean_since_acq;
            f->lines_since_clean = 0;
            f->relock_have = false;
            /* Level measurements stay sparse; clock fitting reuses each
             * qualified fast-path edge without additional phase reads. */
            if ((f->clean & 31u) == 1u) learn_levels(f, r, start);
            if (fast && (!f->rebuild_lines || native_sync_unsafe(f)) && f->levels)
                observe_clock(f, start);
        } else {
            if (type == P_NONE) ++f->missed;
            if (f->lines_since_clean < 0xFFFFFFFFu) ++f->lines_since_clean;
        }

        /* Only missing or malformed pulses are rebuilt; a real pulse off the
         * prediction steers the PLL instead. Needs the field phase so the
         * vertical interval is never mistaken for missing H syncs. */
        bool keep = in_v || (normal && !native_sync_unsafe(f) && found >= -CLEAN_ERR && found <= CLEAN_ERR &&
                             clean_pulse(f, r, start, HSYNC, 12u));
        /* Only lines that must carry a normal H sync (field phase known,
         * outside the vertical interval) judge the signal. */
        if (in_v) {
            /* vertical interval: judged by its slots */
        } else if (keep) {
            if (f->pristine_run < 0xFFFFu) ++f->pristine_run;
            if (f->pristine_run >= 64u) f->rebuild_lines = 0;
        } else if (f->v_valid) {
            f->pristine_run = 0;
            if (!f->rebuild_lines) f->period_q16 = f->coast_period_q16;
            f->rebuild_lines = 1;
        }
        if (keep && !in_v && f->rebuild_lines) { keep = false; ++f->rebuilt; }
        /* With the field phase known every line here must carry a normal H
         * sync (the vertical interval belongs to the slots), so a run that
         * noise made look like an equalizing or broad pulse is rebuilt too. */
        if (!keep && repair && (f->v_valid || (normal && steer))) {
            /* With 1.2 us of the 1.65 us front porch at blanking, so noise
             * before the pulse cannot move its leading edge. */
            /* After a phase jump, a genuine classified normal H pulse may
             * still need low-kp output repair before the next V establishes
             * field phase. Its own observed edge is safe; unknown/malformed
             * runs cannot authorize this exception. */
            uint64_t at = f->v_valid ? rnd(base) : start;
            synth(f, r, at - PORCH, PORCH, blank_step(f), HSYNC, sync_step(f), blank_step(f),
                  false, write_floor, avail_end);
            ++f->repaired;
        }

        /* Coasting: look for the real sync anywhere in the line (a VTX that
         * came back at another phase); two matching finds 8 lines apart
         * move the flywheel there in one step. */
        if (locked && f->lines_since_clean >= RELOCK_AFTER && (f->lines % RELOCK_EVERY) == 0u &&
            pred > (uint64_t)rnd((uint64_t)f->period_q16) + 8u) {
            /* The previous line is complete: scan it outside the window
             * around its own prediction (where our pulse may sit). */
            uint32_t period = (uint32_t)rnd((uint64_t)f->period_q16);
            uint64_t line0 = pred - period;
            int32_t off = 0;
            if (find_relock(f, r, line0, period, (unsigned)win, &off)) {
                int32_t d = off - f->relock_off;
                if (f->relock_have && f->lines - f->relock_line == RELOCK_EVERY && d >= -12 && d <= 12) {
                    f->next_q16 += (uint64_t)((int64_t)off * 65536);
                    f->period_q16 = f->nominal_q16;
                    f->v_valid = false;
                    f->v_observed = false;
                    f->v_observed_n = 0;
                    f->v_observed_sum = 0;
                    f->clock_known = false;
                    f->v_armed = false;
                    f->v_end_q16 = 0;
                    f->lines_since_clean = 0;
                    f->relock_have = false;
                    ++f->relocks;
                } else {
                    f->relock_off = off;
                    f->relock_line = f->lines;
                    f->relock_have = true;
                }
            }
        }

        /* Loss duration alone never abandons the last known line/field
         * clock. Reacquisition searches original RX data alongside repair. */
    }
    store_evals(f);
    return processed;
}

/* C5VRX by Twotoz and contributors: root sync flywheel host prototype.
 * Adapted from legacy/c5vrx4/tools/test_sync_flywheel.c.
 * Synthetic PAL/NTSC CVBS (H sync, equalizing/broad vertical interval,
 * burst, picture) -> FM at 40 MS/s -> fine-lane Q4 bytes with noise and CFO
 * -> modeled 4092/32-byte ring geometry and ~16 KiB TX lag -> flywheel ->
 * actual root U85Q-LP arithmetic and LUT, for both pair parities.
 * Active IQ/DAC preservation and recovery pass-through are explicit gates.
 * SFW_KP_LEVEL selects the actual root LUT (0..7); SFW_CLOCK_PPM exercises
 * source-clock error without changing pulse/content gates. Not LCD evidence. */
#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "sync_flywheel.h"
#include "phase8_gain_lut.h"
#include "kp_tables.h"

#define RING 32768u
#define LAG 16384u
#define DSCR 4092u
#define FS 40.0

typedef struct { uint64_t a, b; double amp, noise; bool sync_only; } fade_t;
typedef struct {
    bool pal;
    double cfo_mhz, amp, noise;
    /* fades: half-open sample ranges with the given amplitude and noise */
    fade_t fade[8];
    unsigned nfade;
    uint64_t shift_at;      /* timing jumps by shift samples from here on */
    double shift;
} scene_t;

static unsigned s_seed = 1;
static unsigned s_kp_level;
static bool s_commit_mode;
/* Independent source-monitor fixture: these statuses are injected from the
 * known source scene, never derived from keeper output. This tests admission
 * and transition mechanics; a physical source qualifier remains required. */
static sfw_standard_t s_fallback;
static uint64_t s_source_return, s_generated_anchor;
typedef struct {
    uint8_t *ring;
    uint8_t before[RING];
    sync_flywheel_t *flywheel;
    const uint8_t *workspace;
    unsigned calls;
    bool refuse;
} commit_fixture_t;

static bool commit_fixture(void *opaque, uint64_t start, const uint8_t *samples, uint32_t bytes)
{
    commit_fixture_t *c = opaque;
    /* Every raw byte must still match its pre-synthesis value on entry.
     * Only this executor may modify the raw ring in callback mode. */
    assert(!memcmp(c->before, c->ring, RING));
    assert(samples == c->workspace && bytes && bytes <= 1280u);
    assert(!sync_repair_ledger_original(&c->flywheel->repairs, start, start + bytes,
                                        c->flywheel->evidence_end));
    ++c->calls;
    if (c->refuse) return false;
    for (uint32_t k = 0; k < bytes; ++k) c->ring[(start + k) & (RING - 1u)] = samples[k];
    memcpy(c->before, c->ring, RING);
    return true;
}
/* Inject the former loss-duration boundary without allocating 100+ million
 * samples. The surrounding six-field pure-noise scene still exercises every
 * ring wrap, repair slot and genuine shifted-camera recovery. */
static uint32_t s_inject_lost;
static uint64_t s_inject_at, s_inject_until;
static double s_last_lowm_per_line, s_last_steps_per_line;
static double urand(void) { s_seed = s_seed * 1103515245u + 12345u; return ((s_seed >> 8) & 0xFFFFFF) / 16777216.0; }
static double grand(void)
{
    double u = urand() + 1e-12, v = urand();
    return sqrt(-2 * log(u)) * cos(2 * M_PI * v);
}

/* Half-line geometry: PAL 1250 half-lines per frame, V intervals at 0 and 625
 * (5 pre-equalizing, 5 broad, 5 post-equalizing); NTSC 1050 at 0 and 525 (6/6/6). */
static double s_clock_ppm;
static double line_us(bool pal) { return (pal ? 64.0 : 572.0 / 9.0) * (1.0 + s_clock_ppm * 1e-6); }
static int v_kind(bool pal, long h) /* 0 none, 1 eq, 2 broad */
{
    long frame = pal ? 1250 : 1050, n = pal ? 5 : 6;
    long x = h % frame;
    long f2 = frame / 2;
    long y = x >= f2 ? x - f2 : x;
    if (y < n) return 1;
    if (y < 2 * n) return 2;
    if (y < 3 * n) return 1;
    return 0;
}

/* Deviation in MHz at time t (us). */
static double deviation(const scene_t *s, double t)
{
    double L = line_us(s->pal), H2 = L / 2;
    long h = (long)floor(t / H2 + 1e-9);
    double x = t - h * H2;
    int vk = v_kind(s->pal, h);
    if (vk == 1) return x < (s->pal ? 2.35 : 2.3) ? -4.3 : 0.0;
    if (vk == 2) return x < H2 - 4.7 ? -4.3 : 0.0;
    if (h & 1) x += H2;                 /* position in the full line */
    long frame = s->pal ? 1250 : 1050;
    long y = h % (frame / 2);
    if (x < 4.7) return -4.3;
    if (x >= 5.6 && x < 7.85) return 0.3 * sin(2 * M_PI * 4.43361875 * t);
    if (x < 10.5 || x > 62.0 || y < 46) return 0.0;
    double ramp = (x - 10.5) / 51.5;
    return 4.667 * ramp * (0.4 + 0.6 * ((h / 40) & 1));
}

/* Expected pulses: positions (samples) and kinds within [from, to). */
typedef struct { uint64_t pos; int kind; } pulse_t;
static unsigned expected(const scene_t *s, uint64_t from, uint64_t to, pulse_t *out, unsigned cap)
{
    double H2 = line_us(s->pal) / 2 * FS;
    unsigned n = 0;
    for (long h = (long)(from / H2) - 1; h * H2 < (double)to && n < cap; ++h) {
        if (h < 0) continue;
        int vk = v_kind(s->pal, h);
        uint64_t pos = (uint64_t)llround(h * H2);
        if (s->shift_at && pos >= s->shift_at) pos = (uint64_t)llround(h * H2 + s->shift);
        if (pos < from || pos >= to) continue;
        if (vk) out[n++] = (pulse_t){pos, vk};
        else if (!(h & 1)) out[n++] = (pulse_t){pos, 0};
    }
    return n;
}

static uint8_t *generate(const scene_t *s, uint64_t n)
{
    uint8_t *b = malloc(n);
    double phi = 0;
    for (uint64_t k = 0; k < n; ++k) {
        double t = k / FS;
        if (s->shift_at && k >= s->shift_at) t -= s->shift / FS;
        double amp = s->amp, noise = s->noise;
        for (unsigned j = 0; j < s->nfade; ++j)
            if (k >= s->fade[j].a && k < s->fade[j].b) {
                if (s->fade[j].sync_only) {
                    double L = line_us(s->pal), x = fmod(t, L);
                    if (x > 6.0) continue;
                }
                amp = s->fade[j].amp; noise = s->fade[j].noise;
            }
        /* Root U85Q DAC scaling: porch -1 MHz, sync -5.3 MHz (as in the
         * earlier actual-U85Q sync_iq_mux oracle), plus scene CFO. */
        phi += 2 * M_PI * (deviation(s, t) - 1.0 + s->cfo_mhz) / FS;
        double I = amp * cos(phi) + noise * grand(), Q = amp * sin(phi) + noise * grand();
        int i = (int)floor(I), q = (int)floor(Q);
        i = i < -8 ? -8 : i > 7 ? 7 : i;
        q = q < -8 ? -8 : q > 7 ? 7 : q;
        b[k] = (uint8_t)(((i & 15) << 4) | (q & 15));
    }
    return b;
}

/* Run the live ring: RX completes 4092-byte descriptors, TX reads LAG behind,
 * the flywheel runs every ~200 us. Returns the transmitted stream. */
static uint8_t *run_ring(const uint8_t *in, uint64_t n, sync_flywheel_t *f, const uint8_t *phase,
                         bool mask, uint8_t clear, double *evals_per_line, uint32_t budget)
{
    uint8_t *ring = calloc(RING, 1), *out = malloc(n);
    uint8_t workspace[1280];
    commit_fixture_t commit = {.ring = ring, .flywheel = f, .workspace = workspace};
    sfw_ring_t r = {.ring = ring, .ring_bytes = RING, .phase = phase, .mask_bit0 = mask,
                   .clear_bit0 = clear, .verified_kp_x100 = kp_level_x100[s_kp_level],
                   .program_verified = true, .decoder_lut = kp_tables[s_kp_level]};
    if (s_commit_mode) {
        r.workspace = workspace; r.workspace_bytes = sizeof(workspace);
        r.commit = commit_fixture; r.commit_ctx = &commit;
    }
    sfw_init(f);
    uint64_t rx = 0, tx = 0, evals = 0, lines0 = 0;
    uint64_t lowm = 0, steps = 0;
    bool injected = false;
    uint64_t source_end = 0;
    while (tx < n) {
        /* Match root's 8*4092 + 32-byte tail descriptor geometry. */
        uint64_t remaining = RING - (rx & (RING - 1u));
        uint64_t rx_to = rx + (remaining < DSCR ? remaining : DSCR);
        if (rx_to > n) rx_to = n;
        for (; rx < rx_to; ++rx) ring[rx & (RING - 1u)] = in[rx];
        /* Firmware: woken every ~100 us (each descriptor); analyses and
         * writes only data older than the newest completed descriptor,
         * which the control observers copy. */
        if (rx > DSCR) {
            unsigned protected = getenv("SFW_PROTECT_MULTI") ? 8190u : DSCR;
            uint64_t ceiling = rx > protected ? rx - protected : 0;
            uint64_t tx_now = rx > LAG ? rx - LAG : 0;
            uint64_t tx_local = tx_now & (RING - 1u);
            uint64_t tx_end = (tx_local / DSCR + 1u) * DSCR;
            if (tx_end > RING) tx_end = RING;
            /* Active-descriptor END plus prefetch guard, as a live reader
             * would infer from actual TX descriptor address. The older
             * tx_now+DSCR formula discarded up to one extra descriptor. */
            uint64_t floor = tx_now - tx_local + tx_end + 512u;
            uint32_t before = f->lines;
            uint32_t fields_before = f->vsyncs + f->v_coasted;
            if (s_inject_lost && !injected && rx >= s_inject_at) {
                assert(f->state == SFW_TRACK && f->v_valid && sfw_locked(f));
                f->lines_since_clean = s_inject_lost;
                f->rebuild_lines = 1;
                f->pristine_run = 0;
                f->period_q16 = f->coast_period_q16;
                injected = true;
            }
            if (s_commit_mode) memcpy(commit.before, ring, RING);
            if (s_fallback && ceiling > source_end) {
                sfw_source_t source = s_source_return && rx >= s_source_return ?
                    SFW_SOURCE_CANDIDATE : SFW_SOURCE_ABSENT;
                assert(sfw_bootstrap_source(f, 1u, s_fallback, source_end, ceiling,
                                             rx, RING, source));
                source_end = ceiling;
                if (!s_generated_anchor && f->state == SFW_ACQUIRE &&
                    f->source_samples >= 320000u && f->source_windows >= 4u)
                    s_generated_anchor = ceiling + 48u + 256u;
            }
            sfw_run(f, &r, ceiling, rx, floor, true, budget);
            if (s_commit_mode) assert(!memcmp(commit.before, ring, RING));
            assert(f->repairs.collapses == 0 && f->repairs.count <= 32u);
            if (injected && rx < s_inject_until) {
                assert(f->state == SFW_TRACK && f->v_valid && sfw_locked(f));
                assert(f->lines_since_clean >= s_inject_lost);
            }
            if (getenv("SFW_CLOCK_DEBUG") && fields_before != f->vsyncs + f->v_coasted)
                printf("clock field=%u next=%.5f period=%.8f coast=%.8f repair=%u observed=%.5f\n",
                       f->vsyncs + f->v_coasted, (double)f->v_next_q16 / 65536,
                       (double)f->period_q16 / 65536, (double)f->coast_period_q16 / 65536,
                       f->rebuild_lines, (double)f->v_observed_q16 / 65536);
            evals += f->evals;
            lowm += f->lowm_evals;
            steps += f->phase_steps;
            lines0 += f->lines - before;
            for (uint64_t k = ceiling; k < rx; ++k) assert(ring[k & (RING - 1u)] == in[k]);
        }
        uint64_t tx_to = rx > LAG ? rx - LAG : 0;
        if (rx == n) tx_to = n;
        for (; tx < tx_to; ++tx) out[tx] = ring[tx & (RING - 1u)];
    }
    if (evals_per_line) *evals_per_line = lines0 ? (double)evals / (double)lines0 : 0;
    s_last_lowm_per_line = lines0 ? (double)lowm / (double)lines0 : 0;
    s_last_steps_per_line = lines0 ? (double)steps / (double)lines0 : 0;
    free(ring);
    assert(!s_inject_lost || injected);
    assert(f->bootstrap_refused == 0);
    if (s_commit_mode) {
        assert(f->commit_calls == commit.calls && !f->commit_refused && !f->workspace_refused);
        assert(commit.calls == f->repaired + f->slots_repaired);
    } else assert(!f->commit_calls && !f->commit_refused && !f->workspace_refused);
    return out;
}

static uint8_t *decoded;
static unsigned tx_code(const uint8_t *b, uint64_t k)
{
    (void)b;
    return decoded[k];
}
/* Same U85Q-LP arithmetic as research/u85q_lp.py lp_codes(), using the
 * actual root LUT. Both byte pair parities are exercised. */
static void decode(const uint8_t *raw, uint64_t n, unsigned parity)
{
    free(decoded);
    decoded = calloc(n, 1);
    assert(decoded);
    unsigned state = 0, x = 0, counter = 0;
    static const unsigned bits[10] = {4,5,6,7,0,1,2,3,15,11};
    for (uint64_t k = parity; k + 9 < n; k += 2) {
        unsigned word = raw[k] | ((unsigned)raw[k+1] << 8), address = 0;
        unsigned y = (counter >> 10) & 63;
        decoded[k] = decoded[k+1] = (uint8_t)y;
        for (unsigned j = 0; j < 10; ++j) address |= ((word >> bits[j]) & 1u) << j;
        counter = ((y >> 1) << 10) | address;
        unsigned token = kp_tables[s_kp_level][address] >> 11;
        unsigned hi = ((counter >> 8) + (((x >> 1) << 2) | (state >> 3))) & 255;
        counter = (hi << 8) | (counter & 255);
        unsigned value = kp_tables[s_kp_level][(state << 5) + token];
        x = value & 63;
        state = (value >> 6) & 31;
    }
}

static unsigned sync_run(const uint8_t *b, uint64_t n, uint64_t pos, unsigned a, unsigned tol, int *edge)
{
    uint64_t k0 = pos > tol + 6 ? pos - tol : 6;
    k0 += (2u + a - (unsigned)(k0 % 2u)) % 2u;
    /* Longest sync-level run starting in the window: a lone 75 ns noise
     * click before the pulse is not the pulse. */
    unsigned best = 0;
    for (uint64_t k = k0; k <= pos + tol && k + 2 < n; k += 2) {
        if (tx_code(b, k) > 8u) continue;
        unsigned run = 0, highs = 0;
        uint64_t j = k;
        while (j + 2 < n && highs < 2u) { if (tx_code(b, j) <= 8u) highs = 0; else ++highs; j += 2; run += 2; }
        run -= 2u * highs;
        if (run > best) { best = run; if (edge) *edge = (int)((int64_t)k - (int64_t)pos); }
    }
    return best;
}

typedef struct { unsigned checked, ok, bad_width, missing, max_edge; } verdict_t;

static verdict_t verify(const scene_t *s, const uint8_t *out, uint64_t n, uint64_t from, uint64_t to, unsigned a)
{
    decode(out, n, a);
    static pulse_t p[40000];
    unsigned np = expected(s, from, to, p, 40000);
    double H2 = line_us(s->pal) / 2 * FS;
    verdict_t v = {0};
    for (unsigned j = 0; j < np; ++j) {
        if (p[j].pos + 3000 > n) break;
        int edge = 0;
        unsigned w = sync_run(out, n, p[j].pos, a, 15, &edge);
        unsigned lo, hi;
        if (p[j].kind == 0) { lo = 150; hi = 230; }
        else if (p[j].kind == 1) { lo = 70; hi = 125; }
        else { lo = (unsigned)(H2 - 188 - 60); hi = (unsigned)(H2 - 188 + 40); }
        ++v.checked;
        if (!w || w < lo || w > hi) {
            if (getenv("SFW_DEBUG") && v.missing + v.bad_width < 12)
            {
                int wide_edge = 0;
                unsigned wide = sync_run(out, n, p[j].pos, a, 96, &wide_edge);
                printf("  miss pos=%llu kind=%d field=%.3f width=%u edge=%d wide_width=%u wide_edge=%d\n", (unsigned long long)p[j].pos,
                       p[j].kind, p[j].pos / ((s->pal ? 312.5 : 262.5) * line_us(s->pal) * FS), w, edge, wide, wide_edge);
            }
        }
        if (!w) { ++v.missing; continue; }
        if (w < lo || w > hi) { ++v.bad_width; continue; }
        unsigned ae = (unsigned)(edge < 0 ? -edge : edge);
        if (getenv("SFW_DEBUG") && ae > 15u)
            printf("  edge pos=%llu kind=%d field=%.3f width=%u edge=%d\n", (unsigned long long)p[j].pos,
                   p[j].kind, p[j].pos / ((s->pal ? 312.5 : 262.5) * line_us(s->pal) * FS), w, edge);
        if (ae > v.max_edge) v.max_edge = ae;
        ++v.ok;
    }
    return v;
}

static uint64_t field_samples(bool pal) { return (uint64_t)llround((pal ? 312.5 : 262.5) * line_us(pal) * FS); }

static void verify_source_edges(bool pal)
{
    scene_t scene = {.pal = pal};
    uint64_t field = field_samples(pal);
    pulse_t pulses[2000];
    double half = line_us(pal) * FS / 2u;
    for (unsigned shifted = 0; shifted < 2u; ++shifted) {
        scene.shift_at = shifted ? 3 * field : 0;
        scene.shift = shifted ? 0.37 * 2u * half : 0;
        unsigned count = expected(&scene, 4 * field, 8 * field, pulses, 2000);
        assert(count > 1000u);
        for (unsigned j = 0; j < count; ++j) {
            uint64_t pos = pulses[j].pos, first = pos - 1u;
            while (first <= pos + 1u) {
                double t = (first - scene.shift) / FS;
                if (deviation(&scene, t) < -2.0) break;
                ++first;
            }
            assert(first <= pos + 1u);
            assert(deviation(&scene, (first - 1u - scene.shift) / FS) > -2.0);
            long half_index = lround((pos - scene.shift) / half);
            double continuous_edge = half_index * half + scene.shift;
            /* Expectations refer to the nearest sample to the continuous
             * source edge. The generated FM changes at the first sample on
             * or after it (ceil), at most one sample from that expectation.
             * Round the complete shifted edge, not two separate terms. */
            assert(fabs((double)pos - continuous_edge) <= 0.500001);
            assert((double)first - continuous_edge >= -0.00001 &&
                   (double)first - continuous_edge < 1.00001);
            assert(v_kind(pal, half_index) == pulses[j].kind);
        }
    }
    printf("PASS %s source-edge oracle: shifted/unshifted deviation and V-kind agree within one sample\n",
           pal ? "PAL" : "NTSC");
}

/* Explicit content-preservation gate: repaired sync is useful only if the
 * true source picture/snow remains. Compare both received IQ and the actual
 * U85Q-LP result across every active sample, not just total changed bytes. */
static void preserve_picture_from(const scene_t *s, const uint8_t *in, const uint8_t *out, uint64_t n, uint64_t from)
{
    uint8_t *active = calloc(n, 1);
    assert(active);
    uint64_t checked = 0;
    double L = line_us(s->pal), H2 = L / 2;
    for (uint64_t k = from; k < n; ++k) {
        double t = k / FS;
        if (s->shift_at && k >= s->shift_at) t -= s->shift / FS;
        long h = (long)floor(t / H2 + 1e-9);
        double x = t - h * H2;
        if (h & 1) x += H2;
        long y = h % ((s->pal ? 1250 : 1050) / 2);
        if (!v_kind(s->pal, h) && y >= 46 && x >= 10.5 && x <= 62.0) {
            active[k] = 1;
            if (in[k] != out[k]) {
                printf("active overwrite sample=%llu field=%.4f half=%ld x_us=%.3f input=%u output=%u\n",
                       (unsigned long long)k, k / (double)field_samples(s->pal), h, x, in[k], out[k]);
                assert(in[k] == out[k]);
            }
            ++checked;
        }
    }
    for (unsigned parity = 0; parity < 2; ++parity) {
        decode(in, n, parity);
        uint8_t *baseline = decoded;
        decoded = NULL;
        decode(out, n, parity);
        for (uint64_t k = 0; k < n; ++k) if (active[k]) assert(baseline[k] == decoded[k]);
        free(baseline);
    }
    printf("%s preservation: %llu active IQ samples and both LP parities unchanged\n",
           s->pal ? "PAL" : "NTSC", (unsigned long long)checked);
    free(active);
}

static void preserve_picture(const scene_t *s, const uint8_t *in, const uint8_t *out, uint64_t n)
{
    preserve_picture_from(s, in, out, n, 0);
}

static void scenario(bool pal)
{
    const char *name = pal ? "PAL" : "NTSC";
    uint64_t F = field_samples(pal), n = 10 * F;
    double L = line_us(pal) * FS;

    /* 1. Clean, strong signal: lock, find V, never write a byte. */
    scene_t clean = {.pal = pal, .cfo_mhz = 0.15, .amp = 5.0, .noise = 0.25};
    uint8_t *in = generate(&clean, n);
    sync_flywheel_t f;
    double epl = 0;
    uint8_t *out = run_ring(in, n, &f, c5vrx_phase8_gain_lut, false, 0, &epl, 4000);
    printf("%s clean: state=%d std=%d acquisitions=%u relocks=%u parity=%u lines_since_clean=%u\n", name,
           f.state, sfw_standard(&f), f.acquisitions, f.relocks, f.v_parity, f.lines_since_clean);
    printf("V clock vsync=%u coast=%u next=%llu period=%d nominal=%d clock_line=%u\n",
           f.vsyncs, f.v_coasted, (unsigned long long)f.v_next_q16, f.coast_period_q16, f.nominal_q16, f.clock_line);
    assert(f.state == SFW_TRACK && sfw_locked(&f) && sfw_standard(&f) == (pal ? 1 : 2));
    assert(f.vsyncs >= 8 && f.v_coasted == 0 && f.v_parity == 0);
    verdict_t baseline = verify(&clean, in, n, 4 * F, 9 * F, 0);
    if (baseline.ok == baseline.checked) {
        assert(f.repaired == 0 && f.slots_repaired == 0 && f.relocks == 0);
        assert(memcmp(in, out, n) == 0);
    } else {
        verdict_t repaired = verify(&clean, out, n, 4 * F, 9 * F, 0);
        printf("%s clean RF, kp=%u: actual input pulses %u/%u, repaired %u/%u\n",
               name, kp_level_x100[s_kp_level], baseline.ok, baseline.checked, repaired.ok, repaired.checked);
        assert((f.repaired > 0 || f.slots_repaired > 0) && f.relocks == 0);
        assert(repaired.missing == 0 && repaired.bad_width == 0 && repaired.max_edge <= 15u);
        preserve_picture(&clean, in, out, n);
    }
    printf("%s clean: lines=%u clean=%u fast=%u vsyncs=%u evals/line=%.1f\n",
           name, f.lines, f.clean, f.fast_lines, f.vsyncs, epl);
    if (baseline.ok == baseline.checked) assert(epl < 40.0);
    free(out);
    free(in);

    /* 2. Deep fades: 0.5 field of pure noise (H and V syncs gone), then
     * sync-only damage over two fields. Every expected pulse must still be
     * on the TX output, for every span alignment. */
    scene_t fade = clean;
    fade.fade[0] = (fade_t){(uint64_t)(4.3 * F), (uint64_t)(4.8 * F), 0.0, 1.6, false};
    fade.fade[1] = (fade_t){(uint64_t)(6.0 * F), (uint64_t)(8.0 * F), 0.6, 1.6, true};
    fade.nfade = 2;
    s_seed = 7;
    in = generate(&fade, n);
    out = run_ring(in, n, &f, c5vrx_phase8_gain_lut, false, 0, &epl, 4000);
    scene_t truth = clean;
    unsigned changed = 0;
    for (uint64_t k = 0; k < n; ++k) changed += in[k] != out[k];
    for (unsigned a = 0; a < 2u; ++a) {
        verdict_t raw = verify(&truth, in, n, 4 * F, 9 * F, a);
        verdict_t v = verify(&truth, out, n, 4 * F, 9 * F, a);
        printf("%s fades align=%u: input ok %u/%u -> output ok %u/%u (missing %u, bad width %u, max edge %u)\n",
               name, a, raw.ok, raw.checked, v.ok, v.checked, v.missing, v.bad_width, v.max_edge);
        assert(raw.ok + 300u < raw.checked);          /* the input really is broken */
        /* Over 2 fields with every sync edge destroyed the flywheel coasts on
         * its learned period: <= 1 sample per 100 lines plus span rounding. */
        assert(v.missing == 0 && v.bad_width == 0 && v.max_edge <= 15u);
    }
    printf("%s fades: repaired=%u slots=%u v_coasted=%u vsyncs=%u bytes_changed=%u evals/line=%.1f\n",
           name, f.repaired, f.slots_repaired, f.v_coasted, f.vsyncs, changed, epl);
    assert(f.repaired > 200u && f.slots_repaired > 10u && f.v_coasted >= 1u);
    preserve_picture(&fade, in, out, n);
    /* Complete callback stream must exactly match direct replay, including
     * both first-EQ bootstrap and every subsequent H/V patch. */
    if (s_commit_mode) {
        sync_flywheel_t direct;
        s_commit_mode = false;
        uint8_t *reference = run_ring(in, n, &direct, c5vrx_phase8_gain_lut, false, 0, NULL, 4000);
        s_commit_mode = true;
        assert(!memcmp(reference, out, n));
        free(reference);
    }
    free(out);

    /* 3. Acquisition mask: synthesized bytes keep bit 0 unflagged. */
    out = run_ring(in, n, &f, c5vrx_phase8_gain_lut, true, 0, NULL, 4000);
    unsigned flagged = 0, writes = 0;
    for (uint64_t k = 0; k < n; ++k) if (in[k] != out[k]) { ++writes; flagged += out[k] & 1u; }
    printf("%s mask: writes=%u flagged=%u repaired=%u\n", name, writes, flagged, f.repaired);
    assert(writes > 0 && flagged == 0 && f.repaired > 200u);
    free(out);
    free(in);

    /* 4. VTX gone for 0.6 field, back at another phase: re-lock in one step,
     * no double sync afterwards. */
    scene_t jump = clean;
    jump.fade[0] = (fade_t){(uint64_t)(4.0 * F), (uint64_t)(4.6 * F), 0.0, 1.6, false};
    jump.nfade = 1;
    jump.shift_at = (uint64_t)(4.6 * F);
    jump.shift = 0.37 * L;
    s_seed = 11;
    in = generate(&jump, n);
    out = run_ring(in, n, &f, c5vrx_phase8_gain_lut, false, 0, &epl, 4000);
    verdict_t after = verify(&jump, out, n, (uint64_t)(5.4 * F), 9 * F, 0);
    printf("%s re-lock: relocks=%u acquisitions=%u after: ok %u/%u missing %u\n",
           name, f.relocks, f.acquisitions, after.ok, after.checked, after.missing);
    assert(f.relocks >= 1u && after.missing == 0 && after.bad_width == 0);
    /* No stale flywheel pulse between real ones: count sync starts per line. */
    unsigned extra = 0;
    for (uint64_t k = (uint64_t)(5.5 * F); k + 9 < 9 * F; k += 3) {
        if (tx_code(out, k) <= 8u && tx_code(out, k - 3) > 8u && tx_code(out, k - 6) > 8u) {
            unsigned run = 0;
            for (uint64_t j = k; tx_code(out, j) <= 8u && run < 2000; j += 3) run += 3;
            if (run >= 150 && run <= 230) ++extra;
        }
    }
    unsigned lines = (unsigned)((9 * F - 5.5 * F) / L);
    printf("%s re-lock: %u H-sync-width pulses over %u lines\n", name, extra, lines);
    assert(extra <= lines + 20u);
    free(out);
    free(in);

    /* 5. Range edge: a weak carrier for six fields, every real pulse noisy.
     * All pulses clean on the output, timing within a span of the truth
     * (the PLL keeps steering on the noisy pulses), no repair beyond them. */
    scene_t weak = clean;
    weak.fade[0] = (fade_t){3 * F, 9 * F, 1.5, 0.9, false};
    weak.nfade = 1;
    s_seed = 23;
    in = generate(&weak, n);
    out = run_ring(in, n, &f, c5vrx_phase8_gain_lut, false, 0, &epl, 4000);
    printf("weak final levels=%u sync_q4=%d blank_q4=%d v_phase=%d skipped=%u floor=%u repair=%u v=%u\n",
           f.levels, f.sync_q4, f.blank_q4, f.v_phase_q16, f.skipped_lines, f.skipped_floor, f.repaired, f.slots_repaired);
    for (unsigned a = 0; a < 2u; ++a) {
        verdict_t raw = verify(&weak, in, n, 4 * F, 9 * F, a);
        verdict_t v = verify(&weak, out, n, 4 * F, 9 * F, a);
        printf("%s weak align=%u: input ok %u/%u -> output ok %u/%u (missing %u, bad width %u, max edge %u)\n",
               name, a, raw.ok, raw.checked, v.ok, v.checked, v.missing, v.bad_width, v.max_edge);
        /* The root LP decoder has state/latency absent from the archived
         * span75 DAC checker. Use the same 15-sample (375-ns) edge bound
         * as the faded-field test, while still requiring every H/V pulse. */
        assert(v.missing == 0 && v.bad_width == 0 && v.max_edge <= 15u);
    }
    printf("%s weak: repaired=%u slots=%u clean=%u relocks=%u acquisitions=%u evals/line=%.1f\n",
           name, f.repaired, f.slots_repaired, f.clean, f.relocks, f.acquisitions, epl);
    printf("%s weak detection: lowm/line=%.1f extra phase steps/line=%.1f\n", name,
           s_last_lowm_per_line, s_last_steps_per_line);
    assert(f.relocks == 0u && f.acquisitions == 1u && sfw_locked(&f));
    preserve_picture(&weak, in, out, n);
    /* Once clean reception returned, every byte must pass through again;
     * the keeper must not remain a permanently generated-sync source. */
    uint64_t recovered = (uint64_t)(9.5 * F);
    if (s_kp_level < 5u) assert(memcmp(in + recovered, out + recovered, (size_t)(n - recovered)) == 0);
    if (s_kp_level < 5u)
        printf("%s recovery: all received bytes pass through within half a field\n", name);
    else
        printf("%s recovery: active received content preserved; raw sync still exceeds verified kp margin\n", name);
    free(out);
    free(in);

    /* 6. Continuous holdover: crossing the old 40000-line reset and the
     * uint32 saturation boundary cannot abandon H/V or reacquire our own
     * generated bytes. Real camera returns at another phase after six pure
     * noise fields. This proves the state transition, not 2.5-s clock drift. */
    scene_t lost = clean;
    lost.fade[0] = (fade_t){3 * F, 9 * F, 0.0, 1.6, false};
    lost.nfade = 1;
    lost.shift_at = 9 * F;
    lost.shift = 0.37 * L;
    s_seed = 31;
    in = generate(&lost, n);
    for (unsigned boundary = 0; boundary < 2u; ++boundary) {
        s_inject_lost = boundary ? UINT32_MAX - 2u : 40000u;
        s_inject_at = 3 * F + 2u * DSCR;
        s_inject_until = 9 * F;
        out = run_ring(in, n, &f, c5vrx_phase8_gain_lut, false, 0, &epl, 4000);
        assert(f.state == SFW_TRACK && f.acquisitions == 1u && f.relocks == 1u);
        assert(f.evidence_refused > 0u);
        for (unsigned a = 0; a < 2u; ++a) {
            verdict_t hold = verify(&lost, out, n, 4 * F, 9 * F, a);
            assert(hold.missing == 0 && hold.bad_width == 0 && hold.max_edge <= 15u);
        }
        verdict_t returned = verify(&lost, out, n, (uint64_t)(9.5 * F), n - LAG, 0);
        assert(returned.missing == 0 && returned.bad_width == 0);
        printf("%s continuous loss: injected=%u acquisitions=%u relocks=%u refused=%u evals/line=%.1f\n",
               name, s_inject_lost, f.acquisitions, f.relocks, f.evidence_refused, epl);
        printf("%s loss detection: lowm/line=%.1f extra phase steps/line=%.1f\n", name,
               s_last_lowm_per_line, s_last_steps_per_line);
        /* During pure-noise holdover, preserve each active snow sample and
         * the actual LP result. Shifted reacquisition has a separate test. */
        preserve_picture(&lost, in, out, 9 * F);
        free(out);
    }
    s_inject_lost = 0;
    free(in);
}

static void provenance_admission(void)
{
    uint8_t ring[RING] = {0};
    sfw_ring_t r = {.ring = ring, .ring_bytes = RING, .phase = c5vrx_phase8_gain_lut};
    sync_flywheel_t f;
    sfw_init(&f);
    /* Even a structurally valid old acquisition scan must not histogram
     * synthesized input. The guard covers the phase predecessor too. */
    assert(sync_repair_ledger_record(&f.repairs, 3995u, 10000u));
    sfw_run(&f, &r, 10000u, 10000u, 0, false, 200u);
    assert(f.acq_n == 0 && f.state == SFW_ACQUIRE && f.evidence_refused > 0u);
    assert(!sync_repair_ledger_original(&f.repairs, 3995u, 10000u, 10000u));
    /* The protected observer ceiling stays old while actual completed RX
     * wraps: only the proved overwrite frontier expires repaired bytes. */
    sfw_run(&f, &r, 10000u, RING + 5000u, 0, false, 0);
    assert(f.repairs.oldest_readable == 5000u && f.repairs.count == 1u);
    assert(f.repairs.ranges[0].begin == 5000u);
    sfw_run(&f, &r, 10000u, 9000u, 0, false, 200u); /* impossible RX ceiling */
    assert(f.repairs.oldest_readable == 5000u && f.repairs.count == 1u);
    sfw_run(&f, &r, 10000u, RING + 10000u, 0, false, 0);
    assert(f.repairs.count == 0 && f.repairs.oldest_readable == 10000u);
    assert(!sync_repair_ledger_original(&f.repairs, 9999u, 10000u, 10000u));
    sfw_init(&f);
    assert(f.repairs.count == 0 && f.repairs.oldest_readable == 0 && f.evidence_refused == 0);
    for (unsigned invalid = 0; invalid < 3u; ++invalid) {
        sfw_init(&f);
        f.state = SFW_TRACK;
        f.clean_since_acq = 32;
        f.levels = true;
        f.period_q16 = f.nominal_q16 = SFW_PAL_LINE_Q16;
        f.coast_period_q16 = f.period_q16;
        f.next_q16 = 5000u * 65536u;
        f.v_valid = true;
        f.v_a_line = 10;
        r.verified_kp_x100 = invalid == 0 ? 61u : 85u;
        r.program_verified = invalid != 1;
        r.decoder_lut = invalid == 2 ? NULL : kp_tables[0];
        sfw_run(&f, &r, 10000u, 10000u, 0, true, 1);
        assert(f.context_refused == 1 && f.repair_step_q8 == 0);
        assert(f.repairs.count == 0 && f.repaired == 0 && f.slots_repaired == 0);
        for (unsigned k = 0; k < RING; ++k) assert(ring[k] == 0);
    }
    puts("PASS keeper provenance: synthetic acquisition refused, exact RX expiry, invalid frontier and reset");
    puts("PASS keeper program: unknown kp, unverified program and missing active LUT cannot repair");
}

static void commit_admission(void)
{
    uint8_t ring[RING] = {0}, workspace[1280];
    for (unsigned vertical = 0; vertical < 2; ++vertical)
        for (unsigned fault = 0; fault < 5; ++fault) {
            sync_flywheel_t f;
            sfw_init(&f);
            f.state = SFW_TRACK; f.clean_since_acq = 32; f.levels = true;
            f.period_q16 = f.nominal_q16 = f.coast_period_q16 = SFW_PAL_LINE_Q16;
            f.next_q16 = 5000u * 65536u; f.v_valid = true; f.v_a_line = 10;
            if (vertical) {
                f.v_armed = true; f.v_slot = 0; f.v_next_q16 = f.next_q16;
            }
            commit_fixture_t commit = {.ring = ring, .flywheel = &f, .workspace = workspace, .refuse = true};
            sfw_ring_t r = {.ring = ring, .ring_bytes = RING, .phase = c5vrx_phase8_gain_lut,
                           .verified_kp_x100 = 85, .program_verified = true, .decoder_lut = kp_tables[0],
                           .workspace = workspace, .workspace_bytes = sizeof(workspace),
                           .commit = commit_fixture, .commit_ctx = &commit};
            if (fault == 0) r.workspace = NULL;
            if (fault == 1) r.workspace_bytes = vertical ? 1279u : 235u;
            if (fault == 2) r.workspace = ring + 100u;
            if (fault == 3) r.workspace = (uint8_t *)(UINTPTR_MAX - 16u);
            memset(workspace, 0xa5, sizeof(workspace));
            memcpy(commit.before, ring, RING);
            sfw_run(&f, &r, 10000u, 10000u, 0, true, 1);
            assert(!memcmp(commit.before, ring, RING));
            assert(!f.repaired && !f.slots_repaired);
            if (fault < 4) {
                assert(f.workspace_refused == 1 && !f.commit_calls && !f.commit_refused);
                assert(!f.repairs.count && !commit.calls);
                for (unsigned k = 0; k < sizeof(workspace); ++k) assert(workspace[k] == 0xa5);
            } else {
                assert(!f.workspace_refused && f.commit_calls == 1 && f.commit_refused == 1 && commit.calls == 1);
                assert(f.repairs.count == 1 && f.repairs.ranges[0].end > f.repairs.ranges[0].begin);
                assert(!sync_repair_ledger_original(&f.repairs, f.repairs.ranges[0].begin,
                                                    f.repairs.ranges[0].end, 10000u));
            }
        }
    puts("PASS keeper executor: H/V prepare before commit, invalid workspace refused, failed commits leave IQ intact and taint provenance");
}

static void cold_boot(bool pal)
{
    uint64_t F = field_samples(pal), n = 5u * F;
    scene_t snow = {.pal = pal, .amp = 0, .noise = 1.6};
    /* The scene generator otherwise includes a camera; zero amplitude
     * produces only fresh received IQ noise for the complete acquisition. */
    s_seed = pal ? 91u : 93u;
    uint8_t *in = generate(&snow, n);
    sync_flywheel_t f;
    s_fallback = pal ? SFW_STD_PAL : SFW_STD_NTSC;
    s_generated_anchor = s_source_return = 0;
    uint8_t *out = run_ring(in, n, &f, c5vrx_phase8_gain_lut, false, 0, NULL, 4000);
    assert(f.state == SFW_GENERATED && !sfw_locked(&f) && sfw_standard(&f) == SFW_STD_UNKNOWN);
    assert(sfw_output_standard(&f) == (int)s_fallback && f.generated_starts == 1u);
    assert(f.acquisitions == 0u && f.clean == 0u && f.vsyncs == 0u);
    assert(f.repaired && f.slots_repaired && f.v_coasted >= 3u);
    assert(s_generated_anchor >= 320000u && !memcmp(in, out, 320000u));
    double h2 = (pal ? 1280.0 : 11440.0 / 9.0);
    for (unsigned parity = 0; parity < 2u; ++parity) {
        decode(out, n, parity);
        unsigned checked = 0;
        for (unsigned h = 0; ; ++h) {
            uint64_t p = s_generated_anchor + (uint64_t)llround(h * h2);
            if (p + 3000u >= n - LAG) break;
            if (p < F) continue;
            int kind = v_kind(pal, h);
            if (!kind && (h & 1u)) continue;
            int edge = 0;
            unsigned width = sync_run(out, n, p, parity, 15u, &edge);
            unsigned lo = kind == 2 ? (unsigned)(h2 - 248u) : kind ? 70u : 150u;
            unsigned hi = kind == 2 ? (unsigned)(h2 - 148u) : kind ? 125u : 230u;
            if (!(width >= lo && width <= hi))
                printf("cold %s kp=%u parity=%u h=%u kind=%d p=%llu width=%u edge=%d\n",
                    pal ? "PAL" : "NTSC", kp_level_x100[s_kp_level], parity, h, kind,
                    (unsigned long long)p, width, edge);
            assert(width >= lo && width <= hi);
            ++checked;
        }
        assert(checked > 800u);
    }
    /* Every sample outside generated blanking remains the received snow.
     * H repairs touch only the front porch + sync; V owns half-line slots. */
    for (uint64_t k = 320000u; k < n - LAG; ++k) {
        if (k < s_generated_anchor) { assert(in[k] == out[k]); continue; }
        double t = (double)(k - s_generated_anchor), hf = floor(t / h2);
        long h = (long)hf;
        int kind = v_kind(pal, h);
        double x = t - hf * h2;
        if (!kind && ((h & 1) || x >= 190.0)) {
            /* The last48 samples of a normal line are its synthetic porch. */
            bool next_v = v_kind(pal, h + 1) != 0;
            if ((h & 1) && x >= h2 - (next_v ? 2.0 : 50.0)) continue;
            if (x < 2.0 || x >= h2 - 2.0) continue; /* rounded half-line boundary */
            if (in[k] != out[k]) printf("snow overwrite %s k=%llu anchor=%llu h=%ld x=%.3f kind=%d\n",
                pal ? "PAL" : "NTSC", (unsigned long long)k, (unsigned long long)s_generated_anchor, h, x, kind);
            assert(in[k] == out[k]);
        }
    }
    free(out); free(in);

    /* Camera appears with another standard and another phase. The independent
     * candidate arrives before rendering those RX samples; fake V cannot
     * serve as the new camera anchor. Ordinary original acquisition follows. */
    bool returned_pal = !pal;
    F = field_samples(returned_pal); n = 7u * F;
    scene_t returned = {.pal = returned_pal, .amp = 5.0, .noise = 0.25,
                        .shift_at = 3u * F, .shift = 0.37 * line_us(returned_pal) * FS};
    returned.fade[0] = (fade_t){0, 3u * F, 0, 1.6, false}; returned.nfade = 1;
    in = generate(&returned, n);
    s_source_return = 3u * F; s_generated_anchor = 0;
    out = run_ring(in, n, &f, c5vrx_phase8_gain_lut, false, 0, NULL, 4000);
    assert(f.generated_starts == 1u && f.generated_cancels == 1u);
    assert(f.state == SFW_TRACK && sfw_locked(&f));
    assert(sfw_standard(&f) == (returned_pal ? SFW_STD_PAL : SFW_STD_NTSC));
    assert(f.acquisitions == 1u && f.vsyncs >= 2u);
    preserve_picture_from(&returned, in, out, n, 3u * F);
    free(out); free(in);
    s_fallback = SFW_STD_UNKNOWN; s_source_return = s_generated_anchor = 0;
    printf("PASS cold %s: selected clock with real snow, both DAC parities, independent candidate cancels fake V before changed-standard camera acquisition\n",
           pal ? "PAL" : "NTSC");
}

static void bootstrap_admission(void)
{
    sync_flywheel_t f;
    sfw_init(&f);
    assert(sfw_bootstrap_source(&f, 1, SFW_STD_PAL, 0, 8000, 8000, RING, SFW_SOURCE_ABSENT));
    assert(f.source_samples == 8000 && f.source_windows == 1);
    assert(sfw_bootstrap_source(&f, 1, SFW_STD_PAL, 16000, 24000, 24000, RING, SFW_SOURCE_ABSENT));
    assert(f.source_samples == 8000 && f.source_windows == 1); /* gap resets */
    assert(!sfw_bootstrap_source(&f, 1, SFW_STD_PAL, 16000, 24000, 24000, RING, SFW_SOURCE_ABSENT));
    assert(!f.source_absent && !f.source_windows); /* replay */
    assert(sync_repair_ledger_record(&f.repairs, 24000, 25000));
    assert(!sfw_bootstrap_source(&f, 1, SFW_STD_PAL, 24000, 26000, 26000, RING, SFW_SOURCE_ABSENT));
    assert(!f.source_absent); /* generated data never proves absence */
    assert(sfw_bootstrap_source(&f, 2, SFW_STD_NTSC, 26000, 32000, 32000, RING, SFW_SOURCE_ABSENT));
    assert(f.source_samples == 6000 && f.source_windows == 1);
    assert(sfw_bootstrap_source(&f, 2, SFW_STD_NTSC, 32000, 40000, 40000, RING, SFW_SOURCE_CANDIDATE));
    assert(!f.source_absent && !f.source_samples);
    puts("PASS cold bootstrap admission: contiguous original coverage, replay/taint refusal, context reset and candidate cancellation");
}

int main(void)
{
    setvbuf(stdout, NULL, _IONBF, 0);
    if (getenv("SFW_KP_LEVEL")) s_kp_level = (unsigned)strtoul(getenv("SFW_KP_LEVEL"), NULL, 10);
    assert(s_kp_level < KP_LEVELS);
    s_commit_mode = getenv("SFW_COMMIT") != NULL;
    printf("keeper kp=%u\n", kp_level_x100[s_kp_level]);
    if (getenv("SFW_CLOCK_PPM")) s_clock_ppm = strtod(getenv("SFW_CLOCK_PPM"), NULL);
    provenance_admission();
    commit_admission();
    bootstrap_admission();
    cold_boot(true);
    cold_boot(false);
    verify_source_edges(true);
    verify_source_edges(false);
    scenario(true);
    scenario(false);
    puts("PASS sync_flywheel: valid received sync untouched, actual-kp H/V repairs preserve active content at both parities, loss holdover and real re-lock");
    return 0;
}

/* C5VRX by Twotoz and contributors: native AGC releases follow the analog
 * field timing and land only in blank VBI lines. Synthetic CVBS FM only;
 * this is not RF or picture evidence. */
#include "native_vbi.h"
#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
    double period, line, broad, sync_broad, eq, sync_eq, blank_end, sync_line;
} std_t;
/* Times relative to the first vertical broad pulse, in us. */
static const std_t PAL = {20000.0, 64.0, 160.0, 27.3, 160.0, 2.35, 1408.0, 4.7};
static const std_t NTSC = {1001000.0 / 60.0, 63.5556, 190.67, 27.1, 190.67, 2.3,
                           1144.4, 4.7};
static uint8_t phase_lut[256];
static uint32_t rng = 12345;
static double frand(void) { rng = rng * 1103515245u + 12345u; return (rng >> 8) / 16777216.0; }

static double level(const std_t *s, double t, int scene, double noise)
{
    t = fmod(t, s->period);
    if (t < 0) t += s->period;
    double half = s->line / 2.0, v;
    if (t < s->broad) v = fmod(t, half) < s->sync_broad ? 0.0 : 0.3;
    else if (t < s->broad + s->eq) v = fmod(t - s->broad, half) < s->sync_eq ? 0.0 : 0.3;
    else if (t >= s->period - s->eq) v = fmod(t - (s->period - s->eq), half) < s->sync_eq ? 0.0 : 0.3;
    else {
        double pos = fmod(t - s->broad - s->eq, s->line);
        if (pos < s->sync_line) v = 0.0;
        else if (t < s->blank_end || pos < 10.5 || pos > s->line - 1.6) v = 0.3;
        else if (scene == 0) v = 0.32;                       /* dark */
        else if (scene == 2) v = pos >= 30.0 && pos < 34.0 ? 1.0 : 0.31; /* dark, small bright object */
        else v = 0.3 + 0.7 * fmod(floor(t) * 0.618034, 1.0); /* busy picture */
    }
    return v + noise * (frand() - 0.5);
}

static void synthesize(uint8_t *raw, size_t n, const std_t *s, double start_us,
                       int scene, double noise)
{
    double phase = frand() * 2.0 * M_PI;
    for (size_t k = 0; k < n; ++k) {
        double t = start_us + k / 40.0;
        double f = -3.0e6 + 6.0e6 * level(s, t, scene, noise);
        phase += 2.0 * M_PI * f * 25e-9;
        int i = (int)lround(6.0 * cos(phase) - 0.5), q = (int)lround(6.0 * sin(phase) - 0.5);
        if (i < -8) i = -8;
        if (i > 7) i = 7;
        if (q < -8) q = -8;
        if (q > 7) q = 7;
        raw[k] = (uint8_t)(((unsigned)i & 15u) << 4 | ((unsigned)q & 15u));
    }
}

static void check_windows(const std_t *s)
{
    uint8_t raw[4092];
    /* Fully inside the broad-pulse region. */
    synthesize(raw, sizeof(raw), s, 20.0, 1, 0.0);
    nv_window_t w = nv_window_analyze(raw, sizeof(raw), phase_lut);
    assert(w.valid && w.broad && w.low_pm > 500);
    /* Picture lines, busy and dark; equalizing and VBI blank lines. */
    const double starts[] = {3000.0, 9000.0, 15000.0, s->broad + 10.0, 700.0,
                             s->period - s->eq + 5.0};
    for (unsigned scene = 0; scene < 3u; ++scene)
        for (unsigned j = 0; j < sizeof(starts) / sizeof(starts[0]); ++j) {
            synthesize(raw, sizeof(raw), s, starts[j], (int)scene, 0.0);
            w = nv_window_analyze(raw, sizeof(raw), phase_lut);
            assert(w.valid && !w.broad && w.low_pm == 0);
        }
    /* Unmodulated carrier: no sync structure, no decision. */
    for (size_t k = 0; k < sizeof(raw); ++k) raw[k] = 0x50;
    assert(!nv_window_analyze(raw, sizeof(raw), phase_lut).valid);
    /* Noise never looks like broad pulses. */
    for (unsigned trial = 0; trial < 200u; ++trial) {
        for (size_t k = 0; k < sizeof(raw); ++k) raw[k] = (uint8_t)(frand() * 256.0);
        assert(!nv_window_analyze(raw, sizeof(raw), phase_lut).broad);
    }
    assert(!nv_window_analyze(raw, 40u * 31u, phase_lut).valid);
    assert(!nv_window_analyze(NULL, sizeof(raw), phase_lut).valid);
}

/* Random 1..3 ms sampler over synthetic video with a field offset; checks
 * lock and that every scheduled release lies in the blank VBI lines. */
static void check_lock(const std_t *s, int expected, double offset_us, double noise)
{
    nv_lock_t lock;
    nv_lock_reset(&lock);
    uint8_t raw[4092];
    uint64_t t = 1000000u, locked_at = 0;
    unsigned releases = 0;
    for (; t < 4000000u; t += 1000u + (uint64_t)(frand() * 2000.0)) {
        synthesize(raw, sizeof(raw), s, (double)t - offset_us, 1, noise);
        nv_window_t w = nv_window_analyze(raw, sizeof(raw), phase_lut);
        if (w.broad) nv_lock_feed(&lock, t + w.centroid_bytes / 40u);
        int standard = nv_lock_standard(&lock, t);
        assert(standard == NV_NONE || standard == expected);
        if (standard == NV_NONE) continue;
        if (!locked_at) locked_at = t;
        uint64_t release;
        assert(nv_lock_next_release(&lock, t, t + 3000u, &release));
        assert(release >= t + 3000u && release < t + 3000u + (uint64_t)s->period + 1u);
        double rel = fmod((double)release - offset_us, s->period);
        /* Leave >=150 us margin for firmware DMA-to-time uncertainty:
         * after the post-equalizing pulses, before the first picture line. */
        assert(rel >= s->broad + s->eq + 150.0 && rel <= s->blank_end - 150.0);
        ++releases;
    }
    assert(locked_at && locked_at < 2500000u && releases > 100u);
    /* Signal gone: the lock expires and the caller falls back to pacing. */
    assert(nv_lock_standard(&lock, t + NV_LOCK_TIMEOUT_US + 1u) == NV_NONE);
    uint64_t release;
    assert(!nv_lock_next_release(&lock, t + NV_LOCK_TIMEOUT_US + 2u, t, &release));
}

static void check_false_events(void)
{
    nv_lock_t lock;
    nv_lock_reset(&lock);
    uint64_t field = 20000u, base = 5000000u;
    for (unsigned k = 0; k < 40u; ++k) {
        nv_lock_feed(&lock, base + k * 7u * field + 80u);
        if (k % 5u == 2u) nv_lock_feed(&lock, base + k * 7u * field + 9000u);
    }
    uint64_t now = base + 40u * 7u * field;
    assert(nv_lock_standard(&lock, now) == NV_PAL);
    uint64_t release;
    assert(nv_lock_next_release(&lock, now, now + 3000u, &release));
    uint64_t rel = (release - base) % field;
    assert(rel >= 80u + NV_RELEASE_AFTER_US - 30u && rel <= 80u + NV_RELEASE_AFTER_US + 30u);
}

static nv_level_t lvl(unsigned p50, unsigned p95, unsigned clip, unsigned origin, unsigned coh)
{
    nv_level_t l = {(uint8_t)p50, (uint8_t)p95, (uint16_t)clip, (uint16_t)origin, (uint8_t)coh};
    return l;
}

static void check_demand(void)
{
    nv_demand_t d;
    memset(&d, 0, sizeof(d));
    nv_demand_reset(&d);
    nv_demand_released(&d, 1000);
    nv_level_t ok = lvl(7, 30, 5, 100, 80);
    /* Acquisition samples during settling never vote. */
    nv_level_t junk = lvl(60, 113, 400, 0, 10);
    assert(!nv_demand_update(&d, &junk, 1500));
    assert(!nv_demand_update(&d, &junk, 2999));
    for (unsigned k = 0; k < 3u; ++k) assert(!nv_demand_update(&d, &ok, 3000 + k));
    assert(d.base_p50 == 7);
    /* Held level within +-3 dB of the hardware's own level: hold. */
    for (unsigned k = 0; k < 50u; ++k) {
        nv_level_t drift = lvl(4 + k % 11u, 40, 10, 150, 70);
        assert(!nv_demand_update(&d, &drift, 4000 + k));
    }
    /* One bad window is not enough; two are. */
    nv_level_t weak = lvl(3, 20, 0, 400, 60);
    assert(!nv_demand_update(&d, &weak, 5000));
    assert(nv_demand_update(&d, &weak, 5001));
    assert(!nv_demand_update(&d, &ok, 5002));
    nv_level_t strong = lvl(9, 85, 30, 10, 90);
    assert(!nv_demand_update(&d, &strong, 5003) && nv_demand_update(&d, &strong, 5004));
    nv_level_t lost = lvl(2, 6, 0, 800, 10);
    assert(!nv_demand_update(&d, &lost, 5005) && nv_demand_update(&d, &lost, 5006));
    assert(d.demands == 3u);
    /* Severe saturation while held: release now, not at the next VBI. */
    nv_level_t saturated = lvl(40, 105, 300, 0, 90);
    assert(nv_demand_update(&d, &saturated, 5007) == NV_HOLD);
    assert(nv_demand_update(&d, &saturated, 5008) == NV_NOW);
    assert(nv_demand_update(&d, &strong, 5009) == NV_HOLD);
    assert(nv_demand_update(&d, &strong, 5010) == NV_VBI);
    assert(d.demands == 5u);
    /* Deep fade (>6 dB down, near-origin samples) is urgent; a mild one is not. */
    nv_level_t faded = lvl(1, 8, 0, 500, 40), mild = lvl(3, 20, 0, 300, 60);
    assert(nv_demand_update(&d, &faded, 5011) == NV_HOLD);
    assert(nv_demand_update(&d, &faded, 5012) == NV_NOW);
    assert(nv_demand_update(&d, &mild, 5013) == NV_HOLD);
    assert(nv_demand_update(&d, &mild, 5014) == NV_VBI);
    d.demands -= 2u;
    /* A new release relearns; loss still counts before the baseline. */
    nv_demand_released(&d, 6000);
    assert(d.demands == 5u && d.base_count == 0u);
    /* Saturated acquisition samples inside the settle time never vote. */
    assert(nv_demand_update(&d, &saturated, 6001) == NV_HOLD &&
           nv_demand_update(&d, &saturated, 6002) == NV_HOLD);
    assert(!nv_demand_update(&d, &lost, 8000) && nv_demand_update(&d, &lost, 8001));
    assert(!nv_demand_update(&d, NULL, 9000));
}

int main(void)
{
    for (unsigned r = 0; r < 256; ++r) {
        int i = (int8_t)(r & 240u) >> 4, q = (int8_t)((r & 15u) << 4) >> 4;
        double a = atan2(q + 0.5, i + 0.5);
        phase_lut[r] = (uint8_t)((long)lround(a * 128.0 / M_PI) & 255);
    }
    check_windows(&PAL);
    check_windows(&NTSC);
    check_lock(&PAL, NV_PAL, 1234.0, 0.0);
    check_lock(&PAL, NV_PAL, 17777.0, 0.15);
    check_lock(&NTSC, NV_NTSC, 4321.0, 0.0);
    check_lock(&NTSC, NV_NTSC, 9876.5, 0.15);
    check_lock(&PAL, NV_PAL, 333.0, 0.6);
    check_false_events();
    check_demand();
    puts("PASS native VBI: broad-pulse windows, PAL/NTSC field lock, VBI-only releases, "
         "false events, lock expiry and +-3 dB hardware-level hysteresis");
}

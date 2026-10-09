#include "fusion_demod.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>

static uint8_t lut[256];

int main(void)
{
    /* Phase LUT of the C5VRX convention: I high nibble, Q low, cell centres. */
    for (unsigned b = 0; b < 256; ++b) {
        int i = (int)(b >> 4), q = (int)(b & 15);
        double di = (i > 7 ? i - 16 : i) + .5, dq = (q > 7 ? q - 16 : q) + .5;
        lut[b] = (uint8_t)((long)lrint(atan2(dq, di) * 128 / M_PI) & 255);
    }
    /* A constant-frequency carrier with Gaussian phase noise of known rho:
     * the estimate rises with rho and saturates (4-bit phase). */
    static uint8_t buf[4096];
    int prev = -999;
    for (int db = 0; db <= 16; db += 4) {
        double rho = pow(10, db / 10.), sd = sqrt(1 / (2 * rho)), ph = 0;
        srand(11 + db);
        for (unsigned k = 0; k < sizeof(buf); ++k) {
            double u1 = (rand() + 1.) / (RAND_MAX + 2.), u2 = (rand() + 1.) / (RAND_MAX + 2.);
            double n = sqrt(-2 * log(u1)) * cos(2 * M_PI * u2) * sd;
            ph += .3;
            int i = (int)floor(6 * cos(ph + n)), q = (int)floor(6 * sin(ph + n));
            buf[k] = (uint8_t)(((i & 15) << 4) | (q & 15));
        }
        int est = fdemod_phase_cnr_x10(buf, sizeof(buf), lut);
        assert(est > prev);
        prev = est;
    }
    fdemod_t f = {0};
    int64_t t = 10000000;
    for (int k = 0; k < 10; ++k, t += 50000) assert(!fdemod_step(&f, 180, t));
    /* One low window (e.g. across a gain change) does not switch, also not
     * below the panic level; a sustained drop does (median of three). */
    assert(!fdemod_step(&f, 40, t)); t += 50000;
    assert(!fdemod_step(&f, 180, t)); t += 50000;
    assert(!fdemod_step(&f, 180, t)); t += 50000;
    assert(!fdemod_step(&f, 100, t)); t += 50000;     /* median still 180 */
    assert(fdemod_step(&f, 100, t) && f.edge); t += 50000;
    /* Recovery to the PAIR top gear needs 3 s continuously above 16.5. */
    for (int k = 0; k < 40; ++k, t += 50000) assert(!fdemod_step(&f, 170, t));
    assert(!fdemod_step(&f, 160, t)); t += 50000;      /* dip resets the hold */
    int64_t start = t; bool back = false;
    for (; t - start < 3200000; t += 50000) if (fdemod_step(&f, 170, t)) { back = true; break; }
    assert(back && !f.edge && t - start >= FUSION_PAIR_HOLD_US);
    /* Two windows below the panic level switch at once (after the dwell). */
    {
        fdemod_t g = {0};
        int64_t u = 50000000;
        for (int k = 0; k < 5; ++k, u += 50000) assert(!fdemod_step(&g, 180, u));
        assert(!fdemod_step(&g, 30, u)); u += 50000;
        assert(fdemod_step(&g, 30, u) && g.edge);
    }
    /* Minimum dwell after a switch. */
    assert(!fdemod_step(&f, 50, t + 50000) && !fdemod_step(&f, 50, t + 100000));
    assert(fdemod_step(&f, 50, t + FUSION_MIN_DWELL_US + 1) && f.edge);
    /* CVT alpha curve: monotone, 0.15..1 in eighths. */
    assert(fdemod_alpha_step(30) == 1 && fdemod_alpha_step(130) == CVT_ALPHA_STEPS);
    for (int c = 0; c < 200; c += 5) assert(fdemod_alpha_step(c + 5) >= fdemod_alpha_step(c));
    puts("PASS: CVT alpha curve; FusionDemod phase C/N estimator, gain-change-robust hysteresis, panic, dwell");
    return 0;
}

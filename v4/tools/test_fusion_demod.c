#include "fusion_demod.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>

int main(void)
{
    /* Ratio from the Rician formula at 4, 9.6 and 14 dB. */
    for (int db = 2; db <= 16; db += 2) {
        double rho = pow(10, db / 10.0), r = (rho + 1) * (rho + 1) / (2 * rho + 1);
        assert(abs(fdemod_cnr_x10((unsigned)lrint(r * 100)) - db * 10) <= 2);
    }
    assert(fdemod_cnr_x10(100) == -99);
    fdemod_t f = {0};
    int64_t t = 10000000;
    for (int k = 0; k < 10; ++k, t += 50000) assert(!fdemod_step(&f, 180, t));
    /* One noisy tick does not switch; two do (median of three). */
    assert(!fdemod_step(&f, 60, t)); t += 50000;
    assert(fdemod_step(&f, 60, t) && f.edge); t += 50000;
    /* Recovery needs 3 s continuously above 12 dB. */
    for (int k = 0; k < 40; ++k, t += 50000) assert(!fdemod_step(&f, 130, t));
    assert(!fdemod_step(&f, 110, t)); t += 50000;     /* dip resets the hold */
    int64_t start = t; bool back = false;
    for (; t - start < 3200000; t += 50000) if (fdemod_step(&f, 130, t)) { back = true; break; }
    assert(back && !f.edge && t - start >= FUSION_PAIR_HOLD_US);
    /* Minimum dwell after a switch. */
    assert(!fdemod_step(&f, 50, t + 50000) && !fdemod_step(&f, 50, t + 100000));
    assert(fdemod_step(&f, 50, t + FUSION_MIN_DWELL_US + 1) && f.edge && f.to_edge == 2 && f.to_pair == 1);
    puts("PASS: FusionDemod C/N from envelope ratio, fast-down/slow-up hysteresis, dwell");
    return 0;
}

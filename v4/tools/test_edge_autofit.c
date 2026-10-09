/* Host proof: on-device EDGE AutoFit synthesis equals edge_fsm.synthesize. */
#include "edge_autofit.h"
#include "edge_autofit_golden.h"
#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>

int main(void)
{
    edge_af_params_t p;
    edge_af_pinned(&p);
    static uint16_t low[1024];
    for (unsigned g = 0; g < EDGE_AF_GOLDEN_COUNT; ++g) {
        assert(edge_af_synthesize(&p, edge_af_golden_fit[g][0], edge_af_golden_fit[g][1], low));
        unsigned bad = 0;
        for (unsigned i = 0; i < 1024; ++i)
            if (low[i] != (edge_af_golden[g][i] & 0x1fffu)) ++bad;
        if (bad) printf("fit %u: %u words differ\n", g, bad);
        assert(!bad);
    }
    for (unsigned g = 0; g < EDGE_AF_GOLDEN_COUNT; ++g) {
        pair_af_remap(pair_af_base, edge_af_golden_fit[g][0], edge_af_golden_fit[g][1], low);
        for (unsigned i = 0; i < 1024; ++i) assert(low[i] == pair_af_golden[g][i]);
    }
    /* Nominal fit leaves PAIR untouched except rounding at most one code. */
    pair_af_remap(pair_af_base, 1.0, 1e6, low);
    for (unsigned i = 0; i < 1024; ++i) assert(abs((int)(low[i] & 63) - (int)(pair_af_base[i] & 63)) <= 1);
    for (unsigned g = 0; g < EDGE_AF_BLEND_COUNT; ++g) {
        edge_af_pinned(&p);
        assert(edge_af_synthesize(&p, edge_af_golden_fit[0][0], edge_af_golden_fit[0][1], low));
        edge_af_blend(low, edge_af_golden_alpha[g]);
        for (unsigned i = 0; i < 1024; ++i) assert(low[i] == edge_af_golden_blend[g][i]);
    }
    double d, c;
    /* Nominal VTX centred by AFC: blanking -436 kHz, sync -2350 kHz. */
    assert(edge_af_fit(-2350, -436, &d, &c) && fabs(d - 1.0) < .001 && fabs(c - 1e6) < 2e3);
    assert(edge_af_fit(-2790, -436, &d, &c) && fabs(d - 1.2299) < .002);
    assert(!edge_af_fit(-500, -436, &d, &c));      /* no sync separation */
    p.phases = 3;
    assert(!edge_af_synthesize(&p, 1.0, 1e6, low));
    puts("PASS: EDGE AutoFit synthesis, CVT blend and PAIR AutoFit remap bit-exact for all golden fits; fit estimator");
    return 0;
}

/* Host proof: on-device EDGE AutoFit synthesis equals edge_fsm.synthesize. */
#include "edge_autofit.h"
#include "edge_autofit_golden.h"
#include <assert.h>
#include <math.h>
#include <stdio.h>

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
    double d, c;
    /* Nominal VTX centred by AFC: blanking -436 kHz, sync -2350 kHz. */
    assert(edge_af_fit(-2350, -436, &d, &c) && fabs(d - 1.0) < .001 && fabs(c - 1e6) < 2e3);
    assert(edge_af_fit(-2790, -436, &d, &c) && fabs(d - 1.2299) < .002);
    assert(!edge_af_fit(-500, -436, &d, &c));      /* no sync separation */
    p.phases = 3;
    assert(!edge_af_synthesize(&p, 1.0, 1e6, low));
    puts("PASS: EDGE AutoFit C synthesis bit-exact with edge_fsm for all golden fits; fit estimator");
    return 0;
}

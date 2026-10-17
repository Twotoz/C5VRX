/* C5VRX by Twotoz/contributors: holdover PLL estimator. Host logic only. */
#include <assert.h>
#include <stdio.h>
#include "sync_clock_pll.h"

int main(void)
{
    unsigned checks = 0;
    const uint64_t nominal = (5720u * (uint64_t)SCP_DEN + 4u) / 9u; /* NTSC halfline */
    scp_t p = {0};
    uint64_t out = 0;
    /* +30 ppm: the camera gains 0.019 tick per halfline. */
    scp_start(&p, nominal, 1000u, 3);
    for (unsigned i = 0; i < SCP_MIN_ALIGNED; ++i) scp_aligned(&p);
    int64_t gained = (int64_t)(nominal * 30u / 1000000u) * SCP_MIN_HALVES / SCP_DEN; /* ticks */
    scp_trim(&p, (int32_t)(gained - 5));
    assert(!scp_estimate(&p, 1000u + SCP_MIN_HALVES - 1u, 8, &out)); ++checks; /* too short */
    assert(scp_estimate(&p, 1000u + SCP_MIN_HALVES, 3 + 5, &out)); ++checks;
    int64_t ppm_x1000 = ((int64_t)out - (int64_t)nominal) * 1000000000 / (int64_t)nominal;
    assert(ppm_x1000 > 29000 && ppm_x1000 < 31000); ++checks;  /* residual counted, start residual removed */
    assert(p.updates == 1u && p.period_q == out && !p.trims && p.aligned == 0u); ++checks;
    /* Beyond 200 ppm: rejected, period kept, window restarts. */
    scp_start(&p, nominal, 0u, 0);
    for (unsigned i = 0; i < SCP_MIN_ALIGNED; ++i) scp_aligned(&p);
    scp_trim(&p, 20000);
    assert(!scp_estimate(&p, SCP_MIN_HALVES, 0, &out) && p.rejected == 1u && p.period_q == nominal); ++checks;
    /* Sparse camera evidence: no estimate; the window restarts after 4x. */
    scp_start(&p, nominal, 0u, 0);
    for (unsigned i = 0; i < 10u; ++i) scp_aligned(&p);
    assert(!scp_estimate(&p, SCP_MIN_HALVES, 0, &out) && p.start_half == 0u); ++checks;
    assert(!scp_estimate(&p, 4u * SCP_MIN_HALVES + 1u, 0, &out) && p.start_half == 4u * SCP_MIN_HALVES + 1u); ++checks;
    /* Negative drift and the iterator's period conversion. */
    assert(scp_period_q(635u, 5u, 9u) == nominal); ++checks;
    scp_start(&p, nominal, 0u, 0);
    for (unsigned i = 0; i < SCP_MIN_ALIGNED; ++i) scp_aligned(&p);
    scp_trim(&p, -300);
    assert(scp_estimate(&p, SCP_MIN_HALVES, 0, &out) && out < nominal); ++checks;
    printf("PASS: %u holdover PLL checks\n", checks);
    return 0;
}

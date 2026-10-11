/* C5VRX by Twotoz/contributors: shadow ownership/context regression checks. */
#include "../main/lane_shadow_logic.h"
#include <assert.h>
#include <stdio.h>
int main(void)
{
    uint8_t raw[4092];
    for (unsigned k = 0; k < sizeof(raw); ++k) raw[k] = (uint8_t)k;
    iqf_shadow_t s = {0};
    iqf_observe(&s, raw, sizeof(raw), 1000, 1, 1, true);
    assert(s.valid && s.windows == 1 && !s.ready);
    iqf_observe(&s, NULL, 0, 2000, 1, 1, false);
    assert(!s.valid && !s.ready && !s.confirmations && s.proposed == IQF_CURRENT);
    for (unsigned code = 0; code < 256; ++code) {
        memset(raw, code, sizeof(raw));
        iqf_observe(&s, raw, sizeof(raw), 3000000u + code * 1000000u, 2, 2, true);
        if (s.proposed != IQF_CURRENT) assert(s.advantage_pm > 0);
        if (s.ready) assert(s.advantage_pm >= 100 && s.confirmations >= 3);
        assert(s.proposed < IQF_CANDIDATES);
    }
    iqf_observe(&s, raw, sizeof(raw), 500000000, 3, 2, true);
    assert(!s.ready && s.confirmations <= 1 && s.epoch == 3);
    iqf_observe(&s, raw, sizeof(raw), 500000001, 3, 3, true);
    assert(!s.ready && s.gain_epoch == 3);
    iqf_observe(&s, raw, sizeof(raw), 1, 3, 3, true);
    assert(!s.ready);
    puts("IQLaneFusion shadow context/ownership checks passed");
}

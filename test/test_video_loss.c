/* C5VRX by Twotoz/contributors: issue #213 actual sampled loss policy. */
#include <assert.h>
#include <stdio.h>
#include "video_loss.h"
int main(void)
{
    video_loss_t p = {0};
    assert(!video_loss_update(&p, 1, true, true, false, 0, false));
    for (unsigned k = 1; k < 4; ++k)
        assert(!video_loss_update(&p, 1, true, true, false, k * 2000u, false));
    assert(video_loss_update(&p, 1, true, true, false, 8000, false));
    assert(video_loss_update(&p, 1, true, false, false, 10000, true));
    assert(!video_loss_update(&p, 1, true, true, true, 12000, true));
    for (unsigned k = 1; k < 6; ++k)
        assert(!video_loss_update(&p, 1, true, true, false, 12000 + k * 2000u, false));
    assert(video_loss_update(&p, 1, true, true, false, 24000, false));
    /* Missing copies alone never manufacture camera-sync loss. */
    p = (video_loss_t){0};
    for (unsigned k = 0; k < 100; ++k)
        assert(!video_loss_update(&p, 2, true, false, false, k * 50000u, false));
    /* B/W windows alternate one sync (unconfirmed) and two confirmed syncs. */
    p = (video_loss_t){0};
    for (unsigned k = 0; k < 100; ++k)
        assert(!video_loss_update(&p, 3, true, true, k % 2, k * 50000u, false));
    /* Retune/settle/menu/calibration never count as missing camera sync. */
    assert(!video_loss_update(&p, 4, true, true, false, 6000000, false));
    assert(!video_loss_update(&p, 4, false, true, false, 7000000, false));
    assert(video_loss_update(&p, 5, false, true, true, 8000000, true));
    assert(!video_loss_update(&p, 5, true, true, true, 8050000, true));
    puts("PASS: sync-loss delay, first-sync return, hysteresis, missing copies, B/W, contexts and other owners");
    return 0;
}

/* C5VRX by Twotoz and contributors: export the actual firmware target
 * for the range audit; no independently reimplemented selection curve. */
#include <stdio.h>
#include "kp_ladder.h"
#include "kp_tables.h"
int main(void)
{
    const int devs[] = {0, 69, 100, 140, 170, 200};
    for (unsigned d = 0; d < sizeof(devs) / sizeof(devs[0]); ++d)
        for (int cn = -20; cn <= 100; cn += 10)
            for (int narrow = 0; narrow <= 1; ++narrow)
                for (int wide = 0; wide <= 1; ++wide) {
                    unsigned level = kp_ladder_target_g(cn, devs[d], true, narrow, wide);
                    printf("{\"dev\":%d,\"cn\":%d,\"narrow\":%d,\"wide\":%d,\"kp\":%u}\n",
                           devs[d], cn, narrow, wide, kp_level_x100[level]);
                }
    return 0;
}

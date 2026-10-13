/* C5VRX by Twotoz/contributors: actual gain/profile gates on synthetic IQ.
 * Input records: uint32 little-endian case ID, filter MHz, 256 IQ bytes. */
#include <stdio.h>
#include <stdint.h>
#include "gain.h"
#include "profile_logic.h"
#include "cn_estimate.h"
#include "phase8_gain_lut.h"
unsigned char phy_param[2048];
int main(int argc, char **argv)
{
    if (argc != 2) return 2;
    FILE *f = fopen(argv[1], "rb");
    if (!f) return 2;
    uint32_t id, filter;
    uint8_t raw[256];
    printf("case_id,coherence,p50,clip_pm,env_x100,cn_x10,old_gate,new_gate\n");
    while (fread(&id, sizeof(id), 1, f) == 1) {
        if (fread(&filter, sizeof(filter), 1, f) != 1 || fread(raw, sizeof(raw), 1, f) != 1) return 3;
        dg_observation_t o = dg_measure(raw, sizeof(raw), c5vrx_phase8_gain_lut, 1);
        int cn = dg_stuck(&o) ? CN_UNKNOWN_X10 : cn_estimate_env_x10(o.env_ratio_x100, (int)filter);
        bool gain_ok = dev_gain_usable(o.p50, o.clip_pm, true);
        bool quality = cn >= DEV_MONITOR_MIN_CN_X10;
        printf("%u,%u,%u,%u,%u,%d,%d,%d\n", id, o.coherence, o.p50, o.clip_pm, o.env_ratio_x100, cn,
               gain_ok && quality && o.coherence >= 80, gain_ok && quality);
    }
    fclose(f);
    return 0;
}

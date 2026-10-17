/* C5VRX by Twotoz/contributors: run production DC estimator on replay IQ.
 * Offline probe only; no CPU in the live 40-MS/s path. */
#include "dsp.h"
#include "dc_live.h"
#include <stdio.h>
#include <stdlib.h>
int main(int argc, char **argv)
{
    if (argc != 2) return 2;
    FILE *f = fopen(argv[1], "rb");
    if (!f) return 2;
    puts("id,circle,dc_i_mcells,dc_q_mcells,env_x100,mean_i_mcells,mean_q_mcells,rail_pm,live_qualified");
    uint32_t header[2];
    while (fread(header, sizeof(header), 1, f) == 1) {
        if (header[1] < 64 || header[1] > 8192) return 2;
        uint8_t raw[8192];
        if (fread(raw, 1, header[1], f) != header[1]) return 2;
        dsp_circle_t c = {0}; int di, dq; unsigned env, rail = 0;
        dsp_circle_sums(raw, header[1], &c);
        int circle = dsp_circle_dc(&c, &di, &dq, &env);
        for (unsigned k = 0; k < header[1]; ++k)
            rail += dsp_i(raw[k]) == -8 || dsp_i(raw[k]) == 7 || dsp_q(raw[k]) == -8 || dsp_q(raw[k]) == 7;
        const unsigned rail_pm = rail * 1000 / header[1];
        printf("%u,%d,%d,%d,%u,%lld,%lld,%u,%u\n", header[0], circle, di, dq, env,
               (long long)(c.x * 500 / c.n), (long long)(c.y * 500 / c.n), rail_pm,
               (unsigned)dc_live_fit(&c, rail_pm, NULL, NULL, NULL));
    }
    fclose(f);
    return 0;
}

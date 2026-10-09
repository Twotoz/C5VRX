/* Host probe: run the firmware AFC v2 measurement on exported IQ snapshots
 * (C5VRX by Twotoz/contributors). Diagnoses why AFC/AutoFit get no valid
 * windows on the board without guessing (PR #190, 2026-10-09). */
#include "afc_v2.h"
#include "phase8_gain_lut.h"
#include <stdio.h>

int main(int argc, char **argv)
{
    for (int a = 1; a < argc; ++a) {
        FILE *f = fopen(argv[a], "rb");
        if (!f) continue;
        static uint8_t b[65536];
        size_t n = fread(b, 1, sizeof(b), f);
        fclose(f);
        if (n > AFC2_MAX_SAMPLES) n = AFC2_MAX_SAMPLES;   /* firmware window size limit */
        afc2_result_t r = afc2_measure(b, n, c5vrx_phase8_gain_lut);
        printf("%s n=%zu stationary=%d lines=%u std=%u pol=%d burst_x10=%d sync_pairs=%u porch_pairs=%u "
               "sync_khz=%d porch_khz=%d\n", argv[a], n, (int)afc2_envelope_stationary(b, n), (unsigned)r.lines,
               (unsigned)r.standard, (int)r.polarity, (int)r.burst_x10, (unsigned)r.sync_pairs,
               (unsigned)r.porch_pairs, (int)r.sync_khz, (int)r.porch_khz);
    }
    return 0;
}

/* C5VRX by Twotoz and the C5VRX contributors: sync and porch levels of board
 * IQ snapshots (console I, tools/capture_iq_snapshot.py), measured by the
 * production afc2_measure() on firmware-sized 4092-byte windows.
 *
 *   cc -O2 -std=c11 -I../main afc_snapshot_levels.c -lm -o afc_snapshot_levels
 *   ./afc_snapshot_levels data/afc_2026-10-18/s_*.bin
 *
 * Per snapshot: the mean porch / sync level (kHz) of the windows with
 * confirmed lines and their span (deviation x 1.914 MHz). Host analysis of
 * captured IQ, not a live measurement. */
#include <stdio.h>
#include "afc.h"
#include "phase8_gain_lut.h"

int main(int argc, char **argv)
{
    for (int a = 1; a < argc; ++a) {
        static uint8_t raw[8190];
        FILE *f = fopen(argv[a], "rb");
        if (!f) { printf("%s unreadable\n", argv[a]); continue; }
        size_t n = fread(raw, 1, sizeof(raw), f);
        fclose(f);
        long porch = 0, sync = 0;
        unsigned windows = 0, lines = 0;
        for (size_t o = 0; o + 4092u <= n; o += 2046u) {
            afc2_result_t r = afc2_measure(raw + o, 4092u, c5vrx_phase8_gain_lut);
            if (!r.lines || !r.porch_pairs || !r.sync_pairs) continue;
            porch += r.porch_khz;
            sync += r.sync_khz;
            lines += r.lines;
            ++windows;
        }
        if (!windows) { printf("%s no confirmed lines\n", argv[a]); continue; }
        printf("%s windows=%u lines=%u porch_khz=%ld sync_khz=%ld span_khz=%ld\n", argv[a], windows, lines,
               porch / (long)windows, sync / (long)windows, (porch - sync) / (long)windows);
    }
    return 0;
}

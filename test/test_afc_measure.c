/* C5VRX by Twotoz and contributors: real IQ -> afc2_measure regression.
 * Synthetic FM plateaus, not mocked result structs or a board range proof. */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include "afc.h"
#include "phase8_gain_lut.h"

static uint8_t raw[4096];
static unsigned checks, failures;
static void check(const char *name, bool ok)
{
    ++checks; failures += !ok;
    printf("%s %s\n", ok ? "ok" : "FAIL", name);
}
static void fm(unsigned period, int polarity, bool burst, double picture_hz)
{
    double phase = .3;
    for (unsigned k = 0; k < sizeof(raw); ++k) {
        unsigned pos = (k + period - 160u) % period;
        double hz = pos < 188 ? -2914000. : pos < 400 || pos >= period - 100 ? -1000000. : picture_hz;
        if (burst && pos >= 228 && pos < 308)
            hz += 957000. * sin(6.283185307179586 * (period == 2560 ? 4433618.75 : 3579545.45) * k / 40000000.);
        phase += 6.283185307179586 * hz / 40000000. * -polarity;
        int i = (int)floor(4.2 * cos(phase)), q = (int)floor(4.2 * sin(phase));
        raw[k] = (uint8_t)(((unsigned)i & 15u) << 4 | ((unsigned)q & 15u));
    }
}
int main(void)
{
    for (unsigned standard = 1; standard <= 2; ++standard)
        for (int polarity = -1; polarity <= 1; polarity += 2) {
            unsigned period = standard == 2 ? 2560 : 2542;
            fm(period, polarity, false, 2000000.);
            afc2_result_t r = afc2_measure(raw, sizeof(raw), c5vrx_phase8_gain_lut);
            char label[96];
            snprintf(label, sizeof(label), "B/W %s polarity %+d: timing-confirmed line", standard == 2 ? "PAL" : "NTSC", polarity);
            check(label, r.lines && r.standard == standard && r.polarity == polarity &&
                  r.sync_pairs && r.porch_pairs && r.burst_x10 < AFC2_BURST_MIN_X10);
            snprintf(label, sizeof(label), "B/W %s polarity %+d: unbiased porch/span", standard == 2 ? "PAL" : "NTSC", polarity);
            check(label, abs(r.porch_khz - polarity * 1000) <= 50 && abs(abs(r.porch_khz - r.sync_khz) - 1914) <= 60);
            r = afc2_measure(raw, 2000, c5vrx_phase8_gain_lut);
            check("single B/W sync has no timing evidence", !r.lines);
            fm(period, polarity, true, 2000000.);
            r = afc2_measure(raw, 2000, c5vrx_phase8_gain_lut);
            check("single colour sync still works", r.lines && r.standard == standard && r.polarity == polarity);
        }
    fm(2400, -1, false, 2000000.);
    check("wrong sync period without burst is rejected", !afc2_measure(raw, sizeof(raw), c5vrx_phase8_gain_lut).lines);
    fm(2560, -1, false, 7500000.);
    check("fast picture FM does not block timing-confirmed sync", afc2_measure(raw, sizeof(raw), c5vrx_phase8_gain_lut).lines > 0);
    uint32_t seed = 42;
    bool noise_ok = true;
    for (unsigned n = 0; n < 64; ++n) {
        for (unsigned k = 0; k < sizeof(raw); ++k) {
            seed = seed * 1664525u + 1013904223u;
            raw[k] = (uint8_t)(seed >> 24);
        }
        noise_ok = noise_ok && !afc2_measure(raw, sizeof(raw), c5vrx_phase8_gain_lut).lines;
    }
    check("64 independent noise windows supply no sync evidence", noise_ok);
    printf("%s: %u checks, %u failures\n", failures ? "FAIL" : "PASS", checks, failures);
    return failures != 0;
}

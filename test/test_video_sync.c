/* Actual raw FM/quantization, exhaustive sample placement: issue #213. */
#include <assert.h>
#include <math.h>
#include <stdio.h>
#include "video_sync.h"
#include "phase8_gain_lut.h"
static uint8_t raw[VIDEO_SYNC_BYTES];
static void fm(unsigned period, unsigned start, int polarity, bool burst, double deviation, double picture)
{
    double phase = .3;
    for (unsigned k = 0; k < sizeof(raw); ++k) {
        unsigned pos = (k + period - start) % period;
        double hz = pos < 188 ? -1000000. - 1914000. * deviation :
                    pos < 400 || pos >= period - 100 ? -1000000. : picture;
        if (burst && pos >= 228 && pos < 308)
            hz += deviation * 957000. * sin(6.283185307179586 * (period == 2560 ? 4433618.75 : 3579545.45) * k / 40000000.);
        phase += 6.283185307179586 * hz / 40000000. * polarity;
        int i = (int)floor(4.2 * cos(phase)), q = (int)floor(4.2 * sin(phase));
        raw[k] = (uint8_t)(((unsigned)i & 15u) << 4 | ((unsigned)q & 15u));
    }
}
int main(void)
{
    unsigned checks = 0;
    const double deviations[] = {.69, 1., 1.7, 2.};
    for (unsigned t = 0; t < 2; ++t)
        for (int pol = -1; pol <= 1; pol += 2)
            for (unsigned b = 0; b < 2; ++b)
                for (unsigned d = 0; d < 4; ++d)
                    for (unsigned offset = 0; offset < 2560; ++offset) {
                        fm(t ? 2560 : 2542, offset % (t ? 2560 : 2542), pol, b, deviations[d], 2000000.);
                        if (!video_sync_present(raw, sizeof(raw), c5vrx_phase8_gain_lut)) {
                            printf("FAIL: standard=%u pol=%d burst=%u deviation=%g offset=%u\n", t, pol, b, deviations[d], offset);
                            return 1;
                        }
                        ++checks;
                    }
    fm(2560, 160, 1, false, 1., 7500000.);
    assert(video_sync_present(raw, sizeof(raw), c5vrx_phase8_gain_lut));
    fm(2560, 160, 1, false, 1., -1000000.);
    assert(video_sync_present(raw, sizeof(raw), c5vrx_phase8_gain_lut));
    fm(32768, 160, 1, false, 1., 2000000.);
    assert(!video_sync_present(raw, sizeof(raw), c5vrx_phase8_gain_lut));
    fm(2400, 160, 1, false, 1., 2000000.);
    assert(!video_sync_present(raw, sizeof(raw), c5vrx_phase8_gain_lut));
    uint32_t seed = 42;
    for (unsigned n = 0; n < 512; ++n) {
        for (unsigned k = 0; k < sizeof(raw); ++k) {
            seed = seed * 1664525u + 1013904223u; raw[k] = (uint8_t)(seed >> 24);
        }
        assert(!video_sync_present(raw, sizeof(raw), c5vrx_phase8_gain_lut));
    }
    printf("PASS: %u placed PAL/NTSC colour/BW/polarity/deviation scenes, fast FM, wrong period, 512 noise windows\n", checks);
    return 0;
}

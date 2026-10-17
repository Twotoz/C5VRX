/* C5VRX by Twotoz/contributors: actual DMA raster timing at both standards. */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "menu_raster.h"
static menu_raster_t raster;
typedef struct { uint64_t samples; unsigned nodes, sync, burst; } stats_t;
static bool segment(void *ctx, const uint8_t *data, unsigned length)
{
    stats_t *s = ctx;
    assert(length && length <= 4092 && !(length & 3) && !((uintptr_t)data & 3));
    s->samples += length; ++s->nodes;
    for (unsigned k = 0; k < length; ++k) {
        assert(data[k] <= 63);
        s->sync += data[k] == 0;
        s->burst += data[k] >= 25 && data[k] <= 28;
    }
    return true;
}
int main(void)
{
    for (unsigned standard = 0; standard < 2; ++standard) {
        video_standard_t v = (video_standard_t)standard;
        menu_raster_init(&raster, v);
        memset(raster.ui, 20, sizeof(raster.ui));
        stats_t s = {0};
        assert(menu_raster_emit(&raster, v, segment, &s));
        unsigned halves = (v == VIDEO_STD_PAL ? 625u : 525u) * MENU_FIELDS;
        assert(s.samples == menu_half_sample(v, halves));
        assert(s.nodes && s.nodes <= MENU_MAX_NODES && s.sync > 1000 && s.burst > 1000);
        for (unsigned h = 1; h <= halves; ++h) {
            unsigned length = menu_half_sample(v, h) - menu_half_sample(v, h - 1);
            assert(v == VIDEO_STD_PAL ? length == 1280 : length == 1268 || length == 1272);
        }
    }
    puts("PASS: PAL/NTSC black raster, full frame timing, sync/burst, DAC bounds and DMA alignment");
    return 0;
}

/* C5VRX-5 by Twotoz and the C5VRX contributors: host test of the OSD key
 * raster (main/osd_key_logic.h) against the actual PAL and NTSC menu
 * rasters (main/menu_raster.c). The key frame is assembled exactly as the
 * firmware chain does (zero runs + shared content buffers); every set bit
 * must land on samples the menu emits from its UI buffer, never on sync,
 * burst, blanking or the vertical interval. dx/dy must move the whole
 * frame exactly. The NTSC key grid must only apply while armed.
 *
 *   cc -O2 -std=c11 -I../main test_osd_key.c ../main/menu_raster.c -lm -o test_osd_key && ./test_osd_key
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "menu_font.h"
#include "osd_key_logic.h"

static unsigned s_fail, s_run;
static void check(const char *name, int ok)
{
    ++s_run;
    printf("%-4s %s\n", ok ? "ok" : "FAIL", name);
    s_fail += !ok;
}

#define MAX_SAMPLES 1600000u

static menu_raster_t s_raster;
static uint8_t s_ui[MAX_SAMPLES / 8u];        /* sample emitted from the UI buffer */
static uint32_t s_pos;
static uint32_t s_starts[2000];               /* sync prefix positions */
static unsigned s_start_count;
static osd_layout_t s_layout;

static bool in(const void *p, const void *base, size_t size)
{
    return (const uint8_t *)p >= (const uint8_t *)base && (const uint8_t *)p < (const uint8_t *)base + size;
}

static bool record(void *ctx, const uint8_t *data, unsigned length)
{
    (void)ctx;
    if (in(data, s_raster.ui, sizeof(s_raster.ui)))
        for (unsigned k = 0; k < length; ++k) s_ui[(s_pos + k) >> 3] |= (uint8_t)(1u << ((s_pos + k) & 7u));
    if ((in(data, s_raster.prefix, sizeof(s_raster.prefix)) || in(data, s_raster.no_burst, sizeof(s_raster.no_burst))) &&
        s_start_count < 2000u)
        s_starts[s_start_count++] = s_pos;
    s_pos += length;
    return true;
}

static bool ui_sample(uint32_t s) { return s < MAX_SAMPLES && (s_ui[s >> 3] >> (s & 7u) & 1u); }

/* Key frame bits exactly as the GDMA chain emits them. */
static uint32_t *assemble(const osd_layout_t *l)
{
    uint32_t *frame = calloc(l->frame_words, 4);
    uint32_t content[OSD_KEY_CONTENT_WORDS];
    for (unsigned k = 0; k < l->lines; ++k) {
        osd_key_render(content, (osd_row_t)l->buf[l->run[k].buf].type, l->buf[l->run[k].buf].phase, s_font8x8);
        memcpy(frame + l->run[k].word, content, sizeof(content));
    }
    return frame;
}

static void standard(video_standard_t std, const char *tag, unsigned line_samples, unsigned want_lines)
{
    char name[200];
    memset(s_ui, 0, sizeof(s_ui));
    s_pos = 0;
    s_start_count = 0;
    menu_raster_init(&s_raster, std);
    check("menu raster emits", menu_raster_emit(&s_raster, std, record, NULL));
    uint32_t frame = menu_frame_samples(std);
    snprintf(name, sizeof(name), "%s frame loop %u samples, whole 32-bit key words", tag, (unsigned)frame);
    check(name, s_pos == frame && frame % 64u == 0);
    unsigned odd = 0;
    for (unsigned k = 0; k < s_start_count; ++k) odd += s_starts[k] % 4u != 0;
    check("line starts stay on DMA words", odd == 0);

    osd_key_walk_begin(&s_layout, &s_raster, std);
    menu_raster_emit(&s_raster, std, osd_key_walk, &s_layout);
    snprintf(name, sizeof(name), "%s walk finds %u UI scanlines (want %u)", tag, s_layout.lines, want_lines);
    check(name, !s_layout.overflow && s_layout.lines == want_lines);
    check("layout dx=0 dy=0", osd_key_layout(&s_layout, frame, line_samples, 0, 0));
    unsigned full = 0, text = 0;
    for (unsigned k = 0; k < s_layout.lines; ++k) {
        full += s_layout.line[k].type == OSD_ROW_FULL;
        text += s_layout.line[k].type >= OSD_ROW_TEXT0;
    }
    snprintf(name, sizeof(name), "%s rows: %u full, %u text", tag, full, text);
    check(name, full == 2u * (2u + 2u + 1u) && text == 2u * 8u * menu_ui_y_repeat(std));

    uint32_t *base = assemble(&s_layout);
    unsigned set = 0, bad = 0;
    for (uint32_t k = 0; k < s_layout.frame_words * 32u; ++k) {
        if (!osd_key_get_bit(base, k)) continue;
        ++set;
        bad += !ui_sample(2u * k) || !ui_sample(2u * k + 1u);
    }
    snprintf(name, sizeof(name), "%s %u key bits, all on menu UI samples (%u outside)", tag, set, bad);
    check(name, set > 1000 && bad == 0);
    snprintf(name, sizeof(name), "%s chain: %u descriptors, %u content buffers (%u B), %u B zero",
             tag, osd_key_descriptors(&s_layout), s_layout.bufs, s_layout.bufs * OSD_KEY_CONTENT_WORDS * 4u,
             OSD_KEY_ZERO_WORDS * 4u);
    check(name, s_layout.bufs <= OSD_KEY_MAX_BUFS && osd_key_descriptors(&s_layout) < 2000u);

    /* dx/dy move the whole key frame by exactly dx + dy * line / 2 bits. */
    unsigned shift_bad = 0, layouts = 0;
    const int cases[][2] = {{1, 0}, {-1, 0}, {37, 0}, {-OSD_KEY_DX_MAX, 0}, {OSD_KEY_DX_MAX, 0},
                            {0, 1}, {0, -1}, {0, OSD_KEY_DY_MAX}, {0, -OSD_KEY_DY_MAX}, {-77, 13}};
    for (unsigned c = 0; c < sizeof(cases) / sizeof(cases[0]); ++c) {
        osd_layout_t *moved = malloc(sizeof(*moved));
        memcpy(moved, &s_layout, sizeof(*moved));
        bool okl = osd_key_layout(moved, frame, line_samples, cases[c][0], cases[c][1]);
        layouts += okl;
        unsigned before_bad = shift_bad;
        uint32_t *f = assemble(moved);
        int64_t shift = (int64_t)cases[c][1] * line_samples / 2 + cases[c][0];
        int64_t bits = (int64_t)moved->frame_words * 32;
        for (int64_t k = 0; k < bits; ++k) {
            int64_t src = ((k - shift) % bits + bits) % bits;
            shift_bad += osd_key_get_bit(f, (unsigned)k) != osd_key_get_bit(base, (unsigned)src);
        }
        if (!okl || shift_bad != before_bad)
            printf("     dx=%d dy=%d: layout %d, %u bits differ\n", cases[c][0], cases[c][1], okl, shift_bad - before_bad);
        free(f);
        free(moved);
    }
    check("dx/dy move the frame exactly", layouts == 10 && shift_bad == 0);
    {
        osd_layout_t *a = malloc(sizeof(*a)), *b = malloc(sizeof(*b));
        memcpy(a, &s_layout, sizeof(*a));
        memcpy(b, &s_layout, sizeof(*b));
        osd_key_layout(a, frame, line_samples, 999, -999);
        osd_key_layout(b, frame, line_samples, OSD_KEY_DX_MAX, -OSD_KEY_DY_MAX);
        check("dx/dy are clamped", memcmp(a->run, b->run, sizeof(a->run)) == 0);
        free(a);
        free(b);
    }
    free(base);
}

int main(void)
{
    standard(VIDEO_STD_PAL, "PAL", 2560u, 2u * MENU_PAL_UI_Y_REPEAT * MENU_UI_LINES);

    check("NTSC default raster unchanged (key grid off)", !menu_raster_key_grid() &&
          menu_frame_samples(VIDEO_STD_NTSC) == 1334668u && menu_half_sample(VIDEO_STD_NTSC, 1050) == 1334668u);
    menu_raster_init(&s_raster, VIDEO_STD_NTSC);
    osd_key_walk_begin(&s_layout, &s_raster, VIDEO_STD_NTSC);
    menu_raster_emit(&s_raster, VIDEO_STD_NTSC, osd_key_walk, &s_layout);
    check("NTSC without the key grid is refused", !osd_key_layout(&s_layout, 1334668u, 2542u, 0, 0));
    menu_raster_set_key_grid(true);
    unsigned lengths_ok = 1;
    for (unsigned h = 0; h + 2u <= 1050u; ++h) {
        uint32_t d = menu_half_sample(VIDEO_STD_NTSC, h + 2) - menu_half_sample(VIDEO_STD_NTSC, h);
        lengths_ok &= d == 2540u || d == 2544u;
    }
    check("NTSC key grid: lines 2540/2544 samples, loop 1334656", lengths_ok &&
          menu_half_sample(VIDEO_STD_NTSC, 1050) == 1334656u);
    standard(VIDEO_STD_NTSC, "NTSC", 2542u, 2u * MENU_NTSC_UI_Y_REPEAT * MENU_UI_LINES);
    menu_raster_set_key_grid(false);
    check("PAL raster independent of the key grid", menu_frame_samples(VIDEO_STD_PAL) == 1600000u);

    uint32_t w[3] = {0};
    osd_key_set_bit(w, 0);
    osd_key_set_bit(w, 31);
    osd_key_set_bit(w, 32);
    check("MSB-first 32-bit slot order", w[0] == 0x80000001u && w[1] == 0x80000000u && w[2] == 0);
    uint32_t c[OSD_KEY_CONTENT_WORDS];
    osd_key_render(c, OSD_ROW_FULL, 31, s_font8x8);
    unsigned last = 0;
    for (unsigned k = 0; k < OSD_KEY_CONTENT_WORDS * 32u; ++k) if (osd_key_get_bit(c, k)) last = k;
    check("content run holds the pattern at the largest phase", last == 31u + OSD_KEY_UI_BITS - 1u);
    check("key GPIO check: GPIO2 free, DAC/IQ/USB/flash/BOOT pins not",
          osd_key_gpio_free(2) && !osd_key_gpio_free(9) && !osd_key_gpio_free(1) && !osd_key_gpio_free(13) &&
          !osd_key_gpio_free(18) && !osd_key_gpio_free(28) && !osd_key_gpio_free(-1));
    printf("%s: %u checks, %u failures\n", s_fail ? "FAIL" : "PASS", s_run, s_fail);
    return s_fail ? 1 : 0;
}

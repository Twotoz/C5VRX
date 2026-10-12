/* C5VRX-5 by Twotoz and the C5VRX contributors: host test of the OSD key
 * raster (main/osd_key_logic.h) against the actual PAL menu raster
 * (main/menu_raster.c): every key bit of the bench pattern must land on a
 * sample the menu emits from its UI buffer, never on sync, burst, blanking
 * or a vertical-interval half-line; the frame line mapping must match the
 * emitter; dx/dy only move the pattern and keep it inside its key line.
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

#define FRAME_SAMPLES (OSD_KEY_PAL_LINES * 2560u)

static menu_raster_t s_raster;
static uint8_t s_ui[FRAME_SAMPLES / 8u];     /* sample emitted from the UI buffer */
static uint8_t s_line_start[OSD_KEY_PAL_LINES]; /* a sync prefix starts this frame line */
static unsigned s_pos, s_misaligned;

static bool in(const void *p, const void *base, size_t size)
{
    return (const uint8_t *)p >= (const uint8_t *)base && (const uint8_t *)p < (const uint8_t *)base + size;
}

static bool record(void *ctx, const uint8_t *data, unsigned length)
{
    (void)ctx;
    if (in(data, s_raster.ui, sizeof(s_raster.ui)))
        for (unsigned k = 0; k < length; ++k) s_ui[(s_pos + k) >> 3] |= (uint8_t)(1u << ((s_pos + k) & 7u));
    if (in(data, s_raster.prefix, sizeof(s_raster.prefix)) || in(data, s_raster.no_burst, sizeof(s_raster.no_burst))) {
        if (s_pos % 2560u) ++s_misaligned;
        else s_line_start[s_pos / 2560u] = 1;
    }
    s_pos += length;
    return true;
}

static bool ui_sample(unsigned s) { return s < FRAME_SAMPLES && (s_ui[s >> 3] >> (s & 7u) & 1u); }

int main(void)
{
    char name[160];
    menu_raster_init(&s_raster, VIDEO_STD_PAL);
    check("menu raster emits", menu_raster_emit(&s_raster, VIDEO_STD_PAL, record, NULL));
    check("PAL key frame = menu frame (625 x 2560 samples)", s_pos == FRAME_SAMPLES &&
          menu_half_sample(VIDEO_STD_PAL, 1250) == FRAME_SAMPLES);
    check("every menu line starts on the 2560-sample grid", s_misaligned == 0);

    unsigned mismatch = 0;
    for (unsigned j = 0; j < OSD_KEY_PAL_LINES; ++j)
        mismatch += (osd_key_pal_field_line(j) >= 0) != (s_line_start[j] != 0);
    snprintf(name, sizeof(name), "field-line mapping matches the emitter's full lines (%u mismatches)", mismatch);
    check(name, mismatch == 0);

    /* Every set bit (video coordinates, dx = 0) is two UI samples. */
    uint32_t row[OSD_KEY_PAL_LINE_WORDS];
    unsigned bad = 0, set = 0, lines[OSD_ROW_TYPES] = {0};
    for (unsigned j = 0; j < OSD_KEY_PAL_LINES; ++j) {
        osd_row_t type = osd_key_pal_row(j);
        ++lines[type];
        osd_key_render_row(row, type, 0, s_font8x8);
        for (unsigned k = 0; k < OSD_KEY_PAL_LINE_BITS; ++k) {
            if (!osd_key_get_bit(row, k)) continue;
            ++set;
            unsigned s = j * 2560u + k * OSD_KEY_SAMPLES_PER_BIT;
            bad += !ui_sample(s) || !ui_sample(s + 1u);
        }
    }
    snprintf(name, sizeof(name), "%u key bits, all on menu UI samples (%u outside)", set, bad);
    check(name, set > 0 && bad == 0);
    unsigned text_lines = 0;
    for (unsigned t = OSD_ROW_TEXT0; t < OSD_ROW_TYPES; ++t) text_lines += lines[t];
    snprintf(name, sizeof(name), "pattern covers the UI: full %u, sides %u, text %u lines (560 UI lines)",
             lines[OSD_ROW_FULL], lines[OSD_ROW_SIDES], text_lines);
    check(name, lines[OSD_ROW_FULL] + lines[OSD_ROW_SIDES] + text_lines == 2u * OSD_KEY_UI_FIELD_LINES &&
          lines[OSD_ROW_FULL] == 2u * 3u * OSD_KEY_EDGE_LINES && text_lines == 2u * 8u * MENU_PAL_UI_Y_REPEAT);

    unsigned text_bits = 0;
    for (unsigned t = OSD_ROW_TEXT0; t < OSD_ROW_TYPES; ++t) {
        uint32_t sides[OSD_KEY_PAL_LINE_WORDS];
        osd_key_render_row(sides, OSD_ROW_SIDES, 0, s_font8x8);
        osd_key_render_row(row, (osd_row_t)t, 0, s_font8x8);
        for (unsigned k = 0; k < OSD_KEY_PAL_LINE_BITS; ++k) text_bits += osd_key_get_bit(row, k) && !osd_key_get_bit(sides, k);
    }
    check("text rows add glyph bits", text_bits > 50);

    /* dx shifts every row exactly and stays inside the key line. */
    unsigned shift_bad = 0;
    for (int dx = -OSD_KEY_DX_MAX; dx <= OSD_KEY_DX_MAX; dx += 7)
        for (unsigned t = 0; t < OSD_ROW_TYPES; ++t) {
            uint32_t base[OSD_KEY_PAL_LINE_WORDS];
            osd_key_render_row(base, (osd_row_t)t, 0, s_font8x8);
            osd_key_render_row(row, (osd_row_t)t, dx, s_font8x8);
            for (int k = 0; k < (int)OSD_KEY_PAL_LINE_BITS; ++k) {
                int src = k - dx;
                bool want = src >= 0 && src < (int)OSD_KEY_PAL_LINE_BITS && osd_key_get_bit(base, (unsigned)src);
                shift_bad += want != osd_key_get_bit(row, (unsigned)k);
            }
            unsigned before = 0, after = 0;
            for (unsigned k = 0; k < OSD_KEY_PAL_LINE_BITS; ++k) { before += osd_key_get_bit(base, k); after += osd_key_get_bit(row, k); }
            shift_bad += before != after;
        }
    check("dx moves the pattern without losing bits", shift_bad == 0);
    check("safe area +- DX_MAX fits one key line", OSD_KEY_SAFE_LO >= (unsigned)OSD_KEY_DX_MAX &&
          OSD_KEY_SAFE_HI + OSD_KEY_DX_MAX <= OSD_KEY_PAL_LINE_BITS);
    {
        uint32_t a[OSD_KEY_PAL_LINE_WORDS], b[OSD_KEY_PAL_LINE_WORDS];
        osd_key_render_row(a, OSD_ROW_FULL, 5000, s_font8x8);
        osd_key_render_row(b, OSD_ROW_FULL, OSD_KEY_DX_MAX, s_font8x8);
        check("dx is clamped", memcmp(a, b, sizeof(a)) == 0);
    }

    unsigned dy_bad = 0;
    for (int dy = -OSD_KEY_DY_MAX; dy <= OSD_KEY_DY_MAX; ++dy)
        for (unsigned j = 0; j < OSD_KEY_PAL_LINES; ++j)
            dy_bad += osd_key_row_at(j, dy) != osd_key_pal_row((j + 2u * OSD_KEY_PAL_LINES - (unsigned)(dy + 625)) % 625u);
    check("dy moves the pattern by whole frame lines", dy_bad == 0);
    check("dy is clamped", osd_key_row_at(100, 999) == osd_key_row_at(100, OSD_KEY_DY_MAX));

    uint32_t w[OSD_KEY_PAL_LINE_WORDS] = {0};
    osd_key_set_bit(w, 0);
    osd_key_set_bit(w, 31);
    osd_key_set_bit(w, 32);
    osd_key_set_bit(w, OSD_KEY_PAL_LINE_BITS);  /* ignored */
    check("MSB-first 32-bit slot order", w[0] == 0x80000001u && w[1] == 0x80000000u && w[2] == 0);
    check("key GPIO check: GPIO2 free, DAC/IQ/USB/flash/BOOT pins not",
          osd_key_gpio_free(2) && !osd_key_gpio_free(9) && !osd_key_gpio_free(1) && !osd_key_gpio_free(13) &&
          !osd_key_gpio_free(18) && !osd_key_gpio_free(28) && !osd_key_gpio_free(-1));
    printf("%s: %u checks, %u failures\n", s_fail ? "FAIL" : "PASS", s_run, s_fail);
    return s_fail ? 1 : 0;
}

#pragma once
/* C5VRX-5 by Twotoz and the C5VRX contributors: OSD key raster (step 1).
 *
 * A separate 1-bit "key" stream on I2S0 DOUT, clocked from PLL_F240M like
 * PARLIO TX (240/6 MHz mclk, 20 MHz bclk: one key bit per two DAC samples),
 * so the key and the video cannot drift. A diode + resistor from the key
 * pin into the DAC node adds white where a bit is 1; when the bit is 0 the
 * diode blocks and the tested DAC network is unloaded.
 *
 * Step 1 is a bench test over the menu/status raster only (we generate its
 * sync, so no genlock is needed): a PAL frame is 625 lines of 2560 samples
 * (menu_raster.c), i.e. 625 key lines of 1280 bits. Each key line is one
 * GDMA descriptor pointing to a shared buffer of its row type, so the whole
 * key frame costs 625 descriptors and a few 160-byte lines. The pattern
 * frames the menu UI (outline, centre cross, "OSD KEY" text): when it sits
 * exactly on the menu UI edges, the key/video latency is known (dx, dy).
 * NTSC is refused: its menu loop (1,334,668 samples) is no whole number of
 * 32-bit key words. Live video needs genlock (step 2) and never gets a key.
 *
 * Pattern bits are defined in video coordinates and clipped to the menu UI
 * rectangle (test/test_osd_key.c checks every set bit against the actual
 * menu raster: never sync, burst, blanking or the vertical interval). The
 * key coordinate is video + (dx, dy); dx/dy are bench offsets, clamped so
 * every bit stays inside its own key line.
 *
 * I2S STD MSB format, 32-bit stereo slots, left_align, little-endian words,
 * MSB first on the wire (ESP-IDF v6.0 generic I2S_STD_MSB_SLOT_DEFAULT_CONFIG):
 * time-ordered bit k is word k/32, bit 31 - k%32. A wrong assumption shows
 * on the bench as a scrambled pattern, not as a fault. */
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include "menu_raster.h"

#define OSD_KEY_SAMPLES_PER_BIT 2u
#define OSD_KEY_PAL_LINES 625u
#define OSD_KEY_PAL_LINE_BITS (2560u / OSD_KEY_SAMPLES_PER_BIT)
#define OSD_KEY_PAL_LINE_WORDS (OSD_KEY_PAL_LINE_BITS / 32u)
#define OSD_KEY_PAL_LINE_BYTES (OSD_KEY_PAL_LINE_WORDS * 4u)
/* PLL_F240M / 6 = 40 MHz mclk = 312.5 kHz x 128; bclk 20 MHz (32-bit stereo). */
#define OSD_KEY_SAMPLE_RATE_HZ 312500u

/* Menu UI rectangle in key bits (PAL prefix 652 samples, 384 UI pixels). */
#define OSD_KEY_UI_SAMPLE(x) (MENU_PAL_PREFIX_BYTES + (unsigned)(x) * MENU_UI_X_SCALE_NUM / MENU_UI_X_SCALE_DEN)
#define OSD_KEY_SAFE_LO ((OSD_KEY_UI_SAMPLE(0) + 1u) / OSD_KEY_SAMPLES_PER_BIT)
#define OSD_KEY_SAFE_HI (OSD_KEY_UI_SAMPLE(MENU_UI_WIDTH) / OSD_KEY_SAMPLES_PER_BIT)
#define OSD_KEY_UI_FIELD_LINES (MENU_PAL_UI_Y_REPEAT * MENU_UI_LINES)

/* Bench offsets: +-7.5 us and +-20 lines keep every bit in its key line. */
#define OSD_KEY_DX_MAX 150
#define OSD_KEY_DY_MAX 20

#define OSD_KEY_EDGE_BITS 4u       /* 200 ns vertical strokes */
#define OSD_KEY_EDGE_LINES 2u      /* field lines of each horizontal stroke */
#define OSD_KEY_TEXT_X 296
#define OSD_KEY_TEXT_Y 47
#define OSD_KEY_TEXT "OSD KEY"

typedef enum {
    OSD_ROW_NONE,
    OSD_ROW_FULL,       /* horizontal stroke over the whole UI width */
    OSD_ROW_SIDES,      /* left, centre and right strokes */
    OSD_ROW_TEXT0,      /* SIDES plus font row 0..7 of the text */
    OSD_ROW_TYPES = OSD_ROW_TEXT0 + 8,
} osd_row_t;

static inline int osd_key_clamp(int v, int limit) { return v < -limit ? -limit : v > limit ? limit : v; }

/* Field line of PAL menu frame line j (menu_raster_emit: h = 2j,
 * pos = (h + 5) mod 625), or -1 where the emitter has no sync prefix
 * (vertical-interval half-lines). Line 312 is the last half-line. */
static inline int osd_key_pal_field_line(unsigned j)
{
    unsigned pos = (2u * j + 5u) % 625u;
    if (j >= OSD_KEY_PAL_LINES || pos < 15u) return -1;
    return (int)(pos / 2u);
}

/* Row type of video frame line j (video coordinates, before dy). */
static inline osd_row_t osd_key_pal_row(unsigned j)
{
    int line = osd_key_pal_field_line(j);
    if (line < (int)MENU_PAL_UI_FIRST_LINE || line >= (int)(MENU_PAL_UI_FIRST_LINE + OSD_KEY_UI_FIELD_LINES))
        return OSD_ROW_NONE;
    unsigned f = (unsigned)line - MENU_PAL_UI_FIRST_LINE, mid = OSD_KEY_UI_FIELD_LINES / 2u;
    if (f < OSD_KEY_EDGE_LINES || f >= OSD_KEY_UI_FIELD_LINES - OSD_KEY_EDGE_LINES ||
        (f >= mid - 1u && f < mid - 1u + OSD_KEY_EDGE_LINES))
        return OSD_ROW_FULL;
    unsigned uy = f / MENU_PAL_UI_Y_REPEAT;
    if (uy >= (unsigned)OSD_KEY_TEXT_Y && uy < (unsigned)OSD_KEY_TEXT_Y + 8u)
        return (osd_row_t)(OSD_ROW_TEXT0 + (uy - (unsigned)OSD_KEY_TEXT_Y));
    return OSD_ROW_SIDES;
}

/* Key line j shows video line j - dy. */
static inline osd_row_t osd_key_row_at(unsigned j, int dy)
{
    int v = ((int)j - osd_key_clamp(dy, OSD_KEY_DY_MAX)) % (int)OSD_KEY_PAL_LINES;
    return osd_key_pal_row((unsigned)(v < 0 ? v + (int)OSD_KEY_PAL_LINES : v));
}

static inline void osd_key_set_bit(uint32_t *words, unsigned k)
{
    if (k < OSD_KEY_PAL_LINE_BITS) words[k >> 5] |= 0x80000000u >> (k & 31u);
}

static inline bool osd_key_get_bit(const uint32_t *words, unsigned k)
{
    return k < OSD_KEY_PAL_LINE_BITS && (words[k >> 5] & (0x80000000u >> (k & 31u)));
}

/* Video bits [lo, hi), clipped to the UI rectangle, shifted by dx. */
static inline void osd_key_span(uint32_t *words, unsigned lo, unsigned hi, int dx)
{
    if (lo < OSD_KEY_SAFE_LO) lo = OSD_KEY_SAFE_LO;
    if (hi > OSD_KEY_SAFE_HI) hi = OSD_KEY_SAFE_HI;
    dx = osd_key_clamp(dx, OSD_KEY_DX_MAX);
    for (unsigned b = lo; b < hi; ++b) osd_key_set_bit(words, (unsigned)((int)b + dx));
}

static inline unsigned osd_key_ui_bit(int x) { return OSD_KEY_UI_SAMPLE(x) / OSD_KEY_SAMPLES_PER_BIT; }

/* One key line of the given type; font is the 8x8 menu font (bit 7 left). */
static inline void osd_key_render_row(uint32_t *words, osd_row_t type, int dx, const uint8_t font[96][8])
{
    memset(words, 0, OSD_KEY_PAL_LINE_BYTES);
    if (type == OSD_ROW_NONE || type >= OSD_ROW_TYPES) return;
    if (type == OSD_ROW_FULL) { osd_key_span(words, OSD_KEY_SAFE_LO, OSD_KEY_SAFE_HI, dx); return; }
    unsigned centre = osd_key_ui_bit((int)MENU_UI_WIDTH / 2);
    osd_key_span(words, OSD_KEY_SAFE_LO, OSD_KEY_SAFE_LO + OSD_KEY_EDGE_BITS, dx);
    osd_key_span(words, centre - OSD_KEY_EDGE_BITS / 2u, centre + OSD_KEY_EDGE_BITS / 2u, dx);
    osd_key_span(words, OSD_KEY_SAFE_HI - OSD_KEY_EDGE_BITS, OSD_KEY_SAFE_HI, dx);
    if (type < OSD_ROW_TEXT0) return;
    unsigned gy = (unsigned)type - OSD_ROW_TEXT0;
    int x = OSD_KEY_TEXT_X;
    for (const char *s = OSD_KEY_TEXT; *s; ++s, x += 8) {
        unsigned index = (*s >= 32 && *s <= 126) ? (unsigned)*s - 32u : 0u;
        for (int gx = 0; gx < 8; ++gx)
            if (font[index][gy] & (0x80u >> gx))
                osd_key_span(words, osd_key_ui_bit(x + gx), osd_key_ui_bit(x + gx + 1), dx);
    }
}

/* GPIOs the receiver owns (rf.c IQ, transport.c DAC, BOOT, USB, flash/PSRAM). */
static inline bool osd_key_gpio_free(int gpio)
{
    static const int used[] = {1, 0, 25, 7, 10, 5, 3, 4, 23, 24, 11, 12, 8, 9, 28, 13, 14};
    if (gpio < 0 || gpio > 28 || (gpio >= 16 && gpio <= 22)) return false;
    for (unsigned k = 0; k < sizeof(used) / sizeof(used[0]); ++k) if (used[k] == gpio) return false;
    return true;
}

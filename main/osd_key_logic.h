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
 * sync, so no genlock is needed), PAL and NTSC. The key frame is the menu
 * frame loop (menu_frame_samples / 2 bits; NTSC uses the key grid of
 * menu_raster.c so the loop is whole 32-bit words). Its layout comes from
 * the menu raster itself: osd_key_walk() records where every UI scanline
 * starts, so no line formula has to agree with the emitter.
 *
 * Each UI scanline gets one content run (the words that hold its pattern)
 * between zero runs. Content buffers are shared per (row type, bit phase):
 * PAL lines all have one phase; NTSC lines (2540/2544 samples) take up to
 * 16. Zero runs point into one shared zero buffer.
 *
 * Pattern bits are defined relative to the UI start and clipped to the UI
 * width; test/test_osd_key.c checks every set bit against the actual menu
 * raster (never sync, burst, blanking or the vertical interval). The key
 * position is video + dx bits + dy lines (bench offsets for the fixed
 * key/video latency, clamped).
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
/* PLL_F240M / 6 = 40 MHz mclk = 312.5 kHz x 128; bclk 20 MHz (32-bit stereo). */
#define OSD_KEY_SAMPLE_RATE_HZ 312500u

/* Pattern width: the menu UI (384 pixels at 208/50 samples). */
#define OSD_KEY_UI_SAMPLES (MENU_UI_WIDTH * MENU_UI_X_SCALE_NUM / MENU_UI_X_SCALE_DEN)
#define OSD_KEY_UI_BITS (OSD_KEY_UI_SAMPLES / OSD_KEY_SAMPLES_PER_BIT)
/* One content run: pattern bits plus the phase, rounded up to words. */
#define OSD_KEY_CONTENT_WORDS ((OSD_KEY_UI_BITS + 31u + 31u) / 32u)

/* Bench offsets: +-7.5 us and +-20 lines keep the pattern inside the
 * active picture's line and away from the frame loop end. */
#define OSD_KEY_DX_MAX 150
#define OSD_KEY_DY_MAX 20

#define OSD_KEY_EDGE_BITS 4u       /* 200 ns vertical strokes */
#define OSD_KEY_TEXT_X 296
#define OSD_KEY_TEXT_Y 47
#define OSD_KEY_TEXT "OSD KEY"

#define OSD_KEY_MAX_LINES 600u     /* UI scanlines per frame: PAL 560, NTSC 448 */
#define OSD_KEY_MAX_BUFS 160u      /* distinct (row type, phase) content runs */
#define OSD_KEY_ZERO_WORDS 256u    /* shared zero buffer, 1 KiB */

typedef enum {
    OSD_ROW_NONE,
    OSD_ROW_FULL,       /* horizontal stroke over the whole UI width */
    OSD_ROW_SIDES,      /* left, centre and right strokes */
    OSD_ROW_TEXT0,      /* SIDES plus font row 0..7 of the text */
    OSD_ROW_TYPES = OSD_ROW_TEXT0 + 8,
} osd_row_t;

static inline int osd_key_clamp(int v, int limit) { return v < -limit ? -limit : v > limit ? limit : v; }

/* UI row r, its rep-th scanline in this field (0..repeat-1). */
static inline osd_row_t osd_key_ui_row_type(unsigned row, unsigned rep, unsigned repeat)
{
    if (row == 0u && rep < 2u) return OSD_ROW_FULL;
    if (row == MENU_UI_LINES - 1u && rep + 2u >= repeat) return OSD_ROW_FULL;
    if (row == MENU_UI_LINES / 2u && rep == 0u) return OSD_ROW_FULL;
    if (row >= (unsigned)OSD_KEY_TEXT_Y && row < (unsigned)OSD_KEY_TEXT_Y + 8u)
        return (osd_row_t)(OSD_ROW_TEXT0 + (row - (unsigned)OSD_KEY_TEXT_Y));
    return OSD_ROW_SIDES;
}

static inline void osd_key_set_bit(uint32_t *words, unsigned k)
{
    words[k >> 5] |= 0x80000000u >> (k & 31u);
}

static inline bool osd_key_get_bit(const uint32_t *words, unsigned k)
{
    return (words[k >> 5] & (0x80000000u >> (k & 31u))) != 0;
}

/* UI-relative key bit of UI pixel x. */
static inline unsigned osd_key_ui_bit(int x)
{
    return (unsigned)x * MENU_UI_X_SCALE_NUM / MENU_UI_X_SCALE_DEN / OSD_KEY_SAMPLES_PER_BIT;
}

/* UI-relative bits [lo, hi), clipped to the UI width, at offset phase. */
static inline void osd_key_span(uint32_t *words, unsigned phase, unsigned lo, unsigned hi)
{
    if (hi > OSD_KEY_UI_BITS) hi = OSD_KEY_UI_BITS;
    for (unsigned b = lo; b < hi; ++b) osd_key_set_bit(words, phase + b);
}

/* One content run (OSD_KEY_CONTENT_WORDS words): the row pattern starting
 * at bit `phase` (0..31). font is the 8x8 menu font (bit 7 left). */
static inline void osd_key_render(uint32_t *words, osd_row_t type, unsigned phase, const uint8_t font[96][8])
{
    memset(words, 0, OSD_KEY_CONTENT_WORDS * 4u);
    if (type == OSD_ROW_NONE || type >= OSD_ROW_TYPES || phase > 31u) return;
    if (type == OSD_ROW_FULL) { osd_key_span(words, phase, 0, OSD_KEY_UI_BITS); return; }
    unsigned centre = osd_key_ui_bit((int)MENU_UI_WIDTH / 2);
    osd_key_span(words, phase, 0, OSD_KEY_EDGE_BITS);
    osd_key_span(words, phase, centre - OSD_KEY_EDGE_BITS / 2u, centre + OSD_KEY_EDGE_BITS / 2u);
    osd_key_span(words, phase, OSD_KEY_UI_BITS - OSD_KEY_EDGE_BITS, OSD_KEY_UI_BITS);
    if (type < OSD_ROW_TEXT0) return;
    unsigned gy = (unsigned)type - OSD_ROW_TEXT0;
    int x = OSD_KEY_TEXT_X;
    for (const char *s = OSD_KEY_TEXT; *s; ++s, x += 8) {
        unsigned index = (*s >= 32 && *s <= 126) ? (unsigned)*s - 32u : 0u;
        for (int gx = 0; gx < 8; ++gx)
            if (font[index][gy] & (0x80u >> gx))
                osd_key_span(words, phase, osd_key_ui_bit(x + gx), osd_key_ui_bit(x + gx + 1));
    }
}

/* ---- Layout from the menu raster ------------------------------------- */

typedef struct {
    uint32_t sample;   /* UI start sample in the frame loop (video) */
    uint8_t type;
} osd_line_t;

typedef struct {
    uint32_t word;     /* first content word in the key frame */
    uint16_t buf;      /* content buffer index */
} osd_run_t;

typedef struct {
    /* walk */
    const uint8_t *ui;          /* raster->ui base */
    unsigned repeat;
    uint32_t pos;
    unsigned last_row, rep;
    bool overflow;
    osd_line_t line[OSD_KEY_MAX_LINES];
    unsigned lines;
    /* layout */
    uint32_t frame_words;
    osd_run_t run[OSD_KEY_MAX_LINES];   /* sorted by word */
    struct { uint8_t type, phase; } buf[OSD_KEY_MAX_BUFS];
    unsigned bufs;
} osd_layout_t;

static inline void osd_key_walk_begin(osd_layout_t *l, const menu_raster_t *r, video_standard_t standard)
{
    memset(l, 0, sizeof(*l));
    l->ui = &r->ui[0][0];
    l->repeat = menu_ui_y_repeat(standard);
    l->last_row = ~0u;
}

/* menu_segment_fn: records the start of every UI scanline. */
static inline bool osd_key_walk(void *ctx, const uint8_t *data, unsigned length)
{
    osd_layout_t *l = ctx;
    if (data >= l->ui && data < l->ui + (size_t)MENU_UI_LINES * MENU_UI_BYTES) {
        unsigned row = (unsigned)(data - l->ui) / MENU_UI_BYTES;
        l->rep = row == l->last_row ? l->rep + 1u : 0u;
        l->last_row = row;
        if (l->lines < OSD_KEY_MAX_LINES)
            l->line[l->lines++] = (osd_line_t){l->pos, (uint8_t)osd_key_ui_row_type(row, l->rep, l->repeat)};
        else l->overflow = true;
    } else if (length > MENU_UI_BYTES) {
        l->last_row = ~0u;      /* a blank tail ends no UI row; keep counting */
    }
    l->pos += length;
    return true;
}

/* Place the walked lines at (dx bits, dy lines of line_samples), share
 * content buffers, sort the runs. False if the frame loop is no whole
 * number of words, runs overlap or straddle the loop end, or a table is
 * full. */
static inline bool osd_key_layout(osd_layout_t *l, uint32_t frame_samples, unsigned line_samples, int dx, int dy)
{
    if (l->overflow || frame_samples % (32u * OSD_KEY_SAMPLES_PER_BIT) || l->pos != frame_samples) return false;
    const uint32_t frame_bits = frame_samples / OSD_KEY_SAMPLES_PER_BIT;
    l->frame_words = frame_bits / 32u;
    dx = osd_key_clamp(dx, OSD_KEY_DX_MAX);
    dy = osd_key_clamp(dy, OSD_KEY_DY_MAX);
    l->bufs = 0;
    for (unsigned k = 0; k < l->lines; ++k) {
        /* The key frame loops: positions wrap; a run may not straddle the end. */
        int64_t s = (int64_t)l->line[k].sample + (int64_t)dy * line_samples;
        s = (s % frame_samples + frame_samples) % frame_samples;
        int64_t bit = ((s / OSD_KEY_SAMPLES_PER_BIT + dx) % frame_bits + frame_bits) % frame_bits;
        if (bit + OSD_KEY_UI_BITS + 31 > (int64_t)frame_bits) return false;
        unsigned phase = (unsigned)(bit & 31), b;
        for (b = 0; b < l->bufs; ++b) if (l->buf[b].type == l->line[k].type && l->buf[b].phase == phase) break;
        if (b == l->bufs) {
            if (l->bufs == OSD_KEY_MAX_BUFS) return false;
            l->buf[l->bufs].type = l->line[k].type;
            l->buf[l->bufs++].phase = (uint8_t)phase;
        }
        l->run[k] = (osd_run_t){(uint32_t)(bit >> 5), (uint16_t)b};
    }
    for (unsigned k = 1; k < l->lines; ++k) {   /* insertion sort: ordered unless dy/dx wrap */
        osd_run_t v = l->run[k];
        unsigned j = k;
        for (; j > 0 && l->run[j - 1].word > v.word; --j) l->run[j] = l->run[j - 1];
        l->run[j] = v;
    }
    for (unsigned k = 1; k < l->lines; ++k)
        if (l->run[k].word < l->run[k - 1].word + OSD_KEY_CONTENT_WORDS) return false;
    return !l->lines || l->run[l->lines - 1].word + OSD_KEY_CONTENT_WORDS <= l->frame_words;
}

/* Descriptors the chain needs: one per content run, zero runs split at
 * OSD_KEY_ZERO_WORDS (GDMA length limit 4092 bytes is larger). */
static inline unsigned osd_key_zero_pieces(uint32_t words)
{
    return (words + OSD_KEY_ZERO_WORDS - 1u) / OSD_KEY_ZERO_WORDS;
}

static inline unsigned osd_key_descriptors(const osd_layout_t *l)
{
    unsigned n = 0;
    uint32_t at = 0;
    for (unsigned k = 0; k < l->lines; ++k) {
        n += osd_key_zero_pieces(l->run[k].word - at) + 1u;
        at = l->run[k].word + OSD_KEY_CONTENT_WORDS;
    }
    return n + osd_key_zero_pieces(l->frame_words - at);
}

/* GPIOs the receiver owns (rf.c IQ, transport.c DAC, BOOT, USB, flash/PSRAM). */
static inline bool osd_key_gpio_free(int gpio)
{
    static const int used[] = {1, 0, 25, 7, 10, 5, 3, 4, 23, 24, 11, 12, 8, 9, 28, 13, 14};
    if (gpio < 0 || gpio > 28 || (gpio >= 16 && gpio <= 22)) return false;
    for (unsigned k = 0; k < sizeof(used) / sizeof(used[0]); ++k) if (used[k] == gpio) return false;
    return true;
}

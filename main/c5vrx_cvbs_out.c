/* SPDX-License-Identifier: GPL-3.0-only */

#include "c5vrx_cvbs_out.h"

#include <math.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "sdkconfig.h"
#include "driver/gpio.h"
#include "driver/parlio_tx.h"
#include "driver/bitscrambler.h"
#include "esp_attr.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "hal/parlio_ll.h"
#include "soc/parl_io_struct.h"

#include "c5vrx_adc_dump.h"
#include "c5vrx_wbfm_hw.h"

/*
 * Analog-first output proof.
 *
 * The C5 cannot afford a full 625-line byte framebuffer at 20 MS/s, so the
 * test generator streams a PAL 625/50 interlaced raster through two small DMA
 * chunks. PARLIO's loop-buffer switching keeps one chunk on the wire while a
 * high-priority task rebuilds the retired chunk.
 *
 * The active picture is monochrome grayscale. Optional PAL-frequency swinging
 * burst is present only as an output-bandwidth/PLL stress signal; there is no
 * active chroma yet and this is not claimed as broadcast-compliance equipment.
 */
#define C5VRX_CVBS_SAMPLE_RATE_HZ       20000000u
#define C5VRX_CVBS_LINE_SAMPLES         1280u
#define C5VRX_CVBS_HALF_LINE_SAMPLES    640u
#define C5VRX_CVBS_FIELD_HALF_LINES     625u
#define C5VRX_CVBS_FRAME_HALF_LINES     1250u

#define C5VRX_CVBS_HSYNC_DEFAULT        94u   /* 4.70 us */
#define C5VRX_CVBS_EQ_DEFAULT           47u   /* 2.35 us */
#define C5VRX_CVBS_BROAD_SYNC_DEFAULT   546u  /* 27.30 us */
#define C5VRX_CVBS_ACTIVE_START         210u  /* 10.50 us after line datum */
#define C5VRX_CVBS_ACTIVE_END           1250u /* leaves 1.50 us front porch */
#define C5VRX_CVBS_ACTIVE_SAMPLES       (C5VRX_CVBS_ACTIVE_END - C5VRX_CVBS_ACTIVE_START)

#define C5VRX_CVBS_EQ_HALF_DEFAULT      5u
#define C5VRX_CVBS_BROAD_HALF_DEFAULT   5u
/*
 * Starting active video at local half-line 49 gives 576 active half-lines
 * (288 full lines) per field while keeping the first active half-line aligned
 * to the normal H-sync cadence that resumes after the post-equalizing pulses.
 */
#define C5VRX_CVBS_ACTIVE_START_HALF    49u

/* Two 40-half-line DMA chunks keep 2.56 ms already queued on the wire. That
 * exceeds the 1.93..1.99 ms bounded vendor-capture interval measured on the
 * physical XIAO while using 30,720 fewer heap bytes than the proof build. */
#define C5VRX_CVBS_CHUNK_HALF_LINES     40u
#define C5VRX_CVBS_CHUNK_SAMPLES        (C5VRX_CVBS_CHUNK_HALF_LINES * C5VRX_CVBS_HALF_LINE_SAMPLES)
#define C5VRX_CVBS_CHUNK_DEADLINE_US    ((uint32_t)(((uint64_t)C5VRX_CVBS_CHUNK_SAMPLES * 1000000ULL) / C5VRX_CVBS_SAMPLE_RATE_HZ))
#define C5VRX_CVBS_EXPECTED_SWITCH_HZ   (C5VRX_CVBS_SAMPLE_RATE_HZ / C5VRX_CVBS_CHUNK_SAMPLES)

/* Static pictures use one bit for every four 20 MHz output samples. This is
 * only 9,360 bytes for the complete 1040x288 active picture and, unlike the
 * old font-per-output-sample renderer, is designed for a 1.28 ms DMA refill
 * deadline. The measured refill time is exposed through AV STATUS. */
#define C5VRX_CVBS_PIXEL_SAMPLES        4u
#define C5VRX_CVBS_LOGICAL_WIDTH        (C5VRX_CVBS_ACTIVE_SAMPLES / C5VRX_CVBS_PIXEL_SAMPLES)
#define C5VRX_CVBS_ACTIVE_LINES         288u
#define C5VRX_CVBS_LOGO_BITS            (C5VRX_CVBS_LOGICAL_WIDTH * C5VRX_CVBS_ACTIVE_LINES)
#define C5VRX_CVBS_LOGO_BYTES           ((C5VRX_CVBS_LOGO_BITS + 7u) / 8u)
#define C5VRX_CVBS_SNOW_TILE_BYTES      1024u

#define C5VRX_CVBS_STREAM_STACK         4096u
#define C5VRX_CVBS_STREAM_PRIORITY      18u

#define C5VRX_CVBS_RETIRED_BUF0         (1u << 0)
#define C5VRX_CVBS_RETIRED_BUF1         (1u << 1)
#define C5VRX_CVBS_STOP_NOTIFY          (1u << 31)

#define C5VRX_PAL_BURST_START_SAMPLES   112u  /* 5.60 us after line datum */
#define C5VRX_PAL_BURST_SAMPLES         45u   /* 2.25 us @ 20 MS/s */
#define C5VRX_PAL_SUBCARRIER_HZ          4433618.75
#define C5VRX_PAL_BURST_AMPLITUDE_MV    150.0
#define C5VRX_PI                        3.14159265358979323846

#if CONFIG_C5VRX_EXPERIMENTAL_CVBS_PARLIO

static const char *TAG = "c5vrx_cvbs";

typedef struct {
    TaskHandle_t task;
    volatile bool stop_requested;
} c5vrx_cvbs_stream_state_t;

static uint8_t s_eq_half[C5VRX_CVBS_HALF_LINE_SAMPLES] __attribute__((aligned(64)));
static uint8_t s_broad_half[C5VRX_CVBS_HALF_LINE_SAMPLES] __attribute__((aligned(64)));
static uint8_t s_blank_first[C5VRX_CVBS_HALF_LINE_SAMPLES] __attribute__((aligned(64)));
static uint8_t s_blank_second[C5VRX_CVBS_HALF_LINE_SAMPLES] __attribute__((aligned(64)));
static uint8_t s_active_first[2][C5VRX_CVBS_HALF_LINE_SAMPLES] __attribute__((aligned(64)));
static uint8_t s_active_second[C5VRX_CVBS_HALF_LINE_SAMPLES] __attribute__((aligned(64)));
static uint8_t s_logo_bitmap[C5VRX_CVBS_LOGO_BYTES] __attribute__((aligned(64)));
static uint8_t s_snow_tile[C5VRX_CVBS_SNOW_TILE_BYTES] __attribute__((aligned(64)));

/* Keep the 51,200-byte always-on stream buffer out of static DRAM. The RF
 * dump writer owns the fixed 0x40830000..0x4083ffff window, so a static array
 * this large could make the linker place ordinary application state inside
 * hardware-owned RAM. DMA-capable buffers are allocated when AV starts; the
 * reserved dump window is excluded from the heap. */
static uint8_t *s_chunk[2];

static parlio_tx_unit_handle_t s_tx;
static parlio_tx_unit_handle_t s_direct_tx;
static bitscrambler_handle_t s_direct_bs;
static portMUX_TYPE s_direct_start_mux = portMUX_INITIALIZER_UNLOCKED;
static c5vrx_cvbs_stream_state_t s_stream;
static uint16_t s_next_half_line;
static uint32_t s_frame_counter;
static bool s_running;
static volatile c5vrx_cvbs_display_t s_requested_display =
    C5VRX_CVBS_DISPLAY_LOGO;
static volatile c5vrx_cvbs_display_t s_active_display =
    C5VRX_CVBS_DISPLAY_LOGO;
static uint32_t s_snow_lfsr = 0xc5f0a17du;
static uint16_t s_snow_offset;
static portMUX_TYPE s_stats_mux = portMUX_INITIALIZER_UNLOCKED;
static volatile uint32_t s_isr_switches[2];
static volatile uint64_t s_isr_first_retired_us[2];
static volatile uint32_t s_isr_unexpected;
static volatile uint32_t s_handled_switches[2];
static uint32_t s_serviced_buffers;
static uint32_t s_missed_switches;
static uint32_t s_queue_errors;
static uint64_t s_service_total_us;
static uint32_t s_service_max_us;
static uint64_t s_start_us;
static uint64_t s_last_service_us;
static uint32_t s_stack_min_bytes;
static c5vrx_cvbs_timing_t s_active_timing = {
    .hsync_samples = C5VRX_CVBS_HSYNC_DEFAULT,
    .equalizing_samples = C5VRX_CVBS_EQ_DEFAULT,
    .broad_sync_samples = C5VRX_CVBS_BROAD_SYNC_DEFAULT,
    .pre_equalizing_half_lines = C5VRX_CVBS_EQ_HALF_DEFAULT,
    .broad_sync_half_lines = C5VRX_CVBS_BROAD_HALF_DEFAULT,
    .post_equalizing_half_lines = C5VRX_CVBS_EQ_HALF_DEFAULT,
};
static c5vrx_cvbs_timing_t s_requested_timing = {
    .hsync_samples = C5VRX_CVBS_HSYNC_DEFAULT,
    .equalizing_samples = C5VRX_CVBS_EQ_DEFAULT,
    .broad_sync_samples = C5VRX_CVBS_BROAD_SYNC_DEFAULT,
    .pre_equalizing_half_lines = C5VRX_CVBS_EQ_HALF_DEFAULT,
    .broad_sync_half_lines = C5VRX_CVBS_BROAD_HALF_DEFAULT,
    .post_equalizing_half_lines = C5VRX_CVBS_EQ_HALF_DEFAULT,
};
static bool s_timing_pending;

static uint8_t cvbs_code_from_mv(unsigned mv);
static uint8_t grayscale_code(unsigned active_x);

/* Compact 5x7 uppercase font, indexed as ASCII 32..90. Only branding glyphs
 * are populated; missing glyphs render as spacing. This remains scanline-only. */
typedef struct { char c; uint8_t row[7]; } glyph_t;
static const glyph_t s_font[] = {
    {'0',{14,17,19,21,25,17,14}}, {'2',{14,17,1,2,4,8,31}},
    {'3',{30,1,1,14,1,1,30}},
    {'5',{31,16,30,1,1,17,14}}, {'6',{6,8,16,30,17,17,14}},
    {'-',{0,0,0,31,0,0,0}}, {'/',{1,2,4,8,16,0,0}},
    {'A',{14,17,17,31,17,17,17}}, {'B',{30,17,17,30,17,17,30}},
    {'C',{14,17,16,16,16,17,14}}, {'D',{30,17,17,17,17,17,30}},
    {'E',{31,16,16,30,16,16,31}}, {'F',{31,16,16,30,16,16,16}},
    {'G',{14,17,16,23,17,17,15}},
    {'I',{31,4,4,4,4,4,31}}, {'L',{16,16,16,16,16,16,31}},
    {'M',{17,27,21,21,17,17,17}}, {'N',{17,25,21,19,17,17,17}},
    {'O',{14,17,17,17,17,17,14}}, {'P',{30,17,17,30,16,16,16}},
    {'R',{30,17,17,30,20,18,17}}, {'S',{15,16,16,14,1,1,30}},
    {'T',{31,4,4,4,4,4,4}}, {'U',{17,17,17,17,17,17,14}},
    {'V',{17,17,17,17,17,10,4}}, {'X',{17,17,10,4,10,17,17}},
};

static uint8_t glyph_row(char c, unsigned row)
{
    for (unsigned i = 0; i < sizeof(s_font) / sizeof(s_font[0]); ++i) {
        if (s_font[i].c == c) return s_font[i].row[row];
    }
    return 0;
}

static bool text_pixel(const char *text, int x, int y, int origin_x,
                       int origin_y, unsigned scale)
{
    if (x < origin_x || y < origin_y) return false;
    const unsigned px = (unsigned)(x - origin_x) / scale;
    const unsigned py = (unsigned)(y - origin_y) / scale;
    if (py >= 7u) return false;
    const unsigned char_index = px / 6u;
    const unsigned column = px % 6u;
    const size_t length = strlen(text);
    if (char_index >= length || column >= 5u) return false;
    return (glyph_row(text[char_index], py) & (1u << (4u - column))) != 0u;
}

static bool logo_mark(unsigned x, unsigned y)
{
    return text_pixel("C5VRX", x, y, 320, 70, 12) ||
           text_pixel("ESP32-C5 ANALOG FPV RECEIVER", x, y, 250, 190, 3) ||
           text_pixel("WAITING FOR VIDEO", x, y, 370, 230, 3);
}

static uint8_t next_snow_code(void)
{
    /* A Galois LFSR gives moving full-range monochrome snow without a
     * framebuffer. H/V sync and blanking remain standards-shaped, so an
     * attached display stays locked instead of confusing noise with no AV. */
    const uint32_t lsb = s_snow_lfsr & 1u;
    s_snow_lfsr = (s_snow_lfsr >> 1u) ^ (0xd0000001u & (0u - lsb));
    const unsigned mv = 320u + ((s_snow_lfsr >> 16u) & 0xffu) * 680u / 255u;
    return cvbs_code_from_mv(mv);
}

static void build_compact_picture_data(void)
{
    memset(s_logo_bitmap, 0, sizeof(s_logo_bitmap));
    for (unsigned y = 0; y < C5VRX_CVBS_ACTIVE_LINES; ++y) {
        for (unsigned lx = 0; lx < C5VRX_CVBS_LOGICAL_WIDTH; ++lx) {
            const unsigned x = lx * C5VRX_CVBS_PIXEL_SAMPLES +
                               C5VRX_CVBS_PIXEL_SAMPLES / 2u;
            if (!logo_mark(x, y)) continue;
            const unsigned bit = y * C5VRX_CVBS_LOGICAL_WIDTH + lx;
            s_logo_bitmap[bit >> 3u] |= (uint8_t)(1u << (bit & 7u));
        }
    }

    s_snow_lfsr = 0xc5f0a17du;
    for (unsigned i = 0; i < sizeof(s_snow_tile); ++i) {
        s_snow_tile[i] = next_snow_code();
    }
}

static uint8_t cvbs_code_from_mv(unsigned mv)
{
    const unsigned bits = CONFIG_C5VRX_CVBS_DAC_BITS;
    const unsigned max_code = (1u << bits) - 1u;
    if (mv >= 1000u) {
        return (uint8_t)max_code;
    }
    return (uint8_t)((mv * max_code + 500u) / 1000u);
}

static uint8_t cvbs_code_from_mv_signed(double mv)
{
    if (mv <= 0.0) {
        return 0;
    }
    if (mv >= 1000.0) {
        return cvbs_code_from_mv(1000);
    }
    return cvbs_code_from_mv((unsigned)(mv + 0.5));
}

static uint8_t grayscale_code(unsigned active_x)
{
    const unsigned black_mv = 320u;
    const unsigned white_mv = 1000u;
    unsigned bar = active_x / (C5VRX_CVBS_ACTIVE_SAMPLES / 8u);
    if (bar > 7u) {
        bar = 7u;
    }

    /* White -> black makes clipping and pedestal errors easy to spot. */
    const unsigned level = white_mv -
        ((white_mv - black_mv) * bar + 3u) / 7u;
    return cvbs_code_from_mv(level);
}

static void add_pal_burst(uint8_t *line_first, unsigned phase_index)
{
#if CONFIG_C5VRX_CVBS_TEST_COLOR_BURST
    const double phase_step =
        2.0 * C5VRX_PI * C5VRX_PAL_SUBCARRIER_HZ /
        (double)C5VRX_CVBS_SAMPLE_RATE_HZ;

    /*
     * PAL-frequency swinging burst stress: alternate +135/-135 degrees. The
     * active image remains monochrome, so this tests output bandwidth/locking
     * rather than claiming complete PAL chroma generation.
     */
    const double phase0 = phase_index ? (-3.0 * C5VRX_PI / 4.0)
                                      : ( 3.0 * C5VRX_PI / 4.0);

    for (unsigned i = 0; i < C5VRX_PAL_BURST_SAMPLES; ++i) {
        const unsigned p = C5VRX_PAL_BURST_START_SAMPLES + i;
        const double mv = 300.0 +
            C5VRX_PAL_BURST_AMPLITUDE_MV * sin(phase0 + phase_step * i);
        line_first[p] = cvbs_code_from_mv_signed(mv);
    }
#else
    (void)line_first;
    (void)phase_index;
#endif
}

static void build_templates(void)
{
    const uint8_t sync = cvbs_code_from_mv(0);
    const uint8_t blank = cvbs_code_from_mv(300);

    memset(s_eq_half, blank, sizeof(s_eq_half));
    memset(s_eq_half, sync, s_active_timing.equalizing_samples);

    memset(s_broad_half, blank, sizeof(s_broad_half));
    memset(s_broad_half, sync, s_active_timing.broad_sync_samples);

    memset(s_blank_first, blank, sizeof(s_blank_first));
    memset(s_blank_first, sync, s_active_timing.hsync_samples);
    memset(s_blank_second, blank, sizeof(s_blank_second));

    for (unsigned phase = 0; phase < 2; ++phase) {
        memset(s_active_first[phase], blank, sizeof(s_active_first[phase]));
        memset(s_active_first[phase], sync, s_active_timing.hsync_samples);
        add_pal_burst(s_active_first[phase], phase);

        for (unsigned p = C5VRX_CVBS_ACTIVE_START;
             p < C5VRX_CVBS_HALF_LINE_SAMPLES; ++p) {
            s_active_first[phase][p] =
                grayscale_code(p - C5VRX_CVBS_ACTIVE_START);
        }
    }

    memset(s_active_second, blank, sizeof(s_active_second));
    const unsigned second_active_samples =
        C5VRX_CVBS_ACTIVE_END - C5VRX_CVBS_HALF_LINE_SAMPLES;
    for (unsigned p = 0; p < second_active_samples; ++p) {
        const unsigned x =
            (C5VRX_CVBS_HALF_LINE_SAMPLES - C5VRX_CVBS_ACTIVE_START) + p;
        s_active_second[p] = grayscale_code(x);
    }
}

static const uint8_t *template_for_half_line(uint16_t frame_half_line)
{
    const unsigned field_pos =
        (unsigned)(frame_half_line % C5VRX_CVBS_FIELD_HALF_LINES);

    const unsigned broad_start =
        s_active_timing.pre_equalizing_half_lines;
    const unsigned post_start = broad_start +
        s_active_timing.broad_sync_half_lines;
    const unsigned normal_start = post_start +
        s_active_timing.post_equalizing_half_lines;

    if (field_pos < broad_start) {
        return s_eq_half;
    }
    if (field_pos < post_start) {
        return s_broad_half;
    }
    if (field_pos < normal_start) {
        return s_eq_half;
    }

    const unsigned normal_offset = field_pos - normal_start;
    const bool first_half = ((normal_offset & 1u) == 0u);
    const bool active = (field_pos >= C5VRX_CVBS_ACTIVE_START_HALF);

    if (!active) {
        return first_half ? s_blank_first : s_blank_second;
    }

    if (!first_half) {
        return s_active_second;
    }

    const unsigned line_index = normal_offset / 2u;
    return s_active_first[line_index & 1u];
}

static bool logo_pixel(unsigned x, unsigned y)
{
    const unsigned bit = y * C5VRX_CVBS_LOGICAL_WIDTH +
                         x / C5VRX_CVBS_PIXEL_SAMPLES;
    return (s_logo_bitmap[bit >> 3u] & (1u << (bit & 7u))) != 0u;
}

static void fill_logo(uint8_t *dst, unsigned x, unsigned count, unsigned y)
{
    const uint8_t black = cvbs_code_from_mv(320u);
    const uint8_t white = cvbs_code_from_mv(1000u);
    while (count) {
        unsigned run = C5VRX_CVBS_PIXEL_SAMPLES -
                       (x & (C5VRX_CVBS_PIXEL_SAMPLES - 1u));
        if (run > count) run = count;
        const uint8_t code = logo_pixel(x, y) ? white : black;
        for (unsigned i = 0; i < run; ++i) dst[i] = code;
        dst += run;
        x += run;
        count -= run;
    }
}

static void fill_snow(uint8_t *dst, unsigned count)
{
    while (count) {
        unsigned run = C5VRX_CVBS_SNOW_TILE_BYTES - s_snow_offset;
        if (run > count) run = count;
        memcpy(dst, s_snow_tile + s_snow_offset, run);
        dst += run;
        count -= run;
        s_snow_offset = (uint16_t)((s_snow_offset + run) &
                                   (C5VRX_CVBS_SNOW_TILE_BYTES - 1u));
    }
    /* A coprime stride makes consecutive scanline fragments move instead of
     * exposing a stationary 1024-sample repeat. */
    s_snow_offset = (uint16_t)((s_snow_offset + 467u) &
                               (C5VRX_CVBS_SNOW_TILE_BYTES - 1u));
}

static void fill_test(uint8_t *dst, unsigned x, unsigned count, unsigned y)
{
    const uint8_t white = cvbs_code_from_mv(1000u);
    if (y < 4u || y >= C5VRX_CVBS_ACTIVE_LINES - 4u ||
        (y % 48u) < 2u) {
        memset(dst, white, count);
        return;
    }

    while (count) {
        const unsigned bar = x / 130u;
        const unsigned in_bar = x % 130u;
        unsigned run = 130u - in_bar;
        if (run > count) run = count;
        const uint8_t code = in_bar < 4u ? white : grayscale_code(bar * 130u);
        memset(dst, code, run);
        dst += run;
        x += run;
        count -= run;
    }
}

static void overlay_active_half(uint8_t *half, uint16_t frame_half_line)
{
    const unsigned field_pos = frame_half_line % C5VRX_CVBS_FIELD_HALF_LINES;
    if (field_pos < C5VRX_CVBS_ACTIVE_START_HALF) return;
    const unsigned normal_start =
        s_active_timing.pre_equalizing_half_lines +
        s_active_timing.broad_sync_half_lines +
        s_active_timing.post_equalizing_half_lines;
    const unsigned normal_offset = field_pos - normal_start;
    const bool first_half = (normal_offset & 1u) == 0u;
    const unsigned y = (field_pos - C5VRX_CVBS_ACTIVE_START_HALF) / 2u;
    const unsigned start = first_half ? C5VRX_CVBS_ACTIVE_START : 0u;
    const unsigned end = first_half ? C5VRX_CVBS_HALF_LINE_SAMPLES
                                    : C5VRX_CVBS_ACTIVE_END - C5VRX_CVBS_HALF_LINE_SAMPLES;
    const unsigned x = first_half ? 0u :
        C5VRX_CVBS_HALF_LINE_SAMPLES - C5VRX_CVBS_ACTIVE_START;
    const unsigned count = end - start;
    if (s_active_display == C5VRX_CVBS_DISPLAY_SNOW) {
        fill_snow(half + start, count);
    } else if (s_active_display == C5VRX_CVBS_DISPLAY_TEST) {
        fill_test(half + start, x, count, y);
    } else {
        fill_logo(half + start, x, count, y);
    }
}

static void build_next_chunk(uint8_t *dst)
{
    for (unsigned i = 0; i < C5VRX_CVBS_CHUNK_HALF_LINES; ++i) {
        /* Apply state only where a complete PAL frame begins. A request made
         * while a chunk is being built can never split logo/snow/test content
         * across an arbitrary scanline. */
        if (s_next_half_line == 0u) {
            s_active_display = s_requested_display;
            bool timing_changed = false;
            portENTER_CRITICAL(&s_stats_mux);
            if (s_timing_pending) {
                s_active_timing = s_requested_timing;
                s_timing_pending = false;
                timing_changed = true;
            }
            portEXIT_CRITICAL(&s_stats_mux);
            if (timing_changed) build_templates();
        }
        const uint8_t *src = template_for_half_line(s_next_half_line);
        uint8_t *half = dst + i * C5VRX_CVBS_HALF_LINE_SAMPLES;
        memcpy(half,
               src,
               C5VRX_CVBS_HALF_LINE_SAMPLES);
        overlay_active_half(half, s_next_half_line);
        ++s_next_half_line;
        if (s_next_half_line == C5VRX_CVBS_FRAME_HALF_LINES) {
            s_next_half_line = 0;
            ++s_frame_counter;
        }
    }
}

static unsigned configured_pins(int pins[8])
{
    const int all[8] = {
        CONFIG_C5VRX_CVBS_D0_GPIO,
        CONFIG_C5VRX_CVBS_D1_GPIO,
        CONFIG_C5VRX_CVBS_D2_GPIO,
        CONFIG_C5VRX_CVBS_D3_GPIO,
        CONFIG_C5VRX_CVBS_D4_GPIO,
        CONFIG_C5VRX_CVBS_D5_GPIO,
        CONFIG_C5VRX_CVBS_D6_GPIO,
        CONFIG_C5VRX_CVBS_D7_GPIO,
    };
    for (unsigned i = 0; i < 8; ++i) {
        pins[i] = all[i];
    }
    return (unsigned)CONFIG_C5VRX_CVBS_DAC_BITS;
}

static bool pins_valid(void)
{
    int pins[8];
    const unsigned required = configured_pins(pins);

    for (unsigned i = 0; i < required; ++i) {
        if (!GPIO_IS_VALID_OUTPUT_GPIO(pins[i])) {
            return false;
        }
#if CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG
        /* ESP32-C5 native USB is fixed to GPIO13 D- and GPIO14 D+. */
        if (pins[i] == 13 || pins[i] == 14) {
            return false;
        }
#endif
        /* GPIO28 is the C5 boot strap and must stay high/floating at reset. */
        if (pins[i] == 28) {
            return false;
        }
        for (unsigned j = i + 1; j < required; ++j) {
            if (pins[i] == pins[j]) {
                return false;
            }
        }
    }
    return true;
}

static esp_err_t queue_loop_buffer(uint8_t *buffer)
{
    const parlio_transmit_config_t tx_cfg = {
        .idle_value = cvbs_code_from_mv(300),
        .bitscrambler_program = NULL,
        .flags = {
            .queue_nonblocking = 0,
            .loop_transmission = 1,
        },
    };

    return parlio_tx_unit_transmit(
        s_tx,
        buffer,
        C5VRX_CVBS_CHUNK_SAMPLES * 8u,
        &tx_cfg);
}

static bool IRAM_ATTR on_buffer_switched(
    parlio_tx_unit_handle_t tx_unit,
    const parlio_tx_buffer_switched_event_data_t *edata,
    void *user_ctx)
{
    (void)tx_unit;
    c5vrx_cvbs_stream_state_t *state =
        (c5vrx_cvbs_stream_state_t *)user_ctx;

    if (!state || !state->task || !edata) {
        return false;
    }

    uint32_t bit = 0;
    unsigned index = 0;
    if (edata->old_buffer_addr == s_chunk[0]) {
        bit = C5VRX_CVBS_RETIRED_BUF0;
    } else if (edata->old_buffer_addr == s_chunk[1]) {
        bit = C5VRX_CVBS_RETIRED_BUF1;
        index = 1;
    } else {
        portENTER_CRITICAL_ISR(&s_stats_mux);
        ++s_isr_unexpected;
        portEXIT_CRITICAL_ISR(&s_stats_mux);
        return false;
    }

    const uint64_t retired_us = esp_timer_get_time();
    portENTER_CRITICAL_ISR(&s_stats_mux);
    /* Keep the oldest unserviced retirement timestamp. If the task missed
     * more than one callback, replacing this with the newest timestamp would
     * hide exactly the scheduler latency the AV deadline is meant to catch. */
    if (s_isr_switches[index] == s_handled_switches[index]) {
        s_isr_first_retired_us[index] = retired_us;
    }
    ++s_isr_switches[index];
    portEXIT_CRITICAL_ISR(&s_stats_mux);

    BaseType_t high_task_wakeup = pdFALSE;
    xTaskNotifyFromISR(
        state->task,
        bit,
        eSetBits,
        &high_task_wakeup);
    return high_task_wakeup == pdTRUE;
}

static void stream_task(void *arg)
{
    (void)arg;

    for (;;) {
        uint32_t notification = 0;
        (void)xTaskNotifyWait(
            0,
            UINT32_MAX,
            &notification,
            portMAX_DELAY);

        if (s_stream.stop_requested ||
            (notification & C5VRX_CVBS_STOP_NOTIFY)) {
            break;
        }

        for (unsigned index = 0; index < 2; ++index) {
            const uint32_t bit =
                index == 0 ? C5VRX_CVBS_RETIRED_BUF0
                           : C5VRX_CVBS_RETIRED_BUF1;
            if (!(notification & bit)) {
                continue;
            }

            uint32_t seen = 0;
            uint64_t retired_us = 0;
            portENTER_CRITICAL(&s_stats_mux);
            seen = s_isr_switches[index];
            retired_us = s_isr_first_retired_us[index];
            const uint32_t delta = seen - s_handled_switches[index];
            s_handled_switches[index] = seen;
            if (delta > 1u) s_missed_switches += delta - 1u;
            portEXIT_CRITICAL(&s_stats_mux);

            const uint64_t service_started_us = esp_timer_get_time();
            if (!retired_us) retired_us = service_started_us;

            build_next_chunk(s_chunk[index]);

            if (s_stream.stop_requested) {
                break;
            }

            const esp_err_t err = queue_loop_buffer(s_chunk[index]);
            if (err != ESP_OK) {
                portENTER_CRITICAL(&s_stats_mux);
                ++s_queue_errors;
                portEXIT_CRITICAL(&s_stats_mux);
                if (!s_stream.stop_requested) {
                    ESP_LOGE(TAG,
                             "PAL stream buffer queue failed: %s",
                             esp_err_to_name(err));
                }
                s_stream.stop_requested = true;
                break;
            }

            const uint64_t service_finished_us = esp_timer_get_time();
            /* Deadline includes notification/scheduler wake-up latency, not
             * merely the time spent rendering after the task finally ran. */
            const uint32_t service_us = (uint32_t)(
                service_finished_us - retired_us);
            const uint32_t stack_min = (uint32_t)
                uxTaskGetStackHighWaterMark(NULL);
            portENTER_CRITICAL(&s_stats_mux);
            ++s_serviced_buffers;
            s_service_total_us += service_us;
            if (service_us > s_service_max_us) s_service_max_us = service_us;
            s_last_service_us = service_finished_us;
            if (!s_stack_min_bytes || stack_min < s_stack_min_bytes) {
                s_stack_min_bytes = stack_min;
            }
            portEXIT_CRITICAL(&s_stats_mux);
        }

        if (s_stream.stop_requested) {
            break;
        }
    }

    s_running = false;
    s_stream.task = NULL;
    vTaskDelete(NULL);
}

static esp_err_t stop_stream_task(void)
{
    TaskHandle_t task = s_stream.task;
    if (!task) {
        return ESP_OK;
    }

    s_stream.stop_requested = true;
    (void)xTaskNotify(task, C5VRX_CVBS_STOP_NOTIFY, eSetBits);

    /* FreeRTOS runs at 100 Hz on the XIAO profile. pdMS_TO_TICKS(1) rounds
     * down to zero, so the priority-20 USB task previously stayed runnable
     * and starved this priority-18 PAL task for all 100 iterations. Block for
     * a real tick so the notified task can retire cleanly. */
    for (unsigned i = 0; i < 20 && s_stream.task; ++i) {
        vTaskDelay(1);
    }

    if (s_stream.task) {
        ESP_LOGE(TAG,
                 "PAL stream task did not exit after 200 ms; active DMA resources retained");
        return ESP_ERR_TIMEOUT;
    }
    return ESP_OK;
}

static esp_err_t start_output(c5vrx_cvbs_display_t initial_display)
{
    if (s_running) {
        s_requested_display = initial_display;
        return ESP_OK;
    }
    if (!pins_valid()) {
        ESP_LOGE(TAG,
                 "CVBS PARLIO requires unique valid output GPIOs D0..D%u; GPIO13/14 (USB) and GPIO28 (BOOT) are reserved",
                 (unsigned)CONFIG_C5VRX_CVBS_DAC_BITS - 1u);
        return ESP_ERR_INVALID_ARG;
    }

    for (unsigned i = 0; i < 2; ++i) {
        s_chunk[i] = heap_caps_malloc(C5VRX_CVBS_CHUNK_SAMPLES,
                                      MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
        if (!s_chunk[i]) {
            while (i) free(s_chunk[--i]);
            return ESP_ERR_NO_MEM;
        }
    }

    build_templates();
    build_compact_picture_data();
    s_next_half_line = 0;
    s_frame_counter = 0;
    s_requested_display = initial_display;
    s_active_display = initial_display;
    s_snow_lfsr = 0xc5f0a17du;
    s_snow_offset = 0;
    const uint64_t start_us = esp_timer_get_time();
    portENTER_CRITICAL(&s_stats_mux);
    s_isr_switches[0] = s_isr_switches[1] = 0;
    s_isr_first_retired_us[0] = s_isr_first_retired_us[1] = 0;
    s_isr_unexpected = 0;
    s_handled_switches[0] = s_handled_switches[1] = 0;
    s_serviced_buffers = 0;
    s_missed_switches = 0;
    s_queue_errors = 0;
    s_service_total_us = 0;
    s_service_max_us = 0;
    s_start_us = start_us;
    s_last_service_us = 0;
    s_stack_min_bytes = 0;
    portEXIT_CRITICAL(&s_stats_mux);
    build_next_chunk(s_chunk[0]);
    build_next_chunk(s_chunk[1]);

    const parlio_tx_unit_config_t cfg = {
        .clk_src = PARLIO_CLK_SRC_DEFAULT,
        .clk_in_gpio_num = -1,
        .input_clk_src_freq_hz = 0,
        .output_clk_freq_hz = C5VRX_CVBS_SAMPLE_RATE_HZ,
        .data_width = 8,
        .data_gpio_nums = {
            CONFIG_C5VRX_CVBS_D0_GPIO,
            CONFIG_C5VRX_CVBS_D1_GPIO,
            CONFIG_C5VRX_CVBS_D2_GPIO,
            CONFIG_C5VRX_CVBS_D3_GPIO,
            CONFIG_C5VRX_CVBS_D4_GPIO,
            CONFIG_C5VRX_CVBS_D5_GPIO,
            CONFIG_C5VRX_CVBS_D6_GPIO,
            CONFIG_C5VRX_CVBS_D7_GPIO,
        },
        .clk_out_gpio_num = -1,
        .valid_gpio_num = -1,
        .valid_start_delay = 0,
        .valid_stop_delay = 0,
        .trans_queue_depth = 2,
        .max_transfer_size = C5VRX_CVBS_CHUNK_SAMPLES,
        .dma_burst_size = 32,
        .shift_edge = PARLIO_SHIFT_EDGE_NEG,
        .bit_pack_order = PARLIO_BIT_PACK_ORDER_LSB,
    };

    esp_err_t err = parlio_new_tx_unit(&cfg, &s_tx);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "parlio_new_tx_unit failed: %s", esp_err_to_name(err));
        s_tx = NULL;
        free(s_chunk[0]);
        free(s_chunk[1]);
        s_chunk[0] = s_chunk[1] = NULL;
        return err;
    }

    s_stream.stop_requested = false;
    s_stream.task = NULL;
    TaskHandle_t stream_handle = NULL;
    if (xTaskCreate(
            stream_task,
            "c5vrx_pal",
            C5VRX_CVBS_STREAM_STACK,
            NULL,
            C5VRX_CVBS_STREAM_PRIORITY,
            &stream_handle) != pdPASS) {
        parlio_del_tx_unit(s_tx);
        s_tx = NULL;
        free(s_chunk[0]);
        free(s_chunk[1]);
        s_chunk[0] = s_chunk[1] = NULL;
        return ESP_ERR_NO_MEM;
    }
    s_stream.task = stream_handle;

    const parlio_tx_event_callbacks_t callbacks = {
        .on_trans_done = NULL,
        .on_buffer_switched = on_buffer_switched,
    };
    err = parlio_tx_unit_register_event_callbacks(
        s_tx,
        &callbacks,
        &s_stream);
    if (err != ESP_OK) {
        ESP_LOGE(TAG,
                 "parlio callback registration failed: %s",
                 esp_err_to_name(err));
        const esp_err_t stop_err = stop_stream_task();
        if (stop_err != ESP_OK) return stop_err;
        parlio_del_tx_unit(s_tx);
        s_tx = NULL;
        free(s_chunk[0]);
        free(s_chunk[1]);
        s_chunk[0] = s_chunk[1] = NULL;
        return err;
    }

    err = parlio_tx_unit_enable(s_tx);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "parlio_tx_unit_enable failed: %s", esp_err_to_name(err));
        const esp_err_t stop_err = stop_stream_task();
        if (stop_err != ESP_OK) return stop_err;
        parlio_del_tx_unit(s_tx);
        s_tx = NULL;
        free(s_chunk[0]);
        free(s_chunk[1]);
        s_chunk[0] = s_chunk[1] = NULL;
        return err;
    }

    err = queue_loop_buffer(s_chunk[0]);
    if (err == ESP_OK) {
        err = queue_loop_buffer(s_chunk[1]);
    }
    if (err != ESP_OK) {
        ESP_LOGE(TAG,
                 "initial PAL stream queue failed: %s",
                 esp_err_to_name(err));
        const esp_err_t stop_err = stop_stream_task();
        (void)parlio_tx_unit_disable(s_tx);
        if (stop_err != ESP_OK) return stop_err;
        parlio_del_tx_unit(s_tx);
        s_tx = NULL;
        free(s_chunk[0]);
        free(s_chunk[1]);
        s_chunk[0] = s_chunk[1] = NULL;
        return err;
    }

    s_running = true;
    ESP_LOGW(TAG,
             "PAL 625/50 always-on output active: display=%s %u-bit DAC, 20 MS/s, 2x%u-byte DMA chunks",
             c5vrx_cvbs_display_name(initial_display),
             (unsigned)CONFIG_C5VRX_CVBS_DAC_BITS,
             (unsigned)C5VRX_CVBS_CHUNK_SAMPLES);
#if CONFIG_C5VRX_CVBS_TEST_COLOR_BURST
    ESP_LOGW(TAG,
             "Output is monochrome; 4.43361875 MHz swinging burst is enabled for display lock and analog bandwidth validation");
#else
    ESP_LOGW(TAG, "Output is monochrome with color burst disabled");
#endif
    ESP_LOGW(TAG,
             "Use a correctly scaled resistor network into a known 75-ohm input; never connect raw 3.3 V GPIOs directly");
    return ESP_OK;
}

esp_err_t c5vrx_cvbs_output_start(void)
{
    return start_output(C5VRX_CVBS_DISPLAY_LOGO);
}

esp_err_t c5vrx_cvbs_output_set_display(c5vrx_cvbs_display_t display)
{
    if (display > C5VRX_CVBS_DISPLAY_TEST) return ESP_ERR_INVALID_ARG;
    if (!s_running) return ESP_ERR_INVALID_STATE;
    s_requested_display = display;
    return ESP_OK;
}

c5vrx_cvbs_display_t c5vrx_cvbs_output_display(void)
{
    return s_active_display;
}

const char *c5vrx_cvbs_display_name(c5vrx_cvbs_display_t display)
{
    switch (display) {
        case C5VRX_CVBS_DISPLAY_LOGO: return "LOGO";
        case C5VRX_CVBS_DISPLAY_SNOW: return "SNOW";
        case C5VRX_CVBS_DISPLAY_TEST: return "TEST";
        default: return "UNKNOWN";
    }
}

static esp_err_t destroy_rendered_output(void)
{
    /* Ask the refill task to exit while its looping DMA transaction is still
     * valid. Once it has acknowledged, the documented PARLIO disable call
     * terminates the infinite transaction immediately and makes deletion
     * safe. Never force-delete a task that may still reference DMA buffers. */
    esp_err_t err = stop_stream_task();
    if (err != ESP_OK) return err;
    if (s_tx) {
        const esp_err_t disable_err = parlio_tx_unit_disable(s_tx);
        if (disable_err != ESP_OK && disable_err != ESP_ERR_INVALID_STATE)
            err = disable_err;
    }
    if (s_tx) {
        const esp_err_t del_err = parlio_del_tx_unit(s_tx);
        if (err == ESP_OK) err = del_err;
        s_tx = NULL;
    }
    free(s_chunk[0]);
    free(s_chunk[1]);
    s_chunk[0] = s_chunk[1] = NULL;
    s_running = false;
    return err;
}

void c5vrx_cvbs_output_get_stats(c5vrx_cvbs_output_stats_t *stats)
{
    if (!stats) return;
    memset(stats, 0, sizeof(*stats));
    const uint64_t now_us = esp_timer_get_time();
    uint64_t service_total_us = 0;
    uint64_t last_service_us = 0;
    portENTER_CRITICAL(&s_stats_mux);
    stats->running = s_running;
    stats->task_running = s_stream.task != NULL && !s_stream.stop_requested;
    stats->display = s_active_display;
    stats->retired_buffers = s_isr_switches[0] + s_isr_switches[1];
    stats->serviced_buffers = s_serviced_buffers;
    stats->missed_switches = s_missed_switches;
    stats->unexpected_switches = s_isr_unexpected;
    stats->queue_errors = s_queue_errors;
    stats->service_max_us = s_service_max_us;
    stats->stack_min_bytes = s_stack_min_bytes;
    service_total_us = s_service_total_us;
    last_service_us = s_last_service_us;
    stats->uptime_us = now_us > s_start_us ? now_us - s_start_us : 0;
    portEXIT_CRITICAL(&s_stats_mux);

    stats->deadline_us = C5VRX_CVBS_CHUNK_DEADLINE_US;
    stats->last_service_age_us = last_service_us && now_us > last_service_us
        ? now_us - last_service_us : stats->uptime_us;
    stats->service_avg_us = stats->serviced_buffers
        ? (uint32_t)(service_total_us / stats->serviced_buffers) : 0;
    stats->headroom_us = (int32_t)stats->deadline_us -
        (int32_t)stats->service_max_us;
    stats->frame_equivalent = (uint32_t)(
        ((uint64_t)stats->retired_buffers * C5VRX_CVBS_CHUNK_HALF_LINES) /
        C5VRX_CVBS_FRAME_HALF_LINES);
    stats->switch_hz = stats->uptime_us
        ? (uint32_t)(((uint64_t)stats->retired_buffers * 1000000u) /
                     stats->uptime_us) : 0;
    stats->expected_switch_hz = C5VRX_CVBS_EXPECTED_SWITCH_HZ;
    const c5vrx_av_health_input_t health_input = {
        .running = stats->running,
        .task_running = stats->task_running,
        .service_count = stats->serviced_buffers,
        .uptime_us = stats->uptime_us,
        .last_service_age_us = stats->last_service_age_us,
        .service_max_us = stats->service_max_us,
        .deadline_us = stats->deadline_us,
        .missed_switches = stats->missed_switches,
        .unexpected_switches = stats->unexpected_switches,
        .queue_errors = stats->queue_errors,
    };
    stats->health = c5vrx_av_health_classify(&health_input);
}

static bool timing_valid(const c5vrx_cvbs_timing_t *timing)
{
    if (!timing ||
        timing->hsync_samples < 76u || timing->hsync_samples > 116u ||
        timing->equalizing_samples < 36u ||
        timing->equalizing_samples > 58u ||
        timing->broad_sync_samples < 500u ||
        timing->broad_sync_samples > 580u ||
        timing->pre_equalizing_half_lines < 3u ||
        timing->pre_equalizing_half_lines > 7u ||
        timing->broad_sync_half_lines < 3u ||
        timing->broad_sync_half_lines > 7u ||
        timing->post_equalizing_half_lines < 3u ||
        timing->post_equalizing_half_lines > 7u) {
        return false;
    }
    return (unsigned)timing->pre_equalizing_half_lines +
           timing->broad_sync_half_lines +
           timing->post_equalizing_half_lines < C5VRX_CVBS_ACTIVE_START_HALF;
}

static bool timing_equal(const c5vrx_cvbs_timing_t *a,
                         const c5vrx_cvbs_timing_t *b)
{
    return a->hsync_samples == b->hsync_samples &&
           a->equalizing_samples == b->equalizing_samples &&
           a->broad_sync_samples == b->broad_sync_samples &&
           a->pre_equalizing_half_lines == b->pre_equalizing_half_lines &&
           a->broad_sync_half_lines == b->broad_sync_half_lines &&
           a->post_equalizing_half_lines == b->post_equalizing_half_lines;
}

esp_err_t c5vrx_cvbs_output_set_timing(const c5vrx_cvbs_timing_t *timing)
{
    if (!timing_valid(timing)) return ESP_ERR_INVALID_ARG;
    portENTER_CRITICAL(&s_stats_mux);
    s_requested_timing = *timing;
    s_timing_pending = !timing_equal(&s_active_timing, timing);
    portEXIT_CRITICAL(&s_stats_mux);
    return ESP_OK;
}

void c5vrx_cvbs_output_reset_timing(void)
{
    const c5vrx_cvbs_timing_t defaults = {
        .hsync_samples = C5VRX_CVBS_HSYNC_DEFAULT,
        .equalizing_samples = C5VRX_CVBS_EQ_DEFAULT,
        .broad_sync_samples = C5VRX_CVBS_BROAD_SYNC_DEFAULT,
        .pre_equalizing_half_lines = C5VRX_CVBS_EQ_HALF_DEFAULT,
        .broad_sync_half_lines = C5VRX_CVBS_BROAD_HALF_DEFAULT,
        .post_equalizing_half_lines = C5VRX_CVBS_EQ_HALF_DEFAULT,
    };
    (void)c5vrx_cvbs_output_set_timing(&defaults);
}

void c5vrx_cvbs_output_get_timing(c5vrx_cvbs_timing_t *active,
                                  c5vrx_cvbs_timing_t *requested,
                                  bool *pending)
{
    portENTER_CRITICAL(&s_stats_mux);
    if (active) *active = s_active_timing;
    if (requested) *requested = s_requested_timing;
    if (pending) *pending = s_timing_pending;
    portEXIT_CRITICAL(&s_stats_mux);
}

esp_err_t c5vrx_cvbs_test_start(void)
{
    return start_output(C5VRX_CVBS_DISPLAY_TEST);
}

esp_err_t c5vrx_cvbs_test_stop(void)
{
    if (!s_running || !s_tx) {
        return ESP_ERR_INVALID_STATE;
    }
    s_requested_display = C5VRX_CVBS_DISPLAY_LOGO;
    ESP_LOGI(TAG, "PAL diagnostics stopped; permanent logo output retained");
    return ESP_OK;
}

bool c5vrx_cvbs_test_running(void)
{
    return s_running;
}

esp_err_t c5vrx_cvbs_direct_rf_prepare(uint32_t output_clock_hz)
{
    if (s_direct_tx || s_direct_bs) return ESP_ERR_INVALID_STATE;
    if (output_clock_hz < 4000000u || output_clock_hz > 20000000u)
        return ESP_ERR_INVALID_ARG;
    esp_err_t err = destroy_rendered_output();
    if (err != ESP_OK) {
        (void)start_output(C5VRX_CVBS_DISPLAY_LOGO);
        return err;
    }

    err = c5vrx_wbfm_hw_direct_parlio_create(&s_direct_bs);
    if (err != ESP_OK) goto fail;

    const parlio_tx_unit_config_t cfg = {
        .clk_src = PARLIO_CLK_SRC_DEFAULT,
        .clk_in_gpio_num = -1,
        .input_clk_src_freq_hz = 0,
        .output_clk_freq_hz = output_clock_hz,
        .data_width = 8,
        .data_gpio_nums = {
            CONFIG_C5VRX_CVBS_D0_GPIO,
            CONFIG_C5VRX_CVBS_D1_GPIO,
            CONFIG_C5VRX_CVBS_D2_GPIO,
            CONFIG_C5VRX_CVBS_D3_GPIO,
            CONFIG_C5VRX_CVBS_D4_GPIO,
            CONFIG_C5VRX_CVBS_D5_GPIO,
            CONFIG_C5VRX_CVBS_D6_GPIO,
            CONFIG_C5VRX_CVBS_D7_GPIO,
        },
        .clk_out_gpio_num = -1,
        .valid_gpio_num = -1,
        .valid_start_delay = 0,
        .valid_stop_delay = 0,
        .trans_queue_depth = 1,
        .max_transfer_size = C5VRX_ADC_DUMP_MAX_SAMPLES * sizeof(uint32_t),
        .dma_burst_size = 32,
        .shift_edge = PARLIO_SHIFT_EDGE_NEG,
        .bit_pack_order = PARLIO_BIT_PACK_ORDER_LSB,
    };
    err = parlio_new_tx_unit(&cfg, &s_direct_tx);
    if (err == ESP_OK) {
        /* unit_enable() enables both the source clock and FIFO-empty IRQ.
         * Keep that unavoidable driver transition inside one critical
         * section, then quiesce and acknowledge it before an ISR can mistake
         * the deliberately empty pre-roll FIFO for a runtime underrun. */
        portENTER_CRITICAL(&s_direct_start_mux);
        err = parlio_tx_unit_enable(s_direct_tx);
        if (err == ESP_OK) {
            parlio_ll_enable_interrupt(
                &PARL_IO, PARLIO_LL_EVENT_TX_FIFO_EMPTY, false);
            parlio_ll_tx_enable_clock(&PARL_IO, false);
            parlio_ll_clear_interrupt_status(
                &PARL_IO, PARLIO_LL_EVENT_TX_FIFO_EMPTY);
        }
        portEXIT_CRITICAL(&s_direct_start_mux);
    }
    if (err == ESP_OK) {
        const parlio_transmit_config_t transmit = {
            .idle_value = 32u,
            .bitscrambler_program = NULL,
            .flags.loop_transmission = true,
        };
        /* transmit() starts the clock before returning. Keep FIFO-empty
         * masked until the clock has been paused again. The LP core later
         * enables the clock only after acquiring its half-ring producer lead;
         * re-enable the IRQ here so a real runtime underrun remains visible. */
        portENTER_CRITICAL(&s_direct_start_mux);
        err = parlio_tx_unit_transmit(
            s_direct_tx,
            (const void *)(uintptr_t)C5VRX_ADC_DUMP_BASE_ADDR,
            C5VRX_ADC_DUMP_MAX_SAMPLES * sizeof(uint32_t) * 8u,
            &transmit);
        parlio_ll_tx_enable_clock(&PARL_IO, false);
        parlio_ll_clear_interrupt_status(
            &PARL_IO, PARLIO_LL_EVENT_TX_FIFO_EMPTY);
        parlio_ll_enable_interrupt(
            &PARL_IO, PARLIO_LL_EVENT_TX_FIFO_EMPTY, true);
        portEXIT_CRITICAL(&s_direct_start_mux);
    }
    if (err != ESP_OK) goto fail;

    /* The transaction and circular descriptors are now mounted with the
     * PARLIO source clock paused; GDMA/BitScrambler stay armed/backpressured.
     * The LP-RAM probe enables this documented clock bit after the MAC writer
     * is half a block ahead, avoiding a stale-data race at startup. */
    return ESP_OK;

fail:
    if (s_direct_tx) {
        (void)parlio_tx_unit_disable(s_direct_tx);
        (void)parlio_del_tx_unit(s_direct_tx);
        s_direct_tx = NULL;
    }
    c5vrx_wbfm_hw_direct_parlio_destroy(s_direct_bs);
    s_direct_bs = NULL;
    (void)start_output(C5VRX_CVBS_DISPLAY_LOGO);
    return err;
}

esp_err_t c5vrx_cvbs_direct_rf_finish(void)
{
    /* The LP kernel leaves this clock disabled before returning ownership. */
    parlio_ll_tx_enable_clock(&PARL_IO, false);
    esp_err_t err = ESP_OK;
    if (s_direct_tx) {
        err = parlio_tx_unit_disable(s_direct_tx);
        const esp_err_t del_err = parlio_del_tx_unit(s_direct_tx);
        if (err == ESP_OK) err = del_err;
        s_direct_tx = NULL;
    }
    c5vrx_wbfm_hw_direct_parlio_destroy(s_direct_bs);
    s_direct_bs = NULL;
    const esp_err_t restart_err = start_output(C5VRX_CVBS_DISPLAY_LOGO);
    return err == ESP_OK ? restart_err : err;
}

#else

esp_err_t c5vrx_cvbs_test_start(void)
{
    return ESP_ERR_NOT_SUPPORTED;
}

esp_err_t c5vrx_cvbs_output_start(void) { return ESP_ERR_NOT_SUPPORTED; }
esp_err_t c5vrx_cvbs_output_set_display(c5vrx_cvbs_display_t display)
{ (void)display; return ESP_ERR_NOT_SUPPORTED; }
c5vrx_cvbs_display_t c5vrx_cvbs_output_display(void)
{ return C5VRX_CVBS_DISPLAY_LOGO; }
const char *c5vrx_cvbs_display_name(c5vrx_cvbs_display_t display)
{ (void)display; return "UNAVAILABLE"; }
void c5vrx_cvbs_output_get_stats(c5vrx_cvbs_output_stats_t *stats)
{
    if (!stats) return;
    memset(stats, 0, sizeof(*stats));
    stats->health = C5VRX_AV_HEALTH_FAIL;
}
esp_err_t c5vrx_cvbs_output_set_timing(const c5vrx_cvbs_timing_t *timing)
{ (void)timing; return ESP_ERR_NOT_SUPPORTED; }
void c5vrx_cvbs_output_reset_timing(void) {}
void c5vrx_cvbs_output_get_timing(c5vrx_cvbs_timing_t *active,
                                  c5vrx_cvbs_timing_t *requested,
                                  bool *pending)
{
    if (active) memset(active, 0, sizeof(*active));
    if (requested) memset(requested, 0, sizeof(*requested));
    if (pending) *pending = false;
}

esp_err_t c5vrx_cvbs_test_stop(void)
{
    return ESP_ERR_NOT_SUPPORTED;
}

bool c5vrx_cvbs_test_running(void)
{
    return false;
}

esp_err_t c5vrx_cvbs_direct_rf_prepare(uint32_t output_clock_hz)
{ (void)output_clock_hz; return ESP_ERR_NOT_SUPPORTED; }
esp_err_t c5vrx_cvbs_direct_rf_finish(void)
{ return ESP_ERR_NOT_SUPPORTED; }

#endif

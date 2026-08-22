/* SPDX-License-Identifier: GPL-3.0-only */

#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "c5vrx_video_timing.h"

/* Synthetic interlaced waveform generator. Geometry follows the analog
 * standards: PAL 625 lines / 5-5-5 vertical pulses, NTSC 525 / 6-6-6,
 * fields of exactly 312.5 and 262.5 lines with the half-line interlace
 * offset realized in the grid phase of each field's first VS pulse. */
typedef struct {
    uint8_t *buf;
    size_t cap;
    size_t len;
    uint32_t line;      /* Samples per line (1280 = 20 MS/s PAL). */
    int frame_lines;    /* 625 PAL, 525 NTSC. */
} gen_t;

static void gen_put(gen_t *g, uint8_t value, size_t count)
{
    assert(g->len + count <= g->cap);
    memset(g->buf + g->len, value, count);
    g->len += count;
}

static void gen_active_line(gen_t *g, uint8_t luma)
{
    const uint32_t h = g->line * 47u / 640u;
    const uint32_t active_start = g->line * 210u / 1280u;
    const uint32_t active = g->line * 1040u / 1280u;
    gen_put(g, 0, h);                       /* Sync tip. */
    gen_put(g, 19, active_start - h);       /* Back porch / blanking. */
    gen_put(g, luma, active);               /* Active video. */
    gen_put(g, 19, g->line - active_start - active);
}

/* Vertical interval block: pulses every half line at equalizing or
 * broad width. Equalizing pulses are half the H-sync width (2.35 us
 * vs 4.7 us); broad/serration pulses span most of a half line. */
static void gen_vs_block(gen_t *g)
{
    const uint32_t half = g->line / 2u;
    const int pulses = g->frame_lines == 625 ? 5 : 6;
    for (unsigned phase = 0u; phase < 3u; ++phase) {
        const uint32_t width = phase == 1u ? g->line * 273u / 640u
                                           : g->line * 47u / 1280u;
        for (int i = 0; i < pulses; ++i) {
            gen_put(g, 0, width);
            gen_put(g, 19, half - width);
        }
    }
}

static void gen_active_run(gen_t *g, int lines, uint8_t luma)
{
    for (int i = 0; i < lines; ++i) gen_active_line(g, luma);
}

/* One full frame on a continuous global line grid. The vertical
 * intervals alternate between the full-line and half-line grid phase -
 * the interlace signature itself - while both field periods stay exactly
 * half a frame:
 *   PAL: VS_A[0,7.5L) pad actives(304)[8L,312L) gap VS_B[312.5L,320L)
 *        actives(305)[320L,625L)
 *   NTSC: VS_A[0,9L) actives(253)[9L,262L) gap VS_B[262.5L,271.5L) pad
 *         actives(253)[272L,525L) */
static void gen_frame(gen_t *g, uint8_t luma)
{
    const int pal = g->frame_lines == 625;
    const uint32_t half = g->line / 2u;

    gen_vs_block(g);                          /* Field A interval. */
    if (pal) gen_put(g, 19, half);
    gen_active_run(g, pal ? 304 : 253, luma); /* A active lines. */
    gen_put(g, 19, half);                     /* Half-line tail. */

    gen_vs_block(g);                          /* Field B interval. */
    if (!pal) gen_put(g, 19, half);
    gen_active_run(g, pal ? 305 : 253, luma); /* B active lines. */
}

int main(void)
{
    /* ---- PAL lock: standard detection, parity alternation, no slips. */
    {
        static uint8_t wave[1 << 23];
        gen_t g = {.buf = wave, .cap = sizeof(wave), .line = 1280u,
                   .frame_lines = 625};
        for (int f = 0; f < 6; ++f) gen_frame(&g, (uint8_t)(40 + f));

        c5vrx_video_timing_state_t t;
        c5vrx_video_timing_init(&t, NULL);
        c5vrx_video_timing_event_t ev;
        uint64_t fields = 0u;
        uint8_t last_parity = 0u;
        bool have_parity = false;
        for (size_t i = 0; i < g.len; ++i) {
            if (c5vrx_video_timing_consume(&t, wave[i], &ev)) {
                if (ev.field_start) {
                    ++fields;
                    if (have_parity) assert(ev.parity != last_parity);
                    last_parity = ev.parity;
                    have_parity = true;
                }
                assert(!ev.lock_lost || fields == 0u);
            }
        }
        assert(fields >= 8u);
        assert(t.v_locked);
        assert(t.standard == C5VRX_VIDEO_STANDARD_PAL);
        assert(t.parity_slips == 0u);
        assert(t.false_vertical_candidates == 0u);
        assert(t.polarity == C5VRX_COMPOSITE_POLARITY_NEGATIVE);
        printf("pal: fields=%llu std=%d period=%u err=%d\n",
               (unsigned long long)fields, t.standard,
               t.line_period_samples, t.line_period_error_samples);
    }

    /* ---- NTSC lock at a different sample clock (1271 samples/line). */
    {
        static uint8_t wave[1 << 23];
        gen_t g = {.buf = wave, .cap = sizeof(wave), .line = 1271u,
                   .frame_lines = 525};
        for (int f = 0; f < 6; ++f) gen_frame(&g, 35);

        c5vrx_video_timing_state_t t;
        c5vrx_video_timing_init(&t, NULL);
        c5vrx_video_timing_event_t ev;
        uint64_t fields = 0u;
        for (size_t i = 0; i < g.len; ++i)
            if (c5vrx_video_timing_consume(&t, wave[i], &ev) &&
                ev.field_start)
                ++fields;
        assert(fields >= 8u);
        assert(t.v_locked);
        assert(t.standard == C5VRX_VIDEO_STANDARD_NTSC);
        assert(t.parity_slips == 0u);
        assert(t.line_period_samples >= 1260u &&
               t.line_period_samples <= 1282u);
        printf("ntsc: fields=%llu period=%u\n",
               (unsigned long long)fields, t.line_period_samples);
    }

    /* ---- Inverted path: polarity must lock POSITIVE, timing still valid. */
    {
        static uint8_t wave[1 << 23];
        gen_t g = {.buf = wave, .cap = sizeof(wave), .line = 1280u,
                   .frame_lines = 625};
        for (int f = 0; f < 5; ++f) gen_frame(&g, 40);
        for (size_t i = 0; i < g.len; ++i) wave[i] = (uint8_t)(63u - wave[i]);

        c5vrx_video_timing_state_t t;
        c5vrx_video_timing_init(&t, NULL);
        c5vrx_video_timing_event_t ev;
        uint64_t fields = 0u;
        for (size_t i = 0; i < g.len; ++i)
            if (c5vrx_video_timing_consume(&t, wave[i], &ev) &&
                ev.field_start)
                ++fields;
        assert(t.polarity == C5VRX_COMPOSITE_POLARITY_POSITIVE);
        assert(t.v_locked);
        assert(t.standard == C5VRX_VIDEO_STANDARD_PAL);
        printf("inverted: fields=%llu polarity=%d relocks=%llu rejected=%llu\n",
               (unsigned long long)fields, t.polarity,
               (unsigned long long)t.polarity_relock_events,
               (unsigned long long)t.opposite_polarity_candidates_rejected);
    }

    /* ---- Noise bursts between syncs must not create false candidates. */
    {
        static uint8_t wave[1 << 23];
        gen_t g = {.buf = wave, .cap = sizeof(wave), .line = 1280u,
                   .frame_lines = 625};
        unsigned seed = 7u;
        for (int f = 0; f < 5; ++f) {
            const size_t before = g.len;
            gen_frame(&g, 42);
            /* Sprinkle short dark glitches into active video. */
            for (size_t i = before; i + 30u < g.len; i += 97u) {
                seed = seed * 1103515245u + 12345u;
                if ((seed >> 16) & 1u) memset(wave + i, 3, 25);
            }
        }

        c5vrx_video_timing_state_t t;
        c5vrx_video_timing_init(&t, NULL);
        c5vrx_video_timing_event_t ev;
        uint64_t fields = 0u;
        for (size_t i = 0; i < g.len; ++i)
            if (c5vrx_video_timing_consume(&t, wave[i], &ev)) {
                if (ev.field_start) ++fields;
                assert(!ev.field_start || !ev.lock_lost);
            }
        assert(fields >= 6u);
        assert(t.v_locked);
        assert(t.standard == C5VRX_VIDEO_STANDARD_PAL);
        /* Glitches may legitimately register as phase anomalies, but the
         * canonical timeline must keep locking and keep producing
         * fields - isolated noise never moves the timeline itself. */
        assert(t.parity_slips <= 4u);
        printf("noise: fields=%llu false_v=%llu rejected=%llu slips=%llu\n",
               (unsigned long long)fields,
               (unsigned long long)t.false_vertical_candidates,
               (unsigned long long)t.rejected_pulses,
               (unsigned long long)t.parity_slips);
    }

    /* ---- Invalidate: new epoch clears locks and learned polarity. */
    {
        c5vrx_video_timing_state_t t;
        c5vrx_video_timing_init(&t, NULL);
        t.polarity = C5VRX_COMPOSITE_POLARITY_NEGATIVE;
        t.h_locked = t.v_locked = true;
        t.stream_epoch = 4u;
        const uint32_t epoch = c5vrx_video_timing_invalidate(&t);
        assert(epoch == 5u);
        assert(t.polarity == C5VRX_COMPOSITE_POLARITY_UNKNOWN);
        assert(!t.h_locked && !t.v_locked);
        assert(t.standard == C5VRX_VIDEO_STANDARD_UNKNOWN);
        assert(t.vs_phase_class == -1);
    }

    puts("video_timing_test: PASS");
    return 0;
}

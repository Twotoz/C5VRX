#pragma once

#include <stdbool.h>
#include <stdint.h>

/**
 * grab.c - composite video frame grabber on the raw I/Q ring (link mode).
 *
 * Step 2 of the T-Embed display link: FM-demodulate the ring in software
 * (per-sample phase difference from a 256-entry angle table), lock onto the
 * horizontal and vertical sync of the received PAL or NTSC signal, and pick
 * GRAB_H lines of GRAB_W luma pixels out of the active picture, normally all
 * within one field.
 */

#define GRAB_W 224
#define GRAB_H 168

typedef struct {
    int      rows;          /* rows captured (GRAB_H when complete) */
    int      fields;        /* fields visited */
    bool     pal;           /* standard detected from the line period */
    float    line_us;       /* tracked line period */
    int      acquire_ms;    /* time spent finding the vertical interval (0 when already locked) */
    int      grab_ms;       /* total time of the call */
    int      late;          /* rows whose samples were overwritten before use */
    int      nosync;        /* rows without a horizontal sync where predicted */
    float    sync_level;    /* tracked levels, phase units per sample (256 = one turn) */
    float    blank_level;
    float    jitter_ns;     /* RMS difference between predicted and measured sync edges */
    int      windows;       /* search windows demodulated while acquiring */
    int      window_us;     /* total time spent demodulating them */
    int      row_us;        /* total time spent in captured rows (wait included) */
    float    power;         /* mean I^2+Q^2 of the samples read (4-bit units, 0..128) */
    float    clip;          /* fraction of those samples with I or Q at full scale */
    int      big_err;       /* rows whose sync edge was more than 0.5 us off the prediction */
    int      max_err;       /* largest prediction error, samples */
    int      wide_hits;     /* rows found only by the fallback search over a whole line */
    int      fifo_ovf;      /* PARLIO RX FIFO overflowed during the grab (samples lost) */
    const char *error;      /* NULL on success */
} grab_info_t;

/* Per-row diagnostics of the most recent rows (ring of GRAB_TRACE_LEN). */
#define GRAB_TRACE_LEN 48
typedef struct {
    int32_t  line;          /* grid line index */
    int16_t  err;           /* measured - predicted sync edge, samples (clamped) */
    uint8_t  row;           /* output row */
    char     result;        /* 'o' ok, 'w' ok via wide search, 'n' no sync, 'l' late, 'r' resync */
} grab_trace_t;

/** Copy the last rows' diagnostics, oldest first; returns the count. */
int grab_trace(grab_trace_t *out, int max);

/** Build the angle table. Call once. */
void grab_init(void);

/** Forget the lock (after a channel change, for example). */
void grab_unlock(void);

/**
 * Grab one frame into img (GRAB_H rows of GRAB_W bytes, 0 = blanking level,
 * 255 = nominal white). Busy-waits on the ring; call from a task that may
 * block the CPU for up to timeout_ms. Returns the number of rows captured;
 * rows not captured are left untouched.
 */
int grab_frame(uint8_t *img, grab_info_t *info, int timeout_ms);

/**
 * grab.c - composite video frame grabber on the raw I/Q ring (link mode).
 *
 * Signal path: the 16 KiB RX ring holds 409.6 us of 40 MS/s Q4/I4 samples.
 * The FM discriminator is the per-sample phase step, taken from a 256-entry
 * angle table (angle of each 4-bit I/Q point, 256 units per turn) as the
 * wrapped difference of consecutive angles: amplitude-independent, one
 * table load and a subtraction per sample. Sync tip is the lowest phase
 * step, white the highest (checked on hardware captures).
 *
 * Timing: every ring byte has an absolute sample index S (ring offset plus
 * 16384 x wraps; the wrap count comes from esp_timer, which runs from the
 * same crystal as the 40 MHz sample clock). Data before the descriptor the
 * GDMA is writing is complete; data older than one ring minus one
 * descriptor is gone.
 *
 * Lock: search windows (two descriptors, 204.8 us) for horizontal sync
 * pulses (line period, PAL/NTSC, levels) and for the vertical interval
 * (equalizing pulses followed by broad pulses). The first broad pulse
 * fixes the field start; the first captured line settles the field parity
 * (line grid on or half a line off the broad pulse). After that a line grid
 * S(N) = S_A + (N - N_A) T is tracked from every captured line's sync edge,
 * and field f starts at grid position F0 + f x lines_per_field.
 *
 * Capture: row y of the output is line floor(F0 + f LPF + first + 0.5 + j)
 * with j = y x active_lines / GRAB_H. Each row waits until its line is in
 * the ring, finds the sync edge near the prediction, measures the sync and
 * blanking levels of that line (tracked with a slow average) and sums the
 * phase steps of 9 samples per pixel. Rows missed in one field (late or no
 * sync found) are taken from the next field.
 */

#include "grab.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>

#include "esp_attr.h"
#include "esp_timer.h"
#include "sdkconfig.h"

#include "video.h"
#include "soc/parl_io_struct.h"

#define RING        VIDEO_RX_RING_BYTES
#define RING_MASK   (RING - 1u)
#define SPS         40                          /* samples per microsecond */

/* Search windows. */
#define BLK         16                          /* samples per block (0.4 us) */
#define WIN_SAMPLES 6144                        /* 153.6 us: two line periods plus margin */
#define WIN_BLOCKS  (WIN_SAMPLES / BLK)
#define MAX_PULSES  48

/* Line layout relative to the sync edge E (samples). */
#define SYNC_FROM   40                          /* 1.0 .. 4.0 us: sync tip */
#define SYNC_LEN    120
#define BLANK_FROM  320                         /* 8.0 us: after the colour burst */
#define BLANK_LEN_PAL  81                       /* .. 10.0 us (back porch ends at 10.4) */
#define BLANK_LEN_NTSC 48                       /* .. 9.2 us (back porch ends at 9.4) */
#define PIX_SAMPLES 9                           /* 224 x 9 = 2016 samples = 50.4 us */
#define PIX_STEP    3                           /* phase read every 3 samples: 75 ns, < half a turn up to 6.6 MHz */
#define LVL_STEP    3                           /* same for the level windows */
#define BLK_STEP    2                           /* 50 ns steps in the search blocks (bright white never wraps) */
#define ACTIVE_PAL  452                         /* 11.3 us: 10.4 us + 0.8 us trim */
#define ACTIVE_NTSC 420                         /* 10.5 us: 9.4 us + 1.1 us trim */

#define NARROW_SEARCH 100                       /* +/- 2.5 us around the predicted edge */
#define MAX_MISSES    12                        /* consecutive failed rows before unlock */

static const int8_t k_sign4[16] = { 0, 1, 2, 3, 4, 5, 6, 7, -8, -7, -6, -5, -4, -3, -2, -1 };

static uint8_t s_ang[256];
static uint8_t s_pow[256];         /* I^2 + Q^2 of a sample byte */
static uint8_t s_clp[256];         /* 1 if I or Q is at full scale */
static uint32_t s_pw_sum, s_pw_n, s_clip_n;   /* power statistics of the current grab */
static const uint8_t *s_ring;
static uint32_t s_max_dscr = 4092u;

/* Sample source of the demod functions: the live ring, or a private copy of
 * a search window. Both are indexed by absolute sample index & mask, so the
 * same code reads either. The acquisition works on the copy because the
 * GDMA overwrites the start of a window while the pulses in it are still
 * being refined (seen on hardware: every edge came out as garbage). */
#define WIN_COPY      8192u
#define WIN_COPY_MASK (WIN_COPY - 1u)
static const uint8_t *s_src;
static uint32_t s_src_mask = RING_MASK;
static uint8_t s_win[WIN_COPY];

/* Timebase: absolute sample index of the ring. */
static int64_t s_tb_t0;
static int64_t s_tb_s0;

/* Sync model. */
static bool    s_locked;
static bool    s_parity_pending;   /* first row after lock: wide search, settles F0 */
static bool    s_resync_pending;   /* first row of a later grab: wide search, corrects T */
static int64_t s_last_grab_us;     /* end of the previous grab */
static bool    s_pal = true;
static double  s_T = 2560.0;       /* line period, samples */
static double  s_SA;               /* sample index of the sync edge of grid line s_NA */
static int32_t s_NA;
static double  s_F0;               /* grid position of the first broad pulse of field 0 */
static double  s_sync = -20.0f;    /* tracked levels, phase units per sample */
static double  s_blank = -5.0f;
static bool    s_levels_ok;
/* Centre of the video swing (sync .. white), phase units per sample. Every
 * phase step is unwrapped around it, so steps of several samples stay
 * unambiguous whatever the carrier offset: the swing itself is ~64 units
 * (10 MHz), the unwrap window 256 units per step. Measured offsets moved
 * between -5.6 and +3 MHz from one tuning to the next. */
static double  s_center;

static int16_t s_blk[WIN_BLOCKS];

static grab_trace_t s_trace[GRAB_TRACE_LEN];
static unsigned s_trace_pos, s_trace_n;

static void trace(int32_t line, double err, int row, char result)
{
    grab_trace_t *t = &s_trace[s_trace_pos];
    t->line = line;
    t->err = (int16_t)(err > 32767.0 ? 32767 : err < -32768.0 ? -32768 : (int)lround(err));
    t->row = (uint8_t)row;
    t->result = result;
    s_trace_pos = (s_trace_pos + 1u) % GRAB_TRACE_LEN;
    if (s_trace_n < GRAB_TRACE_LEN) s_trace_n++;
}

int grab_trace(grab_trace_t *out, int max)
{
    int n = (int)s_trace_n < max ? (int)s_trace_n : max;
    unsigned start = (s_trace_pos + GRAB_TRACE_LEN - (unsigned)n) % GRAB_TRACE_LEN;
    for (int k = 0; k < n; ++k) out[k] = s_trace[(start + (unsigned)k) % GRAB_TRACE_LEN];
    return n;
}

typedef struct {
    int64_t start;   /* first sample below the threshold (block precision) */
    int     len;     /* samples */
} pulse_t;

void grab_init(void)
{
    for (unsigned b = 0u; b < 256u; ++b) {
        int q = k_sign4[b & 0x0fu];
        int i = k_sign4[b >> 4];
        s_pow[b] = (uint8_t)(i * i + q * q);
        s_clp[b] = (uint8_t)((i == 7 || i == -8 || q == 7 || q == -8) ? 1u : 0u);
        if (i == 0 && q == 0) {
            s_ang[b] = 0u;
        } else {
            float a = atan2f((float)q, (float)i) * (128.0f / (float)M_PI);
            s_ang[b] = (uint8_t)((int)lroundf(a) & 0xff);
        }
    }
    s_ring = video_rx_ring();
    s_src = s_ring;
    s_src_mask = RING_MASK;
    s_max_dscr = video_rx_max_descriptor();
}

void grab_unlock(void)
{
    s_locked = false;
    s_parity_pending = false;
    s_resync_pending = false;
}

/* ------------------------------------------------------------------ timebase */

static bool ring_now(int64_t *s_w);

/* (Re-)anchor the timebase at a descriptor switch. The first call defines the
 * sample numbering; later calls keep it (the lock model holds absolute sample
 * indices) and only remove any accumulated timer/sample-clock drift. */
static bool timebase_init(void)
{
    static bool s_tb_ok;
    uint32_t a, b;
    if (!video_rx_write_offset(&a)) return false;
    int64_t deadline = esp_timer_get_time() + 2000;
    do {
        if (!video_rx_write_offset(&b)) return false;
        if (esp_timer_get_time() > deadline) return false;   /* ring stalled */
    } while (b == a);
    int64_t s_w = (int64_t)b;
    if (s_tb_ok && !ring_now(&s_w)) return false;
    s_tb_t0 = esp_timer_get_time();
    s_tb_s0 = s_w;
    s_tb_ok = true;
    return true;
}

/* Absolute sample index of the first byte of the descriptor being written. */
static bool ring_now(int64_t *s_w)
{
    uint32_t off;
    if (!video_rx_write_offset(&off)) return false;
    int64_t est = s_tb_s0 + (esp_timer_get_time() - s_tb_t0) * SPS;
    int64_t k = (est - (int64_t)off + (int64_t)(RING / 2u));
    k = (k >= 0) ? k / RING : -((-k + RING - 1) / RING);
    *s_w = (int64_t)off + k * (int64_t)RING;
    return true;
}

/* Oldest sample still guaranteed in the ring, given the write descriptor start
 * (the GDMA may be anywhere inside that descriptor). Used before reading. */
static inline int64_t oldest_valid(int64_t s_w)
{
    return s_w + (int64_t)s_max_dscr - (int64_t)RING + 64;
}

/* After reading oldest to newest at less than the sample rate, the newest
 * sample is the one closest to being overwritten: it is intact if the GDMA
 * cannot have reached its slot yet. No margin: s_w is descriptor-granular,
 * so a margin would reject every window that took three descriptor times. */
static inline bool newest_intact(int64_t S_newest, int64_t s_w_now)
{
    return S_newest + (int64_t)RING > s_w_now + (int64_t)s_max_dscr;
}

/* ----------------------------------------------------------------- demod */

static inline uint8_t ang_at(uint32_t i)
{
    return s_ang[s_src[i & s_src_mask]];
}

/* Phase advance over samples [S, S + n): the sum of the per-sample phase
 * steps. It telescopes, so the phase is read only every `step` samples;
 * each step must stay below half a turn (n must be a multiple of step). */
static inline int bias_for(int step)
{
    return (int)lround(s_center * step);
}

static IRAM_ATTR int32_t sum_dphi_step(int64_t S, int n, int step)
{
    const int bias = bias_for(step);
    uint32_t i = ((uint32_t)S - 1u) & s_src_mask;
    uint8_t prev = s_ang[s_src[i]];
    int32_t acc = 0;
    for (int k = n / step; k > 0; --k) {
        i = (i + (uint32_t)step) & s_src_mask;
        uint8_t a = s_ang[s_src[i]];
        acc += (int8_t)(uint8_t)(a - prev - bias) + bias;
        prev = a;
    }
    return acc;
}

static IRAM_ATTR void block_demod(int64_t S0, int nblk, int16_t *out)
{
    const uint8_t *ring = s_src;
    const uint8_t *ang = s_ang;
    const int bias = bias_for(BLK_STEP);
    uint32_t i = ((uint32_t)S0 - 1u) & s_src_mask;
    uint8_t prev = ang[ring[i]];
    for (int b = 0; b < nblk; ++b) {
        int32_t acc = 0;
        for (int k = 0; k < BLK / BLK_STEP; ++k) {
            i = (i + BLK_STEP) & s_src_mask;
            uint8_t a = ang[ring[i]];
            acc += (int8_t)(uint8_t)(a - prev - bias) + bias;
            prev = a;
        }
        out[b] = (int16_t)acc;
    }
}

/* First falling crossing of the 8-sample running sum below thr8 in
 * [S_from, S_to), after at least 24 samples above it. Sub-sample edge. */
static IRAM_ATTR bool find_edge(int64_t S_from, int64_t S_to, int thr8, double *edge)
{
    const int bias = bias_for(1);
    uint32_t i = ((uint32_t)S_from - 8u) & s_src_mask;
    uint8_t prev = ang_at(i - 1u);
    int win[8];
    int sum = 0;
    for (int k = 0; k < 8; ++k) {
        uint8_t a = s_ang[s_src[i]];
        win[k] = (int8_t)(uint8_t)(a - prev - bias) + bias;
        sum += win[k];
        prev = a;
        i = (i + 1u) & s_src_mask;
    }
    int above = 0, prev_sum = sum, k = 0;
    for (int64_t s = S_from; s < S_to; ++s) {
        uint8_t a = s_ang[s_src[i]];
        int d = (int8_t)(uint8_t)(a - prev - bias) + bias;
        prev = a;
        i = (i + 1u) & s_src_mask;
        sum += d - win[k];
        win[k] = d;
        k = (k + 1) & 7;
        if (sum >= thr8) {
            above++;
        } else {
            if (above >= 24) {
                /* A sync pulse stays at sync level for 4.7 us; an FM click
                 * (a noise-driven phase slip of a whole turn) dips below the
                 * threshold for ~0.1 us only. Accept the edge if the mean
                 * over 0.4 .. 2.8 us after it is below the threshold. */
                int32_t tail = sum_dphi_step(s + 16, 96, 3);
                if (tail * 8 < thr8 * 96) {
                    double fr = (double)(prev_sum - thr8) / (double)(prev_sum - sum);
                    *edge = (double)(s - 1) + fr - 3.5;
                    return true;
                }
            }
            above = 0;
        }
        prev_sum = sum;
    }
    return false;
}

static inline int thr8_now(void)
{
    return (int)lround(8.0 * (s_sync + s_blank) * 0.5);
}

/* Sync and blanking level of the line whose sync edge is at E. */
static void line_levels(double E, double *sy, double *bl)
{
    int64_t e = (int64_t)llround(E);
    int blen = s_pal ? BLANK_LEN_PAL : BLANK_LEN_NTSC;
    *sy = (double)sum_dphi_step(e + SYNC_FROM, SYNC_LEN, LVL_STEP) / (double)SYNC_LEN;
    *bl = (double)sum_dphi_step(e + BLANK_FROM, blen, LVL_STEP) / (double)blen;
}

static void track_levels(double sy, double bl)
{
    if (bl - sy < 2.0) return;                 /* implausible line */
    if (!s_levels_ok) {
        s_sync = sy;
        s_blank = bl;
        s_levels_ok = true;
    } else {
        s_sync += 0.2 * (sy - s_sync);
        s_blank += 0.2 * (bl - s_blank);
    }
    double ratio = s_pal ? (7.0 / 3.0) : 2.5;
    double white = s_blank + ratio * (s_blank - s_sync);
    s_center = 0.5 * (s_sync + white);
}

/* ------------------------------------------------------------- acquisition */

static int cmp_i16(const void *a, const void *b)
{
    return (int)*(const int16_t *)a - (int)*(const int16_t *)b;
}

/* Runs of blocks below thr (single-block gaps bridged). */
static int find_pulses(const int16_t *blk, int nblk, int thr, int64_t S0, pulse_t *p, int maxp)
{
    int n = 0;
    for (int b = 0; b < nblk && n < maxp; ) {
        if (blk[b] >= thr) { ++b; continue; }
        int s = b;
        while (b < nblk && (blk[b] < thr || (b + 1 < nblk && blk[b + 1] < thr))) ++b;
        if (s > 0 && b < nblk) {                /* drop runs cut by the window edges */
            p[n].start = S0 + (int64_t)s * BLK;
            p[n].len = (b - s) * BLK;
            ++n;
        }
    }
    return n;
}

static inline bool is_broad(const pulse_t *p) { return p->len >= 15 * SPS; }
static inline bool is_short(const pulse_t *p) { return p->len >= 1 * SPS && p->len < 7 * SPS / 2; }
static inline bool is_hsync(const pulse_t *p) { return p->len >= 7 * SPS / 2 && p->len <= 7 * SPS; }

/* Refine a pulse start to a sub-sample edge. */
static bool refine(const pulse_t *p, double *edge)
{
    return find_edge(p->start - 3 * BLK, p->start + 2 * BLK, thr8_now(), edge);
}

static bool acquire(int timeout_ms, grab_info_t *info)
{
    int64_t t_end = esp_timer_get_time() + (int64_t)timeout_ms * 1000;
    int64_t last_w = -1;
    double sum_T = 0.0;
    int n_T = 0;
    static pulse_t pulses[MAX_PULSES];
    static int16_t sorted[WIN_BLOCKS];

    s_levels_ok = false;
    while (esp_timer_get_time() < t_end) {
        int64_t s_w;
        if (!ring_now(&s_w)) { info->error = "ring pointer lost"; return false; }
        if (s_w == last_w) continue;             /* wait for a fresh descriptor */
        last_w = s_w;
        int64_t S0 = s_w - WIN_SAMPLES;
        int64_t tw = esp_timer_get_time();
        /* Private copy of [S0 - 64, S0 + WIN_SAMPLES), same index mapping. */
        for (int64_t S = S0 - 64; S < S0 + WIN_SAMPLES; ) {
            uint32_t r = (uint32_t)S & RING_MASK, w = (uint32_t)S & WIN_COPY_MASK;
            uint32_t n = (uint32_t)(S0 + WIN_SAMPLES - S);
            if (n > RING - r) n = RING - r;
            if (n > WIN_COPY - w) n = WIN_COPY - w;
            memcpy(s_win + w, s_ring + r, n);
            S += n;
        }
        int64_t s_w2;
        if (!ring_now(&s_w2) || !newest_intact(S0 + WIN_SAMPLES - 1, s_w2)) { info->late++; continue; }
        s_src = s_win;
        s_src_mask = WIN_COPY_MASK;
        for (int64_t S = S0; S < S0 + WIN_SAMPLES; S += 16) {
            uint8_t b = s_win[(uint32_t)S & WIN_COPY_MASK];
            s_pw_sum += s_pow[b];
            s_clip_n += s_clp[b];
            s_pw_n++;
        }
        block_demod(S0, WIN_BLOCKS, s_blk);
        info->windows++;
        info->window_us += (int)(esp_timer_get_time() - tw);

        /* Block threshold: tracked levels, or percentiles of this window. */
        int thr;
        if (s_levels_ok) {
            thr = (int)lround(BLK * (s_sync + s_blank) * 0.5);
        } else {
            memcpy(sorted, s_blk, sizeof(sorted));
            qsort(sorted, WIN_BLOCKS, sizeof(sorted[0]), cmp_i16);
            /* Re-centre the unwrap on this window first (unknown carrier
             * offset); the next window is demodulated around it. */
            double med = (double)sorted[WIN_BLOCKS / 2] / BLK;
            if (fabs(med - s_center) > 6.0) {
                s_center = med;
                continue;
            }
            int32_t low = 0;
            int nlow = WIN_BLOCKS / 20;
            for (int k = 0; k < nlow; ++k) low += sorted[k];
            int sync_blk = low / nlow;
            int hi = sorted[WIN_BLOCKS * 98 / 100];
            if (hi - sync_blk < 4 * BLK) continue;   /* flat: no carrier or no video */
            thr = sync_blk + (hi - sync_blk) / 5;
            s_sync = (double)sync_blk / BLK;
            s_blank = (double)(2 * thr - sync_blk) / BLK;   /* provisional, midpoint = thr */
        }

        int np = find_pulses(s_blk, WIN_BLOCKS, thr, S0, pulses, MAX_PULSES);
#ifdef GRAB_TRACE
        printf("win S0=%lld thr=%d center=%.1f lv=%d sync=%.1f blank=%.1f np=%d:", (long long)S0, thr, s_center,
               (int)s_levels_ok, s_sync, s_blank, np);
        for (int k = 0; k < np && k < 8; ++k)
            printf(" [%lld %d]", (long long)(pulses[k].start - S0), pulses[k].len);
        printf(" nT=%d\n", n_T);
#endif

        /* Horizontal sync: line period and levels from consecutive pulses
         * whose level windows lie inside the copy. */
        double prev_edge = -1.0;
        for (int k = 0; k < np; ++k) {
            if (!is_hsync(&pulses[k]) || pulses[k].start + 440 > S0 + WIN_SAMPLES) { prev_edge = -1.0; continue; }
            double e;
            if (!refine(&pulses[k], &e)) {
#ifdef GRAB_TRACE
                printf("  refine failed at %lld thr8=%d\n", (long long)(pulses[k].start - S0), thr8_now());
#endif
                prev_edge = -1.0;
                continue;
            }
            double sy, bl;
            line_levels(e, &sy, &bl);
#ifdef GRAB_TRACE
            printf("  hsync edge %.1f sy=%.2f bl=%.2f\n", e - (double)S0, sy, bl);
#endif
            track_levels(sy, bl);
            if (prev_edge > 0.0) {
                double sp = e - prev_edge;
                if (sp > 2480.0 && sp < 2620.0) {
                    sum_T += sp;
                    n_T++;
                }
            }
            prev_edge = e;
        }
        if (n_T < 4) continue;
        double T = sum_T / n_T;
        s_pal = T > 2551.0;
        s_T = s_pal ? 2560.0 : 2542.4;           /* nominal; tracked from here on */

        /* Vertical interval: short pulse then the first broad pulse half a
         * line later, or the last broad pulse followed by a short one. */
        double half = s_T * 0.5, tol = 3.0 * SPS;
        int fb = -1, lb = -1;
        for (int k = 0; k < np; ++k) {
            if (is_broad(&pulses[k])) {
                if (fb < 0) fb = k;
                lb = k;
            }
        }
        if (fb < 0) continue;
        double sv = -1.0;
        if (fb >= 1 && is_short(&pulses[fb - 1]) &&
            fabs((double)(pulses[fb].start - pulses[fb - 1].start) - half) < tol) {
            double e;
            if (refine(&pulses[fb], &e)) sv = e;
        } else if (lb + 1 < np && is_short(&pulses[lb + 1]) &&
                   fabs((double)(pulses[lb + 1].start - pulses[lb].start) - half) < tol) {
            double e;
            int n_broad = s_pal ? 5 : 6;
            if (refine(&pulses[lb], &e)) sv = e - (double)(n_broad - 1) * half;
        }
        if (sv < 0.0) continue;

        /* Tentative grid: line 0 starts at the broad pulse (even field). The
         * first captured row settles the parity. */
        s_SA = sv;
        s_NA = 0;
        s_F0 = 0.0;
        s_locked = true;
        s_parity_pending = true;
        s_src = s_ring;
        s_src_mask = RING_MASK;
        return true;
    }
    s_src = s_ring;
    s_src_mask = RING_MASK;
    info->error = n_T < 4 ? "no horizontal sync (no carrier or no video?)" : "no vertical interval found";
    return false;
}

/* ------------------------------------------------------------------ capture */

typedef struct {
    double err_sq;
    int    err_n;
} jitter_t;

/* GRAB_W pixels of PIX_SAMPLES samples from S_act: phase advance per pixel,
 * 0 = blanking, 255 = nominal white. */
static IRAM_ATTR void extract_pixels(int64_t S_act, uint8_t *dst, int32_t bl_q8, int64_t mul)
{
    const uint8_t *ring = s_src;
    const uint8_t *ang = s_ang;
    const int bias = bias_for(PIX_STEP);
    uint32_t i = ((uint32_t)S_act - 1u) & s_src_mask;
    uint8_t prev = ang[ring[i]];
    uint32_t pw = 0u, clipped = 0u;
    for (int px = 0; px < GRAB_W; ++px) {
        int32_t acc = 0;
        for (int k = 0; k < PIX_SAMPLES / PIX_STEP; ++k) {
            i = (i + PIX_STEP) & s_src_mask;
            uint8_t b = ring[i];
            uint8_t a = ang[b];
            pw += s_pow[b];
            clipped += s_clp[b];
            acc += (int8_t)(uint8_t)(a - prev - bias) + bias;
            prev = a;
        }
        int32_t num = acc * 256 - PIX_SAMPLES * bl_q8;
        int32_t v = (int32_t)(((int64_t)num * mul) >> 16);
        dst[px] = (uint8_t)(v < 0 ? 0 : v > 255 ? 255 : v);
    }
    s_pw_sum += pw;
    s_clip_n += clipped;
    s_pw_n += (uint32_t)(GRAB_W * (PIX_SAMPLES / PIX_STEP));
}

static bool capture_row(int32_t N, int row, uint8_t *dst, grab_info_t *info, jitter_t *jit)
{
    s_src = s_ring;
    s_src_mask = RING_MASK;
    double S_pred = s_SA + (double)(N - s_NA) * s_T;
    int half = (s_parity_pending || s_resync_pending) ? (int)(s_T * 0.5) + 64 : NARROW_SEARCH;
    int64_t S_from = (int64_t)floor(S_pred) - half;
    int64_t S_to = (int64_t)floor(S_pred) + half;
    int64_t S_need = (int64_t)floor(S_pred) + half + (int64_t)s_T + 64;

    int64_t s_w;
    int64_t deadline = esp_timer_get_time() + 30000;
    for (;;) {
        if (!ring_now(&s_w)) return false;
        if (s_w >= S_need) break;
        if (esp_timer_get_time() > deadline) return false;
    }
    if (S_from - 16 < oldest_valid(s_w)) { info->late++; trace(N, 0.0, row, 'l'); return false; }

    double E;
    bool wide = false;
    if (!find_edge(S_from, S_to, thr8_now(), &E)) {
        /* Not within +/-2.5 us: look over a whole line before giving up.
         * The line must still be intact in the ring for the wider window. */
        int64_t W_from = (int64_t)floor(S_pred) - (int64_t)(s_T * 0.5);
        int64_t W_to = (int64_t)floor(S_pred) + (int64_t)(s_T * 0.5);
        if (half >= (int)(s_T * 0.5) || W_from - 16 < oldest_valid(s_w) ||
            !find_edge(W_from, W_to, thr8_now(), &E)) {
            info->nosync++;
            trace(N, 0.0, row, 'n');
            return false;
        }
        wide = true;
        info->wide_hits++;
    }
    {
        double e_abs = fabs(E - S_pred);
        if (!s_parity_pending && !s_resync_pending) {
            if (e_abs > 20.0) info->big_err++;
            if ((int)e_abs > info->max_err) info->max_err = (int)e_abs;
        }
        trace(N, E - S_pred, row, (s_parity_pending || s_resync_pending) ? 'r' : wide ? 'w' : 'o');
    }

    if (s_parity_pending) {
        /* Label this line N; the field start sits on the grid or half a
         * line off it. */
        double f0 = (double)N - (E - s_SA) / s_T;
        s_F0 = floor(f0 * 2.0 + 0.5) * 0.5;
        s_parity_pending = false;
    } else if (s_resync_pending) {
        /* Thousands of lines since the last grab: the error is the line
         * period error times that count, a precise correction. */
        double e = E - S_pred;
        if (N - s_NA > 0) s_T += e / (double)(N - s_NA);
        s_resync_pending = false;
    } else if (!wide) {
        double e = E - S_pred;
        jit->err_sq += e * e;
        jit->err_n++;
        if (N - s_NA >= 4) s_T += 0.3 * e / (double)(N - s_NA);
    }
    s_SA = E;
    s_NA = N;

    double sy, bl;
    line_levels(E, &sy, &bl);
    track_levels(sy, bl);

    /* Pixels: 9 phase steps each; 0 = blanking, 255 = nominal white. */
    double ratio = s_pal ? (7.0 / 3.0) : 2.5;
    int32_t bl_q8 = (int32_t)lround(s_blank * 256.0);
    int32_t white_q8 = (int32_t)lround((s_blank - s_sync) * ratio * 256.0);
    if (white_q8 < 256) return false;
    int64_t mul = ((int64_t)255 << 16) / ((int64_t)PIX_SAMPLES * white_q8);
    int64_t S_act = (int64_t)llround(E) + (s_pal ? ACTIVE_PAL : ACTIVE_NTSC);
    extract_pixels(S_act, dst, bl_q8, mul);

    /* The samples must not have been overwritten while we used them: the
     * oldest was checked before reading, the newest is checked now. */
    if (!ring_now(&s_w) || !newest_intact((int64_t)llround(E) + 2560, s_w)) { info->late++; return false; }
    return true;
}

int grab_frame(uint8_t *img, grab_info_t *info, int timeout_ms)
{
    static uint8_t done[GRAB_H];
    memset(info, 0, sizeof(*info));
    memset(done, 0, sizeof(done));
    s_pw_sum = s_pw_n = s_clip_n = 0u;
    PARL_IO.int_clr.rx_fifo_wovf_int_clr = 1;
    int64_t t0 = esp_timer_get_time();
    int64_t t_end = t0 + (int64_t)timeout_ms * 1000;

    if (!timebase_init()) { info->error = "RX ring not moving"; return 0; }
    /* Between grabs the line grid is only extrapolated. Up to ~1.5 s the
     * error stays below half a line and one wide search re-anchors it;
     * after longer gaps look for the vertical interval again. */
    if (s_locked) {
        if (t0 - s_last_grab_us > 1500000) grab_unlock();
        else s_resync_pending = true;
    }
    if (!s_locked) {
        if (!acquire(timeout_ms, info)) {
            info->grab_ms = (int)((esp_timer_get_time() - t0) / 1000);
            info->power = s_pw_n ? (float)s_pw_sum / (float)s_pw_n : 0.0f;
            info->clip = s_pw_n ? (float)s_clip_n / (float)s_pw_n : 0.0f;
            return 0;
        }
        info->acquire_ms = (int)((esp_timer_get_time() - t0) / 1000);
    }

    const double lpf = s_pal ? 312.5 : 262.5;
    const double first = s_pal ? 22.0 : 17.0;       /* active lines after the first broad pulse */
    const int active = s_pal ? 288 : 240;           /* active lines per field */
    const int picture = 2 * active;                 /* interlaced picture lines */
    jitter_t jit = { 0.0, 0 };
    int misses = 0;
    int64_t f = INT64_MIN;

    /* Interlaced: output row y shows picture line p = y * picture / GRAB_H.
     * Even p lie in the field whose vertical interval starts on a line
     * boundary, odd p in the other one, so each field only has to deliver
     * about half the rows (twice the CPU time per line), and a complete
     * frame takes two consecutive fields. */
    while (info->rows < GRAB_H && esp_timer_get_time() < t_end && s_locked) {
        int64_t s_w;
        if (!ring_now(&s_w)) break;
        double n_now = (double)s_NA + ((double)s_w - s_SA) / s_T;
        int64_t f_next = (int64_t)ceil((n_now + 3.0 - s_F0 - first - 0.5) / lpf);
        if (f < f_next) f = f_next;
        double start = s_F0 + (double)f * lpf;
        int parity = (fabs(start - floor(start + 0.5)) < 0.25) ? 0 : 1;
        info->fields++;

        for (int y = 0; y < GRAB_H && s_locked; ++y) {
            if (done[y]) continue;
            int p = y * picture / GRAB_H;
            if ((p & 1) != parity) continue;
            int32_t N = (int32_t)floor(start + first + 0.5 + (double)(p >> 1));
            int nosync_before = info->nosync;
            int64_t tr = esp_timer_get_time();
            if (capture_row(N, y, img + (size_t)y * GRAB_W, info, &jit)) {
                done[y] = 1;
                info->rows++;
                info->row_us += (int)(esp_timer_get_time() - tr);
                misses = 0;
            } else if (info->nosync != nosync_before && ++misses >= MAX_MISSES) {
                /* Only missing syncs mean the model is wrong; late rows just
                 * mean the CPU fell behind and are retried in a later field. */
                s_locked = false;
                info->error = "lost sync";
            }
            if (esp_timer_get_time() > t_end) break;
        }
        f++;
    }

    info->pal = s_pal;
    info->line_us = (float)(s_T / SPS);
    info->sync_level = (float)s_sync;
    info->blank_level = (float)s_blank;
    info->jitter_ns = jit.err_n ? (float)(sqrt(jit.err_sq / jit.err_n) * 25.0) : 0.0f;
    info->grab_ms = (int)((esp_timer_get_time() - t0) / 1000);
    info->power = s_pw_n ? (float)s_pw_sum / (float)s_pw_n : 0.0f;
    info->clip = s_pw_n ? (float)s_clip_n / (float)s_pw_n : 0.0f;
    info->fifo_ovf = PARL_IO.int_raw.rx_fifo_wovf_int_raw ? 1 : 0;
    s_last_grab_us = esp_timer_get_time();
    if (info->rows < GRAB_H && !info->error) info->error = "timeout";
    return info->rows;
}

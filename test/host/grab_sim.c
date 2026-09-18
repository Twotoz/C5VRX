/**
 * grab_sim.c - host simulation of main/grab.c against a synthetic FPV signal.
 *
 * Generates PAL (625/50, interlaced, full vertical interval with equalizing
 * and broad pulses) or NTSC (525/60) composite video of a test pattern,
 * FM-modulates it like an analog VTX, adds noise, quantizes to the
 * receiver's Q4/I4 bytes and streams it into a 16 KiB ring with the same
 * descriptor layout as the PARLIO RX GDMA (4 x 4092 + 16 bytes). Simulated
 * time advances with every esp_timer_get_time() call, so grab.c's busy-wait
 * loops drive the "DMA". Processing itself costs no simulated time.
 *
 * build: gcc -O2 -Itest/host/stubs -Imain test/host/grab_sim.c main/grab.c -lm -o grab_sim
 * run:   grab_sim [pal|ntsc] [frames] [noise_sigma] [cfo_mhz] [out_prefix] [slowdown]
 */

#include <math.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <windows.h>

#include "grab.h"
#include "video.h"

#define RING VIDEO_RX_RING_BYTES
#define FS   40e6

static uint8_t g_ring[RING];
static int64_t g_written;            /* samples written so far (absolute) */
static double  g_time_us = 1000.0;
static double  g_phase;
static bool    g_pal = true;
static double  g_noise = 0.6;        /* per axis, 4-bit LSB */
static double  g_cfo = 0.3e6;        /* carrier offset, Hz */
static double  g_radius = 5.0;
static double  g_hz_per_volt = 7.8e6;
static double  g_line_us;
static int     g_lines;
static int64_t g_start_offset = 123457;   /* stream starts mid-frame */
static uint8_t *g_replay;                 /* replay a raw hardware capture instead */
static long    g_replay_len;

/* ------------------------------------------------------------ stub API */

int64_t esp_timer_get_time(void);
const uint8_t *video_rx_ring(void) { return g_ring; }
uint32_t video_rx_max_descriptor(void) { return 4092u; }

bool video_rx_write_offset(uint32_t *offset)
{
    uint32_t pos = (uint32_t)(g_written % RING);
    static const uint32_t starts[5] = { 0u, 4092u, 8184u, 12276u, 16368u };
    uint32_t s = 0u;
    for (int i = 0; i < 5; ++i)
        if (pos >= starts[i]) s = starts[i];
    *offset = s;
    return true;
}

/* ------------------------------------------------------------ test signal */

static double pattern(double x, double y)       /* 0..1 luma, x, y in [0,1) */
{
    double cx = x - 0.5, cy = (y - 0.5) * 0.75;
    double r = sqrt(cx * cx + cy * cy);
    if (fabs(r - 0.28) < 0.012) return 1.0;                 /* white ring */
    if (fabs((x - y)) < 0.01) return 0.0;                   /* black diagonal */
    if (y < 0.33) return floor(x * 8.0) / 7.0;              /* 8 grey bars */
    if (y < 0.66) return x;                                 /* horizontal ramp */
    return ((int)(x * 16) + (int)(y * 12)) & 1 ? 0.85 : 0.15;   /* checkerboard */
}

/* PAL vertical interval: pulse type of half line h (0 = first half) of
 * frame line L (1-based): 'n' normal, 'e' equalizing, 'b' broad, 'x' none. */
static char pal_half(int L, int h)
{
    if (L == 623) return h ? 'e' : 'n';
    if (L == 624 || L == 625) return 'e';
    if (L == 1 || L == 2) return 'b';
    if (L == 3) return h ? 'e' : 'b';
    if (L == 4 || L == 5) return 'e';
    if (L == 311 || L == 312) return 'e';
    if (L == 313) return h ? 'b' : 'e';
    if (L == 314 || L == 315) return 'b';
    if (L == 316 || L == 317) return 'e';
    if (L == 318) return h ? 'x' : 'e';
    return 'n';
}

/* NTSC: lines 1-3 eq, 4-6 broad, 7-9 eq (field 1); 263.5.. for field 2. */
static char ntsc_half(int L, int h)
{
    int hl = (L - 1) * 2 + h;                    /* half-line index 0..1049 */
    if (hl < 6) return 'e';
    if (hl < 12) return 'b';
    if (hl < 18) return 'e';
    int h2 = hl - 525;                           /* field 2 starts at line 263.5 */
    if (h2 >= 0 && h2 < 6) return 'e';
    if (h2 >= 6 && h2 < 12) return 'b';
    if (h2 >= 12 && h2 < 18) return 'e';
    return 'n';
}

static double composite(int64_t s)
{
    double t_us = (double)(s + g_start_offset) / 40.0;
    double frame_us = g_line_us * g_lines;
    double tf = fmod(t_us, frame_us);
    int L = (int)(tf / g_line_us) + 1;
    double tl = tf - (L - 1) * g_line_us;
    int h = tl >= g_line_us * 0.5 ? 1 : 0;
    double th = tl - h * g_line_us * 0.5;
    char k = g_pal ? pal_half(L, h) : ntsc_half(L, h);
    const double SYNC = -0.3, BLANK = 0.0;
    switch (k) {
    case 'e': return th < 2.35 ? SYNC : BLANK;
    case 'b': return th < (g_line_us * 0.5 - 4.7) ? SYNC : BLANK;
    case 'x': return BLANK;
    default: break;
    }
    if (h == 1 && (k == 'e' || k == 'b')) return BLANK;
    /* normal line */
    if (tl < 4.7) return SYNC;
    double a0 = g_pal ? 10.5 : 9.4, alen = g_pal ? 52.0 : 52.6;
    int first1 = g_pal ? 23 : 21, last1 = g_pal ? 310 : 262;
    int first2 = g_pal ? 336 : 284, last2 = g_pal ? 622 : 524;
    int row = -1, rows = g_pal ? 576 : 484;
    if (L >= first1 && L <= last1) row = 2 * (L - first1);
    else if (L >= first2 && L <= last2) row = 2 * (L - first2) + 1;
    if (row < 0 || tl < a0 || tl >= a0 + alen) return BLANK;
    return 0.7 * pattern((tl - a0) / alen, (double)row / rows);
}

static double gauss(void)
{
    double u1 = (rand() + 1.0) / (RAND_MAX + 2.0), u2 = (rand() + 1.0) / (RAND_MAX + 2.0);
    return sqrt(-2.0 * log(u1)) * cos(2.0 * M_PI * u2);
}

static int q4(double v)
{
    int n = (int)lround(v);
    return n < -8 ? -8 : n > 7 ? 7 : n;
}

static void advance_to(int64_t S)
{
    if (g_replay) {
        while (g_written < S) {
            g_ring[g_written % RING] = g_replay[g_written % g_replay_len];
            g_written++;
        }
        return;
    }
    while (g_written < S) {
        double f = g_cfo + g_hz_per_volt * composite(g_written);
        g_phase += 2.0 * M_PI * f / FS;
        if (g_phase > M_PI) g_phase -= 2.0 * M_PI;
        if (g_phase < -M_PI) g_phase += 2.0 * M_PI;
        int i = q4(g_radius * cos(g_phase) + g_noise * gauss());
        int q = q4(g_radius * sin(g_phase) + g_noise * gauss());
        g_ring[g_written % RING] = (uint8_t)(((i & 15) << 4) | (q & 15));
        g_written++;
    }
}

/* Simulated time: 0.3 us per call, plus the host CPU time spent in grab.c
 * since the previous call scaled by g_slowdown (the ESP32-C5 at 240 MHz is
 * roughly 30x slower than a desktop core on these loops). Signal generation
 * inside this function is not charged. */
static double g_slowdown = 30.0;
static LARGE_INTEGER g_qpf, g_exit;

int64_t esp_timer_get_time(void)
{
    LARGE_INTEGER now;
    QueryPerformanceCounter(&now);
    if (g_exit.QuadPart)
        g_time_us += (double)(now.QuadPart - g_exit.QuadPart) * 1e6 / (double)g_qpf.QuadPart * g_slowdown;
    g_time_us += 0.3;
    advance_to((int64_t)(g_time_us * 40.0));
    QueryPerformanceCounter(&g_exit);
    return (int64_t)g_time_us;
}

/* ------------------------------------------------------------ main */

int main(int argc, char **argv)
{
    int frames = 3;
    const char *prefix = "grab_sim";
    if (argc > 1) g_pal = strcmp(argv[1], "ntsc") != 0;
    if (argc > 2 && strcmp(argv[1], "replay") == 0) {
        FILE *rf = fopen(argv[2], "rb");
        if (!rf) { perror(argv[2]); return 2; }
        fseek(rf, 0, SEEK_END);
        g_replay_len = ftell(rf);
        fseek(rf, 0, SEEK_SET);
        g_replay = malloc((size_t)g_replay_len);
        if (fread(g_replay, 1, (size_t)g_replay_len, rf) != (size_t)g_replay_len) return 2;
        fclose(rf);
        printf("replaying %ld samples in a loop\n", g_replay_len);
        argv[2] = "2";
    }
    if (argc > 2) frames = atoi(argv[2]);
    if (argc > 3) g_noise = atof(argv[3]);
    if (argc > 4) g_cfo = atof(argv[4]) * 1e6;
    if (argc > 5) prefix = argv[5];
    if (argc > 6) g_slowdown = atof(argv[6]);
    QueryPerformanceFrequency(&g_qpf);
    g_line_us = g_pal ? 64.0 : 63.5556;
    g_lines = g_pal ? 625 : 525;
    srand(1);
    advance_to((int64_t)(g_time_us * 40.0));

    grab_init();
    static uint8_t img[GRAB_H][GRAB_W];
    int ok = 0;
    for (int n = 0; n < frames; ++n) {
        memset(img, 0, sizeof(img));
        grab_info_t info;
        int rows = grab_frame(&img[0][0], &info, 400);
        printf("frame %d: rows=%d std=%s line_us=%.4f fields=%d acquire_ms=%d grab_ms=%d late=%d nosync=%d "
               "sync=%.2f blank=%.2f jitter_ns=%.0f error=%s\n",
               n, rows, info.pal ? "PAL" : "NTSC", info.line_us, info.fields, info.acquire_ms, info.grab_ms,
               info.late, info.nosync, info.sync_level, info.blank_level, info.jitter_ns,
               info.error ? info.error : "none");
        char name[256];
        snprintf(name, sizeof(name), "%s_%d.pgm", prefix, n);
        FILE *fp = fopen(name, "wb");
        if (fp) {
            fprintf(fp, "P5\n%d %d\n255\n", GRAB_W, GRAB_H);
            fwrite(img, 1, sizeof(img), fp);
            fclose(fp);
        }
        if (rows == GRAB_H) ok++;
        else {
            grab_trace_t tr[GRAB_TRACE_LEN];
            int nt = grab_trace(tr, GRAB_TRACE_LEN);
            printf("  trace:");
            for (int k = 0; k < nt; ++k) printf(" r%d/L%ld:%d%c", tr[k].row, (long)tr[k].line, tr[k].err, tr[k].result);
            printf("\n");
            for (int y = 0; y < GRAB_H; ++y) {
                int blank = 1;
                for (int x = 0; x < GRAB_W; ++x) if (img[y][x]) { blank = 0; break; }
                if (blank) printf("  row %d never captured\n", y);
            }
        }
        g_time_us += 7000.0;              /* idle between grabs */
        advance_to((int64_t)(g_time_us * 40.0));
    }
    return ok == frames ? 0 : 1;
}

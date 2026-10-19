/* C5VRX-5 by Twotoz and the C5VRX contributors: host test of the end-to-end
 * video quality (main/video_quality.h, afc2_snr_x10) on board captures.
 *
 *   cc -O2 -std=c11 -I../main test_video_quality.c -lm -o t
 *   ./t ../research/data/afc_2026-10-18 ../research/data/edge_dc_2026-10-19
 */
#include <dirent.h>
#include <stdio.h>
#include <string.h>
#include "video_quality.h"
#include "phase8_gain_lut.h"

static unsigned s_fail, s_run;
static void check(const char *name, int ok)
{
    ++s_run;
    printf("%-4s %s\n", ok ? "ok" : "FAIL", name);
    s_fail += !ok;
}

/* Every 4096-sample window of every capture in dir (name filter) into one period. */
static vq_t over(const char *dir, const char *contains)
{
    vq_acc_t a = {.start_us = 1};
    DIR *d = opendir(dir);
    if (!d) return (vq_t){0};
    struct dirent *e;
    while ((e = readdir(d))) {
        const size_t len = strlen(e->d_name);
        if (len < 4 || strcmp(e->d_name + len - 4, ".bin") || !strstr(e->d_name, contains)) continue;
        char path[1024];
        snprintf(path, sizeof(path), "%s/%s", dir, e->d_name);
        FILE *f = fopen(path, "rb");
        if (!f) continue;
        static uint8_t b[8192];
        const size_t n = fread(b, 1, sizeof(b), f);
        fclose(f);
        for (size_t o = 0; o + 4096u <= n; o += 2048u) {
            const afc2_result_t r = afc2_measure(b + o, 4096, c5vrx_phase8_gain_lut);
            vq_add(&a, &r);
        }
    }
    closedir(d);
    vq_t v = {0};
    (void)vq_close(&a, 1 + VQ_PERIOD_US, &v);
    return v;
}

int main(int argc, char **argv)
{
    vq_acc_t a = {0};
    vq_t v;
    check("first call only starts the period", !vq_close(&a, 5, &v) && a.start_us == 5);
    check("no close before the period", !vq_close(&a, 5 + VQ_PERIOD_US - 1, &v));
    check("empty period: unknown", vq_close(&a, 5 + VQ_PERIOD_US, &v) && !v.known && v.snr_x10 == 0);
    check("flat spread is clamped, not a division by zero",
          afc2_snr_x10(-3200, 3200 * 100, 32, 0, 0, 32) == 600);
    check("too few pairs: no SNR", afc2_snr_x10(100, 1000, 10, 0, 0, 10) == 0);
    if (argc == 3) {
        const vq_t video = over(argv[1], "s_");
        printf("     afc captures: windows %lu, lines %lu, lines_x100 %u, snr %.1f dB\n",
               (unsigned long)video.windows, (unsigned long)video.lines, video.lines_x100, video.snr_x10 / 10.);
        check("carrier captures: lines found, SNR measured", video.known && video.lines_x100 >= 30 &&
              video.snr_x10 >= 20 && video.snr_x10 <= 150);
        const vq_t lost = over(argv[2], "capfar2_");
        printf("     edge DC captures: windows %lu, lines %lu, lines_x100 %u\n", (unsigned long)lost.windows,
               (unsigned long)lost.lines, lost.lines_x100);
        check("picture lost in DC (2026-10-19 edge): no lines", lost.windows >= 10u && lost.lines == 0u);
    }
    printf("%u/%u video quality checks passed\n", s_run - s_fail, s_run);
    return s_fail != 0;
}

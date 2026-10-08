/* C5VRX by Twotoz/contributors: realtime AutoFit for the EDGE range demod.
 *
 * The AFC v2 measurement already yields, per completed IQ window, the mean
 * instantaneous frequency of the sync tip and of the burst-free back porch.
 * Their difference is the VTX deviation (1914 kHz at the search model's
 * nominal transfer), the porch gives the carrier centre. A median of 16
 * windows with a bounded spread re-synthesizes the EDGE LUT (bits 0..12)
 * when the fit moved by >3 % or >50 kHz; the last fit per channel is stored
 * and re-applied at the next boot. The CPU only writes LUT words; the
 * 40 MS/s path stays in the BitScrambler. */
#include "video_internal.h"
#include "afc_v2.h"
#include "edge_autofit.h"
#include "cvbs_level_hw.h"
#include <math.h>
#include <stdlib.h>

#define AF_WINDOWS     16u
#define AF_SPREAD_KHZ  250
#define AF_MIN_GAP_US  2000000LL

typedef struct { uint16_t version, freq_mhz; int32_t dev_x10000, centre_hz; } af_saved_t;
#define AF_SAVED_VERSION 1u

static int32_t s_sync[AF_WINDOWS], s_porch[AF_WINDOWS];
static unsigned s_n;
static double s_dev, s_centre;
static bool s_applied, s_boot_tried;
static uint32_t s_fits, s_updates, s_rejects;
static int64_t s_last_us;
static uint16_t s_lut[1024];
static edge_af_params_t s_params;
static bool s_params_set;

static int cmp_i32(const void *a, const void *b)
{
    int32_t x = *(const int32_t *)a, y = *(const int32_t *)b;
    return (x > y) - (x < y);
}

/* Median with the spread of the middle half (robust to scene leakage). */
static bool robust(const int32_t *v, int32_t *median)
{
    int32_t s[AF_WINDOWS];
    memcpy(s, v, sizeof(s));
    qsort(s, AF_WINDOWS, sizeof(s[0]), cmp_i32);
    *median = (s[AF_WINDOWS / 2 - 1] + s[AF_WINDOWS / 2]) / 2;
    return s[3 * AF_WINDOWS / 4] - s[AF_WINDOWS / 4] <= AF_SPREAD_KHZ;
}

void edge_autofit_set_params(const edge_af_params_t *p)
{
    s_params = *p; s_params_set = true;
    s_applied = false;              /* re-synthesize with the new rung */
}

static bool apply(double dev, double centre)
{
    if (!s_params_set) { edge_af_pinned(&s_params); s_params_set = true; }
    if (!edge_af_synthesize(&s_params, dev, centre, s_lut)) return false;
    if (!c5v4_edge_lut_write(s_lut)) return false;
    s_dev = dev; s_centre = centre; s_applied = true; ++s_updates;
    s_last_us = esp_timer_get_time();
    return true;
}

bool edge_autofit_reapply(void)
{
    return s_applied && apply(s_dev, s_centre);
}

void edge_autofit_observe(const afc2_result_t *r, bool valid, bool settled)
{
    if (!c5vrx4_edge_autofit_demod() || !c5v4_edge_lut_ready()) return;
    if (!s_boot_tried) {
        s_boot_tried = true;
        af_saved_t sv;
        if (c5vrx4_blob_load("edge_fit", &sv, sizeof(sv)) && sv.version == AF_SAVED_VERSION &&
            sv.freq_mhz == rf_get_frequency_mhz() &&
            apply(sv.dev_x10000 / 10000.0, (double)sv.centre_hz))
            printf("EDGE_AF restored dev=%.3f centre_khz=%.0f\n", s_dev, s_centre / 1000.0);
    }
    if (!valid || !r->sync_pairs || !r->porch_pairs) return;
    s_sync[s_n] = r->sync_khz; s_porch[s_n] = r->porch_khz;
    if (++s_n < AF_WINDOWS) return;
    s_n = 0;
    int32_t sync, porch;
    double dev, centre;
    if (!robust(s_sync, &sync) || !robust(s_porch, &porch) || !edge_af_fit(sync, porch, &dev, &centre)) {
        ++s_rejects;
        return;
    }
    ++s_fits;
    int64_t now = esp_timer_get_time();
    bool moved = !s_applied || fabs(dev - s_dev) > .03 * s_dev || fabs(centre - s_centre) > 50e3;
    if (!moved || !settled || now - s_last_us < AF_MIN_GAP_US) return;
    if (!apply(dev, centre)) return;
    printf("EDGE_AF fit dev=%.3f centre_khz=%.0f sync_khz=%ld porch_khz=%ld\n", dev, centre / 1000.0,
           (long)sync, (long)porch);
    af_saved_t sv = {AF_SAVED_VERSION, rf_get_frequency_mhz(), (int32_t)lrint(dev * 10000.0), (int32_t)lrint(centre)};
    (void)c5vrx4_blob_store("edge_fit", &sv, sizeof(sv));
}

void edge_autofit_print(void)
{
    if (!c5vrx4_edge_autofit_demod()) return;
    printf("EDGE_AF applied=%u dev=%.3f centre_khz=%.0f fits=%lu updates=%lu rejects=%lu rung=%ux%u/out%u kp=%.2f ki=%.2f\n",
           s_applied, s_dev, s_centre / 1000.0, (unsigned long)s_fits, (unsigned long)s_updates,
           (unsigned long)s_rejects, s_params.phases, s_params.frequencies, s_params.output, s_params.kp, s_params.ki);
    c5v4_edge_lut_print();
}

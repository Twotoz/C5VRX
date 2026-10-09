/* C5VRX by Twotoz/contributors: AutoFit for the PAIR and EDGE range demods.
 *
 * The AFC v2 measurement yields, per completed IQ window, the mean
 * instantaneous frequency of the sync tip and of the burst-free back porch.
 * Their difference is the VTX deviation (1914 kHz at the search model's
 * nominal transfer), the porch gives the carrier centre. A median of 16
 * windows with a bounded spread is a fit; fits are taken only on a good
 * carrier (caller gate), whichever program runs, and stored per channel.
 *
 * Review 2026-10-09 (PR #190): words are never written while TX reads the
 * LUT. The transport applies the fit in the halted-engine window of every
 * program load (boot, menu exit, FusionDemod swap); a new VTX fit that
 * differs from the loaded one requests one reload, at most every 30 s. */
#include "video_internal.h"
#include "cvbs_level_hw.h"
#include <math.h>
#include <stdlib.h>

#define AF_WINDOWS       16u
#define AF_SPREAD_KHZ    250
#define AF_RELOAD_GAP_US 30000000LL

typedef struct { uint16_t version, freq_mhz; int32_t dev_x10000, centre_hz; } af_saved_t;
#define AF_SAVED_VERSION 2u

static int32_t s_sync[AF_WINDOWS], s_porch[AF_WINDOWS];
static unsigned s_n;
static uint16_t s_freq;                 /* channel of s_fit */
static bool s_fit_valid, s_loaded_valid, s_reload_req;
static double s_dev, s_centre;          /* fit of this channel */
static double s_loaded_dev, s_loaded_centre;
static int s_loaded_kind;
static uint32_t s_fits, s_rejects, s_stored, s_applied, s_reloads;
static int64_t s_last_reload_us;
static uint16_t s_words[1024];

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

static void key_for(uint16_t freq, char key[12]) { snprintf(key, 12, "af%u", (unsigned)freq); }

static void load_channel(uint16_t freq)
{
    char key[12]; af_saved_t sv;
    key_for(freq, key);
    s_freq = freq; s_n = 0;
    s_fit_valid = c5vrx4_blob_load(key, &sv, sizeof(sv)) && sv.version == AF_SAVED_VERSION &&
                  sv.freq_mhz == freq;
    if (s_fit_valid) { s_dev = sv.dev_x10000 / 10000.0; s_centre = sv.centre_hz; }
}

static void sync_channel(void)
{
    uint16_t freq = rf_get_frequency_mhz();
    if (freq != s_freq) load_channel(freq);
}

bool autofit_active(void)
{
    return c5vrx4_autofit_enabled() && (c5vrx4_edge_autofit_demod() || c5vrx4_pair_autofit_demod());
}

/* Halted-engine window of a program load: the transport has called
 * c5v4_fit_set_program(kind), loaded the program and run the self-test. */
bool autofit_apply_stopped(void)
{
    s_loaded_kind = c5v4_fit_ready();
    s_loaded_valid = false;
    if (!autofit_active() || !s_loaded_kind) return false;
    sync_channel();
    if (!s_fit_valid) return false;              /* pristine words = nominal fit */
    const uint16_t *pristine = c5v4_fit_pristine();
    if (s_loaded_kind == C5V4_FIT_EDGE) {
        edge_af_params_t p; edge_af_pinned(&p);
        if (!edge_af_synthesize(&p, s_dev, s_centre, s_words)) return false;
        for (unsigned i = 0; i < 1024; ++i)
            s_words[i] = (uint16_t)((pristine[i] & 0xe000u) | (s_words[i] & 0x1fffu));
    } else {
        pair_af_remap(pristine, s_dev, s_centre, s_words);
    }
    if (!c5v4_fit_write_stopped(s_words)) return false;
    s_loaded_valid = true; s_loaded_dev = s_dev; s_loaded_centre = s_centre;
    ++s_applied;
    return true;
}

bool autofit_take_reload_request(void)
{
    if (!s_reload_req) return false;
    s_reload_req = false; ++s_reloads; s_last_reload_us = esp_timer_get_time();
    return true;
}

void autofit_observe(const afc2_result_t *r, bool good, bool settled)
{
    if (!autofit_active()) return;
    sync_channel();
    if (!good || !r->sync_pairs || !r->porch_pairs) return;
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
    if (!settled) return;                  /* AFC may still retune */
    if (!s_fit_valid || fabs(dev - s_dev) > .03 * s_dev || fabs(centre - s_centre) > 50e3) {
        s_dev = dev; s_centre = centre; s_fit_valid = true;
        char key[12]; key_for(s_freq, key);
        af_saved_t sv = {AF_SAVED_VERSION, s_freq, (int32_t)lrint(dev * 10000.0), (int32_t)lrint(centre)};
        if (c5vrx4_blob_store(key, &sv, sizeof(sv))) ++s_stored;
        printf("AUTOFIT fit freq=%u dev=%.3f centre_khz=%.0f sync_khz=%ld porch_khz=%ld\n",
               s_freq, dev, centre / 1000.0, (long)sync, (long)porch);
    }
    /* The running words carry another fit: one reload, rate-limited. */
    bool stale = s_loaded_kind && (!s_loaded_valid || fabs(s_dev - s_loaded_dev) > .04 * s_dev ||
                                   fabs(s_centre - s_loaded_centre) > 80e3);
    if (stale && esp_timer_get_time() - s_last_reload_us >= AF_RELOAD_GAP_US) s_reload_req = true;
}

/* Adaptive receiver supervisor, phase 1 (PR #190 review, 2026-10-09): one
 * read-only view of every estimator with the control epoch it belongs to,
 * before any coordinated actuation is added. Measuring is not changing. */
void rxsup_print(void)
{
    printf("RXSUP epoch=%lu/%lu/%lu gain=%u cnr_db=%.1f p50=%d clip_pm=%d coherence=%d "
           "dc_mstep=%d/%d drift_mcells=%d/%d cfo_khz=%d afc=%u/%u iq_skew_pm=%d "
           "fit=%u dev=%.3f centre_khz=%.0f sphase=%s/%u program=%s actuators=read_only\n",
           (unsigned long)s_profile_generation, (unsigned long)phy_rx_lab_generation(),
           (unsigned long)s_gain_transition_count, s_current_gain, s_cnr_x10 / 10.0, s_v3_p50,
           s_v3_clip_pm, s_v3_coherence, s_v3_dc_i_mstep, s_v3_dc_q_mstep, s_drift_avg[0], s_drift_avg[1],
           s_cfo_khz, (unsigned)s_afc_mode, s_afc_video_locked, s_last_iq_skew_permille,
           s_fit_valid, s_dev, s_centre / 1000.0, sphase_state_name(), s_sphase_auto_ppm,
           !c5vrx4_edge_autofit_demod() ? c5vrx4_demodulator_name() : s_fdemod.edge ? "EDGE" : "PAIR");
}

void autofit_print(void)
{
    rxsup_print();
    if (!autofit_active()) return;
    if (c5vrx4_edge_autofit_demod())
        printf("FUSION enabled=%u program=%s cnr_db=%.1f to_edge=%lu to_pair=%lu swaps=%lu\n",
               c5vrx4_fusion_enabled(), s_fdemod.edge ? "EDGE" : "PAIR", s_cnr_x10 / 10.0,
               (unsigned long)s_fdemod.to_edge, (unsigned long)s_fdemod.to_pair, (unsigned long)s_fdemod_swaps);
    printf("AUTOFIT freq=%u fit=%u dev=%.3f centre_khz=%.0f loaded=%s/%u loaded_dev=%.3f fits=%lu rejects=%lu "
           "stored=%lu applied=%lu reloads=%lu cnr_db=%.1f\n",
           s_freq, s_fit_valid, s_dev, s_centre / 1000.0,
           s_loaded_kind == C5V4_FIT_EDGE ? "EDGE" : s_loaded_kind == C5V4_FIT_PAIR ? "PAIR" : "none",
           s_loaded_valid, s_loaded_dev, (unsigned long)s_fits, (unsigned long)s_rejects,
           (unsigned long)s_stored, (unsigned long)s_applied, (unsigned long)s_reloads, s_cnr_x10 / 10.0);
    c5v4_fit_print();
}

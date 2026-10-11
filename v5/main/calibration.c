/* C5VRX-5 by Twotoz and the C5VRX contributors: automatic receive-chain
 * calibration, from C5VRX-4 main/video_calibration.c, video_dco.c,
 * video_drift.c and video_sampling.c. One low-priority task (250 ms tick),
 * never in the 40 MS/s path:
 *  - sampling phase: PARLIO must not sample MODEM_DIAG mid-transition;
 *  - narrow analog filter: one measured sweep (VTX off) per board;
 *  - per-gain RX DC correction, searched only in confirmed quiet;
 *  - DC drift tracking and persistence of the Direct Gain map. */
#include "receiver.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "driver/temperature_sensor.h"
#include "dsp.h"
#include "esp_attr.h"
#include "esp_timer.h"
#include "phy.h"
#include "rf.h"
#include "soc/pcr_struct.h"

extern unsigned char phy_param[];

typedef struct {
    uint32_t windows;
    uint64_t hf4_d4, hf4_count;
    int dc_i, dc_q;          /* milli-cells: circle centre or plain mean */
    bool dc_circle;
    unsigned envelope_x100;
    metrics_t m;             /* last window */
} window_stats_t;

static bool quiet_owner(void)
{
    return !s_menu_active && !s_scan_active && !receiver_paused() && !phy_busy();
}

static bool collect(unsigned windows, window_stats_t *out)
{
    uint8_t sample[PROBE_BYTES];
    dsp_circle_t circle = {0};
    memset(out, 0, sizeof(*out));
    for (unsigned tries = 0; tries < windows * 3u && out->windows < windows; ++tries) {
        vTaskDelay(2);   /* two ticks: one tick starved IDLE with the observer */
        if (!copy_probe_window(sample, NULL)) continue;
        for (unsigned r = 0; r < PROBE_REGIONS; ++r)
            dsp_hf4_sums(sample + r * PROBE_REGION_BYTES, PROBE_REGION_BYTES, &out->hf4_d4);
        out->hf4_count += PROBE_REGIONS * (PROBE_REGION_BYTES - 4u);
        dsp_circle_sums(sample, sizeof(sample), &circle);
        out->m = measure_window(sample, sizeof(sample));
        ++out->windows;
    }
    if (!out->windows) return false;
    out->dc_circle = dsp_circle_dc(&circle, &out->dc_i, &out->dc_q, &out->envelope_x100);
    return out->windows * 2u >= windows;
}

/* ---- Sampling phase --------------------------------------------------------
 * PARLIO RX (PLL_F240M/6) and MODEM_DIAG (every 3 ticks of 240 MHz) have a
 * phase fixed at reset; about 3 boots in 10 sample mid-transition (zerowidth
 * PR #3). The fourth-difference excess separates them on noise and on a
 * carrier; a bad phase is slipped one RX tick at a time. Re-checked every
 * two minutes and after a retune. */
#define SPHASE_LIMIT      300
#define SPHASE_POSITIONS  9u
#define SPHASE_TRIES      12u
#define SPHASE_MAX_SCANS  5u
static int s_sphase_excess = -1;
static unsigned s_sphase_scans;
static const char *s_sphase_state = "unverified";

static int sphase_excess(const window_stats_t *w)
{
    int e = dsp_hf4_excess_milli(w->hf4_d4, w->hf4_count);
    return e > 0 ? e : 0;
}

static bool sphase_scan(void)
{
    if (!receiver_pause("SPHASE")) return false;
    window_stats_t w;
    int best = 0x7fffffff;
    for (unsigned pos = 0; pos < SPHASE_POSITIONS; ++pos) {
        if (pos) { rx_clock_slip(1); vTaskDelay(pdMS_TO_TICKS(20)); }
        if (collect(48, &w) && sphase_excess(&w) < best) best = sphase_excess(&w);
    }
    /* Positions repeat every three ticks: stop on one near the cleanest. */
    bool settled = false;
    for (unsigned n = 0; best != 0x7fffffff && n < SPHASE_TRIES; ++n) {
        if (!collect(48, &w)) break;
        int e = sphase_excess(&w), margin = best / 4 > 100 ? best / 4 : 100;
        if (e <= best + margin && e < SPHASE_LIMIT) { settled = true; s_sphase_excess = e; break; }
        rx_clock_slip(1);
        vTaskDelay(pdMS_TO_TICKS(20));
    }
    receiver_resume();
    printf("SPHASE scan best=%d settled=%u\n", best, settled);
    return settled;
}

static void sphase_service(void)
{
    static int64_t next_us, settled_us;
    static uint16_t freq;
    if (!quiet_owner()) return;
    int64_t now = esp_timer_get_time();
    if (freq != rf_frequency_mhz()) { freq = rf_frequency_mhz(); settled_us = 0; s_sphase_scans = 0; }
    if (settled_us && now - settled_us < 120000000LL) return;
    if (now < next_us) return;
    /* A held carrier in band, or receiver noise without clipping. */
    bool carrier = s_gain.state == DG_HOLD && s_obs_coherence >= 60 && s_obs_p50 >= 13 && s_obs_p50 <= 46;
    bool noise = s_obs_p50 <= 8 && s_obs_clip_pm == 0 && s_obs_coherence < 40;
    if (!carrier && !noise) return;
    window_stats_t w;
    if (!collect(48, &w)) return;
    s_sphase_excess = sphase_excess(&w);
    if (s_sphase_excess < SPHASE_LIMIT) { s_sphase_state = "settled"; settled_us = now; return; }
    if (s_sphase_scans >= SPHASE_MAX_SCANS) {   /* no endless slips in flight */
        s_sphase_state = "failed";
        next_us = now + 60000000LL;
        return;
    }
    ++s_sphase_scans;
    s_sphase_state = "checking";
    if (sphase_scan()) { s_sphase_state = "settled"; settled_us = esp_timer_get_time(); }
    else { s_sphase_state = "unsettled"; next_us = esp_timer_get_time() + 10000000LL; }
}

/* ---- Narrow analog filter --------------------------------------------------
 * The FM threshold follows the pre-detection noise bandwidth, and PARLIO
 * keeps every second sample of the ~80 MS/s bus unfiltered, so the noise
 * the demod sees is set by the analog filter, alias included. The
 * capacitor codes are per-chip calibrated, so the filter is measured, not
 * assumed: with no carrier at maximum gain every setting (digital BW40/
 * BW20 x RX0 code x second-stage skirt) gets a 64-point noise spectrum, and
 * the one with the lowest noise bandwidth whose -3 dB width still covers
 * 14 MHz (enough for every tested VTX deviation, docs/FINDINGS.md) and whose
 * noise stays incoherent (so Direct Gain still recognises "no carrier")
 * is kept. Built on ESPARGOS esp-sdr's C5 BANDWIDTH control (GPL-3.0). */
#define FILTER_VERSION     1u
#define FILTER_MIN_KHZ     14000u
#define QUIET_QPHASE       34     /* V5 coherence < 25 */
#define QUIET_CLIP_PM      50
#define CAL_WINDOWS        96u
typedef struct {
    uint8_t version, valid, bw20, skirt;
    int8_t code;
    uint16_t width_khz, nbw_khz, calibrated_nbw_khz;
} filter_blob_t;
static filter_blob_t s_filter_cal;
static const char *s_filter_result = "never";

static bool noise_measure(unsigned *width, unsigned *nbw, int *q_phase, int *clip_pm)
{
    float psd[DSP_FFT_N] = {0};
    uint8_t sample[PROBE_BYTES];
    unsigned got = 0;
    int q_sum = 0, clip_max = 0;
    for (unsigned tries = 0; tries < CAL_WINDOWS * 3u && got < CAL_WINDOWS; ++tries) {
        vTaskDelay(1);
        if (!copy_probe_window(sample, NULL)) continue;
        for (unsigned r = 0; r < PROBE_REGIONS; ++r) dsp_psd_accumulate(sample + r * PROBE_REGION_BYTES, psd);
        metrics_t m = measure_window(sample, sizeof(sample));
        q_sum += m.q_phase;
        if (m.clip_pm > clip_max) clip_max = m.clip_pm;
        ++got;
    }
    if (got * 2u < CAL_WINDOWS) return false;
    *width = dsp_psd_width_khz(psd);
    *nbw = dsp_psd_nbw_khz(psd);
    *q_phase = q_sum / (int)got;
    *clip_pm = clip_max;
    return true;
}

static bool measure_setting(rf_filter_t f, unsigned *width, unsigned *nbw, bool *quiet, bool *carrier)
{
    rf_set_filter(f);
    vTaskDelay(pdMS_TO_TICKS(20));
    int q = 0, clip = 0;
    if (!noise_measure(width, nbw, &q, &clip)) return false;
    *quiet = q < QUIET_QPHASE && clip < QUIET_CLIP_PM;
    *carrier = clip >= QUIET_CLIP_PM;
    printf("FILTER_CAL bw=%s code=%d skirt=%u width_khz=%u nbw_khz=%u q=%d clip_pm=%d\n",
           f.bw20 ? "BW20" : "BW40", f.code, f.skirt, *width, *nbw, q, clip);
    return true;
}

bool filter_calibrate(bool automatic)
{
    if (phy_filter_calibrated_code() < 0) { s_filter_result = "unsupported_phy"; return false; }
    if (!receiver_pause("FILTER_CAL")) { s_filter_result = "busy"; return false; }
    const uint8_t saved_gain = s_current_gain;
    receiver_set_gain(rf_gain_table()->max_index);
    vTaskDelay(pdMS_TO_TICKS(50));
    const rf_filter_t previous = rf_filter(), calibrated = {PHY_FILTER_CALIBRATED, 0, false};
    unsigned width, nbw, cal_nbw = 0;
    bool quiet = false, carrier = false;
    printf("FILTER_CAL begin mode=%s freq=%u target_width_khz=%u\n", automatic ? "auto" : "manual",
           rf_frequency_mhz(), FILTER_MIN_KHZ);
    bool ok = measure_setting(calibrated, &width, &cal_nbw, &quiet, &carrier) && quiet;
    rf_filter_t best = calibrated;
    unsigned best_nbw = ok && width >= FILTER_MIN_KHZ ? cal_nbw : 0, best_width = width;
    static const int8_t codes[] = {PHY_FILTER_CALIBRATED, 0, 8, 16, 24, 32, 40, 48, 56, 60};
    static const uint8_t skirts[] = {0, 8, 16, 24, 32};
    for (unsigned d = 0; ok && d < 2u; ++d)
        for (unsigned c = 0; ok && c < sizeof(codes); ++c)
            for (unsigned s = 0; ok && s < sizeof(skirts); ++s) {
                rf_filter_t f = {codes[c], skirts[s], d == 1u};
                if (!measure_setting(f, &width, &nbw, &quiet, &carrier)) { ok = false; break; }
                if (carrier) { ok = false; break; }   /* a VTX switched on mid-sweep */
                if (quiet && nbw && width >= FILTER_MIN_KHZ && (!best_nbw || nbw < best_nbw)) {
                    best = f; best_nbw = nbw; best_width = width;
                }
            }
    /* Re-check on the calibrated bytes before trusting the sweep. */
    if (ok) ok = measure_setting(calibrated, &width, &nbw, &quiet, &carrier) && quiet;
    if (ok) {
        s_filter_cal = (filter_blob_t){FILTER_VERSION, 1u, best.bw20, best.skirt, best.code,
                                       (uint16_t)best_width, (uint16_t)(best_nbw > 65535u ? 65535u : best_nbw),
                                       (uint16_t)(cal_nbw > 65535u ? 65535u : cal_nbw)};
        (void)blob_store("filter", &s_filter_cal, sizeof(s_filter_cal));
        rf_set_filter(best);
        s_filter_result = "stored";
        printf("FILTER_CAL chosen bw=%s code=%d skirt=%u width_khz=%u nbw_khz=%u calibrated_nbw_khz=%u "
               "gain_db=%.1f\n", best.bw20 ? "BW20" : "BW40", best.code, best.skirt, best_width, best_nbw,
               cal_nbw, best_nbw && cal_nbw ? 10.0 * log10((double)cal_nbw / best_nbw) : 0.0);
    } else {
        rf_set_filter(previous);
        s_filter_result = carrier ? "carrier_present" : "sample_failure";
        printf("FILTER_CAL aborted=%s\n", s_filter_result);
    }
    receiver_set_gain(saved_gain);
    receiver_resume();
    return ok;
}

/* Console request: runs in this task, so the console keeps draining USB. */
static volatile bool s_filter_requested;
void filter_request(void) { s_filter_requested = true; }

/* First boot: after 3 s of quiet listening at maximum gain; at most once a
 * minute while a carrier keeps interrupting it. */
static void filter_service(void)
{
    static unsigned quiet_ticks;
    static int64_t last_try;
    if (s_filter_requested && !s_menu_active) { s_filter_requested = false; (void)filter_calibrate(false); return; }
    bool want = !s_filter_cal.valid && phy_filter_calibrated_code() >= 0 && quiet_owner() &&
                s_current_gain == rf_gain_table()->max_index && s_gain.state != DG_SETTLE &&
                s_obs_coherence < 25;
    if (!want) { quiet_ticks = 0; return; }
    if (++quiet_ticks < 12u) return;
    int64_t now = esp_timer_get_time();
    if (last_try && now - last_try < 60000000LL) return;
    last_try = now;
    quiet_ticks = 0;
    (void)filter_calibrate(true);
}

/* ---- Per-gain RX DC correction --------------------------------------------
 * Near the range edge the carrier is a few cells; a receiver DC of ~1 cell
 * biases every origin-adjacent phase. The DC DAC pair is searched per gain
 * row in confirmed quiet (no burst-confirmed sync for 3 s, a noise-like
 * envelope for 5 s, never on a channel where a carrier was seen this boot)
 * and held right after every gain write by the post-gain hook (board,
 * 2026-10-07: DC median ~134 LSB without the hook, ~11 with it). */
#define DCO_VERSION      5u
#define DCO_RESEARCH_US  120000000LL
#define DCO_NO_SYNC_US   3000000LL
#define DCO_QUIET_US     5000000LL
#define DCO_NOISE_RATIO  115u
typedef struct {
    int16_t code[2];
    int16_t residual_mcells[2];
    uint8_t valid;
} dco_entry_t;
typedef struct {
    uint16_t freq;
    uint8_t version, lo, hi, band5, iq_scale_sel, filter_bw20;
    int8_t filter_code, filter_skirt;
    dco_entry_t e[ARC_VENDOR_GAIN_MAX + 1u];
} dco_table_t;
/* CPU-only state in LP RAM: internal SRAM is the menu's DMA heap. */
static RTC_FAST_ATTR dco_table_t s_dco;
static RTC_FAST_ATTR int64_t s_dco_found_us[ARC_VENDOR_GAIN_MAX + 1u];
static RTC_FAST_ATTR uint8_t s_env_buf[24u * PROBE_BYTES];
static uint8_t s_dco_banned[ARC_VENDOR_GAIN_MAX + 1u];
static uint8_t s_dco_floor;
static bool s_dco_dirty;
static int64_t s_quiet_since_us, s_quiet_eval_us;
static bool s_quiet_last;
static uint16_t s_carrier_freq;
static uint32_t s_dco_searches, s_dco_hook_holds, s_dco_refusals, s_dco_aborts;

static void dco_context(dco_table_t *t, uint16_t freq)
{
    const arc_gain_table_t *table = rf_gain_table();
    rf_filter_t f = rf_filter();
    t->version = DCO_VERSION;
    t->freq = freq;
    t->lo = arc_gain_highest_rf_stage_start(table);
    t->hi = table->max_index;
    t->band5 = table->band5;
    t->iq_scale_sel = phy_param[650];   /* phy_rxiq_scale_set() selector */
    t->filter_code = f.code;
    t->filter_skirt = (int8_t)f.skirt;
    t->filter_bw20 = f.bw20;
}

static bool dco_same_context(const dco_table_t *a, const dco_table_t *b)
{
    return a->version == b->version && a->freq == b->freq && a->lo == b->lo && a->hi == b->hi &&
           a->band5 == b->band5 && a->iq_scale_sel == b->iq_scale_sel && a->filter_code == b->filter_code &&
           a->filter_skirt == b->filter_skirt && a->filter_bw20 == b->filter_bw20;
}

static void dco_select(void)
{
    dco_table_t want = {0}, saved;
    dco_context(&want, rf_frequency_mhz());
    if (dco_same_context(&want, &s_dco)) return;
    (void)phy_dco_release();
    memset(&s_dco, 0, sizeof(s_dco));
    memset(s_dco_found_us, 0, sizeof(s_dco_found_us));
    memset(s_dco_banned, 0, sizeof(s_dco_banned));
    s_dco_floor = 0;
    if (blob_load("dco", &saved, sizeof(saved)) && dco_same_context(&saved, &want)) s_dco = saved;
    else s_dco = want;
}

void dco_post_gain(uint8_t g)
{
    if (receiver_paused() || g < s_dco.lo || g > s_dco.hi || g < s_dco_floor || !s_dco.e[g].valid ||
        s_dco_banned[g] || s_dco.freq != rf_frequency_mhz()) return;
    if (phy_dco_hold(s_dco.e[g].code[0], s_dco.e[g].code[1]) == ESP_OK) ++s_dco_hook_holds;
}

static bool dco_measure(int dc[2])
{
    window_stats_t w;
    vTaskDelay(pdMS_TO_TICKS(50));
    if (!collect(64, &w)) return false;
    dc[0] = w.dc_i;
    dc[1] = w.dc_q;
    return true;
}

/* Quiet = receiver noise: flat envelope statistic, and not dead or railing
 * IQ (which also has one; board 2026-10-07). */
static bool noise_like(void)
{
    unsigned got = 0;
    for (unsigned tries = 0; tries < 72u && got < 24u; ++tries) {
        vTaskDelay(1);
        if (copy_probe_window(s_env_buf + got * PROBE_BYTES, NULL)) ++got;
    }
    if (got < 16u) return false;
    unsigned origin = 0, rail = 0, n = got * PROBE_BYTES;
    for (unsigned k = 0; k < n; ++k) {
        int i = dsp_i(s_env_buf[k]), q = dsp_q(s_env_buf[k]);
        origin += (i == 0 || i == -1) && (q == 0 || q == -1);
        rail += i == -8 || i == 7 || q == -8 || q == 7;
    }
    if (origin * 2u > n || rail * 5u > n) return false;
    return dsp_envelope_ratio_x100(s_env_buf, n) <= DCO_NOISE_RATIO;
}

/* A search needs noise wide enough to dither the quantizer: >= 3 cells
 * per axis each with >= 5 % of the samples. */
static bool noise_measurable(void)
{
    uint8_t buf[PROBE_BYTES];
    unsigned hi[16] = {0}, hq[16] = {0}, n = 0;
    for (unsigned tries = 0; tries < 24u && n < 8u * sizeof(buf); ++tries) {
        vTaskDelay(1);
        if (!copy_probe_window(buf, NULL)) continue;
        for (unsigned k = 0; k < sizeof(buf); ++k) { ++hi[dsp_i(buf[k]) & 15]; ++hq[dsp_q(buf[k]) & 15]; }
        n += sizeof(buf);
    }
    if (n < 4u * sizeof(buf)) return false;
    unsigned ci = 0, cq = 0;
    for (unsigned c = 0; c < 16u; ++c) { ci += hi[c] * 20u >= n; cq += hq[c] * 20u >= n; }
    return ci >= 3u && cq >= 3u;
}

static void dco_service(void)
{
    if (!quiet_owner()) return;
    dco_select();
    const uint16_t freq = rf_frequency_mhz();
    const int64_t now = esp_timer_get_time();
    bool no_carrier = false;
    if (now - s_last_video_us > DCO_NO_SYNC_US) {
        if (now - s_quiet_eval_us < 1000000LL) no_carrier = s_quiet_last;
        else {
            s_quiet_eval_us = now;
            no_carrier = noise_like();
            s_quiet_last = no_carrier;
        }
    }
    /* A carrier seen on this channel makes "quiet" ambiguous: a weak FM
     * carrier at the edge has a noise-like envelope (board 2026-10-09). */
    if (!no_carrier && now - s_quiet_eval_us < 1000000LL) s_carrier_freq = freq;
    if (now - s_last_video_us <= DCO_NO_SYNC_US) s_carrier_freq = freq;
    if (s_carrier_freq == freq && no_carrier) { no_carrier = false; ++s_dco_refusals; }
    if (!no_carrier) s_quiet_since_us = 0;
    else if (!s_quiet_since_us) s_quiet_since_us = now;
    if (no_carrier && now - s_quiet_since_us >= DCO_QUIET_US) {
        int target = -1, low = s_dco_floor > s_dco.lo ? s_dco_floor : s_dco.lo;
        for (int g = s_dco.hi; g >= low; --g)
            if (!s_dco_found_us[g] || now - s_dco_found_us[g] > DCO_RESEARCH_US) { target = g; break; }
        if (target >= 0) {
            if (!receiver_pause("DCO")) return;
            const uint8_t restore = s_current_gain;
            (void)phy_dco_release();
            receiver_set_gain((uint8_t)target);
            vTaskDelay(pdMS_TO_TICKS(20));
            if (!noise_measurable()) {
                /* Noise below one quantizer step: no codes here or below. */
                for (int g = target; g >= (int)s_dco.lo; --g) s_dco.e[g].valid = 0;
                s_dco_floor = (uint8_t)(target + 1);
                s_dco_dirty = true;
            } else {
                phy_dco_result_t res;
                (void)phy_dco_search(dco_measure, &res);
                if (res.measured) {
                    s_dco.e[target] = (dco_entry_t){{(int16_t)res.codes[0], (int16_t)res.codes[1]},
                        {(int16_t)res.residual_mcells[0], (int16_t)res.residual_mcells[1]}, 1u};
                    s_dco_dirty = true;
                }
                s_dco_found_us[target] = now;
                ++s_dco_searches;
            }
            receiver_set_gain(restore);
            receiver_resume();
            return;
        }
        if (s_dco_dirty && blob_store("dco", &s_dco, sizeof(s_dco))) s_dco_dirty = false;
    }
    /* Hold guard: held codes must never coincide with dead or railing IQ. */
    static unsigned bad_ticks;
    const uint8_t g = s_current_gain;
    if (phy_dco_held()) {
        bad_ticks = (s_obs_origin_pm >= 900 || s_obs_clip_pm >= 500) ? bad_ticks + 1u : 0u;
        if (bad_ticks >= 3u) {
            (void)phy_dco_release();
            s_dco_banned[g] = 1u;
            bad_ticks = 0;
            ++s_dco_aborts;
            printf("DCO hold abort gain=%u\n", g);
        }
    } else if (s_gain.state != DG_SETTLE) {
        dco_post_gain(g);
    }
}

/* Thermal drift of the held DC during flight: nudge the held codes one
 * step when the measured centre stays > 0.3 cell off for 5 s (at most four
 * steps per gain per boot). Only a carrier's circle centre or receiver
 * noise is a DC measurement. */
static int s_drift_offset[ARC_VENDOR_GAIN_MAX + 1u][2];
static int s_drift_avg[2];
static uint32_t s_drift_nudges;
static float s_temp_c = -100.0f;

static void drift_service(void)
{
    static temperature_sensor_handle_t tsens;
    static int32_t sum[2];
    static unsigned n, over_s, ticks;
    static int64_t last_nudge;
    if (!tsens) {
        temperature_sensor_config_t cfg = TEMPERATURE_SENSOR_CONFIG_DEFAULT(-10, 80);
        if (temperature_sensor_install(&cfg, &tsens) == ESP_OK) (void)temperature_sensor_enable(tsens);
        else tsens = (temperature_sensor_handle_t)(uintptr_t)1u;
    }
    if ((uintptr_t)tsens > 1u && (ticks++ & 3u) == 0u) (void)temperature_sensor_get_celsius(tsens, &s_temp_c);
    const uint8_t g = s_current_gain;
    if (!quiet_owner() || s_gain.state != DG_HOLD || !phy_dco_held() || g > ARC_VENDOR_GAIN_MAX ||
        !s_dco.e[g].valid) { n = 0; sum[0] = sum[1] = 0; over_s = 0; return; }
    window_stats_t w;
    if (!collect(8, &w) || s_current_gain != g || s_gain.state != DG_HOLD ||
        (!w.dc_circle && w.envelope_x100 > 130u)) { n = 0; sum[0] = sum[1] = 0; return; }
    sum[0] += w.dc_i;
    sum[1] += w.dc_q;
    if (++n < 4u) return;
    s_drift_avg[0] = sum[0] / (int32_t)n;
    s_drift_avg[1] = sum[1] / (int32_t)n;
    n = 0; sum[0] = sum[1] = 0;
    bool over = abs(s_drift_avg[0]) > 300 || abs(s_drift_avg[1]) > 300;
    over_s = over ? over_s + 1u : 0u;
    int64_t now = esp_timer_get_time();
    if (over_s < 5u || now - last_nudge < 2000000LL) return;
    int di = s_drift_avg[0] > 300 ? -1 : s_drift_avg[0] < -300 ? 1 : 0;
    int dq = s_drift_avg[1] > 300 ? -1 : s_drift_avg[1] < -300 ? 1 : 0;
    if (abs(s_drift_offset[g][0] + di) > 4) di = 0;
    if (abs(s_drift_offset[g][1] + dq) > 4) dq = 0;
    int codes[2];
    if ((!di && !dq) || !phy_dco_nudge(di, dq, codes)) return;
    s_drift_offset[g][0] += di;
    s_drift_offset[g][1] += dq;
    s_dco.e[g].code[0] = (int16_t)codes[0];
    s_dco.e[g].code[1] = (int16_t)codes[1];
    s_dco_dirty = true;
    last_nudge = now;
    over_s = 0;
    ++s_drift_nudges;
}

/* Persist the measured gain map: at most every 2 minutes, while holding,
 * only when it changed (flash wear). */
static void gain_map_service(void)
{
    static int64_t last;
    static uint32_t last_learned;
    int64_t now = esp_timer_get_time();
    if (now - last < 120000000LL || s_gain.state != DG_HOLD || s_gain.learned == last_learned) return;
    last = now;
    last_learned = s_gain.learned;
    dg_map_blob_t blob;
    if (!dg_export_map(&s_gain, &blob)) return;
    blob.freq_mhz = rf_frequency_mhz();
    blob.lane_mode = 2u;
    if (s_gain_map_saved_valid && !memcmp(&blob, &s_gain_map_saved, sizeof(blob))) return;
    if (blob_store("gain_map", &blob, sizeof(blob))) { s_gain_map_saved = blob; s_gain_map_saved_valid = true; }
}

static void calibration_task(void *arg)
{
    (void)arg;
    rf_set_post_gain_hook(dco_post_gain);
    for (;;) {
        vTaskDelay(pdMS_TO_TICKS(250));
        dco_service();
        drift_service();
        gain_map_service();
        sphase_service();
        filter_service();
    }
}

void calibration_start(void)
{
    if (blob_load("filter", &s_filter_cal, sizeof(s_filter_cal)) && s_filter_cal.version == FILTER_VERSION &&
        s_filter_cal.valid) {
        rf_set_filter((rf_filter_t){s_filter_cal.code, s_filter_cal.skirt, s_filter_cal.bw20});
        s_filter_result = "loaded";
    } else memset(&s_filter_cal, 0, sizeof(s_filter_cal));
    ESP_ERROR_CHECK(xTaskCreate(calibration_task, "calibration", 4096, NULL, 2, NULL) == pdPASS ?
                    ESP_OK : ESP_ERR_NO_MEM);
}

void calibration_print(void)
{
    rf_filter_t f = rf_filter();
    unsigned valid = 0;
    for (unsigned g = 0; g <= ARC_VENDOR_GAIN_MAX; ++g) valid += s_dco.e[g].valid;
    printf("FILTER %s bw=%s code=%d skirt=%u width_khz=%u nbw_khz=%u calibrated_nbw_khz=%u\n", s_filter_result,
           f.bw20 ? "BW20" : "BW40", f.code, f.skirt, s_filter_cal.width_khz, s_filter_cal.nbw_khz,
           s_filter_cal.calibrated_nbw_khz);
    printf("DCO held=%u gains_valid=%u floor=%u searches=%lu hook_holds=%lu carrier_refusals=%lu aborts=%lu "
           "drift_avg_mcells=%d/%d nudges=%lu temp_c=%.1f\n", phy_dco_held(), valid, s_dco_floor,
           (unsigned long)s_dco_searches, (unsigned long)s_dco_hook_holds, (unsigned long)s_dco_refusals,
           (unsigned long)s_dco_aborts, s_drift_avg[0], s_drift_avg[1], (unsigned long)s_drift_nudges,
           (double)s_temp_c);
    printf("SPHASE state=%s excess_mc2=%d limit=%d scans=%u rx_div=%lu\n", s_sphase_state, s_sphase_excess,
           SPHASE_LIMIT, s_sphase_scans, (unsigned long)PCR.parl_clk_rx_conf.parl_clk_rx_div_num + 1ul);
}

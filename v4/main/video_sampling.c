#define SPHASE_POSITIONS 9u
#define SPHASE_SETTLE_TRIES 12u
/* Mixed MODEM_DIAG reads (zerowidth/C5VRX PR #3 fourth-difference probe, as
 * absolute excess energy): board scans 2026-10-08, fixed ultrafine, noise and
 * carrier: clean positions -70..+42, mid-change positions 391..3742
 * milli-cell^2. Borderline readings 121..199 (with glitches) rescan rather
 * than keep a low-margin position. The glitch-ppm test passed bad
 * positions at 756..4368 ppm under its 5000 limit. */
#define SPHASE_EXCESS_LIMIT 300u
#include "video_internal.h"

static sphase_scan_t lab_run_sample_phase_scan_result(unsigned *final_ppm);
static unsigned sphase_metric(const predemod_window_t *w)
{
    int excess = predemod_hf4_excess_milli(w->hf4_d4, w->hf4_count);
    return excess > 0 ? (unsigned)excess : 0u;
}
#define SPHASE_RETRY_US    10000000LL
#define SPHASE_MAX_SCANS   5u
static sphase_state_t s_sphase_state = SPHASE_UNVERIFIED;
bool s_sphase_auto_done;     /* = SETTLED, kept for the status line */
unsigned s_sphase_auto_ppm = UINT32_MAX;
unsigned s_sphase_scans;
static int64_t s_sphase_next_us;
/* Periodic re-verification (zerowidth/C5VRX PR #3: the clean position's
 * margin is unknown and may drift as the boards warm up). A clean result
 * costs one 48-window collection and no slip. */
#define SPHASE_RECHECK_US  120000000LL
static int64_t s_sphase_settled_us;
static uint16_t s_sphase_freq;

static sphase_scan_t lab_run_sample_phase_scan_result(unsigned *final_ppm)
{
    analog_agc_mode_t saved_mode = s_agc_mode;
    if (final_ppm) *final_ppm = UINT32_MAX;
    /* Clock slips are gain-independent: under native AGC the hardware AGC
     * keeps running and nothing is paused (there is no firmware gain owner). */
    const bool native = rf_native_agc_active();
    if (native) {
        if (s_menu_active || s_rssi_probe_active || s_pre_q4_probe_active) {
            printf("SPHASE refused=other_lab_or_menu\n");
            return SPHASE_SCAN_REFUSED;
        }
    } else if (!predemod_pause("SPHASE", &saved_mode)) {
        return SPHASE_SCAN_REFUSED;
    }
    printf("SPHASE begin rx_div=%lu lane=%u slip_us=1 positions=%u "
           "metric=hf4_excess_mc2 limit=%u\n",
           (unsigned long)PCR.parl_clk_rx_conf.parl_clk_rx_div_num + 1ul,
           rf_get_iq_lanes(), SPHASE_POSITIONS, SPHASE_EXCESS_LIMIT);
    predemod_window_t w;
    unsigned best = UINT32_MAX;
    for (unsigned pos = 0; pos < SPHASE_POSITIONS; ++pos) {
        if (pos) { rx_clock_slip(1); vTaskDelay(pdMS_TO_TICKS(20)); }
        if (!predemod_collect(48, &w)) { printf("SPHASE slip=%u sample=unavailable\n", pos); continue; }
        unsigned ppm = sphase_metric(&w);
        if (ppm < best) best = ppm;
        predemod_print("SPHASE", "SCAN", (int)pos, &w);
    }
    /* Positions repeat every three ticks: stop on one near the cleanest seen. */
    bool settled = false;
    unsigned last_ppm = UINT32_MAX;
    for (unsigned n = 0; best != UINT32_MAX && n < SPHASE_SETTLE_TRIES; ++n) {
        if (!predemod_collect(48, &w)) break;
        unsigned ppm = sphase_metric(&w);
        last_ppm = ppm;
        unsigned margin = best / 4u > 100u ? best / 4u : 100u;
        if (ppm <= best + margin && ppm < SPHASE_EXCESS_LIMIT) { settled = true; break; }
        rx_clock_slip(1);
        vTaskDelay(pdMS_TO_TICKS(20));
    }
    if (w.windows) predemod_print("SPHASE", settled ? "FINAL" : "UNSETTLED", (int)best, &w);
    if (!native) predemod_resume(saved_mode);
    printf("SPHASE done best_ppm=%u settled=%u persistent=0\n", best, settled);
    if (final_ppm) *final_ppm = settled ? last_ppm : UINT32_MAX;
    return settled ? SPHASE_SCAN_SETTLED : SPHASE_SCAN_UNSETTLED;
}

const char *sphase_state_name(void)
{
    return s_sphase_state == SPHASE_SETTLED ? "settled" : s_sphase_state == SPHASE_CHECKING ? "checking" :
           s_sphase_state == SPHASE_FAILED ? "failed" : "unverified";
}

void predemod_sphase_autocheck(void)
{
    if (!c5vrx4_sphase_auto_enabled()) return;
    /* Like predemod_quiet_owner(), but native AGC is a valid owner here. */
    if (s_menu_active || s_rssi_probe_active || s_pre_q4_probe_active ||
        phy_rx_lab_busy()) return;
    if (!rf_native_agc_active() &&
        (s_rx_profile != RX_PROFILE_DIRECT_GAIN || s_agc_mode != ANALOG_AGC_ACTIVE)) return;
    const uint16_t freq = rf_get_frequency_mhz();
    if (freq != s_sphase_freq) {           /* re-check after a retune (cheap unless bad) */
        s_sphase_freq = freq;
        if (s_sphase_state == SPHASE_SETTLED) s_sphase_state = SPHASE_UNVERIFIED;
        s_sphase_auto_done = false;
    }
    const int64_t now = esp_timer_get_time();
    if (s_sphase_state == SPHASE_SETTLED) {
        if (now - s_sphase_settled_us < SPHASE_RECHECK_US) return;
        s_sphase_state = SPHASE_UNVERIFIED;   /* periodic re-check */
        s_sphase_scans = 0;
    }
    if (now < s_sphase_next_us) return;
    /* A usable carrier. Direct V5: holding with the envelope in band, and
     * only moderate coherence (bad sampling itself lowers it). Native AGC
     * (no V5 observer; external research 2026-10-07 asked for this route):
     * a sync fragment within the last second. */
    if (IDLE_RASTER_ACTIVE()) return;
    if (rf_native_agc_active()) {
        if (now - s_last_idle_sync_us > 1000000LL) return;
    } else {
        /* A held carrier in band, or (new) receiver noise without clipping:
         * the excess probe separates both on the board, so a bad position
         * is fixed at boot before a VTX appears. */
        bool carrier = s_direct_gain_v3.state == DG3_HOLD && s_v3_coherence >= 60 &&
                       s_v3_p50 >= 13 && s_v3_p50 <= 46;
        bool noise = s_v3_p50 <= 8 && s_v3_clip_pm == 0 && s_v3_coherence < 40;
        if (!carrier && !noise) return;
    }
    predemod_window_t w;
    if (!predemod_collect(48, &w)) return;
    s_sphase_auto_ppm = sphase_metric(&w);
    bool good = s_sphase_auto_ppm < SPHASE_EXCESS_LIMIT;
    printf("SPHASE auto_check excess_mc2=%u threshold=%u glitch_ppm=%u coherence=%d state=%s action=%s scans=%u\n",
           s_sphase_auto_ppm, SPHASE_EXCESS_LIMIT, predemod_ppm(w.glitches, w.samples),
           s_v3_coherence, sphase_state_name(),
           good ? "none" : s_sphase_scans < SPHASE_MAX_SCANS ? "scan" : "give_up", s_sphase_scans);
    if (good) {
        s_sphase_state = SPHASE_SETTLED;
        s_sphase_auto_done = true;
        s_sphase_settled_us = now;
        return;
    }
    if (s_sphase_scans >= SPHASE_MAX_SCANS) {
        /* No endless slips during flight video. */
        s_sphase_state = SPHASE_FAILED;
        s_sphase_next_us = now + 6 * SPHASE_RETRY_US;   /* only re-measure now and then */
        return;
    }
    s_sphase_state = SPHASE_CHECKING;
    ++s_sphase_scans;
    unsigned final_ppm;
    sphase_scan_t r = lab_run_sample_phase_scan_result(&final_ppm);
    if (r == SPHASE_SCAN_SETTLED && final_ppm < SPHASE_EXCESS_LIMIT) {
        s_sphase_state = SPHASE_SETTLED;
        s_sphase_auto_done = true;
        s_sphase_auto_ppm = final_ppm;
        s_sphase_settled_us = esp_timer_get_time();
    } else {
        s_sphase_state = s_sphase_scans >= SPHASE_MAX_SCANS ? SPHASE_FAILED : SPHASE_UNVERIFIED;
        s_sphase_next_us = esp_timer_get_time() + SPHASE_RETRY_US;
    }
    printf("SPHASE auto_result scan=%d final_ppm=%u state=%s\n", (int)r, final_ppm, sphase_state_name());
}

void lab_run_sample_phase_scan(void) { (void)lab_run_sample_phase_scan_result(NULL); }

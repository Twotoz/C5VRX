/* C5VRX-5 by Twotoz and the C5VRX contributors: receive profile per channel.
 *
 * The VTX deviation sets the best filter width and tracker loop gain
 * (v5/research, simulation, usable-picture threshold):
 *   deviation 0.69: 10 MHz + kp 0.5, 3.2 dB better than 14 MHz + kp 0.85;
 *   deviation 1.0:  10 MHz + kp 0.6, 1.7 dB better;
 *   deviation 1.4:  12 MHz + kp 0.85 (the default) is already best;
 *   deviation 1.7:  16 MHz + kp 0.85, 0.4 dB better and +2 dB SINAD at 8 dB;
 *   deviation 2.0:  16 MHz + kp 0.85, 2.8 dB better (data/wide.txt).
 * The narrow profiles hurt a wide VTX badly, so a profile is only taken
 * from a clear measurement, kept per channel in NVS, and chosen before live
 * TX starts. It never changes under live video: a different profile is
 * applied only while the menu or a status screen owns TX. Anything
 * uncertain keeps DEFAULT.
 *
 * A stored deviation is not a VTX identity: another VTX on the same
 * channel can be much wider. So every boot and every channel change starts
 * in DEFAULT, and a matched profile needs a fresh measurement in the same
 * channel session, taken in the bounded startup phase (or a manual
 * CALIBRATE) while the DEFAULT picture keeps running; only then the switch
 * happens behind a status screen. After startup the measurement continues
 * passively on live video (no retune, no filter or tracker change): its
 * result is stored as PENDING and verified at the next boot. A VTX that
 * reads wide while a matched profile is active is reported loudly; the
 * receiver returns to DEFAULT at the next boot, channel change or
 * CALIBRATE, never by itself in flight.
 *
 * Deviation: the AFC measures the sync tip and the burst-free porch of
 * burst-confirmed lines; their distance is deviation x 1.914 MHz (40 of 140
 * IRE over 6.7 MHz). A decision needs three batches of 32 estimates, each
 * with a spread <= 8 % and all medians within 5 % of each other, on a
 * centred (AFC locked), coherent carrier in the healthy gain band. The
 * estimator and thresholds are pure (profile_logic.h) and host-tested. */
#include "receiver.h"
#include "profile_logic.h"
#include "phy.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static const char *const s_names[PROFILES] = {"DEFAULT 12MHZ KP0.85", "MATCHED 10MHZ KP0.6", "MATCHED 10MHZ KP0.5",
                                              "WIDE 16MHZ KP0.85"};
static uint16_t s_dev_table[FPV_CHANNELS];    /* last deviation x100 per channel, provisional */
static volatile int s_profile = PROFILE_DEFAULT;
static const char *volatile s_reason = "boot_default";

/* Measurement state, written by the control task through profile_observe().
 * STARTUP may switch behind a status screen; LIVE only records. */
enum { MEASURE_OFF, MEASURE_STARTUP, MEASURE_LIVE };
static volatile int s_measuring = MEASURE_OFF;
static dev_estimator_t s_dev;
static volatile int s_live_dev = -1;          /* this channel session, -1 = none yet */
static volatile size_t s_live_channel = (size_t)-1;
static volatile uint32_t s_observed, s_gated;

const char *profile_name(int p) { return p >= 0 && p < PROFILES ? s_names[p] : "?"; }
int profile_active(void) { return s_profile; }
const char *profile_reason(void) { return s_reason; }
int profile_deviation_x100(size_t channel) { return channel < FPV_CHANNELS ? s_dev_table[channel] : 0; }

int profile_for_deviation(int dev_x100)
{
    rf_filter_t f;
    return profile_class(dev_x100, filter_setting(FILTER_NARROW, &f), filter_setting(FILTER_WIDE, &f));
}

/* Filter class and tracker of a profile. Only while TX is not live: before
 * transport_start or with the menu / a status screen owning TX. A filter
 * that does not read back leaves DEFAULT, never a half-applied profile. */
bool profile_set(int p)
{
    if (p < 0 || p >= PROFILES) return false;
    rf_filter_t f;
    const int cls = p == PROFILE_DEFAULT ? FILTER_NORMAL : p == PROFILE_WIDE ? FILTER_WIDE : FILTER_NARROW;
    if (!filter_setting(cls, &f)) {
        if (p != PROFILE_DEFAULT) return false;
        f = rf_filter();            /* uncalibrated: keep what is set */
    }
    int tracker = p == PROFILE_KP050 ? TRACKER_KP050 : p == PROFILE_KP060 ? TRACKER_KP060 : TRACKER_KP085;
#ifdef C5VRX5_PAIR_ASSIST
    /* Narrow/medium deviations keep U85Q: assistance can worsen goggle
     * lock despite better luma SINAD. WIDE is measured at startup; no
     * in-flight demodulator or filter switch is introduced. */
    if (p == PROFILE_WIDE) tracker = TRACKER_PAIR085;
#endif
    if (!transport_set_tracker(tracker)) return false;   /* refuses while TX is live */
    if ((!rf_filter_equal(rf_filter(), f) || !rf_filter_applied()) && !rf_set_filter(f)) {
        printf("PROFILE error: filter code=%d skirt=%u not applied (read back code=%d skirt=%d)\n", f.code, f.skirt,
               phy_filter_code(), phy_filter_skirt());
        if (p != PROFILE_DEFAULT) {
            rf_filter_t d;
            if (filter_setting(FILTER_NORMAL, &d)) (void)rf_set_filter(d);
            (void)transport_set_tracker(TRACKER_KP085);
            s_profile = PROFILE_DEFAULT;
            return false;
        }
    }
    s_profile = p;
    return true;
}

void profile_load(void)
{
    if (!blob_load("vtxdev", s_dev_table, sizeof(s_dev_table))) memset(s_dev_table, 0, sizeof(s_dev_table));
}

/* Boot, before live TX: always DEFAULT; the stored deviation is only a hint
 * until this boot measures the VTX again. */
void profile_boot(void)
{
    if (!profile_set(PROFILE_DEFAULT)) printf("PROFILE boot error: DEFAULT not applied\n");
    s_reason = "boot_default";
    printf("PROFILE boot channel=%s stored_deviation_x100=%u (provisional) profile=%s\n", rf_channel()->name,
           s_dev_table[rf_channel_index()], s_names[s_profile]);
}

/* After a channel change: back to DEFAULT through a TX-owned transition
 * (the retune already interrupts the picture). Another channel may carry
 * another VTX; a matched profile needs a new measurement. */
void profile_channel_changed(void)
{
    s_measuring = MEASURE_OFF;
    s_live_dev = -1;
    s_live_channel = (size_t)-1;
    s_reason = "channel_changed";
    if (s_profile == PROFILE_DEFAULT) { profile_live_begin(); return; }
    if (s_menu_active) { (void)profile_set(PROFILE_DEFAULT); }
    else {
        status_screen("CHANNEL", rf_channel()->name, profile_name(PROFILE_DEFAULT), -1);
        (void)profile_set(PROFILE_DEFAULT);
        status_screen_end();
    }
    profile_live_begin();
}

void profile_measure_begin(void)
{
    s_measuring = MEASURE_OFF;
    dev_reset(&s_dev);
    s_measuring = MEASURE_STARTUP;
}

void profile_measure_end(void) { s_measuring = MEASURE_OFF; }
int profile_measure_result(void) { return s_dev.result_x100; }

/* Passive measurement on live video for the rest of the channel session. */
void profile_live_begin(void)
{
    if (calibration_in_startup()) return;   /* the startup step owns the estimator */
    if (s_live_channel == rf_channel_index() && s_live_dev > 0) return;   /* measured this session */
    s_measuring = MEASURE_OFF;
    dev_reset(&s_dev);
    s_live_channel = rf_channel_index();
    s_measuring = MEASURE_LIVE;
}

static void live_decided(int dev_x100)
{
    size_t ch = rf_channel_index();
    if (ch != s_live_channel) return;
    s_live_dev = dev_x100;
    if (s_dev_table[ch] != dev_x100) {
        s_dev_table[ch] = (uint16_t)dev_x100;
        (void)blob_store("vtxdev", s_dev_table, sizeof(s_dev_table));
    }
    int want = profile_for_deviation(dev_x100);
    if (want == s_profile)
        printf("PROFILE live channel=%s deviation_x100=%d confirms profile=%s\n", rf_channel()->name, dev_x100,
               s_names[s_profile]);
    else if (s_profile != PROFILE_DEFAULT && want == PROFILE_DEFAULT)
        printf("PROFILE WARNING live channel=%s deviation_x100=%d is too wide for active %s: DEFAULT at the next "
               "boot, channel change or CALIBRATE (never switched in flight)\n", rf_channel()->name, dev_x100,
               s_names[s_profile]);
    else
        printf("PROFILE live channel=%s deviation_x100=%d pending=%s (verified at the next boot or CALIBRATE; "
               "active stays %s)\n", rf_channel()->name, dev_x100, s_names[want], s_names[s_profile]);
}

/* One burst-confirmed AFC window; gates on a usable, centred carrier. */
void profile_observe(const afc2_result_t *r)
{
    const int mode = s_measuring;
    if (mode == MEASURE_OFF || s_dev.result_x100 >= 0 || !r->sync_pairs || !r->porch_pairs || !s_afc_locked) return;
    ++s_observed;
    if (s_obs_p50 < 13 || s_obs_p50 > 32 || s_obs_coherence < 80 || s_obs_clip_pm || s_gain.state != DG_HOLD) {
        ++s_gated;
        return;
    }
    int32_t span = r->polarity < 0 ? r->porch_khz - r->sync_khz : r->sync_khz - r->porch_khz;
    unsigned rejected = s_dev.rejected;
    int dev = dev_add(&s_dev, span);
    if (s_dev.rejected != rejected) printf("PROFILE batch rejected (spread or disagreement), count=%u\n", s_dev.rejected);
    if (dev >= 0 && mode == MEASURE_LIVE) { s_measuring = MEASURE_OFF; live_decided(dev); }
}

/* Store a deviation measured in this channel session (startup or manual
 * CALIBRATE) and select its profile through a TX-owned transition. */
void profile_commit(int dev_x100)
{
    size_t ch = rf_channel_index();
    if (dev_x100 <= 0 || dev_x100 > 400) return;
    if (s_dev_table[ch] != dev_x100) {
        s_dev_table[ch] = (uint16_t)dev_x100;
        (void)blob_store("vtxdev", s_dev_table, sizeof(s_dev_table));
    }
    s_live_dev = dev_x100;
    s_live_channel = ch;
    int p = profile_for_deviation(dev_x100);
    if (p == PROFILE_DEFAULT) {
        s_reason = dev_x100 >= DEV_WIDE_MIN ? "no_wide_filter" : dev_x100 > DEV_KP060_MAX ? "measured_default_vtx" :
                   "no_narrow_filter";
        if (s_profile != PROFILE_DEFAULT) {
            status_screen("VTX MEASURED", "DEFAULT PROFILE", profile_name(PROFILE_DEFAULT), 100);
            (void)profile_set(PROFILE_DEFAULT);
            vTaskDelay(pdMS_TO_TICKS(800));
            status_screen_end();
        }
    } else {
        char line[48];
        snprintf(line, sizeof(line), "VTX DEVIATION %d.%02d", dev_x100 / 100, dev_x100 % 100);
        status_screen("VTX MATCHED", line, profile_name(p), 100);
        s_reason = profile_set(p) ? "measured_this_boot" : "narrow_filter_not_applied";
        vTaskDelay(pdMS_TO_TICKS(1200));
        status_screen_end();
    }
    printf("PROFILE channel=%s deviation_x100=%d profile=%s reason=%s\n", rf_channel()->name, dev_x100,
           s_names[s_profile], s_reason);
}

void profile_set_reason(const char *reason) { s_reason = reason; }

void profile_print(void)
{
    size_t ch = rf_channel_index();
    int want = s_live_dev > 0 ? profile_for_deviation(s_live_dev) : -1;
    printf("PROFILE active=%s reason=%s measured_this_session_x100=%d pending=%s stored_x100=%u (provisional) "
           "measuring=%s windows=%u batches=%u rejected_batches=%u observed=%lu gated=%lu\n", s_names[s_profile],
           s_reason, s_live_channel == ch ? s_live_dev : -1,
           want < 0 || want == s_profile ? "none" : s_names[want], s_dev_table[ch],
           s_measuring == MEASURE_STARTUP ? "startup" : s_measuring == MEASURE_LIVE ? "live" : "off",
           s_dev.n, s_dev.batches, s_dev.rejected, (unsigned long)s_observed, (unsigned long)s_gated);
}

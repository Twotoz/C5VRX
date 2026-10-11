/* C5VRX by Twotoz/contributors: opt-in mathematical shadow, no actuators.
 * Uses one existing snapshot per second. All proposals are MODEL_ONLY:
 * the radius/noise grid is not a measurement of the unseen ADC bits.
 * Console prints outside control; J toggles, T reports. */
#include "receiver.h"
#include "lane_shadow_logic.h"
#include "esp_timer.h"
#include "phy.h"
#include <stdio.h>

static portMUX_TYPE s_lock = portMUX_INITIALIZER_UNLOCKED;
static iqf_shadow_t s_result = {.pending = IQF_CURRENT, .proposed = IQF_CURRENT};
static bool s_enabled, s_unread;
static uint64_t s_next_us;
static uint32_t s_toggle_epoch;

void lane_shadow_toggle(void)
{
    taskENTER_CRITICAL(&s_lock);
    s_enabled = !s_enabled;
    ++s_toggle_epoch;
    iqf_invalidate(&s_result);
    s_unread = false;
    bool enabled = s_enabled;
    taskEXIT_CRITICAL(&s_lock);
    printf("IQLANE_SHADOW enabled=%u actuator=none model=analytic_grid\n", enabled);
}

void lane_shadow_window(const uint8_t *raw, size_t bytes, uint32_t epoch,
    uint32_t gain_epoch, bool valid)
{
    uint64_t now = (uint64_t)esp_timer_get_time();
    taskENTER_CRITICAL(&s_lock);
    bool enabled = s_enabled;
    uint32_t toggle_epoch = s_toggle_epoch;
    iqf_shadow_t result = s_result;
    taskEXIT_CRITICAL(&s_lock);
    if (!enabled) return;
    valid = valid && !receiver_paused() && !s_menu_active && !s_scan_active &&
            !calibration_in_startup() && rf_lane() == RF_LANE_ULTRAFINE;
    /* Invalidate immediately, including between the one-second integrations. */
    if (result.epoch != epoch || result.gain_epoch != gain_epoch || !valid) {
        iqf_invalidate(&result);
        result.epoch = epoch;
        result.gain_epoch = gain_epoch;
    }
    if (valid && now >= s_next_us) {
        s_next_us = now + 1000000u;
        iqf_observe(&result, raw, bytes, now, epoch, gain_epoch, true);
        /* A gain write can race this passive computation. Discard the result. */
        if (s_gain_epoch != gain_epoch || s_menu_active || receiver_paused()) iqf_invalidate(&result);
    }
    taskENTER_CRITICAL(&s_lock);
    if (s_enabled && toggle_epoch == s_toggle_epoch) {
        s_result = result;
        s_unread = true;
    }
    taskEXIT_CRITICAL(&s_lock);
}

static void report(const iqf_shadow_t *r, bool enabled)
{
    bool evidence = r->valid && !receiver_paused() && !s_menu_active && !s_scan_active &&
                    !calibration_in_startup();
    printf("IQLANE_SHADOW enabled=%u status=%s proposed=%s ready=%u "
           "model_advantage_pm=%u confirmations=%u windows=%lu epoch=%lu gain_epoch=%lu "
           "actuator=none contexts=8 measured_context=0\n", enabled,
           !enabled ? "OFF" : !evidence ? "NO_EVIDENCE" : r->proposed == IQF_CURRENT ? "HOLD_OR_UNCERTAIN" : "MODEL_ONLY",
           iqf_names[evidence ? r->proposed : IQF_CURRENT], evidence && r->ready, r->advantage_pm, r->confirmations,
           (unsigned long)r->windows, (unsigned long)r->epoch, (unsigned long)r->gain_epoch);
}

void lane_shadow_print(void)
{
    taskENTER_CRITICAL(&s_lock);
    iqf_shadow_t result = s_result;
    bool enabled = s_enabled;
    taskEXIT_CRITICAL(&s_lock);
    report(&result, enabled);
}

void lane_shadow_poll(void)
{
    static uint64_t next;
    uint64_t now = (uint64_t)esp_timer_get_time();
    if (now < next) return;
    taskENTER_CRITICAL(&s_lock);
    bool unread = s_unread, enabled = s_enabled;
    iqf_shadow_t result = s_result;
    s_unread = false;
    taskEXIT_CRITICAL(&s_lock);
    if (unread) { next = now + 1000000u; report(&result, enabled); }
}

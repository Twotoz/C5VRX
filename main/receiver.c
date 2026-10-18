/* C5VRX-5 by Twotoz and the C5VRX contributors: Direct Gain V6 runtime.
 * A 200 us timer wakes sentinel/gain; full C/N runs every 20 ms with
 * context/age validation. Bound a continuously runnable observer batch so
 * IDLE/calibration get time even if notifications accumulate. */
#include "receiver.h"
#include <stdio.h>
#include <string.h>
#include "cn_estimate.h"
#include "cn_observation.h"
#include "esp_timer.h"
#include "gain_map_preset.h"
#include "phase8_gain_lut.h"
#include "phy.h"
#include "rf.h"

direct_gain_t s_gain;
volatile uint8_t s_current_gain = 62u;
volatile uint32_t s_gain_epoch, s_control_epoch;
volatile int64_t s_last_gain_write_us;
volatile int s_obs_p50, s_obs_p95, s_obs_origin_pm, s_obs_clip_pm, s_obs_coherence;
volatile bool s_obs_stuck;
volatile int s_obs_env_ratio_x100;
volatile uint32_t s_obs_us_max, s_obs_windows;
dg_map_blob_t s_gain_map_saved;
bool s_gain_map_saved_valid;

static TaskHandle_t s_observer, s_sentinel;
static volatile uint8_t s_gain_mode = GAIN_MODE_AUTO;

/* Menu/console cycle: AUTO, MAX, then fixed G78 down to G24. */
static const uint8_t s_gain_modes[] = {GAIN_MODE_AUTO, GAIN_MODE_MAX, 78, 72, 66, 60, 54, 48, 42, 36, 30, 24};

uint8_t receiver_gain_mode(void) { return s_gain_mode; }

void receiver_set_gain_mode(uint8_t mode, bool save)
{
    if (mode != GAIN_MODE_AUTO && mode != GAIN_MODE_MAX && (mode < 20u || mode > ARC_VENDOR_GAIN_MAX)) return;
    s_gain_mode = mode;
    if (save) settings_save_gain_mode(mode);
    char buf[16];
    printf("GAIN mode=%s %s\n", receiver_gain_mode_name(mode, buf, sizeof(buf)), save ? "saved=1" : "boot_restore=saved");
}

uint8_t receiver_gain_mode_next(uint8_t mode)
{
    for (unsigned k = 0; k < sizeof(s_gain_modes); ++k)
        if (s_gain_modes[k] == mode) return s_gain_modes[(k + 1u) % sizeof(s_gain_modes)];
    return GAIN_MODE_AUTO;
}

const char *receiver_gain_mode_name(uint8_t mode, char *buf, unsigned size)
{
    if (mode == GAIN_MODE_AUTO) return "AUTO";
    if (mode == GAIN_MODE_MAX) return "MAX";
    snprintf(buf, size, "G%u", mode);
    return buf;
}

/* Fixed modes bypass the controller; apply_target clamps to the live table. */
static uint8_t gain_decision(const dg_observation_t *o)
{
    const uint8_t mode = s_gain_mode;
    if (mode == GAIN_MODE_AUTO) return dg_tick(&s_gain, o);
    const uint8_t target = mode == GAIN_MODE_MAX ? rf_gain_table()->max_index : mode;
    /* Once written and settled the model reads HOLD, so profile/DC/C-N gates
     * waiting for a held gain behave as with a steady carrier. */
    if (target == s_current_gain && s_gain.state != DG_HOLD &&
        o->observed_us > (uint64_t)s_last_gain_write_us && o->observed_us - (uint64_t)s_last_gain_write_us >= 2000u)
        s_gain.state = DG_HOLD;
    return target;
}
static volatile bool s_paused;
static volatile uint32_t s_overload_state;   /* 0 free, 1 writing, 2 published */
static dg_observation_t s_overload;
static uint32_t s_overload_gain_epoch, s_overload_control_epoch, s_overload_phy;
static uint32_t s_map_imports, s_preset_imports;
static bool s_map_bound;
static uint32_t s_map_control, s_map_phy, s_map_table;
extern unsigned char phy_param[];

static dg_map_context_t map_context(void)
{
    const rf_filter_t f = rf_filter();
    return (dg_map_context_t){.freq_mhz=rf_frequency_mhz(), .lane_mode=rf_lane(),
        .iq_scale=phy_param[650], .filter_skirt=f.skirt, .filter_bw20=f.bw20,
        .offset_khz=rf_offset_khz(), .filter_code=f.code};
}

/* No vendor calls or waits here. A nonblocking actuator lease rejects even
 * phy_try_actuator owners (which do not set phy_busy); short task exclusion
 * protects the observer's ~700-byte snapshot. Context stamping belongs to
 * that observer, never to a later calibration task. */
unsigned receiver_export_gain_map(dg_map_blob_t *blob)
{
    if (!blob) return 0;
    if (!phy_try_actuator(phy_generation())) { memset(blob, 0, sizeof(*blob)); return 0; }
    vTaskSuspendAll();
    const uint32_t control=s_control_epoch, phy=phy_generation(), table=rf_table_generation();
    const uint32_t gain_epoch=s_gain_epoch;
    const dg_map_context_t context=map_context();
    unsigned n=0;
    if (s_map_bound && receiver_active() && s_gain.state == DG_HOLD &&
        cn_gain_settled((uint64_t)esp_timer_get_time(), (uint64_t)s_last_gain_write_us, false) &&
        s_gain.current_gain == s_current_gain &&
        control == s_map_control && phy == s_map_phy && table == s_map_table &&
        dg_map_matches(&s_gain, rf_gain_table(), &context))
        n=dg_export_map(&s_gain, blob);
    const dg_map_context_t after=map_context();
    if (control != s_control_epoch || gain_epoch != s_gain_epoch || phy != phy_generation() || table != rf_table_generation() ||
        !dg_map_context_equal(&context, &after)) n=0;
    phy_end_actuator();
    (void)xTaskResumeAll();
    if (!n) memset(blob, 0, sizeof(*blob));
    return n;
}

static void gain_map_bind(const dg_map_context_t *context, uint32_t control, uint32_t phy, uint32_t table)
{
    dg_reset_context(&s_gain, rf_gain_table(), s_current_gain, context);
    /* Every import checks the current transfer context and complete table,
     * including after A -> B -> A. A stored map for A remains available
     * while B runs but can never be relabelled/imported as B. */
    if (!s_gain.learned && s_gain_map_saved_valid && dg_import_map(&s_gain, &s_gain_map_saved)) ++s_map_imports;
    else if (!s_gain.learned && GAIN_MAP_PRESET_BOARDS &&
             dg_import_preset(&s_gain, c5vrx5_gain_map_preset_q10, GAIN_MAP_PRESET_MAX_INDEX + 1u,
                              GAIN_MAP_PRESET_MAX_INDEX, GAIN_MAP_PRESET_BAND5)) ++s_preset_imports;
    s_map_control=control; s_map_phy=phy; s_map_table=table; s_map_bound=true;
}
static volatile cn_observation_t s_cn;
static volatile uint32_t s_cn_sequence;
static uint32_t s_cn_windows;

bool receiver_active(void)
{
    return !s_paused && !s_menu_active && !phy_busy();
}

bool receiver_paused(void) { return s_paused; }

bool receiver_pause(const char *tag)
{
    if (s_paused) {
        printf("%s refused=busy\n", tag);
        return false;
    }
    s_paused = true;
    vTaskDelay(pdMS_TO_TICKS(20));   /* let an in-flight window finish */
    return true;
}

void receiver_resume(void)
{
    ++s_control_epoch;
    s_paused = false;
}

/* Gain write by the owner of a pause (calibration, scan). */
void receiver_set_gain(uint8_t gain)
{
    const arc_gain_table_t *t = rf_gain_table();
    if (gain < 2u) gain = 2u;
    if (gain > t->max_index) gain = t->max_index;
    rf_force_gain(gain);
    s_current_gain = gain;
    s_last_gain_write_us = esp_timer_get_time();
    ++s_gain_epoch;
}

static void apply_target(uint8_t target, uint32_t control, uint32_t phy)
{
    if (phy != phy_generation() || phy_busy() || control != s_control_epoch || !receiver_active()) return;
    if (target == s_current_gain) return;
    const arc_gain_table_t *t = rf_gain_table();
    if (target < 2u) target = 2u;
    if (target > t->max_index) target = t->max_index;
    /* A task preemption between the first check and the write must not let
     * another PHY owner change the analog state underneath this write. */
    if (!phy_try_actuator(phy)) return;
    if (control != s_control_epoch || !receiver_active()) {
        phy_end_actuator();
        return;
    }
    /* A busy or changed PHY must not leave software ahead of hardware. */
    if (!rf_set_gain(target, phy)) {
        dg_sync_applied(&s_gain, s_current_gain, (uint64_t)esp_timer_get_time());
        phy_end_actuator();
        return;
    }
    s_current_gain = target;
    s_last_gain_write_us = esp_timer_get_time();
    ++s_gain_epoch;
    dg_sync_applied(&s_gain, target, (uint64_t)s_last_gain_write_us);
    phy_end_actuator();
}

int receiver_obs_cn_x10(void)
{
    /* Envelope model (cn_estimate.h); a fixed IQ point has no envelope to
     * judge and read 14-20 dB in the coherence model with no VTX at all. */
    uint32_t seq = s_cn_sequence;
    __sync_synchronize();
    cn_observation_t o = s_cn;
    __sync_synchronize();
    if ((seq & 1u) || seq != s_cn_sequence || s_obs_stuck || !receiver_active() ||
        !cn_gain_settled((uint64_t)esp_timer_get_time(), (uint64_t)s_last_gain_write_us,
                         s_gain.state == DG_SETTLE) ||
        !cn_observation_fresh(&o, (uint64_t)esp_timer_get_time(), s_gain_epoch, s_control_epoch,
                             phy_generation(), rf_table_generation())) return CN_UNKNOWN_X10;
    return cn_estimate_env_x10(o.ratio, calibration_filter_class_mhz());
}

static void tick_cb(void *arg)
{
    (void)arg;
    if (s_sentinel) xTaskNotifyGive(s_sentinel);
    if (s_observer) xTaskNotifyGive(s_observer);
}

/* 64 bytes from the middle of the newest completed descriptor every 200 us:
 * a clipped envelope requests the observer's emergency path at once. */
static void sentinel_task(void *arg)
{
    (void)arg;
    uint8_t sample[64];
    for (;;) {
        (void)ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
        if (!receiver_active() || s_overload_state || s_rx_dma_ch < 0 || s_rx_node_count < 2) continue;
        uint32_t gain_epoch = s_gain_epoch, control = s_control_epoch, phy = phy_generation();
        if (!copy_sentinel_window(sample) || gain_epoch != s_gain_epoch) continue;
        dg_observation_t o = dg_measure_gain(sample, sizeof(sample), c5vrx_phase8_gain_lut,
                                        (uint64_t)esp_timer_get_time());
        if (o.clip_pm < 125u && o.p95 < 95u) continue;
        if (control != s_control_epoch || phy != phy_generation() || phy_busy()) continue;
        if (!__sync_bool_compare_and_swap(&s_overload_state, 0u, 1u)) continue;
        s_overload = o;
        s_overload_gain_epoch = gain_epoch;
        s_overload_control_epoch = control;
        s_overload_phy = phy;
        __sync_synchronize();
        __sync_lock_test_and_set(&s_overload_state, 2u);
        if (s_observer) xTaskNotifyGive(s_observer);
    }
}

static void observer_task(void *arg)
{
    (void)arg;
    uint8_t sample[PROBE_BYTES];
    uint32_t seen_control = UINT32_MAX, seen_table = UINT32_MAX, seen_phy = UINT32_MAX;
    bool was_active = false;
    int last_node = -1;
    int64_t runnable_since = esp_timer_get_time();
    for (;;) {
        int64_t before_wait = esp_timer_get_time();
        if (before_wait - runnable_since >= 800) {
            /* taskYIELD cannot let priority-0 IDLE run. A real block can.
             * Do not disable/feed its watchdog to hide CPU saturation. */
            vTaskDelay(1);
            before_wait = runnable_since = esp_timer_get_time();
        }
        (void)ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(1));
        /* IRQ time can make an already-pending notification look like a
         * blocked wait. Only the explicit delay above resets our runnable
         * budget, so periodic sync IRQs cannot suppress the IDLE opportunity. */
        if (!receiver_active()) {
            was_active = false;
            if (__sync_bool_compare_and_swap(&s_overload_state, 2u, 1u))
                __sync_lock_release(&s_overload_state);
            continue;
        }
        uint32_t control = s_control_epoch, table = rf_table_generation(), phy = phy_generation();
        const dg_map_context_t context = map_context();
        if (s_paused) continue;
        if (!was_active || control != seen_control || table != seen_table || phy != seen_phy ||
            s_gain.current_gain != s_current_gain || !dg_map_matches(&s_gain, rf_gain_table(), &context)) {
            gain_map_bind(&context, control, phy, table);
            seen_control = control;
            seen_table = table;
            seen_phy = phy;
            was_active = true;
        }
        if (__sync_bool_compare_and_swap(&s_overload_state, 2u, 1u)) {
            __sync_synchronize();
            dg_observation_t o = s_overload;
            bool current = s_overload_gain_epoch == s_gain_epoch &&
                           s_overload_control_epoch == control && s_overload_phy == phy &&
                           (uint64_t)esp_timer_get_time() - o.observed_us <= 1000u;
            __sync_lock_release(&s_overload_state);
            if (current) apply_target(gain_decision(&o), control, phy);
            continue;
        }
        uint32_t gain_epoch = s_gain_epoch;
        int node = -1;
        if (!copy_probe_window(sample, &node) || gain_epoch != s_gain_epoch) continue;
        /* Never the same descriptor twice: settling counts fresh windows. */
        if (node == last_node) continue;
        last_node = node;
        const int64_t t0 = esp_timer_get_time();
        cn_observation_t previous_cn = s_cn; /* sole writer: observer */
        bool measure_cn = cn_gain_settled((uint64_t)t0, (uint64_t)s_last_gain_write_us,
                                         s_gain.state == DG_SETTLE) &&
                         (!cn_observation_fresh(&previous_cn, (uint64_t)t0, gain_epoch, control, phy, table) ||
                          (uint64_t)t0 - previous_cn.us >= CN_OBSERVE_PERIOD_US);
        dg_observation_t o = measure_cn ? dg_measure(sample, sizeof(sample), c5vrx_phase8_gain_lut, (uint64_t)t0) :
                                         dg_measure_gain(sample, sizeof(sample), c5vrx_phase8_gain_lut, (uint64_t)t0);
        s_obs_p50 = o.p50;
        s_obs_p95 = o.p95;
        s_obs_origin_pm = o.origin_pm;
        s_obs_clip_pm = o.clip_pm;
        s_obs_coherence = o.coherence;
        s_obs_stuck = dg_stuck(&o);
        const dg_map_context_t after_context = map_context();
        if (phy != phy_generation() || phy_busy() || gain_epoch != s_gain_epoch || control != s_control_epoch ||
            table != rf_table_generation() || !dg_map_context_equal(&context, &after_context)) continue;
        if (measure_cn) {
            ++s_cn_sequence;
            __sync_synchronize();
            s_cn = (cn_observation_t){gain_epoch, control, phy, table, (uint64_t)t0, o.env_ratio_x100};
            s_obs_env_ratio_x100 = o.env_ratio_x100;
            __sync_synchronize();
            ++s_cn_sequence;
            ++s_cn_windows;
        }
        /* Learn the gain map only from a real VTX: burst-confirmed sync seen
         * by the AFC within the last 500 ms (never from receiver noise). */
        const int64_t video = s_last_video_us;
        s_gain.learn_enabled = video != 0 && t0 - video < 500000;
        apply_target(gain_decision(&o), control, phy);
        uint32_t us = (uint32_t)(esp_timer_get_time() - t0);
        if (us > s_obs_us_max) s_obs_us_max = us;
        ++s_obs_windows;
    }
}

void receiver_start(void)
{
    s_gain_map_saved_valid = blob_load("gain_map", &s_gain_map_saved, sizeof(s_gain_map_saved)) &&
                             s_gain_map_saved.version == DG_MAP_VERSION && s_gain_map_saved.context_valid;
    ESP_ERROR_CHECK(xTaskCreate(observer_task, "gain_obs", 4096, NULL, 3, &s_observer) == pdPASS ?
                    ESP_OK : ESP_ERR_NO_MEM);
    ESP_ERROR_CHECK(xTaskCreate(sentinel_task, "gain_fast", 3072, NULL, 4, &s_sentinel) == pdPASS ?
                    ESP_OK : ESP_ERR_NO_MEM);
    /* Notifications request a fresh snapshot, not replay of missed samples. */
    const esp_timer_create_args_t args = {.callback = tick_cb, .dispatch_method = ESP_TIMER_TASK,
        .name = "gain_tick", .skip_unhandled_events = true};
    esp_timer_handle_t timer;
    ESP_ERROR_CHECK(esp_timer_create(&args, &timer));
    ESP_ERROR_CHECK(esp_timer_start_periodic(timer, 200));
}

void receiver_print(void)
{
    printf("CN_OBSERVER windows=%lu period_us=%u max_age_us=%u\n", (unsigned long)s_cn_windows,
           CN_OBSERVE_PERIOD_US, CN_OBSERVE_MAX_AGE_US);
    char mode[16];
    printf("GAIN owner=%s mode=%s gain=%u state=%u p50=%d p95=%d coherence=%d env_x100=%d stuck=%u cn_x10=%d origin_pm=%d clip_pm=%d "
           "anchor=%u anchor_valid=%u anchor_returns=%lu climbs=%lu "
           "writes=%lu holds=%lu overloads=%lu bursts=%lu fade_holds=%lu damp=%lu map_learned=%lu learn_refused=%lu "
           "map_imports=%lu preset_imports=%lu "
           "obs_windows=%lu obs_us_max=%lu\n",
           s_paused ? "paused" :
           s_gain_mode == GAIN_MODE_AUTO ? "direct_gain_v6" : "manual",
           receiver_gain_mode_name(s_gain_mode, mode, sizeof(mode)),
           s_current_gain, (unsigned)s_gain.state, s_obs_p50, s_obs_p95, s_obs_coherence, s_obs_env_ratio_x100,
           (unsigned)s_obs_stuck, receiver_obs_cn_x10(),
           s_obs_origin_pm, s_obs_clip_pm, (unsigned)s_gain.anchor_gain, (unsigned)s_gain.anchor_valid,
           (unsigned long)s_gain.anchor_returns, (unsigned long)s_gain.climbs, (unsigned long)s_gain.writes, (unsigned long)s_gain.holds,
           (unsigned long)s_gain.overloads, (unsigned long)s_gain.bursts_ignored, (unsigned long)s_gain.fade_holds,
           (unsigned long)s_gain.damp_events, (unsigned long)s_gain.learned, (unsigned long)s_gain.learn_refused,
           (unsigned long)s_map_imports, (unsigned long)s_preset_imports, (unsigned long)s_obs_windows,
           (unsigned long)s_obs_us_max);
}

/* Console M: the learned map on one line, for gain_map_preset.py. */
void receiver_print_map(void)
{
    dg_map_blob_t blob;
    unsigned n = receiver_export_gain_map(&blob);
    if (!n) { printf("GAINMAP refused=unsettled_or_changed_context\n"); return; }
    printf("GAINMAP v=%u max=%u band5=%u freq=%u entries=%u", DG_MAP_VERSION, blob.max_index, blob.band5,
           rf_frequency_mhz(), n);
    for (unsigned k = 0; k <= blob.max_index && k < DG_STATES; ++k)
        if (blob.confidence[k] && blob.power_q10[k])
            printf(" %u:%u:%u:%u", k, blob.power_q10[k], blob.confidence[k], blob.uncertainty_pm[k]);
    printf("\n");
}


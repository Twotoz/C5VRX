/* C5VRX-5 by Twotoz and the C5VRX contributors: line TBC runtime.
 *
 * Runs the pure sync_tbc.h on the live raw IQ ring, between the RX DMA
 * (which has completed the data) and the TX DMA (which has not read it
 * yet, ~600 us later). A GPTimer wakes the task every 200 us; every wake
 * re-reads both DMA positions, so no CPU or interrupt time ever places an
 * output edge: edges are sample positions in the clock-locked stream.
 * Default ON (menu TBC, console W, NVS "tbc"); off restores pure
 * pass-through at once. It never runs while the menu, a status screen,
 * calibration, a scan or a paused receiver owns the ring. */
#include "receiver.h"
#include "sync_tbc.h"
#include "phy.h"
#include "phase8_gain_lut.h"
#include "driver/gptimer.h"
#include "esp_attr.h"
#include "esp_cpu.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include <stdio.h>

#define TBC_WAKE_US        200u
#define TBC_LINES_PER_WAKE 6u
#define TBC_TX_MARGIN      (4092 + 4096)   /* TX descriptor in flight + its progress during a wake */
#define TBC_CPU_LIMIT_PERMILLE 300u        /* above this for 3 s: switch off and say so */
#define TBC_ACQUIRE_GAP_US 20000            /* an acquisition scan (~0.6 ms) at most every 20 ms */

static tbc_tables_t s_tables;
static tbc_t s_state;
static TaskHandle_t s_task;
static gptimer_handle_t s_timer;
static volatile bool s_on = true, s_cpu_tripped;
static bool s_started_task, s_available;
static volatile uint32_t s_reset_seq;

/* Reference levels from the control task (AFC, burst/line-timing confirmed). */
typedef struct {
    int32_t sync_khz, porch_khz;
    uint8_t standard;
    uint32_t seq, phy, control, gain;
    int64_t measured_us;
} tbc_reference_t;
static tbc_reference_t s_ref;
static portMUX_TYPE s_ref_mux = portMUX_INITIALIZER_UNLOCKED;
static tbc_reference_t reference_snapshot(void)
{
    portENTER_CRITICAL(&s_ref_mux);
    tbc_reference_t ref = s_ref;
    portEXIT_CRITICAL(&s_ref_mux);
    return ref;
}
/* A confirmed AFC window expires after 500 ms and cannot cross a receive
 * context. Volatile fields alone would not make this multiword tuple atomic. */
static bool reference_current(const tbc_reference_t *ref, int64_t now)
{
    return ref->seq && ref->standard && now >= ref->measured_us &&
        now - ref->measured_us <= 500000 && ref->phy == phy_generation() &&
        ref->control == s_control_epoch && ref->gain == s_gain_epoch;
}

/* Absolute RX position across ring wraps, reconciled with elapsed time so a
 * long CPU blackout cannot lose a wrap. */
static struct {
    bool valid;
    uint32_t generation;
    int64_t abs, us;
} s_pos;

static struct {
    uint64_t busy_cycles, window_cycles0;
    int64_t window_us;
    unsigned over_s;
    uint32_t permille, wakes, idle_wakes;
    uint8_t standard;
} s_cpu;

static int64_t reconcile(int64_t predicted, uint32_t offset)
{
    const int64_t ring = RAW_RING_BYTES;
    int64_t k = (predicted - (int64_t)offset + ring / 2) / ring;
    if (predicted - (int64_t)offset + ring / 2 < 0) k -= 1;
    return (int64_t)offset + k * ring;
}

static bool IRAM_ATTR on_alarm(gptimer_handle_t t, const gptimer_alarm_event_data_t *e, void *ctx)
{
    (void)t; (void)e; (void)ctx;
    BaseType_t woken = pdFALSE;
    if (s_task) vTaskNotifyGiveFromISR(s_task, &woken);
    return woken == pdTRUE;
}

static void reset_tracking(int standard)
{
    const int32_t nominal = standard == 2 ? (int32_t)(2560 * 65536) : (int32_t)(2542.2216 * 65536.0 + 0.5);
    tbc_t keep = s_state;
    tbc_reset(&s_state, nominal);
    /* Counters survive resets (T reports a session total). */
    s_state.lines = keep.lines; s_state.measured = keep.measured; s_state.rewritten = keep.rewritten;
    s_state.refused_late = keep.refused_late; s_state.refused_shape = keep.refused_shape;
    s_state.locks = keep.locks; s_state.unlocks = keep.unlocks + keep.locked; s_state.acquisitions = keep.acquisitions;
    s_cpu.standard = (uint8_t)standard;
}

typedef struct { uint32_t generation; tbc_reference_t ref; } tbc_write_context_t;

/* Acquisition/measurement can outlast a wake interval. Never trust the old
 * TX deadline: validate the live DMA positions again immediately before the
 * short store loop. The descriptor margin remains conservative, not a C5
 * execution-time proof. No output edge is paced by this callback. */
static bool write_allowed(void *arg, int64_t first, int64_t end)
{
    const tbc_write_context_t *ctx = arg;
    uint32_t rx, tx, gen;
    const int64_t now = esp_timer_get_time();
    if (!s_on || s_cpu_tripped || receiver_paused() || calibration_in_startup() ||
        s_scan_active || phy_busy() || !reference_current(&ctx->ref, now) ||
        !transport_ring_view(&rx, &tx, &gen) || gen != ctx->generation) return false;
    const int64_t rx_abs = reconcile(s_pos.abs + (now - s_pos.us) * (IQ_RATE_HZ / 1000000), rx);
    const int64_t tx_abs = reconcile(rx_abs - LIVE_PIPELINE_LAG_BYTES, tx);
    return first >= tx_abs + TBC_TX_MARGIN && first - 32 >= rx_abs - RAW_RING_BYTES + 4096 && end + 8 <= rx_abs;
}

static void tbc_task(void *arg)
{
    (void)arg;
    uint32_t seen_ref = 0, seen_reset = 0;
    tbc_reference_t previous_ref = {0};
    for (;;) {
        ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(10));
        ++s_cpu.wakes;
        if (seen_reset != s_reset_seq) {
            seen_reset = s_reset_seq; seen_ref = 0; s_pos.valid = false;
            reset_tracking(s_cpu.standard);
        }
        const uint32_t c0 = esp_cpu_get_cycle_count();
        const int64_t now = esp_timer_get_time();
        uint32_t rx, tx, gen;
        const tbc_reference_t ref = reference_snapshot();
        const bool run = s_available && s_on && !s_cpu_tripped && !phy_busy() && reference_current(&ref, now) &&
                         !calibration_in_startup() && !receiver_paused() && !s_scan_active &&
                         transport_ring_view(&rx, &tx, &gen);
        if (!run) {
            if (s_state.have || s_state.ref) reset_tracking(s_cpu.standard);
            seen_ref = 0;
            s_pos.valid = false;
            ++s_cpu.idle_wakes;
        } else {
            if (!s_pos.valid || s_pos.generation != gen) {
                s_pos.valid = true;
                s_pos.generation = gen;
                s_pos.abs = rx;
                s_pos.us = now;
                reset_tracking(s_cpu.standard);
                seen_ref = 0;
            } else {
                s_pos.abs = reconcile(s_pos.abs + (now - s_pos.us) * (IQ_RATE_HZ / 1000000), rx);
                s_pos.us = now;
            }
            if (ref.seq != seen_ref) {
                seen_ref = ref.seq;
                const int std = ref.standard;
                if (std != s_cpu.standard || ref.phy != previous_ref.phy ||
                    ref.control != previous_ref.control || ref.gain != previous_ref.gain)
                    reset_tracking(std);
                previous_ref = ref;
                tbc_set_reference(&s_state, ref.sync_khz, ref.porch_khz);
            }
            static int64_t last_acquire_us;
            const bool may_acquire = now - last_acquire_us >= TBC_ACQUIRE_GAP_US;
            if (s_cpu.standard && (s_state.have || may_acquire)) {
                if (!s_state.have) last_acquire_us = now;
                const int64_t tx_abs = reconcile(s_pos.abs - LIVE_PIPELINE_LAG_BYTES, tx);
                tbc_write_context_t context = {.generation = gen, .ref = ref};
                tbc_ring_t r = {.ring = s_raw_ring, .mask = RAW_RING_BYTES - 1u, .complete = s_pos.abs,
                                .write_min = tx_abs + TBC_TX_MARGIN, .can_write = write_allowed,
                                .write_ctx = &context};
                tbc_run(&s_tables, &s_state, &r, TBC_LINES_PER_WAKE);
            }
        }
        s_cpu.busy_cycles += (uint32_t)(esp_cpu_get_cycle_count() - c0);
        if (now - s_cpu.window_us >= 1000000) {
            const uint64_t span = (uint64_t)(now - s_cpu.window_us) * 240u;   /* 240 MHz */
            s_cpu.permille = s_cpu.window_us ? (uint32_t)(s_cpu.busy_cycles * 1000u / span) : 0u;
            s_cpu.busy_cycles = 0;
            s_cpu.window_us = now;
            s_cpu.over_s = s_cpu.permille > TBC_CPU_LIMIT_PERMILLE ? s_cpu.over_s + 1u : 0u;
            if (s_cpu.over_s >= 3u && !s_cpu_tripped) {
                s_cpu_tripped = true;
                printf("TBC off: CPU %lu permille for 3 s (limit %u); pass-through until W or reboot\n",
                       (unsigned long)s_cpu.permille, TBC_CPU_LIMIT_PERMILLE);
            }
        }
    }
}

void sync_tbc_start(void)
{
    if (s_started_task) return;
    tbc_tables_init(&s_tables, c5vrx_phase8_gain_lut);
    reset_tracking(0);
    if (xTaskCreate(tbc_task, "tbc", 6144, NULL, 5, &s_task) != pdPASS) {
        s_on = false;
        printf("TBC unavailable: no memory for its task; pass-through\n");
        return;
    }
    const gptimer_config_t cfg = {.clk_src = GPTIMER_CLK_SRC_DEFAULT, .direction = GPTIMER_COUNT_UP,
                                  .resolution_hz = 1000000};
    const gptimer_event_callbacks_t cbs = {.on_alarm = on_alarm};
    const gptimer_alarm_config_t alarm = {.alarm_count = TBC_WAKE_US, .reload_count = 0,
                                          .flags.auto_reload_on_alarm = true};
    esp_err_t err = gptimer_new_timer(&cfg, &s_timer);
    bool enabled = false;
    if (err == ESP_OK) err = gptimer_register_event_callbacks(s_timer, &cbs, NULL);
    if (err == ESP_OK) err = gptimer_set_alarm_action(s_timer, &alarm);
    if (err == ESP_OK) { err = gptimer_enable(s_timer); enabled = err == ESP_OK; }
    if (err == ESP_OK) err = gptimer_start(s_timer);
    s_available = err == ESP_OK;
    if (!s_available) {
        s_on = false;
        if (enabled) (void)gptimer_disable(s_timer);
        if (s_timer) (void)gptimer_del_timer(s_timer);
        s_timer = NULL;
        vTaskDelete(s_task); s_task = NULL;
        printf("TBC unavailable: timer start failed; pass-through\n");
    }
    s_started_task = true;
}

void sync_tbc_reference(int32_t sync_khz, int32_t porch_khz, int standard,
                        uint32_t phy, uint32_t control, uint32_t gain)
{
    /* Preserve the measurement's context, not the context after a gain task
     * preempted its publication. Called before profile_observe can retune. */
    if (phy != phy_generation() || control != s_control_epoch || gain != s_gain_epoch ||
        (standard != 1 && standard != 2)) return;
    const int64_t now = esp_timer_get_time();
    portENTER_CRITICAL(&s_ref_mux);
    if (control != s_control_epoch || gain != s_gain_epoch) {
        portEXIT_CRITICAL(&s_ref_mux); return;
    }
    if (!s_ref.seq || standard != s_ref.standard || phy != s_ref.phy ||
        control != s_ref.control || gain != s_ref.gain) {
        s_ref.sync_khz = sync_khz;
        s_ref.porch_khz = porch_khz;
    } else {
        s_ref.sync_khz += (sync_khz - s_ref.sync_khz) / 8;
        s_ref.porch_khz += (porch_khz - s_ref.porch_khz) / 8;
    }
    s_ref.standard = (uint8_t)standard;
    s_ref.phy = phy; s_ref.control = control; s_ref.gain = gain; s_ref.measured_us = now;
    if (++s_ref.seq == 0) ++s_ref.seq;
    portEXIT_CRITICAL(&s_ref_mux);
}

bool sync_tbc_enabled(void) { return s_on; }
void sync_tbc_set_boot(bool on) { s_on = on; ++s_reset_seq; }
void sync_tbc_set(bool on, bool save)
{
    if (on && !s_available) {
        printf("TBC unavailable: task/timer initialization failed; pass-through\n");
        return;
    }
    s_on = on;
    ++s_reset_seq;
    s_cpu_tripped = false;
    if (save) settings_save_tbc(on);
    printf("TBC %s: sample-domain line TBC for HDZero and other decoders without a time-base corrector\n",
           on ? "ON" : "OFF");
}

void sync_tbc_print(void)
{
    const tbc_t s = s_state;
    const tbc_reference_t ref = reference_snapshot();
    printf("TBC on=%u running=%u locked=%u standard=%s period_x1000=%lld lines=%lu measured=%lu rewritten=%lu "
           "refused_late=%lu refused_shape=%lu locks=%lu unlocks=%lu acquisitions=%lu last_err_ns=%ld "
           "ref_sync_khz=%ld ref_porch_khz=%ld cpu_permille=%lu cpu_tripped=%u wakes=%lu idle_wakes=%lu\n",
           (unsigned)s_on, (unsigned)s_pos.valid, (unsigned)s.locked,
           s_cpu.standard == 2 ? "PAL" : s_cpu.standard == 1 ? "NTSC" : "none",
           (long long)s.period_q16 * 1000 / 65536, (unsigned long)s.lines, (unsigned long)s.measured,
           (unsigned long)s.rewritten, (unsigned long)s.refused_late, (unsigned long)s.refused_shape,
           (unsigned long)s.locks, (unsigned long)s.unlocks, (unsigned long)s.acquisitions,
           (long)(s.last_err_q8 * 25 / 256), (long)ref.sync_khz, (long)ref.porch_khz,
           (unsigned long)s_cpu.permille, (unsigned)s_cpu_tripped, (unsigned long)s_cpu.wakes,
           (unsigned long)s_cpu.idle_wakes);
}

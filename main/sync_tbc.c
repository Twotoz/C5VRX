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
static bool s_started_task;

/* Reference levels from the control task (AFC, burst/line-timing confirmed). */
static volatile struct {
    int32_t sync_khz, porch_khz;
    uint8_t standard;
    uint32_t seq;
} s_ref;

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

static void tbc_task(void *arg)
{
    (void)arg;
    uint32_t seen_ref = 0;
    for (;;) {
        ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(10));
        ++s_cpu.wakes;
        const uint32_t c0 = esp_cpu_get_cycle_count();
        const int64_t now = esp_timer_get_time();
        uint32_t rx, tx, gen;
        const bool run = s_on && !s_cpu_tripped && !calibration_in_startup() && !receiver_paused() && !s_scan_active &&
                         transport_ring_view(&rx, &tx, &gen);
        if (!run) {
            if (s_state.have) reset_tracking(s_cpu.standard);
            s_pos.valid = false;
            ++s_cpu.idle_wakes;
        } else {
            if (!s_pos.valid || s_pos.generation != gen) {
                s_pos.valid = true;
                s_pos.generation = gen;
                s_pos.abs = rx;
                s_pos.us = now;
                reset_tracking(s_cpu.standard);
            } else {
                s_pos.abs = reconcile(s_pos.abs + (now - s_pos.us) * (IQ_RATE_HZ / 1000000), rx);
                s_pos.us = now;
            }
            if (s_ref.seq != seen_ref) {
                seen_ref = s_ref.seq;
                const int std = s_ref.standard;
                if (std && std != s_cpu.standard) reset_tracking(std);
                tbc_set_reference(&s_state, s_ref.sync_khz, s_ref.porch_khz);
            }
            static int64_t last_acquire_us;
            const bool may_acquire = now - last_acquire_us >= TBC_ACQUIRE_GAP_US;
            if (s_cpu.standard && (s_state.have || may_acquire)) {
                if (!s_state.have) last_acquire_us = now;
                const int64_t tx_abs = reconcile(s_pos.abs - LIVE_PIPELINE_LAG_BYTES, tx);
                tbc_ring_t r = {.ring = s_raw_ring, .mask = RAW_RING_BYTES - 1u, .complete = s_pos.abs,
                                .write_min = tx_abs + TBC_TX_MARGIN};
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
        printf("TBC unavailable: no memory for its task; pass-through\n");
        return;
    }
    const gptimer_config_t cfg = {.clk_src = GPTIMER_CLK_SRC_DEFAULT, .direction = GPTIMER_COUNT_UP,
                                  .resolution_hz = 1000000};
    const gptimer_event_callbacks_t cbs = {.on_alarm = on_alarm};
    const gptimer_alarm_config_t alarm = {.alarm_count = TBC_WAKE_US, .reload_count = 0,
                                          .flags.auto_reload_on_alarm = true};
    if (gptimer_new_timer(&cfg, &s_timer) != ESP_OK || gptimer_register_event_callbacks(s_timer, &cbs, NULL) != ESP_OK ||
        gptimer_set_alarm_action(s_timer, &alarm) != ESP_OK || gptimer_enable(s_timer) != ESP_OK ||
        gptimer_start(s_timer) != ESP_OK) {
        /* The task still runs on its 10 ms timeout, but cannot keep ahead of
         * TX that way: stay off rather than half-work. */
        s_on = false;
        printf("TBC unavailable: timer start failed; pass-through\n");
    }
    s_started_task = true;
}

void sync_tbc_reference(int32_t sync_khz, int32_t porch_khz, int standard)
{
    /* Smoothed: one window carries one or two lines. */
    if (!s_ref.seq || standard != s_ref.standard) {
        s_ref.sync_khz = sync_khz;
        s_ref.porch_khz = porch_khz;
    } else {
        s_ref.sync_khz += (sync_khz - s_ref.sync_khz) / 8;
        s_ref.porch_khz += (porch_khz - s_ref.porch_khz) / 8;
    }
    s_ref.standard = (uint8_t)standard;
    ++s_ref.seq;
}

bool sync_tbc_enabled(void) { return s_on; }
void sync_tbc_set_boot(bool on) { s_on = on; }
void sync_tbc_set(bool on, bool save)
{
    s_on = on;
    s_cpu_tripped = false;
    if (save) settings_save_tbc(on);
    printf("TBC %s (saved): sample-domain line TBC for HDZero and other decoders without a time-base corrector\n",
           on ? "ON" : "OFF");
}

void sync_tbc_print(void)
{
    const tbc_t s = s_state;
    printf("TBC on=%u running=%u locked=%u standard=%s period_x1000=%lld lines=%lu measured=%lu rewritten=%lu "
           "refused_late=%lu refused_shape=%lu locks=%lu unlocks=%lu acquisitions=%lu last_err_ns=%ld "
           "ref_sync_khz=%ld ref_porch_khz=%ld cpu_permille=%lu cpu_tripped=%u wakes=%lu idle_wakes=%lu\n",
           (unsigned)s_on, (unsigned)s_pos.valid, (unsigned)s.locked,
           s_cpu.standard == 2 ? "PAL" : s_cpu.standard == 1 ? "NTSC" : "none",
           (long long)s.period_q16 * 1000 / 65536, (unsigned long)s.lines, (unsigned long)s.measured,
           (unsigned long)s.rewritten, (unsigned long)s.refused_late, (unsigned long)s.refused_shape,
           (unsigned long)s.locks, (unsigned long)s.unlocks, (unsigned long)s.acquisitions,
           (long)(s.last_err_q8 * 25 / 256), (long)s_ref.sync_khz, (long)s_ref.porch_khz,
           (unsigned long)s_cpu.permille, (unsigned)s_cpu_tripped, (unsigned long)s_cpu.wakes,
           (unsigned long)s_cpu.idle_wakes);
}

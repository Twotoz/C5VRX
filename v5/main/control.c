/* C5VRX-5 by Twotoz and the C5VRX contributors: 50 ms control loop.
 * BOOT button and menu, AFC acquisition, PAL/NTSC detection, signal
 * strength and the channel scanner. Observes completed RX descriptors only. */
#include "receiver.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "afc_ctrl.h"
#include "analog_video_detect.h"
#include "driver/gpio.h"
#include "esp_heap_caps.h"
#include "esp_timer.h"
#include "phase8_gain_lut.h"
#include "phy.h"
#include "rf.h"

#define BOOT_BUTTON      GPIO_NUM_28    /* XIAO ESP32-C5 BOOT */
#define TICK_MS          50
#define LONG_TICKS       12             /* 0.6 s */
#define MENU_IDLE_TICKS  600            /* 30 s without a press closes the menu */
#define RETUNE_SETTLE_TICKS 10          /* 500 ms after a PHY transaction */
#define SCAN_PAIRS       4092u

volatile bool s_menu_active;
volatile int s_signal_strength;
volatile bool s_scan_active;
volatile unsigned s_scan_progress;
volatile int s_cfo_khz;
volatile bool s_afc_locked;
volatile std_mode_t s_std_mode = STD_MODE_AUTO;
volatile video_standard_t s_video_std = VIDEO_STD_NTSC;
volatile bool s_detected_std_valid;
volatile video_standard_t s_detected_std = VIDEO_STD_NTSC;
volatile int64_t s_last_video_us;
QueueHandle_t s_commands;

static uint8_t s_window[CONTROL_BYTES];
static uint8_t s_pal_votes, s_ntsc_votes;
static uint8_t s_last_std = 0xFF;      /* last stable standard (NVS) */
static unsigned s_afc_corrections;

static void standard_reset(void)
{
    s_pal_votes = s_ntsc_votes = 0;
    s_detected_std_valid = false;
}

/* Burst-confirmed standard from the AFC estimator; a few agreeing
 * windows make it stable and remembered for the next boot's menu. */
static void standard_vote(unsigned afc_standard)
{
    bool pal = afc_standard == 2u;
    if (pal) { if (s_pal_votes < 8u) ++s_pal_votes; if (s_ntsc_votes) --s_ntsc_votes; }
    else { if (s_ntsc_votes < 8u) ++s_ntsc_votes; if (s_pal_votes) --s_pal_votes; }
    if (s_pal_votes >= 3u && s_pal_votes >= s_ntsc_votes + 2u) { s_detected_std = VIDEO_STD_PAL; s_detected_std_valid = true; }
    else if (s_ntsc_votes >= 3u && s_ntsc_votes >= s_pal_votes + 2u) { s_detected_std = VIDEO_STD_NTSC; s_detected_std_valid = true; }
    if (s_detected_std_valid && s_last_std != (uint8_t)s_detected_std) {
        s_last_std = (uint8_t)s_detected_std;
        (void)blob_store("last_std", &s_last_std, 1);
    }
}

video_standard_t menu_standard(void)
{
    if (s_std_mode == STD_MODE_PAL) return VIDEO_STD_PAL;
    if (s_std_mode == STD_MODE_NTSC) return VIDEO_STD_NTSC;
    if (s_detected_std_valid) return s_detected_std;
    return s_last_std == VIDEO_STD_PAL ? VIDEO_STD_PAL : VIDEO_STD_NTSC;
}

/* 0..100 from phase coherence, envelope and gain headroom. */
static int strength(const metrics_t *m, uint8_t gain)
{
    if (m->q_phase < 18 || m->origin_pm > 850) return 0;
    int coherence = (m->q_phase - 18) * 100 / 62;
    int power = ((m->p_median >> 4) - 6) * 100 / 28;   /* ultrafine lane: 4^2 */
    int max_gain = rf_gain_table()->max_index;
    int headroom = (max_gain - (int)gain) * 100 / (max_gain > 2 ? max_gain - 2 : 1);
    coherence = coherence < 0 ? 0 : coherence > 100 ? 100 : coherence;
    power = power < 0 ? 0 : power > 100 ? 100 : power;
    headroom = headroom < 0 ? 0 : headroom > 100 ? 100 : headroom;
    return (coherence * 2 + power + headroom) / 4;
}

static void after_retune(void)
{
    ++s_control_epoch;
    s_cfo_khz = 0;
    s_afc_locked = false;
    standard_reset();
    settings_save();
    if (s_menu_active) menu_render();
    printf("[CHANNEL] %s %u MHz\n", rf_channel()->name, rf_channel()->freq_mhz);
}

/* ---- Channel scanner -------------------------------------------------------
 * RF strength alone let 5 GHz Wi-Fi win (issue #128): a channel only
 * qualifies with analog-video confidence (line-period periodicity), and its
 * carrier must sit within 3 MHz of the channel centre (a VTX also shows on
 * neighbouring channels through the filter, and the 50 ns offset estimate
 * wraps every 20 MHz). Among centred candidates the strongest wins. */
static uint8_t *s_scan_buf;

static analog_video_t scan_confidence(void)
{
    analog_video_t v[3] = {{0}};
    for (unsigned w = 0; w < 3u; ++w) {
        if (w) vTaskDelay(pdMS_TO_TICKS(10));
        if (copy_recent_endpoints(s_scan_buf, SCAN_PAIRS))
            v[w] = analog_video_detect(s_scan_buf, SCAN_PAIRS, c5vrx_phase8_gain_lut);
    }
    analog_video_t a = v[0], b = v[1], c = v[2], t;   /* median by confidence */
    if (a.confidence > b.confidence) { t = a; a = b; b = t; }
    if (b.confidence > c.confidence) { t = b; b = c; c = t; }
    if (a.confidence > b.confidence) { t = a; a = b; b = t; }
    return b;
}

static analog_video_t channel_confidence(uint8_t scan_gain, uint8_t max_gain, int *quality)
{
    vTaskDelay(pdMS_TO_TICKS(90));
    metrics_t m = {0};
    if (copy_control_window(s_window, sizeof(s_window))) m = measure_window(s_window, sizeof(s_window));
    if (quality) *quality = strength(&m, scan_gain);
    analog_video_t video = scan_confidence();
    /* A far VTX is starved at the scan gain: look again at maximum. */
    if (video.confidence < ANALOG_VIDEO_MIN_CONFIDENCE && m.origin_pm > 500) {
        receiver_set_gain(max_gain);
        vTaskDelay(pdMS_TO_TICKS(20));
        analog_video_t far = scan_confidence();
        receiver_set_gain(scan_gain);
        if (far.confidence > video.confidence) video = far;
    }
    return video;
}

static void scan(void)
{
    if (!receiver_pause("SCAN")) return;
    s_scan_buf = heap_caps_malloc(SCAN_PAIRS, MALLOC_CAP_INTERNAL);
    if (!s_scan_buf) { printf("SCAN refused=no_memory\n"); receiver_resume(); return; }
    const size_t original = rf_channel_index();
    const uint8_t max_gain = rf_gain_table()->max_index, scan_gain = 52u;
    size_t best = original;
    int best_quality = -1, best_video = -1, best_offset = 0x7fffffff;
    s_scan_active = true;
    s_scan_progress = 0;
    receiver_set_gain(scan_gain);   /* every channel compared at one gain */
    for (size_t ch = 0; ch < FPV_CHANNELS; ++ch) {
        s_scan_progress = (unsigned)(ch * 100u / FPV_CHANNELS);
        if (s_menu_active) menu_render();
        if (rf_set_channel(ch) != ESP_OK) continue;
        int quality = 0;
        analog_video_t v = channel_confidence(scan_gain, max_gain, &quality);
        if (v.confidence < ANALOG_VIDEO_MIN_CONFIDENCE) continue;
        int off = abs(v.offset_khz);
        printf("SCAN candidate %s %u MHz video=%d offset_khz=%d quality=%d %s\n", rf_channel()->name,
               rf_channel()->freq_mhz, v.confidence, v.offset_khz, quality, v.standard == 1 ? "PAL" : "NTSC");
        bool centred = off <= 3000, best_centred = best_offset <= 3000;
        if ((centred && !best_centred) ||
            (centred == best_centred &&
             (quality > best_quality ||
              (quality == best_quality &&
               (off / 500 < best_offset / 500 ||
                (off / 500 == best_offset / 500 && v.confidence > best_video)))))) {
            best = ch;
            best_quality = quality;
            best_video = v.confidence;
            best_offset = off;
        }
    }
    /* Confirm the winner: still video, still centred. */
    if (best_video >= 0) {
        (void)rf_set_channel(best);
        analog_video_t v = channel_confidence(scan_gain, max_gain, NULL);
        if (v.confidence < ANALOG_VIDEO_MIN_CONFIDENCE || abs(v.offset_khz) > 3000) best_video = -1;
    }
    if (best_video < 0) best = original;
    (void)rf_set_channel(best);
    heap_caps_free(s_scan_buf);
    s_scan_buf = NULL;
    s_scan_active = false;
    receiver_set_gain(max_gain);
    receiver_resume();
    printf("SCAN result=%s channel=%s\n", best_video < 0 ? "none_kept" : "found", rf_channel()->name);
    after_retune();
}

/* ---- Menu actions ---------------------------------------------------------- */
static void menu_action(void)
{
    switch (s_menu_cursor) {
    case MENU_BAND: rf_next_band(); after_retune(); break;
    case MENU_CHANNEL: rf_next_channel(); after_retune(); break;
    case MENU_SCAN: scan(); break;
    case MENU_VIDEO:
        menu_set_standard_mode(s_std_mode == STD_MODE_AUTO ? STD_MODE_NTSC :
                               s_std_mode == STD_MODE_NTSC ? STD_MODE_PAL : STD_MODE_AUTO);
        settings_save();
        break;
    default: menu_close(); return;
    }
    if (s_menu_active) menu_render();
}

static void command(int c, unsigned *menu_idle)
{
    *menu_idle = 0;
    switch (c) {
    case 'o': if (s_menu_active) menu_close(); else if (menu_open()) s_menu_cursor = 0; break;
    case 'n': if (s_menu_active) { s_menu_cursor = (s_menu_cursor + 1) % MENU_ITEMS; menu_render(); } break;
    case 'x': if (s_menu_active) menu_action(); break;
    case 'c': rf_next_channel(); after_retune(); break;
    case 'b': rf_next_band(); after_retune(); break;
    case 's': scan(); break;
    default: break;
    }
}

static uint32_t afc_context(void)
{
    const uint32_t parts[5] = {(uint32_t)rf_channel_index(), s_control_epoch, (uint32_t)rf_offset_khz(),
                               rf_table_generation(), phy_generation()};
    uint32_t h = 2166136261u;
    for (unsigned k = 0; k < 5u; ++k) h = (h ^ parts[k]) * 16777619u;
    return h;
}

static void control_task(void *arg)
{
    (void)arg;
    afc2_ctrl_t afc = {.target_khz = AFC_PORCH_TARGET_KHZ};
    bool own_write = false;
    uint8_t afc_lost = 0;
    unsigned settle = RETUNE_SETTLE_TICKS, btn = 0, boot_grace = 20, menu_idle = 0, menu_refresh = 0;
    bool long_fired = false, was_locked = false;
    uint32_t seen_phy = phy_generation(), seen_control = s_control_epoch;
    for (;;) {
        vTaskDelay(pdMS_TO_TICKS(TICK_MS));
        int c;
        for (unsigned k = 0; k < 8 && xQueueReceive(s_commands, &c, 0) == pdTRUE; ++k) command(c, &menu_idle);

        /* BOOT button: short = next channel (menu: next item); 0.6 s = menu
         * (menu: do the selected item). */
        if (boot_grace) { --boot_grace; btn = 0; long_fired = false; }
        else if (gpio_get_level(BOOT_BUTTON) == 0) {
            ++btn;
            menu_idle = 0;
            if (btn >= LONG_TICKS && !long_fired) {
                long_fired = true;
                if (!s_menu_active) { if (menu_open()) s_menu_cursor = 0; }
                else menu_action();
            }
        } else if (btn) {
            if (!long_fired && btn >= 2) {
                if (s_menu_active) { s_menu_cursor = (s_menu_cursor + 1) % MENU_ITEMS; menu_render(); }
                else { rf_next_channel(); after_retune(); }
            }
            btn = 0;
            long_fired = false;
        }
        if (s_menu_active) {
            if (++menu_idle >= MENU_IDLE_TICKS) { menu_close(); printf("[MENU] closed after 30 s idle\n"); }
            else if (++menu_refresh >= 5) { menu_refresh = 0; menu_render(); }
        } else menu_idle = menu_refresh = 0;

        transport_poll_faults();
        if (phy_generation() != seen_phy || s_control_epoch != seen_control) {
            seen_phy = phy_generation();
            seen_control = s_control_epoch;
            s_cfo_khz = 0;
            afc2_ctrl_invalidate(&afc);
            settle = own_write ? 2u : RETUNE_SETTLE_TICKS;
        }
        uint32_t gain_epoch = s_gain_epoch, ctx = afc_context();
        if (s_scan_active || !copy_control_window(s_window, sizeof(s_window))) {
            afc2_ctrl_invalidate(&afc);
            continue;
        }
        metrics_t m = measure_window(s_window, sizeof(s_window));
        s_signal_strength = (s_signal_strength * 3 + strength(&m, s_current_gain) + 2) / 4;

        /* AFC: burst-confirmed sync/porch levels only (issue #115), never the
         * scene-biased FM slope; acquisition only, sticky once locked. */
        if (afc2_ctrl_sync(&afc, ctx, own_write)) { s_afc_locked = false; afc_lost = 0; }
        own_write = false;
        int64_t now = esp_timer_get_time();
        bool window_ok = settle == 0 && gain_epoch == s_gain_epoch && ctx == afc_context() &&
                         now - s_last_gain_write_us >= 100000 &&
                         afc2_envelope_stationary(s_window, sizeof(s_window));
        if (settle) --settle;
        afc2_result_t r = {0};
        if (window_ok) {
            r = afc2_measure(s_window, sizeof(s_window), c5vrx_phase8_gain_lut);
            afc2_ctrl_observe(&afc, &r);
        } else afc2_ctrl_invalidate(&afc);
        s_cfo_khz = r.porch_pairs ? r.porch_khz : 0;
        bool valid = window_ok && r.lines && r.standard && r.porch_pairs && r.sync_pairs &&
                     r.burst_x10 >= AFC2_BURST_MIN_X10;
        if (valid) { s_last_video_us = now; standard_vote(r.standard); }
        bool was = s_afc_locked;
        s_afc_locked = afc2_native_lock(s_afc_locked, valid, afc2_ctrl_can_lock(&afc, true), afc.n, &afc_lost);
        if (was && !s_afc_locked) afc2_ctrl_reset(&afc, ctx, true);
        if (!s_afc_locked && window_ok && m.q_phase >= 55 && !s_menu_active &&
            phy_try_actuator(phy_generation())) {
            int32_t step = 0;
            if (afc2_ctrl_decide(&afc, gain_epoch == s_gain_epoch, &step)) {
                rf_set_offset_khz(rf_offset_khz() + (int)step);
                s_last_gain_write_us = esp_timer_get_time();   /* restore re-forces gain */
                ++s_gain_epoch;
                ++s_afc_corrections;
                own_write = true;
            }
            phy_end_actuator();
        }
        if (s_afc_locked && !was_locked)
            printf("[VIDEO] locked %s %u MHz %s offset_khz=%d gain=%u\n", rf_channel()->name,
                   rf_channel()->freq_mhz, r.standard == 2 ? "PAL" : "NTSC", rf_offset_khz(), s_current_gain);
        was_locked = s_afc_locked;
    }
}

void control_start(void)
{
    const gpio_config_t cfg = {
        .pin_bit_mask = 1ULL << BOOT_BUTTON,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    gpio_config(&cfg);
    if (!blob_load("last_std", &s_last_std, 1)) s_last_std = 0xFF;
    s_commands = xQueueCreate(16, sizeof(int));
    ESP_ERROR_CHECK(s_commands ? ESP_OK : ESP_ERR_NO_MEM);
    ESP_ERROR_CHECK(xTaskCreate(control_task, "control", 6144, NULL, 3, NULL) == pdPASS ? ESP_OK : ESP_ERR_NO_MEM);
}

void control_print(void)
{
    printf("CONTROL channel=%s freq=%u offset_khz=%d cfo_khz=%d afc_locked=%u afc_corrections=%u "
           "standard=%s detected=%s signal=%d menu=%u\n",
           rf_channel()->name, rf_frequency_mhz(), rf_offset_khz(), s_cfo_khz, s_afc_locked,
           s_afc_corrections, s_std_mode == STD_MODE_AUTO ? "AUTO" : s_std_mode == STD_MODE_PAL ? "PAL" : "NTSC",
           s_detected_std_valid ? (s_detected_std == VIDEO_STD_PAL ? "PAL" : "NTSC") : "none",
           s_signal_strength, s_menu_active);
}

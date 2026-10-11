/* C5VRX-5 by Twotoz and the C5VRX contributors: USB serial console.
 * Polled, never paces the realtime path. */
#include "receiver.h"
#include <stdio.h>
#include <stdlib.h>
#include "esp_app_desc.h"
#include "esp_heap_caps.h"
#include "esp_timer.h"
#include "hal/usb_serial_jtag_ll.h"
#include "rf.h"
#include "snapshot.h"

extern volatile const char *s_boot_stage;

static void status(void)
{
    printf("C5VRX5 version=%s demod=U85Q_1a09f5c092e8 lane=ultrafine gain_owner=direct_gain_v5 "
           "uptime_s=%lld heap_internal=%u\n", esp_app_get_description()->version,
           esp_timer_get_time() / 1000000, (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL));
    control_print();
    receiver_print();
    calibration_print();
    transport_print();
}

/* 8190 contiguous IQ bytes as hex lines (tools/capture_iq_snapshot.py). */
static void snapshot(void)
{
    if (s_menu_active || receiver_paused()) { printf("IQSNAP_REFUSED context_busy\n"); return; }
    uint8_t *raw = malloc(C5V4_LEVEL_SAMPLE_BYTES);
    if (!raw) { printf("IQSNAP_REFUSED memory\n"); return; }
    uint32_t epoch = s_gain_epoch;
    int64_t now = esp_timer_get_time();
    bool settled = now - s_last_gain_write_us >= 500 && s_gain.state != DG_SETTLE;
    if (!settled || !copy_snapshot(raw) || epoch != s_gain_epoch) {
        free(raw);
        printf("IQSNAP_REFUSED stale_or_settling\n");
        return;
    }
    uint32_t hash = 2166136261u;
    for (unsigned k = 0; k < C5V4_LEVEL_SAMPLE_BYTES; ++k) hash = (hash ^ raw[k]) * 16777619u;
    printf("IQSNAP_BEGIN bytes=%u rate_hz=%u format=Ihigh_Qlow gain=%u lane=2 freq_mhz=%u fnv1a=%08lx\n",
           C5V4_LEVEL_SAMPLE_BYTES, IQ_RATE_HZ, s_current_gain, rf_frequency_mhz(), (unsigned long)hash);
    static const char hex[] = "0123456789abcdef";
    char line[513];
    for (unsigned offset = 0; offset < C5V4_LEVEL_SAMPLE_BYTES; offset += 256) {
        unsigned size = C5V4_LEVEL_SAMPLE_BYTES - offset > 256 ? 256 : C5V4_LEVEL_SAMPLE_BYTES - offset;
        for (unsigned k = 0; k < size; ++k) {
            line[2 * k] = hex[raw[offset + k] >> 4];
            line[2 * k + 1] = hex[raw[offset + k] & 15];
        }
        line[2 * size] = 0;
        printf("IQSNAP_DATA offset=%u %s\n", offset, line);
        vTaskDelay(1);
    }
    free(raw);
    printf("IQSNAP_END fnv1a=%08lx\n", (unsigned long)hash);
}

static void help(void)
{
    printf("C5VRX-5 keys: T status, I IQ snapshot, o menu open/close, n/x menu next/select,\n"
           "  c next channel, b next band, s scan, F filter calibration (VTX off),\n"
           "  ` reboot to USB download (flashing)\n");
}

static void console_task(void *arg)
{
    (void)arg;
    for (;;) {
        for (unsigned k = 0; k < 64; ++k) {
            uint8_t c;
            if (usb_serial_jtag_ll_read_rxfifo(&c, 1) == 0) break;
            switch (c) {
            case 'T': status(); break;
            case 'I': snapshot(); break;
            case 'F': filter_request(); break;
            case '`': rf_reboot_to_download(); break;
            case 'h': case '?': help(); break;
            case 'o': case 'n': case 'x': case 'c': case 'b': case 's': {
                int command = c;
                if (s_commands) (void)xQueueSend(s_commands, &command, 0);
                break;
            }
            default: break;
            }
        }
        /* Heartbeat: shows the console task is alive and not starved. */
        static int64_t beat;
        if (esp_timer_get_time() - beat > 5000000) {
            beat = esp_timer_get_time();
            printf("HB t_s=%lld stage=%s gain=%u menu=%u\n", beat / 1000000, s_boot_stage,
                   s_current_gain, s_menu_active);
        }
        vTaskDelay(pdMS_TO_TICKS(10));
    }
}

void console_start(void)
{
    ESP_ERROR_CHECK(xTaskCreate(console_task, "console", 6144, NULL, 1, NULL) == pdPASS ? ESP_OK : ESP_ERR_NO_MEM);
}

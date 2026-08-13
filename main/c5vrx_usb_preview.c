#include "c5vrx_usb_preview.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "esp_heap_caps.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#define FRAME_BYTES (C5VRX_USB_PREVIEW_WIDTH * C5VRX_USB_PREVIEW_HEIGHT)
#define LINE_SAMPLES 1280u
#define ACTIVE_START 210u
#define ACTIVE_SAMPLES 1040u
#define SYNC_THRESHOLD 8u
#define SYNC_MIN_SAMPLES 40u

typedef struct {
    uint8_t *frame[2];
    unsigned fill_index;
    unsigned ready_index;
    unsigned x;
    unsigned y;
    unsigned line_phase;
    unsigned line_number;
    unsigned low_run;
    uint64_t sequence;
    uint64_t dropped;
    bool sending[2];
    volatile bool ready;
    volatile bool running;
    TaskHandle_t task;
    portMUX_TYPE lock;
} preview_state_t;

static preview_state_t s_preview = {.lock = portMUX_INITIALIZER_UNLOCKED};

/* Standard reflected CRC-32/ISO-HDLC, matching Python zlib.crc32 exactly. */
static uint32_t preview_crc32(const uint8_t *data, size_t count)
{
    uint32_t crc = UINT32_MAX;
    for (size_t i = 0; i < count; ++i) {
        crc ^= data[i];
        for (unsigned bit = 0; bit < 8u; ++bit)
            crc = (crc >> 1u) ^ (0xedb88320u & (0u - (crc & 1u)));
    }
    return ~crc;
}

static void preview_task(void *arg)
{
    (void)arg;
    while (s_preview.running) {
        ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(100));
        if (!s_preview.running) break;
        unsigned index = 0;
        bool have_frame = false;
        taskENTER_CRITICAL(&s_preview.lock);
        if (s_preview.ready) {
            index = s_preview.ready_index;
            s_preview.ready = false;
            s_preview.sending[index] = true;
            have_frame = true;
        }
        taskEXIT_CRITICAL(&s_preview.lock);
        if (!have_frame) continue;

        const uint32_t crc = preview_crc32(
            s_preview.frame[index], FRAME_BYTES);
        /* Hold stdio's lock over header, binary payload and footer so normal
         * diagnostics cannot corrupt framing. A disconnected preview consumer
         * may drop frames; it never owns or stops AV/PARLIO. */
        flockfile(stdout);
        printf("C5VRX_USB_FRAME seq=%llu width=%u height=%u bytes=%u encoding=GRAY8 crc32=%08lx\n",
               (unsigned long long)s_preview.sequence++,
               C5VRX_USB_PREVIEW_WIDTH, C5VRX_USB_PREVIEW_HEIGHT,
               FRAME_BYTES, (unsigned long)crc);
        fwrite(s_preview.frame[index], 1, FRAME_BYTES, stdout);
        printf("\nC5VRX_USB_FRAME_END\n");
        fflush(stdout);
        funlockfile(stdout);
        taskENTER_CRITICAL(&s_preview.lock);
        s_preview.sending[index] = false;
        taskEXIT_CRITICAL(&s_preview.lock);
    }
    s_preview.task = NULL;
    vTaskDelete(NULL);
}

esp_err_t c5vrx_usb_preview_start(void)
{
    if (s_preview.running) return ESP_ERR_INVALID_STATE;
    memset(&s_preview, 0, offsetof(preview_state_t, lock));
    for (unsigned i = 0; i < 2; ++i) {
        s_preview.frame[i] = heap_caps_malloc(FRAME_BYTES, MALLOC_CAP_INTERNAL);
        if (!s_preview.frame[i]) {
            while (i) free(s_preview.frame[--i]);
            return ESP_ERR_NO_MEM;
        }
        memset(s_preview.frame[i], 0, FRAME_BYTES);
    }
    s_preview.running = true;
    if (xTaskCreate(preview_task, "c5vrx_preview", 3072, NULL, 4,
                    &s_preview.task) != pdPASS) {
        s_preview.running = false;
        free(s_preview.frame[0]);
        free(s_preview.frame[1]);
        s_preview.frame[0] = s_preview.frame[1] = NULL;
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}

esp_err_t c5vrx_usb_preview_stop(void)
{
    if (!s_preview.running) return ESP_ERR_INVALID_STATE;
    s_preview.running = false;
    if (s_preview.task) xTaskNotifyGive(s_preview.task);
    for (unsigned i = 0; i < 100u && s_preview.task; ++i)
        vTaskDelay(pdMS_TO_TICKS(1));
    if (s_preview.task) return ESP_ERR_TIMEOUT;
    free(s_preview.frame[0]);
    free(s_preview.frame[1]);
    s_preview.frame[0] = s_preview.frame[1] = NULL;
    return ESP_OK;
}

bool c5vrx_usb_preview_running(void) { return s_preview.running; }

void c5vrx_usb_preview_ingest(const uint8_t *cvbs, size_t samples)
{
    if (!s_preview.running || !cvbs) return;
    for (size_t n = 0; n < samples; ++n) {
        const uint8_t value = cvbs[n] & 0x3fu;
        if (value <= SYNC_THRESHOLD) {
            ++s_preview.low_run;
            if (s_preview.low_run == SYNC_MIN_SAMPLES) {
                s_preview.line_phase = s_preview.low_run;
                ++s_preview.line_number;
                s_preview.x = 0;
            }
        } else {
            s_preview.low_run = 0;
        }

        if ((s_preview.line_number & 1u) == 0u &&
            s_preview.y < C5VRX_USB_PREVIEW_HEIGHT &&
            s_preview.x < C5VRX_USB_PREVIEW_WIDTH) {
            const unsigned target = ACTIVE_START +
                s_preview.x * ACTIVE_SAMPLES / C5VRX_USB_PREVIEW_WIDTH;
            if (s_preview.line_phase == target) {
                s_preview.frame[s_preview.fill_index]
                    [s_preview.y * C5VRX_USB_PREVIEW_WIDTH + s_preview.x] =
                    (uint8_t)((value * 255u + 31u) / 63u);
                ++s_preview.x;
            }
        }
        ++s_preview.line_phase;
        if (s_preview.line_phase >= LINE_SAMPLES) {
            s_preview.line_phase = 0;
            if ((s_preview.line_number & 1u) == 0u) {
                ++s_preview.y;
                if (s_preview.y == C5VRX_USB_PREVIEW_HEIGHT) {
                    taskENTER_CRITICAL(&s_preview.lock);
                    const unsigned next = s_preview.fill_index ^ 1u;
                    if (s_preview.ready || s_preview.sending[next]) {
                        ++s_preview.dropped;
                    } else {
                        s_preview.ready_index = s_preview.fill_index;
                        s_preview.ready = true;
                        s_preview.fill_index = next;
                        if (s_preview.task) xTaskNotifyGive(s_preview.task);
                    }
                    taskEXIT_CRITICAL(&s_preview.lock);
                    s_preview.y = 0;
                }
            }
        }
    }
}

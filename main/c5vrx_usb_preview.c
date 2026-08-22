/* SPDX-License-Identifier: GPL-3.0-only */

#include "c5vrx_usb_preview.h"

#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "c5vrx_cvbs_sync.h"
#include "c5vrx_preview_geometry.h"
#include "c5vrx_sample_ring.h"
#include "c5vrx_usb_transport.h"
#include "esp_heap_caps.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "sdkconfig.h"

#ifndef CONFIG_C5VRX_USB_PREVIEW_WIDTH
#define CONFIG_C5VRX_USB_PREVIEW_WIDTH 320
#endif
#ifndef CONFIG_C5VRX_USB_PREVIEW_HEIGHT
#define CONFIG_C5VRX_USB_PREVIEW_HEIGHT 240
#endif

#define USB_HEADER_BYTES 32u
#define FRAME_DESCRIPTOR_BYTES 8u
#define PACKET_STREAM_INFO 1u
#define PACKET_GRAY8_FRAME 2u
#define PIXEL_FORMAT_GRAY8 1u
#define FIRST_ACTIVE_FIELD_LINE 20u
#define ACTIVE_FIELD_LINES 240u
#define NOMINAL_LINE_SAMPLES 1280u
#define NOMINAL_ACTIVE_START 210u
#define NOMINAL_ACTIVE_SAMPLES 1040u

/* Shallow best-effort staging between the AV hot path and this worker.
 * Deep enough to absorb scheduler jitter at 1024-sample sink blocks,
 * small enough that sustained overload drops visibly instead of hiding
 * in queue growth. */
#define PREVIEW_STAGE_BYTES 16384u

static const uint8_t s_usb_magic[8] = {0x00, 'C', '5', 'V', 'R', 'X', 0xa5, 0x5a};

typedef struct {
    uint8_t *frame[2];
    uint16_t width;
    uint16_t height;
    uint32_t frame_bytes;
    unsigned fill_index;
    unsigned ready_index;
    unsigned x;
    uint16_t current_row;
    uint32_t line_phase;
    uint32_t line_period;
    uint32_t sequence;
    uint64_t dropped;
    uint32_t frames_completed;
    uint32_t frames_sent;
    c5vrx_cvbs_sync_tracker_t sync;
    c5vrx_usb_preview_stats_t telemetry;
    bool current_line;
    bool sending[2];
    volatile bool ready;
    volatile bool running;
    c5vrx_sample_ring_t stage;
    TaskHandle_t task;
    portMUX_TYPE lock;
} preview_state_t;

static preview_state_t s_preview = {.lock = portMUX_INITIALIZER_UNLOCKED};

/* Kept allocated across stop/start on purpose: a producer may still be
 * inside submit() while the preview is being stopped, so the staging
 * storage must outlive every running flag transition. */
static uint8_t *s_stage_storage;

/* Scratch for draining staged samples in the preview worker only. */
static uint8_t s_stage_drain[2048];

static void ingest_samples(const uint8_t *cvbs, size_t samples);
static void update_telemetry(void);

static void resolve_size(uint16_t *width, uint16_t *height)
{
    uint16_t w = (uint16_t)CONFIG_C5VRX_USB_PREVIEW_WIDTH;
    uint16_t h = (uint16_t)CONFIG_C5VRX_USB_PREVIEW_HEIGHT;
    if (!c5vrx_preview_size_valid(w, h)) { w = 160u; h = 120u; }
    *width = w;
    *height = h;
}

esp_err_t c5vrx_usb_preview_set_size(const uint16_t width,
                                     const uint16_t height)
{
    if (!c5vrx_preview_size_valid(width, height)) {
        return ESP_ERR_INVALID_ARG;
    }
    if (s_preview.running || s_preview.task) return ESP_ERR_INVALID_STATE;
    s_preview.width = width;
    s_preview.height = height;
    return ESP_OK;
}

void c5vrx_usb_preview_get_size(uint16_t *width, uint16_t *height)
{
    if (!width || !height) return;
    if (!s_preview.width) {
        resolve_size(&s_preview.width, &s_preview.height);
    }
    *width = s_preview.width;
    *height = s_preview.height;
}

static void put_le16(uint8_t *out, uint16_t value)
{
    out[0] = (uint8_t)value;
    out[1] = (uint8_t)(value >> 8u);
}

static void put_le32(uint8_t *out, uint32_t value)
{
    for (unsigned i = 0; i < 4u; ++i) out[i] = (uint8_t)(value >> (8u * i));
}

static void put_le64(uint8_t *out, uint64_t value)
{
    for (unsigned i = 0; i < 8u; ++i) out[i] = (uint8_t)(value >> (8u * i));
}

/* Standard reflected CRC-32/ISO-HDLC, matching Python zlib.crc32 exactly.
 * Table-driven: at 320x240 the per-bit loop would burn ~0.6M iterations
 * per frame on the worker. */
static uint32_t s_crc_table[256];
static bool s_crc_table_ready;

static void crc32_init_table(void)
{
    if (s_crc_table_ready) return;
    for (unsigned i = 0; i < 256u; ++i) {
        uint32_t c = i;
        for (unsigned bit = 0; bit < 8u; ++bit)
            c = (c >> 1u) ^ (0xedb88320u & (0u - (c & 1u)));
        s_crc_table[i] = c;
    }
    s_crc_table_ready = true;
}

static uint32_t crc32_update(uint32_t crc, const uint8_t *data, size_t count)
{
    crc = ~crc;
    for (size_t i = 0; i < count; ++i)
        crc = (crc >> 8u) ^ s_crc_table[(crc ^ data[i]) & 0xFFu];
    return ~crc;
}

static uint32_t crc32_parts(const uint8_t *first, size_t first_count,
                            const uint8_t *second, size_t second_count)
{
    if (!s_crc_table_ready) crc32_init_table();
    uint32_t crc = crc32_update(UINT32_MAX, first, first_count);
    crc = crc32_update(~crc, second, second_count);
    return crc;
}

static void write_packet(unsigned type,
                         const uint8_t *first, size_t first_count,
                         const uint8_t *second, size_t second_count)
{
    uint8_t header[USB_HEADER_BYTES] = {0};
    memcpy(header, s_usb_magic, sizeof(s_usb_magic));
    header[8] = C5VRX_USB_PREVIEW_PROTOCOL_VERSION;
    header[9] = (uint8_t)type;
    put_le16(header + 10, USB_HEADER_BYTES);
    put_le32(header + 12, s_preview.sequence++);
    put_le32(header + 16, (uint32_t)(first_count + second_count));
    put_le64(header + 20, (uint64_t)esp_timer_get_time());
    put_le32(header + 28, crc32_parts(header, 28u, NULL, 0u));
    const uint32_t payload_crc =
        crc32_parts(first, first_count, second, second_count);
    uint8_t trailer[4];
    put_le32(trailer, payload_crc);

    const c5vrx_usb_iovec_t packet[] = {
        {.data = header, .size = sizeof(header)},
        {.data = first, .size = first_count},
        {.data = second, .size = second_count},
        {.data = trailer, .size = sizeof(trailer)},
    };
    (void)c5vrx_usb_writev(packet, sizeof(packet) / sizeof(packet[0]));
}

static void make_descriptor(uint8_t descriptor[FRAME_DESCRIPTOR_BYTES],
                            uint8_t flags)
{
    put_le16(descriptor, s_preview.width);
    put_le16(descriptor + 2, s_preview.height);
    put_le16(descriptor + 4, s_preview.width);
    descriptor[6] = PIXEL_FORMAT_GRAY8;
    descriptor[7] = flags;
}

static void preview_task(void *arg)
{
    (void)arg;
    uint8_t descriptor[FRAME_DESCRIPTOR_BYTES];
    make_descriptor(descriptor, 0u);
    write_packet(PACKET_STREAM_INFO, descriptor, sizeof(descriptor), NULL, 0u);

    while (s_preview.running) {
        (void)ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(20));
        if (!s_preview.running) break;

        /* Drain the AV side tap first: this task runs far below the RF
         * and AV priorities and only ever sees what survived without
         * delaying c5vrx_cvbs_live_out_write(). */
        for (;;) {
            size_t got;
            taskENTER_CRITICAL(&s_preview.lock);
            got = c5vrx_sample_ring_read(&s_preview.stage, s_stage_drain,
                                         sizeof(s_stage_drain));
            taskEXIT_CRITICAL(&s_preview.lock);
            if (!got) break;
            ingest_samples(s_stage_drain, got);
        }
        update_telemetry();

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

        make_descriptor(descriptor, 1u);
        write_packet(PACKET_GRAY8_FRAME,
                     descriptor, sizeof(descriptor),
                     s_preview.frame[index], s_preview.frame_bytes);
        taskENTER_CRITICAL(&s_preview.lock);
        ++s_preview.frames_sent;
        s_preview.telemetry.frames_sent = s_preview.frames_sent;
        s_preview.sending[index] = false;
        taskEXIT_CRITICAL(&s_preview.lock);
    }

    /*
     * Do not send STREAM_END here.  A stopped host may no longer drain USB,
     * making the task block in the direct writer while stop() waits for it.
     * The ASCII STOP acknowledgement is the authoritative lifecycle marker.
     */
    /* Clear the handle under the lock so producers can never hand work to
     * a task that has already left the scheduler. */
    taskENTER_CRITICAL(&s_preview.lock);
    s_preview.task = NULL;
    taskEXIT_CRITICAL(&s_preview.lock);
    vTaskDelete(NULL);
}

static void publish_frame(void)
{
    taskENTER_CRITICAL(&s_preview.lock);
    ++s_preview.frames_completed;
    s_preview.telemetry.frames_completed = s_preview.frames_completed;
    const unsigned next = s_preview.fill_index ^ 1u;
    if (s_preview.ready || s_preview.sending[next]) {
        ++s_preview.dropped;
        s_preview.telemetry.frames_dropped =
            s_preview.dropped > UINT32_MAX ? UINT32_MAX : (uint32_t)s_preview.dropped;
    } else {
        s_preview.ready_index = s_preview.fill_index;
        s_preview.ready = true;
        s_preview.fill_index = next;
        if (s_preview.task) xTaskNotifyGive(s_preview.task);
    }
    taskEXIT_CRITICAL(&s_preview.lock);
}

esp_err_t c5vrx_usb_preview_start(void)
{
    /* START is deliberately idempotent for reconnecting Windows hosts. */
    if (s_preview.running) return ESP_OK;
    if (s_preview.task) return ESP_ERR_INVALID_STATE;
    crc32_init_table();
    uint16_t width, height;
    resolve_size(&width, &height);
    if (!s_preview.width) {
        s_preview.width = width;
        s_preview.height = height;
    }
    const uint16_t requested_w = s_preview.width;
    const uint16_t requested_h = s_preview.height;
    memset(&s_preview, 0, offsetof(preview_state_t, lock));
    c5vrx_cvbs_sync_init(&s_preview.sync);
    s_preview.current_row = C5VRX_CVBS_SYNC_NO_LINE;
    s_preview.line_period = NOMINAL_LINE_SAMPLES;

    /* Allocate the configured rung; fall back down the ladder when the
     * heap cannot host two full frames, so START degrades instead of
     * failing outright. */
    static const uint16_t ladder[][2] = {
        {320, 240}, {256, 192}, {224, 168}, {192, 144},
        {176, 132}, {160, 120},
    };
    unsigned rung = 0u;
    for (; rung < sizeof(ladder) / sizeof(ladder[0]); ++rung) {
        if (!requested_w ||
            (ladder[rung][0] <= requested_w &&
             c5vrx_preview_size_valid(ladder[rung][0], ladder[rung][1]))) {
            break;
        }
    }
    if (rung >= sizeof(ladder) / sizeof(ladder[0])) rung = 5u;
    for (; rung < sizeof(ladder) / sizeof(ladder[0]); ++rung) {
        s_preview.width = ladder[rung][0];
        s_preview.height = ladder[rung][1];
        s_preview.frame_bytes =
            (uint32_t)s_preview.width * s_preview.height;
        unsigned i = 0u;
        for (; i < 2u; ++i) {
            s_preview.frame[i] = heap_caps_malloc(
                s_preview.frame_bytes, MALLOC_CAP_INTERNAL);
            if (!s_preview.frame[i]) break;
            memset(s_preview.frame[i], 0, s_preview.frame_bytes);
        }
        if (i == 2u) break;
        while (i) free(s_preview.frame[--i]);
        s_preview.frame[0] = s_preview.frame[1] = NULL;
    }
    if (!s_preview.frame[0]) {
        s_preview.width = requested_w;
        s_preview.height = requested_h;
        return ESP_ERR_NO_MEM;
    }
    if (!s_stage_storage) {
        s_stage_storage = heap_caps_malloc(PREVIEW_STAGE_BYTES,
                                           MALLOC_CAP_INTERNAL);
        if (!s_stage_storage) {
            free(s_preview.frame[0]);
            free(s_preview.frame[1]);
            s_preview.frame[0] = s_preview.frame[1] = NULL;
            return ESP_ERR_NO_MEM;
        }
    }
    if (!c5vrx_sample_ring_init(&s_preview.stage, s_stage_storage,
                                PREVIEW_STAGE_BYTES)) {
        free(s_preview.frame[0]);
        free(s_preview.frame[1]);
        s_preview.frame[0] = s_preview.frame[1] = NULL;
        return ESP_ERR_INVALID_STATE;
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
    /* STOP is deliberately idempotent as well. */
    if (!s_preview.running && !s_preview.task) return ESP_OK;
    taskENTER_CRITICAL(&s_preview.lock);
    s_preview.running = false;
    taskEXIT_CRITICAL(&s_preview.lock);
    if (s_preview.task) xTaskNotifyGive(s_preview.task);
    /* One scheduler tick per iteration; pdMS_TO_TICKS(1) can round to zero. */
    for (unsigned i = 0; i < 50u && s_preview.task; ++i)
        vTaskDelay(1);
    if (s_preview.task) return ESP_ERR_TIMEOUT;
    free(s_preview.frame[0]);
    free(s_preview.frame[1]);
    s_preview.frame[0] = s_preview.frame[1] = NULL;
    return ESP_OK;
}

bool c5vrx_usb_preview_running(void) { return s_preview.running; }

void c5vrx_usb_preview_get_stats(c5vrx_usb_preview_stats_t *stats)
{
    if (!stats) return;
    taskENTER_CRITICAL(&s_preview.lock);
    *stats = s_preview.telemetry;
    taskEXIT_CRITICAL(&s_preview.lock);
}

static void update_telemetry(void)
{
    c5vrx_usb_preview_stats_t next = {
        .samples_ingested = s_preview.sync.samples_seen > UINT32_MAX ?
            UINT32_MAX : (uint32_t)s_preview.sync.samples_seen,
        .horizontal_syncs = s_preview.sync.horizontal_events,
        .vertical_syncs = s_preview.sync.vertical_events,
        .rejected_sync_pulses = s_preview.sync.rejected_pulses,
        .lock_acquisitions = s_preview.sync.lock_acquisitions,
        .lock_losses = s_preview.sync.lock_losses,
        .frames_completed = s_preview.frames_completed,
        .frames_sent = s_preview.frames_sent,
        .frames_dropped = s_preview.dropped > UINT32_MAX ?
            UINT32_MAX : (uint32_t)s_preview.dropped,
        .line_period_samples = s_preview.sync.line_period_samples,
        .last_hsync_width = s_preview.sync.last_hsync_width,
        .last_vsync_width = s_preview.sync.last_vsync_width,
        .sync_threshold = c5vrx_cvbs_sync_threshold(&s_preview.sync),
        .horizontal_locked = s_preview.sync.horizontal_locked,
        .vertical_locked = s_preview.sync.vertical_locked,
        .staged_dropped_samples = s_preview.stage.dropped_bytes,
        .staged_peak_bytes = (uint16_t)(s_preview.stage.peak_bytes > 0xFFFFu ?
            0xFFFFu : s_preview.stage.peak_bytes),
        .width = s_preview.width,
        .height = s_preview.height,
    };
    taskENTER_CRITICAL(&s_preview.lock);
    s_preview.telemetry = next;
    taskEXIT_CRITICAL(&s_preview.lock);
}

void c5vrx_usb_preview_submit(const uint8_t *cvbs, size_t samples)
{
    if (!cvbs || !samples) return;
    taskENTER_CRITICAL(&s_preview.lock);
    /* Notify inside the same critical section: the worker clears its task
     * handle under this lock too, so a stale handle can never be woken. */
    if (s_preview.running && s_preview.task &&
        c5vrx_sample_ring_write(&s_preview.stage, cvbs, samples)) {
        xTaskNotifyGive(s_preview.task);
    }
    taskEXIT_CRITICAL(&s_preview.lock);
}

void c5vrx_usb_preview_ingest(const uint8_t *cvbs, size_t samples)
{
    if (!s_preview.running || !cvbs) return;
    ingest_samples(cvbs, samples);
    update_telemetry();
}

static void ingest_samples(const uint8_t *cvbs, size_t samples)
{
    for (size_t n = 0; n < samples; ++n) {
        const uint8_t value = cvbs[n] & 0x3fu;
        c5vrx_cvbs_sync_event_t event;
        if (c5vrx_cvbs_sync_consume(&s_preview.sync, value, &event)) {
            if (event.vertical) {
                s_preview.current_line = false;
                s_preview.current_row = C5VRX_CVBS_SYNC_NO_LINE;
                s_preview.x = 0u;
            } else if (event.horizontal) {
                s_preview.current_line = false;
                s_preview.x = 0u;
                s_preview.line_phase =
                    (uint32_t)(event.sample_index - event.sync_start);
                s_preview.line_period = event.line_period_samples;
                if (event.locked &&
                    event.field_line >= FIRST_ACTIVE_FIELD_LINE &&
                    event.field_line <
                        FIRST_ACTIVE_FIELD_LINE + ACTIVE_FIELD_LINES) {
                    /* Bob-style per-field mapping: every scanned line of
                     * THIS field lands somewhere in the full height, so
                     * PAL ~50 / NTSC ~59.94 temporal updates survive at
                     * every resolution rung (issue #5 section 12). */
                    s_preview.current_row =
                        c5vrx_preview_row_for_active_line(
                            (uint16_t)(event.field_line -
                                       FIRST_ACTIVE_FIELD_LINE),
                            ACTIVE_FIELD_LINES, s_preview.height);
                    s_preview.current_line = true;
                }
            }
        }

        if (s_preview.current_line &&
            s_preview.current_row < s_preview.height &&
            s_preview.x < s_preview.width) {
            const uint32_t active_start =
                s_preview.line_period * NOMINAL_ACTIVE_START /
                NOMINAL_LINE_SAMPLES;
            const uint32_t active_samples =
                s_preview.line_period * NOMINAL_ACTIVE_SAMPLES /
                NOMINAL_LINE_SAMPLES;
            const uint32_t target = active_start +
                s_preview.x * active_samples / s_preview.width;
            if (s_preview.line_phase >= target) {
                s_preview.frame[s_preview.fill_index]
                    [s_preview.current_row * s_preview.width +
                     s_preview.x] = (uint8_t)((value * 255u + 31u) / 63u);
                ++s_preview.x;
                if (s_preview.x == s_preview.width) {
                    s_preview.current_line = false;
                    if (s_preview.current_row ==
                        s_preview.height - 1u)
                        publish_frame();
                }
            }
        }
        ++s_preview.line_phase;
    }
}

/**
 * link.c - BitScrambler loopback demodulation of raw ring snapshots and hex
 * dumps for the host (CONFIG_C5VRX_LINK_MODE).
 *
 * The ESP32-C5 has one BitScrambler. The production build decorates it on
 * PARLIO TX for the analog DAC output; this build leaves PARLIO TX unused and
 * runs the same Phase5 core (fm_loop.bsasm, trailing bytes added for bounded
 * runs) memory-to-memory on a snapshot of the RX ring. Each run demodulates
 * ~300 us of I/Q (4.8 NTSC lines) in DMA time; the CPU only copies and prints.
 */

#include "link.h"

#include <stdio.h>
#include <string.h>

#include "driver/bitscrambler_loopback.h"
#include "esp_attr.h"
#include "esp_heap_caps.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "hal/bitscrambler_peri_select.h"
#include "sdkconfig.h"

#include "grab.h"
#include "uart_link.h"
#include "freertos/semphr.h"
#include "rf.h"
#include "video.h"

#if !CONFIG_C5VRX_LINK_MODE
/* Production build: the BitScrambler belongs to PARLIO TX; nothing here. */
bool link_dump_active(void) { return false; }
esp_err_t link_init(void) { return ESP_ERR_NOT_SUPPORTED; }
void link_dump(void) {}
void link_grab(void) {}
void link_stream_toggle(void) {}
void link_pack_toggle(void) {}
#else

BITSCRAMBLER_PROGRAM(s_fm_loop_program, "fm_loop");

/* One snapshot is at most the ring minus the descriptor being written:
 * three 4092-byte descriptors plus the 16-byte tail. Static, DMA-capable
 * SRAM: the heap of this firmware is too fragmented for two 16 KiB blocks. */
#define IN_BYTES   (3u * 4092u + 16u)
#define OUT_BYTES  (IN_BYTES + 64u)                 /* 1:1 bytes plus trailing garbage */

static bitscrambler_handle_t s_bs;
static SemaphoreHandle_t s_grab_lock;         /* grab_frame() is not reentrant */
static volatile bool s_streaming = CONFIG_C5VRX_LINK_STREAM;
static volatile bool s_pack4;
static void stream_task(void *arg);
static DMA_ATTR __attribute__((aligned(64))) uint8_t s_in[IN_BYTES];
static DMA_ATTR __attribute__((aligned(64))) uint8_t s_out[OUT_BYTES];
static volatile bool s_dumping;

bool link_dump_active(void)
{
    return s_dumping;
}

esp_err_t link_init(void)
{
    printf("[LINK] free heap %u bytes, largest DMA block %u bytes\n",
           (unsigned)heap_caps_get_free_size(MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL),
           (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL));

    /* Any idle peripheral works as the attachment; I2S0 is unused here and
     * is what the IDF loopback example uses. PARLIO must stay free for RX. */
    esp_err_t err = bitscrambler_loopback_create(&s_bs, SOC_BITSCRAMBLER_ATTACH_I2S0, OUT_BYTES);
    if (err != ESP_OK) {
        printf("[LINK] bitscrambler_loopback_create: %s\n", esp_err_to_name(err));
        return err;
    }
    err = bitscrambler_load_program(s_bs, s_fm_loop_program);
    if (err != ESP_OK) {
        printf("[LINK] bitscrambler_load_program: %s\n", esp_err_to_name(err));
        return err;
    }
    printf("[LINK] loopback BitScrambler ready (fm_loop, %u byte snapshots); 'v' dumps raw + cvbs\n",
           (unsigned)IN_BYTES);
    grab_init();
    printf("[LINK] frame grabber ready (%ux%u); 'g' grabs a frame, RX descriptors up to %u bytes\n",
           (unsigned)GRAB_W, (unsigned)GRAB_H, (unsigned)video_rx_max_descriptor());
    s_grab_lock = xSemaphoreCreateMutex();
    if (!s_grab_lock) return ESP_ERR_NO_MEM;
    if ((err = uart_link_init()) != ESP_OK) {
        printf("[LINK] UART link: %s\n", esp_err_to_name(err));
        return err;
    }
    video_set_rx_gain(56);   /* manual gain from here on; link_gain_control() moves it */
    xTaskCreate(stream_task, "link_stream", 4096, NULL, 2, NULL);
    printf("[LINK] streaming %s; 'S' toggles, 'P' toggles 4-bit rows\n", s_streaming ? "on" : "off");
    return ESP_OK;
}

static void print_hex_block(const char *name, const uint8_t *p, size_t n)
{
    static const char k_hex[] = "0123456789abcdef";
    static char row[64u * 2u + 2u];
    printf("[DUMP %s len=%u gain=%u freq=%u bw40=%d]\n", name, (unsigned)n,
           (unsigned)(rf_get_rx_gain_reg() >> 24), (unsigned)rf_get_frequency_mhz(),
           rf_get_analog_bandwidth() ? 1 : 0);
    for (size_t off = 0u; off < n; off += 64u) {
        size_t len = n - off < 64u ? n - off : 64u;
        for (size_t k = 0u; k < len; ++k) {
            row[2u * k]      = k_hex[p[off + k] >> 4];
            row[2u * k + 1u] = k_hex[p[off + k] & 0x0fu];
        }
        row[2u * len] = '\n';
        row[2u * len + 1u] = '\0';
        fputs(row, stdout);
    }
    printf("[DUMP end %s]\n", name);
    fflush(stdout);
}

void link_dump(void)
{
    if (!s_bs) {
        printf("[DUMP error link not initialised]\n");
        return;
    }
    s_dumping = true;
    vTaskDelay(pdMS_TO_TICKS(60));   /* let a status line already in flight finish */

    size_t n = video_copy_recent_rx(s_in, IN_BYTES);
    if (n == 0u) {
        printf("[DUMP error no completed RX descriptors]\n");
        s_dumping = false;
        return;
    }
    /* Keep the run a multiple of the core's 16-bit reads. */
    n &= ~(size_t)1u;
    print_hex_block("raw", s_in, n);

    size_t written = 0u;
    int64_t t0 = esp_timer_get_time();
    esp_err_t err = bitscrambler_loopback_run(s_bs, s_in, n, s_out, OUT_BYTES, &written);
    int64_t dt = esp_timer_get_time() - t0;
    if (err != ESP_OK) {
        printf("[DUMP error loopback %s after %lld us, written=%u]\n", esp_err_to_name(err), dt, (unsigned)written);
        fflush(stdout);
        s_dumping = false;
        return;
    }
    printf("[DUMP info loopback %u -> %u bytes in %lld us]\n", (unsigned)n, (unsigned)written, dt);
    print_hex_block("cvbs", s_out, written < n ? written : n);
    s_dumping = false;
}

static uint8_t s_img[GRAB_H][GRAB_W];

/* The grabber busy-waits on the ring for one to three fields; run it above
 * the AGC task so no line is lost to preemption. */
static int locked_grab(uint8_t *img, grab_info_t *info, int timeout_ms)
{
    xSemaphoreTake(s_grab_lock, portMAX_DELAY);
    UBaseType_t prio = uxTaskPriorityGet(NULL);
    vTaskPrioritySet(NULL, 10);
    int rows = grab_frame(img, info, timeout_ms);
    vTaskPrioritySet(NULL, prio);
    xSemaphoreGive(s_grab_lock);
    return rows;
}

/* Grab and send forever while streaming. uart_link_send_frame() blocks while
 * the TX buffer is full, so the loop runs at the UART rate (about 10 frames/s
 * with 8-bit rows at 4 Mbaud, twice that with 4-bit rows) and yields the CPU
 * to the AGC and console tasks while it waits. */
typedef struct {
    int frames, complete, partial, empty;
    int err_timeout, err_lost, err_nohsync, err_novsync, err_other;
    int grab_ms_sum, grab_ms_max, fields_sum, late, nosync, acquires, acquire_ms_sum;
    float jitter_sum;
    int jitter_n;
    int64_t send_us_sum;
    float power_sum, clip_sum;
    int big_err, max_err, wide_hits, fifo_ovf;
} stream_stats_t;

static void stats_add(stream_stats_t *st, int rows, const grab_info_t *info)
{
    st->frames++;
    if (rows == GRAB_H) st->complete++;
    else if (rows > 0) st->partial++;
    else st->empty++;
    const char *e = info->error;
    if (e) {
        if (strstr(e, "timeout")) st->err_timeout++;
        else if (strstr(e, "lost")) st->err_lost++;
        else if (strstr(e, "horizontal")) st->err_nohsync++;
        else if (strstr(e, "vertical")) st->err_novsync++;
        else st->err_other++;
    }
    st->grab_ms_sum += info->grab_ms;
    if (info->grab_ms > st->grab_ms_max) st->grab_ms_max = info->grab_ms;
    st->fields_sum += info->fields;
    st->late += info->late;
    st->nosync += info->nosync;
    if (info->acquire_ms > 0 || info->windows > 0) {
        st->acquires++;
        st->acquire_ms_sum += info->acquire_ms;
    }
    if (info->jitter_ns > 0.0f) {
        st->jitter_sum += info->jitter_ns;
        st->jitter_n++;
    }
    st->big_err += info->big_err;
    if (info->max_err > st->max_err) st->max_err = info->max_err;
    st->wide_hits += info->wide_hits;
    st->fifo_ovf += info->fifo_ovf;
}

static void stats_print(const stream_stats_t *st, int ms)
{
    int n = st->frames ? st->frames : 1;
    printf("[STREAM] %d ms: frames=%d complete=%d partial=%d empty=%d | timeout=%d lost=%d nohsync=%d novsync=%d other=%d"
           " | grab avg=%d max=%d ms fields/frame=%.1f late=%d nosync=%d acquires=%d (avg %d ms) jitter=%.0f ns send=%lld us/frame"
           " | gain=%u power=%.1f clip=%.1f%% | big_err=%d max_err=%d wide=%d fifo_ovf=%d\n",
           ms, st->frames, st->complete, st->partial, st->empty,
           st->err_timeout, st->err_lost, st->err_nohsync, st->err_novsync, st->err_other,
           st->grab_ms_sum / n, st->grab_ms_max, (double)st->fields_sum / n, st->late, st->nosync,
           st->acquires, st->acquires ? st->acquire_ms_sum / st->acquires : 0,
           st->jitter_n ? (double)(st->jitter_sum / st->jitter_n) : 0.0,
           (long long)(st->send_us_sum / n), (unsigned)(rf_get_rx_gain_reg() >> 24),
           (double)(st->power_sum / n), (double)(st->clip_sum * 100.0f / n),
           st->big_err, st->max_err, st->wide_hits, st->fifo_ovf);
}

/* Link-mode gain control, once per frame and never during a grab. The FM
 * phase demodulator does not care about amplitude between a few LSB and
 * full scale, so the window is wide and the steps small; what it must avoid
 * are the mid-frame gain jumps of the video receiver's AGC (which decided
 * from 6.4 us samples and hunted several times a second on this board). */
static void link_gain_control(const grab_info_t *info)
{
    int g = video_rx_gain(), ng = g;
    if (info->power <= 0.0f) return;
    if (info->clip > 0.05f || info->power > 55.0f) ng -= 4;
    else if (info->power > 40.0f) ng -= 2;
    else if (info->power < 5.0f) ng += 4;
    else if (info->power < 12.0f) ng += 2;
    if (ng < 16) ng = 16;
    if (ng > 62) ng = 62;
    if (ng != g) video_set_rx_gain((uint8_t)ng);
}

static void stream_task(void *arg)
{
    (void)arg;
    static uint8_t img[GRAB_H][GRAB_W];
    uint8_t frame = 0u;
    stream_stats_t st;
    memset(&st, 0, sizeof(st));
    int64_t t_stats = esp_timer_get_time();
    for (;;) {
        if (!s_streaming || s_dumping) {
            vTaskDelay(pdMS_TO_TICKS(50));
            t_stats = esp_timer_get_time();
            memset(&st, 0, sizeof(st));
            continue;
        }
        grab_info_t info;
        int rows = locked_grab(&img[0][0], &info, 300);
        int64_t t_send = esp_timer_get_time();
        uart_link_send_frame(&img[0][0], rows, &info, frame++, s_pack4);
        st.send_us_sum += esp_timer_get_time() - t_send;
        stats_add(&st, rows, &info);
        if (info.error && strstr(info.error, "lost")) {
            /* Show how the lock was lost, at most once per second. */
            static int64_t t_last_dump;
            int64_t tn = esp_timer_get_time();
            if (tn - t_last_dump > 1000000) {
                t_last_dump = tn;
                grab_trace_t tr[GRAB_TRACE_LEN];
                int nt = grab_trace(tr, GRAB_TRACE_LEN);
                printf("[LOST] last %d rows (line:err/result):", nt);
                for (int k = 0; k < nt; ++k) printf(" %ld:%d%c", (long)tr[k].line, tr[k].err, tr[k].result);
                printf("\n");
            }
        }
        link_gain_control(&info);
        st.power_sum += info.power;
        st.clip_sum += info.clip;
        if (rows == 0) vTaskDelay(pdMS_TO_TICKS(20));   /* no signal: let the console work */

        int64_t now = esp_timer_get_time();
        if (now - t_stats >= 1000000) {
            stats_print(&st, (int)((now - t_stats) / 1000));
            memset(&st, 0, sizeof(st));
            t_stats = now;
        }
    }
}

void link_stream_toggle(void)
{
    s_streaming = !s_streaming;
    printf("[LINK] streaming %s\n", s_streaming ? "on" : "off");
}

void link_pack_toggle(void)
{
    s_pack4 = !s_pack4;
    printf("[LINK] UART rows %s\n", s_pack4 ? "4-bit" : "8-bit");
}

void link_grab(void)
{
    s_dumping = true;
    vTaskDelay(pdMS_TO_TICKS(60));   /* let a status line already in flight finish */

    grab_info_t info;
    int rows = locked_grab(&s_img[0][0], &info, 400);

    printf("[FRAME w=%u h=%u rows=%d std=%s line_us=%.3f fields=%d acquire_ms=%d grab_ms=%d late=%d nosync=%d "
           "sync=%.2f blank=%.2f jitter_ns=%.0f windows=%d window_us=%d row_us=%d freq=%u gain=%u error=%s]\n",
           (unsigned)GRAB_W, (unsigned)GRAB_H, rows, info.pal ? "PAL" : "NTSC", (double)info.line_us,
           info.fields, info.acquire_ms, info.grab_ms, info.late, info.nosync,
           (double)info.sync_level, (double)info.blank_level, (double)info.jitter_ns,
           info.windows, info.windows ? info.window_us / info.windows : 0,
           rows ? info.row_us / rows : 0,
           (unsigned)rf_get_frequency_mhz(), (unsigned)(rf_get_rx_gain_reg() >> 24),
           info.error ? info.error : "none");
    if (rows > 0) {
        static const char k_hex[] = "0123456789abcdef";
        static char line[4u + 2u * GRAB_W + 2u];
        for (int y = 0; y < GRAB_H; ++y) {
            line[0] = (char)('0' + y / 100);
            line[1] = (char)('0' + (y / 10) % 10);
            line[2] = (char)('0' + y % 10);
            line[3] = ':';
            for (int x = 0; x < GRAB_W; ++x) {
                line[4 + 2 * x] = k_hex[s_img[y][x] >> 4];
                line[5 + 2 * x] = k_hex[s_img[y][x] & 0x0f];
            }
            line[4 + 2 * GRAB_W] = '\n';
            line[5 + 2 * GRAB_W] = '\0';
            fputs(line, stdout);
        }
    }
    printf("[FRAME end]\n");
    fflush(stdout);
    s_dumping = false;
}

#endif /* CONFIG_C5VRX_LINK_MODE */

/* C5VRX by Twotoz and contributors: bounded input-only RMT DACbit4 probe.
 * This manual observer and its RX ISR stay in flash to preserve receiver
 * task SRAM. Cache-disabled intervals may lose observations; this module
 * never supplies production clock evidence or schedules output pulses.
 * Captures durations, not absolute GPTIMER timestamps. Bright-picture aliases
 * and generated gates are not camera evidence. Owner must exclude gates.
 * No raw IQ, LUT, DMA descriptors, PHY registers or GPIO output data writes. */
#include "sync_output_probe.h"
#include <stdio.h>
#include <string.h>
#include "driver/rmt_rx.h"
#include "driver/rmt_common.h"
#include "esp_attr.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "soc/gpio_reg.h"
#include "soc/io_mux_reg.h"
#include "soc/soc.h"

#define SOP_GPIO 8u
#define SOP_MASK (1u << SOP_GPIO)
#define SOP_SYMBOLS 48u
#define SOP_WINDOW_US 50000
#define SOP_HARD_US 100000

typedef struct {
    uint32_t callbacks, symbols, halves, epochs, endings, low, h, broad;
    uint32_t h_periods, pal32, ntsc32, broad5, broad6, pal_fields, ntsc_fields;
    uint32_t bad_data, elapsed, run_start, run_width, previous_h, train_start;
    uint32_t previous_broad, broad_start, previous_group, previous_group_count;
    uint32_t h_train, broad_train;
    uint32_t generation;
    bool (*live)(uint32_t);
    volatile bool armed, stop, ended, cancelled;
    bool run_valid, run_level, run_edge;
    bool have_h, have_broad, have_group;
} sop_context_t;

/* Keep ISR pointers/buffers alive even if an SDK cleanup operation fails.
 * A quarantined channel prevents subsequent calls from reusing its storage. */
static DRAM_ATTR sop_context_t s_probe;
static DRAM_ATTR rmt_symbol_word_t s_symbols[SOP_SYMBOLS];
static rmt_channel_handle_t s_quarantined;
static bool s_busy;

static inline bool sop_near(uint32_t actual, uint32_t expected, uint32_t tolerance)
{
    return actual >= expected - tolerance && actual <= expected + tolerance;
}

static void sop_group(sop_context_t *s)
{
    if (!s->have_broad) return;
    uint32_t n = s->broad_train;
    if (n == 5u) ++s->broad5;
    if (n == 6u) ++s->broad6;
    if ((n == 5u || n == 6u) && s->have_group && s->previous_group_count == n) {
        uint32_t delta = s->broad_start - s->previous_group;
        if (n == 5u && sop_near(delta, 400000u, 1000u)) ++s->pal_fields;
        if (n == 6u && sop_near(delta, 333667u, 1000u)) ++s->ntsc_fields;
    }
    s->have_group = n == 5u || n == 6u;
    s->previous_group = s->broad_start;
    s->previous_group_count = n;
    s->have_broad = false;
}

static void sop_low(sop_context_t *s, uint32_t start, uint32_t width)
{
    ++s->low;
    if (width >= 76u && width <= 112u) {
        ++s->h;
        sop_group(s);
        if (s->have_h) {
            uint32_t delta = start - s->previous_h;
            if (delta >= 1260u && delta <= 1290u) {
                ++s->h_periods;
                if (++s->h_train == 32u) {
                    uint32_t fit = start - s->train_start;
                    if (sop_near(fit, 40960u, 32u)) ++s->pal32;
                    if (sop_near(fit, 40676u, 32u)) ++s->ntsc32;
                    s->train_start = start; s->h_train = 0u;
                }
            } else { s->train_start = start; s->h_train = 0u; }
        } else { s->train_start = start; s->h_train = 0u; }
        s->previous_h = start; s->have_h = true;
    } else if (width >= 520u && width <= 580u) {
        ++s->broad;
        s->have_h = false; s->h_train = 0u;
        if (!s->have_broad || !sop_near(start - s->previous_broad, 638u, 8u)) {
            sop_group(s);
            s->broad_start = start; s->broad_train = 0u;
        }
        s->have_broad = true; s->previous_broad = start; ++s->broad_train;
    }
}

/* An RMT symbol has two ordered level durations. Merge identical adjacent
 * levels, including across callbacks. Never classify a truncated first run
 * or the unfinished final run. 50ms fits comfortably in32-bit tick counters. */
static bool sop_half(sop_context_t *s, uint32_t duration, bool level)
{
    if (!duration) return false;  /* RMT end marker, not a zero-length edge. */
    if (s->elapsed > UINT32_MAX - duration || s->run_width > UINT32_MAX - duration) {
        ++s->bad_data; s->stop = true; return false;
    }
    if (!s->run_valid) {
        s->run_valid = true; s->run_level = level;
        s->run_start = s->elapsed; s->run_width = 0u; s->run_edge = false;
    } else if (level != s->run_level) {
        if (!s->run_level && s->run_edge) sop_low(s, s->run_start, s->run_width);
        s->run_start = s->elapsed; s->run_width = 0u;
        s->run_level = level; s->run_edge = true;
    }
    s->run_width += duration; s->elapsed += duration; ++s->halves;
    return true;
}

static bool sop_received(rmt_channel_handle_t channel,
                                   const rmt_rx_done_event_data_t *event, void *arg)
{
    (void)channel;
    sop_context_t *s = arg;
    if (!s->armed || s->stop) return false;
    if (!s->live(s->generation)) {
        s->cancelled = true; s->stop = true; return false;
    }
    ++s->callbacks;
    if (!event || event->num_symbols > SOP_SYMBOLS ||
        (event->num_symbols && !event->received_symbols) || s->ended) {
        ++s->bad_data; s->stop = true; return false;
    }
    s->symbols += (uint32_t)event->num_symbols;
    bool marker = false;
    for (size_t i = 0; i < event->num_symbols; ++i) {
        rmt_symbol_word_t word = event->received_symbols[i];
        if (!sop_half(s, word.duration0, word.level0)) { marker = true; break; }
        if (!sop_half(s, word.duration1, word.level1)) { marker = true; break; }
    }
    if (marker && !event->flags.is_last && !s->stop) {
        ++s->bad_data; s->stop = true;
    }
    if (event->flags.is_last) {
        ++s->endings; s->ended = true;
        sop_group(s);
        /* No evidence may bridge an idle stop and a later receive epoch. */
        s->run_valid = s->have_h = s->have_broad = s->have_group = false;
        s->h_train = s->broad_train = 0u;
    }
    return false;
}

static void sop_epoch(sop_context_t *s)
{
    ++s->epochs;
    s->elapsed = s->run_width = 0u;
    s->run_valid = s->run_edge = s->have_h = s->have_broad = s->have_group = false;
    s->h_train = s->broad_train = 0u; s->ended = false;
}

void sync_output_probe(uint32_t generation, bool (*live)(uint32_t))
{
    if (s_busy || s_quarantined) {
        printf("SYNC_OUTPUT_PROBE source=native_dac manual=1 refusal=busy_or_quarantined\n"); return;
    }
    if (!live || !live(generation)) {
        printf("SYNC_OUTPUT_PROBE source=native_dac manual=1 refusal=not_live\n"); return;
    }
    s_busy = true;
    uint32_t iomux = REG_READ(IO_MUX_GPIO8_REG);
    uint32_t matrix = REG_READ(GPIO_FUNC8_OUT_SEL_CFG_REG);
    uint32_t enabled = REG_READ(GPIO_ENABLE_REG) & SOP_MASK;
    /* PARLIO DATA4 index52, push-pull, no inversion. Either peripheral or
     * GPIO-controlled OE is valid: this input-only observer changes neither.
     * The root caller additionally verifies LP and unscaled1x reference LUT. */
    bool pin_ok = (matrix & GPIO_FUNC8_OUT_SEL_M) == 52u &&
        !(matrix & (GPIO_FUNC8_OUT_INV_SEL_M | GPIO_FUNC8_OE_INV_SEL_M)) &&
        enabled &&
        !(REG_READ(GPIO_PIN8_REG) & GPIO_PIN8_PAD_DRIVER_M) &&
        ((iomux & MCU_SEL_M) >> MCU_SEL_S) == 1u;
    if (!pin_ok) {
        printf("SYNC_OUTPUT_PROBE source=native_dac manual=1 refusal=pin_context matrix=0x%08lx iomux=0x%08lx enable=0x%08lx pad=0x%08lx\n",
            (unsigned long)matrix, (unsigned long)iomux, (unsigned long)enabled,
            (unsigned long)REG_READ(GPIO_PIN8_REG));
        s_busy = false; return;
    }
    memset(&s_probe, 0, sizeof(s_probe));
    s_probe.generation = generation; s_probe.live = live;
    rmt_channel_handle_t channel = NULL;
    bool enabled_rx = false, pin_changed = false;
    const char *failure = "none";
    esp_err_t err = ESP_OK, cleanup = ESP_OK;
    const rmt_rx_channel_config_t cfg = {.gpio_num = SOP_GPIO,
        .clk_src = RMT_CLK_SRC_PLL_F80M, .resolution_hz = 20000000u,
        .mem_block_symbols = 96u, .intr_priority = 2};
    const rmt_rx_event_callbacks_t callbacks = {.on_recv_done = sop_received};
    const rmt_receive_config_t receive = {.signal_range_min_ns = 2500u,
        .signal_range_max_ns = 100000u, .flags = {.en_partial_rx = true}};
    int64_t began = esp_timer_get_time();
    err = rmt_new_rx_channel(&cfg, &channel);
    if (err != ESP_OK) { failure = "new_rx"; goto done; }
    /* SDK may set FUN_IE and reselect the same GPIO function. All other
     * matrix/OE and IO MUX bits must remain unchanged before enabling RX. */
    pin_changed = REG_READ(GPIO_FUNC8_OUT_SEL_CFG_REG) != matrix ||
        (REG_READ(GPIO_ENABLE_REG) & SOP_MASK) != enabled ||
        REG_READ(IO_MUX_GPIO8_REG) != (iomux | FUN_IE);
    if (pin_changed) { failure = "pin_mutated"; goto done; }
    err = rmt_rx_register_event_callbacks(channel, &callbacks, &s_probe);
    if (err != ESP_OK) { failure = "callbacks"; goto done; }
    if (!live(generation)) { s_probe.cancelled = true; failure = "not_live"; goto done; }
    err = rmt_enable(channel);
    if (err != ESP_OK) { failure = "enable"; goto done; }
    enabled_rx = true;
    s_probe.armed = true;
    sop_epoch(&s_probe);
    err = rmt_receive(channel, s_symbols, sizeof(s_symbols), &receive);
    if (err != ESP_OK) { failure = "receive"; goto done; }
    while (esp_timer_get_time() - began < SOP_WINDOW_US && !s_probe.stop) {
        if (!live(generation)) { s_probe.cancelled = true; failure = "not_live"; break; }
        if (s_probe.ended) {
            sop_epoch(&s_probe);
            err = rmt_receive(channel, s_symbols, sizeof(s_symbols), &receive);
            if (err != ESP_OK) { failure = "restart"; break; }
        }
        if (esp_timer_get_time() - began >= SOP_HARD_US) { failure = "timeout"; break; }
        vTaskDelay(1u);
    }
done:
    s_probe.armed = false;
    if (s_probe.bad_data) failure = "bad_data";
    else if (s_probe.cancelled) failure = "not_live";
    if (channel) {
        if (enabled_rx) {
            esp_err_t stopped = rmt_disable(channel);
            if (stopped != ESP_OK) cleanup = stopped;
        }
        const rmt_rx_event_callbacks_t no_callbacks = {0};
        esp_err_t detached = rmt_rx_register_event_callbacks(channel, &no_callbacks, NULL);
        if (detached != ESP_OK) cleanup = detached;
        esp_err_t deleted = rmt_del_channel(channel);
        if (deleted != ESP_OK) { cleanup = deleted; s_quarantined = channel; }
    }
    /* Restore input-enable and mux selection even on allocation/setup errors.
     * No writes to GPIO_OUT or GPIO_OUT_W1TS/W1TC are made. */
    if (REG_READ(IO_MUX_GPIO8_REG) != iomux) REG_WRITE(IO_MUX_GPIO8_REG, iomux);
    if (REG_READ(GPIO_FUNC8_OUT_SEL_CFG_REG) != matrix)
        REG_WRITE(GPIO_FUNC8_OUT_SEL_CFG_REG, matrix);
    if ((REG_READ(GPIO_ENABLE_REG) & SOP_MASK) != enabled)
        REG_WRITE(enabled ? GPIO_ENABLE_W1TS_REG : GPIO_ENABLE_W1TC_REG, SOP_MASK);
    bool restored = REG_READ(IO_MUX_GPIO8_REG) == iomux &&
        REG_READ(GPIO_FUNC8_OUT_SEL_CFG_REG) == matrix &&
        (REG_READ(GPIO_ENABLE_REG) & SOP_MASK) == enabled;
    bool exclusion = !s_probe.cancelled && live(generation);
    printf("SYNC_OUTPUT_PROBE source=native_dac manual=1 gpio=8 bit=4 output_x100=100 "
        "self_gate_excluded=%u absolute_time=0 camera_qualified=0 restored=%u "
        "pin_mutated=%u error=%s api=%d cleanup=%d duration_us=%lld "
        "callbacks=%lu symbols=%lu halves=%lu epochs=%lu endings=%lu low=%lu "
        "h=%lu broad=%lu h_periods=%lu pal32=%lu ntsc32=%lu broad5=%lu broad6=%lu "
        "pal_fields=%lu ntsc_fields=%lu bad_data=%lu cancelled=%u\n",
        (unsigned)exclusion, (unsigned)restored, (unsigned)pin_changed,
        failure, (int)err, (int)cleanup, (long long)(esp_timer_get_time() - began),
        (unsigned long)s_probe.callbacks, (unsigned long)s_probe.symbols,
        (unsigned long)s_probe.halves, (unsigned long)s_probe.epochs,
        (unsigned long)s_probe.endings, (unsigned long)s_probe.low,
        (unsigned long)s_probe.h, (unsigned long)s_probe.broad,
        (unsigned long)s_probe.h_periods, (unsigned long)s_probe.pal32,
        (unsigned long)s_probe.ntsc32, (unsigned long)s_probe.broad5,
        (unsigned long)s_probe.broad6, (unsigned long)s_probe.pal_fields,
        (unsigned long)s_probe.ntsc_fields, (unsigned long)s_probe.bad_data,
        (unsigned)s_probe.cancelled);
    s_busy = false;
}

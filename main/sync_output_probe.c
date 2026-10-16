/* C5VRX by Twotoz and contributors: bounded input-only RMT DACbit4 probe.
 * This manual observer and its RX ISR stay in flash to preserve receiver
 * task SRAM. Cache-disabled intervals may lose observations; this module
 * never supplies production clock evidence or schedules output pulses.
 * Captures durations, not absolute GPTIMER timestamps. Bright-picture aliases
 * and generated gates are not camera evidence. Owner must exclude gates.
 * GPIO2 (unused MTMS pad) mirrors PARLIO DATA4 independently of DAC OE.
 * Only the isolated GPIO2 data bit may be driven for a private clock marker.
 * No raw IQ, LUT, DMA descriptors, PHY registers or DAC output data writes. */
#include "sync_output_probe.h"
#include "sync_edge_clock.h"
#include "sync_phase_bracket.h"
#include <stdio.h>
#include <string.h>
#include "driver/rmt_rx.h"
#include "driver/rmt_common.h"
#include "esp_attr.h"
#include "esp_timer.h"
#include "esp_rom_sys.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "soc/gpio_reg.h"
#include "soc/io_mux_reg.h"
#include "soc/soc.h"

#define SOP_GPIO 2u
#define SOP_MASK (1u << SOP_GPIO)
_Static_assert((SOP_MASK & UINT32_C(0x01801b00)) == 0u,
               "native witness must be outside all six DAC output enables");
#define SOP_SYMBOLS 48u
#define SOP_WINDOW_US 50000
#define SOP_HARD_US 100000
#define SOP_ARM_MAX_US 200

typedef struct {
    uint32_t callbacks, symbols, halves, epochs, endings, low, h, broad;
    uint32_t h_periods, pal32, ntsc32, broad5, broad6, pal_fields, ntsc_fields;
    uint32_t bad_data, elapsed, run_start, run_width, previous_h, train_start;
    uint32_t previous_broad, broad_start, previous_group, previous_group_count;
    uint32_t h_train, broad_train;
    uint32_t generation;
    uint32_t discard_before,first_duration;
    bool first_level, marker_required, marker_invalid;
    uint32_t prefix[3];
    unsigned prefix_n;
    bool (*live)(uint32_t);
    volatile bool armed, stop, ended, cancelled, rate_limited;
    bool run_valid, run_level, run_edge;
    bool have_h, have_broad, have_group;
} sop_context_t;

/* Keep ISR pointers/buffers alive even if an SDK cleanup operation fails.
 * A quarantined channel prevents subsequent calls from reusing its storage. */
static DRAM_ATTR sop_context_t s_probe;
static DRAM_ATTR rmt_symbol_word_t s_symbols[SOP_SYMBOLS];
static rmt_channel_handle_t s_quarantined;
static bool s_busy;
static bool s_clock_fault;

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
    if(start<s->discard_before)return; /* private marker/handoff is not camera sync */
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
    if(s->prefix_n<3u)s->prefix[s->prefix_n++]=duration;
    if(!s->first_duration){
        s->first_duration=duration;s->first_level=level;
        if(s->marker_required && (!level || duration<280u || duration>400u)) {
            s->marker_invalid=true;s->stop=true;return false;
        }
    }
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
    if(s->callbacks>=256u){s->rate_limited=true;s->stop=true;return false;}
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

static void sync_output_probe_run(uint32_t generation, bool (*live)(uint32_t), bool edge_clock)
{
    if (s_busy || s_quarantined || s_clock_fault) {
        printf("SYNC_OUTPUT_PROBE source=native_dac manual=1 refusal=busy_or_quarantined\n"); return;
    }
    if (!live || !live(generation)) {
        printf("SYNC_OUTPUT_PROBE source=native_dac manual=1 refusal=not_live\n"); return;
    }
    s_busy = true;
    uint32_t iomux = REG_READ(IO_MUX_GPIO8_REG);
    uint32_t matrix = REG_READ(GPIO_FUNC8_OUT_SEL_CFG_REG);
    uint32_t enabled = REG_READ(GPIO_ENABLE_REG) & (1u << 8);
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
    /* The native witness must continue seeing PARLIO while all six DAC pads
     * are gated. GPIO2 is not wired to the DAC and must be unused/disabled.
     * Its MTMS role is already unavailable in this RX GPIO3/4/5 topology.
     * Refuse an occupied output rather than taking it from another owner. */
    uint32_t mirror_iomux = REG_READ(IO_MUX_GPIO2_REG);
    uint32_t mirror_matrix = REG_READ(GPIO_FUNC2_OUT_SEL_CFG_REG);
    uint32_t mirror_pin = REG_READ(GPIO_PIN2_REG);
    uint32_t mirror_enable = REG_READ(GPIO_ENABLE_REG) & SOP_MASK;
    uint32_t mirror_output = REG_READ(GPIO_OUT_REG) & SOP_MASK;
    if (mirror_enable || (mirror_pin & GPIO_PIN2_PAD_DRIVER_M)) {
        printf("SYNC_OUTPUT_PROBE source=native_dac manual=1 refusal=mirror_occupied\n");
        s_busy = false; return;
    }
    uint32_t witness_iomux = (mirror_iomux & ~(MCU_SEL_M | FUN_PU | FUN_PD)) |
        (1u << MCU_SEL_S) | FUN_IE;
    uint32_t witness_matrix = (mirror_matrix & ~(GPIO_FUNC2_OUT_SEL_M |
        GPIO_FUNC2_OUT_INV_SEL_M | GPIO_FUNC2_OE_INV_SEL_M)) | (edge_clock?256u:52u) | GPIO_FUNC2_OE_SEL_M;
    memset(&s_probe, 0, sizeof(s_probe));
    s_probe.generation = generation; s_probe.live = live;
    s_probe.marker_required=edge_clock;
    if(edge_clock)s_probe.discard_before=UINT32_MAX; /* IRQs may precede handoff publication. */
    rmt_channel_handle_t channel = NULL;
    bool enabled_rx = false, pin_changed = false;
    bool clock_attempted=false,clock_read_ok=false,clock_closed=true;
    uint64_t clock_ticks=0;
    uint32_t common_filter_ns=0;
    int64_t arm_us=-1;
    uint64_t rise_before=0,rise_after=0,fall_before=0,fall_after=0;
    const char *failure = "none";
    esp_err_t err = ESP_OK, cleanup = ESP_OK;
    const rmt_rx_channel_config_t cfg = {.gpio_num = SOP_GPIO,
        .clk_src = RMT_CLK_SRC_PLL_F80M, .resolution_hz = 20000000u,
        .mem_block_symbols = 96u, .intr_priority = 2};
    const rmt_rx_event_callbacks_t callbacks = {.on_recv_done = sop_received};
    const rmt_receive_config_t receive = {.signal_range_min_ns = edge_clock?0u:2500u,
        .signal_range_max_ns = edge_clock?1000000u:100000u, .flags = {.en_partial_rx = true}};
    int64_t began = esp_timer_get_time();
    if(edge_clock)REG_WRITE(GPIO_OUT_W1TC_REG,SOP_MASK);
    REG_WRITE(IO_MUX_GPIO2_REG, witness_iomux);
    REG_WRITE(GPIO_FUNC2_OUT_SEL_CFG_REG, witness_matrix);
    REG_WRITE(GPIO_ENABLE_W1TS_REG, SOP_MASK);
    if (REG_READ(IO_MUX_GPIO2_REG) != witness_iomux ||
        REG_READ(GPIO_FUNC2_OUT_SEL_CFG_REG) != witness_matrix ||
        (REG_READ(GPIO_ENABLE_REG) & SOP_MASK) != SOP_MASK) {
        failure = "mirror_readback"; goto done;
    }
    err = rmt_new_rx_channel(&cfg, &channel);
    if (err != ESP_OK) { failure = "new_rx"; goto done; }
    /* SDK may set FUN_IE and reselect the same GPIO function. All other
     * matrix/OE and IO MUX bits must remain unchanged before enabling RX. */
    pin_changed = REG_READ(GPIO_FUNC2_OUT_SEL_CFG_REG) != witness_matrix ||
        (REG_READ(GPIO_ENABLE_REG) & SOP_MASK) != SOP_MASK ||
        REG_READ(IO_MUX_GPIO2_REG) != witness_iomux;
    if (pin_changed) { failure = "pin_mutated"; goto done; }
    err = rmt_rx_register_event_callbacks(channel, &callbacks, &s_probe);
    if (err != ESP_OK) { failure = "callbacks"; goto done; }
    if(edge_clock) {
        clock_attempted=true;
        if(!sync_edge_clock_prepare()){failure="clock_prepare";goto done;}
        common_filter_ns=sync_edge_clock_filter_ns();
        esp_rom_delay_us(50); /* Private GPIO data drives a quiet LOW; DAC is untouched. */
    }
    if (!live(generation)) { s_probe.cancelled = true; failure = "not_live"; goto done; }
    err = rmt_enable(channel);
    if (err != ESP_OK) { failure = "enable"; goto done; }
    enabled_rx = true;
    s_probe.armed = true;
    sop_epoch(&s_probe);
    if(edge_clock) {
        /* RX idle expiry can run before its first edge. Do not let a gain
         * task preempt the short RX/ETM/mirror arm sequence. These APIs do
         * not allocate or wait; interrupts continue and a time/ended guard
         * refuses even an interrupt-delayed arm before accepting evidence. */
        bool ready=false;
        vTaskSuspendAll();
        int64_t arm_start=esp_timer_get_time();
        err=rmt_receive(channel,s_symbols,sizeof(s_symbols),&receive);
        if(err!=ESP_OK)failure="receive";
        else if(!sync_edge_clock_arm())failure="clock_arm";
        else if(esp_timer_get_time()-arm_start>SOP_ARM_MAX_US || s_probe.ended)failure="arm_gap";
        else {
            ready=true;
        }
        arm_us=esp_timer_get_time()-arm_start;
        if(arm_us>SOP_ARM_MAX_US){ready=false;failure="arm_gap";}
        if(ready) {
            uint64_t quiet;
            if(!sync_edge_clock_read(&quiet) || quiet!=0u){ready=false;failure="clock_not_quiet";}
        }
        if(ready) {
            /* The first raw edge is a recognizable private HIGH marker.
             * Neither consumer has a private/variable filter at this point.
             * Subsequent ETM events are idempotent START, never reload. */
            REG_WRITE(GPIO_OUT_W1TS_REG,SOP_MASK);
            esp_rom_delay_us(16);
            REG_WRITE(GPIO_OUT_W1TC_REG,SOP_MASK);
            esp_rom_delay_us(16);
            if(!sync_edge_clock_enable_filter()){ready=false;failure="clock_filter";}
            else {
                esp_rom_delay_us(4);
                /* A second, filtered HIGH marker measures the whole native
                 * input path's delay. Both physical edges are bracketed by
                 * timer reads and flushed GPIO data readbacks. The marker
                 * remains confined to GPIO2 and below the source barrier. */
                if(!sync_edge_clock_read(&rise_before)){ready=false;failure="clock_rise_before";}
                if(ready) {
                    __sync_synchronize();REG_WRITE(GPIO_OUT_W1TS_REG,SOP_MASK);
                    bool high=(REG_READ(GPIO_OUT_REG)&SOP_MASK)!=0u;__sync_synchronize();
                    if(!high || !sync_edge_clock_read(&rise_after)){ready=false;failure="clock_rise_after";}
                }
                if(ready) {
                    esp_rom_delay_us(16);
                    if(!sync_edge_clock_read(&fall_before)){ready=false;failure="clock_fall_before";}
                }
                if(ready) {
                    __sync_synchronize();REG_WRITE(GPIO_OUT_W1TC_REG,SOP_MASK);
                    bool low=(REG_READ(GPIO_OUT_REG)&SOP_MASK)==0u;__sync_synchronize();
                    if(!low || !sync_edge_clock_read(&fall_after)){ready=false;failure="clock_fall_after";}
                }
                if(ready)esp_rom_delay_us(16);
                if(ready)REG_WRITE(GPIO_FUNC2_OUT_SEL_CFG_REG,(witness_matrix&~GPIO_FUNC2_OUT_SEL_M)|52u);
                uint64_t native_start;
                if(ready && (!sync_edge_clock_read(&native_start) || native_start>UINT32_MAX-64u)) {
                    ready=false;failure="clock_native_start";
                } else if(ready)s_probe.discard_before=(uint32_t)native_start+common_filter_ns/50u+4u;
            }
        }
        (void)xTaskResumeAll();
        if(!ready)goto done;
    } else {
        err=rmt_receive(channel,s_symbols,sizeof(s_symbols),&receive);
        if(err!=ESP_OK){failure="receive";goto done;}
    }
    while (esp_timer_get_time() - began < SOP_WINDOW_US && !s_probe.stop) {
        if (!live(generation)) { s_probe.cancelled = true; failure = "not_live"; break; }
        if (s_probe.ended) {
            if(edge_clock){failure="epoch_end";break;} /* A restart has a new, unknown first edge. */
            sop_epoch(&s_probe);
            err = rmt_receive(channel, s_symbols, sizeof(s_symbols), &receive);
            if (err != ESP_OK) { failure = "restart"; break; }
        }
        if (esp_timer_get_time() - began >= SOP_HARD_US) { failure = "timeout"; break; }
        vTaskDelay(1u);
    }
done:
    s_probe.armed = false;
    if(edge_clock && clock_attempted)clock_read_ok=sync_edge_clock_read(&clock_ticks);
    if (s_probe.bad_data) failure = "bad_data";
    else if (s_probe.marker_invalid) failure = "bad_marker";
    else if (s_probe.cancelled) failure = "not_live";
    else if (s_probe.rate_limited) failure = "event_rate";
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
    /* Restore the unused mirror pad even on allocation/setup errors.
     * Only the private GPIO2 marker data bit is restored; no DAC data writes. */
    REG_WRITE(GPIO_ENABLE_W1TC_REG, SOP_MASK);
    if(clock_attempted) {
        clock_closed=sync_edge_clock_close();
        if(!clock_closed)s_clock_fault=true;
        uint32_t rmt_rise=s_probe.prefix[0]+s_probe.prefix[1];
        uint32_t rmt_fall=rmt_rise+s_probe.prefix[2];
        sync_phase_bracket_t phase=s_probe.prefix_n==3u?
            sync_phase_bracket(rmt_rise,rmt_fall,rise_before,rise_after,fall_before,fall_after):
            (sync_phase_bracket_t){0};
        printf("SYNC_PHASE_BRACKET manual=1 absolute_time=0 camera_qualified=0 prefix_n=%u rmt_rise=%lu rmt_fall=%lu rise_before=%llu rise_after=%llu fall_before=%llu fall_after=%llu valid=%u offset_lo=%lld offset_hi=%lld\n",
            s_probe.prefix_n,(unsigned long)rmt_rise,(unsigned long)rmt_fall,
            (unsigned long long)rise_before,(unsigned long long)rise_after,
            (unsigned long long)fall_before,(unsigned long long)fall_after,
            (unsigned)phase.valid,(long long)phase.lo,(long long)phase.hi);
        printf("SYNC_EDGE_CLOCK manual=1 absolute_time=0 camera_qualified=0 common_filter_ns=%lu timer_hz=20000000 ticks=%llu read_ok=%u edges_seen=%u closed=%u arm_us=%lld marker_ticks=%lu marker_level=%u marker_ok=%u native_after_ticks=%lu error=%s\n",
            (unsigned long)common_filter_ns,(unsigned long long)clock_ticks,
            (unsigned)clock_read_ok,(unsigned)(s_probe.halves!=0u),
            (unsigned)clock_closed,(long long)arm_us,(unsigned long)s_probe.first_duration,
            (unsigned)s_probe.first_level,(unsigned)(s_probe.first_level &&
                s_probe.first_duration>=280u && s_probe.first_duration<=400u),
            (unsigned long)s_probe.discard_before,sync_edge_clock_error());
    }
    if(edge_clock && (REG_READ(GPIO_OUT_REG)&SOP_MASK)!=mirror_output)
        REG_WRITE(mirror_output?GPIO_OUT_W1TS_REG:GPIO_OUT_W1TC_REG,SOP_MASK);
    if (REG_READ(IO_MUX_GPIO2_REG) != mirror_iomux) REG_WRITE(IO_MUX_GPIO2_REG, mirror_iomux);
    if (REG_READ(GPIO_FUNC2_OUT_SEL_CFG_REG) != mirror_matrix)
        REG_WRITE(GPIO_FUNC2_OUT_SEL_CFG_REG, mirror_matrix);
    bool restored = REG_READ(IO_MUX_GPIO2_REG) == mirror_iomux &&
        REG_READ(GPIO_FUNC2_OUT_SEL_CFG_REG) == mirror_matrix &&
        REG_READ(GPIO_PIN2_REG) == mirror_pin &&
        (REG_READ(GPIO_ENABLE_REG) & SOP_MASK) == mirror_enable &&
        (REG_READ(GPIO_OUT_REG) & SOP_MASK) == mirror_output &&
        REG_READ(IO_MUX_GPIO8_REG) == iomux &&
        REG_READ(GPIO_FUNC8_OUT_SEL_CFG_REG) == matrix &&
        (REG_READ(GPIO_ENABLE_REG) & (1u << 8)) == enabled;
    bool exclusion = !s_probe.cancelled && live(generation);
    printf("SYNC_OUTPUT_PROBE source=native_dac manual=1 gpio=2 mirror_source_gpio=8 bit=4 output_x100=100 "
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

void sync_output_probe(uint32_t generation,bool (*live)(uint32_t))
{ sync_output_probe_run(generation,live,false); }
void sync_output_clock_probe(uint32_t generation,bool (*live)(uint32_t))
{ sync_output_probe_run(generation,live,true); }

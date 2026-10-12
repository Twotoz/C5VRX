/* C5VRX-5 by Twotoz and the C5VRX contributors: OSD key bench, step 1
 * (osd_key_logic.h). I2S0 DOUT carries a 1-bit key that transport.c starts
 * in the same interrupt-free instant as the menu/status raster on PARLIO TX;
 * both run from PLL_F240M, so the key cannot drift against the raster.
 * Only the menu/status raster ever gets a key: live video has no genlock
 * yet (step 2), and every TX stop parks the key pin low first.
 *
 * Hardware (bench, not on the tested board): OSD_KEY_GPIO -> Schottky diode
 * (anode at the GPIO) -> resistor -> DAC output node. Start high (a few kOhm)
 * and lower it until the white is readable; a low key never conducts.
 *
 * The owned key chain follows transport.c: 625 GDMA descriptors (one per
 * PAL frame line) with cleared suc_eof and quiet interrupts, so the IDF I2S
 * driver only configures clock, slots and pin and never runs its own DMA. */
#include "receiver.h"
#include <stdio.h>
#include <string.h>
#include "driver/gpio.h"
#include "driver/i2s_std.h"
#include "esp_heap_caps.h"
#include "esp_rom_gpio.h"
#include "esp_rom_sys.h"
#include "esp_timer.h"
#include "hal/i2s_ll.h"
#include "soc/ahb_dma_struct.h"
#include "soc/gpio_sig_map.h"
#include "soc/i2s_struct.h"
#include "menu_font.h"
#include "osd_key.h"
#include "osd_key_logic.h"

/* Key output. GPIO2 is a strap pin; the diode keeps it unloaded at reset.
 * Change this for a board where another free GPIO is reachable. */
#define OSD_KEY_GPIO 2
#define I2S0_GDMA_PERIPH 3   /* SOC_GDMA_TRIG_PERIPH_I2S0 */

static i2s_chan_handle_t s_i2s;
static int s_dma_ch = -1;
static bool s_armed, s_primed, s_running;
static int s_dx, s_dy;
static dma_descriptor_t *s_nodes;
static uint32_t *s_rows;     /* OSD_ROW_TYPES key lines */
static uint32_t s_starts, s_refused;
static const char *s_reason = "off";

static uint32_t *row_buf(osd_row_t t) { return s_rows + (size_t)t * OSD_KEY_PAL_LINE_WORDS; }

static void park_pin(void)
{
    esp_rom_gpio_connect_out_signal(OSD_KEY_GPIO, SIG_GPIO_OUT_IDX, false, false);
    gpio_set_level(OSD_KEY_GPIO, 0);
}

static int find_dma(void)
{
    for (int k = 0; k < 3; ++k)
        if (AHB_DMA.channel[k].out.out_peri_sel.peri_out_sel_chn == I2S0_GDMA_PERIPH) return k;
    return -1;
}

/* Created on first use, never at boot: an idle receiver has no I2S. */
static esp_err_t create_i2s(void)
{
    if (s_i2s) return ESP_OK;
    if (!osd_key_gpio_free(OSD_KEY_GPIO)) return ESP_ERR_INVALID_ARG;
    i2s_chan_config_t chan = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
    chan.dma_desc_num = 2;          /* driver buffers stay unused */
    chan.dma_frame_num = 8;
    esp_err_t err = i2s_new_channel(&chan, &s_i2s, NULL);
    if (err != ESP_OK) { s_i2s = NULL; return err; }
    const i2s_std_config_t std = {
        /* 240 MHz / 6 = 40 MHz mclk, bclk 20 MHz: integer dividers only. */
        .clk_cfg = {.sample_rate_hz = OSD_KEY_SAMPLE_RATE_HZ, .clk_src = I2S_CLK_SRC_PLL_240M,
                    .mclk_multiple = I2S_MCLK_MULTIPLE_128},
        .slot_cfg = I2S_STD_MSB_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_STEREO),
        .gpio_cfg = {.mclk = I2S_GPIO_UNUSED, .bclk = I2S_GPIO_UNUSED, .ws = I2S_GPIO_UNUSED,
                     .dout = OSD_KEY_GPIO, .din = I2S_GPIO_UNUSED},
    };
    err = i2s_channel_init_std_mode(s_i2s, &std);
    s_dma_ch = err == ESP_OK ? find_dma() : -1;
    if (err == ESP_OK && s_dma_ch < 0) err = ESP_ERR_NOT_FOUND;
    if (err != ESP_OK) {
        (void)i2s_del_channel(s_i2s);
        s_i2s = NULL;
        return err;
    }
    gpio_set_direction(OSD_KEY_GPIO, GPIO_MODE_OUTPUT);
    park_pin();
    return ESP_OK;
}

static void render_rows(void)
{
    for (unsigned t = 0; t < OSD_ROW_TYPES; ++t) osd_key_render_row(row_buf((osd_row_t)t), (osd_row_t)t, s_dx, s_font8x8);
    sync_dma_c2m(s_rows, OSD_ROW_TYPES * OSD_KEY_PAL_LINE_BYTES);
}

static void link_rows(void)
{
    for (unsigned j = 0; j < OSD_KEY_PAL_LINES; ++j) s_nodes[j].buffer = row_buf(osd_key_row_at(j, s_dy));
    sync_dma_c2m(s_nodes, OSD_KEY_PAL_LINES * sizeof(dma_descriptor_t));
}

static esp_err_t build_chain(void)
{
    if (!s_nodes) s_nodes = heap_caps_aligned_alloc(64u, OSD_KEY_PAL_LINES * sizeof(dma_descriptor_t),
                                                    MALLOC_CAP_DMA_DESC_AHB | MALLOC_CAP_INTERNAL);
    if (!s_rows) s_rows = heap_caps_aligned_alloc(64u, OSD_ROW_TYPES * OSD_KEY_PAL_LINE_BYTES,
                                                  MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
    if (!s_nodes || !s_rows) return ESP_ERR_NO_MEM;
    for (unsigned j = 0; j < OSD_KEY_PAL_LINES; ++j) {
        dma_descriptor_t *d = &s_nodes[j];
        memset(d, 0, sizeof(*d));
        d->dw0.size = OSD_KEY_PAL_LINE_BYTES;
        d->dw0.length = OSD_KEY_PAL_LINE_BYTES;
        d->dw0.owner = 1;
        d->next = &s_nodes[(j + 1u) % OSD_KEY_PAL_LINES];
    }
    render_rows();
    link_rows();
    return ESP_OK;
}

static void free_chain(void)
{
    heap_caps_free(s_nodes);
    heap_caps_free(s_rows);
    s_nodes = NULL;
    s_rows = NULL;
}

/* transport_start_chain, before the raster starts: load the key FIFO. */
void osd_key_prime(void)
{
    s_primed = false;
    if (!s_armed) return;
    if (s_video_std != VIDEO_STD_PAL) { ++s_refused; s_reason = "ntsc_refused"; return; }
    esp_err_t err = create_i2s();
    if (err == ESP_OK) err = build_chain();
    if (err != ESP_OK) {
        ++s_refused;
        s_reason = err == ESP_ERR_NO_MEM ? "no_memory" : "i2s_unavailable";
        printf("OSDKEY refused: %s\n", esp_err_to_name(err));
        return;
    }
    i2s_ll_tx_enable_clock(&I2S0);
    i2s_ll_tx_stop(&I2S0);
    i2s_ll_tx_reset(&I2S0);
    i2s_ll_tx_reset_fifo(&I2S0);
    AHB_DMA.out_intr[s_dma_ch].ena.val = 0;
    AHB_DMA.out_intr[s_dma_ch].clr.val = UINT32_MAX;
    AHB_DMA.channel[s_dma_ch].out.out_conf0.out_rst_chn = 1;
    AHB_DMA.channel[s_dma_ch].out.out_conf0.out_rst_chn = 0;
    __asm__ __volatile__("fence iorw, iorw" ::: "memory");
    AHB_DMA.out_link_addr[s_dma_ch].val = (uint32_t)&s_nodes[0];
    AHB_DMA.channel[s_dma_ch].out.out_link.outlink_start_chn = 1;
    esp_rom_delay_us(20);            /* GDMA fills the I2S TX FIFO */
    /* tx_update now (bounded), so the co-start below is a single store. */
    I2S0.tx_conf.tx_update = 1;
    int64_t deadline = esp_timer_get_time() + 1000;
    while (I2S0.tx_conf.tx_update && esp_timer_get_time() < deadline) {}
    if (I2S0.tx_conf.tx_update) {
        AHB_DMA.channel[s_dma_ch].out.out_link.outlink_stop_chn = 1;
        ++s_refused;
        s_reason = "update_timeout";
        return;
    }
    esp_rom_gpio_connect_out_signal(OSD_KEY_GPIO, I2SO_SD_OUT_IDX, false, false);
    s_primed = true;
}

/* transport_start_chain, interrupts masked, right after PARLIO TX starts. */
void osd_key_go(void)
{
    if (!s_primed) return;
    I2S0.tx_conf.tx_start = 1;
    s_primed = false;
    s_running = true;
    ++s_starts;
    s_reason = "running";
}

/* Every TX stop and menu exit: key pin low first, then stop I2S and GDMA. */
void osd_key_stop(void)
{
    s_primed = false;
    if (!s_i2s) return;
    park_pin();
    i2s_ll_tx_stop(&I2S0);
    if (s_dma_ch >= 0) AHB_DMA.channel[s_dma_ch].out.out_link.outlink_stop_chn = 1;
    esp_rom_delay_us(5);   /* no descriptor fetch in flight before the chain may be freed */
    s_running = false;
}

void osd_key_print(void)
{
    printf("OSDKEY armed=%u running=%u gpio=%d dx_bits=%d dx_ns=%d dy_lines=%d std=%s starts=%lu refused=%lu "
           "state=%s key=I2S0_PLL240_20MHz scope=menu_status_raster_only\n",
           s_armed, s_running, OSD_KEY_GPIO, s_dx, s_dx * 50, s_dy, s_video_std == VIDEO_STD_PAL ? "PAL" : "NTSC",
           (unsigned long)s_starts, (unsigned long)s_refused, s_reason);
}

/* Control task: O arms/disarms; < > coarse and , . fine dx; + - dy. */
void osd_key_command(int c)
{
    if (c == 'O') {
        s_armed = !s_armed;
        if (!s_armed) {
            osd_key_stop();
            free_chain();
            s_reason = "off";
        } else {
            s_reason = s_menu_active ? "armed" : "armed_waiting_for_menu";
        }
        if (s_armed && s_menu_active) menu_restart_chain();   /* co-start; the picture blinks once */
        osd_key_print();
        return;
    }
    int dx = s_dx, dy = s_dy;
    switch (c) {
    case '<': dx -= 8; break;
    case '>': dx += 8; break;
    case ',': dx -= 1; break;
    case '.': dx += 1; break;
    case '-': dy -= 1; break;
    case '+': dy += 1; break;
    default: return;
    }
    s_dx = osd_key_clamp(dx, OSD_KEY_DX_MAX);
    s_dy = osd_key_clamp(dy, OSD_KEY_DY_MAX);
    /* Running chain: rows and line pointers change in place; at most one
     * frame mixes old and new positions. Bench offsets only. */
    if (s_rows && s_nodes) { render_rows(); link_rows(); }
    osd_key_print();
}


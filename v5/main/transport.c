/* C5VRX-5 by Twotoz and the C5VRX contributors: realtime transport.
 * MODEM_DIAG -> PARLIO RX 40 MS/s -> 32 KiB cyclic ring -> TX BitScrambler
 * (U85Q) -> PARLIO TX 40 MHz -> 6-bit resistor DAC. Both GDMA rings run
 * with interrupts off and cleared suc_eof (espressif/esp-idf#19091), so no
 * DMA boundary is a stream boundary; the CPU never paces samples. */
#include "receiver.h"
#include <string.h>
#include "driver/bitscrambler.h"
#include "driver/parlio_rx.h"
#include "driver/parlio_tx.h"
#include "esp_attr.h"
#include "esp_cache.h"
#include "esp_rom_sys.h"
#include "esp_timer.h"
#include "hal/misc.h"
#include "hal/parlio_ll.h"
#include "snapshot.h"
#include "soc/ahb_dma_struct.h"
#include "soc/bitscrambler_struct.h"
#include "soc/parl_io_struct.h"
#include "soc/pcr_struct.h"

/* U85Q UNTRAINED (model 1a09f5c092e8, C5VRX PR #190): closed-form 32-phase
 * tracker on the PAIR4411 shared word, origin cells as coarse quadrant
 * tokens, linear -6..+9 MHz output window. docs/FINDINGS.md. */
BITSCRAMBLER_PROGRAM(s_u85q, "u85q");

/* 6-bit resistor DAC, LSB first (XIAO D4..D9; 8.2k/3.9k/2k/1k/470R/240R
 * plus 200R). Do not change the tested pin order. */
static const int s_dac_gpio[8] = {23, 24, 11, 12, 8, 9, -1, -1};

DMA_ATTR __attribute__((aligned(64))) uint8_t s_raw_ring[RAW_RING_BYTES];
static parlio_rx_unit_handle_t s_rx;
static parlio_rx_delimiter_handle_t s_rx_delimiter;
static parlio_tx_unit_handle_t s_tx;
static bitscrambler_handle_t s_bs;
int s_rx_dma_ch = -1, s_tx_dma_ch = -1;
ring_node_t s_rx_nodes[MAX_RING_DESCRIPTORS];
int s_rx_node_count;
static uint32_t s_tx_empty, s_rx_overflow, s_dma_faults, s_bs_overload;

void sync_dma_c2m(const void *addr, size_t size)
{
    if (!addr || !size) return;
    uint32_t start = (uint32_t)addr & ~63u, end = ((uint32_t)addr + size + 63u) & ~63u;
    (void)esp_cache_msync((void *)start, end - start,
                          ESP_CACHE_MSYNC_FLAG_DIR_C2M | ESP_CACHE_MSYNC_FLAG_UNALIGNED);
}

void sync_dma_m2c(const void *addr, size_t size)
{
    if (!addr || !size) return;
    uint32_t start = (uint32_t)addr & ~63u, end = ((uint32_t)addr + size + 63u) & ~63u;
    (void)esp_cache_msync((void *)start, end - start,
                          ESP_CACHE_MSYNC_FLAG_DIR_M2C | ESP_CACHE_MSYNC_FLAG_UNALIGNED);
}

static esp_err_t prepare_rx(void)
{
    const parlio_rx_unit_config_t cfg = {
        .trans_queue_depth = 1u,
        .max_recv_size = sizeof(s_raw_ring),
        .dma_burst_size = 32u,
        .data_width = 8u,
        .clk_src = PARLIO_CLK_SRC_DEFAULT,
        .exp_clk_freq_hz = IQ_RATE_HZ,
        .clk_in_gpio_num = -1,
        .clk_out_gpio_num = -1,
        .valid_gpio_num = -1,
        /* Same order as rf.c s_iq_pins: Q bits 0..3, I bits 4..7. */
        .data_gpio_nums = {GPIO_NUM_1, GPIO_NUM_0, GPIO_NUM_25, GPIO_NUM_7,
                           GPIO_NUM_10, GPIO_NUM_5, GPIO_NUM_3, GPIO_NUM_4},
        .flags = {.free_clk = true, .clk_gate_en = false, .allow_pd = false},
    };
    esp_err_t err = parlio_new_rx_unit(&cfg, &s_rx);
    if (err != ESP_OK) return err;
    const parlio_rx_soft_delimiter_config_t delim = {
        .sample_edge = PARLIO_SAMPLE_EDGE_POS,
        .bit_pack_order = PARLIO_BIT_PACK_ORDER_LSB,
        .eof_data_len = sizeof(s_raw_ring),
        .timeout_ticks = 0u,
    };
    err = parlio_new_rx_soft_delimiter(&delim, &s_rx_delimiter);
    if (err != ESP_OK) return err;
    return parlio_rx_unit_enable(s_rx, false);
}

static esp_err_t prepare_tx(void)
{
    parlio_tx_unit_config_t cfg = {
        .clk_src = PARLIO_CLK_SRC_DEFAULT,
        .clk_in_gpio_num = -1,
        .output_clk_freq_hz = 40000000u,
        .data_width = 8u,
        .clk_out_gpio_num = -1,
        .valid_gpio_num = -1,
        .trans_queue_depth = 1u,
        .max_transfer_size = sizeof(s_raw_ring),
        .dma_burst_size = 32u,
        .shift_edge = PARLIO_SHIFT_EDGE_NEG,
        .bit_pack_order = PARLIO_BIT_PACK_ORDER_LSB,
    };
    for (unsigned k = 0; k < 8u; ++k) cfg.data_gpio_nums[k] = s_dac_gpio[k];
    esp_err_t err = parlio_new_tx_unit(&cfg, &s_tx);
    if (err != ESP_OK) return err;
    const bitscrambler_config_t bs = {.dir = BITSCRAMBLER_DIR_TX, .attach_to = SOC_BITSCRAMBLER_ATTACH_PARL_IO};
    err = bitscrambler_new(&bs, &s_bs);
    if (err != ESP_OK) return err;
    return parlio_tx_unit_enable(s_tx);
}

static esp_err_t start_rx(void)
{
    esp_err_t err = parlio_rx_soft_delimiter_start_stop(s_rx, s_rx_delimiter, true);
    if (err != ESP_OK) return err;
    const parlio_receive_config_t cfg = {
        .delimiter = s_rx_delimiter,
        .flags = {.partial_rx_en = true, .indirect_mount = false},
    };
    return parlio_rx_unit_receive(s_rx, s_raw_ring, sizeof(s_raw_ring), &cfg);
}

static esp_err_t start_tx(void)
{
    /* The LUT is embedded in the program; loop_transmission never ends. */
    const parlio_transmit_config_t cfg = {
        .idle_value = DAC_IDLE_CODE,
        .bitscrambler_program = NULL,
        .flags.loop_transmission = true,
    };
    return parlio_tx_unit_transmit(s_tx, s_raw_ring, sizeof(s_raw_ring) * 8u, &cfg);
}

static void start_demodulator(void)
{
    ESP_ERROR_CHECK(bitscrambler_enable(s_bs));
    ESP_ERROR_CHECK(bitscrambler_load_program(s_bs, s_u85q));
    ESP_ERROR_CHECK(bitscrambler_reset(s_bs));
    ESP_ERROR_CHECK(bitscrambler_start(s_bs));
}

static int find_dma(bool rx)
{
    for (int k = 0; k < 3; ++k) {
        if (rx && AHB_DMA.channel[k].in.in_peri_sel.peri_in_sel_chn == 9) return k;
        if (!rx && AHB_DMA.channel[k].out.out_peri_sel.peri_out_sel_chn == 9) return k;
    }
    return -1;
}

/* Clear suc_eof on every ring descriptor: the ring becomes endless. */
static int clear_eof(int ch, bool rx)
{
    if (ch < 0 || ch >= 3) return 0;
    uint32_t first = rx ? AHB_DMA.channel[ch].in.in_dscr_bf0.val : AHB_DMA.channel[ch].out.out_dscr_bf0.val;
    if (first < 0x40800000u || first >= 0x40860000u) return 0;
    dma_descriptor_t *d = (dma_descriptor_t *)(uintptr_t)first;
    int count = 0;
    while (d && count < MAX_RING_DESCRIPTORS) {
        d->dw0.suc_eof = 0;
        sync_dma_c2m(d, sizeof(*d));
        if (rx) {
            s_rx_nodes[count].dscr = d;
            s_rx_nodes[count].buffer = (uint8_t *)d->buffer;
            s_rx_nodes[count].length = d->dw0.size ? d->dw0.size : CONTROL_BYTES;
        }
        d = d->next;
        ++count;
        if ((uintptr_t)d == first) break;
    }
    if (rx) s_rx_node_count = count;
    __asm__ __volatile__("fence rw, rw" ::: "memory");
    return count;
}

static void quiet_interrupts(void)
{
    for (int k = 0; k < 3; ++k) { AHB_DMA.in_intr[k].ena.val = 0; AHB_DMA.out_intr[k].ena.val = 0; }
    PARL_IO.int_ena.val = 0;
    /* External-enable EOF selection: soft-delimiter RX never stalls on EOF. */
    PARL_IO.rx_genrl_cfg.rx_eof_gen_sel = 1;
}

/* RX first, TX half a ring later; both run from PLL_F240M/6, so the
 * producer/consumer separation cannot drift. */
static void start_rings(void)
{
    ESP_ERROR_CHECK(start_rx());
    quiet_interrupts();
    esp_rom_delay_us((RAW_RING_BYTES / 2ULL) * 1000000ULL / IQ_RATE_HZ);
    ESP_ERROR_CHECK(start_tx());
    s_rx_dma_ch = find_dma(true);
    s_tx_dma_ch = find_dma(false);
    quiet_interrupts();
    clear_eof(s_rx_dma_ch, true);
    clear_eof(s_tx_dma_ch, false);
    PARL_IO.int_clr.val = UINT32_MAX;
    if (s_rx_dma_ch >= 0) AHB_DMA.in_intr[s_rx_dma_ch].clr.val = UINT32_MAX;
    if (s_tx_dma_ch >= 0) AHB_DMA.out_intr[s_tx_dma_ch].clr.val = UINT32_MAX;
    BITSCRAMBLER.state[BITSCRAMBLER_DIR_TX].val = 1u << 31;
}

esp_err_t transport_start(void)
{
    memset(s_raw_ring, 0, sizeof(s_raw_ring));
    sync_dma_c2m(s_raw_ring, sizeof(s_raw_ring));
    esp_err_t err = prepare_rx();
    if (err == ESP_OK) err = prepare_tx();
    if (err != ESP_OK) return err;
    start_demodulator();
    start_rings();
    return ESP_OK;
}

/* Live -> menu: stop the producer once; the menu raster takes TX. */
void transport_stop_tx(void)
{
    ESP_ERROR_CHECK(parlio_tx_unit_disable(s_tx));
    ESP_ERROR_CHECK(bitscrambler_disable(s_bs));
}

/* Start a standalone descriptor chain (menu raster) on the TX GDMA. */
void transport_start_chain(dma_descriptor_t *first)
{
    ESP_ERROR_CHECK(parlio_tx_unit_enable(s_tx));
    s_tx_dma_ch = find_dma(false);
    ESP_ERROR_CHECK(s_tx_dma_ch >= 0 ? ESP_OK : ESP_ERR_NOT_FOUND);
    AHB_DMA.out_intr[s_tx_dma_ch].ena.val = 0;
    AHB_DMA.out_intr[s_tx_dma_ch].clr.val = UINT32_MAX;
    PARL_IO.int_ena.val = 0;
    parlio_ll_tx_enable_clock(&PARL_IO, false);
    parlio_ll_tx_reset_clock(&PARL_IO);
    parlio_ll_tx_reset_fifo(&PARL_IO);
    parlio_ll_tx_set_idle_data_value(&PARL_IO, DAC_IDLE_CODE);
    parlio_ll_tx_set_eof_condition(&PARL_IO, PARLIO_LL_TX_EOF_COND_DATA_LEN);
    parlio_ll_tx_set_trans_bit_len(&PARL_IO, 1);
    __asm__ __volatile__("fence rw, rw" ::: "memory");
    AHB_DMA.out_link_addr[s_tx_dma_ch].val = (uint32_t)first;
    AHB_DMA.channel[s_tx_dma_ch].out.out_link.outlink_start_chn = 1;
    /* Wait up to 50 ms, yielding; PARLIO outputs the idle code meanwhile. */
    int64_t deadline = esp_timer_get_time() + 50000;
    while (!parlio_ll_tx_is_ready(&PARL_IO) && esp_timer_get_time() < deadline) taskYIELD();
    parlio_ll_tx_start(&PARL_IO, true);
    parlio_ll_tx_enable_clock(&PARL_IO, true);
}

void transport_stop_chain(void)
{
    ESP_ERROR_CHECK(parlio_tx_unit_disable(s_tx));
}

/* Menu -> live: the exact boot topology again. */
void transport_resume_live(void)
{
    start_demodulator();
    ESP_ERROR_CHECK(parlio_rx_unit_disable(s_rx));
    ESP_ERROR_CHECK(parlio_rx_unit_enable(s_rx, false));
    ESP_ERROR_CHECK(parlio_tx_unit_enable(s_tx));
    start_rings();
}

void transport_poll_faults(void)
{
    if (PARL_IO.int_raw.tx_fifo_rempty_int_raw) { ++s_tx_empty; PARL_IO.int_clr.tx_fifo_rempty_int_clr = 1; }
    if (PARL_IO.int_raw.rx_fifo_wovf_int_raw) { ++s_rx_overflow; PARL_IO.int_clr.rx_fifo_wovf_int_clr = 1; }
    if (s_rx_dma_ch >= 0) {
        uint32_t raw = AHB_DMA.in_intr[s_rx_dma_ch].raw.val & 0xfcu;
        if (raw) { ++s_dma_faults; AHB_DMA.in_intr[s_rx_dma_ch].clr.val = raw; }
    }
    if (s_tx_dma_ch >= 0) {
        uint32_t raw = AHB_DMA.out_intr[s_tx_dma_ch].raw.val & 0x7cu;
        if (raw) { ++s_dma_faults; AHB_DMA.out_intr[s_tx_dma_ch].clr.val = raw; }
    }
    if (BITSCRAMBLER.state[BITSCRAMBLER_DIR_TX].eof_overload) {
        ++s_bs_overload;
        BITSCRAMBLER.state[BITSCRAMBLER_DIR_TX].val = 1u << 31;
    }
}

void transport_print(void)
{
    printf("TRANSPORT rx_descriptors=%d tx_empty=%lu rx_overflow=%lu dma_faults=%lu bs_overload=%lu\n",
           s_rx_node_count, (unsigned long)s_tx_empty, (unsigned long)s_rx_overflow,
           (unsigned long)s_dma_faults, (unsigned long)s_bs_overload);
}

int ring_node_index(uint32_t addr)
{
    for (int k = 0; k < s_rx_node_count; ++k)
        if ((uintptr_t)s_rx_nodes[k].dscr == addr) return k;
    return -1;
}

static uint32_t rx_active_addr(void) { return AHB_DMA.channel[s_rx_dma_ch].in.in_dscr_bf0.val; }

/* The newest completed descriptor, copied and proven not lapped. */
bool copy_control_window(uint8_t *dst, size_t bytes)
{
    if (s_rx_dma_ch < 0 || s_rx_node_count < 3) return false;
    int64_t start = esp_timer_get_time();
    int active = ring_node_index(rx_active_addr());
    if (active < 0) return false;
    int idx = (active - 1 + s_rx_node_count) % s_rx_node_count;
    uint8_t *src = s_rx_nodes[idx].buffer;
    if (!src || s_rx_nodes[idx].length < bytes || src < s_raw_ring ||
        src + bytes > s_raw_ring + sizeof(s_raw_ring)) return false;
    sync_dma_m2c(src, bytes);
    memcpy(dst, src, bytes);
    int after = ring_node_index(rx_active_addr());
    uint64_t safe_bytes = 0;
    for (int k = 0; k < s_rx_node_count; ++k)
        if (k != active && k != idx) safe_bytes += s_rx_nodes[k].length;
    int advance = after < 0 ? s_rx_node_count : (after - active + s_rx_node_count) % s_rx_node_count;
    return after >= 0 && after != idx && advance < s_rx_node_count - 1 &&
           (uint64_t)(esp_timer_get_time() - start) < safe_bytes * 1000000ULL / IQ_RATE_HZ;
}

/* Four separated 64-byte regions of the newest completed descriptor. */
bool copy_probe_window(uint8_t dst[PROBE_BYTES], int *node)
{
    static const size_t offset[PROBE_REGIONS] = {512u, 1536u, 2560u, 4028u};
    if (s_rx_dma_ch < 0 || s_rx_node_count < 2) return false;
    int64_t start = esp_timer_get_time();
    int active = ring_node_index(rx_active_addr());
    if (active < 0) return false;
    int idx = (active - 1 + s_rx_node_count) % s_rx_node_count;
    uint8_t *src = s_rx_nodes[idx].buffer;
    if (!src || s_rx_nodes[idx].length < CONTROL_BYTES || src < s_raw_ring ||
        src + CONTROL_BYTES > s_raw_ring + sizeof(s_raw_ring)) return false;
    for (unsigned r = 0; r < PROBE_REGIONS; ++r) {
        sync_dma_m2c(src + offset[r], PROBE_REGION_BYTES);
        memcpy(dst + r * PROBE_REGION_BYTES, src + offset[r], PROBE_REGION_BYTES);
    }
    if (esp_timer_get_time() - start > 300) return false;
    if (node) *node = idx;
    return ring_node_index(rx_active_addr()) != idx;
}

/* 8190 contiguous bytes (204.75 us) ending where the active descriptor
 * began, valid only if RX could not have reached them during the copy. */
bool copy_snapshot(uint8_t *dst)
{
    if (s_rx_dma_ch < 0 || s_rx_node_count < 4) return false;
    int64_t start = esp_timer_get_time();
    int active = ring_node_index(rx_active_addr());
    if (active < 0) return false;
    uintptr_t base = (uintptr_t)s_raw_ring, first = (uintptr_t)s_rx_nodes[0].buffer;
    if (first < base || first - base >= sizeof(s_raw_ring)) return false;
    size_t total = 0;
    for (int k = 0; k < s_rx_node_count; ++k) {
        uintptr_t a = (uintptr_t)s_rx_nodes[k].buffer;
        if (a < base || !c5v4_snapshot_segment(sizeof(s_raw_ring), first - base, total, a - base,
                                               s_rx_nodes[k].length)) return false;
        total += s_rx_nodes[k].length;
    }
    if (total != sizeof(s_raw_ring)) return false;
    size_t end = (size_t)(s_rx_nodes[active].buffer - s_raw_ring);
    c5v4_snapshot_plan_t plan;
    if (!c5v4_snapshot_plan(total, end, s_rx_nodes[active].length, C5V4_LEVEL_SAMPLE_BYTES, &plan)) return false;
    sync_dma_m2c(s_raw_ring + plan.offset, plan.first);
    memcpy(dst, s_raw_ring + plan.offset, plan.first);
    size_t rest = C5V4_LEVEL_SAMPLE_BYTES - plan.first;
    if (rest) { sync_dma_m2c(s_raw_ring, rest); memcpy(dst + plan.first, s_raw_ring, rest); }
    uint64_t elapsed = (uint64_t)(esp_timer_get_time() - start);
    int later = ring_node_index(rx_active_addr());
    if (later < 0) return false;
    size_t after = (size_t)(s_rx_nodes[later].buffer - s_raw_ring);
    return c5v4_snapshot_current(total, C5V4_LEVEL_SAMPLE_BYTES, (after + total - end) % total,
                                 s_rx_nodes[later].length, elapsed, plan.safe_bytes, IQ_RATE_HZ);
}

/* Endpoint bytes (odd ring byte of each pair) of the newest `n` pairs. */
bool copy_recent_endpoints(uint8_t *dst, size_t n)
{
    if (s_rx_dma_ch < 0 || s_rx_node_count < 2 || 2u * n > sizeof(s_raw_ring) / 2u) return false;
    int idx = ring_node_index(rx_active_addr());
    if (idx < 0) return false;
    uint8_t *buf = s_rx_nodes[idx].buffer;
    if (buf < s_raw_ring || buf >= s_raw_ring + sizeof(s_raw_ring)) return false;
    size_t end = (size_t)(buf - s_raw_ring) & ~(size_t)1u;
    size_t pos = (end + sizeof(s_raw_ring) - 2u * n) % sizeof(s_raw_ring);
    sync_dma_m2c(s_raw_ring, sizeof(s_raw_ring));
    for (size_t k = 0; k < n; ++k) {
        dst[k] = s_raw_ring[pos + 1u];
        pos = (pos + 2u) % sizeof(s_raw_ring);
    }
    return true;
}

/* Sampling phase: holding the RX divider one count higher for ~1 us moves
 * the RX edge against the MODEM_DIAG update by a few 4.17 ns ticks
 * (zerowidth PR #3 does this on its sample clock). */
void rx_clock_slip(uint32_t us)
{
    static portMUX_TYPE mux = portMUX_INITIALIZER_UNLOCKED;
    portENTER_CRITICAL(&mux);
    uint32_t div = PCR.parl_clk_rx_conf.parl_clk_rx_div_num;
    HAL_FORCE_MODIFY_U32_REG_FIELD(PCR.parl_clk_rx_conf, parl_clk_rx_div_num, div + 1u);
    esp_rom_delay_us(us);
    HAL_FORCE_MODIFY_U32_REG_FIELD(PCR.parl_clk_rx_conf, parl_clk_rx_div_num, div);
    portEXIT_CRITICAL(&mux);
}

metrics_t measure_window(const uint8_t *s, size_t n)
{
    metrics_t m = {0};
    uint16_t hist[129] = {0};
    int clip = 0, origin = 0, coherent = 0, pi = 0, pq = 0;
    for (size_t k = 0; k < n; ++k) {
        int q = (int8_t)((s[k] & 0x0fu) << 4) >> 4, i = (int8_t)(s[k] & 0xf0u) >> 4;
        if (i == -8 || i == 7 || q == -8 || q == 7) ++clip;
        int p = i * i + q * q;
        if (p <= 4) ++origin;
        if (k) {
            int dot = i * pi + q * pq, cross = q * pi - i * pq;
            if (p >= 8 && dot > 0 && (cross < 0 ? -cross : cross) <= dot) ++coherent;
        }
        ++hist[p > 128 ? 128 : p];
        pi = i; pq = q;
    }
    unsigned cumulative = 0;
    for (unsigned p = 0; p <= 128u; ++p) {
        cumulative += hist[p];
        if (cumulative >= (n + 1u) / 2u) { m.p_median = (int)p; break; }
    }
    m.q_phase = n > 1u ? coherent * 100 / (int)(n - 1u) : 0;
    m.clip_pm = n ? clip * 1000 / (int)n : 0;
    m.origin_pm = n ? origin * 1000 / (int)n : 1000;
    return m;
}

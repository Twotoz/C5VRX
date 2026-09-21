#include "true80_lab.h"

#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "driver/gpio.h"
#include "driver/parlio_rx.h"
#include "driver/parlio_tx.h"
#include "esp_err.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_rom_gpio.h"
#include "esp_timer.h"
#include "modem/modem_syscon_reg.h"
#include "soc/gpio_sig_map.h"

#define TRUE80_RATE_HZ          80000000u
#define TRUE40_RATE_HZ          40000000u
#define TRUE80_CLOCK_GPIO       GPIO_NUM_2
#define LOOPBACK_RX_BYTES       32768u
#define LOOPBACK_GUARD_BYTES       64u
#define CLOCK_SCAN_BYTES        16384u
#define TRUE80_CAPTURE_BYTES    32768u
#define CLOCK_SCAN_TIMEOUT_MS       4
#define CAPTURE_TIMEOUT_MS           8

#define REG32(a) (*(volatile uint32_t *)(uintptr_t)(a))

static const char *TAG = "true80_lab";

/*
 * Keep this mapping exactly aligned with rf.c/video.c:
 *   raw bits 0..3 = Q[9:6]
 *   raw bits 4..7 = I[9:6]
 */
static const gpio_num_t s_iq_pins[8] = {
    GPIO_NUM_1, GPIO_NUM_0, GPIO_NUM_25, GPIO_NUM_7,
    GPIO_NUM_10, GPIO_NUM_5, GPIO_NUM_3, GPIO_NUM_4,
};

static const uint8_t s_iq_diag[8] = {
    6u, 7u, 8u, 9u,
    16u, 17u, 18u, 19u,
};

typedef struct {
    esp_err_t err;
    uint32_t elapsed_us;
    uint32_t measured_rate_hz;
} clock_capture_result_t;

typedef struct {
    uint32_t hash;
    uint32_t transitions;
    uint32_t origin_permille;
    uint32_t rail_permille;
    uint32_t parity_l1_permille;
    uint16_t q_values_mask;
    uint16_t i_values_mask;
} iq_metrics_t;

static inline int s4(uint8_t nibble)
{
    nibble &= 0x0fu;
    return (nibble & 0x08u) ? (int)nibble - 16 : (int)nibble;
}

static uint32_t fnv1a32(const uint8_t *data, size_t bytes)
{
    uint32_t h = 2166136261u;
    for (size_t i = 0; i < bytes; ++i) {
        h ^= data[i];
        h *= 16777619u;
    }
    return h;
}

static void analyze_iq(const uint8_t *raw, size_t bytes, iq_metrics_t *m)
{
    uint32_t even_hist[256] = {0};
    uint32_t odd_hist[256] = {0};
    uint32_t origins = 0;
    uint32_t rails = 0;
    uint32_t transitions = 0;
    uint16_t qmask = 0;
    uint16_t imask = 0;

    memset(m, 0, sizeof(*m));
    if (!raw || bytes == 0) return;

    for (size_t n = 0; n < bytes; ++n) {
        const uint8_t v = raw[n];
        const uint8_t qn = v & 0x0fu;
        const uint8_t in = (v >> 4u) & 0x0fu;
        const int q = s4(qn);
        const int i = s4(in);

        qmask |= (uint16_t)(1u << qn);
        imask |= (uint16_t)(1u << in);
        if (q >= -1 && q <= 1 && i >= -1 && i <= 1) origins++;
        if (q == -8 || q == 7 || i == -8 || i == 7) rails++;
        if (n && raw[n] != raw[n - 1u]) transitions++;

        if (n & 1u) odd_hist[v]++;
        else even_hist[v]++;
    }

    uint64_t l1 = 0;
    for (unsigned v = 0; v < 256u; ++v) {
        int64_t d = (int64_t)even_hist[v] - (int64_t)odd_hist[v];
        if (d < 0) d = -d;
        l1 += (uint64_t)d;
    }

    m->hash = fnv1a32(raw, bytes);
    m->transitions = transitions;
    m->origin_permille = (uint32_t)((uint64_t)origins * 1000u / bytes);
    m->rail_permille = (uint32_t)((uint64_t)rails * 1000u / bytes);
    m->parity_l1_permille = (uint32_t)(l1 * 1000u / bytes);
    m->q_values_mask = qmask;
    m->i_values_mask = imask;
}

static esp_err_t configure_gpio2_output(uint8_t diag_signal)
{
    const gpio_config_t cfg = {
        .pin_bit_mask = 1ULL << TRUE80_CLOCK_GPIO,
        .mode = GPIO_MODE_INPUT_OUTPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    esp_err_t err = gpio_config(&cfg);
    if (err != ESP_OK) return err;

    esp_rom_gpio_connect_out_signal(TRUE80_CLOCK_GPIO,
                                    MODEM_DIAG0_IDX + diag_signal,
                                    false, false);
    __asm__ __volatile__("fence iorw, iorw" ::: "memory");
    return ESP_OK;
}

static void restore_modem_iq_route(void)
{
    uint64_t mask = 0u;
    for (unsigned lane = 0; lane < 8u; ++lane)
        mask |= 1ULL << s_iq_pins[lane];

    const gpio_config_t cfg = {
        .pin_bit_mask = mask,
        .mode = GPIO_MODE_INPUT_OUTPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    (void)gpio_config(&cfg);

    for (unsigned lane = 0; lane < 8u; ++lane) {
        esp_rom_gpio_connect_out_signal(s_iq_pins[lane],
                                        MODEM_DIAG0_IDX + s_iq_diag[lane],
                                        false, false);
    }
    __asm__ __volatile__("fence iorw, iorw" ::: "memory");
}

static void cleanup_rx(parlio_rx_unit_handle_t rx,
                       parlio_rx_delimiter_handle_t delimiter,
                       bool enabled)
{
    if (rx && delimiter)
        (void)parlio_rx_soft_delimiter_start_stop(rx, delimiter, false);
    if (rx && enabled)
        (void)parlio_rx_unit_disable(rx);
    if (delimiter)
        (void)parlio_del_rx_delimiter(delimiter);
    if (rx)
        (void)parlio_del_rx_unit(rx);
}

static clock_capture_result_t capture_external_clock(uint32_t expected_hz,
                                                     parlio_sample_edge_t edge,
                                                     uint8_t *buffer,
                                                     size_t bytes,
                                                     int timeout_ms)
{
    clock_capture_result_t result = {
        .err = ESP_FAIL,
        .elapsed_us = 0,
        .measured_rate_hz = 0,
    };

    parlio_rx_unit_handle_t rx = NULL;
    parlio_rx_delimiter_handle_t delimiter = NULL;
    bool enabled = false;

    const parlio_rx_unit_config_t cfg = {
        .trans_queue_depth = 1u,
        .max_recv_size = bytes,
        .dma_burst_size = 32u,
        .data_width = 8u,
        .clk_src = PARLIO_CLK_SRC_EXTERNAL,
        .ext_clk_freq_hz = expected_hz,
        .exp_clk_freq_hz = expected_hz,
        .clk_in_gpio_num = TRUE80_CLOCK_GPIO,
        .clk_out_gpio_num = -1,
        .valid_gpio_num = -1,
        .data_gpio_nums = {
            GPIO_NUM_1, GPIO_NUM_0, GPIO_NUM_25, GPIO_NUM_7,
            GPIO_NUM_10, GPIO_NUM_5, GPIO_NUM_3, GPIO_NUM_4,
        },
        .flags = {
            .free_clk = true,
            .clk_gate_en = false,
            .allow_pd = false,
        },
    };

    esp_err_t err = parlio_new_rx_unit(&cfg, &rx);
    if (err != ESP_OK) {
        result.err = err;
        return result;
    }

    const parlio_rx_soft_delimiter_config_t dcfg = {
        .sample_edge = edge,
        .bit_pack_order = PARLIO_BIT_PACK_ORDER_LSB,
        .eof_data_len = bytes,
        .timeout_ticks = 0u,
    };
    err = parlio_new_rx_soft_delimiter(&dcfg, &delimiter);
    if (err != ESP_OK) {
        result.err = err;
        cleanup_rx(rx, delimiter, enabled);
        return result;
    }

    err = parlio_rx_unit_enable(rx, false);
    if (err != ESP_OK) {
        result.err = err;
        cleanup_rx(rx, delimiter, enabled);
        return result;
    }
    enabled = true;

    err = parlio_rx_soft_delimiter_start_stop(rx, delimiter, true);
    if (err != ESP_OK) {
        result.err = err;
        cleanup_rx(rx, delimiter, enabled);
        return result;
    }

    const parlio_receive_config_t rcfg = {
        .delimiter = delimiter,
    };

    memset(buffer, 0xa5, bytes);
    const int64_t t0 = esp_timer_get_time();
    err = parlio_rx_unit_receive(rx, buffer, bytes, &rcfg);
    if (err == ESP_OK)
        err = parlio_rx_unit_wait_all_done(rx, timeout_ms);
    const int64_t t1 = esp_timer_get_time();

    result.err = err;
    if (t1 > t0) {
        result.elapsed_us = (uint32_t)(t1 - t0);
        result.measured_rate_hz =
            (uint32_t)((uint64_t)bytes * 1000000ULL / (uint64_t)(t1 - t0));
    }

    cleanup_rx(rx, delimiter, enabled);
    return result;
}

static esp_err_t run_parlio_80m_loopback(void)
{
    const size_t tx_bytes = LOOPBACK_RX_BYTES + LOOPBACK_GUARD_BYTES;
    uint8_t *tx_buffer = heap_caps_malloc(tx_bytes,
        MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
    uint8_t *rx_buffer = heap_caps_malloc(LOOPBACK_RX_BYTES,
        MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
    if (!tx_buffer || !rx_buffer) {
        free(tx_buffer);
        free(rx_buffer);
        return ESP_ERR_NO_MEM;
    }

    uint32_t x = 0x5a17c3e1u;
    for (size_t i = 0; i < tx_bytes; ++i) {
        x ^= x << 13;
        x ^= x >> 17;
        x ^= x << 5;
        tx_buffer[i] = (uint8_t)x;
    }
    memset(rx_buffer, 0xa5, LOOPBACK_RX_BYTES);

    parlio_rx_unit_handle_t rx = NULL;
    parlio_rx_delimiter_handle_t delimiter = NULL;
    parlio_tx_unit_handle_t tx = NULL;
    bool rx_enabled = false;
    bool tx_enabled = false;
    esp_err_t err = ESP_OK;

    const parlio_rx_unit_config_t rxcfg = {
        .trans_queue_depth = 1u,
        .max_recv_size = LOOPBACK_RX_BYTES,
        .dma_burst_size = 32u,
        .data_width = 8u,
        .clk_src = PARLIO_CLK_SRC_EXTERNAL,
        .ext_clk_freq_hz = TRUE80_RATE_HZ,
        .exp_clk_freq_hz = TRUE80_RATE_HZ,
        .clk_in_gpio_num = TRUE80_CLOCK_GPIO,
        .clk_out_gpio_num = -1,
        .valid_gpio_num = -1,
        .data_gpio_nums = {
            GPIO_NUM_1, GPIO_NUM_0, GPIO_NUM_25, GPIO_NUM_7,
            GPIO_NUM_10, GPIO_NUM_5, GPIO_NUM_3, GPIO_NUM_4,
        },
        .flags = {
            .free_clk = true,
            .clk_gate_en = false,
            .allow_pd = false,
        },
    };
    err = parlio_new_rx_unit(&rxcfg, &rx);
    if (err != ESP_OK) goto cleanup;

    const parlio_rx_soft_delimiter_config_t dcfg = {
        .sample_edge = PARLIO_SAMPLE_EDGE_POS,
        .bit_pack_order = PARLIO_BIT_PACK_ORDER_LSB,
        .eof_data_len = LOOPBACK_RX_BYTES,
        .timeout_ticks = 0u,
    };
    err = parlio_new_rx_soft_delimiter(&dcfg, &delimiter);
    if (err != ESP_OK) goto cleanup;

    const parlio_tx_unit_config_t txcfg = {
        .clk_src = PARLIO_CLK_SRC_DEFAULT,
        .clk_in_gpio_num = -1,
        .input_clk_src_freq_hz = 0u,
        .output_clk_freq_hz = TRUE80_RATE_HZ,
        .data_width = 8u,
        .data_gpio_nums = {
            GPIO_NUM_1, GPIO_NUM_0, GPIO_NUM_25, GPIO_NUM_7,
            GPIO_NUM_10, GPIO_NUM_5, GPIO_NUM_3, GPIO_NUM_4,
        },
        .clk_out_gpio_num = TRUE80_CLOCK_GPIO,
        .valid_gpio_num = -1,
        .valid_start_delay = 0,
        .valid_stop_delay = 0,
        .trans_queue_depth = 1u,
        .max_transfer_size = tx_bytes,
        .dma_burst_size = 32u,
        .shift_edge = PARLIO_SHIFT_EDGE_NEG,
        .bit_pack_order = PARLIO_BIT_PACK_ORDER_LSB,
    };
    err = parlio_new_tx_unit(&txcfg, &tx);
    if (err != ESP_OK) goto cleanup;

    err = parlio_rx_unit_enable(rx, false);
    if (err != ESP_OK) goto cleanup;
    rx_enabled = true;

    err = parlio_tx_unit_enable(tx);
    if (err != ESP_OK) goto cleanup;
    tx_enabled = true;

    err = parlio_rx_soft_delimiter_start_stop(rx, delimiter, true);
    if (err != ESP_OK) goto cleanup;

    const parlio_receive_config_t rcfg = {.delimiter = delimiter};
    err = parlio_rx_unit_receive(rx, rx_buffer, LOOPBACK_RX_BYTES, &rcfg);
    if (err != ESP_OK) goto cleanup;

    const parlio_transmit_config_t tcfg = {
        .idle_value = 0u,
        .bitscrambler_program = NULL,
        .flags.loop_transmission = false,
    };

    const int64_t t0 = esp_timer_get_time();
    err = parlio_tx_unit_transmit(tx, tx_buffer, tx_bytes * 8u, &tcfg);
    if (err == ESP_OK)
        err = parlio_rx_unit_wait_all_done(rx, 20);
    const int64_t t1 = esp_timer_get_time();
    (void)parlio_tx_unit_wait_all_done(tx, 20);
    if (err != ESP_OK) goto cleanup;

    uint32_t best_mismatch = UINT32_MAX;
    unsigned best_offset = 0;
    for (unsigned offset = 0; offset <= LOOPBACK_GUARD_BYTES; ++offset) {
        uint32_t mismatch = 0;
        for (size_t i = 0; i < LOOPBACK_RX_BYTES; ++i)
            mismatch += rx_buffer[i] != tx_buffer[i + offset];
        if (mismatch < best_mismatch) {
            best_mismatch = mismatch;
            best_offset = offset;
        }
    }

    const uint32_t elapsed_us = t1 > t0 ? (uint32_t)(t1 - t0) : 0u;
    const uint32_t measured_hz = elapsed_us ?
        (uint32_t)((uint64_t)LOOPBACK_RX_BYTES * 1000000ULL / elapsed_us) : 0u;

    ESP_LOGW(TAG,
             "TRUE80 LOOPBACK rate=%u.%03u MS/s offset=%u mismatch=%u/%u",
             measured_hz / 1000000u, (measured_hz / 1000u) % 1000u,
             best_offset, best_mismatch, LOOPBACK_RX_BYTES);

    if (best_mismatch != 0u ||
        measured_hz < 60000000u || measured_hz > 100000000u) {
        err = ESP_ERR_INVALID_RESPONSE;
    }

cleanup:
    if (rx && delimiter)
        (void)parlio_rx_soft_delimiter_start_stop(rx, delimiter, false);
    if (tx && tx_enabled)
        (void)parlio_tx_unit_disable(tx);
    if (rx && rx_enabled)
        (void)parlio_rx_unit_disable(rx);
    if (delimiter)
        (void)parlio_del_rx_delimiter(delimiter);
    if (tx)
        (void)parlio_del_tx_unit(tx);
    if (rx)
        (void)parlio_del_rx_unit(rx);
    free(tx_buffer);
    free(rx_buffer);

    restore_modem_iq_route();
    (void)gpio_reset_pin(TRUE80_CLOCK_GPIO);
    return err;
}

static int scan_modem_clock(uint32_t saved_test_conf,
                            uint32_t clock_select_bit,
                            uint32_t expected_hz,
                            uint8_t *scratch,
                            uint32_t *best_rate_hz)
{
    const uint32_t debug_mask =
        MODEM_SYSCON_FPGA_DEBUG_CLK10 |
        MODEM_SYSCON_FPGA_DEBUG_CLK20 |
        MODEM_SYSCON_FPGA_DEBUG_CLK40 |
        MODEM_SYSCON_FPGA_DEBUG_CLK80 |
        MODEM_SYSCON_FPGA_DEBUG_CLKSWITCH;

    REG32(MODEM_SYSCON_TEST_CONF_REG) =
        (saved_test_conf & ~debug_mask) |
        MODEM_SYSCON_FPGA_DEBUG_CLKSWITCH |
        clock_select_bit;
    __asm__ __volatile__("fence iorw, iorw" ::: "memory");

    int best_signal = -1;
    uint32_t best_delta = UINT32_MAX;
    uint32_t best_rate = 0;

    for (unsigned signal = 0; signal < 32u; ++signal) {
        if (configure_gpio2_output((uint8_t)signal) != ESP_OK)
            continue;

        clock_capture_result_t r =
            capture_external_clock(expected_hz, PARLIO_SAMPLE_EDGE_POS,
                                   scratch, CLOCK_SCAN_BYTES,
                                   CLOCK_SCAN_TIMEOUT_MS);
        if (r.err != ESP_OK || r.measured_rate_hz == 0u)
            continue;

        uint32_t delta = r.measured_rate_hz > expected_hz ?
                         r.measured_rate_hz - expected_hz :
                         expected_hz - r.measured_rate_hz;

        /* Only report plausible clock-like candidates. Random diagnostic data
         * can clock PARLIO too, but will normally have a much lower and less
         * stable edge rate than CLK40/CLK80. */
        if (r.measured_rate_hz >= expected_hz / 2u) {
            ESP_LOGW(TAG, "CLK%u candidate DIAG[%u] -> %u.%03u MS/s",
                     expected_hz / 1000000u, signal,
                     r.measured_rate_hz / 1000000u,
                     (r.measured_rate_hz / 1000u) % 1000u);
        }

        if (delta < best_delta) {
            best_delta = delta;
            best_rate = r.measured_rate_hz;
            best_signal = (int)signal;
        }
    }

    if (best_rate_hz) *best_rate_hz = best_rate;

    /* Require the winning lane to be within 25% of the requested clock. */
    if (best_signal >= 0 && best_delta <= expected_hz / 4u)
        return best_signal;
    return -1;
}

static esp_err_t run_true80_capture(uint8_t clk80_signal,
                                    uint32_t saved_test_conf)
{
    uint8_t *buffer = heap_caps_malloc(TRUE80_CAPTURE_BYTES,
        MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
    if (!buffer) return ESP_ERR_NO_MEM;

    const uint32_t debug_mask =
        MODEM_SYSCON_FPGA_DEBUG_CLK10 |
        MODEM_SYSCON_FPGA_DEBUG_CLK20 |
        MODEM_SYSCON_FPGA_DEBUG_CLK40 |
        MODEM_SYSCON_FPGA_DEBUG_CLK80 |
        MODEM_SYSCON_FPGA_DEBUG_CLKSWITCH;

    REG32(MODEM_SYSCON_TEST_CONF_REG) =
        (saved_test_conf & ~debug_mask) |
        MODEM_SYSCON_FPGA_DEBUG_CLKSWITCH |
        MODEM_SYSCON_FPGA_DEBUG_CLK80;
    __asm__ __volatile__("fence iorw, iorw" ::: "memory");

    esp_err_t final_err = ESP_OK;
    for (unsigned pass = 0; pass < 2u; ++pass) {
        const parlio_sample_edge_t edge =
            pass == 0 ? PARLIO_SAMPLE_EDGE_POS : PARLIO_SAMPLE_EDGE_NEG;

        esp_err_t route_err = configure_gpio2_output(clk80_signal);
        if (route_err != ESP_OK) {
            final_err = route_err;
            break;
        }

        clock_capture_result_t r =
            capture_external_clock(TRUE80_RATE_HZ, edge, buffer,
                                   TRUE80_CAPTURE_BYTES, CAPTURE_TIMEOUT_MS);
        if (r.err != ESP_OK) {
            ESP_LOGW(TAG, "TRUE80 %s capture failed: %s",
                     pass == 0 ? "POS" : "NEG", esp_err_to_name(r.err));
            final_err = r.err;
            continue;
        }

        iq_metrics_t metrics;
        analyze_iq(buffer, TRUE80_CAPTURE_BYTES, &metrics);
        ESP_LOGW(TAG,
                 "TRUE80 %s rate=%u.%03u MS/s hash=%08lx trans=%lu "
                 "origin=%lu.%01lu%% rail=%lu.%01lu%% parityL1=%lu.%01lu%% "
                 "Qmask=%04x Imask=%04x",
                 pass == 0 ? "POS" : "NEG",
                 r.measured_rate_hz / 1000000u,
                 (r.measured_rate_hz / 1000u) % 1000u,
                 (unsigned long)metrics.hash,
                 (unsigned long)metrics.transitions,
                 (unsigned long)(metrics.origin_permille / 10u),
                 (unsigned long)(metrics.origin_permille % 10u),
                 (unsigned long)(metrics.rail_permille / 10u),
                 (unsigned long)(metrics.rail_permille % 10u),
                 (unsigned long)(metrics.parity_l1_permille / 10u),
                 (unsigned long)(metrics.parity_l1_permille % 10u),
                 metrics.q_values_mask, metrics.i_values_mask);

        if (r.measured_rate_hz < 60000000u ||
            r.measured_rate_hz > 100000000u) {
            final_err = ESP_ERR_INVALID_RESPONSE;
        }
    }

    free(buffer);
    return final_err;
}

esp_err_t true80_lab_boot_probe(void)
{
    ESP_LOGW(TAG,
             "=======================================================\n"
             " TRUE80 RX ORACLE -- bounded boot experiment\n"
             " Production video will start after this probe.\n"
             "=======================================================");

    esp_err_t loopback_err = run_parlio_80m_loopback();

    uint8_t *scratch = heap_caps_malloc(CLOCK_SCAN_BYTES,
        MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
    if (!scratch) {
        ESP_LOGW(TAG, "TRUE80 clock scan skipped: no DMA memory");
        return ESP_ERR_NO_MEM;
    }

    restore_modem_iq_route();
    const uint32_t saved_test_conf = REG32(MODEM_SYSCON_TEST_CONF_REG);

    uint32_t clk80_rate = 0;
    int clk80_signal = scan_modem_clock(saved_test_conf,
                                        MODEM_SYSCON_FPGA_DEBUG_CLK80,
                                        TRUE80_RATE_HZ, scratch,
                                        &clk80_rate);

    uint32_t clk40_rate = 0;
    int clk40_signal = scan_modem_clock(saved_test_conf,
                                        MODEM_SYSCON_FPGA_DEBUG_CLK40,
                                        TRUE40_RATE_HZ, scratch,
                                        &clk40_rate);

    free(scratch);

    ESP_LOGW(TAG,
             "TRUE80 clock search: CLK80=DIAG[%d] %u.%03u MS/s | "
             "CLK40=DIAG[%d] %u.%03u MS/s",
             clk80_signal,
             clk80_rate / 1000000u, (clk80_rate / 1000u) % 1000u,
             clk40_signal,
             clk40_rate / 1000000u, (clk40_rate / 1000u) % 1000u);

    esp_err_t capture_err = ESP_ERR_NOT_FOUND;
    if (clk80_signal >= 0)
        capture_err = run_true80_capture((uint8_t)clk80_signal,
                                         saved_test_conf);

    REG32(MODEM_SYSCON_TEST_CONF_REG) = saved_test_conf;
    __asm__ __volatile__("fence iorw, iorw" ::: "memory");
    (void)gpio_reset_pin(TRUE80_CLOCK_GPIO);
    restore_modem_iq_route();

    if (loopback_err == ESP_OK && clk80_signal >= 0 &&
        capture_err == ESP_OK) {
        ESP_LOGW(TAG,
                 "TRUE80 RESULT: PASS -- PARLIO RX80 + MODEM source clock + "
                 "bounded 80 MS/s Q4/I4 capture are available.");
        ESP_LOGW(TAG,
                 "Next gate: realtime 80->40 discriminate/combine throughput; "
                 "normal live video remains on the proven 40 MS/s path.");
        return ESP_OK;
    }

    ESP_LOGW(TAG,
             "TRUE80 RESULT: INCOMPLETE loopback=%s clk80=%d capture=%s; "
             "falling back to proven 40 MS/s live video.",
             esp_err_to_name(loopback_err), clk80_signal,
             esp_err_to_name(capture_err));
    return ESP_ERR_NOT_SUPPORTED;
}

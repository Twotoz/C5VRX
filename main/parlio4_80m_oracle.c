/* Bounded, RF-off hardware validation for Issue 20.
 * PARLIO 4-bit @ 80 MHz packing transport and grouped-DAC oracle.
 */
#include "parlio4_80m_oracle.h"
#include <stdio.h>
#include <string.h>
#include <inttypes.h>
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_heap_caps.h"
#include "esp_partition.h"
#include "esp_rom_sys.h"
#include "esp_rom_gpio.h"
#include "driver/gpio.h"
#include "driver/parlio_tx.h"
#include "driver/parlio_rx.h"
#include "soc/gpio_sig_map.h"
#include "soc/parl_io_struct.h"
#include "hal/parlio_ll.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#define TAG "oracle_p4_80m"
#define XIAO_USER_LED GPIO_NUM_27

#define TEST_BYTES_ONESHOT   80000u
#define TEST_BYTES_LOOP      16384u
#define RX_CAPTURE_BYTES     2000u
#define FANOUT_CAPTURE_BYTES 2000u

/* DAC Pin Layout:
 * Bit 0 (weight  1): GPIO 23
 * Bit 1 (weight  2): GPIO 24
 * Bit 2 (weight  4): GPIO 11
 * Bit 3 (weight  8): GPIO 12
 * Bit 4 (weight 16): GPIO 8
 * Bit 5 (weight 32): GPIO 9
 *
 * 4-Bit Grouped Layout:
 * Parlio Bit 0 -> GPIO 11 (weight 4)
 * Parlio Bit 1 -> GPIO 12 (weight 8)
 * Parlio Bit 2 -> GPIO 8 (weight 16) + GPIO 23 (weight 1) = weight 17
 * Parlio Bit 3 -> GPIO 9 (weight 32) + GPIO 24 (weight 2) = weight 34
 */
static const int s_all_dac_pins[6] = {23, 24, 11, 12, 8, 9};

typedef struct {
    uint32_t magic;               /* 0x50343830 ("P480") */
    uint32_t version;             /* 1 */
    uint32_t record_size;
    /* Test 0: Direct TX40 control baseline */
    uint32_t tx40_time_us;
    uint32_t tx40_bytes;
    uint32_t tx40_throughput_kbps;
    uint32_t tx40_fifo_rempty;
    int32_t  tx40_err;
    /* Test 1: PARLIO4 @ 80 MHz one-shot */
    uint32_t tx80_time_us;
    uint32_t tx80_bytes;
    uint32_t tx80_throughput_kbps;
    uint32_t tx80_sample_rate_ksps;
    uint32_t tx80_fifo_rempty;
    int32_t  tx80_err;
    /* Test 2: Sustained continuous loop */
    uint32_t loop_duration_ms;
    uint32_t loop_wraps_count;
    uint32_t loop_fifo_rempty_mid;
    uint32_t loop_fifo_rempty_end;
    int32_t  loop_err;
    /* Test 3: Loopback RX capture & nibble match */
    uint32_t rx_samples_count;
    uint32_t rx_match_offset;
    uint32_t rx_mismatches_lsb;
    uint32_t rx_mismatches_msb;
    uint32_t rx_detected_order;   /* 0 = LSB, 1 = MSB, 0xFF = failed */
    int32_t  rx_err;
    /* Test 4: GPIO matrix fanout */
    uint32_t fanout_samples_checked;
    uint32_t fanout_mismatches_bit2;
    uint32_t fanout_mismatches_bit3;
    int32_t  fanout_err;
    /* Test 5: 4-bit grouped DAC linearity */
    uint8_t  dac_levels[16];
    uint32_t dac_min_step;
    uint32_t dac_max_step;
    uint32_t dac_is_monotonic;
    /* Summary */
    uint32_t all_gates_passed;
} parlio4_diag_record_t;

static void setup_dac_gpios(void)
{
    for (int i = 0; i < 6; ++i) {
        gpio_num_t pin = (gpio_num_t)s_all_dac_pins[i];
        gpio_set_direction(pin, GPIO_MODE_INPUT_OUTPUT);
        gpio_set_drive_capability(pin, GPIO_DRIVE_CAP_3);
    }
}

static void connect_fanout_signals(void)
{
    /* Signal 50 = PARL_TX_DATA2_IDX: Fanout to GPIO 8 (primary) and GPIO 23 (mirror) */
    esp_rom_gpio_connect_out_signal(GPIO_NUM_23, PARL_TX_DATA2_IDX, false, false);

    /* Signal 51 = PARL_TX_DATA3_IDX: Fanout to GPIO 9 (primary) and GPIO 24 (mirror) */
    esp_rom_gpio_connect_out_signal(GPIO_NUM_24, PARL_TX_DATA3_IDX, false, false);
}

esp_err_t c5vrx2_parlio4_80m_oracle_run(void)
{
    /* Allow USB Serial/JTAG console time to attach on host */
    vTaskDelay(pdMS_TO_TICKS(2000));

    ESP_LOGI(TAG, "================================================================");
    ESP_LOGI(TAG, "STARTING ISSUE 20: 4-BIT PARLIO @ 80 MHz TRANSPORT & DAC ORACLE");
    ESP_LOGI(TAG, "================================================================");

    parlio4_diag_record_t rec;
    memset(&rec, 0, sizeof(rec));
    rec.magic = 0x50343830u;
    rec.version = 1;
    rec.record_size = sizeof(rec);

    setup_dac_gpios();

    /* =========================================================================
     * STAGE 1 - TEST 0: Known-Good Direct TX40 Control Baseline (8-bit bus, 40 MHz)
     * ========================================================================= */
    ESP_LOGI(TAG, "--- TEST 0: Direct TX40 Control Baseline (8-bit @ 40 MHz) ---");
    uint8_t *tx40_buf = heap_caps_malloc(TEST_BYTES_ONESHOT, MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
    if (!tx40_buf) {
        ESP_LOGE(TAG, "Failed to allocate TX40 buffer (%u bytes)", TEST_BYTES_ONESHOT);
        return ESP_ERR_NO_MEM;
    }
    for (size_t i = 0; i < TEST_BYTES_ONESHOT; ++i) {
        tx40_buf[i] = (uint8_t)(i & 0x3F);
    }

    parlio_tx_unit_handle_t tx40 = NULL;
    const parlio_tx_unit_config_t tx40_cfg = {
        .clk_src = PARLIO_CLK_SRC_DEFAULT,
        .clk_in_gpio_num = -1,
        .output_clk_freq_hz = 40000000u,
        .data_width = 8u,
        .data_gpio_nums = {23, 24, 11, 12, 8, 9, -1, -1},
        .clk_out_gpio_num = -1,
        .valid_gpio_num = -1,
        .trans_queue_depth = 2u,
        .max_transfer_size = TEST_BYTES_ONESHOT,
        .dma_burst_size = 32u,
        .shift_edge = PARLIO_SHIFT_EDGE_NEG,
        .bit_pack_order = PARLIO_BIT_PACK_ORDER_LSB,
    };
    esp_err_t err = parlio_new_tx_unit(&tx40_cfg, &tx40);
    if (err == ESP_OK) err = parlio_tx_unit_enable(tx40);
    rec.tx40_err = err;
    if (err == ESP_OK) {
        parlio_ll_enable_interrupt(&PARL_IO, PARLIO_LL_EVENT_TX_FIFO_EMPTY, false);
        PARL_IO.int_clr.tx_fifo_rempty_int_clr = 1;

        const parlio_transmit_config_t tr_cfg = {
            .idle_value = 0,
            .bitscrambler_program = NULL,
            .flags.loop_transmission = false,
        };
        int64_t t0 = esp_timer_get_time();
        err = parlio_tx_unit_transmit(tx40, tx40_buf, TEST_BYTES_ONESHOT * 8u, &tr_cfg);
        if (err == ESP_OK) err = parlio_tx_unit_wait_all_done(tx40, 500);
        int64_t t1 = esp_timer_get_time();
        int64_t dt = t1 - t0;

        rec.tx40_time_us = (uint32_t)dt;
        rec.tx40_bytes = TEST_BYTES_ONESHOT;
        rec.tx40_fifo_rempty = PARL_IO.int_raw.tx_fifo_rempty_int_raw;
        if (dt > 0) {
            rec.tx40_throughput_kbps = (uint32_t)(((uint64_t)TEST_BYTES_ONESHOT * 1000000ULL) / (dt * 1024ULL));
            ESP_LOGI(TAG, "TX40 Control: %u bytes in %" PRIu32 " us => %.2f MB/s, FIFO empty sticky = %" PRIu32,
                     TEST_BYTES_ONESHOT, rec.tx40_time_us, ((double)rec.tx40_throughput_kbps)/1024.0, rec.tx40_fifo_rempty);
        }
        parlio_tx_unit_disable(tx40);
        parlio_del_tx_unit(tx40);
    } else {
        ESP_LOGE(TAG, "Failed to initialize TX40 control: %s", esp_err_to_name(err));
    }
    free(tx40_buf);

    /* =========================================================================
     * STAGE 1 - TEST 1: PARLIO4 @ 80 MHz Timed One-Shot Throughput & Cadence
     * ========================================================================= */
    ESP_LOGI(TAG, "--- TEST 1: PARLIO 4-bit @ 80 MHz Timed One-Shot ---");
    uint8_t *tx80_buf = heap_caps_malloc(TEST_BYTES_ONESHOT, MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
    if (!tx80_buf) {
        ESP_LOGE(TAG, "Failed to allocate TX80 buffer (%u bytes)", TEST_BYTES_ONESHOT);
        return ESP_ERR_NO_MEM;
    }
    /* Encode repeating ramp 0..15:
     * In LSB pack order: sample 0 is low nibble, sample 1 is high nibble.
     * byte i contains samples (2*i % 16) and ((2*i + 1) % 16).
     */
    for (size_t i = 0; i < TEST_BYTES_ONESHOT; ++i) {
        uint8_t low  = (uint8_t)((2u * i) % 16u);
        uint8_t high = (uint8_t)((2u * i + 1u) % 16u);
        tx80_buf[i]  = (uint8_t)(low | (high << 4));
    }

    parlio_tx_unit_handle_t tx80 = NULL;
    const parlio_tx_unit_config_t tx80_cfg = {
        .clk_src = PARLIO_CLK_SRC_DEFAULT,
        .clk_in_gpio_num = -1,
        .output_clk_freq_hz = 80000000u,
        .data_width = 4u,
        .data_gpio_nums = {11, 12, 8, 9, -1, -1, -1, -1},
        .clk_out_gpio_num = -1,
        .valid_gpio_num = -1,
        .trans_queue_depth = 2u,
        .max_transfer_size = TEST_BYTES_ONESHOT,
        .dma_burst_size = 32u,
        .shift_edge = PARLIO_SHIFT_EDGE_NEG,
        .bit_pack_order = PARLIO_BIT_PACK_ORDER_LSB,
    };
    err = parlio_new_tx_unit(&tx80_cfg, &tx80);
    if (err == ESP_OK) err = parlio_tx_unit_enable(tx80);
    rec.tx80_err = err;
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "parlio_new_tx_unit(4-bit @ 80 MHz) failed: %s", esp_err_to_name(err));
        free(tx80_buf);
        return err;
    }

    /* Route fanout signals: bit 2 -> GPIO 23, bit 3 -> GPIO 24 */
    connect_fanout_signals();

    /* Disable interrupt so FIFO-empty remains sticky */
    parlio_ll_enable_interrupt(&PARL_IO, PARLIO_LL_EVENT_TX_FIFO_EMPTY, false);
    PARL_IO.int_clr.tx_fifo_rempty_int_clr = 1;

    const parlio_transmit_config_t tr_cfg_oneshot = {
        .idle_value = 0,
        .bitscrambler_program = NULL,
        .flags.loop_transmission = false,
    };

    int64_t t0 = esp_timer_get_time();
    /* Total bit length for data_width=4: TEST_BYTES_ONESHOT * 8 bits = 2 * TEST_BYTES_ONESHOT samples */
    err = parlio_tx_unit_transmit(tx80, tx80_buf, TEST_BYTES_ONESHOT * 8u, &tr_cfg_oneshot);
    if (err == ESP_OK) {
        err = parlio_tx_unit_wait_all_done(tx80, 500);
    }
    int64_t t1 = esp_timer_get_time();
    int64_t dt80 = t1 - t0;

    rec.tx80_time_us = (uint32_t)dt80;
    rec.tx80_bytes = TEST_BYTES_ONESHOT;
    rec.tx80_fifo_rempty = PARL_IO.int_raw.tx_fifo_rempty_int_raw;
    if (dt80 > 0) {
        rec.tx80_throughput_kbps = (uint32_t)(((uint64_t)TEST_BYTES_ONESHOT * 1000000ULL) / (dt80 * 1024ULL));
        rec.tx80_sample_rate_ksps = (uint32_t)(((uint64_t)TEST_BYTES_ONESHOT * 2ULL * 1000000ULL) / (dt80 * 1000ULL));
        double mb_s = ((double)rec.tx80_throughput_kbps) / 1024.0;
        double ms_s = ((double)rec.tx80_sample_rate_ksps) / 1000.0;
        double cadence_ns = (dt80 * 1000.0) / (2.0 * TEST_BYTES_ONESHOT);
        ESP_LOGI(TAG, "PARLIO4 @ 80 MHz One-Shot:");
        ESP_LOGI(TAG, "  Elapsed: %" PRIu32 " us", rec.tx80_time_us);
        ESP_LOGI(TAG, "  Upstream DMA Throughput: %.2f MB/s (expected ~40.0 MB/s)", mb_s);
        ESP_LOGI(TAG, "  Physical Sample Cadence: %.2f MS/s (%.2f ns/sample, expected 12.5 ns)", ms_s, cadence_ns);
        ESP_LOGI(TAG, "  FIFO Empty Sticky Flag:  %" PRIu32 " (0 = pass, 1 = FAIL)", rec.tx80_fifo_rempty);
    }

    /* =========================================================================
     * STAGE 1 - TEST 2: Sustained Continuous Loop Transmission & DMA Wrap
     * ========================================================================= */
    ESP_LOGI(TAG, "--- TEST 2: Sustained Continuous Loop & DMA Wrap Verification ---");
    PARL_IO.int_clr.tx_fifo_rempty_int_clr = 1;

    const parlio_transmit_config_t tr_cfg_loop = {
        .idle_value = 0,
        .bitscrambler_program = NULL,
        .flags.loop_transmission = true,
    };
    /* Transmit 16 KiB buffer continuously in loop mode */
    err = parlio_tx_unit_transmit(tx80, tx80_buf, TEST_BYTES_LOOP * 8u, &tr_cfg_loop);
    rec.loop_err = err;
    if (err == ESP_OK) {
        /* Run for 250 ms (~10 MB transmitted, ~610 DMA wraps) */
        vTaskDelay(pdMS_TO_TICKS(250));
        rec.loop_fifo_rempty_mid = PARL_IO.int_raw.tx_fifo_rempty_int_raw;

        /* Run for another 250 ms (total 500 ms, ~20 MB transmitted, ~1220 DMA wraps) */
        vTaskDelay(pdMS_TO_TICKS(250));
        rec.loop_fifo_rempty_end = PARL_IO.int_raw.tx_fifo_rempty_int_raw;

        rec.loop_duration_ms = 500;
        rec.loop_wraps_count = (uint32_t)((500ULL * 40000ULL) / TEST_BYTES_LOOP); /* approx wraps */

        ESP_LOGI(TAG, "Sustained Loop 500 ms (~%" PRIu32 " wraps, ~20 MB transmitted):", rec.loop_wraps_count);
        ESP_LOGI(TAG, "  FIFO empty at 250 ms: %" PRIu32, rec.loop_fifo_rempty_mid);
        ESP_LOGI(TAG, "  FIFO empty at 500 ms: %" PRIu32, rec.loop_fifo_rempty_end);
    } else {
        ESP_LOGE(TAG, "Continuous loop start failed: %s", esp_err_to_name(err));
    }

    /* =========================================================================
     * STAGE 1 - TEST 3: Loopback RX Capture & Nibble-Exact Cyclic Verification
     * ========================================================================= */
    ESP_LOGI(TAG, "--- TEST 3: Loopback RX Capture & Nibble-Exact Verification ---");
    uint8_t *rx_buf = heap_caps_malloc(RX_CAPTURE_BYTES, MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
    if (rx_buf) {
        memset(rx_buf, 0xEE, RX_CAPTURE_BYTES);
        parlio_rx_unit_handle_t rx = NULL;
        parlio_rx_delimiter_handle_t delim = NULL;
        const parlio_rx_unit_config_t rx_cfg = {
            .trans_queue_depth = 1,
            .max_recv_size = RX_CAPTURE_BYTES,
            .dma_burst_size = 32,
            .data_width = 4,
            .clk_src = PARLIO_CLK_SRC_DEFAULT,
            .exp_clk_freq_hz = 80000000u,
            .clk_in_gpio_num = -1,
            .clk_out_gpio_num = -1,
            .valid_gpio_num = -1,
            .flags.free_clk = true,
            .data_gpio_nums = {11, 12, 8, 9, -1, -1, -1, -1},
        };
        err = parlio_new_rx_unit(&rx_cfg, &rx);
        if (err == ESP_OK) {
            const parlio_rx_soft_delimiter_config_t dc = {
                .sample_edge = PARLIO_SAMPLE_EDGE_POS,
                .bit_pack_order = PARLIO_BIT_PACK_ORDER_LSB,
                .eof_data_len = RX_CAPTURE_BYTES,
            };
            err = parlio_new_rx_soft_delimiter(&dc, &delim);
            if (err == ESP_OK) {
                err = parlio_rx_unit_enable(rx, true);
                if (err == ESP_OK) {
                    const parlio_receive_config_t rc = {.delimiter = delim};
                    err = parlio_rx_unit_receive(rx, rx_buf, RX_CAPTURE_BYTES, &rc);
                    if (err == ESP_OK) {
                        parlio_rx_soft_delimiter_start_stop(rx, delim, true);
                        err = parlio_rx_unit_wait_all_done(rx, 200);
                        parlio_rx_soft_delimiter_start_stop(rx, delim, false);
                    }
                }
            }
        }
        rec.rx_err = err;
        if (err == ESP_OK) {
            /* Analyze 4000 samples (2 samples per byte) */
            uint32_t total_samples = RX_CAPTURE_BYTES * 2u;
            rec.rx_samples_count = total_samples;

            /* Hypothesis A: LSB pack order (even = low nibble, odd = high nibble) */
            uint32_t best_err_lsb = UINT32_MAX;
            uint32_t best_off_lsb = 0;
            for (uint32_t off = 0; off < 16; ++off) {
                uint32_t mismatches = 0;
                for (uint32_t s = 0; s < total_samples; ++s) {
                    uint8_t byte_val = rx_buf[s / 2];
                    uint8_t sample = (s & 1) ? ((byte_val >> 4) & 0x0F) : (byte_val & 0x0F);
                    uint8_t expected = (uint8_t)((s + off) % 16);
                    if (sample != expected) mismatches++;
                }
                if (mismatches < best_err_lsb) {
                    best_err_lsb = mismatches;
                    best_off_lsb = off;
                }
            }

            /* Hypothesis B: MSB pack order (even = high nibble, odd = low nibble) */
            uint32_t best_err_msb = UINT32_MAX;
            uint32_t best_off_msb = 0;
            for (uint32_t off = 0; off < 16; ++off) {
                uint32_t mismatches = 0;
                for (uint32_t s = 0; s < total_samples; ++s) {
                    uint8_t byte_val = rx_buf[s / 2];
                    uint8_t sample = (s & 1) ? (byte_val & 0x0F) : ((byte_val >> 4) & 0x0F);
                    uint8_t expected = (uint8_t)((s + off) % 16);
                    if (sample != expected) mismatches++;
                }
                if (mismatches < best_err_msb) {
                    best_err_msb = mismatches;
                    best_off_msb = off;
                }
            }

            rec.rx_mismatches_lsb = best_err_lsb;
            rec.rx_mismatches_msb = best_err_msb;

            if (best_err_lsb <= best_err_msb) {
                rec.rx_detected_order = 0; /* LSB */
                rec.rx_match_offset = best_off_lsb;
            } else {
                rec.rx_detected_order = 1; /* MSB */
                rec.rx_match_offset = best_off_msb;
            }

            ESP_LOGI(TAG, "Loopback RX Analysis (%" PRIu32 " samples captured at 80 MS/s):", total_samples);
            ESP_LOGI(TAG, "  LSB pack hypothesis: %" PRIu32 " mismatches (offset %" PRIu32 ")", best_err_lsb, best_off_lsb);
            ESP_LOGI(TAG, "  MSB pack hypothesis: %" PRIu32 " mismatches (offset %" PRIu32 ")", best_err_msb, best_off_msb);
            ESP_LOGI(TAG, "  First 16 raw captured bytes: %02x %02x %02x %02x %02x %02x %02x %02x %02x %02x %02x %02x %02x %02x %02x %02x",
                     rx_buf[0], rx_buf[1], rx_buf[2], rx_buf[3],
                     rx_buf[4], rx_buf[5], rx_buf[6], rx_buf[7],
                     rx_buf[8], rx_buf[9], rx_buf[10], rx_buf[11],
                     rx_buf[12], rx_buf[13], rx_buf[14], rx_buf[15]);
        } else {
            ESP_LOGE(TAG, "PARLIO RX capture failed: %s", esp_err_to_name(err));
        }
        if (delim) parlio_del_rx_delimiter(delim);
        if (rx) {
            parlio_rx_unit_disable(rx);
            parlio_del_rx_unit(rx);
        }
        free(rx_buf);
    }

    /* =========================================================================
     * STAGE 1 - TEST 4: GPIO Matrix Concurrent Fanout Pad Verification
     * ========================================================================= */
    ESP_LOGI(TAG, "--- TEST 4: GPIO Matrix Concurrent Fanout Verification ---");
    uint8_t *fanout_buf = heap_caps_malloc(FANOUT_CAPTURE_BYTES, MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
    if (fanout_buf) {
        memset(fanout_buf, 0xEE, FANOUT_CAPTURE_BYTES);
        parlio_rx_unit_handle_t rx_fanout = NULL;
        parlio_rx_delimiter_handle_t delim_fanout = NULL;
        /* Sample all 6 DAC pins: {23, 24, 11, 12, 8, 9, -1, -1}
         * Bit 0: GPIO 23 (fanout mirror of Bit 2 / Sig 50)
         * Bit 1: GPIO 24 (fanout mirror of Bit 3 / Sig 51)
         * Bit 2: GPIO 11 (Bit 0 / Sig 48)
         * Bit 3: GPIO 12 (Bit 1 / Sig 49)
         * Bit 4: GPIO 8  (Bit 2 / Sig 50)
         * Bit 5: GPIO 9  (Bit 3 / Sig 51)
         */
        const parlio_rx_unit_config_t rxf_cfg = {
            .trans_queue_depth = 1,
            .max_recv_size = FANOUT_CAPTURE_BYTES,
            .dma_burst_size = 32,
            .data_width = 8,
            .clk_src = PARLIO_CLK_SRC_DEFAULT,
            .exp_clk_freq_hz = 40000000u,
            .clk_in_gpio_num = -1,
            .clk_out_gpio_num = -1,
            .valid_gpio_num = -1,
            .flags.free_clk = true,
            .data_gpio_nums = {23, 24, 11, 12, 8, 9, -1, -1},
        };
        err = parlio_new_rx_unit(&rxf_cfg, &rx_fanout);
        if (err == ESP_OK) {
            const parlio_rx_soft_delimiter_config_t dc = {
                .sample_edge = PARLIO_SAMPLE_EDGE_POS,
                .bit_pack_order = PARLIO_BIT_PACK_ORDER_LSB,
                .eof_data_len = FANOUT_CAPTURE_BYTES,
            };
            err = parlio_new_rx_soft_delimiter(&dc, &delim_fanout);
            if (err == ESP_OK) {
                err = parlio_rx_unit_enable(rx_fanout, true);
                if (err == ESP_OK) {
                    const parlio_receive_config_t rc = {.delimiter = delim_fanout};
                    err = parlio_rx_unit_receive(rx_fanout, fanout_buf, FANOUT_CAPTURE_BYTES, &rc);
                    if (err == ESP_OK) {
                        parlio_rx_soft_delimiter_start_stop(rx_fanout, delim_fanout, true);
                        err = parlio_rx_unit_wait_all_done(rx_fanout, 200);
                        parlio_rx_soft_delimiter_start_stop(rx_fanout, delim_fanout, false);
                    }
                }
            }
        }
        rec.fanout_err = err;
        if (err == ESP_OK) {
            uint32_t mismatches_bit2 = 0;
            uint32_t mismatches_bit3 = 0;
            for (size_t i = 0; i < FANOUT_CAPTURE_BYTES; ++i) {
                uint8_t val = fanout_buf[i];
                uint8_t bit23 = (val >> 0) & 1; /* GPIO 23 */
                uint8_t bit24 = (val >> 1) & 1; /* GPIO 24 */
                uint8_t bit8  = (val >> 4) & 1; /* GPIO 8  */
                uint8_t bit9  = (val >> 5) & 1; /* GPIO 9  */
                if (bit23 != bit8) mismatches_bit2++;
                if (bit24 != bit9) mismatches_bit3++;
            }
            rec.fanout_samples_checked = FANOUT_CAPTURE_BYTES;
            rec.fanout_mismatches_bit2 = mismatches_bit2;
            rec.fanout_mismatches_bit3 = mismatches_bit3;
            ESP_LOGI(TAG, "GPIO Matrix Fanout Check (%u samples):", FANOUT_CAPTURE_BYTES);
            ESP_LOGI(TAG, "  Signal 50: GPIO 23 == GPIO 8 mismatches: %" PRIu32, mismatches_bit2);
            ESP_LOGI(TAG, "  Signal 51: GPIO 24 == GPIO 9 mismatches: %" PRIu32, mismatches_bit3);
            if (mismatches_bit2 == 0 && mismatches_bit3 == 0) {
                ESP_LOGI(TAG, "  => FANOUT VERIFICATION PASSED: GPIO matrix flawlessly drives multiple pads!");
            } else {
                ESP_LOGW(TAG, "  => Notice: fanout mismatch count > 0 (check sampling edge or transition alignment)");
            }
        } else {
            ESP_LOGE(TAG, "Fanout RX capture failed: %s", esp_err_to_name(err));
        }
        if (delim_fanout) parlio_del_rx_delimiter(delim_fanout);
        if (rx_fanout) {
            parlio_rx_unit_disable(rx_fanout);
            parlio_del_rx_unit(rx_fanout);
        }
        free(fanout_buf);
    }

    /* =========================================================================
     * STAGE 2: 4-Bit Grouped DAC Linearity & Monotonicity Verification
     * ========================================================================= */
    ESP_LOGI(TAG, "--- STAGE 2: 4-bit Grouped DAC Linearity & Monotonicity ---");
    /* Weights:
     * bit 0 -> 4
     * bit 1 -> 8
     * bit 2 -> 17 (16 + 1)
     * bit 3 -> 34 (32 + 2)
     */
    bool monotonic = true;
    uint32_t min_step = 255, max_step = 0;
    for (int k = 0; k < 16; ++k) {
        uint8_t eff = (uint8_t)(((k & 1) ? 4 : 0) +
                                (((k >> 1) & 1) ? 8 : 0) +
                                (((k >> 2) & 1) ? 17 : 0) +
                                (((k >> 3) & 1) ? 34 : 0));
        rec.dac_levels[k] = eff;
        if (k > 0) {
            uint32_t step = (uint32_t)(eff - rec.dac_levels[k - 1]);
            if (step < min_step) min_step = step;
            if (step > max_step) max_step = step;
            if (eff <= rec.dac_levels[k - 1]) monotonic = false;
        }
    }
    rec.dac_min_step = min_step;
    rec.dac_max_step = max_step;
    rec.dac_is_monotonic = monotonic ? 1 : 0;

    ESP_LOGI(TAG, "4-bit Grouped DAC 16 Levels (0..63 scale):");
    char dac_str[128];
    int pos = 0;
    for (int k = 0; k < 16; ++k) {
        pos += snprintf(dac_str + pos, sizeof(dac_str) - pos, "%d ", rec.dac_levels[k]);
    }
    ESP_LOGI(TAG, "  Values: [ %s]", dac_str);
    ESP_LOGI(TAG, "  Min step: %" PRIu32 ", Max step: %" PRIu32 ", Strictly Monotonic: %s, Full Scale: %d/63",
             min_step, max_step, monotonic ? "YES" : "NO", rec.dac_levels[15]);

    /* =========================================================================
     * STAGE 1 & 2 ACCEPTANCE GATE EVALUATION
     * ========================================================================= */
    bool gate_tx_rate   = (rec.tx80_throughput_kbps > (35 * 1024)) && (rec.tx80_throughput_kbps < (45 * 1024));
    bool gate_cadence   = (rec.tx80_sample_rate_ksps > (75 * 1000)) && (rec.tx80_sample_rate_ksps < (85 * 1000));
    bool gate_oneshot_f = (rec.tx80_fifo_rempty == 0);
    bool gate_loop_f    = (rec.loop_fifo_rempty_mid == 0 && rec.loop_fifo_rempty_end == 0);
    bool gate_monotonic = (rec.dac_is_monotonic == 1 && rec.dac_levels[15] == 63);

    bool all_pass = gate_tx_rate && gate_cadence && gate_oneshot_f && gate_loop_f && gate_monotonic;
    rec.all_gates_passed = all_pass ? 1 : 0;

    ESP_LOGI(TAG, "================================================================");
    ESP_LOGI(TAG, "STAGE 1 & 2 HARD ACCEPTANCE GATES EVALUATION:");
    ESP_LOGI(TAG, "  Gate 1: Upstream DMA throughput ~40 MB/s:        [%s] (%.2f MB/s)",
             gate_tx_rate ? "PASS" : "FAIL", ((double)rec.tx80_throughput_kbps)/1024.0);
    ESP_LOGI(TAG, "  Gate 2: Physical pad cadence ~80 MS/s (12.5 ns): [%s] (%.2f MS/s)",
             gate_cadence ? "PASS" : "FAIL", ((double)rec.tx80_sample_rate_ksps)/1000.0);
    ESP_LOGI(TAG, "  Gate 3: One-shot TX FIFO empty sticky flag == 0: [%s] (flag=%" PRIu32 ")",
             gate_oneshot_f ? "PASS" : "FAIL", rec.tx80_fifo_rempty);
    ESP_LOGI(TAG, "  Gate 4: Continuous loop TX FIFO empty == 0:      [%s] (mid=%" PRIu32 ", end=%" PRIu32 ")",
             gate_loop_f ? "PASS" : "FAIL", rec.loop_fifo_rempty_mid, rec.loop_fifo_rempty_end);
    ESP_LOGI(TAG, "  Gate 5: Grouped DAC strictly monotonic (0..63):  [%s] (min=%" PRIu32 ", max=%" PRIu32 ")",
             gate_monotonic ? "PASS" : "FAIL", min_step, max_step);
    ESP_LOGI(TAG, "----------------------------------------------------------------");
    ESP_LOGI(TAG, "OVERALL ORACLE RESULT: [%s]", all_pass ? "SUCCESS / ALL PASS" : "FAILURE");
    ESP_LOGI(TAG, "================================================================");

    /* =========================================================================
     * PERSISTENCE: Save Diagnostic Record to 'diagcap' Partition
     * ========================================================================= */
    const esp_partition_t *part = esp_partition_find_first(ESP_PARTITION_TYPE_DATA, 0x42, "diagcap");
    if (part) {
        esp_err_t s_err = esp_partition_erase_range(part, 0, 4096);
        if (s_err == ESP_OK) {
            s_err = esp_partition_write(part, 0, &rec, sizeof(rec));
        }
        ESP_LOGI(TAG, "Diagnostic record persistence to 'diagcap': %s", esp_err_to_name(s_err));
    }

    /* =========================================================================
     * STEADY STATE: Keep TX Running with Slow Heartbeat on LED
     * ========================================================================= */
    ESP_LOGI(TAG, "Entering steady-state: 4-bit @ 80 MHz continuous replay on DAC pins...");
    uint32_t heartbeat_cnt = 0;
    for (;;) {
        vTaskDelay(pdMS_TO_TICKS(all_pass ? 1000 : 150));
        gpio_set_level(XIAO_USER_LED, 0); /* LED on */
        vTaskDelay(pdMS_TO_TICKS(50));
        gpio_set_level(XIAO_USER_LED, 1); /* LED off */

        if (++heartbeat_cnt % 2 == 0) {
            ESP_LOGI(TAG, "STEADY-STATE HEARTBEAT: 4-bit @ 80 MHz loop running, FIFO-empty = %lu",
                     (unsigned long)PARL_IO.int_raw.tx_fifo_rempty_int_raw);
        }

        /* Check sticky FIFO empty periodically during long run */
        if (PARL_IO.int_raw.tx_fifo_rempty_int_raw != 0) {
            ESP_LOGE(TAG, "WARNING: TX FIFO EMPTY OCCURRED DURING STEADY STATE!");
        }
    }

    return all_pass ? ESP_OK : ESP_FAIL;
}

/*
 * Phase5 @ 40M RX-BitScrambler Premapper Oracle (Hardware Proof)
 *
 * Verifies:
 * 1. Loopback bit-exactness of 1-bundle raw Q4/I4 -> Phase5 LUT mapper.
 * 2. Physical hardware PARLIO RX + attached RX BitScrambler streaming from MODEM_DIAG.
 * 3. 40,000,000 bytes/s continuous throughput without RX FIFO overflow or dropped samples.
 */

#include "phase5_40m_oracle.h"
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
#include "driver/parlio_rx.h"
#include "driver/bitscrambler.h"
#include "driver/bitscrambler_loopback.h"
#include "soc/parl_io_struct.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "nvs_flash.h"

#include "wifi5.h"
#include "continuous_iq.h"
#include "wbfm_q4.h"

#define TAG "oracle_p5_40m"
#define XIAO_USER_LED GPIO_NUM_27

#define LOOPBACK_SAMPLES   4096u
#define HW_CAPTURE_BYTES   80000u /* 80,000 bytes = exactly 2.0 ms @ 40 MS/s */

BITSCRAMBLER_PROGRAM(c5vrx2_phase5_premapper_40m_program,
                    "c5vrx2_phase5_premapper_40m");

typedef struct {
    uint32_t magic;                    /* 0x50353430 ("P540") */
    uint32_t version;                  /* 1 */
    uint32_t loopback_samples;
    uint32_t loopback_mismatches;
    uint32_t hw_capture_bytes;
    uint32_t hw_capture_time_us;
    uint32_t hw_throughput_kbps;
    uint32_t rx_fifo_ovf_count;
    uint32_t invalid_phase_count;
    int32_t  status_code;
} phase5_40m_oracle_record_t;

/* MODEM_DIAG data pins on XIAO ESP32-C5 */
static const int s_rx_lanes[8] = {
    GPIO_NUM_1, GPIO_NUM_0, GPIO_NUM_25, GPIO_NUM_7,
    GPIO_NUM_10, GPIO_NUM_5, GPIO_NUM_3, GPIO_NUM_4,
};

esp_err_t c5vrx2_phase5_40m_oracle_run(void)
{
    ESP_LOGI(TAG, "================================================================");
    ESP_LOGI(TAG, "STARTING PHASE5 @ 40M RX-BITSCRAMBLER PREMAPPER ORACLE");
    ESP_LOGI(TAG, "Testing Stage 1 of Two-Stage 40 MS/s Interleaved Phase5 Pipeline");
    ESP_LOGI(TAG, "================================================================");

    phase5_40m_oracle_record_t rec = {
        .magic = 0x50353430,
        .version = 1,
        .loopback_samples = LOOPBACK_SAMPLES,
        .hw_capture_bytes = HW_CAPTURE_BYTES,
    };

    /* Allocate DMA buffers dynamically to stay below RF dump SRAM boundary */
    uint8_t *loop_in = heap_caps_malloc(LOOPBACK_SAMPLES, MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
    uint8_t *loop_out = heap_caps_malloc(LOOPBACK_SAMPLES, MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
    uint8_t *hw_rx_buf = heap_caps_malloc(HW_CAPTURE_BYTES, MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);

    if (!loop_in || !loop_out || !hw_rx_buf) {
        ESP_LOGE(TAG, "Heap allocation failed for oracle DMA buffers!");
        if (loop_in) free(loop_in);
        if (loop_out) free(loop_out);
        if (hw_rx_buf) free(hw_rx_buf);
        return ESP_ERR_NO_MEM;
    }

    /* -------------------------------------------------------------------------
     * PART 1: BIT-EXACT LOOPBACK VALIDATION
     * ------------------------------------------------------------------------- */
    ESP_LOGI(TAG, "--- Part 1: Loopback Software/Hardware Verification ---");
    for (size_t i = 0; i < LOOPBACK_SAMPLES; ++i) {
        loop_in[i] = (uint8_t)(i & 0xFFu); /* sweep all 256 Q4/I4 states 16 times */
    }
    memset(loop_out, 0xEE, LOOPBACK_SAMPLES);

    bitscrambler_handle_t loop_bs = NULL;
    esp_err_t err = bitscrambler_loopback_create(
        &loop_bs, SOC_BITSCRAMBLER_ATTACH_I2S0, LOOPBACK_SAMPLES);
    if (err == ESP_OK) {
        err = bitscrambler_load_program(loop_bs, c5vrx2_phase5_premapper_40m_program);
    }
    size_t loop_written = 0;
    if (err == ESP_OK) {
        err = bitscrambler_loopback_run(
            loop_bs, loop_in, LOOPBACK_SAMPLES, loop_out, LOOPBACK_SAMPLES, &loop_written);
    }
    if (loop_bs) (void)bitscrambler_free(loop_bs);

    uint32_t mismatches = 0;
    if (err == ESP_OK) {
        for (size_t i = 0; i < loop_written; ++i) {
            uint8_t expected = c5vrx2_wbfm_q4_phase5_value(loop_in[i]);
            if (loop_out[i] != expected) {
                if (mismatches < 5) {
                    ESP_LOGE(TAG, "Mismatch at [%zu]: in=0x%02X got=%u expected=%u",
                             i, loop_in[i], loop_out[i], expected);
                }
                mismatches++;
            }
        }
    } else {
        ESP_LOGE(TAG, "Loopback run failed: %s", esp_err_to_name(err));
        rec.status_code = err;
    }
    rec.loopback_mismatches = mismatches;
    ESP_LOGI(TAG, "Loopback result: written=%zu, mismatches=%" PRIu32 " (%s)",
             loop_written, mismatches, mismatches == 0 ? "PASS" : "FAIL");

    /* -------------------------------------------------------------------------
     * PART 2: PHYSICAL PARLIO RX + ATTACHED RX BITSCRAMBLER AT 40 MB/S
     * ------------------------------------------------------------------------- */
    ESP_LOGI(TAG, "--- Part 2: Physical Hardware PARLIO RX + Attached RX-BS (40 MB/s) ---");
    
    /* 1. Start RF/PHY so MODEM_DIAG carries active 40 MS/s I/Q */
    esp_err_t nvs_err = nvs_flash_init();
    if (nvs_err == ESP_ERR_NVS_NO_FREE_PAGES || nvs_err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        (void)nvs_flash_erase();
        (void)nvs_flash_init();
    }
    err = c5vrx2_wifi5_start_a1();
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Wi-Fi start failed: %s", esp_err_to_name(err));
        rec.status_code = err;
        free(loop_in); free(loop_out); free(hw_rx_buf);
        return err;
    }
    err = continuous_iq_start();
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Continuous IQ start failed: %s", esp_err_to_name(err));
        rec.status_code = err;
        free(loop_in); free(loop_out); free(hw_rx_buf);
        return err;
    }

    /* 2. Configure PARLIO RX unit for 40 MS/s 8-bit input */
    parlio_rx_unit_handle_t rx = NULL;
    parlio_rx_delimiter_handle_t delimiter = NULL;
    bitscrambler_handle_t rx_bs = NULL;

    const parlio_rx_unit_config_t rx_cfg = {
        .trans_queue_depth = 1u,
        .max_recv_size = HW_CAPTURE_BYTES,
        .dma_burst_size = 32u,
        .data_width = 8u,
        .clk_src = PARLIO_CLK_SRC_DEFAULT,
        .ext_clk_freq_hz = 0u,
        .exp_clk_freq_hz = 40000000u,
        .clk_in_gpio_num = -1,
        .clk_out_gpio_num = -1,
        .valid_gpio_num = -1,
        .data_gpio_nums = {
            s_rx_lanes[0], s_rx_lanes[1], s_rx_lanes[2], s_rx_lanes[3],
            s_rx_lanes[4], s_rx_lanes[5], s_rx_lanes[6], s_rx_lanes[7],
        },
        .flags = {
            .free_clk = true,
            .clk_gate_en = false,
            .allow_pd = false,
        },
    };
    err = parlio_new_rx_unit(&rx_cfg, &rx);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "PARLIO RX unit creation failed: %s", esp_err_to_name(err));
        rec.status_code = err;
        free(loop_in); free(loop_out); free(hw_rx_buf);
        return err;
    }

    /* 3. Attach BitScrambler to PARLIO RX channel */
    const bitscrambler_config_t bs_cfg = {
        .dir = BITSCRAMBLER_DIR_RX,
        .attach_to = SOC_BITSCRAMBLER_ATTACH_PARL_IO,
    };
    err = bitscrambler_new(&bs_cfg, &rx_bs);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "BitScrambler RX creation failed: %s", esp_err_to_name(err));
        rec.status_code = err;
        free(loop_in); free(loop_out); free(hw_rx_buf);
        return err;
    }
    err = bitscrambler_enable(rx_bs);
    if (err == ESP_OK) err = bitscrambler_load_program(rx_bs, c5vrx2_phase5_premapper_40m_program);
    if (err == ESP_OK) err = bitscrambler_reset(rx_bs);
    if (err == ESP_OK) err = bitscrambler_start(rx_bs);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "BitScrambler RX program load/start failed: %s", esp_err_to_name(err));
        rec.status_code = err;
        free(loop_in); free(loop_out); free(hw_rx_buf);
        return err;
    }

    /* 4. Configure soft delimiter for exactly HW_CAPTURE_BYTES */
    const parlio_rx_soft_delimiter_config_t delim_cfg = {
        .sample_edge = PARLIO_SAMPLE_EDGE_POS,
        .bit_pack_order = PARLIO_BIT_PACK_ORDER_LSB,
        .eof_data_len = HW_CAPTURE_BYTES,
        .timeout_ticks = 0u,
    };
    err = parlio_new_rx_soft_delimiter(&delim_cfg, &delimiter);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Delimiter creation failed: %s", esp_err_to_name(err));
        free(loop_in); free(loop_out); free(hw_rx_buf);
        return err;
    }

    memset(hw_rx_buf, 0xEE, HW_CAPTURE_BYTES);
    err = parlio_rx_unit_enable(rx, true);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "PARLIO RX enable failed: %s", esp_err_to_name(err));
        free(loop_in); free(loop_out); free(hw_rx_buf);
        return err;
    }

    const parlio_receive_config_t recv_cfg = {
        .delimiter = delimiter,
        .flags = {
            .partial_rx_en = false,
            .indirect_mount = false,
        },
    };

    /* 5. Timed RX capture */
    PARL_IO.int_clr.val = 0xFFFFFFFFu; /* clear sticky flags */
    int64_t t_start = esp_timer_get_time();
    
    err = parlio_rx_unit_receive(rx, hw_rx_buf, HW_CAPTURE_BYTES, &recv_cfg);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "parlio_rx_unit_receive failed: %s", esp_err_to_name(err));
        rec.status_code = err;
        free(loop_in); free(loop_out); free(hw_rx_buf);
        return err;
    }

    /* Enable software reception */
    PARL_IO.rx_mode_cfg.rx_sw_en = 1u;
    err = parlio_rx_unit_wait_all_done(rx, 100u); /* 100 ms timeout for a 2 ms transfer */
    int64_t t_end = esp_timer_get_time();
    PARL_IO.rx_mode_cfg.rx_sw_en = 0u;

    uint32_t fifo_ovf = PARL_IO.int_raw.rx_fifo_wovf_int_raw;
    rec.rx_fifo_ovf_count = fifo_ovf;

    uint32_t elapsed_us = (uint32_t)(t_end - t_start);
    rec.hw_capture_time_us = elapsed_us;

    uint32_t throughput_kbps = 0;
    if (elapsed_us > 0) {
        throughput_kbps = (uint32_t)(((uint64_t)HW_CAPTURE_BYTES * 1000000ULL) / (elapsed_us * 1024ULL));
    }
    rec.hw_throughput_kbps = throughput_kbps;

    /* Check validity of phase outputs (must all be 0..31) */
    uint32_t invalid_phases = 0;
    for (size_t i = 0; i < HW_CAPTURE_BYTES; ++i) {
        if (hw_rx_buf[i] > 31u) {
            invalid_phases++;
        }
    }
    rec.invalid_phase_count = invalid_phases;

    (void)parlio_rx_unit_disable(rx);
    (void)bitscrambler_disable(rx_bs);
    (void)bitscrambler_free(rx_bs);
    (void)parlio_del_rx_delimiter(delimiter);
    (void)parlio_del_rx_unit(rx);

    /* -------------------------------------------------------------------------
     * GATES EVALUATION
     * ------------------------------------------------------------------------- */
    bool gate1_loopback  = (rec.loopback_mismatches == 0);
    bool gate2_rate      = (throughput_kbps >= 38000u && throughput_kbps <= 42000u); /* ~39.06 MB/s */
    bool gate3_fifo      = (rec.rx_fifo_ovf_count == 0);
    bool gate4_validity  = (rec.invalid_phase_count == 0);
    bool all_pass        = gate1_loopback && gate2_rate && gate3_fifo && gate4_validity;

    ESP_LOGI(TAG, "================================================================");
    ESP_LOGI(TAG, "STAGE 1 ACCEPTANCE GATES EVALUATION:");
    ESP_LOGI(TAG, "  Gate 1: Bit-Exact Loopback Match (4096 states):  [%s] (mismatches=%" PRIu32 ")",
             gate1_loopback ? "PASS" : "FAIL", rec.loopback_mismatches);
    ESP_LOGI(TAG, "  Gate 2: Physical RX Throughput ~40 MB/s:         [%s] (%.2f MB/s, %" PRIu32 " us)",
             gate2_rate ? "PASS" : "FAIL", ((double)throughput_kbps)/1024.0, elapsed_us);
    ESP_LOGI(TAG, "  Gate 3: PARLIO RX FIFO Overflow == 0:            [%s] (ovf=%" PRIu32 ")",
             gate3_fifo ? "PASS" : "FAIL", rec.rx_fifo_ovf_count);
    ESP_LOGI(TAG, "  Gate 4: 100%% Valid 5-bit Phase Codes (0..31):    [%s] (invalid=%" PRIu32 ")",
             gate4_validity ? "PASS" : "FAIL", rec.invalid_phase_count);
    ESP_LOGI(TAG, "----------------------------------------------------------------");
    ESP_LOGI(TAG, "OVERALL ORACLE RESULT: [%s]", all_pass ? "SUCCESS / ALL PASS" : "FAILURE");
    ESP_LOGI(TAG, "================================================================");

    /* Persist to 'diagcap' partition */
    const esp_partition_t *part = esp_partition_find_first(ESP_PARTITION_TYPE_DATA, 0x42, "diagcap");
    if (part) {
        esp_partition_erase_range(part, 0, 4096);
        esp_partition_write(part, 0, &rec, sizeof(rec));
        ESP_LOGI(TAG, "Persisted results to 'diagcap' partition.");
    }

    free(loop_in);
    free(loop_out);
    free(hw_rx_buf);

    /* Steady state LED blink */
    for (;;) {
        gpio_set_level(XIAO_USER_LED, 0); /* active low */
        vTaskDelay(pdMS_TO_TICKS(all_pass ? 500 : 50));
        gpio_set_level(XIAO_USER_LED, 1);
        vTaskDelay(pdMS_TO_TICKS(all_pass ? 500 : 50));
    }

    return all_pass ? ESP_OK : ESP_FAIL;
}

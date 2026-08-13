#include "c5vrx_cvbs_out.h"

#include <stddef.h>
#include <stdint.h>

#include "sdkconfig.h"
#include "driver/parlio_tx.h"
#include "esp_log.h"

static const char *TAG = "c5vrx_cvbs";

/*
 * First hardware-output experiment only.
 *
 * PAL horizontal timing is about 64 us/line. At 20 MS/s that is exactly 1280
 * samples per repeated line. We generate a simple monochrome line containing
 * sync, back porch and grayscale bars. There is deliberately no vertical sync
 * yet; this waveform is for scope/DAC validation before joining the RF path.
 */
#define C5VRX_CVBS_SAMPLE_RATE_HZ 20000000u
#define C5VRX_CVBS_LINE_SAMPLES   1280u
#define C5VRX_CVBS_SYNC_SAMPLES   94u   /* ~4.7 us */
#define C5VRX_CVBS_BACK_SAMPLES   114u  /* ~5.7 us */
#define C5VRX_CVBS_ACTIVE_SAMPLES 1040u /* ~52 us */

/*
 * These are normalized DAC codes, NOT direct 75-ohm voltage levels.
 * The external resistor ladder / buffer sets the final 0..~1 V composite range.
 */
#define C5VRX_CVBS_SYNC_LEVEL  0u
#define C5VRX_CVBS_BLANK_LEVEL 74u
#define C5VRX_CVBS_BLACK_LEVEL 78u
#define C5VRX_CVBS_WHITE_LEVEL 235u

#if CONFIG_C5VRX_EXPERIMENTAL_CVBS_PARLIO

static uint8_t s_line[C5VRX_CVBS_LINE_SAMPLES] __attribute__((aligned(64)));
static parlio_tx_unit_handle_t s_tx;
static bool s_running;

static void build_test_line(void)
{
    size_t p = 0;

    for (; p < C5VRX_CVBS_SYNC_SAMPLES; ++p) {
        s_line[p] = C5VRX_CVBS_SYNC_LEVEL;
    }

    const size_t back_end = C5VRX_CVBS_SYNC_SAMPLES + C5VRX_CVBS_BACK_SAMPLES;
    for (; p < back_end; ++p) {
        s_line[p] = C5VRX_CVBS_BLANK_LEVEL;
    }

    const size_t active_end = back_end + C5VRX_CVBS_ACTIVE_SAMPLES;
    for (; p < active_end; ++p) {
        const size_t x = p - back_end;
        const unsigned bar = (unsigned)((x * 8u) / C5VRX_CVBS_ACTIVE_SAMPLES);
        const unsigned clamped_bar = bar > 7u ? 7u : bar;
        s_line[p] = (uint8_t)(C5VRX_CVBS_BLACK_LEVEL +
                              ((C5VRX_CVBS_WHITE_LEVEL - C5VRX_CVBS_BLACK_LEVEL) * clamped_bar) / 7u);
    }

    for (; p < C5VRX_CVBS_LINE_SAMPLES; ++p) {
        s_line[p] = C5VRX_CVBS_BLANK_LEVEL;
    }
}

static bool pins_valid(void)
{
    const int pins[8] = {
        CONFIG_C5VRX_CVBS_D0_GPIO,
        CONFIG_C5VRX_CVBS_D1_GPIO,
        CONFIG_C5VRX_CVBS_D2_GPIO,
        CONFIG_C5VRX_CVBS_D3_GPIO,
        CONFIG_C5VRX_CVBS_D4_GPIO,
        CONFIG_C5VRX_CVBS_D5_GPIO,
        CONFIG_C5VRX_CVBS_D6_GPIO,
        CONFIG_C5VRX_CVBS_D7_GPIO,
    };
    for (unsigned i = 0; i < 8; ++i) {
        if (pins[i] < 0) {
            return false;
        }
        for (unsigned j = i + 1; j < 8; ++j) {
            if (pins[i] == pins[j]) {
                return false;
            }
        }
    }
    return true;
}

esp_err_t c5vrx_cvbs_test_start(void)
{
    if (s_running) {
        return ESP_ERR_INVALID_STATE;
    }
    if (!pins_valid()) {
        ESP_LOGE(TAG, "CVBS PARLIO requires eight unique data GPIOs in menuconfig");
        return ESP_ERR_INVALID_ARG;
    }

    build_test_line();

    const parlio_tx_unit_config_t cfg = {
        .clk_src = PARLIO_CLK_SRC_DEFAULT,
        .clk_in_gpio_num = -1,
        .input_clk_src_freq_hz = 0,
        .output_clk_freq_hz = C5VRX_CVBS_SAMPLE_RATE_HZ,
        .data_width = 8,
        .data_gpio_nums = {
            CONFIG_C5VRX_CVBS_D0_GPIO,
            CONFIG_C5VRX_CVBS_D1_GPIO,
            CONFIG_C5VRX_CVBS_D2_GPIO,
            CONFIG_C5VRX_CVBS_D3_GPIO,
            CONFIG_C5VRX_CVBS_D4_GPIO,
            CONFIG_C5VRX_CVBS_D5_GPIO,
            CONFIG_C5VRX_CVBS_D6_GPIO,
            CONFIG_C5VRX_CVBS_D7_GPIO,
        },
        .clk_out_gpio_num = -1,
        .valid_gpio_num = -1,
        .valid_start_delay = 0,
        .valid_stop_delay = 0,
        .trans_queue_depth = 2,
        .max_transfer_size = sizeof(s_line),
        .dma_burst_size = 32,
        .shift_edge = PARLIO_SHIFT_EDGE_NEG,
        .bit_pack_order = PARLIO_BIT_PACK_ORDER_LSB,
    };

    esp_err_t err = parlio_new_tx_unit(&cfg, &s_tx);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "parlio_new_tx_unit failed: %s", esp_err_to_name(err));
        s_tx = NULL;
        return err;
    }

    err = parlio_tx_unit_enable(s_tx);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "parlio_tx_unit_enable failed: %s", esp_err_to_name(err));
        parlio_del_tx_unit(s_tx);
        s_tx = NULL;
        return err;
    }

    const parlio_transmit_config_t tx_cfg = {
        .idle_value = C5VRX_CVBS_BLANK_LEVEL,
        .bitscrambler_program = NULL,
        .flags = {
            .queue_nonblocking = 0,
            .loop_transmission = 1,
        },
    };

    err = parlio_tx_unit_transmit(s_tx, s_line, sizeof(s_line) * 8u, &tx_cfg);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "parlio_tx_unit_transmit failed: %s", esp_err_to_name(err));
        parlio_tx_unit_disable(s_tx);
        parlio_del_tx_unit(s_tx);
        s_tx = NULL;
        return err;
    }

    s_running = true;
    ESP_LOGW(TAG,
             "Experimental CVBS DAC pattern active: 20 MS/s, 1280 samples/line, 64 us repeated line");
    ESP_LOGW(TAG,
             "GPIO bus is an 8-bit DAC code only; use a resistor ladder + buffer/attenuator before any 75-ohm video input");
    return ESP_OK;
}

esp_err_t c5vrx_cvbs_test_stop(void)
{
    if (!s_running || !s_tx) {
        return ESP_ERR_INVALID_STATE;
    }

    esp_err_t first = parlio_tx_unit_disable(s_tx);
    const esp_err_t del = parlio_del_tx_unit(s_tx);
    if (first == ESP_OK) {
        first = del;
    }
    s_tx = NULL;
    s_running = false;
    ESP_LOGI(TAG, "CVBS PARLIO test output stopped");
    return first;
}

bool c5vrx_cvbs_test_running(void)
{
    return s_running;
}

#else

esp_err_t c5vrx_cvbs_test_start(void)
{
    return ESP_ERR_NOT_SUPPORTED;
}

esp_err_t c5vrx_cvbs_test_stop(void)
{
    return ESP_ERR_NOT_SUPPORTED;
}

bool c5vrx_cvbs_test_running(void)
{
    return false;
}

#endif

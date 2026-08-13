#include "c5vrx_cvbs_live_out.h"

#include <stdlib.h>
#include <string.h>
#include "sdkconfig.h"
#include "driver/parlio_tx.h"
#include "esp_heap_caps.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#if CONFIG_C5VRX_EXPERIMENTAL_CVBS_PARLIO
static parlio_tx_unit_handle_t s_live_tx;
static uint8_t *s_live_buffer[2];
static size_t s_live_samples;
static TaskHandle_t s_writer;
static bool s_transmission_started;

static bool on_switched(parlio_tx_unit_handle_t unit,
                        const parlio_tx_buffer_switched_event_data_t *event,
                        void *context)
{
    (void)unit; (void)context;
    if (!event || !s_writer) return false;
    uint32_t value = event->old_buffer_addr == s_live_buffer[0] ? 1u :
                     event->old_buffer_addr == s_live_buffer[1] ? 2u : 0u;
    if (!value) return false;
    BaseType_t wake = pdFALSE;
    xTaskNotifyFromISR(s_writer, value, eSetBits, &wake);
    return wake == pdTRUE;
}

static esp_err_t queue_buffer(uint8_t *buffer)
{
    const parlio_transmit_config_t config = {
        .idle_value = 19u,
        .flags = {.queue_nonblocking = 0, .loop_transmission = 1},
    };
    return parlio_tx_unit_transmit(s_live_tx, buffer, s_live_samples * 8u, &config);
}

esp_err_t c5vrx_cvbs_live_out_start(size_t block_samples)
{
    if (s_live_tx || block_samples == 0u) return ESP_ERR_INVALID_STATE;
    s_live_samples = block_samples;
    for (unsigned i = 0; i < 2; ++i) {
        s_live_buffer[i] = heap_caps_malloc(block_samples,
                                            MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
        if (!s_live_buffer[i]) { c5vrx_cvbs_live_out_stop(); return ESP_ERR_NO_MEM; }
        memset(s_live_buffer[i], 19, block_samples);
    }
    const parlio_tx_unit_config_t config = {
        .clk_src = PARLIO_CLK_SRC_DEFAULT, .clk_in_gpio_num = -1,
        .output_clk_freq_hz = 20000000u, .data_width = 8,
        .data_gpio_nums = {CONFIG_C5VRX_CVBS_D0_GPIO, CONFIG_C5VRX_CVBS_D1_GPIO,
            CONFIG_C5VRX_CVBS_D2_GPIO, CONFIG_C5VRX_CVBS_D3_GPIO,
            CONFIG_C5VRX_CVBS_D4_GPIO, CONFIG_C5VRX_CVBS_D5_GPIO,
            CONFIG_C5VRX_CVBS_D6_GPIO, CONFIG_C5VRX_CVBS_D7_GPIO},
        .clk_out_gpio_num = -1, .valid_gpio_num = -1,
        .trans_queue_depth = 2, .max_transfer_size = block_samples,
        .dma_burst_size = 32, .shift_edge = PARLIO_SHIFT_EDGE_NEG,
        .bit_pack_order = PARLIO_BIT_PACK_ORDER_LSB,
    };
    esp_err_t err = parlio_new_tx_unit(&config, &s_live_tx);
    const parlio_tx_event_callbacks_t callbacks = {.on_buffer_switched = on_switched};
    if (err == ESP_OK) err = parlio_tx_unit_register_event_callbacks(s_live_tx, &callbacks, NULL);
    if (err == ESP_OK) err = parlio_tx_unit_enable(s_live_tx);
    if (err != ESP_OK) c5vrx_cvbs_live_out_stop();
    return err;
}

esp_err_t c5vrx_cvbs_live_out_write(const uint8_t *samples, size_t count,
                                    void *context)
{
    (void)context;
    if (!s_live_tx || !samples || count != s_live_samples) return ESP_ERR_INVALID_ARG;
    s_writer = xTaskGetCurrentTaskHandle();
    if (!s_transmission_started) {
        memcpy(s_live_buffer[0], samples, count);
        memcpy(s_live_buffer[1], samples, count);
        esp_err_t err = queue_buffer(s_live_buffer[0]);
        if (err == ESP_OK) err = queue_buffer(s_live_buffer[1]);
        if (err == ESP_OK) s_transmission_started = true;
        return err;
    }
    uint32_t retired = 0;
    if (xTaskNotifyWait(0, UINT32_MAX, &retired, pdMS_TO_TICKS(20)) != pdTRUE)
        return ESP_ERR_TIMEOUT;
    if (retired == 3u) return ESP_ERR_TIMEOUT; /* Both retired: output starved. */
    const unsigned index = retired == 1u ? 0u : retired == 2u ? 1u : 2u;
    if (index > 1u) return ESP_FAIL;
    memcpy(s_live_buffer[index], samples, count);
    return queue_buffer(s_live_buffer[index]);
}

esp_err_t c5vrx_cvbs_live_out_stop(void)
{
    if (s_live_tx) {
        (void)parlio_tx_unit_disable(s_live_tx);
        (void)parlio_del_tx_unit(s_live_tx);
        s_live_tx = NULL;
    }
    for (unsigned i = 0; i < 2; ++i) { free(s_live_buffer[i]); s_live_buffer[i] = NULL; }
    s_writer = NULL; s_live_samples = 0; s_transmission_started = false;
    return ESP_OK;
}
#else
esp_err_t c5vrx_cvbs_live_out_start(size_t n) { (void)n; return ESP_ERR_NOT_SUPPORTED; }
esp_err_t c5vrx_cvbs_live_out_write(const uint8_t *s, size_t n, void *c)
{ (void)s; (void)n; (void)c; return ESP_ERR_NOT_SUPPORTED; }
esp_err_t c5vrx_cvbs_live_out_stop(void) { return ESP_OK; }
#endif

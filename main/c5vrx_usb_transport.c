/* SPDX-License-Identifier: GPL-3.0-only */

#include "c5vrx_usb_transport.h"

#include <stdarg.h>
#include <stdio.h>

#include "driver/usb_serial_jtag.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

static StaticSemaphore_t s_tx_mutex_storage;
static SemaphoreHandle_t s_tx_mutex;

esp_err_t c5vrx_usb_transport_init(void)
{
    if (!s_tx_mutex) {
        s_tx_mutex = xSemaphoreCreateMutexStatic(&s_tx_mutex_storage);
    }
    return s_tx_mutex ? ESP_OK : ESP_ERR_NO_MEM;
}

static esp_err_t write_locked(const uint8_t *data, size_t size)
{
    size_t offset = 0;
    unsigned empty_writes = 0;
    while (offset < size) {
        const int written = usb_serial_jtag_write_bytes(
            data + offset, size - offset, pdMS_TO_TICKS(100));
        if (written < 0) return ESP_FAIL;
        if (written == 0) {
            if (++empty_writes >= 20u) return ESP_ERR_TIMEOUT;
            vTaskDelay(1);
            continue;
        }
        empty_writes = 0;
        offset += (size_t)written;
    }
    return ESP_OK;
}

esp_err_t c5vrx_usb_writev(const c5vrx_usb_iovec_t *parts, size_t part_count)
{
    if ((!parts && part_count) || !s_tx_mutex) return ESP_ERR_INVALID_STATE;
    if (xSemaphoreTake(s_tx_mutex, pdMS_TO_TICKS(2500)) != pdTRUE)
        return ESP_ERR_TIMEOUT;

    esp_err_t result = ESP_OK;
    for (size_t i = 0; i < part_count; ++i) {
        if (!parts[i].size) continue;
        if (!parts[i].data) {
            result = ESP_ERR_INVALID_ARG;
            break;
        }
        result = write_locked(parts[i].data, parts[i].size);
        if (result != ESP_OK) break;
    }
    xSemaphoreGive(s_tx_mutex);
    return result;
}

esp_err_t c5vrx_usb_write(const void *data, size_t size)
{
    const c5vrx_usb_iovec_t part = {.data = data, .size = size};
    return c5vrx_usb_writev(&part, 1u);
}

int c5vrx_usb_vprintf(const char *format, va_list args)
{
    char line[2048];
    const int required = vsnprintf(line, sizeof(line), format, args);
    if (required < 0) return required;
    const size_t count = (size_t)required < sizeof(line)
                             ? (size_t)required
                             : sizeof(line) - 1u;
    return c5vrx_usb_write(line, count) == ESP_OK ? required : -1;
}

int c5vrx_usb_printf(const char *format, ...)
{
    va_list args;
    va_start(args, format);
    const int result = c5vrx_usb_vprintf(format, args);
    va_end(args);
    return result;
}

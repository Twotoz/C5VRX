/* SPDX-License-Identifier: GPL-3.0-only */

#include "c5vrx_status_led.h"

#include <stdbool.h>
#include <stdint.h>

#include "sdkconfig.h"
#include "driver/gpio.h"
#include "esp_timer.h"
#include "c5vrx_cvbs_out.h"

static bool s_initialized;
static gpio_num_t s_gpio = GPIO_NUM_NC;

static void set_led(bool on)
{
#ifdef CONFIG_C5VRX_STATUS_LED_ACTIVE_LOW
    const int level = !on;
#else
    const int level = on;
#endif
    gpio_set_level(s_gpio, level);
}

esp_err_t c5vrx_status_led_init(void)
{
    if (CONFIG_C5VRX_STATUS_LED_GPIO < 0) return ESP_OK;
    s_gpio = (gpio_num_t)CONFIG_C5VRX_STATUS_LED_GPIO;
    const gpio_config_t config = {
        .pin_bit_mask = 1ULL << (unsigned)s_gpio,
        .mode = GPIO_MODE_OUTPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    const esp_err_t err = gpio_config(&config);
    if (err == ESP_OK) {
        s_initialized = true;
        set_led(false);
    }
    return err;
}

void c5vrx_status_led_tick(void)
{
    if (!s_initialized) return;

    c5vrx_cvbs_output_stats_t stats = {0};
    c5vrx_cvbs_output_get_stats(&stats);
    const uint32_t phase_ms = (uint32_t)(esp_timer_get_time() / 1000u) % 1000u;
    bool on = false;
    if (stats.health == C5VRX_AV_HEALTH_FAIL) {
        on = (phase_ms % 200u) < 100u;
    } else if (stats.health == C5VRX_AV_HEALTH_WARN) {
        on = phase_ms < 80u || (phase_ms >= 160u && phase_ms < 240u) ||
             (phase_ms >= 320u && phase_ms < 400u);
    } else if (stats.health == C5VRX_AV_HEALTH_STARTING) {
        on = phase_ms < 500u;
    } else if (stats.display == C5VRX_CVBS_DISPLAY_SNOW) {
        on = phase_ms < 100u || (phase_ms >= 200u && phase_ms < 300u);
    } else {
        on = phase_ms < 100u;
    }
    set_led(on);
}

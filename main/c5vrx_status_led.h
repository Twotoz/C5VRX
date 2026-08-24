/* SPDX-License-Identifier: GPL-3.0-only */

#pragma once

#include "esp_err.h"

esp_err_t c5vrx_status_led_init(void);
void c5vrx_status_led_tick(void);

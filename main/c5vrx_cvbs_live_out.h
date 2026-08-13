#pragma once
#include <stddef.h>
#include <stdint.h>
#include "esp_err.h"

esp_err_t c5vrx_cvbs_live_out_start(size_t block_samples);
esp_err_t c5vrx_cvbs_live_out_write(const uint8_t *samples, size_t count,
                                    void *context);
esp_err_t c5vrx_cvbs_live_out_stop(void);

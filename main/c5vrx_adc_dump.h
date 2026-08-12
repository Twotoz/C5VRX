#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/*
 * ESP32-C5 ESP-IDF v6.0.2 librftest.a uses this fixed FE/ADC dump RAM in
 * loop_dump_test(): base 0x40830000, size 0x10000 bytes.
 */
#define C5VRX_ADC_DUMP_BASE_ADDR 0x40830000u
#define C5VRX_ADC_DUMP_SIZE_BYTES 0x10000u
#define C5VRX_ADC_DUMP_MAX_SAMPLES (C5VRX_ADC_DUMP_SIZE_BYTES / sizeof(uint32_t))

typedef struct {
    int16_t i;
    int16_t q;
    uint32_t raw;
} c5vrx_iq10_sample_t;

/** Decode the lower 20 bits of one C5 RF-test dump word as signed 10-bit I/Q. */
c5vrx_iq10_sample_t c5vrx_adc_decode_word(uint32_t raw);

/**
 * EXPERIMENTAL finite ADC/IQ capture using the vendor RF-test adctrig path.
 *
 * The ABI and dump layout are supported by C5 v6.0.2 disassembly, but actual
 * capture behavior still needs physical ESP32-C5 validation. The routine uses
 * software trigger, 80 MHz sample mode and 10-bit dump mode.
 */
esp_err_t c5vrx_adc_dump_capture(size_t sample_count, bool print_raw_words);

#ifdef __cplusplus
}
#endif

/* C5VRX-5 by Twotoz and the C5VRX contributors: ESP32-C5 receive-only RF
 * front end. Wi-Fi/PHY tunes the channel; MODEM_DIAG streams the ADC's
 * ultrafine Q4/I4 bits {9,6,5,4} to the PARLIO RX pins. Firmware owns the
 * gain (vendor AGC disabled), the analog filter and the frequency offset. */
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "esp_err.h"
#include "arc_phy.h"

esp_err_t rf_start(void);
/* Stop the dump writer and Wi-Fi, request a USB download boot and restart,
 * so esptool can flash with --before no-reset. Never returns. */
void rf_reboot_to_download(void);

/* Firmware gain (vendor table index). True only when written under the
 * expected PHY generation. */
bool rf_set_gain(uint8_t gain, uint32_t expected_generation);
void rf_force_gain(uint8_t gain);           /* caller owns the receiver */
uint8_t rf_gain(void);
/* Called after every gain write and PHY restore (DC hold re-assert). */
void rf_set_post_gain_hook(void (*hook)(uint8_t gain));
const arc_gain_table_t *rf_gain_table(void);
uint32_t rf_table_generation(void);

/* Analog receive filter. code PHY_FILTER_CALIBRATED = the chip's own
 * calibration; skirt = second stage offset; bw20 = digital filter BW20. */
typedef struct { int8_t code; uint8_t skirt; bool bw20; } rf_filter_t;
void rf_set_filter(rf_filter_t filter);
rf_filter_t rf_filter(void);

typedef struct { const char *name; uint16_t freq_mhz; } fpv_channel_t;
enum { FPV_BAND_R, FPV_BAND_A, FPV_BAND_B, FPV_BAND_E, FPV_BAND_F, FPV_BAND_L, FPV_BANDS };
#define FPV_CHANNELS (FPV_BANDS * 8u)
const fpv_channel_t *rf_channel(void);
size_t rf_channel_index(void);
const char *rf_band_name(unsigned band);
esp_err_t rf_set_channel(size_t index);
void rf_next_band(void);
void rf_next_channel(void);
uint16_t rf_frequency_mhz(void);
int rf_offset_khz(void);
void rf_set_offset_khz(int offset_khz);

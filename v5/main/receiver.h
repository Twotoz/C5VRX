/* C5VRX-5 by Twotoz and the C5VRX contributors: receiver-wide state.
 *
 * Data plane (hardware only): MODEM_DIAG Q4/I4 (fixed ultrafine lane) ->
 * PARLIO RX 40 MS/s -> 32 KiB cyclic raw ring -> TX BitScrambler U85Q
 * (50 ns span, two bundles, LUT16) -> PARLIO TX 40 MHz [D,D] -> 6-bit DAC.
 * The CPU only observes completed RX descriptors and owns gain, AFC, DC,
 * filter, menu and console. */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "hal/dma_types.h"
#include "gain.h"
#include "menu_raster.h"

#define IQ_RATE_HZ          40000000u
#define RAW_RING_BYTES      32768u
#define DAC_IDLE_CODE       20u          /* blanking level; sync is 0 */
#define CONTROL_BYTES       4092u        /* one completed GDMA descriptor */
#define PROBE_REGIONS       4u
#define PROBE_REGION_BYTES  64u
#define PROBE_BYTES         (PROBE_REGIONS * PROBE_REGION_BYTES)
#define MAX_RING_DESCRIPTORS 16
/* AFC centres the VTX blanking level here. The U85Q window (-6..+9 MHz)
 * holds more of the sync-to-white swing with the porch at -1.0 MHz than at
 * the -436 kHz that suited the trained trackers: held-out simulation,
 * 400 cases, picture 0.969 vs 0.908, +0.85 dB SINAD, deviation 1.8 VTXs
 * 0.90 vs 0.64 (docs/FINDINGS.md). */
#define AFC_PORCH_TARGET_KHZ (-1000)

typedef struct {
    dma_descriptor_t *dscr;
    uint8_t *buffer;
    uint32_t length;
} ring_node_t;

/* Cheap envelope/phase statistics of a raw Q4/I4 window. */
typedef struct {
    int p_median;      /* median i^2+q^2 of the signed nibbles */
    int q_phase;       /* % of adjacent pairs with a coherent phase step */
    int clip_pm;       /* rail codes, per mille */
    int origin_pm;     /* origin cells, per mille */
} metrics_t;
metrics_t measure_window(const uint8_t *s, size_t n);

typedef enum { STD_MODE_AUTO = 0, STD_MODE_NTSC = 1, STD_MODE_PAL = 2 } std_mode_t;

/* ---- transport.c ---- */
extern uint8_t s_raw_ring[RAW_RING_BYTES];
extern int s_rx_dma_ch, s_tx_dma_ch;
extern ring_node_t s_rx_nodes[MAX_RING_DESCRIPTORS];
extern int s_rx_node_count;
esp_err_t transport_start(void);
void transport_stop_tx(void);
void transport_resume_live(void);
void transport_start_chain(dma_descriptor_t *first);
void transport_stop_chain(void);
void transport_poll_faults(void);
void transport_print(void);
void sync_dma_c2m(const void *addr, size_t size);
void sync_dma_m2c(const void *addr, size_t size);
int ring_node_index(uint32_t addr);
/* Copies that RX cannot have overwritten (false = stale, retry). */
bool copy_control_window(uint8_t *dst, size_t bytes);
bool copy_probe_window(uint8_t dst[PROBE_BYTES], int *node);
bool copy_snapshot(uint8_t *dst);
bool copy_recent_endpoints(uint8_t *dst, size_t n);
void rx_clock_slip(uint32_t us);

/* ---- receiver.c (Direct Gain V5 owner) ---- */
extern direct_gain_t s_gain;
extern volatile uint8_t s_current_gain;
extern volatile uint32_t s_gain_epoch;      /* bumps on every gain/offset write */
extern volatile uint32_t s_control_epoch;   /* bumps on retune/scan/calibration */
extern volatile int64_t s_last_gain_write_us;
extern volatile int s_obs_p50, s_obs_p95, s_obs_origin_pm, s_obs_clip_pm, s_obs_coherence;
extern volatile uint32_t s_obs_us_max, s_obs_windows;
extern dg_map_blob_t s_gain_map_saved;
extern bool s_gain_map_saved_valid;
void receiver_start(void);
bool receiver_active(void);
bool receiver_paused(void);
/* Pause gain control for a calibration/scan that owns the gain itself. */
bool receiver_pause(const char *tag);
void receiver_resume(void);
void receiver_set_gain(uint8_t gain);
void receiver_print(void);

/* ---- control.c ---- */
extern volatile bool s_menu_active;
extern volatile int s_signal_strength;
extern volatile bool s_scan_active;
extern volatile unsigned s_scan_progress;
extern volatile int s_cfo_khz;
extern volatile bool s_afc_locked;
extern volatile std_mode_t s_std_mode;
extern volatile video_standard_t s_video_std;      /* standard the menu emits */
extern volatile bool s_detected_std_valid;
extern volatile video_standard_t s_detected_std;
extern volatile int64_t s_last_video_us;           /* last burst-confirmed sync */
extern QueueHandle_t s_commands;
void control_start(void);
video_standard_t menu_standard(void);
void control_print(void);

/* ---- calibration.c ---- */
void calibration_start(void);
bool filter_calibrate(bool automatic);
void filter_request(void);
void calibration_print(void);
void dco_post_gain(uint8_t gain);

/* ---- menu.c ---- */
enum { MENU_BAND, MENU_CHANNEL, MENU_SCAN, MENU_VIDEO, MENU_EXIT, MENU_ITEMS };
extern volatile int s_menu_cursor;
bool menu_open(void);
void menu_close(void);
void menu_render(void);
void menu_set_standard_mode(std_mode_t mode);

/* ---- settings.c ---- */
void settings_load(void);
void settings_save(void);
bool blob_load(const char *key, void *data, size_t size);
bool blob_store(const char *key, const void *data, size_t size);

/* ---- console.c ---- */
void console_start(void);

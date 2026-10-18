/* C5VRX by Twotoz/contributors: camera-clock pulse planning and conditional
 * admission ONLY. No timer, GPIO/OE, PHY, task or boot activation here.
 * Backend seam: borrow the observer's 20 MHz GPTIMER, alarm at alarm_tick,
 * wait boundedly until start, call decide(), and gate ONLY REPAIR tips.
 * Backend must restore OE between every tip and synchronously on cancel.
 * Its alarm/vertical-gap WCET and analogue output still need qualification. */
#pragma once
#include <stdbool.h>
#include <stdint.h>

typedef enum { SLG_NTSC=1, SLG_PAL=2 } slg_standard_t;
typedef enum { SLG_EQ, SLG_BROAD, SLG_H } slg_kind_t;
typedef enum { SLG_CANCELLED, SLG_EARLY, SLG_LATE, SLG_NATIVE, SLG_REPAIR } slg_decision_t;
typedef struct {
    uint32_t lead_ticks, late_ticks, native_window_ticks, native_age_ticks;
    uint32_t model_age_ticks, max_uncertainty_ticks;
} slg_config_t;
typedef struct {
    uint64_t field, last_h, observed_at;
    uint32_t line_ticks_x32, receive_epoch, uncertainty_ticks;
    slg_standard_t standard;
    bool qualified;
} slg_model_t;
typedef struct {
    uint64_t start, alarm_tick, half, field;
    uint32_t width, serial;
    uint16_t index;
    slg_kind_t kind;
} slg_pulse_t;
/* Fast witness must be native DATA4, independent of DAC OE. A LOW candidate
 * is admitted only with independently qualified camera phase, current receive
 * epoch and a fresh observed falling edge in the expected phase window.
 * This cannot prove the eventual full width of an in-progress native pulse. */
typedef struct {
    uint64_t fall, observed_at;
    uint32_t receive_epoch;
    bool low, qualified;
} slg_native_t;
typedef bool (*slg_native_fn)(void *arg,const slg_pulse_t *pulse,slg_native_t *out);
typedef struct sync_live_gate sync_live_gate_t;
typedef struct {
    uint64_t origin,next_half;
    uint32_t numerator;
    uint8_t denominator,h_parity;
    slg_standard_t standard;
} slg_clock_seed_t;
bool sync_live_gate_export(const sync_live_gate_t *gate,slg_clock_seed_t *out);

/* Allocate after receiver startup. All task mutations must be serialized;
 * C5 ISR readers refuse interrupted publications instead of waiting. */
sync_live_gate_t *sync_live_gate_create(const slg_config_t *config);
void sync_live_gate_destroy(sync_live_gate_t *gate);
/* Explicit selected-standard fallback; no claim of camera acquisition. */
bool sync_live_gate_nominal(sync_live_gate_t *gate,slg_standard_t standard,
                           uint64_t first_eq,uint8_t h_half_parity,uint32_t receive_epoch,uint64_t now);
bool sync_live_gate_arm(sync_live_gate_t *gate);

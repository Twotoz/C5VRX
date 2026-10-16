/* C5VRX by Twotoz and contributors: native DAC timing observation.
 * Manual wrappers require the PHY actuator lease, verified LP/output1.0x and
 * no menu/restart while live(generation) remains true. GPIO2 must be unused;
 * it temporarily mirrors PARLIO DATA4 independently of the six DAC OEs.
 * GPIO8 and all DAC settings remain unchanged by this observer. The live
 * predicate is called from ISR context and must be IRAM safe. No activation
 * at boot; no camera-lock or analogue waveform claim follows from this probe. */
#pragma once
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include "sync_native_trace.h"
void sync_output_probe(uint32_t generation, bool (*live)(uint32_t));
void sync_output_clock_probe(uint32_t generation, bool (*live)(uint32_t));

/* Resident observer. Task context only; caller serializes begin/poll/end
 * against transport mutations with short ownership, never a resident lease.
 * At1x the resident DATA4 observation can be investigated on either U85Q
 * program; absence of a qualified model alone does not authorize repairing
 * an unqualified strong-signal program. No output gates are enabled here.
 * Invalidate BEFORE a receive
 * mutation; poll rearms fresh evidence on the same running timer. Snapshot
 * freshness includes RMT delivery latency; it is not a per-line loss flag.
 * Snapshot coordinates remain available with valid=false during holdover.
 * A failed end quarantines driver ownership and prevents subsequent begin. */
typedef struct {
    uint64_t last_h, field, now;
    uint32_t generation, line_ticks_x32;
    int64_t phase_lo, phase_hi;
    uint8_t standard; /* Native qualifier IDs: PAL=2, NTSC=1. */
    bool valid;
} sync_output_live_model_t;
bool sync_output_live_begin(uint32_t generation, bool (*live)(uint32_t));
bool sync_output_live_poll(void);
bool sync_output_live_end(void);
bool sync_output_live_active(void);
/* Task-side teardown dependency: stop any consumer of the shared clock and
 * restore DAC ownership BEFORE observer teardown checks/clock deletion. */
void sync_output_live_before_close(bool (*callback)(void));
void sync_output_live_invalidate(void);
bool sync_output_live_snapshot(sync_output_live_model_t *out);
/* Task-side diagnostic snapshot, under the same serialized ownership. */
bool sync_output_live_diagnostics(char *out,size_t size);
bool sync_output_live_trace_snapshot(snt_trace_t *out);

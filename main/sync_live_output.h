/* C5VRX by Twotoz/contributors: conditional tips on the native timebase.
 * Caller serializes task APIs and cancels before changing transport/pad owner.
 * No task/PHY lease is owned for the lifetime. Analogue/monitor proof is separate. */
#pragma once
#include <stdbool.h>
#include <stdint.h>
#include "sync_live_gate.h"

typedef bool (*slo_live_fn)(uint32_t generation);
/* IRAM source guard: false during markers, RX rearm and receive mutations. */
typedef bool (*slo_source_fn)(void *arg,uint32_t *epoch);
typedef bool (*slo_native_fn)(uint64_t start,uint32_t width,slg_kind_t kind,void *arg);
typedef struct {
    uint32_t repaired,suppressed,batches,max_late_ticks,queue_low,fault,missed,miss_kind;
    uint32_t max_irq_delay_ticks,max_callback_ticks,miss_phase,misses_by_phase[6],budget_yields;
    /* Camera-phase guard: tips left to a present off-grid camera, grid
     * re-anchors (H incl. fine trims, V) from coherent native pulses. */
    uint32_t yielded,guard_h_jumps,guard_v_jumps,guard_trims;
    bool active,guard_yield;
} slo_status_t;
/* Explicit selected-standard fallback if model is NULL/unqualified. Native
 * callback must be cache-safe/IRAM; NULL admits no native suppression evidence. */
bool sync_live_output_begin(const slg_model_t *model,slg_standard_t fallback,
    uint32_t generation,slo_live_fn live,slo_native_fn native,void *arg);
bool sync_live_output_poll(void);
bool sync_live_output_end(void);
void sync_live_output_status(slo_status_t *out);
/* Task-side diagnostic tag: 0 other,1 observer,2 snapshot,3 model update,
 *4 transport restart,5 reserved. ISR records only bounded counters. */
void sync_live_output_phase(unsigned phase);

/* Production comparator source: HIGH sync on GPIO2, DAC GPIO23 clamped LOW. */
bool sync_live_output_flag_begin(const slg_model_t *model,slg_standard_t fallback,
    uint32_t generation,slo_live_fn live,slo_source_fn source,void *arg);
/* Only independently confirmed models may take over retained phase/standard.
 * Task publishes a bounded update; ISR consumes between complete tips. */
bool sync_live_output_update(const slg_model_t *model);
/* Fresh vertical evidence admits native V tips; a retained clock alone does
 * not. H admission additionally comes from the caller's source predicate. */
void sync_live_output_native_fresh(bool vertical);
void sync_live_output_native_polarity(bool inverted);
/* Task context, every native comparator pulse on the common clock (`high`
 * is the comparator level). A present camera that runs off the held grid
 * gets the OE back and the grid re-anchors to it; see sync_phase_guard.h. */
void sync_live_output_native_pulse(uint64_t tick,uint32_t width,bool high);
/* Control tick: ends a yield once the camera has been gone for 100 ms. */
void sync_live_output_guard_tick(void);

/* C5VRX by Twotoz/contributors: conditional tips on the native timebase.
 * Caller serializes task APIs and cancels before changing transport/pad owner.
 * No task/PHY lease is owned for the lifetime. Analogue/monitor proof is separate. */
#pragma once
#include <stdbool.h>
#include <stdint.h>
#include "sync_live_gate.h"

typedef bool (*slo_live_fn)(uint32_t generation);
typedef bool (*slo_native_fn)(uint64_t start,uint32_t width,slg_kind_t kind,void *arg);
typedef struct {
    uint32_t repaired,suppressed,batches,max_late_ticks,queue_low,fault,missed,miss_kind;
    bool active;
} slo_status_t;
/* Explicit selected-standard fallback if model is NULL/unqualified. Native
 * callback must be cache-safe/IRAM; NULL admits no native suppression evidence. */
bool sync_live_output_begin(const slg_model_t *model,slg_standard_t fallback,
    uint32_t generation,slo_live_fn live,slo_native_fn native,void *arg);
bool sync_live_output_poll(void);
bool sync_live_output_end(void);
void sync_live_output_status(slo_status_t *out);

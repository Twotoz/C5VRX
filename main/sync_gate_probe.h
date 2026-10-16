/* C5VRX by Twotoz and contributors: manual, bounded DAC output-enable probe.
 * No camera alignment, automatic activation, or monitor-lock claim. */
#pragma once
#include <stdbool.h>
#include <stdint.h>

/* Caller holds exclusive transport/program ownership throughout this call.
 * live must be IRAM-safe and return false if the supplied generation changes.
 * All six DAC pads must already carry PARLIO data0..5, be enabled, and have
 * both weak pulls disabled. The probe refuses any other initial condition. */
void sync_gate_probe(bool pal, uint32_t generation, bool (*live)(uint32_t));

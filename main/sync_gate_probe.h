/* C5VRX by Twotoz and contributors: manual, bounded DAC output-enable probe.
 * No camera alignment, automatic activation, or monitor-lock claim. */
#pragma once
#include <stdbool.h>
#include <stdint.h>

/* Caller holds exclusive transport/program ownership throughout this call.
 * live must be IRAM-safe and return false if the supplied generation changes.
 * All six DAC pads must already carry PARLIO data0..5 and be enabled.
 * Weak pulls are temporarily disabled without changing any other IOMUX bit;
 * the exact saved pad/matrix configuration is restored on every exit. */
void sync_gate_probe(bool pal, uint32_t generation, bool (*live)(uint32_t));

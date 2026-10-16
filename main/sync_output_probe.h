/* C5VRX by Twotoz and contributors: manual native DAC timing observation.
 * Caller holds the PHY actuator lease, verifies LP/output1.0x, and guarantees
 * no GPIO sync gate/menu/restart while live(generation) remains true. The live
 * predicate is called from ISR context and must be IRAM safe. No activation
 * at boot; no camera-lock or analogue waveform claim follows from this probe. */
#pragma once
#include <stdbool.h>
#include <stdint.h>
void sync_output_probe(uint32_t generation, bool (*live)(uint32_t));

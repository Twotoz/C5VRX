/* C5VRX by Twotoz and contributors: manual native DAC timing observation.
 * Caller holds the PHY actuator lease, verifies LP/output1.0x, and guarantees
 * no menu/restart while live(generation) remains true. GPIO2 must be unused;
 * it temporarily mirrors PARLIO DATA4 independently of the six DAC OEs.
 * GPIO8 and all DAC settings remain unchanged by this observer. The live
 * predicate is called from ISR context and must be IRAM safe. No activation
 * at boot; no camera-lock or analogue waveform claim follows from this probe. */
#pragma once
#include <stdbool.h>
#include <stdint.h>
void sync_output_probe(uint32_t generation, bool (*live)(uint32_t));
void sync_output_clock_probe(uint32_t generation, bool (*live)(uint32_t));

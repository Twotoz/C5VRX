/* C5VRX by Twotoz and contributors: optional digital video integration.
 *
 * Purpose (issue #256): an external FPGA can capture the six PARLIO TX
 * data pins with the exported 40 MHz clock, avoiding the resistor-DAC ->
 * analog-video-decoder round trip. This is a tap of composite samples;
 * sync recovery, colour decoding and any multi-receiver fusion belong to
 * the external device. It adds no precision to the current six-bit output.
 * The normal analog receiver does not need this option; it defaults OFF.
 *
 * Lifecycle: keep this only while an integration uses or evaluates it.
 * If it goes unused and no integrations depend on it, it may be removed;
 * it is not a permanent receiver requirement. Remove the Kconfig option,
 * this API/header, TX clock routing, sample-clock guards in transport and
 * sync_output_probe, conditional menu entries in receiver/menu/control,
 * and its dedicated host/CI checks and documentation together. Restore
 * clk_out_gpio_num=-1 and normal flywheel/menu behaviour; retain the
 * existing flywheel observer's generic occupied-pin protection.
 * See https://github.com/Twotoz/C5VRX/issues/256 for the integration request.
 */
#pragma once
#include <stdbool.h>

/* True even while TX is stopped: observers must never borrow the pad. */
bool transport_sample_clock_enabled(void);

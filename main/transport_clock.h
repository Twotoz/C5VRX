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
 * this API/header, TX clock routing/status and its dedicated host/CI
 * checks and documentation together. Restore clk_out_gpio_num=-1.
 * The sync flywheel was removed from main; do not restore it.
 * See https://github.com/Twotoz/C5VRX/issues/256 for the integration request.
 *
 * Extra DAC bits (menuconfig "Output pins"): with the flywheel removed,
 * GPIO2 is either the clock, the lowest bit of an 8-bit DAC or a future
 * button pin; for now it is the 40 MHz clock pin. Extra bits sit below the D4/GPIO23 LSB;
 * P64 emits 6-bit codes, so they are reserved and held low until a wider
 * demod program drives them through PARLIO. GPIO10 needs PR #259 (IQ
 * I-LSB on GPIO27) first.
 */
#pragma once
#include <stdbool.h>

/* True even while TX is stopped: other features must never borrow the pad. */
bool transport_sample_clock_enabled(void);
/* Physical DAC width of this build (6, 7 or 8); live codes stay 6-bit. */
unsigned transport_dac_bits(void);

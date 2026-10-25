/* C5VRX by Twotoz and contributors: issue #200 manual DAC6 scaling.
 * 1x is bit-exact. HDZERO adds a static soft-knee experiment.
 * High gains can clip; output impedance is not calibrated.
 * No phase/token bits may change when scaling DAC output. */
#pragma once
#include <stdint.h>
#include <stdbool.h>
/* HDZero compatibility experiment. Separate keys keep main's user choices
 * intact when testers return to a normal build. Retire these defaults/keys
 * after bench acceptance; do not silently migrate the ordinary settings. */
#define OUTPUT_HDZERO 10u
#define OUTPUT_LEVELS 11
#define OUTPUT_DEFAULT OUTPUT_HDZERO
#define OUTPUT_NVS_KEY "hdz_output"
#define FLYWHEEL_NVS_KEY "hdz_flywheel"
#define FLYWHEEL_DEFAULT false
static inline uint16_t output_entry(uint16_t value, unsigned level, bool *clipped)
{
    if (level == OUTPUT_HDZERO) {
        /* Static DAC-only curve, never a sync detector or pulse generator.
         * AFC's -1 MHz porch corresponds to canonical code 21. Use 3x
         * slope through sync/porch/burst: 15 -> 6, 21 -> 24, 27 -> 42.
         * Above 27 compress highlights continuously to 63 instead of
         * hard-clipping the entire bright part of the picture as 3x does.
         * Colour burst must remain in the linear section; unusually wide
         * deviation or residual CFO can cross the knee and distort colour.
         * Weak/noisy input is NOT evidence for automatic gain changes.
         * This cannot increase physical DAC swing or guarantee 75-ohm levels.
         * Validate H/V, burst and moving bright scenes on the real goggles. */
        int source = value & 63u;
        int code = source <= 27 ? 24 + 3 * (source - 21) :
                   42 + ((source - 27) * 21 + 18) / 36;
        if (clipped) *clipped = code < 0;
        if (code < 0) code = 0;
        return (uint16_t)((value & 0xffc0u) | (unsigned)code);
    }
    /* Keep the existing 1.0..2.5x maps; extend their half-step contrast
     * curve through 5.0x, plus 0.5x half-scale. Saturation is always limited to DAC6 bits. */
    static const int pivot[OUTPUT_LEVELS] = {21, 13, 16, 19, 22, 25, 28, 31, 34, 21};
    static const int scale[OUTPUT_LEVELS] = { 2,  3,  4,  5,  6,  7,  8,  9, 10,  1};
    if (level >= OUTPUT_LEVELS) level = 0;
    int twice = 2 * pivot[level] + scale[level] * ((int)(value & 63u) - 21);
    /* Round to nearest code, including negative values before saturation. */
    int code = twice >= 0 ? (twice + 1) / 2 : twice / 2;
    if (clipped) *clipped = code < 0 || code > 63;
    if (code < 0) code = 0;
    if (code > 63) code = 63;
    return (uint16_t)((value & 0xffc0u) | (unsigned)code);
}

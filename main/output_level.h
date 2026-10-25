/* C5VRX by Twotoz and contributors: issue #200 manual DAC6 scaling.
 * 1x is bit-exact. High gains can clip; output impedance is not calibrated.
 * No phase/token bits may change when scaling DAC output. */
#pragma once
#include <stdint.h>
#include <stdbool.h>
#define OUTPUT_LEVELS 10
static inline uint16_t output_entry(uint16_t value, unsigned level, bool *clipped)
{
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

/* OUTPUT AUTO (default): CVBS levels referenced to the VTX's own sync.
 * The goggle decoder (HDZero: TP2825, no time-base corrector) slices and
 * locks its line PLL on the sync pulse. At 1.0x the fixed P64 transfer
 * (-6..+9 MHz -> codes 0..63, 4.2 codes/MHz) gives a nominal VTX (sync
 * depth 1.914 MHz) only ~8 codes of sync (~0.13 V) and ~0.45 V sync to
 * white under one 75-ohm load; the standard is 0.286 V / 1.0 V.
 *
 * AUTO maps the measured blanking (porch) to code OUTPUT_AUTO_PORCH_OUT and
 * scales so the measured sync depth becomes OUTPUT_AUTO_SYNC_X10 / 10 codes,
 * capped so the measured white peaks stay at or below OUTPUT_AUTO_WHITE_MAX.
 * Resistor DAC into 200 ohm || 75 ohm: ~16.1 mV per code (dac_check.h model,
 * GPIO resistance ignored), so the targets are sync tip ~0.03 V, blanking
 * ~0.29 V (sync depth ~0.26 V) and white ~0.93 V for a standard VTX: ~5
 * codes (~0.08 V) stay free above white for saturated chroma, so colours do
 * not clip. Only DAC6 bits change, as for the manual levels. */
#define OUTPUT_AUTO         10u
#define OUTPUT_CHOICES      11u
#define OUTPUT_AUTO_PORCH_OUT  18
#define OUTPUT_AUTO_SYNC_X10   160
#define OUTPUT_AUTO_WHITE_MAX  58
#define OUTPUT_AUTO_GAIN_MIN_X100  100   /* never below the old fixed 1.0x */
#define OUTPUT_AUTO_GAIN_MAX_X100  300
/* The sync decides lock: white peaks may cut the sync-referenced gain by at
 * most this much; brighter whites than that clip instead. */
#define OUTPUT_AUTO_WHITE_CUT_MAX_PCT 30
/* 1.0x code of the P64 transfer: (f + 6 MHz) * 63 / 15 MHz. */
#define OUTPUT_P64_CODE_X10(khz) ((int)(((long)(khz) + 6000L) * 630L / 15000L))

typedef struct { int gain_x100, porch_x10; } output_auto_t;

/* Nominal VTX (deviation 1.0) with porch at the AFC target (-1.0 MHz,
 * code 21): the boot value before this channel's VTX is measured. */
#define OUTPUT_AUTO_DEFAULT ((output_auto_t){.gain_x100 = 199, .porch_x10 = 210})

static inline uint16_t output_auto_entry(uint16_t value, output_auto_t a, bool *clipped)
{
    /* out = PORCH_OUT + gain * (code - porch), in 1/1000 codes. */
    const long num = (long)OUTPUT_AUTO_PORCH_OUT * 1000L +
                     (long)a.gain_x100 * ((long)(value & 63u) * 10L - (long)a.porch_x10);
    int code = num >= 0 ? (int)((num + 500L) / 1000L) : -(int)((-num + 500L) / 1000L);
    if (clipped) *clipped = code < 0 || code > 63;
    if (code < 0) code = 0;
    if (code > 63) code = 63;
    return (uint16_t)((value & 0xffc0u) | (unsigned)code);
}

/* Target for a measured VTX: sync depth (sync tip to porch), porch and the
 * white extent above porch (0 = not measured), all in kHz of the
 * demodulated frequency. */
static inline output_auto_t output_auto_target(int sync_depth_khz, int porch_khz, int white_ext_khz)
{
    output_auto_t a = OUTPUT_AUTO_DEFAULT;
    if (sync_depth_khz > 0) {
        /* gain = (SYNC_X10 / 10) / (depth * 63 / 15000), x100 */
        long g = (long)OUTPUT_AUTO_SYNC_X10 * 150000L / (63L * sync_depth_khz);
        if (white_ext_khz > 0) {
            long w = (long)(OUTPUT_AUTO_WHITE_MAX - OUTPUT_AUTO_PORCH_OUT) * 1500000L / (63L * white_ext_khz);
            const long floor = g * (100L - OUTPUT_AUTO_WHITE_CUT_MAX_PCT) / 100L;
            if (w < g) g = w < floor ? floor : w;
        }
        if (g < OUTPUT_AUTO_GAIN_MIN_X100) g = OUTPUT_AUTO_GAIN_MIN_X100;
        if (g > OUTPUT_AUTO_GAIN_MAX_X100) g = OUTPUT_AUTO_GAIN_MAX_X100;
        a.gain_x100 = (int)g;
        int p = OUTPUT_P64_CODE_X10(porch_khz);
        /* A porch far off the AFC target is a bad measurement, not a level. */
        a.porch_x10 = p < 120 ? 120 : p > 300 ? 300 : p;
    }
    return a;
}

/* Worth a restart (each one is a video loss the goggle re-locks after):
 * gain >= 8 % off, or blanking >= 1.5 output codes off. */
#define OUTPUT_AUTO_GAIN_HYST_PCT  8
#define OUTPUT_AUTO_PORCH_HYST_X10 15
static inline bool output_auto_differs(output_auto_t now, output_auto_t want)
{
    const int dg = want.gain_x100 - now.gain_x100;
    const long dp = (long)(want.porch_x10 - now.porch_x10) * want.gain_x100 / 100L;
    return (dg < 0 ? -dg : dg) * 100 >= now.gain_x100 * OUTPUT_AUTO_GAIN_HYST_PCT ||
           (dp < 0 ? -dp : dp) >= OUTPUT_AUTO_PORCH_HYST_X10;
}

/* C5VRX-5 by Twotoz and the C5VRX contributors: DAC self-test model
 * (pure, host test test/test_dac_check.c; screen and steps in menu.c,
 * measurement analysis in tools/dac_check.py).
 *
 * The six DAC pins drive their resistors into one node with a 200 ohm load
 * to GND. A low pin pulls its resistor to GND, so the node is a plain
 * conductance divider:
 *
 *     V(code) = VDD * sum(G_k, bit k set) / (sum(G_k, all k) + G_load)
 *
 * Pin driver resistance (tens of ohms) is ignored here; it lowers the two
 * smallest branches (470R, 240R) by a few percent at most.
 *
 * Self-test steps: the on-screen pattern, then one bit at a time, all bits
 * and no bits, each held as a flat level for a multimeter (no sync). */
#pragma once
#include <stdint.h>

#define DAC_CHECK_BITS       6
#define DAC_CHECK_LOAD_OHMS  200u
#define DAC_CHECK_VDD_MV     3300
/* Bit 0 (D4) .. bit 5 (D9), the README / schematic network. */
static const uint16_t dac_check_ohms[DAC_CHECK_BITS] = {8200u, 3900u, 2000u, 1000u, 470u, 240u};
static const char *const dac_check_pins[DAC_CHECK_BITS] = {"D4", "D5", "D6", "D7", "D8", "D9"};

typedef enum {
    DAC_STEP_PATTERN = 0,
    DAC_STEP_BIT0 = 1,                       /* .. DAC_STEP_BIT0 + 5: one bit high */
    DAC_STEP_ALL = DAC_STEP_BIT0 + DAC_CHECK_BITS,
    DAC_STEP_NONE,
    DAC_STEPS,
} dac_step_t;

static inline dac_step_t dac_check_next(dac_step_t s)
{
    return (dac_step_t)(((unsigned)s + 1u) % (unsigned)DAC_STEPS);
}

/* DAC code a meter step holds; the pattern step has none (0). */
static inline uint8_t dac_check_code(dac_step_t s)
{
    if (s >= DAC_STEP_BIT0 && s < DAC_STEP_ALL) return (uint8_t)(1u << (s - DAC_STEP_BIT0));
    return s == DAC_STEP_ALL ? (uint8_t)((1u << DAC_CHECK_BITS) - 1u) : 0u;
}

/* "D6", "ALL", "NONE" or "PATTERN". */
static inline const char *dac_check_step_name(dac_step_t s)
{
    if (s >= DAC_STEP_BIT0 && s < DAC_STEP_ALL) return dac_check_pins[s - DAC_STEP_BIT0];
    return s == DAC_STEP_ALL ? "ALL" : s == DAC_STEP_NONE ? "NONE" : "PATTERN";
}

/* Expected node voltage for a code, in mV, with no display connected. */
static inline int dac_check_expected_mv(uint8_t code, int vdd_mv)
{
    uint64_t on = 0, total = 1000000000ull / DAC_CHECK_LOAD_OHMS;   /* nanosiemens */
    for (unsigned k = 0; k < DAC_CHECK_BITS; ++k) {
        const uint64_t g = 1000000000ull / dac_check_ohms[k];
        total += g;
        if (code & (1u << k)) on += g;
    }
    return (int)(((uint64_t)vdd_mv * on + total / 2u) / total);
}

/* Actual video wiring: 200 ohms to GND in PARALLEL with the display's
 * 75-ohm input, not 200 ohms in series. Nominal uV; GPIO VDD/impedance
 * and resistor tolerances still require a scope measurement. */
/* Constant-folded divider at nominal 3.3 V; avoids integer division in
 * the bounded output observer. Each conductance is floor(1e9 / R) nS. */
static const int32_t dac_loaded_levels_uv[64] = {
    0, 15183, 31923, 47106, 62250, 77433, 94173, 109356,
    124500, 139683, 156423, 171606, 186750, 201933, 218673, 233856,
    264894, 280076, 296817, 312000, 327144, 342327, 359067, 374250,
    389394, 404577, 421317, 436500, 451644, 466827, 483567, 498750,
    518750, 533933, 550673, 565856, 581000, 596183, 612923, 628106,
    643250, 658433, 675173, 690356, 705500, 720683, 737423, 752606,
    783644, 798827, 815567, 830750, 845894, 861077, 877817, 893000,
    908144, 923327, 940067, 955250, 970394, 985577, 1002317, 1017500,
};
static inline int32_t dac_loaded_uv(unsigned code)
{ return dac_loaded_levels_uv[code & 63u]; }

/* Pattern: a ramp over every level from blanking (20) to the top code (63),
 * left to right over `width` samples. A correct DAC shows a smooth ramp; a
 * wrong, open or swapped resistor shows bands or brightness reversals. */
#define DAC_CHECK_RAMP_LOW   20u
#define DAC_CHECK_RAMP_HIGH  63u
static inline uint8_t dac_check_ramp_code(unsigned x, unsigned width)
{
    const unsigned levels = DAC_CHECK_RAMP_HIGH - DAC_CHECK_RAMP_LOW + 1u;
    unsigned step = width ? x * levels / width : 0u;
    if (step >= levels) step = levels - 1u;
    return (uint8_t)(DAC_CHECK_RAMP_LOW + step);
}

/* Pattern: one bar per bit, two halves that differ in that bit alone, so
 * each bar's contrast is the weight of one resistor: it should double from
 * D4 to D9. Both halves stay at or above blanking (no false sync). */
static inline uint8_t dac_check_bar_base(unsigned bit)
{
    return bit + 1u < DAC_CHECK_BITS ? 32u : 20u;   /* bit 5 needs a base without it */
}

static inline uint8_t dac_check_bar_top(unsigned bit)
{
    return (uint8_t)(dac_check_bar_base(bit) | (1u << bit));
}

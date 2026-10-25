/* C5VRX-5 by Twotoz and the C5VRX contributors: OUTPUT AUTO host test
 * (output_level.h, output_auto.h). Nominal DAC model, not a scope
 * measurement or an HDZero acceptance. */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include "output_auto.h"
#include "p64_tables.h"

#define SYNC_KHZ_PER_DEV 1914   /* profile_logic.h DEV_SYNC_KHZ */
#define MV_PER_CODE_X10 161     /* 1.017 V / 63 into 200 || 75 ohm */

static unsigned checks, failures;
static void check(const char *name, bool ok)
{
    ++checks; failures += !ok;
    printf("%s %s\n", ok ? "ok" : "FAIL", name);
}
/* Output code of a demodulated frequency through the P64 1.0x transfer. */
static int out_code(output_auto_t a, int khz)
{
    int in = (OUTPUT_P64_CODE_X10(khz) + 5) / 10;
    if (in < 0) in = 0;
    if (in > 63) in = 63;
    return (int)(output_auto_entry((uint16_t)in, a, NULL) & 63u);
}
/* A VTX: sync tip, porch, white (kHz); the levels AUTO gives it. */
static void vtx(const char *name, int sync, int porch, int white, int min_depth_mv, bool white_fits)
{
    output_auto_t a = output_auto_target(porch - sync, porch, white - porch);
    const int s = out_code(a, sync), p = out_code(a, porch), w = out_code(a, white);
    const int depth_mv = (p - s) * MV_PER_CODE_X10 / 10, span_mv = (w - s) * MV_PER_CODE_X10 / 10;
    printf("   %-34s gain %d.%02dx  sync %2d porch %2d white %2d  sync depth %3d mV  sync->white %4d mV\n", name,
           a.gain_x100 / 100, a.gain_x100 % 100, s, p, w, depth_mv, span_mv);
    char label[128];
    snprintf(label, sizeof(label), "%s: sync tip above 0, sync depth >= %d mV", name, min_depth_mv);
    check(label, s >= 1 && depth_mv >= min_depth_mv);
    snprintf(label, sizeof(label), "%s: blanking at code %d +-1", name, OUTPUT_AUTO_PORCH_OUT);
    check(label, abs(p - OUTPUT_AUTO_PORCH_OUT) <= 1);
    /* Saturated chroma rides up to ~0.1 V above white: it must still fit. */
    if (white_fits) {
        snprintf(label, sizeof(label), "%s: >= 5 codes (~80 mV) of chroma headroom above white", name);
        check(label, 63 - w >= 5);
        snprintf(label, sizeof(label), "%s: white at or below code %d (no white clipping)", name, OUTPUT_AUTO_WHITE_MAX);
        check(label, w <= OUTPUT_AUTO_WHITE_MAX);
    }
}

int main(void)
{
    /* 1.0x for a nominal VTX: the root cause. */
    const output_auto_t one = {.gain_x100 = 100, .porch_x10 = 210};
    const int d1 = (out_code(one, -1000) - out_code(one, -2914)) * MV_PER_CODE_X10 / 10;
    printf("   1.0x, nominal VTX: sync depth %d mV (standard 286 mV)\n", d1);
    check("fixed 1.0x gives a nominal VTX less than half the standard sync depth", d1 < 143);

    vtx("nominal VTX (deviation 1.0)", -2914, -1000, 3786, 240, true);
    vtx("wide VTX (deviation 1.7, A1 board)", -4254, -1000, 7136, 240, true);
    vtx("narrow VTX (deviation 0.69)", -2321, -1000, 2303, 240, true);
    /* Issue #200: compressed sync (1.3 MHz) under a full picture; 2.0x was
     * reported to give HDZero live video. The white cap wins here. */
    vtx("issue #200 VTX (sync 1.3 MHz)", -2800, -1500, 3700, 180, false);
    output_auto_t i200 = output_auto_target(1300, -1500, 5200);
    check("issue #200 VTX: at least the reported working 2.0x, white cut <= 30 %",
          i200.gain_x100 >= 200 && i200.gain_x100 * 100 >= output_auto_target(1300, -1500, 0).gain_x100 * 69);
    check("AUTO never goes below the old fixed 1.0x", output_auto_target(9000, -1000, 3000).gain_x100 >= 100);
    vtx("nominal VTX, AFC 0.4 MHz off", -2514, -600, 4186, 240, true);

    /* Bounds and validity. */
    check("no measurement keeps the nominal default",
          output_auto_target(0, 0, 0).gain_x100 == OUTPUT_AUTO_DEFAULT.gain_x100);
    check("very wide VTX is limited to the minimum gain", output_auto_target(9000, -1000, 0).gain_x100 ==
                                                             OUTPUT_AUTO_GAIN_MIN_X100);
    check("very narrow VTX is limited to the maximum gain", output_auto_target(300, -1000, 0).gain_x100 ==
                                                               OUTPUT_AUTO_GAIN_MAX_X100);
    check("an implausible porch is bounded", output_auto_target(1914, 6000, 0).porch_x10 == 300 &&
                                             output_auto_target(1914, -6000, 0).porch_x10 == 120);
    output_auto_t def = OUTPUT_AUTO_DEFAULT;
    check("default equals a nominal VTX at the AFC porch target",
          output_auto_target(1914, -1000, 0).gain_x100 == def.gain_x100 &&
          abs(output_auto_target(1914, -1000, 0).porch_x10 - def.porch_x10) <= 1);

    /* LUT: only DAC6 bits change, monotone, clipping only at the rails. */
    bool bits = true, mono = true, rails = true;
    const output_auto_t gains[] = {{75, 210}, {120, 210}, {205, 210}, {300, 180}, {196, 189}};
    for (unsigned g = 0; g < sizeof(gains) / sizeof(gains[0]); ++g) {
        int prev = -1;
        for (unsigned c = 0; c < 64; ++c) {
            int o = (int)(output_auto_entry((uint16_t)c, gains[g], NULL) & 63u);
            mono = mono && o >= prev;
            prev = o;
        }
        for (unsigned i = 0; i < P64_TABLE_ENTRIES; ++i) {
            bool clipped;
            uint16_t out = output_auto_entry(p64_table[i], gains[g], &clipped);
            bits = bits && (out & 0xffc0u) == (p64_table[i] & 0xffc0u);
            rails = rails && (!clipped || (out & 63u) == 0 || (out & 63u) == 63);
        }
    }
    check("every P64 entry keeps its state/token bits", bits);
    check("transfer is monotone for every gain", mono);
    check("clipped entries sit on a rail", rails);

    /* Restart hysteresis: each restart is a goggle re-lock. */
    check("2 % gain change does not restart", !output_auto_differs((output_auto_t){205, 210}, (output_auto_t){209, 210}));
    check("10 % gain change restarts", output_auto_differs((output_auto_t){205, 210}, (output_auto_t){226, 210}));
    check("0.5-code blanking change does not restart", !output_auto_differs((output_auto_t){205, 210}, (output_auto_t){205, 212}));
    check("2-code blanking change restarts", output_auto_differs((output_auto_t){205, 210}, (output_auto_t){205, 220}));

    /* Evidence collection. */
    output_auto_obs_t o;
    output_auto_obs_reset(&o);
    output_auto_t a;
    for (unsigned k = 0; k < 20; ++k) output_auto_obs_push(&o, -1000, 4786);
    check("too few windows decide nothing", !output_auto_obs_decide(&o, 100, SYNC_KHZ_PER_DEV, &a));
    for (unsigned k = 0; k < OUTPUT_AUTO_WINDOWS; ++k) output_auto_obs_push(&o, -1000 + (int)(k % 3) * 10, k < 90 ? 2000 : 9000);
    check("decided from the cycle's windows", output_auto_obs_decide(&o, 100, SYNC_KHZ_PER_DEV, &a));
    check("a few white spikes (90th percentile) do not cap a dark scene", a.gain_x100 == 199);
    output_auto_obs_cycle(&o);
    for (unsigned k = 0; k < OUTPUT_AUTO_WINDOWS; ++k) output_auto_obs_push(&o, -1000, 6000);
    (void)output_auto_obs_decide(&o, 100, SYNC_KHZ_PER_DEV, &a);
    const int bright = a.gain_x100;
    check("a bright scene caps the gain", bright < 199);
    output_auto_obs_cycle(&o);
    for (unsigned k = 0; k < OUTPUT_AUTO_WINDOWS; ++k) output_auto_obs_push(&o, -1000, 1000);
    (void)output_auto_obs_decide(&o, 100, SYNC_KHZ_PER_DEV, &a);
    check("the cap only tightens within a channel session (no scene-driven restarts)", a.gain_x100 == bright);
    output_auto_obs_reset(&o);
    for (unsigned k = 0; k < OUTPUT_AUTO_WINDOWS; ++k) output_auto_obs_push(&o, -1000, 1000);
    (void)output_auto_obs_decide(&o, 100, SYNC_KHZ_PER_DEV, &a);
    check("a new channel session starts without the old cap", a.gain_x100 == 199);
    check("prior without a stored deviation is the default", output_auto_prior(0, SYNC_KHZ_PER_DEV).gain_x100 == 199);
    check("prior from a stored wide deviation", output_auto_prior(170, SYNC_KHZ_PER_DEV).gain_x100 == 117);

    printf("%s: %u checks, %u failures\n", failures ? "FAIL" : "PASS", checks, failures);
    return failures != 0;
}

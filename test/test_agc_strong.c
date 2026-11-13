/* C5VRX-5 by Twotoz and the C5VRX contributors: LEVEL and the core near the
 * bottom of the gain table (an external LNA close in, 2026-11-13).
 *
 * A Discord report (an RX5818-style LNA in front of the XIAO ESP32-C5): far
 * more range, a worse picture close in. Without an LNA the board already
 * parked at G21/G24 close in (FINDINGS, session 55); an LNA of 15-20 dB
 * moves that into G2-G9, where two limits of the firmware sat:
 *  - LEVEL's input-level histogram ended at +80 dB cells^2 at the top row,
 *    about G9 for a carrier on its best radius; a stronger carrier was read
 *    as +79 and held a row that folded (this test, LEVEL part);
 *  - dg_reset clamped the core to G20 or higher, so with the hardware under
 *    G20 the receiver saw a core/hardware mismatch and rebound the whole
 *    gain core on every window (receiver.c, core part).
 * Host model only: an ideal carrier, the gain model of the vendor table; no
 * LNA, compression or range claim. */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include "agc.h"
#include "gain.h"
#include "strong_lane.h"

unsigned char phy_param[1024];
static agc_level_t lv;
static direct_gain_t dg;

#define CHECK(c, ...) do { if (!(c)) { printf("FAIL " __VA_ARGS__); printf("\n"); exit(1); } } while (0)

int main(void)
{
    arc_gain_table_t t;
    arc_gain_table_from_bytes(&t, NULL, ARC_5G_GAIN_MAX);
    /* Core part: a reset keeps a low hardware gain. */
    for (unsigned g = 2; g <= 20; ++g) {
        dg_reset(&dg, &t, (uint8_t)g);
        CHECK(dg.current_gain == g, "core reset at G%u gave G%u", g, dg.current_gain);
    }
    /* LEVEL part: the carrier power referred to the top row (dB cells^2),
     * from 10 dB (the range edge) through ~71 dB (the bare board close in,
     * ~5 cells at G21) to 92 dB (an LNA of ~20 dB close in). */
    const float noise_top = 4.8f;   /* cells^2, the AGC default order */
    agc_level_reset(&lv, dg.model_db_q8, t.max_index, (uint16_t)(noise_top * 16.0f));
    unsigned worst_folds = 0;
    for (int level = 10; level <= 92; level += 6) {
        agc_level_reset(&lv, dg.model_db_q8, t.max_index, (uint16_t)(noise_top * 16.0f));
        uint8_t row = (uint8_t)t.max_index;
        unsigned folds_late = 0;
        for (unsigned w = 0; w < 4000; ++w) {
            const float rel = lv.rel_q8[row] / 256.0f;
            const float c = powf(10.0f, (level + rel) / 10.0f), n = noise_top * powf(10.0f, rel / 10.0f);
            dg_observation_t o = {.observed_us = 1000u + w * 250u};
            if (sqrtf(c + n) > 7.0f) {   /* past the 4-bit window: both rails, a folded (low) power */
                o.rails = 15; o.clip_pm = 300; o.pwr_x16 = (uint16_t)(42.0f * 16.0f);   /* ~uniform over the 16 codes per axis */
                if (w >= 2000) ++folds_late;
            } else {
                const float p = (c + n) * 16.0f;
                o.pwr_x16 = (uint16_t)(p > 65535.0f ? 65535.0f : p);
            }
            row = agc_level_tick(&lv, &o, row, o.observed_us);
            if (row < 2u) row = 2u;   /* receiver.c apply_target */
        }
        const float rel = lv.rel_q8[row] / 256.0f, rms = sqrtf(powf(10.0f, (level + rel) / 10.0f));
        printf("level %2d dB: G%02u carrier %.1f cells rms, folded windows in the last half %u\n", level, row, rms, folds_late);
        if (folds_late > worst_folds) worst_folds = folds_late;
        CHECK(folds_late == 0 && rms <= 7.0f, "level %d dB held G%u folding", level, row);
    }
    /* Over the window on the floor row: LEVEL asks for its lowest home (G0,
     * under the receiver floor G2). That is no room left, and STRONG LANE
     * steps (board log 2026-11-13: it did not, the raw target read as room). */
    {
        agc_level_reset(&lv, dg.model_db_q8, t.max_index, (uint16_t)(noise_top * 16.0f));
        strong_lane_t sl = {0};
        uint8_t row = 2;
        bool below_floor = false;
        for (unsigned w = 0; w < 400 && !sl.steps; ++w) {
            dg_observation_t o = {.observed_us = 1000u + w * 250u, .rails = 15, .clip_pm = 400, .pwr_x16 = (uint16_t)(42.0f * 16.0f)};
            const uint8_t target = agc_level_tick(&lv, &o, row, o.observed_us);
            below_floor |= target < STRONG_LANE_GAIN_FLOOR;
            if (strong_lane_tick(&sl, strong_lane_no_room(row, target), true, true, o.pwr_x16, o.observed_us) > 0)
                strong_lane_applied(&sl, 1, o.observed_us);
            row = target < 2u ? 2u : target;
        }
        CHECK(row == 2 && sl.steps == 1, "folding on G2: row G%u, lane steps %u", row, sl.steps);
        printf("folding on the floor row: LEVEL target under G2 %s, lane step taken\n", below_floor ? "seen" : "not seen");
    }
    printf("PASS test_agc_strong (worst late folds %u)\n", worst_folds);
    return 0;
}

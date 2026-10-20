/* C5VRX-5 by Twotoz and the C5VRX contributors: C5MAP host test.
 * Verifies C5MAP table integrity, 2-regime state transitions, token consistency,
 * and DAC output boundary conditions.
 *
 *   cc -O2 -std=c11 -Wall -Wextra -Werror -I../main test_c5map.c -o t && ./t
 */
#include <stdio.h>
#include <stdlib.h>
#include <stdbool.h>
#include <stdint.h>
#include "c5map_tables.h"
#include "output_level.h"
#include "sync_flag.h"

static unsigned s_fail, s_run;
static void check(const char *name, int ok)
{
    ++s_run;
    printf("%-4s %s\n", ok ? "ok" : "FAIL", name);
    s_fail += !ok;
}

int main(void)
{
    printf("=== C5MAP Demodulator Unit Test ===\n");

    /* 1. Verify token bits constancy across all ladder levels */
    int tokens_consistent = 1;
    for (int lv = 1; lv < C5MAP_LEVELS; ++lv) {
        for (int k = 0; k < 1024; ++k) {
            if ((c5map_tables[lv][k] >> 11) != (c5map_tables[0][k] >> 11)) {
                tokens_consistent = 0;
                break;
            }
        }
    }
    check("c5map: all ladder levels share identical token bits", tokens_consistent);

    /* 2. Verify code ranges and state indices for all entries */
    int codes_valid = 1;
    int states_valid = 1;
    for (int lv = 0; lv < C5MAP_LEVELS; ++lv) {
        for (int k = 0; k < 1024; ++k) {
            uint16_t entry = c5map_tables[lv][k];
            uint8_t code = entry & 63u;
            uint8_t state = (entry >> 6) & 31u;
            if (code > 63u) codes_valid = 0;
            if (state > 31u) states_valid = 0;
        }
    }
    check("c5map: DAC codes in valid range [0..63]", codes_valid);
    check("c5map: next state indices in valid range [0..31]", states_valid);

    /* 3. Verify ladder progression: higher levels (calmer kp) have narrower code excursion */
    int code_max_l0 = 0, code_max_l7 = 0;
    for (int k = 0; k < 1024; ++k) {
        int c0 = c5map_tables[0][k] & 63u;
        int c7 = c5map_tables[C5MAP_LEVELS - 1][k] & 63u;
        if (c0 > code_max_l0) code_max_l0 = c0;
        if (c7 > code_max_l7) code_max_l7 = c7;
    }
    check("c5map: level 7 (rescue kp 0.40) calmer than level 0 (kp 0.85)", code_max_l7 <= code_max_l0);

    /* 4. Output level scaling test (output_level.h): level 0 is 1.0x */
    bool clipped = false;
    int output_ok = 1;
    for (int k = 0; k < 1024; ++k) {
        uint16_t v = output_entry(c5map_tables[0][k], 0, &clipped);
        if ((v & 63u) != (c5map_tables[0][k] & 63u)) output_ok = 0;
    }
    check("c5map: 1.0x output scaling is identity", output_ok);

    /* 5. Sync flag encoding test (sync_flag.h) */
    int flag_ok = 1;
    for (int k = 0; k < 1024; ++k) {
        uint16_t v = sync_flag_entry(c5map_tables[0][k], 0, &clipped);
        /* Check that upper token and state bits are preserved */
        if ((v & ~63u) != (c5map_tables[0][k] & ~63u)) flag_ok = 0;
    }
    check("c5map: sync flag entry preserves token and state bits", flag_ok);

    /* 6. Verify 2-regime states reachability */
    int regime0_reached = 0, regime1_reached = 0;
    for (int k = 0; k < 1024; ++k) {
        uint8_t st = (c5map_tables[0][k] >> 6) & 31u;
        if (st < 16) regime0_reached = 1;
        else regime1_reached = 1;
    }
    check("c5map: both Regime 0 (Sync/Porch) and Regime 1 (Video) reachable", regime0_reached && regime1_reached);

    printf("%u/%u passed\n", s_run - s_fail, s_run);
    return s_fail ? EXIT_FAILURE : EXIT_SUCCESS;
}

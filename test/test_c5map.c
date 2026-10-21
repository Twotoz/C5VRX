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

    static const uint8_t expected_levels[] = {85, 80, 75, 70, 65, 60, 55, 45};
    check("c5map: ladder metadata matches generated levels",
          C5MAP_LEVELS == (int)(sizeof(expected_levels) / sizeof(expected_levels[0])));
    int levels_ok = C5MAP_LEVELS == (int)(sizeof(expected_levels) / sizeof(expected_levels[0]));
    for (int lv = 0; levels_ok && lv < C5MAP_LEVELS; ++lv)
        levels_ok = c5map_level_x100[lv] == expected_levels[lv];
    check("c5map: ladder runs from kp 0.85 to rescue kp 0.45", levels_ok);

    /* Token classification is independent of the transition/output ladder. */
    int tokens_consistent = 1, b_signs_ignored = 1;
    bool token_seen[32] = {false};
    for (int lv = 1; lv < C5MAP_LEVELS; ++lv) {
        for (int k = 0; k < 1024; ++k) {
            if ((c5map_tables[lv][k] >> 11) != (c5map_tables[0][k] >> 11)) {
                tokens_consistent = 0;
                break;
            }
        }
    }
    check("c5map: all ladder levels share identical token bits", tokens_consistent);
    for (int k = 0; k < 1024; ++k) token_seen[c5map_tables[0][k] >> 11] = true;
    int token_set_ok = 1;
    for (int token = 0; token < 32; ++token)
        token_set_ok &= token_seen[token] == (token < 28);
    check("c5map: final model emits exactly phase tokens 0..27", token_set_ok);
    for (int a = 0; a < 256; ++a) {
        uint16_t token = c5map_tables[0][a] >> 11;
        for (int signs = 1; signs < 4; ++signs)
            b_signs_ignored &= (c5map_tables[0][a + signs * 256] >> 11) == token;
    }
    check("c5map: dormant Sample-B sign bits do not change a token", b_signs_ignored);

    /* Calmer ladder levels must narrow the FM step/code excursion. */
    int excursion_ok = 1, previous_max = 64;
    for (int lv = 0; lv < C5MAP_LEVELS; ++lv) {
        int code_max = 0;
        for (int k = 0; k < 1024; ++k) {
            int code = c5map_tables[lv][k] & 63u;
            if (code > code_max) code_max = code;
        }
        excursion_ok &= code_max < previous_max;
        previous_max = code_max;
    }
    check("c5map: each calmer level narrows the code excursion", excursion_ok);

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

    /* Follow every emitted token from state 0. This proves both regimes and
     * all 32 recurrent states are reachable in the actual final model. */
    bool state_seen[32] = {true};
    unsigned queue[32] = {0}, head = 0, tail = 1;
    while (head < tail) {
        unsigned state = queue[head++];
        for (unsigned token = 0; token < 28; ++token) {
            unsigned next = (c5map_tables[0][state * 32 + token] >> 6) & 31u;
            if (!state_seen[next]) {
                state_seen[next] = true;
                queue[tail++] = next;
            }
        }
    }
    check("c5map: all 32 states in both regimes are reachable", tail == 32);

    printf("%u/%u passed\n", s_run - s_fail, s_run);
    return s_fail ? EXIT_FAILURE : EXIT_SUCCESS;
}

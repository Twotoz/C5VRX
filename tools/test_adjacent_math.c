#include <assert.h>
#include <stdio.h>
#include "adjacent_math.h"

int main(void)
{
    /* User-visible failure case: roughly +140 deg + +140 deg. On a phase7
     * circle that is about +50 + +50. Endpoint-only sees +100 wrapped to -28,
     * while exact adjacent retains the +100 winding before the real /2. */
    int pair = adjacent_pair_sum7(0u, 50u, 100u);
    int endpoint = adjacent_wrap_delta7(100);
    assert(pair == 100);
    assert(endpoint == -28);
    assert(adjacent_pair_proves_winding(pair));
    assert(adjacent_pair_qsum7(pair) == 50);
    assert(adjacent_map_qsum_to_cvbs(adjacent_pair_qsum7(pair)) == 63u);

    /* Ordinary modulation remains ordinary: no winding flag and no hold
     * implication just because motion is nonzero. */
    pair = adjacent_pair_sum7(5u, 15u, 27u);
    assert(pair == 22);
    assert(!adjacent_pair_proves_winding(pair));
    assert(adjacent_pair_qsum7(pair) == 11);

    /* Crossing the phase-circle boundary inside either adjacent interval is
     * handled before summation, not after it. */
    pair = adjacent_pair_sum7(120u, 12u, 28u);
    assert(pair == 36);
    assert(!adjacent_pair_proves_winding(pair));

    /* Negative winding is retained symmetrically. */
    pair = adjacent_pair_sum7(0u, 78u, 28u); /* -50 + -50 */
    assert(pair == -100);
    assert(adjacent_pair_proves_winding(pair));
    assert(adjacent_pair_qsum7(pair) == -50);
    assert(adjacent_map_qsum_to_cvbs(adjacent_pair_qsum7(pair)) == 0u);

    puts("Adjacent no-rewrap math tests passed");
    return 0;
}

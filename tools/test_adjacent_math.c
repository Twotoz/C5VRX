#include <assert.h>
#include <stdio.h>
#include "adjacent_math.h"

int main(void)
{
    /* Representative +140deg + +140deg case at Phase5 resolution:
     * +12 + +12 states = +24 states (~270deg). Endpoint-only wraps +24 to
     * -8 (~-90deg), while exact adjacent keeps +24 for the 2:1 map. */
    int pair = adjacent_pair_sum5(0u, 12u, 24u);
    int endpoint = adjacent_wrap_delta5(24);
    assert(pair == 24);
    assert(endpoint == -8);
    assert(adjacent_pair_proves_winding(pair));
    assert(adjacent_map_pair_to_cvbs(pair) == 63u);

    /* Ordinary large motion is not itself a repair condition. */
    pair = adjacent_pair_sum5(2u, 6u, 11u);
    assert(pair == 9);
    assert(!adjacent_pair_proves_winding(pair));

    /* Crossing the circular boundary inside an adjacent interval is handled
     * before the pair sum. */
    pair = adjacent_pair_sum5(30u, 3u, 7u);
    assert(pair == 9);
    assert(!adjacent_pair_proves_winding(pair));

    /* Negative winding is preserved symmetrically. */
    pair = adjacent_pair_sum5(0u, 20u, 8u); /* -12 + -12 */
    assert(pair == -24);
    assert(adjacent_pair_proves_winding(pair));
    assert(adjacent_map_pair_to_cvbs(pair) == 0u);

    puts("Adjacent no-rewrap Phase5 math tests passed");
    return 0;
}

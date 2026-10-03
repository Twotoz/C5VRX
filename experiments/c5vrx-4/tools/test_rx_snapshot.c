/* C5VRX by Twotoz and contributors: stalled copies must not drive gain. */
#include "rx_snapshot.h"
#include <assert.h>
#include <stdio.h>
int main(void)
{
    /* 16 KiB ring: four 4092-byte nodes plus a 16-byte tail. With node
     * zero active and tail sampled, only three full nodes are safe. */
    const uint64_t rate = 40000000;
    assert(rx_snapshot_safe(0, 4, 0, 5, 12276, rate, 1000, 1010));
    assert(rx_snapshot_safe(0, 4, 3, 5, 12276, rate, 1000, 1305));
    assert(!rx_snapshot_safe(0, 4, 4, 5, 12276, rate, 1000, 1010));
    assert(!rx_snapshot_safe(0, 4, 0, 5, 12276, rate, 1000, 1410));
    /* For a full sampled node, the intervening short tail reduces the
     * horizon to 205 us: the old 300-us probe limit could accept a lap. */
    assert(rx_snapshot_safe(1, 0, 2, 5, 8200, rate, 1000, 1204));
    assert(!rx_snapshot_safe(1, 0, 2, 5, 8200, rate, 1000, 1205));
    assert(!rx_snapshot_safe(1, 0, 1, 5, 8200, rate, 1000, 1250));
    assert(!rx_snapshot_safe(1, 0, -1, 5, 8200, rate, 1000, 1010));
    assert(!rx_snapshot_safe(1, 0, 2, 5, 8200, rate, 1000, 999));
    assert(!rx_snapshot_safe(1, 0, 1, 2, 0, rate, 1000, 1000));
    puts("PASS RX snapshot: short-tail deadline, overwrite, wrapped pointer, invalid pointer and time rollback");
}

/* C5VRX by Twotoz and contributors: stalled copies must not drive gain. */
#include "rx_snapshot.h"
#include <assert.h>
#include <stdio.h>
int main(void)
{
    /* Deployed geometry: RAW_RING_BYTES is fixed at 32 KiB, eight
     * 4092-byte nodes plus a 32-byte tail (index 8). */
    const uint64_t rate = 40000000;
    const uint32_t len[9] = {4092, 4092, 4092, 4092, 4092, 4092, 4092, 4092, 32};
    int sum = 0;
    for (unsigned i = 0; i < 9; ++i) sum += (int)len[i];
    assert(sum == 32768);

    /* The newest completed node is used unless it is the short tail. */
    assert(rx_snapshot_pick(len, 9, 3, 4092) == 2);
    assert(rx_snapshot_pick(len, 9, 0, 4092) == 7);
    assert(rx_snapshot_pick(len, 9, 0, 32) == 8);
    assert(rx_snapshot_pick(len, 9, -1, 4092) == -1);
    assert(rx_snapshot_pick(len, 2, 0, 32) == -1);

    /* Full node sampled behind the active node: six full nodes and the tail
     * intervene, a 24,584-byte (614 us) horizon. The old 300-us probe limit
     * was therefore not lappable in this build; only the sentinel lacked a
     * deadline. */
    uint64_t gap = rx_snapshot_gap_bytes(len, 9, 1, 0);
    assert(gap == 24584);
    assert(rx_snapshot_safe(1, 0, 1, 9, gap, rate, 1000, 1300));
    assert(rx_snapshot_safe(1, 0, 7, 9, gap, rate, 1000, 1613));
    assert(!rx_snapshot_safe(1, 0, 1, 9, gap, rate, 1000, 1614));
    /* DMA reached or passed the sampled node. */
    assert(!rx_snapshot_safe(1, 0, 0, 9, gap, rate, 1000, 1010));

    /* Tail skipped: it comes after the sample, so it gives no protection. */
    gap = rx_snapshot_gap_bytes(len, 9, 0, 7);
    assert(gap == 6u * 4092u);
    assert(rx_snapshot_safe(0, 7, 6, 9, gap, rate, 1000, 1612));
    assert(!rx_snapshot_safe(0, 7, 0, 9, gap, rate, 1000, 1613));
    assert(!rx_snapshot_safe(0, 7, 7, 9, gap, rate, 1000, 1010));
    assert(!rx_snapshot_safe(0, 7, 8, 9, gap, rate, 1000, 1010));

    /* Invalid pointer, time rollback and degenerate rings. */
    assert(!rx_snapshot_safe(1, 0, -1, 9, 24584, rate, 1000, 1010));
    assert(!rx_snapshot_safe(1, 0, 2, 9, 24584, rate, 1000, 999));
    assert(!rx_snapshot_safe(1, 0, 1, 2, 0, rate, 1000, 1000));
    assert(rx_snapshot_gap_bytes(len, 9, 9, 0) == 0);
    puts("PASS RX snapshot: 32-KiB geometry, tail skip, gap bytes, overwrite, "
         "invalid pointer and time rollback");
}

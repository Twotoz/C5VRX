/* SPDX-License-Identifier: GPL-3.0-only */

#include <assert.h>
#include <stdint.h>
#include <stdio.h>

#include "c5vrx_ring_tracker.h"

int main(void)
{
    c5vrx_ring_tracker_t t;
    assert(c5vrx_ring_tracker_init(&t, 16384u, 512u,
                                   240000000u, 88000000u) == 0);
    assert(c5vrx_ring_tracker_observe(&t, 16000u, 1000u) ==
           C5VRX_RING_TRACK_OK);
    assert(c5vrx_ring_tracker_place_consumer(&t, 8192u, 8u) ==
           C5VRX_RING_TRACK_OK);
    const uint64_t initial_consumer = t.consumer_absolute;
    assert(c5vrx_ring_tracker_observe(&t, 128u, 2500u) ==
           C5VRX_RING_TRACK_OK);
    assert(t.wraps == 1u);
    assert(t.producer_absolute == 16384u + 16000u + 512u);
    assert(c5vrx_ring_tracker_lag(&t) ==
           t.producer_absolute - initial_consumer);
    assert(c5vrx_ring_tracker_consume(&t, 4096u) == C5VRX_RING_TRACK_OK);
    assert(t.consumer_absolute == initial_consumer + 4096u);
    assert(c5vrx_ring_tracker_deadline_cycles(&t) > 0u);

    /* A modulo delta is forbidden when a complete ring could fit between
     * observations: the pointer could have wrapped any number of times. */
    assert(c5vrx_ring_tracker_observe(&t, 256u, 2500u + 50000u) ==
           C5VRX_RING_TRACK_INTERVAL_AMBIGUOUS);
    assert(t.ambiguous_intervals == 1u);

    c5vrx_ring_tracker_t bad_pointer;
    assert(c5vrx_ring_tracker_init(&bad_pointer, 16384u, 512u,
                                   240000000u, 88000000u) == 0);
    assert(c5vrx_ring_tracker_observe(&bad_pointer, 16384u, 0u) ==
           C5VRX_RING_TRACK_POINTER_OUT_OF_RANGE);

    c5vrx_ring_tracker_t guard;
    assert(c5vrx_ring_tracker_init(&guard, 16384u, 512u,
                                   240000000u, 80000000u) == 0);
    assert(c5vrx_ring_tracker_observe(&guard, 0u, 0u) == C5VRX_RING_TRACK_OK);
    assert(c5vrx_ring_tracker_place_consumer(&guard, 15872u, 8u) ==
           C5VRX_RING_TRACK_GUARD_VIOLATION);

    puts("ring_tracker_test: PASS");
    return 0;
}

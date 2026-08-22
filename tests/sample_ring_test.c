/* SPDX-License-Identifier: GPL-3.0-only */

#include <assert.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "c5vrx_sample_ring.h"

int main(void)
{
    uint8_t storage[64];
    c5vrx_sample_ring_t ring;

    assert(!c5vrx_sample_ring_init(NULL, storage, sizeof(storage)));
    assert(!c5vrx_sample_ring_init(&ring, NULL, sizeof(storage)));
    assert(!c5vrx_sample_ring_init(&ring, storage, 63u)); /* Not power of two. */
    assert(c5vrx_sample_ring_init(&ring, storage, sizeof(storage)));
    assert(c5vrx_sample_ring_free(&ring) == 64u);
    assert(c5vrx_sample_ring_read(&ring, storage, sizeof(storage)) == 0u);

    /* Basic ordering and occupancy accounting. */
    uint8_t out[80];
    memset(out, 0xEE, sizeof(out));
    assert(c5vrx_sample_ring_write(&ring, (const uint8_t *)"abcd", 4u) == 4u);
    assert(c5vrx_sample_ring_used(&ring) == 4u);
    assert(c5vrx_sample_ring_read(&ring, out, sizeof(out)) == 4u);
    assert(!memcmp(out, "abcd", 4u));
    assert(c5vrx_sample_ring_used(&ring) == 0u);
    assert(ring.dropped_bytes == 0u);

    /* Wrap-around preserves order across the physical end of storage. */
    assert(c5vrx_sample_ring_write(&ring, (const uint8_t *)"0123456789", 10u) == 10u);
    assert(c5vrx_sample_ring_read(&ring, out, 6u) == 6u);
    assert(!memcmp(out, "012345", 6u));
    assert(c5vrx_sample_ring_write(&ring, (const uint8_t *)"ABCDEF", 6u) == 6u);
    assert(c5vrx_sample_ring_read(&ring, out, 10u) == 10u);
    assert(!memcmp(out, "6789ABCDEF", 10u));

    /* Drop-newest: an oversized write keeps its fitting prefix, drops the
     * rest, and never corrupts the readable prefix order. */
    assert(c5vrx_sample_ring_free(&ring) == 64u);
    uint8_t big[70];
    for (unsigned i = 0; i < sizeof(big); ++i) big[i] = (uint8_t)i;
    assert(c5vrx_sample_ring_write(&ring, big, sizeof(big)) == 64u);
    assert(ring.dropped_bytes == 6u);
    assert(c5vrx_sample_ring_used(&ring) == 64u);
    assert(ring.peak_bytes == 64u);
    assert(c5vrx_sample_ring_write(&ring, big, sizeof(big)) == 0u);
    assert(ring.dropped_bytes == 76u);
    assert(c5vrx_sample_ring_read(&ring, out, sizeof(out)) == 64u);
    for (unsigned i = 0; i < 64u; ++i) assert(out[i] == (uint8_t)i);
    assert(c5vrx_sample_ring_used(&ring) == 0u);

    /* Read limit below availability leaves the remainder intact. */
    assert(c5vrx_sample_ring_write(&ring, (const uint8_t *)"xyz", 3u) == 3u);
    assert(c5vrx_sample_ring_read(&ring, out, 1u) == 1u);
    assert(out[0] == 'x');
    assert(c5vrx_sample_ring_read(&ring, out, 1u) == 1u);
    assert(out[0] == 'y');
    assert(c5vrx_sample_ring_read(&ring, out, sizeof(out)) == 1u);
    assert(out[0] == 'z');

    /* Long alternating bursts at varying alignment cross the wrap many
     * times while staying deterministic and fully drained each step. */
    assert(c5vrx_sample_ring_init(&ring, storage, sizeof(storage)));
    unsigned seed = 12345u;
    uint8_t burst[24];
    for (unsigned step = 0; step < 200u; ++step) {
        seed = seed * 1103515245u + 12345u;
        const size_t count = 1u + (seed >> 16) % sizeof(burst);
        for (size_t i = 0; i < count; ++i) {
            seed = seed * 1103515245u + 12345u;
            burst[i] = (uint8_t)(seed >> 24);
        }
        assert(c5vrx_sample_ring_write(&ring, burst, count) == count);
        assert(c5vrx_sample_ring_read(&ring, out, sizeof(out)) == count);
        assert(!memcmp(out, burst, count));
    }
    assert(ring.dropped_bytes == 0u);

    puts("sample_ring_test: PASS");
    return 0;
}

/* SPDX-License-Identifier: GPL-3.0-only */

#include "c5vrx_sample_ring.h"

#include <string.h>

bool c5vrx_sample_ring_init(c5vrx_sample_ring_t *ring, void *storage,
                            uint32_t capacity)
{
    if (!ring || !storage || !capacity || capacity > UINT32_C(0x80000000) ||
        (capacity & (capacity - 1u))) {
        return false;
    }
    *ring = (c5vrx_sample_ring_t){
        .storage = (uint8_t *)storage,
        .capacity = capacity,
        .mask = capacity - 1u,
    };
    return true;
}

size_t c5vrx_sample_ring_write(c5vrx_sample_ring_t *ring, const uint8_t *data,
                               size_t count)
{
    if (!ring || !ring->storage || (!data && count)) return 0u;
    const uint32_t used = ring->head - ring->tail;
    const uint32_t free_words = ring->capacity - used;
    const uint32_t accept = (uint64_t)count < free_words ?
        (uint32_t)count : free_words;
    if (accept < count) ring->dropped_bytes += (uint32_t)(count - accept);
    if (!accept) return 0u;

    const uint32_t head = ring->head & ring->mask;
    const uint32_t first = ring->capacity - head;
    const uint32_t lead = accept < first ? accept : first;
    memcpy(ring->storage + head, data, lead);
    if (accept > lead) memcpy(ring->storage, data + lead, accept - lead);
    ring->head += accept;

    const uint32_t used_now = ring->head - ring->tail;
    if (used_now > ring->peak_bytes) ring->peak_bytes = used_now;
    return accept;
}

size_t c5vrx_sample_ring_read(c5vrx_sample_ring_t *ring, uint8_t *out,
                              size_t limit)
{
    if (!ring || !ring->storage || !out || !limit) return 0u;
    uint32_t take = ring->head - ring->tail;
    if (!take) return 0u;
    if ((uint64_t)take > limit) take = (uint32_t)limit;

    const uint32_t tail = ring->tail & ring->mask;
    const uint32_t first = ring->capacity - tail;
    const uint32_t lead = take < first ? take : first;
    memcpy(out, ring->storage + tail, lead);
    if (take > lead) memcpy(out + lead, ring->storage, take - lead);
    ring->tail += take;
    return take;
}

uint32_t c5vrx_sample_ring_used(const c5vrx_sample_ring_t *ring)
{
    return ring ? ring->head - ring->tail : 0u;
}

uint32_t c5vrx_sample_ring_free(const c5vrx_sample_ring_t *ring)
{
    return ring ? ring->capacity - (ring->head - ring->tail) : 0u;
}

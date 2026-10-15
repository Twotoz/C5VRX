/* C5VRX by Twotoz/contributors: RX DONE progress for read-only diagnostics.
 * TRM1.1 p235 describes DONE completion; BF0 is only pre-read metadata.
 * No register, descriptor, raw IQ or LUT writes. Not memory-write permission,
 * not proof of cache coherency/publication lag or an in-flight payload bound. */
#pragma once
#include "dma_progress.h"
#include "raw_access.h"

typedef struct {
    uintptr_t done, bf0;
    uint64_t first_us, last_us;
    raw_access_context_t context;
    uint32_t rx_clock, tx_clock, memory_rate_bound;
    bool live, metadata_owned;
} sync_done_capture_t;

typedef enum {
    SDP_OK, SDP_NOT_LIVE, SDP_LAYOUT, SDP_CONTEXT, SDP_CLOCK, SDP_TIME,
    SDP_UNSTABLE, SDP_DESCRIPTOR, SDP_AMBIGUOUS, SDP_JUMP, SDP_OVERFLOW, SDP_NOT_READY
} sync_done_result_t;

typedef struct {
    raw_access_context_t context;
    uint64_t done_end, fresh_from, last_first_us, last_last_us;
    uint32_t epoch, rx_clock, tx_clock, memory_rate_bound, max_node_bytes;
    unsigned index;
    bool ready, failed;
} sync_done_progress_t;

typedef struct {
    uint64_t done_end, fresh_from;
    uint32_t epoch;
    bool observed, newly_completed;
    /* Deliberately always false. A completed-descriptor coordinate never
     * certifies a CPU write window or the lifetime of a direct raw read. */
    bool memory_write_permission;
} sync_done_observation_t;

static inline sync_done_result_t sync_done_fail(sync_done_progress_t *p, sync_done_result_t why)
{
    p->ready = false;
    p->failed = true;
    return why;
}

/* Explicit reset after the caller discarded its private detector/clock.
 * Preserve epoch numbering so old snapshots cannot alias the new epoch. */
static inline void sync_done_restart(sync_done_progress_t *p)
{
    p->ready = p->failed = false;
}

/* Same-layout contract: caller discovers/validates immutable descriptor
 * geometry once per stream generation, outside the service loop. Context
 * changes are explicit epoch breaks; initialize/re-bootstrap only after
 * discarding private detector state. Never splice disconnected raw windows. */
static inline sync_done_result_t sync_done_observe(sync_done_progress_t *p,
    const dma_progress_layout_t *layout, const sync_done_capture_t *first,
    const sync_done_capture_t *last, sync_done_observation_t *out)
{
    if (!p || !out || !layout || !first || !last) return SDP_LAYOUT;
    *out = (sync_done_observation_t){0};
    if (p->failed) return SDP_NOT_READY;
    if (!first->live || !last->live || !first->metadata_owned || !last->metadata_owned)
        return sync_done_fail(p, SDP_NOT_LIVE);
    if (!dma_progress_layout_valid(layout)) return sync_done_fail(p, SDP_LAYOUT);
    if (!raw_access_same(first->context, last->context)) return sync_done_fail(p, SDP_CONTEXT);
    if (first->rx_clock != last->rx_clock || first->tx_clock != last->tx_clock ||
        first->rx_clock != 0x60005u || first->tx_clock != 0x60005u ||
        !first->memory_rate_bound || first->memory_rate_bound > 192000000u ||
        first->memory_rate_bound != last->memory_rate_bound) return sync_done_fail(p, SDP_CLOCK);
    if (last->last_us < first->first_us || first->last_us < first->first_us ||
        last->first_us < first->first_us || last->last_us < last->first_us)
        return sync_done_fail(p, SDP_TIME);
    /* Crossed DONE boundaries are retryable, but their time still counts
     * toward the last accepted capture's hidden-lap deadline. BF0 may move
     * independently and is intentionally NOT a completion witness. */
    if (first->done != last->done) return SDP_UNSTABLE;
    int index = dma_progress_index(layout, last->done);
    if (index < 0) return sync_done_fail(p, SDP_DESCRIPTOR);
    uint32_t max_node_bytes = 0;
    for (unsigned k = 0; k < DMA_PROGRESS_NODES; ++k)
        if (layout->nodes[k].bytes > max_node_bytes) max_node_bytes = layout->nodes[k].bytes;
    uint64_t capture_motion;
    if (dma_progress_motion(last->last_us - first->first_us, last->memory_rate_bound, 0,
                            &capture_motion) != DPG_OK ||
        capture_motion + max_node_bytes >= DMA_PROGRESS_RING)
        return sync_done_fail(p, SDP_AMBIGUOUS);
    uint32_t end = (layout->nodes[index].offset + layout->nodes[index].bytes) % DMA_PROGRESS_RING;
    bool advance = false;
    if (!p->ready) {
        if (p->epoch == UINT32_MAX) return SDP_OVERFLOW;
        ++p->epoch;
        p->context = last->context;
        p->rx_clock = last->rx_clock; p->tx_clock = last->tx_clock;
        p->memory_rate_bound = last->memory_rate_bound;
        p->max_node_bytes = max_node_bytes;
        p->done_end = p->fresh_from = 2u * DMA_PROGRESS_RING + end;
        p->ready = true;
    } else {
        if (!raw_access_same(p->context, last->context)) return sync_done_fail(p, SDP_CONTEXT);
        if (p->rx_clock != last->rx_clock || p->tx_clock != last->tx_clock ||
            p->memory_rate_bound != last->memory_rate_bound) return sync_done_fail(p, SDP_CLOCK);
        if (first->first_us < p->last_last_us) return sync_done_fail(p, SDP_TIME);
        uint64_t motion;
        if (dma_progress_motion(last->last_us - p->last_first_us, p->memory_rate_bound, 0, &motion) != DPG_OK ||
            motion + p->max_node_bytes >= DMA_PROGRESS_RING) return sync_done_fail(p, SDP_AMBIGUOUS);
        uint32_t prior = (uint32_t)(p->done_end % DMA_PROGRESS_RING);
        uint32_t distance = (end + DMA_PROGRESS_RING - prior) % DMA_PROGRESS_RING;
        /* At the earlier observation the next descriptor can already be
         * partly transferred. Account for that descriptor without calling
         * BF0 a payload pointer or inventing a fixed short-tail length. */
        if (distance > motion + p->max_node_bytes) return sync_done_fail(p, SDP_JUMP);
        if (p->done_end > UINT64_MAX - distance) return sync_done_fail(p, SDP_OVERFLOW);
        p->done_end += distance;
        advance = distance != 0;
    }
    p->index = (unsigned)index;
    p->last_first_us = first->first_us; p->last_last_us = last->last_us;
    *out = (sync_done_observation_t){p->done_end, p->fresh_from, p->epoch, true, advance, false};
    return SDP_OK;
}

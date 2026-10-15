/* C5VRX by Twotoz/contributors: retrospective raw READ lifetime validation.
 * Whole-current+next descriptor operating model; never write permission.
 * Caller instruments EVERY actual source load (including lookbehind), resets
 * source caches at turn entry, and keeps a balanced READ lease through the
 * ending capture. Observations must come from accepted sync_done_observe calls
 * against one immutable layout; use a private candidate progress until success.
 * Any rejection requires discarding the FULL private detector/progress, not
 * retaining partial camera votes. This helper performs no rollback or writes. */
#pragma once
#include "sync_done_progress.h"

typedef struct {
    uint64_t first, last, count; /* half-open envelope, count includes duplicates */
    bool overflow;
} sync_read_extent_t;

typedef enum {
    SRV_OK, SRV_ARGUMENT, SRV_LEASE, SRV_METADATA, SRV_LINK, SRV_CONTEXT,
    SRV_CLOCK, SRV_TIME, SRV_EPOCH, SRV_OVERFLOW, SRV_AMBIGUOUS,
    SRV_EXTENT, SRV_EXPIRED, SRV_INCOMPLETE
} sync_read_result_t;

static inline void sync_read_extent_note(sync_read_extent_t *r, uint64_t k)
{
    if (!r) return;
    if (k==UINT64_MAX || r->count==UINT64_MAX) { r->overflow=true; return; }
    if (!r->count || k<r->first) r->first=k;
    if (!r->count || k+1u>r->last) r->last=k+1u;
    ++r->count;
}

static inline sync_read_result_t sync_read_capture_cover(
    const dma_progress_layout_t *layout, const sync_done_capture_t *f,
    const sync_done_capture_t *l, const sync_done_observation_t *o, uint32_t *cover)
{
    if (!f->live || !l->live || !f->metadata_owned || !l->metadata_owned ||
        f->done!=l->done || f->bf0!=l->bf0 || !o->observed || o->memory_write_permission)
        return SRV_METADATA;
    if (!raw_access_same(f->context,l->context) || !f->context.stream) return SRV_CONTEXT;
    if (f->rx_clock!=0x60005u || f->tx_clock!=0x60005u ||
        l->rx_clock!=f->rx_clock || l->tx_clock!=f->tx_clock ||
        !f->memory_rate_bound || f->memory_rate_bound>192000000u ||
        l->memory_rate_bound!=f->memory_rate_bound) return SRV_CLOCK;
    if (f->last_us<f->first_us || l->first_us<f->last_us || l->last_us<l->first_us)
        return SRV_TIME;
    int done=dma_progress_index(layout,l->done), current=dma_progress_index(layout,l->bf0);
    if (done<0 || current<0 || layout->nodes[done].next!=l->bf0) return SRV_LINK;
    if (!o->epoch || !o->fresh_from || o->fresh_from>o->done_end ||
        o->done_end%DMA_PROGRESS_RING!=layout->nodes[current].offset) return SRV_EPOCH;
    unsigned next=((unsigned)current+1u)%DMA_PROGRESS_NODES;
    *cover=layout->nodes[current].bytes+layout->nodes[next].bytes;
    return SRV_OK;
}

static inline sync_read_result_t sync_read_validate(const sync_read_extent_t *reads,
    const dma_progress_layout_t *layout,
    const sync_done_capture_t *before_first, const sync_done_capture_t *before_last,
    const sync_done_observation_t *before,
    const sync_done_capture_t *after_first, const sync_done_capture_t *after_last,
    const sync_done_observation_t *after, bool balanced_lease_end_valid)
{
    if (!reads || !layout || !before_first || !before_last || !before ||
        !after_first || !after_last || !after) return SRV_ARGUMENT;
    if (!balanced_lease_end_valid) return SRV_LEASE;
    if (!dma_progress_layout_valid(layout)) return SRV_METADATA;
    uint32_t bc,ac;
    sync_read_result_t r=sync_read_capture_cover(layout,before_first,before_last,before,&bc);
    if (r!=SRV_OK) return r;
    r=sync_read_capture_cover(layout,after_first,after_last,after,&ac);
    if (r!=SRV_OK) return r;
    if (!raw_access_same(before_last->context,after_first->context)) return SRV_CONTEXT;
    if (before_last->rx_clock!=after_first->rx_clock ||
        before_last->tx_clock!=after_first->tx_clock ||
        before_last->memory_rate_bound!=after_first->memory_rate_bound) return SRV_CLOCK;
    if (before->epoch!=after->epoch || before->fresh_from!=after->fresh_from ||
        after->done_end<before->done_end) return SRV_EPOCH;
    if (after_first->first_us<before_last->last_us) return SRV_TIME;
    uint64_t dt=after_last->last_us-before_first->first_us;
    /* Entire turn + timer quantization + initial/final descriptor uncertainty.
     * The conservative192MB/s bound excludes an unseen lap; it is NOT deducted
     * prospectively from the usable source window or an80us cadence deadline. */
    if (dt==UINT64_MAX || dt+1u>UINT64_MAX/192u) return SRV_OVERFLOW;
    uint64_t motion=(dt+1u)*192u;
    uint32_t cover=bc>ac?bc:ac;
    if (motion>=DMA_PROGRESS_RING-cover) return SRV_AMBIGUOUS;
    if (after->done_end>UINT64_MAX-ac) return SRV_OVERFLOW;
    uint64_t upper=after->done_end+ac;
    uint64_t oldest=upper>DMA_PROGRESS_RING?upper-DMA_PROGRESS_RING:0;
    if (oldest<after->fresh_from) oldest=after->fresh_from;
    if (reads->overflow) return SRV_OVERFLOW;
    if (reads->count) {
        if (reads->first>=reads->last) return SRV_EXTENT;
        if (reads->first<oldest) return SRV_EXPIRED;
        if (reads->last>before->done_end) return SRV_INCOMPLETE;
    }
    return SRV_OK;
}

/* C5VRX by Twotoz/contributors: retrospective read-only detector turn.
 * Whole-current+successor transfer operating model, not a DMA publication
 * proof or permission to write. Caller supplies actual balanced READ lease,
 * source-load ordering and one exclusive kernel owner. */
#pragma once
#include <string.h>
#include "sync_done_progress.h"
#include "sync_flywheel.h"

typedef enum {
    SRO_OK, SRO_NOT_READY, SRO_LEASE, SRO_METADATA, SRO_LINK,
    SRO_FRESHNESS, SRO_TIME, SRO_AMBIGUOUS, SRO_OVERFLOW,
    SRO_EXPIRED, SRO_INCOMPLETE, SRO_CONTEXT, SRO_READ_OVERFLOW
} sro_result_t;
typedef struct {
    uint64_t first, last, count;
    bool overflow;
} sro_reads_t;
typedef struct {
    sync_flywheel_t checkpoint;
    sync_done_progress_t candidate;
    sync_done_capture_t before;
    sro_reads_t reads;
    uint64_t complete_before, fresh_from, first_us, token;
    uint32_t before_cover, epoch;
    bool active, permit_run, checkpointed, camera_evidence;
    bool memory_write_permission; /* Always false, including SRO_OK. */
} sro_turn_t;

/* Instrument every actual raw source load, including predecessor/lookahead.
 * Cache hits need no new source loads; cache MUST reset at turn entry. */
static inline void sro_read(sro_reads_t *r, uint64_t k)
{
    if (!r) return;
    if (k == UINT64_MAX || r->count == UINT64_MAX) { r->overflow=true; return; }
    if (!r->count || k<r->first) r->first=k;
    if (!r->count || k+1u>r->last) r->last=k+1u;
    ++r->count;
}
static inline sro_result_t sro_cover(const dma_progress_layout_t *layout,
    const sync_done_capture_t *first, const sync_done_capture_t *last, uint32_t *cover)
{
    if (!layout || !first || !last || !cover || !dma_progress_layout_valid(layout)) return SRO_METADATA;
    if (first->done!=last->done || first->bf0!=last->bf0) return SRO_METADATA;
    int done=dma_progress_index(layout,last->done), current=dma_progress_index(layout,last->bf0);
    if (done<0 || current<0 || layout->nodes[done].next!=last->bf0) return SRO_LINK;
    unsigned next=((unsigned)current+1u)%DMA_PROGRESS_NODES;
    *cover=layout->nodes[current].bytes+layout->nodes[next].bytes;
    return SRO_OK;
}
static inline sro_result_t sro_reject(sro_turn_t *t, sync_flywheel_t *f,
    sync_done_progress_t *progress, sro_result_t why)
{
    if (t && t->checkpointed && f) *f=t->checkpoint;
    if (t) { t->active=false;t->camera_evidence=false;t->memory_write_permission=false; }
    /* A rejected turn must circuit-break, never splice an unobserved epoch
     * into a later camera clock. Caller explicitly discards/restarts it. */
    if (progress) { progress->ready=false;progress->failed=true; }
    return why;
}
static inline sro_result_t sro_begin(sro_turn_t *t, sync_flywheel_t *f,
    sync_done_progress_t *progress, const dma_progress_layout_t *layout,
    const sync_done_capture_t *first, const sync_done_capture_t *last,
    const raw_access_token_t *token)
{
    if (!t || !f || !progress || !layout || !first || !last || t->active) return SRO_NOT_READY;
    memset(t,0,sizeof(*t));
    if (!token || !token->ticket || token->slot>=RAW_ACCESS_READERS ||
        !raw_access_same(token->context,first->context)) return sro_reject(t,f,progress,SRO_LEASE);
    uint32_t cover;
    sro_result_t why=sro_cover(layout,first,last,&cover);
    if (why!=SRO_OK) return sro_reject(t,f,progress,why);
    t->candidate=*progress;
    sync_done_observation_t observed;
    if (sync_done_observe(&t->candidate,layout,first,last,&observed)!=SDP_OK)
        return sro_reject(t,f,progress,SRO_METADATA);
    t->before=*last;t->first_us=first->first_us;t->before_cover=cover;
    t->complete_before=observed.done_end;t->fresh_from=observed.fresh_from;
    t->epoch=observed.epoch;t->token=token->ticket;
    /* Every event may drain a pending acquisition cursor. Comparing only
     * newly_completed against the previous END capture would drop nodes
     * that completed during the preceding read turn. */
    t->permit_run=observed.done_end>=observed.fresh_from &&
        observed.done_end-observed.fresh_from>=DMA_PROGRESS_RING;
    if (t->permit_run) { t->checkpoint=*f;t->checkpointed=true; }
    t->active=true;
    return SRO_OK;
}
static inline sro_result_t sro_finish(sro_turn_t *t, sync_flywheel_t *f,
    sync_done_progress_t *progress, const dma_progress_layout_t *layout,
    const sync_done_capture_t *first, const sync_done_capture_t *last,
    bool actual_lease_end_valid, uint32_t service_us)
{
    if (!t || !t->active || !f || !progress || !first || !last) return SRO_NOT_READY;
    if (!actual_lease_end_valid) return sro_reject(t,f,progress,SRO_LEASE);
    if (!raw_access_same(t->before.context,first->context) ||
        !raw_access_same(t->before.context,last->context)) return sro_reject(t,f,progress,SRO_CONTEXT);
    uint32_t cover;
    sro_result_t why=sro_cover(layout,first,last,&cover);
    if (why!=SRO_OK) return sro_reject(t,f,progress,why);
    sync_done_observation_t after;
    if (sync_done_observe(&t->candidate,layout,first,last,&after)!=SDP_OK)
        return sro_reject(t,f,progress,SRO_METADATA);
    if (after.epoch!=t->epoch || after.fresh_from!=t->fresh_from) return sro_reject(t,f,progress,SRO_CONTEXT);
    if (!service_us || last->last_us<t->first_us || last->last_us-t->first_us>=service_us)
        return sro_reject(t,f,progress,SRO_TIME);
    /* AHB ceiling is used only to exclude an unseen full lap. It is NOT
     * subtracted prospectively from every source interval. Include timer
     * quantization and the entire initial current+successor uncertainty. */
    uint64_t dt=last->last_us-t->first_us;
    if (dt==UINT64_MAX || dt+1u>UINT64_MAX/192u) return sro_reject(t,f,progress,SRO_OVERFLOW);
    uint32_t worst_cover=cover>t->before_cover?cover:t->before_cover;
    if ((dt+1u)*192u+worst_cover>=DMA_PROGRESS_RING) return sro_reject(t,f,progress,SRO_AMBIGUOUS);
    if (after.done_end>UINT64_MAX-cover) return sro_reject(t,f,progress,SRO_OVERFLOW);
    uint64_t upper=after.done_end+cover;
    uint64_t oldest=upper>DMA_PROGRESS_RING?upper-DMA_PROGRESS_RING:0;
    if (oldest<t->fresh_from) oldest=t->fresh_from;
    if (t->reads.overflow) return sro_reject(t,f,progress,SRO_READ_OVERFLOW);
    if (t->reads.count) {
        if (!t->permit_run || t->reads.first<oldest) return sro_reject(t,f,progress,SRO_EXPIRED);
        if (t->reads.last>t->complete_before) return sro_reject(t,f,progress,SRO_INCOMPLETE);
        t->camera_evidence=true;
    }
    *progress=t->candidate;
    t->active=false;
    return SRO_OK;
}

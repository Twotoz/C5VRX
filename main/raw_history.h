/* C5VRX by Twotoz/contributors: original-evidence history exclusion.
 * Pure candidate contract, no live caller/writer. Requires independently
 * proved contiguous RX publication and raw reader/writer token ownership. */
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#define RAW_HISTORY_RING 32768u
#define RAW_HISTORY_NODES 9u
#define RAW_HISTORY_CONTROL_MAX 4092u
#define RAW_HISTORY_ENDPOINT_PAIRS_MAX 4092u
#define RAW_HISTORY_CONTIGUOUS_MAX 8192u
#define RAW_HISTORY_CACHE_MARGIN 64u
typedef struct {
    uint32_t node_begin[RAW_HISTORY_NODES], newest_long_distance, protected_bytes;
    bool valid;
} raw_history_layout_t;
typedef struct {
    /* Active descriptor START, not a finer pointer inside its payload. */
    uint64_t fresh_from, rx_descriptor_begin;
    uint32_t generation, epoch;
    bool contiguous_proven, valid, overflow;
} raw_history_snapshot_t;
typedef enum { RHR_CONTROL, RHR_PROBE, RHR_SENTINEL, RHR_SNAPSHOT, RHR_ENDPOINTS } raw_history_reader_t;
/* Requests are API logical lengths: bytes except ENDPOINTS, which is pairs.
 * Probe/sentinel offsets remain fixed by their existing transport code. */
static inline bool raw_history_request(raw_history_reader_t reader, size_t length)
{
    switch (reader) {
    case RHR_CONTROL: return length && length<=RAW_HISTORY_CONTROL_MAX;
    case RHR_PROBE: return length==256u;
    case RHR_SENTINEL: return length==64u;
    case RHR_SNAPSHOT: return length==8190u;
    case RHR_ENDPOINTS: return length && length<=RAW_HISTORY_ENDPOINT_PAIRS_MAX;
    default: return false;
    }
}
static inline raw_history_layout_t raw_history_layout(const uint32_t lengths[RAW_HISTORY_NODES], uint32_t first_offset)
{
    raw_history_layout_t out={0};
    if (!lengths || first_offset>=RAW_HISTORY_RING) return out;
    uint32_t total=0;
    for (unsigned i=0;i<RAW_HISTORY_NODES;++i) {
        if (!lengths[i] || lengths[i]>RAW_HISTORY_CONTROL_MAX) return out;
        out.node_begin[i]=(first_offset+total)%RAW_HISTORY_RING;
        if (lengths[i]>RAW_HISTORY_RING-out.node_begin[i]) return (raw_history_layout_t){0};
        total+=lengths[i];
    }
    if (total!=RAW_HISTORY_RING) return out;
    for (unsigned active=0;active<RAW_HISTORY_NODES;++active) {
        uint32_t distance=0; bool found=false;
        for (unsigned k=1;k<=RAW_HISTORY_NODES;++k) {
            unsigned node=(active+RAW_HISTORY_NODES-k)%RAW_HISTORY_NODES;
            distance+=lengths[node];
            if (lengths[node]>=RAW_HISTORY_CONTROL_MAX) { found=true; break; }
        }
        if (!found) return (raw_history_layout_t){0};
        if (distance>out.newest_long_distance) out.newest_long_distance=distance;
    }
    uint32_t history=out.newest_long_distance>RAW_HISTORY_CONTIGUOUS_MAX ?
        out.newest_long_distance : RAW_HISTORY_CONTIGUOUS_MAX;
    if (history>RAW_HISTORY_RING-RAW_HISTORY_CACHE_MARGIN) return (raw_history_layout_t){0};
    out.protected_bytes=history+RAW_HISTORY_CACHE_MARGIN; out.valid=true; return out;
}
/* All counters are absolute logical bytes in ONE relative startup epoch.
 * A new generation/epoch needs new progress discovery; never splice counters
 * or subtract modulo-ring pointers into an invented absolute frontier. */
static inline bool raw_history_boundary(const raw_history_layout_t *layout,
    const raw_history_snapshot_t *snapshot, uint32_t generation, uint32_t epoch, uint64_t *begin)
{
    if (!layout || !snapshot || !begin || !layout->valid || !snapshot->valid ||
        !snapshot->contiguous_proven || snapshot->overflow || !generation || !epoch ||
        snapshot->generation!=generation || snapshot->epoch!=epoch ||
        snapshot->rx_descriptor_begin<snapshot->fresh_from ||
        snapshot->rx_descriptor_begin-snapshot->fresh_from<layout->protected_bytes) return false;
    bool boundary=false;
    for (unsigned i=0;i<RAW_HISTORY_NODES;++i)
        boundary|=snapshot->rx_descriptor_begin%RAW_HISTORY_RING==layout->node_begin[i];
    if (!boundary) return false;
    *begin=(snapshot->rx_descriptor_begin-layout->protected_bytes)&~(uint64_t)(RAW_HISTORY_CACHE_MARGIN-1u);
    return true;
}
static inline bool raw_history_window(const raw_history_layout_t *layout,
    const raw_history_snapshot_t *snapshot, uint32_t generation, uint32_t epoch,
    uint64_t *first, uint64_t *last)
{
    if (!first || !last || !raw_history_boundary(layout,snapshot,generation,epoch,last)) return false;
    uint64_t oldest=snapshot->rx_descriptor_begin>=RAW_HISTORY_RING ? snapshot->rx_descriptor_begin-RAW_HISTORY_RING : 0;
    if (oldest<snapshot->fresh_from) oldest=snapshot->fresh_from;
    if (oldest>UINT64_MAX-(RAW_HISTORY_CACHE_MARGIN-1u)) return false;
    *first=(oldest+RAW_HISTORY_CACHE_MARGIN-1u)&~(uint64_t)(RAW_HISTORY_CACHE_MARGIN-1u);
    return *first<*last;
}
/* This is only the original-history gate. TX-consumed/fetch frontiers,
 * RX overwrite deadlines, cache-line publication and WCET are separate. */
static inline bool raw_history_patch(const raw_history_layout_t *layout,
    const raw_history_snapshot_t *snapshot, uint32_t generation, uint32_t epoch,
    uint64_t first, uint64_t last)
{
    uint64_t begin,end;
    if (!raw_history_window(layout,snapshot,generation,epoch,&begin,&end) || first>=last ||
        last-first>=RAW_HISTORY_RING || first<begin || last>end || last>UINT64_MAX-63u) return false;
    uint64_t rounded_first=first&~63ull, rounded_last=(last+63u)&~63ull;
    return rounded_first>=begin && rounded_last<=end;
}

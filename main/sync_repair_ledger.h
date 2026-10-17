/* C5VRX by Twotoz and contributors: raw-source provenance for sync research.
 * Absolute sample coordinates; a modified IQ byte is never camera evidence.
 * Host candidate only. One CPU owner records BEFORE writes and advances only
 * with a proved RX overwrite frontier. The hardware consumer must independently
 * enforce RX/TX lifetime, cache and prefetch constraints. */
#pragma once
#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#define SYNC_REPAIR_LEDGER_CAPACITY 64u
typedef struct { uint64_t begin, end; } sync_repair_interval_t;
typedef struct {
    sync_repair_interval_t ranges[SYNC_REPAIR_LEDGER_CAPACITY];
    uint64_t oldest_readable;
    uint32_t collapses;
    unsigned count;
} sync_repair_ledger_t;

static inline void sync_repair_ledger_init(sync_repair_ledger_t *l)
{
    memset(l, 0, sizeof(*l));
}

/* Dropping provenance is safe only after RX has overwritten those absolute
 * samples. A ring address wrapping is not by itself this proof. */
static inline bool sync_repair_ledger_advance(sync_repair_ledger_t *l, uint64_t oldest)
{
    if (oldest < l->oldest_readable) return false;
    l->oldest_readable = oldest;
    unsigned dst = 0;
    for (unsigned i = 0; i < l->count; ++i) {
        if (l->ranges[i].end <= oldest) continue;
        l->ranges[dst] = l->ranges[i];
        if (l->ranges[dst].begin < oldest) l->ranges[dst].begin = oldest;
        ++dst;
    }
    l->count = dst;
    return true;
}

/* Record the entire admitted write, including any synthesized porch. Invalid
 * input returns false: the caller must not perform that write. Merging repeated
 * and adjacent writes bounds normal density. On pathological overflow collapse
 * ALL ranges to one conservative tainted span; genuine evidence may be refused,
 * but no previously synthesized sample becomes "real". */
static inline bool sync_repair_ledger_record(sync_repair_ledger_t *l,
                                             uint64_t begin, uint64_t end)
{
    if (begin >= end || begin < l->oldest_readable) return false;
    unsigned first = 0;
    while (first < l->count && l->ranges[first].end < begin) ++first;
    unsigned last = first;
    while (last < l->count && l->ranges[last].begin <= end) {
        if (l->ranges[last].begin < begin) begin = l->ranges[last].begin;
        if (l->ranges[last].end > end) end = l->ranges[last].end;
        ++last;
    }
    if (first == last && l->count == SYNC_REPAIR_LEDGER_CAPACITY) {
        if (l->ranges[0].begin < begin) begin = l->ranges[0].begin;
        if (l->ranges[l->count - 1u].end > end) end = l->ranges[l->count - 1u].end;
        l->ranges[0] = (sync_repair_interval_t){begin, end};
        l->count = 1;
        ++l->collapses;
        return true;
    }
    unsigned removed = last - first;
    unsigned count = l->count - removed + 1u;
    memmove(&l->ranges[first + 1u], &l->ranges[last],
            (l->count - last) * sizeof(l->ranges[0]));
    l->ranges[first] = (sync_repair_interval_t){begin, end};
    l->count = count;
    return true;
}

/* Query every byte read to derive an edge, width, level, clock or reacquisition
 * judgment, not just its centre. [begin,end) is exclusive at end. The RX-complete
 * ceiling is supplied independently; unsigned underflow/future reads refuse.
 * Sorted disjoint ranges give O(log 64) overlap lookup and a cheap newest-data
 * path. Does not promise that noise is real sync, only unmodified provenance. */
static inline bool sync_repair_ledger_original(const sync_repair_ledger_t *l,
                                               uint64_t begin, uint64_t end,
                                               uint64_t rx_complete)
{
    if (begin >= end || begin < l->oldest_readable || end > rx_complete) return false;
    if (!l->count || begin >= l->ranges[l->count - 1u].end || end <= l->ranges[0].begin)
        return true;
    unsigned lo = 0, hi = l->count;
    while (lo < hi) {
        unsigned mid = lo + (hi - lo) / 2u;
        if (l->ranges[mid].end <= begin) lo = mid + 1u;
        else hi = mid;
    }
    return lo == l->count || l->ranges[lo].begin >= end;
}

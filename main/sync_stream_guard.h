/* C5VRX by Twotoz/contributors: pure whole-patch streaming admission.
 * Hardware must supply proved absolute frontiers and an actual WCET bound.
 * This helper neither derives DMA pointer semantics nor proves those inputs. */
#pragma once
#include <stdbool.h>
#include <stdint.h>

typedef enum {
    SSG_OK = 0, SSG_CONTEXT, SSG_FRONTIERS, SSG_AGE, SSG_OBSERVER,
    SSG_SOURCE, SSG_TX_DEADLINE, SSG_RX_OVERWRITE, SSG_PROTECTED
} sync_stream_guard_result_t;

typedef struct {
    uint64_t rx_complete;       /* start of validated current RX descriptor */
    uint64_t rx_upper;          /* end of that descriptor */
    uint64_t tx_upper;          /* end of validated TX descriptor + proven prefetch */
    uint64_t protected_begin;   /* newest completed long raw observer node */
    uint32_t ring_bytes, rate_hz;
    uint32_t snapshot_age_us, operation_bound_us;
    uint32_t rx_backlog_bytes;  /* proven extra FIFO/in-flight bytes beyond rx_upper */
    bool context_verified, prefetch_proven, raw_reader_active, source_original;
    bool rx_burst_proven;       /* nominal sample rate alone is not a DMA burst bound */
} sync_stream_guard_t;

/* All coordinates are absolute and belong to ONE transport generation.
 * Stores and cache synchronization cover the rounded 64-byte interval.
 * A failed check authorizes no partial write. Caller keeps exclusive raw
 * ownership through stores/cache maintenance and starts within this bound. */
static inline sync_stream_guard_result_t sync_stream_admit(
    const sync_stream_guard_t *g, uint64_t begin, uint64_t end,
    uint64_t evidence_begin, uint64_t evidence_end)
{
    if (!g || !g->context_verified || !g->prefetch_proven || !g->rx_burst_proven) return SSG_CONTEXT;
    if (!g->rate_hz || g->ring_bytes < 128u || (g->ring_bytes & 63u) ||
        g->rx_upper <= g->rx_complete || g->rx_upper - g->rx_complete >= g->ring_bytes ||
        g->rx_complete < g->tx_upper || g->rx_complete - g->tx_upper >= g->ring_bytes ||
        g->protected_begin > g->rx_complete || g->rx_backlog_bytes >= g->ring_bytes) return SSG_FRONTIERS;
    uint64_t us = (uint64_t)g->snapshot_age_us + g->operation_bound_us;
    /* No full modulo lap can remain invisible during an admitted section. */
    if (!g->operation_bound_us || us > UINT64_MAX / g->rate_hz ||
        us * g->rate_hz >= (uint64_t)g->ring_bytes * 1000000u)
        return SSG_AGE;
    uint64_t advance = (us * g->rate_hz + 999999u) / 1000000u;
    if (g->raw_reader_active) return SSG_OBSERVER;
    if (begin >= end || end > UINT64_MAX - 63u ||
        evidence_begin >= evidence_end || evidence_end > g->rx_complete || !g->source_original)
        return SSG_SOURCE;
    uint64_t first = begin & ~UINT64_C(63), last = (end + 63u) & ~UINT64_C(63);
    if (g->tx_upper > UINT64_MAX - advance || first < g->tx_upper + advance)
        return SSG_TX_DEADLINE;
    uint64_t producer_advance = advance + g->rx_backlog_bytes;
    if (g->rx_upper > UINT64_MAX - producer_advance) return SSG_RX_OVERWRITE;
    uint64_t producer = g->rx_upper + producer_advance;
    uint64_t oldest = producer > g->ring_bytes ? producer - g->ring_bytes : 0;
    if (first < oldest || evidence_begin < oldest) return SSG_RX_OVERWRITE;
    if (last > g->protected_begin) return SSG_PROTECTED;
    return SSG_OK;
}

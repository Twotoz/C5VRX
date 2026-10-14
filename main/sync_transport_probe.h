/* C5VRX by Twotoz/contributors: read-only bridge from real DMA captures.
 * Explicitly unproved clock/FIFO limits cannot become valid frontiers.
 * No raw/LUT/descriptor/register write is performed by this helper. */
#pragma once
#include "dma_progress.h"

enum {
    STP_BLOCK_OWNER = 1u, STP_BLOCK_STOPPED = 2u, STP_BLOCK_GEOMETRY = 4u,
    STP_BLOCK_CLOCK = 8u, STP_BLOCK_FIFO = 16u, STP_BLOCK_LIMITS = 32u
};
typedef struct {
    dma_progress_t progress;
    dma_progress_frontiers_t frontiers;
    dma_progress_capture_t capture;
    dma_progress_result_t result;
    uint32_t blocked, calls;
    bool initialized;
} sync_transport_probe_t;

static inline void sync_transport_probe_update(sync_transport_probe_t *p,
    const dma_progress_layout_t *rx, const dma_progress_layout_t *tx,
    const dma_progress_limits_t *limits, const dma_progress_capture_t *capture)
{
    if (!p->initialized) { dma_progress_init(&p->progress); p->initialized = true; }
    ++p->calls;
    p->capture = *capture;
    p->blocked = 0;
    if (!capture->ownership_verified) p->blocked |= STP_BLOCK_OWNER;
    if (!capture->running) p->blocked |= STP_BLOCK_STOPPED;
    bool geometry = limits->geometry_proven && dma_progress_layout_valid(rx) && dma_progress_layout_valid(tx);
    if (!geometry) p->blocked |= STP_BLOCK_GEOMETRY;
    if (!limits->clock_proven) p->blocked |= STP_BLOCK_CLOCK;
    if (!limits->bounds_proven) p->blocked |= STP_BLOCK_FIFO;
    dma_progress_limits_t verified = *limits;
    verified.geometry_proven = geometry;
    p->frontiers.valid = false;
    if (p->progress.ready)
        p->result = dma_progress_update(&p->progress, capture, &p->frontiers);
    else
        p->result = dma_progress_bootstrap(&p->progress, rx, tx, &verified, capture, &p->frontiers);
    /* A previously proved setup cannot outlive the proof inputs of this
     * capture. In particular a lost FIFO/clock proof must not be ignored by
     * dma_progress_update(), whose stored limits belong to the old epoch. */
    const dma_progress_limits_t *previous = &p->progress.limits;
    bool same_limits = previous->min_rate_hz == verified.min_rate_hz &&
        previous->max_rate_hz == verified.max_rate_hz && previous->rx_backlog_bytes == verified.rx_backlog_bytes &&
        previous->tx_prefetch_bytes == verified.tx_prefetch_bytes && previous->geometry_proven == verified.geometry_proven &&
        previous->clock_proven == verified.clock_proven && previous->bounds_proven == verified.bounds_proven;
    if (!p->blocked && !same_limits) p->blocked |= STP_BLOCK_LIMITS;
    if (p->blocked) {
        p->progress.ready = false;
        p->frontiers.valid = false;
        if (p->blocked) p->result = geometry ? DPG_UNPROVEN : DPG_LAYOUT;
    }
}

static inline const char *sync_transport_probe_reason(const sync_transport_probe_t *p)
{
    if (p->blocked & STP_BLOCK_OWNER) return "owner_unavailable";
    if (p->blocked & STP_BLOCK_STOPPED) return "not_live";
    if (p->blocked & STP_BLOCK_GEOMETRY) return "geometry_unverified";
    if (p->blocked & STP_BLOCK_CLOCK) return "clock_unverified";
    if (p->blocked & STP_BLOCK_FIFO) return "fifo_bound_unverified";
    if (p->blocked & STP_BLOCK_LIMITS) return "limits_changed";
    switch (p->result) {
    case DPG_OK: return "captured";
    case DPG_NOT_READY: return "epoch_not_ready";
    case DPG_UNPROVEN: return "proof_missing";
    case DPG_LAYOUT: return "geometry_invalid";
    case DPG_DESCRIPTOR: return "descriptor_unknown";
    case DPG_GENERATION: return "generation_changed";
    case DPG_CLOCK: return "clock_changed";
    case DPG_TIME: return "timestamp_invalid";
    case DPG_AMBIGUOUS: return "lap_ambiguous";
    case DPG_JUMP: return "progress_impossible";
    case DPG_STALLED: return "progress_stalled";
    case DPG_SEPARATION: return "lag_unverified";
    case DPG_OVERFLOW: return "coordinate_overflow";
    }
    return "unknown";
}

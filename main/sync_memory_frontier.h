/* C5VRX by Twotoz/contributors: observed DMA memory frontiers.
 * Conditional coordinates are diagnostics, not permission to modify raw IQ.
 * Register publication/in-flight ordering and a commit WCET still need proof. */
#pragma once
#include "dma_progress.h"

typedef struct {
    uint32_t rx_bf0, rx_done, tx_bf0, tx_done, tx_next, tx_state;
    uint32_t sysclk, ahb_div, bus_update, rx_clock, tx_clock;
} sync_memory_registers_t;

typedef enum {
    SMF_OK, SMF_NOT_LIVE, SMF_GEOMETRY, SMF_UNSTABLE, SMF_CLOCK,
    SMF_DESCRIPTOR, SMF_LINK, SMF_PROGRESS
} sync_memory_result_t;

typedef struct {
    dma_progress_t progress;
    dma_progress_frontiers_t conditional;
    sync_memory_result_t result;
    dma_progress_result_t progress_result;
    uint32_t ahb_hz, memory_bound_hz, inflight_prefix_bytes;
    bool tuple_stable, conditional_valid;
} sync_memory_frontier_t;

/* Pinned clk_tree_ll.h uses source3=PLL240,2=PLL160; the register comment
 * repeats2 by mistake. Require actual AHB<=40MHz, retaining20% headroom
 * below the TRM48MHz ceiling.192MB/s is48MHz*one32-bit AHB beat.
 * AHB_DIV_NUM is actual divider minus1. Pending bus update is unqualified. */
static inline bool sync_memory_clock(const sync_memory_registers_t *r, uint32_t *hz)
{
    uint32_t source = (r->sysclk >> 16) & 3u;
    uint32_t root = source == 3 ? 240000000u : source == 2 ? 160000000u : 0;
    uint32_t divider = (r->ahb_div & 255u) + 1u;
    if (!root || (r->bus_update & 1u) || root % divider) return false;
    *hz = root / divider;
    return *hz <= 40000000u && *hz != 0 &&
        r->rx_clock == 0x00060005u && r->tx_clock == 0x00060005u;
}

static inline bool sync_memory_same(const sync_memory_registers_t *a,
                                    const sync_memory_registers_t *b)
{
    return a->rx_bf0 == b->rx_bf0 && a->rx_done == b->rx_done &&
        a->tx_bf0 == b->tx_bf0 && a->tx_done == b->tx_done &&
        a->tx_next == b->tx_next && a->tx_state == b->tx_state &&
        a->sysclk == b->sysclk && a->ahb_div == b->ahb_div &&
        a->bus_update == b->bus_update && a->rx_clock == b->rx_clock &&
        a->tx_clock == b->tx_clock;
}

static inline sync_memory_result_t sync_memory_observe(sync_memory_frontier_t *p,
    const dma_progress_layout_t *rx, const dma_progress_layout_t *tx,
    const sync_memory_registers_t *first, const sync_memory_registers_t *last,
    const dma_progress_capture_t *capture)
{
    p->conditional_valid = false;
    p->conditional.valid = false;
    p->tuple_stable = sync_memory_same(first, last);
    p->ahb_hz = 0;
    p->memory_bound_hz = 192000000u;
    /* One maximum INCR16 prefix is a candidate reserve, not a proven total
     * outstanding-read/write bound. No writer consumes this diagnostic. */
    p->inflight_prefix_bytes = 64u;
    sync_memory_result_t error = SMF_OK;
    if (!capture->running || !capture->ownership_verified) error = SMF_NOT_LIVE;
    else if (!dma_progress_layout_valid(rx) || !dma_progress_layout_valid(tx)) error = SMF_GEOMETRY;
    else if (!p->tuple_stable) error = SMF_UNSTABLE;
    else if (!sync_memory_clock(last, &p->ahb_hz)) error = SMF_CLOCK;
    int ri = dma_progress_index(rx, last->rx_bf0), rd = dma_progress_index(rx, last->rx_done);
    int ti = dma_progress_index(tx, last->tx_bf0), td = dma_progress_index(tx, last->tx_done);
    int tn = dma_progress_index(tx, last->tx_next);
    if (error == SMF_OK && (ri < 0 || rd < 0 || ti < 0 || td < 0 || tn < 0)) error = SMF_DESCRIPTOR;
    if (error == SMF_OK && (rx->nodes[rd].next != last->rx_bf0 ||
        tx->nodes[td].next != last->tx_bf0 || tx->nodes[ti].next != last->tx_next ||
        (last->tx_state & 0x3ffffu) != (last->tx_next & 0x3ffffu))) error = SMF_LINK;
    if (error != SMF_OK) {
        p->progress.ready = false;
        return p->result = error;
    }
    dma_progress_limits_t limits = {39990000u, p->memory_bound_hz,
        p->inflight_prefix_bytes, 4092u + p->inflight_prefix_bytes, true, true, true};
    dma_progress_capture_t c = *capture;
    c.rx_descriptor = last->rx_bf0; c.tx_descriptor = last->tx_bf0;
    c.min_rate_hz = limits.min_rate_hz; c.max_rate_hz = limits.max_rate_hz;
    bool reset = !p->progress.ready || p->progress.generation != c.generation;
    p->progress_result = reset ? dma_progress_bootstrap(&p->progress, rx, tx, &limits, &c, &p->conditional) :
        dma_progress_update(&p->progress, &c, &p->conditional);
    if (p->progress_result != DPG_OK) return p->result = SMF_PROGRESS;
    p->conditional_valid = true;
    return p->result = SMF_OK;
}

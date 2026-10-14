/* C5VRX by Twotoz/contributors: bounded BF0 descriptor progress epochs.
 * RX/TX may partition the same 32-KiB ring differently. BF0 is the currently fetched descriptor
 * in the pinned ESP32-C5 IDF register definitions. This is a pure adapter
 * helper, not hardware proof of FIFO, clock, cache or atomic capture bounds. */
#pragma once
#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#define DMA_PROGRESS_RING 32768u
#define DMA_PROGRESS_NODES 9u
typedef struct { uintptr_t address, next; uint32_t offset, bytes; } dma_progress_node_t;
typedef struct { dma_progress_node_t nodes[DMA_PROGRESS_NODES]; } dma_progress_layout_t;
typedef struct {
    uint32_t min_rate_hz, max_rate_hz, rx_backlog_bytes, tx_prefetch_bytes;
    bool geometry_proven, clock_proven, bounds_proven;
} dma_progress_limits_t;
typedef struct {
    uintptr_t rx_descriptor, tx_descriptor;
    uint64_t first_read_us, last_read_us;
    uint32_t generation, min_rate_hz, max_rate_hz;
    bool running, ownership_verified;
} dma_progress_capture_t;
typedef enum {
    DPG_OK, DPG_NOT_READY, DPG_UNPROVEN, DPG_LAYOUT, DPG_DESCRIPTOR,
    DPG_GENERATION, DPG_CLOCK, DPG_TIME, DPG_AMBIGUOUS, DPG_JUMP,
    DPG_STALLED, DPG_SEPARATION, DPG_OVERFLOW
} dma_progress_result_t;
typedef struct { uint64_t start, first_us, boundary_first_us; unsigned index; } dma_progress_channel_t;
typedef struct {
    dma_progress_layout_t rx_layout, tx_layout;
    dma_progress_limits_t limits;
    dma_progress_channel_t rx, tx;
    uint64_t fresh_from;
    uint32_t generation, epoch;
    bool ready;
} dma_progress_t;
typedef struct {
    uint64_t rx_lo, rx_hi, tx_lo, tx_hi; /* descriptor bounds before reserves */
    uint64_t tx_prefetch_hi, rx_overwrite_lo, protected_begin;
    uint64_t source_fresh_from, snapshot_first_us, snapshot_last_us;
    uint32_t generation, epoch, rx_backlog_bytes;
    bool valid;
} dma_progress_frontiers_t;

static inline void dma_progress_init(dma_progress_t *p) { memset(p, 0, sizeof(*p)); }

static inline int dma_progress_index(const dma_progress_layout_t *l, uintptr_t address)
{
    for (unsigned i = 0; i < DMA_PROGRESS_NODES; ++i)
        if (l->nodes[i].address == address) return (int)i;
    return -1;
}

/* Discovery can start at any physical node. Require the exact complete cyclic
 * byte layout and address links, not a guessed index*4092 at the short tail. */
static inline bool dma_progress_layout_valid(const dma_progress_layout_t *l)
{
    if (!l) return false;
    unsigned longs = 0;
    uint32_t total = 0;
    for (unsigned i = 0; i < DMA_PROGRESS_NODES; ++i) {
        const dma_progress_node_t *n = &l->nodes[i];
        const dma_progress_node_t *next = &l->nodes[(i + 1u) % DMA_PROGRESS_NODES];
        if (!n->address || (n->address & 3u) || n->next != next->address) return false;
        if (n->offset >= DMA_PROGRESS_RING ||
            n->bytes < 1u || n->bytes > 4092u ||
            n->bytes > DMA_PROGRESS_RING - n->offset) return false;
        if (n->bytes == 4092u) ++longs;
        total += n->bytes;
        if ((n->offset + n->bytes) % DMA_PROGRESS_RING != next->offset) return false;
        for (unsigned j = 0; j < i; ++j)
            if (l->nodes[j].address == n->address || l->nodes[j].offset == n->offset) return false;
    }
    return longs != 0 && total == DMA_PROGRESS_RING;
}

static inline dma_progress_result_t dma_progress_fail(dma_progress_t *p, dma_progress_result_t why)
{
    p->ready = false;
    return why;
}

static inline dma_progress_result_t dma_progress_capture_valid(
    const dma_progress_limits_t *limits, const dma_progress_capture_t *c)
{
    if (!limits->geometry_proven || !limits->clock_proven || !limits->bounds_proven ||
        !c->running || !c->ownership_verified) return DPG_UNPROVEN;
    if (!limits->min_rate_hz || limits->min_rate_hz > limits->max_rate_hz ||
        c->min_rate_hz != limits->min_rate_hz || c->max_rate_hz != limits->max_rate_hz ||
        limits->rx_backlog_bytes >= DMA_PROGRESS_RING || limits->tx_prefetch_bytes >= DMA_PROGRESS_RING)
        return DPG_CLOCK;
    return c->last_read_us >= c->first_read_us ? DPG_OK : DPG_TIME;
}

static inline dma_progress_result_t dma_progress_motion(uint64_t elapsed, uint32_t rate,
                                                        uint32_t reserve, uint64_t *out)
{
    if (elapsed > UINT64_MAX / rate) return DPG_AMBIGUOUS;
    uint64_t ticks = elapsed * rate;
    /* Divide before rounding, so even a malicious timestamp cannot overflow. */
    *out = ticks / 1000000u + (ticks % 1000000u != 0) + reserve;
    return *out >= DMA_PROGRESS_RING ? DPG_AMBIGUOUS : DPG_OK;
}

static inline dma_progress_result_t dma_progress_step(dma_progress_channel_t *channel,
    const dma_progress_layout_t *layout, unsigned index, const dma_progress_capture_t *c,
    const dma_progress_limits_t *limits, uint32_t reserve)
{
    if (c->first_read_us < channel->first_us) return DPG_TIME;
    uint64_t motion;
    dma_progress_result_t result = dma_progress_motion(c->last_read_us - channel->first_us,
                                                       limits->max_rate_hz, reserve, &motion);
    if (result != DPG_OK) return result;
    uint32_t previous_bytes = layout->nodes[channel->index].bytes;
    /* The prior position was anywhere INSIDE its descriptor: a full boundary
     * lap can be hidden in less than ring/rate, e.g. ~717us for a 4092 node. */
    if (motion + previous_bytes >= DMA_PROGRESS_RING) return DPG_AMBIGUOUS;
    uint32_t distance = (layout->nodes[index].offset + DMA_PROGRESS_RING -
                         layout->nodes[channel->index].offset) % DMA_PROGRESS_RING;
    if (distance > motion + previous_bytes - 1u) return DPG_JUMP;
    if (!distance) {
        uint64_t age = c->last_read_us - channel->boundary_first_us;
        uint64_t capacity = (uint64_t)previous_bytes + reserve;
        if (age > UINT64_MAX / limits->min_rate_hz ||
            age * limits->min_rate_hz > capacity * 1000000u) return DPG_STALLED;
    } else {
        if (channel->start > UINT64_MAX - distance) return DPG_OVERFLOW;
        channel->start += distance;
        channel->boundary_first_us = c->first_read_us;
    }
    channel->index = index;
    channel->first_us = c->first_read_us;
    return DPG_OK;
}

static inline dma_progress_result_t dma_progress_export(dma_progress_t *p,
    const dma_progress_capture_t *c, dma_progress_frontiers_t *out)
{
    if (!out) return dma_progress_fail(p, DPG_NOT_READY);
    out->valid = false;
    uint32_t rb = p->rx_layout.nodes[p->rx.index].bytes;
    uint32_t tb = p->tx_layout.nodes[p->tx.index].bytes;
    if (p->rx.start > UINT64_MAX - rb || p->tx.start > UINT64_MAX - tb - p->limits.tx_prefetch_bytes)
        return dma_progress_fail(p, DPG_OVERFLOW);
    uint64_t rhi = p->rx.start + rb, thi = p->tx.start + tb;
    /* Both uncertainty intervals must describe fresh data in ONE ring epoch. */
    if (p->rx.start < thi + p->limits.tx_prefetch_bytes ||
        rhi - p->tx.start >= DMA_PROGRESS_RING) return dma_progress_fail(p, DPG_SEPARATION);
    uint64_t protected_begin = p->rx.start;
    unsigned index = p->rx.index;
    do {
        index = (index + DMA_PROGRESS_NODES - 1u) % DMA_PROGRESS_NODES;
        protected_begin -= p->rx_layout.nodes[index].bytes;
    } while (p->rx_layout.nodes[index].bytes < 4092u);
    uint64_t old = p->rx.start > DMA_PROGRESS_RING ? p->rx.start - DMA_PROGRESS_RING : 0;
    if (old < p->fresh_from) old = p->fresh_from;
    *out = (dma_progress_frontiers_t){p->rx.start, rhi, p->tx.start, thi,
        thi + p->limits.tx_prefetch_bytes, old, protected_begin, p->fresh_from,
        c->first_read_us, c->last_read_us, p->generation, p->epoch, p->limits.rx_backlog_bytes, true};
    return DPG_OK;
}

/* Establish a NEW RELATIVE data epoch, not a claim about how many hardware
 * laps preceded this capture. All pre-bootstrap RX bytes remain unknown.
 * A lease and verified source/consumer separation must surround capture.
 * The caller discards old keeper/ledger/scratch work when epoch changes. */
static inline dma_progress_result_t dma_progress_bootstrap(dma_progress_t *p,
    const dma_progress_layout_t *rx, const dma_progress_layout_t *tx,
    const dma_progress_limits_t *limits, const dma_progress_capture_t *c,
    dma_progress_frontiers_t *out)
{
    p->ready = false;
    if (out) out->valid = false;
    if (!out || !dma_progress_layout_valid(rx) || !dma_progress_layout_valid(tx)) return DPG_LAYOUT;
    dma_progress_result_t result = dma_progress_capture_valid(limits, c);
    if (result != DPG_OK) return result;
    int ri = dma_progress_index(rx, c->rx_descriptor), ti = dma_progress_index(tx, c->tx_descriptor);
    if (ri < 0 || ti < 0) return DPG_DESCRIPTOR;
    uint64_t motion;
    result = dma_progress_motion(c->last_read_us - c->first_read_us, limits->max_rate_hz,
                                 limits->rx_backlog_bytes + limits->tx_prefetch_bytes, &motion);
    if (result != DPG_OK || motion + 4092u >= DMA_PROGRESS_RING) return DPG_AMBIGUOUS;
    if (p->epoch == UINT32_MAX) return DPG_OVERFLOW;
    p->rx_layout = *rx; p->tx_layout = *tx; p->limits = *limits;
    p->generation = c->generation; ++p->epoch;
    uint64_t rlo = 2u * DMA_PROGRESS_RING + rx->nodes[ri].offset;
    uint32_t behind = (rx->nodes[ri].offset + DMA_PROGRESS_RING - tx->nodes[ti].offset) % DMA_PROGRESS_RING;
    /* RX/TX registers are sequential observations. A long initial capture
     * can make TX's later descriptor belong to the NEXT data lap relative to
     * RX's earlier one. Do not choose the previous-lap origin by modulo alone. */
    if ((uint64_t)behind + rx->nodes[ri].bytes + motion >= DMA_PROGRESS_RING)
        return dma_progress_fail(p, DPG_AMBIGUOUS);
    p->rx = (dma_progress_channel_t){rlo, c->first_read_us, c->first_read_us, (unsigned)ri};
    p->tx = (dma_progress_channel_t){rlo - behind, c->first_read_us, c->first_read_us, (unsigned)ti};
    /* The descriptor currently in progress is also excluded at bootstrap:
     * its partial contents predate the capture and are not a fresh snapshot. */
    p->fresh_from = rlo + rx->nodes[ri].bytes;
    p->ready = true;
    return dma_progress_export(p, c, out);
}

/* Failure latches this epoch unusable. A valid later BF0 poll never silently
 * recovers a missed lap; explicit bootstrap starts a different epoch. */
static inline dma_progress_result_t dma_progress_update(dma_progress_t *p,
    const dma_progress_capture_t *c, dma_progress_frontiers_t *out)
{
    if (out) out->valid = false;
    if (!p->ready || !out) return DPG_NOT_READY;
    if (c->generation != p->generation) return dma_progress_fail(p, DPG_GENERATION);
    dma_progress_result_t result = dma_progress_capture_valid(&p->limits, c);
    if (result != DPG_OK) return dma_progress_fail(p, result);
    int ri = dma_progress_index(&p->rx_layout, c->rx_descriptor);
    int ti = dma_progress_index(&p->tx_layout, c->tx_descriptor);
    if (ri < 0 || ti < 0) return dma_progress_fail(p, DPG_DESCRIPTOR);
    dma_progress_channel_t r = p->rx, t = p->tx;
    result = dma_progress_step(&r, &p->rx_layout, (unsigned)ri, c, &p->limits, p->limits.rx_backlog_bytes);
    if (result == DPG_OK)
        result = dma_progress_step(&t, &p->tx_layout, (unsigned)ti, c, &p->limits, p->limits.tx_prefetch_bytes);
    if (result != DPG_OK) return dma_progress_fail(p, result);
    p->rx = r; p->tx = t;
    return dma_progress_export(p, c, out);
}

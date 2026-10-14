/* C5VRX by Twotoz/contributors: absolute DMA progress, independent positions.
 * Synthetic BF0 captures; no physical FIFO/prefetch or scheduler proof. */
#include <assert.h>
#include <stdio.h>
#include "dma_progress.h"

static dma_progress_layout_t layout(unsigned rotation, uintptr_t address)
{
    dma_progress_layout_t l;
    for (unsigned i = 0; i < 9u; ++i) {
        unsigned physical = (i + rotation) % 9u;
        l.nodes[i] = (dma_progress_node_t){address + i * 16u, address + ((i + 1u) % 9u) * 16u,
                                          physical * 4092u, physical == 8u ? 32u : 4092u};
    }
    return l;
}
static uintptr_t descriptor(const dma_progress_layout_t *l, uint64_t position)
{
    unsigned offset = position % DMA_PROGRESS_RING;
    for (unsigned i = 0; i < 9; ++i)
        if (offset >= l->nodes[i].offset && offset < l->nodes[i].offset + l->nodes[i].bytes)
            return l->nodes[i].address;
    assert(0); return 0;
}
static uint64_t physical_boundary(const dma_progress_layout_t *l, uint64_t position)
{
    uint64_t offset = position % DMA_PROGRESS_RING;
    int i = dma_progress_index(l, descriptor(l, position));
    assert(i >= 0);
    uint64_t lower = l->nodes[i].offset;
    return position - offset + lower;
}
static dma_progress_capture_t capture(const dma_progress_layout_t *rx,
                                      const dma_progress_layout_t *tx,
                                      uint64_t rp, uint64_t tp, uint64_t time)
{
    return (dma_progress_capture_t){descriptor(rx, rp), descriptor(tx, tp), time, time + 1u,
                                    5u, 39990000u, 40010000u, true, true};
}
static dma_progress_limits_t limits(void)
{
    /* Values here are fixtures, not claims that these are C5 FIFO capacities. */
    return (dma_progress_limits_t){39990000u, 40010000u, 128u, 512u, true, true, true};
}

static void continuous_oracle(unsigned rx_rotation, unsigned tx_rotation, unsigned lag, bool balanced)
{
    dma_progress_t p; dma_progress_init(&p);
    dma_progress_layout_t rx = layout(rx_rotation, 0x1000), tx = layout(tx_rotation, 0x2000);
    if (balanced) {
        for (unsigned i = 0; i < 9u; ++i) {
            unsigned physical = (i + rx_rotation) % 9u;
            if (physical == 7u) rx.nodes[i].bytes = 2064u;
            if (physical == 8u) { rx.nodes[i].offset = 30708u; rx.nodes[i].bytes = 2060u; }
        }
        assert(dma_progress_layout_valid(&rx));
    }
    dma_progress_limits_t cap = limits();
    uint64_t rp = 65000, tp = rp - lag, time = 1000;
    dma_progress_capture_t c = capture(&rx, &tx, rp, tp, time);
    dma_progress_frontiers_t f;
    assert(dma_progress_bootstrap(&p, &rx, &tx, &cap, &c, &f) == DPG_OK);
    assert(f.valid);
    int64_t shift = (int64_t)f.rx_lo - (int64_t)physical_boundary(&rx, rp);
    assert((int64_t)f.tx_lo - (int64_t)physical_boundary(&tx, tp) == shift);
    assert(f.source_fresh_from == f.rx_hi && f.rx_overwrite_lo == f.rx_hi);
    for (unsigned i = 0; i < 12000u; ++i) {
        unsigned dt = 17u + i % 37u;
        time += dt; rp += dt * 40u; tp += dt * 40u;
        c = capture(&rx, &tx, rp, tp, time);
        assert(dma_progress_update(&p, &c, &f) == DPG_OK);
        assert((int64_t)f.rx_lo == (int64_t)physical_boundary(&rx, rp) + shift);
        assert((int64_t)f.tx_lo == (int64_t)physical_boundary(&tx, tp) + shift);
        assert((uint64_t)((int64_t)rp + shift) >= f.rx_lo && (uint64_t)((int64_t)rp + shift) < f.rx_hi);
        assert((uint64_t)((int64_t)tp + shift) >= f.tx_lo && (uint64_t)((int64_t)tp + shift) < f.tx_hi);
        assert(f.tx_prefetch_hi == f.tx_hi + cap.tx_prefetch_bytes);
        assert(f.protected_begin < f.rx_lo && f.rx_lo - f.protected_begin <= (balanced ? 8216u : 4124u));
        unsigned ri = (unsigned)dma_progress_index(&rx, descriptor(&rx, rp));
        uint64_t protected_distance = 0;
        do {
            ri = (ri + 8u) % 9u;
            protected_distance += rx.nodes[ri].bytes;
        } while (rx.nodes[ri].bytes < 4092u);
        assert(f.rx_lo - f.protected_begin == protected_distance);
        assert(f.snapshot_first_us == time && f.snapshot_last_us == time + 1u);
        assert(f.epoch == 1u && f.generation == 5u);
    }
    printf("continuous lag=%u rotation=%u/%u: 12000 independent position captures across wraps\n",
           lag, rx_rotation, tx_rotation);
}

int main(void)
{
    dma_progress_layout_t rx = layout(0, 0x1000), tx = layout(4, 0x2000);
    dma_progress_limits_t cap = limits();
    dma_progress_capture_t c = capture(&rx, &tx, 21000, 5000, 1000);
    dma_progress_t p; dma_progress_init(&p);
    dma_progress_frontiers_t f;
    assert(dma_progress_layout_valid(&rx) && dma_progress_layout_valid(&tx));
    dma_progress_layout_t all_short = rx;
    for (unsigned i = 0; i < 9; ++i) {
        all_short.nodes[i].offset = i * 3640u;
        all_short.nodes[i].bytes = i == 8 ? 3648u : 3640u;
    }
    assert(!dma_progress_layout_valid(&all_short)); /* no observer-sized node */
    dma_progress_layout_t broken = rx;
    broken.nodes[8].bytes = 4092;
    assert(!dma_progress_layout_valid(&broken));
    broken = rx; broken.nodes[0].next = broken.nodes[2].address;
    assert(!dma_progress_layout_valid(&broken));
    broken = rx; broken.nodes[2].address = broken.nodes[1].address;
    assert(!dma_progress_layout_valid(&broken));
    assert(dma_progress_bootstrap(&p, &rx, &tx, &cap, &c, &f) == DPG_OK);
    assert(p.epoch == 1 && f.rx_overwrite_lo == f.source_fresh_from && f.source_fresh_from == f.rx_hi);
    c.rx_descriptor = 0xBAD;
    assert(dma_progress_update(&p, &c, &f) == DPG_DESCRIPTOR && !p.ready);
    assert(!f.valid); /* cannot reuse previously exported successful frontiers */
    c = capture(&rx, &tx, 21000, 5000, 1000);
    assert(dma_progress_update(&p, &c, &f) == DPG_NOT_READY);
    assert(dma_progress_bootstrap(&p, &rx, &tx, &cap, &c, &f) == DPG_OK && p.epoch == 2);
    ++c.generation;
    assert(dma_progress_update(&p, &c, &f) == DPG_GENERATION && !p.ready);

    c = capture(&rx, &tx, 21000, 5000, 1000);
    assert(dma_progress_bootstrap(&p, &rx, &tx, &cap, &c, &f) == DPG_OK);
    --c.max_rate_hz;
    assert(dma_progress_update(&p, &c, &f) == DPG_CLOCK);
    c = capture(&rx, &tx, 21000, 5000, 1000);
    assert(dma_progress_bootstrap(&p, &rx, &tx, &cap, &c, &f) == DPG_OK);
    c.first_read_us = 999; c.last_read_us = 1000;
    assert(dma_progress_update(&p, &c, &f) == DPG_TIME);

    c = capture(&rx, &tx, 21000, 5000, 1000);
    assert(dma_progress_bootstrap(&p, &rx, &tx, &cap, &c, &f) == DPG_OK);
    c.first_read_us += 717; c.last_read_us += 717;
    assert(dma_progress_update(&p, &c, &f) == DPG_AMBIGUOUS); /* <819us can hide a boundary lap */
    c = capture(&rx, &tx, 21000, 5000, 1000);
    assert(dma_progress_bootstrap(&p, &rx, &tx, &cap, &c, &f) == DPG_OK);
    c.first_read_us += 820; c.last_read_us += 820;
    assert(dma_progress_update(&p, &c, &f) == DPG_AMBIGUOUS);
    c = capture(&rx, &tx, 21000, 5000, 1000);
    assert(dma_progress_bootstrap(&p, &rx, &tx, &cap, &c, &f) == DPG_OK);
    c = capture(&rx, &tx, 30000, 5000, 1001);
    assert(dma_progress_update(&p, &c, &f) == DPG_JUMP);

    c = capture(&rx, &tx, 21000, 5000, 1000);
    assert(dma_progress_bootstrap(&p, &rx, &tx, &cap, &c, &f) == DPG_OK);
    c.first_read_us += 50; c.last_read_us += 50;
    assert(dma_progress_update(&p, &c, &f) == DPG_OK);
    c.first_read_us += 70; c.last_read_us += 70;
    assert(dma_progress_update(&p, &c, &f) == DPG_STALLED);
    cap.bounds_proven = false;
    assert(dma_progress_bootstrap(&p, &rx, &tx, &cap, &c, &f) == DPG_UNPROVEN);
    cap = limits(); c = capture(&rx, &tx, 21000, 20500, 1000);
    assert(dma_progress_bootstrap(&p, &rx, &tx, &cap, &c, &f) == DPG_SEPARATION);
    c = capture(&rx, &tx, 21000, 5000, 1000); c.last_read_us = UINT64_MAX;
    assert(dma_progress_bootstrap(&p, &rx, &tx, &cap, &c, &f) == DPG_AMBIGUOUS);
    c = capture(&rx, &tx, 21000, 5000, 1000); c.last_read_us += 700u;
    assert(dma_progress_bootstrap(&p, &rx, &tx, &cap, &c, &f) == DPG_AMBIGUOUS);
    c = capture(&rx, &tx, 21000, 5000, 1000); p.epoch = UINT32_MAX;
    assert(dma_progress_bootstrap(&p, &rx, &tx, &cap, &c, &f) == DPG_OVERFLOW);
    /* Direct short-tail transition across physical buffer zero: exactly32
     * absolute samples advance, protected long observer node skips the tail. */
    dma_progress_init(&p);
    c = capture(&rx, &tx, 32740, 16000, 1000);
    assert(dma_progress_bootstrap(&p, &rx, &tx, &cap, &c, &f) == DPG_OK);
    uint64_t tail_begin = f.rx_lo;
    assert(f.rx_hi - f.rx_lo == 32u);
    c = capture(&rx, &tx, 32820, 16080, 1002);
    assert(dma_progress_update(&p, &c, &f) == DPG_OK);
    assert(f.rx_lo == tail_begin + 32u && f.rx_hi - f.rx_lo == 4092u);
    assert(f.protected_begin == f.rx_lo - 4124u);
    assert(f.source_fresh_from == tail_begin + 32u);
    /* RX can move legally while TX has an impossible descriptor jump. The
     * partially successful RX unwrap must not be published or committed. */
    dma_progress_init(&p);
    c = capture(&rx, &tx, 21000, 5000, 1000);
    assert(dma_progress_bootstrap(&p, &rx, &tx, &cap, &c, &f) == DPG_OK);
    uint64_t old_rx = p.rx.start;
    c = capture(&rx, &tx, 24560, 17000, 1002);
    assert(dma_progress_update(&p, &c, &f) == DPG_JUMP);
    assert(!p.ready && !f.valid && p.rx.start == old_rx);
    continuous_oracle(0, 0, 16384, false);
    continuous_oracle(0, 0, 16384, true);
    continuous_oracle(7, 3, 16384, false);
    continuous_oracle(7, 3, 16384, true);
    continuous_oracle(8, 5, 24576, false);
    continuous_oracle(8, 5, 24576, true);
    puts("PASS: bootstrap freshness, absolute wrap/tail progress, generation, ambiguous laps, rate, stall and epoch faults");
}

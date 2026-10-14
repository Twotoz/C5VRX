/* C5VRX by Twotoz/contributors: independently positioned read-only memory
 * captures. No physical publication/in-flight/WCET proof is inferred. */
#include <assert.h>
#include <stdio.h>
#include "sync_memory_frontier.h"

static dma_progress_layout_t layout(bool rx)
{
    dma_progress_layout_t l = {0};
    uint32_t offset = 0;
    for (unsigned i = 0; i < 9; ++i) {
        uint32_t bytes = i < 7 ? 4092 : rx ? (i == 7 ? 2062 : 2062) : (i == 7 ? 4092 : 32);
        uintptr_t base = rx ? 0x40853000u : 0x40812000u;
        l.nodes[i] = (dma_progress_node_t){base + i * 12, base + ((i + 1) % 9) * 12, offset, bytes};
        offset += bytes;
    }
    assert(offset == 32768 && dma_progress_layout_valid(&l));
    return l;
}
static unsigned index_at(const dma_progress_layout_t *l, uint64_t position)
{
    uint32_t offset = position % 32768;
    for (unsigned i = 0; i < 9; ++i)
        if (offset >= l->nodes[i].offset && offset < l->nodes[i].offset + l->nodes[i].bytes) return i;
    assert(0); return 0;
}
static sync_memory_registers_t registers(const dma_progress_layout_t *rx,
    const dma_progress_layout_t *tx, uint64_t rp, uint64_t tp)
{
    unsigned r = index_at(rx, rp), t = index_at(tx, tp);
    return (sync_memory_registers_t){rx->nodes[r].address, rx->nodes[(r + 8) % 9].address,
        tx->nodes[t].address, tx->nodes[(t + 8) % 9].address, tx->nodes[t].next,
        tx->nodes[t].next & 0x3ffffu, 3u << 16, 5u, 0u, 0x60005u, 0x60005u};
}
int main(void)
{
    dma_progress_layout_t rx = layout(true), tx = layout(false);
    sync_memory_frontier_t p = {0};
    dma_progress_capture_t c = {.first_read_us=1000,.last_read_us=1001,
        .generation=2,.running=true,.ownership_verified=true};
    uint64_t rp = 65000, tp = rp - 16384;
    sync_memory_registers_t r = registers(&rx,&tx,rp,tp), changed;
    assert(sync_memory_observe(&p,&rx,&tx,&r,&r,&c) == SMF_OK);
    assert(p.conditional_valid && p.ahb_hz == 40000000 && p.memory_bound_hz == 192000000);
    uint64_t shift = p.conditional.rx_lo - (rp - rp % 32768 + rx.nodes[index_at(&rx,rp)].offset);
    for (unsigned i = 0; i < 20000; ++i) {
        unsigned dt = 17 + i % 37;
        rp += 40u * dt; tp += 40u * dt;
        c.first_read_us += dt; c.last_read_us += dt;
        r = registers(&rx,&tx,rp,tp);
        assert(sync_memory_observe(&p,&rx,&tx,&r,&r,&c) == SMF_OK);
        uint64_t actual_r = rp + shift, actual_t = tp + shift;
        assert(p.conditional.rx_lo <= actual_r && actual_r < p.conditional.rx_hi);
        assert(p.conditional.tx_lo <= actual_t && actual_t < p.conditional.tx_hi);
        assert(p.conditional.tx_prefetch_hi == p.conditional.tx_hi + 4092 + 64);
    }
    changed = r; ++changed.rx_done;
    assert(sync_memory_observe(&p,&rx,&tx,&r,&changed,&c) == SMF_UNSTABLE && !p.conditional_valid);
    changed = r; changed.bus_update = 1;
    assert(sync_memory_observe(&p,&rx,&tx,&changed,&changed,&c) == SMF_CLOCK);
    changed = r; changed.sysclk = 1u << 16;
    assert(sync_memory_observe(&p,&rx,&tx,&changed,&changed,&c) == SMF_CLOCK);
    changed = r; changed.ahb_div = 0;
    assert(sync_memory_observe(&p,&rx,&tx,&changed,&changed,&c) == SMF_CLOCK);
    changed = r; changed.rx_done = 0;
    assert(sync_memory_observe(&p,&rx,&tx,&changed,&changed,&c) == SMF_DESCRIPTOR);
    changed = r; changed.tx_done = changed.tx_bf0;
    assert(sync_memory_observe(&p,&rx,&tx,&changed,&changed,&c) == SMF_LINK);
    changed = r; changed.tx_state ^= 4;
    assert(sync_memory_observe(&p,&rx,&tx,&changed,&changed,&c) == SMF_LINK);
    c.last_read_us = c.first_read_us + 200;
    assert(sync_memory_observe(&p,&rx,&tx,&r,&r,&c) == SMF_PROGRESS && !p.conditional_valid);
    c.last_read_us = c.first_read_us + 1; ++c.generation;
    assert(sync_memory_observe(&p,&rx,&tx,&r,&r,&c) == SMF_OK);
    c.running = false;
    assert(sync_memory_observe(&p,&rx,&tx,&r,&r,&c) == SMF_NOT_LIVE && !p.conditional_valid);
    puts("PASS: 20000 connected memory captures, independent RX/TX partitions, clock/DONE/link/stability/deadline refusals");
}

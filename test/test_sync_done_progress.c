/* C5VRX by Twotoz/contributors: independent payload/DONE progress oracle.
 * Synthetic traces only: they cannot prove physical DMA publication order. */
#include <assert.h>
#include <stdio.h>
#include "sync_done_progress.h"

static dma_progress_layout_t layout(bool balanced, unsigned rotation)
{
    dma_progress_layout_t l = {0};
    uint32_t sizes[9], offsets[9], total = 0;
    for (unsigned i = 0; i < 9; ++i) {
        offsets[i] = total;
        sizes[i] = i < 7 ? 4092u : balanced ? 2062u : i == 7 ? 4092u : 32u;
        total += sizes[i];
    }
    assert(total == DMA_PROGRESS_RING);
    for (unsigned i = 0; i < 9; ++i) {
        unsigned n = (i + rotation) % 9;
        l.nodes[i] = (dma_progress_node_t){0x40850000u + 16u * i,
            0x40850000u + 16u * ((i + 1) % 9), offsets[n], sizes[n]};
    }
    assert(dma_progress_layout_valid(&l));
    return l;
}
static unsigned active(const dma_progress_layout_t *l, uint64_t position)
{
    unsigned offset = position % DMA_PROGRESS_RING;
    for (unsigned i = 0; i < 9; ++i)
        if (offset >= l->nodes[i].offset && offset < l->nodes[i].offset + l->nodes[i].bytes) return i;
    assert(0); return 0;
}
static sync_done_capture_t capture(const dma_progress_layout_t *l, uint64_t position, uint64_t time)
{
    unsigned a = active(l, position);
    return (sync_done_capture_t){.done=l->nodes[(a + 8) % 9].address,
        /* Independently pre-read two descriptors ahead of actual payload. */
        .bf0=l->nodes[(a + 2) % 9].address, .first_us=time, .last_us=time,
        .context={5, 7, 8, 9}, .rx_clock=0x60005, .tx_clock=0x60005,
        .memory_rate_bound=192000000, .live=true, .metadata_owned=true};
}
static void oracle(bool balanced, unsigned rotation)
{
    dma_progress_layout_t l = layout(balanced, rotation);
    sync_done_progress_t p = {0}; sync_done_observation_t out;
    uint64_t position = 65000, time = 1000, shift = 0, prior_end = 0;
    for (unsigned k = 0; k < 20000; ++k) {
        sync_done_capture_t f = capture(&l, position, time), last = capture(&l, position + 40, time + 1);
        /* BF0 metadata can change without any completion; do not reject or
         * invent fresh data based on it. DONE is independently stable. */
        last.bf0 = l.nodes[(active(&l, position) + 3) % 9].address;
        sync_done_result_t result = sync_done_observe(&p, &l, &f, &last, &out);
        if (f.done != last.done) assert(result == SDP_UNSTABLE && !out.observed);
        else {
            assert(result == SDP_OK && out.observed && !out.memory_write_permission);
            uint64_t physical = position - position % DMA_PROGRESS_RING + l.nodes[active(&l, position)].offset;
            if (!prior_end) { shift = p.done_end - physical; assert(!out.newly_completed); }
            assert(p.done_end == physical + shift);
            if (prior_end) assert(out.newly_completed == (p.done_end != prior_end));
            prior_end = p.done_end;
        }
        unsigned dt = 17u + k % 37u;
        time += dt; position += 40u * dt;
    }
}
int main(void)
{
    for (unsigned rotation = 0; rotation < 9; ++rotation) {
        oracle(false, rotation); oracle(true, rotation);
    }
    dma_progress_layout_t l = layout(false, 0);
    sync_done_progress_t p = {0}; sync_done_observation_t o;
    sync_done_capture_t f = capture(&l, 4092u * 8, 1000), last = f;
    assert(sync_done_observe(&p, &l, &f, &last, &o) == SDP_OK);
    uint64_t end = o.done_end; uint32_t epoch = o.epoch;
    f = last = capture(&l, 32768, 1001);
    assert(sync_done_observe(&p, &l, &f, &last, &o) == SDP_OK && o.done_end == end + 32);
    end = o.done_end;
    f.first_us = f.last_us = last.first_us = last.last_us = 1002;
    last.bf0 = l.nodes[4].address;
    assert(sync_done_observe(&p, &l, &f, &last, &o) == SDP_OK && !o.newly_completed && o.done_end == end);
    f.first_us = f.last_us = last.first_us = last.last_us = 1152; /*150us: possible hidden lap */
    assert(sync_done_observe(&p, &l, &f, &last, &o) == SDP_AMBIGUOUS && !o.observed);
    assert(sync_done_observe(&p, &l, &f, &last, &o) == SDP_NOT_READY);
    sync_done_restart(&p);
    assert(sync_done_observe(&p, &l, &f, &last, &o) == SDP_OK && o.epoch == epoch + 1);
    last.context.phy++;
    assert(sync_done_observe(&p, &l, &f, &last, &o) == SDP_CONTEXT && !o.observed);
    sync_done_restart(&p); last = f; last.rx_clock = 0x60006;
    assert(sync_done_observe(&p, &l, &f, &last, &o) == SDP_CLOCK);
    sync_done_restart(&p); last = f; f.done = last.done = 0;
    assert(sync_done_observe(&p, &l, &f, &last, &o) == SDP_DESCRIPTOR);
    sync_done_restart(&p); f = last = capture(&l, 32768, 1); last.last_us = 151;
    assert(sync_done_observe(&p, &l, &f, &last, &o) == SDP_AMBIGUOUS);
    sync_done_restart(&p); last = f; last.last_us = 0;
    assert(sync_done_observe(&p, &l, &f, &last, &o) == SDP_TIME);
    puts("PASS DONE observer: 360000 independent captures, both actual geometries/all rotations, BF0≠completion, short tail, hidden laps, context/clock/time and explicit epoch reset; no write permission");
    return 0;
}

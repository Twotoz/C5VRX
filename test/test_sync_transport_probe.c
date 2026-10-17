/* C5VRX by Twotoz/contributors: actual capture adapter never invents proof. */
#include <assert.h>
#include <stdio.h>
#include "sync_transport_probe.h"
static dma_progress_layout_t layout(uintptr_t first)
{
    dma_progress_layout_t l;
    for (unsigned i=0;i<9;++i)
        l.nodes[i]=(dma_progress_node_t){first+16*i,first+16*((i+1)%9),4092*i,i==8?32:4092};
    return l;
}
int main(void)
{
    sync_transport_probe_t p={0};
    dma_progress_layout_t rx=layout(0x1000),tx=layout(0x2000);
    dma_progress_limits_t limits={40000000,40000000,128,512,true,false,false};
    dma_progress_capture_t c={rx.nodes[5].address,tx.nodes[1].address,1000,1001,7,40000000,40000000,true,true};
    sync_transport_probe_update(&p,&rx,&tx,&limits,&c);
    assert(!p.frontiers.valid && !p.progress.ready && p.calls==1);
    assert(p.blocked==(STP_BLOCK_CLOCK|STP_BLOCK_FIFO));
    assert(!strcmp(sync_transport_probe_reason(&p),"clock_unverified"));
    limits.clock_proven=true;
    sync_transport_probe_update(&p,&rx,&tx,&limits,&c);
    assert(!p.frontiers.valid && p.blocked==STP_BLOCK_FIFO);
    assert(!strcmp(sync_transport_probe_reason(&p),"fifo_bound_unverified"));
    limits.bounds_proven=true;
    sync_transport_probe_update(&p,&rx,&tx,&limits,&c);
    assert(p.frontiers.valid && p.progress.ready && p.frontiers.generation==7);
    assert(!strcmp(sync_transport_probe_reason(&p),"captured"));
    limits.bounds_proven=false;c.first_read_us+=10;c.last_read_us+=10;
    sync_transport_probe_update(&p,&rx,&tx,&limits,&c);
    assert(!p.frontiers.valid && !p.progress.ready && p.blocked==STP_BLOCK_FIFO);
    limits.bounds_proven=true;
    sync_transport_probe_update(&p,&rx,&tx,&limits,&c);
    assert(p.frontiers.valid && p.frontiers.epoch==2);
    ++c.generation;
    sync_transport_probe_update(&p,&rx,&tx,&limits,&c);
    assert(!p.frontiers.valid && !strcmp(sync_transport_probe_reason(&p),"generation_changed"));
    sync_transport_probe_update(&p,&rx,&tx,&limits,&c);
    assert(p.frontiers.valid && p.frontiers.epoch==3);
    limits.tx_prefetch_bytes+=32;
    sync_transport_probe_update(&p,&rx,&tx,&limits,&c);
    assert(!p.frontiers.valid && !strcmp(sync_transport_probe_reason(&p),"limits_changed"));
    sync_transport_probe_update(&p,&rx,&tx,&limits,&c);
    assert(p.frontiers.valid && p.frontiers.epoch==4);
    c.first_read_us+=1000;c.last_read_us+=1000;
    sync_transport_probe_update(&p,&rx,&tx,&limits,&c);
    assert(!p.frontiers.valid && !strcmp(sync_transport_probe_reason(&p),"lap_ambiguous"));
    c.ownership_verified=false;
    sync_transport_probe_update(&p,&rx,&tx,&limits,&c);
    assert(!p.frontiers.valid && !strcmp(sync_transport_probe_reason(&p),"owner_unavailable"));
    c.ownership_verified=true;c.running=false;
    sync_transport_probe_update(&p,&rx,&tx,&limits,&c);
    assert(!p.frontiers.valid && !strcmp(sync_transport_probe_reason(&p),"not_live"));
    c.running=true;rx.nodes[8].bytes=4092;
    sync_transport_probe_update(&p,&rx,&tx,&limits,&c);
    assert(!p.frontiers.valid && !strcmp(sync_transport_probe_reason(&p),"geometry_unverified"));
    puts("PASS: capture adapter unknown clock/FIFO, lost proof, generation, lap, owner and geometry refusal");
}

/* C5VRX by Twotoz/contributors: actual geometry whole-current/NEXT model.
 * Tests arithmetic under the stated TRM sequencing contract, not hardware
 * publication/WCET. No raw writer or proof booleans are enabled. */
#include <assert.h>
#include <stdio.h>
#include "sync_descriptor_window.h"
static dma_progress_layout_t layout(bool rx,unsigned rotation,uintptr_t base)
{
    dma_progress_layout_t l={0};
    uint32_t lengths[9],starts[9],sum=0;
    for(unsigned i=0;i<9;++i) {
        lengths[i]=rx ? (i<7 ? 4092 : 2062) : (i<8 ? 4092 : 32);
        starts[i]=sum; sum+=lengths[i];
    }
    assert(sum==32768);
    for(unsigned i=0;i<9;++i) {
        unsigned physical=(i+rotation)%9;
        l.nodes[i]=(dma_progress_node_t){base+16*physical,base+16*((physical+1)%9),starts[physical],lengths[physical]};
    }
    assert(dma_progress_layout_valid(&l)); return l;
}
static unsigned at(const dma_progress_layout_t *l,uint64_t p)
{
    unsigned offset=p%32768;
    for(unsigned i=0;i<9;++i) if(offset>=l->nodes[i].offset && offset-l->nodes[i].offset<l->nodes[i].bytes) return i;
    assert(false); return 0;
}
static sdw_capture_t capture(const sdw_model_t *m,uint64_t rx,uint64_t tx)
{
    unsigned ri=at(&m->rx,rx),ti=at(&m->tx,tx),tn=(ti+1)%9;
    sdw_capture_t c={.context={7,8,9,10},.epoch=3,.first_us=1000,.last_us=1001,.fresh_from=32768};
    c.rx_current=rx-rx%32768+m->rx.nodes[ri].offset;
    c.tx_current=tx-tx%32768+m->tx.nodes[ti].offset;
    c.rx_done_end=c.rx_current;c.tx_done_end=c.tx_current;
    c.first=(sync_memory_registers_t){
        .rx_bf0=m->rx.nodes[ri].address,.rx_done=m->rx.nodes[(ri+8)%9].address,
        .tx_bf0=m->tx.nodes[ti].address,.tx_done=m->tx.nodes[(ti+8)%9].address,
        .tx_next=m->tx.nodes[tn].address,.tx_state=m->tx.nodes[tn].address&0x3ffffu,
        .sysclk=3u<<16,.ahb_div=5,.rx_clock=0x60005,.tx_clock=0x60005};
    c.last=c.first; return c;
}
static void oracle(const sdw_model_t *m,const sdw_capture_t *c,const sdw_window_t *w)
{
    unsigned ri=at(&m->rx,c->rx_current),ti=at(&m->tx,c->tx_current);
    /* Independent extremal payload oracle: current/NEXT can be fully
     * transferred, then one32-bit beat per48MHz cycle through publication.
     * Entire cache interval stays beyond both readers and RX wrap writes. */
    uint64_t tx_final=c->tx_current+m->tx.nodes[ti].bytes+m->tx.nodes[(ti+1)%9].bytes+w->motion_bytes;
    uint64_t rx_final=c->rx_current+m->rx.nodes[ri].bytes+m->rx.nodes[(ri+1)%9].bytes+w->motion_bytes;
    assert(w->cache_first>=tx_final && w->cache_first+32768>=rx_final);
    assert(w->cache_last<=c->rx_done_end-8280);
    assert(w->cache_first>=c->fresh_from && !(w->cache_first%64) && !(w->cache_last%64));
    assert(w->cache_last-w->cache_first>=1280);
}
static void prefetch(const sdw_model_t *m,sdw_capture_t *c,bool rx,bool tx)
{
    unsigned ri=at(&m->rx,c->rx_current),ti=at(&m->tx,c->tx_current);
    if(rx)c->rx_current+=m->rx.nodes[ri].bytes;
    if(tx)c->tx_current+=m->tx.nodes[ti].bytes;
    ri=at(&m->rx,c->rx_current);ti=at(&m->tx,c->tx_current);
    c->first.rx_bf0=m->rx.nodes[ri].address;
    c->first.tx_bf0=m->tx.nodes[ti].address;
    c->first.tx_next=m->tx.nodes[(ti+1)%9].address;
    c->first.tx_state=c->first.tx_next&0x3ffffu;
    c->last=c->first;
}
int main(void)
{
    unsigned admitted24=0,rejected16=0,total=0,prefetch_admitted=0,prefetch_refused=0;
    for(unsigned rr=0;rr<9;++rr) for(unsigned tr=0;tr<9;++tr) {
        dma_progress_layout_t rx=layout(true,rr,0x1000),tx=layout(false,tr,0x2000);
        sdw_model_t m; assert(sdw_init(&m,&rx,&tx,7,3)==SDW_OK && m.history_bytes==8280);
        for(unsigned phase=0;phase<32768;phase+=127)
            for(unsigned rp=0;rp<2;++rp)for(unsigned tp=0;tp<2;++tp){
                uint64_t pos=4ull*32768+phase;
                sdw_capture_t c=capture(&m,pos,pos-24576);sdw_window_t w;
                prefetch(&m,&c,rp!=0,tp!=0);
                assert(sdw_admit(&m,&c,c.context,3,1001,5,1,1281,&w)==SDW_WINDOW);
                sdw_result_t r=sdw_admit(&m,&c,c.context,3,1001,5,w.safe_first,w.safe_first+1280,&w);
                if(r==SDW_OK){++prefetch_admitted;oracle(&m,&c,&w);}
                else {assert(r==SDW_WINDOW);++prefetch_refused;}
                /* Correct modulo offsets with a false lap are still invalid. */
                sdw_capture_t bad=c;bad.rx_current+=32768;
                assert(sdw_admit(&m,&bad,c.context,3,1001,5,1,1281,&w)==SDW_REGISTERS);
                bad=c;bad.tx_done_end+=32768;
                assert(sdw_admit(&m,&bad,c.context,3,1001,5,1,1281,&w)==SDW_REGISTERS);
            }
        for(unsigned phase=0;phase<32768;++phase) {
            uint64_t pos=4ull*32768+phase;
            sdw_capture_t c=capture(&m,pos,pos-24576); sdw_window_t w;
            sdw_result_t result=sdw_admit(&m,&c,c.context,3,1001,5,1,1281,&w);
            assert(result==SDW_WINDOW);
            uint64_t first=w.safe_first,last=first+1280;
            result=sdw_admit(&m,&c,c.context,3,1001,5,first,last,&w);
            assert(result==SDW_OK); ++admitted24; oracle(&m,&c,&w);
            /* Existing whole pulse, delayed/expired: never partial approval. */
            assert(sdw_admit(&m,&c,c.context,3,1041,5,first,last,&w)==SDW_WINDOW);
            assert(sdw_admit(&m,&c,c.context,3,1161,5,first,last,&w)==SDW_AMBIGUOUS);
            c=capture(&m,pos,pos-16384);
            result=sdw_admit(&m,&c,c.context,3,1001,5,1,1281,&w); assert(result==SDW_WINDOW);
            first=w.safe_first; last=first+1280;
            result=sdw_admit(&m,&c,c.context,3,1001,5,first,last,&w);
            rejected16+=result==SDW_WINDOW;
            ++total;
        }
    }
    dma_progress_layout_t rx=layout(true,0,0x1000),tx=layout(false,0,0x2000);
    sdw_model_t m={0}; sdw_window_t w; sdw_capture_t c={0};
    assert(sdw_admit(&m,&c,c.context,0,0,5,1,1281,&w)==SDW_NOT_READY);
    assert(sdw_init(&m,&rx,&tx,7,3)==SDW_OK); c=capture(&m,4ull*32768,4ull*32768-24576);
    assert(sdw_admit(&m,&c,c.context,3,1001,5,1,1281,&w)==SDW_WINDOW);
    uint64_t first=w.safe_first,last=first+1280;
    assert(sdw_admit(&m,&c,c.context,3,1001,5,first,last,&w)==SDW_OK);
    for(unsigned offset=0;offset<64;++offset) {
        assert(sdw_admit(&m,&c,c.context,3,1001,5,first+offset,last+offset,&w)==SDW_OK);
        oracle(&m,&c,&w);
    }
    sdw_capture_t bad=c; ++bad.last.tx_next;
    assert(sdw_admit(&m,&bad,c.context,3,1001,5,first,last,&w)==SDW_REGISTERS);
    bad=c; bad.first.tx_done=bad.last.tx_done=bad.last.tx_bf0;
    assert(sdw_admit(&m,&bad,c.context,3,1001,5,first,last,&w)==SDW_REGISTERS);
    bad=c; ++bad.first.tx_state; ++bad.last.tx_state;
    assert(sdw_admit(&m,&bad,c.context,3,1001,5,first,last,&w)==SDW_REGISTERS);
    bad=c; bad.first.sysclk=bad.last.sysclk=0;
    assert(sdw_admit(&m,&bad,c.context,3,1001,5,first,last,&w)==SDW_CLOCK);
    raw_access_context_t stale=c.context; ++stale.gain;
    assert(sdw_admit(&m,&c,stale,3,1001,5,first,last,&w)==SDW_CONTEXT);
    assert(sdw_admit(&m,&c,c.context,4,1001,5,first,last,&w)==SDW_CONTEXT);
    bad=c; bad.fresh_from=c.rx_current-8279;
    assert(sdw_admit(&m,&bad,c.context,3,1001,5,first,last,&w)==SDW_FRESHNESS);
    bad=c; ++bad.rx_current;
    assert(sdw_admit(&m,&bad,c.context,3,1001,5,first,last,&w)==SDW_REGISTERS);
    /* Concrete old BF0-anchor error: DONE.end=12276, BF0=16368. The old
     * history ceiling allowed a pulse inside still-protected CPU history. */
    bad=capture(&m,4ull*32768+12276,4ull*32768+12276-24576);
    prefetch(&m,&bad,true,false);
    uint64_t unsafe_last=(bad.rx_current-m.history_bytes)&~63ull;
    assert(sdw_admit(&m,&bad,bad.context,3,1001,5,unsafe_last-1280,unsafe_last,&w)==SDW_WINDOW);
    assert(w.safe_last==((bad.rx_done_end-m.history_bytes)&~63ull));
    assert(sdw_admit(&m,&c,c.context,3,1000,5,first,last,&w)==SDW_TIME);
    bad=c; bad.first_us=0;
    assert(sdw_admit(&m,&bad,c.context,3,1001,5,first,last,&w)==SDW_TIME);
    assert(sdw_admit(&m,&c,c.context,3,1001,0,first,last,&w)==SDW_TIME);
    assert(sdw_admit(&m,&c,c.context,3,UINT64_MAX-1,5,first,last,&w)==SDW_TIME);
    assert(sdw_admit(&m,&c,c.context,3,1001,5,first,first+1281,&w)==SDW_LENGTH);
    assert(sdw_admit(&m,&c,c.context,3,1001,5,UINT64_MAX-4,UINT64_MAX,&w)==SDW_LENGTH);
    uint64_t near=UINT64_MAX-UINT64_MAX%32768;
    bad=capture(&m,near+32760,near+32760-24576);
    assert(sdw_admit(&m,&bad,bad.context,3,1001,5,first,last,&w)==SDW_OVERFLOW);
    assert(rejected16 && admitted24==total);
    assert(prefetch_admitted&&prefetch_refused);
    printf("PASS prefetch union: %u admitted / %u refused, both RX/TX positions, independent DONE history and lap-alias refusals\n",prefetch_admitted,prefetch_refused);
    printf("PASS: %u exact phases/all81RX-TX rotations, 1280-byte whole pulse admitted at24KiB/5us; %u 16KiB refusals; expired/stale/unknown/clock/length/overflow fail closed. TRM model, not physical WCET proof.\n",total,rejected16);
}

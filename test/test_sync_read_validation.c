/* C5VRX by Twotoz/contributors: actual DONE observer + retrospective reads.
 * Synthetic adversarial traces, not physical DMA publication evidence. */
#include <assert.h>
#include <stdio.h>
#include "sync_read_validation.h"

typedef struct {
    dma_progress_layout_t l;
    sync_done_capture_t bf,bl,af,al;
    sync_done_observation_t b,a;
    sync_read_extent_t e;
} fixture_t;
static unsigned checks;
static sync_read_result_t validate(const fixture_t *f, bool lease)
{
    ++checks;
    return sync_read_validate(&f->e,&f->l,&f->bf,&f->bl,&f->b,
        &f->af,&f->al,&f->a,lease);
}
static unsigned active(const dma_progress_layout_t *l,uint64_t p)
{
    p%=DMA_PROGRESS_RING;
    for(unsigned i=0;i<9;++i)
        if(p>=l->nodes[i].offset && p<l->nodes[i].offset+l->nodes[i].bytes)return i;
    assert(0);return 0;
}
static sync_done_capture_t capture(const dma_progress_layout_t *l,uint64_t p,uint64_t t)
{
    unsigned i=active(l,p);
    return (sync_done_capture_t){.done=l->nodes[(i+8)%9].address,
        .bf0=l->nodes[i].address,.first_us=t,.last_us=t,
        .context={1,2,3,4},.rx_clock=0x60005,.tx_clock=0x60005,
        .memory_rate_bound=192000000,.live=true,.metadata_owned=true};
}
static fixture_t fixture(unsigned rotation,unsigned initial,unsigned duration,bool tails)
{
    fixture_t f={0};uint32_t offsets[9],sizes[9],total=0;
    for(unsigned i=0;i<9;++i){offsets[i]=total;
        sizes[i]=i<7?4092u:tails?2062u:i==7?4092u:32u;total+=sizes[i];}
    assert(total==32768);
    for(unsigned i=0;i<9;++i){unsigned n=(i+rotation)%9;
        f.l.nodes[i]=(dma_progress_node_t){0x40850000u+16u*i,
            0x40850000u+16u*((i+1)%9),offsets[n],sizes[n]};}
    assert(dma_progress_layout_valid(&f.l));
    sync_done_progress_t p={0};sync_done_observation_t o;
    uint64_t pos=3u*32768u+f.l.nodes[initial].offset,time=1000;
    sync_done_capture_t c=capture(&f.l,pos,time);
    assert(sync_done_observe(&p,&f.l,&c,&c,&o)==SDP_OK);
    /* Bootstrap an entire known fresh ring through actual observer calls. */
    for(unsigned n=0;n<9;++n){unsigned len=f.l.nodes[(initial+n)%9].bytes;
        pos+=len;time+=(len+39u)/40u;c=capture(&f.l,pos,time);
        assert(sync_done_observe(&p,&f.l,&c,&c,&o)==SDP_OK);}
    f.bf=f.bl=c;f.b=o;
    f.af=f.al=capture(&f.l,pos+duration*40u,time+duration);
    assert(sync_done_observe(&p,&f.l,&f.af,&f.al,&f.a)==SDP_OK);
    sync_read_extent_note(&f.e,f.b.done_end-4092u);
    sync_read_extent_note(&f.e,f.b.done_end-1u);
    return f;
}
#define BAD(change,expected) do {fixture_t q=f;change;assert(validate(&q,true)==(expected));}while(0)
int main(void)
{
    for(unsigned tails=0;tails<2;++tails)for(unsigned r=0;r<9;++r)
        for(unsigned i=0;i<9;++i)for(unsigned dt=85;dt<=100;++dt){
            fixture_t f=fixture(r,i,dt,tails!=0);
            assert(validate(&f,true)==SRV_OK);
            /* Exhaust every location in the source ring against independent
             * ending whole-current+next and starting completed-data oracle. */
            unsigned ai=active(&f.l,f.a.done_end);
            uint64_t upper=f.a.done_end+f.l.nodes[ai].bytes+f.l.nodes[(ai+1)%9].bytes;
            uint64_t oldest=upper-32768u;if(oldest<f.a.fresh_from)oldest=f.a.fresh_from;
            for(uint64_t k=f.b.done_end-32768u;dt==100 && k<=f.b.done_end;++k){
                f.e=(sync_read_extent_t){0};sync_read_extent_note(&f.e,k);
                sync_read_result_t expected=k<oldest?SRV_EXPIRED:k>=f.b.done_end?SRV_INCOMPLETE:SRV_OK;
                assert(validate(&f,true)==expected);
            }
        }
    fixture_t f=fixture(3,7,100,true);
    assert(validate(&f,false)==SRV_LEASE);
    BAD(q.bf.done=q.l.nodes[0].address,SRV_METADATA);
    BAD(q.al.bf0=q.l.nodes[(active(&q.l,q.a.done_end)+1)%9].address,SRV_METADATA);
    BAD(q.af.live=false,SRV_METADATA);
    BAD(q.bl.metadata_owned=false,SRV_METADATA);
    BAD(q.b.observed=false,SRV_METADATA);
    BAD(q.a.memory_write_permission=true,SRV_METADATA);
    BAD(q.bf.context.gain++,SRV_CONTEXT);
    BAD(q.af.context.phy++;q.al.context.phy++,SRV_CONTEXT);
    BAD(q.af.rx_clock++;q.al.rx_clock++,SRV_CLOCK);
    BAD(q.af.memory_rate_bound=191000000;q.al.memory_rate_bound=191000000,SRV_CLOCK);
    BAD(q.a.epoch++,SRV_EPOCH);
    BAD(q.a.fresh_from++,SRV_EPOCH);
    BAD(q.a.done_end++,SRV_EPOCH);
    BAD(q.af.first_us=q.bf.first_us-1;q.af.last_us=q.af.first_us;
        q.al=q.af,SRV_TIME);
    BAD(q.al.last_us=q.bf.first_us+200,SRV_AMBIGUOUS);
    BAD(q.bf.first_us=q.bf.last_us=q.bl.first_us=q.bl.last_us=0;
        q.af.first_us=q.af.last_us=q.al.first_us=q.al.last_us=UINT64_MAX,SRV_OVERFLOW);
    BAD(q.e.overflow=true,SRV_OVERFLOW);
    BAD(q.e.first=q.e.last,SRV_EXTENT);
    BAD(q.e.first=q.b.fresh_from-1,SRV_EXPIRED);
    BAD(q.e.last=q.b.done_end+1,SRV_INCOMPLETE);
    BAD(q.bf.bf0=q.bl.bf0=q.bf.done,SRV_LINK);
    BAD(q.l.nodes[0].next=0,SRV_METADATA);
    { fixture_t q=fixture(0,7,1,true);
      q.b.done_end=(UINT64_MAX/32768u)*32768u+q.b.done_end%32768u;
      q.a.done_end=q.b.done_end;
      assert(validate(&q,true)==SRV_OVERFLOW); }
    /* Same DONE can be valid continuation; absence of new reads is not
     * camera evidence and still cannot bypass context/lifetime checks. */
    f=fixture(0,0,1,true);assert(f.a.done_end==f.b.done_end);
    assert(validate(&f,true)==SRV_OK);
    f.e=(sync_read_extent_t){0};assert(validate(&f,true)==SRV_OK);
    f.e.count=UINT64_MAX;sync_read_extent_note(&f.e,1);assert(f.e.overflow);
    f.e=(sync_read_extent_t){0};sync_read_extent_note(&f.e,UINT64_MAX);assert(f.e.overflow);
    f.e=(sync_read_extent_t){0};sync_read_extent_note(&f.e,17);sync_read_extent_note(&f.e,5);
    sync_read_extent_note(&f.e,17);assert(f.e.first==5 && f.e.last==18 && f.e.count==3);
    puts("PASS retrospective raw read validation: all rotations, tails,85-100us, expiration and failure guards");
    printf("%u validation checks\n",checks);return 0;
}

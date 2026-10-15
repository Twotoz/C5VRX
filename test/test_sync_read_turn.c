/* C5VRX by Twotoz/contributors: retrospective read turn contract tests.
 * Independent payload coordinate oracle, not physical DMA publication proof. */
#include <assert.h>
#include <stdio.h>
#include "sync_read_turn.h"
static dma_progress_layout_t make_layout(bool balanced,unsigned rotation)
{
    dma_progress_layout_t l={0};uint32_t offsets[9],sizes[9],total=0;
    for(unsigned i=0;i<9;++i){offsets[i]=total;sizes[i]=i<7?4092u:balanced?2062u:i==7?4092u:32u;total+=sizes[i];}
    assert(total==32768);
    for(unsigned i=0;i<9;++i){unsigned n=(i+rotation)%9;l.nodes[i]=(dma_progress_node_t){0x1000+16*i,0x1000+16*((i+1)%9),offsets[n],sizes[n]};}
    assert(dma_progress_layout_valid(&l));return l;
}
static sync_done_capture_t capture(const dma_progress_layout_t *l,uint64_t position,uint64_t us)
{
    unsigned index=0;uint32_t offset=(uint32_t)(position%32768u);
    while(!(offset>=l->nodes[index].offset&&offset<l->nodes[index].offset+l->nodes[index].bytes)){assert(++index<9);}
    return (sync_done_capture_t){.done=l->nodes[(index+8)%9].address,.bf0=l->nodes[index].address,
        .first_us=us,.last_us=us,.context={1,2,3,4},.rx_clock=0x60005,.tx_clock=0x60005,
        .memory_rate_bound=192000000,.live=true,.metadata_owned=true};
}
static void seed(sync_done_progress_t *p,const dma_progress_layout_t *l,uint64_t pos,uint64_t us)
{
    *p=(sync_done_progress_t){0};sync_done_capture_t c=capture(l,pos,us);sync_done_observation_t o;
    assert(sync_done_observe(p,l,&c,&c,&o)==SDP_OK);p->fresh_from-=32768;
}
int main(void)
{
    unsigned cases=0;
    for(unsigned balanced=0;balanced<2;++balanced)for(unsigned rot=0;rot<9;++rot)
    for(uint64_t offset=0;offset<32768;offset+=64){
        dma_progress_layout_t l=make_layout(balanced,rot);uint64_t pos=98304+offset;
        sync_done_progress_t p;seed(&p,&l,pos,1000);
        sync_done_capture_t b=capture(&l,pos,1001),a=capture(&l,pos+400,1011);
        raw_access_token_t token={b.context,1,0};sync_flywheel_t f={.state=SFW_TRACK,.lines=17,.acquisitions=1};
        sync_flywheel_t original=f;sro_turn_t t={0};
        assert(sro_begin(&t,&f,&p,&l,&b,&b,&token)==SRO_OK&&t.permit_run);
        uint64_t complete=t.complete_before;
        /* A10us original read near the completed frontier is safe under
         * independent sequential payload geometry, even through short tails. */
        sro_read(&t.reads,complete-6000);sro_read(&t.reads,complete-1);++f.lines;
        assert(sro_finish(&t,&f,&p,&l,&a,&a,true,80)==SRO_OK&&t.camera_evidence);
        assert(f.lines==18&&!t.memory_write_permission);++cases;
        /* The same turn must restore every FSM byte when its source has
         * since been overwritten; validity is not a write-time permission. */
        seed(&p,&l,pos,1000);t=(sro_turn_t){0};f=original;
        assert(sro_begin(&t,&f,&p,&l,&b,&b,&token)==SRO_OK);
        sro_read(&t.reads,t.fresh_from-1);f.state=SFW_ACQUIRE;f.lines=999;
        assert(sro_finish(&t,&f,&p,&l,&a,&a,true,80)==SRO_EXPIRED);
        assert(!memcmp(&f,&original,sizeof(f))&&p.failed&&!t.camera_evidence&&!t.memory_write_permission);
    }
    dma_progress_layout_t l=make_layout(true,0);uint64_t pos=100000;
    for(unsigned mode=0;mode<8;++mode){
        sync_done_progress_t p;seed(&p,&l,pos,1000);
        sync_done_capture_t b=capture(&l,pos,1001),a=capture(&l,pos+400,1011);
        raw_access_token_t token={b.context,7,0};sync_flywheel_t f={.lines=2},original=f;sro_turn_t t={0};
        assert(sro_begin(&t,&f,&p,&l,&b,&b,&token)==SRO_OK);sro_read(&t.reads,t.complete_before-1);++f.lines;
        if(mode==0)++a.context.phy;
        if(mode==1)a.live=false;
        if(mode==2)++a.rx_clock;
        if(mode==3)a.bf0=l.nodes[4].address;
        if(mode==4)a.first_us=a.last_us=1081; /* deadline */
        if(mode==5)sro_read(&t.reads,UINT64_MAX);
        if(mode==6)sro_read(&t.reads,t.complete_before); /* uncompleted */
        assert(sro_finish(&t,&f,&p,&l,&a,&a,mode!=7,80)!=SRO_OK);
        assert(!memcmp(&f,&original,sizeof(f))&&p.failed&&!t.memory_write_permission);
    }
    printf("PASS read turn:%u geometry/rotation/phase cases, exact FSM rollback, source/context/lease/clock/time/overflow refusal; writes denied\n",cases);
}

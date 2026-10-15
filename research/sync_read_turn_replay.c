/* C5VRX by Twotoz/contributors: actual-core read-only DONE-cadence replay.
 * Sequential payload oracle and8us modeled read span, not C5 scheduling. */
#include <stdio.h>
#include <assert.h>
#include <stdlib.h>
#include <string.h>
#include "sync_read_turn.h"
#include "phase8_gain_lut.h"
extern unsigned sfw_research_acq_skips;
static sro_turn_t turn;
static bool recording;
void sfw_research_raw_read(uint64_t k){if(recording)sro_read(&turn.reads,k);}
static dma_progress_layout_t layout(bool balanced)
{
    dma_progress_layout_t l={0};uint32_t offset=0;
    for(unsigned i=0;i<9;++i){uint32_t size=i<7?4092u:balanced?2062u:i==7?4092u:32u;
        l.nodes[i]=(dma_progress_node_t){0x1000+16*i,0x1000+16*((i+1)%9),offset,size};offset+=size;}
    return l;
}
static sync_done_capture_t capture(const dma_progress_layout_t *l,uint64_t pos,uint64_t time)
{
    unsigned i=0;uint32_t offset=pos%32768;
    while(!(offset>=l->nodes[i].offset&&offset<l->nodes[i].offset+l->nodes[i].bytes))++i;
    return (sync_done_capture_t){.done=l->nodes[(i+8)%9].address,.bf0=l->nodes[i].address,
        .first_us=time,.last_us=time,.context={1,2,3,4},.rx_clock=0x60005,.tx_clock=0x60005,
        .memory_rate_bound=192000000,.live=true,.metadata_owned=true};
}
int main(int argc,char **argv)
{
    if(argc!=3&&argc!=4)return 2;unsigned budget=argc==4?(unsigned)atoi(argv[3]):192u;
    if(!budget||budget>1200u)return 2;FILE *in=fopen(argv[1],"rb");if(!in)return 2;
    if(fseek(in,0,SEEK_END))return 2;long length=ftell(in);if(length<1||fseek(in,0,SEEK_SET))return 2;
    uint8_t *raw=malloc((size_t)length);if(!raw||fread(raw,1,(size_t)length,in)!=(size_t)length)return 2;fclose(in);
    uint8_t ring[32768]={0},before[32768];dma_progress_layout_t l=layout(atoi(argv[2])!=0);
    sfw_ring_t r={.ring=ring,.ring_bytes=sizeof(ring),.phase=c5vrx_phase8_gain_lut};
    sync_flywheel_t f;sfw_init(&f);sfw_run(&f,&r,0,0,0,false,0);
    sync_done_progress_t progress={0};raw_access_t access={0};
    uint64_t position=0,first_track=0,first_lock=0,first_v=0,max_reads=0,min_slack=UINT64_MAX;
    unsigned calls=0,accepted=0,rejected=0,warm=0,max_acquire=0,max_track=0,nonempty=0;
    for(uint64_t time=0;(time+8)*40u<=(uint64_t)length;time+=80){
        uint64_t target=time*40u;for(;position<target;++position)ring[position&32767u]=raw[position];
        sync_done_capture_t b=capture(&l,position,time),a;
        raw_access_token_t token;if(!raw_access_read_begin(&access,b.context,true,&token))return 3;
        sro_result_t result=sro_begin(&turn,&f,&progress,&l,&b,&b,&token);
        if(result!=SRO_OK)return 4;
        if(turn.permit_run){
            memcpy(before,ring,sizeof(ring));bool acq=f.state==SFW_ACQUIRE;
            recording=true;sfw_run(&f,&r,turn.complete_before,turn.complete_before,0,false,budget);recording=false;
            if(memcmp(before,ring,sizeof(ring)))return 5;
            ++calls;unsigned *m=acq?&max_acquire:&max_track;if(f.evals>*m)*m=f.evals;
            if(turn.reads.count>max_reads)max_reads=turn.reads.count;
        }else ++warm;
        target=(time+8)*40u;for(;position<target;++position)ring[position&32767u]=raw[position];
        a=capture(&l,position,time+8);
        bool ended=raw_access_read_end(&access,&token,a.context,true);
        result=sro_finish(&turn,&f,&progress,&l,&a,&a,ended,80);
        if(result!=SRO_OK){++rejected;break;}
        ++accepted;
        if(turn.camera_evidence){++nonempty;uint32_t cover;assert(sro_cover(&l,&a,&a,&cover)==SRO_OK);
            uint64_t oldest=progress.done_end+cover-32768u;
            if(turn.reads.first-oldest<min_slack)min_slack=turn.reads.first-oldest;}
        if(!first_track&&f.state==SFW_TRACK)first_track=time+8;
        if(!first_lock&&sfw_locked(&f))first_lock=time+8;
        if(!first_v&&f.v_valid)first_v=time+8;
        if(raw_access_readers(&access))return 6;
    }
    printf("{\"calls\":%u,\"accepted\":%u,\"rejected\":%u,\"warmup\":%u,\"nonempty\":%u,"
        "\"first_track_us\":%llu,\"first_lock_us\":%llu,\"first_v_us\":%llu,"
        "\"max_acquire_evals\":%u,\"max_track_evals\":%u,\"max_actual_raw_reads\":%llu,"
        "\"min_postvalidation_slack\":%llu,\"acquire_stale_skips\":%u,\"acquisitions\":%u,"
        "\"real_v_fields\":%u,\"relocks\":%u,\"standard\":%d,\"locked\":%s,"
        "\"raw_writes\":0,\"readers_left\":%u,\"write_permission\":false,\"hardware_cycles_proven\":false}\n",
        calls,accepted,rejected,warm,nonempty,(unsigned long long)first_track,(unsigned long long)first_lock,
        (unsigned long long)first_v,max_acquire,max_track,(unsigned long long)max_reads,
        (unsigned long long)(min_slack==UINT64_MAX?0:min_slack),sfw_research_acq_skips,f.acquisitions,f.vsyncs,
        f.relocks,sfw_standard(&f),sfw_locked(&f)?"true":"false",raw_access_readers(&access));
    free(raw);return 0;
}

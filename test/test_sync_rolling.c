/* C5VRX by Twotoz/contributors: actual compact rolling window against an
 * independent scalar nine-step oracle, including source-write boundaries. */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "phase8_gain_lut.h"
#include "kp_tables.h"
static unsigned phase_reads;
#define SFW_PHASE_READ() (++phase_reads)
#include "../main/sync_flywheel.c"
static uint32_t rng=212;
static uint32_t next(void){rng^=rng<<13;rng^=rng>>17;rng^=rng<<5;return rng;}
static bool reference(const sync_flywheel_t*f,const sfw_ring_t*r,uint64_t k)
{
    int sum=0;
    for(unsigned j=0;j<9;++j){
        uint8_t a=r->phase[r->ring[(uint32_t)(k-5u+j)&(r->ring_bytes-1u)]];
        uint8_t b=r->phase[r->ring[(uint32_t)(k-4u+j)&(r->ring_bytes-1u)]];
        int delta=(int)b-(int)a;
        while(delta>127)delta-=256;
        while(delta< -128)delta+=256;
        sum+=delta;
    }
    return sum<=9*f->thr;
}
int main(void)
{
    uint8_t raw[32768],phase[256];
    for(unsigned j=0;j<32768;++j)raw[j]=(uint8_t)next();
    for(unsigned j=0;j<256;++j)phase[j]=(uint8_t)j;
    sfw_ring_t r={.ring=raw,.ring_bytes=sizeof(raw),.phase=phase};
    sync_flywheel_t f;sfw_init(&f);
    uint64_t k=65536,last=0;bool valid=false;unsigned rolling=0,restarts=0;
    lowm_reset();
    for(unsigned j=0;j<1000000;++j){
        if((next()&7u)!=0)k+=3u;else k=65536u+next()%32768u;
        if(j%8191u==0){raw[next()%32768u]^=0xffu;phase[next()%256u]^=0x7fu;lowm_reset();valid=false;}
        if(j%65521u==0)k=UINT64_MAX-2u;
        f.thr=(int16_t)((int)(next()%256u)-128);
        bool reuse=valid&&k>last&&k-last==3u;phase_reads=0;
        assert(lowm(&f,&r,k)==reference(&f,&r,k));
        assert(phase_reads==(reuse?3u:10u));
        if(reuse)++rolling;else ++restarts;
        valid=true;last=k;
    }
    phase_reads=0;lowm_reset();(void)lowm(&f,&r,1000);assert(phase_reads==10);
    assert(!sfw_run(NULL,&r,0,0,0,false,0));
    phase_reads=0;(void)lowm(&f,&r,1003);assert(phase_reads==10);
    assert(!synth(&f,&r,1000,48,-1632,188,-9830,-1632,false,2000,4096));
    phase_reads=0;(void)lowm(&f,&r,1006);assert(phase_reads==10);
    assert(sizeof(s_lowm_window)<=32u);
    unsigned coarse_reads=0;
    for(unsigned j=0;j<1000000;++j){
        k=(j%4093u==0)?UINT64_MAX-2u:65536u+next()%32768u;
        f.thr=(int16_t)((int)(next()%256u)-128);
        if(j%8191u==0){raw[next()%32768u]^=0xffu;phase[next()%256u]^=0x7fu;}
        phase_reads=0;
        assert(coarse_lowm(&f,&r,k)==reference(&f,&r,k));
        assert(phase_reads>=2u&&phase_reads<=10u&&!s_lowm_window.valid);
        coarse_reads+=phase_reads;
    }
    printf("PASS: 1000000 exact coarse windows, %u phase reads instead of 10000000, no rolling cache reuse\n",coarse_reads);
    sync_read_extent_t extent={0};r.read_extent=&extent;
    sfw_init(&f);f.scan_pos=65536u;
    assert(!sfw_run(&f,&r,70000u,89452u,0,false,52u));
    uint64_t cursor=f.scan_pos;unsigned votes=f.acq_n;
    assert(votes==52u);
    assert(extent.count==104u&&extent.first==65535u&&extent.last<=70000u&&!extent.overflow);
    extent=(sync_read_extent_t){0};
    assert(!sfw_run(&f,&r,70000u,89452u,0,false,52u));
    assert(f.scan_pos>cursor&&f.acq_n>votes&&f.scan_pos>=69984u);
    assert(extent.count&&extent.last<=70000u);
    extent=(sync_read_extent_t){0};
    cursor=f.scan_pos;votes=f.acq_n;
    assert(!sfw_run(&f,&r,70000u,89452u,0,false,52u));
    assert(f.scan_pos==cursor&&f.acq_n==votes&&f.evals==0u);
    assert(!extent.count);
    puts("PASS: repeated DONE drains pending cursor, then stops without replaying votes or reading future samples");
    printf("PASS: 1000000 exact original windows (%u rolling/%u restarts),3vs10reads, mutations/rollover/run+synth reset, cache=%zu bytes\n",rolling,restarts,sizeof(s_lowm_window));
}

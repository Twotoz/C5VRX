/* C5VRX by Twotoz/contributors: block-only profiling preserves exact output,
 * observes at most seven timestamps, and handles counter wrap/alias refusal. */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "phase8_gain_lut.h"
#include "kp_tables.h"
#include "output_level.h"
static uint32_t ticks,reads;
static uint32_t fake_tick(void){++reads;return ++ticks;}
#define SFW_BENCH_CLOCK() fake_tick()
#include "../main/sync_flywheel.c"
int main(void)
{
    uint8_t raw[4096],before[4096],workspace[1280],other[1280];uint16_t lut[1024];
    sfw_bench_profile_t profile;
    sfw_ring_t r={.ring=raw,.ring_bytes=sizeof(raw),.phase=c5vrx_phase8_gain_lut,
        .program_verified=true,.verified_kp_x100=85,.decoder_lut=lut,
        .workspace=workspace,.workspace_bytes=sizeof(workspace),.benchmark_profile=&profile};
    unsigned cases=0;
    for(unsigned kp=0;kp<8;++kp)for(unsigned output=0;output<10;++output) {
        r.verified_kp_x100=kp_level_x100[kp];
        r.reference_lut=kp_tables[kp];r.verified_output=output;
        for(unsigned j=0;j<1024;++j)lut[j]=output_entry(kp_tables[kp][j],output,NULL);
        assert(sfw_bench_seed_private(&r,12345));memcpy(before,raw,sizeof(raw));
        for(unsigned pal=0;pal<2;++pal)for(unsigned kind=0;kind<4;++kind) {
            sfw_private_pulse_t p,q;ticks=UINT32_MAX-2u;reads=0;
            assert(sfw_bench_prepare_private(&r,pal,(sfw_bench_kind_t)kind,&p));
            unsigned expected=kind==3?7u:4u;
            assert(reads==expected && profile.stage_mask==(kind==3?127u:99u));
            assert(profile.init_done-profile.begin==1 && profile.commit_done-profile.body_done==1);
            assert(profile.commit_done-profile.begin==expected-1u);
            assert(profile.minmask_in_selection);
            if(kind==3) {
                assert(profile.prefix_done-profile.init_done==1 && profile.regular_done-profile.prefix_done==1);
                assert(profile.selection_done-profile.regular_done==1 && profile.body_done-profile.selection_done==1);
                assert(profile.prefix_states[0] && profile.prefix_states[1] && profile.minmask_builds<=32u);
            } else assert(!profile.prefix_done && !profile.regular_done && !profile.selection_done && !profile.minmask_builds);
            sfw_ring_t plain=r;plain.workspace=other;plain.benchmark_profile=NULL;
            reads=0;assert(sfw_bench_prepare_private(&plain,pal,(sfw_bench_kind_t)kind,&q));assert(!reads);
            assert(p.bytes==q.bytes && p.start==q.start && p.bootstrap_refused==q.bootstrap_refused);
            assert(!memcmp(workspace,other,p.bytes) && !memcmp(raw,before,sizeof(raw)));++cases;
        }
    }
    sfw_private_pulse_t p;
    r.benchmark_profile=(sfw_bench_profile_t *)raw;assert(!sfw_bench_prepare_private(&r,true,SFW_BENCH_H,&p));
    r.benchmark_profile=(sfw_bench_profile_t *)workspace;assert(!sfw_bench_prepare_private(&r,true,SFW_BENCH_H,&p));
    r.benchmark_profile=(sfw_bench_profile_t *)lut;assert(!sfw_bench_prepare_private(&r,true,SFW_BENCH_H,&p));
    r.benchmark_profile=(sfw_bench_profile_t *)r.reference_lut;assert(!sfw_bench_prepare_private(&r,true,SFW_BENCH_H,&p));
    r.benchmark_profile=(sfw_bench_profile_t *)r.phase;assert(!sfw_bench_prepare_private(&r,true,SFW_BENCH_H,&p));
    assert(!memcmp(raw,before,sizeof(raw)));
    printf("PASS: %u block-profiled pulses byte-identical, 4/7 reads, counter wrap, stage consistency and source-alias refusal\n",cases);
}

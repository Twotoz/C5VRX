/* C5VRX by Twotoz/contributors: differential proof against the physical-J
 * baseline. All ordered states, ambiguity counts and prepared tip bytes must
 * match, including failure/partial private output; no CPU timing claim. */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "phase8_gain_lut.h"
#include "kp_tables.h"
#include "output_level.h"
#include "../main/sync_flywheel.c"
#include "sync_prefix_reference.inc"

static uint32_t rng=0x213c5u;
static uint32_t random32(void){rng^=rng<<13;rng^=rng>>17;rng^=rng<<5;return rng;}
int main(void)
{
    uint8_t raw[4096],before[4096];uint16_t lut[1024];
    unsigned prefixes=0,tips=0,ambiguities=0,refusals=0;
    for(unsigned kp=0;kp<KP_LEVELS;++kp)for(unsigned output=0;output<OUTPUT_LEVELS;++output)
        for(unsigned pattern=0;pattern<40;++pattern)for(unsigned native=0;native<3;++native) {
            for(unsigned j=0;j<1024;++j)lut[j]=output_entry(kp_tables[kp][j],output,NULL);
            sfw_ring_t r={.ring=raw,.ring_bytes=sizeof(raw),.phase=c5vrx_phase8_gain_lut,
                .program_verified=true,.verified_kp_x100=kp_level_x100[kp],.decoder_lut=lut,
                .mask_bit0=native!=0,.clear_bit0=native==2};
            build_cells(&r);
            uint32_t phase=random32();
            for(unsigned j=0;j<sizeof(raw);++j) {
                phase+=(uint32_t)(pattern<16?-1638:pattern<24?-9830:pattern<32?0:(int32_t)random32());
                raw[j]=pattern<32?s_cell[(uint8_t)(phase>>8)]:(uint8_t)random32();
            }
            if(pattern==24)memset(raw,0,sizeof(raw));
            if(pattern==25)memset(raw,255,sizeof(raw));
            memcpy(before,raw,sizeof(raw));
            for(unsigned parity=0;parity<2;++parity) {
                uint64_t a=512u+parity;
                sync_flywheel_t old_f,new_f;sfw_init(&old_f);sfw_init(&new_f);
                baseline_bound_t old_states[2][32];decoder_bound_t new_states[2][32];
                unsigned old_counts[2],new_counts[2];uint64_t old_last[2],new_last[2];
                assert(baseline_prefix(&old_f,&r,a,old_states,old_counts,old_last));
                assert(decoder_prefix(&new_f,&r,a,new_states,new_counts,new_last));
                assert(!memcmp(old_counts,new_counts,sizeof(old_counts)) && !memcmp(old_last,new_last,sizeof(old_last)));
                assert(old_f.bootstrap_unknown==new_f.bootstrap_unknown);
                ambiguities+=new_f.bootstrap_unknown;
                for(unsigned p=0;p<2;++p)for(unsigned j=0;j<old_counts[p];++j)
                    assert(new_states[p][j]==((old_states[p][j].phase<<6)|old_states[p][j].x));
                ++prefixes;
                for(unsigned low=92;low<=94;low+=2) {
                    uint8_t old_tip[128],new_tip[128];memset(old_tip,0xa5,sizeof(old_tip));memset(new_tip,0xa5,sizeof(new_tip));
                    sfw_init(&old_f);sfw_init(&new_f);
                    old_f.repair_step_q8=new_f.repair_step_q8=verified_sync_step(&r);
                    bool old_ok=baseline_tip(&old_f,&r,a,low,old_tip);
                    bool new_ok=first_v_tip(&new_f,&r,a,low,new_tip);
                    assert(old_ok==new_ok && !memcmp(old_tip,new_tip,sizeof(old_tip)));
                    assert(old_f.bootstrap_unknown==new_f.bootstrap_unknown);
                    refusals+=!new_ok;++tips;
                }
                assert(!memcmp(raw,before,sizeof(raw)));
            }
        }
    printf("PASS: ordered prefix/state/ambiguity equivalence=%u, byte-exact tips=%u, ambiguous=%u, refusals=%u; all8kp/10outputs/native masks/parities\n",
           prefixes,tips,ambiguities,refusals);
}

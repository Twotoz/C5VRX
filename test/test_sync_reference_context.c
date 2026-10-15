/* C5VRX by Twotoz/contributors: output-independent first-EQ construction
 * requires a complete canonical reference/output binding. Actual tracker bits
 * stay unchanged; source IQ and the active LUT must never be overwritten. */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "phase8_gain_lut.h"
#include "kp_tables.h"
#include "sync_lut_context.h"
#include "../main/sync_flywheel.c"
static uint32_t random_state=0xc5213u;
static uint32_t random32(void)
{random_state^=random_state<<13;random_state^=random_state>>17;random_state^=random_state<<5;return random_state;}
int main(void)
{
    uint8_t raw[4096],before[4096];uint16_t lut[1024],saved[1024];
    unsigned cases=0;
    assert(!sync_lut_context_matches(NULL,kp_tables[0],0));
    assert(!sync_lut_context_matches(kp_tables[0],NULL,0));
    assert(!sync_lut_context_matches(kp_tables[0],kp_tables[0],OUTPUT_LEVELS));
    for(unsigned kp=0;kp<8;++kp)for(unsigned output=0;output<OUTPUT_LEVELS;++output) {
        for(unsigned j=0;j<1024;++j)lut[j]=output_entry(kp_tables[kp][j],output,NULL);
        assert(sync_lut_context_matches(lut,kp_tables[kp],output));
        memcpy(saved,lut,sizeof(lut));
        for(unsigned j=0;j<1024;++j) {
            lut[j]^=UINT16_C(0x0041);
            assert(!sync_lut_context_matches(lut,kp_tables[kp],output));
            lut[j]^=UINT16_C(0x0041);
        }
        sfw_ring_t r={.ring=raw,.ring_bytes=sizeof(raw),.phase=c5vrx_phase8_gain_lut,
            .program_verified=true,.verified_kp_x100=kp_level_x100[kp],.decoder_lut=lut,
            .reference_lut=kp_tables[kp],.verified_output=output};
        assert(verified_sync_step(&r));
        r.verified_output=OUTPUT_LEVELS;assert(!verified_sync_step(&r));r.verified_output=output;
        if(output){r.reference_lut=NULL;assert(!verified_sync_step(&r));r.reference_lut=kp_tables[kp];}
        for(unsigned native=0;native<3;++native)for(unsigned pattern=0;pattern<8;++pattern) {
            r.mask_bit0=native!=0;r.clear_bit0=native==2;
            build_cells(&r);uint32_t phase=random32();
            for(unsigned j=0;j<sizeof(raw);++j) {
                phase+=(uint32_t)(pattern<3?-1638:pattern<6?-9830:(int32_t)random32());
                raw[j]=pattern<6?s_cell[(uint8_t)(phase>>8)]:(uint8_t)random32();
            }
            memcpy(before,raw,sizeof(raw));
            sfw_ring_t unity=r;unity.decoder_lut=kp_tables[kp];unity.reference_lut=NULL;unity.verified_output=0;
            for(unsigned parity=0;parity<2;++parity)for(unsigned low=92;low<=94;low+=2) {
                sync_flywheel_t f,g;sfw_init(&f);sfw_init(&g);
                f.repair_step_q8=g.repair_step_q8=verified_sync_step(&r);
                uint8_t actual[128],reference[128];memset(actual,0xa5,128);memset(reference,0xa5,128);
                bool ok=first_v_tip(&f,&r,512u+parity,low,actual);
                bool refok=first_v_tip(&g,&unity,512u+parity,low,reference);
                assert(ok==refok && !memcmp(actual,reference,128));
                assert(f.bootstrap_unknown==g.bootstrap_unknown);++cases;
            }
            assert(!memcmp(raw,before,sizeof(raw)) && !memcmp(lut,saved,sizeof(lut)));
        }
    }
    printf("PASS: %u exact unity-reference tips across8kp/10outputs/masks/prefixes;81920 corrupt LUT words rejected, no source/table writes\n",cases);
}

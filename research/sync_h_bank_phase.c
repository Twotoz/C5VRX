/* C5VRX by Twotoz and contributors: exact H entry-phase/output sweep.
 * Independent actual LP recurrence, unchanged fixed normalized8/21 gate.
 * SFW_CORE_FILE selects a temporary prototype or unchanged production core. */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "phase8_gain_lut.h"
#include "kp_tables.h"
#include "output_level.h"
#ifndef SFW_CORE_FILE
#define SFW_CORE_FILE "../main/sync_flywheel.c"
#endif
#include SFW_CORE_FILE
#define N 1024u
#define A 512u
static uint8_t raw[4096],before[4096],decoded[N];
static void decode(const uint16_t *lut,unsigned parity,unsigned initial,unsigned level)
{
    unsigned phase=initial,x=level,y=level;
    /* Unknown physical state at the first fully generated pair. This also
     * covers an arbitrary real/synthetic boundary pair at the other parity,
     * rather than converging all initial states through a fixed prelude. */
    for(unsigned k=A+parity;k+1u<A+236u+32u;k+=2u) {
        decoded[k]=decoded[k+1]=(uint8_t)y;
        unsigned a=raw[k],b=raw[k+1u];
        unsigned addr=(a>>4)|((a&15u)<<4)|((b&128u)<<1)|((b&8u)<<6);
        unsigned token=lut[addr]>>11,carry=((addr>>8)+(phase>>3))>>2;
        unsigned v=lut[phase*32u+token];y=(y>>1)+(x>>1)+carry;x=v&63u;phase=(v>>6)&31u;
    }
}
static unsigned width(unsigned parity,unsigned limit)
{
    unsigned best=0;
    for(unsigned k=A+48u-15u;k<=A+48u+15u;++k) {
        if((k&1u)!=parity||decoded[k]>limit)continue;
        unsigned n=0,high=0;
        for(unsigned j=k;j+1u<N&&high<2u;j+=2u){if(decoded[j]<=limit)high=0;else ++high;n+=2;}
        n-=high*2;if(n>best)best=n;
    }
    return best;
}
int main(void)
{
    puts("kp,output,seed,previous_phase,following_phase,cases,accepted,width_min,width_max");
    for(unsigned kp=0;kp<8;++kp)for(unsigned output=0;output<10;++output) {
        uint16_t lut[1024];for(unsigned j=0;j<1024;++j)lut[j]=output_entry(kp_tables[kp][j],output,NULL);
        sfw_ring_t r={.ring=raw,.ring_bytes=4096,.phase=c5vrx_phase8_gain_lut,
            .verified_kp_x100=kp_level_x100[kp],.program_verified=true,.decoder_lut=lut};
        build_cells(&r);
        unsigned floor=output_entry(0,output,NULL)&63u,blank=output_entry(21,output,NULL)&63u;
        unsigned limit=floor+(8u*(blank-floor)+10u)/21u;
        /* Independent16x16 endpoint grid; do not couple both endpoints to
         * one continuous porch carrier. All32 decoder phases remain tested. */
        for(unsigned seed=0;seed<256;++seed) {
            uint32_t phase=(seed/16u)*4096u+A*1638u;
            for(unsigned k=0;k<4096;++k){phase=(phase-1638u)&65535u;raw[k]=s_cell[(uint8_t)((phase+128u)>>8)];}
            phase=(seed%16u)*4096u+1638u;
            for(unsigned k=A+236u;k<4096;++k){phase=(phase-1638u)&65535u;raw[k]=s_cell[(uint8_t)((phase+128u)>>8)];}
            memcpy(before,raw,sizeof(raw));
            unsigned previous=ph_at(&r,A-1u),following=ph_at(&r,A+236u);
            sync_flywheel_t f;sfw_init(&f);f.blank_q4=-102;f.repair_kp_x100=kp_level_x100[kp];
            f.repair_step_q8=verified_sync_step(&r);f.evidence_end=4096;
            assert(synth(&f,&r,A,48,-1632,188,f.repair_step_q8,-1632,false,0,4096));
            assert(!memcmp(raw,before,A)&&!memcmp(raw+A+236,before+A+236,4096-A-236));
            unsigned cases=0,accepted=0,lo=999,hi=0;
            for(unsigned parity=0;parity<2;++parity)for(unsigned initial=0;initial<32;++initial)
                for(unsigned level=0;level<=63;level+=63) {
                    memset(decoded,0,sizeof(decoded));decode(lut,parity,initial,level);
                    unsigned w=width(parity,limit);++cases;accepted+=w>=150&&w<=230;
                    if(w<lo)lo=w;if(w>hi)hi=w;
                }
            printf("%u,%u,%u,%u,%u,%u,%u,%u,%u\n",kp_level_x100[kp],output,seed,previous,following,cases,accepted,lo,hi);
        }
    }
}

/* C5VRX by Twotoz/contributors: bounded phase-only decoder-history work.
 * This is decoder state, never a source-sync detector or live admission proof. */
#include <assert.h>
#include <stdio.h>
#include "phase8_gain_lut.h"
#include "kp_tables.h"
#include "../main/sync_flywheel.c"
static bool phase_set(const uint8_t *raw,const uint16_t *lut,unsigned parity,
                      unsigned budget,uint32_t *states,unsigned *work)
{
    uint32_t set=UINT32_MAX;*work=0;
    for(unsigned k=parity;k<64u+parity;k+=2u) {
        unsigned a=raw[k],b=raw[k+1u];
        unsigned address=(a>>4)|((a&15u)<<4)|((b&128u)<<1)|((b&8u)<<6);
        unsigned token=lut[address]>>11;
        uint32_t todo=set,next=0;
        while(todo) {
            if(*work==budget){*states=UINT32_MAX;return false;}
            unsigned phase=first_token(todo);todo&=todo-1u;++*work;
            unsigned value=lut[phase*32u+token];
            next|=UINT32_C(1)<<((value>>6)&31u);
        }
        set=next;
        if(!(set&(set-1u))) {
            unsigned phase=first_token(set);
            for(k+=2u;k<64u+parity;k+=2u) {
                if(*work==budget){*states=UINT32_MAX;return false;}
                a=raw[k];b=raw[k+1u];
                address=(a>>4)|((a&15u)<<4)|((b&128u)<<1)|((b&8u)<<6);
                token=lut[address]>>11;
                phase=(lut[phase*32u+token]>>6)&31u;++*work;
            }
            *states=UINT32_C(1)<<phase;return true;
        }
    }
    *states=set;return true;
}
int main(void)
{
    uint8_t raw[4096];sfw_ring_t r={.ring=raw,.ring_bytes=4096,.phase=c5vrx_phase8_gain_lut};build_cells(&r);
    /* Exhaustion never publishes a partly narrowed state set. */
    memset(raw,0,sizeof(raw));uint32_t states;unsigned work;
    assert(!phase_set(raw,kp_tables[7],0,1,&states,&work)&&states==UINT32_MAX&&work==1);
    puts("kp,cases,max_transitions,ambiguous_after64,budget_refused");
    for(unsigned kp=6;kp<8;++kp) {
        unsigned cases=0,max=0,ambiguous=0,refused=0;
        for(int step=-1664;step<=-1600;++step)for(unsigned seed=0;seed<256;++seed) {
            uint32_t p=seed*256u;
            for(unsigned j=0;j<65;++j){p=(p+(uint32_t)step)&65535u;raw[j]=s_cell[(uint8_t)((p+128u)>>8)];}
            for(unsigned parity=0;parity<2;++parity) {
                uint32_t states;unsigned work;
                if(!phase_set(raw,kp_tables[kp],parity,256,&states,&work))++refused;
                else ambiguous+=(states&(states-1u))!=0;
                if(work>max)max=work;++cases;
            }
        }
        printf("%u,%u,%u,%u,%u\n",kp_level_x100[kp],cases,max,ambiguous,refused);
    }
}

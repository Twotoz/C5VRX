/* C5VRX by Twotoz/contributors: generated carrier bytes against actual root
 * strong-cell selection, independent exact phase oracle, and blank-history API. */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "sync_period_bank.h"
#include "../main/sync_flywheel.c"
static uint8_t sample(const sfw_period_slice_t *s,unsigned j)
{return j<s->first_bytes?s->first[j]:s->second[j-s->first_bytes];}
static uint8_t oracle(uint16_t phase,int step,unsigned j)
{return s_cell[(uint8_t)(((phase+(j+1u)*(uint32_t)step+128u)&65535u)>>8)];}
int main(void)
{
    uint8_t raw[4096]={0};sfw_ring_t r={.ring=raw,.ring_bytes=4096,.phase=c5vrx_phase8_gain_lut};build_cells(&r);
    assert(!memcmp(s_cell,sfw_period_data,256));
    assert(sfw_period_phase_compatible(c5vrx_phase8_gain_lut,false));
    assert(!sfw_period_phase_compatible(NULL,false)&&!sfw_period_phase_compatible(c5vrx_phase8_gain_lut,true));
    uint8_t altered[256];
    for(unsigned i=0;i<256;++i){memcpy(altered,c5vrx_phase8_gain_lut,256);altered[i]^=1;assert(!sfw_period_phase_compatible(altered,false));}
    unsigned bytes=0,phase_cases=0;uint32_t next=256;
    for(unsigned d=0;d<7;++d) {
        const sfw_period_descriptor_t *p=&sfw_period_descriptors[d];unsigned gcd=1u<<p->shift;
        assert(p->offset==next && p->period*gcd==65536u &&
            (((((uint32_t)p->step&65535u)>>p->shift)*p->inverse)&(p->period-1u))==1u);
        for(unsigned k=0;k<p->period;++k){assert(sfw_period_data[p->offset+k]==s_cell[(uint8_t)(((k*(uint32_t)p->step+128u)&65535u)>>8)]);++bytes;}
        next+=p->period;
        for(unsigned phase=0;phase<65536u;phase+=gcd) {
            sfw_period_slice_t s;unsigned n=(phase&2u)?188u:1280u;
            assert(sfw_period_slice(p->step,(uint16_t)phase,n,&s));
            assert(s.first_bytes && s.first_bytes+s.second_bytes==n &&
                s.first>=sfw_period_data+p->offset && s.first+s.first_bytes<=sfw_period_data+p->offset+p->period &&
                s.second==sfw_period_data+p->offset && s.second_bytes<=p->period);
            unsigned positions[]={0,n/2u,n-1u,s.first_bytes-1u};
            for(unsigned j=0;j<4;++j)assert(sample(&s,positions[j])==oracle((uint16_t)phase,p->step,positions[j]));
            if(s.second_bytes)assert(sample(&s,s.first_bytes)==oracle((uint16_t)phase,p->step,s.first_bytes));
            ++phase_cases;
        }
        sfw_period_slice_t s;
        assert(sfw_period_slice(p->step,0,p->period,&s)&&s.first_bytes+s.second_bytes==p->period);
        assert(!sfw_period_slice(p->step,0,p->period+1u,&s)&&!s.first&&!s.second&&!s.first_bytes&&!s.second_bytes);
        assert(!sfw_period_slice(p->step,0,0,&s));
        if(gcd>1)assert(!sfw_period_slice(p->step,1,1,&s));
    }
    assert(next==SFW_PERIOD_DATA_BYTES && bytes==264192u && phase_cases==264192u);
    sfw_period_slice_t s;assert(!sfw_period_slice(INT32_MIN,0,1,&s));assert(!sfw_period_slice(-7373,0,1,NULL));
    uint8_t history[64];
    for(unsigned offset=0;offset<2048;++offset) {
        for(unsigned j=0;j<64;++j)history[j]=s_cell[(uint8_t)((((offset+j)*(uint32_t)-1632+128u)&65535u)>>8)];
        assert(sfw_period_blank64_matches(history,offset));
        const unsigned indices[]={0,31,63};
        for(unsigned j=0;j<3;++j){history[indices[j]]^=1;assert(!sfw_period_blank64_matches(history,offset));history[indices[j]]^=1;}
    }
    assert(!sfw_period_blank64_matches(NULL,0)&&!sfw_period_blank64_matches(history,2048)&&!sfw_period_blank64_matches(history,UINT32_MAX));
    puts("PASS period bank: actual256cells/264192carrierbytes,264192exact phase slices, wrap/refusal,2048blank offsets with corruption/native/map refusal");
}

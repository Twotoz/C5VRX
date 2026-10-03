/* C5VRX by Twotoz and contributors. Golden control evidence regressions. */
#include "cvbs_level.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
int main(void)
{
    cvbs_level_t s;
    cvbs_level_init(&s);
    cvbs_level_stats_t v = {.valid=true, .repeated=2, .period=1280,
        .sync_uv=100000, .blank_uv=350000};
    assert(!cvbs_level_observe(&s,&v,true,1,50000));
    assert(!cvbs_level_observe(&s,&v,true,1,100000));
    assert(cvbs_level_observe(&s,&v,true,1,150000));
    uint8_t held[64]; memcpy(held,s.codes,64);
    assert(!cvbs_level_observe(&s,&v,true,1,200000));
    assert(!memcmp(held,s.codes,64));
    assert(!cvbs_level_observe(&s,&v,true,2,250000));
    assert(s.good==1);
    assert(!cvbs_level_observe(&s,&v,true,2,250000));
    assert(s.good==0);
    assert(!cvbs_level_observe(&s,&v,true,2,240000));
    assert(s.good==0);
    assert(!cvbs_level_observe(&s,&v,true,2,300000));
    assert(!cvbs_level_observe(&s,&v,true,2,550000));
    assert(s.good==1);
    assert(!cvbs_level_observe(&s,&v,false,2,600000));
    assert(s.good==0 && !memcmp(held,s.codes,64));
    v.clip_pm=201;
    assert(!cvbs_level_observe(&s,&v,true,2,650000));
    v.clip_pm=0; v.sync_mad_uv=35001;
    assert(!cvbs_level_observe(&s,&v,true,2,700000));
    v.sync_mad_uv=0; v.blank_uv=150000;
    assert(!cvbs_level_observe(&s,&v,true,2,750000));
    /* Code order is deliberately unrelated to voltage; duplicates included. */
    uint32_t uv[64];
    for(unsigned c=0;c<64;++c) uv[c]=((c*19)%64)/2*1000;
    for(unsigned a=0;a<64;++a) for(unsigned b=0;b<64;++b) {
        unsigned next=cvbs_level_slew(a,b,uv);
        if(uv[a]==uv[b]) { assert(next==b); continue; }
        if(uv[a]<uv[b]) {
            assert(uv[next]>uv[a] && uv[next]<=uv[b]);
            for(unsigned c=0;c<64;++c) assert(!(uv[c]>uv[a] && uv[c]<uv[next]));
        } else {
            assert(uv[next]<uv[a] && uv[next]>=uv[b]);
            for(unsigned c=0;c<64;++c) assert(!(uv[c]<uv[a] && uv[c]>uv[next]));
        }
    }
    puts("PASS: voltage-order slew, timing, context, loss and quality gates");
}

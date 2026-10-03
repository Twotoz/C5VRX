/* C5VRX by Twotoz and contributors. Check the lost-amplitude failure mode. */
#include "cvbs_level.h"
#include "cvbs_tables.h"
#include <assert.h>
#include <string.h>
#include <stdio.h>
static c5v4_cvbs_stats_t signal(int span, int blank)
{
    c5v4_cvbs_stats_t v = {.levels_valid=true,.repeated=1,.span_bins=span,
        .blank_bins=blank,.sync_bins=blank-span,.period_raw=2559};
    return v;
}
static void converge(c5v4_level_t *s, c5v4_cvbs_stats_t *v, uint32_t ctx)
{
    uint64_t start = s->observed_valid ? s->observed_us : 0;
    for (unsigned k=0;k<80;++k) {
        uint8_t before[256]; memcpy(before,s->codes,256);
        c5v4_level_observe(s,v,true,ctx,start+(uint64_t)(k+1)*100000);
        for(unsigned j=0;j<256;++j) {
            int difference=(int)s->codes[j]-before[j];
            assert(difference>=-1 && difference<=1);
        }
    }
}
static void check_calibrated_slew(void)
{
    uint32_t uv[64];
    /* Permute code order and include two equal measured voltages. */
    for (unsigned i=0;i<64;++i) uv[i]=((i*17)%64)*16000;
    uv[12]=uv[13];
    for (unsigned from=0;from<64;++from) for(unsigned to=0;to<64;++to) {
        uint8_t current=(uint8_t)from;
        for (unsigned step=0;current!=to && step<64;++step) {
            uint8_t next=c5v4_level_slew(current,(uint8_t)to,uv);
            assert(next!=current);
            if(uv[to]>uv[current]) {
                assert(uv[next]>uv[current] && uv[next]<=uv[to]);
                for(unsigned j=0;j<64;++j)
                    assert(!(uv[j]>uv[current] && uv[j]<uv[next]));
            } else if(uv[to]<uv[current]) {
                assert(uv[next]<uv[current] && uv[next]>=uv[to]);
                for(unsigned j=0;j<64;++j)
                    assert(!(uv[j]<uv[current] && uv[j]>uv[next]));
            } else assert(uv[next]==uv[to]);
            current=next;
        }
        assert(current==to);
    }
}
static void check_evidence_age(void)
{
    c5v4_level_t s; c5v4_level_init(&s);
    c5v4_cvbs_stats_t v=signal(16,2);
    uint8_t held[256]; memcpy(held,s.codes,256);
    assert(!c5v4_level_observe(&s,&v,true,1,0));
    assert(!c5v4_level_observe(&s,&v,true,1,50000));
    /* A delayed third window starts a new evidence chain. */
    assert(!c5v4_level_observe(&s,&v,true,1,300001));
    assert(s.good==1 && !memcmp(held,s.codes,256));
    assert(!c5v4_level_observe(&s,&v,true,1,300001));
    assert(s.good==0); /* duplicates cannot complete the chain */
    assert(!c5v4_level_observe(&s,&v,true,1,350001));
    assert(!c5v4_level_observe(&s,&v,true,1,400001));
    assert(c5v4_level_observe(&s,&v,true,1,450001));
    memcpy(held,s.codes,256);
    /* Clock reversal must not underflow the update cadence into a write. */
    assert(!c5v4_level_observe(&s,&v,true,1,1));
    assert(!c5v4_level_observe(&s,&v,true,1,50001));
    assert(!c5v4_level_observe(&s,&v,true,1,100001));
    assert(!memcmp(held,s.codes,256));
}
static int mv(const c5v4_level_t *s,int d)
{
    /* no winding for the reference plateau samples */
    unsigned i=(unsigned)(d+128)/4;
    return (int)(c5v4_dac_uv[s->codes[i]]/1000);
}
int main(void)
{
    check_calibrated_slew();
    check_evidence_age();
    for (int span=16;span<=80;span+=16) {
        for(int blank=-14;blank<=18;blank+=16) {
            c5v4_level_t s; c5v4_level_init(&s);
            c5v4_cvbs_stats_t v=signal(span,blank);
            assert(!c5v4_level_observe(&s,&v,true,1,0));
            assert(!c5v4_level_observe(&s,&v,true,1,100000));
            converge(&s,&v,1);
            assert(mv(&s,blank)>=285 && mv(&s,blank)<=315);
            assert(mv(&s,blank-span)<=15);
            /* Information-independent noise must not increase gain after loss. */
            uint8_t held[256]; memcpy(held,s.codes,256);
            for(unsigned k=0;k<100;++k) c5v4_level_observe(&s,&v,false,1,9000000+k*100000);
            assert(!memcmp(held,s.codes,256));
            v.levels_valid=false;
            assert(!c5v4_level_observe(&s,&v,true,1,20000000));
            assert(!memcmp(held,s.codes,256));
        }
    }
    c5v4_level_t s; c5v4_level_init(&s);
    c5v4_cvbs_stats_t v=signal(16,2);
    converge(&s,&v,1);
    uint8_t held[256];memcpy(held,s.codes,256);
    assert(!c5v4_level_observe(&s,&v,true,2,9000000)); /* new epoch, reacquire */
    assert(!memcmp(held,s.codes,256));
    v.span_bins=8; assert(!c5v4_level_observe(&s,&v,true,2,10000000));
    v=signal(16,2); v.origin_pm=900;
    assert(!c5v4_level_observe(&s,&v,true,2,11000000));
    assert(!memcmp(held,s.codes,256));
    for(unsigned upper=0;upper<1024;++upper) for(unsigned code=0;code<64;++code) {
        uint16_t word=c5v4_level_word((uint16_t)((upper<<6)|32),code);
        assert((word>>6)==upper && (word&63)==code);
    }
    puts("PASS CVBS servo: amplitude/CFO, calibrated voltage slew, aged/duplicate/reversed evidence, loss hold and epochs");
}

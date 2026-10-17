/* C5VRX/Twotoz: incremental ISR clock against independent division oracle. */
#include <assert.h>
#include <stdio.h>
#include "../main/sync_live_clock.h"
int main(void)
{
    unsigned checks=0;
    for(unsigned standard=1;standard<=2;++standard)
    for(unsigned parity=0;parity<2;++parity)
    for(unsigned variant=0;variant<5;++variant) {
        uint32_t n=standard==SLG_PAL?40960u:40676u;
        uint8_t d=64u;
        if(variant==0 && standard==SLG_NTSC){n=5720;d=9;}
        else n+=variant-2u;
        slg_clock_seed_t seed={.origin=UINT64_C(1000000000000),
            .next_half=variant?UINT64_C(10000003):0u,.numerator=n,
            .denominator=d,.h_parity=parity,.standard=(slg_standard_t)standard};
        slc_t s;assert(slc_init(&s,&seed));
        uint64_t half=seed.next_half;
        unsigned halves=standard==SLG_PAL?625u:525u,section=standard==SLG_PAL?5u:6u;
        for(unsigned i=0;i<50000;++i) {
            unsigned index=(unsigned)(half%halves);
            if(index>=3u*section && (half&1u)!=parity){++half;index=(unsigned)(half%halves);}
            uint64_t tick=seed.origin+(half*n+d/2u)/d;
            slg_kind_t kind=index<section?SLG_EQ:index<2u*section?SLG_BROAD:
                index<3u*section?SLG_EQ:SLG_H;
            unsigned width=kind==SLG_H?94u:kind==SLG_EQ?(standard==SLG_PAL?47u:46u):
                (unsigned)(((half+1u)*n+d/2u)/d-(half*n+d/2u)/d)-94u;
            slg_pulse_t p;assert(slc_next(&s,&p));
            assert(p.start==tick&&p.half==half&&p.index==index&&p.field==half/halves);
            assert(p.kind==kind&&p.width==width);++checks;++half;
        }
    }
    puts("PASS: 1000000 incremental clock/oracle cases, nominal/measured PAL/NTSC and both H parities");
    assert(checks==1000000u);return 0;
}

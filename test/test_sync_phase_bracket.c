/* C5VRX by Twotoz/contributors: independent physical-time fixtures for the
 * two-edge offset intersection. No fixture establishes actual GPIO latency. */
#include <assert.h>
#include <stdio.h>
#include "../main/sync_phase_bracket.h"

int main(void)
{
    unsigned checks=0;
    for(int64_t origin=-100;origin<=100;++origin)
        for(unsigned left=0;left<=35u;left+=7u)
            for(unsigned right=0;right<=35u;right+=7u) {
                uint64_t rise=(uint64_t)(1000+origin),fall=(uint64_t)(2000+origin);
                sync_phase_bracket_t r=sync_phase_bracket(1000,2000,
                    rise-left,rise+right,fall-right/2u,fall+left/2u);
                assert(r.valid && r.lo<=origin && r.hi>=origin && r.hi-r.lo<=64);
                ++checks;
            }
    /* Large continuously running timer: preserve64-bit origin exactly. */
    uint64_t base=UINT64_C(1000000000000000);
    sync_phase_bracket_t r=sync_phase_bracket(1000,2000,base+990,base+1010,base+1995,base+2005);
    assert(r.valid&&r.lo<=(int64_t)base&&r.hi>=(int64_t)base);++checks;
    /* Each marker edge alone can be uncertain; their intersection is narrow. */
    r=sync_phase_bracket(1000,2000,900,1100,2005,2010);
    assert(r.valid&&r.lo==3&&r.hi==12);++checks;
    assert(!sync_phase_bracket(1000,2000,900,910,2090,2100).valid);++checks;
    assert(!sync_phase_bracket(1000,2000,900,1100,1900,2100).valid);++checks;
    assert(!sync_phase_bracket(0,2000,0,1,2,3).valid);++checks;
    assert(!sync_phase_bracket(1000,1000,0,1,2,3).valid);++checks;
    assert(!sync_phase_bracket(1000,2000,1001,1000,2000,2001).valid);++checks;
    assert(!sync_phase_bracket(1000,2000,1000,2001,2000,2002).valid);++checks;
    assert(!sync_phase_bracket(1000,2000,1000,1001,2001,2000).valid);++checks;
    assert(!sync_phase_bracket(1000,2000,1,2,3,UINT64_MAX).valid);++checks;
    /* Full32-bit RMT-relative coordinates do not become signed32-bit. */
    r=sync_phase_bracket(UINT32_MAX-100u,UINT32_MAX-1u,
        base+UINT32_MAX-100u,base+UINT32_MAX-100u,
        base+UINT32_MAX-1u,base+UINT32_MAX-1u);
    assert(r.valid&&r.lo==(int64_t)base-2&&r.hi==(int64_t)base+2);++checks;
    printf("PASS: %u two-edge clock bracket fixtures; physical assumptions unproven\n",checks);
    return 0;
}

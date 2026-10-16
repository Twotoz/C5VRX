/* C5VRX by Twotoz/contributors: native timing qualification fixtures.
 * Generated logical pulses do not establish camera/analogue performance. */
#include <assert.h>
#include <stdio.h>
#include "../main/sync_native_track.h"
#include "../main/sync_gate_schedule.h"

static sync_native_track_t run(sgs_standard_t standard,unsigned mode,uint64_t origin)
{
    sync_native_track_t t={0};sgs_t plan;
    assert(sgs_init(&plan,standard));
    sgs_pulse_t p;
    while(sgs_next(&plan,&p) && p.field<4u) {
        uint64_t tick=origin+p.start;
        uint32_t width=p.width;
        if(mode==1u && p.kind!=SGS_NORMAL)continue; /* H only */
        if(mode==2u && p.kind==SGS_NORMAL)continue; /* V only */
        if(mode==3u && p.kind==SGS_BROAD)width=94u; /* Missing broad tips */
        if(mode==4u)tick+=p.field*2000u; /* Wrong field period */
        if(mode==5u)tick+=(p.half*17u)%7u; /* Small bounded edge jitter */
        sync_native_low(&t,tick,width);
    }
    return t;
}

int main(void)
{
    unsigned checks=0;
    for(unsigned std=0;std<2u;++std) {
        for(unsigned large=0;large<2u;++large) {
            uint64_t base=large?(UINT64_C(1)<<44):10000u;
            sync_native_track_t t=run((sgs_standard_t)std,0u,base);
            assert(t.valid && t.standard==(std==SGS_PAL?2u:1u));++checks;
            assert(t.qualified_fields>=2u && t.field>base);++checks;
            for(unsigned mode=1;mode<=4u;++mode) {
                t=run((sgs_standard_t)std,mode,base);
                assert(!t.valid);++checks;
            }
            t=run((sgs_standard_t)std,5u,base);
            assert(t.valid);++checks;
            sync_native_low(&t,t.last+10000u,94u);
            assert(!t.valid && !t.have_group);++checks;
            t=run((sgs_standard_t)std,0u,base);
            sync_native_low(&t,t.last,94u);
            assert(!t.valid && !t.seen);++checks;
        }
    }
    sync_native_track_t noise={0};
    uint32_t seed=7;uint64_t tick=10000;
    for(unsigned i=0;i<1000000u;++i) {
        seed=seed*1664525u+1013904223u;
        tick+=100u+seed%1400u;
        sync_native_low(&noise,tick,(seed>>16)%650u+1u);
        assert(!noise.valid);++checks;
    }
    printf("PASS: %u native H/V timing checks; no hardware lock claim\n",checks);
    return 0;
}

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
    sgs_pulse_t p;unsigned h_seen=0;uint64_t true_h=0;
    while(sgs_next(&plan,&p) && p.field<4u) {
        uint64_t tick=origin+p.start;
        uint32_t width=p.width;
        if(mode==1u && p.kind!=SGS_NORMAL)continue; /* H only */
        if(mode==2u && p.kind==SGS_NORMAL)continue; /* V only */
        if(mode==3u && p.kind==SGS_BROAD)width=94u; /* Missing broad tips */
        if(mode==4u)tick+=p.field*2000u; /* Wrong field period */
        if(mode==5u)tick+=(p.half*17u)%7u; /* Small bounded edge jitter */
        if(mode==8u && p.kind==SGS_EQ)continue; /* Filter removed all EQ. */
        unsigned n=standard==SGS_PAL?5u:6u;
        if(mode==9u && p.kind==SGS_EQ && p.index==n-1u)continue; /* Missing pre-EQ. */
        if(mode==10u && p.kind==SGS_EQ && p.index==3u*n-1u)continue; /* Missing post-EQ. */
        if(mode==11u && p.kind==SGS_EQ)width=1u; /* Arbitrary lows cannot be EQ. */
        if(mode==12u && p.kind==SGS_EQ && p.index==2u*n)tick+=40u; /* Wrong EQ period. */
        if(mode==17u && p.kind==SGS_BROAD && p.index==n+1u)continue;
        if(mode==18u && p.kind==SGS_BROAD && p.index==n+1u)width=620u;
        if(mode==16u && p.kind==SGS_NORMAL && ++h_seen<=2u) {
            /* Acquisition starts on a paired bright stripe before real H
             * becomes observable; a single latched H phase would stick. */
            sync_native_low(&t,tick+400u,94u);continue;
        }
        sync_native_low(&t,tick,width);
        if(p.kind==SGS_NORMAL)true_h=tick;
        /* These represent actual possible DAC-bit transitions during active
         * picture, and are long enough to survive the shared GPIO filter. */
        if(mode==6u && p.kind==SGS_NORMAL)sync_native_low(&t,tick+400u,40u);
        if(mode==7u && p.kind==SGS_NORMAL)sync_native_low(&t,tick+400u,94u);
        if(mode==16u && p.kind==SGS_NORMAL)sync_native_low(&t,tick+400u,94u);
        if(mode==13u && p.kind==SGS_NORMAL && p.index<(standard==SGS_PAL?623u:523u)) {
            sync_native_low(&t,tick+200u,25u);
            sync_native_low(&t,tick+400u,94u);
            sync_native_low(&t,tick+800u,40u);
            sync_native_low(&t,tick+1000u,94u);
        }
        if(mode==14u && p.kind==SGS_NORMAL && p.index<(standard==SGS_PAL?623u:523u)) {
            /* A second stable H-like phase exactly half a line away is
             * ambiguous from this single DAC bit; never select it silently. */
            sync_native_low(&t,tick+(standard==SGS_PAL?640u:636u),94u);
        }
        if(mode==15u && p.kind==SGS_EQ && p.index==3u*n-1u)
            sync_native_low(&t,tick+(standard==SGS_PAL?640u:636u),p.width);
    }
    if((mode==6u || mode==7u || mode==13u || mode==16u) && t.valid)
        assert(t.h==true_h); /* Last picture alias cannot steer output phase. */
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
            for(unsigned mode=6u;mode<=7u;++mode) {
                t=run((sgs_standard_t)std,mode,base);
                assert(t.valid&&t.qualified_fields>=2u);++checks;
            }
            t=run((sgs_standard_t)std,13u,base);
            assert(t.valid&&t.qualified_fields>=2u);++checks;
            for(unsigned mode=8u;mode<=12u;++mode) {
                t=run((sgs_standard_t)std,mode,base);
                assert(!t.valid&&!t.qualified_fields);++checks;
            }
            t=run((sgs_standard_t)std,14u,base);
            assert(!t.valid&&!t.qualified_fields);++checks;
            t=run((sgs_standard_t)std,15u,base);
            assert(!t.valid&&!t.qualified_fields);++checks;
            t=run((sgs_standard_t)std,16u,base);
            assert(t.valid&&t.qualified_fields>=2u);++checks;
            for(unsigned mode=17u;mode<=18u;++mode) {
                t=run((sgs_standard_t)std,mode,base);
                assert(!t.valid&&!t.qualified_fields);++checks;
            }
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

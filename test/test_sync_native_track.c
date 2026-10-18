/* C5VRX by Twotoz/contributors: native timing qualification fixtures.
 * Generated logical pulses do not establish camera/analogue performance. */
#include <assert.h>
#include <stdio.h>
#include "../main/sync_native_track.h"
#include "sync_gate_schedule.h"

static sync_native_track_t run(sgs_standard_t standard,unsigned mode,uint64_t origin)
{
    sync_native_track_t t={0};sgs_t plan;
    assert(sgs_init(&plan,standard));
    sgs_pulse_t p;unsigned h_seen=0,h_ordinal=0;uint64_t true_h=0;
    while(sgs_next(&plan,&p) && p.field<4u) {
        uint64_t tick=origin+p.start;
        uint32_t width=p.width;
        if(p.kind==SGS_NORMAL)++h_ordinal;
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
        bool interior=p.index>=30u && p.index<(standard==SGS_PAL?615u:515u);
        if(mode==19u && p.kind==SGS_NORMAL && h_ordinal%3u==0u)width=60u;
        if(mode==20u && p.kind==SGS_NORMAL && h_ordinal%2u==0u)continue;
        if(mode==21u && p.kind==SGS_NORMAL && interior && h_ordinal%4u)continue;
        if(mode==22u && p.kind==SGS_NORMAL && interior && h_ordinal%4u) {
            /* Picture lows still exist where H itself failed width gating. */
            sync_native_low(&t,tick+400u,94u);continue;
        }
        if(mode==23u && p.kind==SGS_NORMAL && interior && h_ordinal%5u)continue;
        if(mode==24u && p.kind==SGS_NORMAL && h_ordinal%4u)continue;
        if(mode==16u && p.kind==SGS_NORMAL && ++h_seen<=2u) {
            /* Acquisition starts on a paired bright stripe before real H
             * becomes observable; a single latched H phase would stick. */
            sync_native_low(&t,tick+400u,94u);continue;
        }
        sync_native_low(&t,tick,width);
        if(p.kind==SGS_NORMAL && width>=76u && width<=112u)true_h=tick;
        /* These represent actual possible DAC-bit transitions during active
         * picture, and are long enough to survive the shared GPIO filter. */
        if(mode==6u && p.kind==SGS_NORMAL)sync_native_low(&t,tick+400u,40u);
        if(mode==7u && p.kind==SGS_NORMAL)sync_native_low(&t,tick+400u,94u);
        if(mode==16u && p.kind==SGS_NORMAL)sync_native_low(&t,tick+400u,94u);
        if(mode==22u && p.kind==SGS_NORMAL)sync_native_low(&t,tick+400u,94u);
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
    if((mode==6u || mode==7u || mode==13u || mode==16u ||
        (mode>=19u && mode<=22u) || mode==24u) && t.valid)
        assert(t.h==true_h); /* Last picture alias cannot steer output phase. */
    return t;
}

static void interval_fits(unsigned *checks)
{
    for(unsigned standard=0;standard<2u;++standard) {
        for(unsigned large=0;large<2u;++large) {
            uint64_t base=large?(UINT64_C(1)<<44):10000u;
            sync_native_track_t t={0};
            for(unsigned n=0;n<=16u;++n) {
                unsigned slot=n*4u;
                uint64_t at=base+(standard?((uint64_t)slot*11440u+4u)/9u:(uint64_t)slot*1280u);
                snt_h(&t,at);
                if(n<16u)assert(!t.h_standard);
            }
            assert(t.h_standard==(standard?1u:2u));++*checks;
            assert(t.line_ticks_x32==(standard?40676u:40960u));++*checks;
            assert(!t.valid&&!t.h); /* H fit alone never publishes output phase. */
            t=(sync_native_track_t){0};
            unsigned slot=0u;
            for(unsigned n=0;n<=160u;++n) {
                uint64_t at=base+(standard?((uint64_t)slot*11440u+4u)/9u:(uint64_t)slot*1280u);
                snt_h(&t,at);slot+=1u+n%4u;
            }
            assert(t.h_standard==(standard?1u:2u));++*checks;
            assert(!t.valid&&!t.h);
        }
    }
    /* These adversaries cannot normalize a wrong period or non-integral
     * phase jumps into PAL/NTSC by simply counting each observation as H. */
    const unsigned wrong[]={1300u,1288u,2560u+400u,5120u+80u,6400u};
    for(unsigned pattern=0;pattern<sizeof(wrong)/sizeof(wrong[0]);++pattern) {
        sync_native_track_t t={0};
        for(unsigned n=0;n<1000u;++n)snt_h(&t,10000u+(uint64_t)n*wrong[pattern]);
        assert(!t.h_standard&&!t.valid);++*checks;
    }
    for(unsigned large=0;large<2u;++large) {
        uint64_t broad=(large?(UINT64_C(1)<<44):10000u)+100000u;
        sync_native_track_t t={.broad_start=broad};unsigned selected=99u;
        t.candidates[0]=(snt_h_candidate_t){.used=true,.standard=2u,
            .line_ticks_x32=40960u,.fit=broad-20000u,.last=broad-7u*1280u};
        assert(snt_h_for_v(&t,2u,&selected)&&selected==0u);++*checks;
        t.candidates[0].last=broad-SNT_V_H_MAX_AGE_TICKS-1u;
        assert(!snt_h_for_v(&t,2u,&selected));++*checks;
        t.candidates[0].last=broad-8u*1280u; /* Stale, exactly on H grid. */
        assert(!snt_h_for_v(&t,2u,&selected));++*checks;
        t.candidates[0].last=broad-7u*1280u-50u; /* Fresh, wrong phase. */
        assert(!snt_h_for_v(&t,2u,&selected));++*checks;
    }
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
            for(unsigned mode=19u;mode<=22u;++mode) {
                t=run((sgs_standard_t)std,mode,base);
                if(!t.valid)fprintf(stderr,"missing H fixture std=%u mode=%u fields=%u hstd=%u\n",std,mode,t.qualified_fields,t.h_standard);
                assert(t.valid&&t.qualified_fields>=2u);++checks;
            }
            t=run((sgs_standard_t)std,23u,base);
            assert(!t.valid&&!t.qualified_fields);++checks;
            t=run((sgs_standard_t)std,24u,base);
            assert(t.valid&&t.qualified_fields>=2u);++checks;
            sync_native_low(&t,t.last+10000u,94u);
            assert(!t.valid && !t.have_group);++checks;
            t=run((sgs_standard_t)std,0u,base);
            sync_native_low(&t,t.last,94u);
            assert(!t.valid && !t.seen);++checks;
        }
    }
    interval_fits(&checks);
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

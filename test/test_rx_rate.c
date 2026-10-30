/* C5VRX by Twotoz/contributors: rate geometry and the software picture
 * decoder against the native counter oracle's exhaustive pair reference. */
#include <assert.h>
#include <stdio.h>
#include <stdint.h>
#include <math.h>
static uint32_t test_hz=40000000u;
#define C5VRX_SAMPLE_HZ test_hz
#include "rx_rate.h"
#include "picture_sync.h"
#include "rx80_counter_table.h"
int main(void)
{
    assert(rx_rate_hz(0)==40000000u && rx_rate_div(0)==6);
    assert(rx_rate_hz(1)==48000000u && rx_rate_div(1)==5);
    assert(rx_rate_hz(2)==80000000u && rx_rate_div(2)==3);
    for(unsigned mode=0;mode<3;++mode) {
        test_hz=rx_rate_hz(mode);
        assert(240000000u/rx_rate_div(mode)==test_hz);
        assert(rx_rate_observer_hz(test_hz)==(mode==2 ? 40000000u : test_hz));
        assert(PICTURE_MIN_LOW_PAIRS==test_hz/1000000u);
        assert(PICTURE_LINE_PAIRS_X10==(uint64_t)12711*test_hz/40000000u);
    }
    uint16_t lut[1024]={0};
    for(unsigned i=0;i<256;++i) lut[i]=rx80_counter_phase[i];
    for(unsigned a=0;a<256;++a) for(unsigned b=0;b<256;++b) {
        unsigned state=0;
        unsigned ref=(rx80_counter_phase[b]-rx80_counter_phase[a]+RX80_COUNTER_BIAS)&63;
        assert(picture_decode(lut,0,&state,a,b)==ref);
    }
    for(unsigned i=0;i<256;++i) lut[i]=rx80_hold40_phase[i];
    for(unsigned a=0;a<256;++a) for(unsigned b=0;b<256;++b) {
        unsigned state=0;
        assert(picture_decode(lut,1,&state,a,b)==
               ((rx80_hold40_phase[b]-rx80_hold40_phase[a]+24u)&63u));
        assert(picture_decode(lut,2,&state,a,b)==
               ((rx80_hold40_phase[b]-rx80_hold40_phase[a]+RX80_HOLD40_BIAS)&63u));
    }
    /* A held40 FM line with negative excursions inside the sync tip.
     * The old output window wraps them into white; B32 must retain sync
     * without those inversions at the same gain/cadence and raw IQ. */
    test_hz=80000000u;
    uint8_t raw[8190];
    double angle=.3;
    for(unsigned k=0;k<sizeof(raw)/2;++k) {
        unsigned pos=(k+2542u-128u)%2542u;
        double hz=pos<188 ? (pos%11u==0 ? -3900000. : -2914000.) : pos<400 ? -1000000. : 2000000.;
        angle+=6.283185307179586*hz/40000000.;
        int i=(int)floor(6.2*cos(angle)),q=(int)floor(6.2*sin(angle));
        raw[2*k]=raw[2*k+1]=(uint8_t)(((unsigned)i&15u)<<4|((unsigned)q&15u));
    }
    picture_state_t old={0},fixed={0};
    (void)picture_sync_window(&old,raw,sizeof(raw),lut,1);
    (void)picture_sync_window(&fixed,raw,sizeof(raw),lut,2);
    picture_sync_t before=picture_sync_window(&old,raw,sizeof(raw),lut,1);
    picture_sync_t after=picture_sync_window(&fixed,raw,sizeof(raw),lut,2);
    unsigned white_before=0,white_after=0;
    for(unsigned k=0;k+1<sizeof(raw)/2;++k) {
        if ((k+1+2542u-128u)%2542u>=188u) continue;
        unsigned state=0;
        white_before+=picture_decode(lut,1,&state,raw[2*k],raw[2*k+2])>40u;
        white_after+=picture_decode(lut,2,&state,raw[2*k],raw[2*k+2])>40u;
    }
    printf("sync-excursion regression: sync old=%u fixed=%u, white inversions old=%u fixed=%u\n",
           before.found,after.found,white_before,white_after);
    assert(after.found && white_before && !white_after);
    puts("PASS rate clock bindings, video timing and all 65536 counter picture pairs");
}

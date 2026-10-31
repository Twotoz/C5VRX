/* C5VRX by Twotoz/contributors: IQ80 observer timing against synthetic RF
 * levels, with native 12.5-ns phase accumulation before exact decimation of
 * the full 16380-byte capture span to the 3.2-line IQ40 AFC window. */
#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "rx_rate.h"
#include "afc.h"
#include "phase8_gain_lut.h"

static uint8_t raw[16384], view[8190], reference[8190];
static void fm80(unsigned period, unsigned start, int polarity, bool burst, int offset)
{
    double phase=.3;
    for(unsigned k=0;k<2*sizeof(reference);++k) {
        unsigned pos=(k+start+period-320u)%period;
        double hz=(pos<376 ? -2914000. : pos<800 || pos>=period-200 ? -1000000. : 2000000.)+offset;
        if(burst && pos>=456 && pos<616)
            hz+=957000.*sin(6.283185307179586*(period==5120 ? 4433618.75 : 3579545.45)*k/80000000.);
        phase+=6.283185307179586*hz/80000000.*-polarity;
        int i=(int)floor(4.2*cos(phase)),q=(int)floor(4.2*sin(phase));
        raw[k]=(uint8_t)(((unsigned)i&15u)<<4|((unsigned)q&15u));
    }
    for(unsigned k=0;k<sizeof(reference);++k) reference[k]=raw[2*k];
}
int main(void)
{
    unsigned accepted=0, trials=0;
    for(unsigned standard=1;standard<=2;++standard)
    for(int polarity=-1;polarity<=1;polarity+=2)
    for(int offset=-1000000;offset<=1000000;offset+=1000000)
    for(unsigned start=0;start<5120;start+=256) {
        fm80(standard==2 ? 5120 : 5084,start,polarity,true,offset);
        rx_rate_decimate2(view,raw,sizeof(view));
        assert(!memcmp(view,reference,sizeof(view)));
        afc2_result_t r=afc2_measure(view,sizeof(view),c5vrx_phase8_gain_lut);
        ++trials;
        if(!r.lines) continue;
        ++accepted;
        assert(r.standard==standard && r.polarity==polarity);
        assert(abs(r.porch_khz-polarity*(1000-offset/1000))<=90);
        assert(abs(abs(r.porch_khz-r.sync_khz)-1914)<=100);
    }
    /* Half a native window (1.6 lines) accepted >=80 %; 3.2 lines must hold all. */
    assert(accepted==trials);
    uint32_t seed=42;
    for(unsigned t=0;t<64;++t) {
        for(unsigned k=0;k<sizeof(raw);++k) {
            seed=seed*1664525u+1013904223u;
            raw[k]=(uint8_t)(seed>>24);
        }
        rx_rate_decimate2(view,raw,sizeof(view));
        assert(!afc2_measure(view,sizeof(view),c5vrx_phase8_gain_lut).lines);
    }
    /* Ring segments: odd counts and unaligned starts take the byte path. */
    for(unsigned shift=0;shift<4;++shift)
        for(size_t count=0;count<9;++count) {
            memset(view,0xA5,sizeof(view));
            rx_rate_decimate2(view,raw+shift,count);
            for(size_t k=0;k<count;++k) assert(view[k]==raw[shift+2*k]);
            assert(view[count]==0xA5);
        }
    printf("PASS IQ80 AFC: %u/%u full 3.2-line colour windows, both standards/polarities, +/-1MHz offsets; 64 noise windows rejected\n",accepted,trials);
}

/* C5VRX by Twotoz/contributors: RX80 headroom/settling/anti-hunt behavior.
 * Synthetic observations establish controller bounds, not RF range gain. */
#include <assert.h>
#include <stdio.h>
#include <math.h>
#include "rx80_agc.h"

static int16_t db[84];
static dg_observation_t at(uint64_t us,unsigned p50,unsigned p95,unsigned clip)
{
    dg_observation_t o={.observed_us=us,.p50=p50,.p95=p95,.clip_pm=clip,.rails=15};
    return o;
}
int main(void)
{
    for(unsigned k=0;k<84;++k) db[k]=(int16_t)(k*256+(k>40?(k-40)*64:0));
    rx80_agc_t a={0};
    uint8_t gain=50;
    dg_observation_t o=at(10000,16,30,0);
    assert(rx80_agc_tick(&a,&o,db,gain,83)==gain);
    o=at(12000,50,90,180);
    uint8_t lower=rx80_agc_tick(&a,&o,db,gain,83);
    assert(lower<gain && db[gain]-db[lower]>=6*256);
    assert(db[gain]-db[lower+1]<6*256);
    gain=lower;
    /* A gain-write transient is not permission to take another step. */
    o=at(12200,50,90,180);
    assert(rx80_agc_tick(&a,&o,db,gain,83)==gain);
    for(uint64_t us=14000;us<212000;us+=200) {
        o=at(us,3,10,0);
        assert(rx80_agc_tick(&a,&o,db,gain,83)==gain);
    }
    o=at(212000,3,10,0);
    uint8_t higher=rx80_agc_tick(&a,&o,db,gain,83);
    assert(higher>gain && db[higher]-db[gain]>=256);
    o.observed_us=20000; /* stale/out-of-order samples cannot write */
    assert(rx80_agc_tick(&a,&o,db,gain,83)==gain);

    /* Scene-dependent short low-envelope/DC-centred probes must not chase
     * the gain up between regular healthy sync/burst observations. */
    a=(rx80_agc_t){0};gain=25;
    for(uint64_t us=1000;us<5000000;us+=200) {
        bool healthy=(us%40000)<1000;
        o=at(us,healthy?15:3,healthy?32:10,0);
        assert(rx80_agc_tick(&a,&o,db,gain,83)==gain);
    }
    assert(!a.attacks && !a.releases);
    /* Measured failure: these unclipped P95 values caused nearly every
     * logged attack across G16..G40. They must stay in the hold band. */
    a=(rx80_agc_t){0};gain=29;
    const unsigned observed_p95[]={45,55,51,45,45,45};
    for(unsigned k=0;k<2000;++k) {
        o=at(1000u+200u*k,33,observed_p95[k%6],0);o.rails=0;
        assert(rx80_agc_tick(&a,&o,db,gain,83)==gain);
    }
    assert(!a.attacks && !a.releases);

    /* A truly sustained weak/absent source still reaches the table maximum
     * in bounded calibrated steps, rather than being frozen for sharpness. */
    a=(rx80_agc_t){0};gain=20;
    for(uint64_t us=1000;us<5000000;us+=200) {
        o=at(us,2,6,0);
        uint8_t next=rx80_agc_tick(&a,&o,db,gain,83);
        assert(next>=gain && next<=83);
        if(next!=gain) assert(db[next]-db[gain]>=256 || next==83);
        gain=next;
    }
    assert(gain==83 && a.releases && a.releases<84);

    /* Closed-loop plant: a coarse up-step used to jump over the entire
     * healthy band. After acquisition, constant input must not hunt. */
    a=(rx80_agc_t){0};gain=83;
    uint8_t min=83,max=0;
    for(uint64_t us=1000;us<3000000;us+=200) {
        double scale=pow(10.,(db[gain]-db[83])/2560.);
        o=at(us,(unsigned)(18*scale+.5),(unsigned)(70*scale+.5),0);
        gain=rx80_agc_tick(&a,&o,db,gain,83);
        if(us>2000000){if(gain<min)min=gain;if(gain>max)max=gain;}
    }
    assert(max==min && a.attacks<=3);

    /* One-sided DC clipping remains with the existing DC owner. */
    a=(rx80_agc_t){0};gain=40;
    o=at(1000,12,24,100);o.rails=1;
    (void)rx80_agc_tick(&a,&o,db,gain,83);
    o.observed_us=4000;
    assert(rx80_agc_tick(&a,&o,db,gain,83)==gain);
    o=at(5000,80,95,200);o.rails=1;
    assert(rx80_agc_tick(&a,&o,db,gain,83)==gain);
    o=at(6000,80,95,0);o.rails=0;o.span=0;o.repeat_pm=1000;
    assert(rx80_agc_tick(&a,&o,db,gain,83)==gain && !a.attacks);

    /* Actual failure shape: a weak floor row below a static high-DC row.
     * Leave time for the DC owner to correct it instead of cycling gain. */
    a=(rx80_agc_t){0};gain=50;
    bool corrected=false;uint64_t held_since=0;uint8_t last=gain;
    for(uint64_t us=1000;us<2000000;us+=200) {
        if(gain!=last){held_since=us;last=gain;}
        bool dc=gain==53 && !corrected;
        if(dc && us-held_since>=100000u){corrected=true;dc=false;}
        o=at(us,dc?80:2,dc?95:6,0);
        if(dc){o.rails=0;o.span=0;o.repeat_pm=1000;}
        gain=rx80_agc_tick(&a,&o,db,gain,83);
    }
    assert(corrected && gain>53 && !a.attacks);
    /* An unmanaged floor row may rise only in bounded steps toward the
     * calibrated DC region, and never from a recent real-video context. */
    a=(rx80_agc_t){.dc_floor=54,.carrier_recent=true};gain=53;
    o=at(1000,80,95,0);o.rails=0;o.span=0;o.repeat_pm=1000;
    (void)rx80_agc_tick(&a,&o,db,gain,83);
    o.observed_us=100000;
    assert(rx80_agc_tick(&a,&o,db,gain,83)==gain);
    a.carrier_recent=false;
    o.observed_us=101000;
    assert(rx80_agc_tick(&a,&o,db,gain,83)==gain);
    o.observed_us=162000;
    assert(rx80_agc_tick(&a,&o,db,gain,83)==54 && !a.attacks);
    /* Board shape: a strong carrier clipping on one side loses its video
     * evidence. That absence must not walk the gain up into the clipping. */
    a=(rx80_agc_t){.dc_floor=54};gain=40;
    for(uint64_t us=1000;us<2000000;us+=200) {
        o=at(us,40,90,300);o.rails=1;
        assert(rx80_agc_tick(&a,&o,db,gain,83)==gain);
    }
    assert(!a.releases);
    puts("PASS RX80 AGC: calibrated attack, settling, stale rejection, no scene chasing, bounded weak-source release, DC ownership");
}

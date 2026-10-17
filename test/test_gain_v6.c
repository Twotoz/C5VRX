/* C5VRX by Twotoz/contributors: experimental V6 decision contracts.
 * Links actual V5 measurement/decision code. Model fixtures test control
 * boundaries, not counterfactual RF/decoded-video quality. */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include "gain_v6_policy.h"
#include "phase8_gain_lut.h"
unsigned char phy_param[2048];
static arc_gain_table_t table;
static direct_gain_t map;
static dg6_input_t input(unsigned gain)
{
    dg6_input_t in = {0};
    in.o = (dg_observation_t){.p50=20,.p90=26,.p95=30,.origin_pm=5,.span=10,.observed_us=100000};
    in.owner = in.dc_verified = in.sync_confirmed = true;
    in.map_context_verified = true;
    in.now_us = 100000; in.last_write_us = 90000;
    in.expected.chain = (dc_trim_context_t){.gain=gain,.gain_epoch=1,.control=2,.phy=3,.table=4,
        .frequency=5865,.filter_code=8,.filter_skirt=32,.bw20=1,.lane=2,.scale=1,
        .packed_gain=map.tuple[gain].packed_state};
    in.expected.dc_pair[0] = in.expected.dc_pair[1] = 256;
    in.measured = in.expected; in.actual_packed = in.expected.chain.packed_gain;
    return in;
}
static void advance(dg6_input_t *in, unsigned dt) { in->now_us += dt; in->o.observed_us += dt; }
static dg6_t enabled(void) { dg6_t s; dg6_init(&s); s.enabled=true; return s; }
static void seed_map(void)
{
    arc_gain_table_from_bytes(&table, NULL, ARC_5G_GAIN_MAX); dg_reset(&map,&table,59);
    for (unsigned k=0;k<=table.max_index;++k) {
        map.confidence[k]=4; map.uncertainty_pm[k]=100; map.relative_power_q10[k]=1024;
    }
    map.relative_power_q10[60]=2048;
}
static void start(dg6_t *s, dg6_input_t *in)
{
    *s=enabled(); *in=input(59); in->o.p50=8; in->o.p95=18;
    assert(dg6_decide(s,&map,in,60).action==DG6_APPLY);
    dg6_context_t actual=in->expected; actual.chain.gain=60; ++actual.chain.gain_epoch;
    actual.chain.packed_gain=map.tuple[60].packed_state;
    assert(dg6_applied(s,true,&actual,in->now_us));
    in->expected=in->measured=actual; in->actual_packed=actual.chain.packed_gain;
    in->last_write_us=in->now_us; advance(in,600);
}
static void contracts(void)
{
    seed_map(); dg6_input_t in=input(59); dg6_t s; dg6_init(&s);
    assert(!s.enabled && dg6_decide(&s,&map,&in,83).action==DG6_V5);
    s=enabled();
    for (unsigned k=0;k<1000;++k) { assert(dg6_decide(&s,&map,&in,83).action==DG6_HOLD); advance(&in,200); }
    assert(!s.awaiting_apply && !s.pending);
    /* Every context component, code, timestamp and tuple must match. */
    for (unsigned k=0;k<sizeof(in.measured.chain)/sizeof(uint32_t);++k) {
        dg6_input_t bad=in; uint32_t words[sizeof(bad.measured.chain)/sizeof(uint32_t)];
        memcpy(words,&bad.measured.chain,sizeof(words)); ++words[k]; memcpy(&bad.measured.chain,words,sizeof(words));
        assert(!dg6_fresh(&bad));
    }
    dg6_input_t bad=in; ++bad.measured.dc_pair[0]; assert(!dg6_fresh(&bad));
    bad=in; ++bad.actual_packed; assert(!dg6_fresh(&bad));
    bad=in; bad.now_us+=DG6_MAX_AGE_US+1; assert(!dg6_fresh(&bad));
    bad=in; bad.o.observed_us=bad.last_write_us+499; assert(!dg6_fresh(&bad));
    bad=in; bad.owner=false; assert(!dg6_fresh(&bad));
    bad=in; bad.now_us=bad.o.observed_us-1; assert(!dg6_fresh(&bad));
    /* G59-style fixed DC: large P50/coherence can never grant carrier. */
    in=input(59); in.o.repeat_pm=900; in.o.span=1; in.o.coherence=100;
    assert(dg6_decide(&s,&map,&in,83).action==DG6_DIAGNOSE_DC);
    in=input(59); in.dc_verified=false; assert(dg6_classify(&s,&in)==DG6_UNKNOWN);
    in.sync_confirmed=false; in.dc_verified=true; assert(dg6_classify(&s,&in)==DG6_UNKNOWN);
    in.noise_confirmed=true; assert(dg6_classify(&s,&in)==DG6_NOISE);
    /* Genuine recent healthy carrier protects 1-10ms weak intervals. */
    s=enabled(); in=input(59); assert(dg6_decide(&s,&map,&in,59).classification==DG6_CARRIER);
    in.o.p50=2; in.o.span=3; in.sync_confirmed=false; in.noise_confirmed=true;
    for (unsigned us=1000;us<=10000;us+=1000) {
        in.now_us=in.o.observed_us=100000+us;
        assert(dg6_decide(&s,&map,&in,83).reason==DG6_FADE_HOLD);
    }
    advance(&in,1); assert(dg6_classify(&s,&in)==DG6_NOISE);
    /* Low confidence/preset priors cannot authorize ordinary changes. */
    s=enabled(); in=input(59); in.o.p50=8; in.o.p95=18;
    map.confidence[60]=1; assert(dg6_decide(&s,&map,&in,60).reason==DG6_MODEL);
    map.confidence[60]=4; map.uncertainty_pm[60]=300;
    assert(dg6_decide(&s,&map,&in,60).reason==DG6_MODEL);
    map.uncertainty_pm[60]=100;
    in.map_context_verified=false; assert(dg6_decide(&s,&map,&in,60).reason==DG6_MODEL);
    in.map_context_verified=true;
    /* A fresh board has no measured map: do not mistake conservatism for
     * a working acquisition algorithm. It must remain default OFF. */
    direct_gain_t empty; memset(&empty,0,sizeof(empty)); dg_reset(&empty,&table,59);
    assert(dg6_decide(&s,&empty,&in,60).reason==DG6_MODEL);
    assert(dg6_decide(&s,&map,&in,60).action==DG6_APPLY);
    assert(!dg6_applied(&s,false,NULL,in.now_us));
    assert(!s.pending && dg6_decide(&s,&map,&in,60).reason==DG6_COOLDOWN);
    /* Fresh improved IQ accepts once; bad readback never starts validation. */
    start(&s,&in); in.o.p50=17; in.o.p95=30;
    assert(dg6_decide(&s,&map,&in,60).reason==DG6_IMPROVED && !s.pending);
    start(&s,&in); in.o.p50=17; in.o.p95=30; in.sync_confirmed=false;
    assert(dg6_decide(&s,&map,&in,60).reason==DG6_VERIFY && s.pending);
    /* Harmful transition: two DISTINCT windows, rollback, penalty, cooldown. */
    start(&s,&in); in.o.p50=50; in.o.p95=70;
    assert(dg6_decide(&s,&map,&in,60).reason==DG6_VERIFY);
    assert(dg6_decide(&s,&map,&in,60).reason==DG6_VERIFY && s.bad_windows==1);
    advance(&in,200); dg6_decision_t d=dg6_decide(&s,&map,&in,60);
    assert(d.action==DG6_ROLLBACK && d.target==59 && s.penalty[60]==1);
    start(&s,&in); advance(&in,DG6_VALIDATE_US);
    assert(dg6_decide(&s,&map,&in,60).reason==DG6_TIMEOUT);
    /* An independent gain/filter/PHY/DC change cancels, never rolls it back. */
    start(&s,&in); ++in.expected.chain.control; in.measured=in.expected;
    assert(dg6_decide(&s,&map,&in,60).reason==DG6_CONTEXT && !s.pending);
    start(&s,&in); ++in.expected.dc_pair[1]; in.measured=in.expected;
    assert(dg6_decide(&s,&map,&in,60).reason==DG6_CONTEXT && !s.pending);
    /* Fast safety inherits actual V5 1ms burst qualification, even DC unknown. */
    direct_gain_t v5; memset(&v5,0,sizeof(v5)); dg_reset(&v5,&table,70);
    s=enabled(); in=input(70); in.dc_verified=false; in.o.p50=100; in.o.p95=110; in.o.clip_pm=700;
    for (unsigned us=0;us<1000;us+=200) {
        uint8_t p=dg_tick(&v5,&in.o); assert(p==70);
        assert(dg6_decide(&s,&map,&in,p).action==DG6_HOLD); advance(&in,200);
    }
    uint8_t p=dg_tick(&v5,&in.o); d=dg6_decide(&s,&map,&in,p);
    assert(d.action==DG6_SAFETY && d.target==p && p<70);
    puts("PASS: default OFF; exact context/freshness; healthy zero-write; DC/fade classes; map confidence; validated apply/rollback; V5 fast safety");
}
static int capture(const char *path, unsigned gain)
{
    seed_map(); FILE *f=fopen(path,"rb"); if (!f || gain>83) return 2;
    uint8_t raw[256]; unsigned count=0,classes[6]={0},actions[6]={0},v5_writes=0;
    while (fread(raw,sizeof(raw),1,f)==1) {
        dg6_input_t in=input(gain); in.now_us=in.o.observed_us=100000+count*200;
        in.o=dg_measure(raw,sizeof(raw),c5vrx_phase8_gain_lut,in.now_us);
        /* Capture headers do not prove synchronized DCO or semantic state. */
        in.dc_verified=in.sync_confirmed=in.noise_confirmed=in.map_context_verified=false;
        direct_gain_t v5; memset(&v5,0,sizeof(v5)); dg_reset(&v5,&table,(uint8_t)gain);
        uint8_t proposal=dg_tick(&v5,&in.o); v5_writes+=proposal!=gain;
        dg6_t s=enabled(); dg6_decision_t d=dg6_decide(&s,&map,&in,proposal);
        ++classes[d.classification]; ++actions[d.action]; ++count;
    }
    fclose(f);
    printf("{\"windows\":%u,\"v5_independent_proposals\":%u,\"unknown\":%u,\"stuck\":%u,\"overload\":%u,\"dc_diagnoses\":%u,\"v6_applies\":%u}\n",
           count,v5_writes,classes[DG6_UNKNOWN],classes[DG6_STUCK],classes[DG6_OVERLOAD],actions[DG6_DIAGNOSE_DC],actions[DG6_APPLY]);
    return 0;
}
int main(int argc,char **argv)
{
    if (argc==4 && !strcmp(argv[1],"--capture")) return capture(argv[2],(unsigned)strtoul(argv[3],NULL,10));
    if (argc!=1) return 2;
    contracts(); return 0;
}

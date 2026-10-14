/* C5VRX by Twotoz/contributors: passive AFC time/provenance bookkeeping.
 * Actual production controller; diagnostics cannot change any decisions. */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "afc_ctrl.h"
static afc2_result_t sample(int residual)
{
    return (afc2_result_t){.lines=8,.standard=2,.polarity=-1,.sync_pairs=200,.porch_pairs=300,
        .porch_khz=-1000+residual,.sync_khz=-4000+residual};
}
static void differential(void)
{
    afc2_ctrl_t plain={.target_khz=-1000}, timed=plain;
    afc2_ctrl_reset(&plain,1,true); afc2_ctrl_reset(&timed,1,true);
    for (unsigned w=0;w<1600;++w) {
        afc2_result_t r=sample((int)(w%49)*31-600);
        if (w%13==0) memset(&r,0,sizeof(r));
        if (w%89==0) r.standard=1;
        if (w%97==0) r.polarity=1;
        if (w%151==0) { afc2_ctrl_sync(&plain,w,w%2); afc2_ctrl_sync(&timed,w,w%2); }
        if (w%17==0) { afc2_ctrl_miss(&plain); afc2_ctrl_miss(&timed); }
        else { afc2_ctrl_observe(&plain,&r); afc2_ctrl_observe_at(&timed,&r,50000ull*(w+1)); }
        afc2_ctrl_t a=plain,b=timed;
        memset(a.observed_us,0,sizeof(a.observed_us)); memset(b.observed_us,0,sizeof(b.observed_us));
        assert(!memcmp(&a,&b,sizeof(a)));
        (void)afc2_ctrl_evidence(&timed,50000ull*(w+1));
        int32_t sa=0,sb=0;
        assert(afc2_ctrl_can_lock(&plain,true)==afc2_ctrl_can_lock(&timed,true));
        bool da=afc2_ctrl_decide(&plain,w%3,&sa), db=afc2_ctrl_decide(&timed,w%3,&sb);
        assert(da==db && sa==sb);
    }
}
int main(void)
{
    differential();
    afc2_ctrl_t c={.target_khz=-1000}; afc2_ctrl_reset(&c,1,true);
    uint64_t now=100000;
    for (unsigned k=0;k<16;++k) { afc2_result_t r=sample(k%2 ? 120 : 100); afc2_ctrl_observe_at(&c,&r,now); now+=50000; }
    afc2_evidence_t e=afc2_ctrl_evidence(&c,now);
    assert(e.n==16 && e.timestamped==16 && e.stable && e.median_khz==110 && e.mad_khz==10);
    assert(e.oldest_us==100000 && e.newest_us==850000);
    assert(afc2_evidence_age_ms(e.oldest_us,now)==800 && afc2_evidence_age_ms(e.newest_us,now)==50);
    assert(afc2_evidence_age_ms(0,now)==-1 && afc2_evidence_age_ms(now+1,now)==-1);
    afc2_result_t invalid={0}; afc2_ctrl_observe_at(&c,&invalid,now);
    e=afc2_ctrl_evidence(&c,now+50000); assert(e.newest_us==850000 && e.n==16);
    afc2_result_t r=sample(110);
    afc2_ctrl_observe_at(&c,&r,now);
    e=afc2_ctrl_evidence(&c,now); assert(e.oldest_us==150000 && e.newest_us==now);
    /* Contradicting standard/polarity restarts aligned timestamp history. */
    r.standard=1; afc2_ctrl_observe_at(&c,&r,now+1);
    e=afc2_ctrl_evidence(&c,now+1); assert(e.n==1 && e.timestamped==1 && e.oldest_us==now+1 && !e.stable);
    r.polarity=1; afc2_ctrl_observe_at(&c,&r,now+2);
    e=afc2_ctrl_evidence(&c,now+2); assert(e.n==1 && e.oldest_us==now+2);
    /* Context and own-retune resets discard time history; own budget rules
     * and pending sign judgement remain exactly those of the controller. */
    c.corrections=2; c.sign_check=true;
    afc2_ctrl_sync(&c,2,true); e=afc2_ctrl_evidence(&c,now);
    assert(e.n==0 && !e.oldest_us && c.corrections==2 && c.sign_check);
    afc2_ctrl_sync(&c,3,false); assert(c.corrections==0 && !c.sign_check);
    /* Legacy untimed callers never manufacture valid age/coverage. */
    r=sample(220); afc2_ctrl_observe(&c,&r); e=afc2_ctrl_evidence(&c,now);
    assert(e.n==1 && e.timestamped==0 && !e.oldest_us && !e.newest_us);
    afc2_ctrl_invalidate(&c); now=100000;
    for (unsigned k=0;k<16;++k) { afc2_ctrl_observe_at(&c,&r,now); now+=50000; }
    for (unsigned sec=0;sec<10;++sec) {
        for (unsigned miss=0;miss<19;++miss) { afc2_ctrl_miss(&c); now+=50000; }
        afc2_ctrl_observe_at(&c,&r,now); now+=50000;
    }
    e=afc2_ctrl_evidence(&c,now);
    assert(e.n==16 && e.timestamped==16 && e.stable && afc2_evidence_age_ms(e.oldest_us,now)>10000);
    for (unsigned k=0;k<=AFC2_CTRL_MAX_MISSES;++k) afc2_ctrl_miss(&c);
    e=afc2_ctrl_evidence(&c,now); assert(!e.n && !e.oldest_us && !e.newest_us);
    printf("PASS: 1600 differential decisions unchanged; aligned ages/MAD/coverage, contradiction, own/context reset, sparse-old and missing evidence; controller=%zu bytes\n",sizeof(c));
}

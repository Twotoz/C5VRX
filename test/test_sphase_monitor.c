/* C5VRX by Twotoz/contributors: no blind sampling adjustment on noise/floor. */
#include <assert.h>
#include <stdio.h>
#include "sphase_monitor.h"
static sphase_monitor_sample_t sample(unsigned id, uint64_t time, int excess)
{
    return (sphase_monitor_sample_t){.context={1,2,3,4,5,6}, .sample_id=id, .observed_us=time,
        .sampling_epoch=9, .phase=0, .excess=excess, .reference_best=35,
        .valid=true,.original=true,.porch_verified=true,.carrier=true,.dc_valid=true,.batch_stable=true,
        .timing_reference_valid=true,.reference_contrast=true};
}
static void prepare(sphase_monitor_t *m)
{
    sphase_monitor_init(m,true,true);
    sphase_monitor_sample_t a=sample(1,1000,300), b=sample(2,2000,290);
    assert(sphase_monitor_observe(m,&a,1000)==SPM_NONE);
    assert(sphase_monitor_observe(m,&b,2000)==SPM_APPLY_ONE);
    assert(m->attempts==1 && m->requested_phase==1);
}
int main(void)
{
    sphase_monitor_t m; sphase_monitor_sample_t s;
    sphase_monitor_init(&m,false,false);
    s=sample(1,1000,40); assert(sphase_monitor_observe(&m,&s,1000)==SPM_NONE && !m.attempts);
    s=sample(2,2000,300); assert(sphase_monitor_observe(&m,&s,2000)==SPM_NONE);
    s=sample(3,3000,290); assert(sphase_monitor_observe(&m,&s,3000)==SPM_NONE && m.state==SPM_BLOCKED);
    for(unsigned reason=0;reason<10;++reason) {
        sphase_monitor_init(&m,true,true); s=sample(1,1000,300);
        switch(reason) {
        case 0:s.original=false;break; case 1:s.carrier=false;break; case 2:s.dc_valid=false;break;
        case 3:s.porch_verified=false;break; case 4:s.clip_pm=51;break; case 5:s.origin_pm=500;break;
        case 6:s.repeat_pm=500;break; case 7:s.batch_stable=false;break;
        case 8:s.reference_contrast=false;break; case 9:s.timing_reference_valid=false;break;
        }
        assert(sphase_monitor_observe(&m,&s,1000)==SPM_NONE);
        ++s.sample_id; s.observed_us=2000;
        assert(sphase_monitor_observe(&m,&s,2000)==SPM_NONE && !m.attempts);
    }
    sphase_monitor_init(&m,true,true); s=sample(1,1000,300);
    assert(sphase_monitor_observe(&m,&s,1000)==SPM_NONE);
    assert(sphase_monitor_observe(&m,&s,2000)==SPM_NONE && m.strikes==1); /* same packet */
    s=sample(2,2000,300); ++s.context.gain;
    assert(sphase_monitor_observe(&m,&s,2000)==SPM_NONE && !m.attempts);
    prepare(&m); assert(sphase_monitor_actuator_done(&m,true,true,true,10,2010)==SPM_NONE);
    s=sample(3,3000,35); s.phase=1;s.sampling_epoch=10;
    assert(sphase_monitor_observe(&m,&s,3000)==SPM_KEEP && m.moved==1);
    s=sample(4,4000,300);s.phase=1;s.sampling_epoch=10;
    assert(sphase_monitor_observe(&m,&s,4000)==SPM_NONE && m.attempts==1);
    prepare(&m); assert(sphase_monitor_actuator_done(&m,true,true,true,10,2010)==SPM_NONE);
    s=sample(3,3000,280); s.phase=1;s.sampling_epoch=10;
    assert(sphase_monitor_observe(&m,&s,3000)==SPM_RESTORE_ORIGINAL && m.requested_phase==0);
    assert(sphase_monitor_actuator_done(&m,true,true,true,11,3100)==SPM_NONE && m.rollbacks==1);
    prepare(&m); assert(sphase_monitor_actuator_done(&m,false,true,true,10,2010)==SPM_RESTORE_ORIGINAL);
    assert(sphase_monitor_actuator_done(&m,false,true,true,11,2020)==SPM_STOP && m.state==SPM_FAULT);
    prepare(&m); assert(sphase_monitor_actuator_done(&m,false,true,true,10,2010)==SPM_RESTORE_ORIGINAL);
    assert(sphase_monitor_actuator_done(&m,true,true,true,10,2020)==SPM_STOP); /* reused epoch */
    prepare(&m); assert(sphase_monitor_actuator_done(&m,true,true,true,10,2010)==SPM_NONE);
    s=sample(3,7000,35);s.phase=1;s.sampling_epoch=10;
    assert(sphase_monitor_observe(&m,&s,7000)==SPM_RESTORE_ORIGINAL); /* late proof */
    prepare(&m); s=sample(3,3000,35); ++s.context.transport;
    assert(sphase_monitor_observe(&m,&s,3000)==SPM_STOP); /* no old rollback into new context */
    sphase_monitor_init(&m,true,true); s=sample(1,1000,300);
    assert(sphase_monitor_observe(&m,&s,200000)==SPM_NONE && !m.strikes);
    sphase_monitor_init(&m,true,true); s=sample(1,1000,300);s.sampling_epoch=UINT32_MAX-1u;
    assert(sphase_monitor_observe(&m,&s,1000)==SPM_NONE);
    s.sample_id=2;s.observed_us=2000;
    assert(sphase_monitor_observe(&m,&s,2000)==SPM_NONE && m.state==SPM_BLOCKED && !m.attempts);
    puts("PASS: sampling evidence, noise/floor/DC/clip/context guards, one candidate, benefit, rollback and deadlines");
}

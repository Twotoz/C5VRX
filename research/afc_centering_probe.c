/* C5VRX by Twotoz/contributors: actual AFC controller monitoring contracts.
 * Synthetic measurement-boundary scenarios, not RF or thermal measurements.
 * Preserves control.c acquisition-only retuning and >=6dB evidence gate. */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "afc_ctrl.h"
#include "cn_estimate.h"
typedef struct {
    afc2_ctrl_t afc;
    bool lock;
    uint8_t lost;
    unsigned corrections, weak_skips;
    int offset;
} model_t;
static afc2_result_t measurement(int error)
{
    return (afc2_result_t){.lines=8,.standard=2,.polarity=-1,.sync_pairs=200,.porch_pairs=300,
        .porch_khz=-1000+error,.sync_khz=-4000+error};
}
static bool tick(model_t *m, bool copied, bool valid, int error, int cn_x10, bool context_same)
{
    afc2_result_t r=measurement(error);
    if (copied && valid) afc2_ctrl_observe(&m->afc,&r); else afc2_ctrl_miss(&m->afc);
    bool was=m->lock;
    if (copied) m->lock=afc2_native_lock(m->lock,valid,afc2_ctrl_can_lock(&m->afc,true),m->afc.n,&m->lost);
    if (was && !m->lock) afc2_ctrl_reacquire(&m->afc);
    int32_t step=0;
    if (!m->lock && valid) {
        bool strong=cn_x10>=AFC_MIN_CN_X10;
        if (afc2_ctrl_decide(&m->afc,context_same && strong,&step)) {
            m->offset+=step; ++m->corrections; return true;
        }
        if (!strong && afc2_ctrl_stable(&m->afc,&step) && (step>50 || step<-50)) ++m->weak_skips;
    }
    return false;
}
static model_t initial(void)
{
    model_t m={0}; m.afc.target_khz=-1000; afc2_ctrl_reset(&m.afc,1,true); return m;
}
int main(void)
{
    model_t m=initial(); int32_t median=0;
    for (unsigned w=0;w<16;++w) tick(&m,true,true,0,150,true);
    assert(m.lock);
    /* Strong valid video stays locked even if thermally drifting. Monitor
     * residual; do not silently reinterpret this as authority to retune. */
    for (unsigned w=0;w<600;++w) tick(&m,true,true,w<500 ? (int)w*2 : 1000,150,true);
    assert(m.lock && m.corrections==0 && afc2_ctrl_stable(&m.afc,&median) && median==1000);
    printf("thermal_locked: valid_windows=600 residual_khz=%ld corrections=%u sticky_lock=%u\n",(long)median,m.corrections,m.lock);
    /* Failed copies cannot manufacture source loss. Four observed invalid
     * windows can drop lock; history is retained through that short fade. */
    for (unsigned w=0;w<10;++w) tick(&m,false,false,0,150,true);
    assert(m.lock && m.afc.n==16);
    for (unsigned w=0;w<4;++w) tick(&m,true,false,0,150,true);
    assert(!m.lock && m.afc.n==16);
    printf("fade: missing_copy_windows=10 lock_retained=1 invalid_windows=4 lock_lost=1 estimates_retained=%u\n",m.afc.n);
    /* At the weak edge even a completely stable porch bias passes MAD.
     * The independent strong-evidence gate is what prevents a bad step. */
    m=initial();
    for (unsigned w=0;w<32;++w) tick(&m,true,true,220,40,true);
    assert(!m.lock && m.corrections==0 && afc2_ctrl_stable(&m.afc,&median) && median==220);
    printf("weak_bias: stable_bias_khz=%ld cn_x10=40 corrections=%u weak_skips=%u\n",(long)median,m.corrections,m.weak_skips);
    assert(tick(&m,true,true,220,60,true));
    assert(m.offset==220 && m.corrections==1);
    printf("biased_input_marked_strong: corrections=%u step_khz=%d physical_correctness=UNKNOWN\n",m.corrections,m.offset);
    /* A good stable median from an old gain/context must never step. */
    m=initial();
    for (unsigned w=0;w<32;++w) tick(&m,true,true,700,150,false);
    assert(m.corrections==0);
    afc2_ctrl_sync(&m.afc,2,false); assert(m.afc.n==0);
    /* The one-second miss budget is consecutive, not an absolute history
     * age bound: sparse evidence can keep a full old estimate window. */
    m=initial();
    for (unsigned w=0;w<16;++w) tick(&m,true,true,220,40,true);
    for (unsigned second=0;second<10;++second) {
        for (unsigned w=0;w<19;++w) afc2_ctrl_miss(&m.afc);
        afc2_result_t r=measurement(220); afc2_ctrl_observe(&m.afc,&r);
    }
    assert(m.afc.n==16 && afc2_ctrl_stable(&m.afc,&median));
    printf("sparse_evidence: elapsed_ms=10000 n=%u stable=1 oldest_estimate_age_ms_at_least=10000\n",m.afc.n);
    puts("PASS: actual AFC controller sticky drift, observed-vs-missing loss, biased porch gate, stale context and sparse-history limits");
}

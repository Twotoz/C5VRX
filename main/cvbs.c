/* C5VRX by Twotoz/contributors: native CVBS level restoration.
 * Replays one bounded control snapshot through the ACTUAL P64 table to
 * measure emitted levels, rather than treating an AFC phase estimate as the
 * physical output. It never paces or modifies the 40 MS/s data plane.
 * One fit per channel/offset session; signal loss holds the last fit.
 */
#include "receiver.h"
#include "cvbs.h"
#include "p64_tables.h"
#include "cn_estimate.h"
#include "phy.h"
#include "esp_timer.h"
#include <stdio.h>
#include <stdlib.h>

static bool apply(const cvbs_fit_t *fit)
{
    if(!phy_try_actuator(phy_generation()))return false;
    bool current=!fit->valid || (fit->channel==rf_channel_index() && fit->offset_khz==rf_offset_khz() &&
                                !calibration_in_startup() && !s_menu_active && s_gain.state==DG_HOLD);
    bool ok=current && transport_set_cvbs_fit(fit);phy_end_actuator();return ok;
}
static struct {
    uint32_t context, windows, refusals, updates;
    int64_t since,last_window_us;
    int channel, offset;
    unsigned standard;
    bool initialized, fitted, reset_pending;
    cvbs_refs_t refs;
    cvbs_fit_t applied;
    const char *held;
} s={.held="waiting_for_native_video"};

/* No histogram percentile is presented as guaranteed future peak white.
 * Source truth labels are never used in firmware. Last native H's inner
 * tip and burst-free porch positions come from the existing AFC detector. */
static bool measure(const uint8_t *raw,size_t n,const afc2_result_t *r,cvbs_refs_t *out)
{
    if(!raw || n<512u || r->level_start<100u || r->level_end<=r->level_start+40u ||
       r->level_end+AFC2_PORCH_START+AFC2_PORCH_LEN>=n)return false;
    unsigned hist[64]={0}, tip_hist[64]={0}, ns=0,np=0,state=0;
    int64_t tip=0,porch=0;
    for(size_t k=0;k+3u<n;k+=2u) {
        unsigned a=((raw[k]>>4)|(raw[k]<<4))&255u;
        a|=(raw[k+1u]&128u)<<1;a|=(raw[k+1u]&8u)<<6;
        uint16_t word=p64_table[(state<<4)|(p64_table[a]>>12)];
        state=(word>>6)&63u;
        unsigned code=word&63u;size_t position=k+2u;
        if(position>=100u)++hist[code];
        if(position>=r->level_start+AFC2_TIP_GUARD && position<r->level_end-AFC2_TIP_GUARD) {
            tip+=dac_loaded_uv(code);++ns;++tip_hist[code];
        }
        if(position>=r->level_end+AFC2_PORCH_START && position<r->level_end+AFC2_PORCH_START+AFC2_PORCH_LEN) {
            porch+=dac_loaded_uv(code);++np;
        }
    }
    if(ns<40u || np<12u)return false;
    unsigned total=0,sum=0,peak=63u;
    for(unsigned c=0;c<64u;++c)total+=hist[c];
    for(unsigned c=0;c<64u;++c){sum+=hist[c];if(sum*1000u>=total*999u){peak=c;break;}}
    unsigned tip_sum=0,floor=0;
    for(unsigned c=0;c<64u;++c){tip_sum+=tip_hist[c];if(tip_sum*100u>=ns){floor=c;break;}}
    *out=(cvbs_refs_t){(int32_t)(tip/ns),(int32_t)(porch/np),dac_loaded_uv(peak),dac_loaded_uv(floor)};
    return true;
}

void cvbs_observe(const uint8_t *raw,size_t n,const afc2_result_t *r,bool usable,uint32_t context)
{
    int channel=(int)rf_channel_index(),offset=rf_offset_khz();
    const bool strong=usable && receiver_obs_cn_x10()>=100 && s_obs_clip_pm<=10 &&
                      s_gain.state==DG_HOLD && !s_menu_active && !calibration_in_startup() && !transport_faulted();
    if(!s.initialized || channel!=s.channel || offset!=s.offset ||
       (strong && r && r->standard && s.standard && r->standard!=s.standard)) {
        s.reset_pending=s.reset_pending || s.fitted;
        s.initialized=true;s.channel=channel;s.offset=offset;s.fitted=false;s.since=0;s.windows=0;
        s.held="new_channel_offset_or_standard";
        s.standard=strong && r?r->standard:0u;
        /* Clear the previous VTX's fit via the same guarded transaction.
         * A refused reset is retried; never qualify new data before it. */
        memset(&s.applied,0,sizeof(s.applied));
    }
    if(transport_output()!=OUTPUT_AUTO_CVBS){s.since=0;s.windows=0;s.held="manual_output";return;}
    if(s.reset_pending) {
        const cvbs_fit_t identity={0};
        if(!apply(&identity)){s.held="reset_waits_for_shared_guard";return;}
        s.reset_pending=false;
    }
    if(s.fitted){s.held="frozen_this_session";return;}
    const int64_t now=esp_timer_get_time();
    cvbs_refs_t ref;
    if(!strong || !r || r->polarity!=-1 || !r->standard || !measure(raw,n,r,&ref)) {
        s.since=0;s.windows=0;++s.refusals;s.held="unqualified_or_busy";return;
    }
    if(!s.since || now-s.last_window_us>150000 || context!=s.context || r->standard!=s.standard || abs(ref.sync_uv-s.refs.sync_uv)>20000 ||
       abs(ref.porch_uv-s.refs.porch_uv)>20000) {
        s.since=now;s.context=context;s.standard=r->standard;s.windows=0;s.refs=ref;
    }
    s.last_window_us=now;
    if(ref.floor_uv<s.refs.floor_uv)s.refs.floor_uv=ref.floor_uv;
    if(ref.peak_uv>s.refs.peak_uv)s.refs.peak_uv=ref.peak_uv;
    ++s.windows;s.held="confirming_2s";
    if(now-s.since<2000000 || s.windows<24u)return;
    cvbs_fit_t fit;
    if(!cvbs_make_fit(s.refs,r->standard==2u,&fit)) {
        s.since=0;s.windows=0;++s.refusals;s.held="invalid_level_headroom";return;
    }
    fit.channel=(uint8_t)channel;fit.offset_khz=(int16_t)offset;
    if(!apply(&fit)){s.held="restart_guard_or_failure";return;}
    s.applied=fit;s.fitted=true;++s.updates;s.held="frozen_this_session";
    cvbs_print();
}
void cvbs_print(void)
{
    printf("CVBS_LEVEL mode=%s fitted=%u held=%s windows=%lu updates=%lu refusals=%lu "
           "source_tip_uv=%ld source_porch_uv=%ld source_floor_uv=%ld peak_guard_uv=%ld planned_depth_uv=%ld headroom_limited=%u "
           "electrical=nominal_200_parallel_75 scope_verified=0\n",
           transport_output()==OUTPUT_AUTO_CVBS?"AUTO":"manual",s.fitted,s.held,(unsigned long)s.windows,
           (unsigned long)s.updates,(unsigned long)s.refusals,(long)s.applied.sync_uv,(long)s.applied.porch_uv,
           (long)s.applied.floor_uv,(long)s.applied.peak_uv,(long)s.applied.depth_uv,s.applied.headroom_limited);
}

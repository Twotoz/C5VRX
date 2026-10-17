/* C5VRX by Twotoz/contributors. Raw comparator flag timing at20MHz.
 * This source can merge H and burst. It is NOT the DATA4 pulse grammar.
 * Missing post-EQs are absence of evidence, never synthesized observations.
 * Call reset on clock/PHY/source changes; timestamps must share one clock. */
#pragma once
#include "sync_native_track.h"

typedef struct {
    sync_native_track_t *native;
    uint64_t pre[7];
    uint8_t pre_count,post_mask;
    bool bad;
} sync_native_flag_track_t;

static inline void sync_native_flag_reset(sync_native_flag_track_t *f)
{
    sync_native_track_t *s=f->native;
    memset(f,0,sizeof(*f));f->native=s;sync_native_reset(s);
}
static inline void sync_native_flag_init(sync_native_flag_track_t *f,sync_native_track_t *s)
{ memset(f,0,sizeof(*f));f->native=s;sync_native_reset(s); }
static inline uint32_t snft_half(unsigned slot,unsigned n)
{ return n==5u?slot*640u:(slot*5720u+4u)/9u; }
static inline bool snft_grid(uint64_t tick,uint64_t anchor,unsigned slot,unsigned n)
{ return tick>=anchor && snt_near(tick-anchor,snft_half(slot,n),24u); }
static inline void snft_partial_clear(sync_native_flag_track_t *f)
{
    f->native->have_broad=false;f->native->post=false;
    f->native->broad_n=0u;f->pre_count=f->post_mask=0u;f->bad=false;
}
static inline void snft_finish(sync_native_flag_track_t *f,uint64_t delimiter)
{
    sync_native_track_t *s=f->native;
    unsigned n=s->broad_n,selected=0u,observed=0u;
    bool complete=!f->bad && (n==5u || n==6u) && f->pre_count==n;
    if(complete) {
        for(unsigned i=0u;i<n;++i) {
            uint32_t ago=snft_half(n-i,n);
            if(s->broad_start<f->pre[i] || !snt_near(s->broad_start-f->pre[i],ago,24u))complete=false;
            if(f->post_mask&(1u<<i))++observed;
        }
        complete=complete && observed>=n-2u &&
            (snft_grid(delimiter,s->broad_start,2u*n,n) ||
             snft_grid(delimiter,s->broad_start,2u*n+1u,n));
    }
    uint8_t standard=n==5u?2u:1u;
    bool fresh=complete && snt_h_for_v(s,standard,&selected);
    if(fresh && s->have_group && s->group_n==n && s->broad_start>s->group &&
       snt_near(s->broad_start-s->group,standard==2u?400000u:333667u,400u)) {
        s->valid=true;s->standard=standard;s->field=s->broad_start;
        s->h=s->candidates[selected].last;
        s->selected_line_ticks_x32=s->candidates[selected].line_ticks_x32;
        s->have_selected=true;++s->qualified_fields;
    }
    s->have_group=fresh;s->group=s->broad_start;s->group_n=(uint8_t)n;
    snft_partial_clear(f);
}
static inline void sync_native_flag_high(sync_native_flag_track_t *f,uint64_t tick,uint32_t width)
{
    sync_native_track_t *s=f->native;
    if(s->seen && tick<=s->last) {sync_native_flag_reset(f);return;}
    if(s->seen && tick-s->last>SNT_H_MAX_GAP_TICKS) {
        /* Idle is not a clock discontinuity. Retain a completed V group,
         * but require independently reacquired H and a whole new V train. */
        memset(s->candidates,0,sizeof(s->candidates));s->have_h=false;
        s->have_selected=false;snft_partial_clear(f);
    }
    s->seen=true;s->last=tick;
    /* A first-broad field interval is <=400400 ticks. Its second train
     * may finish up to13 halfslots later; do not expire it at the delimiter.
     * The actual broad-to-broad comparison in finish remains +/-400. */
    if(s->have_group && tick>s->group && tick-s->group>400400u+13u*640u)s->have_group=false;
    if(s->valid && tick>s->field && tick-s->field>810000u)s->valid=false;
    if(width>=76u && width<=200u) {
        if(s->have_broad)snft_finish(f,tick);
        f->pre_count=0u;snt_h(s,tick);return;
    }
    if(width>=520u && width<=580u) {
        if(!s->have_broad) {
            s->have_broad=true;s->broad_start=tick;s->broad_n=1u;
            f->post_mask=0u;f->bad=false;
        } else {
            if(s->post || !snt_near(tick-s->broad,638u,8u))f->bad=true;
            if(s->broad_n<255u)++s->broad_n;
        }
        s->broad=tick;return;
    }
    if(width>=32u && width<=70u) {
        if(s->have_broad) {
            unsigned n=s->broad_n;
            s->post=true;
            if(n!=5u && n!=6u){f->bad=true;return;}
            bool matched=false;
            for(unsigned i=0u;i<n;++i)if(snft_grid(tick,s->broad_start,n+i,n)) {
                uint8_t bit=(uint8_t)(1u<<i);
                if(f->post_mask&bit)f->bad=true;
                f->post_mask|=bit;matched=true;break;
            }
            if(!matched)f->bad=true;
        } else {
            if(f->pre_count && tick-f->pre[f->pre_count-1u]>700u)f->pre_count=0u;
            if(f->pre_count<7u)f->pre[f->pre_count++]=tick;
            else {memmove(f->pre,f->pre+1,6u*sizeof(f->pre[0]));f->pre[6]=tick;}
        }
    }
}

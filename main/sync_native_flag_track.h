/* C5VRX by Twotoz/contributors. Comparator acquisition at20MHz.
 * Acquisition is evidence; missing evidence never erases an acquired clock.
 * Reset on PHY/source/clock discontinuities. All timestamps share one clock.
 * Thresholds describe eroded raw comparator runs, NOT analogue sync widths. */
#pragma once
#include "sync_native_track.h"

typedef struct {
    sync_native_track_t *native;
    uint64_t pre[7];
    int16_t tail[6];
    uint16_t occupancy;
    uint8_t pre_count,post_mask,slots,expected;
    bool bad,group_shifted;
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
{ return tick>=anchor && snt_near(tick-anchor,snft_half(slot,n),64u); }
static inline void snft_partial_clear(sync_native_flag_track_t *f)
{
    f->native->have_broad=false;f->native->post=false;
    f->native->broad_n=0u;f->post_mask=f->slots=f->expected=0u;
    f->occupancy=0u;f->bad=false;
}
/* At most6 values: fixed insertion sort, no allocation or unbounded search. */
static inline int snft_tail_median(const sync_native_flag_track_t *f)
{
    int16_t a[6];unsigned k=0u;
    for(unsigned i=0u;i<6u;++i)if(f->slots&(1u<<i))a[k++]=f->tail[i];
    for(unsigned i=1u;i<k;++i) {
        int16_t v=a[i];unsigned j=i;
        while(j && a[j-1u]>v){a[j]=a[j-1u];--j;}a[j]=v;
    }
    return k?a[k/2u]:0;
}
/* Unlike DATA4, comparator leading edges move when sync/burst merge.
 * Match H phase against the consensus trailing-derived V anchor. Refuse
 * two surviving H phases even if both happen to lie on a halfline grid. */
static inline bool snft_h_for_v(sync_native_track_t *s,uint8_t standard,uint64_t anchor,unsigned *selected)
{
    bool found=false;uint32_t phase=0u,period=0u;
    for(unsigned i=0u;i<SNT_H_CANDIDATES;++i) {
        const snt_h_candidate_t *c=&s->candidates[i];
        if(!c->used || c->standard!=standard || anchor<c->fit ||
           anchor-c->fit>=100000u || anchor<c->last ||
           anchor-c->last>16u*1290u)continue;
        uint32_t line=(c->line_ticks_x32+16u)>>5;
        uint32_t rem=(uint32_t)(anchor-c->last)%line;
        unsigned half=line/2u;
        if(rem>80u && line-rem>80u && (rem>half?rem-half:half-rem)>80u)continue;
        if(found) {
            uint32_t distance=rem>phase?rem-phase:phase-rem;
            if(distance>24u && period-distance>24u)return false;
        } else {found=true;phase=rem;period=line;*selected=i;}
    }
    return found;
}
static inline void snft_finish(sync_native_flag_track_t *f,uint64_t delimiter)
{
    sync_native_track_t *s=f->native;
    unsigned n=f->expected,observed=0u,pre=0u,post=0u,good=0u,selected=0u;
    bool complete=!f->bad && (n==5u || n==6u);
    int median=snft_tail_median(f);
    uint64_t anchor=s->broad_start;
    if(median<0) {if(anchor<(unsigned)-median)complete=false;else anchor-=(unsigned)-median;}
    else anchor+=(unsigned)median;
    for(unsigned i=0u;i<n;++i)if(f->slots&(1u<<i)) {
        ++observed;int d=f->tail[i]-median;if(d<0)d=-d;if(d<=64)++good;
    }
    /* >=70% of the expected broad region is actually HIGH. Missing one
     * halfslot is permitted, but never invented as an observed pulse. */
    complete=complete && observed>=n-1u && good>=n-1u &&
        (unsigned)f->occupancy*10u>=snft_half(n,n)*7u;
    uint8_t pre_mask=0u;
    for(unsigned i=0u;i<f->pre_count;++i)
        for(unsigned j=1u;j<=n;++j)if(anchor>=f->pre[i] &&
           snt_near(anchor-f->pre[i],snft_half(j,n),64u)) {
            pre_mask|=(uint8_t)(1u<<(j-1u));break;
        }
    for(unsigned i=0u;i<n;++i){if(pre_mask&(1u<<i))++pre;if(f->post_mask&(1u<<i))++post;}
    complete=complete && pre>=2u && post>=2u && delimiter>anchor &&
        delimiter-anchor>=snft_half(2u*n,n)-80u &&
        delimiter-anchor<=snft_half(2u*n+4u,n)+80u;
    uint8_t standard=n==5u?2u:1u;
    bool fresh=complete && snft_h_for_v(s,standard,anchor,&selected);
    bool shifted=false;
    if(fresh) {
        const snt_h_candidate_t *c=&s->candidates[selected];
        uint32_t age=(uint32_t)(anchor-c->last);
        uint32_t line=c->line_ticks_x32;
        unsigned halves=(age*64u+line/2u)/line;
        anchor=c->last+(halves*line+32u)/64u;
        if(s->valid && s->have_selected) {
            shifted=standard!=s->standard;
            if(anchor>=s->h && anchor-s->h<=UINT32_MAX/64u) {
                uint32_t elapsed=(uint32_t)(anchor-s->h);
                uint32_t old_line=s->selected_line_ticks_x32;
                unsigned half=(elapsed*64u+old_line/2u)/old_line;
                uint32_t expected=(half*old_line+32u)/64u;
                uint32_t error=elapsed>expected?elapsed-expected:expected-elapsed;
                shifted=shifted || error>64u;
            } else shifted=true;
        }
    }
    if(fresh && (!shifted || f->group_shifted) && s->have_group && s->group_n==n && anchor>s->group &&
       snt_near(anchor-s->group,standard==2u?400000u:333667u,400u)) {
        s->valid=true;s->standard=standard;s->field=anchor;
        s->h=s->candidates[selected].last;
        s->selected_line_ticks_x32=s->candidates[selected].line_ticks_x32;
        s->line_ticks_x32=s->selected_line_ticks_x32;
        s->have_selected=true;++s->qualified_fields;
    }
    if(fresh){s->have_group=true;s->group=anchor;s->group_n=(uint8_t)n;f->group_shifted=shifted;}
    snft_partial_clear(f);f->pre_count=0u;
}
/* The generic acquisition helper drops selected H after a long absence.
 * A retained clock instead needs a fresh multi-line fit before accepting a
 * same-phase return. One coincidental pulse cannot revive H evidence. */
static inline void snft_observe_h(sync_native_flag_track_t *f,uint64_t tick)
{
    sync_native_track_t *s=f->native;
    bool selected=s->have_selected;uint64_t old_h=s->h;
    snt_h(s,tick);
    if(!selected || s->h==tick)return;
    s->have_selected=true;s->h=old_h;
    uint64_t age=tick-old_h;
    if(age>UINT32_MAX)return;
    uint32_t line=s->selected_line_ticks_x32;
    uint32_t period=(line+16u)>>5;
    if(!period || !line)return;
    uint32_t rem=(uint32_t)age%period;
    bool near_int=rem<=64u || period-rem<=64u;
    uint64_t lines=((uint64_t)age*32u+line/2u)/line;
    uint64_t expected=(lines*line+16u)/32u;
    uint64_t err=age>expected?age-expected:expected-age;
    if(!near_int && err>64u)return;
    for(unsigned i=0u;i<SNT_H_CANDIDATES;++i) {
        const snt_h_candidate_t *c=&s->candidates[i];
        if(c->used && c->standard==s->standard && c->fit==tick) {
            s->h=tick;s->selected_line_ticks_x32=c->line_ticks_x32;return;
        }
    }
}
static inline void sync_native_flag_high(sync_native_flag_track_t *f,uint64_t tick,uint32_t width)
{
    sync_native_track_t *s=f->native;
    if(s->seen && tick<=s->last) {sync_native_flag_reset(f);return;}
    if(s->seen && tick-s->last>SNT_H_MAX_GAP_TICKS) {
        /* Idle removes unfinished evidence, not clock authority. */
        if(tick-s->last>16u*1290u) {
            memset(s->candidates,0,sizeof(s->candidates));s->have_h=false;
        }
        snft_partial_clear(f);f->pre_count=0u;
    }
    s->seen=true;s->last=tick;
    if(s->have_group && tick>s->group && tick-s->group>410000u)s->have_group=false;
    if(width>=250u && width<=620u) {
        if(!s->have_broad) {
            if(!s->h_standard)return;
            s->have_broad=true;s->broad_start=tick;
            f->expected=s->h_standard==2u?5u:6u;
        }
        uint64_t delta=tick-s->broad_start;
        unsigned n=f->expected;
        unsigned slot=(unsigned)((delta+318u)/636u);
        if(slot>=n || delta>5000u){f->bad=true;return;}
        int residual=(int)delta+(int)width-(int)snft_half(slot,n)-541;
        if((f->slots&(1u<<slot)) || residual < -128 || residual>128 || s->post) {
            f->bad=true;return;
        }
        f->slots|=(uint8_t)(1u<<slot);f->tail[slot]=(int16_t)residual;
        f->occupancy=(uint16_t)(f->occupancy+width);
        ++s->broad_n;s->broad=tick;return;
    }
    if(width>=20u && width<=74u) {
        if(s->have_broad) {
            int med=snft_tail_median(f);
            uint64_t anchor=med<0?s->broad_start-(unsigned)-med:s->broad_start+(unsigned)med;
            for(unsigned i=0u;i<f->expected;++i)
                if(snft_grid(tick,anchor,f->expected+i,f->expected)) {
                    f->post_mask|=(uint8_t)(1u<<i);s->post=true;break;
                }
        } else {
            if(f->pre_count && tick-f->pre[f->pre_count-1u]>4000u)f->pre_count=0u;
            if(f->pre_count<7u)f->pre[f->pre_count++]=tick;
            else {memmove(f->pre,f->pre+1,6u*sizeof(f->pre[0]));f->pre[6]=tick;}
            if(width>=56u)snft_observe_h(f,tick);
        }
        return;
    }
    if(width>=75u && width<=240u) {
        if(s->have_broad)snft_finish(f,tick);
        /* Short EQ evidence survives normal H-width aliases until its age
         * rules it out at the V candidate, rather than erasing on any alias. */
        snft_observe_h(f,tick);return;
    }
    if(s->have_broad && tick-s->broad_start>11000u)snft_partial_clear(f);
}

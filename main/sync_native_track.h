/* C5VRX by Twotoz/contributors: native H/V timing qualification at20MHz.
 * Input must exclude private markers and output repair, and share one clock.
 * Requires EQ/broad/EQ structure: an upstream filter that removes EQ cannot
 * qualify. Timing alone does not prove analogue levels or monitor lock. */
#pragma once
#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#define SNT_H_CANDIDATES 8u
typedef struct {
    uint64_t last,start,fit;
    uint32_t line_ticks_x32;
    uint8_t n,standard;
    bool used;
} snt_h_candidate_t;
typedef struct {
    uint64_t last,h,h_start,h_fit,broad,broad_start,group,field;
    uint32_t line_ticks_x32,qualified_fields;
    uint8_t h_n,broad_n,group_n,h_standard,standard;
    bool seen,have_h,have_broad,have_group,valid;
    snt_h_candidate_t candidates[SNT_H_CANDIDATES];
    uint64_t eq_start,eq_last;
    uint32_t selected_line_ticks_x32;
    uint8_t eq_n,pre_n,post_n;
    bool post,have_selected;
} sync_native_track_t;

static inline void sync_native_reset(sync_native_track_t *s)
{ memset(s,0,sizeof(*s)); }
static inline bool snt_near(uint64_t a,uint64_t b,uint64_t tolerance)
{ return a>=b-tolerance && a<=b+tolerance; }

/* H candidates accept only their next line window. Picture lows elsewhere
 * do not erase an established phase. Eight fixed slots bound memory/work;
 * never replace a recent paired candidate merely to accommodate more aliases. */
static inline void snt_h(sync_native_track_t *s,uint64_t tick)
{
    /* Export only the V-selected normal-H phase, never the last arbitrary
     * H-width picture low. Permit the integer number of missing H positions
     * occupied by vertical sync, while preserving that same global H grid. */
    if(s->have_selected) {
        uint64_t age=tick-s->h;
        uint32_t period=(s->selected_line_ticks_x32+16u)>>5;
        if(age>100000u)s->have_selected=false;
        else if(age>=period-24u) {
            uint32_t rem=(uint32_t)age%period;
            if(rem<=24u || period-rem<=24u)s->h=tick;
        }
    }
    bool accepted=false;
    unsigned free_slot=SNT_H_CANDIDATES;
    for(unsigned i=0;i<SNT_H_CANDIDATES;++i) {
        snt_h_candidate_t *c=&s->candidates[i];
        if(c->used && tick-c->last>6000u)c->used=false;
        if(!c->used) {if(free_slot==SNT_H_CANDIDATES)free_slot=i;continue;}
        uint64_t delta=tick-c->last;
        if(!snt_near(delta,1275u,15u))continue;
        accepted=true;c->last=tick;
        if(++c->n==32u) {
            uint64_t fit=tick-c->start;
            c->standard=snt_near(fit,40960u,32u)?2u:
                snt_near(fit,40676u,32u)?1u:0u;
            c->line_ticks_x32=c->standard?(uint32_t)fit:0u;
            c->fit=tick;c->start=tick;c->n=0u;
            if(c->standard) {
                s->h_standard=c->standard;s->h_fit=tick;
                if(!s->have_selected)s->line_ticks_x32=c->line_ticks_x32;
            }
        }
    }
    if(!accepted && free_slot!=SNT_H_CANDIDATES) {
        s->candidates[free_slot]=(snt_h_candidate_t){.last=tick,.start=tick,.used=true};
    }
    s->have_h=true;
}

/* A candidate's phase must agree with the vertical half-line grid. Distinct
 * surviving H phases are ambiguous, including a bright stripe half a line
 * from real H. Refuse that ambiguity instead of selecting the first slot. */
static inline bool snt_h_for_v(sync_native_track_t *s,uint8_t standard,unsigned *selected)
{
    bool found=false;
    uint32_t phase=0u,line=0u,fit=0u;
    for(unsigned i=0;i<SNT_H_CANDIDATES;++i) {
        const snt_h_candidate_t *c=&s->candidates[i];
        if(!c->used || c->standard!=standard || s->broad_start<c->fit ||
           s->broad_start-c->fit>=100000u || s->broad_start<c->last)continue;
        uint64_t age=s->broad_start-c->last;
        if(age>6000u)continue;
        uint32_t period=(c->line_ticks_x32+16u)>>5;
        uint32_t rem=(uint32_t)age%period;
        if(rem>24u && period-rem>24u &&
           (rem>period/2u?rem-period/2u:period/2u-rem)>24u)continue;
        if(found) {
            uint32_t distance=rem>phase?rem-phase:phase-rem;
            if(distance>24u && line-distance>24u)return false;
        } else {found=true;phase=rem;line=period;fit=c->line_ticks_x32;*selected=i;}
    }
    if(found)s->line_ticks_x32=fit;
    return found;
}

/* Called only after a complete post-EQ train. Arbitrary short/picture lows,
 * capture end, and a wrong next broad pulse cannot terminate a valid group. */
static inline void snt_group(sync_native_track_t *s)
{
    uint8_t n=s->broad_n,standard=n==5u?2u:n==6u?1u:0u;
    bool complete=s->have_broad && s->post && standard && s->pre_n==n && s->post_n==n;
    unsigned selected=0u;
    bool fresh=complete && snt_h_for_v(s,standard,&selected);
    if(fresh && s->have_group && s->group_n==n &&
       snt_near(s->broad_start-s->group,standard==2u?400000u:333667u,400u)) {
        s->valid=true;s->standard=standard;s->field=s->broad_start;
        s->h=s->candidates[selected].last;
        s->selected_line_ticks_x32=s->candidates[selected].line_ticks_x32;
        s->have_selected=true;
        ++s->qualified_fields;
    }
    s->have_group=fresh;s->group=s->broad_start;s->group_n=n;
    s->have_broad=false;s->post=false;s->pre_n=s->post_n=0u;
}

static inline void sync_native_low(sync_native_track_t *s,uint64_t tick,uint32_t width)
{
    if(s->seen && tick<=s->last) {sync_native_reset(s);return;}
    if(s->seen && tick-s->last>5000u)sync_native_reset(s);
    s->seen=true;s->last=tick;
    if(s->valid && tick-s->field>810000u)s->valid=false;
    if(width>=76u && width<=112u) {
        /* A normal H closes the post-EQ train. Waiting for this delimiter
         * rejects extra post-EQs instead of qualifying the first N of N+1. */
        if(s->have_broad) {
            snt_group(s);
        }
        s->eq_n=0u;snt_h(s,tick);
    } else if(width>=520u && width<=580u) {
        if(!s->have_broad) {
            s->broad_start=tick;s->broad_n=0u;
            s->pre_n=s->eq_n && snt_near(tick-s->eq_last,638u,8u)?s->eq_n:0u;
        } else if(s->post || !snt_near(tick-s->broad,638u,8u)) {
            s->have_group=false;
            s->broad_start=tick;s->broad_n=0u;s->pre_n=0u;
        }
        s->have_broad=true;s->post=false;s->post_n=0u;s->broad=tick;
        if(s->broad_n<255u)++s->broad_n;
        s->eq_n=0u;
    } else if(width>=38u && width<=56u) {
        if(s->have_broad) {
            uint64_t previous=s->post?s->eq_last:s->broad;
            if(!snt_near(tick-previous,638u,8u)) {
                s->have_broad=s->post=s->have_group=false;
                s->pre_n=s->post_n=0u;
            } else {
                s->post=true;if(s->post_n<255u)++s->post_n;
                s->eq_last=tick;
                return;
            }
        }
        if(!s->eq_n || !snt_near(tick-s->eq_last,638u,8u)) {
            s->eq_start=tick;s->eq_n=0u;
        }
        s->eq_last=tick;if(s->eq_n<255u)++s->eq_n;
    }
    /* Other widths are picture/noise observations, not contradictory H
     * evidence. They also cannot complete an EQ/broad/EQ sequence. */
}

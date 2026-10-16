/* C5VRX by Twotoz/contributors: native H/V timing qualification at 20 MHz.
 * Input must exclude private markers and output repair, and share one clock.
 * This temporal qualifier alone does not prove analogue levels/monitor lock. */
#pragma once
#include <stdbool.h>
#include <stdint.h>
#include <string.h>

typedef struct {
    uint64_t last, h, h_start, h_fit, broad, broad_start, group, field;
    uint32_t line_ticks_x32, qualified_fields;
    uint8_t h_n, broad_n, group_n, h_standard, standard;
    bool seen, have_h, have_broad, have_group, valid;
} sync_native_track_t;

static inline void sync_native_reset(sync_native_track_t *s)
{ memset(s,0,sizeof(*s)); }

static inline bool snt_near(uint64_t a,uint64_t b,uint64_t tolerance)
{ return a>=b-tolerance && a<=b+tolerance; }

/* Finish a broad train only when a later low pulse proves its end. Never
 * classify the truncated train at capture end as a complete V sequence. */
static inline void snt_group(sync_native_track_t *s)
{
    if(!s->have_broad)return;
    uint8_t n=s->broad_n, standard=n==5u?2u:n==6u?1u:0u;
    bool fresh=s->h_standard==standard && s->broad_start>=s->h_fit &&
        s->broad_start-s->h_fit<100000u;
    if(standard && fresh && s->have_group && s->group_n==n &&
       snt_near(s->broad_start-s->group,standard==2u?400000u:333667u,400u)) {
        s->valid=true;s->standard=standard;s->field=s->broad_start;
        ++s->qualified_fields;
    }
    s->have_group=standard && fresh;
    s->group=s->broad_start;s->group_n=n;s->have_broad=false;
}

static inline void sync_native_low(sync_native_track_t *s,uint64_t tick,uint32_t width)
{
    if(s->seen && tick<=s->last) { sync_native_reset(s); return; }
    if(s->seen && tick-s->last>5000u) {
        /* Observation discontinuities cannot bridge evidence. */
        sync_native_reset(s);
    }
    s->seen=true;s->last=tick;
    if(s->valid && tick-s->field>810000u)s->valid=false;
    if(width>=76u && width<=112u) {
        snt_group(s);
        if(s->have_h && snt_near(tick-s->h,1275u,15u)) {
            if(++s->h_n==32u) {
                uint64_t fit=tick-s->h_start;
                uint8_t standard=snt_near(fit,40960u,32u)?2u:
                    snt_near(fit,40676u,32u)?1u:0u;
                s->h_standard=standard;s->h_fit=tick;
                s->line_ticks_x32=standard?(uint32_t)fit:0u;
                if(s->valid && s->standard!=standard)s->valid=false;
                s->h_start=tick;s->h_n=0u;
            }
        } else {s->h_start=tick;s->h_n=0u;}
        s->h=tick;s->have_h=true;
    } else if(width>=520u && width<=580u) {
        s->have_h=false;s->h_n=0u;
        if(!s->have_broad || !snt_near(tick-s->broad,638u,8u)) {
            snt_group(s);s->broad_start=tick;s->broad_n=0u;
        }
        s->have_broad=true;s->broad=tick;
        if(s->broad_n<255u)++s->broad_n;
    } else {
        /* Equalizing pulses delimit broad trains but are not H evidence. */
        snt_group(s);
        s->have_h=false;s->h_n=0u;
    }
}

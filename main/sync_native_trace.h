/* C5VRX by Twotoz/contributors: bounded diagnostic native-low history.
 * These are observations, never camera qualification or output permission. */
#pragma once
#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#define SNT_TRACE_CAPACITY 96u
typedef struct {
    uint32_t tick;          /* Console declares domain: ordinary RMT-local or finite flag common clock. */
    uint16_t width,epoch;   /* epoch is modulo65536, explicitly not absolute time. */
} snt_trace_low_t;
typedef struct {
    snt_trace_low_t lows[SNT_TRACE_CAPACITY];
    uint32_t dropped;
    uint8_t head,count,remaining;
    bool triggered,frozen;
} snt_trace_t;

static inline void snt_trace_reset(snt_trace_t *s)
{ memset(s,0,sizeof(*s)); }

static inline void snt_trace_low(snt_trace_t *s,uint32_t epoch,uint32_t tick,uint32_t width)
{
    if(s->frozen)return;
    if(!width || width>UINT16_MAX){++s->dropped;return;}
    s->lows[s->head]=(snt_trace_low_t){tick,(uint16_t)width,(uint16_t)epoch};
    if(++s->head==SNT_TRACE_CAPACITY)s->head=0;
    if(s->count<SNT_TRACE_CAPACITY)++s->count;
    /* Preserve history before the first broad-width candidate and24 later
     * lows, including the post-EQ area. Triggering does not validate a V. */
    if(!s->triggered && width>=520u && width<=580u) {
        s->triggered=true;s->remaining=24u;
    } else if(s->triggered && --s->remaining==0u)s->frozen=true;
}

/* Flag diagnostics keep structural HIGHs over the whole finite capture, plus
 * four following runs for malformed EQs and the H delimiter. Ordinary Hs do
 * not fill the buffer before the next field arrives. No freeze and no extra
 * allocation: overwrite oldest rows explicitly, counting lost observations.
 * Caller projects starts onto the shared clock AFTER excluding private markers. */
static inline void snt_trace_flag_high(snt_trace_t *s,uint32_t epoch,uint32_t tick,uint32_t width)
{
    if(!width || width>UINT16_MAX){++s->dropped;return;}
    bool structural=width<76u || width>=250u;
    if(!structural && !s->remaining)return;
    if(structural)s->remaining=4u;
    else --s->remaining;
    if(s->count==SNT_TRACE_CAPACITY)++s->dropped;
    s->lows[s->head]=(snt_trace_low_t){tick,(uint16_t)width,(uint16_t)epoch};
    if(++s->head==SNT_TRACE_CAPACITY)s->head=0u;
    if(s->count<SNT_TRACE_CAPACITY)++s->count;
    if(width>=250u)s->triggered=true;
}

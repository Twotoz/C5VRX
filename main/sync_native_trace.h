/* C5VRX by Twotoz/contributors: bounded diagnostic native-low history.
 * These are observations, never camera qualification or output permission. */
#pragma once
#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#define SNT_TRACE_CAPACITY 96u
typedef struct {
    uint32_t tick;          /* RMT-local; compare only inside the same epoch. */
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

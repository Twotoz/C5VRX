/* C5VRX by Twotoz/contributors: camera-period iterator with no division,
 * modulo, allocation, task refill or flash data in its per-pulse path. */
#pragma once
#include "sync_live_gate.h"
#include <limits.h>
typedef struct {
    uint64_t tick,half,field;
    uint32_t whole;
    uint16_t index,halves;
    /* 16-bit fraction: the holdover PLL (sync_clock_pll.h) needs finer
     * than the planner's 1/64-tick halfline. */
    uint16_t remainder,increment,denominator;
    uint8_t parity,section,eq_width;
    bool exhausted;
} slc_t;

/* Initialization is task-side; only this function divides. */
static inline bool slc_init(slc_t *s,const slg_clock_seed_t *seed)
{
    if(!s || !seed || !seed->denominator || seed->h_parity>1u ||
       (seed->standard!=SLG_PAL && seed->standard!=SLG_NTSC))return false;
    uint32_t d=seed->denominator,n=seed->numerator;
    uint64_t q=seed->next_half/d,r=(seed->next_half%d)*n+d/2u;
    if(!n || q>(UINT64_MAX-r/d)/n)return false;
    uint64_t delta=q*n+r/d;
    if(delta>UINT64_MAX-seed->origin)return false;
    uint16_t halves=seed->standard==SLG_PAL?625u:525u;
    *s=(slc_t){.tick=seed->origin+delta,.half=seed->next_half,
        .field=seed->next_half/halves,.index=(uint16_t)(seed->next_half%halves),
        .whole=n/d,.remainder=(uint16_t)(r%d),.increment=(uint16_t)(n%d),
        .denominator=(uint16_t)d,.parity=seed->h_parity,.halves=halves,
        .section=seed->standard==SLG_PAL?5u:6u,.eq_width=seed->standard==SLG_PAL?47u:46u};
    return s->whole>=600u && s->whole<=670u;
}
static inline __attribute__((always_inline)) uint32_t slc_length(const slc_t *s)
{ return s->whole+(s->remainder+s->increment>=s->denominator?1u:0u); }
static inline __attribute__((always_inline)) void slc_advance(slc_t *s)
{
    uint32_t length=slc_length(s);
    if(s->half==UINT64_MAX || s->tick>UINT64_MAX-length){s->exhausted=true;return;}
    s->tick+=length;++s->half;
    unsigned rem=s->remainder+s->increment;
    if(rem>=s->denominator)rem-=s->denominator;
    s->remainder=(uint16_t)rem;
    if(++s->index==s->halves){s->index=0;++s->field;}
}
static inline __attribute__((always_inline)) bool slc_next(slc_t *s,slg_pulse_t *out)
{
    if(!s || !out || s->exhausted)return false;
    if(s->index>=3u*s->section && (s->half&1u)!=s->parity)slc_advance(s);
    if(s->exhausted)return false;
    slg_kind_t kind=s->index<s->section?SLG_EQ:
        s->index<2u*s->section?SLG_BROAD:s->index<3u*s->section?SLG_EQ:SLG_H;
    uint32_t width=kind==SLG_BROAD?slc_length(s)-94u:kind==SLG_EQ?s->eq_width:94u;
    if(s->tick>UINT64_MAX-width){s->exhausted=true;return false;}
    *out=(slg_pulse_t){.start=s->tick,.half=s->half,.field=s->field,.index=s->index,
        .width=width,.kind=kind};
    slc_advance(s);return true;
}

/* C5VRX by Twotoz/contributors: match two filtered marker edges to a
 * free-running timer. Each actual GPIO transition lies inside its BEFORE /
 * AFTER timer reads, including a GPIO readback/fence. The resulting offset
 * maps RMT ticks to the original (pre-filter) native output timeline.
 * These arithmetic bounds do not themselves prove the hardware assumptions. */
#pragma once
#include <stdbool.h>
#include <stdint.h>
#include <limits.h>

typedef struct { int64_t lo,hi; bool valid; } sync_phase_bracket_t;

static inline sync_phase_bracket_t sync_phase_bracket(uint32_t rise,uint32_t fall,
    uint64_t rise_before,uint64_t rise_after,uint64_t fall_before,uint64_t fall_after)
{
    sync_phase_bracket_t r={0};
    if(!rise || fall<=rise || rise_before>rise_after || rise_after>fall_before ||
       fall_before>fall_after || fall_after>(uint64_t)INT64_MAX-2u)return r;
    /* Two ticks cover input/counter quantization at20MHz. Intersect the
     * independent rising/falling constraints rather than adding their widths. */
    int64_t a=(int64_t)rise_before-rise-2, b=(int64_t)rise_after-rise+2;
    int64_t c=(int64_t)fall_before-fall-2, d=(int64_t)fall_after-fall+2;
    int64_t lo=a>c?a:c,hi=b<d?b:d;
    if(lo>hi || hi-lo>64)return r;
    r.lo=lo;r.hi=hi;r.valid=true;return r;
}

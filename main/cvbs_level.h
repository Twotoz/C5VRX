/* C5VRX by Twotoz/contributors: measured CVBS output conditioning.
 * No pixel decoding, synthetic timing, or global fixed multiplier. */
#pragma once
#include <string.h>
#include "output_level.h"
#include "sync_flag.h"
#include "dac_check.h"

typedef struct {
    uint8_t code[64];
    uint64_t clipped;
    int32_t sync_uv, porch_uv, peak_uv, depth_uv, floor_uv;
    int16_t offset_khz;
    uint8_t channel;
    bool valid, headroom_limited;
} cvbs_fit_t;

typedef struct { int32_t sync_uv, porch_uv, peak_uv, floor_uv; } cvbs_refs_t;

static inline unsigned cvbs_nearest_code(int32_t uv)
{
    unsigned best=0; int32_t error=INT32_MAX;
    for(unsigned i=0;i<64u;++i) {
        int32_t e=dac_loaded_uv(i)-uv;if(e<0)e=-e;
        if(e<error){error=e;best=i;}
    }
    return best;
}

/* Linear in nominal LOADED VOLTAGE, with independent tip offset and scale.
 * A dark frame cannot request excess gain: reserve standard 140/40 IRE
 * sync-to-white headroom as well as the greatest observed peak. This reserve
 * is not a guarantee for a nonstandard camera or a future brighter scene.
 * Anchor at the observed lower tip envelope, not its mean: otherwise normal
 * P64 tip ripple would be clipped at zero and shift the mean sync level.
 * The caller must establish stable native H, signal quality and context. */
static inline bool cvbs_make_fit(cvbs_refs_t r, bool pal, cvbs_fit_t *out)
{
    const int32_t span=r.porch_uv-r.sync_uv;
    if(span<50000 || span>500000 || r.sync_uv<0 || r.floor_uv<0 || r.floor_uv>r.sync_uv || r.porch_uv>=900000)return false;
    int32_t peak=r.sync_uv+(pal?span*10/3:span*7/2);
    if(r.peak_uv+32000>peak)peak=r.peak_uv+32000;
    if(peak<=r.porch_uv || peak-r.sync_uv>4000000)return false;
    const int32_t target=pal?300000:286000, tip=0, white=1010000;
    int64_t gain=(int64_t)target*65536/span;
    int64_t cap=(int64_t)(white-tip)*65536/(peak-r.floor_uv);
    if(gain>cap)gain=cap;
    if(gain<16384 || gain>6*65536)return false;
    cvbs_fit_t fit={.sync_uv=r.sync_uv,.porch_uv=r.porch_uv,.peak_uv=peak,
        .floor_uv=r.floor_uv,.depth_uv=(int32_t)(gain*span/65536),.headroom_limited=gain<(int64_t)target*65536/span,.valid=true};
    const int32_t maximum=dac_loaded_uv(63);
    for(unsigned c=0;c<64u;++c) {
        int64_t wanted=tip+gain*(dac_loaded_uv(c)-r.floor_uv)/65536;
        if(wanted<0 || wanted>maximum)fit.clipped|=UINT64_C(1)<<c;
        if(wanted<0)wanted=0;
        if(wanted>maximum)wanted=maximum;
        fit.code[c]=(uint8_t)cvbs_nearest_code((int32_t)wanted);
    }
    *out=fit;return true;
}

static inline uint16_t output_entry_with_fit(uint16_t value,unsigned level,const cvbs_fit_t *fit,bool *clipped)
{
    if(level!=OUTPUT_AUTO_CVBS || !fit || !fit->valid)return output_entry(value,level,clipped);
    unsigned c=value&63u;
    if(clipped)*clipped=((fit->clipped>>c)&1u)!=0;
    return (uint16_t)((value&0xffc0u)|fit->code[c]);
}
static inline uint16_t output_flag_with_fit(uint16_t value,unsigned level,const cvbs_fit_t *fit,bool *clipped)
{
    return (uint16_t)((output_entry_with_fit(value,level,fit,clipped)&~1u)|((value&63u)<SYNC_FLAG_THRESHOLD));
}

/* C5VRX by Twotoz/contributors: private preparation cannot invoke a live
 * executor and must leave its source/guards unchanged. */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "sync_private_bench.h"
#include "phase8_gain_lut.h"
#include "kp_tables.h"
#include "output_level.h"
static unsigned callbacks;
static bool forbidden(void *ctx,uint64_t a,const uint8_t *bytes,uint32_t n)
{ (void)ctx;(void)a;(void)bytes;(void)n;++callbacks;return true; }
int main(void)
{
    uint8_t raw[4096],before[4096],storage[1408];
    uint16_t lut[1024];
    sfw_ring_t r={.ring=raw,.ring_bytes=sizeof(raw),.phase=c5vrx_phase8_gain_lut,
        .verified_kp_x100=85,.program_verified=true,.decoder_lut=lut,
        .workspace=storage+64,.workspace_bytes=1280};
    unsigned checked=0;
    for (unsigned kp=0;kp<KP_LEVELS;++kp) for(unsigned output=0;output<OUTPUT_LEVELS;++output) {
        for(unsigned j=0;j<1024;++j)lut[j]=output_entry(kp_tables[kp][j],output,NULL);
        r.verified_kp_x100=kp_level_x100[kp];
        r.reference_lut=kp_tables[kp];r.verified_output=output;
        assert(sfw_bench_seed_private(&r,12345));memcpy(before,raw,sizeof(raw));
        for(unsigned pal=0;pal<2;++pal)for(unsigned kind=0;kind<4;++kind) {
            memset(storage,0xa5,sizeof(storage));sfw_private_pulse_t pulse;
            assert(sfw_bench_prepare_private(&r,pal,(sfw_bench_kind_t)kind,&pulse));
            assert(pulse.prepared && pulse.start==512 && pulse.bytes==(kind==0?236u:pal?1280u:1271u));
            assert(!pulse.bootstrap_refused);
            assert(!memcmp(raw,before,sizeof(raw)));
            for(unsigned j=0;j<64;++j)assert(storage[j]==0xa5);
            for(unsigned j=64+pulse.bytes;j<sizeof(storage);++j)assert(storage[j]==0xa5);
            ++checked;
        }
    }
    sfw_private_pulse_t p;
    r.commit=forbidden;assert(!sfw_bench_prepare_private(&r,true,SFW_BENCH_H,&p) && !callbacks);r.commit=NULL;
    r.workspace=raw;assert(!sfw_bench_prewarm(&r));r.workspace=storage+64;
    r.workspace_bytes=1279;assert(!sfw_bench_seed_private(&r,0));r.workspace_bytes=1280;
    r.program_verified=false;assert(!sfw_bench_prepare_private(&r,true,SFW_BENCH_H,&p));r.program_verified=true;
    assert(!sfw_bench_prepare_private(&r,true,(sfw_bench_kind_t)-1,&p));
    assert(!sfw_bench_prepare_private(&r,true,(sfw_bench_kind_t)4,&p));
    printf("PASS: %u private whole-pulse preparations, actual output LUTs, no source writes/callbacks/guard corruption\n",checked);
}

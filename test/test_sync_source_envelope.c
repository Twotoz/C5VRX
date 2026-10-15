/* C5VRX by Twotoz/contributors: actual-core source-envelope coverage oracle.
 * Every actual raw phase load must already be covered by an accepted ORIGINAL
 * query. Uses the existing independent PAL/NTSC FM/IQ generator; no writer.
 * Build this TU alone: it includes the actual core with a host-only load hook. */
#define _DEFAULT_SOURCE
#define main original_flywheel_fixture_main
#define pulse_t fixture_pulse_t
#include "test_sync_flywheel.c"
#undef pulse_t
#undef main

static sync_flywheel_t *coverage_detector;
static uint64_t coverage_loads,coverage_turns,coverage_repeated,coverage_pending;
static unsigned coverage_phases,coverage_tracking,coverage_tainted;
static bool coverage_active;
static void coverage_load(sync_read_extent_t *extent,uint64_t k)
{
    assert(coverage_active && extent && !extent->overflow && extent->count);
    /* No derived expected window: inspect what the REAL original() queries
     * admitted before this REAL source load. Removing/narrowing accounting
     * therefore makes this assertion fail at the first uncovered load. */
    assert(k>=extent->first && k<extent->last);
    if(coverage_detector->state==SFW_ACQUIRE)
        coverage_phases|=1u<<coverage_detector->acq_phase;
    else ++coverage_tracking;
    ++coverage_loads;
}
#define SFW_SOURCE_READ_CHECK(extent,k) coverage_load((extent),(k))
#include "../main/sync_flywheel.c"
#undef SFW_SOURCE_READ_CHECK

static uint64_t completed(uint64_t rx,bool balanced)
{
    uint64_t base=rx&~(uint64_t)(RING-1u),offset=rx&(RING-1u),end=0;
    for(unsigned i=0;i<9;++i){
        unsigned bytes=i<7?4092u:balanced?2062u:i==7?4092u:32u;
        if(end+bytes>offset)break;
        end+=bytes;
    }
    return base+end;
}
static void replay(bool pal,unsigned budget,bool balanced,bool noise_only)
{
    uint64_t field=field_samples(pal),n=field*4u;
    scene_t scene={.pal=pal,.amp=noise_only?0:6,.noise=noise_only?2:.025,
        .cfo_mhz=.18,.shift_at=field*2u,.shift=pal?173:-127};
    if(!noise_only){scene.nfade=1;scene.fade[0]=(fade_t){field+field/4u,field*2u+field/4u,0,2,false};}
    uint8_t *source=generate(&scene,n),*ring=calloc(RING,1),before[RING];
    assert(source && ring);
    sync_flywheel_t detector;sfw_init(&detector);coverage_detector=&detector;
    sync_read_extent_t extent={0};
    sfw_ring_t r={.ring=ring,.ring_bytes=RING,.phase=c5vrx_phase8_gain_lut,.read_extent=&extent};
    uint64_t prior_done=0;unsigned acquired=0,tracked=0;
    for(uint64_t rx=0;rx<n;){
        uint64_t next=rx+3200u;if(next>n)next=n;
        for(;rx<next;++rx)ring[rx&(RING-1u)]=source[rx];
        uint64_t done=completed(rx,balanced);
        if(done<RING)continue;
        if(done>prior_done && (coverage_turns%113u)==17u){
            assert(sync_repair_ledger_record(&detector.repairs,done-5000u,done-4500u));
            ++coverage_tainted;
        }
        /* Repeated completion metadata drains pending cursors without any
         * new source bytes. Tiny alternating budgets also force partial pulse
         * and acquire coarse/fine continuation across call boundaries. */
        for(unsigned repeat=0;repeat<3;++repeat){
            extent=(sync_read_extent_t){0};memcpy(before,ring,RING);
            coverage_active=true;
            sfw_run(&detector,&r,done,done,0,false,repeat==1?1u:budget);
            coverage_active=false;
            assert(!s_read_extent); /* no retained stack/owner pointer */
            assert(!memcmp(before,ring,RING));
            if(repeat || done==prior_done)++coverage_repeated;
            if(detector.state==SFW_TRACK){++tracked;if(!extent.count)++coverage_pending;}
            if(detector.acquisitions)acquired=1;
            ++coverage_turns;
        }
        prior_done=done;
    }
    if(!noise_only)assert(acquired && tracked && detector.lines>100u);
    free(source);free(ring);coverage_detector=NULL;
}
int main(void)
{
    for(unsigned pal=0;pal<2;++pal)for(unsigned balanced=0;balanced<2;++balanced)
        for(unsigned budget=52;budget<=64;budget+=12)
            replay(pal!=0,budget,balanced!=0,false);
    replay(true,52,true,true);replay(false,64,false,true);
    assert(coverage_phases==15u && coverage_tracking && coverage_tainted);
    assert(coverage_repeated && coverage_pending && coverage_loads>100000u);
    printf("PASS actual-core ORIGINAL envelope: %llu loads,%llu turns,%llu repeated DONE,%llu pending TRACK; all4 acquire phases, PAL/NTSC fades/return/noise,52/64 budgets, both layouts and tainted wraps\n",
        (unsigned long long)coverage_loads,(unsigned long long)coverage_turns,
        (unsigned long long)coverage_repeated,(unsigned long long)coverage_pending);
    return 0;
}

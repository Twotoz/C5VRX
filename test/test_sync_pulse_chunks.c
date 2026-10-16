/* C5VRX by Twotoz/contributors: actual lowm resumable pulse bridge.
 * Synthetic original IQ and descriptor traces; no live hardware timing claim. */
#define _DEFAULT_SOURCE
#define main fixture_original_main
#define pulse_t fixture_pulse_t
#include "test_sync_flywheel.c"
#undef pulse_t
#undef main

static uint32_t actual_reads;
static uint64_t total_reads, chunks, aborted, completed_cases;
static bool check_coverage;
static void actual_load(sync_read_extent_t *extent, uint64_t k)
{
    if (check_coverage) {
        assert(extent && extent->count && !extent->overflow);
        assert(k >= extent->first && k < extent->last);
    }
}
#define SFW_PHASE_READ() (++actual_reads)
#define SFW_SOURCE_READ_CHECK(e,k) actual_load((e),(k))
#include "../main/sync_flywheel.c"
#undef SFW_PHASE_READ
#undef SFW_SOURCE_READ_CHECK

static const uint32_t quotas[] = {0,3,9,10,13,32,64,128,208,320};

static spf_status_t chunk(sync_flywheel_t *f, const sfw_ring_t *r,
                           spf_t *observation, uint32_t quota)
{
    sync_read_extent_t extent = {0};
    lowm_reset();
    actual_reads = 0;
    s_read_extent = &extent;
    check_coverage = true;
    uint32_t used = UINT32_MAX;
    spf_status_t result = find_pulse_chunk(f,r,observation,quota,&used);
    check_coverage = false;
    s_read_extent = NULL;
    assert(used == actual_reads && used <= quota);
    total_reads += actual_reads;
    ++chunks;
    return result;
}

static void static_case(const uint8_t *source, uint64_t prediction,
                        int window, uint64_t available, int threshold)
{
    uint8_t raw[RING], before[RING];
    uint64_t base = 2u * RING;
    memcpy(raw,source,RING); memcpy(before,raw,RING);
    sfw_ring_t ring = {.ring=raw,.ring_bytes=RING,.phase=c5vrx_phase8_gain_lut};
    sync_flywheel_t reference;
    sfw_init(&reference); reference.thr=(int16_t)threshold;
    reference.evidence_end=base+available;
    lowm_reset(); s_read_extent=NULL; actual_reads=0;
    int expected_edge = 123456;
    uint32_t expected_width = 654321;
    pulse_t expected = find_pulse(&reference,&ring,base+prediction,window,
                                   base+available,&expected_edge,&expected_width);
    for (unsigned q=0;q<sizeof(quotas)/sizeof(quotas[0]);++q) {
        sync_flywheel_t detector;
        sfw_init(&detector); detector.thr=(int16_t)threshold;
        detector.evidence_end=base+available;
        spf_t observation;
        assert(spf_init(&observation,base+prediction,window,base+available));
        uint32_t quota=quotas[q];
        if (quota<10u) {
            spf_t prior=observation;
            assert(chunk(&detector,&ring,&observation,quota)==prior.status);
            assert(memcmp(&prior,&observation,sizeof(prior))==0 && actual_reads==0u);
            quota=10u;
        }
        unsigned turns=0;
        while(observation.status!=SPF_DONE && observation.status!=SPF_ABORTED) {
            spf_pulse_t pulse=SPF_PULSE_BROAD;
            int edge=123456;uint32_t width=654321;
            assert(!spf_result(&observation,&pulse,&edge,&width));
            assert(pulse==SPF_PULSE_BROAD && edge==123456 && width==654321);
            assert(++turns<10000u);
            (void)chunk(&detector,&ring,&observation,quota);
            assert(!memcmp(raw,before,RING));
        }
        assert(observation.status==SPF_DONE);
        assert(!detector.lines && !detector.acquisitions);
        spf_pulse_t pulse;
        int edge=123456;uint32_t width=654321;
        assert(spf_result(&observation,&pulse,&edge,&width));
        assert((pulse_t)pulse==expected && edge==expected_edge && width==expected_width);
        assert(chunk(&detector,&ring,&observation,quota)==SPF_DONE && actual_reads==0u);
        ++completed_cases;
    }
}

static void expiry_cases(const uint8_t *source)
{
    uint8_t raw[RING],before[RING];memcpy(raw,source,RING);memcpy(before,raw,RING);
    sfw_ring_t ring={.ring=raw,.ring_bytes=RING,.phase=c5vrx_phase8_gain_lut};
    for(unsigned reason=0;reason<3;++reason) {
        sync_flywheel_t f;sfw_init(&f);f.thr=-12;f.evidence_end=3u*RING;
        spf_t observation;uint64_t pred=2u*RING+6400u;
        assert(spf_init(&observation,pred,24,3u*RING));
        assert(chunk(&f,&ring,&observation,32u)!=SPF_DONE);
        uint64_t at=observation.prediction+(uint64_t)(int64_t)observation.offset;
        if(observation.status==SPF_WIDTH)at=observation.walk.start+observation.walk.w;
        if(reason==0)assert(sync_repair_ledger_advance(&f.repairs,at+20u));
        else if(reason==1)f.evidence_end=at-20u;
        else assert(sync_repair_ledger_record(&f.repairs,at-20u,at+20u));
        assert(chunk(&f,&ring,&observation,32u)==SPF_ABORTED && actual_reads==0u);
        spf_pulse_t pulse=SPF_PULSE_EQ;int edge=123;uint32_t width=456;
        assert(!spf_result(&observation,&pulse,&edge,&width));
        assert(pulse==SPF_PULSE_EQ && edge==123 && width==456);
        assert(chunk(&f,&ring,&observation,320u)==SPF_ABORTED && actual_reads==0u);
        assert(!memcmp(before,raw,RING));++aborted;
    }
}

static unsigned node_at(const dma_progress_layout_t *l,uint64_t p)
{
    p%=RING;
    for(unsigned j=0;j<9;++j)
        if(p>=l->nodes[j].offset && p<l->nodes[j].offset+l->nodes[j].bytes)return j;
    assert(0);return 0;
}
static sync_done_capture_t capture(const dma_progress_layout_t *l,uint64_t p,uint64_t us)
{
    unsigned i=node_at(l,p);
    return(sync_done_capture_t){.done=l->nodes[(i+8u)%9u].address,
        .bf0=l->nodes[(i+1u)%9u].address,.first_us=us,.last_us=us,
        .context={1,2,3,4},.rx_clock=0x60005u,.tx_clock=0x60005u,
        .memory_rate_bound=192000000u,.live=true,.metadata_owned=true};
}

/* Run one real-source observation while RX advances and overwrites its ring.
 * Actual DONE observer bootstraps freshness, paired DONE+2 captures validate
 * every real load footprint. Lower quota must expire cleanly, never P_NONE. */
static void moving_case(const uint8_t *source,bool pal,uint32_t quota,unsigned us)
{
    dma_progress_layout_t layout={0};uint32_t offset=0;
    for(unsigned j=0;j<9;++j){
        unsigned bytes=j<7?4092u:2062u;
        layout.nodes[j]=(dma_progress_node_t){0x40850000u+16u*j,
            0x40850000u+16u*((j+1u)%9u),offset,bytes};offset+=bytes;
    }
    assert(offset==RING && dma_progress_layout_valid(&layout));
    sync_done_progress_t progress={0};sync_done_observation_t observation;
    uint64_t rx=0,time=1000;
    sync_done_capture_t c=capture(&layout,rx,time);
    assert(sync_done_observe(&progress,&layout,&c,&c,&observation)==SDP_OK);
    /* More than a ring of accepted physical progress, at true40 bytes/us. */
    for(unsigned turn=0;turn<41;++turn){rx+=1600u;time+=40u;c=capture(&layout,rx,time);
        assert(sync_done_observe(&progress,&layout,&c,&c,&observation)==SDP_OK);}
    uint64_t base=4u*RING;
    /* Place the generated first V interval near the newest complete data. */
    uint64_t pred=base+(pal?6400u:7627u);
    while(observation.done_end<pred+1400u){rx+=400u;time+=10u;c=capture(&layout,rx,time);
        assert(sync_done_observe(&progress,&layout,&c,&c,&observation)==SDP_OK);}
    uint8_t raw[RING],before[RING];
    for(uint64_t k=rx-RING;k<rx;++k)raw[k&(RING-1u)]=source[(k+2u*RING-base)&(RING-1u)];
    sfw_ring_t ring={.ring=raw,.ring_bytes=RING,.phase=c5vrx_phase8_gain_lut};
    sync_flywheel_t f;sfw_init(&f);f.thr=-12;
    spf_t pulse;assert(spf_init(&pulse,pred,24,observation.done_end));
    sync_flywheel_t ref;sfw_init(&ref);ref.thr=f.thr;ref.evidence_end=observation.done_end;
    int expected_edge=0;uint32_t expected_width=0;
    lowm_reset();s_read_extent=NULL;
    assert(find_pulse(&ref,&ring,pred,24,observation.done_end,
                        &expected_edge,&expected_width)==P_BROAD);
    unsigned turns=0;bool expired=false;
    while(pulse.status!=SPF_DONE && pulse.status!=SPF_ABORTED){
        assert(++turns<1000u);
        sync_done_capture_t bf=c,bl=c;
        sync_done_observation_t b=observation;
        uint32_t cover;
        assert(sync_read_capture_cover(&layout,&bf,&bl,&b,&cover)==SRV_OK);
        uint64_t oldest=b.done_end+cover>RING?b.done_end+cover-RING:0;
        if(oldest<b.fresh_from)oldest=b.fresh_from;
        assert(sync_repair_ledger_advance(&f.repairs,oldest));
        f.evidence_end=b.done_end;
        sync_read_extent_t extent={0};s_read_extent=&extent;lowm_reset();actual_reads=0;
        check_coverage=true;uint32_t used;
        memcpy(before,raw,RING);
        spf_status_t result=find_pulse_chunk(&f,&ring,&pulse,quota,&used);
        check_coverage=false;s_read_extent=NULL;
        assert(used==actual_reads && used<=quota && !memcmp(before,raw,RING));
        total_reads+=actual_reads;++chunks;
        /* One us actual work here is synthetic; cadence controls next entry. */
        sync_done_capture_t af=capture(&layout,rx+40u,time+1u),al=af;
        sync_done_observation_t a;
        assert(sync_done_observe(&progress,&layout,&af,&al,&a)==SDP_OK);
        sync_read_result_t valid=sync_read_validate(&extent,&layout,&bf,&bl,&b,&af,&al,&a,true);
        if(result==SPF_ABORTED){assert(actual_reads==0u);expired=true;break;}
        assert(valid==SRV_OK);
        uint64_t next=rx+40u*us;
        for(uint64_t k=rx;k<next;++k)raw[k&(RING-1u)]=source[(k+2u*RING-base)&(RING-1u)];
        rx=next;time+=us;c=capture(&layout,rx,time);
        assert(sync_done_observe(&progress,&layout,&c,&c,&observation)==SDP_OK);
    }
    if(expired){assert(pulse.status==SPF_ABORTED);++aborted;}
    else{
        assert(pulse.status==SPF_DONE && pulse.pulse==SPF_PULSE_BROAD);
        assert(pulse.edge==expected_edge && pulse.width==expected_width);
        ++completed_cases;
    }
    assert(!f.lines && !f.acquisitions);
    if((quota==10u || quota==128u) && us==80u)assert(expired);
    if(quota==208u || quota==320u)assert(!expired);
}

int main(void)
{
    for(unsigned pal=0;pal<2;++pal){
        scene_t scene={.pal=pal!=0,.amp=6,.noise=.025,.cfo_mhz=.18};
        uint8_t *source=generate(&scene,RING);assert(source);
        uint64_t broad=pal?6400u:7627u;
        static_case(source,broad,24,RING,-12);
        static_case(source,0u+100u,24,RING,-12);
        static_case(source,pal?25600u:25422u,96,RING,-12);
        static_case(source,broad+2u,96,broad+1000u,-12);
        if(pal)expiry_cases(source);
        moving_case(source,pal!=0,320u,1u);
        moving_case(source,pal!=0,320u,80u);
        moving_case(source,pal!=0,208u,80u);
        moving_case(source,pal!=0,128u,80u);
        moving_case(source,pal!=0,10u,80u);
        free(source);
    }
    uint8_t noise[RING];for(unsigned j=0;j<RING;++j)noise[j]=(uint8_t)(j*73u+j/7u);
    static_case(noise,16000u,96,RING,-12);
    assert(aborted>=5u && completed_cases>=90u);
    printf("PASS actual IQ pulse chunks: %llu exact results, %llu expiry aborts, "
           "%llu chunks/%llu phase loads; quotas0/3/9/10/13/32/64/128/208/320, "
           "cold restarts, original envelopes, moving DONE+2 source, no writes\n",
           (unsigned long long)completed_cases,(unsigned long long)aborted,
           (unsigned long long)chunks,(unsigned long long)total_reads);
    return 0;
}

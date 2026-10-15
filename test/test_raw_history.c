/* C5VRX by Twotoz/contributors: adversarial original-evidence history model.
 * Logical-byte RX overwrite oracle; conditional on actual contiguous prefix,
 * fixed declared readers and cache margin. Not physical DMA publication proof. */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "raw_history.h"
static const uint32_t lengths[9]={4092,4092,4092,4092,4092,4092,4092,2062,2062};
static unsigned tree[RAW_HISTORY_RING+1], marked[RAW_HISTORY_RING];
static void mark(unsigned p,int delta)
{
    marked[p]=(unsigned)((int)marked[p]+delta);
    for (unsigned i=p+1;i<=RAW_HISTORY_RING;i+=i&-i) tree[i]=(unsigned)((int)tree[i]+delta);
}
static unsigned prefix(unsigned end) { unsigned n=0; for (unsigned i=end;i;i-=i&-i) n+=tree[i]; return n; }
static unsigned sum(unsigned first,unsigned last) { return prefix(last)-prefix(first); }
static bool clean(uint64_t first,uint64_t last)
{
    assert(first<=last && last-first<RAW_HISTORY_RING);
    unsigned a=first%RAW_HISTORY_RING,n=(unsigned)(last-first);
    return n<=RAW_HISTORY_RING-a ? sum(a,a+n)==0 : sum(a,RAW_HISTORY_RING)+sum(0,n-(RAW_HISTORY_RING-a))==0;
}
static unsigned active_at(unsigned offset)
{
    unsigned pos=0; for (unsigned i=0;i<9;++i) { if (offset<pos+lengths[i]) return i; pos+=lengths[i]; }
    assert(false); return 0;
}
static uint32_t node_start(unsigned active) { uint32_t n=0; for (unsigned k=0;k<active;++k) n+=lengths[k]; return n; }
static uint64_t previous_source(uint64_t active_begin,unsigned active,unsigned need)
{
    for (unsigned back=1;back<9;++back) {
        unsigned node=(active+9-back)%9; active_begin-=lengths[node];
        if (lengths[node]>=need) return active_begin;
    }
    assert(false); return 0;
}
int main(void)
{
    raw_history_layout_t layout=raw_history_layout(lengths,0);
    assert(layout.valid && layout.newest_long_distance==8216 && layout.protected_bytes==8280);
    /* Discovery rotation preserves exact physical geometry and exclusion. */
    for (unsigned rotation=0;rotation<9;++rotation) {
        uint32_t rotated[9]; for (unsigned k=0;k<9;++k) rotated[k]=lengths[(rotation+k)%9];
        raw_history_layout_t d=raw_history_layout(rotated,node_start(rotation));
        assert(d.valid && d.protected_bytes==8280);
    }
    uint32_t bad_lengths[9]; memcpy(bad_lengths,lengths,sizeof(lengths)); --bad_lengths[8];
    assert(!raw_history_layout(bad_lengths,0).valid);
    assert(!raw_history_layout(lengths,1).valid);
    assert(raw_history_request(RHR_CONTROL,4092) && !raw_history_request(RHR_CONTROL,4093));
    assert(raw_history_request(RHR_PROBE,256) && !raw_history_request(RHR_PROBE,257));
    assert(raw_history_request(RHR_SENTINEL,64) && !raw_history_request(RHR_SENTINEL,65));
    assert(raw_history_request(RHR_SNAPSHOT,8190) && !raw_history_request(RHR_SNAPSHOT,8192));
    assert(raw_history_request(RHR_ENDPOINTS,4092) && !raw_history_request(RHR_ENDPOINTS,SIZE_MAX));
    raw_history_snapshot_t s={.fresh_from=32768,.rx_descriptor_begin=65536,.generation=7,.epoch=3,
        .contiguous_proven=true,.valid=true};
    uint64_t boundary=0;
    assert(raw_history_boundary(&layout,&s,7,3,&boundary) && boundary==((65536-8280)&~63u));
    assert(raw_history_patch(&layout,&s,7,3,boundary-1,boundary));
    assert(raw_history_patch(&layout,&s,7,3,32768,32769)); /* aligned oldest loses no extra64 */
    assert(!raw_history_patch(&layout,&s,7,3,boundary,boundary+1));
    assert(!raw_history_boundary(&layout,&s,8,3,&boundary) && !raw_history_boundary(&layout,&s,7,4,&boundary));
    s.overflow=true; assert(!raw_history_boundary(&layout,&s,7,3,&boundary)); s.overflow=false;
    s.contiguous_proven=false; assert(!raw_history_boundary(&layout,&s,7,3,&boundary)); s.contiguous_proven=true;
    s.fresh_from=s.rx_descriptor_begin-8279; assert(!raw_history_boundary(&layout,&s,7,3,&boundary));
    s.fresh_from=32768; ++s.rx_descriptor_begin; assert(!raw_history_boundary(&layout,&s,7,3,&boundary));
    s.rx_descriptor_begin=UINT64_MAX; assert(!raw_history_boundary(&layout,&s,7,3,&boundary));
    /* Arbitrary relative-epoch freshness origins need cache-ceil too. */
    for (unsigned origin=0;origin<128;++origin) {
        s.rx_descriptor_begin=3ull*RAW_HISTORY_RING;
        s.fresh_from=2ull*RAW_HISTORY_RING+origin;
        uint64_t first,last;
        assert(raw_history_window(&layout,&s,7,3,&first,&last));
        assert(first==((s.fresh_from+63u)&~63ull));
        assert(raw_history_patch(&layout,&s,7,3,first,last));
        if (first>s.fresh_from) assert(!raw_history_patch(&layout,&s,7,3,first-1,first));
    }
    unsigned positions=0,queries=0;
    /* Patch EVERY admitted past/cache byte. Both safe bounds are64aligned;
     * arbitrary subinterval cache rounding remains contained. Advance RX for two
     * full laps; query every actual CPU read envelope after each byte. */
    for (unsigned start_node=0;start_node<9;++start_node) {
        memset(tree,0,sizeof(tree)); memset(marked,0,sizeof(marked));
        uint64_t r=3ull*RAW_HISTORY_RING+node_start(start_node);
        s.fresh_from=RAW_HISTORY_RING; s.rx_descriptor_begin=r;
        assert(raw_history_boundary(&layout,&s,7,3,&boundary));
        uint64_t first,last;
        assert(raw_history_window(&layout,&s,7,3,&first,&last) && last==boundary);
        assert(raw_history_patch(&layout,&s,7,3,first,boundary));
        assert(first>=r-RAW_HISTORY_RING && first-(r-RAW_HISTORY_RING)<64);
        assert((first%64)==0 && (last%64)==0);
        for (unsigned offset=0;offset<64;++offset) {
            assert(raw_history_patch(&layout,&s,7,3,first+offset,last-offset));
        }
        if (first>r-RAW_HISTORY_RING) assert(!raw_history_patch(&layout,&s,7,3,first-1,first));
        for (uint64_t p=first;p<last;++p) mark((unsigned)(p%RAW_HISTORY_RING),1);
        for (unsigned advance=0;advance<2*RAW_HISTORY_RING;++advance) {
            uint64_t q=r+advance; unsigned active=active_at((unsigned)(q%RAW_HISTORY_RING));
            uint64_t end=q-q%RAW_HISTORY_RING+node_start(active);
            uint64_t control=previous_source(end,active,4092), sentinel=previous_source(end,active,2048);
            assert(clean(control,control+4092)); /* all control/probe subsets */
            assert(clean(sentinel+1984,sentinel+2048));
            assert(clean(end-8190,end));
            uint64_t even=end&~1ull; assert(clean(even-8184,even));
            assert(clean(end-8192,end));
            queries+=5; ++positions;
            unsigned physical=(unsigned)(q%RAW_HISTORY_RING);
            if (marked[physical]) mark(physical,-1); /* ONLY real RX overwrites taint */
        }
    }
    printf("PASS: history8216+cache64=8280; %u future RX positions/%u reader queries, two laps/all descriptor starts; limits, startup, rotation, generation, epoch, overflow; conditional model only\n",positions,queries);
}

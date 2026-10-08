#include "cvbs_snapshot.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
int main(void)
{
    uint8_t ring[32768], copy[C5V4_LEVEL_SAMPLE_BYTES];
    for (unsigned i=0;i<sizeof(ring);++i) ring[i]=(uint8_t)(i*31+i/256);
    /* Discovery walks from the active descriptor. Every rotation, including
     * the short tail node, must validate without assuming node0 == ring0. */
    size_t offsets[9], sizes[9];
    for (unsigned k=0;k<9;++k) {
        offsets[k]=k*4092u;
        sizes[k]=sizeof(ring)-offsets[k] < 4092u ? sizeof(ring)-offsets[k] : 4092u;
    }
    for (unsigned rotation=0;rotation<9;++rotation) {
        size_t covered=0;
        for (unsigned k=0;k<9;++k) {
            unsigned index=(rotation+k)%9;
            assert(c5v4_snapshot_segment(sizeof(ring),offsets[rotation],covered,
                                         offsets[index],sizes[index]));
            assert(!c5v4_snapshot_segment(sizeof(ring),offsets[rotation],covered,
                                          offsets[index]+1,sizes[index]));
            covered+=sizes[index];
        }
        assert(covered==sizeof(ring));
        assert(!c5v4_snapshot_segment(sizeof(ring),offsets[rotation],covered,0,1));
    }
    assert(!c5v4_snapshot_segment(sizeof(ring),0,0,0,0));
    assert(!c5v4_snapshot_segment(sizeof(ring),0,0,0,sizeof(ring)+1));
    assert(!c5v4_snapshot_segment(sizeof(ring),0,0,sizeof(ring),1));
    assert(!c5v4_snapshot_segment(sizeof(ring),32760,0,32760,32));
    for (unsigned end=0;end<sizeof(ring);end+=4092) {
        size_t active=sizeof(ring)-end < 4092 ? sizeof(ring)-end : 4092;
        c5v4_snapshot_plan_t p;
        assert(c5v4_snapshot_plan(sizeof(ring),end,active,sizeof(copy),&p));
        memcpy(copy,ring+p.offset,p.first);
        memcpy(copy+p.first,ring,sizeof(copy)-p.first);
        for (unsigned j=0;j<sizeof(copy);++j) {
            assert(copy[j]==ring[(p.offset+j)%sizeof(ring)]);
            size_t at=(p.offset+j)%sizeof(ring);
            assert(at<end || at>=end+active); /* never active RX memory */
        }
        assert(c5v4_snapshot_current(end,end,10,p.safe_bytes,40000000));
        assert(!c5v4_snapshot_current(end,end+1,10,p.safe_bytes,40000000));
        assert(!c5v4_snapshot_current(end,end,51,p.safe_bytes,40000000));
        assert(!c5v4_snapshot_current(end,end,1000,p.safe_bytes,40000000));
    }
    /* Lane-only switches were missing from the gain/PHY timestamp guard. */
    assert(c5v4_level_source_ready(10000,0,0,0,false));
    for (unsigned age=0;age<500;++age) {
        assert(!c5v4_level_source_ready(10000,10000-age,0,0,false));
        assert(!c5v4_level_source_ready(10000,0,10000-age,0,false));
        assert(!c5v4_level_source_ready(10000,0,0,10000-age,false));
    }
    assert(c5v4_level_source_ready(10000,9500,9500,9500,false));
    assert(!c5v4_level_source_ready(10000,9500,9500,9500,true));
    assert(!c5v4_level_source_ready(10000,0,0,10001,false));
    c5v4_snapshot_plan_t p;
    assert(!c5v4_snapshot_plan(8192,0,4092,8190,&p));
    assert(!c5v4_snapshot_plan(32768,32768,32,8190,&p));
    assert(!c5v4_snapshot_plan(32768,32760,32,8190,&p));
    puts("PASS: multi-descriptor snapshot, wrap order, active exclusion and deadline/lap refusal");
}

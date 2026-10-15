/* C5VRX by Twotoz/contributors: raw-reader exclusion across live epochs. */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "raw_access.h"
static void conversions(void)
{
    raw_access_context_t a={1,2,3,4},b;
    for(unsigned slot=0;slot<RAW_ACCESS_READERS;++slot) {
        raw_access_t s={0};raw_access_token_t r[RAW_ACCESS_READERS],out={0},stale;
        raw_access_conversion_t h={0},copy;
        for(unsigned i=0;i<RAW_ACCESS_READERS;++i)assert(raw_access_read_begin(&s,a,true,&r[i]));
        /* Every possible competing reader prevents upgrade, preserving both
         * leases and all tickets. Filling/freeing slots also exercises ABA. */
        for(unsigned other=0;other<RAW_ACCESS_READERS;++other)if(other!=slot){
            raw_access_t before=s;raw_access_token_t original=r[slot];
            assert(!raw_access_read_upgrade(&s,&r[slot],a,true,&h));
            assert(!memcmp(&s,&before,sizeof(s)) && !memcmp(&r[slot],&original,sizeof(original)));
            assert(raw_access_read_end(&s,&r[other],a,true));
        }
        stale=r[slot];uint64_t issued=s.issued;
        assert(raw_access_read_upgrade(&s,&r[slot],a,true,&h));
        assert(!r[slot].ticket && !raw_access_readers(&s));
        assert(s.writer==issued+1 && s.issued==issued+2 && h.resumed_ticket==issued+2);
        assert(h.slot==slot && s.conversion==&h);
        assert(!raw_access_read_begin(&s,a,true,&out));
        assert(!raw_access_write_begin(&s,a,true,&out));
        assert(!raw_access_read_end(&s,&stale,a,true));
        copy=h;assert(!raw_access_read_downgrade(&s,&copy,a,true,&out));
        h.resumed_ticket++;assert(!raw_access_read_downgrade(&s,&h,a,true,&out));h=copy;
        h.context.gain++;assert(!raw_access_read_downgrade(&s,&h,a,true,&out));h=copy;
        h.slot=(uint8_t)((slot+1u)%RAW_ACCESS_READERS);
        assert(!raw_access_read_downgrade(&s,&h,a,true,&out));h=copy;
        raw_access_token_t forged={h.context,h.writer_ticket,0};
        assert(!raw_access_write_end(&s,&forged,a,true));
        assert(!raw_access_read_downgrade(&s,&h,a,true,(raw_access_token_t *)&h));
        assert(s.writer==h.writer_ticket);
        assert(raw_access_read_downgrade(&s,&h,a,true,&r[slot]));
        assert(r[slot].ticket==issued+2 && r[slot].slot==slot && !s.writer && !s.conversion);
        assert(!raw_access_read_downgrade(&s,&copy,a,true,&out));
        assert(!raw_access_read_downgrade(&s,&h,a,true,&out));
        assert(raw_access_read_end(&s,&r[slot],a,true));
        /* Reusing the SAME handle address does not resurrect old tickets. */
        assert(raw_access_read_begin(&s,a,true,&r[slot]));
        assert(raw_access_read_upgrade(&s,&r[slot],a,true,&h));
        raw_access_conversion_t current=h;h=copy;
        assert(!raw_access_read_downgrade(&s,&h,a,true,&out));h=current;
        assert(raw_access_read_downgrade(&s,&h,a,true,&out));
        assert(raw_access_read_end(&s,&out,a,true));
    }
    for(unsigned axis=0;axis<5;++axis){
        raw_access_t s={0};raw_access_token_t r,out={0};raw_access_conversion_t h={0};
        assert(raw_access_read_begin(&s,a,true,&r));b=a;
        switch(axis){case 0:b.stream++;break;case 1:b.control++;break;
            case 2:b.gain++;break;case 3:b.phy++;break;default:break;}
        raw_access_t before=s;
        assert(!raw_access_read_upgrade(&s,&r,b,axis!=4,&h));
        assert(!memcmp(&s,&before,sizeof(s)) && r.ticket);
        raw_access_context_t saved=r.context;r.context=b;
        if(axis!=4)assert(!raw_access_read_upgrade(&s,&r,b,true,&h));
        r.context=saved;
        assert(raw_access_read_upgrade(&s,&r,a,true,&h));
        /* A changed source invalidates result but MUST restore a closeable
         * reader even when no further tickets can ever be issued. */
        s.issued=UINT64_MAX;
        assert(!raw_access_read_downgrade(&s,&h,b,axis!=4,&out));
        assert(out.ticket && raw_access_readers(&s)==1 && !s.writer);
        assert(!raw_access_read_end(&s,&out,b,axis!=4));
        assert(!raw_access_readers(&s));
    }
    for(unsigned remaining=0;remaining<=3;++remaining){
        raw_access_t s={0};raw_access_token_t r,out={0};raw_access_conversion_t h={0};
        assert(raw_access_read_begin(&s,a,true,&r));s.issued=UINT64_MAX-remaining;
        raw_access_t before=s;
        if(remaining<2){assert(!raw_access_read_upgrade(&s,&r,a,true,&h));
            assert(!memcmp(&s,&before,sizeof(s)));assert(raw_access_read_end(&s,&r,a,true));}
        else {assert(raw_access_read_upgrade(&s,&r,a,true,&h));
            assert(h.writer_ticket==UINT64_MAX-remaining+1 && h.resumed_ticket==UINT64_MAX-remaining+2);
            assert(raw_access_read_downgrade(&s,&h,a,true,&out));assert(raw_access_read_end(&s,&out,a,true));}
    }
    {raw_access_t s={0};raw_access_token_t r;raw_access_conversion_t h={0};
     assert(raw_access_read_begin(&s,a,true,&r));raw_access_t before=s;
     assert(!raw_access_read_upgrade(&s,&r,a,true,(raw_access_conversion_t *)&r));
     assert(!raw_access_read_upgrade(&s,&r,a,true,(raw_access_conversion_t *)&s));
     assert(!raw_access_read_upgrade(&s,(raw_access_token_t *)&s,a,true,&h));
     assert(!memcmp(&s,&before,sizeof(s)) && r.ticket);
     assert(!raw_access_disjoint((const void *)(UINTPTR_MAX-1u),4,&s,sizeof(s)));
     assert(raw_access_read_end(&s,&r,a,true));}
    {raw_access_t s={0};raw_access_token_t r,out={0};raw_access_conversion_t h={0};
     assert(raw_access_read_begin(&s,a,true,&r));s.issued=UINT32_MAX-1u;
     s.writer=123;assert(!raw_access_read_upgrade(&s,&r,a,true,&h));
     assert(r.ticket && raw_access_readers(&s)==1);s.writer=0;
     assert(raw_access_read_upgrade(&s,&r,a,true,&h));
     assert(h.writer_ticket==UINT32_MAX && h.resumed_ticket==(uint64_t)UINT32_MAX+1u);
     assert(raw_access_read_downgrade(&s,&h,a,true,&out));
     assert(raw_access_read_end(&s,&out,a,true));}
}
int main(void)
{
    raw_access_t state={0};
    raw_access_context_t a={1,2,3,4}, b=a;
    raw_access_token_t readers[RAW_ACCESS_READERS], writer, stale, extra;
    for (unsigned k=0;k<RAW_ACCESS_READERS;++k)
        assert(raw_access_read_begin(&state,a,true,&readers[k]));
    assert(!raw_access_read_begin(&state,a,true,&extra));
    assert(!raw_access_write_begin(&state,a,true,&writer));
    b.stream++;
    stale=readers[0];
    /* A restart cannot discard readers that still touch the old ring. */
    assert(!raw_access_read_end(&state,&readers[0],b,true));
    assert(raw_access_readers(&state)==RAW_ACCESS_READERS-1);
    assert(raw_access_read_begin(&state,b,true,&extra));
    assert(!raw_access_read_end(&state,&stale,b,true));
    assert(raw_access_readers(&state)==RAW_ACCESS_READERS);
    assert(raw_access_read_end(&state,&extra,b,true));
    for (unsigned k=1;k<RAW_ACCESS_READERS;++k)
        assert(!raw_access_read_end(&state,&readers[k],b,true));
    assert(!raw_access_readers(&state));
    assert(raw_access_write_begin(&state,b,true,&writer));
    stale=writer;
    assert(!raw_access_read_begin(&state,b,true,&extra));
    assert(!raw_access_write_begin(&state,b,true,&extra));
    b.control++;
    assert(!raw_access_write_end(&state,&writer,b,true));
    assert(!state.writer);
    assert(!raw_access_write_end(&state,&stale,b,true));
    for (unsigned axis=0;axis<4;++axis) {
        b=a;
        assert(raw_access_read_begin(&state,a,true,&extra));
        switch(axis) {case 0:++b.stream;break;case 1:++b.control;break;case 2:++b.gain;break;default:++b.phy;}
        assert(!raw_access_read_end(&state,&extra,b,true));
        assert(!raw_access_readers(&state));
    }
    assert(!raw_access_read_begin(&state,a,false,&extra));
    assert(raw_access_read_begin(&state,a,true,&extra));
    assert(!raw_access_read_end(&state,&extra,a,false));
    assert(!raw_access_readers(&state));
    /* This must keep working across the old 32-bit uptime boundary. */
    state.issued=UINT32_MAX;
    assert(raw_access_read_begin(&state,a,true,&extra));
    assert(extra.ticket==(uint64_t)UINT32_MAX+1);
    assert(raw_access_read_end(&state,&extra,a,true));
    state.issued=UINT64_MAX-1;
    assert(raw_access_read_begin(&state,a,true,&extra));
    assert(!raw_access_write_begin(&state,a,true,&writer));
    assert(raw_access_read_end(&state,&extra,a,true));
    assert(!raw_access_read_begin(&state,a,true,&extra));
    assert(!raw_access_write_begin(&state,a,true,&writer));
    conversions();
    puts("PASS: readers/writers plus atomic conversion: all slots, contention, copied handles, ABA, context, aliases and ticket exhaustion");
}

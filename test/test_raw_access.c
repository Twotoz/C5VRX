/* C5VRX by Twotoz/contributors: raw-reader exclusion across live epochs. */
#include <assert.h>
#include <stdio.h>
#include "raw_access.h"
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
    puts("PASS: reader saturation, stale/duplicate tokens, context changes, exclusive writers and 64-bit ticket exhaustion");
}

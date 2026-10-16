/* C5VRX Twotoz/contributors: capture preserves chronology/epoch boundaries;
 * a broad-looking low freezes diagnostics without granting camera validity. */
#include <assert.h>
#include <stdio.h>
#include "sync_native_trace.h"
int main(void)
{
    snt_trace_t s;
    snt_trace_reset(&s);
    for(unsigned i=0;i<200u;++i)snt_trace_low(&s,65535u,i*1280u,94u);
    assert(s.count==96u && s.head==8u && !s.triggered && !s.frozen);
    for(unsigned i=0;i<s.count;++i) {
        const snt_trace_low_t *p=&s.lows[(s.head+i)%SNT_TRACE_CAPACITY];
        assert(p->tick==(104u+i)*1280u && p->epoch==65535u && p->width==94u);
    }
    snt_trace_low(&s,65536u,100u,544u);
    assert(s.triggered && !s.frozen && s.remaining==24u);
    for(unsigned i=0;i<24u;++i)snt_trace_low(&s,65536u,738u+i*638u,47u);
    assert(s.frozen && s.remaining==0u && s.count==96u);
    snt_trace_t copy=s;
    snt_trace_low(&s,65537u,100u,94u);
    assert(!memcmp(&s,&copy,sizeof(s)));
    assert(s.lows[(s.head+95u)%96u].epoch==0u);
    snt_trace_reset(&s);
    snt_trace_low(&s,1u,20u,0u);
    snt_trace_low(&s,1u,20u,65536u);
    assert(s.dropped==2u && !s.count && !s.triggered);
    snt_trace_low(&s,1u,20u,65535u);
    assert(s.count==1u && s.lows[0].width==65535u);
    puts("PASS: bounded native trace chronology, wrap, trigger, freeze and width limits");
    return 0;
}

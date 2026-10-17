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
    snt_trace_reset(&s);
    for(unsigned i=0;i<300u;++i)snt_trace_flag_high(&s,1u,i*1271u,160u);
    assert(!s.count); /* Ordinary H does not crowd out a second field. */
    snt_trace_flag_high(&s,1u,400000u,47u);
    for(unsigned i=0;i<20u;++i)snt_trace_flag_high(&s,1u,401000u+i*1271u,160u);
    assert(s.count==5u && !s.remaining);
    snt_trace_flag_high(&s,2u,733667u,541u);
    for(unsigned i=0;i<20u;++i)snt_trace_flag_high(&s,2u,734667u+i*1271u,160u);
    assert(s.count==10u && s.triggered && !s.frozen);
    assert(s.lows[5].tick-s.lows[0].tick==333667u); /* Common clock survives RX rearm. */
    snt_trace_reset(&s);
    for(unsigned i=0;i<116u;++i)snt_trace_flag_high(&s,3u,10000u+i*638u,541u);
    assert(s.count==96u && s.dropped==20u && !s.frozen);
    assert(s.lows[s.head].tick==10000u+20u*638u);
    snt_trace_flag_high(&s,3u,90000u,65536u);
    assert(s.dropped==21u && s.count==96u);
    puts("PASS: bounded native trace chronology, wrap, trigger, freeze and width limits");
    return 0;
}

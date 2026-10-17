/* C5VRX by Twotoz/contributors: independent source-profile fixtures. */
#include <stdio.h>
#include <stdlib.h>
#include "sync_native_flag_track.h"
static unsigned checks;
#define CHECK(x) do {++checks;if(!(x)){fprintf(stderr,"FAIL line%d: %s\n",__LINE__,#x);exit(1);}}while(0)
static void group(sync_native_flag_track_t *f,uint64_t b,unsigned n,unsigned missing,unsigned defect)
{
    for(unsigned j=defect==8u?0u:48u;j>0;--j)sync_native_flag_high(f,b-snft_half(n+1u+2u*j,n),160u);
    sync_native_flag_high(f,b-snft_half(n+1u,n),160u);
    for(unsigned i=0;i<n;++i)if(!(defect==4u&&i==0u))sync_native_flag_high(f,b-snft_half(n-i,n)+(defect==1u&&i==2u?30u:0u),i==2u?66u:47u);
    for(unsigned i=0;i<n+(defect==2u);++i)sync_native_flag_high(f,b+snft_half(i,n)+(defect==6u&&i==2u?20u:0u),541u);
    for(unsigned i=0;i<n;++i)if(!(missing&(1u<<i)))sync_native_flag_high(f,b+snft_half(n+i,n),48u);
    if(defect==5u)sync_native_flag_high(f,b+snft_half(2u*n,n),48u);
    if(defect==7u)sync_native_flag_high(f,b+snft_half(2u*n-1u,n)+1u,48u);
    sync_native_flag_high(f,b+snft_half(2u*n+(n==6u),n)+(defect==3u?40u:0u),160u);
}
int main(void)
{
    sync_native_track_t native;
    sync_native_flag_track_t f;
    for(unsigned n=5;n<=6;++n) {
        uint64_t period=n==5u?400000u:333667u;
        sync_native_flag_init(&f,&native);
        group(&f,100000u,n,3u<<(n-2u),0u);
        CHECK(native.have_group);CHECK(!native.valid);
        group(&f,100000u+period,n,3u<<(n-2u),0u);
        CHECK(native.valid);CHECK(native.standard==(n==5u?2u:1u));CHECK(native.qualified_fields==1u);
        for(unsigned defect=1u;defect<=8u;++defect) {
            sync_native_flag_reset(&f);
            group(&f,100000u,n,0u,defect);
            group(&f,100000u+period,n,0u,defect);
            CHECK(!native.valid);CHECK(!native.have_group);
        }
        sync_native_flag_reset(&f);
        group(&f,100000u,n,7u,0u);CHECK(!native.have_group);
        group(&f,100000u+period,n,7u,0u);CHECK(!native.valid);
        sync_native_flag_reset(&f);
        group(&f,100000u,n,0u,0u);
        group(&f,100000u+period+401u,n,0u,0u);CHECK(!native.valid);
    }
    /* Real camera log, reconstructed ONLY between adjacent LOWs in epoch2.
     * Frozen~6ms trace contains one V train: never claim two-field lock. */
    static const struct {uint32_t tick,width;} captured[]={{73707u,171u},{74978u,159u},{76248u,157u},{77519u,159u},{78796u,151u},{80079u,141u},{81332u,176u},{82605u,158u},{83875u,157u},{85146u,161u},{86417u,176u},{87689u,158u},{88959u,157u},{90230u,160u},{91503u,156u},{92773u,177u},{94043u,167u},{95315u,160u},{96586u,174u},{97857u,168u},{99128u,162u},{100400u,159u},{101670u,175u},{102942u,164u},{104219u,155u},{105483u,171u},{106764u,171u},{108028u,176u},{109297u,171u},{110568u,171u},{111840u,156u},{113111u,153u},{114382u,163u},{115653u,169u},{116925u,162u},{118194u,161u},{119467u,163u},{120737u,160u},{122008u,164u},{123279u,155u},{124550u,151u},{125820u,172u},{127093u,173u},{128364u,158u},{129635u,172u},{130906u,168u},{132177u,175u},{133447u,160u},{134719u,178u},{135991u,159u},{137262u,157u},{138531u,161u},{139804u,155u},{141075u,164u},{142345u,179u},{143616u,160u},{144889u,162u},{146160u,153u},{147438u,163u},{148701u,154u},{149982u,147u},{151244u,165u},{152520u,167u},{153784u,171u},{155057u,165u},{156328u,214u},{156964u,47u},{157599u,51u},{158235u,56u},{158888u,35u},{159505u,66u},{160141u,56u},{160777u,542u},{161412u,543u},{162048u,541u},{162684u,541u},{163321u,541u},{163954u,543u},{164591u,48u},{165227u,49u},{165863u,49u},{166498u,46u},{169050u,147u},{170325u,141u},{171606u,134u},{172877u,103u},{174160u,57u},{175395u,151u},{176714u,45u},{177970u,123u},{179217u,138u},{181811u,59u},{183022u,150u},{184299u,88u},{186846u,103u}};
    sync_native_flag_reset(&f);
    for(unsigned i=0;i<sizeof(captured)/sizeof(captured[0]);++i)
        sync_native_flag_high(&f,captured[i].tick,captured[i].width);
    CHECK(native.have_group);CHECK(!native.valid);CHECK(native.qualified_fields==0u);
    CHECK(native.group==160777u);CHECK(native.group_n==6u);
    /* Strict DATA4 profile deliberately rejects this merged-H source. */
    sync_native_reset(&native);
    for(unsigned i=0;i<sizeof(captured)/sizeof(captured[0]);++i)
        sync_native_low(&native,captured[i].tick,captured[i].width);
    CHECK(!native.have_group);CHECK(!native.valid);
    sync_native_flag_reset(&f);
    uint32_t random=0x12345678u;uint64_t tick=1000u;
    for(unsigned i=0;i<1000000u;++i) {
        random=random*1664525u+1013904223u;tick+=100u+(random%1500u);
        sync_native_flag_high(&f,tick,(random>>16)%700u);
        CHECK(!native.valid);
    }
    printf("PASS: %u checks\n",checks);return 0;
}

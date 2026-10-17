/* C5VRX by Twotoz/contributors: source acquisition and retained clock. */
#include <stdio.h>
#include <stdlib.h>
#include "sync_native_flag_track.h"
static unsigned checks;
#define CHECK(x) do {++checks;if(!(x)){fprintf(stderr,"FAIL line%d: %s\n",__LINE__,#x);exit(1);}}while(0)
typedef struct {uint32_t tick,width;} pulse_t;
static void group(sync_native_flag_track_t *f,uint64_t b,unsigned n,unsigned defect)
{
    for(unsigned j=48u;j>0;--j)sync_native_flag_high(f,b-snft_half(n+1u+2u*j,n),160u);
    sync_native_flag_high(f,b-snft_half(n+1u,n),160u);
    if(defect!=1u)for(unsigned i=0;i<n;++i)
        sync_native_flag_high(f,b-snft_half(n-i,n),47u);
    for(unsigned i=0;i<n;++i) {
        if(defect==3u&&i==2u)continue;
        sync_native_flag_high(f,b+snft_half(i,n)+(defect==4u?90u*i:0u),defect==5u?310u:541u);
    }
    if(defect==6u)sync_native_flag_high(f,b+snft_half(n,n),541u);
    if(defect!=2u)for(unsigned i=0;i<n;++i)if(i<2u || defect==7u)
        sync_native_flag_high(f,b+snft_half(n+i,n),48u);
    sync_native_flag_high(f,b+snft_half(2u*n+1u,n),160u);
}
static const pulse_t camera_m[]={{73707u,171u},{74978u,159u},{76248u,157u},{77519u,159u},{78796u,151u},{80079u,141u},{81332u,176u},{82605u,158u},{83875u,157u},{85146u,161u},{86417u,176u},{87689u,158u},{88959u,157u},{90230u,160u},{91503u,156u},{92773u,177u},{94043u,167u},{95315u,160u},{96586u,174u},{97857u,168u},{99128u,162u},{100400u,159u},{101670u,175u},{102942u,164u},{104219u,155u},{105483u,171u},{106764u,171u},{108028u,176u},{109297u,171u},{110568u,171u},{111840u,156u},{113111u,153u},{114382u,163u},{115653u,169u},{116925u,162u},{118194u,161u},{119467u,163u},{120737u,160u},{122008u,164u},{123279u,155u},{124550u,151u},{125820u,172u},{127093u,173u},{128364u,158u},{129635u,172u},{130906u,168u},{132177u,175u},{133447u,160u},{134719u,178u},{135991u,159u},{137262u,157u},{138531u,161u},{139804u,155u},{141075u,164u},{142345u,179u},{143616u,160u},{144889u,162u},{146160u,153u},{147438u,163u},{148701u,154u},{149982u,147u},{151244u,165u},{152520u,167u},{153784u,171u},{155057u,165u},{156328u,214u},{156964u,47u},{157599u,51u},{158235u,56u},{158888u,35u},{159505u,66u},{160141u,56u},{160777u,542u},{161412u,543u},{162048u,541u},{162684u,541u},{163321u,541u},{163954u,543u},{164591u,48u},{165227u,49u},{165863u,49u},{166498u,46u},{169050u,147u},{170325u,141u},{171606u,134u},{172877u,103u},{174160u,57u},{175395u,151u},{176714u,45u},{177970u,123u},{179217u,138u},{181811u,59u},{183022u,150u},{184299u,88u},{186846u,103u}};
static const pulse_t camera_n0[]={{3416u,179u},{4688u,171u},{5958u,163u},{7260u,142u},{8508u,166u},{9793u,151u},{11075u,141u},{12313u,170u},{13586u,175u},{14891u,142u},{16125u,182u},{17413u,80u},{18690u,146u},{19941u,159u},{21223u,164u},{22483u,177u},{23754u,175u},{25064u,132u},{26295u,178u},{27568u,171u},{28840u,173u},{30111u,172u},{31415u,141u},{32652u,169u},{33955u,146u},{35205u,166u},{36476u,164u},{37734u,161u},{39007u,178u},{40277u,170u},{41564u,158u},{42859u,131u},{44092u,169u},{45362u,177u},{46633u,163u},{47921u,160u},{49194u,160u},{50448u,178u},{51719u,177u},{52989u,173u},{54260u,174u},{55555u,148u},{56802u,177u},{58074u,179u},{59390u,131u},{60615u,177u},{61886u,174u},{63169u,149u},{64428u,163u},{65704u,166u},{66975u,167u},{68243u,158u},{69526u,79u},{70805u,151u},{72105u,114u},{73338u,154u},{74610u,162u},{75869u,179u},{77141u,168u},{78411u,172u},{79682u,164u},{82240u,147u},{83495u,168u},{84766u,157u},{86062u,150u},{87334u,135u},{88580u,165u},{90487u,63u},{91760u,115u},{92412u,45u},{93030u,49u},{93664u,543u},{94382u,460u},{94942u,554u},{95650u,463u},{96397u,391u},{96843u,593u},{98115u,45u},{98762u,34u},{100020u,49u},{102562u,161u},{103834u,152u},{106427u,44u},{108940u,130u},{110216u,66u},{114055u,106u},{116604u,35u},{120358u,93u},{121663u,119u}};
static const pulse_t camera_n1[]={{44790u,159u},{46092u,138u},{47355u,152u},{48602u,172u},{49875u,169u},{51166u,156u},{52417u,176u},{53688u,171u},{54959u,175u},{56229u,172u},{57501u,161u},{58772u,177u},{60054u,168u},{61315u,172u},{62586u,179u},{63881u,152u},{65126u,174u},{66397u,179u},{67670u,171u},{68942u,171u},{70212u,178u},{71495u,163u},{72754u,169u},{74025u,165u},{75296u,178u},{76568u,177u},{77839u,179u},{79110u,167u},{80380u,178u},{81653u,177u},{82923u,176u},{84196u,172u},{85465u,176u},{86739u,176u},{88008u,171u},{89278u,178u},{90549u,165u},{91820u,165u},{93090u,179u},{94363u,170u},{95634u,179u},{96906u,172u},{98178u,176u},{99447u,172u},{100731u,148u},{101990u,178u},{103263u,171u},{104533u,175u},{105802u,166u},{107074u,176u},{108345u,176u},{109616u,179u},{110887u,178u},{112157u,179u},{113428u,176u},{114713u,155u},{115987u,151u},{117243u,177u},{118513u,173u},{119785u,173u},{121057u,174u},{122350u,147u},{123606u,171u},{124885u,151u},{126149u,163u},{127411u,176u},{128736u,107u},{129989u,128u},{131232u,170u},{132496u,175u},{133153u,68u},{133767u,68u},{134404u,48u},{135038u,82u},{135683u,40u},{137033u,509u},{137581u,544u},{138247u,511u},{138860u,533u},{139488u,581u},{140181u,484u},{142030u,47u},{143957u,28u},{145219u,146u},{146478u,160u},{147789u,118u},{149050u,130u},{151563u,93u},{152846u,144u},{155376u,162u},{157935u,140u}};
int main(void)
{
    sync_native_track_t native;sync_native_flag_track_t f;
    CHECK(sizeof(f)<=128u);
    for(unsigned n=5;n<=6;++n) {
        uint64_t period=n==5u?400000u:333667u;
        sync_native_flag_init(&f,&native);
        group(&f,100000u,n,0u);CHECK(native.have_group);CHECK(!native.valid);
        group(&f,100000u+period,n,0u);
        CHECK(native.valid);CHECK(native.standard==(n==5u?2u:1u));CHECK(native.qualified_fields==1u);
        uint32_t v_age=(uint32_t)(native.field>native.h?native.field-native.h:native.h-native.field);
        unsigned v_halves=(v_age*64u+native.selected_line_ticks_x32/2u)/native.selected_line_ticks_x32;
        uint32_t v_expected=(v_halves*native.selected_line_ticks_x32+32u)/64u;
        CHECK(v_age==v_expected);
        uint64_t retained_field=native.field;uint32_t retained_period=native.selected_line_ticks_x32;
        sync_native_flag_high(&f,100000u+period+2000000u,12u);
        CHECK(native.valid);CHECK(native.field==retained_field);
        CHECK(native.selected_line_ticks_x32==retained_period);
        uint64_t old_h=native.h;
        uint32_t line=(retained_period+16u)>>5;
        uint64_t resume=old_h+2000u*line;
        sync_native_flag_high(&f,resume,160u);CHECK(native.h==old_h);
        for(unsigned j=1u;j<=40u;++j)sync_native_flag_high(&f,resume+j*line,160u);
        CHECK(native.have_selected);CHECK(native.h>old_h);CHECK(native.field==retained_field);
        sync_native_flag_reset(&f);CHECK(!native.valid);CHECK(!native.have_group);
        for(unsigned defect=1u;defect<=6u;++defect) {
            if(defect==3u)continue; /* one missing broad is supported if occupancy remains adequate */
            group(&f,100000u,n,defect);group(&f,100000u+period,n,defect);
            CHECK(!native.valid);sync_native_flag_reset(&f);
        }
        group(&f,100000u,n,0u);group(&f,100000u+period+401u,n,0u);CHECK(!native.valid);
        sync_native_flag_reset(&f);
        group(&f,100000u,n,0u);group(&f,100000u+period,n,0u);CHECK(native.valid);
        /* One off-phase V group cannot replace an established clock. */
        uint64_t old=native.field;group(&f,100000u+2u*period+300u,n,0u);
        CHECK(native.valid);CHECK(native.field==old);
        /* A second independently confirmed changed phase may replace it. */
        group(&f,100000u+3u*period+300u,n,0u);CHECK(native.valid);CHECK(native.field!=old);
        sync_native_flag_high(&f,1u,160u);CHECK(!native.valid); /* clock regression */
    }
    const pulse_t *sets[]={camera_m,camera_n0,camera_n1};
    const unsigned lengths[]={sizeof(camera_m)/sizeof(camera_m[0]),sizeof(camera_n0)/sizeof(camera_n0[0]),sizeof(camera_n1)/sizeof(camera_n1[0])};
    for(unsigned z=0;z<3u;++z) {
        sync_native_flag_reset(&f);
        for(unsigned i=0;i<lengths[z];++i)sync_native_flag_high(&f,sets[z][i].tick,sets[z][i].width);
        CHECK(native.have_group);CHECK(!native.valid);CHECK(native.qualified_fields==0u);
        /* A real frozen single-group trace proves a candidate only. No
         * duplicated trace is used as evidence for an actual field pair. */
    }
    /* Hundreds of valid H lines alone grant no vertical authority. */
    sync_native_flag_reset(&f);
    for(unsigned i=0u;i<1000u;++i)sync_native_flag_high(&f,1000u+i*1280u,160u);
    CHECK(native.h_standard==2u);CHECK(!native.have_group);CHECK(!native.valid);
    /* Complete-looking V patterns without independent H never qualify. */
    sync_native_flag_reset(&f);
    for(unsigned field=0u;field<10u;++field) {
        uint64_t b=100000u+field*400000u;
        for(unsigned i=0u;i<5u;++i)sync_native_flag_high(&f,b-3200u+i*640u,48u);
        for(unsigned i=0u;i<5u;++i)sync_native_flag_high(&f,b+i*640u,541u);
        for(unsigned i=0u;i<5u;++i)sync_native_flag_high(&f,b+3200u+i*640u,48u);
        sync_native_flag_high(&f,b+6400u,160u);CHECK(!native.valid);
    }
    sync_native_flag_reset(&f);
    uint32_t random=0x12345678u;uint64_t tick=1000u;
    for(unsigned i=0;i<1000000u;++i) {
        random=random*1664525u+1013904223u;tick+=100u+(random%1500u);
        sync_native_flag_high(&f,tick,(random>>16)%700u);CHECK(!native.valid);
    }
    printf("PASS: %u checks; wrapper=%zu bytes\n",checks,sizeof(f));return 0;
}

/* C5VRX by Twotoz and contributors. Extends the pinned PHY work in
 * legacy/c5vrx4/main/phy_rx_lab.c (#150-155). No realtime-path changes.
 * 11p observation originally reported by SushiDude (@Ready4Sushi).
 * Static ABI evidence: research/data/nf_phy_2026-10-24/phy-evidence.txt. */
#include "phy_noise_lab.h"
#include "receiver.h"
#include "phy.h"
#include "snapshot.h"
#include "cn_auto.h"
#include "esp_timer.h"
#include "esp_system.h"
#include <stdio.h>
#include <stdlib.h>
#include <math.h>
#include <inttypes.h>

#ifdef C5VRX_PHY_PINNED
static volatile bool ack_waiting,ack_received;
static volatile uint32_t ack_token,ack_hash;
static uint32_t capture_token;
bool phy_noise_lab_ack_byte(uint8_t byte)
{
    static unsigned pos;
    static uint32_t token,hash;
    if(byte==6){pos=1;token=hash=0;return true;}
    if(!pos)return false;
    if(pos==17){
        if(byte=='\n'&&ack_waiting&&token==ack_token&&hash==ack_hash)ack_received=true;
        pos=0;return true;
    }
    unsigned digit;
    if(byte>='0'&&byte<='9')digit=byte-'0';
    else if(byte>='a'&&byte<='f')digit=byte-'a'+10;
    else {pos=0;return true;}
    if(pos<=8)token=(token<<4)|digit;else hash=(hash<<4)|digit;
    ++pos;return true;
}
#ifndef MM
#define MM(a) (*(volatile uint32_t *)(uintptr_t)(a))
#endif
static uint8_t lab_gain;
static bool entry_rejected;
static unsigned lab_dwell_ms=300;
static unsigned lab_windows=4;
static int sample_adc_expected=-1;
static int sample_locap_expected=-1;
static uint32_t lab_packed_gain;
static bool lab_packed_valid;
static bool sample_dco_check;
static int sample_dco[2];
typedef struct { uint32_t addr, mask, value; } patch_t;
typedef struct { const char *name; unsigned n; patch_t p[12]; bool analog; } profile_t;
static const profile_t *sample_profile;
static uint32_t sample_expected[12];
static bool verify(const profile_t *p,const uint32_t *values);
extern unsigned char phy_param[];
extern uint8_t phy_i2c_readReg(uint8_t, uint8_t, uint8_t);
extern void phy_i2c_writeReg(uint8_t, uint8_t, uint8_t, uint8_t);
extern void phy_11p_set(uint8_t, uint8_t);
extern void phy_adc_rate_set(uint32_t);
extern uint16_t phy_pbus_rd(uint32_t,uint32_t);
static const profile_t profiles[] = {
    {"11P",4,{{0x600A7CE4,0x1c,0x10},{0x600A7030,0x20,0},
                 {0x600A7048,0x7f00,0x4000},{0x600A71C4,0xfe0000,0x440000}},true},
    {"CHAN_A_ARG0",1,{{0x600A7904,0x400007,0x400000}},false},
    {"CHAN_A_ARG1",1,{{0x600A7904,0x400000,0}},false},
    {"CHAN_B_ARG0",1,{{0x600A7074,0x2000,0x2000}},false},
    {"CHAN_B_ARG1",1,{{0x600A7074,0x2000,0}},false},
    /* All five coupled enables from phy_iq_corr_enable; calibrated
     * coefficients/scaling remain untouched and are read back separately. */
    {"IQ_CORR_OFF",2,{{0x600A0438,0xe0000000,0},{0x600A0C0C,0x6000,0}},false},
    {"SPUR_SLOT0_OFF",1,{{0x600A7C14,0x2000,0}},false},
};
/* Second audit, from the exact same pinned disassembly. RX coefficients
 * are set by phy_rxiq_set_reg at 0438; TX coefficients by phy_txiq_set_reg
 * at 0C0C. The old five-bit test deliberately combined both banks; do not
 * attribute its whole effect to RX correction. Bit names describe writes,
 * not established individual DC/IQ/scaling semantics. Coefficients ZERO
 * means raw zero fields, never a claimed calibrated identity transform. */
static const profile_t audit_profiles[] = {
    {"RX_0438_BIT29_OFF",1,{{0x600A0438,0x20000000,0}},false},
    {"RX_0438_BIT30_OFF",1,{{0x600A0438,0x40000000,0}},false},
    {"RX_0438_BIT31_OFF",1,{{0x600A0438,0x80000000,0}},false},
    {"RX_0438_ALL_OFF",1,{{0x600A0438,0xe0000000,0}},false},
    {"TX_0C0C_BIT13_OFF",1,{{0x600A0C0C,0x2000,0}},false},
    {"TX_0C0C_BIT14_OFF",1,{{0x600A0C0C,0x4000,0}},false},
    {"TX_0C0C_ALL_OFF",1,{{0x600A0C0C,0x6000,0}},false},
    {"RX_COEFF_A_ZERO",1,{{0x600A0438,0x1fc00000,0}},false},
    {"RX_COEFF_B_ZERO",1,{{0x600A0438,0x003f0000,0}},false},
    {"RX_COEFF_AB_ZERO",1,{{0x600A0438,0x1fff0000,0}},false},
    {"PKDADC_BIT31_OFF",1,{{0x600A0C38,0x80000000,0}},false},
    {"BB_CCA_BIT30_OFF",1,{{0x600A7018,0x40000000,0}},false},
    /* phy_disable/enable_cca writes BOTH two-bit fields in this mask. */
    {"MAC_CCA_DISABLE",1,{{0x600A4C5C,0xf0000000,0xa0000000}},false},
    {"MAC_CCA_ENABLE",1,{{0x600A4C5C,0xf0000000,0}},false},
    /* Previously working digital modes; preserve vendor ADC selector. */
    {"DFILT_MODE4",1,{{0x600A0430,0x003c0000,0x00100000}},false},
    {"DFILT_MODE8",1,{{0x600A0430,0x003c0000,0x00200000}},false},
    {"DFILT_MODE12",1,{{0x600A0430,0x003c0000,0x00300000}},false},
    /* phy_fft_scale_force(a,b): enable at bit19, scale at bits27:20.
     * Only disable the force or force scale zero; no arbitrary FFT codes. */
    {"FFT_FORCE_OFF",1,{{0x600A7800,0x00080000,0}},false},
    {"FFT_FORCE_SCALE0",1,{{0x600A7800,0x0ff80000,0x00080000}},false},
};
/* Complete final MMIO fields of both branches in pinned phy_rx_11b_opt
 * and phy_rx_sense_set. No analog writes, calibration flags or helper
 * calls occur in either function. Retain every unrelated field. These
 * are packet/RX-policy controls, not documented continuous-FM filters. */
static const profile_t packet_profiles[] = {
    {"RX11B_OPT_ARG0",6,{
        {0x600A7044,0x003f3f00,0x003e1800},{0x600A7124,0x0000fc0f,0x00006004},
        {0x600A8004,0x0000f000,0x00006000},{0x600A7104,0x000001ff,0x000001c8},
        {0x600A71D8,0x0003fe00,0x0003cc00},{0x600A7014,0xff800000,0xff000000}},false},
    {"RX11B_OPT_ARG1",6,{
        {0x600A7044,0x003f3f00,0x003f2100},{0x600A7124,0x0000fc0f,0x00008403},
        {0x600A8004,0x0000f000,0x00009000},{0x600A7104,0x000001ff,0x000001bc},
        {0x600A71D8,0x0003fe00,0x0003cc00},{0x600A7014,0xff800000,0xff000000}},false},
    {"RXSENSE_ARG0",4,{
        {0x600A7010,0xff800000,0},{0x600A7014,0xff800000,0},
        {0x600A7044,0x000000ff,0},{0x600A7108,0x00000200,0x00000200}},false},
    {"RXSENSE_ARG1",4,{
        {0x600A7010,0xff800000,0x00800000},{0x600A7014,0xff800000,0x00800000},
        {0x600A7044,0x000000ff,1},{0x600A7108,0x00000200,0}},false},
};
static const profile_t clock_profiles[] = {
    /* Complete masks from phy_set_txclk_en and phy_wifi_enable_set.
     * Never touch RX clock gates, PLL, CPU/USB clocks or I2C master gates. */
    {"TXCLK_ARG0",1,{{0x600A0890,0x00030000,0}},false},
    {"TXCLK_ARG1",1,{{0x600A0890,0x00030000,0x00030000}},false},
    {"WIFI_ENABLE_ARG0",1,{{0x600A9C18,2,0}},false},
    {"WIFI_ENABLE_ARG1",1,{{0x600A9C18,2,2}},false},
};
/* Predeclared joint probes, expressly excluding 11p and calibrated IQ
 * coefficient changes. The RX-sense and RX11b branches conflict at 7014,
 * so test two coherent combinations rather than silently overriding one.
 * ADC analog/digital coupling is saved/restored by run_profile below. */
static const profile_t combo_profiles[] = {
    {"COMBO_SENSE0_EXTRA",9,{
        {0x600A0448,3,0},{0x600A7018,0x40000000,0},{0x600A0C38,0x80000000,0},
        {0x600A4C5C,0xf0000000,0xa0000000},{0x600A7074,0x2000,0},
        {0x600A7010,0xff800000,0},{0x600A7014,0xff800000,0},
        {0x600A7044,0xff,0},{0x600A7108,0x200,0x200}},false},
    {"COMBO_RX11B0_EXTRA",11,{
        {0x600A0448,3,0},{0x600A7018,0x40000000,0},{0x600A0C38,0x80000000,0},
        {0x600A4C5C,0xf0000000,0xa0000000},{0x600A7074,0x2000,0},
        {0x600A7044,0x003f3f00,0x003e1800},{0x600A7124,0xfc0f,0x6004},
        {0x600A8004,0xf000,0x6000},{0x600A7104,0x1ff,0x1c8},
        {0x600A71D8,0x3fe00,0x3cc00},{0x600A7014,0xff800000,0xff000000}},false},
    {"STOCK_SHAM_EXTRA",0,{{0}},false},
};
/* Complete isolated MMIO footprints of pinned phy_lltf_mask_en(a,b),
 * phy_wifi_rifs_mode_en(a), phy_vht_support(a), phy_rx11blr_cfg(a).
 * No ADC clocks, packet dump/source selection, PBUS or analog helpers.
 * Packet-oriented names do not establish a continuous-IQ filter effect. */
static const profile_t policy_profiles[] = {
    {"LLTF_MASK_00",1,{{0x600A790C,0x3000,0}},false},
    {"LLTF_MASK_10",1,{{0x600A790C,0x3000,0x2000}},false},
    {"LLTF_MASK_01",1,{{0x600A790C,0x3000,0x1000}},false},
    {"LLTF_MASK_11",1,{{0x600A790C,0x3000,0x3000}},false},
    {"RIFS_ARG0",1,{{0x600A70F4,1,0}},false},
    {"RIFS_ARG1",1,{{0x600A70F4,1,1}},false},
    {"VHT_ARG0",1,{{0x600A7CE4,0x20,0}},false},
    {"VHT_ARG1",1,{{0x600A7CE4,0x20,0x20}},false},
    {"RX11BLR_ARG0",2,{{0x600A8060,0xc00,0},{0x600A807C,0x800,0}},false},
    {"RX11BLR_ARG1",2,{{0x600A8060,0xc00,0xc00},{0x600A807C,0x800,0x800}},false},
    {"POLICY_SHAM",0,{{0}},false},
};
/* Exact final MMIO footprints of phy_hemu_ru26_good_res() and
 * phy_en_pwdet(). Names do not establish polarity or an RX noise effect.
 * Neither helper starts a tone or changes PBUS/ADC clocks; apply masks
 * directly, preserving every unrelated field and the incoming baseline. */
static const profile_t aux_profiles[] = {
    {"AUX_SHAM",0,{{0}},false},
    {"HEMU_RU26_VENDOR",1,{{0x600A7890,0x03000000,0x01000000}},false},
    {"PWDET_VENDOR_CLEAR",1,{{0x600A0808,0x0e,0}},false},
    {"AUX_SHAM",0,{{0}},false},
};
static const uint32_t watch[] = {
    0x600A7018,0x600A7C44,0x600A7C50,0x600A7CE4,0x600A7030,
    0x600A7048,0x600A71C4,0x600A7904,0x600A7074,0x600A0438,
    0x600A0C0C,0x600A043C,0x600A0430,0x600A0448,0x600A7C14,
    0x600A7C18,0x600A7C1C,0x600A7C20,0x600A7810,0x600A7814,
    0x600A7818,0x600A781C,0x600A7820,0x600A7824,0x600A7828,
    0x600A782C,0x600A7830,0x600A0C38,0x600A4C5C,0x600A7800,
    0x600A7044,0x600A7124,0x600A8004,0x600A7104,0x600A71D8,
    0x600A7010,0x600A7014,0x600A7108,0x600A0890,0x600A9C18,
    0x600A790C,0x600A70F4,0x600A8060,0x600A807C,0x600A7890,0x600A0808
};
static void state(unsigned id, const char *stage)
{
    for (unsigned i=0;i<sizeof(watch)/sizeof(watch[0]);++i)
        printf("PHYLAB_REG id=%u stage=%s addr=%08" PRIx32 " value=%08" PRIx32 "\n",id,stage,watch[i],MM(watch[i]));
    printf("PHYLAB_PARAM id=%u stage=%s flags=%02x/%02x iq_scale_sel=%u\n",id,stage,phy_param[0x26],phy_param[0x27],phy_param[650]);
    for (uint8_t r=6;r<=13;++r)
        printf("PHYLAB_I2C id=%u stage=%s block=67 host=1 reg=%u value=%02x\n",id,stage,r,phy_i2c_readReg(0x67,1,r));
    printf("PHYLAB_ADC id=%u stage=%s block=66 host=0 reg=4 value=%02x selector_bits=%u\n",id,stage,phy_i2c_readReg(0x66,0,4),(unsigned)(MM(0x600A0448)&3));
    /* Read-only witness of all fields written by phy_bias_reg_set().
     * The helper uses fixed 0x9f/0x7f bytes; code numbers do not establish
     * bias-current direction, LNA noise figure or a safe overdrive range. */
    for(uint8_t reg=0;reg<2;++reg)
        printf("PHYLAB_BIAS id=%u stage=%s block=6a host=1 reg=%u value=%02x\n",id,stage,reg,phy_i2c_readReg(0x6a,1,reg));
    printf("PHYLAB_LOCAP id=%u stage=%s block=63 host=1 reg=21 value=%02x\n",id,stage,phy_i2c_readReg(0x63,1,21));
    for(uint8_t reg=4;reg<=6;++reg)
        printf("PHYLAB_ULP id=%u stage=%s block=61 host=0 reg=%u value=%02x\n",id,stage,reg,phy_i2c_readReg(0x61,0,reg));
    printf("PHYLAB_ULP_CACHE id=%u stage=%s param652=%02x param653=%02x\n",id,stage,phy_param[652],phy_param[653]);
}
static bool capture(uint8_t *raw)
{
    for (unsigned i=0;i<40;++i) { vTaskDelay(1); if(copy_snapshot(raw)) return true; }
    return false;
}
static void power(const uint8_t *raw, double *m2, double *kurt, double *rail)
{
    cna_sums_t s={0}; unsigned rails=0;
    for(unsigned k=0;k<C5V4_LEVEL_SAMPLE_BYTES;++k) {
        cna_sums_add(&s,raw[k]);
        unsigned i=raw[k]>>4,q=raw[k]&15;
        rails+=(i==7||i==8||q==7||q==8);
    }
    float mi,mq; double m4; cna_mean(&s,&mi,&mq); cna_moments(&s,mi,mq,m2,&m4);
    *kurt=m4/(*m2 * *m2); *rail=(double)rails/C5V4_LEVEL_SAMPLE_BYTES;
}
static double noise_headroom(const uint8_t *raw)
{
    cna_sums_t s={0};
    for(unsigned k=0;k<C5V4_LEVEL_SAMPLE_BYTES;++k)cna_sums_add(&s,raw[k]);
    double inv=1.0/s.n,mx=0.5*s.sx*inv,my=0.5*s.sy*inv;
    double vx=0.25*s.sxx*inv-mx*mx,vy=0.25*s.syy*inv-my*my;
    /* Six-sigma plus the measured mean must stay inside +/-6 cells,
     * reserving 1.5 cells against the sign-preserving lane's fold rails.
     * This is a conservative Gaussian screening margin, not a guarantee
     * against later DC movement or external interference. */
    double x=fabs(mx)+6.0*sqrt(fmax(0.0,vx));
    double y=fabs(my)+6.0*sqrt(fmax(0.0,vy));
    return 6.0-fmax(x,y);
}
static bool hardware_gain_ok(void)
{
    if(!lab_packed_valid)return true; /* direct host lifecycle fixtures */
    /* Same pinned PBUS readback/predicate used by phy_dco_try_pair(). */
    uint16_t rf=phy_pbus_rd(8,1),bb=phy_pbus_rd(0,2),fine=phy_pbus_rd(1,2);
    bool ok=rf==(lab_packed_gain>>12) && (bb&127u)==((lab_packed_gain>>4)&127u) &&
        ((bb&~127u)==0u||(bb&~127u)==0x100u) && fine==(0xd0u|(lab_packed_gain&7u));
    if(!ok)printf("PHYLAB_ABORT reason=hardware_gain rf=%u bb=%u fine=%u expected=%08" PRIx32 "\n",rf,bb,fine,lab_packed_gain);
    return ok;
}
static bool dco_expected_ok(void)
{
    if(!sample_dco_check)return true;
    int pair[2];
    return phy_dco_held()&&phy_dco_read_pair(pair)&&pair[0]==sample_dco[0]&&pair[1]==sample_dco[1];
}
static bool measure(unsigned id, unsigned step, uint8_t *raw, bool (*context_ok)(void),int64_t deadline)
{
    const char *stage=(step==0||step==3)?"STOCK":"TEST";
    vTaskDelay(pdMS_TO_TICKS(lab_dwell_ms));
    state(id,stage);
    if(sample_dco_check)printf("PHYLAB_DCO_STATE id=%u step=%u i=%d q=%d held=%u\n",id,step,sample_dco[0],sample_dco[1],phy_dco_held());
    if(!verify(sample_profile,sample_expected))return false;
    uint32_t hashes[16]={0};
    for(unsigned rep=0;rep<lab_windows;++rep) {
        if(esp_timer_get_time()>deadline||!context_ok()||s_current_gain!=lab_gain||
           !verify(sample_profile,sample_expected)||
           (sample_adc_expected>=0 && (phy_i2c_readReg(0x66,0,4)&4)!=(unsigned)sample_adc_expected)||
           (sample_locap_expected>=0 && (phy_i2c_readReg(0x63,1,21)&31)!=(unsigned)sample_locap_expected)||
           !hardware_gain_ok()||!dco_expected_ok()||!capture(raw)||!hardware_gain_ok()||!dco_expected_ok()||
           (sample_locap_expected>=0 && (phy_i2c_readReg(0x63,1,21)&31)!=(unsigned)sample_locap_expected)) return false;
        double m2,kurt,rail; power(raw,&m2,&kurt,&rail);
        if(!isfinite(m2)||m2<=0.05||!isfinite(kurt)) return false;
        uint32_t hash=2166136261u;
        for(unsigned k=0;k<C5V4_LEVEL_SAMPLE_BYTES;++k) hash=(hash^raw[k])*16777619u;
        for(unsigned j=0;j<rep;++j)if(hash==hashes[j]){
            printf("PHYLAB_ABORT reason=repeated_noise_capture id=%u step=%u rep=%u\n",id,step,rep);return false;
        }
        hashes[rep]=hash;
        const int64_t timestamp=esp_timer_get_time();const uint32_t token=++capture_token;
        ack_waiting=false;ack_token=token;ack_hash=hash;ack_received=false;ack_waiting=true;
        for(unsigned attempt=0;attempt<3&&!ack_received;++attempt){
        printf("PHYLAB_IQ_BEGIN id=%u step=%u stage=%s rep=%u bytes=%u rate_hz=%u gain=%u freq=%u offset=%d m2=%.6f kurt=%.4f rails=%.6f fnv1a=%08" PRIx32 " t_us=%" PRId64 " tx_token=%08" PRIx32 " tx_attempt=%u\n",
               id,step,stage,rep,C5V4_LEVEL_SAMPLE_BYTES,IQ_RATE_HZ,s_current_gain,rf_frequency_mhz(),rf_offset_khz(),m2,kurt,rail,hash,timestamp,token,attempt);
        static const char hex[]="0123456789abcdef"; char line[513];
        for(unsigned off=0;off<C5V4_LEVEL_SAMPLE_BYTES;off+=256) {
            unsigned n=C5V4_LEVEL_SAMPLE_BYTES-off; if(n>256)n=256;
            for(unsigned k=0;k<n;++k) { line[2*k]=hex[raw[off+k]>>4];line[2*k+1]=hex[raw[off+k]&15]; }
            line[2*n]=0; printf("PHYLAB_IQ_DATA offset=%u %s\n",off,line);vTaskDelay(pdMS_TO_TICKS(5));
        }
        printf("PHYLAB_IQ_END fnv1a=%08" PRIx32 "\n",hash);
        fflush(stdout);
        for(unsigned spin=0;spin<60&&!ack_received&&context_ok()&&esp_timer_get_time()<deadline;++spin)vTaskDelay(pdMS_TO_TICKS(10));
        if(!context_ok()||esp_timer_get_time()>deadline)break;
        }
        ack_waiting=false;
        if(!ack_received){printf("PHYLAB_ABORT reason=transport_ack_timeout token=%08" PRIx32 "\n",token);return false;}
    }
    return context_ok();
}
static bool verify(const profile_t *p,const uint32_t *values)
{
    for(unsigned i=0;i<p->n;++i) if((MM(p->p[i].addr)&p->p[i].mask)!=values[i])return false;
    return true;
}
static void masked(uint32_t addr,uint32_t mask,uint32_t value)
{ MM(addr)=(MM(addr)&~mask)|(value&mask); }
static bool run_profile(unsigned id,const profile_t *p,uint8_t *raw,bool (*context_ok)(void),int64_t deadline)
{
    entry_rejected=false;
    uint32_t saved[12],enabled[12];uint8_t analog[8]={0},flags[2]={0};
    int adc_slot=-1;uint8_t saved_adc=0;
    for(unsigned i=0;i<p->n;++i){saved[i]=MM(p->p[i].addr)&p->p[i].mask;enabled[i]=p->p[i].value;}
    for(unsigned i=0;i<p->n;++i)if(p->p[i].addr==0x600A0448&&p->p[i].mask==3)adc_slot=(int)i;
    if(adc_slot>=0)saved_adc=phy_i2c_readReg(0x66,0,4)&4;
    sample_adc_expected=adc_slot>=0?(int)saved_adc:-1;
    if(p->analog){flags[0]=phy_param[0x26];flags[1]=phy_param[0x27];for(uint8_t i=0;i<8;++i)analog[i]=phy_i2c_readReg(0x67,1,6+i);}
    sample_profile=p;for(unsigned i=0;i<p->n;++i)sample_expected[i]=saved[i];
    printf("PHYLAB_PROFILE id=%u name=%s sequence=STOCK_TEST_TEST_STOCK\n",id,p->name);
    bool ok=measure(id,0,raw,context_ok,deadline);
    if(ok) {
        if(p->analog)phy_11p_set(1,0);
        else for(unsigned i=0;i<p->n;++i)masked(p->p[i].addr,p->p[i].mask,p->p[i].value);
        if(adc_slot>=0){
            unsigned selector=p->p[adc_slot].value&1u;phy_adc_rate_set(selector);
            sample_adc_expected=selector?0:4;
        }
        ok=verify(p,enabled);
        if(adc_slot>=0)ok=ok&&((phy_i2c_readReg(0x66,0,4)&4)==(unsigned)sample_adc_expected);
        if(p->analog){ok=ok&&phy_param[0x26]==1&&phy_param[0x27]==0;for(uint8_t i=0;i<8;++i)ok=ok&&phy_i2c_readReg(0x67,1,6+i)==60;}
        printf("PHYLAB_APPLY id=%u verified=%u\n",id,ok);
        if(!ok){entry_rejected=true;state(id,"REJECTED_ENTRY");}
        for(unsigned i=0;i<p->n;++i)sample_expected[i]=enabled[i];
        if(ok)ok=measure(id,1,raw,context_ok,deadline);
        if(ok)ok=measure(id,2,raw,context_ok,deadline);
    }
    if(p->analog){phy_param[0x26]=flags[0];phy_param[0x27]=flags[1];for(uint8_t i=0;i<8;++i)phy_i2c_writeReg(0x67,1,6+i,analog[i]);}
    if(adc_slot>=0){phy_i2c_writeReg(0x66,0,4,(phy_i2c_readReg(0x66,0,4)&~4u)|saved_adc);sample_adc_expected=saved_adc;}
    for(unsigned i=0;i<p->n;++i)masked(p->p[i].addr,p->p[i].mask,saved[i]);
    bool restored=verify(p,saved);
    if(adc_slot>=0)restored=restored&&((phy_i2c_readReg(0x66,0,4)&4)==saved_adc);
    if(p->analog){restored=restored&&phy_param[0x26]==flags[0]&&phy_param[0x27]==flags[1];for(uint8_t i=0;i<8;++i)restored=restored&&phy_i2c_readReg(0x67,1,6+i)==analog[i];}
    printf("PHYLAB_RESTORE id=%u verified=%u\n",id,restored);
    if(!restored){printf("PHYLAB_FATAL restore_failed reboot_required\n");fflush(stdout);esp_restart();}
    if(!ok)state(id,"RESTORED_AFTER_ABORT");
    for(unsigned i=0;i<p->n;++i)sample_expected[i]=saved[i];
    if(ok)ok=measure(id,3,raw,context_ok,deadline);
    sample_adc_expected=-1;
    printf("PHYLAB_RESULT id=%u status=%s restored=1 carrier_delta_db=unknown cn=unknown video_sinad=unknown\n",id,ok?"PASS":(entry_rejected?"REJECTED_ENTRY":"INCOMPLETE"));
    return ok;
}
static bool run_output(unsigned id,uint8_t *raw,bool (*context_ok)(void),int64_t deadline)
{
    const profile_t p={"DAC_OUTPUT_OFF",0,{{0}},false};
    sample_profile=&p;
    if(!transport_live()||transport_faulted())return false;
    printf("PHYLAB_PROFILE id=%u name=DAC_OUTPUT_OFF sequence=ON_OFF_OFF_ON\n",id);
    bool ok=measure(id,0,raw,context_ok,deadline);
    if(ok){
        transport_stop_tx();
        ok=!transport_live()&&!transport_faulted();
        printf("PHYLAB_APPLY id=%u verified=%u output_live=%u\n",id,ok,transport_live());
        if(ok)ok=measure(id,1,raw,context_ok,deadline);
        if(ok)ok=measure(id,2,raw,context_ok,deadline);
    }
    if(!transport_live())transport_resume_live();
    bool restored=transport_live()&&!transport_faulted();
    printf("PHYLAB_RESTORE id=%u verified=%u output_live=%u\n",id,restored,transport_live());
    if(!restored){printf("PHYLAB_FATAL output_restore_failed reboot_required\n");fflush(stdout);esp_restart();}
    if(ok)ok=measure(id,3,raw,context_ok,deadline);
    printf("PHYLAB_RESULT id=%u status=%s restored=1 carrier_delta_db=unknown cn=unknown video_sinad=unknown\n",id,ok?"PASS":"INCOMPLETE");
    return ok;
}
static bool run_adc(unsigned id,unsigned selector,uint8_t *raw,bool (*context_ok)(void),int64_t deadline)
{
    profile_t p={selector?"ADC_SELECTOR1":"ADC_SELECTOR0",1,{{0x600A0448,3,selector?3:0}},false};
    const uint32_t saved=MM(0x600A0448)&3;
    const uint8_t saved_analog=phy_i2c_readReg(0x66,0,4)&4;
    sample_profile=&p;sample_expected[0]=saved;sample_adc_expected=saved_analog;
    printf("PHYLAB_PROFILE id=%u name=%s sequence=STOCK_TEST_TEST_STOCK\n",id,p.name);
    bool ok=measure(id,0,raw,context_ok,deadline);
    if(ok){
        /* Exact pinned ABI: analog 66/0/reg4[2]=!selector, digital
         * 0448[1:0]=selector duplicated. No dfilt/PLL/calibration call. */
        phy_adc_rate_set(selector);
        sample_expected[0]=selector?3:0;sample_adc_expected=selector?0:4;
        ok=verify(&p,sample_expected)&&((phy_i2c_readReg(0x66,0,4)&4)==(unsigned)sample_adc_expected);
        printf("PHYLAB_APPLY id=%u verified=%u\n",id,ok);
        if(ok)ok=measure(id,1,raw,context_ok,deadline);
        if(ok)ok=measure(id,2,raw,context_ok,deadline);
    }
    /* Restore only owned bits, retaining all other current analog fields. */
    phy_i2c_writeReg(0x66,0,4,(phy_i2c_readReg(0x66,0,4)&~4u)|saved_analog);
    masked(0x600A0448,3,saved);
    sample_expected[0]=saved;sample_adc_expected=saved_analog;
    bool restored=verify(&p,sample_expected)&&((phy_i2c_readReg(0x66,0,4)&4)==saved_analog);
    printf("PHYLAB_RESTORE id=%u verified=%u\n",id,restored);
    if(!restored){printf("PHYLAB_FATAL adc_restore_failed reboot_required\n");fflush(stdout);esp_restart();}
    if(ok)ok=measure(id,3,raw,context_ok,deadline);
    sample_adc_expected=-1;
    printf("PHYLAB_RESULT id=%u status=%s restored=1 carrier_delta_db=unknown cn=unknown video_sinad=unknown\n",id,ok?"PASS":"INCOMPLETE");
    return ok;
}
static bool run_gain_row(unsigned id,uint8_t gain,uint8_t *raw,bool (*context_ok)(void),int64_t deadline)
{
    receiver_set_gain(gain);vTaskDelay(pdMS_TO_TICKS(100));
    arc_gain_tuple_t t;
    lab_packed_valid=arc_gain_tuple_decode(rf_gain_table(),s_current_gain,&t);
    if(!lab_packed_valid)return false;
    lab_gain=s_current_gain;lab_packed_gain=t.packed_state;
    char name[24];snprintf(name,sizeof(name),"GAIN_ROW_%u",gain);
    const profile_t p={name,0,{{0}},false};
    printf("PHYLAB_GAIN_ROW id=%u gain=%u rf_stage=%u rf_code=%u bb=%u fine=%u packed=%08" PRIx32 "\n",id,gain,t.rf_stage,t.rf_code,t.bb_code,t.fine_code,t.packed_state);
    return run_profile(id,&p,raw,context_ok,deadline);
}
static bool run_locap(unsigned id,unsigned target,uint8_t *raw,bool (*context_ok)(void),int64_t deadline)
{
    /* Recovered phy_set_freq_i2c_new owns exactly this five-bit field.
     * phy_get_dcap_degen clamps the vendor code to 10..31. Test only an
     * adjacent in-range code; never call channel/VCO/PLL calibration here. */
    const uint8_t saved=phy_i2c_readReg(0x63,1,21)&31;
    if(saved<10||saved>31){
        printf("PHYLAB_LOCAP_REFUSED reason=stock_outside_vendor_interval saved=%u\n",saved);return false;
    }
    if(target<10||target>31){
        printf("PHYLAB_SKIP id=%u reason=locap_vendor_interval saved=%u target=%u\n",id,saved,target);return true;
    }
    char name[32];snprintf(name,sizeof(name),"LO_CAP_CODE_%u",target);
    const profile_t p={name,0,{{0}},false};sample_profile=&p;
    sample_locap_expected=saved;
    printf("PHYLAB_PROFILE id=%u name=%s sequence=STOCK_TEST_TEST_STOCK\n",id,name);
    printf("PHYLAB_LOCAP_TARGET id=%u saved=%u target=%u mask=1f persistent=0\n",id,saved,target);
    bool ok=measure(id,0,raw,context_ok,deadline);
    if(ok){
        phy_i2c_writeReg(0x63,1,21,(phy_i2c_readReg(0x63,1,21)&0xe0u)|target);
        sample_locap_expected=(int)target;
        ok=(phy_i2c_readReg(0x63,1,21)&31)==target;
        printf("PHYLAB_APPLY id=%u verified=%u\n",id,ok);
        if(ok)ok=measure(id,1,raw,context_ok,deadline);
        if(ok)ok=measure(id,2,raw,context_ok,deadline);
    }
    phy_i2c_writeReg(0x63,1,21,(phy_i2c_readReg(0x63,1,21)&0xe0u)|saved);
    const bool restored=(phy_i2c_readReg(0x63,1,21)&31)==saved;
    printf("PHYLAB_RESTORE id=%u verified=%u\n",id,restored);
    if(!restored){printf("PHYLAB_FATAL locap_restore_failed reboot_required\n");fflush(stdout);esp_restart();}
    sample_locap_expected=saved;
    if(ok)ok=measure(id,3,raw,context_ok,deadline);
    sample_locap_expected=-1;
    printf("PHYLAB_RESULT id=%u status=%s restored=1 carrier_delta_db=unknown cn=unknown video_sinad=unknown\n",id,ok?"PASS":"INCOMPLETE");
    return ok;
}
/* Only an already-held DAC pair is eligible: keep the entry debug/work mode.
 * Move one axis at a time through the existing verified actuator; no NVS,
 * new RF bias values, gain changes inside ABBA or unverified PBUS writes. */
static bool dco_move(const int target[2])
{
    int cur[2];
    if(!phy_dco_held()||!hardware_gain_ok()||!phy_dco_read_pair(cur))return false;
    for(unsigned axis=0;axis<2;++axis){
        while(cur[axis]!=target[axis]){
            int next[2]={cur[0],cur[1]},delta=target[axis]-cur[axis];
            if(delta>PHY_DCO_TRY_MAX)delta=PHY_DCO_TRY_MAX;
            if(delta< -PHY_DCO_TRY_MAX)delta= -PHY_DCO_TRY_MAX;
            next[axis]+=delta;
            if(phy_dco_try_pair(lab_packed_gain,cur,next)!=ESP_OK)return false;
            cur[0]=next[0];cur[1]=next[1];
        }
    }
    sample_dco[0]=cur[0];sample_dco[1]=cur[1];
    return dco_expected_ok();
}
static bool dco_score(unsigned id,uint8_t *raw,bool (*context_ok)(void),int64_t deadline,double *score)
{
    double sum=0;
    for(unsigned k=0;k<4;++k){
        if(!context_ok()||esp_timer_get_time()>deadline||!hardware_gain_ok()||!dco_expected_ok()||!capture(raw)||!dco_expected_ok()||!hardware_gain_ok())return false;
        cna_sums_t sums={0};
        for(unsigned n=0;n<C5V4_LEVEL_SAMPLE_BYTES;++n)cna_sums_add(&sums,raw[n]);
        float i,q;cna_mean(&sums,&i,&q);
        double m2,kurt,rail;power(raw,&m2,&kurt,&rail);
        if(!isfinite(m2)||m2<0.05||!isfinite(kurt))return false;
        /* Cell-centre means, not the host's integer nibble means. Rail
         * penalty discourages a false wrapped centre; neither is RF SNR. */
        sum+=i*i+q*q+64.0*rail;
        printf("PHYLAB_DCO_PROBE id=%u i=%d q=%d rep=%u mean_i=%.5f mean_q=%.5f m2=%.6f kurt=%.4f rails=%.6f\n",id,sample_dco[0],sample_dco[1],k,i,q,m2,kurt,rail);
    }
    *score=sum/4;return true;
}
static bool run_dco(unsigned id,uint8_t gain,uint8_t *raw,bool (*context_ok)(void),int64_t deadline)
{
    receiver_set_gain(gain);vTaskDelay(pdMS_TO_TICKS(100));
    if(!phylab_hold_current_gain()){
        printf("PHYLAB_DCO_REFUSED id=%u reason=no_matching_calibrated_row\n",id);return false;
    }
    arc_gain_tuple_t tuple;
    lab_packed_valid=arc_gain_tuple_decode(rf_gain_table(),s_current_gain,&tuple);
    if(!lab_packed_valid||s_current_gain!=gain)return false;
    lab_gain=gain;lab_packed_gain=tuple.packed_state;
    int original[2];
    if(!phy_dco_held()||!hardware_gain_ok()||!phy_dco_read_pair(original)){
        printf("PHYLAB_DCO_REFUSED id=%u reason=requires_stable_existing_hold\n",id);return false;
    }
    sample_dco[0]=original[0];sample_dco[1]=original[1];sample_dco_check=true;
    int best[2]={original[0],original[1]};double best_score=0;
    bool ok=dco_score(id,raw,context_ok,deadline,&best_score);
    /* Bounded local coordinate search, maximum 32 codes from entry. Search
     * samples are separate from all exported ABBA validation captures. */
    for(int step=4;step>=1&&ok;step/=2)
        for(unsigned round=0;round<4&&ok;++round){
            bool improved=false;
            for(unsigned axis=0;axis<2&&ok;++axis)
                for(int sign=-1;sign<=1&&ok;sign+=2){
                    int target[2]={best[0],best[1]};target[axis]+=sign*step;
                    if(target[axis]<0||target[axis]>511||abs(target[axis]-original[axis])>32)continue;
                    double score=0;
                    ok=dco_move(target)&&dco_score(id,raw,context_ok,deadline,&score);
                    if(ok&&score+0.01<best_score){best[0]=target[0];best[1]=target[1];best_score=score;improved=true;}
                    if(ok)ok=dco_move(best);
                }
            if(!improved)break;
        }
    /* Always restore before validation, even when search/context failed. */
    if(!dco_move(original)){
        printf("PHYLAB_FATAL dco_restore_failed reboot_required\n");fflush(stdout);esp_restart();
    }
    char name[32];snprintf(name,sizeof(name),"DCO_CENTER_G%u",gain);
    const profile_t p={name,0,{{0}},false};sample_profile=&p;
    printf("PHYLAB_PROFILE id=%u name=%s sequence=STOCK_TEST_TEST_STOCK\n",id,name);
    printf("PHYLAB_DCO_SELECTED id=%u original_i=%d original_q=%d target_i=%d target_q=%d objective=%.6f gain=%u packed=%08" PRIx32 "\n",id,original[0],original[1],best[0],best[1],best_score,gain,lab_packed_gain);
    if(ok)ok=measure(id,0,raw,context_ok,deadline);
    if(ok)ok=dco_move(best)&&measure(id,1,raw,context_ok,deadline)&&measure(id,2,raw,context_ok,deadline);
    if(!dco_move(original)){
        printf("PHYLAB_FATAL dco_restore_failed reboot_required\n");fflush(stdout);esp_restart();
    }
    printf("PHYLAB_RESTORE id=%u verified=1 held=1\n",id);
    if(ok)ok=measure(id,3,raw,context_ok,deadline);
    sample_dco_check=false;
    printf("PHYLAB_RESULT id=%u status=%s restored=1 carrier_delta_db=unknown cn=unknown video_sinad=unknown\n",id,ok?"PASS":"INCOMPLETE");
    return ok;
}
#else
bool phy_noise_lab_ack_byte(uint8_t byte){(void)byte;return false;}
#endif

void phy_noise_lab_run(bool (*context_ok)(void),unsigned dwell_ms,unsigned suite)
{
#ifndef C5VRX_PHY_PINNED
    (void)context_ok;(void)dwell_ms;(void)suite;printf("PHYLAB refused=unverified_phy\n");
#else
    if((dwell_ms!=300&&dwell_ms!=1200)||suite>13){printf("PHYLAB refused=arguments\n");return;}
    lab_dwell_ms=dwell_ms;
    lab_windows=(suite==7||suite==8||suite>=10)?16:4;
    sample_dco_check=false;
    uint8_t *raw=malloc(C5V4_LEVEL_SAMPLE_BYTES);
    if(!raw){printf("PHYLAB refused=memory\n");return;}
    /* Caller has paused the receiver; whole-suite task lease excludes tune,
     * gain, filter and sync PHY transactions, including during USB output. */
    phy_begin();
    const int64_t deadline=esp_timer_get_time()+INT64_C(240000000);
    bool ok=context_ok();
    /* Qualify in the same DC state used by the comparison. New calibrated
     * suites hold the matching loaded row before screening each gain.
     * Require zero observed signed rails and the six-sigma margin; never
     * adapt gain inside a comparison. */
    bool gain_ok=false;
    for(int g=rf_gain_table()->max_index;g>=2&&ok;--g){
        receiver_set_gain((uint8_t)g);vTaskDelay(pdMS_TO_TICKS(100));
        if(suite>=9&&!phylab_hold_current_gain()){
            printf("PHYLAB_GAIN_SKIP gain=%d reason=no_matching_calibrated_hold\n",g);continue;
        }
        bool clean=true;
        for(unsigned rep=0;rep<4&&ok;++rep){
            if(!capture(raw)){ok=false;break;}
            double m2,kurt,rail;power(raw,&m2,&kurt,&rail);
            double headroom=noise_headroom(raw);
            printf("PHYLAB_GAIN gain=%d rep=%u m2=%.6f kurt=%.4f rails=%.6f headroom6=%.3f\n",g,rep,m2,kurt,rail,headroom);
            clean=clean&&isfinite(m2)&&m2>0.67&&isfinite(kurt)&&kurt>1.75&&kurt<2.4&&rail==0.0&&headroom>=0.0;
        }
        if(clean&&ok){gain_ok=true;break;}
    }
    ok=ok&&gain_ok;
    lab_gain=s_current_gain;
    unsigned rejected=0;
    arc_gain_tuple_t tuple;
    lab_packed_valid=arc_gain_tuple_decode(rf_gain_table(),s_current_gain,&tuple);
    if(lab_packed_valid){
        lab_packed_gain=tuple.packed_state;
        printf("PHYLAB_GAIN_TUPLE gain=%u rf_stage=%u rf_code=%u bb_code=%u fine_code=%u packed=%08" PRIx32 " highest_rf_start=%u max_index=%u\n",
               s_current_gain,tuple.rf_stage,tuple.rf_code,tuple.bb_code,tuple.fine_code,tuple.packed_state,
               arc_gain_highest_rf_stage_start(rf_gain_table()),rf_gain_table()->max_index);
    } else ok=false;
    if(ok&&suite>=9){
        ok=phy_dco_held()&&phy_dco_read_pair(sample_dco);
        sample_dco_check=ok;
        if(!ok)printf("PHYLAB_POLICY_REFUSED reason=no_matching_calibrated_hold\n");
    }
    printf("PHYLAB_BEGIN fw=%s freq=%u gain=%u lane=2 dfilt=%d dwell_ms=%u suite=%u windows_per_stage=%u screen_dc=%s format=Ihigh_Qlow persistent=0 outer_restore=required\n",firmware_id(),rf_frequency_mhz(),s_current_gain,phy_dfilt_hw_mode(),lab_dwell_ms,suite,lab_windows,suite>=9?"calibrated":"vendor");
    for(unsigned id=0;id<16&&ok&&suite==0;++id){
        unsigned n=id^(id>>1);
        profile_t p={"NF_AUTO",3,{{0x600A7018,0x10800000,((n&1)?0x800000:0)|((n&2)?0x10000000:0)},
                                  {0x600A7C44,1,(n>>2)&1},{0x600A7C50,1,(n>>3)&1}},false};
        printf("PHYLAB_PATTERN id=%u pattern=%X\n",id,n);
        ok=run_profile(id,&p,raw,context_ok,deadline);
        /* A failed entry has no test captures. Only after exact rollback
         * may another independent profile be attempted. IQ/context faults
         * still stop the whole suite; restore failure never returns. */
        if(!ok&&entry_rejected&&context_ok()&&esp_timer_get_time()<deadline){++rejected;ok=true;}
    }
    for(unsigned i=0;i<sizeof(profiles)/sizeof(profiles[0])&&ok&&suite==0;++i){
        if(i==6 && !(MM(0x600A7C14)&0x2000)){state(16+i,"INSPECT");printf("PHYLAB_SKIP id=%u name=%s reason=slot0_inactive_other_slots_readonly\n",16+i,profiles[i].name);continue;}
        ok=run_profile(16+i,&profiles[i],raw,context_ok,deadline);
        if(!ok&&entry_rejected&&context_ok()&&esp_timer_get_time()<deadline){++rejected;ok=true;}
    }
    for(unsigned i=0;i<sizeof(audit_profiles)/sizeof(audit_profiles[0])&&ok&&suite==1;++i){
        ok=run_profile(32+i,&audit_profiles[i],raw,context_ok,deadline);
        if(!ok&&entry_rejected&&context_ok()&&esp_timer_get_time()<deadline){++rejected;ok=true;}
    }
    for(unsigned i=0;i<3&&ok&&suite==2;++i)ok=run_output(64+i,raw,context_ok,deadline);
    for(unsigned i=0;i<2&&ok&&suite==3;++i)ok=run_adc(80+i,i,raw,context_ok,deadline);
    for(unsigned i=0;i<sizeof(packet_profiles)/sizeof(packet_profiles[0])&&ok&&suite==4;++i){
        ok=run_profile(96+i,&packet_profiles[i],raw,context_ok,deadline);
        if(!ok&&entry_rejected&&context_ok()&&esp_timer_get_time()<deadline){++rejected;ok=true;}
    }
    for(unsigned i=0;i<sizeof(clock_profiles)/sizeof(clock_profiles[0])&&ok&&suite==5;++i){
        ok=run_profile(112+i,&clock_profiles[i],raw,context_ok,deadline);
        if(!ok&&entry_rejected&&context_ok()&&esp_timer_get_time()<deadline){++rejected;ok=true;}
    }
    static const uint8_t gains[]={72,74,76,78,80,83};
    for(unsigned pass=0;pass<2&&ok&&suite==6;++pass)
        for(unsigned i=0;i<sizeof(gains)&&ok;++i){unsigned n=pass?sizeof(gains)-1-i:i;ok=run_gain_row(128+pass*6+i,gains[n],raw,context_ok,deadline);}
    for(unsigned pass=0;pass<2&&ok&&suite==7;++pass)
        for(unsigned i=0;i<3&&ok;++i){unsigned n=pass?2-i:i;ok=run_profile(160+pass*3+i,&combo_profiles[n],raw,context_ok,deadline);}
    static const uint8_t dc_gains[]={78,80,83};
    for(unsigned i=0;i<sizeof(dc_gains)&&ok&&suite==8;++i)ok=run_dco(176+i,dc_gains[i],raw,context_ok,deadline);
    for(unsigned i=0;i<sizeof(policy_profiles)/sizeof(policy_profiles[0])&&ok&&suite==9;++i)
        ok=run_profile(192+i,&policy_profiles[i],raw,context_ok,deadline);
    if(ok&&suite==10){
        const unsigned code=phy_i2c_readReg(0x63,1,21)&31;
        ok=run_locap(208,code,raw,context_ok,deadline);
        if(ok)ok=run_locap(209,code-1,raw,context_ok,deadline);
        if(ok)ok=run_locap(210,code+1,raw,context_ok,deadline);
    }
    for(unsigned i=0;i<5&&ok&&suite==11;++i){
        const unsigned n=i<4?i:10; /* all four LLTF states plus sham */
        ok=run_profile(224+i,&policy_profiles[n],raw,context_ok,deadline);
    }
    /* Independent order control for the small 5805-MHz LLTF00 clue.
     * Start/end shams and repeated unchanged LLTF10 bracket two LLTF00
     * executions; the candidate is never the first profile. */
    static const unsigned confirm_order[]={10,1,0,1,0,10};
    for(unsigned i=0;i<sizeof(confirm_order)/sizeof(confirm_order[0])&&ok&&suite==12;++i)
        ok=run_profile(240+i,&policy_profiles[confirm_order[i]],raw,context_ok,deadline);
    for(unsigned i=0;i<sizeof(aux_profiles)/sizeof(aux_profiles[0])&&ok&&suite==13;++i)
        ok=run_profile(256+i,&aux_profiles[i],raw,context_ok,deadline);
    sample_dco_check=false;
    printf("PHYLAB_END status=%s rejected=%u restored=1 input_referred_improvement=unknown\n",ok?(rejected?"COMPLETE_WITH_REJECTIONS":"PASS"):"INCOMPLETE",rejected);
    phy_end();free(raw);
#endif
}

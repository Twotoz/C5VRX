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
#ifndef MM
#define MM(a) (*(volatile uint32_t *)(uintptr_t)(a))
#endif
static uint8_t lab_gain;
static bool entry_rejected;
static unsigned lab_dwell_ms=300;
typedef struct { uint32_t addr, mask, value; } patch_t;
typedef struct { const char *name; unsigned n; patch_t p[4]; bool analog; } profile_t;
static const profile_t *sample_profile;
static uint32_t sample_expected[4];
static bool verify(const profile_t *p,const uint32_t *values);
extern unsigned char phy_param[];
extern uint8_t phy_i2c_readReg(uint8_t, uint8_t, uint8_t);
extern void phy_i2c_writeReg(uint8_t, uint8_t, uint8_t, uint8_t);
extern void phy_11p_set(uint8_t, uint8_t);
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
static const uint32_t watch[] = {
    0x600A7018,0x600A7C44,0x600A7C50,0x600A7CE4,0x600A7030,
    0x600A7048,0x600A71C4,0x600A7904,0x600A7074,0x600A0438,
    0x600A0C0C,0x600A043C,0x600A0430,0x600A0448,0x600A7C14,
    0x600A7C18,0x600A7C1C,0x600A7C20,0x600A7810,0x600A7814,
    0x600A7818,0x600A781C,0x600A7820,0x600A7824,0x600A7828,
    0x600A782C,0x600A7830,0x600A0C38,0x600A4C5C,0x600A7800
};
static void state(unsigned id, const char *stage)
{
    for (unsigned i=0;i<sizeof(watch)/sizeof(watch[0]);++i)
        printf("PHYLAB_REG id=%u stage=%s addr=%08" PRIx32 " value=%08" PRIx32 "\n",id,stage,watch[i],MM(watch[i]));
    printf("PHYLAB_PARAM id=%u stage=%s flags=%02x/%02x iq_scale_sel=%u\n",id,stage,phy_param[0x26],phy_param[0x27],phy_param[650]);
    for (uint8_t r=6;r<=13;++r)
        printf("PHYLAB_I2C id=%u stage=%s block=67 host=1 reg=%u value=%02x\n",id,stage,r,phy_i2c_readReg(0x67,1,r));
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
static bool measure(unsigned id, unsigned step, uint8_t *raw, bool (*context_ok)(void),int64_t deadline)
{
    const char *stage=(step==0||step==3)?"STOCK":"TEST";
    vTaskDelay(pdMS_TO_TICKS(lab_dwell_ms));
    state(id,stage);
    if(!verify(sample_profile,sample_expected))return false;
    for(unsigned rep=0;rep<4;++rep) {
        if(esp_timer_get_time()>deadline||!context_ok()||s_current_gain!=lab_gain||
           !verify(sample_profile,sample_expected)||!capture(raw)) return false;
        double m2,kurt,rail; power(raw,&m2,&kurt,&rail);
        if(!isfinite(m2)||m2<=0.05||!isfinite(kurt)) return false;
        uint32_t hash=2166136261u;
        for(unsigned k=0;k<C5V4_LEVEL_SAMPLE_BYTES;++k) hash=(hash^raw[k])*16777619u;
        printf("PHYLAB_IQ_BEGIN id=%u step=%u stage=%s rep=%u bytes=%u rate_hz=%u gain=%u freq=%u offset=%d m2=%.6f kurt=%.4f rails=%.6f fnv1a=%08" PRIx32 " t_us=%" PRId64 "\n",
               id,step,stage,rep,C5V4_LEVEL_SAMPLE_BYTES,IQ_RATE_HZ,s_current_gain,rf_frequency_mhz(),rf_offset_khz(),m2,kurt,rail,hash,esp_timer_get_time());
        static const char hex[]="0123456789abcdef"; char line[513];
        for(unsigned off=0;off<C5V4_LEVEL_SAMPLE_BYTES;off+=256) {
            unsigned n=C5V4_LEVEL_SAMPLE_BYTES-off; if(n>256)n=256;
            for(unsigned k=0;k<n;++k) { line[2*k]=hex[raw[off+k]>>4];line[2*k+1]=hex[raw[off+k]&15]; }
            line[2*n]=0; printf("PHYLAB_IQ_DATA offset=%u %s\n",off,line);vTaskDelay(pdMS_TO_TICKS(2));
        }
        printf("PHYLAB_IQ_END fnv1a=%08" PRIx32 "\n",hash);
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
    uint32_t saved[4],enabled[4];uint8_t analog[8]={0},flags[2]={0};
    for(unsigned i=0;i<p->n;++i){saved[i]=MM(p->p[i].addr)&p->p[i].mask;enabled[i]=p->p[i].value;}
    if(p->analog){flags[0]=phy_param[0x26];flags[1]=phy_param[0x27];for(uint8_t i=0;i<8;++i)analog[i]=phy_i2c_readReg(0x67,1,6+i);}
    sample_profile=p;for(unsigned i=0;i<p->n;++i)sample_expected[i]=saved[i];
    printf("PHYLAB_PROFILE id=%u name=%s sequence=STOCK_TEST_TEST_STOCK\n",id,p->name);
    bool ok=measure(id,0,raw,context_ok,deadline);
    if(ok) {
        if(p->analog)phy_11p_set(1,0);
        else for(unsigned i=0;i<p->n;++i)masked(p->p[i].addr,p->p[i].mask,p->p[i].value);
        ok=verify(p,enabled);
        if(p->analog){ok=ok&&phy_param[0x26]==1&&phy_param[0x27]==0;for(uint8_t i=0;i<8;++i)ok=ok&&phy_i2c_readReg(0x67,1,6+i)==60;}
        printf("PHYLAB_APPLY id=%u verified=%u\n",id,ok);
        if(!ok){entry_rejected=true;state(id,"REJECTED_ENTRY");}
        for(unsigned i=0;i<p->n;++i)sample_expected[i]=enabled[i];
        if(ok)ok=measure(id,1,raw,context_ok,deadline);
        if(ok)ok=measure(id,2,raw,context_ok,deadline);
    }
    if(p->analog){phy_param[0x26]=flags[0];phy_param[0x27]=flags[1];for(uint8_t i=0;i<8;++i)phy_i2c_writeReg(0x67,1,6+i,analog[i]);}
    for(unsigned i=0;i<p->n;++i)masked(p->p[i].addr,p->p[i].mask,saved[i]);
    bool restored=verify(p,saved);
    if(p->analog){restored=restored&&phy_param[0x26]==flags[0]&&phy_param[0x27]==flags[1];for(uint8_t i=0;i<8;++i)restored=restored&&phy_i2c_readReg(0x67,1,6+i)==analog[i];}
    printf("PHYLAB_RESTORE id=%u verified=%u\n",id,restored);
    if(!restored){printf("PHYLAB_FATAL restore_failed reboot_required\n");fflush(stdout);esp_restart();}
    if(!ok)state(id,"RESTORED_AFTER_ABORT");
    for(unsigned i=0;i<p->n;++i)sample_expected[i]=saved[i];
    if(ok)ok=measure(id,3,raw,context_ok,deadline);
    printf("PHYLAB_RESULT id=%u status=%s restored=1 carrier_delta_db=unknown cn=unknown video_sinad=unknown\n",id,ok?"PASS":(entry_rejected?"REJECTED_ENTRY":"INCOMPLETE"));
    return ok;
}
#endif

void phy_noise_lab_run(bool (*context_ok)(void),unsigned dwell_ms,unsigned suite)
{
#ifndef C5VRX_PHY_PINNED
    (void)context_ok;(void)dwell_ms;(void)suite;printf("PHYLAB refused=unverified_phy\n");
#else
    if((dwell_ms!=300&&dwell_ms!=1200)||suite>1){printf("PHYLAB refused=arguments\n");return;}
    lab_dwell_ms=dwell_ms;
    uint8_t *raw=malloc(C5V4_LEVEL_SAMPLE_BYTES);
    if(!raw){printf("PHYLAB refused=memory\n");return;}
    /* Caller has paused the receiver; whole-suite task lease excludes tune,
     * gain, filter and sync PHY transactions, including during USB output. */
    phy_begin();
    const int64_t deadline=esp_timer_get_time()+INT64_C(240000000);
    bool ok=context_ok();
    /* Choose the highest row with <0.2% signed rails; never adapt gain inside
     * a comparison. Rail/folding proxies and per-state raw data remain visible. */
    bool gain_ok=false;
    for(int g=rf_gain_table()->max_index;g>=2&&ok;--g){
        receiver_set_gain((uint8_t)g);vTaskDelay(pdMS_TO_TICKS(100));
        bool clean=true;
        for(unsigned rep=0;rep<4&&ok;++rep){
            if(!capture(raw)){ok=false;break;}
            double m2,kurt,rail;power(raw,&m2,&kurt,&rail);
            printf("PHYLAB_GAIN gain=%d rep=%u m2=%.6f kurt=%.4f rails=%.6f\n",g,rep,m2,kurt,rail);
            clean=clean&&isfinite(m2)&&m2>0.67&&isfinite(kurt)&&kurt>1.75&&kurt<2.4&&rail<0.002;
        }
        if(clean&&ok){gain_ok=true;break;}
    }
    ok=ok&&gain_ok;
    lab_gain=s_current_gain;
    unsigned rejected=0;
    printf("PHYLAB_BEGIN fw=%s freq=%u gain=%u lane=2 dfilt=%d dwell_ms=%u suite=%u format=Ihigh_Qlow persistent=0\n",firmware_id(),rf_frequency_mhz(),s_current_gain,phy_dfilt_hw_mode(),lab_dwell_ms,suite);
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
    printf("PHYLAB_END status=%s rejected=%u restored=1 input_referred_improvement=unknown\n",ok?(rejected?"COMPLETE_WITH_REJECTIONS":"PASS"):"INCOMPLETE",rejected);
    phy_end();free(raw);
#endif
}

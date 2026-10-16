/* C5VRX by Twotoz/contributors. Camera-period increments run entirely in
 * the alarm ISR: no task refill or flash planner is needed during holdover.
 * Vertical tips separated by4.7us share a bounded ISR, exposing real DAC data
 * in every gap. No hard WCET or physical monitor-lock claim follows. */
#include "sync_live_output.h"
#include "sync_edge_clock.h"
#include "sync_live_clock.h"
#include "esp_attr.h"
#include "esp_cpu.h"
#include "esp_heap_caps.h"
#include "esp_private/esp_clk.h"
#include "soc/gpio_reg.h"
#include "soc/io_mux_reg.h"
#include "soc/soc.h"
#include <stdlib.h>
#include <string.h>

#define SLO_MASK UINT32_C(0x01801b00)
#define SLO_LEAD 240u
#define SLO_LATE 160u
#define SLO_BATCH_CYCLES (224u*240u)
typedef struct {uint64_t start;uint16_t width;uint8_t kind;} slo_tip_t;
typedef struct {
    sync_live_gate_t *planner;
    slc_t clock;
    slo_tip_t tip;
    uint32_t saved_mux[6],saved_pad[6],generation;
    slo_live_fn live;slo_native_fn native;void *native_arg;
    slo_status_t status;
    bool saved,configured,quarantined;
} slo_t;
static slo_t *s_output;
static const unsigned pins[6]={23,24,11,12,8,9};
static const uint32_t pads[6]={IO_MUX_GPIO23_REG,IO_MUX_GPIO24_REG,IO_MUX_GPIO11_REG,
    IO_MUX_GPIO12_REG,IO_MUX_GPIO8_REG,IO_MUX_GPIO9_REG};

static void IRAM_ATTR fail(slo_t *s,uint32_t code)
{
    /* Never energize pads here if ownership has already changed. Normal
     * pulse completion always enables them before any next decision. */
    s->status.fault=code;s->status.active=false;
    (void)sync_edge_clock_alarm_off();
}

static bool IRAM_ATTR output_alarm(uint64_t count,void *arg)
{
    (void)count;slo_t *s=arg;
    uint32_t batch_start=esp_cpu_get_cycle_count(),n=0;
    for(;;) {
        if(!s->status.active)return false;
        if(!s->live(s->generation)){fail(s,1);return false;}
        slo_tip_t tip=s->tip;
        uint64_t now;
        if(!sync_edge_clock_read(&now)){fail(s,3);return false;}
        uint32_t wait=esp_cpu_get_cycle_count();
        while(now<tip.start) {
            if(esp_cpu_get_cycle_count()-wait>(SLO_LEAD+SLO_LATE)*12u ||
               !sync_edge_clock_read(&now)){fail(s,4);return false;}
        }
        uint64_t late=now-tip.start;
        if(late>s->status.max_late_ticks)
            s->status.max_late_ticks=late>UINT32_MAX?UINT32_MAX:(uint32_t)late;
        if(late>SLO_LATE) {
            /* Do not emit a pulse outside its8us admission window. One lost
             * deadline is missing evidence, not a reason to abandon the
             * retained clock forever. Advance on the SAME absolute grid. */
            ++s->status.missed;s->status.miss_kind=tip.kind;
            goto advance;
        }
        bool native=s->native && s->native(tip.start,tip.width,(slg_kind_t)tip.kind,s->native_arg);
        /* Revalidate AFTER evidence callback, immediately before touching OE. */
        if(!s->status.active || !s->live(s->generation)){fail(s,1);return false;}
        if(native)++s->status.suppressed;
        else {
            REG_WRITE(GPIO_ENABLE_W1TC_REG,SLO_MASK);
            uint32_t start=esp_cpu_get_cycle_count();
            while(esp_cpu_get_cycle_count()-start<(uint32_t)tip.width*12u){}
            REG_WRITE(GPIO_ENABLE_W1TS_REG,SLO_MASK);
            ++s->status.repaired;
        }
advance:
        if(++n>8u || esp_cpu_get_cycle_count()-batch_start>SLO_BATCH_CYCLES){fail(s,6);return false;}
        slg_pulse_t pulse;
        if(!slc_next(&s->clock,&pulse)){++s->status.queue_low;fail(s,2);return false;}
        slo_tip_t next={pulse.start,(uint16_t)pulse.width,(uint8_t)pulse.kind};
        s->tip=next;
        if(!sync_edge_clock_read(&now)){fail(s,3);return false;}
        if(next.start>now && next.start-now>SLO_LEAD+60u) {
            if(n>1u)++s->status.batches;
            if(!sync_edge_clock_alarm_at(next.start-SLO_LEAD))fail(s,7);
            return false;
        }
        /* Driver rearm cannot fit a4.7us gap: continue on absolute timer,
         * bounded to one vertical burst. Pads remain ON while waiting. */
    }
}

bool sync_live_output_poll(void)
{
    slo_t *s=s_output;
    if(!s || !s->status.active || s->quarantined)return false;
    if(!s->live(s->generation))return false;
    return true;
}

bool sync_live_output_end(void)
{
    slo_t *s=s_output;
    if(!s)return true;
    if(s->quarantined)return false;
    s->status.active=false;__sync_synchronize();
    if(!sync_edge_clock_alarm_bind(NULL,NULL)){s->quarantined=true;return false;}
    if(s->configured) {
        REG_WRITE(GPIO_ENABLE_W1TS_REG,SLO_MASK);
        for(unsigned i=0;i<6u;++i) {
            REG_WRITE(GPIO_FUNC0_OUT_SEL_CFG_REG+4u*pins[i],s->saved_mux[i]);
            REG_WRITE(pads[i],s->saved_pad[i]);
        }
        for(unsigned i=0;i<6u;++i)
            if(REG_READ(GPIO_FUNC0_OUT_SEL_CFG_REG+4u*pins[i])!=s->saved_mux[i] ||
               REG_READ(pads[i])!=s->saved_pad[i]){s->quarantined=true;return false;}
        if((REG_READ(GPIO_ENABLE_REG)&SLO_MASK)!=SLO_MASK){s->quarantined=true;return false;}
    }
    sync_live_gate_destroy(s->planner);free(s);s_output=NULL;return true;
}

bool sync_live_output_begin(const slg_model_t *model,slg_standard_t fallback,
    uint32_t generation,slo_live_fn live,slo_native_fn native,void *arg)
{
    if(s_output || !live || !live(generation) || esp_clk_cpu_freq()!=240000000)return false;
    uint64_t now;
    if(!sync_edge_clock_read(&now) || now>UINT64_MAX-20000u)return false;
    slo_t *s=heap_caps_calloc(1,sizeof(*s),MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT);
    if(!s)return false;
    s_output=s;s->live=live;s->generation=generation;s->native=native;s->native_arg=arg;
    const slg_config_t config={SLO_LEAD,SLO_LATE,100u,100u,UINT32_MAX,64u};
    s->planner=sync_live_gate_create(&config);
    if(!s->planner)goto failed;
    if(model && model->qualified) {
        if(!sync_live_gate_model(s->planner,model,now+20000u))goto failed;
    } else if(!sync_live_gate_nominal(s->planner,fallback,now+20000u,0u,0u,now))goto failed;
    if(!sync_live_gate_arm(s->planner))goto failed;
    slg_clock_seed_t seed;slg_pulse_t first;
    if(!sync_live_gate_export(s->planner,&seed) || !slc_init(&s->clock,&seed) ||
       !slc_next(&s->clock,&first))goto failed;
    s->tip=(slo_tip_t){first.start,(uint16_t)first.width,(uint8_t)first.kind};
    sync_live_gate_destroy(s->planner);s->planner=NULL;
    if((REG_READ(GPIO_ENABLE_REG)&SLO_MASK)!=SLO_MASK)goto failed;
    for(unsigned i=0;i<6u;++i) {
        uint32_t reg=GPIO_FUNC0_OUT_SEL_CFG_REG+4u*pins[i];
        s->saved_mux[i]=REG_READ(reg);s->saved_pad[i]=REG_READ(pads[i]);
        if((s->saved_mux[i]&GPIO_FUNC0_OUT_SEL_M)!=48u+i ||
           (s->saved_mux[i]&(GPIO_FUNC0_OUT_INV_SEL|GPIO_FUNC0_OE_INV_SEL)))goto failed;
    }
    s->saved=true;s->status.active=true;
    if(!sync_live_output_poll())goto failed;
    if(!sync_edge_clock_alarm_bind(output_alarm,s))goto failed;
    s->configured=true;
    for(unsigned i=0;i<6u;++i) {
        REG_WRITE(pads[i],s->saved_pad[i]&~(FUN_PU|FUN_PD));
        REG_WRITE(GPIO_FUNC0_OUT_SEL_CFG_REG+4u*pins[i],s->saved_mux[i]|GPIO_FUNC0_OE_SEL);
    }
    __sync_synchronize();
    if(!live(generation) || !sync_edge_clock_alarm_at(s->tip.start-SLO_LEAD))goto failed;
    return true;
failed:
    (void)sync_live_output_end();return false;
}

void sync_live_output_status(slo_status_t *out)
{
    if(out)*out=s_output?s_output->status:(slo_status_t){0};
}

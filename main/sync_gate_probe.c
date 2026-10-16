/* C5VRX by Twotoz and contributors. A manually invoked three-field analogue
 * hypothesis probe: gate DAC pad OE while real PARLIO data continues. R7 is a
 * 200-ohm shunt; a zero-volt sync tip is a prediction requiring measurement.
 * No raw IQ, LUT, descriptor, DMA, or PHY writes. This is not a live flywheel. */
#include "sync_gate_probe.h"
#include "sync_gate_schedule.h"
#include "driver/gptimer.h"
#include "esp_attr.h"
#include "esp_cpu.h"
#include "esp_private/esp_clk.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "soc/gpio_reg.h"
#include "soc/gpio_sig_map.h"
#include "soc/io_mux_reg.h"
#include "soc/soc.h"
#include <stdio.h>
#include <string.h>

#define GATE_MASK UINT32_C(0x01801b00)
#define GATE_FIELDS 3u
#define GATE_INITIAL_TICKS 2000u
#define GATE_LATE_TICKS 160u
#define GATE_TIMEOUT_US 250000

typedef enum {
    GATE_RUNNING, GATE_COMPLETE, GATE_LATE, GATE_CONTEXT, GATE_COUNT,
    GATE_REARM, GATE_SCHEDULE, GATE_TIMEOUT
} gate_reason_t;

typedef struct {
    gptimer_handle_t timer;
    bool (*live)(uint32_t);
    uint32_t generation;
    sgs_t schedule;
    sgs_pulse_t pulse;
    volatile bool done;
    volatile gate_reason_t reason;
    volatile uint32_t pulses, eq, broad, normal;
    volatile uint32_t min_hold, max_hold, max_late;
    volatile uint32_t kind_min[3],kind_max[3];
} gate_context_t;

static gate_context_t s_gate;
static bool s_busy;
static const unsigned s_pins[6] = {23,24,11,12,8,9};
static const uint32_t s_iomux[6] = {
    IO_MUX_GPIO23_REG,IO_MUX_GPIO24_REG,IO_MUX_GPIO11_REG,
    IO_MUX_GPIO12_REG,IO_MUX_GPIO8_REG,IO_MUX_GPIO9_REG
};
static const unsigned s_signals[6] = {
    PARL_TX_DATA0_IDX,PARL_TX_DATA1_IDX,PARL_TX_DATA2_IDX,
    PARL_TX_DATA3_IDX,PARL_TX_DATA4_IDX,PARL_TX_DATA5_IDX
};

static inline uint32_t matrix_reg(unsigned pin)
{ return GPIO_FUNC0_OUT_SEL_CFG_REG + 4u * pin; }

/* No flash callee, driver call, or log between disabling and restoring OE.
 * Counter wrap is valid for these <=28us unsigned cycle differences. IRQs
 * above this ISR may still extend the measured hold; no hard WCET is claimed. */
static uint32_t IRAM_ATTR hold_tip(uint32_t ticks)
{
    REG_WRITE(GPIO_ENABLE_W1TC_REG,GATE_MASK);
    uint32_t start=esp_cpu_get_cycle_count();
    while ((uint32_t)(esp_cpu_get_cycle_count()-start)<ticks*12u) { }
    REG_WRITE(GPIO_ENABLE_W1TS_REG,GATE_MASK);
    return (uint32_t)(esp_cpu_get_cycle_count()-start);
}

static bool IRAM_ATTR gate_alarm(gptimer_handle_t timer,
    const gptimer_alarm_event_data_t *event, void *opaque)
{
    gate_context_t *g=opaque;
    (void)event;
    if (g->done) return false;
    if (!g->live(g->generation)) {
        g->reason=GATE_CONTEXT;g->done=true;return false;
    }
    uint64_t now;
    if(gptimer_get_raw_count(timer,&now)!=ESP_OK) {
        g->reason=GATE_COUNT;g->done=true;return false;
    }
    uint64_t target=GATE_INITIAL_TICKS+g->pulse.start;
    if(now<target || now-target>GATE_LATE_TICKS) {
        if(now>=target && now-target<UINT32_MAX && now-target>g->max_late)
            g->max_late=(uint32_t)(now-target);
        g->reason=GATE_LATE;g->done=true;return false;
    }
    uint32_t late=(uint32_t)(now-target);
    if(late>g->max_late)g->max_late=late;
    uint32_t held=hold_tip(g->pulse.width);
    if(held<g->min_hold)g->min_hold=held;
    if(held>g->max_hold)g->max_hold=held;
    if(held<g->kind_min[g->pulse.kind])g->kind_min[g->pulse.kind]=held;
    if(held>g->kind_max[g->pulse.kind])g->kind_max[g->pulse.kind]=held;
    ++g->pulses;
    if(g->pulse.kind==SGS_EQ)++g->eq;
    else if(g->pulse.kind==SGS_BROAD)++g->broad;
    else ++g->normal;
    /* All driver/schedule operations occur after the six pads are enabled. */
    if(!sgs_next(&g->schedule,&g->pulse)) {
        g->reason=GATE_SCHEDULE;g->done=true;return false;
    }
    if(g->pulse.field>=GATE_FIELDS) {
        g->reason=GATE_COMPLETE;g->done=true;return false;
    }
    gptimer_alarm_config_t alarm={.alarm_count=GATE_INITIAL_TICKS+g->pulse.start};
    if(gptimer_set_alarm_action(timer,&alarm)!=ESP_OK) {
        g->reason=GATE_REARM;g->done=true;
    }
    return false;
}

void sync_gate_probe(bool pal,uint32_t generation,bool (*live)(uint32_t))
{
    if(__atomic_exchange_n(&s_busy,true,__ATOMIC_ACQ_REL)) {
        printf("SYNC_GATE end reason=busy generation=%lu restored=1\n",(unsigned long)generation);
        return;
    }
    uint32_t saved_mux[6]={0},saved_pad[6]={0},saved_enable=0;
    bool saved=false,mux_changed=false,enabled=false,started=false,restored=true;
    const char *failure=NULL;
    gate_context_t *g=&s_gate;
    memset(g,0,sizeof(*g));g->live=live;g->generation=generation;g->min_hold=UINT32_MAX;
    for(unsigned j=0;j<3;++j)g->kind_min[j]=UINT32_MAX;
    printf("SYNC_GATE begin standard=%s generation=%lu fields=%u cpu_hz=%d automatic=0\n",
        pal?"PAL":"NTSC",(unsigned long)generation,GATE_FIELDS,esp_clk_cpu_freq());
    if(!live || esp_clk_cpu_freq()!=240000000) {failure="cpu_or_callback";goto cleanup;}
    if(!live(generation)) {failure="context";goto cleanup;}
    saved_enable=REG_READ(GPIO_ENABLE_REG);
    for(unsigned j=0;j<6;++j) {
        saved_mux[j]=REG_READ(matrix_reg(s_pins[j]));
        saved_pad[j]=REG_READ(s_iomux[j]);
    }
    saved=true;
    if((saved_enable&GATE_MASK)!=GATE_MASK) {failure="pads_disabled";goto cleanup;}
    for(unsigned j=0;j<6;++j) {
        if((saved_mux[j]&GPIO_FUNC0_OUT_SEL_M)!=s_signals[j] ||
            (saved_mux[j]&(GPIO_FUNC0_OUT_INV_SEL|GPIO_FUNC0_OE_INV_SEL))) {failure="data_mux";goto cleanup;}
        if(saved_pad[j]&(FUN_PU|FUN_PD)) {failure="weak_pull";goto cleanup;}
    }
    if(!sgs_init(&g->schedule,pal?SGS_PAL:SGS_NTSC) ||
        !sgs_next(&g->schedule,&g->pulse)) {failure="schedule";goto cleanup;}
    gptimer_config_t config={.clk_src=GPTIMER_CLK_SRC_DEFAULT,
        .direction=GPTIMER_COUNT_UP,.resolution_hz=SGS_TIMER_HZ,.intr_priority=3};
    if(gptimer_new_timer(&config,&g->timer)!=ESP_OK){failure="timer_create";goto cleanup;}
    gptimer_event_callbacks_t callbacks={.on_alarm=gate_alarm};
    if(gptimer_register_event_callbacks(g->timer,&callbacks,g)!=ESP_OK){failure="timer_callback";goto cleanup;}
    if(gptimer_enable(g->timer)!=ESP_OK){failure="timer_enable";goto cleanup;}
    enabled=true;
    if(gptimer_set_raw_count(g->timer,0)!=ESP_OK){failure="timer_count";goto cleanup;}
    gptimer_alarm_config_t alarm={.alarm_count=GATE_INITIAL_TICKS+g->pulse.start};
    if(gptimer_set_alarm_action(g->timer,&alarm)!=ESP_OK){failure="timer_alarm";goto cleanup;}
    if(!live(generation)){failure="context";goto cleanup;}
    for(unsigned j=0;j<6;++j) {
        REG_WRITE(matrix_reg(s_pins[j]),
            (saved_mux[j]|GPIO_FUNC0_OE_SEL)&~GPIO_FUNC0_OE_INV_SEL);
    }
    mux_changed=true;
    for(unsigned j=0;j<6;++j)
        if(REG_READ(matrix_reg(s_pins[j]))!=
            ((saved_mux[j]|GPIO_FUNC0_OE_SEL)&~GPIO_FUNC0_OE_INV_SEL)) {
            failure="oe_mux_readback";goto cleanup;
        }
    if(gptimer_start(g->timer)!=ESP_OK){failure="timer_start";goto cleanup;}
    started=true;
    int64_t began=esp_timer_get_time();
    while(!g->done && esp_timer_get_time()-began<GATE_TIMEOUT_US)vTaskDelay(1);
    if(!g->done){g->reason=GATE_TIMEOUT;g->done=true;}
cleanup:
    /* Stop callback delivery before restoring muxes or releasing ownership. */
    g->done=true;
    bool reusable=true;
    if(started && gptimer_stop(g->timer)!=ESP_OK){restored=false;failure="timer_stop";reusable=false;}
    if(enabled && gptimer_disable(g->timer)!=ESP_OK){restored=false;failure="timer_disable";reusable=false;}
    if(g->timer && gptimer_del_timer(g->timer)!=ESP_OK){
        restored=false;failure="timer_delete";reusable=false;
    }
    if(saved && mux_changed) {
        REG_WRITE(GPIO_ENABLE_W1TS_REG,saved_enable&GATE_MASK);
        REG_WRITE(GPIO_ENABLE_W1TC_REG,(~saved_enable)&GATE_MASK);
        for(unsigned j=0;j<6;++j)REG_WRITE(matrix_reg(s_pins[j]),saved_mux[j]);
    }
    if(saved) {
        if((REG_READ(GPIO_ENABLE_REG)&GATE_MASK)!=(saved_enable&GATE_MASK))restored=false;
        for(unsigned j=0;j<6;++j)
            if(REG_READ(matrix_reg(s_pins[j]))!=saved_mux[j] ||
                REG_READ(s_iomux[j])!=saved_pad[j])restored=false;
    }
    printf("SYNC_GATE end standard=%s generation=%lu reason=%s code=%u pulses=%lu eq=%lu broad=%lu normal=%lu hold_min_cycles=%lu hold_max_cycles=%lu eq_min_cycles=%lu eq_max_cycles=%lu broad_min_cycles=%lu broad_max_cycles=%lu normal_min_cycles=%lu normal_max_cycles=%lu late_max_ticks=%lu restored=%u monitor_lock_proven=0\n",
        pal?"PAL":"NTSC",(unsigned long)generation,failure?failure:"isr",
        (unsigned)g->reason,(unsigned long)g->pulses,(unsigned long)g->eq,
        (unsigned long)g->broad,(unsigned long)g->normal,
        (unsigned long)(g->pulses?g->min_hold:0u),(unsigned long)g->max_hold,
        (unsigned long)(g->eq?g->kind_min[SGS_EQ]:0u),(unsigned long)g->kind_max[SGS_EQ],
        (unsigned long)(g->broad?g->kind_min[SGS_BROAD]:0u),(unsigned long)g->kind_max[SGS_BROAD],
        (unsigned long)(g->normal?g->kind_min[SGS_NORMAL]:0u),(unsigned long)g->kind_max[SGS_NORMAL],
        (unsigned long)g->max_late,(unsigned)restored);
    /* A failed deletion must not let an old callback alias a new context. */
    if(reusable)__atomic_store_n(&s_busy,false,__ATOMIC_RELEASE);
}

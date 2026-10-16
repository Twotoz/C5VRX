/* C5VRX by Twotoz and contributors. A manually invoked bounded analogue
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
#define GATE_LONG_TIMEOUT_US 3500000
#define GATE_BATCH_PULSES 7u
#define GATE_BATCH_MAX_CYCLES (7u*640u*12u) /* 224us manual ISR ceiling */

typedef enum {
    GATE_RUNNING, GATE_COMPLETE, GATE_LATE, GATE_CONTEXT, GATE_COUNT,
    GATE_REARM, GATE_SCHEDULE, GATE_TIMEOUT, GATE_BATCH
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
    /* Only the vertical bursts are precomputed, not an entire field. This
     * avoids driver calls and flash schedule fetches in the 4.7us gaps. */
    sgs_pulse_t batch[GATE_FIELDS][GATE_BATCH_PULSES];
    sgs_t after_batch[GATE_FIELDS];
    uint8_t batch_count[GATE_FIELDS];
    uint8_t batch_index;
    uint32_t field_limit,cycle_ticks,cycle_halves;
    uint64_t batch_tick_offset,batch_half_offset,batch_field_offset;
    volatile uint32_t batches,batch_max_cycles,batch_max_pulses,batch_late_max_cycles;
    volatile uint32_t schedule_max_cycles,rearm_max_cycles,count_max_cycles;
    uint32_t failed_event_late,failed_count_late;
    uint8_t failed_stage; /* 1 timer entry/count, 2 vertical cycle grid */
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
static uint32_t IRAM_ATTR hold_tip(uint32_t ticks,uint32_t *edge_cycle)
{
    REG_WRITE(GPIO_ENABLE_W1TC_REG,GATE_MASK);
    uint32_t start=esp_cpu_get_cycle_count();
    if(edge_cycle)*edge_cycle=start;
    while ((uint32_t)(esp_cpu_get_cycle_count()-start)<ticks*12u) { }
    REG_WRITE(GPIO_ENABLE_W1TS_REG,GATE_MASK);
    return (uint32_t)(esp_cpu_get_cycle_count()-start);
}

static void IRAM_ATTR record_tip(gate_context_t *g,uint32_t held)
{
    if(held<g->min_hold)g->min_hold=held;
    if(held>g->max_hold)g->max_hold=held;
    if(held<g->kind_min[g->pulse.kind])g->kind_min[g->pulse.kind]=held;
    if(held>g->kind_max[g->pulse.kind])g->kind_max[g->pulse.kind]=held;
    ++g->pulses;
    if(g->pulse.kind==SGS_EQ)++g->eq;
    else if(g->pulse.kind==SGS_BROAD)++g->broad;
    else ++g->normal;
}

static bool prepare_batches(gate_context_t *g,bool pal)
{
    sgs_t cursor;sgs_pulse_t pulse;bool ready[GATE_FIELDS]={false};
    if(!sgs_init(&cursor,pal?SGS_PAL:SGS_NTSC))return false;
    while(sgs_next(&cursor,&pulse) && pulse.field<GATE_FIELDS) {
        unsigned field=(unsigned)pulse.field;
        uint8_t count=g->batch_count[field];
        if(pulse.kind!=SGS_BROAD &&
            !(pulse.kind==SGS_EQ && count && !ready[field]))continue;
        if(ready[field] || count>=GATE_BATCH_PULSES)return false;
        g->batch[field][count]=pulse;
        g->batch_count[field]=(uint8_t)(count+1u);
        if(pulse.kind==SGS_EQ){g->after_batch[field]=cursor;ready[field]=true;}
    }
    for(unsigned field=0;field<GATE_FIELDS;++field)
        if(!ready[field] || g->batch_count[field]!=(pal?6u:7u))return false;
    return true;
}

/* A manual diagnostic deliberately occupies one priority-three ISR through
 * <=7 vertical half-lines (at most224us), with real DAC data exposed in every
 * inter-pulse gap. This is not the production flywheel scheduling design.
 * All pulse starts use one absolute CPU-cycle grid, so delay cannot accumulate
 * pulse by pulse. The live callback and lateness checks run with pads ON. */
static bool IRAM_ATTR broad_batch(gate_context_t *g,uint32_t anchor,
                                   uint32_t initial_late)
{
    unsigned plan=g->batch_index,count=g->batch_count[plan],emitted=1u;
    uint64_t first=g->pulse.start;
    ++g->batches;
    for(unsigned i=1;i<count;++i) {
        sgs_pulse_t next=g->batch[plan][i];
        next.start+=g->batch_tick_offset;
        next.half+=g->batch_half_offset;
        next.field+=g->batch_field_offset;
        uint64_t ticks=next.start-first;
        if(ticks>GATE_BATCH_MAX_CYCLES/12u) {
            g->reason=GATE_BATCH;g->done=true;break;
        }
        uint32_t deadline=(uint32_t)ticks*12u;
        uint32_t elapsed=esp_cpu_get_cycle_count()-anchor;
        while(elapsed<deadline && elapsed<GATE_BATCH_MAX_CYCLES)
            elapsed=esp_cpu_get_cycle_count()-anchor;
        if(!g->live(g->generation)) {
            g->reason=GATE_CONTEXT;g->done=true;break;
        }
        elapsed=esp_cpu_get_cycle_count()-anchor;
        if(elapsed>=GATE_BATCH_MAX_CYCLES || elapsed<deadline) {
            g->reason=GATE_BATCH;g->done=true;break;
        }
        uint32_t late=elapsed-deadline+initial_late*12u;
        if(late>g->batch_late_max_cycles)g->batch_late_max_cycles=late;
        if((late+11u)/12u>g->max_late)g->max_late=(late+11u)/12u;
        if(late>GATE_LATE_TICKS*12u) {
            g->failed_stage=2u;
            g->reason=GATE_LATE;g->done=true;break;
        }
        g->pulse=next;
        uint32_t held=hold_tip(g->pulse.width,NULL);
        record_tip(g,held);++emitted;
    }
    uint32_t duration=esp_cpu_get_cycle_count()-anchor;
    if(duration>g->batch_max_cycles)g->batch_max_cycles=duration;
    if(emitted>g->batch_max_pulses)g->batch_max_pulses=emitted;
    if(duration>=GATE_BATCH_MAX_CYCLES && !g->done){g->reason=GATE_BATCH;g->done=true;}
    if(g->done)return false;
    g->schedule=g->after_batch[plan];
    g->schedule.next_tick+=g->batch_tick_offset;
    g->schedule.next_half+=g->batch_half_offset;
    g->schedule.field+=g->batch_field_offset;
    /* NTSC three-field half-count1575 is divisible by9: the fractional tick
     * remainder repeats exactly. PAL has no fractional carry. Preserve local
     * index/remainder, but relocate GLOBAL half parity for the ordinary H grid.
     * No modulo or wide division is needed in this ISR. */
    if(++g->batch_index==GATE_FIELDS) {
        g->batch_index=0;
        g->batch_tick_offset+=g->cycle_ticks;
        g->batch_half_offset+=g->cycle_halves;
        g->batch_field_offset+=GATE_FIELDS;
    }
    return true;
}

static bool IRAM_ATTR gate_alarm(gptimer_handle_t timer,
    const gptimer_alarm_event_data_t *event, void *opaque)
{
    gate_context_t *g=opaque;
    if (g->done) return false;
    if (!g->live(g->generation)) {
        g->reason=GATE_CONTEXT;g->done=true;return false;
    }
    uint64_t now;
    uint32_t count_start=esp_cpu_get_cycle_count();
    if(gptimer_get_raw_count(timer,&now)!=ESP_OK) {
        g->reason=GATE_COUNT;g->done=true;return false;
    }
    uint32_t count_cycles=esp_cpu_get_cycle_count()-count_start;
    if(count_cycles>g->count_max_cycles)g->count_max_cycles=count_cycles;
    uint64_t target=GATE_INITIAL_TICKS+g->pulse.start;
    if(now<target || now-target>GATE_LATE_TICKS) {
        g->failed_stage=1u;
        g->failed_count_late=now>=target && now-target<UINT32_MAX?(uint32_t)(now-target):UINT32_MAX;
        g->failed_event_late=event && event->count_value>=target &&
            event->count_value-target<UINT32_MAX?(uint32_t)(event->count_value-target):UINT32_MAX;
        if(now>=target && now-target<UINT32_MAX && now-target>g->max_late)
            g->max_late=(uint32_t)(now-target);
        g->reason=GATE_LATE;g->done=true;return false;
    }
    uint32_t late=(uint32_t)(now-target);
    if(late>g->max_late)g->max_late=late;
    bool batch=g->pulse.kind==SGS_BROAD;
    if(batch && (g->pulse.field>=g->field_limit || g->batch_index>=GATE_FIELDS ||
        g->batch_count[g->batch_index]<2u ||
        g->batch_count[g->batch_index]>GATE_BATCH_PULSES ||
        g->batch[g->batch_index][0].start+g->batch_tick_offset!=g->pulse.start ||
        g->batch[g->batch_index][0].half+g->batch_half_offset!=g->pulse.half)) {
        g->reason=GATE_BATCH;g->done=true;return false;
    }
    uint32_t anchor;
    uint32_t held=hold_tip(g->pulse.width,&anchor);
    record_tip(g,held);
    if(batch && !broad_batch(g,anchor,late))return false;
    /* All driver/schedule operations occur after the six pads are enabled. */
    uint32_t schedule_start=esp_cpu_get_cycle_count();
    if(!sgs_next(&g->schedule,&g->pulse)) {
        g->reason=GATE_SCHEDULE;g->done=true;return false;
    }
    uint32_t schedule_cycles=esp_cpu_get_cycle_count()-schedule_start;
    if(schedule_cycles>g->schedule_max_cycles)g->schedule_max_cycles=schedule_cycles;
    if(g->pulse.field>=g->field_limit) {
        g->reason=GATE_COMPLETE;g->done=true;return false;
    }
    gptimer_alarm_config_t alarm={.alarm_count=GATE_INITIAL_TICKS+g->pulse.start};
    uint32_t rearm_start=esp_cpu_get_cycle_count();
    if(gptimer_set_alarm_action(timer,&alarm)!=ESP_OK) {
        g->reason=GATE_REARM;g->done=true;
    }
    uint32_t rearm_cycles=esp_cpu_get_cycle_count()-rearm_start;
    if(rearm_cycles>g->rearm_max_cycles)g->rearm_max_cycles=rearm_cycles;
    return false;
}

static void gate_probe_run(bool pal,uint32_t generation,bool (*live)(uint32_t),
                            uint32_t fields,int64_t timeout_us)
{
    if(__atomic_exchange_n(&s_busy,true,__ATOMIC_ACQ_REL)) {
        printf("SYNC_GATE end reason=busy generation=%lu restored=1\n",(unsigned long)generation);
        return;
    }
    uint32_t saved_mux[6]={0},saved_pad[6]={0},saved_enable=0,pulls_removed_mask=0;
    bool saved=false,mux_changed=false,pads_changed=false,enabled=false,started=false,restored=true;
    const char *failure=NULL;
    gate_context_t *g=&s_gate;
    memset(g,0,sizeof(*g));g->live=live;g->generation=generation;g->min_hold=UINT32_MAX;
    g->field_limit=fields;g->cycle_ticks=pal?1200000u:1001000u;
    g->cycle_halves=pal?1875u:1575u;
    for(unsigned j=0;j<3;++j)g->kind_min[j]=UINT32_MAX;
    printf("SYNC_GATE begin standard=%s generation=%lu fields=%u cpu_hz=%d automatic=0 burst_isr_limit_us=224\n",
        pal?"PAL":"NTSC",(unsigned long)generation,(unsigned)fields,esp_clk_cpu_freq());
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
    }
    if(!prepare_batches(g,pal) || !sgs_init(&g->schedule,pal?SGS_PAL:SGS_NTSC) ||
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
    /* Preserve drive, pad function and every unrelated bit. Flag cleanup
     * before the first write so even a partial readback failure restores all. */
    pads_changed=true;
    for(unsigned j=0;j<6;++j) {
        if(saved_pad[j]&(FUN_PU|FUN_PD)) {
            pulls_removed_mask|=UINT32_C(1)<<s_pins[j];
            REG_WRITE(s_iomux[j],saved_pad[j]&~(FUN_PU|FUN_PD));
        }
        if(REG_READ(s_iomux[j])!=(saved_pad[j]&~(FUN_PU|FUN_PD))) {
            failure="pull_clear_readback";goto cleanup;
        }
    }
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
    while(!g->done && esp_timer_get_time()-began<timeout_us)vTaskDelay(1);
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
    if(saved && pads_changed)
        for(unsigned j=0;j<6;++j)REG_WRITE(s_iomux[j],saved_pad[j]);
    if(saved) {
        if((REG_READ(GPIO_ENABLE_REG)&GATE_MASK)!=(saved_enable&GATE_MASK))restored=false;
        for(unsigned j=0;j<6;++j)
            if(REG_READ(matrix_reg(s_pins[j]))!=saved_mux[j] ||
                REG_READ(s_iomux[j])!=saved_pad[j])restored=false;
    }
    printf("SYNC_GATE end standard=%s generation=%lu reason=%s code=%u pulses=%lu eq=%lu broad=%lu normal=%lu hold_min_cycles=%lu hold_max_cycles=%lu eq_min_cycles=%lu eq_max_cycles=%lu broad_min_cycles=%lu broad_max_cycles=%lu normal_min_cycles=%lu normal_max_cycles=%lu late_max_ticks=%lu batches=%lu batch_max_cycles=%lu batch_max_pulses=%lu batch_late_max_cycles=%lu pulls_removed_mask=0x%08lx restored=%u monitor_lock_proven=0\n",
        pal?"PAL":"NTSC",(unsigned long)generation,failure?failure:"isr",
        (unsigned)g->reason,(unsigned long)g->pulses,(unsigned long)g->eq,
        (unsigned long)g->broad,(unsigned long)g->normal,
        (unsigned long)(g->pulses?g->min_hold:0u),(unsigned long)g->max_hold,
        (unsigned long)(g->eq?g->kind_min[SGS_EQ]:0u),(unsigned long)g->kind_max[SGS_EQ],
        (unsigned long)(g->broad?g->kind_min[SGS_BROAD]:0u),(unsigned long)g->kind_max[SGS_BROAD],
        (unsigned long)(g->normal?g->kind_min[SGS_NORMAL]:0u),(unsigned long)g->kind_max[SGS_NORMAL],
        (unsigned long)g->max_late,(unsigned long)g->batches,
        (unsigned long)g->batch_max_cycles,(unsigned long)g->batch_max_pulses,
        (unsigned long)g->batch_late_max_cycles,(unsigned long)pulls_removed_mask,(unsigned)restored);
    printf("SYNC_GATE_TIMING failed_stage=%u field=%lu half_index=%u kind=%u event_late_ticks=%lu count_late_ticks=%lu count_max_cycles=%lu schedule_max_cycles=%lu rearm_max_cycles=%lu\n",
        (unsigned)g->failed_stage,(unsigned long)g->pulse.field,(unsigned)g->pulse.index,
        (unsigned)g->pulse.kind,(unsigned long)g->failed_event_late,
        (unsigned long)g->failed_count_late,(unsigned long)g->count_max_cycles,
        (unsigned long)g->schedule_max_cycles,(unsigned long)g->rearm_max_cycles);
    /* A failed deletion must not let an old callback alias a new context. */
    if(reusable)__atomic_store_n(&s_busy,false,__ATOMIC_RELEASE);
}

void sync_gate_probe(bool pal,uint32_t generation,bool (*live)(uint32_t))
{ gate_probe_run(pal,generation,live,GATE_FIELDS,GATE_TIMEOUT_US); }

void sync_gate_probe_long(bool pal,uint32_t generation,bool (*live)(uint32_t))
{ gate_probe_run(pal,generation,live,pal?150u:180u,GATE_LONG_TIMEOUT_US); }

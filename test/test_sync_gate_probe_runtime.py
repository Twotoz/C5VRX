#!/usr/bin/env python3
"""C5VRX/Twotoz: compile the actual manual OE probe against an adversarial SDK.

No register simulation here establishes electrical levels or physical timing.
Run with a host C compiler, e.g. inside the pinned ESP-IDF Docker container.
"""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SDK = r'''
#ifndef MOCK_SDK_H
#define MOCK_SDK_H
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#define IRAM_ATTR
#define ESP_OK 0
#define GPTIMER_CLK_SRC_DEFAULT 0
#define GPTIMER_COUNT_UP 0
typedef int esp_err_t;
typedef struct mock_timer *gptimer_handle_t;
typedef struct { uint64_t count_value,alarm_value; } gptimer_alarm_event_data_t;
typedef struct { uint64_t alarm_count; } gptimer_alarm_config_t;
typedef struct { int clk_src,direction;uint32_t resolution_hz;int intr_priority; } gptimer_config_t;
typedef struct { bool (*on_alarm)(gptimer_handle_t,const gptimer_alarm_event_data_t*,void*); } gptimer_event_callbacks_t;
int gptimer_new_timer(const gptimer_config_t*,gptimer_handle_t*);
int gptimer_register_event_callbacks(gptimer_handle_t,const gptimer_event_callbacks_t*,void*);
int gptimer_enable(gptimer_handle_t);
int gptimer_disable(gptimer_handle_t);
int gptimer_start(gptimer_handle_t);
int gptimer_stop(gptimer_handle_t);
int gptimer_del_timer(gptimer_handle_t);
int gptimer_set_raw_count(gptimer_handle_t,uint64_t);
int gptimer_get_raw_count(gptimer_handle_t,uint64_t*);
int gptimer_set_alarm_action(gptimer_handle_t,const gptimer_alarm_config_t*);
int esp_clk_cpu_freq(void);
uint32_t esp_cpu_get_cycle_count(void);
int64_t esp_timer_get_time(void);
void vTaskDelay(unsigned);
uint32_t mock_read(uint32_t);
void mock_write(uint32_t,uint32_t);
#define REG_READ(r) mock_read(r)
#define REG_WRITE(r,v) mock_write(r,v)
#define GPIO_ENABLE_REG 0u
#define GPIO_ENABLE_W1TS_REG 4u
#define GPIO_ENABLE_W1TC_REG 8u
#define GPIO_FUNC0_OUT_SEL_CFG_REG 0x100u
#define GPIO_FUNC0_OUT_SEL_M 511u
#define GPIO_FUNC0_OUT_INV_SEL (1u<<9)
#define GPIO_FUNC0_OE_SEL (1u<<10)
#define GPIO_FUNC0_OE_INV_SEL (1u<<11)
#define PARL_TX_DATA0_IDX 48u
#define PARL_TX_DATA1_IDX 49u
#define PARL_TX_DATA2_IDX 50u
#define PARL_TX_DATA3_IDX 51u
#define PARL_TX_DATA4_IDX 52u
#define PARL_TX_DATA5_IDX 53u
#define FUN_PU (1u<<8)
#define FUN_PD (1u<<7)
#define IO_MUX_GPIO23_REG (0x300u+4u*23u)
#define IO_MUX_GPIO24_REG (0x300u+4u*24u)
#define IO_MUX_GPIO11_REG (0x300u+4u*11u)
#define IO_MUX_GPIO12_REG (0x300u+4u*12u)
#define IO_MUX_GPIO8_REG (0x300u+4u*8u)
#define IO_MUX_GPIO9_REG (0x300u+4u*9u)
#endif
'''
HARNESS = r'''
#include "mock_sdk.h"
#include "sync_gate_probe.c"
struct mock_timer {
    gptimer_event_callbacks_t callbacks;
    void *context;
    uint64_t alarm,count;
    bool enabled,started,exists;
};
static struct mock_timer mt;
static uint32_t registers[256],before[256],cycles;
static unsigned clears,sets,alarm_calls,live_calls,fail_stage;
static unsigned lose_after,late_after;
static unsigned pull_fail_pin;
static bool pull_fail_used;
static bool stall;
static int cpu_hz=240000000;
static int64_t wall;
static unsigned runs;
static sgs_t expected_schedule;
static sgs_pulse_t expected_pulse;
static bool in_batch,release_ends_batch,inject_batch_delay,batch_delay_used;
static bool corrupt_batch;
static uint32_t batch_anchor,batch_pulses;
static uint64_t batch_first_tick;
static uint64_t previous_start;
static uint32_t previous_width;
static bool have_previous,oracle_pal;
static bool live(uint32_t generation)
{
    if(mt.started){
        assert((registers[0]&GATE_MASK)==GATE_MASK);
        for(unsigned j=0;j<6;++j)assert(!(registers[s_iomux[j]/4u]&(FUN_PU|FUN_PD)));
        if(in_batch && inject_batch_delay && !batch_delay_used){cycles+=2000u;batch_delay_used=true;}
    }
    assert(generation==213u);
    return ++live_calls<=lose_after;
}
uint32_t mock_read(uint32_t address){assert(address/4u<256);return registers[address/4u];}
void mock_write(uint32_t address,uint32_t value)
{
    assert(address/4u<256);
    if(address==GPIO_ENABLE_W1TC_REG){
        if(value==GATE_MASK){
            assert((registers[0]&GATE_MASK)==GATE_MASK);
            assert(sgs_next(&expected_schedule,&expected_pulse));
            assert(expected_pulse.start==s_gate.pulse.start && expected_pulse.half==s_gate.pulse.half);
            assert(expected_pulse.field==s_gate.pulse.field && expected_pulse.index==s_gate.pulse.index);
            assert(expected_pulse.width==s_gate.pulse.width && expected_pulse.kind==s_gate.pulse.kind);
            uint64_t rational=oracle_pal?expected_pulse.half*640u:(expected_pulse.half*5720u+4u)/9u;
            assert(expected_pulse.start==rational);
            assert(expected_pulse.field==expected_pulse.half/(oracle_pal?625u:525u));
            if(expected_pulse.kind==SGS_NORMAL)assert(!(expected_pulse.half&1u));
            if(have_previous)assert(expected_pulse.start>=previous_start+previous_width);
            have_previous=true;previous_start=expected_pulse.start;previous_width=expected_pulse.width;
            if(expected_pulse.kind==SGS_BROAD && !in_batch){
                in_batch=true;batch_anchor=cycles;batch_first_tick=expected_pulse.start;batch_pulses=0;
            }
            if(in_batch){
                uint32_t deadline=(uint32_t)(expected_pulse.start-batch_first_tick)*12u;
                assert((uint32_t)(cycles-batch_anchor)>=deadline);
                assert((uint32_t)(cycles-batch_anchor)-deadline<=GATE_LATE_TICKS*12u);
                assert(++batch_pulses<=GATE_BATCH_PULSES);
                release_ends_batch=expected_pulse.kind==SGS_EQ;
            }
        }
        registers[0]&=~value;
        if(value==GATE_MASK)++clears;
    }else if(address==GPIO_ENABLE_W1TS_REG){
        registers[0]|=value;if(value==GATE_MASK)++sets;
        if(release_ends_batch){in_batch=false;release_ends_batch=false;}
    }
    else {
        for(unsigned j=0;j<6;++j){
            if(address==s_iomux[j])
                assert(value==before[address/4u] || value==(before[address/4u]&~(FUN_PU|FUN_PD)));
            if(address==matrix_reg(s_pins[j]))
                assert((value&~(GPIO_FUNC0_OE_SEL|GPIO_FUNC0_OE_INV_SEL))==
                    (before[address/4u]&~(GPIO_FUNC0_OE_SEL|GPIO_FUNC0_OE_INV_SEL)));
        }
        if(pull_fail_pin<6u && !pull_fail_used && address==s_iomux[pull_fail_pin] &&
            !(value&(FUN_PU|FUN_PD)) && (registers[address/4u]&(FUN_PU|FUN_PD))){
            pull_fail_used=true;return;
        }
        registers[address/4u]=value;
    }
}
int esp_clk_cpu_freq(void){return cpu_hz;}
uint32_t esp_cpu_get_cycle_count(void){return cycles++;}
int64_t esp_timer_get_time(void){return wall;}
int gptimer_new_timer(const gptimer_config_t *c,gptimer_handle_t *out)
{
    assert(c->resolution_hz==20000000u && c->intr_priority==3);
    if(fail_stage==1)return -1;
    assert(!mt.exists);mt.exists=true;*out=&mt;return 0;
}
int gptimer_register_event_callbacks(gptimer_handle_t t,const gptimer_event_callbacks_t *c,void *ctx)
{if(fail_stage==2)return -1;t->callbacks=*c;t->context=ctx;return 0;}
int gptimer_enable(gptimer_handle_t t){if(fail_stage==3)return -1;t->enabled=true;return 0;}
int gptimer_set_raw_count(gptimer_handle_t t,uint64_t n){if(fail_stage==4)return -1;t->count=n;return 0;}
int gptimer_get_raw_count(gptimer_handle_t t,uint64_t *n)
{assert(!in_batch);assert((registers[0]&GATE_MASK)==GATE_MASK);if(fail_stage==7)return -1;*n=t->count;return 0;}
int gptimer_set_alarm_action(gptimer_handle_t t,const gptimer_alarm_config_t *a)
{
    assert((registers[0]&GATE_MASK)==GATE_MASK);
    assert(!in_batch);
    assert(a->alarm_count==GATE_INITIAL_TICKS+s_gate.pulse.start);
    ++alarm_calls;
    if((fail_stage==5 && alarm_calls==1u)||(fail_stage==8 && alarm_calls==5u))return -1;
    t->alarm=a->alarm_count;return 0;
}
int gptimer_start(gptimer_handle_t t){
    if(fail_stage==6)return -1;
    assert(t->enabled);t->started=true;
    if(corrupt_batch)s_gate.batch[0][1].start=s_gate.batch[0][0].start+5000u;
    return 0;
}
int gptimer_stop(gptimer_handle_t t){assert(t->started);t->started=false;return fail_stage==9?-1:0;}
int gptimer_disable(gptimer_handle_t t){assert(!t->started);t->enabled=false;return fail_stage==10?-1:0;}
int gptimer_del_timer(gptimer_handle_t t){assert(!t->started && !t->enabled);if(fail_stage==11)return -1;t->exists=false;return 0;}
void vTaskDelay(unsigned ticks)
{
    assert(ticks==1u);
    if(stall){wall+=10000;return;}
    assert(mt.started && mt.callbacks.on_alarm);
    mt.count=mt.alarm+(clears>=late_after?161u:0u);
    /* First tip crosses UINT32 wrap; subsequent alarm times share one clock. */
    cycles=(uint32_t)(mt.count*12u)+0xffffa200u;
    wall=(int64_t)(mt.count/20u);
    gptimer_alarm_event_data_t event={.count_value=mt.count,.alarm_value=mt.alarm};
    mt.callbacks.on_alarm(&mt,&event,mt.context);
    assert((registers[0]&GATE_MASK)==GATE_MASK);
}
static void init(void)
{
    memset(&mt,0,sizeof(mt));memset(registers,0,sizeof(registers));
    registers[0]=GATE_MASK|0x51u;
    for(unsigned j=0;j<6;++j){
        registers[matrix_reg(s_pins[j])/4u]=s_signals[j]|(j%2u?GPIO_FUNC0_OE_SEL:0u);
        registers[s_iomux[j]/4u]=0x600u;
    }
    memcpy(before,registers,sizeof(before));
    cycles=0xfffffff0u;clears=sets=alarm_calls=live_calls=0;
    lose_after=late_after=UINT32_MAX;fail_stage=0;wall=0;stall=false;cpu_hz=240000000;
    s_busy=false;
    pull_fail_pin=UINT32_MAX;pull_fail_used=false;
    in_batch=release_ends_batch=inject_batch_delay=batch_delay_used=corrupt_batch=false;
    have_previous=false;
}
static void run_mode(bool pal,bool long_test)
{
    oracle_pal=pal;
    assert(sgs_init(&expected_schedule,pal?SGS_PAL:SGS_NTSC));
    if(long_test)sync_gate_probe_long(pal,213u,live);
    else sync_gate_probe(pal,213u,live);
    ++runs;
    assert(!memcmp(before,registers,sizeof(before)));
    assert(!mt.started && !mt.enabled);
    assert(!mt.exists || fail_stage==11);
    assert(!s_busy || fail_stage>=9);
    assert(wall<(long_test?4000000:1000000));
    if(s_gate.pulses){
        assert(s_gate.pulses==clears);
        assert(s_gate.min_hold>=46u*12u);
        assert(s_gate.max_hold<=546u*12u+2u);
        assert(s_gate.eq+s_gate.broad+s_gate.normal==s_gate.pulses);
    }
    unsigned prior=clears;
    assert(s_gate.done);
    gate_alarm(&mt,NULL,&s_gate);
    assert(clears==prior); /* Late callback cannot gate after cleanup. */
    if(fail_stage>=9){
        assert(s_busy);
        sync_gate_probe(pal,213u,live);
        assert(s_busy && clears==prior); /* No old-context reuse. */
    }
}
static void run(bool pal){run_mode(pal,false);}
int main(void)
{
    assert(sizeof(s_gate.batch)/sizeof(s_gate.batch[0])==3u);
    for(unsigned pal=0;pal<2;++pal){
        init();run(pal!=0);assert(s_gate.reason==GATE_COMPLETE);
        sgs_t schedule;sgs_pulse_t pulse;assert(sgs_init(&schedule,pal?SGS_PAL:SGS_NTSC));
        unsigned count=0,eq=0,broad=0;
        while(sgs_next(&schedule,&pulse)&&pulse.field<3u){++count;eq+=pulse.kind==SGS_EQ;broad+=pulse.kind==SGS_BROAD;}
        assert(clears==count && s_gate.eq==eq && s_gate.broad==broad);
        assert(s_gate.batches==3u && s_gate.batch_max_pulses==(pal?6u:7u));
        assert(s_gate.batch_max_cycles<GATE_BATCH_MAX_CYCLES);
        init();run_mode(pal!=0,true);assert(s_gate.reason==GATE_COMPLETE);
        unsigned fields=pal?150u:180u;
        assert(s_gate.field_limit==fields && s_gate.pulse.field==fields);
        assert(s_gate.pulses==(pal?48000u:48870u) && s_gate.batches==fields);
        assert(s_gate.eq==fields*(pal?10u:12u) && s_gate.broad==fields*(pal?5u:6u));
        assert(s_gate.batch_index==0u && s_gate.batch_field_offset==fields);
        assert(s_gate.batch_tick_offset==(pal?60000000u:60060000u));
        assert(s_gate.batch_half_offset==(uint64_t)fields*(pal?625u:525u));
        assert(s_gate.batch_max_cycles<GATE_BATCH_MAX_CYCLES);
        assert(wall>2900000 && wall<3100000);
        assert(s_gate.pulse.start==(pal?60000000u:60060000u));
        init();late_after=2000u;run_mode(pal!=0,true);
        assert(s_gate.reason==GATE_LATE && clears>=2000u && clears<2010u);
        init();lose_after=4000u;run_mode(pal!=0,true);
        assert(s_gate.reason==GATE_CONTEXT && clears==3998u);
        init();stall=true;run_mode(pal!=0,true);
        assert(s_gate.reason==GATE_TIMEOUT && !clears && wall>=3500000);
        init();late_after=5u;run(pal!=0);assert(clears==5u && s_gate.reason==GATE_LATE);
        init();lose_after=8u;run(pal!=0);assert(clears==6u && s_gate.reason==GATE_CONTEXT);
        init();lose_after=pal?8u:9u;run(pal!=0);
        assert(clears==(pal?6u:7u) && s_gate.batches==1u && s_gate.reason==GATE_CONTEXT);
        init();inject_batch_delay=true;run(pal!=0);
        assert(batch_delay_used && clears==(pal?6u:7u) && s_gate.reason==GATE_LATE);
        init();corrupt_batch=true;run(pal!=0);
        assert(clears==(pal?6u:7u) && s_gate.reason==GATE_BATCH);
        init();stall=true;run(pal!=0);assert(!clears && s_gate.reason==GATE_TIMEOUT);
        for(unsigned stage=1;stage<=11;++stage){
            init();fail_stage=stage;
            for(unsigned j=0;j<6;++j)registers[s_iomux[j]/4u]|=FUN_PU|FUN_PD;
            memcpy(before,registers,sizeof(before));run(pal!=0);
        }
    }
    for(unsigned j=0;j<6;++j){
        init();registers[s_iomux[j]/4u]|=FUN_PU;memcpy(before,registers,sizeof(before));run(true);assert(clears==960u && !mt.exists);
        init();registers[s_iomux[j]/4u]|=FUN_PD;memcpy(before,registers,sizeof(before));run(true);assert(clears==960u && !mt.exists);
        init();
        for(unsigned k=0;k<6;++k)registers[s_iomux[k]/4u]|=FUN_PU|FUN_PD;
        memcpy(before,registers,sizeof(before));pull_fail_pin=j;run(true);
        assert(pull_fail_used && !clears && !mt.exists);
        init();registers[matrix_reg(s_pins[j])/4u]^=1u;memcpy(before,registers,sizeof(before));run(true);assert(!clears && !mt.exists);
        init();registers[matrix_reg(s_pins[j])/4u]|=GPIO_FUNC0_OE_INV_SEL;memcpy(before,registers,sizeof(before));run(true);assert(!clears && !mt.exists);
        init();registers[0]&=~(1u<<s_pins[j]);memcpy(before,registers,sizeof(before));
        run(true);assert(!clears && !mt.exists);
    }
    init();cpu_hz=160000000;run(true);assert(!clears && !mt.exists);
    init();s_busy=true;sync_gate_probe(true,213u,live);assert(!clears && !mt.exists && s_busy);
    puts("PASS actual GPIO OE probe mocked runtime: PAL150/NTSC180 fields (48000/48870 pulses) exact rational/half/field/H parity/no overlap, three reused V plans, bounded bursts, exact restoration, context/lateness/timeout, all timer failures, invalid pads/mux/CPU, no physical timing claim");
    return 0;
}
'''


def main():
    cc = os.environ.get("CC") or shutil.which("cc") or shutil.which("gcc")
    if not cc:
        raise SystemExit("A host C compiler is required (run inside pinned IDF Docker).")
    with tempfile.TemporaryDirectory(prefix="c5-gate-test-") as directory:
        temporary = Path(directory)
        (temporary / "mock_sdk.h").write_text(SDK, encoding="utf-8")
        for name in (
            "driver/gptimer.h", "esp_attr.h", "esp_cpu.h",
            "esp_private/esp_clk.h", "esp_timer.h", "freertos/FreeRTOS.h",
            "freertos/task.h", "soc/gpio_reg.h", "soc/gpio_sig_map.h",
            "soc/io_mux_reg.h", "soc/soc.h",
        ):
            path = temporary / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('#include "mock_sdk.h"\n', encoding="utf-8")
        harness = temporary / "harness.c"
        harness.write_text(HARNESS, encoding="utf-8")
        binary = temporary / "gate-test"
        subprocess.run([
            cc, "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror",
            "-I", str(temporary), "-I", str(ROOT / "main"),
            str(harness), "-o", str(binary),
        ], check=True)
        result = subprocess.run([str(binary)], text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT)
        if result.returncode:
            print(result.stdout)
            result.check_returncode()
        print(result.stdout.splitlines()[-1])


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""C5VRX/Twotoz: actual GPIO2 witness with bounded adversarial SDK traces.

Host timestamps are synthetic; passing proves neither filter nor ISR latency.
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
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <assert.h>
#define IRAM_ATTR
#define ESP_OK 0
#define ESP_INTR_FLAG_LEVEL3 8
#define ESP_INTR_FLAG_IRAM 16
#define ETS_GPIO_INTR_SOURCE 13
typedef struct mock_interrupt *intr_handle_t;
int esp_intr_alloc(int,int,void (*)(void*),void*,intr_handle_t*);
int esp_intr_free(intr_handle_t);
typedef int portMUX_TYPE;
#define portMUX_INITIALIZER_UNLOCKED 0
void mock_enter(portMUX_TYPE*);
void mock_exit(portMUX_TYPE*);
#define portENTER_CRITICAL(x) mock_enter(x)
#define portEXIT_CRITICAL(x) mock_exit(x)
#define GPIO_PIN2_REG 0x108u
#define GPIO_PIN2_INT_TYPE_S 7u
#define GPIO_PIN2_INT_TYPE_M (7u<<7)
#define GPIO_PIN2_INT_ENA_S 13u
#define GPIO_PIN2_INT_ENA_M (31u<<13)
#define GPIO_FUNC0_OUT_SEL_CFG_REG 0x200u
#define IO_MUX_GPIO2_REG 0x308u
#define GPIO_IN_REG 0x20u
#define GPIO_STATUS_REG 0x24u
#define GPIO_STATUS_W1TC_REG 0x28u
uint32_t mock_read(uint32_t);
void mock_write(uint32_t,uint32_t);
#define REG_READ(r) mock_read(r)
#define REG_WRITE(r,v) mock_write(r,v)
#endif
'''
HARNESS = r'''
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include "mock_sdk.h"
static void *witness_calloc(size_t,size_t);
static void witness_free(void*);
#define calloc witness_calloc
#define free witness_free
#include "sync_native_witness.c"
#undef calloc
#undef free
struct mock_interrupt { void (*fn)(void*);void *context;bool owned; } interrupt;
static uint32_t regs[256],before[256];
static uint64_t tick;
static bool clock_ok,allocate_error,free_error,in_irq;
static unsigned critical,reads,allocations,frees,checks;
static unsigned heap_live;
static bool heap_error;
static void *witness_calloc(size_t n,size_t bytes)
{assert(!in_irq);if(heap_error)return NULL;void *p=calloc(n,bytes);if(p)++heap_live;return p;}
static void witness_free(void *p)
{assert(!in_irq);if(p){assert(heap_live);--heap_live;}free(p);}
void mock_enter(portMUX_TYPE *lock){(void)lock;++critical;}
void mock_exit(portMUX_TYPE *lock){(void)lock;assert(critical);--critical;}
uint32_t mock_read(uint32_t reg){assert(reg/4u<256u);return regs[reg/4u];}
void mock_write(uint32_t reg,uint32_t value)
{
    if(reg==GPIO_STATUS_W1TC_REG){assert(value==SNW_PIN_MASK);regs[GPIO_STATUS_REG/4u]&=~value;}
    else{assert(reg==GPIO_PIN2_REG);assert((value&~SNW_INT_MASK)==(before[reg/4u]&~SNW_INT_MASK));regs[reg/4u]=value;}
}
bool sync_edge_clock_read(uint64_t *out)
{
    ++reads;
    if(s_witness && s_witness->view.status==SNW_ARMED && !in_irq)assert(critical);
    if(!clock_ok)return false;
    *out=tick;return true;
}
int esp_intr_alloc(int source,int flags,void (*fn)(void*),void *ctx,intr_handle_t *out)
{
    ++allocations;assert(source==ETS_GPIO_INTR_SOURCE && flags==ESP_INTR_FLAG_LEVEL3);
    if(allocate_error || interrupt.owned)return -1;
    interrupt=(struct mock_interrupt){fn,ctx,true};*out=&interrupt;return 0;
}
int esp_intr_free(intr_handle_t handle)
{
    ++frees;assert(handle==&interrupt && interrupt.owned);
    if(free_error)return -1;
    interrupt.owned=false;return 0;
}
static void init(void)
{
    /* Reset quarantined mocks as a simulated reboot, never as runtime reuse. */
    if(s_witness)witness_free(s_witness);
    s_witness=NULL;assert(!heap_live);heap_error=false;memset(&interrupt,0,sizeof(interrupt));
    memset(regs,0,sizeof(regs));regs[GPIO_PIN2_REG/4u]=0x45u;
    regs[IO_MUX_GPIO2_REG/4u]=0x12345678u;regs[SNW_MATRIX_REG/4u]=48u;
    memcpy(before,regs,sizeof(before));tick=1000u;
    clock_ok=true;allocate_error=free_error=in_irq=false;critical=reads=allocations=frees=0;
}
static void edge(uint64_t when,bool high)
{
    assert(interrupt.owned);
    tick=when;regs[GPIO_STATUS_REG/4u]|=SNW_PIN_MASK;
    if(high)regs[GPIO_IN_REG/4u]|=SNW_PIN_MASK;else regs[GPIO_IN_REG/4u]&=~SNW_PIN_MASK;
    in_irq=true;interrupt.fn(interrupt.context);in_irq=false;
    assert(!(regs[GPIO_STATUS_REG/4u]&SNW_PIN_MASK));
    if(!s_witness || s_witness->view.status!=SNW_ARMED)assert(!(regs[GPIO_PIN2_REG/4u]&SNW_INT_MASK));
    ++checks;
}
static snw_snapshot_t snapshot(void)
{
    snw_snapshot_t out;assert(sync_native_witness_snapshot(&out));assert(!critical);return out;
}
static void arm(void)
{assert(sync_native_witness_enable_window(1100u,20u,20u,80u,110u));}
static void finish(void)
{
    assert(sync_native_witness_end());assert(!interrupt.owned && !s_witness && !heap_live);
    assert(regs[GPIO_PIN2_REG/4u]==before[GPIO_PIN2_REG/4u]);
    assert(regs[IO_MUX_GPIO2_REG/4u]==before[IO_MUX_GPIO2_REG/4u]);
    assert(regs[SNW_MATRIX_REG/4u]==before[SNW_MATRIX_REG/4u]);
}
int main(void)
{
    init();assert(sync_native_witness_begin());assert(!sync_native_witness_begin());arm();
    edge(1100,false);snw_snapshot_t s=snapshot();assert(s.have_falling && !s.completed_low && s.falling==1100);
    edge(1194,true);s=snapshot();assert(s.status==SNW_COMPLETE && s.completed_low && s.low_width==94u && s.edges==2u && !s.dropped);
    unsigned n=reads;edge(1200,false);assert(reads==n);finish();
    /* Every accepted width, at both ends of the falling-edge window. */
    for(unsigned start=1080;start<=1120;start+=40)for(unsigned width=80;width<=110;++width){
        init();assert(sync_native_witness_begin());arm();edge(start,false);edge(start+width,true);
        s=snapshot();assert(s.status==SNW_COMPLETE && s.falling==start && s.rising==start+width && s.low_width==width);finish();
    }
    init();assert(sync_native_witness_begin());arm();edge(1100,false);edge(1130,true);
    s=snapshot();assert(!s.completed_low && s.dropped==1u && !s.have_falling);
    edge(1131,false);assert(snapshot().status==SNW_EXPIRED);finish();
    init();assert(sync_native_witness_begin());arm();edge(1100,false);edge(1101,false);
    s=snapshot();assert(s.status==SNW_RATE_FAULT && s.rate_fault && !s.completed_low);
    assert(!sync_native_witness_enable_window(1200,20,20,80,110));finish();
    init();assert(sync_native_witness_begin());arm();
    for(unsigned i=0;i<SNW_EDGE_LIMIT;++i)edge(1001u+i,(i&1u)!=0);
    s=snapshot();assert(s.status==SNW_RATE_FAULT && s.edges==SNW_EDGE_LIMIT && s.dropped==SNW_EDGE_LIMIT);
    n=reads;for(unsigned i=0;i<1000;++i)edge(1200u+i,true);assert(reads==n);finish();
    init();assert(sync_native_witness_begin());arm();tick=1231;
    assert(snapshot().status==SNW_EXPIRED);finish();
    init();assert(sync_native_witness_begin());arm();assert(sync_native_witness_disable_window());
    assert(snapshot().status==SNW_EXPIRED);finish();
    init();assert(sync_native_witness_begin());arm();clock_ok=false;edge(1100,false);
    assert(snapshot().status==SNW_CLOCK_FAULT);assert(!sync_native_witness_enable_window(1200,20,20,80,110));finish();
    init();assert(sync_native_witness_begin());arm();edge(1090,false);edge(1089,true);
    assert(snapshot().clock_fault);finish();
    /* Rearming requires completion/expiry; counters preserve all arm history. */
    init();assert(sync_native_witness_begin());arm();assert(!sync_native_witness_enable_window(1100,20,20,80,110));
    edge(1100,false);edge(1194,true);tick=1200;
    assert(sync_native_witness_enable_window(1300,20,20,80,110));edge(1300,false);edge(1394,true);
    s=snapshot();assert(s.windows==2u && s.completed==2u && s.edges==2u);finish();
    init();assert(sync_native_witness_begin());
    assert(!sync_native_witness_enable_window(1,2,0,80,110));
    assert(!sync_native_witness_enable_window(UINT64_MAX,0,1,80,110));
    assert(!sync_native_witness_enable_window(UINT64_MAX-100u,0,0,80,110));
    assert(!sync_native_witness_enable_window(1100,20,20,0,110));
    assert(!sync_native_witness_enable_window(1100,20,20,111,110));
    tick=1200;assert(!sync_native_witness_enable_window(1100,20,20,80,110));finish();
    init();assert(sync_native_witness_begin());tick=999;
    assert(!sync_native_witness_enable_window(1100,20,20,80,110));
    assert(snapshot().clock_fault);finish();
    init();regs[GPIO_PIN2_REG/4u]|=1u<<GPIO_PIN2_INT_TYPE_S;
    assert(!sync_native_witness_begin() && !allocations);
    init();regs[(GPIO_PIN2_REG-4u)/4u]|=1u<<GPIO_PIN2_INT_ENA_S;
    assert(!sync_native_witness_begin() && !allocations);
    init();regs[GPIO_STATUS_REG/4u]|=SNW_PIN_MASK;assert(!sync_native_witness_begin());
    init();interrupt.owned=true;assert(!sync_native_witness_begin() && allocations==1u);
    init();clock_ok=false;assert(!sync_native_witness_begin() && !allocations);
    init();allocate_error=true;assert(!sync_native_witness_begin());
    init();assert(sync_native_witness_begin());arm();free_error=true;
    assert(!sync_native_witness_end() && s_witness && s_witness->quarantined && heap_live==1u);
    assert(regs[GPIO_PIN2_REG/4u]==before[GPIO_PIN2_REG/4u]);
    n=reads;edge(1100,false);assert(reads==n && !sync_native_witness_begin());
    init();assert(sync_native_witness_begin());regs[SNW_MATRIX_REG/4u]^=1u;
    assert(!sync_native_witness_end()); /* Detect external route mutation; never rewrite it. */
    assert(!s_witness && !heap_live);
    init();heap_error=true;assert(!sync_native_witness_begin() && !s_witness && !heap_live && !allocations);
    printf("PASS actual native witness: %u IRQ traces, <=8 observations/arm, exact GPIO2 restoration/no matrix or IOMUX writes, ownership/clock/expiry/noise failures, no physical latency claim\n",checks);
    return 0;
}
'''


def main():
    cc = os.environ.get("CC") or shutil.which("cc") or shutil.which("gcc")
    if not cc:
        raise SystemExit("A host C compiler is required (use pinned IDF Docker).")
    with tempfile.TemporaryDirectory(prefix="c5-native-witness-") as directory:
        temporary = Path(directory)
        (temporary / "mock_sdk.h").write_text(SDK, encoding="utf-8")
        for name in ("esp_attr.h", "esp_intr_alloc.h", "freertos/FreeRTOS.h",
                     "soc/gpio_reg.h", "soc/io_mux_reg.h", "soc/interrupts.h", "soc/soc.h"):
            path = temporary / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('#include "mock_sdk.h"\n', encoding="utf-8")
        source = temporary / "harness.c"
        source.write_text(HARNESS, encoding="utf-8")
        binary = temporary / "witness-test"
        subprocess.run([cc, "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror",
                        "-I", str(temporary), "-I", str(ROOT / "main"),
                        str(source), "-o", str(binary)], check=True)
        result = subprocess.run([str(binary)], text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT)
        if result.returncode:
            print(result.stdout)
            result.check_returncode()
        print(result.stdout.strip())


if __name__ == "__main__":
    main()

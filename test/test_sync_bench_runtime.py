"""C5VRX by Twotoz/contributors: actual manual bench adapter ownership.

Production function extracted unchanged; actual synthesis linked separately.
No serial/device access. Register access would fail this minimal compilation.
"""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PREFIX = r'''
#include <assert.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "sync_private_bench.h"
#include "phase8_gain_lut.h"
#include "kp_tables.h"
#include "output_level.h"
typedef int esp_err_t;
#define ESP_OK 0
#define ESP_ERR_NOT_SUPPORTED 0x106
#define ESP_ERR_INVALID_ARG 0x102
#define SOC_CACHE_INTERNAL_MEM_VIA_L1CACHE 0
#define MALLOC_CAP_INTERNAL 1
#define MALLOC_CAP_DMA 2
#define MALLOC_CAP_8BIT 4
#define ESP_CACHE_MSYNC_FLAG_DIR_C2M 1
#define ESP_CACHE_MSYNC_FLAG_DIR_M2C 2
typedef int portMUX_TYPE;
#define portMUX_INITIALIZER_UNLOCKED 0
static uint8_t s_raw_ring[32768];
static uint16_t s_lut_live[1024];
static _Alignas(64) uint8_t allocation[9088];
static bool s_started=true,s_have_verified=true,s_lut_known=true,s_transport_fault;
static bool s_stream_generation_overflow,s_menu_active,s_rx_enabled=true,s_tx_enabled=true,s_bs_enabled=true;
static bool s_prog_lp=true,s_verified_lp=true;
static int s_verified_level;
static unsigned s_verified_output,s_output;
static uint32_t s_stream_generation=7;
static unsigned suspended,critical,enters,exits,allocations,frees,prepares,delays;
static bool fail_allocate,change_during_prepare,cache_fail,allocated;
static uint64_t ticks;
static char log_text[16384];static size_t log_bytes;
static void vTaskSuspendAll(void){assert(!suspended && !critical);++suspended;}
static int xTaskResumeAll(void){assert(suspended==1 && !critical);--suspended;return 0;}
static void enter(portMUX_TYPE *mux){(void)mux;assert(!critical && !suspended);++critical;++enters;}
static void leave(portMUX_TYPE *mux){(void)mux;assert(critical==1);--critical;++exits;}
#define portENTER_CRITICAL(p) enter(p)
#define portEXIT_CRITICAL(p) leave(p)
static void *heap_caps_aligned_alloc(size_t alignment,size_t bytes,unsigned caps){
    assert(!critical && !suspended && alignment==64 && bytes==sizeof(allocation) && caps==7 && !allocated);
    if(fail_allocate)return NULL;
    allocated=true;++allocations;return allocation;
}
static void heap_caps_free(void *p){assert(p==allocation && allocated && !critical && !suspended);allocated=false;++frees;}
static uint64_t esp_timer_get_time(void){return ++ticks;}
static uint32_t esp_cpu_get_cycle_count(void){return (uint32_t)(++ticks*240);}
static uint32_t esp_clk_cpu_freq(void){return 240000000;}
static void vTaskDelay(unsigned n){assert(n==1 && !critical && !suspended);++delays;}
static esp_err_t esp_cache_msync(void *p,size_t size,int flags){
    assert(((uintptr_t)p&63)==0 && (size&63)==0 && (uint8_t*)p>=allocation &&
        (uint8_t*)p+size<=allocation+sizeof(allocation));
    assert(flags==ESP_CACHE_MSYNC_FLAG_DIR_C2M || flags==ESP_CACHE_MSYNC_FLAG_DIR_M2C);
    if(cache_fail && critical)return ESP_ERR_INVALID_ARG;
    return ESP_ERR_NOT_SUPPORTED;
}
static int recorded_printf(const char *fmt,...){
    assert(!critical && !suspended);va_list ap;va_start(ap,fmt);
    int n=vsnprintf(log_text+log_bytes,sizeof(log_text)-log_bytes,fmt,ap);va_end(ap);
    assert(n>0 && (size_t)n<sizeof(log_text)-log_bytes);log_bytes+=(size_t)n;return n;
}
static bool instrumented_prepare(const sfw_ring_t*r,bool pal,sfw_bench_kind_t kind,sfw_private_pulse_t*p){
    assert(!critical && !suspended && r->ring==allocation && r->workspace==allocation+4096);
    assert(r->phase==allocation+8832 && !memcmp(r->phase,c5vrx_phase8_gain_lut,256));
    ++prepares;bool ok=sfw_bench_prepare_private(r,pal,kind,p);
    if(change_during_prepare)++s_stream_generation;
    return ok;
}
#define sfw_bench_prepare_private instrumented_prepare
#define printf recorded_printf
'''
SUFFIX = r'''
#undef printf
#undef sfw_bench_prepare_private
static void reset_log(void){log_bytes=0;log_text[0]=0;}
static void check_immutable(const uint8_t *raw,const uint16_t *lut){
    assert(!memcmp(raw,s_raw_ring,sizeof(s_raw_ring)) && !memcmp(lut,s_lut_live,sizeof(s_lut_live)));
    assert(!critical && !suspended && !allocated && allocations==frees && enters==exits);
}
int main(void){
    for(unsigned j=0;j<sizeof(s_raw_ring);++j)s_raw_ring[j]=(uint8_t)(j*53);
    memcpy(s_lut_live,kp_tables[0],sizeof(s_lut_live));
    uint8_t raw[sizeof(s_raw_ring)];uint16_t lut[1024];
    memcpy(raw,s_raw_ring,sizeof(raw));memcpy(lut,s_lut_live,sizeof(lut));
    sync_bench_context_t ctx;
    assert(!transport_bench_context_copy(&ctx,(uint16_t*)s_raw_ring));
    assert(transport_bench_context_copy(&ctx,(uint16_t*)allocation));
    assert(transport_bench_context_valid(&ctx));
    s_menu_active=true;assert(!transport_bench_context_copy(&ctx,(uint16_t*)allocation));s_menu_active=false;
    ++s_stream_generation;assert(!transport_bench_context_valid(&ctx));
    assert(transport_bench_context_copy(&ctx,(uint16_t*)allocation));
    s_output=9;assert(!transport_bench_context_valid(&ctx));s_output=0;
    s_prog_lp=false;assert(!transport_bench_context_valid(&ctx));s_prog_lp=true;
    transport_sync_bench();check_immutable(raw,lut);
    assert(prepares==32 && enters==32 && delays==8 && strstr(log_text,"end valid=1 writes_live=0"));
    assert(strstr(log_text,"irq_latency_measured=0") && strstr(log_text,"live_wcet_proven=0"));
    reset_log();fail_allocate=true;transport_sync_bench();fail_allocate=false;
    assert(strstr(log_text,"refused=memory"));check_immutable(raw,lut);
    reset_log();s_started=false;transport_sync_bench();s_started=true;
    assert(strstr(log_text,"refused=context"));check_immutable(raw,lut);
    reset_log();change_during_prepare=true;unsigned old_enters=enters;
    transport_sync_bench();change_during_prepare=false;
    assert(enters==old_enters && strstr(log_text,"end valid=0"));check_immutable(raw,lut);
    reset_log();cache_fail=true;transport_sync_bench();cache_fail=false;
    assert(strstr(log_text,"end valid=0"));check_immutable(raw,lut);
    reset_log();s_prog_lp=s_verified_lp=false;unsigned old_prepares=prepares;
    transport_sync_bench();assert(prepares-old_prepares==24 && strstr(log_text,"eligible=0 reason=lp_model_only"));
    check_immutable(raw,lut);
    puts("PASS: actual manual adapter allocation/free, private-only copies, context races, cache failures, IRQ envelopes and nonLP refusal");
}
'''


class BenchRuntimeTest(unittest.TestCase):
    def test_actual_adapter(self):
        source = (ROOT / "main/transport.c").read_text(encoding="utf-8")
        start = source.index("typedef struct {\n    uint32_t generation;\n    int level;")
        stop = source.index("void transport_print(void)", start)
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "bench.c").write_text(PREFIX + source[start:stop] + SUFFIX, encoding="utf-8")
            binary = folder / "bench"
            subprocess.run([os.environ.get("CC", "cc"), "-O2", "-std=c11", "-Wall", "-Wextra", "-Werror",
                            "-I", str(ROOT / "main"), str(folder / "bench.c"),
                            str(ROOT / "main/sync_flywheel.c"), "-o", str(binary)], check=True)
            subprocess.run([str(binary)], check=True)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""C5VRX by Twotoz/contributors: actual transport BF0 diagnostic bridge.

Real production function, mocked registers/PHY ownership. Checks no data,
descriptor or register writes and no logging while the PHY lease is held.
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
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "sync_transport_probe.h"
#include "sync_memory_frontier.h"
#define IQ_RATE_HZ 40000000u
#define LIVE_TX_NODES 9u
typedef struct dma_descriptor {
    struct { uint32_t size,length; } dw0;
    uint8_t *buffer;
    struct dma_descriptor *next;
} dma_descriptor_t;
typedef struct { dma_descriptor_t *dscr; uint8_t *buffer;uint32_t length; } ring_node_t;
typedef struct { uint32_t val; } reg_t;
typedef struct { struct { reg_t in_dscr_bf0;struct {uint32_t infifo_cnt_chn;} infifo_status;} in;
    struct {reg_t out_dscr_bf0,out_dscr,out_state;struct {uint32_t outfifo_cnt_chn;} outfifo_status;} out;} channel_t;
static struct { channel_t channel[3]; struct {reg_t in_done_des_addr,out_done_des_addr;} done_des_addr[3]; } AHB_DMA;
static struct {reg_t parl_clk_rx_conf,parl_clk_tx_conf,sysclk_conf,ahb_freq_conf,bus_clk_update;} PCR;
static struct {struct {uint32_t rx_fifo_wr_bit_cnt;} rx_st0;
    struct {uint32_t rx_fifo_rd_bit_cnt;} rx_st1;
    struct {uint32_t tx_fifo_rd_bit_cnt;} tx_st0;} PARL_IO;
static uint8_t s_raw_ring[32768];
static dma_descriptor_t rx_desc[9],s_live_tx_nodes[9];
static ring_node_t s_rx_nodes[9];
static int s_rx_node_count=9,s_rx_dma_ch=0,s_tx_dma_ch=1;
static bool s_started=true,s_have_verified=true,s_lut_known=true,s_transport_fault;
static bool s_stream_generation_overflow,s_menu_active,s_rx_enabled=true,s_tx_enabled=true,s_bs_enabled=true;
static uint32_t s_stream_generation=17;
static bool lease_available=true;
static unsigned lease,acquired,released;
static uint64_t timer=1000;
static char line[4096];
static void vTaskSuspendAll(void) {assert(!lease);++lease;++acquired;}
static int xTaskResumeAll(void) {assert(lease==1);--lease;++released;return 0;}
static uint64_t esp_timer_get_time(void) {timer+=2;return timer;}
static int recorded_printf(const char *format,...) {
    assert(!lease);va_list ap;va_start(ap,format);int n=vsnprintf(line,sizeof(line),format,ap);va_end(ap);
    assert(n>0 && (size_t)n<sizeof(line));return n;
}
#define printf recorded_printf
'''
SUFFIX = r'''
#undef printf
static void initialize(void) {
    for(unsigned i=0;i<9;++i) {
        unsigned length=i==8?32:4092;
        rx_desc[i]=(dma_descriptor_t){{length,length},s_raw_ring+4092*i,&rx_desc[(i+1)%9]};
        s_live_tx_nodes[i]=(dma_descriptor_t){{length,length},s_raw_ring+4092*i,&s_live_tx_nodes[(i+1)%9]};
        s_rx_nodes[i]=(ring_node_t){&rx_desc[i],rx_desc[i].buffer,length};
    }
    /* AHB descriptor registers are32bit on C5. Compile the host test as PIE
     * disabled so these real descriptor addresses also fit the mocked regs. */
    assert((uintptr_t)&rx_desc[8]<=UINT32_MAX);
    AHB_DMA.channel[0].in.in_dscr_bf0.val=(uint32_t)(uintptr_t)&rx_desc[5];
    AHB_DMA.channel[1].out.out_dscr_bf0.val=(uint32_t)(uintptr_t)&s_live_tx_nodes[1];
    AHB_DMA.channel[0].in.infifo_status.infifo_cnt_chn=13;
    AHB_DMA.channel[1].out.outfifo_status.outfifo_cnt_chn=37;
    PCR.parl_clk_rx_conf.val=PCR.parl_clk_tx_conf.val=0x60005;
    PCR.sysclk_conf.val=3u<<16;PCR.ahb_freq_conf.val=5;
    AHB_DMA.done_des_addr[0].in_done_des_addr.val=(uint32_t)(uintptr_t)&rx_desc[4];
    AHB_DMA.done_des_addr[1].out_done_des_addr.val=(uint32_t)(uintptr_t)&s_live_tx_nodes[0];
    AHB_DMA.channel[1].out.out_dscr.val=(uint32_t)(uintptr_t)&s_live_tx_nodes[2];
    AHB_DMA.channel[1].out.out_state.val=(uint32_t)(uintptr_t)&s_live_tx_nodes[2]&0x3ffffu;
    PARL_IO.rx_st0.rx_fifo_wr_bit_cnt=400;PARL_IO.rx_st1.rx_fifo_rd_bit_cnt=352;
    PARL_IO.tx_st0.tx_fifo_rd_bit_cnt=112;
    for(unsigned i=0;i<sizeof(s_raw_ring);++i)s_raw_ring[i]=(uint8_t)(i*179u+17u);
}
static void probe(void) {
    uint8_t before[sizeof(s_raw_ring)], descriptors[sizeof(rx_desc)+sizeof(s_live_tx_nodes)];
    unsigned char registers[sizeof(AHB_DMA)+sizeof(PCR)+sizeof(PARL_IO)];
    memcpy(before,s_raw_ring,sizeof(before));memcpy(descriptors,rx_desc,sizeof(rx_desc));
    memcpy(descriptors+sizeof(rx_desc),s_live_tx_nodes,sizeof(s_live_tx_nodes));
    memcpy(registers,&AHB_DMA,sizeof(AHB_DMA));memcpy(registers+sizeof(AHB_DMA),&PCR,sizeof(PCR));
    memcpy(registers+sizeof(AHB_DMA)+sizeof(PCR),&PARL_IO,sizeof(PARL_IO));
    transport_sync_probe_print();
    assert(!lease && acquired==released);
    assert(!memcmp(before,s_raw_ring,sizeof(before)));
    assert(!memcmp(descriptors,rx_desc,sizeof(rx_desc)));
    assert(!memcmp(descriptors+sizeof(rx_desc),s_live_tx_nodes,sizeof(s_live_tx_nodes)));
    assert(!memcmp(registers,&AHB_DMA,sizeof(AHB_DMA)));
    assert(!memcmp(registers+sizeof(AHB_DMA),&PCR,sizeof(PCR)));
    assert(!memcmp(registers+sizeof(AHB_DMA)+sizeof(PCR),&PARL_IO,sizeof(PARL_IO)));
    assert(strstr(line,"mode=read_only writes=0 continuous=0 valid=0"));
    assert(strstr(line,"clock_proven=0 fifo_proven=0"));
}
int main(void) {
    initialize();probe();
    assert(strstr(line,"reason=clock_unverified") && strstr(line,"geometry=1"));
    assert(strstr(line,"stream_gen=17") && strstr(line,"rx_l1_bytes=13 tx_l1_bytes=37"));
    assert(strstr(line,"mem_candidate=1 mem_result=0") && strstr(line,"ahb_hz=40000000"));
    assert(strstr(line,"mem_writes_safe=0"));
    /* An analog PHY owner cannot stall the independent DMA observation. */
    lease_available=false;probe();assert(strstr(line,"mem_candidate=1"));lease_available=true;
    PCR.bus_clk_update.val=1;probe();assert(strstr(line,"mem_candidate=0 mem_result=4"));PCR.bus_clk_update.val=0;
    ++AHB_DMA.channel[1].out.out_dscr.val;probe();assert(strstr(line,"mem_candidate=0 mem_result=5"));
    --AHB_DMA.channel[1].out.out_dscr.val;
    s_menu_active=true;probe();assert(strstr(line,"reason=not_live"));s_menu_active=false;
    s_rx_nodes[8].length=4092;probe();assert(strstr(line,"reason=geometry_unverified"));s_rx_nodes[8].length=32;
    rx_desc[3].next=&rx_desc[5];probe();
    assert(strstr(line,"geom_error=4 geom_side=1 geom_index=3"));rx_desc[3].next=&rx_desc[4];
    s_live_tx_nodes[2].dw0.length=4080;probe();
    assert(strstr(line,"geom_error=5 geom_side=2 geom_index=2 geom_offset=8184 geom_bytes=4080"));
    s_live_tx_nodes[2].dw0.length=4092;
    s_rx_node_count=8;probe();assert(strstr(line,"geom_error=1"));s_rx_node_count=9;
    s_rx_dma_ch=-1;probe();assert(strstr(line,"reason=geometry_unverified"));s_rx_dma_ch=0;
    rx_desc[7].dw0.size=rx_desc[7].dw0.length=s_rx_nodes[7].length=2062;
    rx_desc[8].buffer=s_rx_nodes[8].buffer=s_raw_ring+30706;
    rx_desc[8].dw0.size=rx_desc[8].dw0.length=s_rx_nodes[8].length=2062;
    probe();assert(strstr(line,"geometry=1") && strstr(line,"geom_error=0"));
    ++s_stream_generation;probe();assert(strstr(line,"stream_gen=18"));
    s_stream_generation_overflow=true;probe();assert(strstr(line,"reason=not_live"));
    puts("PASS: actual BF0 bridge source, metadata, raw/descriptor/register immutability and balanced logging leases");
}
'''


class TransportRuntimeTest(unittest.TestCase):
    def test_actual_read_only_bridge(self):
        source = (ROOT / "main/transport.c").read_text(encoding="utf-8")
        begin = source.index("static sync_memory_registers_t transport_memory_registers(void)")
        end = source.index("void transport_print(void)", begin)
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for name in ("sync_transport_probe.h", "dma_progress.h", "sync_memory_frontier.h"):
                (folder / name).write_bytes((ROOT / "main" / name).read_bytes())
            (folder / "probe.c").write_text(PREFIX + source[begin:end] + SUFFIX, encoding="utf-8")
            binary = folder / "runtime"
            subprocess.run([os.environ.get("CC", "cc"), "-O2", "-std=c11", "-Wall", "-Wextra", "-Werror",
                            "-no-pie", "-I", str(folder), str(folder / "probe.c"), "-o", str(binary)], check=True)
            subprocess.run([str(binary)], check=True)


if __name__ == "__main__":
    unittest.main()

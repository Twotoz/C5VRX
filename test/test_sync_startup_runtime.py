#!/usr/bin/env python3
"""C5VRX by Twotoz/contributors: actual halted RX/TX startup ordering.

Compile production start_rings_tail with hardware/driver boundaries mocked.
Uses actual receiver macros; does not claim actual lag or DMA publication.
"""
from pathlib import Path
import os
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def function(source, signature):
    start = source.index(signature + "\n{")
    brace = source.index("{", start)
    depth, end = 1, brace + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end] + "\n"


PREFIX = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef int esp_err_t;
#define ESP_OK 0
#define ESP_ERR_NOT_FOUND 1
#define ESP_ERR_INVALID_STATE 2
#define TEST_TX_ERROR 99
#define BITSCRAMBLER_DIR_TX 0
static uint32_t s_stream_generation;
static bool s_stream_generation_overflow;
static int s_rx_dma_ch=-1,s_tx_dma_ch=-1;
typedef struct {uint32_t val;} reg_t;
static struct {reg_t int_clr;} PARL_IO;
static struct {struct {reg_t clr;} in_intr[3],out_intr[3];} AHB_DMA;
static struct {reg_t state[1];} BITSCRAMBLER;
static int rx_found[2],tx_found[2],rx_find,tx_find,rx_nodes,tx_nodes,tx_result;
static unsigned quiets,rx_clears,tx_clears,starts,delays;
static uint32_t generation_before;
static bool initial_overflow, rx_endless;
static uint64_t delayed_us;
static unsigned hardware_rotation,discovered_rotation,metadata[9];
static char trace[32];
static void event(char code) {size_t n=strlen(trace); assert(n+1<sizeof(trace));trace[n]=code;trace[n+1]=0;}
static void quiet_interrupts(void) {
    assert(s_stream_generation==generation_before+1u);
    assert(s_stream_generation_overflow==(initial_overflow || generation_before==UINT32_MAX));
    ++quiets; event('Q');
}
static int find_dma(bool rx) {
    event(rx?'R':'T');
    if (rx) {assert(rx_find<2);return rx_found[rx_find++];}
    assert(tx_find<2);return tx_found[tx_find++];
}
static int clear_eof(int channel,bool rx) {
    assert(channel>=0 && channel<3);
    event(rx?'E':'e');
    if(rx) {
        assert(!starts && !delays); ++rx_clears;
        discovered_rotation=hardware_rotation;
        for(unsigned i=0;i<9;++i) metadata[i]=(hardware_rotation+i)%9;
        rx_endless=rx_nodes>=3; return rx_nodes;
    }
    assert(starts==1 && quiets==2 && rx_clears==1); ++tx_clears;return tx_nodes;
}
static void esp_rom_delay_us(uint64_t us) {
    assert(rx_endless && rx_clears==1 && !starts);
    ++delays; delayed_us=us; event('D');
}
static esp_err_t start_tx(void) {
    assert(rx_endless && delays==1 && rx_clears==1);
    ++starts; event('S');
    /* Model a long driver-ready wait crossing many RX descriptor boundaries.
     * RX must already be endless. Pointer rotation is not a new allocation. */
    hardware_rotation=(hardware_rotation+5)%9;
    for(unsigned i=0;i<9;++i) assert(metadata[i]==(discovered_rotation+i)%9);
    return tx_result;
}
'''
SUFFIX = r'''
static void reset(void) {
    rx_found[0]=rx_found[1]=0;tx_found[0]=tx_found[1]=1;
    rx_find=tx_find=0;rx_nodes=9;tx_nodes=9;tx_result=ESP_OK;
    quiets=rx_clears=tx_clears=starts=delays=0;delayed_us=0;rx_endless=false;trace[0]=0;
    s_rx_dma_ch=s_tx_dma_ch=-1;
    memset(&PARL_IO,0,sizeof(PARL_IO));memset(&AHB_DMA,0,sizeof(AHB_DMA));memset(&BITSCRAMBLER,0,sizeof(BITSCRAMBLER));
    generation_before=s_stream_generation;initial_overflow=s_stream_generation_overflow;
}
static void check_attempt(int expected,bool counters) {
    assert(start_rings_tail()==expected);
    assert(s_stream_generation==generation_before+1u);
    assert(s_stream_generation_overflow==(initial_overflow || generation_before==UINT32_MAX));
    assert((PARL_IO.int_clr.val==UINT32_MAX)==counters);
    assert((BITSCRAMBLER.state[0].val==(1u<<31))==counters);
    for(unsigned i=0;i<3;++i) {
        assert((AHB_DMA.in_intr[i].clr.val==UINT32_MAX)==(counters && (int)i==s_rx_dma_ch));
        assert((AHB_DMA.out_intr[i].clr.val==UINT32_MAX)==(counters && (int)i==s_tx_dma_ch));
    }
}
int main(void) {
    assert(LIVE_PIPELINE_LAG_BYTES==3u*RAW_RING_BYTES/4u);
    assert((uint64_t)LIVE_PIPELINE_LAG_BYTES*1000000ULL/IQ_RATE_HZ==614u);
    /* All discoverable ring rotations remain valid through the TX wait. */
    for(unsigned rotation=0;rotation<9;++rotation) {
        hardware_rotation=rotation;reset();check_attempt(ESP_OK,true);
        assert(!strcmp(trace,"QREDTSRTQe") && rx_clears==1 && tx_clears==1 && delays==1);
        assert(delayed_us==(uint64_t)LIVE_PIPELINE_LAG_BYTES*1000000ULL/IQ_RATE_HZ);
    }
    reset();rx_found[0]=-1;check_attempt(ESP_ERR_NOT_FOUND,false);assert(!strcmp(trace,"QR") && !delays && !starts);
    for(int n=0;n<3;++n) {
        reset();rx_nodes=n;check_attempt(ESP_ERR_INVALID_STATE,false);
        assert(!strcmp(trace,"QRE") && !delays && !starts);
    }
    reset();tx_found[0]=-1;check_attempt(ESP_ERR_NOT_FOUND,false);
    assert(!strcmp(trace,"QREDT") && rx_endless && !starts);
    reset();tx_result=TEST_TX_ERROR;check_attempt(TEST_TX_ERROR,false);
    assert(!strcmp(trace,"QREDTS") && rx_endless && !tx_clears);
    reset();rx_found[1]=-1;check_attempt(ESP_ERR_NOT_FOUND,false);
    assert(!strcmp(trace,"QREDTSRT") && rx_endless && rx_clears==1);
    reset();tx_found[1]=-1;check_attempt(ESP_ERR_NOT_FOUND,false);
    assert(!strcmp(trace,"QREDTSRT") && rx_endless && rx_clears==1);
    reset();tx_nodes=0;check_attempt(ESP_ERR_INVALID_STATE,false);
    assert(!strcmp(trace,"QREDTSRTQe") && rx_endless);
    /* A rotating BF0 in one immutable ring is fine; a channel relocation
     * invalidates the early metadata. Refuse before overwriting globals or
     * clearing counters on a channel never captured at startup. */
    reset();rx_found[1]=2;check_attempt(ESP_ERR_INVALID_STATE,false);
    assert(!strcmp(trace,"QREDTSRT") && s_rx_dma_ch==0 && s_tx_dma_ch==1 && rx_clears==1 && !tx_clears);
    reset();tx_found[1]=0;check_attempt(ESP_ERR_INVALID_STATE,false);
    assert(!strcmp(trace,"QREDTSRT") && s_rx_dma_ch==0 && s_tx_dma_ch==1 && rx_clears==1 && !tx_clears);
    reset();rx_found[1]=2;tx_found[1]=0;check_attempt(ESP_ERR_INVALID_STATE,false);
    assert(s_rx_dma_ch==0 && s_tx_dma_ch==1 && !tx_clears);
    /* Subsequent recovery attempt re-discovers both channels and metadata;
     * the failed attempt's indices do not select counter-clear targets. */
    reset();rx_found[0]=rx_found[1]=2;tx_found[0]=tx_found[1]=0;check_attempt(ESP_OK,true);
    assert(s_rx_dma_ch==2 && s_tx_dma_ch==0 && rx_clears==1 && tx_clears==1);
    s_stream_generation=UINT32_MAX;s_stream_generation_overflow=false;
    reset();rx_found[0]=-1;check_attempt(ESP_ERR_NOT_FOUND,false);
    assert(s_stream_generation==0 && s_stream_generation_overflow);
    reset();check_attempt(ESP_OK,true);assert(s_stream_generation==1 && s_stream_generation_overflow);
    puts("PASS: actual startup RX endless before614us delay/TX wait, all failure paths, successful-only counter clear, discovery rotations, generation/overflow latch, no IRQ masking");
}
'''


class SyncStartupRuntime(unittest.TestCase):
    def test_actual_start(self):
        source = (ROOT / "main/transport.c").read_text(encoding="utf8")
        receiver = (ROOT / "main/receiver.h").read_text(encoding="utf8")
        macros = "\n".join(re.search(rf"^#define\s+{name}\s+.+$", receiver, re.M)[0]
                           for name in ("RAW_RING_BYTES", "LIVE_PIPELINE_LAG_BYTES", "IQ_RATE_HZ")) + "\n"
        actual = function(source, "static esp_err_t start_rings_tail(void)")
        # Hardware interrupt-source suppression is intentional. CPU interrupt
        # masking around the614us delay or driver wait is not.
        self.assertNotIn("portENTER", actual)
        self.assertNotIn("vTaskSuspendAll", actual)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "startup.c"
            path.write_text(PREFIX + macros + actual + SUFFIX, encoding="utf8")
            binary = Path(directory) / "startup"
            subprocess.run([os.environ.get("CC", "cc"), "-O2", "-std=c11", "-Wall", "-Wextra", "-Werror",
                            str(path), "-o", str(binary)], check=True)
            subprocess.run([str(binary)], check=True)


if __name__ == "__main__":
    unittest.main()

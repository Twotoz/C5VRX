#!/usr/bin/env python3
"""Execute production TBC context/deadline guards with mocked DMA/time boundaries.

These are ownership checks, not C5 execution-time or physical-video proof.
"""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def function(source, signature):
    start = source.index(signature)
    brace = source.index('{', start)
    end, depth = brace + 1, 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


class RuntimeGuards(unittest.TestCase):
    def test_context_and_fresh_dma(self):
        source = (ROOT / 'main/sync_tbc.c').read_text()
        ref_start = source.index('typedef struct {')
        ref_end = source.index('} tbc_reference_t;', ref_start) + len('} tbc_reference_t;')
        prefix = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#define RAW_RING_BYTES 32768
#define IQ_RATE_HZ 40000000
#define LIVE_PIPELINE_LAG_BYTES 24576
#define TBC_TX_MARGIN 8188
static uint32_t s_control_epoch=4,s_gain_epoch=5,phy=3;
static int64_t now=1000000;
static uint32_t rx_offset=30000,tx_offset=5424,generation=7;
static bool s_on=true,s_cpu_tripped,paused,startup,s_scan_active,busy,live=true;
static struct {int64_t abs,us;} s_pos={30000,1000000};
static uint32_t phy_generation(void){return phy;}
static int64_t esp_timer_get_time(void){return now;}
static bool receiver_paused(void){return paused;}
static bool calibration_in_startup(void){return startup;}
static bool phy_busy(void){return busy;}
static bool transport_ring_view(uint32_t *rx,uint32_t *tx,uint32_t *gen){
 *rx=rx_offset;*tx=tx_offset;*gen=generation;return live;
}
'''
        context = 'typedef struct {uint32_t generation;tbc_reference_t ref;} tbc_write_context_t;\n'
        actual = '\n'.join(function(source, sig) for sig in (
            'static bool reference_current(', 'static int64_t reconcile(',
            'static bool write_allowed('))
        suffix = r'''
int main(void){
 tbc_write_context_t c={.generation=7,.ref={.seq=1,.standard=2,.phy=3,.control=4,.gain=5,.measured_us=1000000}};
 assert(reference_current(&c.ref,now));
 assert(write_allowed(&c,20000,20152));
 now+=500001;assert(!write_allowed(&c,20000,20152));now=1000000;
 now--;assert(!write_allowed(&c,20000,20152));now++;
 ++phy;assert(!write_allowed(&c,20000,20152));--phy;
 ++s_gain_epoch;assert(!write_allowed(&c,20000,20152));--s_gain_epoch;
 ++s_control_epoch;assert(!write_allowed(&c,20000,20152));--s_control_epoch;
 ++generation;assert(!write_allowed(&c,20000,20152));--generation;
 live=false;assert(!write_allowed(&c,20000,20152));live=true;
 paused=true;assert(!write_allowed(&c,20000,20152));paused=false;
 startup=true;assert(!write_allowed(&c,20000,20152));startup=false;
 busy=true;assert(!write_allowed(&c,20000,20152));busy=false;
 s_scan_active=true;assert(!write_allowed(&c,20000,20152));s_scan_active=false;
 s_on=false;assert(!write_allowed(&c,20000,20152));s_on=true;
 s_cpu_tripped=true;assert(!write_allowed(&c,20000,20152));s_cpu_tripped=false;
 /* Analysis finished, but TX has advanced since the old snapshot. */
 tx_offset=15000;assert(!write_allowed(&c,20000,20152));tx_offset=5424;
 assert(!write_allowed(&c,20000,30000)); /* RX has not completed the endpoint */
 /* After a full ring wrap, data from the previous pass is rejected. */
 now+=820;assert(!write_allowed(&c,20000,20152));
 puts("PASS: production TBC freshness/context, menu/paused/PHY/CPU refusal and fresh DMA write deadlines");
}
'''
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)
            (p/'guard.c').write_text(prefix+source[ref_start:ref_end]+'\n'+context+actual+suffix)
            subprocess.run([os.environ.get('CC','cc'),'-O2','-std=c11','-Wall','-Wextra','-Werror',
                            str(p/'guard.c'),'-o',str(p/'guard')],check=True)
            subprocess.run([str(p/'guard')],check=True)


if __name__ == '__main__':
    unittest.main()

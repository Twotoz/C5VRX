"""C5VRX by Twotoz/contributors: execute production IQ80 gain probe copy.

Check IQ40 time geometry at every ring wrap/descriptor discovery rotation,
plus lapping, deadlines and malformed-ring refusal. DMA/MMIO only are mocked.
"""
from pathlib import Path
import subprocess
import tempfile
import unittest
from test_dfilt_runtime import function

ROOT=Path(__file__).resolve().parents[1]
SDK=r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include "snapshot.h"
#define PROBE_REGIONS 4u
#define PROBE_REGION_BYTES 64u
#define PROBE_BYTES 256u
#define IQ_RATE_HZ 80000000u
static uint8_t s_raw_ring[32768];
static struct {uint8_t *buffer;size_t length;} s_rx_nodes[9];
static int s_rx_node_count=9,s_rx_dma_ch=1,active,later;
static unsigned reads;
static uint64_t elapsed=1;
static int64_t esp_timer_get_time(void){return reads++?(int64_t)elapsed:0;}
static uint32_t rx_active_addr(void){return (uint32_t)(reads<=1?active:later);}
static int ring_node_index(uint32_t a){return (int)a;}
static void sync_dma_m2c(void *p,size_t n){(void)p;(void)n;}
'''
MAIN=r'''
int main(void){
 uint8_t dst[256];int node=-1;
 const size_t offsets[4]={512,1536,2560,4028};
 for(unsigned i=0;i<32768;++i)s_raw_ring[i]=(uint8_t)(i/2);
 for(unsigned rotation=0;rotation<9;++rotation){
  for(unsigned k=0;k<9;++k){unsigned p=(rotation+k)%9;s_rx_nodes[k].buffer=s_raw_ring+p*4092;s_rx_nodes[k].length=p==8?32:4092;}
  for(active=0;active<9;++active){
   later=active;reads=0;elapsed=1;
   assert(copy_probe_window80(dst,&node)&&node==active);
   size_t end=(size_t)(s_rx_nodes[active].buffer-s_raw_ring);
   size_t start=(end+32768-8190)%32768;
   for(unsigned r=0;r<4;++r)for(unsigned k=0;k<64;++k){
    size_t p=(start+2*(offsets[r]+k))%32768;
    assert(dst[r*64+k]==(uint8_t)(p/2));
   }
   elapsed=1000;reads=0;assert(!copy_probe_window80(dst,0));
   elapsed=1;later=(active+8)%9;reads=0;assert(!copy_probe_window80(dst,0));
  }
 }
 active=later=0;reads=0;++s_rx_nodes[3].buffer;
 assert(!copy_probe_window80(dst,0));
 s_rx_dma_ch=-1;reads=0;assert(!copy_probe_window80(dst,0));
 puts("PASS IQ80 gain probe: exact IQ40 sample/time geometry at 81 ring positions, stale/lapped/malformed reads rejected");
}
'''
class Probe(unittest.TestCase):
    def test_production_copy(self):
        body=function((ROOT/'main/transport.c').read_text(),'static bool copy_probe_window80(')
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            (p/'t.c').write_text(SDK+body+MAIN)
            subprocess.run(['cc','-O2','-std=c11','-Wall','-Wextra','-Werror','-I',str(ROOT/'main'),str(p/'t.c'),'-o',str(p/'t')],check=True)
            subprocess.run([str(p/'t')],check=True)
if __name__=='__main__':unittest.main()

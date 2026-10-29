#!/usr/bin/env python3
"""Execute the production bounded patch engine against a register boundary.
Checks exact STOCK rollback, masked fields, sequential selections, unsupported
PHY and an injected readback failure; no RF performance claims.
"""
from pathlib import Path
import subprocess
import tempfile
import unittest
import re
ROOT = Path(__file__).resolve().parents[1]
PREFIX = r'''
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include <assert.h>
#include <stdlib.h>
#include "experimental.h"
#ifdef C5VRX_PHY_PINNED
static uint8_t phy_param[2048], i2c[256];
static uint16_t bus[16];
static uint32_t regs[16];
static uint32_t addresses[16];
static unsigned reg_count, writes;
static bool fail_i2c;
static int64_t now;
static uint32_t *reg_ptr(uint32_t addr) {
 if (addr==0x600a0890) {
  for(unsigned i=0;i<reg_count;++i) if(addresses[i]==0x600a0884 && (regs[i]&2)) {
   const unsigned word=regs[i];bus[(word>>2)&15]=(word>>6)&0x1ff;
  }
 }
 for(unsigned i=0;i<reg_count;++i) if(addresses[i]==addr)return &regs[i];
 assert(reg_count<16);addresses[reg_count]=addr;return &regs[reg_count++];
}
static int64_t esp_timer_get_time(void){return ++now;}
static uint16_t phy_pbus_rd(uint32_t b,uint32_t bank){assert(bank==2);return bus[b];}
static uint8_t phy_i2c_readReg(uint8_t b,uint8_t h,uint8_t r){assert(b==0x6a&&h==1);return i2c[r];}
static void phy_i2c_writeReg(uint8_t b,uint8_t h,uint8_t r,uint8_t v){assert(b==0x6a&&h==1);++writes;if(fail_i2c){fail_i2c=false;return;}i2c[r]=v;}
static void phy_current_level_set(uint8_t v){++writes;phy_param[41]=v;}
static void esp_restart(void){abort();}
#endif
'''
HARNESS = r'''
int main(void) {
#ifdef C5VRX_PHY_PINNED
 REG(0x600a0438)=0xabcdef12;REG(0x600a0c0c)=0x12345678;
 REG(0x600a0884)=1;REG(0x600a0890)=0;
 REG(0x600a7c14)=0x55554000;REG(0x600a7810)=0x11223344;
 REG(0x600a7814)=0x55667788;REG(0x600a7830)=0xabcdef80;
 i2c[0]=0xcf;i2c[1]=0x7f;phy_param[41]=77;phy_param[42]=1;phy_param[690]=42;
 for(unsigned k=0;k<7;++k){phy_param[0x2d2+2*k]=20;phy_param[0x2d3+2*k]=8;}
 bus[9]=0x123;bus[10]=0x122;
 const unsigned ids[]={EXP_IQ_CORR,EXP_LNA_BIAS,EXP_CURRENT,EXP_LOOP_CAP,EXP_SPUR0};
 uint32_t stock[16];memcpy(stock,regs,sizeof(regs));
 for(unsigned i=0;i<5;++i)assert(experimental_phy_option(ids[i],0));
 assert(!writes&&!memcmp(stock,regs,sizeof(regs)));
 for(unsigned i=0;i<5;++i) {
   for(unsigned v=1;v<experimental_items[ids[i]].count;++v)assert(experimental_phy_option(ids[i],v));
   assert(experimental_phy_option(ids[i],0));
   assert(!memcmp(stock,regs,sizeof(regs)));
   assert(i2c[0]==0xcf&&phy_param[41]==77&&phy_param[690]==42&&bus[9]==0x123&&bus[10]==0x122);
 }
 for(unsigned i=0;i<5;++i)assert(experimental_phy_option(ids[i],1));
 experimental_phy_prepare_restore();assert(!memcmp(stock,regs,sizeof(regs)));
 fail_i2c=true;assert(!experimental_phy_option(EXP_LNA_BIAS,1));assert(i2c[0]==0xcf);
 i2c[0]=0xab;assert(!experimental_phy_option(EXP_LNA_BIAS,1));assert(i2c[0]==0xab);
 REG(0x600a7c14)|=0x2000;assert(!experimental_phy_option(EXP_SPUR0,1));
#else
 assert(!experimental_phy_option(EXP_IQ_CORR,1));assert(experimental_phy_option(EXP_IQ_CORR,0));
 experimental_phy_prepare_restore();
#endif
 return 0;
}
'''

class Patches(unittest.TestCase):
    def test_production(self):
        source = (ROOT/'main/experimental_phy.c').read_text()
        source = re.sub(r'^#include[^\n]*\n', '', source, flags=re.M)
        source = source.replace('#define REG(a) (*(volatile uint32_t *)(uintptr_t)(a))', '#define REG(a) (*reg_ptr(a))')
        source = re.sub(r'^extern [^\n]+;\n', '', source, flags=re.M)
        for pinned in [False, True]:
            with self.subTest(pinned=pinned), tempfile.TemporaryDirectory() as tmp:
                p=Path(tmp);(p/'t.c').write_text(PREFIX+source+HARNESS)
                cmd=['cc','-O2','-std=c11','-Wall','-Wextra','-Werror','-I',str(ROOT/'main')]
                if pinned:cmd+=['-DC5VRX_PHY_PINNED=1']
                subprocess.run(cmd+[str(p/'t.c'),'-o',str(p/'t')],check=True)
                subprocess.run([str(p/'t')],check=True)

if __name__=='__main__':unittest.main()

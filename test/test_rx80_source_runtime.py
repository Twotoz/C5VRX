"""C5VRX by Twotoz/contributors: production ADC probe ownership/rollback."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from test_dfilt_runtime import function

ROOT=Path(__file__).resolve().parents[1]
SDK=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
typedef int esp_err_t;
#define ESP_OK 0
#define ESP_FAIL -1
#define ESP_ERR_NOT_SUPPORTED 1
#define ESP_ERR_INVALID_ARG 2
#define ESP_ERR_INVALID_RESPONSE 3
#define DFILT_REG 0x600a0430u
#define DFILT_SHIFT 18u
#define DFILT_MASK (15u<<18)
#ifdef C5VRX_PHY_PINNED
static uint32_t filt=0xa5a5a5a5u, adc=0x16100003u;
static uint8_t i2c=0x53;
static unsigned depth,calls;
static bool fail_entry,fail_restore;
static uint32_t read_reg(uint32_t a){assert(depth);return a==DFILT_REG?filt:adc;}
static void write_reg(uint32_t a,uint32_t v){assert(depth);if(a==DFILT_REG)filt=v;else adc=v;}
#define REG_READ(a) read_reg(a)
#define REG_WRITE(a,v) write_reg(a,v)
static void phy_begin(void){assert(!depth);++depth;}
static void phy_end(void){assert(depth==1);--depth;}
static uint8_t phy_i2c_readReg(uint8_t b,uint8_t h,uint8_t r){assert(depth&&b==0x66&&h==0&&r==4);return i2c;}
static void phy_i2c_writeReg(uint8_t b,uint8_t h,uint8_t r,uint8_t v){assert(depth&&b==0x66&&h==0&&r==4);i2c=v^(fail_restore?0x80:0);}
void phy_adc_rate_set(uint32_t s){assert(depth&&s<=1);adc=(adc&~3u)|(s?3u:0u);i2c=(i2c&~4u)|(s?0u:4u);if(fail_entry){adc^=1u;fail_entry=false;}}
static void phy_rx_filter_mode(uint32_t m){assert(depth);filt=(filt&~DFILT_MASK)|(m<<DFILT_SHIFT);}
static int phy_dfilt_hw_mode(void){return (filt&DFILT_MASK)>>DFILT_SHIFT;}
static void observe(unsigned s,unsigned m){assert(depth==1);assert((adc&3u)==(s?3u:0u));assert((i2c&4u)==(s?0u:4u));assert(phy_dfilt_hw_mode()==(int)m);++calls;}
#endif
'''
MAIN=r'''
int main(void){
#ifdef C5VRX_PHY_PINNED
 const uint32_t f=filt,a=adc;const uint8_t i=i2c;
 assert(phy_rx80_source_probe(0)==ESP_ERR_INVALID_ARG);
 assert(phy_rx80_source_probe(observe)==ESP_OK);
 assert(calls==8 && !depth && filt==f && adc==a && i2c==i);
 fail_entry=true;calls=0;
 assert(phy_rx80_source_probe(observe)==ESP_ERR_INVALID_RESPONSE);
 assert(!calls && !depth && filt==f && adc==a && i2c==i);
 fail_restore=true;
 assert(phy_rx80_source_probe(observe)==ESP_FAIL);
 assert(!depth);
#else
 assert(phy_rx80_source_probe(0)==ESP_ERR_NOT_SUPPORTED);
#endif
}
'''

class SourceProbe(unittest.TestCase):
    def test_production_probe(self):
        body=function((ROOT/'main/phy.c').read_text(),'esp_err_t phy_rx80_source_probe(')
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            (p/'t.c').write_text(SDK+body+MAIN)
            for pinned in (False,True):
                args=['cc','-std=c11','-Wall','-Wextra','-Werror']
                if pinned:args+=['-DC5VRX_PHY_PINNED=1']
                subprocess.run(args+[str(p/'t.c'),'-o',str(p/'t')],check=True)
                subprocess.run([str(p/'t')],check=True)

if __name__=='__main__':unittest.main()

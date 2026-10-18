#!/usr/bin/env python3
"""C5VRX by Twotoz/contributors: execute actual flag pad helpers on mocked MMIO.

Tests exact restoration, unrelated GPIO preservation and observable write faults.
This is not a physical pin voltage or complete transport transaction proof.
"""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PREFIX = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "sync_native_trace.h"
#define IRAM_ATTR
enum {IO_MUX_GPIO23_REG,GPIO_FUNC23_OUT_SEL_CFG_REG,GPIO_PIN23_REG,
 GPIO_ENABLE_REG,GPIO_OUT_REG,GPIO_ENABLE_W1TC_REG,GPIO_ENABLE_W1TS_REG,
 GPIO_OUT_W1TC_REG,GPIO_OUT_W1TS_REG};
static uint32_t reg[9];
static unsigned writes,drop;
static bool writes_started;
#define REG_READ(addr) reg[addr]
static void write_reg(unsigned addr,uint32_t value) {
 ++writes;
 if(addr==GPIO_ENABLE_W1TC_REG || addr==GPIO_ENABLE_W1TS_REG ||
    addr==GPIO_OUT_W1TC_REG || addr==GPIO_OUT_W1TS_REG)
  assert(value==(UINT32_C(1)<<23));
 if(!writes_started){assert(addr==GPIO_ENABLE_W1TC_REG);writes_started=true;}
 if(writes==drop)return;
 switch(addr) {
 case GPIO_ENABLE_W1TC_REG:reg[GPIO_ENABLE_REG]&=~value;break;
 case GPIO_ENABLE_W1TS_REG:reg[GPIO_ENABLE_REG]|=value;break;
 case GPIO_OUT_W1TC_REG:reg[GPIO_OUT_REG]&=~value;break;
 case GPIO_OUT_W1TS_REG:reg[GPIO_OUT_REG]|=value;break;
 default:reg[addr]=value;break;
 }
}
#define REG_WRITE(addr,value) write_reg(addr,value)
'''
SUFFIX = r'''
static flag_pin_t storage;
static uint32_t original[9];
static void reset(unsigned enable,unsigned output) {
 memset(reg,0,sizeof(reg));
 reg[IO_MUX_GPIO23_REG]=0x12341000u;
 reg[GPIO_FUNC23_OUT_SEL_CFG_REG]=48u;
 reg[GPIO_PIN23_REG]=0x1200u;
 reg[GPIO_ENABLE_REG]=0x415aa55au | (enable?FLAG_PIN_MASK:0u);
 reg[GPIO_OUT_REG]=0x104a595au | (output?FLAG_PIN_MASK:0u);
 memcpy(original,reg,sizeof(reg));
 storage=(flag_pin_t){.iomux=reg[IO_MUX_GPIO23_REG],
  .matrix=reg[GPIO_FUNC23_OUT_SEL_CFG_REG],.pad=reg[GPIO_PIN23_REG],
  .enable=reg[GPIO_ENABLE_REG]&FLAG_PIN_MASK,
  .output=reg[GPIO_OUT_REG]&FLAG_PIN_MASK,.clamp_matrix=256u|1024u,
  .saved=true,.active=true};
 s_flag_storage=&storage;s_flag_pin=s_flag_storage;
 writes=drop=0;writes_started=false;
}
static void unrelated_unchanged(void) {
 assert((reg[GPIO_ENABLE_REG]&~FLAG_PIN_MASK)==(original[GPIO_ENABLE_REG]&~FLAG_PIN_MASK));
 assert((reg[GPIO_OUT_REG]&~FLAG_PIN_MASK)==(original[GPIO_OUT_REG]&~FLAG_PIN_MASK));
}
int main(void) {
 unsigned checks=0;
 for(unsigned enable=0;enable<2u;++enable)for(unsigned output=0;output<2u;++output){
  reset(enable,output);
  assert(flag_pin_apply(true)&&flag_pin_clamped());unrelated_unchanged();
  assert(!flag_pin_restored());
  writes=0;writes_started=false;
  assert(flag_pin_apply(false)&&flag_pin_restored());unrelated_unchanged();
  assert(!memcmp(reg,original,sizeof(reg)));checks+=4;
 }
 /* Lose the output-low write while the original latch was high. */
 reset(1,1);drop=2;
 assert(!flag_pin_apply(true)&&!flag_pin_clamped());++checks;
 /* Lose the matrix clamp: DATA0 must never be declared isolated. */
 reset(1,0);drop=4;
 assert(!flag_pin_apply(true)&&!flag_pin_clamped());++checks;
 /* Lose the final enable: an electrically disabled pad is not a clamp. */
 reset(1,0);drop=6;
 assert(!flag_pin_apply(true)&&!flag_pin_clamped());++checks;
 /* Restoration must detect a missing reconnect and a missing latch restore. */
 reset(1,1);assert(flag_pin_apply(true));writes=0;drop=4;writes_started=false;
 assert(!flag_pin_apply(false)&&!flag_pin_restored());++checks;
 reset(1,1);assert(flag_pin_apply(true));writes=0;drop=2;writes_started=false;
 assert(!flag_pin_apply(false)&&!flag_pin_restored());++checks;
 reset(1,0);storage.saved=false;
 assert(!flag_pin_apply(true)&&writes==0);++checks;
 storage.saved=true;storage.active=false;
 assert(!flag_pin_apply(true)&&flag_pin_apply(false)&&writes==0);++checks;
 s_flag_pin=NULL;
 assert(!flag_pin_apply(true)&&flag_pin_apply(false)&&writes==0);++checks;
 printf("PASS: %u actual flag-pad restoration/fault checks\n",checks);
 return 0;
}
'''


class Runtime(unittest.TestCase):
    def test_actual_flag_pin_helpers(self):
        source = (ROOT / 'main/transport.c').read_text()
        begin = source.index('typedef struct {\n    uint32_t iomux,matrix,pad,enable,output,clamp_matrix,generation;')
        end = source.index('\n/* Pin identity is acquired', begin)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / 'pin.c').write_text(PREFIX + source[begin:end] + SUFFIX)
            subprocess.run([os.environ.get('CC', 'cc'), '-O2', '-std=c11', '-Wall',
                            '-Wextra', '-Werror', '-I', str(ROOT / 'main'),
                            str(path / 'pin.c'), '-o', str(path / 'pin')], check=True)
            result = subprocess.run([str(path / 'pin')], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertIn('PASS:', result.stdout)
            print(result.stdout, end='')


if __name__ == '__main__':
    unittest.main()

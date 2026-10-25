#!/usr/bin/env python3
"""C5VRX by Twotoz/contributors: GPIO2 clock ownership, production code.

Host driver mocks check TX setup and error propagation in both builds;
not physical clock/data timing or FPGA validation.
"""
from pathlib import Path
import os
import re
import subprocess
import tempfile
import unittest

def function(source, signature):
    start = source.index(signature)
    brace = source.index('{', start)
    depth = 1
    end = brace + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


ROOT = Path(__file__).resolve().parents[1]

PREFIX = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
typedef int esp_err_t;
#define ESP_OK 0
#define PARLIO_CLK_SRC_DEFAULT 1
#define PARLIO_SHIFT_EDGE_NEG 2
#define PARLIO_BIT_PACK_ORDER_LSB 3
#define BITSCRAMBLER_DIR_TX 4
#define SOC_BITSCRAMBLER_ATTACH_PARL_IO 5
static char s_raw_ring[32768];
static const int s_dac_gpio[8]={23,24,11,12,8,9,-1,-1};
#define GPIO_MODE_OUTPUT 6
#define GPIO_PULLUP_DISABLE 0
#define GPIO_PULLDOWN_DISABLE 0
#define GPIO_INTR_DISABLE 0
typedef struct {unsigned long long pin_bit_mask;int mode,pull_up_en,pull_down_en,intr_type;} gpio_config_t;
static int gpio_calls,gpio_fail,gpio_level[64],gpio_out[64];
static esp_err_t gpio_set_level(int pin,int level) {
 ++gpio_calls;assert(pin>=0&&pin<64&&level==0);gpio_level[pin]=1;
 return gpio_fail==gpio_calls?98:ESP_OK;
}
static esp_err_t gpio_config(const gpio_config_t *c) {
 ++gpio_calls;assert(c->mode==GPIO_MODE_OUTPUT&&!c->pull_up_en&&!c->pull_down_en);
 assert(__builtin_popcountll(c->pin_bit_mask)==1);
 int pin=__builtin_ctzll(c->pin_bit_mask);assert(gpio_level[pin]);gpio_out[pin]=1;
 return gpio_fail==gpio_calls?98:ESP_OK;
}
typedef struct {
 int clk_src,clk_in_gpio_num,output_clk_freq_hz,data_width,clk_out_gpio_num;
 int valid_gpio_num,trans_queue_depth,max_transfer_size,dma_burst_size;
 int shift_edge,bit_pack_order,data_gpio_nums[8];
} parlio_tx_unit_config_t;
typedef struct {int dir,attach_to;} bitscrambler_config_t;
static int s_tx,s_bs,fail_stage,stage;
static esp_err_t parlio_new_tx_unit(const parlio_tx_unit_config_t *c,int *tx) {
 ++stage;assert(tx==&s_tx);
#ifdef CONFIG_C5VRX_PARLIO_SAMPLE_CLOCK
 assert(c->clk_out_gpio_num==2);
#else
 assert(c->clk_out_gpio_num==-1);
#endif
 assert(c->clk_in_gpio_num==-1 && c->valid_gpio_num==-1);
 assert(c->output_clk_freq_hz==40000000 && c->data_width==8);
 assert(c->shift_edge==PARLIO_SHIFT_EDGE_NEG);
 for(unsigned k=0;k<8;++k)assert(c->data_gpio_nums[k]==s_dac_gpio[k]);
 return fail_stage==stage?99:ESP_OK;
}
static esp_err_t bitscrambler_new(const bitscrambler_config_t *c,int *bs) {
 ++stage;assert(bs==&s_bs && c->dir==BITSCRAMBLER_DIR_TX);
 assert(c->attach_to==SOC_BITSCRAMBLER_ATTACH_PARL_IO);
 return fail_stage==stage?99:ESP_OK;
}
static esp_err_t enable_tx(void){++stage;return fail_stage==stage?99:ESP_OK;}
'''
SUFFIX = r'''
int main(void) {
 for(fail_stage=0;fail_stage<=3;++fail_stage) {
  stage=0;assert(prepare_tx()==(fail_stage?99:ESP_OK));
  assert(stage==(fail_stage?fail_stage:3));
 }
 assert(transport_sample_clock_enabled()==
#ifdef CONFIG_C5VRX_PARLIO_SAMPLE_CLOCK
 true
#else
 false
#endif
 );
 const unsigned bits=transport_dac_bits();
 assert(bits==CONFIG_C5VRX_DAC_BITS);
 assert(reserve_extra_dac_pins()==ESP_OK);
 assert(gpio_calls==3*(int)(bits-6));
 assert(gpio_out[10]==(bits>6) && gpio_out[2]==(bits>7));
 for(int k=0;k<64;++k) if(k!=10&&k!=2) assert(!gpio_out[k]&&!gpio_level[k]);
 for(gpio_fail=1;gpio_fail<=gpio_calls;++gpio_fail) {
  const int expected=gpio_calls;gpio_calls=0;
  assert(reserve_extra_dac_pins()==98);
  assert(gpio_calls==gpio_fail);gpio_calls=expected;
 }
 puts("PASS: TX clock/pins/edge/error propagation and extra DAC pins");
}
'''


class SampleClock(unittest.TestCase):
    def compile_run(self, source, enabled, extra=()):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            (path / 'test.c').write_text(source)
            args = [os.environ.get('CC', 'cc'), '-O2', '-std=c11', '-Wall', '-Wextra', '-Werror']
            if enabled:
                args += ['-DCONFIG_C5VRX_PARLIO_SAMPLE_CLOCK=1']
            subprocess.run(args + list(extra) + [str(path / 'test.c'), '-o', str(path / 'test')], check=True)
            subprocess.run([str(path / 'test')], check=True)

    # (clock, physical DAC bits): the four menuconfig output-pin choices.
    MODES = ((False, 6), (True, 6), (True, 7), (False, 8))

    def guards(self, source):
        start = source.index('#ifndef CONFIG_C5VRX_DAC_BITS')
        return source[start:source.index('bool transport_sample_clock_enabled(void)')]

    def test_tx_and_clock_status(self):
        source = (ROOT / 'main/transport.c').read_text()
        signatures = ['static esp_err_t prepare_tx(void)',
                      'bool transport_sample_clock_enabled(void)',
                      'unsigned transport_dac_bits(void)',
                      'static esp_err_t reserve_extra_dac_pins(void)']
        actual = '\n'.join(function(source, sig) for sig in signatures)
        for enabled, bits in self.MODES:
            with self.subTest(clock=enabled, bits=bits):
                # PR #259 defines RF_IQ_GPIO_NUMS when GPIO10 leaves IQ.
                self.compile_run('#define RF_IQ_GPIO_NUMS {1,0,25,7,27,5,3,4}\n' +
                                 PREFIX + self.guards(source) + actual + SUFFIX, enabled,
                                 ['-DCONFIG_C5VRX_DAC_BITS=%d' % bits])

    def test_pin_conflicts_stop_the_build(self):
        guards = self.guards((ROOT / 'main/transport.c').read_text())
        cases = (('', False, 6, True), ('', True, 6, True), ('', True, 7, False),
                 ('', False, 8, False), ('#define RF_IQ_GPIO_NUMS {0}\n', True, 7, True),
                 ('#define RF_IQ_GPIO_NUMS {0}\n', False, 8, True),
                 ('#define RF_IQ_GPIO_NUMS {0}\n', True, 8, False))
        for iq, clock, bits, ok in cases:
            with self.subTest(iq=bool(iq), clock=clock, bits=bits):
                with tempfile.TemporaryDirectory() as d:
                    path = Path(d) / 'guard.c'
                    path.write_text(iq + guards.split('static const int')[0] + 'int x;\n')
                    args = [os.environ.get('CC', 'cc'), '-std=c11', '-fsyntax-only',
                            '-DCONFIG_C5VRX_DAC_BITS=%d' % bits, str(path)]
                    if clock:
                        args.append('-DCONFIG_C5VRX_PARLIO_SAMPLE_CLOCK=1')
                    result = subprocess.run(args, capture_output=True, text=True)
                    self.assertEqual(result.returncode == 0, ok, result.stderr)



if __name__ == '__main__':
    unittest.main()

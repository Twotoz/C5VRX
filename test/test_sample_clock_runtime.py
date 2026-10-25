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
 puts("PASS: TX clock/pins/edge/error propagation in both configurations");
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

    def test_tx_and_clock_status(self):
        source = (ROOT / 'main/transport.c').read_text()
        signatures = ['static esp_err_t prepare_tx(void)',
                      'bool transport_sample_clock_enabled(void)']
        actual = '\n'.join(function(source, sig) for sig in signatures)
        for enabled in (False, True):
            with self.subTest(clock=enabled):
                self.compile_run(PREFIX + actual + SUFFIX, enabled)



if __name__ == '__main__':
    unittest.main()

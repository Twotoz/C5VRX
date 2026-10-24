#!/usr/bin/env python3
"""C5VRX by Twotoz/contributors: GPIO2 clock ownership, production code.

Host driver mocks check TX setup and flywheel/probe refusal in both builds;
not physical clock/data timing or FPGA validation.
"""
from pathlib import Path
import os
import re
import subprocess
import tempfile
import unittest

from test_sync_startup_runtime import function
from test_sync_output_probe_runtime import PREFIX as PROBE_PREFIX

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
static unsigned saves;
static bool saved,expected_saved;
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
typedef struct {bool output_requested,requested;} sync_live_owner_t;
static sync_live_owner_t *s_live_owner;
#ifdef CONFIG_C5VRX_PARLIO_SAMPLE_CLOCK
static bool s_flywheel_boot=false;
#else
static bool s_flywheel_boot=true;
#endif
static void settings_save_flywheel(bool on){++saves;saved=on;}
'''
SUFFIX = r'''
int main(void) {
 for(fail_stage=0;fail_stage<=3;++fail_stage) {
  stage=0;assert(prepare_tx()==(fail_stage?99:ESP_OK));
  assert(stage==(fail_stage?fail_stage:3));
 }
#ifdef CONFIG_C5VRX_PARLIO_SAMPLE_CLOCK
 assert(!s_flywheel_boot);
#else
 assert(s_flywheel_boot);
#endif
 for(unsigned initial=0;initial<2;++initial) {
  saved=expected_saved=initial;saves=0;
  transport_sync_flywheel_set_boot(initial);
#ifdef CONFIG_C5VRX_PARLIO_SAMPLE_CLOCK
  assert(!transport_sync_flywheel_enabled());
  transport_sync_flywheel_toggle();
  assert(!s_live_owner && !transport_sync_flywheel_enabled());
  transport_sync_flywheel_set(false,true);
  assert(saved==expected_saved && !saves && !s_live_owner);
#else
  assert(transport_sync_flywheel_enabled()==(bool)initial);
  transport_sync_flywheel_set(true,true);
  assert(s_live_owner && transport_sync_flywheel_enabled() && saved && saves==1);
  transport_sync_flywheel_toggle();
  assert(!transport_sync_flywheel_enabled() && !saved && saves==2);
  free(s_live_owner);s_live_owner=NULL;
#endif
 }
 puts("PASS: TX clock/pins/edge/error propagation and flywheel/NVS ownership");
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

    def test_tx_and_preferences(self):
        source = (ROOT / 'main/transport.c').read_text()
        signatures = ['static esp_err_t prepare_tx(void)',
                      'void transport_sync_flywheel_set_boot(bool on)',
                      'bool transport_sync_flywheel_enabled(void)',
                      'void transport_sync_flywheel_set(bool on, bool save)',
                      'void transport_sync_flywheel_toggle(void)']
        actual = '\n'.join(function(source, sig) for sig in signatures)
        for enabled in (False, True):
            with self.subTest(clock=enabled):
                self.compile_run(PREFIX + actual + SUFFIX, enabled)

    def test_probe_reservation(self):
        source = (ROOT / 'main/sync_output_probe.c').read_text()
        source = re.sub(r'^#include[^\n]*\n', '', source, flags=re.M)
        headers = ''
        for name in ('sync_native_trace', 'sync_output_probe', 'sync_phase_bracket',
                     'sync_native_track', 'sync_native_flag_track'):
            text = (ROOT / f'main/{name}.h').read_text()
            headers += re.sub(r'^#(?:include|pragma)[^\n]*\n', '', text, flags=re.M)
        # The fixture already stubs all peripheral APIs. No session allocation,
        # clock setup or pin writes may occur, even with a free GPIO2.
        suffix = r'''
int main(void) {
 (void)vTaskDelay;
 (void)owner_live;
 assert(!sync_output_live_begin(7,NULL));
 assert(!sync_output_live_begin_flag(7,NULL));
 assert(!s_session && !s_native && !s_busy);
 assert(!writes && !clock_calls && !sync_output_live_active());
 assert(sync_output_live_end());
 puts("PASS: clock build probe refuses before allocation or hardware access");
}
'''
        # Fixture helpers are shared with the much larger ordinary-probe test.
        # Separate TU matches firmware: probe cannot fold the transport query.
        with tempfile.TemporaryDirectory() as d:
            clock = Path(d) / 'clock.c'
            transport = (ROOT / 'main/transport.c').read_text()
            clock.write_text('#include <stdbool.h>\n' + function(
                transport, 'bool transport_sample_clock_enabled(void)'))
            declaration = 'bool transport_sample_clock_enabled(void);\n'
            self.compile_run(PROBE_PREFIX + declaration + headers + source + suffix,
                             True, (str(clock),))


if __name__ == '__main__':
    unittest.main()

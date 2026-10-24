"""Exercise actual PHY lab C lifecycle with MMIO/I2C fault injection.

C5VRX by Twotoz and contributors. Host stubs replace only hardware/RTOS
includes; the profile and rollback code is compiled directly from main/.
"""
from pathlib import Path
import subprocess
import tempfile
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import phy_noise_lab as lab


class Transport(unittest.TestCase):
    def records(self):
        raw = bytes(range(256))*31 + bytes(range(254))
        checksum = lab.fnv(raw)
        lines = ['PHYLAB_PATTERN id=0 pattern=F', 'PHYLAB_PROFILE id=0 name=NF_AUTO',
                 f'PHYLAB_IQ_BEGIN id=0 step=0 rep=0 stage=STOCK bytes=8190 rate_hz=40000000 fnv1a={checksum}']
        lines += [f'PHYLAB_IQ_DATA offset={off} {raw[off:off+256].hex()}' for off in range(0, len(raw), 256)]
        lines += [f'PHYLAB_IQ_END fnv1a={checksum}', 'PHYLAB_RESULT id=0 status=PASS restored=1']
        return lines

    def test_integrity_and_pattern(self):
        with tempfile.TemporaryDirectory() as d:
            manifest = lab.decode('\n'.join(self.records()), Path(d))
            self.assertEqual(manifest['captures'][0]['profile'], 'NF_F')
            self.assertEqual(len((Path(d) / manifest['captures'][0]['file']).read_bytes()), 8190)

    def test_lost_data_rejected(self):
        lines = self.records(); del lines[4]
        with tempfile.TemporaryDirectory() as d, self.assertRaises(ValueError):
            lab.decode('\n'.join(lines), Path(d))

    def test_corrupt_checksum_rejected(self):
        lines = self.records(); lines[-2] = 'PHYLAB_IQ_END fnv1a=00000000'
        with tempfile.TemporaryDirectory() as d, self.assertRaises(ValueError):
            lab.decode('\n'.join(lines), Path(d))

    def test_corrupt_capture_retained_as_rejection(self):
        lines = self.records(); del lines[4]
        with tempfile.TemporaryDirectory() as d:
            manifest = lab.decode('\n'.join(lines), Path(d), tolerate_corrupt=True)
            self.assertEqual(manifest['captures'], [])
            self.assertEqual(len(manifest['rejected_captures']), 1)


STUBS = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <setjmp.h>
#include <math.h>
#include <inttypes.h>
#define C5VRX_PHY_PINNED 1
#define C5V4_LEVEL_SAMPLE_BYTES 8190u
#define IQ_RATE_HZ 40000000u
#define pdMS_TO_TICKS(x) (x)
#include "cn_auto.h"
static uint32_t regs[0x3000];
#define MM(a) regs[((a)-0x600A0000u)/4]
unsigned char phy_param[1024];
static uint8_t analog_regs[256];
static int inject, stages;
static bool mutated;
static jmp_buf jump;
uint8_t s_current_gain=80;
static void vTaskDelay(unsigned ticks){(void)ticks;}
static int64_t esp_timer_get_time(void){return 1000;}
static void esp_restart(void){longjmp(jump,1);}
static void phy_begin(void){}
static void phy_end(void){}
static bool tx_live=true,tx_fault;
static bool transport_live(void){return tx_live;}
static bool transport_faulted(void){return tx_fault;}
static void transport_stop_tx(void){tx_live=false;}
static void transport_resume_live(void){tx_live=true;}
static int phy_dfilt_hw_mode(void){return 0;}
static const char *firmware_id(void){return "HOST_TEST_ONLY";}
static unsigned rf_frequency_mhz(void){return 5865;}
static int rf_offset_khz(void){return 0;}
typedef struct{uint8_t max_index;} arc_gain_table_t;
typedef struct{uint8_t rf_stage,bb_code,fine_code;uint16_t rf_code;uint32_t packed_state;} arc_gain_tuple_t;
static bool arc_gain_tuple_decode(const arc_gain_table_t *t,uint8_t g,arc_gain_tuple_t *out){(void)t;(void)g;memset(out,0,sizeof(*out));return true;}
static unsigned arc_gain_highest_rf_stage_start(const arc_gain_table_t *t){(void)t;return 54;}
static const arc_gain_table_t *rf_gain_table(void){static arc_gain_table_t t={80};return &t;}
static void receiver_set_gain(uint8_t g){s_current_gain=g;}
#define PHY_DCO_TRY_MAX 8
#define ESP_OK 0
#define ESP_FAIL -1
static int dc_pair[2]={240,250},dc_fail,dc_writes;
static bool dc_held=true;
static bool phy_dco_held(void){return dc_held;}
static bool phy_dco_read_pair(int out[2]){out[0]=dc_pair[0];out[1]=dc_pair[1];return true;}
static int phy_dco_try_pair(uint32_t gain,const int expected[2],const int target[2]){
    (void)gain;assert(expected[0]==dc_pair[0]&&expected[1]==dc_pair[1]);
    int di=abs(target[0]-expected[0]),dq=abs(target[1]-expected[1]);
    assert((di==0)!=(dq==0));assert(di<=8&&dq<=8);
    ++dc_writes;if(dc_fail&&dc_writes==dc_fail)return ESP_FAIL;
    dc_pair[0]=target[0];dc_pair[1]=target[1];return ESP_OK;
}
uint8_t phy_i2c_readReg(uint8_t b,uint8_t h,uint8_t r){(void)b;(void)h;return analog_regs[r];}
void phy_i2c_writeReg(uint8_t b,uint8_t h,uint8_t r,uint8_t v){(void)b;(void)h;if(inject==2&&v!=60)return;analog_regs[r]=v;}
void phy_adc_rate_set(uint32_t mode){MM(0x600A0448)=(MM(0x600A0448)&~3u)|(mode?3:0);analog_regs[4]=(analog_regs[4]&~4u)|(mode?0:4);}
uint16_t phy_pbus_rd(uint32_t block,uint32_t bank){(void)bank;return block==1?0xd0u:0u;}
void phy_11p_set(uint8_t en,uint8_t mode){
    phy_param[0x26]=en;phy_param[0x27]=mode;
    MM(0x600A7CE4)=(MM(0x600A7CE4)&~0x1cu)|0x10;
    MM(0x600A7030)&=~0x20u;
    MM(0x600A7048)=(MM(0x600A7048)&~0x7f00u)|0x4000;
    MM(0x600A71C4)=(MM(0x600A71C4)&~0xfe0000u)|0x440000;
    if(inject!=1)for(int i=6;i<=13;++i)analog_regs[i]=60;
}
static bool copy_snapshot(uint8_t *raw){
    for(unsigned i=0;i<8190;++i)raw[i]=(uint8_t)((((i+stages)%5)<<4)|(((i+stages)/5)%5));
    ++stages;
    if(inject==4 && stages==5)MM(0x600A7CE4)^=4;
    if(!mutated){MM(0x600A7CE4)^=0x80000000;mutated=true;}
    return true;
}
static bool context_ok(void){return inject!=3;}
'''

HARNESS = r'''
static void reset(void){
    memset(regs,0x55,sizeof(regs));
    for(unsigned i=0;i<256;++i)analog_regs[i]=(uint8_t)(i+17);
    phy_param[0x26]=7;phy_param[0x27]=9;
    inject=0;stages=0;mutated=false;lab_gain=80;
    dc_pair[0]=240;dc_pair[1]=250;dc_held=true;dc_fail=0;dc_writes=0;sample_dco_check=false;
}
int main(void){
    uint8_t *raw=malloc(8190);uint32_t original[4];uint8_t saved[8];
    reset();
    for(unsigned i=0;i<4;++i)original[i]=MM(profiles[0].p[i].addr);
    for(unsigned i=0;i<8;++i)saved[i]=analog_regs[6+i];
    assert(run_profile(16,&profiles[0],raw,context_ok,999999));
    assert(stages==16&&phy_param[0x26]==7&&phy_param[0x27]==9);
    for(unsigned i=0;i<8;++i)assert(saved[i]==analog_regs[6+i]);
    for(unsigned i=0;i<4;++i)assert(MM(profiles[0].p[i].addr)==(original[i]^(i==0?0x80000000:0)));
    reset();inject=1;
    assert(!run_profile(16,&profiles[0],raw,context_ok,999999));
    assert(stages==4&&phy_param[0x26]==7&&phy_param[0x27]==9);
    reset();inject=3;
    assert(!run_profile(17,&profiles[1],raw,context_ok,999999));assert(stages==0);
    reset();inject=4;
    assert(!run_profile(16,&profiles[0],raw,context_ok,999999));assert(stages==5);
    assert((MM(0x600A7CE4)&0x1c)==(0x55555555&0x1c));
    reset();inject=2;
    if(setjmp(jump)==0){run_profile(16,&profiles[0],raw,context_ok,999999);assert(!"failed rollback must reboot");}
    assert(stages==12);
    for(unsigned k=0;k<sizeof(audit_profiles)/sizeof(audit_profiles[0]);++k){
        reset();const profile_t *p=&audit_profiles[k];
        uint32_t before=MM(p->p[0].addr);
        assert(run_profile(32+k,p,raw,context_ok,999999));
        assert(MM(p->p[0].addr)==before);
        assert(phy_param[0x26]==7&&phy_param[0x27]==9);
        for(unsigned i=0;i<8;++i)assert(analog_regs[6+i]==i+23);
    }
    reset();tx_live=true;tx_fault=false;
    assert(run_output(64,raw,context_ok,999999));assert(tx_live&&stages==16);
    for(unsigned mode=0;mode<2;++mode){
        reset();uint32_t digital=MM(0x600A0448);uint8_t analog=analog_regs[4];
        assert(run_adc(80+mode,mode,raw,context_ok,999999));
        assert(MM(0x600A0448)==digital&&analog_regs[4]==analog&&sample_adc_expected==-1);
    }
    for(unsigned k=0;k<sizeof(packet_profiles)/sizeof(packet_profiles[0]);++k){
        reset();const profile_t *p=&packet_profiles[k];uint32_t before[8];
        for(unsigned j=0;j<p->n;++j)before[j]=MM(p->p[j].addr);
        assert(run_profile(96+k,p,raw,context_ok,999999));
        for(unsigned j=0;j<p->n;++j)assert(MM(p->p[j].addr)==before[j]);
    }
    for(unsigned k=0;k<sizeof(clock_profiles)/sizeof(clock_profiles[0]);++k){
        reset();const profile_t *p=&clock_profiles[k];uint32_t before=MM(p->p[0].addr);
        assert(run_profile(112+k,p,raw,context_ok,999999));assert(MM(p->p[0].addr)==before);
    }
    lab_packed_valid=true;lab_packed_gain=0;assert(hardware_gain_ok());
    lab_packed_gain=1;assert(!hardware_gain_ok());lab_packed_valid=false;
    for(unsigned k=0;k<sizeof(combo_profiles)/sizeof(combo_profiles[0]);++k){
        reset();const profile_t *p=&combo_profiles[k];uint32_t before[12];uint8_t adc=analog_regs[4];
        for(unsigned j=0;j<p->n;++j)before[j]=MM(p->p[j].addr);
        assert(run_profile(160+k,p,raw,context_ok,999999));
        for(unsigned j=0;j<p->n;++j)assert(MM(p->p[j].addr)==before[j]);
        assert(analog_regs[4]==adc&&phy_param[0x26]==7&&phy_param[0x27]==9);
    }
    reset();lab_packed_valid=false;sample_dco_check=true;
    const int target[2]={258,229},original_dc[2]={240,250};
    assert(dco_move(target));assert(dc_pair[0]==258&&dc_pair[1]==229&&dc_writes==6);
    assert(dco_move(original_dc));assert(dc_pair[0]==240&&dc_pair[1]==250);
    dc_held=false;assert(!dco_move(target));dc_held=true;
    sample_dco[0]=239;assert(!dco_expected_ok());
    reset();lab_packed_valid=false;dc_fail=2;sample_dco_check=true;
    assert(!dco_move(target));assert(dco_move(original_dc));assert(dc_pair[0]==240&&dc_pair[1]==250);
    reset();lab_packed_valid=false;inject=3;
    assert(!run_dco(176,78,raw,context_ok,999999));assert(dc_pair[0]==240&&dc_pair[1]==250&&dc_held);
    reset();lab_packed_valid=false;
    assert(run_dco(176,78,raw,context_ok,999999));assert(dc_pair[0]==240&&dc_pair[1]==250&&dc_held&&!sample_dco_check);
    free(raw);fprintf(stderr,"PHY lab lifecycle: PASS (rollback, unrelated bits, apply fault, context refusal, fatal restore, bounded DC trials)\n");
    return 0;
}
'''


def c_test():
    source = (ROOT / 'main/phy_noise_lab.c').read_text()
    source = '\n'.join(line for line in source.splitlines() if not line.startswith('#include'))
    with tempfile.TemporaryDirectory() as d:
        c = Path(d) / 'test.c'; exe = Path(d) / 'test'
        c.write_text(STUBS + '\n' + source + '\n' + HARNESS)
        subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-I'+str(ROOT/'main'), str(c), '-lm', '-o', str(exe)], check=True)
        with (Path(d)/'output.log').open('w') as log:
            subprocess.run([str(exe)], stdout=log, check=True)


if __name__ == '__main__':
    if '--c' in sys.argv:
        c_test()
    else:
        unittest.main()

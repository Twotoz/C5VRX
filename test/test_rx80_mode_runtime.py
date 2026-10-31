"""C5VRX by Twotoz/contributors: production rate/AGC mode integration."""
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from test_dfilt_runtime import function

ROOT=Path(__file__).resolve().parents[1]
SDK=r'''
#include <assert.h>
#include <stdio.h>
#include "rx80_agc.h"
#define FLOG_E_GAIN_MODE 1
static uint8_t s_gain_mode=GAIN_MODE_DEFAULT,s_gain80_restore=GAIN_MODE_DEFAULT;
static unsigned s_gain_rate,events;
static rx80_agc_t s_rx80_agc;
static bool s_receiver_running=true;
static uint8_t s_current_gain=54;
static bool receiver_paused(void){return false;}
static void receiver_set_gain(uint8_t g){s_current_gain=g;}
static void flightlog_event(int e,const void *p,size_t n){assert(e==1&&p&&n==1);++events;}
uint8_t receiver_gain_mode(void);
const char *receiver_gain_mode_name(uint8_t,char*,unsigned);
'''
MAIN=r'''
int main(void){
 assert(receiver_gain_mode()==GAIN_MODE_LEVEL);
 uint8_t mode=GAIN_MODE_LEVEL;
 for(unsigned n=0;n<40;++n){mode=receiver_gain_mode_next(mode);assert(mode!=GAIN_MODE_RX80);}
 receiver_rate_changed(2);assert(receiver_gain_mode()==GAIN_MODE_RX80);
 receiver_rate_changed(0);assert(receiver_gain_mode()==GAIN_MODE_LEVEL);
 receiver_set_gain_mode(GAIN_MODE_RSSI,false);
 receiver_rate_changed(2);assert(receiver_gain_mode()==GAIN_MODE_RX80);
 receiver_rate_changed(1);assert(receiver_gain_mode()==GAIN_MODE_RSSI);
 receiver_rate_changed(2);assert(receiver_gain_mode()==GAIN_MODE_RX80);
 receiver_set_gain_mode(GAIN_MODE_LEVEL,true);
 receiver_rate_changed(2);assert(receiver_gain_mode()==GAIN_MODE_LEVEL); /* explicit comparison choice wins */
 receiver_rate_changed(0);assert(receiver_gain_mode()==GAIN_MODE_LEVEL);
 receiver_set_gain_mode(29,false);
 receiver_rate_changed(2);assert(receiver_gain_mode()==29); /* fixed-gain bench stays fixed */
 receiver_rate_changed(0);assert(receiver_gain_mode()==29);
 receiver_set_gain_mode(GAIN_MODE_RX80,false);assert(receiver_gain_mode()==GAIN_MODE_DEFAULT);
 assert(events==1); /* automatic rate routing neither persists nor impersonates user changes */
 puts("PASS RX80 mode: auto on rate entry, old model restored, 40-mode cycle unchanged, operator/fixed overrides respected");
}
'''
class Mode(unittest.TestCase):
    def test_production_routing(self):
        h=(ROOT/'main/receiver.h').read_text()
        src=(ROOT/'main/receiver.c').read_text()
        constants='\n'.join(re.findall(r'^#define GAIN_MODE_.*$',h,re.M))+'\n'
        array=re.search(r'static const uint8_t s_gain_modes\[\].*?;',src,re.S).group(0)
        bodies=''.join(function(src,sig) for sig in ('uint8_t receiver_gain_mode(void)',
            'const char *receiver_gain_mode_name(', 'void receiver_set_gain_mode(',
            'void receiver_rate_changed(', 'uint8_t receiver_gain_mode_next('))
        auto=function(h,'static inline bool receiver_gain_mode_auto(')
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            (p/'t.c').write_text(constants+SDK+auto+array+bodies+MAIN)
            subprocess.run(['cc','-O2','-std=c11','-Wall','-Wextra','-Werror','-I',str(ROOT/'main'),str(p/'t.c'),'-o',str(p/'t')],check=True)
            subprocess.run([str(p/'t')],check=True)
if __name__=='__main__':unittest.main()

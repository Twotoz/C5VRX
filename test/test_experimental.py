#!/usr/bin/env python3
"""C5VRX by Twotoz/contributors: production experiment state across reboot.
SDK/receiver boundaries are stubbed; table validation, NVS handling, fallback,
mask, legacy snapshots and logging use the production implementation.
"""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BOUNDARY = r'''
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <assert.h>
#include <stdio.h>
#include "experimental.h"
#define GAIN_MODE_DEFAULT 254
#define PHY_DFILT_MODE_DEFAULT 12
#define RF_LANE_COARSE 0
#define RF_LANE_FINE 1
#define RF_LANE_ULTRAFINE 2
#define FLOG_E_EXP 17
#define FLOG_T_EXP 7
static bool s_afc_enabled=true, live, fail_phy, busy, stored;
static uint32_t s_control_epoch;
static unsigned mode=254, dfilt=12, demod, lane=2, events, records, dc_resets;
static experimental_blob_t disk;
static uint8_t event_data[4];
static bool blob_load(const char *k, void *d, size_t n) {
 assert(!strcmp(k,"exp") && n==sizeof(disk));
 if (!stored) return false;
 memcpy(d,&disk,n);return true;
}
static bool blob_store(const char *k,const void *d,size_t n) {
 assert(!strcmp(k,"exp") && n==sizeof(disk)); memcpy(&disk,d,n);stored=true;return true;
}
static unsigned receiver_gain_mode(void){return mode;}
static unsigned phy_dfilt_mode(void){return dfilt;}
static unsigned transport_demod(void){return demod;}
static unsigned rf_lane(void){return lane;}
static uint32_t phy_generation(void){return 1;}
static bool phy_try_actuator(uint32_t gen){assert(gen==1);return !busy;}
static void phy_end_actuator(void){}
static void phy_observation_changed(void){}
static void receiver_dc_reset_all(void){++dc_resets;}
static void profile_set_live_mode(bool on){live=on;}
static bool profile_live_mode(void){return live;}
static void flightlog_event(unsigned kind,const void *p,unsigned len){assert(kind==17 && len==4);memcpy(event_data,p,4);++events;}
static void flightlog_record(unsigned kind,const void *p,unsigned len){assert(kind==7&&len==EXP_ITEMS+2);assert(((const uint8_t*)p)[0]==EXP_VERSION);++records;}
bool experimental_phy_option(unsigned item,uint8_t value){(void)item;(void)value;return !fail_phy;}
'''
HARNESS = r'''
int main(void) {
 experimental_load();assert(!experimental_mask());
 experimental_log_state();assert(records==1);
 assert(experimental_log_interval()==1);
 for(unsigned id=EXP_IQ_CORR;id<EXP_ITEMS;++id) {
   for(unsigned v=1;v<experimental_items[id].count;++v) {
     assert(experimental_set(id,v));assert(experimental_get(id)==v);
     assert(disk.value[id]==v && experimental_blob_valid(&disk));
     if(id!=EXP_AUTO_PROFILE) assert(experimental_mask() & (1u<<id));
   }
   assert(experimental_set(id,0));
 }
 assert(!experimental_mask());
 assert(!experimental_set(EXP_ITEMS,1));assert(!experimental_set(EXP_GAIN,1));
 assert(!experimental_set(EXP_FOLD_MEMORY,3));
 disk.value[EXP_FOLD_MEMORY]=255;stored=true;experimental_load();
 assert(experimental_get(EXP_FOLD_MEMORY)==0); /* reject whole corrupt blob */
 disk=(experimental_blob_t){.version=EXP_VERSION};disk.value[EXP_FOLD_MEMORY]=2;
 experimental_load();assert(experimental_get(EXP_FOLD_MEMORY)==2);
 assert(experimental_set(EXP_LOG_CADENCE,1));assert(experimental_log_interval()==5);
 assert(experimental_set(EXP_LOG_CADENCE,2));assert(!experimental_log_interval());
 busy=true;assert(!experimental_set(EXP_IQ_CORR,1));busy=false;
 fail_phy=true;assert(!experimental_set(EXP_IQ_CORR,1));
 assert(!experimental_get(EXP_IQ_CORR)&&experimental_phy_failed()&&!event_data[3]);
 fail_phy=false;assert(experimental_set(EXP_IQ_CORR,1));assert(experimental_transfer_active());
 fail_phy=true;assert(!experimental_phy_restore());
 assert(!experimental_get(EXP_IQ_CORR));experimental_poll();assert(!disk.value[EXP_IQ_CORR]);
 fail_phy=false;experimental_reset();assert(!experimental_mask());
 mode=255;dfilt=4;demod=1;s_afc_enabled=false;live=true;
 assert((experimental_mask()&15)==15);
 uint8_t v[EXP_ITEMS];experimental_snapshot(v);
 assert(v[0]==255&&v[1]==4&&v[2]==1&&v[3]==1&&v[EXP_AUTO_PROFILE]==1);
 assert(experimental_set(EXP_IQ_LANE,1));assert(experimental_reboot_needed());
 lane=1;assert(!experimental_reboot_needed()&&experimental_transfer_active());
 assert(events&&records&&dc_resets&&s_control_epoch);
 return 0;
}
'''

class Experimental(unittest.TestCase):
    def test_production_state(self):
        source = (ROOT / 'main/experimental.c').read_text()
        source = '\n'.join(line for line in source.splitlines() if not line.startswith('#include'))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path / 'test.c').write_text(BOUNDARY + source + HARNESS)
            subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-I', str(ROOT / 'main'),
                            str(path / 'test.c'), '-o', str(path / 'test')], check=True)
            subprocess.run([str(path / 'test')], check=True, stdout=subprocess.DEVNULL)

if __name__ == '__main__':
    unittest.main()

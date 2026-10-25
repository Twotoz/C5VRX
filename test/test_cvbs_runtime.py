"""C5VRX/Twotoz: production observer, real Q4/P64 replay and native AFC labels."""
from pathlib import Path
import shutil
import subprocess
import tempfile
ROOT=Path(__file__).resolve().parents[1]
RECEIVER=r'''
#pragma once
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#define DG_HOLD 1
static struct { int state; } s_gain={DG_HOLD};
static int s_obs_clip_pm;
static bool s_menu_active;
static unsigned channel,output=10;
static int offset,cn=200;
static bool startup,fault;
static unsigned rf_channel_index(void){return channel;}
static int rf_offset_khz(void){return offset;}
static unsigned transport_output(void){return output;}
static bool calibration_in_startup(void){return startup;}
static bool transport_faulted(void){return fault;}
static int receiver_obs_cn_x10(void){return cn;}
'''
PHY=r'''
#pragma once
#include <assert.h>
static bool leased;
static uint32_t phy_generation(void){return 1;}
static bool phy_try_actuator(uint32_t g){assert(g==1&&!leased);leased=true;return true;}
static void phy_end_actuator(void){assert(leased);leased=false;}
'''
MAIN=r'''
#include <assert.h>
#include <math.h>
#include <string.h>
#include "cvbs.c"
#include "phase8_gain_lut.h"
static cvbs_fit_t applied;
static bool reject;
static unsigned applications;
bool transport_set_cvbs_fit(const cvbs_fit_t *fit){
 assert(leased);if(reject)return false;++applications;applied=*fit;return true;
}
int main(void){
 uint8_t raw[4092];double phase=.27;
 for(unsigned k=0;k<sizeof(raw);++k){
  unsigned slot=k%2542;double f=-1e6;
  if(slot>=800&&slot<988)f-=1.3e6;
  if(slot>=1028&&slot<1108)f+=.65e6*sin(6.283185307179586*3579545.*k/40e6);
  int i=(int)floor(4*cos(phase)),q=(int)floor(4*sin(phase));
  raw[k]=(uint8_t)(((i&15)<<4)|(q&15));phase+=6.283185307179586*f/40e6;
 }
 afc2_result_t r=afc2_measure(raw,sizeof(raw),c5vrx_phase8_gain_lut);
 fprintf(stderr,"native lines=%u pol=%d std=%u snr=%d tip=%ld porch=%ld burst=%u\n",r.lines,r.polarity,r.standard,r.snr_x10,(long)r.sync_khz,(long)r.porch_khz,r.burst_x10);
 assert(r.lines&&r.polarity==-1&&r.standard==1);
 cvbs_refs_t refs;assert(measure(raw,sizeof(raw),&r,&refs));
 assert(refs.porch_uv-refs.sync_uv>60000 && refs.porch_uv-refs.sync_uv<140000);
 /* No isolated good observation, no weak/absent evidence can request a fit. */
 for(unsigned k=0;k<30;++k){cvbs_observe(raw,sizeof(raw),&r,true,1);now+=50000;}
 assert(!applications&&!s.fitted);
 now+=1000000;cvbs_observe(raw,sizeof(raw),&r,true,1);now+=50000;
 assert(!applications && s.windows==1);
 cvbs_observe(raw,sizeof(raw),&r,false,1);now+=50000;
 assert(!s.since&&!applications);
 cn=-1;for(unsigned k=0;k<80;++k){cvbs_observe(raw,sizeof(raw),&r,true,1);now+=50000;}
 assert(!applications);cn=200;
 for(unsigned k=0;k<45;++k){cvbs_observe(raw,sizeof(raw),&r,true,1);now+=50000;}
 assert(applications==1&&s.fitted&&applied.valid);
 cvbs_fit_t frozen=applied;
 cn=-1;r.standard=2;cvbs_observe(raw,sizeof(raw),&r,true,1);now+=50000;
 assert(s.fitted&&applications==1);cn=200;r.standard=1;
 for(unsigned k=0;k<100;++k){cvbs_observe(NULL,0,&r,false,1);now+=50000;}
 assert(applications==1&&!memcmp(&frozen,&applied,sizeof(applied)));
 /* New RF session clears the old VTX map before new qualification, and
  * respects refused transactions instead of publishing a successful fit. */
 ++channel;reject=true;cvbs_observe(raw,sizeof(raw),&r,true,2);now+=50000;
 assert(s.reset_pending&&!s.fitted&&applications==1);
 ++offset;cvbs_observe(raw,sizeof(raw),&r,true,3);now+=50000;
 assert(s.reset_pending&&applications==1);
 reject=false;cvbs_observe(raw,sizeof(raw),&r,true,3);now+=50000;
 assert(applications==2&&!applied.valid&&!s.reset_pending);
 reject=true;
 for(unsigned k=0;k<45;++k){cvbs_observe(raw,sizeof(raw),&r,true,3);now+=50000;}
 assert(!s.fitted&&applications==2);
 reject=false;cvbs_observe(raw,sizeof(raw),&r,true,3);now+=50000;
 assert(s.fitted&&applications==3&&applied.channel==channel&&applied.offset_khz==offset);
 /* A manual setting keeps ownership; AUTO never silently overrides it. */
 output=0;++channel;
 for(unsigned k=0;k<80;++k){cvbs_observe(raw,sizeof(raw),&r,true,4);now+=50000;}
 assert(applications==3);
 puts("PASS: actual native AFC labels/P64 replay, 2s confidence, unknown C/N, loss hold, context reset, refused apply and manual ownership");
}
'''
def main():
    with tempfile.TemporaryDirectory() as directory:
        p=Path(directory);shutil.copyfile(ROOT/'main/cvbs.c',p/'cvbs.c')
        for name,text in [('receiver.h',RECEIVER),('phy.h',PHY),('cn_estimate.h',''),('esp_timer.h','#pragma once\nstatic int64_t now=1000000;\nstatic int64_t esp_timer_get_time(void){return now;}\n'),('test.c',MAIN)]:
            (p/name).write_text(text)
        subprocess.run(['cc','-O2','-std=c11','-Wall','-Wextra','-Werror','-I',str(p),'-I',str(ROOT/'main'),str(p/'test.c'),'-lm','-o',str(p/'test')],check=True)
        subprocess.run([str(p/'test')],check=True)
if __name__=='__main__':main()

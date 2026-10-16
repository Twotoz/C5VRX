#!/usr/bin/env python3
"""C5VRX/Twotoz: execute actual conditional output queue/ISR/cleanup.

Software MMIO fixtures do not establish physical CVBS or monitor lock.
"""
from pathlib import Path
import re
import subprocess
import tempfile
from test_sync_gate_probe_runtime import SDK

ROOT=Path(__file__).resolve().parents[1]
HARNESS=r'''
static uint32_t registers[256],saved[256];
static uint64_t cycles,alarm_tick;
static sync_edge_alarm_fn handler;
static void *handler_arg;
static unsigned clears,sets;
static bool live_ok,native_ok,alarm_enabled,clock_ok,bind_ok;
uint32_t mock_read(uint32_t r){assert(r/4<256);return registers[r/4];}
void mock_write(uint32_t r,uint32_t v){
 assert(r/4<256);
 if(r==GPIO_ENABLE_W1TC_REG){registers[0]&=~v;++clears;}
 else if(r==GPIO_ENABLE_W1TS_REG){registers[0]|=v;++sets;}
 else registers[r/4]=v;
}
int esp_clk_cpu_freq(void){return 240000000;}
uint32_t esp_cpu_get_cycle_count(void){return (uint32_t)++cycles;}
bool sync_edge_clock_read(uint64_t *v){cycles+=24;*v=cycles/12;return clock_ok;}
bool sync_edge_clock_alarm_bind(sync_edge_alarm_fn fn,void *arg){
 if(!bind_ok){return false;}handler=fn;handler_arg=arg;alarm_enabled=false;return true;
}
bool sync_edge_clock_alarm_at(uint64_t tick){alarm_tick=tick;alarm_enabled=true;return true;}
bool sync_edge_clock_alarm_off(void){alarm_enabled=false;return true;}
static bool owner(uint32_t g){assert(g==7);return live_ok;}
static bool present(uint64_t start,uint32_t width,slg_kind_t kind,void *arg){
 (void)start;(void)width;(void)kind;(void)arg;return native_ok;
}
static void reset(void){
 assert(!s_output);memset(registers,0,sizeof(registers));cycles=100000;
 registers[0]=SLO_MASK|1u;live_ok=clock_ok=bind_ok=true;native_ok=false;
 clears=sets=0;handler=NULL;handler_arg=NULL;alarm_enabled=false;
 for(unsigned i=0;i<6;++i){registers[(GPIO_FUNC0_OUT_SEL_CFG_REG+4u*pins[i])/4]=48u+i;
  registers[pads[i]/4]=FUN_PU|FUN_PD|0x100000u;}
 memcpy(saved,registers,sizeof(saved));
}
static void fire(void){
 assert(alarm_enabled&&handler);alarm_enabled=false;
 if(cycles<alarm_tick*12u)cycles=alarm_tick*12u;
 cycles+=100;handler(cycles/12u,handler_arg);
}
int main(void){
 unsigned checks=0;
 for(unsigned standard=1;standard<=2;++standard)for(unsigned native=0;native<=1;++native){
  reset();native_ok=native;
  assert(sync_live_output_begin(NULL,(slg_standard_t)standard,7,owner,present,NULL));
  for(unsigned i=0;i<3000;++i){assert(sync_live_output_poll());fire();
   assert(s_output->status.active&&!s_output->status.fault);
   assert((registers[0]&SLO_MASK)==SLO_MASK);++checks;}
  assert(native?(s_output->status.suppressed>=3000&&!clears):
    (s_output->status.repaired>=3000&&clears==s_output->status.repaired));
  assert((native||s_output->status.batches>0)&&s_output->status.queue_low==0);
  assert(sync_live_output_end()&&!alarm_enabled&&!memcmp(saved,registers,sizeof(saved)));++checks;
 }
 reset();assert(sync_live_output_begin(NULL,SLG_PAL,7,owner,NULL,NULL));
 live_ok=false;fire();assert(!clears&&s_output->status.fault==1);assert(sync_live_output_end());++checks;
 reset();assert(sync_live_output_begin(NULL,SLG_PAL,7,owner,NULL,NULL));
 cycles=(alarm_tick+SLO_LEAD+SLO_LATE+1u)*12u;fire();
 assert(!clears&&s_output->status.active&&!s_output->status.fault&&s_output->status.missed==1&&alarm_enabled);assert(sync_live_output_end());++checks;
 reset();registers[0]&=~(1u<<8);assert(!sync_live_output_begin(NULL,SLG_PAL,7,owner,NULL,NULL)&&!s_output);++checks;
 reset();assert(sync_live_output_begin(NULL,SLG_PAL,7,owner,NULL,NULL));
 s_output->clock.exhausted=true;fire();assert(s_output->status.fault==2&&clears==1);
 assert(sync_live_output_end());++checks;
 printf("PASS: %u actual conditional-output checks; no physical timing/monitor claim\n",checks);
}
'''


def main():
    def stripped(path):
        return re.sub(r'^#(?:include|pragma)[^\n]*\n','',path.read_text(),flags=re.MULTILINE)
    prefix=SDK+'''\n#include <stdlib.h>\n#define MALLOC_CAP_INTERNAL 1\n#define MALLOC_CAP_8BIT 2
#define heap_caps_calloc(n,s,caps) calloc(n,s)\n'''
    api=stripped(ROOT/'main/sync_edge_clock.h')+stripped(ROOT/'main/sync_live_gate.h')
    api+=stripped(ROOT/'main/sync_live_output.h')+stripped(ROOT/'main/sync_live_clock.h')
    source=stripped(ROOT/'main/sync_live_gate.c')+stripped(ROOT/'main/sync_live_output.c')
    with tempfile.TemporaryDirectory() as directory:
        p=Path(directory);(p/'run.c').write_text(prefix+api+source+HARNESS)
        subprocess.run(['cc','-O2','-std=c11','-Wall','-Wextra','-Werror',str(p/'run.c'),'-o',str(p/'run')],check=True)
        subprocess.run([str(p/'run')],check=True)


if __name__=='__main__':main()

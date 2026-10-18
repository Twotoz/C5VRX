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
static unsigned clears,sets,clock_extra_cycles;
static bool live_ok,native_ok,alarm_enabled,clock_ok,bind_ok;
static bool source_ok;static uint32_t source_epoch;
static uint64_t high_start,high_end,clear_tick,set_tick;
static bool epoch_at_high,check_repair_admission;
/* Optional camera on the comparator pin: NTSC/PAL halfline structure with
 * EQ/broad/EQ and H on alternate halves, optionally one missing H. */
static bool cam_gpio;static double cam_g0,cam_ghl;
static unsigned cam_gidx0,cam_ghalves,cam_gsection,cam_gvoff;static uint64_t cam_gap=UINT64_MAX;
static uint64_t clamp_start;static unsigned long max_cam_clamp,full_clamps;
static unsigned cam_width(uint64_t jj){
 unsigned idx=(unsigned)((cam_gidx0+jj+cam_ghalves-cam_gvoff)%cam_ghalves),n=cam_gsection;
 return idx<n?47u:idx<2u*n?541u:idx<3u*n?47u:(jj&1u)?0u:160u;
}
static bool cam_high(uint64_t t){
 if(!cam_gpio||(double)t<cam_g0)return false;
 uint64_t jj=(uint64_t)(((double)t-cam_g0)/cam_ghl);
 if(jj==cam_gap)return false;
 return (double)t-(cam_g0+jj*cam_ghl)<(double)cam_width(jj);
}
uint32_t mock_read(uint32_t r){assert(r/4<256);
 if(r==GPIO_IN_REG)return (cycles/12>=high_start && cycles/12<high_end) || cam_high(cycles/12)?4u:0;
 return registers[r/4];}
void mock_write(uint32_t r,uint32_t v){
 assert(r/4<256);
 if(r==GPIO_ENABLE_W1TC_REG){
  if(check_repair_admission)assert(cycles/12<=s_output->tip.start+SLO_FLAG_LATE+2u);
  registers[0]&=~v;++clears;clear_tick=cycles/12;clamp_start=cycles/12;
 }
 else if(r==GPIO_ENABLE_W1TS_REG){registers[0]|=v;++sets;set_tick=cycles/12;
  unsigned long d=(unsigned long)(cycles/12-clamp_start);
  if(cam_gpio&&d>max_cam_clamp)max_cam_clamp=d;
  if(d>=80u)++full_clamps;}
 else registers[r/4]=v;
}
int esp_clk_cpu_freq(void){return 240000000;}
uint32_t esp_cpu_get_cycle_count(void){return (uint32_t)++cycles;}
bool sync_edge_clock_read(uint64_t *v){cycles+=24u+clock_extra_cycles;*v=cycles/12;return clock_ok;}
bool sync_edge_clock_alarm_bind(sync_edge_alarm_fn fn,void *arg){
 if(!bind_ok){return false;}handler=fn;handler_arg=arg;alarm_enabled=false;return true;
}
bool sync_edge_clock_alarm_at(uint64_t tick){alarm_tick=tick;alarm_enabled=true;return true;}
bool sync_edge_clock_alarm_off(void){alarm_enabled=false;return true;}
static bool owner(uint32_t g){assert(g==7);return live_ok;}
static bool present(uint64_t start,uint32_t width,slg_kind_t kind,void *arg){
 (void)start;(void)width;(void)kind;(void)arg;return native_ok;
}
static bool source(void *arg,uint32_t *epoch){
 (void)arg;*epoch=source_epoch+(epoch_at_high && cycles/12>=high_start?1u:0u);return source_ok;
}
static void reset(void){
 assert(!s_output);memset(registers,0,sizeof(registers));cycles=100000;
 registers[0]=SLO_MASK|1u;live_ok=clock_ok=bind_ok=true;native_ok=false;
 clears=sets=clock_extra_cycles=0;sync_live_output_phase(0);source_ok=true;source_epoch=1;epoch_at_high=false;
 cam_gpio=false;cam_gap=UINT64_MAX;max_cam_clamp=full_clamps=0;clamp_start=0;
 high_start=high_end=UINT64_MAX;clear_tick=set_tick=0;check_repair_admission=false;handler=NULL;handler_arg=NULL;alarm_enabled=false;
 for(unsigned i=0;i<6;++i){registers[(GPIO_FUNC0_OUT_SEL_CFG_REG+4u*pins[i])/4]=48u+i;
  registers[pads[i]/4]=FUN_PU|FUN_PD|0x100000u;}
 memcpy(saved,registers,sizeof(saved));
}
/* Independent camera authority plus fresh H/V evidence on the grid. */
static void grant(void){
 s_output->native_authority = true;
 s_output->h_trust_tick=s_output->v_trust_tick=cycles/12;
 s_output->h_trusted=s_output->v_trusted=true;
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
  /* A nominal clock without camera authority never clamps OE. */
  for(unsigned i=0;i<200;++i){fire();assert(!clears&&!s_output->status.repaired);}
  grant(); /* repair path after independent authority */
  for(unsigned i=0;i<3000;++i){assert(sync_live_output_poll());fire();
   assert(s_output->status.active&&!s_output->status.fault);
   assert((registers[0]&SLO_MASK)==SLO_MASK);++checks;}
  assert(native?(s_output->status.suppressed>=3000&&!clears):
    (s_output->status.repaired>=3000&&clears==s_output->status.repaired));
  assert((native||s_output->status.batches>0)&&s_output->status.queue_low==0);
  assert(s_output->status.max_callback_ticks<=3u); /* exclude later tips in V batches */
  assert(sync_live_output_end()&&!alarm_enabled&&!memcmp(saved,registers,sizeof(saved)));++checks;
 }
 reset();assert(sync_live_output_begin(NULL,SLG_PAL,7,owner,NULL,NULL));
 live_ok=false;fire();assert(!clears&&s_output->status.fault==1);assert(sync_live_output_end());++checks;
 reset();assert(sync_live_output_begin(NULL,SLG_PAL,7,owner,NULL,NULL));
 cycles=(alarm_tick+SLO_LEAD+SLO_LATE+1u)*12u;fire();
 assert(!clears&&s_output->status.active&&!s_output->status.fault&&s_output->status.missed==1&&alarm_enabled);assert(sync_live_output_end());++checks;
 reset();registers[0]&=~(1u<<8);assert(!sync_live_output_begin(NULL,SLG_PAL,7,owner,NULL,NULL)&&!s_output);++checks;
 reset();assert(sync_live_output_begin(NULL,SLG_PAL,7,owner,NULL,NULL));
 grant();s_output->clock.exhausted=true;fire();assert(s_output->status.fault==2&&clears==1);
 assert(sync_live_output_end());++checks;
 /* Comparator pin contract: no ordinary pin23 mux may be admitted. */
 reset();assert(!sync_live_output_flag_begin(NULL,SLG_NTSC,7,owner,source,NULL)&&!s_output);++checks;
 reset();registers[(GPIO_FUNC0_OUT_SEL_CFG_REG+4u*23)/4]=256;
 registers[GPIO_OUT_REG/4]=1u<<23;
 assert(!sync_live_output_flag_begin(NULL,SLG_NTSC,7,owner,source,NULL)&&!s_output);++checks;
 for(unsigned mode=0;mode<6;++mode){
  reset();registers[(GPIO_FUNC0_OUT_SEL_CFG_REG+4u*23)/4]=256;
  memcpy(saved,registers,sizeof(saved));
  assert(sync_live_output_flag_begin(NULL,SLG_NTSC,7,owner,source,NULL));
  /* This fixture tests the fast witness after independent camera authority. */
  grant();s_output->native_v_fresh=true;
  assert(registers[pads[0]/4]==saved[pads[0]/4]);
  uint64_t tip=s_output->tip.start,end=tip+s_output->tip.width;
  /* 0 absent;1 genuine early edge;2 late edge;3 stale HIGH;
   * 4 epoch changes on HIGH;5 markers/source disabled. */
  if(mode==1){high_start=tip-10;high_end=end;}
  if(mode==2){high_start=tip+10;high_end=end;}
  if(mode==3){high_start=0;high_end=UINT64_MAX;}
  if(mode==4){high_start=tip-10;high_end=end;epoch_at_high=true;}
  if(mode==5){high_start=tip-10;high_end=end;source_ok=false;}
  fire();
  assert(!s_output->status.fault);
  if(mode==1)assert(!clears&&s_output->status.suppressed);
  else {
   assert(clears&&s_output->status.repaired);
   assert(set_tick<=end+3u); /* timer sample loop <=150ns in this fixture */
   if(mode==2)assert(set_tick<tip+30u);
   else assert(set_tick>=end);
  }
  assert(sync_live_output_end()&&!memcmp(saved,registers,sizeof(saved)));++checks;
 }
 reset();registers[(GPIO_FUNC0_OUT_SEL_CFG_REG+4u*23)/4]=256;
 assert(sync_live_output_flag_begin(NULL,SLG_NTSC,7,owner,source,NULL));
 high_start=s_output->tip.start-10;high_end=UINT64_MAX;fire();
 assert(!s_output->status.repaired&&!s_output->status.suppressed&&!clears); /* no authority: no clamp */
 grant();s_output->native_v_fresh=true;
 high_start=0;fire();
 assert(s_output->status.repaired&&!s_output->status.suppressed); /* unqualified noise cannot cancel */
 assert(sync_live_output_end());++checks;
 reset();registers[(GPIO_FUNC0_OUT_SEL_CFG_REG+4u*23)/4]=256;
 assert(sync_live_output_flag_begin(NULL,SLG_NTSC,7,owner,source,NULL));
 cycles=(s_output->tip.start+SLO_FLAG_LATE+1u)*12u;
 fire();assert(!clears&&s_output->status.missed&&s_output->status.active);
 assert(sync_live_output_end());++checks;
 /* Delivery latency and callback work are separate clock quantities; phase
  * accounting must survive an overdue first tip without treating later tips
  * in the same batch as fresh interrupt deliveries. */
 reset();registers[(GPIO_FUNC0_OUT_SEL_CFG_REG+4u*23)/4]=256;
 assert(sync_live_output_flag_begin(NULL,SLG_NTSC,7,owner,source,NULL));
 uint64_t delayed_tip=s_output->tip.start;
 clock_extra_cycles=120u;sync_live_output_phase(4u);
 cycles=(delayed_tip+30u)*12u;fire();
 assert(s_output->status.max_irq_delay_ticks==SLO_LEAD+38u);
 assert(s_output->status.max_callback_ticks==12u);
 assert(s_output->status.miss_phase==4u&&s_output->status.misses_by_phase[4u]==1u);
 for(unsigned phase=0;phase<6u;++phase)if(phase!=4u)assert(!s_output->status.misses_by_phase[phase]);
 assert(sync_live_output_end());++checks;
 /* A long masked/cache interval skips stale backlog in bounded chunks,
  * without permanently stopping sync or changing the absolute halfline grid. */
 reset();registers[(GPIO_FUNC0_OUT_SEL_CFG_REG+4u*23)/4]=256;
 assert(sync_live_output_flag_begin(NULL,SLG_NTSC,7,owner,source,NULL));
 uint64_t initial_origin=s_output->tip.start-(s_output->tip.half*5720u+4u)/9u;
 grant();
 cycles=(s_output->tip.start+100000u)*12u; /*5ms unavailable CPU*/
 check_repair_admission=true;
 unsigned callbacks=0;
 do {
  uint32_t consumed=s_output->status.missed+s_output->status.repaired+s_output->status.suppressed;
  fire();++callbacks;
  assert(s_output->status.active&&!s_output->status.fault&&alarm_enabled);
  assert(s_output->status.missed+s_output->status.repaired+s_output->status.suppressed-consumed<=8u);
  assert((registers[0]&SLO_MASK)==SLO_MASK&&clears==sets);
  assert(s_output->tip.start==initial_origin+(s_output->tip.half*5720u+4u)/9u);
  assert(callbacks<40u);
 }while(s_output->tip.start<=cycles/12+SLO_LEAD+60u);
 assert(callbacks>1u&&s_output->status.budget_yields&&s_output->status.missed>50u);
 for(unsigned i=0;i<30u;++i)fire();
 assert(!s_output->status.fault&&s_output->status.repaired&&clears==sets);
 assert(sync_live_output_end());++checks;
 /* Model publications leave the running timer/pulse intact until ISR boundary. */
 reset();registers[(GPIO_FUNC0_OUT_SEL_CFG_REG+4u*23)/4]=256;
 assert(sync_live_output_flag_begin(NULL,SLG_NTSC,7,owner,source,NULL));
 for(unsigned i=0;i<50;++i)fire();
 uint64_t old_tip=s_output->tip.start;
 uint16_t index=s_output->tip.index;
 assert(index>20u);
 uint64_t origin=old_tip-((uint64_t)index*40676u+32u)/64u;
 slg_model_t model={.field=origin+(6u*40676u+32u)/64u,
   .last_h=origin+((uint64_t)(index-2u)*40676u+32u)/64u,
   .observed_at=cycles/12,.line_ticks_x32=40676u,.receive_epoch=1,
   .uncertainty_ticks=64,.standard=SLG_NTSC,.qualified=true};
 unsigned allocations=heap_allocations,frees=heap_frees;
 for(unsigned repeat=0;repeat<100u;++repeat)assert(sync_live_output_update(&model));
 assert(heap_allocations==allocations&&heap_frees==frees);
 assert((s_output->pending_mode==SLO_PUB_SLEW) && s_output->tip.start==old_tip && alarm_enabled);
 unsigned publication=s_output->publication;
 fire();assert(s_output->applied==publication && s_output->standard==SLG_NTSC);
 assert(!s_output->status.fault);++checks;
 model.last_h+=(40676u+32u)/64u;model.observed_at=cycles/12;
 assert(sync_live_output_update(&model)&&!(s_output->pending_mode==SLO_PUB_SLEW));++checks;
 model.qualified=false;assert(!sync_live_output_update(&model));++checks;
 model.qualified=true;model.line_ticks_x32=0u;
 slc_t running=s_output->clock;slo_tip_t running_tip=s_output->tip;
 publication=s_output->publication;
 assert(!sync_live_output_update(&model));
 assert(!memcmp(&running,&s_output->clock,sizeof(running))&&
        !memcmp(&running_tip,&s_output->tip,sizeof(running_tip))&&s_output->publication==publication);
 assert(heap_allocations==allocations&&heap_frees==frees);++checks;
 model=(slg_model_t){.field=cycles/12-30000u,.last_h=cycles/12-4400u,
   .observed_at=cycles/12,.line_ticks_x32=40960u,.receive_epoch=2,
   .uncertainty_ticks=64,.standard=SLG_PAL,.qualified=true};
 assert(sync_live_output_update(&model)&&!(s_output->pending_mode==SLO_PUB_SLEW));
 fire();assert(s_output->standard==SLG_PAL&&!s_output->status.fault);++checks;
 for(unsigned i=0;i<100;++i)fire();
 assert(sync_live_output_end());
 /* A delayed full-reseed publication is consumed without granting native
  * authority to the still-running nominal clock. This is the measured board
  * failure regime: ISR delay longer than the candidate's future lead. */
 reset();registers[(GPIO_FUNC0_OUT_SEL_CFG_REG+4u*23)/4]=256;
 assert(sync_live_output_flag_begin(NULL,SLG_NTSC,7,owner,source,NULL));
 uint64_t nominal_origin=s_output->tip.start-(s_output->tip.half*5720u+4u)/9u;
 for(unsigned i=0;i<50;++i)fire();
 assert(!s_output->native_authority);
 model=(slg_model_t){.field=cycles/12-30000u,.last_h=cycles/12-4400u,
   .observed_at=cycles/12,.line_ticks_x32=40960u,.receive_epoch=2,
   .uncertainty_ticks=64,.standard=SLG_PAL,.qualified=true};
 assert(sync_live_output_update(&model)&&!(s_output->pending_mode==SLO_PUB_SLEW));
 uint64_t expired_candidate=s_output->pending_tip.start;
 publication=s_output->publication;
 cycles=(expired_candidate+1000u)*12u;
 fire();
 assert(s_output->applied==publication&&!s_output->native_authority);
 assert(s_output->standard==SLG_NTSC&&!s_output->status.fault);
 assert(s_output->tip.start==nominal_origin+(s_output->tip.half*5720u+4u)/9u);++checks;
 /* Once a fresh full reseed is actually admitted, old source/edge history
  * must be discarded before the new phase gets fast native suppression. */
 model.field=cycles/12-30000u;model.last_h=cycles/12-4400u;model.observed_at=cycles/12;
 assert(sync_live_output_update(&model)&&!(s_output->pending_mode==SLO_PUB_SLEW));
 s_output->source_seen=s_output->low_seen=s_output->rise_valid=true;
 s_output->last_high=true;s_output->rise=s_output->tip.start;
 fire();
 assert(s_output->native_authority&&s_output->standard==SLG_PAL);
 assert(!s_output->source_seen&&!s_output->low_seen&&!s_output->rise_valid);
 assert(!s_output->status.fault);assert(sync_live_output_end());++checks;
 /* Camera-phase guard (board: slow horizontal roll and the top of the
  * picture at the bottom after VTX off/static). A present camera off the
  * held grid must get the OE back and re-anchor the grid; noise must not. */
 for(unsigned scenario=0;scenario<5u;++scenario){
  reset();registers[(GPIO_FUNC0_OUT_SEL_CFG_REG+4u*23)/4]=256;
  assert(sync_live_output_flag_begin(NULL,SLG_NTSC,7,owner,source,NULL));
  grant();s_output->native_v_fresh=true;
  for(unsigned i=0;i<20;++i)fire();
  double halfline=s_output->clock.whole+(double)s_output->clock.increment/s_output->clock.denominator;
  slo_tip_t t=s_output->tip;
  /* Absolute position of halfline h on the running grid. */
  #define GRID(h) ((double)t.start+((double)(h)-(double)t.half)*halfline)
  uint64_t h0=t.half+((t.half&1u)!=s_output->clock.parity?1u:0u)+40u; /* future H line */
  uint64_t old_start=t.start;uint64_t old_half=t.half;
  if(scenario==0){ /* camera 300 ticks late: yield, then one H re-anchor */
   for(unsigned k=0;k<SPG_LOCK;++k)sync_live_output_native_pulse((uint64_t)(GRID(h0+2u*k)+300.5),160u,true);
   assert(s_output->yield&&s_output->status.guard_h_jumps==1u&&s_output->pending_mode==SLO_PUB_JUMP_H);
   int32_t jump=s_output->pending_shift;assert(jump>=295&&jump<=305);
   unsigned before=s_output->status.yielded;
   fire();assert(s_output->applied==s_output->publication);
   assert(s_output->tip.start>=(uint64_t)(old_start+(s_output->tip.half-old_half)*halfline)+jump-2u);
   for(unsigned i=0;i<10;++i)fire();
   assert(s_output->status.yielded>before); /* camera owns sync until confirmed */
   uint64_t guard=s_output->guard_after;
   t=s_output->tip;h0=t.half+((t.half&1u)!=s_output->clock.parity?1u:0u);
   while(GRID(h0)<(double)guard)h0+=2u;
   for(unsigned k=0;k<SPG_LOCK;++k)sync_live_output_native_pulse((uint64_t)(GRID(h0+2u*k)+0.5),160u,true);
   assert(!s_output->yield&&s_output->status.guard_h_jumps==1u);
   unsigned clamps=clears;fire();fire();assert(clears>clamps); /* holdover repairs resume */
  }
  if(scenario==1){ /* incoherent noise at random phases: never yields or jumps */
   uint32_t lcg=12345u;
   for(unsigned k=0;k<4000u;++k){lcg=lcg*1103515245u+12345u;
    sync_live_output_native_pulse((uint64_t)(GRID(h0)+k*97.0+(lcg>>20)%1271u),80u+(lcg>>8)%150u,true);
    assert(!s_output->yield);}
   assert(!s_output->status.guard_h_jumps&&!s_output->status.guard_v_jumps);
  }
  if(scenario==2){ /* H in phase, V 7 halflines late on two fields: V re-anchor */
   for(unsigned k=0;k<SPG_LOCK;++k)sync_live_output_native_pulse((uint64_t)(GRID(h0+2u*k)+0.5),160u,true);
   uint64_t f0=h0+600u-((h0+600u)%525u)+6u+7u; /* camera first broad at index section+7 */
   sync_live_output_native_pulse((uint64_t)(GRID(f0)+0.5),541u,true);
   sync_live_output_native_pulse((uint64_t)(GRID(f0+1u)+0.5),541u,true); /* same train */
   assert(!s_output->status.guard_v_jumps);
   sync_live_output_native_pulse((uint64_t)(GRID(f0+525u)+0.5),541u,true);
   assert(s_output->status.guard_v_jumps==1u&&s_output->pending_mode==SLO_PUB_JUMP_V&&s_output->pending_shift==7);
   assert(s_output->yield);
   uint16_t index=s_output->clock.index;fire();
   assert(s_output->clock.index==(uint16_t)((index+525u-7u+1u)%525u)||s_output->clock.index==(uint16_t)((index+525u-7u+2u)%525u));
  }
  if(scenario==3){ /* small coherent drift: one fine trim, no yield */
   for(unsigned k=0;k<SPG_TRIM_N;++k)sync_live_output_native_pulse((uint64_t)(GRID(h0+2u*k)+20.5),160u,true);
   assert(!s_output->yield&&s_output->status.guard_trims==1u&&s_output->pending_shift>=19&&s_output->pending_shift<=21);
  }
  if(scenario==4){ /* camera disappears while yielding: holdover resumes after 100 ms */
   for(unsigned k=0;k<SPG_LOCK;++k)sync_live_output_native_pulse((uint64_t)(GRID(h0+2u*k)+500.5),160u,true);
   assert(s_output->yield);
   sync_live_output_guard_tick();assert(s_output->yield);
   cycles+=(uint64_t)(SPG_HOLD_TICKS+(uint64_t)(GRID(h0+2u*SPG_LOCK)-cycles/12.0)+1000u)*12u;
   sync_live_output_guard_tick();assert(!s_output->yield&&!s_output->status.guard_yield);
  }
  assert(!s_output->status.fault&&(registers[0]&SLO_MASK)==SLO_MASK);
  assert(sync_live_output_end());++checks;
  #undef GRID
 }
 /* Guard acquisition: no probe model (the strict EQ/broad/EQ grammar often
  * failed on the board at range), a camera 300 ticks and 7 halflines off
  * the nominal grid. Coherent H plus field-spaced V must grant authority on
  * the camera's grid; a PAL camera on the NTSC grid and pure noise never.
  * Variant 3: camera H only for 100 ms, then only V-like broad trains: old H
  * confidence must not combine with new V evidence. */
 for(unsigned variant=0;variant<4u;++variant){
  reset();registers[(GPIO_FUNC0_OUT_SEL_CFG_REG+4u*23)/4]=256;
  assert(sync_live_output_flag_begin(NULL,SLG_NTSC,7,owner,source,NULL));
  assert(!s_output->native_authority);
  for(unsigned i=0;i<20;++i)fire();
  slo_tip_t t=s_output->tip;slc_t c=s_output->clock;
  double hl=c.whole+(double)c.increment/c.denominator;
  unsigned halves=variant==1u?625u:525u,section=variant==1u?5u:6u;
  double chl=variant==1u?640.0:hl;
  /* Camera halfline j: grid index (t.index + j + 7) at +300 ticks. */
  double cam0=(double)t.start+300.0+40.0*hl;
  uint64_t j=0,start=cycles/12;uint32_t lcg=5u;unsigned clamps=0;
  if(variant==0u){ /* the same camera drives the comparator pin */
   cam_gpio=true;cam_g0=cam0;cam_ghl=chl;cam_gidx0=t.index+40u;cam_ghalves=halves;
   cam_gsection=section;cam_gvoff=7u;}
  while(cycles/12<start+6000000u){ /* 300 ms */
   fire();
   while(cam0+j*chl<=(double)(cycles/12)){
    double at=cam0+j*chl;
    unsigned voff=variant==3u?0u:7u; /* 3: V already on the grid, no V jump */
    unsigned idx=(unsigned)((t.index+40u+j+halves-voff)%halves);++j;
    if(variant==2u){lcg=lcg*1103515245u+12345u;
     sync_live_output_native_pulse((uint64_t)(at+(lcg>>8)%640u),(lcg>>4)%2u?160u:541u,true);continue;}
    unsigned w=idx<section?47u:idx<2u*section?541u:idx<3u*section?47u:((j-1u)&1u)?0u:160u;
    if(variant==3u && (at>(double)start+2000000.0)!=(w==541u))w=0u; /* H only, then V only */
    if(w)sync_live_output_native_pulse((uint64_t)(at+0.5),w,true);
   }
   if(s_output->native_authority&&!clamps)clamps=clears;
  }
  fprintf(stderr,"guard acquisition variant=%u acquired=%lu h_jumps=%lu v_jumps=%lu\n",variant,
          (unsigned long)s_output->status.guard_acquired,(unsigned long)s_output->status.guard_h_jumps,
          (unsigned long)s_output->status.guard_v_jumps);
  if(variant==0u){
   assert(s_output->native_authority&&s_output->status.guard_acquired==1u);
   assert(s_output->status.guard_v_jumps==1u&&s_output->status.guard_h_jumps==1u);
   /* A present camera owns sync on both H and V: its pulses suppress our
    * tips, at most a residual-phase sliver overlaps (trims keep it < 8
    * ticks), never a second full-width sync. */
   sync_live_output_guard_tick();
   assert(!s_output->yield&&s_output->status.h_trusted&&s_output->status.v_trusted);
   assert(s_output->status.suppressed>1000u&&max_cam_clamp<=24u&&!full_clamps);
   assert(s_output->status.repaired==0u); /* slivers are the camera's pulses */
   fprintf(stderr,"camera owns sync: suppressed=%lu repaired=%lu max overlap=%lu ticks\n",
           (unsigned long)s_output->status.suppressed,(unsigned long)s_output->status.repaired,max_cam_clamp);
   /* One missing camera H is a real gap: exactly that tip is filled. */
   uint64_t now_jj=(uint64_t)(((double)(cycles/12)-cam0)/chl);
   cam_gap=(now_jj+40u)|0u;if(cam_width(cam_gap)!=160u)cam_gap+=cam_width(cam_gap+1u)==160u?1u:2u;
   assert(cam_width(cam_gap)==160u);
   unsigned long before=full_clamps;
   while((double)(cycles/12)<cam0+(double)(cam_gap+6u)*chl)fire();
   assert(full_clamps==before+1u);
   /* Camera gone: holdover continues at once on the trusted H/V grid
    * (no 20 ms gap), V tips included. */
   cam_gpio=false;
   uint64_t gone=cycles/12;unsigned long h_before=full_clamps;
   while(cycles/12<gone+400000u)fire(); /* 20 ms */
   sync_live_output_guard_tick();
   assert(full_clamps>h_before+200u&&s_output->status.v_trusted);
  } else assert(!s_output->native_authority&&!s_output->status.guard_acquired&&!clears);
  assert(!s_output->status.fault&&sync_live_output_end());++checks;
 }
 /* Per-type repair authority (review 2026-10-18): fresh camera H on the grid
  * does not authorise V. Without V trust no EQ/broad tip is clamped (only H
  * gaps are filled); without any trust nothing is clamped. */
 for(unsigned trust=0;trust<2u;++trust){
  reset();registers[(GPIO_FUNC0_OUT_SEL_CFG_REG+4u*23)/4]=256;
  assert(sync_live_output_flag_begin(NULL,SLG_NTSC,7,owner,source,NULL));
  s_output->native_authority=true;
  s_output->h_trust_tick=trust?cycles/12:0u;s_output->v_trust_tick=0u;
  s_output->h_trusted=trust;s_output->v_trusted=false;
  unsigned long longest=0;uint64_t start=cycles/12;
  while(cycles/12<start+400000u){ /* one NTSC field+ */
   unsigned before=clears;fire();
   if(clears!=before){unsigned long d=(unsigned long)(set_tick-clear_tick);if(d>longest)longest=d;}
  }
  if(trust)assert(s_output->status.repaired>200u&&longest<=100u&&s_output->status.camera_yields>=12u);
  else assert(!clears&&!s_output->status.repaired&&s_output->status.camera_yields>200u);
  assert(!s_output->status.fault&&sync_live_output_end());++checks;
 }
 /* Holdover PLL: a camera 30 ppm slower than the nominal NTSC grid. The
  * model period is quantised to 1/64 tick per halfline (up to ~24 us/s of
  * holdover drift); after 2.6 s of aligned camera H the PLL period must
  * track it, so 1 s of pure holdover drifts far less than the 600 ticks an
  * uncorrected period loses. Phase alignment alone (guard trims) cannot. */
 for(unsigned noisy=0;noisy<2u;++noisy){
  /* noisy: +-3 tick edge jitter, 30 % of camera H missing, and one random
   * H-width noise pulse per four lines at an arbitrary phase. */
  uint32_t lcg=99u;
  reset();registers[(GPIO_FUNC0_OUT_SEL_CFG_REG+4u*23)/4]=256;
  assert(sync_live_output_flag_begin(NULL,SLG_NTSC,7,owner,source,NULL));
  grant();s_output->native_v_fresh=true;
  for(unsigned i=0;i<20;++i)fire();
  #define CENTRED(x,p) ((x)-(double)(long long)((x)/(p)+((x)>=0?0.5:-0.5))*(p))
  slo_tip_t t=s_output->tip;slc_t c=s_output->clock;
  double hl=c.whole+(double)c.increment/c.denominator;
  double cam0=(double)t.start+((t.half&1u)!=c.parity?hl:0.0)+40.0*hl;
  double cam_line=2.0*hl*(1.0+30e-6);
  uint64_t k=0,start=cycles/12;
  while(cycles/12<start+52000000u){
   fire();
   while(cam0+k*cam_line<=(double)(cycles/12)){
    double at=cam0+k*cam_line;++k;
    if(noisy){
     lcg=lcg*1103515245u+12345u;
     if((lcg>>16)%4u==0u)sync_live_output_native_pulse((uint64_t)(at+(lcg>>4)%1200u),90u+(lcg>>9)%140u,true);
     if((lcg>>12)%10u<3u)continue;
     at+=(double)((int)((lcg>>20)%7u)-3);
    }
    sync_live_output_native_pulse((uint64_t)(at+0.5),160u,true);}
  }
  sync_live_output_guard_tick();
  assert(s_output->status.pll_updates>=1u&&!s_output->status.pll_rejected);
  assert(s_output->status.pll_ppb>27000&&s_output->status.pll_ppb<33000);
  uint64_t hold=cycles/12;
  while(cycles/12<hold+20000000u)fire(); /* 1 s holdover: no camera pulses */
  t=s_output->tip;c=s_output->clock;hl=c.whole+(double)c.increment/c.denominator;
  double grid=(double)t.start+((t.half&1u)!=c.parity?hl:0.0);
  double cam=cam0+(double)(long long)((grid-cam0)/cam_line+0.5)*cam_line;
  double err=CENTRED(cam-grid,2.0*hl);
  fprintf(stderr,"PLL holdover (%s): ppb=%ld updates=%lu drift after 1 s=%.1f ticks (uncorrected ~600)\n",noisy?"noisy":"clean",
          (long)s_output->status.pll_ppb,(unsigned long)s_output->status.pll_updates,err);
  assert(err>-40.0&&err<40.0);
  assert(!s_output->status.fault&&sync_live_output_end());++checks;
  #undef CENTRED
 }
 printf("PASS: %u actual conditional-output checks; no physical timing/monitor claim\n",checks);
}
'''


def main():
    def stripped(path):
        return re.sub(r'^#(?:include|pragma)[^\n]*\n','',path.read_text(),flags=re.MULTILINE)
    prefix=SDK+'''\n#include <stdlib.h>\n#define DRAM_ATTR\n#define GPIO_IN_REG 12u
#define GPIO_OUT_REG 16u
#define MALLOC_CAP_INTERNAL 1\n#define MALLOC_CAP_8BIT 2
static unsigned heap_allocations,heap_frees;
static void *counted_calloc(size_t n,size_t size){++heap_allocations;return calloc(n,size);}
static void counted_free(void *p){++heap_frees;free(p);}
#define calloc(n,s) counted_calloc(n,s)
#define free(p) counted_free(p)
#define heap_caps_calloc(n,s,caps) counted_calloc(n,s)\n'''
    api=stripped(ROOT/'main/sync_edge_clock.h')+stripped(ROOT/'main/sync_live_gate.h')
    api+=stripped(ROOT/'main/sync_live_output.h')+stripped(ROOT/'main/sync_live_clock.h')
    api+=stripped(ROOT/'main/sync_phase_guard.h')+stripped(ROOT/'main/sync_clock_pll.h')
    source=stripped(ROOT/'main/sync_live_gate.c')+stripped(ROOT/'main/sync_live_output.c')
    with tempfile.TemporaryDirectory() as directory:
        p=Path(directory);(p/'run.c').write_text(prefix+api+source+HARNESS)
        subprocess.run(['cc','-O2','-std=c11','-Wall','-Wextra','-Werror',str(p/'run.c'),'-o',str(p/'run')],check=True)
        subprocess.run([str(p/'run')],check=True)


if __name__=='__main__':main()

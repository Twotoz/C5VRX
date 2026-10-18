#!/usr/bin/env python3
"""C5VRX by Twotoz/contributors: actual read-only RMT probe on mocked SDK.
Executes production callback/parser/setup/cleanup. No hardware timing claim.
"""
from pathlib import Path
import os
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PREFIX = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define IRAM_ATTR
#define DRAM_ATTR
#define RTC_FAST_ATTR
#define ESP_OK 0
bool sync_output_live_source(void *arg,uint32_t *epoch);
typedef int esp_err_t;
typedef void *rmt_channel_handle_t;
typedef union {
 struct { uint32_t duration0:15, level0:1, duration1:15, level1:1; };
 uint32_t val;
} rmt_symbol_word_t;
typedef struct {rmt_symbol_word_t *received_symbols;size_t num_symbols;struct {bool is_last;} flags;} rmt_rx_done_event_data_t;
typedef struct {bool(*on_recv_done)(rmt_channel_handle_t,const rmt_rx_done_event_data_t*,void*);} rmt_rx_event_callbacks_t;
typedef struct {unsigned gpio_num,clk_src,resolution_hz,mem_block_symbols,intr_priority;} rmt_rx_channel_config_t;
typedef struct {unsigned signal_range_min_ns,signal_range_max_ns;struct{bool en_partial_rx;}flags;}rmt_receive_config_t;
#define RMT_CLK_SRC_PLL_F80M 80
#define IO_MUX_GPIO8_REG 1u
#define GPIO_FUNC8_OUT_SEL_CFG_REG 2u
#define GPIO_ENABLE_REG 3u
#define GPIO_PIN8_REG 4u
#define GPIO_ENABLE_W1TS_REG 5u
#define GPIO_ENABLE_W1TC_REG 6u
#define IO_MUX_GPIO2_REG 7u
#define GPIO_FUNC2_OUT_SEL_CFG_REG 8u
#define GPIO_PIN2_REG 9u
#define GPIO_OUT_REG 10u
#define GPIO_OUT_W1TS_REG 11u
#define GPIO_OUT_W1TC_REG 12u
#define GPIO_FUNC2_OUT_SEL_M 511u
#define GPIO_FUNC2_OUT_INV_SEL_M 512u
#define GPIO_FUNC2_OE_SEL_M 1024u
#define GPIO_FUNC2_OE_INV_SEL_M 2048u
#define GPIO_PIN2_PAD_DRIVER_M 4u
#define GPIO_FUNC8_OUT_SEL_M 511u
#define GPIO_FUNC8_OUT_INV_SEL_M 512u
#define GPIO_FUNC8_OE_SEL_M 1024u
#define GPIO_FUNC8_OE_INV_SEL_M 2048u
#define GPIO_PIN8_PAD_DRIVER_M 4u
#define MCU_SEL_M 28672u
#define MCU_SEL_S 12u
#define FUN_IE 512u
#define FUN_PU 256u
#define FUN_PD 128u
static uint32_t registers[13];
static unsigned writes, api_calls, fail_at, mode, receives, deletes, disables;
static bool live_value, mutate_pin, channel_live, rx_enabled, receiving;
static int64_t now;
static unsigned clock_calls,clock_fail;
static bool clock_prepared,clock_armed;
static bool marker_sent;
static unsigned clock_reads;
static uint64_t clock_epoch_base,clock_epoch_stride;
static unsigned clock_prepares,clock_arms,clock_pauses;
static bool scheduler_held,flag_source_seen;
static void vTaskSuspendAll(void){assert(!scheduler_held);scheduler_held=true;}
static int xTaskResumeAll(void){assert(scheduler_held);scheduler_held=false;return 0;}
static bool irq_masked; /* marker edge brackets: short, never nested, scheduler held */
static void portDISABLE_INTERRUPTS(void){assert(scheduler_held&&!irq_masked);irq_masked=true;}
static void portENABLE_INTERRUPTS(void){assert(irq_masked);irq_masked=false;}
static bool sync_edge_clock_prepare(void){++clock_calls;++clock_prepares;assert((registers[GPIO_ENABLE_REG]&(1u<<2))&&!(registers[GPIO_OUT_REG]&(1u<<2)));clock_prepared=clock_fail!=1u;return clock_prepared;}
static bool sync_edge_clock_arm(void){++clock_calls;++clock_arms;assert(scheduler_held&&clock_prepared&&receiving&&!(registers[GPIO_OUT_REG]&(1u<<2)));if(clock_fail==4u)now+=701;clock_armed=clock_fail!=2u;return clock_armed;}
static bool sync_edge_clock_enable_filter(void){++clock_calls;assert(scheduler_held&&clock_armed&&!(registers[GPIO_OUT_REG]&(1u<<2)));return clock_fail!=5u;}
static bool sync_edge_clock_disable_filter(void){++clock_calls;++clock_pauses;assert(clock_armed&&!(registers[GPIO_OUT_REG]&(1u<<2)));clock_epoch_base+=clock_epoch_stride;clock_reads=0;marker_sent=false;return clock_fail!=6u;}
static bool sync_edge_clock_read(uint64_t *n){++clock_calls;if(!clock_armed)return false;const unsigned samples[]={0,1000,1030,1400,1430,1600,123456};*n=clock_epoch_base+samples[clock_reads<7u?clock_reads:6u];if(mode==4u&&clock_epoch_base&&clock_reads==0u)*n=0u;++clock_reads;return true;}
static bool sync_edge_clock_close(void){++clock_calls;assert(!(registers[GPIO_ENABLE_REG]&(1u<<2)));clock_prepared=clock_armed=false;return clock_fail!=3u;}
static uint32_t sync_edge_clock_filter_ns(void){return 1200u;}
static const char *sync_edge_clock_error(void){return "mock";}
static void esp_rom_delay_us(unsigned n){assert((n==50u||n==16u||n==4u||n==3u)&&(registers[GPIO_ENABLE_REG]&(1u<<2)));now+=n;}
static rmt_rx_event_callbacks_t mock_cb;
static void *mock_context;
#define REG_READ(address) registers[address]
static void reg_write(unsigned address,uint32_t value){
 assert(!sync_output_live_source(NULL,NULL)); /* no marker/handoff write while admitted */
 if(address==GPIO_FUNC2_OUT_SEL_CFG_REG && (value&GPIO_FUNC2_OUT_SEL_M)==48u)flag_source_seen=true;
 assert(address==IO_MUX_GPIO2_REG||address==GPIO_FUNC2_OUT_SEL_CFG_REG||address==GPIO_ENABLE_W1TS_REG||address==GPIO_ENABLE_W1TC_REG||address==GPIO_OUT_W1TS_REG||address==GPIO_OUT_W1TC_REG);
 if(address==GPIO_ENABLE_W1TS_REG||address==GPIO_ENABLE_W1TC_REG||address==GPIO_OUT_W1TS_REG||address==GPIO_OUT_W1TC_REG)assert(value==(1u<<2));
 ++writes;
 if(address==GPIO_ENABLE_W1TS_REG)registers[GPIO_ENABLE_REG]|=value;
 else if(address==GPIO_ENABLE_W1TC_REG)registers[GPIO_ENABLE_REG]&=~value;
 else if(address==GPIO_OUT_W1TS_REG)registers[GPIO_OUT_REG]|=value;
 else if(address==GPIO_OUT_W1TC_REG)registers[GPIO_OUT_REG]&=~value;
 else registers[address]=value;
}
#define REG_WRITE(address,value) reg_write(address,value)
static bool owner_live(uint32_t generation){return live_value&&generation==7u;}
static int64_t esp_timer_get_time(void){return now;}
static int rmt_new_rx_channel(const rmt_rx_channel_config_t *cfg,void **out){
 ++api_calls;assert(cfg->gpio_num==2&&cfg->clk_src==80&&cfg->resolution_hz==20000000&&cfg->mem_block_symbols==96);
 if(fail_at==1)return -1;
 assert(!channel_live);channel_live=true;*out=(void*)1;
 registers[IO_MUX_GPIO2_REG]|=FUN_IE;
 if(mutate_pin)registers[GPIO_FUNC2_OUT_SEL_CFG_REG]^=1u;
 return 0;
}
static int rmt_rx_register_event_callbacks(void *channel,const rmt_rx_event_callbacks_t *cb,void *arg){
 ++api_calls;assert(channel&&channel_live);
 if((cb->on_recv_done&&fail_at==2)||(!cb->on_recv_done&&fail_at==8))return -2;
 mock_cb=*cb;mock_context=arg;return 0;
}
static int rmt_enable(void *channel){++api_calls;assert(channel&&channel_live);if(fail_at==3)return -3;rx_enabled=true;return 0;}
static int rmt_receive(void *channel,void *buffer,size_t size,const rmt_receive_config_t *cfg){
 ++api_calls;assert(channel&&channel_live&&rx_enabled&&buffer&&size==48u*sizeof(rmt_symbol_word_t));
 assert(cfg->signal_range_min_ns==(clock_prepared?1000u:2500u)&&cfg->signal_range_max_ns==(clock_prepared?1000000u:100000u)&&cfg->flags.en_partial_rx);
 ++receives;if(fail_at==4||(fail_at==5&&receives>1))return -4;
 receiving=true;return 0;
}
static int rmt_disable(void *channel){++api_calls;assert(channel&&channel_live);++disables;if(fail_at==6)return -6;rx_enabled=receiving=false;return 0;}
static int rmt_del_channel(void *channel){++api_calls;assert(channel&&channel_live);++deletes;if(fail_at==7||rx_enabled)return -7;channel_live=false;return 0;}
static void vTaskDelay(unsigned ticks){
 assert(ticks==1);now+=1000;
 if(mode==2)live_value=false;
 if(receiving&&mock_cb.on_recv_done){
  rmt_symbol_word_t words[48];
  for(unsigned i=0;i<48;++i)words[i]=(rmt_symbol_word_t){.duration0=1186,.level0=1,.duration1=94,.level1=0};
  if(clock_armed && (mode==1u||mode==3u||mode==4u) && !marker_sent){words[0]=(rmt_symbol_word_t){.duration0=320,.level0=1,.duration1=720,.level1=0};words[1]=(rmt_symbol_word_t){.duration0=400,.level0=1,.duration1=94,.level1=0};marker_sent=true;}
  rmt_rx_done_event_data_t event={.received_symbols=words,.num_symbols=(mode==1u||mode==3u||mode==4u)?48u:0u,.flags={.is_last=mode!=1}};
  if(event.flags.is_last)receiving=false;
  assert(!mock_cb.on_recv_done((void*)1,&event,mock_context));
 }
}
'''

SUFFIX = r'''
static unsigned checks;
static void reset(void){
 memset(registers,0,sizeof(registers));registers[IO_MUX_GPIO8_REG]=1u<<MCU_SEL_S;
 registers[GPIO_FUNC8_OUT_SEL_CFG_REG]=52u|GPIO_FUNC8_OE_SEL_M;
 registers[GPIO_ENABLE_REG]=(1u<<8)|0x80000000u;
 writes=api_calls=fail_at=mode=receives=deletes=disables=0;flag_source_seen=false;
 live_value=true;mutate_pin=channel_live=rx_enabled=receiving=false;now=0;
 memset(&mock_cb,0,sizeof(mock_cb));mock_context=NULL;
 s_busy=false;s_quarantined=NULL;memset(&s_probe,0,sizeof(s_probe));
 s_clock_fault=false;clock_calls=clock_fail=clock_reads=0;clock_prepared=clock_armed=scheduler_held=marker_sent=false;
 if(s_native){s_native->trace_flag=false;s_native->resident=false;}
 clock_epoch_base=0;clock_epoch_stride=100000u;clock_prepares=clock_arms=clock_pauses=0;
}
static void parser_start(void){reset();s_probe.armed=true;s_probe.live=owner_live;s_probe.generation=7;sop_epoch(&s_probe);}
static void event(rmt_symbol_word_t *words,size_t n,bool last){
 rmt_rx_done_event_data_t e={.received_symbols=words,.num_symbols=n,.flags={.is_last=last}};
 assert(!sop_received((void*)1,&e,&s_probe));sop_drain(&s_probe);++checks;
}
static void native_projection(void){
 if(!s_native){s_native=calloc(1,sizeof(*s_native));}
 assert(s_native);
 parser_start();s_probe.marker_required=true;s_probe.discard_before=2000;
 s_native->resident=true;snt_trace_reset(&s_native->trace);
 s_native->native_phase_checked=true;
 int64_t base=INT64_C(1)<<44;
 s_native->native_phase=(sync_phase_bracket_t){.lo=base-10,.hi=base+10,.valid=true};
 sop_low(&s_probe,1999,94);assert(!s_native->native.seen);++checks;
 assert(!s_native->trace.count);++checks;
 sop_low(&s_probe,2000,94);assert(s_native->native.last==(uint64_t)base+2000u);++checks;
 assert(s_native->trace.count==1u && s_native->trace.lows[0].tick==2000u &&
        s_native->trace.lows[0].epoch==(uint16_t)s_probe.epochs);++checks;
 sop_epoch(&s_probe);assert(!s_native->native.seen&&!s_native->native_phase.valid);++checks;
 assert(s_native->trace.count==1u);++checks; /* diagnostics retain the explicit old epoch */
 s_probe.discard_before=0;s_native->native_phase_checked=true;
 sop_low(&s_probe,2000,94);assert(!s_native->native.seen);++checks;
}
static void parser_tests(void){
 parser_start();rmt_symbol_word_t words[48];
 for(unsigned i=0;i<48;++i)words[i]=(rmt_symbol_word_t){.duration0=1186,.level0=1,.duration1=94,.level1=0};
 event(words,48,false);event(words,48,false);
 assert(s_probe.h==95&&s_probe.h_periods==94&&s_probe.pal32==2&&s_probe.ntsc32==0);
 rmt_symbol_word_t end={.duration0=1186,.level0=1,.duration1=0,.level1=0};
 event(&end,1,true);assert(s_probe.h==96&&s_probe.ended&&!s_probe.have_h);
 unsigned periods=s_probe.h_periods;sop_epoch(&s_probe);event(words,1,false);
 assert(s_probe.h==96&&s_probe.h_periods==periods); /* no epoch bridge */
 parser_start();rmt_symbol_word_t a={.duration0=100,.level0=1,.duration1=40,.level1=0};
 rmt_symbol_word_t b={.duration0=54,.level0=0,.duration1=1186,.level1=1};
 event(&a,1,false);assert(s_probe.h==0);event(&b,1,false);
 assert(s_probe.h==1&&s_probe.elapsed==1380); /* split low run, correct order */
 parser_start();a=(rmt_symbol_word_t){.duration0=94,.level0=0,.duration1=100,.level1=1};
 event(&a,1,false);assert(s_probe.h==0); /* truncated initial LOW */
 parser_start();event(&end,1,false);assert(s_probe.bad_data==1&&s_probe.stop);
 parser_start();event(words,49,false);assert(s_probe.bad_data==1&&s_probe.stop);
 parser_start();event(NULL,1,false);assert(s_probe.bad_data==1&&s_probe.stop);
 parser_start();live_value=false;event(words,48,false);
 assert(s_probe.cancelled&&s_probe.stop&&!s_probe.h&&!s_probe.symbols);
 parser_start();s_probe.armed=false;event(words,48,false);assert(!s_probe.callbacks);
 parser_start();s_probe.elapsed=UINT32_MAX-20u;event(words,1,false);
 assert(s_probe.bad_data==1&&s_probe.stop);
 parser_start();s_probe.marker_required=true;words[0].duration0=SOP_MARKER_MAX_TICKS+1u;event(words,1,false);
 assert(s_probe.marker_invalid&&s_probe.stop&&!s_probe.h&&!s_probe.halves);
}
/* Pack independent PAL/NTSC half-line fixtures into arbitrary symbol batches.
 * Segments retain their level even when a long HIGH is split into pieces. */
static rmt_symbol_word_t pack[48];static unsigned packed,halfword;
static uint64_t fixture_time;
static void segment(uint32_t duration,bool level){
 while(duration){unsigned n=duration>30000u?30000u:duration;
  if(!halfword){pack[packed]=(rmt_symbol_word_t){.duration0=n,.level0=level};halfword=1;}
  else{pack[packed].duration1=n;pack[packed].level1=level;halfword=0;
   if(++packed==48){event(pack,48,false);packed=0;}}
  duration-=n;fixture_time+=n;
 }
}
static void fixture(bool ntsc){
 parser_start();packed=halfword=0;fixture_time=0;
 unsigned halves=ntsc?525u:625u,group=ntsc?6u:5u;
 for(unsigned half=0;half<halves*3u;++half){
  unsigned i=half%halves;bool broad=i>=group&&i<2u*group;
  bool normal=i>=3u*group&&!(half&1u);
  if(!broad&&!normal)continue;
  uint64_t start=100u+(ntsc?((uint64_t)half*5720u+4u)/9u:(uint64_t)half*640u);
  uint64_t next=100u+(ntsc?((uint64_t)(half+1u)*5720u+4u)/9u:(uint64_t)(half+1u)*640u);
  assert(start>fixture_time);segment((uint32_t)(start-fixture_time),true);
  segment(broad?(uint32_t)(next-start-94u):94u,false);
 }
 segment(100,true);
 if(halfword){pack[packed].duration1=0;pack[packed].level1=0;++packed;}
 event(pack,packed,true);
 assert(!s_probe.bad_data&&s_probe.h_periods>700);
 assert(ntsc?(s_probe.ntsc32>20&&s_probe.pal32==0&&s_probe.broad6==3&&s_probe.ntsc_fields==2):
             (s_probe.pal32>20&&s_probe.ntsc32==0&&s_probe.broad5==3&&s_probe.pal_fields==2));
 /* Fixture intentionally has no idle boundaries. Real EQ filtering plus
  *100us idle may split VBI into separate epochs; those must never be joined. */
}
static void native_polarity_fixture(bool ntsc,bool inverted){
 parser_start();packed=halfword=0;fixture_time=0;
 assert(s_native);s_probe.marker_required=true;s_probe.discard_before=0;
 s_probe.first_duration=320;s_probe.first_level=true;s_probe.prefix_n=3;
 s_native->native_phase_checked=true;
 s_native->native_phase=(sync_phase_bracket_t){.valid=true,.lo=0,.hi=0};
 unsigned halves=ntsc?525u:625u,group=ntsc?6u:5u;
 for(unsigned half=0;half<halves*3u;++half){
  unsigned i=half%halves;bool broad=i>=group&&i<2u*group;
  bool eq=i<group||(i>=2u*group&&i<3u*group);
  bool normal=i>=3u*group&&!(half&1u);
  if(!eq&&!broad&&!normal)continue;
  uint64_t start=100u+(ntsc?((uint64_t)half*5720u+4u)/9u:(uint64_t)half*640u);
  uint64_t next=100u+(ntsc?((uint64_t)(half+1u)*5720u+4u)/9u:(uint64_t)(half+1u)*640u);
  assert(start>fixture_time);segment((uint32_t)(start-fixture_time),!inverted);
  segment(broad?(uint32_t)(next-start-94u):eq?(ntsc?46u:47u):94u,inverted);
 }
 segment(100,!inverted);
 if(halfword){pack[packed].duration1=0;pack[packed].level1=0;++packed;}
 event(pack,packed,true);
 const sync_native_track_t *chosen=inverted?&s_native->inverted:&s_native->native;
 const sync_native_track_t *other=inverted?&s_native->native:&s_native->inverted;
 assert(!s_probe.bad_data&&chosen->valid&&chosen->standard==(ntsc?1u:2u)&&
        chosen->qualified_fields>=1u&&!other->valid);
 ++checks;
}

static void resident_lifecycle(void){
 reset();mode=1;
 uint32_t saved[13];memcpy(saved,registers,sizeof(saved));
 assert(sync_output_live_begin(7,owner_live));
 assert(sync_output_live_active()&&now<200&&clock_prepares==1&&clock_arms==1);
 assert(!sync_output_live_begin(7,owner_live));
 for(unsigned i=0;i<350u;++i){vTaskDelay(1);assert(sync_output_live_poll());}
 assert(s_probe.callbacks>256u&&!s_probe.rate_limited&&clock_prepares==1&&clock_arms==1);
 char diagnostic[768];
 assert(s_native->trace.count==SNT_TRACE_CAPACITY);
 assert(sync_output_live_diagnostics(diagnostic,sizeof(diagnostic))&&strstr(diagnostic,"SYNC_LIVE_DIAG "));
 assert(!sync_output_live_diagnostics(diagnostic,1u)&&!diagnostic[0]);
 ++s_native->sequence;
 assert(!sync_output_live_diagnostics(diagnostic,sizeof(diagnostic))&&!diagnostic[0]);
 ++s_native->sequence;
 checks+=4;
 /* Inject a qualified model to test publication/lifetime independently of
  * the separate real waveform qualification fixtures. */
 s_native->native=(sync_native_track_t){.valid=true,.standard=2,.last=123400,
     .h=123300,.field=100000,.line_ticks_x32=40960};
 s_native->native_phase=(sync_phase_bracket_t){.valid=true,.lo=10,.hi=20};
 sync_output_live_model_t model;
 assert(sync_output_live_snapshot(&model)&&model.valid&&model.standard==2&&
        model.last_h==123300&&model.line_ticks_x32==40960&&model.now==123456);
 s_native->inverted=s_native->native;s_native->native.valid=false;
 assert(sync_output_live_snapshot(&model)&&model.valid&&model.inverted&&model.standard==2);
 s_native->native=s_native->inverted;
 assert(sync_output_live_snapshot(&model)&&!model.valid); /* ambiguous polarities */
 sync_native_reset(&s_native->inverted);checks+=2;
 sync_output_live_invalidate();
 assert(!s_probe.armed&&sync_output_live_snapshot(&model)&&!model.valid);
 unsigned callbacks=s_probe.callbacks,prior_receives=receives;
 rmt_symbol_word_t w={.duration0=1186,.level0=1,.duration1=94,.level1=0};
 event(&w,1,false);assert(s_probe.callbacks==callbacks);
 assert(sync_output_live_poll()&&receives==prior_receives+1u&&clock_prepares==1&&clock_arms==1);
 assert(sync_output_live_snapshot(&model)&&!model.valid&&model.standard==2&&model.last_h==123300);
 /* A natural RX idle ending must flush the hardware/interrupt boundary just
  * like an explicit mutation. Absolute timer ownership must survive it. */
 unsigned before_disables=disables;prior_receives=receives;
 event(&w,1,true);
 assert(sync_output_live_poll()&&disables==before_disables+1u&&
        receives==prior_receives+1u&&clock_prepares==1&&clock_arms==1);
 assert(!s_native->native.seen&&!s_native->native_phase.valid);
 ++checks;
 /* Force the proactive local-counter rollover boundary without running
  * 180 seconds of mock captures. Timer/link must survive the RX rearm. */
 now=s_session->epoch_started+180000000;prior_receives=receives;
 assert(sync_output_live_poll()&&receives==prior_receives+1u&&clock_prepares==1&&clock_arms==1);
 assert(sync_output_live_end()&&!sync_output_live_active()&&!channel_live);
 assert(!memcmp(saved,registers,sizeof(saved))&&sync_output_live_end());
 reset();mode=1;assert(sync_output_live_begin(7,owner_live));live_value=false;
 assert(!sync_output_live_poll()&&!sync_output_live_active()&&!channel_live);
 assert(sync_output_live_end());
 reset();mode=1;assert(sync_output_live_begin(7,owner_live));fail_at=8;
 assert(!sync_output_live_end()&&s_quarantined&&!s_probe.armed);
 assert(!sync_output_live_begin(7,owner_live)&&!sync_output_live_end());
 checks+=10;
}


static unsigned close_hooks;
static uint32_t close_matrix;
static bool consumer_close(void){
 assert(clock_prepared&&clock_armed);++close_hooks;
 registers[GPIO_FUNC8_OUT_SEL_CFG_REG]=close_matrix;return true;
}
static bool consumer_refuses(void){return false;}
static void dependent_teardown(void){
 reset();mode=1;assert(sync_output_live_begin(7,owner_live));
 close_matrix=s_session->matrix;
 registers[GPIO_FUNC8_OUT_SEL_CFG_REG]^=GPIO_FUNC8_OE_SEL_M;
 sync_output_live_before_close(consumer_close);
 assert(sync_output_live_end()&&close_hooks==1&&!s_clock_fault&&!clock_prepared);++checks;
 reset();mode=1;assert(sync_output_live_begin(7,owner_live));
 sync_output_live_before_close(consumer_refuses);
 assert(!sync_output_live_end()&&s_busy&&s_clock_fault&&clock_prepared);++checks;
 parser_start();s_probe.marker_required=true;
 rmt_symbol_word_t delayed={.duration0=5000,.level0=1,.duration1=720,.level1=0};
 event(&delayed,1,false);assert(!s_probe.marker_invalid&&!s_probe.stop);++checks;
}
static void restore_fault_lifecycle(void){
 reset();mode=1;assert(sync_output_live_begin(7,owner_live));
 /* Simulate pin-state restoration/readback mismatch with successful SDK
  * teardown. A subsequent begin must refuse until reboot. */
 registers[GPIO_PIN2_REG]^=GPIO_PIN2_PAD_DRIVER_M;
 assert(!sync_output_live_end()&&s_clock_fault&&!s_busy&&!channel_live);
 unsigned calls=api_calls;
 assert(!sync_output_live_begin(7,owner_live));
 assert(api_calls==calls&&!sync_output_live_end());
 ++checks;
}

/* Source admission is the output ISR's only permission to sample GPIO2.
 * Exercise real setup/marker/teardown and parser stop/idle conditions. */
static void flag_mutation_authority(void){
 reset();mode=1;assert(sync_output_live_begin_flag(7,owner_live));vTaskDelay(1);
 /* A completed first V group survives same-camera gain before authority;
  * all incomplete runs and H acquisition candidates are independently reset. */
 s_native->inverted.have_group=true;s_native->inverted.group=10000u;
 s_native->inverted.group_n=5u;s_native->flag_inverted.group_shifted=true;
 s_native->inverted.candidates[0].used=true;s_native->flag_inverted.pre_count=3u;
 sync_output_live_invalidate();assert(sync_output_live_poll());
 assert(!s_native->inverted.valid&&s_native->inverted.have_group&&
        s_native->inverted.group==10000u&&s_native->inverted.group_n==5u&&
        s_native->flag_inverted.group_shifted);
 assert(!s_native->inverted.candidates[0].used&&!s_native->flag_inverted.pre_count);
 vTaskDelay(1);
 s_native->inverted=(sync_native_track_t){.valid=true,.have_selected=true,
   .standard=2u,.field=10000u,.h=123300u,.last=123400u,.line_ticks_x32=40960u,
   .selected_line_ticks_x32=40960u,.qualified_fields=3u,.h_fit=123300u,.h_standard=2u};
 s_native->native_phase=(sync_phase_bracket_t){.valid=true,.lo=10,.hi=20};
 s_native->inverted.candidates[0].used=true;
 s_native->flag_inverted.pre_count=4u;
 sync_output_live_invalidate();assert(sync_output_live_poll());
 assert(s_native->inverted.valid&&s_native->inverted.have_selected&&
        s_native->inverted.field==10000u&&s_native->inverted.qualified_fields==3u);
 assert(!s_native->inverted.candidates[0].used&&!s_native->flag_inverted.pre_count&&
        !s_native->inverted.h_fit&&s_session->fresh_h_fit);
 vTaskDelay(1);
 s_native->native_phase=(sync_phase_bracket_t){.valid=true,.lo=10,.hi=20};
 s_native->inverted.h=clock_epoch_base+123300u;
 s_native->inverted.last=clock_epoch_base+123400u;
 sync_output_live_model_t model;
 assert(sync_output_live_snapshot(&model)&&!model.tracking);
 s_native->inverted.h_fit=s_native->inverted.h;s_native->inverted.h_standard=2u;
 assert(sync_output_live_snapshot(&model)&&model.tracking);
 s_native->inverted.have_group=true;s_native->inverted.group=clock_epoch_base+10000u;
 s_native->inverted.group_n=5u;s_native->flag_inverted.group_shifted=true;
 sync_output_live_forget();assert(sync_output_live_poll());
 assert(!s_native->inverted.valid&&!s_native->inverted.have_selected&&!s_native->inverted.field&&
        !s_native->inverted.have_group&&!s_native->flag_inverted.group_shifted);
 assert(sync_output_live_snapshot(&model)&&!model.tracking&&!model.valid&&model.standard==2u);
 assert(sync_output_live_end());checks+=12;
}
static void coast_field_grid(void){
 for(unsigned standard=1;standard<=2;++standard){
  uint32_t line=standard==2?40960u:40676u;
  unsigned halves=standard==2?625u:525u;
  for(unsigned k=0;k<17;++k){
   uint64_t anchor=10000;
   uint64_t at=anchor+((uint64_t)k*halves*line+32u)/64u;
   uint64_t h=anchor+(((uint64_t)k*halves+20u)*line+32u)/64u;
   assert(sop_coast_field(anchor,h,line,standard)==at);
   if(at>anchor)assert(sop_coast_field(anchor,at-1u,line,standard)<at);
   uint64_t relative=((h-anchor)*64u+line/2u)/line;
   uint64_t local=((h-at)*64u+line/2u)/line;
   assert(((relative^local)&1u)==(k&1u)); /* alternating field H phase */
   ++checks;
  }
  uint64_t h=UINT64_MAX-1u;
  uint64_t predicted=sop_coast_field(1u,h,line,standard);
  assert(predicted<=h && h-predicted<410000u);++checks;
 }
 assert(sop_coast_field(1000u,999u,40960u,2u)==1000u);++checks;
}
static void flag_resident_source(void){
 reset();mode=1;
 uint32_t epoch,prior;
 assert(!sync_output_live_source(NULL,&epoch));prior=epoch;
 assert(sync_output_live_begin(7,owner_live));
 assert(!sync_output_live_source(NULL,&epoch)&&epoch!=prior);
 assert(sync_output_live_end());prior=epoch;
 reset();mode=1;
 assert(sync_output_live_begin_flag(7,owner_live));
 assert(sync_output_live_source(NULL,&epoch)&&epoch!=prior);
 assert((registers[GPIO_FUNC2_OUT_SEL_CFG_REG]&GPIO_FUNC2_OUT_SEL_M)==48u);
 prior=epoch;
 vTaskDelay(1); /* deliver/calibrate this epoch's private markers */
 sync_output_live_invalidate();
 assert(!sync_output_live_source(NULL,&epoch)&&epoch!=prior);
 prior=epoch;
 assert(sync_output_live_poll());
 assert(sync_output_live_source(NULL,&epoch)&&epoch!=prior);
 prior=epoch;
 vTaskDelay(1);
 s_probe.stop=true;
 assert(!sync_output_live_source(NULL,&epoch));s_probe.stop=false;
 rmt_symbol_word_t w={.duration0=94,.level0=1,.duration1=1186,.level1=0};
 event(&w,1,true);
 assert(!sync_output_live_source(NULL,&epoch));
 assert(sync_output_live_poll());
 assert(sync_output_live_source(NULL,&epoch)&&epoch!=prior);
 /* Known comparator polarity must never be rejected because the LOW alias
  * independently passes a diagnostic qualifier. */
 s_native->native=(sync_native_track_t){.valid=true,.standard=1,.last=clock_epoch_base+123400,
   .h=clock_epoch_base+123300,.field=clock_epoch_base+100000,.line_ticks_x32=40676};
 s_native->inverted=(sync_native_track_t){.valid=true,.standard=2,.last=clock_epoch_base+123400,
   .h=clock_epoch_base+123300,.field=clock_epoch_base+100000,.line_ticks_x32=40960};
 s_native->native_phase=(sync_phase_bracket_t){.valid=true,.lo=10,.hi=20};
 sync_output_live_model_t model;
 assert(sync_output_live_snapshot(&model)&&model.valid&&model.inverted&&model.standard==2);
 char diagnostic[768];
 assert(sync_output_live_diagnostics(diagnostic,sizeof(diagnostic))&&strstr(diagnostic,"inverted=1 ambiguous=0"));
 /* Lost V must not discard a fresh selected H clock. Coast coordinates only;
  * never increment or replace observed field evidence. */
 clock_epoch_base=2000000u;
 s_native->inverted.have_selected=true;
 s_native->inverted.selected_line_ticks_x32=40960u;
 s_native->inverted.h_fit=clock_epoch_base+123300u;s_native->inverted.h_standard=2u;
 s_native->inverted.line_ticks_x32=40676u; /* unrelated generic reacquisition fit */
 s_native->inverted.last=clock_epoch_base+123400u;
 s_native->inverted.h=clock_epoch_base+123300u;
 s_native->inverted.field=100000u;
 unsigned fields=s_native->inverted.qualified_fields;
 assert(sync_output_live_snapshot(&model)&&!model.valid&&model.tracking&&model.line_ticks_x32==40960u);
 assert(model.field==sop_coast_field(100000u,clock_epoch_base+123300u,40960u,2u));
 assert(model.field>100000u && model.field<=model.last_h && model.last_h-model.field<400000u);
 assert(s_native->inverted.field==100000u&&s_native->inverted.qualified_fields==fields);
 s_native->inverted.h=clock_epoch_base-60000u;
 assert(sync_output_live_snapshot(&model)&&!model.tracking&&!model.valid);
 s_native->inverted.h=clock_epoch_base+123300u;
 sync_output_live_invalidate();
 assert(sync_output_live_snapshot(&model)&&!model.tracking&&!model.valid);
 checks+=5;
 assert(sync_output_live_end());
 assert(!sync_output_live_source(NULL,&epoch));
 checks+=18;
}
static void flag_idle_lifetime(void){
 reset();mode=1;assert(sop_session_begin(7,owner_live,true,false,true));
 vTaskDelay(1);sop_drain(&s_probe);
 s_native->inverted.have_group=true;s_native->inverted.group=5000u;
 s_probe.ended=true;
 assert(sop_session_poll(s_session));
 assert(s_native->inverted.have_group&&s_native->inverted.group==5000u);
 assert(!s_native->native_phase.valid&&!s_native->native_phase_checked);
 s_session->invalidated=true;
 assert(sop_session_poll(s_session)&&!s_native->inverted.have_group);
 assert(sop_session_end(s_session));checks+=5;
 reset();mode=1;assert(sop_session_begin(7,owner_live,true,false,true));
 vTaskDelay(1);sop_drain(&s_probe);s_native->inverted.have_group=true;
 s_probe.prefix_n=0;s_probe.ended=true;
 assert(sop_session_poll(s_session)&&!s_native->inverted.have_group);
 assert(sop_session_end(s_session));checks+=2;
}
static void flag_profile_route(void){
 parser_start();s_probe.marker_required=true;s_probe.discard_before=1000u;
 s_native->trace_flag=true;s_native->native_phase_checked=true;
 sync_native_flag_init(&s_native->flag_native,&s_native->native);
 sync_native_flag_init(&s_native->flag_inverted,&s_native->inverted);
 s_native->native_phase=(sync_phase_bracket_t){.lo=10000,.hi=10000,.valid=true};
 for(unsigned i=0;i<45u;++i)sop_native_run(&s_probe,2000u+i*1271u,160u,true);
 assert(s_native->inverted.h_standard==1u&&!s_native->native.h_standard);
 assert(!s_native->inverted.valid);checks+=2;
}
int main(void){flag_mutation_authority();coast_field_grid();flag_resident_source();flag_idle_lifetime();flag_profile_route();dependent_teardown();restore_fault_lifecycle();resident_lifecycle();native_projection();parser_tests();fixture(false);fixture(true);native_polarity_fixture(false,false);native_polarity_fixture(false,true);native_polarity_fixture(true,false);native_polarity_fixture(true,true);printf("PASS: %u probe runtime checks\n",checks);return 0;}
'''


class Runtime(unittest.TestCase):
    def test_actual_probe(self):
        source = (ROOT / "main/sync_output_probe.c").read_text()
        source = re.sub(r'^#include[^\n]*\n', '', source, flags=re.MULTILINE)
        phase = (ROOT / 'main/sync_phase_bracket.h').read_text()
        phase = re.sub(r'^#(?:include|pragma)[^\n]*\n', '', phase, flags=re.MULTILINE)
        native = (ROOT / 'main/sync_native_track.h').read_text()
        native = re.sub(r'^#(?:include|pragma)[^\n]*\n', '', native, flags=re.MULTILINE)
        flag = (ROOT / 'main/sync_native_flag_track.h').read_text()
        flag = re.sub(r'^#(?:include|pragma)[^\n]*\n', '', flag, flags=re.MULTILINE)
        api = (ROOT / "main/sync_output_probe.h").read_text()
        api = re.sub(r'^#(?:include|pragma)[^\n]*\n', '', api, flags=re.MULTILINE)
        trace = (ROOT / 'main/sync_native_trace.h').read_text()
        trace = re.sub(r'^#(?:include|pragma)[^\n]*\n', '', trace, flags=re.MULTILINE)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "probe.c").write_text(PREFIX + trace + api + phase + native + flag + source + SUFFIX)
            compiler = os.environ.get("CC", "cc")
            subprocess.run([compiler, "-O2", "-std=c11", "-Wall", "-Wextra", "-Werror",
                            str(path / "probe.c"), "-o", str(path / "probe")], check=True)
            result = subprocess.run([str(path / "probe")], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout[-6000:])
            self.assertIn("PASS:", result.stdout)
            print(result.stdout, end="")


if __name__ == "__main__":
    unittest.main()

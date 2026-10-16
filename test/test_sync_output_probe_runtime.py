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
#define ESP_OK 0
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
static bool scheduler_held;
static void vTaskSuspendAll(void){assert(!scheduler_held);scheduler_held=true;}
static int xTaskResumeAll(void){assert(scheduler_held);scheduler_held=false;return 0;}
static bool sync_edge_clock_prepare(void){++clock_calls;++clock_prepares;assert((registers[GPIO_ENABLE_REG]&(1u<<2))&&!(registers[GPIO_OUT_REG]&(1u<<2)));clock_prepared=clock_fail!=1u;return clock_prepared;}
static bool sync_edge_clock_arm(void){++clock_calls;++clock_arms;assert(scheduler_held&&clock_prepared&&receiving&&!(registers[GPIO_OUT_REG]&(1u<<2)));if(clock_fail==4u)now+=201;clock_armed=clock_fail!=2u;return clock_armed;}
static bool sync_edge_clock_enable_filter(void){++clock_calls;assert(scheduler_held&&clock_armed&&!(registers[GPIO_OUT_REG]&(1u<<2)));return clock_fail!=5u;}
static bool sync_edge_clock_disable_filter(void){++clock_calls;++clock_pauses;assert(clock_armed&&!(registers[GPIO_OUT_REG]&(1u<<2)));clock_epoch_base+=clock_epoch_stride;clock_reads=0;marker_sent=false;return clock_fail!=6u;}
static bool sync_edge_clock_read(uint64_t *n){++clock_calls;if(!clock_armed)return false;const unsigned samples[]={0,1000,1030,1400,1430,1600,123456};*n=clock_epoch_base+samples[clock_reads<7u?clock_reads:6u];if(mode==4u&&clock_epoch_base&&clock_reads==0u)*n=0u;++clock_reads;return true;}
static bool sync_edge_clock_close(void){++clock_calls;assert(!(registers[GPIO_ENABLE_REG]&(1u<<2)));clock_prepared=clock_armed=false;return clock_fail!=3u;}
static uint32_t sync_edge_clock_filter_ns(void){return 1200u;}
static const char *sync_edge_clock_error(void){return "mock";}
static void esp_rom_delay_us(unsigned n){assert((n==50u||n==16u||n==4u)&&(registers[GPIO_ENABLE_REG]&(1u<<2)));now+=n;}
static rmt_rx_event_callbacks_t mock_cb;
static void *mock_context;
#define REG_READ(address) registers[address]
static void reg_write(unsigned address,uint32_t value){
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
 assert(cfg->signal_range_min_ns==(clock_prepared?0u:2500u)&&cfg->signal_range_max_ns==(clock_prepared?1000000u:100000u)&&cfg->flags.en_partial_rx);
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
 writes=api_calls=fail_at=mode=receives=deletes=disables=0;
 live_value=true;mutate_pin=channel_live=rx_enabled=receiving=false;now=0;
 memset(&mock_cb,0,sizeof(mock_cb));mock_context=NULL;
 s_busy=false;s_quarantined=NULL;memset(&s_probe,0,sizeof(s_probe));
 s_clock_fault=false;clock_calls=clock_fail=clock_reads=0;clock_prepared=clock_armed=scheduler_held=marker_sent=false;
 clock_epoch_base=0;clock_epoch_stride=100000u;clock_prepares=clock_arms=clock_pauses=0;
}
static void parser_start(void){reset();s_probe.armed=true;s_probe.live=owner_live;s_probe.generation=7;sop_epoch(&s_probe);}
static void event(rmt_symbol_word_t *words,size_t n,bool last){
 rmt_rx_done_event_data_t e={.received_symbols=words,.num_symbols=n,.flags={.is_last=last}};
 assert(!sop_received((void*)1,&e,&s_probe));++checks;
}
static void native_projection(void){
 if(!s_native){s_native=calloc(1,sizeof(*s_native));}
 assert(s_native);
 parser_start();s_probe.marker_required=true;s_probe.discard_before=2000;
 s_native->native_phase_checked=true;
 int64_t base=INT64_C(1)<<44;
 s_native->native_phase=(sync_phase_bracket_t){.lo=base-10,.hi=base+10,.valid=true};
 sop_low(&s_probe,1999,94);assert(!s_native->native.seen);++checks;
 sop_low(&s_probe,2000,94);assert(s_native->native.last==(uint64_t)base+2000u);++checks;
 sop_epoch(&s_probe);assert(!s_native->native.seen&&!s_native->native_phase.valid);++checks;
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
 parser_start();s_probe.marker_required=true;event(words,1,false);
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
static void lifecycle(void){
 for(unsigned failure=0;failure<=8;++failure){
  reset();fail_at=failure;uint32_t saved[13];memcpy(saved,registers,sizeof(saved));
  sync_output_probe(7,owner_live);
  assert(!memcmp(registers,saved,sizeof(saved))&&!s_probe.armed&&!s_busy);
  assert(now<=100000);assert(!rx_enabled||s_quarantined);
  if(failure==6||failure==7||failure==8){assert(s_quarantined);unsigned calls=api_calls;
   sync_output_probe(7,owner_live);assert(api_calls==calls);
   rmt_symbol_word_t w={.duration0=1186,.level0=1,.duration1=94,.level1=0};
   unsigned callbacks=s_probe.callbacks;event(&w,1,false);assert(s_probe.callbacks==callbacks);
  }else assert(!channel_live);
  ++checks;
 }
 reset();mode=1;sync_output_probe(7,owner_live);
 assert(s_probe.h>1000&&s_probe.pal32>10&&!s_probe.bad_data&&now==50000);
 reset();registers[GPIO_FUNC8_OUT_SEL_CFG_REG]&=~GPIO_FUNC8_OE_SEL_M;
 uint32_t native_matrix=registers[GPIO_FUNC8_OUT_SEL_CFG_REG];
 mode=1;sync_output_probe(7,owner_live);
 assert(s_probe.h>1000&&!s_probe.bad_data&&receives&&
        registers[GPIO_FUNC8_OUT_SEL_CFG_REG]==native_matrix);
 reset();mode=2;sync_output_probe(7,owner_live);assert(s_probe.cancelled&&now==1000);
 reset();mutate_pin=true;uint32_t saved=registers[GPIO_FUNC8_OUT_SEL_CFG_REG];
 sync_output_probe(7,owner_live);assert(registers[GPIO_FUNC8_OUT_SEL_CFG_REG]==saved&&!receives);
 reset();registers[GPIO_FUNC8_OUT_SEL_CFG_REG]^=1u;sync_output_probe(7,owner_live);assert(!api_calls&&!writes);
 reset();live_value=false;sync_output_probe(7,owner_live);assert(!api_calls&&!writes);
 reset();sync_output_probe(7,NULL);assert(!api_calls&&!writes);
 reset();s_busy=true;sync_output_probe(7,owner_live);assert(!api_calls&&!writes);
 reset();registers[GPIO_ENABLE_REG]|=1u<<2;
 sync_output_probe(7,owner_live);assert(!api_calls&&!writes);
 reset();registers[GPIO_PIN2_REG]|=GPIO_PIN2_PAD_DRIVER_M;
 sync_output_probe(7,owner_live);assert(!api_calls&&!writes);
 reset();registers[IO_MUX_GPIO2_REG]=FUN_PU|FUN_PD|0x100000u;
 registers[GPIO_FUNC2_OUT_SEL_CFG_REG]=0x200000u|256u;
 uint32_t saved_all[13];memcpy(saved_all,registers,sizeof(saved_all));
 sync_output_probe(7,owner_live);assert(receives&&!memcmp(saved_all,registers,sizeof(saved_all)));
 reset();mode=1;sync_output_clock_probe(7,owner_live);
 assert(clock_calls==11u&&!clock_prepared&&!clock_armed&&s_probe.h>1000&&!s_clock_fault);
 reset();mode=1;registers[GPIO_OUT_REG]=(1u<<2)|0x80000000u;
 uint32_t data_before=registers[GPIO_OUT_REG];sync_output_clock_probe(7,owner_live);
 assert(registers[GPIO_OUT_REG]==data_before&&!s_probe.marker_invalid);
 reset();sync_output_clock_probe(7,owner_live);
 assert(receives>1u&&s_probe.epochs>1u&&!clock_prepared&&clock_prepares==1u&&clock_arms==1u);
 reset();mode=3;sync_output_clock_probe(7,owner_live);
 assert(s_probe.epochs>20u&&s_probe.h>1000u&&clock_prepares==1u&&clock_arms==1u&&clock_pauses>20u);
 reset();mode=3;clock_epoch_stride=UINT64_C(1)<<40;sync_output_clock_probe(7,owner_live);
 assert(s_probe.epochs>20u&&s_probe.h>1000u&&clock_prepares==1u&&clock_arms==1u);
 reset();mode=4;sync_output_clock_probe(7,owner_live);
 assert(receives==2u&&s_probe.epochs==2u&&now<50000&&!clock_prepared);
 for(unsigned i=1;i<=5u;++i){reset();mode=1;clock_fail=i;
  sync_output_clock_probe(7,owner_live);assert(!clock_prepared&&!clock_armed);
  assert(s_clock_fault==(i==3u));}
 parser_start();s_probe.callbacks=256u;
 rmt_symbol_word_t rate_word={.duration0=1186,.level0=1,.duration1=94,.level1=0};
 event(&rate_word,1,false);assert(s_probe.stop&&s_probe.rate_limited&&!s_probe.symbols);
}

static void resident_lifecycle(void){
 reset();mode=1;
 uint32_t saved[13];memcpy(saved,registers,sizeof(saved));
 assert(sync_output_live_begin(7,owner_live));
 assert(sync_output_live_active()&&now<200&&clock_prepares==1&&clock_arms==1);
 assert(!sync_output_live_begin(7,owner_live));
 for(unsigned i=0;i<350u;++i){vTaskDelay(1);assert(sync_output_live_poll());}
 assert(s_probe.callbacks>256u&&!s_probe.rate_limited&&clock_prepares==1&&clock_arms==1);
 /* Inject a qualified model to test publication/lifetime independently of
  * the separate real waveform qualification fixtures. */
 s_native->native=(sync_native_track_t){.valid=true,.standard=2,.last=123400,
     .h=123300,.field=100000,.line_ticks_x32=40960};
 s_native->native_phase=(sync_phase_bracket_t){.valid=true,.lo=10,.hi=20};
 sync_output_live_model_t model;
 assert(sync_output_live_snapshot(&model)&&model.valid&&model.standard==2&&
        model.last_h==123300&&model.line_ticks_x32==40960&&model.now==123456);
 sync_output_live_invalidate();
 assert(!s_probe.armed&&sync_output_live_snapshot(&model)&&!model.valid);
 unsigned callbacks=s_probe.callbacks,prior_receives=receives;
 rmt_symbol_word_t w={.duration0=1186,.level0=1,.duration1=94,.level1=0};
 event(&w,1,false);assert(s_probe.callbacks==callbacks);
 assert(sync_output_live_poll()&&receives==prior_receives+1u&&clock_prepares==1&&clock_arms==1);
 assert(sync_output_live_snapshot(&model)&&!model.valid&&model.standard==2&&model.last_h==123300);
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


static void restore_fault_lifecycle(void){
 reset();mode=1;assert(sync_output_live_begin(7,owner_live));
 /* Simulate pin-state restoration/readback mismatch with successful SDK
  * teardown. A subsequent begin must refuse until reboot. */
 registers[GPIO_PIN2_REG]^=GPIO_PIN2_PAD_DRIVER_M;
 assert(!sync_output_live_end()&&s_clock_fault&&!s_busy&&!channel_live);
 unsigned calls=api_calls;
 assert(!sync_output_live_begin(7,owner_live));
 sync_output_probe(7,owner_live);
 assert(api_calls==calls&&!sync_output_live_end());
 ++checks;
}

int main(void){restore_fault_lifecycle();resident_lifecycle();native_projection();parser_tests();fixture(false);fixture(true);lifecycle();printf("PASS: %u probe runtime checks\n",checks);return 0;}
'''


class Runtime(unittest.TestCase):
    def test_actual_probe(self):
        source = (ROOT / "main/sync_output_probe.c").read_text()
        source = re.sub(r'^#include[^\n]*\n', '', source, flags=re.MULTILINE)
        phase = (ROOT / 'main/sync_phase_bracket.h').read_text()
        phase = re.sub(r'^#(?:include|pragma)[^\n]*\n', '', phase, flags=re.MULTILINE)
        native = (ROOT / 'main/sync_native_track.h').read_text()
        native = re.sub(r'^#(?:include|pragma)[^\n]*\n', '', native, flags=re.MULTILINE)
        api = (ROOT / "main/sync_output_probe.h").read_text()
        api = re.sub(r'^#(?:include|pragma)[^\n]*\n', '', api, flags=re.MULTILINE)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "probe.c").write_text(PREFIX + api + phase + native + source + SUFFIX)
            compiler = os.environ.get("CC", "cc")
            subprocess.run([compiler, "-O2", "-std=c11", "-Wall", "-Wextra", "-Werror",
                            str(path / "probe.c"), "-o", str(path / "probe")], check=True)
            result = subprocess.run([str(path / "probe")], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout[-6000:])
            self.assertIn("PASS:", result.stdout)
            self.assertIn("camera_qualified=0", result.stdout)
            self.assertIn("self_gate_excluded=0", result.stdout)
            print(result.stdout, end="")


if __name__ == "__main__":
    unittest.main()

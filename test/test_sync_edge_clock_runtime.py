#!/usr/bin/env python3
"""C5VRX/Twotoz: actual edge-clock ownership/FSM code against failing SDK.

Does not prove physical ETM edge association or filter latency.
"""
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SDK = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#define IRAM_ATTR
#define ESP_OK 0
#define SOC_MOD_CLK_XTAL 1
#define SOC_MOD_CLK_PLL_F80M 2
#define GPTIMER_CLK_SRC_DEFAULT 0
#define GPTIMER_COUNT_UP 0
#define GPIO_ETM_EVENT_EDGE_ANY 3
#define GPTIMER_ETM_TASK_START_COUNT 1
typedef bool (*sync_edge_alarm_fn)(uint64_t,void *);
typedef int glitch_filter_clock_source_t;
typedef void *gpio_glitch_filter_handle_t;
typedef void *gptimer_handle_t;
typedef void *esp_etm_event_handle_t;
typedef void *esp_etm_task_handle_t;
typedef void *esp_etm_channel_handle_t;
typedef struct {int gpio_num,clk_src;uint32_t window_width_ns,window_thres_ns;}gpio_flex_glitch_filter_config_t;
typedef struct {int clk_src,direction,intr_priority;uint32_t resolution_hz;}gptimer_config_t;
typedef struct {int edge;}gpio_etm_event_config_t;
typedef struct {int task_type;}gptimer_etm_task_config_t;
typedef struct {int flags;}esp_etm_channel_config_t;
static struct {union {uint32_t val;struct {uint32_t pad:20,iomux_func_clk_sel:2,rest:10;};}iomux_clk_conf;} PCR;
static bool filter,filter_on,timer,timer_on,hw_run,sdk_run,edge,task,link,link_on;
static const char *fail_name;
static unsigned calls,starts;
typedef struct {uint64_t count_value;}gptimer_alarm_event_data_t;
typedef struct {uint64_t alarm_count;}gptimer_alarm_config_t;
typedef struct {bool(*on_alarm)(void*,const gptimer_alarm_event_data_t*,void*);}gptimer_event_callbacks_t;
static gptimer_event_callbacks_t callbacks;
static void *callback_arg;
static uint64_t alarm_tick;
static bool alarm_on;
static uint64_t ticks;
#define ENTER() do {++calls;if(fail_name&&!strcmp(fail_name,__func__))return -1;}while(0)
static int gpio_new_flex_glitch_filter(const gpio_flex_glitch_filter_config_t *c,void **out){
 ENTER();assert(!filter&&c->gpio_num==2&&c->window_width_ns==c->window_thres_ns);
 assert(c->window_width_ns==(PCR.iomux_clk_conf.iomux_func_clk_sel==0?1200u:750u));
 assert(c->clk_src==(PCR.iomux_clk_conf.iomux_func_clk_sel==0?SOC_MOD_CLK_XTAL:SOC_MOD_CLK_PLL_F80M));
 filter=true;*out=(void*)1;return 0;}
static int gpio_glitch_filter_enable(void *p){ENTER();assert(p&&filter&&!filter_on);filter_on=true;return 0;}
static int gpio_glitch_filter_disable(void *p){ENTER();assert(p&&filter&&filter_on);filter_on=false;return 0;}
static int gpio_del_glitch_filter(void *p){ENTER();assert(p&&filter&&!filter_on);filter=false;return 0;}
static int gptimer_new_timer(const gptimer_config_t *c,void **out){ENTER();assert(!timer&&c->resolution_hz==20000000u);timer=true;*out=(void*)2;return 0;}
static int gptimer_register_event_callbacks(void *p,const gptimer_event_callbacks_t *c,void *arg){ENTER();assert(p&&timer&&!timer_on&&c->on_alarm);callbacks=*c;callback_arg=arg;return 0;}
static int gptimer_set_alarm_action(void *p,const gptimer_alarm_config_t *c){ENTER();assert(p&&timer_on);alarm_on=c!=NULL;if(c)alarm_tick=c->alarm_count;return 0;}
static int gptimer_enable(void *p){ENTER();assert(p&&timer&&!timer_on);timer_on=true;return 0;}
static int gptimer_set_raw_count(void *p,uint64_t n){ENTER();assert(p&&timer_on&&!hw_run&&n==0);ticks=n;return 0;}
static int gptimer_get_raw_count(void *p,uint64_t *n){ENTER();assert(p&&timer_on);*n=ticks;return 0;}
static int gptimer_start(void *p){ENTER();assert(p&&timer_on&&!link);++starts;hw_run=sdk_run=true;return 0;}
static int gptimer_stop(void *p){ENTER();assert(p&&timer_on&&sdk_run);hw_run=sdk_run=false;return 0;}
static int gptimer_disable(void *p){ENTER();assert(p&&timer_on&&!hw_run&&!sdk_run);timer_on=false;return 0;}
static int gptimer_del_timer(void *p){ENTER();assert(p&&timer&&!timer_on&&!task&&!link);timer=false;return 0;}
static int gpio_new_etm_event(const gpio_etm_event_config_t *c,void **out){ENTER();assert(!edge&&c->edge==3);edge=true;*out=(void*)3;return 0;}
static int gpio_etm_event_bind_gpio(void *p,int gpio){ENTER();assert(p&&edge&&gpio==2);return 0;}
static int esp_etm_del_event(void *p){ENTER();assert(p&&edge&&!link);edge=false;return 0;}
static int gptimer_new_etm_task(void *p,const gptimer_etm_task_config_t *c,void **out){ENTER();assert(p&&timer&&!task&&c->task_type==1);task=true;*out=(void*)4;return 0;}
static int esp_etm_del_task(void *p){ENTER();assert(p&&task&&!link);task=false;return 0;}
static int esp_etm_new_channel(const esp_etm_channel_config_t *c,void **out){ENTER();assert(c&&!link);link=true;*out=(void*)5;return 0;}
static int esp_etm_channel_connect(void *p,void *e,void *t){ENTER();assert(p&&e&&t&&link&&edge&&task);return 0;}
static int esp_etm_channel_enable(void *p){ENTER();assert(p&&link&&!link_on&&timer_on);link_on=true;return 0;}
static int esp_etm_channel_disable(void *p){ENTER();assert(p&&link&&link_on);link_on=false;return 0;}
static int esp_etm_del_channel(void *p){ENTER();assert(p&&link&&!link_on);link=false;return 0;}
'''
HARNESS = r'''
static void reset(void){
 memset(&s_clock,0,sizeof(s_clock));memset(&PCR,0,sizeof(PCR));
 filter=filter_on=timer=timer_on=hw_run=sdk_run=edge=task=link=link_on=false;
 fail_name=NULL;calls=starts=0;ticks=0;callbacks=(gptimer_event_callbacks_t){0};callback_arg=NULL;alarm_on=false;alarm_tick=0;
}
static void no_resources(void){assert(!filter&&!filter_on&&!timer&&!timer_on&&!hw_run&&!sdk_run&&!edge&&!task&&!link&&!link_on&&!s_clock.busy);}
static void hardware_edge(void){assert(link_on&&timer_on);hw_run=true;}
static unsigned fired;
static bool target_alarm(uint64_t count,void *arg){assert(arg==(void*)42&&count==900);++fired;return true;}
int main(void){
 unsigned checks=0;
 for(unsigned source=0;source<=2;source+=2){
  reset();PCR.iomux_clk_conf.iomux_func_clk_sel=source;uint32_t before=PCR.iomux_clk_conf.val;
  uint64_t n=999;
  assert(!sync_edge_clock_arm()&&!sync_edge_clock_read(&n));
  assert(sync_edge_clock_prepare()&&PCR.iomux_clk_conf.val==before&&!hw_run&&!starts);
  assert(!sync_edge_clock_prepare()&&!sync_edge_clock_read(&n));
  assert(sync_edge_clock_arm()&&!sync_edge_clock_arm()&&!sync_edge_clock_read(NULL));
  assert(sync_edge_clock_read(&n)&&n==0&&!starts); /* arming is NOT a source edge */
  hardware_edge();ticks+=100;
  assert(sync_edge_clock_enable_filter()&&!sync_edge_clock_enable_filter());
  hardware_edge();assert(ticks==100); /* another edge cannot reload START_COUNT */
  ticks+=50;assert(sync_edge_clock_read(&n)&&n==150&&!starts&&!sdk_run);
  assert(sync_edge_clock_disable_filter()&&!sync_edge_clock_disable_filter());
  hardware_edge();ticks+=50;assert(sync_edge_clock_enable_filter());
  assert(sync_edge_clock_read(&n)&&n==200&&!starts&&!sdk_run);
  assert(sync_edge_clock_close()&&starts==1&&PCR.iomux_clk_conf.val==before);
  no_resources();assert(sync_edge_clock_close());++checks;
 }
 const char *setup[]={"gpio_new_flex_glitch_filter","gptimer_new_timer",
  "gptimer_register_event_callbacks","gptimer_enable","gptimer_set_raw_count","gpio_new_etm_event","gpio_etm_event_bind_gpio",
  "gptimer_new_etm_task","esp_etm_new_channel","esp_etm_channel_connect"};
 for(unsigned i=0;i<sizeof(setup)/sizeof(setup[0]);++i){
  reset();fail_name=setup[i];assert(!sync_edge_clock_prepare());no_resources();++checks;
 }
 const char *cleanup[]={"esp_etm_channel_disable","esp_etm_del_channel","gptimer_start","gptimer_stop",
  "gptimer_disable","esp_etm_del_event","esp_etm_del_task","gptimer_del_timer",
  "gpio_glitch_filter_disable","gpio_del_glitch_filter"};
 for(unsigned i=0;i<sizeof(cleanup)/sizeof(cleanup[0]);++i){
  reset();assert(sync_edge_clock_prepare()&&sync_edge_clock_arm());hardware_edge();ticks=10;
  assert(sync_edge_clock_enable_filter());
  fail_name=cleanup[i];assert(!sync_edge_clock_close()&&s_clock.quarantined&&s_clock.busy);
  unsigned prior=calls;assert(!sync_edge_clock_close()&&!sync_edge_clock_prepare());
  assert(calls==prior);++checks; /* no reuse after a failed hardware teardown */
 }
 reset();PCR.iomux_clk_conf.iomux_func_clk_sel=1;assert(!sync_edge_clock_prepare()&&!calls);no_resources();++checks;
 reset();assert(sync_edge_clock_prepare());fail_name="esp_etm_channel_enable";
 assert(!sync_edge_clock_arm());fail_name=NULL;assert(sync_edge_clock_close());no_resources();++checks;
 reset();assert(sync_edge_clock_prepare()&&sync_edge_clock_arm());fail_name="gptimer_get_raw_count";
 uint64_t n=999;assert(!sync_edge_clock_read(&n)&&n==999);fail_name=NULL;
 assert(sync_edge_clock_close());no_resources();++checks;
 reset();assert(sync_edge_clock_prepare()&&sync_edge_clock_arm());fail_name="gpio_glitch_filter_enable";
 assert(!sync_edge_clock_enable_filter());fail_name=NULL;assert(sync_edge_clock_close());no_resources();++checks;
 reset();assert(sync_edge_clock_prepare()&&sync_edge_clock_arm());
 assert(!sync_edge_clock_alarm_at(900));
 assert(sync_edge_clock_alarm_bind(target_alarm,(void*)42));
 assert(sync_edge_clock_alarm_at(900)&&alarm_on&&alarm_tick==900);
 gptimer_alarm_event_data_t event={.count_value=900};
 assert(callbacks.on_alarm((void*)2,&event,callback_arg)&&fired==1);
 assert(sync_edge_clock_alarm_bind(NULL,NULL)&&!alarm_on);
 assert(!callbacks.on_alarm((void*)2,&event,callback_arg)&&fired==1);
 assert(!sync_edge_clock_alarm_at(1000));
 assert(sync_edge_clock_close());no_resources();++checks;
 reset();assert(sync_edge_clock_prepare()&&sync_edge_clock_arm());
 fail_name="gptimer_set_alarm_action";
 assert(!sync_edge_clock_alarm_bind(target_alarm,(void*)42)&&!s_clock.alarm);
 fail_name=NULL;assert(sync_edge_clock_close());no_resources();++checks;
 printf("PASS: %u edge-clock lifecycle/ownership/FSM cases; no physical phase claim\n",checks);
 return 0;
}
'''


class Runtime(unittest.TestCase):
    def test_actual_clock(self):
        source = (ROOT / 'main/sync_edge_clock.c').read_text()
        source = re.sub(r'^#include[^\n]*\n', '', source, flags=re.MULTILINE)
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / 'clock.c').write_text(SDK + source + HARNESS)
            subprocess.run(['cc', '-O2', '-std=c11', '-Wall', '-Wextra', '-Werror',
                            str(base / 'clock.c'), '-o', str(base / 'clock')], check=True)
            result = subprocess.run([str(base / 'clock')], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            print(result.stdout.strip())


if __name__ == '__main__':
    unittest.main()

#!/usr/bin/env python3
"""C5VRX by Twotoz/contributors: execute actual W ISR/task/turn on mocked SDK.
No test may turn a failed source turn into camera evidence. Hardware timing
and DMA publication are deliberately not simulated as proven properties.
"""
from pathlib import Path
import os
import re
import subprocess
import tempfile
import unittest
ROOT = Path(__file__).resolve().parents[1]
PREFIX = r"""
#include <assert.h>
#include <setjmp.h>
#include "sync_service.h"
#define IRAM_ATTR
#define pdFALSE 0
#define pdTRUE 1
#define pdPASS 1
#define portMAX_DELAY UINT32_MAX
#define ESP_OK 0
#define GPTIMER_CLK_SRC_DEFAULT 0
#define GPTIMER_COUNT_UP 0
typedef int BaseType_t;
typedef void *TaskHandle_t;
typedef void *gptimer_handle_t;
typedef struct {int unused;} gptimer_alarm_event_data_t;
typedef struct {int clk_src,direction;unsigned resolution_hz;} gptimer_config_t;
typedef struct {bool (*on_alarm)(gptimer_handle_t,const gptimer_alarm_event_data_t *,void *);} gptimer_event_callbacks_t;
typedef struct {uint64_t alarm_count,reload_count;struct {bool auto_reload_on_alarm;} flags;} gptimer_alarm_config_t;
static int64_t now;
static unsigned timer_live,timer_enabled,timer_started,task_live,notifications,turn_count,end_count;
static bool fail_end,mutate_context,overrun;
static bool hold_done;
static bool invalid_read,long_overrun;
static unsigned seen_index;
static unsigned cleanup_after;
static unsigned stack_scans;
static void *task_arg;
static jmp_buf deleted;
static uint8_t raw[32768];
static gptimer_event_callbacks_t cb;
static void *cbarg;
static void vTaskSuspendAll(void){}
static int xTaskResumeAll(void){return 0;}
static int64_t esp_timer_get_time(void){return now;}
static uint32_t esp_cpu_get_cycle_count(void){return (uint32_t)now*240u;}
static unsigned uxTaskGetStackHighWaterMark(void *unused){(void)unused;++stack_scans;return 2048;}
static void vTaskNotifyGiveFromISR(void *task,int *wake){assert(task);++notifications;*wake=pdTRUE;}
static void xTaskNotifyGive(void *task){assert(task);++notifications;}
static uint32_t ulTaskNotifyTake(int clear,uint32_t wait);
static void vTaskDelete(void *task){assert(!task && task_live);task_live=0;longjmp(deleted,1);}
static int gptimer_new_timer(const gptimer_config_t *c,void **out){assert(c->resolution_hz==1000000);timer_live=1;*out=(void*)1;return 0;}
static int gptimer_register_event_callbacks(void *t,const gptimer_event_callbacks_t *c,void *arg){assert(t);cb=*c;cbarg=arg;return 0;}
static int gptimer_set_alarm_action(void *t,const gptimer_alarm_config_t *a){assert(t&&a->alarm_count==80&&a->flags.auto_reload_on_alarm);return 0;}
static int gptimer_enable(void *t){assert(t);timer_enabled=1;return 0;}
static int gptimer_start(void *t){assert(t&&timer_enabled);timer_started=1;return 0;}
static int gptimer_stop(void *t){assert(t);timer_started=0;return 0;}
static int gptimer_disable(void *t){assert(t&&!timer_started);timer_enabled=0;return 0;}
static int gptimer_del_timer(void *t){assert(t&&!timer_enabled);timer_live=0;return 0;}
static int xTaskCreate(void (*fn)(void*),const char *name,unsigned stack,void *arg,unsigned priority,void **out){(void)fn;(void)name;assert(stack==2560&&priority==5);task_live=1;task_arg=arg;*out=(void*)2;return pdPASS;}
void sfw_init(sync_flywheel_t *f){*f=(sync_flywheel_t){0};}
unsigned sfw_run(sync_flywheel_t *f,const sfw_ring_t *r,uint64_t end,uint64_t head,uint64_t floor,bool repair,uint32_t budget){
 (void)floor;assert(!repair&&r->phase);assert(!r->program_verified);
 if(!budget){assert(!end&&!head);return 0;}
 assert(budget==52&&head==end&&r->read_extent);++turn_count;f->evals=budget;f->state=SFW_TRACK;
 sync_read_extent_note(r->read_extent,invalid_read?end+1u:end-128u);
 f->acquisitions=1;now+=long_overrun?130:overrun?80:3;return 0;
}
bool sfw_locked(const sync_flywheel_t *f){return f->state==SFW_TRACK;}
int sfw_standard(const sync_flywheel_t *f){return f->state==SFW_TRACK?SFW_STD_PAL:SFW_STD_UNKNOWN;}
bool transport_sync_source_begin(sync_service_source_t *s,bool discover){
 assert(!s->leased);s->leased=true;
 if(discover){for(unsigned i=0;i<9;++i)s->layout.nodes[i]=(dma_progress_node_t){0x1000+16*i,0x1000+16*((i+1)%9),4092*i,i==8?32:4092};
 s->ring=(sfw_ring_t){.ring=raw,.ring_bytes=sizeof(raw)};}
 unsigned index=hold_done?seen_index:(unsigned)((now/80)%9);seen_index=index;
 s->first=s->last=(sync_done_capture_t){.done=s->layout.nodes[index].address,.bf0=s->layout.nodes[(index+1u)%9u].address,.first_us=now,.last_us=now,
 .context={1,2,3,4},.rx_clock=0x60005,.tx_clock=0x60005,.memory_rate_bound=192000000,.live=true,.metadata_owned=true};return true;
}
bool transport_sync_source_end(sync_service_source_t *s){assert(s->leased);s->leased=false;++end_count;
 s->last.first_us=s->last.last_us=(uint64_t)now;
 if(mutate_context)++s->last.context.gain;
 s->finish_last=s->last;return !fail_end;}
"""
SUFFIX = r"""
static uint32_t ulTaskNotifyTake(int clear,uint32_t wait){assert(clear&&wait==portMAX_DELAY);if(cleanup_after && !--cleanup_after)s_stop=true;unsigned n=notifications;notifications=0;return n;}
static void prime(service_t *s){
 sync_done_observation_t o;sfw_init(&s->detector);sync_done_restart(&s->progress);
 assert(transport_sync_source_begin(&s->source,false));
 assert(sync_done_observe(&s->progress,&s->source.layout,&s->source.first,&s->source.last,&o)==SDP_OK);
 assert(transport_sync_source_end(&s->source));s->progress.fresh_from-=32768;
}
int main(void){
 sync_service_toggle();assert(s_active&&timer_started&&task_live);
 service_t *s=task_arg;
 assert(!s->progress.ready&&!s->source.leased&&!s_status.turns&&!s_status.calls&&!s_status.locked);
 assert(s_status.prewarm_reason&&!strcmp(s_status.prewarm_reason,"ok"));
 assert(cb.on_alarm(s->timer,NULL,cbarg)&&notifications==1&&turn_count==0);
 /* Seed a valid prior relative epoch, then advance past one fresh ring. */
 sync_done_observation_t o;
 assert(transport_sync_source_begin(&s->source,false));
 assert(sync_done_observe(&s->progress,&s->source.layout,&s->source.first,&s->source.last,&o)==SDP_OK);
 assert(transport_sync_source_end(&s->source));
 s->progress.fresh_from-=32768;
 now+=80;assert(!service_turn(s)&&turn_count==1&&s_status.locked&&!s->source.leased);
 assert(s_status.worst_begin_cycles+s_status.worst_observe_cycles+s_status.worst_kernel_cycles+
        s_status.worst_end_cycles+s_status.worst_other_cycles==s_status.max_cycles);
 assert(s_status.worst_kernel_cycles==720);
 hold_done=true;now+=80;assert(!service_turn(s)&&turn_count==2&&!s->source.leased);hold_done=false;
 fail_end=true;now+=80;assert(!strcmp(service_turn(s),"source_end")&&!s_status.locked&&!s->source.leased);fail_end=false;
 assert(!s->progress.ready&&s->detector.state==SFW_ACQUIRE);
 prime(s);mutate_context=true;now+=80;assert(!strcmp(service_turn(s),"source_changed")&&!s->source.leased);mutate_context=false;
 assert(!s->progress.ready&&s->detector.state==SFW_ACQUIRE);
 prime(s);overrun=true;now+=80;assert(!service_turn(s)&&s_status.locked&&!s->source.leased&&s_status.late_turns);overrun=false;
 assert(s_status.read_result==SRV_OK); /* A late, still-live read is valid. */
 prime(s);invalid_read=true;now+=80;
 assert(!strcmp(service_turn(s),"read_lifetime")&&s_status.read_result==SRV_INCOMPLETE);
 assert(!s_status.locked&&!s->progress.ready&&s->detector.state==SFW_ACQUIRE&&!s->source.leased);
 invalid_read=false;
 prime(s);long_overrun=true;now+=80;
 assert(!strcmp(service_turn(s),"read_lifetime")&&s_status.read_result==SRV_AMBIGUOUS);
 assert(!s_status.locked&&!s->progress.ready&&s->detector.state==SFW_ACQUIRE&&!s->source.leased);
 long_overrun=false;
 /* Two merged alarms cause one fresh source observation, never two kernel
  * replays or an unobserved camera clock. The next wake requests stop. */
 unsigned prior_turns=s_status.turns;
 assert(!stack_scans);
 notifications=2;cleanup_after=2;if(!setjmp(deleted))service_task(s);
 assert(!s_active&&!task_live&&!timer_live&&!timer_enabled&&!timer_started&&s_status.coalesced==1);
 assert(s_status.turns==prior_turns+1 && !strcmp(s_status.reason,"manual_stop"));
 assert(stack_scans==1); /* Only cleanup scans the stack, never a live turn. */
 /* Start again without reboot; manual stop frees task/timer/context again. */
 sync_service_toggle();assert(s_active);s=task_arg;sync_service_toggle();
 if(!setjmp(deleted))service_task(s);
 assert(!s_active&&!task_live&&!timer_live&&!strcmp(s_status.reason,"manual_stop"));
 puts("PASS actual W ISR/task/turn: notification-only ISR, budget/source rejection, balanced leases, stop/free/restart");
}
"""
class ServiceRuntimeTest(unittest.TestCase):
    def test_source_entry_scope(self):
        # Only the explicit manual service enters this direct reader. Existing
        # copies retain their own adapters and no boot path starts W.
        for path in (ROOT/'main').glob('*.c'):
            text=path.read_text()
            if 'transport_sync_source_begin(' in text:
                self.assertIn(path.name, ('transport.c','sync_service.c'))
            if 'sync_service_toggle(' in text:
                self.assertIn(path.name, ('console.c','sync_service.c'))
        self.assertIn("case 'W': sync_service_toggle(); break;", (ROOT/'main/console.c').read_text())
        bench=(ROOT/'main/transport.c').read_text().split('void transport_sync_bench(void)',1)[1]
        self.assertIn('if (sync_service_active())',bench.split('enum {',1)[0])

    def test_actual_source_adapter(self):
        import test_sync_transport_runtime as bridge
        source=(ROOT/'main/transport.c').read_text()
        prefix=bridge.PREFIX.replace('#include "sync_transport_probe.h"','#include "sync_transport_probe.h"\n#include "sync_service.h"')
        prefix='#define IRAM_ATTR\n'+prefix
        prefix=prefix.replace('} AHB_DMA;', 'struct {reg_t raw;} in_intr[3],out_intr[3]; } AHB_DMA;')
        prefix=prefix.replace('} PARL_IO;', 'struct {unsigned tx_fifo_rempty_int_raw,rx_fifo_wovf_int_raw;} int_raw; } PARL_IO;')
        prefix=prefix.replace('static uint64_t esp_timer_get_time(void) {timer+=2;return timer;}',
            'static void (*capture_tick)(void);\nstatic uint64_t esp_timer_get_time(void) {if(capture_tick)capture_tick();timer+=2;return timer;}')
        prefix+=r"""
static struct {struct {unsigned eof_overload;} state[2];} BITSCRAMBLER;
#define BITSCRAMBLER_DIR_TX 1
static uint16_t s_lut_live[1024];
static uint32_t s_control_epoch=3,s_gain_epoch=4,s_dma_faults,s_tx_empty,s_rx_overflow,s_bs_overload;
static uint32_t phy_generation(void){return 5;}
static unsigned cache_calls;
static void sync_dma_m2c(const void *p,size_t n){assert(p==s_raw_ring&&n==sizeof(s_raw_ring));++cache_calls;}
"""
        memory=source[source.index('static sync_memory_registers_t transport_memory_registers(void)'):source.index('static void transport_sync_probe_print(void)')]
        # Stable function-boundary anchor, independent of descriptive comment.
        raw=source[source.index('static raw_access_t s_raw_access;'):source.index('static bool copy_control_window_unleased')]
        adapter=source[source.index('/* Manual W detector:'):source.index('/* Sampling phase:')]
        init=bridge.SUFFIX[bridge.SUFFIX.index('static void initialize(void)'):bridge.SUFFIX.index('static void probe(void)')]
        body=r"""
static unsigned capture_ticks,crossings;
static void cross_done(void){
 ++capture_ticks;
 if(crossings && (capture_ticks==3 || capture_ticks==5)){
  unsigned i=capture_ticks==3?5:6;
  AHB_DMA.done_des_addr[0].in_done_des_addr.val=(uint32_t)(uintptr_t)&rx_desc[i];--crossings;
 }
}
static void cross_prefetch(void){
 ++capture_ticks;
 if(capture_ticks==3)
  AHB_DMA.channel[0].in.in_dscr_bf0.val=(uint32_t)(uintptr_t)&rx_desc[6];
}
int main(void){
 initialize();sync_service_source_t s={0};
 unsigned char before[sizeof(AHB_DMA)+sizeof(PCR)+sizeof(PARL_IO)];
 uint8_t raw_before[sizeof(s_raw_ring)];memcpy(raw_before,s_raw_ring,sizeof(raw_before));
 memcpy(before,&AHB_DMA,sizeof(AHB_DMA));memcpy(before+sizeof(AHB_DMA),&PCR,sizeof(PCR));
 memcpy(before+sizeof(AHB_DMA)+sizeof(PCR),&PARL_IO,sizeof(PARL_IO));
 assert(transport_sync_source_begin(&s,true)&&s.leased&&raw_access_readers(&s_raw_access)==1);
 assert(s.first.done==(uintptr_t)&rx_desc[4]&&s.first.bf0==(uintptr_t)&rx_desc[5]);
 assert(s.ring.ring==s_raw_ring&&s.ring.decoder_lut==s_lut_live&&!s.ring.program_verified);
 assert(cache_calls==(SOC_CACHE_INTERNAL_MEM_VIA_L1CACHE?1u:0u));
 assert(transport_sync_source_end(&s)&&!s.leased&&!raw_access_readers(&s_raw_access));
 assert(!memcmp(raw_before,s_raw_ring,sizeof(raw_before))&&!memcmp(before,&AHB_DMA,sizeof(AHB_DMA)));
 assert(!memcmp(before+sizeof(AHB_DMA),&PCR,sizeof(PCR)));
 assert(!memcmp(before+sizeof(AHB_DMA)+sizeof(PCR),&PARL_IO,sizeof(PARL_IO)));
 /* An independent BF0 edge needs no third capture. The lifetime validator
  * checks both pointers and reserves their union; neither is DONE evidence. */
 capture_ticks=0;capture_tick=cross_prefetch;
 assert(transport_sync_source_begin(&s,false)&&capture_ticks==4);
 assert(s.first.done==s.last.done&&s.first.bf0==(uintptr_t)&rx_desc[5]);
 assert(s.last.bf0==(uintptr_t)&rx_desc[6]);
 assert(transport_sync_source_end(&s)&&capture_ticks==8);
 AHB_DMA.channel[0].in.in_dscr_bf0.val=(uint32_t)(uintptr_t)&rx_desc[5];
 /* A single completion edge is retried in the same lease; two crossings
  * stay unstable rather than inventing a completed interval. */
 capture_ticks=0;crossings=1;capture_tick=cross_done;
 assert(transport_sync_source_begin(&s,false)&&capture_ticks==6);
 assert(s.first.done==(uintptr_t)&rx_desc[5]&&s.last.done==s.first.done);
 assert(transport_sync_source_end(&s));
 AHB_DMA.done_des_addr[0].in_done_des_addr.val=(uint32_t)(uintptr_t)&rx_desc[4];
 capture_ticks=0;crossings=2;
 assert(transport_sync_source_begin(&s,false)&&capture_ticks==6);
 sync_done_progress_t progress={0};sync_done_observation_t observation;
 assert(sync_done_observe(&progress,&s.layout,&s.first,&s.last,&observation)==SDP_UNSTABLE);
 assert(!progress.ready&&transport_sync_source_end(&s));
 capture_tick=NULL;
 assert(transport_sync_source_begin(&s,false));++s_gain_epoch;
 assert(!transport_sync_source_end(&s)&&!s.leased&&!raw_access_readers(&s_raw_access));
 s_menu_active=true;assert(!transport_sync_source_begin(&s,false)&&!s.leased);s_menu_active=false;
 PCR.bus_clk_update.val=1;assert(!transport_sync_source_begin(&s,false));PCR.bus_clk_update.val=0;
 AHB_DMA.in_intr[0].raw.val=4;assert(!transport_sync_source_begin(&s,false));AHB_DMA.in_intr[0].raw.val=0;
 rx_desc[3].next=&rx_desc[5];assert(!transport_sync_source_begin(&s,true)&&!s.leased);
 assert(!raw_access_readers(&s_raw_access)&&acquired==released);
 puts("PASS actual DONE source adapter: original-ring/LUT pointers, context/fault/clock/layout refusal, balanced READ lease, immutable registers/raw");
}
"""
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);path=folder/'adapter.c';path.write_text(prefix+memory+raw+adapter+'\n#undef printf\n'+init+body)
            binary=folder/'adapter'
            for cached in (0,1):
                subprocess.run([os.environ.get('CC','cc'),'-O2','-std=c11','-Wall','-Wextra','-Werror','-Wno-unused-function','-Wno-unused-variable','-no-pie',f'-DSOC_CACHE_INTERNAL_MEM_VIA_L1CACHE={cached}','-I',str(ROOT/'main'),str(path),'-o',str(binary)],check=True)
                subprocess.run([str(binary)],check=True)

    def test_actual_service(self):
        source=(ROOT/'main/sync_service.c').read_text()
        source=re.sub(r'^#include "(?:driver/|esp_|freertos/)[^\n]+\n','',source,flags=re.M)
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)
            (folder/'test.c').write_text(PREFIX+source+SUFFIX)
            binary=folder/'runtime'
            subprocess.run([os.environ.get('CC','cc'),'-O2','-std=c11','-Wall','-Wextra','-Werror','-I',str(ROOT/'main'),str(folder/'test.c'),'-o',str(binary)],check=True)
            subprocess.run([str(binary)],check=True)
if __name__=='__main__':unittest.main()

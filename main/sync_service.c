/* C5VRX by Twotoz/contributors: manually enabled original-IQ detector.
 * Measurement only: no synthetic/source/output writes. OFF at boot. */
#include "sync_service.h"
#include "phase8_gain_lut.h"
#include "driver/gptimer.h"
#include "esp_attr.h"
#include "esp_timer.h"
#include "esp_cpu.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

enum { SERVICE_US=80, SERVICE_EVALS=192, SERVICE_STACK=4096 };
typedef struct {
    sync_flywheel_t detector;
    sync_done_progress_t progress;
    sync_service_source_t source;
    uint8_t phase[256];
    gptimer_handle_t timer;
    TaskHandle_t task;
} service_t;
typedef struct {
    uint32_t turns, calls, acquire_calls, track_calls, max_us, max_cycles;
    uint32_t max_acquire_evals, max_track_evals, backlog_misses, max_backlog;
    uint32_t max_gap_us, max_capture_us, done_result, max_acquire_cycles, max_track_cycles;
    uint32_t rejected, coalesced, stack_free, acquisitions, vsyncs;
    uint32_t max_begin_cycles, max_observe_cycles, max_end_cycles;
    uint32_t prewarm_cycles;
    const char *prewarm_reason;
    unsigned state, standard;
    bool locked;
    const char *reason;
} service_status_t;
static service_status_t s_status={.reason="off"};
static volatile bool s_active, s_stop;
static TaskHandle_t s_task;

bool sync_service_active(void){return s_active;}
static bool IRAM_ATTR service_alarm(gptimer_handle_t timer,
    const gptimer_alarm_event_data_t *event, void *arg)
{
    (void)timer;(void)event;
    service_t *s=arg;
    BaseType_t wake=pdFALSE;
    vTaskNotifyGiveFromISR(s->task,&wake);
    return wake==pdTRUE;
}
/* Every failure ends the lease before returning. No failed turn contributes
 * a camera clock. Timing overrun discards the entire private detector. */
static const char *service_turn(service_t *s)
{
    int64_t started=esp_timer_get_time();
    uint32_t cycles=esp_cpu_get_cycle_count();
    if(!transport_sync_source_begin(&s->source,false))return "source_begin";
    uint32_t after_begin=esp_cpu_get_cycle_count();
    if(after_begin-cycles>s_status.max_begin_cycles)s_status.max_begin_cycles=after_begin-cycles;
    uint64_t span=s->source.last.last_us-s->source.first.first_us;
    if(span>s_status.max_capture_us)s_status.max_capture_us=(uint32_t)span;
    if(s->progress.ready) {
        uint64_t gap=s->source.last.last_us-s->progress.last_first_us;
        if(gap>s_status.max_gap_us)s_status.max_gap_us=(uint32_t)gap;
    }
    sync_done_observation_t o;
    sync_done_result_t result=sync_done_observe(&s->progress,&s->source.layout,
        &s->source.first,&s->source.last,&o);
    uint32_t after_observe=esp_cpu_get_cycle_count();
    if(after_observe-after_begin>s_status.max_observe_cycles)s_status.max_observe_cycles=after_observe-after_begin;
    s_status.done_result=(uint32_t)result;
    const char *why=NULL;
    if(result!=SDP_OK && result!=SDP_UNSTABLE)why="done_guard";
    if(result==SDP_OK && o.done_end-o.fresh_from>=DMA_PROGRESS_RING && o.newly_completed) {
        /* Prospective maximum head through an80us read, including a partly
         * transferred next descriptor. Original-source ledger rejects any
         * older input. Post-read timing/context validates this diagnostic. */
        uint64_t upper=o.done_end+s->progress.max_node_bytes+192u*SERVICE_US;
        s->source.ring.phase=s->phase;
        bool acquiring=s->detector.state==SFW_ACQUIRE;
        uint64_t old_cursor=s->detector.scan_pos;
        uint32_t kernel_start=esp_cpu_get_cycle_count();
        sfw_run(&s->detector,&s->source.ring,o.done_end,upper,0,false,SERVICE_EVALS);
        uint32_t kernel_cycles=esp_cpu_get_cycle_count()-kernel_start;
        uint32_t *cycle_max=acquiring?&s_status.max_acquire_cycles:&s_status.max_track_cycles;
        if(kernel_cycles>*cycle_max)*cycle_max=kernel_cycles;
        ++s_status.calls;
        if(acquiring)++s_status.acquire_calls;else ++s_status.track_calls;
        uint32_t *max=acquiring?&s_status.max_acquire_evals:&s_status.max_track_evals;
        if(s->detector.evals>*max)*max=s->detector.evals;
        if(acquiring && old_cursor && old_cursor+DMA_PROGRESS_RING/2u<o.done_end)
            ++s_status.backlog_misses;
        if(acquiring && s->detector.scan_pos<o.done_end) {
            uint64_t lag=o.done_end-s->detector.scan_pos;
            if(lag>s_status.max_backlog)s_status.max_backlog=(uint32_t)lag;
        }
    }
    uint32_t before_end=esp_cpu_get_cycle_count();
    bool ended=transport_sync_source_end(&s->source);
    uint32_t end_cycles=esp_cpu_get_cycle_count()-before_end;
    if(end_cycles>s_status.max_end_cycles)s_status.max_end_cycles=end_cycles;
    if(!ended)why="source_end";
    if(!raw_access_same(s->source.first.context,s->source.last.context) ||
        s->source.first.rx_clock!=s->source.last.rx_clock ||
        s->source.first.tx_clock!=s->source.last.tx_clock ||
        s->source.first.memory_rate_bound!=s->source.last.memory_rate_bound)why="source_changed";
    uint64_t elapsed=(uint64_t)(esp_timer_get_time()-started);
    uint32_t used=esp_cpu_get_cycle_count()-cycles;
    if(elapsed>s_status.max_us)s_status.max_us=(uint32_t)elapsed;
    if(used>s_status.max_cycles)s_status.max_cycles=used;
    if(elapsed>=SERVICE_US)why="service_overrun";
    ++s_status.turns;
    if(why) {++s_status.rejected;s_status.locked=false;s_status.standard=SFW_STD_UNKNOWN;return why;}
    s_status.state=s->detector.state;s_status.standard=sfw_standard(&s->detector);
    s_status.locked=sfw_locked(&s->detector);
    s_status.acquisitions=s->detector.acquisitions;s_status.vsyncs=s->detector.vsyncs;
    return NULL;
}
static void service_task(void *arg)
{
    service_t *s=arg;
    for(;;) {
        uint32_t notifications=ulTaskNotifyTake(pdTRUE,portMAX_DELAY);
        if(s_stop){s_status.reason="manual_stop";break;}
        if(notifications!=1){++s_status.coalesced;s_status.reason="coalesced";break;}
        const char *why=service_turn(s);
        if(why){s_status.reason=why;break;}
        s_status.stack_free=(uint32_t)uxTaskGetStackHighWaterMark(NULL);
    }
    /* Timer is stopped before task/context release; a final pending ISR can
     * only notify this still-live task. No diagnostic allocation survives OFF. */
    (void)gptimer_stop(s->timer);
    (void)gptimer_disable(s->timer);
    (void)gptimer_del_timer(s->timer);
    if(s->source.leased)(void)transport_sync_source_end(&s->source);
    s_status.stack_free=(uint32_t)uxTaskGetStackHighWaterMark(NULL);
    s_status.locked=false; /* Stopped results are historical, never a live lock. */
    free(s);
    s_task=NULL;s_active=false;
    vTaskDelete(NULL);
}
void sync_service_print(void)
{
    vTaskSuspendAll();service_status_t st=s_status;bool active=s_active;(void)xTaskResumeAll();
    printf("SYNC_SERVICE active=%u mode=read_only writes=0 reason=%s turns=%lu calls=%lu "
        "acquire_calls=%lu track_calls=%lu max_us=%lu max_cycles=%lu "
        "max_acq_evals=%lu max_track_evals=%lu budget=%u backlog_misses=%lu max_backlog=%lu "
        "rejected=%lu coalesced=%lu stack_free=%lu done_result=%lu capture_us=%lu gap_us=%lu "
        "max_acq_cycles=%lu max_track_cycles=%lu max_begin_cycles=%lu max_observe_cycles=%lu max_end_cycles=%lu state=%u standard=%u real_locked=%u "
        "acquisitions=%lu real_v=%lu prewarm_cycles=%lu prewarm_reason=%s prewarm_accepted=0 publication_proven=0 live_wcet_proven=0\n",
        active,st.reason,(unsigned long)st.turns,(unsigned long)st.calls,
        (unsigned long)st.acquire_calls,(unsigned long)st.track_calls,(unsigned long)st.max_us,
        (unsigned long)st.max_cycles,(unsigned long)st.max_acquire_evals,(unsigned long)st.max_track_evals,
        SERVICE_EVALS,(unsigned long)st.backlog_misses,(unsigned long)st.max_backlog,
        (unsigned long)st.rejected,(unsigned long)st.coalesced,(unsigned long)st.stack_free,
        (unsigned long)st.done_result,(unsigned long)st.max_capture_us,(unsigned long)st.max_gap_us,
        (unsigned long)st.max_acquire_cycles,(unsigned long)st.max_track_cycles,
        (unsigned long)st.max_begin_cycles,(unsigned long)st.max_observe_cycles,(unsigned long)st.max_end_cycles,
        st.state,st.standard,st.locked,(unsigned long)st.acquisitions,(unsigned long)st.vsyncs,
        (unsigned long)st.prewarm_cycles,st.prewarm_reason?st.prewarm_reason:"none");
}
void sync_service_toggle(void)
{
    if(s_active){s_stop=true;if(s_task)xTaskNotifyGive(s_task);sync_service_print();return;}
    service_t *s=calloc(1,sizeof(*s));
    if(!s){s_status.reason="allocation";sync_service_print();return;}
    s_active=true;s_stop=false;
    s_status=(service_status_t){.reason="starting"};
    memcpy(s->phase,c5vrx_phase8_gain_lut,sizeof(s->phase));
    sfw_init(&s->detector);
    bool source=transport_sync_source_begin(&s->source,true);
    if(source)source=transport_sync_source_end(&s->source);
    if(!source){s_status.reason="preflight_source";goto failed;}
    s->source.ring.phase=s->phase;
    sfw_run(&s->detector,&s->source.ring,0,0,0,false,0); /* prepare before timer */
    gptimer_config_t config={.clk_src=GPTIMER_CLK_SRC_DEFAULT,.direction=GPTIMER_COUNT_UP,
        .resolution_hz=1000000};
    if(gptimer_new_timer(&config,&s->timer)!=ESP_OK){s_status.reason="timer_create";goto failed;}
    gptimer_event_callbacks_t callbacks={.on_alarm=service_alarm};
    gptimer_alarm_config_t alarm={.alarm_count=SERVICE_US,.reload_count=0,.flags.auto_reload_on_alarm=true};
    if(gptimer_register_event_callbacks(s->timer,&callbacks,s)!=ESP_OK ||
        gptimer_set_alarm_action(s->timer,&alarm)!=ESP_OK || gptimer_enable(s->timer)!=ESP_OK) {
        s_status.reason="timer_setup";goto failed_timer;
    }
    if(xTaskCreate(service_task,"sync_read",SERVICE_STACK,s,5,&s->task)!=pdPASS) {
        s_status.reason="task_create";(void)gptimer_disable(s->timer);goto failed_timer;
    }
    s_task=s->task;
    /* Populate instruction/data caches before the periodic deadline starts.
     * A fresh progress epoch cannot admit a kernel call here. Discard ALL
     * resulting detector/progress/status state even if this cold pass was
     * timely: it is never camera-clock evidence. DMA and interrupts run. */
    uint32_t warm_start=esp_cpu_get_cycle_count();
    const char *warm_reason=service_turn(s);
    uint32_t warm_cycles=esp_cpu_get_cycle_count()-warm_start;
    sfw_init(&s->detector);
    memset(&s->progress,0,sizeof(s->progress));
    s_status=(service_status_t){.reason="running",.prewarm_cycles=warm_cycles,
        .prewarm_reason=warm_reason?warm_reason:"ok"};
    if(gptimer_start(s->timer)!=ESP_OK) {s_status.reason="timer_start";s_stop=true;xTaskNotifyGive(s_task);}
    sync_service_print();return;
failed_timer:
    (void)gptimer_del_timer(s->timer);
failed:
    if(s->source.leased)(void)transport_sync_source_end(&s->source);
    free(s);s_active=false;sync_service_print();
}

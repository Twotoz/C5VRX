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

/* Read-only task: target stack reports service_task32 + service_turn128 +
 * sfw_run192 + build_cells1056 bytes on its largest core call chain. Keep
 * room for transport/RTOS calls without demanding another4KiB heap block.
 * A future synthesis/publication task needs its own stack qualification. */
enum { SERVICE_US=80, SERVICE_EVALS=52, SERVICE_STACK=3072 };
typedef struct {
    sync_flywheel_t detector;
    sync_done_progress_t progress;
    sync_service_source_t source;
    uint8_t phase[256];
    gptimer_handle_t timer;
    TaskHandle_t task;
    uint64_t alarm_last_us;
    uint64_t wake_last_us;
    sync_read_extent_t reads;
} service_t;
typedef struct {
    uint32_t turns, calls, acquire_calls, track_calls, max_us, max_cycles;
    uint32_t max_acquire_evals, max_track_evals, backlog_misses, max_backlog;
    uint32_t max_gap_us, max_capture_us, done_result, max_acquire_cycles, max_track_cycles;
    uint32_t rejected, coalesced, stack_free, acquisitions, vsyncs;
    uint32_t max_begin_cycles, max_observe_cycles, max_end_cycles;
    uint32_t prewarm_cycles;
    const char *prewarm_reason;
    uint32_t alarms, alarm_min_us, alarm_max_us;
    uint32_t unstable, max_wake_gap_us;
    uint32_t worst_begin_cycles, worst_observe_cycles, worst_kernel_cycles;
    uint32_t worst_end_cycles, worst_other_cycles;
    uint32_t late_turns, read_result;
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
    uint64_t now=(uint64_t)esp_timer_get_time();
    if(s->alarm_last_us) {
        uint64_t dt=now-s->alarm_last_us;
        if(dt<=UINT32_MAX) {
            if(!s_status.alarm_min_us || dt<s_status.alarm_min_us)s_status.alarm_min_us=(uint32_t)dt;
            if(dt>s_status.alarm_max_us)s_status.alarm_max_us=(uint32_t)dt;
        }
    }
    s->alarm_last_us=now;
    if(s_status.alarms!=UINT32_MAX)++s_status.alarms;
    BaseType_t wake=pdFALSE;
    vTaskNotifyGiveFromISR(s->task,&wake);
    return wake==pdTRUE;
}
/* Validation need not occupy scarce IRAM: all source loads and paired ending
 * captures have completed before this helper. Candidate progress is private. */
static __attribute__((noinline)) sync_read_result_t finish_read(service_t *s,
    sync_done_progress_t *candidate, const sync_done_capture_t *before_first,
    const sync_done_capture_t *before_last, const sync_done_observation_t *before, bool ended)
{
    if(!ended)return SRV_LEASE;
    sync_done_observation_t after;
    if(sync_done_observe(candidate,&s->source.layout,&s->source.last,
        &s->source.finish_last,&after)!=SDP_OK)return SRV_METADATA;
    return sync_read_validate(&s->reads,&s->source.layout,before_first,before_last,before,
        &s->source.last,&s->source.finish_last,&after,ended);
}
/* Every failure ends the lease and discards ALL private camera state. No
 * failed turn contributes evidence. 80us remains the cadence, not permission
 * to accept a source load whose actual ending lifetime check failed. */
static const char *IRAM_ATTR service_turn(service_t *s)
{
    int64_t started=esp_timer_get_time();
    uint32_t cycles=esp_cpu_get_cycle_count();
    if(!transport_sync_source_begin(&s->source,false))return "source_begin";
    sync_done_capture_t before_first=s->source.first,before_last=s->source.last;
    sync_done_progress_t candidate=s->progress;
    s->reads=(sync_read_extent_t){0};
    uint32_t after_begin=esp_cpu_get_cycle_count();
    if(after_begin-cycles>s_status.max_begin_cycles)s_status.max_begin_cycles=after_begin-cycles;
    uint64_t span=s->source.last.last_us-s->source.first.first_us;
    if(span>s_status.max_capture_us)s_status.max_capture_us=(uint32_t)span;
    if(s->progress.ready) {
        uint64_t gap=s->source.last.last_us-s->progress.last_first_us;
        if(gap>s_status.max_gap_us)s_status.max_gap_us=(uint32_t)gap;
    }
    sync_done_observation_t o;
    sync_done_result_t result=sync_done_observe(&candidate,&s->source.layout,
        &s->source.first,&s->source.last,&o);
    uint32_t after_observe=esp_cpu_get_cycle_count();
    if(after_observe-after_begin>s_status.max_observe_cycles)s_status.max_observe_cycles=after_observe-after_begin;
    s_status.done_result=(uint32_t)result;
    if(result==SDP_UNSTABLE && s_status.unstable!=UINT32_MAX)++s_status.unstable;
    const char *why=NULL;
    uint32_t measured_kernel=0;
    if(result!=SDP_OK && result!=SDP_UNSTABLE)why="done_guard";
    if(result==SDP_OK && o.done_end-o.fresh_from>=DMA_PROGRESS_RING) {
        /* Drain the cursor on every alarm, even if DONE has not advanced.
         * The kernel's exclusive completed-data ceiling still applies; no
         * queued event or repeated DONE invents samples/camera time. 52
         * coarse probes per80us cover40.95MS/s, while limiting work to new
         * 4092-byte completions made64 probes arrive in ~102us bursts. */
        s->source.ring.phase=s->phase;
        s->source.ring.read_extent=&s->reads;
        bool acquiring=s->detector.state==SFW_ACQUIRE;
        uint64_t old_cursor=s->detector.scan_pos;
        uint32_t kernel_start=esp_cpu_get_cycle_count();
        /* Keep the completed-data ceiling; ending metadata will validate
         * the full actual read envelope before any camera state is used. */
        sfw_run(&s->detector,&s->source.ring,o.done_end,o.done_end,0,false,SERVICE_EVALS);
        uint32_t kernel_cycles=esp_cpu_get_cycle_count()-kernel_start;
        measured_kernel=kernel_cycles;
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
    if(result==SDP_OK) {
        sync_read_result_t read_result=finish_read(s,&candidate,&before_first,&before_last,&o,ended);
        s_status.read_result=(uint32_t)read_result;
        if(read_result!=SRV_OK && !why)why="read_lifetime";
    } else if(result==SDP_UNSTABLE && ended) {
        /* No kernel/read happened: a stable ending pair can refresh DONE
         * progress, without supplying any new camera votes. */
        sync_done_observation_t after;
        if(sync_done_observe(&candidate,&s->source.layout,&s->source.last,
            &s->source.finish_last,&after)!=SDP_OK && !why)why="done_guard";
    }
    uint64_t elapsed=(uint64_t)(esp_timer_get_time()-started);
    uint32_t used=esp_cpu_get_cycle_count()-cycles;
    if(elapsed>s_status.max_us)s_status.max_us=(uint32_t)elapsed;
    if(used>s_status.max_cycles) {
        /* Components of this ONE slowest turn, not unrelated maxima. */
        uint32_t begin=after_begin-cycles,observe=after_observe-after_begin;
        s_status.max_cycles=used;
        s_status.worst_begin_cycles=begin;s_status.worst_observe_cycles=observe;
        s_status.worst_kernel_cycles=measured_kernel;s_status.worst_end_cycles=end_cycles;
        s_status.worst_other_cycles=used-begin-observe-measured_kernel-end_cycles;
    }
    if(elapsed>=SERVICE_US && s_status.late_turns!=UINT32_MAX)++s_status.late_turns;
    ++s_status.turns;
    if(why) {
        ++s_status.rejected;s_status.locked=false;s_status.standard=SFW_STD_UNKNOWN;
        sfw_init(&s->detector);sync_done_restart(&s->progress);
        return why;
    }
    s->progress=candidate;
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
        if(!notifications){s_status.reason="notification_empty";break;}
        uint64_t wake_now=(uint64_t)esp_timer_get_time();
        if(s->wake_last_us && wake_now>=s->wake_last_us) {
            uint64_t gap=wake_now-s->wake_last_us;
            if(gap<=UINT32_MAX && gap>s_status.max_wake_gap_us)s_status.max_wake_gap_us=(uint32_t)gap;
        }
        s->wake_last_us=wake_now;
        /* A delayed wake is not evidence of a lost DMA epoch. Count merged
         * alarms, then let actual DONE timestamps/hidden-lap/context checks
         * decide whether this newest turn is usable. Never replay one
         * detector turn per queued notification against the same bytes. */
        if(notifications>1 && s_status.coalesced!=UINT32_MAX)++s_status.coalesced;
        const char *why=service_turn(s);
        if(why){s_status.reason=why;break;}
        /* High-water measurement scans the untouched stack byte by byte.
         * Doing it after every 80us turn delays the next source capture,
         * outside service_turn's measured interval. Measure only at exit. */
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
        "acquisitions=%lu real_v=%lu prewarm_cycles=%lu prewarm_reason=%s alarms=%lu alarm_min_us=%lu alarm_max_us=%lu unstable=%lu wake_gap_us=%lu "
        "worst_begin_cycles=%lu worst_observe_cycles=%lu worst_kernel_cycles=%lu worst_end_cycles=%lu worst_other_cycles=%lu "
        "late_turns=%lu read_result=%lu prewarm_accepted=0 publication_proven=0 live_wcet_proven=0\n",
        active,st.reason,(unsigned long)st.turns,(unsigned long)st.calls,
        (unsigned long)st.acquire_calls,(unsigned long)st.track_calls,(unsigned long)st.max_us,
        (unsigned long)st.max_cycles,(unsigned long)st.max_acquire_evals,(unsigned long)st.max_track_evals,
        SERVICE_EVALS,(unsigned long)st.backlog_misses,(unsigned long)st.max_backlog,
        (unsigned long)st.rejected,(unsigned long)st.coalesced,(unsigned long)st.stack_free,
        (unsigned long)st.done_result,(unsigned long)st.max_capture_us,(unsigned long)st.max_gap_us,
        (unsigned long)st.max_acquire_cycles,(unsigned long)st.max_track_cycles,
        (unsigned long)st.max_begin_cycles,(unsigned long)st.max_observe_cycles,(unsigned long)st.max_end_cycles,
        st.state,st.standard,st.locked,(unsigned long)st.acquisitions,(unsigned long)st.vsyncs,
        (unsigned long)st.prewarm_cycles,st.prewarm_reason?st.prewarm_reason:"none",
        (unsigned long)st.alarms,(unsigned long)st.alarm_min_us,(unsigned long)st.alarm_max_us,
        (unsigned long)st.unstable,(unsigned long)st.max_wake_gap_us,
        (unsigned long)st.worst_begin_cycles,(unsigned long)st.worst_observe_cycles,
        (unsigned long)st.worst_kernel_cycles,(unsigned long)st.worst_end_cycles,(unsigned long)st.worst_other_cycles,
        (unsigned long)st.late_turns,(unsigned long)st.read_result);
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

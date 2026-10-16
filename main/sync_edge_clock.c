/* C5VRX by Twotoz/contributors. GPIO2 -> shared GPIO filter -> RMT and
 * ETM START_COUNT. A private raw marker starts the timer before enabling the
 * GPIO filter. RMT has a fixed private filter; its offset is measured by the
 * observer's second marker, never assumed to be the ETM input tap.
 * Repeated START_COUNT enables the counter; never reloads.
 * All allocation/control is outside ISR. No DAC, IQ, LUT or PHY writes. */
#include "sync_edge_clock.h"
#include <string.h>
#include "esp_attr.h"
#include "driver/gpio_filter.h"
#include "driver/gpio_etm.h"
#include "driver/gptimer.h"
#include "driver/gptimer_etm.h"
#include "esp_etm.h"
#include "soc/pcr_struct.h"

typedef struct {
    gpio_glitch_filter_handle_t filter;
    gptimer_handle_t timer;
    esp_etm_event_handle_t edge;
    esp_etm_task_handle_t start;
    esp_etm_channel_handle_t link;
    bool busy, quarantined, filter_on, timer_on, link_on;
    uint32_t filter_ns;
    const char *error;
    sync_edge_alarm_fn alarm;
    void *alarm_arg;
} sec_t;
static sec_t s_clock;

static bool IRAM_ATTR sec_alarm(gptimer_handle_t timer,
    const gptimer_alarm_event_data_t *event,void *opaque)
{
    (void)timer;
    sec_t *s=opaque;
    sync_edge_alarm_fn callback=s->alarm;
    if(!s->busy || s->quarantined || !s->link_on || !callback)return false;
    return callback(event->count_value,s->alarm_arg);
}

const char *sync_edge_clock_error(void)
{ return s_clock.error ? s_clock.error : "none"; }
uint32_t sync_edge_clock_filter_ns(void) { return s_clock.filter_ns; }

/* Stop the hardware route BEFORE touching its endpoints. On any teardown
 * failure keep remaining objects owned until reboot; never reuse a timer
 * that an old ETM connection might still start. */
bool sync_edge_clock_close(void)
{
    sec_t *s=&s_clock;
    if(s->quarantined)return false;
    if(!s->busy)return true;
    s->alarm=NULL;
    __sync_synchronize();
#define SEC_CLEAN(call, label) do { if((call)!=ESP_OK) { \
    s->error=(label);s->quarantined=true;return false; } } while(0)
    if(s->link_on) {
        SEC_CLEAN(esp_etm_channel_disable(s->link),"link_disable");s->link_on=false;
    }
    if(s->link) {SEC_CLEAN(esp_etm_del_channel(s->link),"link_delete");s->link=NULL;}
    if(s->timer_on) {
        /* ETM may have enabled counting while the SDK FSM is still ENABLE.
         * SDK start() synchronizes its FSM without resetting the counter;
         * only then can stop() actually disable the hardware counter. */
        SEC_CLEAN(gptimer_start(s->timer),"timer_fsm_start");
        SEC_CLEAN(gptimer_stop(s->timer),"timer_stop");
        SEC_CLEAN(gptimer_disable(s->timer),"timer_disable");s->timer_on=false;
    }
    if(s->edge) {SEC_CLEAN(esp_etm_del_event(s->edge),"edge_delete");s->edge=NULL;}
    if(s->start) {SEC_CLEAN(esp_etm_del_task(s->start),"task_delete");s->start=NULL;}
    if(s->timer) {SEC_CLEAN(gptimer_del_timer(s->timer),"timer_delete");s->timer=NULL;}
    if(s->filter_on) {
        SEC_CLEAN(gpio_glitch_filter_disable(s->filter),"filter_disable");s->filter_on=false;
    }
    if(s->filter) {SEC_CLEAN(gpio_del_glitch_filter(s->filter),"filter_delete");s->filter=NULL;}
    s->busy=false;
#undef SEC_CLEAN
    return true;
}

bool sync_edge_clock_prepare(void)
{
    sec_t *s=&s_clock;
    if(s->busy || s->quarantined)return false;
    memset(s,0,sizeof(*s));s->busy=true;
    /* Preserve the CURRENT shared IOMUX clock, including its frequency.
     * The SDK filter driver writes the same selector; never switch the
     * other live GPIO consumers to another clock as a side effect. */
    uint32_t clock_before=PCR.iomux_clk_conf.val;
    unsigned source=PCR.iomux_clk_conf.iomux_func_clk_sel;
    if(source!=0u && source!=2u){s->error="iomux_clock";goto fail;}
    s->filter_ns=source==0u?1200u:750u;
    gpio_flex_glitch_filter_config_t filter={.gpio_num=2,
        .clk_src=(glitch_filter_clock_source_t)(source==0u?SOC_MOD_CLK_XTAL:SOC_MOD_CLK_PLL_F80M),
        .window_width_ns=s->filter_ns,.window_thres_ns=s->filter_ns};
    const gptimer_config_t timer={.clk_src=GPTIMER_CLK_SRC_DEFAULT,
        .direction=GPTIMER_COUNT_UP,.resolution_hz=20000000u,.intr_priority=3};
    const gpio_etm_event_config_t edge={.edge=GPIO_ETM_EVENT_EDGE_ANY};
    const gptimer_etm_task_config_t start={.task_type=GPTIMER_ETM_TASK_START_COUNT};
    const esp_etm_channel_config_t link={0};
#define SEC_SETUP(call, label) do { if((call)!=ESP_OK) {s->error=(label);goto fail;} } while(0)
    SEC_SETUP(gpio_new_flex_glitch_filter(&filter,&s->filter),"filter_create");
    if(PCR.iomux_clk_conf.val!=clock_before){s->error="iomux_clock_changed";goto fail;}
    SEC_SETUP(gptimer_new_timer(&timer,&s->timer),"timer_create");
    const gptimer_event_callbacks_t callbacks={.on_alarm=sec_alarm};
    SEC_SETUP(gptimer_register_event_callbacks(s->timer,&callbacks,s),"timer_callback");
    SEC_SETUP(gptimer_enable(s->timer),"timer_enable");s->timer_on=true;
    SEC_SETUP(gptimer_set_raw_count(s->timer,0),"timer_zero");
    SEC_SETUP(gpio_new_etm_event(&edge,&s->edge),"edge_create");
    SEC_SETUP(gpio_etm_event_bind_gpio(s->edge,2),"edge_bind");
    SEC_SETUP(gptimer_new_etm_task(s->timer,&start,&s->start),"task_create");
    SEC_SETUP(esp_etm_new_channel(&link,&s->link),"link_create");
    SEC_SETUP(esp_etm_channel_connect(s->link,s->edge,s->start),"link_connect");
#undef SEC_SETUP
    return true;
fail:
    (void)sync_edge_clock_close();
    return false;
}

bool sync_edge_clock_arm(void)
{
    sec_t *s=&s_clock;
    if(!s->busy || s->quarantined || !s->link || s->link_on)return false;
    if(esp_etm_channel_enable(s->link)!=ESP_OK){s->error="link_enable";return false;}
    s->link_on=true;return true;
}

bool IRAM_ATTR sync_edge_clock_read(uint64_t *ticks)
{
    sec_t *s=&s_clock;
    if(!ticks || !s->busy || s->quarantined || !s->timer_on || !s->link_on)return false;
    if(gptimer_get_raw_count(s->timer,ticks)!=ESP_OK){s->error="timer_read";return false;}
    return true;
}

bool sync_edge_clock_enable_filter(void)
{
    sec_t *s=&s_clock;
    if(!s->busy || s->quarantined || !s->link_on || !s->filter || s->filter_on)return false;
    if(gpio_glitch_filter_enable(s->filter)!=ESP_OK){s->error="filter_enable";return false;}
    s->filter_on=true;return true;
}

bool sync_edge_clock_disable_filter(void)
{
    sec_t *s=&s_clock;
    if(!s->busy || s->quarantined || !s->filter || !s->filter_on)return false;
    if(gpio_glitch_filter_disable(s->filter)!=ESP_OK){s->error="filter_pause";return false;}
    s->filter_on=false;return true;
}

bool IRAM_ATTR sync_edge_clock_alarm_off(void)
{
    sec_t *s=&s_clock;
    if(!s->busy || s->quarantined || !s->timer_on)return false;
    if(gptimer_set_alarm_action(s->timer,NULL)!=ESP_OK){s->error="alarm_off";return false;}
    return true;
}

bool sync_edge_clock_alarm_bind(sync_edge_alarm_fn callback,void *arg)
{
    sec_t *s=&s_clock;
    if(!s->busy || s->quarantined || !s->link_on)return false;
    /* Single core: task cannot run while its alarm ISR is executing. Clear
     * authorization before driver work; a pending interrupt then does nothing. */
    s->alarm=NULL;
    __sync_synchronize();
    if(!sync_edge_clock_alarm_off())return false;
    s->alarm_arg=arg;
    __sync_synchronize();
    s->alarm=callback;
    return true;
}

bool IRAM_ATTR sync_edge_clock_alarm_at(uint64_t ticks)
{
    sec_t *s=&s_clock;
    if(!s->busy || s->quarantined || !s->link_on || !s->alarm)return false;
    const gptimer_alarm_config_t alarm={.alarm_count=ticks};
    if(gptimer_set_alarm_action(s->timer,&alarm)!=ESP_OK){s->error="alarm_at";return false;}
    return true;
}

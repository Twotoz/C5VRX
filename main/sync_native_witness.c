/* C5VRX by Twotoz/contributors. Native mirror GPIO2 only: never gate the DAC,
 * alter IQ/LUT/DMA, change GPIO routing, or attach an ETM CAPTURE task. The
 * existing clock read explicitly triggers its own capture; sharing that latch
 * with a separate edge-CAPTURE route would destroy timestamp provenance. */
#include "sync_native_witness.h"
#include "sync_edge_clock.h"
#include "esp_intr_alloc.h"
#include "freertos/FreeRTOS.h"
#include "soc/gpio_reg.h"
#include "soc/io_mux_reg.h"
#include "soc/interrupts.h"
#include "soc/soc.h"
#include <string.h>
#include <stdlib.h>

#define SNW_PIN_MASK (UINT32_C(1)<<2)
#define SNW_INT_MASK (GPIO_PIN2_INT_TYPE_M|GPIO_PIN2_INT_ENA_M)
#define SNW_MATRIX_REG (GPIO_FUNC0_OUT_SEL_CFG_REG+8u)

typedef struct {
    intr_handle_t interrupt;
    snw_snapshot_t view;
    uint64_t first,last,deadline;
    uint32_t min_width,max_width,saved_pin,saved_pad,saved_matrix;
    bool busy,quarantined;
} snw_t;
/* Allocate only after startup: unused diagnostics must not consume the SRAM
 * needed to create the receiver's control task. Retain on teardown failure. */
static snw_t *s_witness;
static portMUX_TYPE s_lock=portMUX_INITIALIZER_UNLOCKED;

static void stop_window(snw_t *s,snw_status_t why)
{
    REG_WRITE(GPIO_PIN2_REG,REG_READ(GPIO_PIN2_REG)&~SNW_INT_MASK);
    REG_WRITE(GPIO_STATUS_W1TC_REG,SNW_PIN_MASK);
    s->view.status=why;
}

static void witness_irq(void *opaque)
{
    snw_t *s=opaque;
    if(!(REG_READ(GPIO_STATUS_REG)&SNW_PIN_MASK))return;
    REG_WRITE(GPIO_STATUS_W1TC_REG,SNW_PIN_MASK);
    if(!s->busy || s->view.status!=SNW_ARMED){
        REG_WRITE(GPIO_PIN2_REG,REG_READ(GPIO_PIN2_REG)&~SNW_INT_MASK);return;
    }
    /* At most one observation per IRQ; no drain-until-quiet noise loop. */
    bool high=(REG_READ(GPIO_IN_REG)&SNW_PIN_MASK)!=0;
    ++s->view.edges;
    uint64_t now;
    if(!sync_edge_clock_read(&now) || now<s->view.last_tick){
        s->view.clock_fault=true;stop_window(s,SNW_CLOCK_FAULT);return;
    }
    s->view.last_tick=now;
    if(now>s->deadline || (!s->view.have_falling && now>s->last)){
        stop_window(s,SNW_EXPIRED);return;
    }
    if(now<s->first){++s->view.dropped;}
    else if(!s->view.have_falling){
        if(high)++s->view.dropped;
        else{s->view.falling=now;s->view.have_falling=true;}
    }else if(!high){
        /* A second LOW without an observed HIGH means a collapsed/missed
         * transition. Do not publish a width assembled across that ambiguity. */
        ++s->view.dropped;s->view.rate_fault=true;stop_window(s,SNW_RATE_FAULT);return;
    }else{
        s->view.rising=now;
        uint64_t width=now-s->view.falling;
        if(width>=s->min_width && width<=s->max_width){
            s->view.low_width=(uint32_t)width;s->view.completed_low=true;
            ++s->view.completed;stop_window(s,SNW_COMPLETE);return;
        }
        ++s->view.dropped;s->view.have_falling=false;
    }
    if(s->view.edges>=SNW_EDGE_LIMIT){
        s->view.rate_fault=true;stop_window(s,SNW_RATE_FAULT);
    }
}

bool sync_native_witness_begin(void)
{
    portENTER_CRITICAL(&s_lock);
    if(s_witness){portEXIT_CRITICAL(&s_lock);return false;}
    portEXIT_CRITICAL(&s_lock);
    snw_t *s=calloc(1,sizeof(*s));
    if(!s)return false;
    portENTER_CRITICAL(&s_lock);
    if(s_witness){portEXIT_CRITICAL(&s_lock);free(s);return false;}
    /* Reserve the singleton before a clock/interrupt API may yield. */
    s_witness=s;s->busy=true;portEXIT_CRITICAL(&s_lock);
    uint64_t now;
    if(!sync_edge_clock_read(&now))goto fail;
    uint32_t pin=REG_READ(GPIO_PIN2_REG);
    if((pin&SNW_INT_MASK) || (REG_READ(GPIO_STATUS_REG)&SNW_PIN_MASK)){
        goto fail;
    }
    /* This is an exclusive nonshared peripheral interrupt. Refuse configured
     * peers too, rather than acknowledging another pin's source accidentally. */
    for(unsigned j=0;j<29u;++j)
        if(REG_READ(GPIO_PIN2_REG+4u*j-8u)&GPIO_PIN2_INT_ENA_M)goto fail;
    s->saved_pin=pin;s->saved_pad=REG_READ(IO_MUX_GPIO2_REG);
    s->saved_matrix=REG_READ(SNW_MATRIX_REG);
    memset(&s->view,0,sizeof(s->view));s->view.last_tick=now;
    /* The existing sync_edge_clock_read is not yet an all-IRAM call chain.
     * SDK cache masking is essential; do not add ESP_INTR_FLAG_IRAM here. */
    if(esp_intr_alloc(ETS_GPIO_INTR_SOURCE,ESP_INTR_FLAG_LEVEL3,
            witness_irq,s,&s->interrupt)!=ESP_OK)goto fail;
    return true;
fail:
    portENTER_CRITICAL(&s_lock);s_witness=NULL;portEXIT_CRITICAL(&s_lock);
    free(s);return false;
}

bool sync_native_witness_enable_window(uint64_t expected,uint32_t early,
    uint32_t late,uint32_t min_width,uint32_t max_width)
{
    if(!min_width || min_width>max_width || expected<early ||
        expected>UINT64_MAX-late || expected+late>UINT64_MAX-max_width)return false;
    portENTER_CRITICAL(&s_lock);
    snw_t *s=s_witness;
    uint64_t now;
    if(!s || !s->busy || s->quarantined || s->view.status==SNW_ARMED ||
        s->view.rate_fault || s->view.clock_fault){
        portEXIT_CRITICAL(&s_lock);return false;
    }
    if(!sync_edge_clock_read(&now) || now<s->view.last_tick){
        s->view.clock_fault=true;stop_window(s,SNW_CLOCK_FAULT);
        portEXIT_CRITICAL(&s_lock);return false;
    }
    if(now>expected+late){portEXIT_CRITICAL(&s_lock);return false;}
    s->first=expected-early;s->last=expected+late;s->deadline=s->last+max_width;
    s->min_width=min_width;s->max_width=max_width;
    s->view.expected=expected;s->view.falling=s->view.rising=0;
    s->view.low_width=s->view.edges=s->view.dropped=0;
    s->view.have_falling=s->view.completed_low=false;s->view.last_tick=now;
    ++s->view.windows;s->view.status=SNW_ARMED;
    REG_WRITE(GPIO_STATUS_W1TC_REG,SNW_PIN_MASK);
    REG_WRITE(GPIO_PIN2_REG,(s->saved_pin&~SNW_INT_MASK)|
        (3u<<GPIO_PIN2_INT_TYPE_S)|(1u<<GPIO_PIN2_INT_ENA_S));
    portEXIT_CRITICAL(&s_lock);return true;
}

bool sync_native_witness_disable_window(void)
{
    portENTER_CRITICAL(&s_lock);snw_t *s=s_witness;
    if(!s || !s->busy){portEXIT_CRITICAL(&s_lock);return false;}
    if(s->view.status==SNW_ARMED)stop_window(s,SNW_EXPIRED);
    portEXIT_CRITICAL(&s_lock);return true;
}

bool sync_native_witness_snapshot(snw_snapshot_t *out)
{
    if(!out)return false;
    uint64_t now;
    portENTER_CRITICAL(&s_lock);
    snw_t *s=s_witness;
    if(!s || !s->busy){portEXIT_CRITICAL(&s_lock);return false;}
    if(s->view.status==SNW_ARMED){
        bool clock_ok=sync_edge_clock_read(&now);
        if(!clock_ok || now<s->view.last_tick){s->view.clock_fault=true;stop_window(s,SNW_CLOCK_FAULT);}
        else if(now>s->deadline)stop_window(s,SNW_EXPIRED);
    }
    *out=s->view;portEXIT_CRITICAL(&s_lock);return true;
}

bool sync_native_witness_end(void)
{
    portENTER_CRITICAL(&s_lock);snw_t *s=s_witness;
    if(!s){portEXIT_CRITICAL(&s_lock);return true;}
    if(s->quarantined){portEXIT_CRITICAL(&s_lock);return false;}
    if(!s->busy){portEXIT_CRITICAL(&s_lock);return true;}
    if(s->view.status==SNW_ARMED)stop_window(s,SNW_EXPIRED);
    REG_WRITE(GPIO_PIN2_REG,s->saved_pin);
    REG_WRITE(GPIO_STATUS_W1TC_REG,SNW_PIN_MASK);
    portEXIT_CRITICAL(&s_lock);
    if(esp_intr_free(s->interrupt)!=ESP_OK){s->quarantined=true;return false;}
    s->interrupt=NULL;
    bool same=REG_READ(GPIO_PIN2_REG)==s->saved_pin &&
        REG_READ(IO_MUX_GPIO2_REG)==s->saved_pad && REG_READ(SNW_MATRIX_REG)==s->saved_matrix;
    portENTER_CRITICAL(&s_lock);s_witness=NULL;portEXIT_CRITICAL(&s_lock);
    free(s);return same;
}

/* C5VRX by Twotoz/contributors. Camera-period increments run entirely in
 * the alarm ISR: no task refill or flash planner is needed during holdover.
 * Vertical tips separated by4.7us share a bounded ISR, exposing real DAC data
 * in every gap. No hard WCET or physical monitor-lock claim follows. */
#include "sync_live_output.h"
#include "sync_edge_clock.h"
#include "sync_live_clock.h"
#include "sync_phase_guard.h"
#include "sync_clock_pll.h"
#include "esp_attr.h"
#include "esp_cpu.h"
#include "esp_heap_caps.h"
#include "esp_private/esp_clk.h"
#include "soc/gpio_reg.h"
#include "soc/io_mux_reg.h"
#include "soc/soc.h"
#include <stdlib.h>
#include <string.h>

#define SLO_MASK UINT32_C(0x01801b00)
#define SLO_LEAD 240u
#define SLO_LATE 160u
/* Flag path: at most 1us late and at least 1us of useful tip remaining.
 * All widths terminate on the original absolute end, including late arrivals. */
#define SLO_FLAG_LATE 40u
#define SLO_FLAG_MIN 20u
#define SLO_PHASE_WINDOW 80u
#define SLO_BATCH_CYCLES (224u*240u)
#define SLO_CATCHUP_LEAD 80u /*4us leaves driver/ISR epilogue time before retry*/
/* Camera presence, independent of the output grid: native H tips spaced one
 * to four line periods (+-0.6us at 20MHz). Repairs only fill real gaps; a
 * grid that drifted off the camera can never add a second sync while the
 * camera's own pulses arrive (operator: sync lost after a while, clocks
 * drifting apart; board 2026-10-18, repaired>suppressed on a strong camera).
 * Native pulses reach the task in batches, so the hold covers one field. */
#define SLO_CAMERA_TOL 12u
#define SLO_CAMERA_HOLD 400000u /* 20ms */
typedef struct {uint64_t start;uint16_t width;uint8_t kind;uint16_t index;uint64_t half;} slo_tip_t;
typedef struct {
    sync_live_gate_t *planner;
    slc_t clock;
    slo_tip_t tip;
    uint32_t saved_mux[6],saved_pad[6],generation;
    slo_live_fn live;slo_native_fn native;void *native_arg;
    slo_source_fn source;
    uint32_t source_epoch;
    uint64_t rise,alarm_tick;
    bool flag,source_seen,low_seen,last_high,rise_valid;
    bool native_authority,native_v_fresh,native_inverted;
    volatile uint32_t publication;
    uint32_t applied;
    slc_t pending_clock;
    slo_tip_t pending_tip;
    slg_standard_t standard;
    uint8_t pending_mode;
    int32_t pending_shift;
    uint32_t pending_whole;
    uint16_t pending_increment,pending_denominator;
    /* Long-baseline camera period; 0 until the PLL has one. */
    scp_t pll;
    uint64_t pll_period_q;
    /* Task-owned camera-phase guard; the ISR reads only `yield`. */
    spg_t guard;
    uint64_t guard_after,last_broad;
    volatile bool yield;
    /* Task writes; the ISR needs both the 32-bit tick and the task's
     * freshness flag, so a torn or wrapped tick never reads as present. */
    uint64_t last_h_tick,camera_tick;
    volatile uint32_t camera_tick32;
    volatile bool camera_fresh;
    slo_status_t status;
    bool saved,configured,quarantined;
} slo_t;
enum {SLO_PUB_SLEW=1,SLO_PUB_TAKEOVER,SLO_PUB_JUMP_H,SLO_PUB_JUMP_V,SLO_PUB_PERIOD};
static slo_t *s_output;
static DRAM_ATTR volatile unsigned s_output_phase;
void sync_live_output_phase(unsigned phase)
{ s_output_phase=phase<6u?phase:0u; }
static const unsigned pins[6]={23,24,11,12,8,9};
static const uint32_t pads[6]={IO_MUX_GPIO23_REG,IO_MUX_GPIO24_REG,IO_MUX_GPIO11_REG,
    IO_MUX_GPIO12_REG,IO_MUX_GPIO8_REG,IO_MUX_GPIO9_REG};

static void IRAM_ATTR fail(slo_t *s,uint32_t code)
{
    /* Never energize pads here if ownership has already changed. Normal
     * pulse completion always enables them before any next decision. */
    s->status.fault=code;s->status.active=false;
    (void)sync_edge_clock_alarm_off();
}

/* GPIO2 mirrors internal DATA0 even while DAC OE is suppressed. An active
 * sync level already present at admission cannot become evidence until a new
 * inactive/active transition is seen. Every source epoch, marker or invalidation clears it. */
static bool IRAM_ATTR flag_native(slo_t *s,uint64_t now,const slo_tip_t *tip)
{
    if(!s->native_authority || (tip->kind!=SLG_H && !s->native_v_fresh))return false;
    uint32_t epoch=0;
    if(!s->source(s->native_arg,&epoch)) {
        s->source_seen=false;s->low_seen=false;s->rise_valid=false;
        return false;
    }
    bool high=((REG_READ(GPIO_IN_REG)&(1u<<2))!=0)==s->native_inverted;
    if(!s->source_seen || epoch!=s->source_epoch) {
        s->source_seen=true;s->source_epoch=epoch;
        s->low_seen=!high;s->last_high=high;s->rise_valid=false;
        return false;
    }
    if(!high)s->low_seen=true;
    if(high && !s->last_high && s->low_seen) {
        s->rise=now;s->rise_valid=true;s->low_seen=false;
    }
    s->last_high=high;
    if(!high || !s->rise_valid)return false;
    uint64_t distance=s->rise>=tip->start?s->rise-tip->start:tip->start-s->rise;
    return distance<=SLO_PHASE_WINDOW && now<tip->start+tip->width;
}

static bool IRAM_ATTR output_alarm(uint64_t count,void *arg)
{
    slo_t *s=arg;
    uint32_t batch_start=esp_cpu_get_cycle_count(),n=0;
    for(;;) {
        if(!s->status.active)return false;
        if(!s->live(s->generation)){fail(s,1);return false;}
        slo_tip_t tip=s->tip;
        uint64_t now;
        if(!sync_edge_clock_read(&now)){fail(s,3);return false;}
        if(!n) {
            uint64_t scheduled=s->alarm_tick;
            uint64_t delay=count>=scheduled?count-scheduled:0u;
            uint64_t callback=now>=count?now-count:0u;
            if(delay>s->status.max_irq_delay_ticks)
                s->status.max_irq_delay_ticks=delay>UINT32_MAX?UINT32_MAX:(uint32_t)delay;
            if(callback>s->status.max_callback_ticks)
                s->status.max_callback_ticks=callback>UINT32_MAX?UINT32_MAX:(uint32_t)callback;
        }
        bool native=false;
        uint32_t wait=esp_cpu_get_cycle_count();
        while(now<tip.start) {
            if(s->flag)native=flag_native(s,now,&tip);
            if(esp_cpu_get_cycle_count()-wait>(SLO_LEAD+SLO_LATE)*12u ||
               !sync_edge_clock_read(&now)){fail(s,4);return false;}
        }
        uint64_t late=now-tip.start;
        if(late>s->status.max_late_ticks)
            s->status.max_late_ticks=late>UINT32_MAX?UINT32_MAX:(uint32_t)late;
        if(late>(s->flag?SLO_FLAG_LATE:SLO_LATE) ||
           (s->flag && late+SLO_FLAG_MIN>=tip.width)) {
            ++s->status.missed;s->status.miss_kind=tip.kind;
            unsigned phase=s_output_phase;
            if(phase>=6u)phase=0u;
            s->status.miss_phase=phase;
            if(s->status.misses_by_phase[phase]!=UINT32_MAX)++s->status.misses_by_phase[phase];
            goto advance;
        }
        if(s->flag)native=flag_native(s,now,&tip);
        else native=s->native && s->native(tip.start,tip.width,(slg_kind_t)tip.kind,s->native_arg);
        if(!s->status.active || !s->live(s->generation)){fail(s,1);return false;}
        const bool camera=s->camera_fresh && (uint32_t)now-s->camera_tick32<SLO_CAMERA_HOLD;
        if(native)++s->status.suppressed;
        else if(camera)++s->status.camera_yields;
        else if(s->native_authority && s->yield)++s->status.yielded;
        else if(s->native_authority) {
            REG_WRITE(GPIO_ENABLE_W1TC_REG,SLO_MASK);
            if(s->flag) {
                /* Restore native video promptly when a correctly phased source
                 * edge arrives; never extend a late repair beyond tip end. */
                uint32_t hold=esp_cpu_get_cycle_count();
                while(now<tip.start+tip.width) {
                    if(flag_native(s,now,&tip))break;
                    if(esp_cpu_get_cycle_count()-hold>(uint32_t)tip.width*12u ||
                       !sync_edge_clock_read(&now))break;
                }
            } else {
                uint32_t start=esp_cpu_get_cycle_count();
                while(esp_cpu_get_cycle_count()-start<(uint32_t)tip.width*12u){}
            }
            REG_WRITE(GPIO_ENABLE_W1TS_REG,SLO_MASK);
            ++s->status.repaired;
        }
advance:
        ++n;
        slg_pulse_t pulse;
        if(!slc_next(&s->clock,&pulse)){++s->status.queue_low;fail(s,2);return false;}
        slo_tip_t next={pulse.start,(uint16_t)pulse.width,(uint8_t)pulse.kind,pulse.index,pulse.half};
        /* Single-core task publication cannot run concurrently with ISR.
         * An interrupted writer is refused; no ISR spins on task state. */
        uint32_t publication=s->publication;
        if(!(publication&1u) && publication!=s->applied) {
            __sync_synchronize();
            slc_t candidate=s->pending_clock;slo_tip_t candidate_tip=s->pending_tip;
            if(publication==s->publication) {
                s->applied=publication;
                uint8_t mode=s->pending_mode;
                if(mode==SLO_PUB_SLEW || mode==SLO_PUB_JUMP_H) {
                    /* A camera-phase re-anchor shifts the whole grid (H and
                     * V together) on the next tip; an expired tip is skipped
                     * by admission, never emitted late. */
                    int32_t shift=s->pending_shift;
                    if(shift>=0) {next.start+=(uint32_t)shift;s->clock.tick+=(uint32_t)shift;}
                    else if(next.start>(uint32_t)-shift && s->clock.tick>(uint32_t)-shift) {
                        next.start-=(uint32_t)-shift;s->clock.tick-=(uint32_t)-shift;
                    }
                }
                if(mode==SLO_PUB_SLEW || mode==SLO_PUB_PERIOD) {
                    if(mode==SLO_PUB_SLEW)s->native_authority=true;
                    s->clock.whole=s->pending_whole;
                    s->clock.increment=s->pending_increment;
                    s->clock.denominator=s->pending_denominator;s->clock.remainder=0;
                } else if(mode==SLO_PUB_JUMP_V) {
                    /* Move the field position by whole halflines; physical
                     * H tips keep their absolute half parity. */
                    int32_t index=((int32_t)s->clock.index-s->pending_shift)%(int32_t)s->clock.halves;
                    if(index<0)index+=s->clock.halves;
                    s->clock.index=(uint16_t)index;
                } else if(mode==SLO_PUB_TAKEOVER && candidate_tip.start>now+SLO_LEAD) {
                    s->clock=candidate;next=candidate_tip;
                    s->standard=candidate.halves==625u?SLG_PAL:SLG_NTSC;
                    s->native_authority=true;
                    /* No edge sampled on the previous phase/standard may
                     * suppress a tip after this confirmed clock takeover. */
                    s->source_seen=false;s->low_seen=false;s->rise_valid=false;
                }
            }
        }
        s->tip=next;
        if(!sync_edge_clock_read(&now)){fail(s,3);return false;}
        if(next.start>now && next.start-now>SLO_LEAD+60u) {
            if(n>1u)++s->status.batches;
            s->alarm_tick=next.start-SLO_LEAD;
            if(!sync_edge_clock_alarm_at(s->alarm_tick))fail(s,7);
            return false;
        }
        if(n>=8u || esp_cpu_get_cycle_count()-batch_start>SLO_BATCH_CYCLES) {
            /* A cache/critical-section delay can leave several expired tips.
             * Keep the exact NEXT tip/iterator, restore task execution, then
             * skip the remaining backlog in another bounded ISR. Admission
             * still rejects every expired tip; no stale pulse is replayed. */
            if(now>UINT64_MAX-SLO_CATCHUP_LEAD){fail(s,7);return false;}
            if(s->status.budget_yields!=UINT32_MAX)++s->status.budget_yields;
            s->alarm_tick=now+SLO_CATCHUP_LEAD;
            if(!sync_edge_clock_alarm_at(s->alarm_tick))fail(s,7);
            return false;
        }
    }
}

bool sync_live_output_poll(void)
{
    slo_t *s=s_output;
    if(!s || !s->status.active || s->quarantined)return false;
    if(!s->live(s->generation))return false;
    return true;
}

bool sync_live_output_end(void)
{
    slo_t *s=s_output;
    if(!s)return true;
    if(s->quarantined)return false;
    s->status.active=false;__sync_synchronize();
    if(!sync_edge_clock_alarm_bind(NULL,NULL)){s->quarantined=true;return false;}
    if(s->configured) {
        REG_WRITE(GPIO_ENABLE_W1TS_REG,SLO_MASK);
        for(unsigned i=0;i<6u;++i) {
            REG_WRITE(GPIO_FUNC0_OUT_SEL_CFG_REG+4u*pins[i],s->saved_mux[i]);
            REG_WRITE(pads[i],s->saved_pad[i]);
        }
        for(unsigned i=0;i<6u;++i)
            if(REG_READ(GPIO_FUNC0_OUT_SEL_CFG_REG+4u*pins[i])!=s->saved_mux[i] ||
               REG_READ(pads[i])!=s->saved_pad[i]){s->quarantined=true;return false;}
        if((REG_READ(GPIO_ENABLE_REG)&SLO_MASK)!=SLO_MASK){s->quarantined=true;return false;}
    }
    sync_live_gate_destroy(s->planner);free(s);s_output=NULL;return true;
}

static bool output_begin(const slg_model_t *model,slg_standard_t fallback,
    uint32_t generation,slo_live_fn live,slo_native_fn native,slo_source_fn source,void *arg)
{
    if(s_output || !live || !live(generation) || esp_clk_cpu_freq()!=240000000)return false;
    uint64_t now;
    if(!sync_edge_clock_read(&now) || now>UINT64_MAX-20000u)return false;
    slo_t *s=heap_caps_calloc(1,sizeof(*s),MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT);
    if(!s)return false;
    s_output=s;s->live=live;s->generation=generation;s->native=native;s->native_arg=arg;
    s->source=source;s->flag=source!=NULL;
    s->native_authority=model && model->qualified;
    s->native_v_fresh=s->native_authority;
    s->native_inverted=true;
    const slg_config_t config={SLO_LEAD,s->flag?SLO_FLAG_LATE:SLO_LATE,
        s->flag?40u:100u,s->flag?40u:100u,UINT32_MAX,64u};
    s->planner=sync_live_gate_create(&config);
    if(!s->planner)goto failed;
    if(model && model->qualified) {
        if(!sync_live_gate_model(s->planner,model,now+20000u))goto failed;
    } else if(!sync_live_gate_nominal(s->planner,fallback,now+20000u,0u,0u,now))goto failed;
    if(!sync_live_gate_arm(s->planner))goto failed;
    slg_clock_seed_t seed;slg_pulse_t first;
    if(!sync_live_gate_export(s->planner,&seed) || !slc_init(&s->clock,&seed) ||
       !slc_next(&s->clock,&first))goto failed;
    s->tip=(slo_tip_t){first.start,(uint16_t)first.width,(uint8_t)first.kind,first.index,first.half};
    s->standard=seed.standard;
    /* Retain the task-owned planner for allocation-free timing updates.
     * ISR execution consumes only the exported incremental clock. */
    if((REG_READ(GPIO_ENABLE_REG)&SLO_MASK)!=SLO_MASK)goto failed;
    for(unsigned i=0;i<6u;++i) {
        uint32_t reg=GPIO_FUNC0_OUT_SEL_CFG_REG+4u*pins[i];
        s->saved_mux[i]=REG_READ(reg);s->saved_pad[i]=REG_READ(pads[i]);
        if((s->saved_mux[i]&GPIO_FUNC0_OUT_SEL_M)!=(s->flag && !i?256u:48u+i) ||
           (s->saved_mux[i]&(GPIO_FUNC0_OUT_INV_SEL|GPIO_FUNC0_OE_INV_SEL)))goto failed;
    }
    if(s->flag && (REG_READ(GPIO_OUT_REG)&(1u<<23)))goto failed;
    s->saved=true;s->status.active=true;
    if(!sync_live_output_poll())goto failed;
    if(!sync_edge_clock_alarm_bind(output_alarm,s))goto failed;
    s->configured=true;
    for(unsigned i=0;i<6u;++i) {
        /* The flag owner proves GPIO23's complete clamp identity on every
         * admission. Keep its IOMUX bits unchanged while borrowing its OE. */
        if(!s->flag || i)REG_WRITE(pads[i],s->saved_pad[i]&~(FUN_PU|FUN_PD));
        REG_WRITE(GPIO_FUNC0_OUT_SEL_CFG_REG+4u*pins[i],s->saved_mux[i]|GPIO_FUNC0_OE_SEL);
    }
    __sync_synchronize();
    s->alarm_tick=s->tip.start-SLO_LEAD;
    if(!live(generation) || !sync_edge_clock_alarm_at(s->alarm_tick))goto failed;
    return true;
failed:
    (void)sync_live_output_end();return false;
}

void sync_live_output_status(slo_status_t *out)
{
    if(out)*out=s_output?s_output->status:(slo_status_t){0};
}
void sync_live_output_native_fresh(bool vertical)
{ if(s_output)s_output->native_v_fresh=vertical; }
void sync_live_output_native_polarity(bool inverted)
{
    slo_t *s=s_output;
    if(!s || s->native_inverted==inverted)return;
    s->native_inverted=inverted;
    s->source_seen=false;s->low_seen=false;s->rise_valid=false;
}

bool sync_live_output_begin(const slg_model_t *model,slg_standard_t fallback,
    uint32_t generation,slo_live_fn live,slo_native_fn native,void *arg)
{ return output_begin(model,fallback,generation,live,native,NULL,arg); }

bool sync_live_output_flag_begin(const slg_model_t *model,slg_standard_t fallback,
    uint32_t generation,slo_live_fn live,slo_source_fn source,void *arg)
{ return source && output_begin(model,fallback,generation,live,NULL,source,arg); }

bool sync_live_output_update(const slg_model_t *model)
{
    slo_t *s=s_output;
    uint64_t now;
    if(!s || !s->flag || !sync_live_output_poll() || !model || !model->qualified ||
       !sync_edge_clock_read(&now) || now>UINT64_MAX-5000u)return false;
    sync_live_gate_t *g=s->planner;
    if(!g)return false;
    slg_clock_seed_t seed;slc_t candidate;slg_pulse_t pulse;
    bool ok=sync_live_gate_model(g,model,now+20000u) && sync_live_gate_arm(g) &&
        sync_live_gate_export(g,&seed) && slc_init(&candidate,&seed) && slc_next(&candidate,&pulse);
    if(!ok)return false;
    /* A confirmed model can replace nominal/old-standard timing. Task-side
     * planning never owns the shared timer or writes output pins. */
    /* Compare the complete field phase at the currently scheduled halfslot.
     * Same-standard small corrections slew <=100ns per publication; missing
     * evidence never erases the existing iterator. Large confirmed changes
     * use the newly acquired field rather than interpreting noise as drift. */
    slo_tip_t current;uint8_t parity;slg_standard_t standard;
    do {current=s->tip;parity=s->clock.parity;standard=s->standard;
        __sync_synchronize();} while(current.start!=s->tip.start);
    uint64_t target=seed.origin+((uint64_t)current.index*model->line_ticks_x32+32u)/64u;
    uint64_t field_ticks=((uint64_t)(model->standard==SLG_PAL?625u:525u)*model->line_ticks_x32+32u)/64u;
    int64_t error=(int64_t)target-(int64_t)current.start;
    int64_t fields=(error>=0?(error+(int64_t)field_ticks/2):(error-(int64_t)field_ticks/2))/(int64_t)field_ticks;
    error-=fields*(int64_t)field_ticks;
    /* PAL and NTSC fields have an odd halfline count. Same V phase alone
     * does not establish the H halfslot: odd field offsets flip it. */
    unsigned wanted=seed.h_parity^((uint64_t)fields&1u);
    unsigned running=parity^((current.half-current.index)&1u);
    /* If the phase error is within the slew window, correct it smoothly but aggressively
     * (up to 16 ticks = 800ns per field) so the flywheel rapidly locks onto the camera without rolling. */
    bool slew=standard==model->standard && wanted==running && error>=-200 && error<=200;
    ++s->publication;__sync_synchronize();
    s->pending_mode=slew?SLO_PUB_SLEW:SLO_PUB_TAKEOVER;
    s->pending_shift=(int32_t)(error>16?16:error< -16?-16:error);
    /* A long-baseline PLL period beats the model's 1/64-tick fit; a new
     * camera (takeover) starts over from the model. */
    if(slew && s->pll_period_q) {
        s->pending_whole=(uint32_t)(s->pll_period_q/SCP_DEN);
        s->pending_increment=(uint16_t)(s->pll_period_q%SCP_DEN);s->pending_denominator=SCP_DEN;
    } else if(slew && s->pll.active) {
        /* Measuring: keep the running period, so the fit's +-1/64-tick
         * jitter neither resets the window nor hides drift in it. */
        s->pending_whole=s->clock.whole;
        s->pending_increment=s->clock.increment;s->pending_denominator=s->clock.denominator;
    } else {
        s->pending_whole=model->line_ticks_x32>>6;
        s->pending_increment=(uint16_t)(model->line_ticks_x32&63u);s->pending_denominator=64u;
    }
    s->pending_clock=candidate;
    s->pending_tip=(slo_tip_t){pulse.start,(uint16_t)pulse.width,(uint8_t)pulse.kind,pulse.index,pulse.half};
    __sync_synchronize();++s->publication;
    /* A slew moves the grid toward the camera like a guard trim. */
    if(slew)scp_trim(&s->pll,s->pending_shift);
    if(!slew) {
        /* A confirmed camera model replaces the grid: old guard evidence
         * describes the previous phase. */
        s->guard=(spg_t){0};s->yield=false;s->guard_after=now+20000u;
        s->pll.active=false;s->pll_period_q=0;
    }
    return true;
}

/* Bounded task-side publication; refused while another is unapplied so a
 * re-anchor can never overwrite a pending model takeover. */
static bool guard_publish(slo_t *s,uint8_t mode,int32_t value)
{
    uint64_t now;
    if((s->publication&1u) || s->publication!=s->applied || !sync_edge_clock_read(&now))return false;
    ++s->publication;__sync_synchronize();
    s->pending_mode=mode;s->pending_shift=value;
    __sync_synchronize();++s->publication;
    /* Pulses observed before the ISR applies it belong to the old grid. */
    s->guard_after=now+20000u;
    if(mode==SLO_PUB_JUMP_V)++s->status.guard_v_jumps;
    else if(mode==SLO_PUB_JUMP_H)++s->status.guard_h_jumps;
    return true;
}

static bool guard_publish_period(slo_t *s,uint64_t period_q)
{
    if((s->publication&1u) || s->publication!=s->applied)return false;
    ++s->publication;__sync_synchronize();
    s->pending_mode=SLO_PUB_PERIOD;
    s->pending_whole=(uint32_t)(period_q/SCP_DEN);
    s->pending_increment=(uint16_t)(period_q%SCP_DEN);s->pending_denominator=SCP_DEN;
    __sync_synchronize();++s->publication;
    ++s->status.pll_updates;
    return true;
}

void sync_live_output_native_pulse(uint64_t tick,uint32_t width,bool high)
{
    slo_t *s=s_output;
    if(s && s->flag && s->status.active && !s->quarantined && high==s->native_inverted &&
       width>=75u && width<=240u) {
        uint64_t d=tick>s->last_h_tick?tick-s->last_h_tick:0u;
        for(uint32_t k=1u;k<=4u && d;++k) {
            uint32_t line=s->standard==SLG_PAL?1280u*k:(12711u*k+5u)/10u;
            if(d+SLO_CAMERA_TOL>=line && d<=line+SLO_CAMERA_TOL) {
                s->camera_tick=tick;s->camera_tick32=(uint32_t)tick;
                __sync_synchronize();s->camera_fresh=true;++s->status.camera_lines;
                break;
            }
        }
        s->last_h_tick=tick;
    }
    /* Without authority the grid is a nominal clock that emits nothing;
     * the guard still aligns it to a coherent camera and may grant it. */
    if(!s || !s->flag || !s->status.active || s->quarantined ||
       high!=s->native_inverted || tick<s->guard_after)return;
    bool h=width>=75u && width<=240u,broad=width>=250u && width<=620u;
    if(!h && !broad)return;
    slo_tip_t tip;slc_t clock;
    do {tip=s->tip;clock=s->clock;__sync_synchronize();} while(tip.start!=s->tip.start);
    if(!clock.denominator || !clock.halves)return;
    /* Halfline in 1/denominator ticks, exactly as the ISR iterator steps. */
    int64_t den=clock.denominator,half=(int64_t)clock.whole*den+clock.increment;
    int64_t delta=((int64_t)tick-(int64_t)tip.start)*den;
    spg_action_t action={SPG_NONE,0};
    if(h) {
        /* H lines sit on halves with the clock's parity. */
        int64_t base=(tip.half&1u)==clock.parity?delta:delta-half;
        int32_t offset=(int32_t)(spg_wrap(base,2*half)/den);
        action=spg_h(&s->guard,offset,(int32_t)(2*half/den),tick);
        if(s->native_authority && spg_abs(offset)<=SPG_WINDOW && !s->guard.yield_h && action.kind==SPG_NONE) {
            uint64_t period_q=scp_period_q(clock.whole,clock.increment,clock.denominator);
            if(!s->pll.active || s->pll.period_q!=period_q)scp_start(&s->pll,period_q,tip.half,offset);
            else {
                scp_aligned(&s->pll);
                uint64_t estimate;
                if(s->publication==s->applied && !(s->publication&1u) &&
                   scp_estimate(&s->pll,tip.half,offset,&estimate) &&
                   guard_publish_period(s,estimate))s->pll_period_q=estimate;
            }
        }
    } else {
        bool first=tick<s->last_broad || tick-s->last_broad>3000u;
        s->last_broad=tick;
        if(!first)return;
        int64_t halves=delta>=0?(delta+half/2)/half:-((-delta+half/2)/half);
        int32_t index=(int32_t)(((int64_t)tip.index+halves)%clock.halves);
        if(index<0)index+=clock.halves;
        int32_t offset=spg_wrap((int64_t)index-clock.section,clock.halves);
        action=spg_v(&s->guard,offset,tick,(uint64_t)((clock.halves*half+den/2)/den));
    }
    if(action.kind==SPG_JUMP_H) {
        bool trim=!s->guard.yield_h;
        if(guard_publish(s,SLO_PUB_JUMP_H,action.value)) {
            if(trim){++s->status.guard_trims;scp_trim(&s->pll,action.value);}
            else s->pll.active=false; /* re-anchor: the window no longer measures drift */
            spg_reset(&s->guard);
        }
    } else if(action.kind==SPG_JUMP_V && guard_publish(s,SLO_PUB_JUMP_V,action.value)) {
        spg_reset(&s->guard);
    }
    s->yield=spg_yield(&s->guard,tick);
    /* Grant only on the grid the evidence describes: nothing unapplied. */
    if(!s->native_authority && spg_acquired(&s->guard,tick) &&
       s->publication==s->applied && !(s->publication&1u)) {
        s->native_authority=true;
        ++s->status.guard_acquired;
    }
}

void sync_live_output_guard_tick(void)
{
    slo_t *s=s_output;
    uint64_t now;
    if(!s || !s->flag || !sync_edge_clock_read(&now))return;
    s->yield=spg_yield(&s->guard,now);
    s->status.guard_yield=s->yield;
    if(s->camera_fresh && (now<s->camera_tick || now-s->camera_tick>=SLO_CAMERA_HOLD))s->camera_fresh=false;
    s->status.camera_present=s->camera_fresh;
    s->status.pll_rejected=s->pll.rejected;
    /* Signed fine-period offset versus nominal, in parts per billion. */
    if(s->pll_period_q) {
        uint64_t nominal=s->standard==SLG_PAL?640u*(uint64_t)SCP_DEN:(5720u*(uint64_t)SCP_DEN+4u)/9u;
        s->status.pll_ppb=(int32_t)(((int64_t)s->pll_period_q-(int64_t)nominal)*1000000000/(int64_t)nominal);
    }
}

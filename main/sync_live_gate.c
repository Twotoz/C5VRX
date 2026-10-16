/* C5VRX by Twotoz/contributors. Pure 20 MHz pulse planner: no pad writes.
 * Camera fields start at their first BROAD pulse; subtract the PAL5/NTSC6
 * preceding EQ half-lines. The measured H anchor resolves actual interlace
 * parity instead of assuming the first observed field is an even field.
 * Holdover keeps this phase/period; it never replaces real active video. */
#include "sync_live_gate.h"
#include <stddef.h>
#include <stdlib.h>

struct sync_live_gate {
    slg_config_t config;
    slg_model_t model;
    uint64_t origin, cursor;
    uint32_t serial;
    volatile uint32_t publication;
    uint8_t h_parity;
    bool ready, active, nominal;
};

bool sync_live_gate_export(const sync_live_gate_t *g,slg_clock_seed_t *out)
{
    if(!g || !out || !g->ready || !g->active || (g->publication&1u))return false;
    bool ntsc=g->nominal && g->model.standard==SLG_NTSC;
    *out=(slg_clock_seed_t){.origin=g->origin,.next_half=g->cursor,
        .numerator=ntsc?5720u:g->model.line_ticks_x32,
        .denominator=ntsc?9u:64u,.h_parity=g->h_parity,.standard=g->model.standard};
    return true;
}

/* Divide before multiplication: preserve long holdover without overflow. */
static bool scaled_ratio(uint32_t period,uint32_t denominator,uint64_t half,uint64_t *out)
{
    uint64_t q=half/denominator,r=((half%denominator)*period+denominator/2u)/denominator;
    if(q>(UINT64_MAX-r)/period)return false;
    *out=q*period+r;return true;
}
static bool scaled_half(uint32_t period,uint64_t half,uint64_t *out)
{ return scaled_ratio(period,64u,half,out); }
static bool at(const sync_live_gate_t *g,uint64_t half,uint64_t *out)
{
    uint64_t delta;
    bool exact_ntsc=g->nominal && g->model.standard==SLG_NTSC;
    if(!scaled_ratio(exact_ntsc?5720u:g->model.line_ticks_x32,exact_ntsc?9u:64u,half,&delta) ||
       delta>UINT64_MAX-g->origin)return false;
    *out=g->origin+delta;return true;
}
static bool seek(uint64_t origin,uint32_t period,uint32_t denominator,uint64_t now,uint32_t lead,uint64_t *half)
{
    if(now>UINT64_MAX-lead)return false;
    uint64_t target=now+lead;
    if(target<=origin){*half=0;return true;}
    uint64_t delta=target-origin,q=delta/period;
    uint64_t remainder=((delta%period)*denominator)/period;
    if(q>(UINT64_MAX-remainder)/denominator)return false;
    *half=q*denominator+remainder;
    uint64_t tick;
    if(!scaled_ratio(period,denominator,*half,&tick))return false;
    if(tick<delta) {
        if(*half==UINT64_MAX)return false;
        ++*half;
    }
    return true;
}
static void publish_begin(sync_live_gate_t *g)
{ ++g->publication;__sync_synchronize(); }
static void publish_end(sync_live_gate_t *g)
{ __sync_synchronize();++g->publication; }

sync_live_gate_t *sync_live_gate_create(const slg_config_t *config)
{
    if(!config || !config->model_age_ticks || config->late_ticks>160u ||
       config->native_window_ticks>=600u || config->native_age_ticks>=600u)return NULL;
    sync_live_gate_t *g=calloc(1,sizeof(*g));
    if(g)g->config=*config;
    return g;
}
void sync_live_gate_destroy(sync_live_gate_t *g)
{ free(g); }

bool sync_live_gate_model(sync_live_gate_t *g,const slg_model_t *m,uint64_t now)
{
    if(!g || !m || !m->qualified || (m->standard!=SLG_PAL && m->standard!=SLG_NTSC) ||
       m->observed_at>now || now-m->observed_at>g->config.model_age_ticks ||
       m->field>m->observed_at || m->last_h>m->observed_at ||
       m->uncertainty_ticks>g->config.max_uncertainty_ticks)return false;
    uint32_t expected=m->standard==SLG_PAL?40960u:40676u;
    if(m->line_ticks_x32<expected-32u || m->line_ticks_x32>expected+32u)return false;
    uint64_t before;
    if(!scaled_half(m->line_ticks_x32,m->standard==SLG_PAL?5u:6u,&before) || m->field<before)return false;
    uint64_t origin=m->field-before;
    uint64_t distance=m->last_h>=origin?m->last_h-origin:origin-m->last_h;
    /* An H anchor must belong to this nearby field, not an old camera. */
    if(distance>810000u)return false;
    uint64_t nearest=(distance*64u+m->line_ticks_x32/2u)/m->line_ticks_x32,fit;
    if(!scaled_half(m->line_ticks_x32,nearest,&fit))return false;
    uint64_t error=fit>distance?fit-distance:distance-fit;
    if(error>m->uncertainty_ticks+2u)return false;
    uint64_t cursor;
    if(!seek(origin,m->line_ticks_x32,64u,now,g->config.lead_ticks,&cursor))return false;
    publish_begin(g);
    g->model=*m;g->origin=origin;g->cursor=cursor;
    g->h_parity=(uint8_t)(nearest&1u);g->nominal=false;g->ready=true;
    ++g->serial;publish_end(g);return true;
}

bool sync_live_gate_nominal(sync_live_gate_t *g,slg_standard_t standard,uint64_t first_eq,
                           uint8_t h_half_parity,uint32_t receive_epoch,uint64_t now)
{
    if(!g || (standard!=SLG_PAL && standard!=SLG_NTSC) || h_half_parity>1u)return false;
    uint32_t period=standard==SLG_PAL?40960u:40676u;
    uint64_t cursor;
    if(!seek(first_eq,standard==SLG_NTSC?5720u:period,standard==SLG_NTSC?9u:64u,now,g->config.lead_ticks,&cursor))return false;
    publish_begin(g);
    g->model=(slg_model_t){.standard=standard,.line_ticks_x32=period,.receive_epoch=receive_epoch};
    g->origin=first_eq;g->cursor=cursor;g->h_parity=h_half_parity;
    g->nominal=true;g->ready=true;++g->serial;publish_end(g);return true;
}

bool sync_live_gate_arm(sync_live_gate_t *g)
{
    if(!g || !g->ready)return false;
    publish_begin(g);g->active=true;++g->serial;publish_end(g);return true;
}
void sync_live_gate_cancel(sync_live_gate_t *g)
{
    if(!g)return;
    publish_begin(g);g->active=false;++g->serial;publish_end(g);
}
void sync_live_gate_invalidate(sync_live_gate_t *g,uint32_t receive_epoch)
{
    if(!g)return;
    publish_begin(g);g->model.qualified=false;g->model.receive_epoch=receive_epoch;
    publish_end(g);
}
bool sync_live_gate_holdover(const sync_live_gate_t *g,uint64_t now)
{
    return !g || (g->publication&1u) || !g->ready || g->nominal || !g->model.qualified ||
        now<g->model.observed_at || now-g->model.observed_at>g->config.model_age_ticks;
}

bool sync_live_gate_next(sync_live_gate_t *g,slg_pulse_t *out)
{
    if(!g || !out || (g->publication&1u) || !g->ready || !g->active)return false;
    uint16_t halves=g->model.standard==SLG_PAL?625u:525u;
    uint16_t section=g->model.standard==SLG_PAL?5u:6u;
    uint64_t half=g->cursor;
    uint16_t index=(uint16_t)(half%halves);
    if(index>=3u*section && (half&1u)!=g->h_parity) {
        if(half==UINT64_MAX)return false;
        ++half;index=(uint16_t)(half%halves);
    }
    uint64_t start,next;
    if(half==UINT64_MAX || !at(g,half,&start) || !at(g,half+1u,&next))return false;
    slg_kind_t kind=index<section?SLG_EQ:index<2u*section?SLG_BROAD:
        index<3u*section?SLG_EQ:SLG_H;
    uint32_t width=kind==SLG_BROAD?(uint32_t)(next-start)-94u:
        kind==SLG_EQ?(g->model.standard==SLG_PAL?47u:46u):94u;
    if(start<g->config.lead_ticks || start>UINT64_MAX-width)return false;
    *out=(slg_pulse_t){.start=start,.alarm_tick=start-g->config.lead_ticks,
        .half=half,.field=half/halves,.width=width,.serial=g->serial,.index=index,.kind=kind};
    g->cursor=half+1u;return true;
}

slg_decision_t sync_live_gate_decide(const sync_live_gate_t *g,const slg_pulse_t *p,
                                   uint64_t now,slg_native_fn native,void *arg)
{
    if(!g || !p)return SLG_CANCELLED;
    uint32_t publication=g->publication;
    if((publication&1u) || !g->active || p->serial!=g->serial)return SLG_CANCELLED;
    if(now<p->start)return SLG_EARLY;
    if(now-p->start>g->config.late_ticks)return SLG_LATE;
    slg_native_t n={0};
    bool present=native && native(arg,p,&n) && n.qualified && n.low &&
        n.receive_epoch==g->model.receive_epoch && n.observed_at<=now &&
        now-n.observed_at<=g->config.native_age_ticks && n.fall<=n.observed_at;
    uint64_t error=n.fall>=p->start?n.fall-p->start:p->start-n.fall;
    present=present && error<=g->config.native_window_ticks;
    __sync_synchronize();
    if(publication!=g->publication || !g->active || p->serial!=g->serial)return SLG_CANCELLED;
    return present?SLG_NATIVE:SLG_REPAIR;
}

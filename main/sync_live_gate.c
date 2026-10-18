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

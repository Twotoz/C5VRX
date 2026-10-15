/* C5VRX by Twotoz/contributors: actual keeper detection-only 80us replay.
 * Input and detector state are private host data. No synthesis, DMA or GPIO.
 * Eval/work counters are not a C5 timing measurement. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "sync_flywheel.h"
#include "phase8_gain_lut.h"
extern unsigned sfw_research_acq_skips;
#define RING 32768u
int main(int argc, char **argv)
{
    if (argc != 3) return 2;
    unsigned budget = (unsigned)atoi(argv[2]);
    if (!budget || budget > 100000) return 2;
    FILE *in = fopen(argv[1], "rb");
    if (!in || fseek(in, 0, SEEK_END)) return 2;
    long length = ftell(in);
    if (length < 2 || fseek(in, 0, SEEK_SET)) return 2;
    uint8_t *raw = malloc((size_t)length);
    if (!raw || fread(raw, 1, (size_t)length, in) != (size_t)length) return 2;
    fclose(in);
    uint8_t ring[RING] = {0}, before[RING];
    sfw_ring_t r = {.ring=ring,.ring_bytes=RING,.phase=c5vrx_phase8_gain_lut};
    sync_flywheel_t f; sfw_init(&f);
    sfw_run(&f,&r,0,0,0,false,0); /* prewarm excluded from service work */
    uint64_t rx=0, total_evals=0, total_steps=0, total_lowm=0;
    uint32_t max_evals=0, max_steps=0, calls=0, acquiring=0, tracking=0, max_backlog=0;
    uint32_t max_acquire_evals=0, max_track_evals=0;
    uint64_t first_track=0, first_lock=0, first_v=0;
    while (rx < (uint64_t)length) {
        uint64_t end=rx+3200; /*80us at40MS/s; no claim about actual scheduler*/
        if (end > (uint64_t)length) end=(uint64_t)length;
        for (;rx<end;++rx) ring[rx&(RING-1u)]=raw[rx];
        if (rx < RING+4092u) continue; /* explicit fresh initial ring epoch */
        uint64_t available=rx-4092u;
        memcpy(before,ring,sizeof(ring));
        bool was_acquiring=f.state==SFW_ACQUIRE;
        if (was_acquiring) ++acquiring; else ++tracking;
        sfw_run(&f,&r,available,rx,0,false,budget);
        if (memcmp(before,ring,sizeof(ring))) return 3;
        uint32_t *stage_max=was_acquiring?&max_acquire_evals:&max_track_evals;
        if(f.evals>*stage_max)*stage_max=f.evals;
        if (!first_track && f.state==SFW_TRACK) first_track=rx;
        if (!first_lock && sfw_locked(&f)) first_lock=rx;
        if (!first_v && f.v_valid) first_v=rx;
        if (f.state==SFW_ACQUIRE && f.scan_pos && available>=f.scan_pos &&
            available-f.scan_pos>max_backlog) max_backlog=(uint32_t)(available-f.scan_pos);
        ++calls; total_evals+=f.evals; total_steps+=f.phase_steps; total_lowm+=f.lowm_evals;
        if (f.evals>max_evals) max_evals=f.evals;
        if (f.phase_steps>max_steps) max_steps=f.phase_steps;
    }
    printf("{\"samples\":%ld,\"budget\":%u,\"calls\":%u,\"acquire_calls\":%u,"
        "\"track_calls\":%u,\"acquire_stale_skips\":%u,\"max_acquire_backlog\":%u,"
        "\"first_track_sample\":%llu,\"first_lock_sample\":%llu,\"first_v_sample\":%llu,"
        "\"acquisitions\":%u,\"standard\":%d,\"locked\":%s,\"real_v_fields\":%u,"
        "\"total_evals\":%llu,\"total_lowm\":%llu,\"total_steps\":%llu,"
        "\"max_acquire_evals\":%u,\"max_track_evals\":%u,\"max_evals\":%u,\"max_steps\":%u,\"raw_writes\":0,\"hardware_cycles_proven\":false}\n",
        length,budget,calls,acquiring,tracking,sfw_research_acq_skips,max_backlog,
        (unsigned long long)first_track,(unsigned long long)first_lock,(unsigned long long)first_v,
        f.acquisitions,sfw_standard(&f),sfw_locked(&f)?"true":"false",f.vsyncs,
        (unsigned long long)total_evals,(unsigned long long)total_lowm,(unsigned long long)total_steps,
        max_acquire_evals,max_track_evals,max_evals,max_steps);
    free(raw); return 0;
}

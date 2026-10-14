/* C5VRX by Twotoz and contributors: root sync-keeper candidate.
 *
 * Adapted from legacy/c5vrx4/sync_flywheel.{c,h}; prior PLL, field and
 * coasting work is credited to Twotoz and the C5VRX contributors. Root
 * detection uses unwrapped adjacent phase at 25 ns; output tests run the
 * actual 50-ns U85Q-LP decoder. Synthesis removes per-sample 64-bit division.
 * This is a host candidate, not integrated streaming firmware. Caller must
 * prove RX/TX lifetimes and preserve every raw observer window. Source age,
 * actual camera clock, physical waveform and CPU scheduling remain gates.
 */
#pragma once
#include <stdbool.h>
#include <stdint.h>
#include "sync_repair_ledger.h"

/* 40 MS/s raw samples (one byte = signed Q4 I in bits 7..4, Q in 3..0). */
#define SFW_PAL_LINE_Q16   (2560u * 65536u)  /* 64 us */
#define SFW_NTSC_LINE_Q16  ((22880u * 65536u + 4u) / 9u) /* exact 572/9 us, rounded once */

typedef struct {
    uint8_t *ring;              /* raw IQ ring shared by RX and TX */
    uint32_t ring_bytes;        /* power of two */
    const uint8_t *phase;       /* Phase8 phase per raw byte (static or mask table) */
    /* Native AGC acquisition mask: synthesized bytes keep data bit 0 at this
     * value (the "not flagged" polarity). Ignored when !mask_bit0. */
    bool mask_bit0;
    uint8_t clear_bit0;
    /* Caller certifies matching kp/LUT/U85Q-LP epoch after transport's full
     * halted-LUT verification. Unknown or unverified context cannot repair;
     * the candidate never infers the active kp/program from signal data. */
    uint8_t verified_kp_x100;
    bool program_verified;
    const uint16_t *decoder_lut; /* same verified active worker/controller words */
} sfw_ring_t;

typedef enum { SFW_ACQUIRE = 0, SFW_TRACK = 1 } sfw_state_t;

typedef struct {
    sfw_state_t state;
    sync_repair_ledger_t repairs;
    uint64_t evidence_end;
    uint32_t evidence_refused;
    int32_t repair_step_q8;
    uint8_t repair_kp_x100;
    uint32_t context_refused;
    uint32_t bootstrap_unknown;
    uint32_t bootstrap_refused;
    /* acquisition */
    uint64_t scan_pos;
    uint8_t acq_phase;
    uint16_t acq_hist[256];
    uint32_t acq_n;
    uint32_t acq_run;
    uint64_t acq_last_start;
    bool acq_have_last;
    uint8_t acq_runs;
    /* line tracking */
    uint64_t next_q16;           /* predicted next line start, absolute sample Q16 */
    int32_t period_q16, nominal_q16;
    uint64_t clock_sample;
    uint32_t clock_line;
    int32_t coast_period_q16;
    bool clock_known;
    uint32_t clock_n;
    uint64_t clock_x, clock_xx;
    int64_t clock_y, clock_xy;
    int16_t thr;                /* sync/not-sync threshold, Phase8 bins per span */
    int16_t sync_q4, blank_q4;  /* learned levels, Phase8 bins per span, Q4 */
    bool levels;
    uint16_t clean_since_acq;
    uint32_t lines_since_clean;
    /* Rebuild every pulse while any recent pulse decoded noisy (a sampled
     * check cannot see every 75 ns span); off after 64 clean lines. */
    uint16_t rebuild_lines, pristine_run;
    /* re-lock while coasting */
    int32_t relock_off;
    uint32_t relock_line;
    bool relock_have;
    /* vertical interval, on the PLL line grid */
    uint32_t grid_line;         /* index of the line at next_q16 */
    bool v_valid;               /* field phase known */
    bool v_armed;               /* slots of the coming vertical interval in progress */
    bool v_half;                /* the next anchor sits half a line after its line start */
    int32_t v_phase_q16;         /* observed V edge relative to the horizontal grid */
    uint32_t v_a_line;          /* grid line of the next anchor */
    uint64_t v_next_q16;         /* armed: anchor = first broad pulse start, Q16 */
    int16_t v_slot;             /* next slot, -npre .. nbroad+npost-1 */
    bool v_found;               /* real broad pulses seen in this field */
    bool v_observed;
    uint16_t v_observed_n;
    int64_t v_observed_sum;
    uint64_t v_observed_q16;     /* genuine edge; never sourced from repaired bytes */
    uint64_t v_end_q16;          /* end of the last handled interval: its lines stay vertical */
    /* counters */
    uint32_t lines, clean, repaired, missed, slots_repaired, vsyncs, v_coasted, v_parity,
             acquisitions, relocks, skipped_lines, fast_lines, skipped_floor, rebuilt;
    uint32_t evals;             /* budget units: lowm calls plus extra sampled phase steps */
    uint32_t lowm_evals;        /* actual nine-step lowm calls, separate from budget units */
    uint32_t phase_steps;       /* additional adjacent steps for acquisition/level/tip checks */
} sync_flywheel_t;

void sfw_init(sync_flywheel_t *f);
/* Process predicted lines / V slots whose data is complete (< avail_end,
 * absolute sample index, exclusive). Writes only samples >= write_floor
 * (ahead of the TX read). At most budget_evals detection evaluations per call;
 * a flywheel that falls behind coasts lines unanalysed (nothing written).
 * rx_complete is the actual monotonically increasing completed RX frontier,
 * distinct from the observer-protected avail_end. Only proved RX overwrite
 * expires repair provenance; caller must additionally prove in-flight DMA
 * and all reader lifetimes. Returns the number of lines handled. */
unsigned sfw_run(sync_flywheel_t *f, const sfw_ring_t *r, uint64_t avail_end,
                 uint64_t rx_complete, uint64_t write_floor, bool allow_repair, uint32_t budget_evals);
bool sfw_locked(const sync_flywheel_t *f);
/* 1 = PAL, 2 = NTSC, 0 = not tracking. */
int sfw_standard(const sync_flywheel_t *f);

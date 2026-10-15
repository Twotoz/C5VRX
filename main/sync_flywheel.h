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

/* The executor rechecks DMA/source/context lifetimes after preparation and
 * admits/copies the COMPLETE interval or refuses it without writing any byte.
 * No partial success is permitted. The workspace is valid only for this
 * synchronous call; retaining its pointer or reentering sfw_run is forbidden.
 * The candidate's ledger is conservatively tainted before this call, even
 * when it returns false. This API alone does not certify live DMA safety. */
typedef bool (*sfw_commit_fn)(void *ctx, uint64_t absolute_start,
                               const uint8_t *samples, uint32_t bytes);

/* Optional PRIVATE benchmark milestones, not CPU-exclusive time or WCET.
 * Seven counter reads maximum; elapsed cycles include IRQ/task preemption.
 * Lazy min-token construction remains inside selection, without per-byte
 * timing or profile stores. stage_mask identifies milestones even at counter wrap. */
typedef struct {
    uint32_t begin, init_done, prefix_done, regular_done, selection_done, body_done, commit_done;
    uint32_t minmask_builds;
    uint8_t prefix_states[2], stage_mask;
    bool minmask_in_selection;
} sfw_bench_profile_t;

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
    /* Optional unscaled worker/controller reference. Caller verifies ALL1024
     * active words against output_entry(reference,verified_output), under the
     * same halted-program/generation binding, before claiming program_verified.
     * Reference token/tracker bits then exactly match the actual decoder.
     * NULL retains legacy unity construction; scaled output requires a reference. */
    const uint16_t *reference_lut;
    uint8_t verified_output;
    /* Optional executor mode. A configured callback requires caller-owned,
     * writable workspace covering the complete pulse and disjoint from the
     * whole raw ring. Synthesis never writes the raw ring in this mode.
     * NULL callback preserves the original direct host-replay mode. */
    uint8_t *workspace;
    uint32_t workspace_bytes;
    sfw_commit_fn commit;
    void *commit_ctx;
    /* Benchmark only: caller-owned writable storage disjoint from buffers.
     * NULL for ordinary host replay and any eventual live keeper. */
    sfw_bench_profile_t *benchmark_profile;
} sfw_ring_t;

typedef enum { SFW_ACQUIRE = 0, SFW_TRACK = 1, SFW_GENERATED = 2 } sfw_state_t;
typedef enum { SFW_STD_UNKNOWN = 0, SFW_STD_PAL = 1, SFW_STD_NTSC = 2 } sfw_standard_t;
typedef enum { SFW_SOURCE_ABSENT, SFW_SOURCE_CANDIDATE, SFW_SOURCE_SYNC } sfw_source_t;

typedef struct {
    sfw_state_t state;
    /* Explicit source monitor admission, independent of generated timing.
     * No source observation may be inferred from keeper output. */
    uint32_t source_context, source_windows, generated_starts, generated_cancels;
    uint64_t source_begin, source_end, source_samples;
    uint8_t fallback_standard;
    bool source_context_known, source_absent;
    sync_repair_ledger_t repairs;
    uint64_t evidence_end;
    uint32_t evidence_refused;
    int32_t repair_step_q8;
    uint8_t repair_kp_x100;
    uint32_t context_refused;
    uint32_t bootstrap_unknown;
    uint32_t bootstrap_refused;
    uint32_t workspace_refused, commit_calls, commit_refused;
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
/* Caller supplies a complete ORIGINAL window and its independently qualified
 * source status before sfw_run. ABSENT requires an original-source sync
 * monitor; elapsed time/failed keeper lock alone cannot certify it. Each
 * window must be fresh, contiguous, in one context and RX-readable. Gaps
 * reset the absence coverage; at least 8ms of actual samples are required.
 * CANDIDATE/SYNC cancel generated timing immediately, before another write.
 * Unknown/changed context, selected standard or stale evidence fail closed.
 * fallback_standard: 0 disabled, 1 PAL, 2 NTSC (selected or last known).
 * A monitor's camera-arrival detection latency remains an integration gate. */
bool sfw_bootstrap_source(sync_flywheel_t *f, uint32_t context, uint8_t fallback_standard,
                          uint64_t begin, uint64_t end, uint64_t rx_complete,
                          uint32_t ring_bytes, sfw_source_t source);
/* Generated output geometry is deliberately separate from real source lock. */
int sfw_output_standard(const sync_flywheel_t *f);
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

/* SPDX-License-Identifier: GPL-3.0-only */

/*
 * Continuous A1 RF writer service for the ESP32-C5 LP core.
 *
 * The proven C5 RF dump mode is a 16384-word one-shot. This program runs from
 * LP SRAM, outside the HP-SRAM window lent to the MAC, and rearms that mode at
 * every terminal pointer. That stitched command has no duration limit. A
 * separate build-gated command observes the bit-17 hardware-ring
 * hypothesis without trigger pulses or rearms and has a strict duration limit.
 */

#include <stdbool.h>
#include <stdint.h>

#include "sdkconfig.h"
#include "riscv/rvruntime-frames.h"
#include "ulp_lp_core_cpu_freq_shared.h"

#define REG32(address) (*(volatile uint32_t *)(uintptr_t)(address))

#define DUMP_CTRL       0x600a9004u
#define DUMP_PTR_MODE   0x600a9008u
#define HP_SRAM_USAGE   0x60095004u
#define PARLIO_TX_CLOCK 0x600960b4u
#define PARLIO_INT_ENA  0x60015028u
#define PARLIO_INT_CLR  0x60015034u
#define AHB_DMA_BASE    0x60080000u

#define CTRL_ENABLE     0x80000000u
#define CTRL_SW_TRIGGER 0x00080000u
#define CTRL_DONE       0x00040000u
#define CTRL_RING_MODE  0x00020000u
#define PARLIO_CLK_EN   0x00040000u
#define PARLIO_TX_FIFO_EMPTY_INT 0x00000001u
#define PARLIO_PREFILL_CYCLES 512u
#define POINTER_MASK    0x00003fffu
#define BURST_WORDS     16384u
#define GDMA_DESC_BYTES 12u
#define GDMA_DESC_WORDS 1023u
#define GDMA_DESC_COUNT 17u

#define COMMAND_NONE       0u
#define COMMAND_BOUNDED    1u
#define COMMAND_CONTINUOUS 2u
#define COMMAND_STOP       3u
#define COMMAND_NATIVE_RING 4u

#define STATE_BOOT        0u
#define STATE_READY       1u
#define STATE_ARMING      2u
#define STATE_RUNNING     3u
#define STATE_DONE        4u
#define STATE_NO_ACTIVITY 5u
#define STATE_REARM_ERROR 6u
#define STATE_STOPPED     7u

#define LEAD_TIMEOUT_US  20000u
#define REARM_TIMEOUT_US  5000u
#define ACTIVITY_TIMEOUT_US 50000u

/* Exported by the ULP build as ulp_c5vrx_* symbols on the HP core. */
volatile uint32_t c5vrx_command;
volatile uint32_t c5vrx_state;
volatile uint32_t c5vrx_duration_us;
volatile uint32_t c5vrx_lead_words;
volatile uint32_t c5vrx_enable_parlio;
volatile uint32_t c5vrx_writer_advance;
volatile uint32_t c5vrx_pointer_changes;
volatile uint32_t c5vrx_pointer_restarts;
volatile uint32_t c5vrx_last_pointer;
volatile uint32_t c5vrx_lead_acquired;
volatile uint32_t c5vrx_bursts_completed;
volatile uint32_t c5vrx_rearms_succeeded;
volatile uint32_t c5vrx_rearm_failures;
volatile uint32_t c5vrx_gap_cycles_total;
volatile uint32_t c5vrx_gap_cycles_max;
volatile uint32_t c5vrx_last_gap_cycles;
volatile uint32_t c5vrx_final_control;
volatile uint32_t c5vrx_runs;
volatile uint32_t c5vrx_expected_block_cycles;
volatile uint32_t c5vrx_block_period_last;
volatile uint32_t c5vrx_block_period_min;
volatile uint32_t c5vrx_block_period_max;
volatile uint32_t c5vrx_run_cycles;
volatile int32_t c5vrx_phase_error_cycles;
volatile uint32_t c5vrx_phase_window_blocks;
volatile uint32_t c5vrx_fault_cause;
volatile uint32_t c5vrx_fault_address;
volatile uint32_t c5vrx_fault_pc;
volatile uint32_t c5vrx_stage;
volatile uint32_t c5vrx_saved_ownership;
volatile uint32_t c5vrx_gdma_channel;
volatile uint32_t c5vrx_gdma_descriptor_base;
volatile uint32_t c5vrx_consumer_pointer;
volatile uint32_t c5vrx_consumer_lead_words;
volatile uint32_t c5vrx_consumer_lead_min_words;
volatile uint32_t c5vrx_consumer_lead_max_words;
volatile uint32_t c5vrx_consumer_observations;
volatile uint32_t c5vrx_consumer_pointer_changes;
volatile uint32_t c5vrx_consumer_wraps;
volatile uint32_t c5vrx_consumer_descriptor_errors;
#if CONFIG_C5VRX_EXPERIMENTAL_NATIVE_RING_PROBE
volatile uint32_t c5vrx_native_observations;
volatile uint32_t c5vrx_native_pointer_changes;
volatile uint32_t c5vrx_native_wraps;
volatile uint32_t c5vrx_native_min_pointer;
volatile uint32_t c5vrx_native_max_pointer;
volatile uint32_t c5vrx_native_last_pointer;
volatile uint32_t c5vrx_native_enable_assertions;
volatile uint32_t c5vrx_native_enable_low;
volatile uint32_t c5vrx_native_mode_low;
volatile uint32_t c5vrx_native_done_observations;
volatile uint32_t c5vrx_native_progress_after_done;
volatile uint32_t c5vrx_native_software_triggers;
volatile uint32_t c5vrx_native_software_rearms;
volatile uint32_t c5vrx_native_trigger_high;
volatile uint32_t c5vrx_native_ambiguous_backwards;
volatile uint32_t c5vrx_native_content_observations;
volatile uint32_t c5vrx_native_content_changes;
volatile uint32_t c5vrx_native_wrap_content_changes;
volatile uint32_t c5vrx_native_iq_power_sum_low;
volatile uint32_t c5vrx_native_iq_power_sum_high;
volatile uint32_t c5vrx_native_content_signature;
volatile uint32_t c5vrx_native_phase_boundaries;
volatile uint32_t c5vrx_native_phase_residual_abs_sum;
volatile uint32_t c5vrx_native_phase_residual_abs_max;
volatile uint32_t c5vrx_native_start_control;
volatile uint32_t c5vrx_native_final_control;
volatile uint32_t c5vrx_native_fault_reason;
volatile uint32_t c5vrx_native_writer_stopped_after_done;
#endif

/* Keep an LP access fault local to the LP core.  The stock weak handler calls
 * ulp_lp_core_abort(), which makes a register-permission mistake look like a
 * whole-chip disconnect.  Shared fault telemetry lets the HP task retain PAL,
 * restore the RF session and report the exact failing address over USB. */
void __attribute__((noreturn)) ulp_lp_core_panic_handler(RvExcFrame *frame,
                                                         int exccause)
{
    const uint32_t failed_stage = c5vrx_stage;
    c5vrx_fault_cause = (uint32_t)exccause;
    c5vrx_fault_address = (uint32_t)frame->mtval;
    c5vrx_fault_pc = (uint32_t)frame->mepc;
    c5vrx_stage = 0xe0000000u | ((uint32_t)exccause & 0xffu);
    if (failed_stage >= 12u) {
        REG32(DUMP_CTRL) &= ~CTRL_ENABLE;
        __asm__ __volatile__("fence iorw, iorw" ::: "memory");
        REG32(HP_SRAM_USAGE) = c5vrx_saved_ownership;
        __asm__ __volatile__("fence iorw, iorw" ::: "memory");
    }
    c5vrx_command = COMMAND_NONE;
    c5vrx_state = STATE_REARM_ERROR;
    for (;;) {
        __asm__ __volatile__("nop");
    }
}

static inline uint32_t cycle_count(void)
{
    uint32_t value;
    __asm__ __volatile__("csrr %0, mcycle" : "=r"(value));
    return value;
}

static inline void io_fence(void)
{
    __asm__ __volatile__("fence iorw, iorw" ::: "memory");
}

static inline uint32_t cycles_for_us(uint32_t us)
{
    return us * LP_CORE_CYCLES_PER_US_NUM /
        LP_CORE_CYCLES_PER_US_DENOM;
}

static void clear_stats(void)
{
    c5vrx_writer_advance = 0u;
    c5vrx_pointer_changes = 0u;
    c5vrx_pointer_restarts = 0u;
    c5vrx_last_pointer = 0u;
    c5vrx_lead_acquired = 0u;
    c5vrx_bursts_completed = 0u;
    c5vrx_rearms_succeeded = 0u;
    c5vrx_rearm_failures = 0u;
    c5vrx_gap_cycles_total = 0u;
    c5vrx_gap_cycles_max = 0u;
    c5vrx_last_gap_cycles = 0u;
    c5vrx_final_control = 0u;
    c5vrx_block_period_last = 0u;
    c5vrx_block_period_min = 0u;
    c5vrx_block_period_max = 0u;
    c5vrx_run_cycles = 0u;
    c5vrx_phase_error_cycles = 0;
    c5vrx_phase_window_blocks = 0u;
    c5vrx_consumer_pointer = 0u;
    c5vrx_consumer_lead_words = 0u;
    c5vrx_consumer_lead_min_words = BURST_WORDS;
    c5vrx_consumer_lead_max_words = 0u;
    c5vrx_consumer_observations = 0u;
    c5vrx_consumer_pointer_changes = 0u;
    c5vrx_consumer_wraps = 0u;
    c5vrx_consumer_descriptor_errors = 0u;
}

static inline uint32_t pointer(void);

#if CONFIG_C5VRX_EXPERIMENTAL_NATIVE_RING_PROBE
static void clear_native_stats(void)
{
    c5vrx_native_observations = 0u;
    c5vrx_native_pointer_changes = 0u;
    c5vrx_native_wraps = 0u;
    c5vrx_native_min_pointer = POINTER_MASK;
    c5vrx_native_max_pointer = 0u;
    c5vrx_native_last_pointer = 0u;
    c5vrx_native_enable_assertions = 0u;
    c5vrx_native_enable_low = 0u;
    c5vrx_native_mode_low = 0u;
    c5vrx_native_done_observations = 0u;
    c5vrx_native_progress_after_done = 0u;
    c5vrx_native_software_triggers = 0u;
    c5vrx_native_software_rearms = 0u;
    c5vrx_native_trigger_high = 0u;
    c5vrx_native_ambiguous_backwards = 0u;
    c5vrx_native_content_observations = 0u;
    c5vrx_native_content_changes = 0u;
    c5vrx_native_wrap_content_changes = 0u;
    c5vrx_native_iq_power_sum_low = 0u;
    c5vrx_native_iq_power_sum_high = 0u;
    c5vrx_native_content_signature = 2166136261u;
    c5vrx_native_phase_boundaries = 0u;
    c5vrx_native_phase_residual_abs_sum = 0u;
    c5vrx_native_phase_residual_abs_max = 0u;
    c5vrx_native_start_control = 0u;
    c5vrx_native_final_control = 0u;
    c5vrx_native_fault_reason = 0u;
    c5vrx_native_writer_stopped_after_done = 0u;
}

static void add_native_iq_power(uint32_t value)
{
    const uint32_t before = c5vrx_native_iq_power_sum_low;
    c5vrx_native_iq_power_sum_low = before + value;
    if (c5vrx_native_iq_power_sum_low < before)
        c5vrx_native_iq_power_sum_high++;
}

static inline int32_t signed_i(uint32_t word)
{
    int32_t value = (int32_t)((word >> 10) & 0x3ffu);
    return value >= 512 ? value - 1024 : value;
}

static inline int32_t signed_q(uint32_t word)
{
    int32_t value = (int32_t)(word & 0x3ffu);
    return value >= 512 ? value - 1024 : value;
}

/* 0..pi/4 in 8-bit-turn units for ratios 0/32..32/32. Keeping this small
 * table in the LP binary avoids floating point and HP-memory lookup traffic. */
static const uint8_t phase_octant[33] = {
    0, 1, 3, 4, 5, 6, 8, 9, 10, 11, 12, 13, 15, 16, 17, 18, 19,
    20, 21, 22, 23, 24, 25, 25, 26, 27, 28, 29, 29, 30, 31, 31, 32,
};

static uint8_t phase8(uint32_t word)
{
    const int32_t x = signed_i(word);
    const int32_t y = signed_q(word);
    const uint32_t ax = x < 0 ? (uint32_t)-x : (uint32_t)x;
    const uint32_t ay = y < 0 ? (uint32_t)-y : (uint32_t)y;
    if ((ax | ay) == 0u) return 0u;
    const uint32_t base = ax >= ay ?
        phase_octant[(ay * 32u) / ax] :
        64u - phase_octant[(ax * 32u) / ay];
    if (x >= 0 && y >= 0) return (uint8_t)base;
    if (x < 0 && y >= 0) return (uint8_t)(128u - base);
    if (x < 0 && y < 0) return (uint8_t)(128u + base);
    return (uint8_t)(256u - base);
}

static inline int32_t phase_delta(uint8_t from, uint8_t to)
{
    return (int32_t)(int8_t)(uint8_t)(to - from);
}

static void observe_phase_boundary(void)
{
    volatile const uint32_t *const ram =
        (volatile const uint32_t *)(uintptr_t)0x40830000u;
    const uint8_t p0 = phase8(ram[POINTER_MASK - 1u]);
    const uint8_t p1 = phase8(ram[POINTER_MASK]);
    const uint8_t p2 = phase8(ram[0]);
    const uint8_t p3 = phase8(ram[1]);
    const int32_t before = phase_delta(p0, p1);
    const int32_t boundary = phase_delta(p1, p2);
    const int32_t after = phase_delta(p2, p3);
    const int32_t normal = (before + after) / 2;
    int32_t residual = phase_delta((uint8_t)normal, (uint8_t)boundary);
    if (residual < 0) residual = -residual;
    c5vrx_native_phase_boundaries++;
    c5vrx_native_phase_residual_abs_sum += (uint32_t)residual;
    if ((uint32_t)residual > c5vrx_native_phase_residual_abs_max)
        c5vrx_native_phase_residual_abs_max = (uint32_t)residual;
}

/* Guarded hardware-circular hypothesis. There are deliberately no calls to
 * trigger_writer() and no writes of CTRL_SW_TRIGGER after entry. ENABLE is
 * asserted once and the observer only reads control, pointer and sparse RAM. */
static void run_native_ring(void)
{
    const uint32_t duration_cycles = cycles_for_us(c5vrx_duration_us);
    volatile const uint32_t *const ram =
        (volatile const uint32_t *)(uintptr_t)0x40830000u;
    uint32_t prior_word = 0u;
    uint32_t prior_wrap_word = 0u;
    bool have_word = false;
    bool have_wrap_word = false;
    bool done_seen = false;
    uint32_t last_change_at;

    clear_native_stats();
    c5vrx_stage = 30u;
    c5vrx_state = STATE_ARMING;
    c5vrx_runs++;
    c5vrx_saved_ownership = REG32(HP_SRAM_USAGE);
    REG32(HP_SRAM_USAGE) =
        (c5vrx_saved_ownership & 0xfffef0ffu) | 0x00010200u;
    io_fence();

    c5vrx_stage = 31u;
    uint32_t control = REG32(DUMP_CTRL) | CTRL_RING_MODE;
    REG32(DUMP_CTRL) = control | CTRL_ENABLE;
    io_fence();
    c5vrx_native_enable_assertions = 1u;
    c5vrx_native_start_control = REG32(DUMP_CTRL);
    uint32_t previous = pointer();
    c5vrx_native_min_pointer = previous;
    c5vrx_native_max_pointer = previous;
    const uint32_t started = cycle_count();
    last_change_at = started;
    c5vrx_state = STATE_RUNNING;

    while ((uint32_t)(cycle_count() - started) < duration_cycles) {
        control = REG32(DUMP_CTRL);
        const uint32_t current = pointer();
        c5vrx_native_observations++;
        if ((control & CTRL_ENABLE) == 0u) c5vrx_native_enable_low++;
        if ((control & CTRL_RING_MODE) == 0u) c5vrx_native_mode_low++;
        if ((control & CTRL_SW_TRIGGER) != 0u) c5vrx_native_trigger_high++;
        if ((control & CTRL_DONE) != 0u) {
            c5vrx_native_done_observations++;
            done_seen = true;
        }
        if (current < c5vrx_native_min_pointer)
            c5vrx_native_min_pointer = current;
        if (current > c5vrx_native_max_pointer)
            c5vrx_native_max_pointer = current;

        if (current != previous) {
            c5vrx_native_pointer_changes++;
            last_change_at = cycle_count();
            if (done_seen) c5vrx_native_progress_after_done++;
            if (current < previous) {
                if (previous >= 0x3000u && current <= 0x0fffu) {
                    c5vrx_native_wraps++;
                    /* Samples 0 and 1 must belong to the new generation. If
                     * the observer caught pointer 0/1, wait only until 1 is
                     * committed; this remains far inside the same revolution. */
                    const uint32_t boundary_wait_started = cycle_count();
                    while (pointer() < 2u) {
                        if ((uint32_t)(cycle_count() -
                                boundary_wait_started) >= cycles_for_us(50u)) {
                            c5vrx_native_fault_reason = 9u;
                            goto stop_native;
                        }
                    }
                    observe_phase_boundary();
                    const uint32_t wrap_word = ram[8192u];
                    if (have_wrap_word && wrap_word != prior_wrap_word)
                        c5vrx_native_wrap_content_changes++;
                    prior_wrap_word = wrap_word;
                    have_wrap_word = true;
                } else {
                    c5vrx_native_ambiguous_backwards++;
                }
            }
            previous = current;
        }

        /* Sparse content sampling avoids a CPU-copy ring. It is telemetry
         * only: one safe lagged word per 64 pointer observations. */
        if ((c5vrx_native_observations & 63u) == 0u) {
            const uint32_t word = ram[(current - 512u) & POINTER_MASK];
            if (have_word && word != prior_word)
                c5vrx_native_content_changes++;
            prior_word = word;
            have_word = true;
            const int32_t i = signed_i(word);
            const int32_t q = signed_q(word);
            add_native_iq_power(
                (uint32_t)(i * i) + (uint32_t)(q * q));
            c5vrx_native_content_signature =
                (c5vrx_native_content_signature ^ word) * 16777619u;
            c5vrx_native_content_observations++;
        }

        if (done_seen &&
            (uint32_t)(cycle_count() - last_change_at) >= cycles_for_us(200u)) {
            c5vrx_native_writer_stopped_after_done = 1u;
            break;
        }
    }

stop_native:
    c5vrx_native_last_pointer = previous;
    c5vrx_native_final_control = REG32(DUMP_CTRL);
    if (c5vrx_native_fault_reason != 0u) {}
    else if (c5vrx_native_enable_low != 0u) c5vrx_native_fault_reason = 1u;
    else if (c5vrx_native_mode_low != 0u) c5vrx_native_fault_reason = 2u;
    else if (c5vrx_native_trigger_high != 0u) c5vrx_native_fault_reason = 3u;
    else if (c5vrx_native_ambiguous_backwards != 0u)
        c5vrx_native_fault_reason = 4u;
    else if (c5vrx_native_pointer_changes == 0u)
        c5vrx_native_fault_reason = 5u;
    else if (c5vrx_native_wraps == 0u) c5vrx_native_fault_reason = 6u;
    else if (c5vrx_native_content_changes == 0u)
        c5vrx_native_fault_reason = 7u;
    else if (c5vrx_native_writer_stopped_after_done != 0u)
        c5vrx_native_fault_reason = 8u;

    REG32(DUMP_CTRL) &= ~CTRL_ENABLE;
    io_fence();
    REG32(HP_SRAM_USAGE) = c5vrx_saved_ownership;
    io_fence();
    c5vrx_command = COMMAND_NONE;
    c5vrx_state = STATE_DONE;
    c5vrx_stage = 0u;
}
#endif

static inline uint32_t pointer(void)
{
    return REG32(DUMP_PTR_MODE) & POINTER_MASK;
}

static inline void trigger_writer(void)
{
    uint32_t control = REG32(DUMP_CTRL) & ~CTRL_ENABLE;
    REG32(DUMP_CTRL) = control;
    io_fence();
    control |= CTRL_ENABLE;
    REG32(DUMP_CTRL) = control;
    REG32(DUMP_CTRL) = control | CTRL_SW_TRIGGER;
    REG32(DUMP_CTRL) = control & ~CTRL_SW_TRIGGER;
    io_fence();
}

/* The public C5 AHB-GDMA register layout places each TX channel 0xc0 bytes
 * apart and exposes the next fetched descriptor at channel+0xf0.  PARLIO's
 * public loop builder uses contiguous 12-byte descriptors carrying at most
 * 4092 bytes, so the descriptor index is a conservative 1023-word consumer
 * position.  Its uncertainty is one descriptor and is reported, never hidden
 * as sample-exact pacing. */
static inline bool observe_consumer(uint32_t writer)
{
    const uint32_t channel = c5vrx_gdma_channel;
    const uint32_t base = c5vrx_gdma_descriptor_base;
    if (channel >= 3u || base == 0u) {
        c5vrx_consumer_descriptor_errors++;
        return false;
    }
    const uint32_t current = REG32(AHB_DMA_BASE + 0xf0u + channel * 0xc0u);
    if (current < base) {
        c5vrx_consumer_descriptor_errors++;
        return false;
    }
    const uint32_t offset = current - base;
    if ((offset % GDMA_DESC_BYTES) != 0u) {
        c5vrx_consumer_descriptor_errors++;
        return false;
    }
    const uint32_t index = offset / GDMA_DESC_BYTES;
    if (index >= GDMA_DESC_COUNT) {
        c5vrx_consumer_descriptor_errors++;
        return false;
    }
    uint32_t reader = index * GDMA_DESC_WORDS;
    if (reader >= BURST_WORDS) reader = BURST_WORDS - 1u;
    const uint32_t lead = (writer - reader) & POINTER_MASK;
    const uint32_t previous_reader = c5vrx_consumer_pointer;
    if (c5vrx_consumer_observations != 0u && reader != previous_reader) {
        c5vrx_consumer_pointer_changes++;
        if (reader < previous_reader) c5vrx_consumer_wraps++;
    }
    c5vrx_consumer_pointer = reader;
    c5vrx_consumer_lead_words = lead;
    if (lead < c5vrx_consumer_lead_min_words)
        c5vrx_consumer_lead_min_words = lead;
    if (lead > c5vrx_consumer_lead_max_words)
        c5vrx_consumer_lead_max_words = lead;
    c5vrx_consumer_observations++;
    return true;
}

static void publish(uint32_t advance, uint32_t changes, uint32_t restarts,
                    uint32_t previous, uint32_t completed,
                    uint32_t rearms, uint32_t failures,
                    uint32_t gap_total, uint32_t gap_max,
                    uint32_t last_gap)
{
    c5vrx_writer_advance = advance;
    c5vrx_pointer_changes = changes;
    c5vrx_pointer_restarts = restarts;
    c5vrx_last_pointer = previous;
    c5vrx_bursts_completed = completed;
    c5vrx_rearms_succeeded = rearms;
    c5vrx_rearm_failures = failures;
    c5vrx_gap_cycles_total = gap_total;
    c5vrx_gap_cycles_max = gap_max;
    c5vrx_last_gap_cycles = last_gap;
}

static void run_writer(uint32_t command)
{
    const bool continuous = command == COMMAND_CONTINUOUS;
    const bool enable_parlio = c5vrx_enable_parlio != 0u;
    const uint32_t duration_cycles = cycles_for_us(c5vrx_duration_us);
    const uint32_t requested_lead = c5vrx_lead_words;
    uint32_t previous;
    uint32_t advance = 0u;
    uint32_t changes = 0u;
    uint32_t restarts = 0u;
    uint32_t completed = 0u;
    uint32_t rearms = 0u;
    uint32_t failures = 0u;
    uint32_t gap_total = 0u;
    uint32_t gap_max = 0u;
    uint32_t last_gap = 0u;
    uint32_t terminal_state = STATE_DONE;
    uint32_t last_completed_at = 0u;
    uint32_t run_start = 0u;
    int32_t phase_error = 0;
    uint32_t phase_blocks = 0u;
    uint32_t last_activity_at = cycle_count();

    clear_stats();
    c5vrx_stage = 10u; /* command accepted; LP-only memory still active */
    c5vrx_state = STATE_ARMING;
    c5vrx_runs++;

    c5vrx_stage = 11u; /* about to touch SYSTEM SRAM ownership */
    c5vrx_saved_ownership = REG32(HP_SRAM_USAGE);
    REG32(HP_SRAM_USAGE) =
        (c5vrx_saved_ownership & 0xfffef0ffu) | 0x00010200u;
    io_fence();

    c5vrx_stage = 12u; /* SRAM handoff succeeded; HP is parked */
    trigger_writer();

    c5vrx_stage = 13u; /* MODEM trigger succeeded */
    previous = pointer();
    const uint32_t lead_start = cycle_count();
    while (advance < requested_lead) {
        const uint32_t current = pointer();
        /* A fresh one-shot cannot wrap before the requested half-buffer
         * lead.  Occasionally the cross-domain pointer read presents an
         * older value for one poll; treating that regression as a modulo
         * wrap fabricates almost 16384 words.  Ignore it and keep the last
         * monotonic observation. */
        if (current > previous) {
            advance += current - previous;
            changes++;
            previous = current;
            last_activity_at = cycle_count();
        }
        if ((uint32_t)(cycle_count() - lead_start) >=
                cycles_for_us(LEAD_TIMEOUT_US)) {
            terminal_state = STATE_NO_ACTIVITY;
            goto stop;
        }
    }

    c5vrx_lead_acquired = 1u;
    c5vrx_stage = 14u; /* producer lead acquired */
    /* The lead acquisition is startup priming, not part of the timed cadence
     * window. Match the proven HP kernel and measure only after this point. */
    advance = 0u;
    changes = 0u;
    restarts = 0u;
    previous = pointer();
    if (enable_parlio) {
        c5vrx_stage = 15u; /* about to touch PCR clock gate */
        REG32(PARLIO_TX_CLOCK) |= PARLIO_CLK_EN;
        io_fence();
        /* Enabling the clock with an initially empty hardware FIFO latches a
         * rempty event before GDMA/BitScrambler can deliver their first item.
         * Keep the interrupt masked across that deterministic prefill, then
         * clear only the startup latch and arm detection for real runtime
         * starvation. 512 LP cycles is ~10.7 us, far below the 8192-word RF
         * lead and long compared with the DMA/FIFO startup latency. */
        const uint32_t prefill_started = cycle_count();
        while ((uint32_t)(cycle_count() - prefill_started) <
                PARLIO_PREFILL_CYCLES) {}
        REG32(PARLIO_INT_CLR) = PARLIO_TX_FIFO_EMPTY_INT;
        REG32(PARLIO_INT_ENA) |= PARLIO_TX_FIFO_EMPTY_INT;
        io_fence();
    }
    c5vrx_stage = 16u; /* all HP peripheral accesses succeeded */
    c5vrx_state = STATE_RUNNING;

    run_start = cycle_count();
    for (;;) {
        const uint32_t current = pointer();
        /* Rearm resets are consumed explicitly below and replace previous
         * with the first accepted pointer. Any other backward observation is
         * stale/metastable and must not become a synthetic full-block delta. */
        if (current > previous) {
            advance += current - previous;
            changes++;
            previous = current;
            last_activity_at = cycle_count();
        }
        if (enable_parlio) (void)observe_consumer(current);

        uint32_t control = REG32(DUMP_CTRL);
        if ((control & CTRL_DONE) != 0u && current == POINTER_MASK) {
            completed++;
            const uint32_t completed_at = cycle_count();
            if (last_completed_at != 0u) {
                const uint32_t period = completed_at - last_completed_at;
                c5vrx_block_period_last = period;
                if (c5vrx_block_period_min == 0u ||
                    period < c5vrx_block_period_min) {
                    c5vrx_block_period_min = period;
                }
                if (period > c5vrx_block_period_max) {
                    c5vrx_block_period_max = period;
                }
                if (c5vrx_expected_block_cycles != 0u) {
                    phase_error += (int32_t)period -
                        (int32_t)c5vrx_expected_block_cycles;
                    phase_blocks++;
                    c5vrx_phase_error_cycles = phase_error;
                    c5vrx_phase_window_blocks = phase_blocks;
                    /* A short rolling window cannot overflow and exposes
                     * slow producer/consumer drift without touching either
                     * high-rate datapath. */
                    if (phase_blocks >= 1024u) {
                        phase_error = 0;
                        phase_blocks = 0u;
                    }
                }
            }
            last_completed_at = completed_at;
            trigger_writer();

            /* Departure from 16383 proves the edge-sensitive rearm worked. */
            for (;;) {
                const uint32_t restarted = pointer();
                if (restarted != POINTER_MASK) {
                    last_gap = cycle_count() - completed_at;
                    gap_total += last_gap;
                    if (last_gap > gap_max) gap_max = last_gap;
                    rearms++;
                    previous = restarted;
                    restarts++;
                    publish(advance, changes, restarts, previous, completed,
                            rearms, failures, gap_total, gap_max, last_gap);
                    break;
                }
                if ((uint32_t)(cycle_count() - completed_at) >=
                        cycles_for_us(REARM_TIMEOUT_US)) {
                    failures++;
                    terminal_state = STATE_NO_ACTIVITY;
                    goto stop;
                }
                if (continuous && c5vrx_command == COMMAND_STOP) {
                    terminal_state = STATE_STOPPED;
                    goto stop;
                }
            }
        }

        if (!continuous &&
            (uint32_t)(cycle_count() - run_start) >= duration_cycles) {
            terminal_state = STATE_DONE;
            break;
        }
        if (continuous && c5vrx_command == COMMAND_STOP) {
            terminal_state = STATE_STOPPED;
            break;
        }
        if ((uint32_t)(cycle_count() - last_activity_at) >=
                cycles_for_us(ACTIVITY_TIMEOUT_US)) {
            terminal_state = STATE_NO_ACTIVITY;
            break;
        }
    }

stop:
    c5vrx_stage = 20u;
    if (run_start != 0u) c5vrx_run_cycles = cycle_count() - run_start;
    if (enable_parlio) {
        REG32(PARLIO_INT_ENA) &= ~PARLIO_TX_FIFO_EMPTY_INT;
        REG32(PARLIO_TX_CLOCK) &= ~PARLIO_CLK_EN;
        REG32(PARLIO_INT_CLR) = PARLIO_TX_FIFO_EMPTY_INT;
        io_fence();
    }
    c5vrx_final_control = REG32(DUMP_CTRL);
    REG32(DUMP_CTRL) &= ~CTRL_ENABLE;
    io_fence();
    REG32(HP_SRAM_USAGE) = c5vrx_saved_ownership;
    io_fence();
    publish(advance, changes, restarts, previous, completed, rearms, failures,
            gap_total, gap_max, last_gap);
    c5vrx_command = COMMAND_NONE;
    c5vrx_state = terminal_state;
    c5vrx_stage = 0u;
}

int main(void)
{
    c5vrx_fault_cause = 0u;
    c5vrx_fault_address = 0u;
    c5vrx_fault_pc = 0u;
    c5vrx_saved_ownership = 0u;
    c5vrx_stage = 0u;
    clear_stats();
    c5vrx_command = COMMAND_NONE;
    c5vrx_state = STATE_READY;

    for (;;) {
        const uint32_t command = c5vrx_command;
        if (command == COMMAND_BOUNDED || command == COMMAND_CONTINUOUS) {
            c5vrx_command = COMMAND_NONE;
            run_writer(command);
        }
#if CONFIG_C5VRX_EXPERIMENTAL_NATIVE_RING_PROBE
        else if (command == COMMAND_NATIVE_RING) {
            c5vrx_command = COMMAND_NONE;
            run_native_ring();
        }
#endif
        /* Intentionally remain resident: HP commands do not restart LP code. */
        __asm__ __volatile__("nop");
    }
}

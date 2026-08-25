/* SPDX-License-Identifier: GPL-3.0-only */

/*
 * Continuous A1 RF writer service for the ESP32-C5 LP core.
 *
 * The undocumented C5 RF dump writer is a 16384-word one-shot.  This program
 * runs from LP SRAM, outside the HP-SRAM window lent to the MAC, and rearms
 * that one-shot at every terminal pointer.  Unlike the old HP-core probe it
 * has no duration limit in continuous mode and does not mask HP interrupts.
 */

#include <stdbool.h>
#include <stdint.h>

#include "ulp_lp_core_cpu_freq_shared.h"

#define REG32(address) (*(volatile uint32_t *)(uintptr_t)(address))

#define DUMP_CTRL       0x600a9004u
#define DUMP_PTR_MODE   0x600a9008u
#define HP_SRAM_USAGE   0x60095004u
#define PARLIO_TX_CLOCK 0x600960b4u

#define CTRL_ENABLE     0x80000000u
#define CTRL_SW_TRIGGER 0x00080000u
#define CTRL_DONE       0x00040000u
#define PARLIO_CLK_EN   0x00040000u
#define POINTER_MASK    0x00003fffu
#define BURST_WORDS     16384u

#define COMMAND_NONE       0u
#define COMMAND_BOUNDED    1u
#define COMMAND_CONTINUOUS 2u
#define COMMAND_STOP       3u

#define STATE_BOOT        0u
#define STATE_READY       1u
#define STATE_ARMING      2u
#define STATE_RUNNING     3u
#define STATE_DONE        4u
#define STATE_NO_ACTIVITY 5u
#define STATE_REARM_ERROR 6u
#define STATE_STOPPED     7u

#define LEAD_TIMEOUT_US  50000u
#define REARM_TIMEOUT_US  5000u

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
volatile int32_t c5vrx_phase_error_cycles;
volatile uint32_t c5vrx_phase_window_blocks;

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
    c5vrx_phase_error_cycles = 0;
    c5vrx_phase_window_blocks = 0u;
}

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
    const uint32_t saved_ownership = REG32(HP_SRAM_USAGE);
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
    int32_t phase_error = 0;
    uint32_t phase_blocks = 0u;

    clear_stats();
    c5vrx_state = STATE_ARMING;
    c5vrx_runs++;

    /* Grant the vendor-observed two-bank/64-KiB dump window to the MAC. */
    REG32(HP_SRAM_USAGE) =
        (saved_ownership & 0xfffef0ffu) | 0x00010200u;
    io_fence();
    trigger_writer();

    previous = pointer();
    const uint32_t lead_start = cycle_count();
    while (advance < requested_lead) {
        const uint32_t current = pointer();
        const uint32_t delta = (current - previous) & POINTER_MASK;
        if (delta != 0u) {
            advance += delta;
            changes++;
            if (current < previous) restarts++;
            previous = current;
        }
        if ((uint32_t)(cycle_count() - lead_start) >=
                cycles_for_us(LEAD_TIMEOUT_US)) {
            terminal_state = STATE_NO_ACTIVITY;
            goto stop;
        }
    }

    c5vrx_lead_acquired = 1u;
    /* The lead acquisition is startup priming, not part of the timed cadence
     * window. Match the proven HP kernel and measure only after this point. */
    advance = 0u;
    changes = 0u;
    restarts = 0u;
    previous = pointer();
    if (enable_parlio) {
        REG32(PARLIO_TX_CLOCK) |= PARLIO_CLK_EN;
        io_fence();
    }
    c5vrx_state = STATE_RUNNING;

    const uint32_t run_start = cycle_count();
    for (;;) {
        const uint32_t current = pointer();
        const uint32_t delta = (current - previous) & POINTER_MASK;
        if (delta != 0u) {
            advance += delta;
            changes++;
            if (current < previous) restarts++;
            previous = current;
        }

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
    }

stop:
    if (enable_parlio) {
        REG32(PARLIO_TX_CLOCK) &= ~PARLIO_CLK_EN;
    }
    c5vrx_final_control = REG32(DUMP_CTRL);
    REG32(DUMP_CTRL) &= ~CTRL_ENABLE;
    io_fence();
    REG32(HP_SRAM_USAGE) = saved_ownership;
    io_fence();
    publish(advance, changes, restarts, previous, completed, rearms, failures,
            gap_total, gap_max, last_gap);
    c5vrx_command = COMMAND_NONE;
    c5vrx_state = terminal_state;
}

int main(void)
{
    clear_stats();
    c5vrx_command = COMMAND_NONE;
    c5vrx_state = STATE_READY;

    for (;;) {
        const uint32_t command = c5vrx_command;
        if (command == COMMAND_BOUNDED || command == COMMAND_CONTINUOUS) {
            c5vrx_command = COMMAND_NONE;
            run_writer(command);
        }
        /* Intentionally remain resident: HP commands do not restart LP code. */
        __asm__ __volatile__("nop");
    }
}

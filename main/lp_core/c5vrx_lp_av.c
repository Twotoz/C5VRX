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

#include "riscv/rvruntime-frames.h"
#include "ulp_lp_core_cpu_freq_shared.h"

#define REG32(address) (*(volatile uint32_t *)(uintptr_t)(address))

#define DUMP_CTRL       0x600a9004u
#define DUMP_PTR_MODE   0x600a9008u
#define PAU_REGDMA_CONF 0x60093000u
#define PAU_INT_RAW     0x6009301cu
#define PAU_INT_CLR     0x60093020u
#define PAU_CURRENT_LINK 0x6009300cu
#define PAU_PERI_ADDR    0x60093010u
#define PAU_MEM_ADDR     0x60093014u
#define HP_SRAM_USAGE   0x60095004u
#define PARLIO_TX_CLOCK 0x600960b4u
#define PARLIO_INT_ENA  0x60015028u
#define PARLIO_INT_CLR  0x60015034u
#define AHB_DMA_BASE    0x60080000u

#define CTRL_ENABLE     0x80000000u
#define CTRL_SW_TRIGGER 0x00080000u
#define CTRL_DONE       0x00040000u
#define PAU_START       0x00000008u
#define PAU_TO_MEM      0x00000010u
#define PAU_LINK_SEL_M  0x000001e0u
#define PAU_LINK3       0x00000060u
#define PAU_DONE_RAW    0x00000001u
#define PAU_ERROR_RAW   0x00000002u
#define PAU_TIMEOUT_CYCLES 8192u
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
volatile uint32_t c5vrx_hardware_rearm;
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
volatile uint32_t c5vrx_regdma_conf;
volatile uint32_t c5vrx_regdma_int_raw;
volatile uint32_t c5vrx_regdma_current_link;
volatile uint32_t c5vrx_regdma_peri_addr;
volatile uint32_t c5vrx_regdma_mem_addr;
volatile uint32_t c5vrx_regdma_timed_out;
volatile uint32_t c5vrx_regdma_link_root;

/* REGDMA must fetch its nodes after HP SRAM has been switched from CPU use to
 * MAC-dump use. Keep the finite four-write restore chain in LP SRAM beside
 * this program. A WRITE node is seven words: two software-stat words, the
 * hardware head, then next/register/value/mask. Hardware entry addresses
 * point at the head (word 2), not at the software-stat prefix. */
#define REGDMA_WRITE_HEAD       0x40020000u
#define REGDMA_WRITE_HEAD_EOF   0xc0020000u
volatile uint32_t c5vrx_regdma_nodes[4][7];

static inline void io_fence(void);

static void prepare_regdma_chain(void)
{
    for (uint32_t i = 0u; i < 4u; ++i) {
        for (uint32_t j = 0u; j < 7u; ++j) {
            c5vrx_regdma_nodes[i][j] = 0u;
        }
        c5vrx_regdma_nodes[i][2] = i == 3u ?
            REGDMA_WRITE_HEAD_EOF : REGDMA_WRITE_HEAD;
        c5vrx_regdma_nodes[i][3] = i == 3u ? 0u :
            (uint32_t)(uintptr_t)&c5vrx_regdma_nodes[i + 1u][2];
        c5vrx_regdma_nodes[i][4] = DUMP_CTRL;
    }
    c5vrx_regdma_nodes[0][5] = 0u;
    c5vrx_regdma_nodes[0][6] = CTRL_ENABLE;
    c5vrx_regdma_nodes[1][5] = CTRL_ENABLE;
    c5vrx_regdma_nodes[1][6] = CTRL_ENABLE;
    c5vrx_regdma_nodes[2][5] = CTRL_SW_TRIGGER;
    c5vrx_regdma_nodes[2][6] = CTRL_SW_TRIGGER;
    c5vrx_regdma_nodes[3][5] = 0u;
    c5vrx_regdma_nodes[3][6] = CTRL_SW_TRIGGER;
    c5vrx_regdma_link_root =
        (uint32_t)(uintptr_t)&c5vrx_regdma_nodes[0][2];
    io_fence();
}

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
    c5vrx_regdma_conf = 0u;
    c5vrx_regdma_int_raw = 0u;
    c5vrx_regdma_current_link = 0u;
    c5vrx_regdma_peri_addr = 0u;
    c5vrx_regdma_mem_addr = 0u;
    c5vrx_regdma_timed_out = 0u;
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

/* Link 3 is prepared by HP before SRAM ownership is lent to RF. RF DONE is
 * not a documented PAU ETM source, so LP starts the finite link only after
 * the proven DONE+terminal-pointer condition. REGDMA performs the four
 * timing-sensitive modem writes and LP merely checks completion. */
static bool trigger_regdma_rearm(void)
{
    REG32(PAU_INT_CLR) = PAU_DONE_RAW | PAU_ERROR_RAW;
    uint32_t conf = REG32(PAU_REGDMA_CONF);
    conf &= ~(PAU_START | PAU_TO_MEM | PAU_LINK_SEL_M);
    conf |= PAU_LINK3;
    REG32(PAU_REGDMA_CONF) = conf;
    io_fence();
    REG32(PAU_REGDMA_CONF) = conf | PAU_START;
    io_fence();

    const uint32_t started = cycle_count();
    uint32_t raw;
    do {
        raw = REG32(PAU_INT_RAW);
        if ((raw & PAU_ERROR_RAW) != 0u) break;
    } while ((raw & PAU_DONE_RAW) == 0u &&
             (uint32_t)(cycle_count() - started) < PAU_TIMEOUT_CYCLES);

    c5vrx_regdma_conf = REG32(PAU_REGDMA_CONF);
    c5vrx_regdma_int_raw = raw;
    c5vrx_regdma_current_link = REG32(PAU_CURRENT_LINK);
    c5vrx_regdma_peri_addr = REG32(PAU_PERI_ADDR);
    c5vrx_regdma_mem_addr = REG32(PAU_MEM_ADDR);
    c5vrx_regdma_timed_out =
        (raw & (PAU_DONE_RAW | PAU_ERROR_RAW)) == 0u ? 1u : 0u;

    REG32(PAU_REGDMA_CONF) = conf & ~PAU_LINK_SEL_M;
    if ((raw & PAU_ERROR_RAW) == 0u) {
        REG32(PAU_INT_CLR) = PAU_DONE_RAW;
    }
    io_fence();
    return (raw & PAU_DONE_RAW) != 0u &&
        (raw & PAU_ERROR_RAW) == 0u;
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
    const bool hardware_rearm = c5vrx_hardware_rearm != 0u;
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
        if ((control & CTRL_DONE) != 0u &&
            current == POINTER_MASK) {
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
            const bool rearm_started = hardware_rearm ?
                trigger_regdma_rearm() : (trigger_writer(), true);
            if (!rearm_started) {
                failures++;
                terminal_state = STATE_REARM_ERROR;
                goto stop;
            }

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
    prepare_regdma_chain();
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

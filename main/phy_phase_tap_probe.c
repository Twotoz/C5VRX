/* Experimental PHY diagnostic-bus explorer. No processing in the live IQ path.
 * Reuses the four register states previously tested in c5vrx2 diagnostics.
 * A CPU GPIO snapshot is NOT a 40 MS/s synchronous capture: a plausible
 * candidate requires a later source-clocked PARLIO validation. */
#include "phy_phase_tap_probe.h"

#include <inttypes.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>

#include "esp_rom_gpio.h"
#include "esp_timer.h"
#include "soc/gpio_reg.h"
#include "soc/gpio_sig_map.h"
#include "modem/modem_syscon_reg.h"

#define DIAG_FIX_REG      0x600a9404u
#define DIAG_EXCHANGE_REG 0x600a9408u
#define TRACE_SAMPLES     512u
#define DIAG_LANES        32u
#define CAPTURE_WIDTH     8u

#define REG32(address) (*(volatile uint32_t *)(uintptr_t)(address))

/* Same eight routed XIAO pins and raw-Q4/I4 lanes as main/rf.c. */
static const uint8_t s_pins[CAPTURE_WIDTH] = {1, 0, 25, 7, 10, 5, 3, 4};
static const uint8_t s_iq_lanes[CAPTURE_WIDTH] = {6, 7, 8, 9, 16, 17, 18, 19};
static uint8_t s_trace[TRACE_SAMPLES];

static inline void io_fence(void)
{
    __asm__ __volatile__("fence iorw, iorw" ::: "memory");
}

static void route_lanes(unsigned first, bool reference)
{
    for (unsigned bit = 0; bit < CAPTURE_WIDTH; ++bit) {
        const unsigned lane = reference ? s_iq_lanes[bit] : first + bit;
        esp_rom_gpio_connect_out_signal(s_pins[bit], MODEM_DIAG0_IDX + lane,
                                         false, false);
    }
    io_fence();
}

static void capture_and_print(unsigned config, unsigned first, bool reference)
{
    /* Discard samples taken while changing the output-matrix selection. */
    for (unsigned i = 0; i < 128; ++i) (void)REG32(GPIO_IN_REG);
    const int64_t start = esp_timer_get_time();
    for (unsigned i = 0; i < TRACE_SAMPLES; ++i) {
        const uint32_t gpio = REG32(GPIO_IN_REG);
        uint8_t packed = 0;
        for (unsigned bit = 0; bit < CAPTURE_WIDTH; ++bit)
            packed |= ((gpio >> s_pins[bit]) & 1u) << bit;
        s_trace[i] = packed;
    }
    const int64_t elapsed = esp_timer_get_time() - start;
    uint32_t transitions[CAPTURE_WIDTH] = {0};
    uint32_t ever_high = 0, ever_low = 0;
    for (unsigned i = 0; i < TRACE_SAMPLES; ++i) {
        const uint8_t value = s_trace[i];
        ever_high |= value;
        ever_low |= (uint8_t)~value;
        if (i) {
            for (unsigned bit = 0; bit < CAPTURE_WIDTH; ++bit)
                transitions[bit] += ((value ^ s_trace[i - 1]) >> bit) & 1u;
        }
    }

    printf("PHY_TAP config=%u lanes=%s%u..%u samples=%u elapsed_us=%lld "
           "vary=0x%02lx transitions=", config, reference ? "IQ_REF/" : "",
           first, first + CAPTURE_WIDTH - 1u, TRACE_SAMPLES,
           (long long)elapsed, (unsigned long)(ever_high & ever_low));
    for (unsigned bit = 0; bit < CAPTURE_WIDTH; ++bit)
        printf("%s%lu", bit ? "," : "", (unsigned long)transitions[bit]);
    printf(" hex=");
    for (unsigned i = 0; i < TRACE_SAMPLES; ++i) printf("%02x", s_trace[i]);
    printf("\n");
}

#define DUMP_CTRL        0x600a9004u
#define DUMP_PTR_MODE    0x600a9008u
#define HP_SRAM_USAGE    0x60095004u
#define DUMP_BASE_ADDR   0x40830000u
#define DUMP_WORDS       8192u
#define Q6_GPIO_SAMPLES  512u
#define CTRL_ENABLE      0x80000000u
#define CTRL_DUMP_FIRST  0x00020000u
#define CHUNK_BYTES      1024u

static const uint8_t s_q6_lanes[CAPTURE_WIDTH] = {4, 5, 6, 7, 8, 9, 16, 17};
static uint32_t s_gpio_raw[Q6_GPIO_SAMPLES];
static uint8_t  s_gpio_packed[Q6_GPIO_SAMPLES];

static inline uint32_t get_cycle_count(void)
{
    uint32_t c;
    __asm__ __volatile__("rdcycle %0" : "=r"(c));
    return c;
}

static void q6_dump_probe_run(void)
{
    /* Route DIAG[4, 5, 6, 7, 8, 9, 16, 17] to the 8 XIAO GPIO pins.
     * Candidate Q[4:5] on bits 0..1, proven Q[6:9] on bits 2..5,
     * proven I[6:7] on bits 6..7. */
    for (unsigned bit = 0; bit < CAPTURE_WIDTH; ++bit) {
        esp_rom_gpio_connect_out_signal(s_pins[bit],
                                        MODEM_DIAG0_IDX + s_q6_lanes[bit],
                                        false, false);
    }
    io_fence();

    /* Discard settling samples after matrix remap. */
    for (unsigned i = 0; i < 128; ++i) (void)REG32(GPIO_IN_REG);

    /* Save and disable machine interrupts during the ~100 us hardware dump window. */
    uint32_t saved_mstatus;
    __asm__ __volatile__("csrrc %0, mstatus, %1"
                         : "=r"(saved_mstatus)
                         : "r"(0x8u)
                         : "memory");

    /* Grant MAC ownership of HP SRAM dump bank (0x40830000). */
    const uint32_t saved_sram_usage = REG32(HP_SRAM_USAGE);
    REG32(HP_SRAM_USAGE) = (saved_sram_usage & 0xfffef0ffu) | 0x00010200u;
    io_fence();

    /* Arm dump engine with DUMP_WORDS in circular pre-trigger mode. */
    uint32_t ctrl = REG32(DUMP_CTRL);
    ctrl &= ~(CTRL_ENABLE | 0x00080000u | 0x00040000u); /* clear ENABLE, START, DONE */
    ctrl |= CTRL_DUMP_FIRST;
    ctrl = (ctrl & ~0x0001ffffu) | DUMP_WORDS;
    REG32(DUMP_CTRL) = ctrl | CTRL_ENABLE;
    io_fence();

    /* Sample GPIO_IN_REG in an unrolled, tight CPU loop (~5 MS/s). */
    const uint32_t t_begin = get_cycle_count();
    for (unsigned i = 0; i < Q6_GPIO_SAMPLES; ++i) {
        s_gpio_raw[i] = REG32(GPIO_IN_REG);
    }
    const uint32_t t_end = get_cycle_count();
    io_fence();

    /* Read write pointer at halt and disable dump. */
    const uint32_t stop_ptr = REG32(DUMP_PTR_MODE) & (DUMP_WORDS - 1u);
    REG32(DUMP_CTRL) = ctrl; /* clear CTRL_ENABLE */
    io_fence();

    /* Restore CPU ownership of HP SRAM. */
    REG32(HP_SRAM_USAGE) = saved_sram_usage;
    io_fence();

    /* Restore machine interrupt state. */
    if ((saved_mstatus & 0x8u) != 0u) {
        __asm__ __volatile__("csrs mstatus, %0" : : "r"(0x8u) : "memory");
    }

    const uint32_t elapsed_us = (t_end - t_begin) / 240u;

    /* Pack GPIO bits: bit 0..7 match s_pins / s_q6_lanes. */
    for (unsigned i = 0; i < Q6_GPIO_SAMPLES; ++i) {
        const uint32_t gpio = s_gpio_raw[i];
        uint8_t packed = 0;
        for (unsigned bit = 0; bit < CAPTURE_WIDTH; ++bit) {
            packed |= ((gpio >> s_pins[bit]) & 1u) << bit;
        }
        s_gpio_packed[i] = packed;
    }

    /* Print header delimiter and metadata for analysis tool. */
    printf("Q6_DUMP BEGIN samples=%u dump_words=%u stop_ptr=%" PRIu32 " elapsed_us=%" PRIu32 "\n",
           Q6_GPIO_SAMPLES, DUMP_WORDS, stop_ptr, elapsed_us);

    /* Print packed GPIO trace. */
    printf("Q6_GPIO hex=");
    for (unsigned i = 0; i < Q6_GPIO_SAMPLES; ++i) {
        printf("%02x", s_gpio_packed[i]);
    }
    printf("\n");

    /* Print post-stop dump ring bytes from 0x40830000 in 1 KiB chunks.
     * Each 32-bit word unpacks:
     *   dump Q[4:9] in bits 0..5
     *   dump I[6:7] in bits 6..7 */
    volatile const uint32_t *dump_sram = (volatile const uint32_t *)DUMP_BASE_ADDR;
    const unsigned num_chunks = DUMP_WORDS / CHUNK_BYTES;
    for (unsigned c = 0; c < num_chunks; ++c) {
        printf("Q6_RING chunk=%u hex=", c);
        const unsigned start_idx = c * CHUNK_BYTES;
        for (unsigned i = 0; i < CHUNK_BYTES; ++i) {
            const uint32_t w = dump_sram[start_idx + i];
            const uint8_t packed = ((w >> 4) & 0x3fu) | (((w >> 16) & 0x03u) << 6);
            printf("%02x", packed);
        }
        printf("\n");
    }
    printf("Q6_DUMP END\n");

    /* Restore reference IQ routing. */
    route_lanes(0, true);
}

void phy_phase_tap_probe_run(void)
{
    const uint32_t saved_fix = REG32(DIAG_FIX_REG);
    const uint32_t saved_exchange = REG32(DIAG_EXCHANGE_REG);
    const uint32_t saved_test = REG32(MODEM_SYSCON_TEST_CONF_REG);

    printf("PHY_TAP BEGIN boot_only=1 cpu_snapshot=1 synchronous=0 "
           "iq_reference_lanes=6,7,8,9,16,17,18,19\n");
    for (unsigned config = 0; config < 4; ++config) {
        uint32_t fix = saved_fix;
        uint32_t exchange = saved_exchange;
        uint32_t test = saved_test;
        if (config == 1 || config == 2) exchange = 2u;
        if (config == 2) fix = (fix & ~0x3ffu) | 0x14eu;
        if (config == 3)
            test |= MODEM_SYSCON_FPGA_DEBUG_CLKSWITCH |
                    MODEM_SYSCON_FPGA_DEBUG_CLK80;
        REG32(DIAG_FIX_REG) = fix;
        REG32(DIAG_EXCHANGE_REG) = exchange;
        REG32(MODEM_SYSCON_TEST_CONF_REG) = test;
        io_fence();
        printf("PHY_TAP selector=%u fix=0x%08" PRIx32 " exchange=0x%08" PRIx32
               " test=0x%08" PRIx32 "\n", config, fix, exchange, test);
        route_lanes(0, true);
        capture_and_print(config, 0, true);
        for (unsigned first = 0; first < DIAG_LANES; first += CAPTURE_WIDTH) {
            route_lanes(first, false);
            capture_and_print(config, first, false);
        }
    }

    REG32(DIAG_FIX_REG) = saved_fix;
    REG32(DIAG_EXCHANGE_REG) = saved_exchange;
    REG32(MODEM_SYSCON_TEST_CONF_REG) = saved_test;
    route_lanes(0, true);
    io_fence();

    /* Execute the decisive aligned DIAG[4:5] <-> dump Q[4:5] correlation test. */
    q6_dump_probe_run();

    printf("PHY_TAP END restored=1 candidate_status=UNVERIFIED\n");
}

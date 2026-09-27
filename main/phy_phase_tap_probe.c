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
    printf("PHY_TAP END restored=1 candidate_status=UNVERIFIED\n");
}

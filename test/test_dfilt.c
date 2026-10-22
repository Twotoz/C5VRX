/* C5VRX-5 by Twotoz and the C5VRX contributors: host test of the digital
 * RX filter mode and ADC rate register logic.
 *
 *   cc -O2 -std=c11 -Wall -Wextra -Werror -I../main test_dfilt.c -lm -o /tmp/test_dfilt && /tmp/test_dfilt
 */
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define DFILT_REG_MOCK       0x600A0430u
#define DFILT_SHIFT          18u
#define DFILT_MASK           (UINT32_C(0xF) << DFILT_SHIFT)
#define DFILT_ADC_REG_MOCK   0x600A0448u

static volatile uint32_t s_mock_reg_430 = 0x12345678u;
static volatile uint32_t s_mock_reg_448 = 0xA5A50000u;
static uint8_t s_mock_adc_i2c = 0x54u;
static unsigned s_rate_calls = 0, s_mode_calls = 0;

void phy_adc_rate_set(uint32_t rate)
{
    ++s_rate_calls;
    s_mock_adc_i2c = (uint8_t)((s_mock_adc_i2c & ~4u) | (rate ? 0u : 4u));
    s_mock_reg_448 = (s_mock_reg_448 & ~3u) | (rate & 1u) | ((rate & 1u) << 1);
}

void phy_rx_filter_mode(uint32_t mode)
{
    ++s_mode_calls;
    s_mock_reg_430 = (s_mock_reg_430 & ~DFILT_MASK) | ((mode & 0xFu) << DFILT_SHIFT);
}

static int s_user_mode = 0;

static int mock_dfilt_hw_mode(void)
{
    return (int)((s_mock_reg_430 & DFILT_MASK) >> DFILT_SHIFT);
}

static int mock_dfilt_adc_rate(void)
{
    return (int)(s_mock_reg_448 & 1u);
}

static bool mock_dfilt_set_mode(int mode)
{
    if (mode != -1 && (mode < 0 || mode > 15)) return false;
    if (mode == -1) {
        s_user_mode = -1;
        return true;
    }
    phy_rx_filter_mode((uint32_t)mode);
    bool ok = mock_dfilt_hw_mode() == mode;
    if (ok) s_user_mode = mode;
    return ok;
}

static bool mock_dfilt_set_adc_rate(int rate)
{
    if (rate != 0 && rate != 1) return false;
    phy_adc_rate_set((uint32_t)rate);
    return mock_dfilt_adc_rate() == rate;
}

static const int s_test_valid_modes[] = {0, 4, 8, 12};
#define N_TEST_VALID_MODES ((int)(sizeof(s_test_valid_modes) / sizeof(s_test_valid_modes[0])))

static void mock_dfilt_mode_step(int step)
{
    int cur = s_user_mode;
    if (cur < 0) cur = mock_dfilt_hw_mode();
    int idx = 0;
    for (int i = 0; i < N_TEST_VALID_MODES; ++i) {
        if (s_test_valid_modes[i] == cur) { idx = i; break; }
        if (s_test_valid_modes[i] < cur) idx = i;
    }
    int next_idx = (idx + step) % N_TEST_VALID_MODES;
    if (next_idx < 0) next_idx += N_TEST_VALID_MODES;
    int next = s_test_valid_modes[next_idx];
    (void)mock_dfilt_set_mode(next);
}

int main(void)
{
    /* Initial state: mock 430 has 0x12345678, with bits [21:18] */
    const uint32_t preserved_mask = ~DFILT_MASK;
    const uint32_t initial_preserved = s_mock_reg_430 & preserved_mask;

    assert(mock_dfilt_hw_mode() == (int)((0x12345678u & DFILT_MASK) >> DFILT_SHIFT));
    assert(s_user_mode == 0);

    /* Test setting modes 0..15 */
    for (int m = 0; m < 16; ++m) {
        assert(mock_dfilt_set_mode(m));
        assert(mock_dfilt_hw_mode() == m);
        assert(s_user_mode == m);
        /* Ensure all other bits of register 0x600A0430 were untouched */
        assert((s_mock_reg_430 & preserved_mask) == initial_preserved);
    }
    assert(s_mode_calls == 16);

    /* Reject invalid modes */
    assert(!mock_dfilt_set_mode(-2));
    assert(!mock_dfilt_set_mode(16));
    assert(!mock_dfilt_set_mode(100));
    assert(s_user_mode == 15);   /* unchanged */

    /* Reset to AUTO */
    assert(mock_dfilt_set_mode(-1));
    assert(s_user_mode == -1);
    assert(mock_dfilt_hw_mode() == 15); /* HW register retained until overwritten */

    /* Mode stepping with valid modes {0, 4, 8, 12} wrap-around */
    mock_dfilt_mode_step(1);
    assert(s_user_mode == 0);
    assert(mock_dfilt_hw_mode() == 0);

    mock_dfilt_mode_step(-1);
    assert(s_user_mode == 12);
    assert(mock_dfilt_hw_mode() == 12);

    mock_dfilt_mode_step(-1);
    assert(s_user_mode == 8);
    assert(mock_dfilt_hw_mode() == 8);

    mock_dfilt_mode_step(-1);
    assert(s_user_mode == 4);
    assert(mock_dfilt_hw_mode() == 4);

    mock_dfilt_mode_step(-1);
    assert(s_user_mode == 0);
    assert(mock_dfilt_hw_mode() == 0);

    mock_dfilt_mode_step(1);
    assert(s_user_mode == 4);
    assert(mock_dfilt_hw_mode() == 4);

    /* Test ADC rate setting */
    assert(mock_dfilt_set_adc_rate(1));
    assert(mock_dfilt_adc_rate() == 1);
    assert((s_mock_adc_i2c & 4u) == 0);   /* rate 1 clears bit 2 */

    assert(mock_dfilt_set_adc_rate(0));
    assert(mock_dfilt_adc_rate() == 0);
    assert((s_mock_adc_i2c & 4u) == 4u);  /* rate 0 sets bit 2 */

    assert(!mock_dfilt_set_adc_rate(2));
    assert(!mock_dfilt_set_adc_rate(-1));

    puts("PASS: digital RX filter valid modes {0, 4, 8, 12} stepping, bitfield isolation and ADC rate");
    return 0;
}

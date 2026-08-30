/* SPDX-License-Identifier: GPL-3.0-only */
#include <assert.h>
#include <stdio.h>
#include "c5vrx_dac.h"

int main(void)
{
    assert(c5vrx_dac_voltage_uv[0] == 0u);
    assert(c5vrx_dac_voltage_uv[18] == 296817u);
    assert(c5vrx_dac_voltage_uv[31] == 498750u);
    assert(c5vrx_dac_voltage_uv[32] == 518750u);
    assert(c5vrx_dac_voltage_uv[62] == 1002317u);
    assert(c5vrx_dac_voltage_uv[63] == 1017500u);
    assert(c5vrx_dac_nearest_code_mv(300u) == 18u);
    assert(c5vrx_dac_nearest_code_mv(1000u) == 62u);
    puts("C5VRX_DAC_MODEL result=PASS");
    return 0;
}

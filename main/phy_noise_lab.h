/* C5VRX by Twotoz and contributors: explicit, nonpersistent bench lab. */
#pragma once
#include <stdbool.h>
void phy_noise_lab_run(bool (*context_ok)(void), unsigned dwell_ms, unsigned suite);

/* C5VRX by Twotoz and contributors: explicit, nonpersistent bench lab. */
#pragma once
#include <stdbool.h>
#include <stdint.h>
bool phy_noise_lab_ack_byte(uint8_t byte); /* USB reader: consume framed lab ACK */
void phy_noise_lab_run(bool (*context_ok)(void), unsigned dwell_ms, unsigned suite);

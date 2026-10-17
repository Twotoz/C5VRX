/* C5VRX by Twotoz/contributors: finite native-comparator bench LUT mapping.
 * The comparator reads the UNSCALED canonical decoder code, independently
 * of contrast. Insert its flag only AFTER ordinary output scaling. Physical
 * DAC bit0 must separately be clamped LOW before this LUT is streamed. */
#pragma once
#include "output_level.h"
#define SYNC_FLAG_THRESHOLD 19u
static inline uint16_t sync_flag_entry(uint16_t canonical,unsigned output,bool *clipped)
{
    uint16_t scaled=output_entry(canonical,output,clipped);
    return (uint16_t)((scaled & ~1u) | ((canonical & 63u)<SYNC_FLAG_THRESHOLD));
}

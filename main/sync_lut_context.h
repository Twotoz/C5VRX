/* C5VRX by Twotoz/contributors: validate a complete output/LUT binding.
 * Run outside streaming deadlines, under the caller's stable generation.
 * Token/tracker bits and every mapped DAC word must match the reference. */
#pragma once
#include "output_level.h"
#include <stddef.h>
static inline bool sync_lut_context_matches(const uint16_t *active,
                                            const uint16_t *reference,
                                            unsigned output)
{
    if (!active || !reference || output >= OUTPUT_LEVELS) return false;
    for (unsigned i=0;i<1024u;++i)
        if (active[i] != output_entry(reference[i],output,NULL)) return false;
    return true;
}

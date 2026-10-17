/* C5VRX by Twotoz/contributors: validate a complete output/LUT binding.
 * Run outside streaming deadlines, under the caller's stable generation.
 * Token/tracker bits and every mapped DAC word must match the reference. */
#pragma once
#include "output_level.h"
#include <stddef.h>
#include <string.h>
static inline bool sync_lut_context_matches(const uint16_t *active,
                                            const uint16_t *reference,
                                            unsigned output)
{
    if (!active || !reference || output >= OUTPUT_LEVELS) return false;
    /* Unity maps every DAC code to itself. Other OUTPUT levels only have64
     * possible DAC results: compute each once, then still check ALL1024 full
     * words, including token/tracker bits. No pointer or sample-only shortcut. */
    if(!output)return !memcmp(active,reference,1024u*sizeof(*active));
    uint8_t mapped[64];
    for(unsigned code=0;code<64u;++code)mapped[code]=(uint8_t)(output_entry((uint16_t)code,output,NULL)&63u);
    for (unsigned i=0;i<1024u;++i)
        if (active[i] != ((reference[i]&0xffc0u)|mapped[reference[i]&63u])) return false;
    return true;
}

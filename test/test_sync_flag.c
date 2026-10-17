/* C5VRX Twotoz/contributors: comparator insertion cannot perturb token/state,
 * contrast scaling, clipping accounting or any physical non-LSB DAC bit. */
#include <assert.h>
#include <stdio.h>
#include "sync_flag.h"
int main(void)
{
    unsigned count=0;
    for(unsigned word=0;word<=UINT16_MAX;++word) {
        unsigned flag=(word&63u)<SYNC_FLAG_THRESHOLD;
        for(unsigned level=0;level<OUTPUT_LEVELS;++level) {
            bool original_clip=false,flag_clip=false;
            uint16_t original=output_entry((uint16_t)word,level,&original_clip);
            uint16_t got=sync_flag_entry((uint16_t)word,level,&flag_clip);
            assert((got&0xffc0u)==(word&0xffc0u));
            assert((got&~1u)==(original&~1u));
            assert((got&1u)==flag && original_clip==flag_clip);
            unsigned physical=(got&63u)&~1u;
            assert(physical<=((unsigned)original&63u));
            assert(((unsigned)original&63u)-physical<=1u);
            assert(sync_flag_entry((uint16_t)word,level,NULL)==got);
            ++count;
        }
    }
    printf("PASS: %u comparator/scaling/token/state/physical-code invariants\n",count);
    return 0;
}

/* C5VRX by Twotoz/contributors: HDZero experimental transfer checks.
 * Verifies digital waveform properties, not a physical goggle decoder. */
#include <assert.h>
#include <stdio.h>
#include "output_level.h"

static unsigned mapped(unsigned code) { return output_entry((uint16_t)code, OUTPUT_HDZERO, NULL) & 63u; }
int main(void)
{
    assert(OUTPUT_DEFAULT == OUTPUT_HDZERO && !FLYWHEEL_DEFAULT);
    /* Exhaust all possible LUT words: phase/token bits must never change.
     * The ten legacy transforms remain exactly the old arithmetic. */
    const int pivot[10] = {21,13,16,19,22,25,28,31,34,21};
    const int scale[10] = {2,3,4,5,6,7,8,9,10,1};
    for (unsigned word = 0; word <= UINT16_MAX; ++word) {
        bool clipped = true;
        uint16_t out = output_entry((uint16_t)word, OUTPUT_HDZERO, &clipped);
        assert((out & 0xffc0u) == (word & 0xffc0u));
        assert(clipped == ((word & 63u) < 13u));
        for (unsigned level = 0; level < 10; ++level) {
            int twice = 2*pivot[level] + scale[level]*((int)(word & 63u)-21);
            int code = twice >= 0 ? (twice+1)/2 : twice/2;
            if (code < 0) code = 0;
            if (code > 63) code = 63;
            assert(output_entry((uint16_t)word, level, NULL) == ((word & 0xffc0u) | (unsigned)code));
        }
    }
    assert(mapped(13)==0 && mapped(15)==6 && mapped(21)==24 && mapped(27)==42 && mapped(63)==63);
    for (unsigned i=1; i<64; ++i) assert(mapped(i)>=mapped(i-1));
    /* Preserve equal positive/negative burst excursions around AFC porch
     * in the linear region; no polarity inversion or knee discontinuity. */
    for (unsigned a=1; a<=6; ++a) {
        assert(mapped(21+a)-mapped(21)==3*a);
        assert(mapped(21)-mapped(21-a)==3*a);
    }
    assert(mapped(28)-mapped(27)<=1);
    /* A low-deviation sync->porch step is 3x deeper. Brighter moving
     * content remains differentiated where the old 3x mode hard-clips. */
    assert(mapped(21)-mapped(15)==18);
    assert(mapped(41)<mapped(50) && mapped(50)<mapped(63));
    assert((output_entry(41,4,NULL)&63u)==63 && (output_entry(50,4,NULL)&63u)==63);
    /* A point mapping never changes sample count or H/V slot boundaries.
     * Exercise normal H and broad V widths at 20 MS/s with a 50% slicer. */
    const unsigned widths[] = {47,94,546};
    for (unsigned w=0; w<sizeof(widths)/sizeof(widths[0]); ++w) {
        unsigned seen=0;
        for (unsigned t=0; t<1271; ++t) {
            bool pulse=t>=80 && t<80+widths[w];
            unsigned input=pulse?15:21;
            bool output_pulse=mapped(input)<(mapped(15)+mapped(21))/2;
            assert(output_pulse==pulse); seen+=output_pulse;
        }
        assert(seen==widths[w]);
    }
    puts("PASS: HDZERO state preservation, legacy maps, burst linearity, white headroom and H/V slot timing");
}

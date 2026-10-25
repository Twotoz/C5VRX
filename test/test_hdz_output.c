/* C5VRX by Twotoz/contributors: measured CVBS conditioning regressions.
 * Nominal voltage tests are not physical goggle acceptance. */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include "cvbs_level.h"
int main(void)
{
    /* A common 75-ohm load is in parallel with the 200-ohm shunt.
     * The rejected series model predicted only ~0.62 V full scale. */
    assert(dac_loaded_uv(0)==0);
    assert(dac_loaded_uv(63)>1000000 && dac_loaded_uv(63)<1040000);
    for(unsigned i=1;i<64u;++i)assert(dac_loaded_uv(i)>dac_loaded_uv(i-1u));
    cvbs_fit_t narrow,wide,limited,untouched={.channel=42};
    assert(cvbs_make_fit((cvbs_refs_t){220000,320000,520000,220000},false,&narrow));
    assert(cvbs_make_fit((cvbs_refs_t){60000,360000,950000,60000},true,&wide));
    assert(cvbs_make_fit((cvbs_refs_t){220000,320000,900000,220000},false,&limited));
    assert(narrow.depth_uv>280000 && narrow.depth_uv<=286000);
    assert(wide.depth_uv>290000 && wide.depth_uv<=300000);
    assert(limited.headroom_limited && limited.depth_uv<narrow.depth_uv);
    assert(!cvbs_make_fit((cvbs_refs_t){300000,300000,900000,300000},false,&untouched));
    assert(!cvbs_make_fit((cvbs_refs_t){400000,300000,900000,400000},false,&untouched));
    assert(untouched.channel==42 && !untouched.valid);
    const cvbs_fit_t *fits[]={&narrow,&wide,&limited};
    for(unsigned k=0;k<3u;++k) {
        const cvbs_fit_t *fit=fits[k];
        for(unsigned i=1;i<64u;++i)assert(fit->code[i]>=fit->code[i-1]);
        for(unsigned word=0;word<=UINT16_MAX;++word) {
            bool clipped;uint16_t out=output_entry_with_fit((uint16_t)word,OUTPUT_AUTO_CVBS,fit,&clipped);
            assert((out&0xffc0u)==(word&0xffc0u));
            assert((out&63u)==fit->code[word&63u]);
            assert(clipped==(((fit->clipped>>(word&63u))&1u)!=0));
            assert(output_entry_with_fit((uint16_t)word,0,fit,NULL)==word);
            assert(output_entry_with_fit((uint16_t)word,OUTPUT_AUTO_CVBS,NULL,NULL)==word);
            assert((output_flag_with_fit((uint16_t)word,OUTPUT_AUTO_CVBS,fit,NULL)&1u)==((word&63u)<SYNC_FLAG_THRESHOLD));
        }
        /* Equal positive and negative burst excursions remain electrically
         * linear, within one code of the nominal inverse-DAC quantizer. */
        unsigned porch=cvbs_nearest_code(fit->porch_uv),a=2;
        int centre=dac_loaded_uv(fit->code[porch]);
        int up=dac_loaded_uv(fit->code[porch+a])-centre;
        int down=centre-dac_loaded_uv(fit->code[porch-a]);
        assert(abs(up-down)<45000);
    }
    const int pivot[10]={21,13,16,19,22,25,28,31,34,21};
    const int scale[10]={2,3,4,5,6,7,8,9,10,1};
    for(unsigned c=0;c<64u;++c)for(unsigned level=0;level<10u;++level) {
        int twice=2*pivot[level]+scale[level]*((int)c-21);
        int code=twice>=0?(twice+1)/2:twice/2;
        if(code<0)code=0;
        if(code>63)code=63;
        assert((output_entry((uint16_t)c,level,NULL)&63u)==(unsigned)code);
    }
    puts("PASS: measured narrow/wide CVBS fit, electrical load, white headroom, linear burst, invalid references, metadata and legacy maps");
}

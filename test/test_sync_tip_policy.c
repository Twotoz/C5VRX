/* C5VRX by Twotoz/contributors: exhaustive policy vs actual-LUT minimax. */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "../main/sync_flywheel.c"
int main(void)
{
    uint8_t raw[4096]={0};
    sfw_ring_t r={.ring=raw,.ring_bytes=4096,.phase=c5vrx_phase8_gain_lut};
    build_cells(&r);
    unsigned cases=0;
    for(unsigned level=0;level<KP_LEVELS;++level) {
        uint8_t cells[28]={0};uint32_t available=0;
        for(unsigned phase=0;phase<256;++phase) {
            uint8_t raw_cell=s_cell[phase];
            unsigned address=(raw_cell>>4)|((raw_cell&15u)<<4);
            unsigned token=kp_tables[level][address]>>11;
            if(token>=28u)continue;
            bool regular=true;
            for(unsigned q=1;q<4;++q)
                if(kp_tables[level][address|(q<<8)]>>11!=token)regular=false;
            if(!regular)continue;
            if(!(available&(1u<<token)))cells[token]=raw_cell;
            available|=1u<<token;
        }
        assert(available==SFW_TIP_POLICY_AVAILABLE);
        for(unsigned token=0;token<28;++token)
            if(available&(1u<<token))assert(cells[token]==sfw_tip_policy_cells[token]);
        for(unsigned state=0;state<32;++state)for(unsigned previous=0;previous<256;++previous) {
            unsigned best=UINT32_MAX,code=UINT32_MAX;uint8_t expected=0;
            for(unsigned token=0;token<28;++token)if(available&(1u<<token)) {
                unsigned dac=kp_tables[level][state*32u+token]&63u;
                int error=wrap8((int)r.phase[cells[token]]-(int)previous-sfw_tip_policy_steps[level]/256);
                if(error<0)error=-error;
                unsigned score=(dac<<20)+(dac<<8)+(unsigned)error;
                if(score<best){best=score;code=dac;expected=cells[token];}
            }
            unsigned index=level*32u+state;
            assert(sfw_tip_policy_minimum[index]==code);
            assert(sfw_tip_policy_raw[index*256u+previous]==expected);
            ++cases;
        }
    }
    printf("PASS first-EQ policy: %u exact actual-LUT choices, all8kp/32states/256prior phases and ordered ties\n",cases);
}

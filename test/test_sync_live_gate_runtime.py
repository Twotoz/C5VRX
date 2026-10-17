#!/usr/bin/env python3
"""C5VRX: compile the actual pure planner; no timer/OE/analogue claims."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = r'''
#include "sync_live_gate.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>

static unsigned checks;
#define CHECK(x) do {assert(x);++checks;} while(0)
static const slg_config_t config={.lead_ticks=240,.late_ticks=160,
    .native_window_ticks=16,.native_age_ticks=40,.model_age_ticks=100000,
    .max_uncertainty_ticks=8};
static uint64_t half_tick(uint64_t half,uint32_t period)
{ return (half*period+32u)/64u; }
static slg_native_t witness;
static bool provide(void *arg,const slg_pulse_t *p,slg_native_t *out)
{ (void)arg;(void)p;*out=witness;return true; }
static bool cancel_in_callback(void *arg,const slg_pulse_t *p,slg_native_t *out)
{ (void)p;*out=witness;sync_live_gate_cancel(arg);return true; }

static void camera_matrix(void)
{
 for(unsigned std=1;std<=2;++std)for(unsigned parity=0;parity<2;++parity)
 for(int ppm=-1;ppm<=1;++ppm) {
    uint32_t period=(std==2?40960u:40676u)+ppm*2;
    uint64_t base=UINT64_C(1)<<40;
    uint16_t halves=std==2?625u:525u,section=std==2?5u:6u;
    slg_model_t m={.standard=(slg_standard_t)std,.qualified=true,
        .line_ticks_x32=period,.field=base+half_tick(section,period),
        .last_h=base-half_tick(parity?1u:2u,period),.receive_epoch=17,
        .uncertainty_ticks=2};
    m.observed_at=m.field+100u;
    sync_live_gate_t *g=sync_live_gate_create(&config);CHECK(g);
    slg_pulse_t p;
    CHECK(!sync_live_gate_next(g,&p));
    CHECK(sync_live_gate_model(g,&m,m.observed_at));
    CHECK(!sync_live_gate_next(g,&p)); /* no implicit arm */
    CHECK(sync_live_gate_arm(g));
    uint64_t previous_end=0;
    unsigned first_broad=0,eq=0,h=0;
    for(unsigned n=0;n<2200u;++n) {
        CHECK(sync_live_gate_next(g,&p));
        CHECK(p.start==base+half_tick(p.half,period));
        CHECK(p.alarm_tick+config.lead_ticks==p.start);
        CHECK(p.start>=previous_end);
        CHECK(p.index==p.half%halves&&p.field==p.half/halves);
        if(p.index<section || (p.index>=2u*section&&p.index<3u*section)) {
            CHECK(p.kind==SLG_EQ&&p.width==(std==2?47u:46u));++eq;
        } else if(p.index<2u*section) {
            CHECK(p.kind==SLG_BROAD);
            CHECK(p.width==half_tick(p.half+1u,period)-half_tick(p.half,period)-94u);
            if(p.index==section) {
                CHECK(p.start==base+half_tick(p.field*halves+section,period));
                ++first_broad;
            }
        } else {
            CHECK(p.kind==SLG_H&&p.width==94u&&(p.half&1u)==parity);++h;
        }
        previous_end=p.start+p.width;
        CHECK(sync_live_gate_decide(g,&p,p.start-1u,NULL,NULL)==SLG_EARLY);
        CHECK(sync_live_gate_decide(g,&p,p.start,NULL,NULL)==SLG_REPAIR);
        CHECK(sync_live_gate_decide(g,&p,p.start+161u,NULL,NULL)==SLG_LATE);
    }
    CHECK(first_broad>=5u&&eq>30u&&h>2000u);
    CHECK(!sync_live_gate_holdover(g,m.observed_at));
    CHECK(sync_live_gate_holdover(g,m.observed_at+config.model_age_ticks+1u));
    /* Prolonged holdover retains camera phase and period, with no takeover
     * threshold. Iterate about2.5seconds to cross the old40000-line limit. */
    for(unsigned n=0;n<41000u;++n)CHECK(sync_live_gate_next(g,&p));
    CHECK(p.start==base+half_tick(p.half,period));
    CHECK(sync_live_gate_decide(g,&p,p.start,NULL,NULL)==SLG_REPAIR);
    /* Receive mutation preserves the already planned output pulse. */
    sync_live_gate_invalidate(g,18u);
    CHECK(sync_live_gate_holdover(g,p.start));
    CHECK(sync_live_gate_decide(g,&p,p.start,NULL,NULL)==SLG_REPAIR);
    sync_live_gate_cancel(g);
    CHECK(!sync_live_gate_next(g,&p));
    CHECK(sync_live_gate_decide(g,&p,p.start,NULL,NULL)==SLG_CANCELLED);
    sync_live_gate_destroy(g);
 }
}
static void native_admission(void)
{
 sync_live_gate_t *g=sync_live_gate_create(&config);CHECK(g);
 CHECK(sync_live_gate_nominal(g,SLG_PAL,10000u,0,3,0));
 CHECK(sync_live_gate_arm(g));slg_pulse_t p;
 CHECK(sync_live_gate_next(g,&p));
 witness=(slg_native_t){.fall=p.start,.observed_at=p.start,.receive_epoch=3,
     .low=true,.qualified=true};
 CHECK(sync_live_gate_decide(g,&p,p.start,provide,NULL)==SLG_NATIVE);
 witness.receive_epoch=2;
 CHECK(sync_live_gate_decide(g,&p,p.start,provide,NULL)==SLG_REPAIR);
 witness.receive_epoch=3;witness.low=false;
 CHECK(sync_live_gate_decide(g,&p,p.start,provide,NULL)==SLG_REPAIR);
 witness.low=true;witness.qualified=false;
 CHECK(sync_live_gate_decide(g,&p,p.start,provide,NULL)==SLG_REPAIR);
 witness.qualified=true;witness.observed_at=p.start-41u;witness.fall=p.start-41u;
 CHECK(sync_live_gate_decide(g,&p,p.start,provide,NULL)==SLG_REPAIR);
 witness.observed_at=p.start;witness.fall=p.start-17u;
 CHECK(sync_live_gate_decide(g,&p,p.start,provide,NULL)==SLG_REPAIR);
 witness.fall=p.start;witness.observed_at=p.start+1u;
 CHECK(sync_live_gate_decide(g,&p,p.start,provide,NULL)==SLG_REPAIR);
 witness.observed_at=p.start;
 CHECK(sync_live_gate_decide(g,&p,p.start,cancel_in_callback,g)==SLG_CANCELLED);
 sync_live_gate_destroy(g);
}
static void nominal_and_rejection(void)
{
 sync_live_gate_t *g=sync_live_gate_create(&config);CHECK(g);
 CHECK(!sync_live_gate_arm(g));
 CHECK(!sync_live_gate_nominal(g,0,10000,0,1,0));
 CHECK(!sync_live_gate_nominal(g,SLG_PAL,10000,2,1,0));
 /* Seek directly after a week; exact nominal NTSC has no rounded-period
  * drift, and skipped time does not require an unbounded ISR catch-up. */
 uint64_t origin=10000u,now=UINT64_C(604800)*20000000u;
 CHECK(sync_live_gate_nominal(g,SLG_NTSC,origin,1,1,now));
 CHECK(sync_live_gate_arm(g));
 slg_pulse_t p;
 for(unsigned n=0;n<2000u;++n){
    CHECK(sync_live_gate_next(g,&p));
    CHECK(p.start==origin+(p.half*5720u+4u)/9u);
    CHECK(p.alarm_tick>=now);
 }
 slg_model_t m={.standard=SLG_PAL,.line_ticks_x32=40960,.field=20000,
     .last_h=17000,.observed_at=21000,.qualified=true,.uncertainty_ticks=2};
 CHECK(!sync_live_gate_model(g,&m,21000)); /* not on a half-line grid */
 m.field=20000;m.last_h=16800; /* origin=16800 */
 CHECK(sync_live_gate_model(g,&m,21000));
 CHECK(sync_live_gate_decide(g,&p,p.start,NULL,NULL)==SLG_CANCELLED);
 m.qualified=false;CHECK(!sync_live_gate_model(g,&m,21000));
 m.qualified=true;m.uncertainty_ticks=9;CHECK(!sync_live_gate_model(g,&m,21000));
 m.uncertainty_ticks=2;m.line_ticks_x32=39000;CHECK(!sync_live_gate_model(g,&m,21000));
 CHECK(!sync_live_gate_nominal(g,SLG_NTSC,UINT64_MAX-100u,0,1,UINT64_MAX-10u));
 sync_live_gate_destroy(g);
}
int main(void){camera_matrix();native_admission();nominal_and_rejection();
 printf("PASS: %u pure live-gate checks; no hardware output enabled\n",checks);return 0;}
'''


class Runtime(unittest.TestCase):
    def test_actual_planner(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "fixture.c").write_text(FIXTURE)
            command = [os.environ.get("CC", "cc"), "-O2", "-std=c11", "-Wall", "-Wextra", "-Werror",
                       "-I", str(ROOT / "main"), str(ROOT / "main/sync_live_gate.c"),
                       str(path / "fixture.c"), "-o", str(path / "fixture")]
            subprocess.run(command, check=True)
            result = subprocess.run([str(path / "fixture")], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertIn("PASS:", result.stdout)
            print(result.stdout, end="")


if __name__ == "__main__":
    unittest.main()

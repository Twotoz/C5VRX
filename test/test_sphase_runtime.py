#!/usr/bin/env python3
"""C5VRX by Twotoz/contributors: compile actual live SPHASE monitor.

Exercises production control flow with measurement/hardware boundaries
mocked; does not infer phase correctness from a synthetic HF4 value.
"""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PREFIX = r'''
#include <assert.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "sphase_logic.h"
typedef struct { int excess; } window_stats_t;
static int s_sphase_excess, s_sphase_white;
static const char *s_sphase_state="unverified";
static sphase_scan_t s_sphase_last={.best=0x7fffffff,.worst=-1};
static sphase_live_t s_sphase_live={.gap_us=SPHASE_LIVE_GAP_US};
static unsigned s_current_gain=50;
static bool owner=true, carrier=true, copied=true;
static int measured;
static int64_t now=1;
static unsigned collections, scans, pauses, slips;
static char logs[8192];
static int64_t esp_timer_get_time(void) { return now; }
static bool quiet_owner(void) { return owner; }
static bool collect(unsigned windows, window_stats_t *w) {
    assert(windows==16); ++collections; w->excess=measured; return copied;
}
static int sphase_record(const window_stats_t *w) { s_sphase_excess=w->excess; return w->excess; }
static bool sphase_on_carrier(void) { return carrier; }
/* Exported mock remains legal if production no longer references it; any
 * revival of the old disruptive live scan is observable to these assertions. */
void sphase_carrier_scan(const char *stage, bool repair) {
    (void)stage;(void)repair; ++scans; ++pauses; slips+=2;
}
static int recorded_printf(const char *format, ...) {
    size_t n=strlen(logs); va_list ap; va_start(ap,format);
    int written=vsnprintf(logs+n,sizeof(logs)-n,format,ap); va_end(ap);
    assert(written>=0 && (size_t)written<sizeof(logs)-n); return written;
}
#define printf recorded_printf
'''
SUFFIX = r'''
#undef printf
static void check_no_actuation(void) { assert(!scans && !pauses && !slips); }
static void tick(int excess, bool has_carrier) {
    now+=SPHASE_LIVE_CHECK_US+1; measured=excess;carrier=has_carrier;sphase_monitor();check_no_actuation();
}
int main(void) {
    measured=20; sphase_monitor();
    assert(collections==1 && !strcmp(s_sphase_state,"unverified"));
    assert(!strstr(logs,"automatic_adjustment=deferred"));check_no_actuation();
    tick(300,true);
    assert(!strcmp(s_sphase_state,"drifted"));
    assert(strstr(logs,"automatic_adjustment=deferred reason=live_actuator_unverified"));
    unsigned previous=collections; logs[0]=0;
    tick(320,true); assert(collections==previous+1);check_no_actuation();
    assert(strstr(logs,"automatic_adjustment=deferred"));
    logs[0]=0; tick(900,false);
    assert(!strstr(logs,"automatic_adjustment=deferred"));check_no_actuation();
    previous=collections;owner=false;tick(300,true);assert(collections==previous);owner=true;
    logs[0]=0;copied=false;tick(300,true);assert(!strstr(logs,"automatic_adjustment=deferred"));copied=true;
    /* A previously startup-qualified phase also stays passive on confirmed
     * live excursions, while the existing diagnosis/confirmation runs. */
    s_sphase_live.qualified=true;s_sphase_live.scanned_once=false;s_sphase_live.strikes=0;
    logs[0]=0;tick(300,true);assert(!strstr(logs,"automatic_adjustment=deferred"));
    tick(310,true);assert(strstr(logs,"automatic_adjustment=deferred"));
    logs[0]=0;tick(30,true);assert(!strstr(logs,"automatic_adjustment=deferred"));
    assert(!strcmp(s_sphase_state,"settled"));check_no_actuation();
    puts("PASS: actual live SPHASE monitor is passive, noisy carrier reports deferred, no healthy-carrier scan/pause/slip");
}
'''


class SamplingRuntimeTest(unittest.TestCase):
    def test_actual_live_monitor(self):
        source = (ROOT / "main/calibration.c").read_text(encoding="utf-8")
        begin = source.index("static void sphase_monitor(void)")
        end = source.index("const char *calibration_sphase_state(void)", begin)
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "sphase_logic.h").write_bytes((ROOT / "main/sphase_logic.h").read_bytes())
            (folder / "monitor.c").write_text(PREFIX + source[begin:end] + SUFFIX, encoding="utf-8")
            binary = folder / "runtime"
            subprocess.run([os.environ.get("CC", "cc"), "-O2", "-std=c11", "-Wall", "-Wextra", "-Werror",
                            "-I", str(folder), str(folder / "monitor.c"), "-o", str(binary)], check=True)
            subprocess.run([str(binary)], check=True)


if __name__ == "__main__":
    unittest.main()

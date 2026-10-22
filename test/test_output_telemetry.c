/* C5VRX-5 by Twotoz and the C5VRX contributors: output telemetry rollup. */
#include <stdio.h>
#include "output_telemetry.h"

static unsigned run, fail;
static void check(const char *name, int ok)
{
    ++run; fail += !ok;
    printf("%-4s %s\n", ok ? "ok" : "FAIL", name);
}

int main(void)
{
    output_telem_acc_t a = {0};
    output_telem_sample_t s = {0};
    check("inactive is sampled but remains unknown", !output_telem_add(&a, &s, 0) &&
          a.samples == 1u && !a.fresh_samples);

    s = (output_telem_sample_t){.active=true, .observer_fresh=true, .camera_present=true,
        .h_trusted=true, .v_trusted=true, .standard=2, .camera_lines=10, .repaired=2,
        .suppressed=8, .observer_gaps=1};
    check("first active sample adopts cumulative counters", !output_telem_add(&a, &s, 10) &&
          a.native_h_lines == 10u && a.repaired == 2u && a.suppressed == 8u && a.observer_gaps == 1u);
    s.camera_lines = 15; s.repaired = 3; s.suppressed = 12;
    check("later active sample adds deltas", !output_telem_add(&a, &s, 20) &&
          a.native_h_lines == 15u && a.repaired == 3u && a.suppressed == 12u);

    s.camera_present = false;
    check("an observed unlock starts without an event", !output_telem_add(&a, &s, 30));
    check("100 ms native-H outage emits one event", output_telem_add(&a, &s, 130));
    check("one outage emits only one event", !output_telem_add(&a, &s, 180) && a.longest_unlock_ms == 150u);
    s.observer_fresh = false;
    check("unknown coverage breaks rather than extends unlock", !output_telem_add(&a, &s, 400) && !a.unlocking);

    s.active = false;
    (void)output_telem_add(&a, &s, 410);
    s = (output_telem_sample_t){.active=true, .observer_fresh=true, .camera_present=true,
        .h_trusted=true, .standard=1, .camera_lines=2, .repaired=1, .suppressed=2};
    check("counter reset after restart is added without underflow", !output_telem_add(&a, &s, 420) &&
          a.native_h_lines == 17u && a.repaired == 4u && a.suppressed == 14u);

    output_telem_roll_t out;
    output_telem_roll(&a, 500, &out);
    check("roll preserves coverage, lock and standard", out.samples == 9u && out.fresh_samples == 6u &&
          out.h_locked_samples == 3u && out.v_locked_samples == 2u && out.standard == 1u);
    check("roll preserves native/assisted split and longest outage", out.native_h_lines == 17u &&
          out.repaired == 4u && out.suppressed == 14u && out.longest_unlock_ms == 150u);
    check("roll resets interval totals", a.samples == 0u && a.native_h_lines == 0u && a.longest_unlock_ms == 0u);
    check("percentages preserve unknown", output_telem_pct(0, 0) == 0xffu &&
          output_telem_pct(3, 4) == 75u && output_telem_pct(8, 4) == 100u);

    printf("%u/%u output telemetry checks passed\n", run - fail, run);
    return fail != 0;
}

#!/usr/bin/env python3
"""C5VRX by Twotoz/contributors: actual native owner code under task races.

Compile receiver.c's production toggle/tick directly, with a two-task
recursive PHY lease mock. RF hardware is a boundary mock, not acquisition
or timing proof. Exercises the real publication/defer and cleanup decisions.
"""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

PREFIX = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "native_acquire.h"
typedef enum { RF_NATIVE_LANDING, RF_NATIVE_REFUSED, RF_NATIVE_TIMEOUT,
               RF_NATIVE_GAP, RF_NATIVE_BAD_GAIN, RF_NATIVE_RESTORE_FAILED } rf_native_result_t;
typedef struct { unsigned reset_count; uint8_t applied; } direct_gain_t;
typedef struct { unsigned dummy; } arc_gain_table_t;
static direct_gain_t s_gain;
static uint8_t s_current_gain = 62;
static uint32_t s_gain_epoch, s_control_epoch;
static int64_t s_last_gain_write_us;
static volatile bool s_paused, s_menu_active, s_native_enabled, s_native_scanning;
static int64_t s_native_next;
static uint32_t s_native_control, s_native_phy;
static uint32_t s_native_attempts, s_native_landings, s_native_faults, s_native_max_open;
static bool native_tick(uint32_t, uint32_t);
void receiver_native_toggle(void);
static unsigned task = 1, lock_owner, lock_depth, generation = 7, config_depth;
static unsigned take_count, give_count, rf_calls, recursive_rf_calls, deferred_ticks;
static bool inject_before_control_release, inject_off_before_observer_try;
static bool startup;
static int64_t now = 1000;
static rf_native_result_t rf_result = RF_NATIVE_LANDING;
static uint8_t rf_applied = 37;
static uint32_t rf_opened = 4;
static const arc_gain_table_t table = {0};

static bool calibration_in_startup(void) { return startup; }
static uint32_t phy_generation(void) { return generation; }
static int64_t esp_timer_get_time(void) { return now; }
static const arc_gain_table_t *rf_gain_table(void) { return &table; }
static void dg_reset(direct_gain_t *gain, const arc_gain_table_t *t, uint8_t applied)
{
    assert(task == 2 && t == &table); /* observer sole model owner */
    ++gain->reset_count; gain->applied = applied;
}
static bool phy_try_actuator(uint32_t expected)
{
    if (task == 2 && inject_off_before_observer_try) {
        inject_off_before_observer_try = false;
        task = 1; receiver_native_toggle(); task = 2;
    }
    if (lock_depth && lock_owner != task) return false;
    if (config_depth || expected != generation) return false;
    lock_owner = task; ++lock_depth; ++take_count; return true;
}
static void phy_end_actuator(void)
{
    assert(lock_depth && lock_owner == task);
    if (task == 1 && inject_before_control_release) {
        inject_before_control_release = false;
        assert(lock_depth == 1 && s_native_enabled);
        task = 2;
        assert(native_tick(s_control_epoch, generation));
        ++deferred_ticks;
        assert(s_native_enabled && !s_native_scanning);
        assert(!s_native_attempts && !s_native_faults && !s_gain.reset_count && !rf_calls);
        task = 1;
        assert(lock_depth == 1 && lock_owner == 1);
    }
    --lock_depth; ++give_count;
    if (!lock_depth) lock_owner = 0;
}
static rf_native_result_t rf_native_acquire(uint32_t phy, uint8_t *applied, uint32_t *opened)
{
    ++rf_calls;
    assert(task == 2 && lock_owner == 2 && lock_depth == 1 && s_native_scanning);
    assert(s_native_enabled && phy == generation);
    assert(phy_try_actuator(phy));
    ++recursive_rf_calls;
    assert(lock_depth == 2);
    if (rf_result != RF_NATIVE_REFUSED) *applied = rf_applied;
    *opened = rf_result == RF_NATIVE_REFUSED ? 0 : rf_opened;
    phy_end_actuator();
    assert(lock_depth == 1);
    return rf_result;
}
'''

SUFFIX = r'''
static void balanced(void) { assert(!lock_depth && !lock_owner && take_count == give_count); }
static void clear(void)
{
    balanced();
    s_gain = (direct_gain_t){0}; s_current_gain = 62; s_gain_epoch = s_control_epoch = 0;
    s_last_gain_write_us = 0; s_paused = s_menu_active = s_native_enabled = s_native_scanning = false;
    s_native_next = 0; s_native_control = s_native_phy = 0;
    s_native_attempts = s_native_landings = s_native_faults = s_native_max_open = 0;
    take_count = give_count = rf_calls = recursive_rf_calls = deferred_ticks = 0;
    task = 1; generation = 7; config_depth = 0; startup = false; now = 1000;
    inject_before_control_release = inject_off_before_observer_try = false;
    rf_result = RF_NATIVE_LANDING; rf_applied = 37; rf_opened = 4;
}
static void enable(void)
{
    task = 1; receiver_native_toggle(); assert(s_native_enabled); balanced(); task = 2;
}
int main(void)
{
    clear(); inject_before_control_release = true;
    receiver_native_toggle();
    assert(deferred_ticks == 1 && !s_gain.reset_count);
    balanced(); task = 2;
    assert(native_tick(s_control_epoch, generation));
    balanced();
    assert(s_native_attempts == 1 && s_native_landings == 1 && !s_native_faults);
    assert(recursive_rf_calls == 1 && s_current_gain == 37 && s_gain_epoch == 1);
    assert(s_gain.reset_count == 1 && s_gain.applied == 37 && s_last_gain_write_us == now);
    assert(s_native_next == now + NATIVE_PERIOD_US && s_native_max_open == 4 && !s_native_scanning);
    unsigned takes = take_count;
    now += NATIVE_PERIOD_US - 1;
    assert(native_tick(s_control_epoch, generation)); balanced();
    assert(s_native_attempts == 1 && take_count == takes + 1);
    ++now; assert(native_tick(s_control_epoch, generation)); balanced();
    assert(s_native_attempts == 2 && s_native_landings == 2 && s_gain_epoch == 2);
    assert(s_last_gain_write_us == now); /* same landing gain still expires old C/N */
    now += 100 * NATIVE_PERIOD_US;
    assert(native_tick(s_control_epoch, generation)); balanced();
    assert(s_native_attempts == 3 && s_native_next == now + NATIVE_PERIOD_US);

    clear(); enable();
    task = 1; assert(phy_try_actuator(generation)); task = 2;
    assert(native_tick(s_control_epoch, generation));
    assert(!s_native_attempts && !s_native_faults && s_native_enabled);
    task = 1; phy_end_actuator(); balanced();
    task = 2; assert(native_tick(s_control_epoch, generation)); balanced();
    assert(s_native_landings == 1);

    /* Pause/menu can appear after observer's outer receiver_active check.
     * The tick must recheck under its lease, preserve mode and defer opening. */
    clear(); enable(); s_paused = true;
    assert(native_tick(s_control_epoch, generation)); balanced();
    assert(s_native_enabled && !rf_calls && !s_native_attempts && !s_gain.reset_count);
    s_paused = false;
    assert(native_tick(s_control_epoch, generation)); balanced();
    assert(s_native_landings == 1);
    clear(); enable(); s_menu_active = true;
    assert(native_tick(s_control_epoch, generation)); balanced();
    assert(s_native_enabled && !rf_calls && !s_native_attempts && !s_gain.reset_count);
    s_menu_active = false;
    assert(native_tick(s_control_epoch, generation)); balanced();
    assert(s_native_landings == 1);

    clear(); enable(); inject_off_before_observer_try = true;
    assert(!native_tick(s_control_epoch, generation)); balanced();
    assert(!s_native_enabled && !rf_calls && !s_gain.reset_count);
    clear(); enable();
    assert(native_tick(s_control_epoch, generation - 1u)); balanced();
    assert(s_native_enabled && !s_native_attempts && !s_native_faults);
    ++generation;
    assert(!native_tick(s_control_epoch, generation)); balanced();
    assert(!s_native_enabled && s_gain.reset_count == 1 && !rf_calls);

    clear(); enable(); ++s_control_epoch;
    assert(!native_tick(s_control_epoch, generation)); balanced();
    assert(!s_native_enabled && !s_native_attempts && !s_native_faults && s_gain.reset_count == 1);
    clear(); enable(); startup = true;
    assert(!native_tick(s_control_epoch, generation)); balanced();
    assert(!s_native_enabled && !rf_calls && s_gain.reset_count == 1);

    for (unsigned result = RF_NATIVE_REFUSED; result <= RF_NATIVE_RESTORE_FAILED; ++result) {
        clear(); enable(); rf_result = (rf_native_result_t)result; rf_applied = 62; rf_opened = 60;
        bool owned = native_tick(s_control_epoch, generation); balanced();
        assert(owned == (result == RF_NATIVE_RESTORE_FAILED));
        assert(!s_native_enabled && !s_native_scanning && s_native_faults == 1 && s_native_attempts == 1);
        assert(s_paused == (result == RF_NATIVE_RESTORE_FAILED));
        assert(s_gain_epoch == (result == RF_NATIVE_REFUSED ? 0u : 1u));
        assert(s_gain.reset_count == (result == RF_NATIVE_REFUSED ? 0u : 1u));
        assert(s_current_gain == 62 && recursive_rf_calls == 1);
    }
    clear(); task = 2;
    assert(!native_tick(0, generation)); balanced(); assert(!take_count);
    clear(); startup = true; receiver_native_toggle(); assert(!s_native_enabled); balanced();
    clear(); s_paused = true; receiver_native_toggle(); assert(!s_native_enabled); balanced();
    clear(); s_menu_active = true; receiver_native_toggle(); assert(!s_native_enabled); balanced();
    clear(); s_native_scanning = true; receiver_native_toggle(); assert(!s_native_enabled); balanced();
    clear(); config_depth = 1; receiver_native_toggle(); assert(!s_native_enabled); balanced();
    clear(); enable(); task = 1; receiver_native_toggle(); assert(!s_native_enabled);
    assert(!s_gain.reset_count); balanced(); task = 2; assert(!native_tick(0, generation)); balanced();
    puts("PASS: actual native toggle/tick task preemption, defer, recursive lease, deadlines, context and all RF exits");
}
'''


class NativeOwnerTest(unittest.TestCase):
    def test_actual_production_source(self):
        source = (ROOT / "main/receiver.c").read_text(encoding="utf-8")
        begin = source.index("void receiver_native_toggle(void)")
        end = source.index("bool receiver_paused(void)", begin)
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "native_acquire.h").write_bytes((ROOT / "main/native_acquire.h").read_bytes())
            (folder / "harness.c").write_text(PREFIX + source[begin:end] + SUFFIX, encoding="utf-8")
            binary = folder / "native_owner"
            subprocess.run([os.environ.get("CC", "cc"), "-O2", "-std=c11", "-Wall", "-Wextra", "-Werror",
                            "-I", str(folder), str(folder / "harness.c"), "-o", str(binary)], check=True)
            subprocess.run([str(binary)], check=True)


if __name__ == "__main__":
    unittest.main()

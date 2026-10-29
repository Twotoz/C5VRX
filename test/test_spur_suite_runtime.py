"""C5VRX by Twotoz/contributors: execute production spur suite and rollback.

MMIO, measurement and physical-context boundaries are mocked. This verifies
software ownership/order, not silicon slot validity or RF response.
"""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def function(source, signature):
    start = source.index(signature)
    end = source.index('{', start) + 1
    depth = 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end] + '\n'


HARNESS = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <inttypes.h>
#include <stdio.h>
#include <string.h>
#include <setjmp.h>
#include "sense_lab_logic.h"
#define P_M SENSE_MMIO
static struct { bool stop; uint8_t gain; unsigned rejected, broken, profiles; } L;
static const sense_profile_t *s_active;
static uint32_t s_expect[SENSE_MAX_PATCHES];
static const sense_profile_t k_sham = {"SHAM", SENSE_K_PATCH, 0, 0, {{0}}};
typedef struct { unsigned unused; } stage_t;
static uint32_t words[13], baseline[13];
static unsigned writes[16], nwrites, candidates, shams, skipped, stages;
static unsigned fail_stage, drop_at, cancel_after, context_calls;
static bool inject, physical, fail_restore_context, expect_fatal;
static const char *cause;
static jmp_buf escape;
static unsigned index_of(uint32_t addr) {
    if (addr >= 0x600a7810 && addr <= 0x600a782c && !(addr & 3))
        return (addr - 0x600a7810) / 4;
    if (addr == 0x600a7830) return 8;
    assert(addr >= 0x600a7c14 && addr <= 0x600a7c20 && !(addr & 3));
    return 9 + (addr - 0x600a7c14) / 4;
}
#define MMIO(a) words[index_of(a)]
static uint32_t patch_read(const sense_patch_t *p) {
    assert(p->kind == SENSE_P_MMIO); return MMIO(p->addr) & p->mask;
}
static void patch_write(const sense_patch_t *p, uint32_t value) {
    assert(p->kind == SENSE_P_MMIO && p->mask == UINT32_MAX);
    assert(nwrites < 16); writes[nwrites++] = index_of(p->addr);
    if (nwrites != drop_at) MMIO(p->addr) = value;
}
static bool physical_context_ok(void) {
    ++context_calls;
    return physical && !(fail_restore_context && context_calls > 1);
}
static bool filter_ok(void) { return true; }
static void phy_observation_changed(void) {}
static void fatal(const char *why) {
    assert(expect_fatal && !strcmp(why, "physical_context_restore_failed"));
    assert(!memcmp(words, baseline, sizeof(words)));
    longjmp(escape, 1);
}
static void skip(unsigned id, const char *name, const char *why) {
    assert(!strcmp(name, "SPUR"));
    assert((id == 1300 && !strcmp(why, "requires_fixed_gain_and_held_dc")) ||
           (id >= 1301 && !strcmp(why, "apparent_slot_already_active")));
    ++skipped;
}
static bool patches_ok(void);
static void print_patches(unsigned id, const sense_profile_t *p, const uint32_t *saved) {
    (void)saved; assert(id >= 1300 && id <= 1316);
    assert(!memcmp(words, baseline, sizeof(words))); nwrites = stages = 0;
    if (!p->n) { assert(p == &k_sham); ++shams; return; }
    ++candidates;
    unsigned slot = (id - 1301) / 4, sign = ((id - 1301) % 4) / 2;
    char name[40];
    snprintf(name, sizeof(name), "SPUR_%s%u_%c512", slot ? "INFERRED" : "REFERENCE", slot, sign ? 'M' : 'P');
    assert(!strcmp(p->name, name) && p->kind == SENSE_K_SPUR && p->n == 4);
    assert(p->p[0].addr == 0x600a7810 + 8*slot && p->p[0].value == 0x2febfafe);
    assert(p->p[1].addr == p->p[0].addr + 4 && p->p[1].value == 0x0865dbae);
    assert(p->p[2].addr == 0x600a7830 && p->p[3].addr == 0x600a7c14 + 4*slot);
    unsigned shift = slot*7;
    assert(p->p[2].value == ((baseline[8] & ~(127u << shift)) | ((sign ? 112u : 16u) << shift)));
    assert(p->p[3].value == ((baseline[9+slot] & ~0x3fffu) | (sign ? 0x3e00u : 0x2200u)));
    for (unsigned i=0;i<4;i++) assert(p->p[i].mask == UINT32_MAX);
}
static const char *measure_stage(unsigned id, unsigned stage, uint8_t gain, bool held, stage_t *out) {
    (void)id; assert(gain == 52 && held && out && stage < 4);
    stages |= 1u << stage;
    assert(patches_ok());
    if (!s_active->n || stage == 0 || stage == 3)
        assert(!memcmp(words, baseline, sizeof(words)));
    else {
        const sense_profile_t *p = s_active;
        unsigned slot = (p->p[3].addr - 0x600a7c14)/4;
        for (unsigned i=0;i<13;i++) {
            bool owned = i == 2*slot || i == 2*slot+1 || i == 8 || i == 9+slot;
            if (!owned) assert(words[i] == baseline[i]);
        }
        assert((words[9+slot] & ~0x3fffu) == (baseline[9+slot] & ~0x3fffu));
        assert((words[8] & ~(127u << (7*slot))) == (baseline[8] & ~(127u << (7*slot))));
    }
    return inject && s_active->n && stage == fail_stage ? cause : NULL;
}
static void result(unsigned id, const char *name, const char *status, const stage_t *st) {
    (void)id; (void)name; assert(st && !s_active);
    assert(!memcmp(words, baseline, sizeof(words)));
    if (nwrites) {
        unsigned start = nwrites == 9 ? 4 : 0;
        assert(nwrites == 9 || nwrites == 5);
        unsigned ctrl = writes[start], slot = ctrl - 9;
        assert(ctrl >= 9 && ctrl <= 12);
        /* Disable first, restore shared/B/A, original control last. */
        assert(writes[start+1] == 8 && writes[start+2] == 2*slot+1);
        assert(writes[start+3] == 2*slot && writes[start+4] == ctrl);
        if (start) assert(writes[0] == 2*slot && writes[1] == 2*slot+1 && writes[2] == 8 && writes[3] == ctrl);
    }
    if (!inject && !drop_at) assert(!strcmp(status, "PASS") && stages == 15);
    if (cancel_after && L.profiles+1 == cancel_after) L.stop = true;
}
'''

MAIN = r'''
static void reset(void) {
    memset(&L, 0, sizeof(L)); L.gain = 52;
    for (unsigned i=0;i<13;i++) words[i] = 0xa5a5c123u ^ (i*0x1020304u);
    for (unsigned i=9;i<13;i++) words[i] &= ~0x2000u;
    memcpy(baseline, words, sizeof(words));
    nwrites = candidates = shams = skipped = context_calls = 0;
    inject = fail_restore_context = expect_fatal = false; physical = true;
    drop_at = cancel_after = 0;
}
int main(void) {
    reset(); suite_spurs(); assert(candidates == 8 && shams == 9 && !skipped && L.profiles == 17);
    for (unsigned mask=0;mask<16;mask++) {
        reset(); unsigned active=0;
        for (unsigned s=0;s<4;s++) if (mask & (1u<<s)) { words[9+s] |= 0x2000; ++active; }
        memcpy(baseline, words, sizeof(words)); suite_spurs();
        assert(skipped == active && candidates == 2*(4-active) && shams == 1+candidates);
    }
    reset(); physical = false; suite_spurs(); assert(skipped == 1 && !L.profiles && !nwrites);
    for (unsigned s=0;s<4;s++) {
        reset(); inject = true; fail_stage = s; cause = "gain"; suite_spurs();
        assert(L.stop && candidates == 1 && !memcmp(words, baseline, sizeof(words)));
    }
    const char *causes[] = {"capture", "no_iq", "stalled_iq"};
    for (unsigned c=0;c<3;c++) for (unsigned s=1;s<=2;s++) {
        reset(); inject = true; fail_stage = s; cause = causes[c]; suite_spurs();
        assert(!L.stop && L.broken == 8 && candidates == 8);
    }
    for (unsigned w=1;w<=4;w++) {
        reset(); drop_at = w; suite_spurs(); assert(L.rejected == 8 && !L.stop);
    }
    for (unsigned stop=1;stop<=17;stop++) {
        reset(); cancel_after = stop; suite_spurs(); assert(L.profiles == stop && L.stop);
    }
    reset(); expect_fatal = fail_restore_context = true;
    if (!setjmp(escape)) { suite_spurs(); assert(!"missing physical-context fatal"); }
    assert(candidates == 1 && !memcmp(words, baseline, sizeof(words)));
    puts("PASS: production spur profiles, four-word rollback/order, skips, failures and physical guard");
}
'''


class SpurSuiteTest(unittest.TestCase):
    def test_production_suite_and_runner(self):
        source = (ROOT / 'main/sense_lab.c').read_text()
        extracted = ''.join(function(source, signature) for signature in (
            'static bool patches_ok(', 'static bool candidate_failure(',
            'static void run_patch(', 'static void suite_spurs('))
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            c, executable = base / 'spur.c', base / 'spur'
            c.write_text(HARNESS + extracted + MAIN)
            subprocess.run([os.environ.get('CC', 'cc'), '-std=c11', '-O2',
                            '-Wall', '-Wextra', '-Werror', '-I', str(ROOT / 'main'),
                            str(c), '-lm', '-o', str(executable)], check=True)
            run = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
            self.assertEqual(run.returncode, 0, (run.stdout + run.stderr)[-12000:])
            print(run.stdout.splitlines()[-1])


if __name__ == '__main__':
    unittest.main()

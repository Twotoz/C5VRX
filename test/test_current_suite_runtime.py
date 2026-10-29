"""C5VRX by Twotoz/contributors: execute production CURRENT profiles on host.

Extract the production accessors, verifier, runner and suite. Only vendor/SDK
boundaries and measurement/reporting are mocked; these tests establish control
flow and byte restoration, not a hardware current or sensitivity effect.
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


BOUNDARIES = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "sense_lab_logic.h"
static uint8_t phy_param[128];
static unsigned setter_calls, i2c_calls, mmio_calls;
static uint8_t setter_values[256];
static bool drop_next_write;
static void phy_current_level_set(uint8_t value) {
    assert(setter_calls < sizeof(setter_values));
    setter_values[setter_calls++] = value;
    if (!drop_next_write) phy_param[41] = value;
    drop_next_write = false;
}
static uint32_t command_port, busy_port;
static bool simulate_pbus, pending_write;
static uint32_t pbus_word, pending_word;
static unsigned pbus_reads, pbus_writes, timer_calls;
static uint32_t *mmio(unsigned addr) {
    ++mmio_calls;
    assert(simulate_pbus);
    if (addr == 0x600a0884u) return &command_port;
    assert(addr == 0x600a0890u);
    /* Observe the command's write strobe on the following status read.
     * Complete it on the next vendor read, even if command is restored first. */
    if (command_port & 2u) {
        assert((command_port & 0x800du) == 0x8009u); /* block 2, host 1, debug */
        pending_word = (command_port >> 6) & 0x1ffu;
        pending_write = true;
        ++pbus_writes;
    }
    return &busy_port; /* idle: no hardware polling delay */
}
#define MMIO(addr) (*mmio(addr))
static uint8_t phy_i2c_readReg(unsigned b, unsigned h, unsigned r) {
    (void)b; (void)h; (void)r; ++i2c_calls; return 0;
}
static void phy_i2c_writeReg(unsigned b, unsigned h, unsigned r, uint8_t v) {
    (void)b; (void)h; (void)r; (void)v; ++i2c_calls;
}
static uint32_t phy_pbus_rd(unsigned block, unsigned host) {
    assert(simulate_pbus && block == 2 && host == 1);
    ++pbus_reads;
    if (pending_write) { pbus_word = pending_word; pending_write = false; }
    return pbus_word;
}
static int64_t esp_timer_get_time(void) {
    assert(simulate_pbus); return ++timer_calls;
}
static void fatal(const char *why) { (void)why; abort(); }
static inline bool physical_context_ok(void) { return true; }
static void check_surroundings(void) {
    for (unsigned i = 0; i < sizeof(phy_param); ++i)
        if (i != 41) assert(phy_param[i] == 0xa5);
    assert(!i2c_calls && !mmio_calls);
}
'''

ACCESSORS = r'''
int main(void) {
    /* Nonzero I2C/MMIO fields make accidental fallthrough observable. */
    const sense_patch_t p = {SENSE_P_CURRENT, 0x6a, 1, 7, 0x600a0000, 0xff, 0};
    memset(phy_param, 0xa5, sizeof(phy_param));
    for (unsigned stock = 0; stock <= 255; ++stock) {
        phy_param[41] = (uint8_t)stock;
        const uint32_t saved = patch_read(&p);
        assert(saved == stock);
        setter_calls = 0;
        patch_write(&p, 255 - stock);
        assert(setter_calls == 1 && setter_values[0] == 255 - stock);
        assert(patch_read(&p) == 255 - stock);
        /* Read the byte, never a cached setter argument. */
        phy_param[41] ^= 0x80;
        assert(patch_read(&p) == ((255 - stock) ^ 0x80));
        patch_write(&p, saved);
        assert(setter_calls == 2 && setter_values[1] == stock);
        assert(patch_read(&p) == stock);
        check_surroundings();
    }
    /* Dropped setter writes must remain visible to the caller. */
    phy_param[41] = 173; setter_calls = 0; drop_next_write = true;
    patch_write(&p, 7);
    assert(setter_calls == 1 && setter_values[0] == 7 && patch_read(&p) == 173);
    check_surroundings();
    puts("PASS: production CURRENT setter/readback and all 256 stock byte restores");
}
'''

RUNTIME = r'''
static struct { bool stop; uint8_t gain; unsigned profiles, rejected, broken; } L;
static const sense_profile_t *s_active;
static uint32_t s_expect[SENSE_MAX_PATCHES];
static const sense_profile_t k_sham = {"SHAM", SENSE_K_PATCH, 0, 0, {{0}}};
typedef struct { unsigned unused; } stage_t;
static const uint8_t levels[] = {
    0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 31, 63, 127, 255
};
static uint8_t stock;
static unsigned reports, stages[41], observations, stop_at, fail_stage;
static unsigned rejected_at;
static const char *failure;
static bool filter_ok(void) { check_surroundings(); return true; }
static void phy_observation_changed(void) { ++observations; }
static void print_patches(unsigned id, const sense_profile_t *p, const uint32_t *saved) {
    assert(id == 1100 + reports && reports < 41 && p == s_active);
    assert(p->kind == SENSE_K_PATCH);
    if (reports % 2 == 0) {
        assert(p == &k_sham && p->n == 0);
    } else {
        const unsigned k = reports / 2;
        char name[32]; snprintf(name, sizeof(name), "CURRENT_LEVEL_%u", levels[k]);
        assert(!strcmp(name, p->name) && p->n == 1 && p->gain == 0);
        const sense_patch_t *q = &p->p[0];
        assert(q->kind == SENSE_P_CURRENT && q->mask == 0xff && q->value == levels[k]);
        assert(!q->block && !q->host && !q->reg && !q->addr);
        assert(saved[0] == stock);
    }
    assert(phy_param[41] == stock);
}
static const char *measure_stage(unsigned id, unsigned stage, uint8_t gain,
                                 bool held, stage_t *out) {
    assert(id == 1100 + reports && gain == 52 && held && out && stage < 4);
    assert(!(stages[reports] & (1u << stage)));
    stages[reports] |= 1u << stage;
    const unsigned value = (reports % 2 && (stage == 1 || stage == 2))
        ? levels[reports / 2] : stock;
    assert(phy_param[41] == value && patches_ok());
    check_surroundings();
    /* A real external byte change is caught by the production verifier. */
    if (s_active->n) {
        phy_param[41] ^= 0x80;
        assert(!patches_ok());
        phy_param[41] ^= 0x80;
        assert(patches_ok());
    }
    if (stop_at == reports + 1 && stage == fail_stage) return failure;
    return NULL;
}
static void result(unsigned id, const char *name, const char *status, const stage_t *st) {
    assert(id == 1100 + reports && name && st && !s_active);
    assert(phy_param[41] == stock);
    if (rejected_at == reports + 1) {
        assert(!strcmp(status, "REJECTED_ENTRY") && stages[reports] == 9);
    } else if (stop_at == reports + 1) {
        assert(!strcmp(status, candidate_failure(failure) ? "BREAKS_IQ" : "INCOMPLETE"));
    } else {
        assert(!strcmp(status, "PASS") && stages[reports] == 15);
    }
    ++reports;
    check_surroundings();
}
static void reset(uint8_t saved) {
    memset(&L, 0, sizeof(L)); L.gain = 52;
    memset(phy_param, 0xa5, sizeof(phy_param));
    stock = saved; phy_param[41] = stock;
    reports = setter_calls = observations = stop_at = rejected_at = 0;
    memset(stages, 0, sizeof(stages));
    drop_next_write = false;
}
'''

SUITE = r'''
int main(void) {
    /* Every possible entry byte, including 255, survives the entire suite. */
    for (unsigned saved = 0; saved <= 255; ++saved) {
        reset((uint8_t)saved); suite_current();
        assert(reports == 41 && L.profiles == 41 && !L.stop);
        assert(!L.rejected && !L.broken && observations == 82 && setter_calls == 40);
        for (unsigned k = 0; k < 20; ++k) {
            assert(setter_values[2*k] == levels[k]);
            assert(setter_values[2*k+1] == saved);
        }
    }
    /* Context failure at any stage of any candidate or sham must stop the
     * suite there, restore first, and never schedule another profile. */
    for (unsigned profile = 1; profile <= 41; ++profile) {
        for (unsigned stage = 0; stage < 4; ++stage) {
            reset(255); stop_at = profile; fail_stage = stage; failure = "gain";
            suite_current();
            assert(L.stop && reports == profile && L.profiles == profile);
            assert(!L.rejected && !L.broken && phy_param[41] == 255);
            unsigned mask = (1u << (stage + 1)) - 1;
            assert(stages[profile-1] == mask);
            const unsigned candidates = profile / 2;
            const bool last_candidate = profile % 2 == 0;
            assert(setter_calls == 2*candidates - (last_candidate && stage == 0));
        }
    }
    /* A failed setter entry is rejected by actual readback, then restored;
     * the stock witness and later candidates still run. */
    reset(173); drop_next_write = true; rejected_at = 2;
    suite_current();
    assert(reports == 41 && L.rejected == 1 && !L.stop && !L.broken);
    assert(setter_calls == 40 && phy_param[41] == 173);
    /* Candidate capture failures restore and witness stock before continuing. */
    const char *causes[] = {"stalled_iq", "no_iq", "capture"};
    for (unsigned i = 0; i < 3; ++i) {
        for (unsigned stage = 1; stage <= 2; ++stage) {
            reset(255); stop_at = 40; fail_stage = stage; failure = causes[i];
            suite_current();
            assert(reports == 41 && !L.stop && L.broken == 1 && !L.rejected);
            assert(stages[39] == (stage == 1 ? 11u : 15u));
            assert(setter_calls == 40 && phy_param[41] == 255);
        }
    }
    puts("PASS: production CURRENT suite coverage, shams, stop, rejection and rollback");
}
'''


PBUS_RUNTIME = r'''
static struct { bool stop; uint8_t gain; unsigned profiles, rejected, broken; } L;
static const sense_profile_t *s_active;
static uint32_t s_expect[SENSE_MAX_PATCHES];
typedef struct { unsigned unused; } stage_t;
static bool patches_ok(void);
static uint32_t entry_command;
static unsigned stages, fail_stage;
static bool inject_failure;
static void phy_observation_changed(void) {}
static bool filter_ok(void) { return true; }
static void print_patches(unsigned id, const sense_profile_t *p, const uint32_t *saved) {
    assert(id == 1200 && p == s_active && p->n == 2);
    assert(saved[0] == (0x155u & 0x7fu) && saved[1] == (0xa5u & 0x3cu));
}
static const char *measure_stage(unsigned id, unsigned stage, uint8_t gain,
                                 bool held, stage_t *out) {
    assert(id == 1200 && gain == 52 && held && out && stage < 4);
    stages |= 1u << stage;
    const bool test = stage == 1 || stage == 2;
    assert(patches_ok());
    assert(pbus_word == (test ? 0x12au : 0x155u));
    assert(phy_param[57] == (test ? 0x99u : 0xa5u));
    if (!test) assert(command_port == entry_command);
    return inject_failure && stage == fail_stage ? "gain" : NULL;
}
static void result(unsigned id, const char *name, const char *status, const stage_t *st) {
    assert(id == 1200 && !strcmp(name, "PBUS_PARAM") && st && !s_active);
    assert(!strcmp(status, inject_failure ? "INCOMPLETE" : "PASS"));
    assert(command_port == entry_command && pbus_word == 0x155u);
    assert(phy_param[57] == 0xa5u && !pending_write);
}
'''

PBUS_MAIN = r'''
int main(void) {
    memset(phy_param, 0xa5, sizeof(phy_param));
    check_surroundings();
    /* PARAM owns only its mask, never its neighbours or CURRENT's byte. */
    const sense_patch_t param = {SENSE_P_PARAM, 0, 0, 0, 57, 0x3c, 0x18};
    for (unsigned stock = 0; stock < 256; ++stock) {
        phy_param[57] = stock;
        uint32_t saved = patch_read(&param);
        assert(saved == (stock & 0x3c));
        patch_write(&param, 0x118);
        assert(phy_param[57] == ((stock & ~0x3cu) | 0x18));
        patch_write(&param, saved);
        assert(phy_param[57] == stock && phy_param[41] == 0xa5);
    }
    phy_param[57] = 0xa5;
    check_surroundings();
    simulate_pbus = true;
    const sense_profile_t profile = {"PBUS_PARAM", SENSE_K_PATCH, 2, 0, {
        {SENSE_P_PBUS, 2, 1, 0, 0, 0x7f, 0x2a},
        {SENSE_P_PARAM, 0, 0, 0, 57, 0x3c, 0x18}
    }};
    /* Full run and context failure at each stage: both the masked PBUS
     * word and the entire original command port must be restored. */
    for (unsigned scenario = 0; scenario < 5; ++scenario) {
        memset(&L, 0, sizeof(L)); L.gain = 52;
        stages = pbus_reads = pbus_writes = timer_calls = 0;
        pbus_word = 0x155; entry_command = command_port = 0xa5a41235u;
        assert(!(command_port & 2u));
        inject_failure = scenario != 0; fail_stage = scenario - 1;
        run_patch(1200, &profile);
        assert(L.profiles == 1 && L.stop == inject_failure);
        assert(!L.rejected && !L.broken && !busy_port);
        assert(stages == (inject_failure ? (1u << (fail_stage + 1)) - 1 : 15u));
        const unsigned writes = inject_failure && fail_stage == 0 ? 1 : 2;
        assert(pbus_writes == writes && timer_calls >= writes && timer_calls <= 4*writes);
        assert(pbus_reads > writes && pbus_reads < 20);
        assert(!setter_calls && !i2c_calls);
        for (unsigned i = 0; i < sizeof(phy_param); ++i) assert(phy_param[i] == 0xa5);
    }
    puts("PASS: production PARAM masks, PBUS readback and exact command restore on success/stop");
}
'''


class CurrentSuiteTest(unittest.TestCase):
    def compile_and_run(self, content):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source, executable = base / 'current.c', base / 'current'
            source.write_text(content)
            subprocess.run([os.environ.get('CC', 'cc'), '-std=c11', '-O2',
                            '-Wall', '-Wextra', '-Werror', '-I', str(ROOT / 'main'),
                            str(source), '-lm', '-o', str(executable)], check=True)
            # Production prints every stage; retain output for failures without
            # flooding CI with the exhaustive stock/cancellation matrix.
            run = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
            self.assertEqual(run.returncode, 0, (run.stdout + run.stderr)[-12000:])
            print(run.stdout.splitlines()[-1])

    def test_current_accessors(self):
        source = (ROOT / 'main/sense_lab.c').read_text()
        accessors = ''.join(function(source, signature) for signature in (
            'static uint32_t patch_read(', 'static void patch_write('))
        self.compile_and_run(BOUNDARIES + accessors + ACCESSORS)

    def test_current_suite_runtime(self):
        source = (ROOT / 'main/sense_lab.c').read_text()
        accessors = ''.join(function(source, signature) for signature in (
            'static uint32_t patch_read(', 'static void patch_write('))
        verifier = function(source, 'static bool patches_ok(')
        candidate = function(source, 'static bool candidate_failure(')
        # Declarations needed by measurement stubs; definitions remain extracted.
        declarations = 'static bool patches_ok(void);\nstatic bool candidate_failure(const char *why);\n'
        self.compile_and_run(BOUNDARIES + accessors + declarations + RUNTIME
                             + verifier + candidate
                             + function(source, 'static void run_patch(')
                             + function(source, 'static void suite_current(') + SUITE)

    def test_pbus_param_command_restore(self):
        source = (ROOT / 'main/sense_lab.c').read_text()
        accessors = ''.join(function(source, signature) for signature in (
            'static uint32_t patch_read(', 'static void patch_write('))
        self.compile_and_run(BOUNDARIES + accessors + PBUS_RUNTIME
                             + function(source, 'static bool patches_ok(')
                             + function(source, 'static bool candidate_failure(')
                             + function(source, 'static void run_patch(') + PBUS_MAIN)


if __name__ == '__main__':
    unittest.main()

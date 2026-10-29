"""C5VRX by Twotoz/contributors: production loopcap suite on simulated PBUS.

Host control-flow evidence only, not evidence of an RX sensitivity effect.
The accessors, runner, suite and physical gain/DC guard are extracted unchanged.
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
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <setjmp.h>
#include "sense_lab_logic.h"
static uint8_t phy_param[1024], entry_param[1024];
static uint32_t pbus[16][3], entry_pbus[16][3];
static uint32_t command, entry_command, busy, pending_command;
static bool pending, held, pair_readable, tuple_valid, fail_dc_after_test;
static int live_pair[2], live_offset;
static unsigned live_freq, s_current_gain;
static unsigned writes, reads, ticks, drop_write, reports, skipped, stage_bits[17];
static unsigned cancel_id, cancel_stage;
static unsigned expected_bin, active_id, rejected_id;
static const char *fatal_reason, *skip_reason;
static jmp_buf fatal_jump;
static bool expect_fatal;
static struct {
    bool stop, dco_check;
    uint8_t gain;
    unsigned freq, profiles, rejected, broken;
    int offset, dco[2];
} L;
static const sense_profile_t *s_active;
static uint32_t s_expect[SENSE_MAX_PATCHES];
static const sense_profile_t k_sham = {"SHAM", SENSE_K_PATCH, 0, 0, {{0}}};
typedef struct { unsigned unused; } stage_t;
static bool patches_ok(void);
static bool physical_context_ok(void);
static void fatal(const char *why) {
    assert(expect_fatal); fatal_reason = why; longjmp(fatal_jump, 1);
}
static void finish_write(void) {
    if (!pending) return;
    const unsigned block = (pending_command >> 2) & 15u;
    const unsigned bank = (pending_command >> 15) & 3u;
    assert((block == 9 || block == 10) && bank == 2);
    if (writes != drop_write) pbus[block][bank] = (pending_command >> 6) & 511u;
    pending = false;
}
static uint32_t *mmio(unsigned address) {
    if (address == 0x600a0884u) return &command;
    assert(address == 0x600a0890u);
    if (command & 2u) {
        finish_write();
        assert(command & 1u);
        pending_command = command; pending = true; ++writes;
    }
    return &busy;
}
#define MMIO(address) (*mmio(address))
static uint32_t phy_pbus_rd(unsigned block, unsigned bank) {
    assert(block < 16 && bank < 3); ++reads; finish_write();
    return pbus[block][bank];
}
static int64_t esp_timer_get_time(void) { return ++ticks; }
static void phy_current_level_set(uint8_t v) { (void)v; abort(); }
static uint8_t phy_i2c_readReg(unsigned b, unsigned h, unsigned r) {
    (void)b; (void)h; (void)r; abort();
}
static void phy_i2c_writeReg(unsigned b, unsigned h, unsigned r, uint8_t v) {
    (void)b; (void)h; (void)r; (void)v; abort();
}
static unsigned rf_frequency_mhz(void) { return live_freq; }
static int rf_offset_khz(void) { return live_offset; }
static bool tuple_of(uint8_t gain, uint32_t *packed) {
    assert(gain == 52); *packed = 0x123456; return tuple_valid;
}
static bool phy_dco_held(void) { return held; }
static bool phy_dco_read_pair(int pair[2]) {
    memcpy(pair, live_pair, sizeof(live_pair)); return pair_readable;
}
static bool filter_ok(void) { return true; }
static void phy_observation_changed(void) {}
static void full_restore(void) {
    finish_write();
    assert(!memcmp(phy_param, entry_param, sizeof(phy_param)));
    assert(!memcmp(pbus, entry_pbus, sizeof(pbus)));
    assert(command == entry_command);
}
static unsigned adjusted_cap(unsigned bin, unsigned b) {
    /* Explicit byte-wrap oracle, including the vendor's negative offsets. */
    unsigned value;
    if (bin < 2) {
        const unsigned subtract = bin == 0 ? 4 : 3;
        value = b < subtract ? 256 + b - subtract : b - subtract;
    } else value = b + 4;
    return value < 1 ? 1 : value > 14 ? 14 : value;
}
static void print_patches(unsigned id, const sense_profile_t *p, const uint32_t *saved) {
    active_id = id;
    assert(id >= 1200 && id <= 1216 && p == s_active);
    full_restore();
    if (!(id & 1u)) { assert(p == &k_sham && p->n == 0); return; }
    const unsigned k = (id - 1201) / 2;
    const unsigned bin = k ? k - 1 : expected_bin;
    const unsigned a = entry_param[0x2d2 + 2*bin], b = entry_param[0x2d3 + 2*bin];
    char name[40]; snprintf(name, sizeof(name), "LOOPCAP_%s_BIN%u", k ? "CAL" : "CURRENT", bin);
    assert(!strcmp(p->name, name) && p->kind == SENSE_K_PATCH && p->n == 3);
    assert(p->p[0].kind == SENSE_P_PARAM && p->p[0].addr == 690);
    assert(p->p[0].mask == 255 && p->p[0].value == 8 && saved[0] == entry_param[690]);
    for (unsigned i = 1; i < 3; ++i) {
        assert(p->p[i].kind == SENSE_P_PBUS && p->p[i].block == 8+i);
        assert(p->p[i].host == 2 && p->p[i].mask == 511);
        assert(saved[i] == entry_pbus[8+i][2]);
    }
    assert(p->p[1].value == ((a << 4) | 8));
    assert(p->p[2].value == adjusted_cap(bin, b));
}
static const char *measure_stage(unsigned id, unsigned stage, uint8_t gain,
                                 bool dc, stage_t *out) {
    assert(id == active_id && stage < 4 && gain == 52 && dc && out);
    assert(!(stage_bits[id-1200] & (1u << stage)));
    stage_bits[id-1200] |= 1u << stage;
    assert(patches_ok());
    if (stage == 0 || stage == 3 || !(id & 1u)) full_restore();
    else {
        assert(phy_param[690] == 8);
        assert(pbus[9][2] == s_active->p[1].value);
        assert(pbus[10][2] == s_active->p[2].value);
        for (unsigned i = 0; i < sizeof(phy_param); ++i)
            if (i != 690) assert(phy_param[i] == entry_param[i]);
    }
    if (fail_dc_after_test && id == 1201 && stage == 1) { held = false; return "dc_pair"; }
    return id == cancel_id && stage == cancel_stage ? "deadline" : NULL;
}
static void result(unsigned id, const char *name, const char *status, const stage_t *st) {
    assert(id == active_id && name && st && !s_active); full_restore();
    const char *expected = id == rejected_id ? "REJECTED_ENTRY" :
        id == cancel_id ? "INCOMPLETE" : "PASS";
    assert(!strcmp(status, expected)); ++reports;
}
static void skip(unsigned id, const char *name, const char *why) {
    assert(id >= 1200 && id <= 1215 && !strcmp(name, "LOOPCAP"));
    ++skipped; skip_reason = why;
}
static void reset(unsigned freq, unsigned b) {
    memset(&L, 0, sizeof(L)); L.gain = 52; L.freq = freq; L.offset = 125;
    L.dco_check = true; L.dco[0] = 17; L.dco[1] = -29;
    live_freq = freq; live_offset = 125; s_current_gain = 52;
    held = pair_readable = tuple_valid = true;
    memcpy(live_pair, L.dco, sizeof(live_pair));
    memset(phy_param, 0xa5, sizeof(phy_param)); phy_param[42] = 1;
    for (unsigned i = 0; i < 7; ++i) {
        phy_param[0x2d2 + 2*i] = i ? 31 : 0;
        phy_param[0x2d3 + 2*i] = b;
    }
    memcpy(entry_param, phy_param, sizeof(phy_param));
    for (unsigned i = 0; i < 16; ++i)
        for (unsigned j = 0; j < 3; ++j) pbus[i][j] = (i*17 + j*31) & 511;
    pbus[8][1] = 0x123; pbus[0][2] = 0x45; pbus[1][2] = 0xd6;
    memcpy(entry_pbus, pbus, sizeof(pbus));
    entry_command = command = 0xa5a41235u; busy = 0; pending = false;
    writes = reads = ticks = drop_write = reports = skipped = cancel_id = rejected_id = 0;
    memset(stage_bits, 0, sizeof(stage_bits));
    fail_dc_after_test = expect_fatal = false; fatal_reason = skip_reason = NULL;
}
'''

MAIN = r'''
int main(void) {
    const unsigned frequencies[] = {5180, 5240, 5241, 5320, 5321, 5560, 5561,
        5640, 5641, 5755, 5756, 5835, 5836, 5865};
    const unsigned bins[] = {0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6};
    for (unsigned f = 0; f < sizeof(frequencies)/sizeof(frequencies[0]); ++f) {
        for (unsigned b = 0; b <= 15; ++b) {
            reset(frequencies[f], b); expected_bin = bins[f]; suite_loopcap();
            assert(reports == 17 && L.profiles == 17 && !L.stop && !skipped);
            assert(writes == 32 && ticks >= writes && ticks <= 4*writes);
            assert(!L.rejected && !L.broken);
            for (unsigned i = 0; i < 17; ++i) assert(stage_bits[i] == 15);
            full_restore();
        }
    }
    /* Dropping either capacitor write must reject entry, restore both words
     * and PARAM, witness STOCK, and continue with the remaining profiles. */
    for (unsigned drop = 1; drop <= 2; ++drop) {
        reset(5865, 0); expected_bin = 6; drop_write = drop; rejected_id = 1201;
        suite_loopcap();
        assert(L.rejected == 1 && !L.stop && reports == 17 && stage_bits[1] == 9);
        assert(writes == 32); full_restore();
    }
    for (unsigned id = 1200; id <= 1216; ++id) {
        for (unsigned stage = 0; stage < 4; ++stage) {
            reset(5865, 15); expected_bin = 6; cancel_id = id; cancel_stage = stage;
            suite_loopcap();
            assert(L.stop && reports == id-1199 && L.profiles == reports);
            assert(stage_bits[id-1200] == (1u << (stage+1))-1);
            assert(!L.rejected && !L.broken); full_restore();
        }
    }
    /* Entry guard exercises the actual physical_context_ok predicate. */
    for (unsigned bad = 0; bad < 13; ++bad) {
        reset(5865, 0);
        switch (bad) {
        case 0: phy_param[42] = 0; break;
        case 1: L.dco_check = false; break;
        case 2: held = false; break;
        case 3: pair_readable = false; break;
        case 4: ++live_pair[0]; break;
        case 5: ++live_pair[1]; break;
        case 6: ++live_freq; break;
        case 7: ++live_offset; break;
        case 8: ++s_current_gain; break;
        case 9: tuple_valid = false; break;
        case 10: pbus[8][1] ^= 1; break;
        case 11: command &= ~1u; break;
        case 12: command |= 2u; break;
        }
        suite_loopcap();
        assert(!reports && !writes && !L.profiles && skipped == 1);
        assert(!strcmp(skip_reason, "requires_5ghz_pbus_debug_idle"));
    }
    /* Invalid calibration bytes are skipped before any patch writes. */
    for (unsigned bad = 0; bad < 2; ++bad) {
        reset(5865, 0); expected_bin = 6;
        for (unsigned i = 0; i < 7; ++i)
            phy_param[0x2d2 + 2*i + bad] = bad ? 16 : 32;
        memcpy(entry_param, phy_param, sizeof(phy_param)); suite_loopcap();
        assert(reports == 1 && skipped == 8 && !writes && !L.stop);
        assert(!strcmp(skip_reason, "calibration_bytes_out_of_range")); full_restore();
    }
    /* Loss of DC during TEST restores all state, then fails closed rather
     * than starting another profile on a matching software gain alone. */
    reset(5865, 0); expected_bin = 6; fail_dc_after_test = expect_fatal = true;
    if (!setjmp(fatal_jump)) { suite_loopcap(); assert(false); }
    assert(!strcmp(fatal_reason, "physical_context_restore_failed"));
    assert(reports == 1 && L.profiles == 1 && writes == 4); full_restore();
    puts("PASS: production loopcap coverage, byte wrap, full rollback, dropped writes and physical DC guards");
}
'''


class LoopcapSuiteTest(unittest.TestCase):
    def test_production_loopcap(self):
        source = (ROOT / 'main/sense_lab.c').read_text()
        production = ''.join(function(source, signature) for signature in (
            'static uint32_t patch_read(', 'static void patch_write(',
            'static bool patches_ok(', 'static bool hw_gain_ok(',
            'static bool physical_context_ok(', 'static bool candidate_failure(',
            'static void run_patch(', 'static void suite_loopcap('))
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            c_file, executable = base / 'loopcap.c', base / 'loopcap'
            c_file.write_text(HARNESS + production + MAIN)
            subprocess.run([os.environ.get('CC', 'cc'), '-std=c11', '-O2',
                            '-Wall', '-Wextra', '-Werror', '-I', str(ROOT / 'main'),
                            str(c_file), '-lm', '-o', str(executable)], check=True)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, (result.stdout + result.stderr)[-12000:])
            print(result.stdout.splitlines()[-1])


if __name__ == '__main__':
    unittest.main()

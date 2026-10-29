"""C5VRX by Twotoz/contributors: production bias suite's bounds and ordering."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class BiasSuiteTest(unittest.TestCase):
    def test_witnessed_states_and_stop(self):
        source = (ROOT / 'main/sense_lab.c').read_text()
        start = source.index('static void suite_bias(void)')
        end = source.index('\n}', start) + 2
        prefix = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "sense_lab_logic.h"
static struct { bool stop; } L;
static bool s_noise_mode;
static uint8_t reg0 = 0xcf, reg1 = 0x7f;
static unsigned calls, skipped, cancel_after;
#define P_I SENSE_I2C
static const sense_profile_t k_sham = {"SHAM", SENSE_K_PATCH, 0, 0, {{0}}};
static uint8_t phy_i2c_readReg(unsigned b, unsigned h, unsigned r) {
 assert(b == 0x6a && h == 1 && r <= 1); return r ? reg1 : reg0;
}
static void skip(unsigned id, const char *name, const char *reason) {
 assert(id == 1000 && !strcmp(name, "BIAS_INIT_REG0"));
 assert(!strcmp(reason, "outside_witnessed_operating_state")); ++skipped;
}
static void run_patch(unsigned id, const sense_profile_t *p) {
 assert(id == 1000 + calls);
 if (calls % 2) {
   assert(p->n == 1 && p->p[0].kind == SENSE_P_I2C);
   assert(p->p[0].block == 0x6a && p->p[0].host == 1 && p->p[0].reg == 0);
   assert(p->p[0].mask == 0xf0 && p->p[0].value == 0x90);
 } else assert(p == &k_sham);
 if (++calls == cancel_after) L.stop = true;
}
'''
        suffix = r'''
int main(void) {
 suite_bias(); assert(calls == 7 && !skipped);
 calls = 0; reg0 = 0x9f; suite_bias(); assert(!calls && skipped == 1);
 reg0 = 0xcf; reg1 = 0x6f; suite_bias(); assert(!calls && skipped == 2);
 reg1 = 0x7f; cancel_after = 1; suite_bias(); assert(calls == 1 && L.stop);
 puts("PASS: bias suite only compares witnessed states, touches one mask, interleaves shams, honors stop");
}
'''
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp)
            (p / 'test.c').write_text(prefix + source[start:end] + suffix)
            subprocess.run([os.environ.get('CC', 'cc'), '-std=c11', '-O2', '-Wall', '-Wextra',
                            '-Werror', '-I', str(ROOT / 'main'), str(p / 'test.c'), '-lm',
                            '-o', str(p / 'test')], check=True)
            subprocess.run([str(p / 'test')], check=True)


if __name__ == '__main__':
    unittest.main()

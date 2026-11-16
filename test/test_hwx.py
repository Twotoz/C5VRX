#!/usr/bin/env python3
"""C5VRX by Twotoz/contributors: hardware experiment pin maps (hwx.h).

Checks every hardware_experiments/<id>/hwx.h against the XIAO ESP32-C5 pads
and the firmware hooks; not electrical timing or FPGA capture.
"""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
# XIAO ESP32-C5 header pads D0..D10 and the back-side JTAG pads MTMS/MTDI/MTCK/MTDO.
XIAO_PADS = {1, 0, 25, 7, 23, 24, 11, 12, 8, 9, 10, 2, 3, 4, 5}
RESERVED = {13, 14, 27, 28}   # USB D-/D+, user LED, BOOT button


def defines(text):
    out = {}
    for name, value in re.findall(r'^#define (HWX_\w+) (.+)$', text, re.M):
        value = value.strip()
        out[name] = [int(v) for v in value.strip('{}').split(',')] if value.startswith('{') else value
    return out


class HardwareExperiments(unittest.TestCase):
    def experiments(self):
        found = sorted(p.parent for p in (ROOT / 'hardware_experiments').glob('*/hwx.h'))
        self.assertTrue(found)
        return found

    def test_ids_and_pads(self):
        for path in self.experiments():
            with self.subTest(path.name):
                self.assertRegex(path.name, r'^[a-z0-9_]+$')
                d = defines((path / 'hwx.h').read_text())
                self.assertIn('HWX_NAME', d)
                pads = list(d.get('HWX_IQ_GPIO_NUMS', []))
                self.assertIn(len(pads), (0, 8))
                n = int(d.get('HWX_EXTRA_BITS', '0'))
                if n:
                    self.assertEqual((len(d['HWX_EXTRA_Q_GPIOS']), len(d['HWX_EXTRA_I_GPIOS'])), (n, n))
                    # Lowest exported bit on ULTRAFINE is ADC4: at most four bits below it.
                    self.assertLessEqual(n, 4)
                    pads += d['HWX_EXTRA_Q_GPIOS'] + d['HWX_EXTRA_I_GPIOS']
                if 'HWX_CLOCK_GPIO' in d:
                    pads.append(int(d['HWX_CLOCK_GPIO']))
                self.assertEqual(len(pads), len(set(pads)), 'a pad is used twice')
                self.assertFalse(set(pads) & RESERVED)
                self.assertLessEqual(set(pads), XIAO_PADS)
                if 'HWX_NO_DAC' not in d:
                    self.assertFalse(set(pads) & {23, 24, 11, 12, 8, 9}, 'DAC pads need HWX_NO_DAC')

    def test_fpga_iq_matches_the_tang_constraints(self):
        d = defines((ROOT / 'hardware_experiments/fpga_iq/hwx.h').read_text())
        self.assertEqual(d['HWX_IQ_GPIO_NUMS'], [1, 0, 25, 7, 10, 5, 3, 4])
        cst = (ROOT / 'hardware_experiments/fpga_iq/tang_primer_25k_dock.cst').read_text()
        balls = dict(re.findall(r'IO_LOC "iq\[(\d)\]" (\w+);', cst))
        self.assertEqual(balls, {'0': 'C11', '1': 'C10', '2': 'B11', '3': 'B10',
                                 '4': 'D11', '5': 'D10', '6': 'G11', '7': 'G10'})

    def test_production_never_includes_an_experiment(self):
        rf = (ROOT / 'main/rf.h').read_text()
        self.assertIn('#ifdef C5VRX_HWX\n#include "hwx.h"\n#endif', rf)
        self.assertIn('#define RF_IQ_GPIO_NUMS {1, 0, 25, 2, 27, 5, 3, 4}', rf)
        cmake = (ROOT / 'main/CMakeLists.txt').read_text()
        self.assertIn('if(C5VRX_HWX)', cmake)
        for name in ('transport.c', 'rf.c', 'experimental.c'):
            source = (ROOT / 'main' / name).read_text()
            for hook in re.findall(r'HWX_\w+', source):
                self.assertIn(hook, {'HWX_CLOCK_GPIO', 'HWX_NO_DAC', 'HWX_EXTRA_BITS',
                                     'HWX_EXTRA_Q_GPIOS', 'HWX_EXTRA_I_GPIOS'}, name)


if __name__ == '__main__':
    unittest.main()

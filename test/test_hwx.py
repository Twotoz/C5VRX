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
                self.assertEqual(len(pads), len(set(pads)), 'a pad is used twice')
                self.assertFalse(set(pads) & RESERVED)
                self.assertLessEqual(set(pads), XIAO_PADS)
                if 'HWX_NO_DAC' not in d:
                    self.assertFalse(set(pads) & {23, 24, 11, 12, 8, 9}, 'DAC pads need HWX_NO_DAC')
                if 'HWX_RX_CLK_GPIO' in d:
                    clk = int(d['HWX_RX_CLK_GPIO'])
                    self.assertIn(clk, XIAO_PADS)
                    self.assertNotIn(clk, pads)
                    self.assertNotIn(clk, RESERVED)
                    self.assertIn('HWX_NO_DAC', d, 'the RX clock pad is a DAC or D10 pad')

    def test_fpga_iq_matches_the_tang_constraints(self):
        d = defines((ROOT / 'hardware_experiments/fpga_iq/hwx.h').read_text())
        self.assertEqual(d['HWX_IQ_GPIO_NUMS'], [1, 0, 25, 7, 10, 5, 3, 4])
        self.assertEqual(set(d), {'HWX_NAME', 'HWX_IQ_GPIO_NUMS', 'HWX_NO_DAC', 'HWX_MENU_ON_IQ'})   # as the J4 table: no extras
        cst = (ROOT / 'hardware_experiments/fpga_iq/tang_primer_25k_dock.cst').read_text()
        balls = dict(re.findall(r'IO_LOC "iq\[(\d)\]" (\w+);', cst))
        self.assertEqual(balls, {'0': 'C11', '1': 'C10', '2': 'B11', '3': 'B10',
                                 '4': 'D11', '5': 'D10', '6': 'G11', '7': 'G10'})

    def test_tang9k_dual_matches_the_tang_nano_9k_constraints(self):
        d = defines((ROOT / 'hardware_experiments/tang9k_dual/hwx.h').read_text())
        self.assertEqual(d['HWX_IQ_GPIO_NUMS'], [1, 0, 25, 7, 23, 24, 12, 8])
        self.assertEqual(int(d['HWX_RX_CLK_GPIO']), 10)
        self.assertNotIn(11, d['HWX_IQ_GPIO_NUMS'])          # LAP87: GPIO11 bit 2 one TX clock late
        self.assertEqual(set(d), {'HWX_NAME', 'HWX_IQ_GPIO_NUMS', 'HWX_RX_CLK_GPIO', 'HWX_NO_DAC',
                                  'HWX_MENU_ON_IQ', 'HWX_FIXED_LANE'})
        cst = (ROOT / 'hardware_experiments/tang9k_dual/fpga/tang_nano_9k.cst').read_text()
        loc = {n: int(p) for n, p in re.findall(r'IO_LOC "([\w\[\]]+)" (\d+);', cst)}
        iotype = dict(re.findall(r'IO_PORT "([\w\[\]]+)" IO_TYPE=(\w+)', cst))
        bank3 = set(range(3, 17)) | set(range(79, 89))           # 1.8 V on the Tang Nano 9K
        lcd = {33, 34, 35, 40, 41, 42, 51, 53, 54, 55, 56, 57, 68, 69, 70, 71, 72, 73, 74, 75}
        c5 = [f'iq_{s}[{k}]' for s in 'ab' for k in range(8)] + ['clk_a', 'clk_b']
        pins = [loc[n] for n in c5]
        self.assertEqual(len(pins), len(set(pins)), 'a Tang pin is used twice')
        self.assertFalse(set(pins) & lcd, 'C5 wiring must not use LCD/HDMI pins')
        for n in c5:
            if n == 'clk_b':
                self.assertIn(loc[n], bank3)                     # through the divider
                self.assertEqual(iotype[n], 'LVCMOS18')
            else:
                self.assertNotIn(loc[n], bank3, n)
                self.assertEqual(iotype[n], 'LVCMOS33', n)
        self.assertEqual(loc['clk_a'], 63)                      # RPLL_T_IN
        dac = [loc[f'dac[{k}]'] for k in range(6)]
        self.assertFalse(set(dac) & set(pins))
        self.assertLessEqual(set(dac), lcd - {68, 69, 70, 71, 72, 73, 74, 75})

    def test_production_never_includes_an_experiment(self):
        rf = (ROOT / 'main/rf.h').read_text()
        self.assertIn('#ifdef C5VRX_HWX\n#include "hwx.h"\n#endif', rf)
        self.assertIn('#define RF_IQ_GPIO_NUMS {1, 0, 25, 2, 27, 5, 3, 4}', rf)
        cmake = (ROOT / 'main/CMakeLists.txt').read_text()
        self.assertIn('if(C5VRX_HWX)', cmake)
        for name in ('transport.c', 'rf.c', 'experimental.c', 'menu.c', 'receiver.c'):
            source = (ROOT / 'main' / name).read_text()
            for hook in re.findall(r'HWX_\w+', source):
                self.assertIn(hook, {'HWX_NO_DAC', 'HWX_MENU_ON_IQ', 'HWX_RX_CLK_GPIO', 'HWX_FIXED_LANE'}, name)


if __name__ == '__main__':
    unittest.main()

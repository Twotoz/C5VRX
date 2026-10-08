#!/usr/bin/env python3
"""LAB pin admission and independent compiled DMA-stream regression."""
import copy
from functools import lru_cache
import json
import random
import unittest
import bs_model as B
from generate_range_options import ROOT, identity, render


class RangeOptionsTest(unittest.TestCase):
    def setUp(self):
        self.model = json.loads((ROOT / 'tools/range32_model.json').read_text())
        self.option = dict(label='RANGE32 CONTROL', model=self.model,
                           sha256=identity(self.model), status='independent_synthetic_confirmation')

    def test_tampering_and_unconfirmed_models_refused(self):
        for mutation in ('lut', 'status', 'schedule'):
            option = copy.deepcopy(self.option)
            if mutation == 'lut':
                option['model']['lut'][0] ^= 1
            elif mutation == 'status':
                option['status'] = 'short_proxy_only'
            else:
                option['model']['params']['token_bits'] = 7
            with self.assertRaises(ValueError):
                render([option])
        with self.assertRaises(ValueError):
            render([self.option, self.option])

    def test_encoder_and_state_mask_survive_ring_wrap(self):
        header, programs = render([self.option])
        self.assertIn('#define C5VRX4_RANGE_OPTION_COUNT 1', header)
        self.check_stream(self.model, programs['c5vrx4_range_option0.bsasm'])
        manifest = ROOT / 'tools/range_options.json'
        if manifest.exists():
            options = json.loads(manifest.read_text())['options']
            _, programs = render(options)
            for index, option in enumerate(options):
                with self.subTest(option=option['label']):
                    self.check_stream(option['model'], programs[f'c5vrx4_range_option{index}.bsasm'])

    def check_stream(self, model, source):
        rng = random.Random(731)
        raw = [rng.randrange(256) for _ in range(70000)]
        lut = model['lut']; p = model['params']; b = p['token_bits']
        state = accumulator = 0; expected = [0, 0]
        counter, context = p.get('counter_phase', False), p.get('context_bits', 0)
        for k in range(0, len(raw), 2):
            address = raw[k] + (0 if counter else 768)
            if context:
                address = raw[k] + (((raw[k + 1] >> 7) & 1) << 8) + (((raw[k + 1] >> 3) & 1) << 9)
            if p.get('pair_layout'):
                # PAIR: kept sign-first bits of both raw samples, I before Q.
                word16 = raw[k] | (raw[k + 1] << 8); address = j = 0
                for keep, base in zip(map(int, p['pair_layout']), (4, 0, 12, 8)):
                    for bit in range(base + 4 - keep, base + 4):
                        address |= ((word16 >> bit) & 1) << j; j += 1
            lookup_state = state
            if counter:
                accumulator = (accumulator + address + (state << 11)) & 65535
                lookup_state = accumulator >> 11
            word = lut[(lookup_state << b) + (lut[address] >> (16 - b))]
            state = (word >> 6) & ((1 << (10 - b)) - 1)
            expected.extend([word & 63] * 2)
        original = B.parse
        B.parse = lru_cache(maxsize=1)(original)
        try:
            stats = {}
            actual = B.simulate(source, raw, len(raw), stats=stats, wrap_rom=True)
        finally:
            B.parse = original
        self.assertEqual([v & 63 for v in actual], expected[:len(raw)])
        self.assertEqual(stats['bundles'], len(raw) - 1)

    def test_empty_manifest_exposes_no_option(self):
        header, programs = render([])
        self.assertIn('#define C5VRX4_RANGE_OPTION_COUNT 0', header)
        self.assertEqual(programs, {})

    def test_range_tradeoff_requires_visible_vetoes(self):
        option = dict(self.option, status='independent_range_tradeoff')
        with self.assertRaises(ValueError):
            render([option])
        option['vetoes'] = ['strong_echo_detail', 'PAL_recovery']
        header, _ = render([option])
        self.assertIn('#define C5VRX4_RANGE_OPTION_COUNT 1', header)


if __name__ == '__main__':
    unittest.main()

"""A range-first objective must still reject erased/inverted or sync-less video."""
import json
from pathlib import Path
import unittest
import numpy as np
import range_objective as J
import search_range as R
import overlay_fsm as O
from validate_range import confirms
from search_compact_range import compact
from search_safe_range import constrain


class ObjectiveTests(unittest.TestCase):
    def test_erased_and_inverted_video_rejected(self):
        c=R.cases(28101)[0]
        for y in (np.zeros(len(c['raw'])), -c['truth']):
            r=J.measure(y,c)
            for profile in J.PROFILES:self.assertTrue(J.strong_failures(r,profile))
            self.assertFalse(J.usable(r))

    def test_pinned_strong_control_preserved(self):
        m=json.loads((Path(__file__).parents[1]/'range32_model.json').read_text())
        for c in R.cases(28102)[:2]:
            r=J.measure(O.decode(c['raw'],m),c)
            for profile in J.PROFILES:self.assertEqual(J.strong_failures(r,profile),[])

    def test_all_generated_schedules_compile(self):
        rng=np.random.default_rng(28103);seen=set()
        for _ in range(100):
            p=R.propose(rng);m=O.synthesize(p);O.compile_model(m)
            seen.add((p.get('context_bits',0),p.get('counter_phase',False)))
        self.assertIn((2,False),seen)
        self.assertIn((0,True),seen)

    def test_luma_gain_cannot_hide_range_or_sync_regression(self):
        control=dict(strong_eligible=True,usable_cnr=10,weak_missing=100,weak_luma_sinad=8)
        self.assertFalse(confirms(dict(control,usable_cnr=12,weak_luma_sinad=20),control))
        self.assertFalse(confirms(dict(control,usable_cnr=8,weak_missing=101),control))
        self.assertFalse(confirms(dict(control,weak_luma_sinad=20),control))
        self.assertTrue(confirms(dict(control,usable_cnr=8),control))

    def test_compact_memory_is_real_frequency_state(self):
        rng=np.random.default_rng(28104)
        for _ in range(40):
            p=compact(R.propose(rng),rng);O.compile_model(O.synthesize(p))
            self.assertGreater((1<<(10-p['token_bits']))//p['phases'],1)
            self.assertFalse(p['counter_phase'])
            self.assertLess(p['low_hz'],p['high_hz'])

    def test_new_search_excludes_failed_coarse_observation_family(self):
        rng=np.random.default_rng(70104)
        for policy in ('plain','context','confidence','compact'):
            for _ in range(40):
                p=constrain(R.propose(rng),rng,policy)
                O.compile_model(O.synthesize(p))
                self.assertGreaterEqual(p['phases'],16)
                self.assertGreaterEqual((1<<p['token_bits'])//p['confidence_groups'],16)
                if policy=='compact':self.assertGreater((1<<(10-p['token_bits']))//p['phases'],1)

    def test_failed_board_model_cannot_pass_new_strong_picture_guard(self):
        options=json.loads((Path(__file__).parents[1]/'range_options.json').read_text())['options']
        failed=next(r['model'] for r in options if r['sha256'].startswith('f96c6225fc10'))
        pinned=json.loads((Path(__file__).parents[1]/'range32_model.json').read_text())
        for c in R.cases(70105,lane_model='fine')[:2]:
            reference=J.measure(O.decode(c['raw'],pinned),c,True)
            self.assertEqual(J.strong_failures(reference,'safe_range',reference),[])
            self.assertTrue(J.strong_failures(J.measure(O.decode(c['raw'],failed),c,True),'safe_range',reference))


if __name__=='__main__':unittest.main()

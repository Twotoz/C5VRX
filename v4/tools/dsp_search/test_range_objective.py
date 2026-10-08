"""A range-first objective must still reject erased/inverted or sync-less video."""
import json
from pathlib import Path
import unittest
import numpy as np
import range_objective as J
import search_range as R
import overlay_fsm as O
from validate_range import confirms


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


if __name__=='__main__':unittest.main()

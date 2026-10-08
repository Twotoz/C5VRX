"""An identical baseline cannot earn a new-model confirmation label."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import validate_range as V


class ValidationTests(unittest.TestCase):
    def test_baseline_winner_stops_without_runner_up_or_duplicate_final(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);search=root/'search';search.mkdir()
            model=copy.deepcopy(V.PINNED);key=V.digest(model);name='RNG-'+key[:12]
            (search/'frozen.json').write_text(json.dumps(dict(finalists=[dict(id=key,model=model)])))
            (search/'protocol.json').write_text(json.dumps(dict(profile='safe_range',
                selection_seed=70301,final_seeds=[70401,70402],stress_seed=70501)))
            metric=dict(strong_eligible=True,usable_cnr=10,weak_missing=10,weak_luma_sinad=20)
            def selection_only(*args,**kwargs):
                self.assertIn(args[4],('selection','selection_envelope'))
                return [dict(stage=args[4])]
            with patch.object(sys,'argv',['validate','--search',str(search),'--output',str(root/'out')]), \
                 patch.object(V,'evaluate',side_effect=selection_only), \
                 patch.object(V,'aggregate',return_value={name:metric,'RANGE32':metric}):V.main()
            result=json.loads((root/'out/summary.json').read_text())
            self.assertEqual(result['selected'],name)
            self.assertFalse(result['confirmed_range_improvement'])
            self.assertFalse(result['independent_final_executed'])
            self.assertIsNone(result['final'])
            self.assertFalse((root/'out/confirmed_model.json').exists())


if __name__=='__main__':unittest.main()

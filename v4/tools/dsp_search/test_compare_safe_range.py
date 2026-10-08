"""Only individually confirmed, identity-checked options enter common testing."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import compare_safe_range as C


class ComparisonTests(unittest.TestCase):
    def test_failed_options_never_enter_shared_comparison(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);run=root/'run';run.mkdir()
            (run/'summary.json').write_text(json.dumps(dict(profile='safe_range',
                selected='failed',confirmed_range_improvement=False)))
            with patch.object(sys,'argv',['compare','--confirmed',str(run),'--output',str(root/'shared')]):C.main()
            self.assertEqual(json.loads((root/'shared/frozen.json').read_text())['finalists'],[])

    def test_verified_options_deduplicated_and_bad_identity_refused(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);run=root/'run';run.mkdir()
            model=json.loads((Path(__file__).parents[1]/'range32_model.json').read_text())
            digest=C.F.digest(model);model['name']='RNG-'+digest[:12]
            (run/'summary.json').write_text(json.dumps(dict(profile='safe_range',
                selected=model['name'],confirmed_range_improvement=True)))
            (run/'confirmed_model.json').write_text(json.dumps(model))
            with patch.object(sys,'argv',['compare','--confirmed',str(run),str(run),'--output',str(root/'shared')]):C.main()
            p=json.loads((root/'shared/protocol.json').read_text())
            self.assertEqual(p['unique_finalists'],1)
            self.assertEqual(p['final_seeds'],[200401,200402])
            model['lut'][0]^=1;(run/'confirmed_model.json').write_text(json.dumps(model))
            with patch.object(sys,'argv',['compare','--confirmed',str(run),'--output',str(root/'bad')]):
                with self.assertRaises(ValueError):C.main()
            self.assertFalse((root/'bad').exists())


if __name__=='__main__':unittest.main()

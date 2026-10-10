"""Physical replay refuses corrupted, incompatible and unlabelled captures."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from capture_iq_snapshot import fnv1a
from replay_iq_sequence import load_capture


class ReplayTest(unittest.TestCase):
    def test_checksums_and_source_format_are_enforced(self):
        raw = np.random.default_rng(2300206).integers(0, 256, 4096, dtype=np.uint8).tobytes()
        meta = dict(bytes=str(len(raw)), rate_hz='40000000', format='Ihigh_Qlow',
                    lane='2', fnv1a=f'{fnv1a(raw):08x}')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'snapshot.iq'
            path.write_bytes(raw)
            path.with_suffix('.json').write_text(json.dumps(meta))
            actual, recorded, lane = load_capture(path)
            self.assertEqual(actual.tobytes(), raw)
            self.assertEqual(recorded, meta)
            self.assertEqual(lane, 'ultrafine')
            for changes in ({'rate_hz': '80000000'}, {'format': 'Qhigh_Ilow'},
                            {'lane': 'unknown'}, {'bytes': '4094'}, {'fnv1a': '00000000'}):
                path.with_suffix('.json').write_text(json.dumps(meta | changes))
                with self.assertRaises(ValueError):
                    load_capture(path)
            path.with_suffix('.json').write_text(json.dumps(meta))
            path.write_bytes(bytes([raw[0] ^ 1]) + raw[1:])
            with self.assertRaises(ValueError):
                load_capture(path)


if __name__ == '__main__':
    unittest.main()

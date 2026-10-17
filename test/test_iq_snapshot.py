"""C5VRX by Twotoz and contributors: complete serial IQ record integrity."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    'capture', Path(__file__).resolve().parents[1] / 'tools/capture_iq_snapshot.py')
capture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capture)


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.raw = bytes((k * 17 + 3) % 256 for k in range(8190))
        checksum = capture.fnv1a(self.raw)
        self.lines = [f'IQSNAP_BEGIN bytes=8190 rate_hz=40000000 format=Ihigh_Qlow '
                      f'gain=83 lane=2 freq_mhz=5865 fnv1a={checksum:08x}']
        self.lines += [f'IQSNAP_DATA offset={at} {self.raw[at:at+256].hex()}'
                       for at in range(0, len(self.raw), 256)]
        self.lines += [f'IQSNAP_END fnv1a={checksum:08x}']

    def test_joined_status_prefix_preserves_complete_payload(self):
        joined = ['STATUS channel=A1 detected=' + line for line in self.lines]
        self.assertEqual(capture.decode(joined)[0], self.raw)

    def test_real_board_record_matches_saved_binary(self):
        root = Path(__file__).resolve().parents[1] / 'research/data/native_refusal_2026-10-13'
        raw, metadata = capture.decode((root / 'iq40-serial.log').read_text().splitlines())
        self.assertEqual(raw, (root / 'iq40.bin').read_bytes())
        self.assertEqual(metadata['fnv1a'], '19a9d0a1')

    def test_missing_or_reordered_chunk_refused(self):
        for lines in (self.lines[:2] + self.lines[3:],
                      self.lines[:1] + [self.lines[2], self.lines[1]] + self.lines[3:]):
            with self.assertRaises(ValueError):
                capture.decode(lines)

    def test_corrupt_payload_or_checksum_refused(self):
        bad = list(self.lines)
        bad[1] = bad[1][:-2] + ('00' if bad[1][-2:] != '00' else 'ff')
        with self.assertRaises(ValueError):
            capture.decode(bad)
        bad = self.lines[:-1] + ['IQSNAP_END fnv1a=00000000']
        with self.assertRaises(ValueError):
            capture.decode(bad)

    def test_truncated_record_refused(self):
        with self.assertRaises(ValueError):
            capture.decode(self.lines[:-1])


if __name__ == '__main__':
    unittest.main()

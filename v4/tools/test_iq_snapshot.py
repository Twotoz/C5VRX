"""Reject corrupted, incomplete, interleaved and noncontiguous IQ transfers."""
import unittest
from capture_iq_snapshot import decode,fnv1a


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.raw=bytes((k*7)&255 for k in range(8190));checksum=f'{fnv1a(self.raw):08x}'
        self.lines=[f'IQSNAP_BEGIN bytes=8190 rate_hz=40000000 format=Ihigh_Qlow gain=40 lane=1 freq_mhz=5800 fnv1a={checksum}']
        for offset in range(0,len(self.raw),256):
            self.lines += ['HB unrelated status',f'IQSNAP_DATA offset={offset} {self.raw[offset:offset+256].hex()}']
        self.lines += [f'IQSNAP_END fnv1a={checksum}']

    def test_complete_and_reference_checksum(self):
        self.assertEqual(fnv1a(b'hello'),0x4f9f2cab)
        raw,meta=decode(self.lines)
        self.assertEqual(raw,self.raw);self.assertEqual(meta['format'],'Ihigh_Qlow')

    def test_missing_reordered_or_corrupt(self):
        variants=[self.lines[:-1],self.lines[1:],self.lines[:2]+self.lines[3:],
                  [s.replace('offset=256 ','offset=257 ') for s in self.lines],
                  [s.replace('00070e15','01070e15') for s in self.lines],
                  [s.replace('Ihigh_Qlow','Ilow_Qhigh') for s in self.lines],
                  [s.replace('bytes=8190','bytes=8192') for s in self.lines],
                  self.lines[:1]+self.lines]
        for lines in variants:
            with self.subTest(lines=len(lines)),self.assertRaises(ValueError):decode(lines)

    def test_refusal(self):
        with self.assertRaisesRegex(ValueError,'settling'):decode(['IQSNAP_REFUSED stale_or_settling'])
        with self.assertRaisesRegex(ValueError,'copied=0'):
            decode(['IQSNAP_REFUSED stale_or_settling source_ready=1 copied=0 epoch_ok=1'])


if __name__=='__main__':unittest.main()

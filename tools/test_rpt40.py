import random
import unittest
from pathlib import Path
from rpt40 import Tracker, packed_state, rx_source, tx_source
from bs_model import simulate
from validate_phase5_quality import phase5_state, build_lut


class RPT40Tests(unittest.TestCase):
    def test_full_mapping(self):
        for raw in range(256):
            self.assertEqual(packed_state(raw)&31,phase5_state(raw))
        self.assertEqual(packed_state(0)>>5,0)
        self.assertGreater(packed_state(0x77)>>5,0)
        raw=list(range(256))*4
        self.assertEqual(simulate(rx_source(),raw+raw,1024),list(map(packed_state,raw)))

    def test_golden_parity_and_hardware_kernel(self):
        rng=random.Random(17)
        raw=[rng.randrange(256) for _ in range(10004)]
        got=Tracker().feed(raw)
        lut=build_lut()
        for parity in (0,1):
            prev=0; expected=[]
            for b in raw[parity::2]:
                phase=phase5_state(b)
                expected.append(lut[(prev<<5)|phase]&63)
                prev=phase
            self.assertEqual(got[parity::2],expected)
        states=list(map(packed_state,raw))
        self.assertEqual(simulate(tx_source(),states,len(raw)-2),got[2:])

    def test_continuity_and_bounded_recovery(self):
        raw=[0x77,0x76,0x67,0x66]*4096
        raw[4096]=0
        tracker=Tracker(suppress=True)
        chunked=[]
        for start in range(0,len(raw),4093):
            chunked+=tracker.feed(raw[start:start+4093])
        self.assertEqual(chunked,Tracker(suppress=True).feed(raw))
        self.assertEqual(chunked[4096],chunked[4094])
        self.assertEqual(chunked[4098],chunked[4096])
        self.assertEqual(chunked[4100],Tracker().feed(raw)[4100])

    def test_generated_sources(self):
        root=Path(__file__).resolve().parents[1]/'main'
        self.assertEqual((root/'rpt40_rx.bsasm').read_text(),rx_source())
        self.assertEqual((root/'rpt40_tx.bsasm').read_text(),tx_source())


if __name__=='__main__': unittest.main()

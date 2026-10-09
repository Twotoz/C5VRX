"""Meaningful quantizer, causal-prefix and independent C5 dataflow proofs."""
import json
from pathlib import Path
import unittest
import numpy as np
import omega_bayes as B
import omega_student as S
import overlay_fsm as O
import compile_overlay as C
import bs_model as BS
from iq_lanes import quantize


class OmegaTest(unittest.TestCase):
    def test_folded_gaussian_likelihood_matches_actual_quantizer(self):
        rng=np.random.default_rng(190)
        for lane in ('coarse','fine','ultrafine'):
            for mean in (-10.2,-.3,9.7):
                p=B.component_likelihood(np.array([mean]),.7,lane)[0]
                raw=quantize(rng.normal(mean,.7,200000)+0j,lane)>>4
                measured=np.bincount(raw,minlength=16)/len(raw)
                self.assertLess(float(np.max(abs(p-measured))),.006)
                self.assertAlmostEqual(float(p.sum()),1.,places=12)
    def test_teacher_prefix_is_causal_and_probability_valid(self):
        raw=np.random.default_rng(19).integers(0,256,256,dtype=np.uint8)
        cfg=B.setup(phases=16,frequencies=16)
        h,x=B.filter(raw,cfg);hh,xx=B.filter(raw[:128],cfg)
        np.testing.assert_array_equal(h[:64],hh)
        np.testing.assert_array_equal(x[:64],xx)
        h40,x40=B.filter(raw,cfg,stride=1)
        np.testing.assert_array_equal(h,h40[1::2])
        np.testing.assert_array_equal(x,x40[1::2])
        self.assertTrue(np.isfinite(x).all())
        np.testing.assert_allclose(x[:,4:7].sum(1),1.,atol=1e-12)
        np.testing.assert_allclose(x[:,7:11].sum(1),1.,atol=1e-12)
    def test_all_allocations_have_bit_exact_state_dac_and_encoder(self):
        rng=np.random.default_rng(19190);raw=rng.integers(0,256,70000,dtype=np.uint8)
        for bits in (2,3,4):
            T=1<<bits;states=1024//T
            for layout in ('4411','3322','2233'):
                dac=rng.integers(0,64,1024,dtype=np.uint16)
                nxt=rng.integers(0,states,1024,dtype=np.uint16)
                enc=rng.integers(0,T,1024,dtype=np.uint16)
                lut=dac|(nxt<<6)|(enc<<(16-bits))
                m=dict(params=dict(token_bits=bits,pair_layout=layout),lut=lut.astype(int).tolist())
                addr=S.addresses(raw,layout);old=S.trace(addr,enc,nxt.reshape(states,T))
                expected=np.zeros(len(raw),np.uint8)
                expected[2:]=np.repeat(dac[old*T+enc[addr]],2)[:len(raw)-2]
                np.testing.assert_array_equal(O.model_codes(raw,m),expected)
                # Distinct source interpreter verifies eight-ROM wrap and
                # actual full 8-bit emitted bytes, including padding fields.
                src=C.build(m);actual=BS.simulate(src,raw,len(raw),wrap_rom=True)
                np.testing.assert_array_equal(np.asarray(actual)&63,expected)
                # Check every word's three fields, not just reachable words.
                np.testing.assert_array_equal((lut>>6)&(states-1),nxt)
                np.testing.assert_array_equal(lut>>(16-bits),enc)
    def test_direct40_alternative_matches_source_and_cadence(self):
        import omega_direct40 as D
        rng=np.random.default_rng(40);raw=rng.integers(0,256,70000,dtype=np.uint8)
        lut=rng.integers(0,4096,1024,dtype=np.int64)
        m=dict(bits=(6,7,2,3),lut=lut.tolist())
        stats={};actual=BS.simulate(D.source(m),raw,len(raw),wrap_rom=True,stats=stats)
        np.testing.assert_array_equal(np.array(actual)&63,D.codes(raw,lut,np.array(m['bits'])))
        self.assertEqual(stats['bundles'],len(raw))
    def test_pinned_model_matches_compiled_source(self):
        path=Path(__file__).resolve().parents[1]/'omega_model.json'
        if not path.exists():self.skipTest('research not pinned yet')
        m=json.loads(path.read_text());raw=np.random.default_rng(91).integers(0,256,70000,dtype=np.uint8)
        source=Path(__file__).resolve().parents[2]/'firmware/programs/c5vrx4_omega.bsasm'
        self.assertEqual(source.read_text(),C.build(m))
        from pair_autofit import remap
        for deviation,centre in ((.6,.5e6),(1.4,1.5e6)):
            mm=remap(m,deviation,centre)
            np.testing.assert_array_equal(np.array(mm['lut'])&0xffc0,np.array(m['lut'])&0xffc0)
        np.testing.assert_array_equal(np.array(BS.simulate(source.read_text(),raw,len(raw),wrap_rom=True))&63,O.model_codes(raw,m))

if __name__=='__main__':unittest.main()

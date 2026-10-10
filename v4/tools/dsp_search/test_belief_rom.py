"""Independent invariants for the Bayesian ROM generator."""
import unittest
import numpy as np
import belief_rom as B
import compile_overlay as C
import overlay_fsm as O
import bs_model as BS


class BeliefROM(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg=B.setup(phases=16,frequencies=9)

    def test_likelihood_partition(self):
        e=self.cfg['emission']
        self.assertTrue(np.all(e>=0))
        np.testing.assert_allclose(e.sum(1),1,atol=2e-6)
        enc=np.arange(1024)%8
        aggregate=np.stack([e[:,enc==t].sum(1) for t in range(8)],1)
        np.testing.assert_allclose(aggregate.sum(1),1,atol=2e-6)

    def test_prediction_is_markov_and_migrates_parameters(self):
        cfg=self.cfg;G=len(cfg['emission'])
        b=np.zeros((1,G),np.float32);b[0,0]=1
        p=B.prediction(b,cfg)
        self.assertTrue(np.all(p>=0))
        np.testing.assert_allclose(p.sum(1),1,atol=2e-6)
        self.assertGreater(p.reshape(12,9,16)[1:].sum(),0)

    def test_reference_prefix_causality(self):
        raw=np.random.default_rng(33).integers(0,256,512,dtype=np.uint8)
        y,_=B.reference(raw,self.cfg,stride=16)
        yy,_=B.reference(raw[:256],self.cfg,stride=16)
        np.testing.assert_array_equal(y[:128],yy)

    def test_projection_and_shared_word_execution(self):
        cfg=self.cfg;G=len(cfg['emission']);rng=np.random.default_rng(39)
        b=rng.dirichlet(np.full(G,.05),128).astype(np.float32)
        enc=np.arange(1024)%8
        m,info=B.project(cfg,b,enc,3)
        self.assertEqual(info['posterior'].shape,(1024,G))
        np.testing.assert_allclose(info['posterior'].sum(1),1,atol=2e-6)
        raw=rng.integers(0,256,10000,dtype=np.uint8)
        np.testing.assert_array_equal(np.asarray(BS.simulate(C.build(m),raw,len(raw),wrap_rom=True))&63,O.model_codes(raw,m))

    def test_acquisition_posterior_cannot_project_back_to_diffuse_prior(self):
        cfg=self.cfg;enc=B.encoder(cfg,3,66)
        raw=np.random.default_rng(40).integers(0,256,8192,dtype=np.uint8)
        _,snap=B.reference(raw,cfg,stride=32)
        book=B.acquisition_codebook(snap,cfg,enc,3,77)
        m,_=B.project(cfg,book,enc,3)
        nxt=(np.asarray(m['lut'][:8])>>6)&127
        self.assertTrue(np.all(nxt!=0),nxt)

    def test_state_context_source_for_every_legal_width(self):
        raw=np.random.default_rng(53).integers(0,256,10000,dtype=np.uint8)
        for bits in (2,3,4):
            m=dict(params=dict(token_bits=bits,context_bits=2,context_state_bits=True),
                   lut=np.random.default_rng(bits).integers(0,65536,1024).tolist())
            np.testing.assert_array_equal(np.asarray(BS.simulate(C.build(m),raw,len(raw),wrap_rom=True))&63,O.model_codes(raw,m))

    def test_policy_rollout_matches_compiler_addresses_and_is_causal(self):
        from refine_belief_rom import rollout
        from omega_student import addresses
        raw=np.random.default_rng(63).integers(0,256,10000,dtype=np.uint8)
        for context in (False,True):
            for bits in (3,4):
                p=dict(token_bits=bits,context_bits=2,context_state_bits=True) if context else dict(token_bits=bits,pair_layout='4411')
                m=dict(params=p,lut=np.random.default_rng(bits+context).integers(0,65536,1024).tolist())
                lut=np.asarray(m['lut']);enc=lut>>(16-bits);tr=((lut>>6)&((1<<(10-bits))-1)).reshape(-1,1<<bits)
                old,a=rollout(raw,enc,tr,bits,context)
                idx=old*(1<<bits)+enc[a]
                np.testing.assert_array_equal(idx[:-1],O.model_indices(raw,m))
                old2,a2=rollout(raw[:5000],enc,tr,bits,context)
                np.testing.assert_array_equal(old[:2500],old2)
                np.testing.assert_array_equal(a[:2500],a2)
                if not context:np.testing.assert_array_equal(a,addresses(raw,'4411'))

    def test_particle_marginal_preserves_causality_mass_and_frequency(self):
        import adaptive_particle as AP
        cfg=AP.setup(particles=256,acceleration=False,innovation=.25,jump=.08)
        raw=np.random.default_rng(69).integers(0,256,1024,dtype=np.uint8)
        hz,x,p=AP.posterior_snapshots(raw,cfg,stride=4)
        hy,xy=AP.filter(raw,cfg)
        np.testing.assert_array_equal(hz,hy);np.testing.assert_array_equal(x[:,:18],xy)
        hh,xx,pp=AP.posterior_snapshots(raw[:512],cfg,stride=4)
        np.testing.assert_array_equal(p[:64],pp)
        np.testing.assert_array_equal(hz[:256],hh)
        self.assertTrue(np.all(p>=0))
        np.testing.assert_allclose(p.sum(1),1,atol=3e-6)
        grid=np.repeat(np.linspace(-10.,10.,65),64)
        np.testing.assert_allclose(p@grid*1e6,hz[3::4],atol=1,rtol=1e-6)
        aa,_,ap=AP.posterior_snapshots(raw,cfg,stride=1,endpoint='A')
        changed=raw.copy();changed[1]^=255
        ab,_,bp=AP.posterior_snapshots(changed,cfg,stride=1,endpoint='A')
        self.assertEqual(aa[0],ab[0])
        np.testing.assert_array_equal(ap[0],bp[0])

    def test_adaptive_geometry_spends_frequency_memory_on_uncertain_phase(self):
        from refine_belief_rom import adaptive_features
        cfg=dict(phases=16,freq=np.arange(4),regimes=[0])
        b=np.zeros((4,64));b[0,0]=1;b[1,48]=1
        b[2,:16]=1/16;b[3,48:]=1/16
        x=adaptive_features(b,cfg)
        # Identical sharp phase with different f maps to one memory feature;
        # output regression remains a separate current-DAC decision.
        np.testing.assert_allclose(x[0],x[1],atol=1e-7)
        self.assertGreater(np.linalg.norm(x[2]-x[3]),1)

    def test_history_register_matches_explicit_64_bit_shift_register(self):
        raw=np.arange(256,dtype=np.uint8);pattern=(8,16,32,8)
        for prefetch in (False,True):
            source='cfg prefetch '+str(prefetch).lower()+'\ncfg lut_width_bits 16\nlut 0\n'
            for k,read in enumerate(pattern):source+=f'k{k}:\n set 0..15 32..47,\n read {read},\n write 16,\n nop\n'
            register=int.from_bytes(raw[:8].tobytes(),'little') if prefetch else 0
            cursor=8 if prefetch else 0;expected=[]
            for k in range(64):
                expected.extend([(register>>32)&255,(register>>40)&255])
                read=pattern[k%4];n=read//8
                new=int.from_bytes(raw[cursor:cursor+n].tobytes(),'little')
                register=(register>>read)|(new<<(64-read));cursor+=n
            np.testing.assert_array_equal(BS.simulate(source,raw,128,wrap_rom=True),expected)

    def test_history_source_all_widths_and_warmup_addresses(self):
        from refine_belief_rom import rollout
        raw=np.random.default_rng(191).integers(0,256,70000,dtype=np.uint8)
        for bits in (2,3,4,5,6):
            m=dict(params=dict(token_bits=bits,history_observation=True),lut=np.random.default_rng(bits).integers(0,65536,1024).tolist())
            np.testing.assert_array_equal(np.asarray(BS.simulate(C.build(m),raw,len(raw),wrap_rom=True))&63,O.model_codes(raw,m))
            lut=np.asarray(m['lut']);enc=lut>>(16-bits);tr=((lut>>6)&((1<<(10-bits))-1)).reshape(-1,1<<bits)
            old,a=rollout(raw,enc,tr,bits,False,True)
            # Controller spans0/1 encode zero, then rawA0. A finite output
            # trace ends three spans before the corresponding training trace.
            np.testing.assert_array_equal((old*(1<<bits)+enc[a])[:-3],O.model_indices(raw,m)[2:])

    def test_empty_state_reseeding_reduces_projection_distortion(self):
        from refine_belief_rom import reseed_unused
        rng=np.random.default_rng(93);pool=rng.dirichlet(np.full(16,.1),40).astype(np.float32)
        book=np.full((8,16),1/16,np.float32);book[1:3]=pool[:2]
        occupied=np.zeros(8,bool);occupied[1:3]=True
        before=(np.sqrt(pool)@np.sqrt(book[occupied]).T).max(1)
        after_book=reseed_unused(book.copy(),occupied,pool)
        after=(np.sqrt(pool)@np.sqrt(after_book).T).max(1)
        self.assertTrue(np.all(after>=before-1e-6))
        np.testing.assert_array_equal(after_book[0],book[0])

if __name__=='__main__':unittest.main()

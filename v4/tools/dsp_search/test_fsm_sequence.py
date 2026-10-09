"""Local adjoint and hard-state proofs for the explicitly surrogate optimizer."""
import unittest
import numpy as np
from fsm_sequence_train import softmax,gradient,rollout

class SequenceTest(unittest.TestCase):
 def test_hard_rollout_keeps_exactly_one_state_and_is_causal(self):
  enc=np.array([0,1,0,1]);tr=np.array([[1,2],[0,2],[1,0]])
  a=np.array([0,1,2,3,1]);old,last=rollout(a,enc,tr,0)
  np.testing.assert_array_equal(old,[0,1,2,1,2]);self.assertEqual(last,0)
  prefix,_=rollout(a[:3],enc,tr,0);np.testing.assert_array_equal(prefix,old[:3])
 def test_legacy_distillation_geometry_remains_identical(self):
  import omega_student as S
  rng=np.random.default_rng(783);seq=[dict(raw=rng.integers(0,256,1024,dtype=np.uint8),features=rng.normal(size=(512,14)),hz=rng.uniform(-3e6,4e6,512))]
  m,_=S.fit(seq,bits=4,seed=42,rounds=1)
  mm,_=S.fit(seq,bits=4,seed=42,rounds=1,feature_scale=[1,1,2,2,.5,.5,.5,.5,.5,.5,.5,2,1,1],feature_prior=[0,0,0,0,1/3,1/3,1/3,.25,.25,.25,.25,1/12,.5,.3])
  self.assertEqual(m['lut'],mm['lut'])
 def test_local_softmax_adjoints_by_finite_differences(self):
  rng=np.random.default_rng(482);el=rng.normal(size=(4,2));tl=rng.normal(size=(3,2,3));v=rng.uniform(size=(3,2))
  ep=softmax(el,1);tp=softmax(tl,1);enc=el.argmax(1);tr=tl.argmax(2)
  a=np.array([0,1]);states,_=rollout(a,enc,tr,0);y=np.array([.2,.7]);w=np.ones(2)
  loss,ge,gt,gc=gradient(a,y,states,enc,tr,v,ep,tp,w)
  dy0=v[states[0],enc[0]]-y[0];dy1=v[states[1],enc[1]]-y[1]
  future=dy1*v[:,enc[1]];s=states[0];t=enc[0]
  eh=np.eye(2)[t]
  def proxy(e,z):
   p=softmax(e,1);q=softmax(z,1);ee=eh+p[0]-ep[0]
   return dy0*ee@v[s]+future@(ee@q[s]-eh@tp[s])
  eps=1e-5
  for k in range(2):
   plus=el.copy();minus=el.copy();plus[0,k]+=eps;minus[0,k]-=eps
   self.assertAlmostEqual((proxy(plus,tl)-proxy(minus,tl))/(2*eps),ge[0,k],places=7)
  for u in range(3):
   plus=tl.copy();minus=tl.copy();plus[s,t,u]+=eps;minus[s,t,u]-=eps
   self.assertAlmostEqual((proxy(el,plus)-proxy(el,minus))/(2*eps),gt[s,t,u],places=7)
if __name__=='__main__':unittest.main()

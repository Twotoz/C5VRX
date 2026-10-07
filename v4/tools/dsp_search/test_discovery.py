"""Independent mathematical, timing and failure regressions for C5VRX."""
import copy
import json
from pathlib import Path
import numpy as np
import engine as S
import hardware as H
import expressions as E
import waveforms as V


def main():
    rng=np.random.default_rng(9701);templates=S.pool()
    raw=rng.integers(0,256,4096,dtype=np.uint8)
    for template in templates:
        expr=dict(op='phase',unit='rad/span')
        model,_,_=S.synthesize(template,expr,'code')
        source=H.compile_model(model)
        actual=H.BS.simulate(source,raw,len(raw),wrap_rom=True)
        assert np.array_equal(np.array(actual)&63,S.codes(raw,model))
    bad=copy.deepcopy(templates[0]);bad['history_samples']=9
    try:H.cost(bad)
    except ValueError:pass
    else:raise AssertionError('unsupported history passed')
    try:E.validate(dict(op='phase',unit='Hz'))
    except ValueError:pass
    else:raise AssertionError('units not checked')
    for hz in (100000,50000,25000):assert not H.pll_resolution(hz)['fits']
    for standard,n in [('PAL',1600000),('NTSC',1334668)]:
        ire,region=V.raster(standard)
        assert len(ire)==n and np.any(region==4) and np.any(region==5)
        starts,width=V.pulses(ire)
        assert np.sum(width>600)==(10 if standard=='PAL' else 12)
        assert np.sum((width>140)&(width<320))>400
        # Inverted sync, removed vertical sync and level drift must never
        # become a good candidate through an affine/noisy calibration fit.
        c=dict(calibration=(0,1,0),truth=ire,region=region)
        ref=V.waveform_metrics(ire,c)
        assert not V.gate(ref,ref)
        inverted=V.waveform_metrics(-ire,c)
        assert 'polarity' in V.gate(inverted,ref)
        lost=ire.copy();lost[region==2]=0
        assert V.gate(V.waveform_metrics(lost,c),ref)
    points=[dict(weak_sinad=2,strong_loss=.2,sync_penalty=0),
            dict(weak_sinad=1,strong_loss=.3,sync_penalty=1),
            dict(weak_sinad=1,strong_loss=0,sync_penalty=0)]
    assert len(S.pareto(points))==2
    # Specific root cause: old PLL encoder reflects phase because I/Q swapped.
    import generate_pll96 as G
    p=json.loads((G.ROOT/'tools/pll96_model.json').read_text())['params']
    _,correct=G.tables(p)
    _,old,_,_=H.BS.parse((G.ROOT/'tools/fixtures/pll96-rejected-84aadd1.bsasm').read_text())
    angles=np.arange(256)*2*np.pi/256
    raw=H.B.D.raw_bytes(4*np.exp(1j*angles)).astype(np.uint8)
    restored=np.unwrap(np.array(correct)[raw]*2*np.pi/8)
    rejected=np.unwrap(np.array(old[768:])[raw]*2*np.pi/8)
    assert np.corrcoef(angles,restored)[0,1]>.99
    assert np.corrcoef(angles,rejected)[0,1]<-.99
    print('PASS all compiled pair/context/state4 schedules, units/resources, full interlaced timing, polarity/sync rejection, Pareto dominance and PLL I/Q regression')


if __name__=='__main__':main()

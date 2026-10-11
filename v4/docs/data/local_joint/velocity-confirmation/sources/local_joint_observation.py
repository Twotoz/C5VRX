"""Local-only C5VRX/Twotoz/contributor joint-observation research.

Adapted from untrained_search.py and compile_overlay.py at 7491a660.
One shared LUT16, 32 states/tokens, RX40/TX40 duplicate DAC6. No live change.
Formula construction only: no fitted entries or supplied runtime C/N.
"""
import copy
import json
from pathlib import Path
import numpy as np
import compile_overlay as C
import overlay_fsm as O

ROOT = Path(__file__).resolve().parents[2]


def baseline():
    opts = json.loads((ROOT / 'tools/range_options.json').read_text())['options']
    return copy.deepcopy(next(o['model'] for o in opts if o['label'] == 'U85Q UNTRAINED'))


def wrap(x):
    return (x + np.pi) % (2 * np.pi) - np.pi


def build(kind, strength=0.5, rotation=0.0):
    """Joint signs or interval innovations; keep the existing transport.

    conditional: B affects only the four origin A cells, by rotating B
      backwards with a *declared fixed prior*, not actual per-case frequency.
    confidence: 24 ordinary angles + quadrant x two agreement classes.
      Agreement is a proxy, not a measured C/N or perfect erasure detector.
    interval: project prior phase onto the observed quadrant before applying
      a correction. This generalizes earlier HC nearest-quadrant geometry;
      it is not a new independent discovery of HC.
    unrounded: diagnostic output-ablation, explicitly not noise shaping.
    nonlinear: origin gain depends on innovation, no additional state bits.
    """
    m = baseline()
    za, zb = O.pair_cells('4411')
    aa = np.angle(za)
    bb = wrap(np.angle(zb) - rotation)
    org = np.abs(za) < .8
    q = np.floor((aa + np.pi) * 4 / (2 * np.pi)).astype(int) % 4
    ta = 24 if kind == 'confidence' else 28
    tok = np.floor((aa + np.pi) * ta / (2 * np.pi)).astype(int) % ta
    if kind == 'conditional':
        joint = np.angle(np.exp(1j * aa) + strength * np.exp(1j * bb))
        q = np.floor((joint + np.pi) * 4 / (2 * np.pi)).astype(int) % 4
        tok[org] = ta + q[org]
    elif kind == 'confidence':
        agree = np.abs(wrap(bb - aa)) <= np.pi / 2
        tok[org] = ta + 2 * q[org] + agree[org].astype(int)
    else:
        tok[org] = ta + q[org]
    ph = (np.arange(32) + .5) * 2 * np.pi / 32 - np.pi
    lut = np.zeros(1024, np.int64)
    for s in range(32):
        for t in range(32):
            coarse = t >= ta
            if coarse:
                qt = (t - ta) // 2 if kind == 'confidence' else t - ta
                obs = (qt + .5) * np.pi / 2 - np.pi
                gain = (.5 if (t-ta) % 2 else strength) if kind == 'confidence' else .5
            else:
                obs = (t + .5) * 2 * np.pi / ta - np.pi
                gain = .85
            e = wrap(obs - ph[s])
            if kind == 'interval' and coarse:
                e = np.sign(e) * max(0., abs(e) - strength * np.pi / 4)
            if kind == 'nonlinear' and coarse:
                gain = .5 / (1 + strength * (abs(e) / np.pi)**2)
            target = ph[s] + gain * e
            nxt = int(np.floor((target + np.pi) * 32 / (2*np.pi))) % 32
            delta = gain * e if kind == 'unrounded' else wrap(ph[nxt] - ph[s])
            hz = delta * 20e6 / (2*np.pi)
            code = int(np.rint(np.clip((hz + 6e6) / 15e6 * 63, 0, 63)))
            lut[s*32+t] = code | (nxt << 6)
    lut |= tok << 11
    m.update(name=f'LOCAL-{kind}-{strength:g}-rot{rotation:g}', lut=lut.tolist())
    m.pop('formula', None)
    m['research'] = dict(kind=kind, strength=strength, rotation=rotation,
                         fixed_frequency_prior_hz=rotation*40e6/(2*np.pi),
                         physical_acceptance=False)
    C.build(m)
    return m


def candidates():
    return [baseline()] + [build(k, a, r) for k, a, r in [
        ('conditional', .5, 0.), ('conditional', 1., 0.),
        ('conditional', .5, 2*np.pi*1.5e6/40e6),
        ('confidence', .25, 0.), ('confidence', .1, 0.),
        ('confidence', .25, 2*np.pi*1.5e6/40e6),
        ('interval', .5, 0.), ('interval', 1., 0.),
        ('nonlinear', 1., 0.), ('nonlinear', 3., 0.),
        ('unrounded', .5, 0.)]]


def source_checks():
    """Independent assembly emulation, including all raw encoder addresses."""
    import bs_model as BS
    from functools import lru_cache
    old = BS.parse
    BS.parse = lru_cache(maxsize=16)(old)
    try:
        raw = np.random.default_rng(6110190).integers(0, 256, 8192, dtype=np.uint8)
        for m in candidates():
            source = C.build(m)
            cfg, lut, blocks, _ = BS.parse(source)
            assert len(lut) == 1024 and len(blocks) == 8
            actual = np.asarray(BS.simulate(source, raw, len(raw), wrap_rom=True)) & 63
            assert np.array_equal(actual, O.model_codes(raw, m))
            # Every 10-bit raw address, with independent state probes.
            for a in range(1024):
                word = sum(((a >> j) & 1) << bit for j, bit in enumerate(C.pair_bits('4411')))
                for s in (0, 7, 16, 31):
                    token = lut[a] >> 11
                    expected = lut[s*32+token] & 63
                    out = BS.simulate(source, [word & 255, word >> 8, 0, 0], 4,
                                      initial=(0,0,0,s << 6), wrap_rom=True)
                    assert [v & 63 for v in out] == [0, 0, expected, expected]
        return dict(models=len(candidates()), streams=8192,
                    encoder_state_probes=1024*4*len(candidates()),
                    status='host_dataflow_pass_not_physical_timing')
    finally:
        BS.parse = old


if __name__ == '__main__':
    print(json.dumps(source_checks()))

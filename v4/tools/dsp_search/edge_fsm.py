"""Second-order range-edge finite-state demods (C5VRX by Twotoz/contributors).

Generalizes tools/generate_edge.py: P phases x F frequency states x T tokens
(P*F*T = 1024), first-sample angle tokens or PAIR4411 learned tokens, click
hold, clipped correction, output = frequency state plus a fraction of the
correction. Built for the range edge; strong-signal detail is not a goal.
"""
import math
import numpy as np
import overlay_fsm as O
import compile_overlay as C

B = O.H.B


def first_tokens(T, dc=0j):
    i, q = B.D.cells(np.arange(256))
    a = np.arctan2(q + .5 - dc.imag, i + .5 - dc.real)
    return (np.floor((a + np.pi) * T / (2 * np.pi)).astype(int)) % T, (np.arange(T) + .5) * 2 * np.pi / T - np.pi


def pair_tokens(T, vectors, rotation):
    v = np.asarray(vectors, float); z = v[:, 0] + 1j * v[:, 1]
    za, _ = O.pair_cells('4411')
    z = np.where(abs(z) > 0, z, .01 * za / np.maximum(abs(za), 1e-12))
    a = np.angle(z) - rotation
    tok = (np.floor((a + np.pi) * T / (2 * np.pi)).astype(int)) % T
    # Token observation: circular mean of member addresses' learned phasors.
    obs = np.zeros(T)
    for t in range(T):
        m = tok == t
        obs[t] = np.angle(np.sum(z[m])) if m.any() else (t + .5) * 2 * np.pi / T - np.pi + rotation
    return tok, obs


NOMINAL_CENTRE_HZ = 1e6  # design carrier for low_hz/high_hz and the DAC transfer


def synthesize(p):
    T = 1 << int(p['token_bits']); P = int(p['phases']); F = int(p['frequencies'])
    if P * F * T != 1024 or P < 4 or F < 2:
        raise ValueError('edge allocation must fill 1024 words with P>=4, F>=2')
    # AutoFit: measured carrier centre, VTX deviation scale and receiver DC
    # (cells) re-place the frequency states and the DAC transfer so a
    # different VTX/board lands on the same nominal video levels.
    fit_c = float(p.get('fit_centre_hz', NOMINAL_CENTRE_HZ)); fit_d = float(p.get('fit_deviation', 1.))
    if not .4 <= fit_d <= 3. or abs(fit_c) > 4e6:
        raise ValueError('autofit outside supported range')
    lo = fit_c + fit_d * (p['low_hz'] - NOMINAL_CENTRE_HZ)
    hi = fit_c + fit_d * (p['high_hz'] - NOMINAL_CENTRE_HZ)
    fmin = 2 * np.pi * lo / 20e6; fmax = 2 * np.pi * hi / 20e6
    if not fmin < fmax:
        raise ValueError('frequency range')
    step = (fmax - fmin) / (F - 1)
    # Structural building blocks (defaults reproduce generate_edge exactly).
    detector = p.get('detector', 'clip'); output = p.get('output', 'freq')
    grid = p.get('grid', 'uniform'); leak = float(p.get('leak', 0.))
    if detector not in ('clip', 'tanh', 'sine', 'softhold') or output not in ('freq', 'advance', 'avg'):
        raise ValueError('unknown edge building block')
    if grid not in ('uniform', 'companded') or not 0 <= leak <= .2:
        raise ValueError('frequency grid/leak')
    if grid == 'uniform':
        levels = fmin + np.arange(F) * step
    else:  # denser near the video range centre, same end points
        u = np.linspace(-1, 1, F); mid = (fmin + fmax) / 2
        levels = mid + (fmax - fmin) / 2 * np.sign(u) * abs(u) ** float(p.get('gamma', 1.6))
    if p.get('pair_layout'):
        tok, obs = pair_tokens(T, p['observation_vectors'], p['rotation'])
    else:
        tok, obs = first_tokens(T, complex(*p.get('fit_dc_cells', (0., 0.)))); obs = obs + p['rotation']
    s = np.arange(P * F); fi = s // P; pi_ = s % P
    fr = levels[fi][:, None]; pr = (pi_ * 2 * np.pi / P)[:, None] + fr
    e = (obs[None, :] - pr + np.pi) % (2 * np.pi) - np.pi
    if detector == 'softhold':  # weight falls smoothly for implausible steps
        e = e * np.exp(-(e / p['hold']) ** 2)
    else:
        e = np.where(abs(e) > p['hold'], 0., e)
        if detector == 'tanh':
            e = p['limit'] * np.tanh(e / p['limit'])
        elif detector == 'sine':
            e = np.sin(e)
    e = np.clip(e, -p['limit'], p['limit'])
    if p.get('reliability') and p.get('pair_layout'):
        v = np.asarray(p['observation_vectors'], float); r = np.hypot(v[:, 0], v[:, 1])
        rt = np.array([r[tok == t].mean() if np.any(tok == t) else 1. for t in range(T)])
        e = e * np.clip(rt / max(rt.max(), 1e-9), .2, 1)[None, :]
    centre = 2 * np.pi * float(p.get('centre_hz', 1e6)) / 20e6
    target = fr + p['ki'] * e - leak * (fr - centre)
    if grid == 'uniform':
        f2 = np.clip(np.rint((target - fmin) / step), 0, F - 1).astype(int)
    else:
        f2 = np.argmin(abs(target[..., None] - levels), axis=-1)
    p2 = (np.floor((pr + p['kp'] * e) * P / (2 * np.pi) + .5).astype(int)) % P
    if output == 'freq':
        out = levels[f2] + p['mix'] * p['kp'] * e
    elif output == 'advance':
        out = fr + p['kp'] * e
    else:
        out = (fr + levels[f2]) / 2 + p['mix'] * p['kp'] * e
    if 'fit_deviation' in p or 'fit_centre_hz' in p:
        # Map this VTX's frequency back onto the nominal transfer.
        cn = 2 * np.pi * NOMINAL_CENTRE_HZ / 20e6; cm = 2 * np.pi * fit_c / 20e6
        out = cn + (out - cm) / fit_d
    code = np.rint(np.clip((out * 128 / np.pi - B.P.OFFSET) * B.P.SCALE, 0, 63)).astype(np.uint16)
    lut = (code + ((f2 * P + p2).astype(np.uint16) << 6)).ravel()
    b = int(p['token_bits'])
    start = 0 if p.get('pair_layout') else 768
    lut[start:start + len(tok)] |= (tok.astype(np.uint16) << (16 - b))
    params = dict(p, edge_fsm=True, confidence_groups=1)
    return dict(name='EDGE', params=params, lut=[int(v) for v in lut])


def test():
    import json
    from pathlib import Path
    import bs_model as BS
    root = Path(__file__).resolve().parents[2]
    ref = json.loads((root / 'tools/edge_model.json').read_text())
    p = dict(token_bits=3, phases=8, frequencies=16, kp=.7, ki=.2, limit=3.1, hold=3.2,
             low_hz=-3.2e6, high_hz=5.2e6, rotation=0., mix=0.)
    m = synthesize(p)
    raw = np.random.default_rng(3).integers(0, 256, 40000).astype(np.uint8)
    assert np.array_equal(O.model_codes(raw, m), O.model_codes(raw, ref)), 'edge generalization diverges from generate_edge'
    for layout in (None, '4411'):
        q = dict(p, token_bits=4, phases=8, frequencies=8)
        if layout:
            v, _ = __import__('pair_decoder').learn('4411', 4242, cases=4)
            q.update(pair_layout='4411', observation_vectors=v)
        mm = synthesize(q); src = C.build(mm)
        out = BS.simulate(src, raw[:20000], 20000, wrap_rom=True)
        assert np.array_equal(np.array(out) & 63, O.model_codes(raw[:20000], mm)), layout
    print('PASS edge_fsm: matches generate_edge; plain/PAIR source equivalence')


if __name__ == '__main__':
    test()

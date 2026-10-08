#!/usr/bin/env python3
"""EDGE LAB: second-order finite-state range-edge demod (C5VRX by Twotoz/contributors).

8 phase x 16 frequency states (7 bits) and 8 first-sample angle tokens (3
bits) fill the shared-word LUT16: phase-locked frequency memory instead of
fine phase. The video output is the frequency state, so a noise-shrunk
phase detector or an FM click does not pull the picture: steps further
than `hold` radians from the prediction are ignored. Built for the C/N 0..8
range edge, not strong-signal detail. Standard library only.
"""
import json
import math
from pathlib import Path
from compile_overlay import build

ROOT = Path(__file__).resolve().parents[1]
PARAMS = dict(token_bits=3, phases=8, confidence_groups=1, frequencies=16,
              kp=0.7, ki=0.2, limit=3.1, hold=3.2,
              low_hz=-3.2e6, high_hz=5.2e6, centre_hz=1.0e6)
# Same Phase50 DAC transfer as every shared-word tracker (overlay_fsm).
OFFSET, SCALE = None, None


def transfer():
    import sys
    sys.path.insert(0, str(ROOT / 'tools/detector_study'))
    import broad_search as B
    return B.P.OFFSET, B.P.SCALE


def wrap(x):
    return (x + math.pi) % (2 * math.pi) - math.pi


def cell(v):
    return v - 16 if v > 7 else v


def token(raw):
    i, q = cell((raw >> 4) & 15), cell(raw & 15)
    a = math.atan2(q + .5, i + .5)
    return int(math.floor((a + math.pi) * 8 / (2 * math.pi))) % 8


def synthesize(p=PARAMS, offset=None, scale=None):
    if offset is None:
        offset, scale = transfer()
    P, F, T = p['phases'], p['frequencies'], 1 << p['token_bits']
    fmin = 2 * math.pi * p['low_hz'] / 20e6
    fmax = 2 * math.pi * p['high_hz'] / 20e6
    step = (fmax - fmin) / (F - 1)
    lut = [0] * 1024
    for s in range(P * F):
        fi, pi = divmod(s, P)
        fr = fmin + fi * step
        pr = pi * 2 * math.pi / P + fr
        for t in range(T):
            obs = (t + .5) * 2 * math.pi / T - math.pi
            e = wrap(obs - pr)
            if abs(e) > p['hold']:
                e = 0.
            e = min(p['limit'], max(-p['limit'], e))
            f2 = min(F - 1, max(0, int(round((fr + p['ki'] * e - fmin) / step))))
            p2 = int(math.floor((pr + p['kp'] * e) * P / (2 * math.pi) + .5)) % P
            out = fmin + f2 * step
            code = int(round(min(63, max(0, (out * 128 / math.pi - offset) * scale))))
            lut[(s << 3) + t] = code | ((f2 * P + p2) << 6)
    for raw in range(256):
        lut[768 + raw] |= token(raw) << 13
    return dict(name='EDGE', params=dict(p, edge_fsm=True), lut=lut)


def test():
    m = synthesize()
    source = build(m)
    assert len(m['lut']) == 1024 and all(0 <= w < 65536 for w in m['lut'])
    import random
    import bs_model as B
    rng = random.Random(5)
    raw = [rng.randrange(256) for _ in range(20000)]
    state, expected = 0, [0, 0]
    for k in range(0, len(raw), 2):
        word = m['lut'][(state << 3) + (m['lut'][768 + raw[k]] >> 13)]
        state = (word >> 6) & 127
        expected += [word & 63] * 2
    actual = B.simulate(source, raw, len(raw), wrap_rom=True)
    assert [v & 63 for v in actual] == expected[:len(raw)]
    print('PASS edge: 8x16 states, 8 tokens, 20k-sample source/host equivalence')


if __name__ == '__main__':
    test()
    (ROOT / 'tools/edge_model.json').write_text(json.dumps(synthesize()) + '\n', encoding='utf-8')

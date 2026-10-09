"""Exact mirror of firmware fdemod_phase_cnr_x10 (C5VRX by Twotoz/contributors)."""
import math
import re
from pathlib import Path
import numpy as np

_LUT = None


def lut():
    global _LUT
    if _LUT is None:
        t = (Path(__file__).resolve().parents[2] / 'main/phase8_gain_lut.h').read_text()
        _LUT = np.array([int(x, 0) for x in re.findall(r'0x[0-9a-fA-F]+|\b\d+\b', t.split('{', 1)[1].split('}')[0])], np.int64)
    return _LUT


def phase_cnr_x10(raw):
    if len(raw) < 64: return -99
    p = lut()[np.asarray(raw, np.int64)]
    d = p[2:] - 2 * p[1:-1] + p[:-2]
    d = ((d % 256) + 256 + 128) % 256 - 128
    hist = np.bincount(np.abs(d), minlength=129); count = len(d); half = count // 2
    cum = 0; med = 128.
    for b in range(129):
        if cum + hist[b] > half:
            med = b - .5 + (half - cum + .5) / hist[b]; break
        cum += hist[b]
    med = max(med, .05)
    sigma = 1.4826 * med * (2 * math.pi / 256); rho = 3. / (sigma * sigma)
    return int(round(100. * math.log10(rho)))

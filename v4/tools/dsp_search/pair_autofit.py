"""PAIR AutoFit research mirror of firmware pair_af_remap (C5VRX).

Remaps the DAC code (bits 0..5) of a fixed LUT to a measured VTX deviation
and carrier centre on the search model's nominal transfer; tracking bits
are unchanged. tools/generate_edge_autofit.py emits bit-exact goldens.
"""
import numpy as np


def remap(m, dev, centre):
    import edge_fsm as F
    B = F.B
    lut = np.array(m['lut'], np.int64); code = lut & 63
    out = (code / B.P.SCALE + B.P.OFFSET) * np.pi / 128
    cn = 2 * np.pi * F.NOMINAL_CENTRE_HZ / 20e6; cm = 2 * np.pi * centre / 20e6
    o2 = cn + (out - cm) / dev
    c2 = np.rint(np.clip((o2 * 128 / np.pi - B.P.OFFSET) * B.P.SCALE, 0, 63)).astype(np.int64)
    return dict(m, lut=[int(v) for v in (lut & ~63) | c2])

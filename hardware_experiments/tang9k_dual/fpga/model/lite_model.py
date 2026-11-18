"""C5VRX by Twotoz and the C5VRX contributors: bit-exact integer model of the
multiplier-free "lite" combiner for the Tang Nano 1K (GW1NZ-1: 1152 LUT4,
4 block RAMs, no DSP). Same pre-detection MRC as mrc_model.py, computed in
the angle/log domain with tables:

  T1[raw]       angle (2^-10 turn) and 4 log2|c| of a 4-bit ultrafine cell
  per branch    me = LPF64(lg), ve = LPF64((lg - me)^2): log mean and
                variance of the envelope; G[ve] - me = 4 log2 of the MRC
                weight a/sigma^2 (G calibrated on simulated carriers)
  tracker       x = LPF128(cos/sin(angA - angB)) (unit phasors, Q6);
                theta = TH[x after sample k - 2, normalised to 5+5 bits]
                (2^-8 turn)
  combine       d = angB + theta - angA (top 7 bits),
                r = ((lwB + lgB) - (lwA + lgA)) / 2 clamped -8..7,
                phi = angA + 2 * T2[d, r], T2 = angle of 1 + 2^(r/2) e^{j d}
                (the resultant of wA zA + wB zB', i.e. MRC)
  discriminator dphi = phi - phi[n-1] (2^-10 turn);
                code = clamp((dphi * 168 + 25805 + 512) >> 10, 0, 63)
B absent: phi = angA. Extends the dual-C5 study (eval_model.py).
"""
import math
from functools import lru_cache

import numpy as np
from numba import njit

M_SHIFT = 6
TH_DELAY = 2         # tracker angle of sample k from the accumulator after sample k - 2 (RTL)
X_SHIFT = 7
G_LEN = 64


def t1_table():
    ang = np.zeros(256, np.int64)
    lg = np.zeros(256, np.int64)
    for raw in range(256):
        i = ((raw >> 4) ^ 8) - 8
        q = ((raw & 15) ^ 8) - 8
        ci, cq = 2 * i + 1, 2 * q + 1
        ang[raw] = int(round(math.atan2(cq, ci) / (2 * math.pi) * 1024)) & 1023
        lg[raw] = int(round(4 * math.log2(math.hypot(ci, cq))))
    return ang, lg


def th_table():
    """angle of a normalised (x, y), each -16..15, 2^-8 turn; 1024 entries."""
    t = np.zeros(1024, np.int64)
    for a in range(1024):
        x = ((a >> 5) ^ 16) - 16
        y = ((a & 31) ^ 16) - 16
        t[a] = int(round(math.atan2(y + 0.5, x + 0.5) / (2 * math.pi) * 256)) & 255
    return t


def cs_table():
    """cos/sin of a 6-bit angle, Q6 (+-31)."""
    c = np.array([int(round(31 * math.cos(2 * math.pi * k / 64))) for k in range(64)], np.int64)
    s = np.array([int(round(31 * math.sin(2 * math.pi * k / 64))) for k in range(64)], np.int64)
    return c, s


def t2_table():
    """2^-9 turn correction for d (7 bits, 2^-7 turn) and r (-8..7, half log2 steps)."""
    t = np.zeros(2048, np.int64)
    for d in range(128):
        delta = 2 * math.pi * (d + 0.5) / 128
        for ri in range(16):
            r = ri - 8
            rho = 2.0 ** (r / 2)
            a = math.atan2(rho * math.sin(delta), 1 + rho * math.cos(delta))
            t[(d << 4) | ri] = int(round(a / (2 * math.pi) * 512)) & 511
    return t


@lru_cache(None)
def g_table():
    """4 log2 of the MRC weight a/sigma^2 (relative to the branch power, the
    -me term is added in hardware) against ve, from simulated 4-bit carriers
    at the AGC radius (6 cells) over -12..30 dB SNR."""
    ang, lg = t1_table()
    rng = np.random.default_rng(1)
    snrs = np.arange(-12, 30.1, 0.5)
    ves, ws = [], []
    for snr_db in snrs:
        s = 10 ** (snr_db / 10)
        n = 40000
        phase = np.cumsum(rng.normal(0, 0.3, n))
        z = np.exp(1j * phase) * math.sqrt(s) + (rng.normal(size=n) + 1j * rng.normal(size=n)) / math.sqrt(2)
        z *= 6.0 / math.sqrt(np.mean(abs(z) ** 2))
        def comp(x):
            adc = np.clip(np.floor(x * 16), -512, 511).astype(np.int64)
            return ((adc >> 4) & 7) | (8 * (adc < 0))
        raw = (comp(z.real) << 4) | comp(z.imag)
        l = lg[raw]
        me_acc, ve_acc, vv, mm = 0, 0, [], []
        for v in l[:8000]:
            me = me_acc >> M_SHIFT
            dev = int(v) - me
            ve_acc += min(dev * dev, 255) - (ve_acc >> M_SHIFT)
            me_acc += int(v) - (me_acc >> M_SHIFT)
            vv.append(ve_acc >> M_SHIFT)
            mm.append(me)
        ve = float(np.mean(vv[1000:]))
        me = float(np.mean(mm[1000:]))
        c = np.where(True, 1, 1)
        cells = 2 * (((raw >> 4) ^ 8) - 8) + 1 + 1j * (2 * (((raw & 15) ^ 8) - 8) + 1)
        m2 = float(np.mean(abs(cells) ** 2))
        a = s / (1 + s)
        w = math.sqrt(a) / max(1 - a, 1 / 32) / math.sqrt(m2)       # a/sigma^2 in raw units
        ves.append(ve)
        ws.append(4 * math.log2(w) + me)                             # hardware adds -me
    ves = np.array(ves)
    ws = np.array(ws)
    order = np.argsort(ves)
    out = np.zeros(G_LEN, np.int64)
    for v in range(G_LEN):
        out[v] = int(round(np.interp(v, ves[order], ws[order])))
    return out


T1_ANG, T1_LG = t1_table()
TH = th_table()
COS6, SIN6 = cs_table()
T2 = t2_table()


def norm5(x, y):
    """Shift both right until they fit -16..15 (shared exponent)."""
    s = 0
    while not (-16 <= (x >> s) <= 15 and -16 <= (y >> s) <= 15):
        s += 1
    return x >> s, y >> s


@njit(cache=True)
def _norm5(x, y):
    s = 0
    while not (-16 <= (x >> s) <= 15 and -16 <= (y >> s) <= 15):
        s += 1
    return x >> s, y >> s


@njit(cache=True)
def _run(ra, rb, en_b, T1A, T1L, TH, C6, S6, T2, G, out):
    me = np.zeros(2, np.int64)
    ve = np.zeros(2, np.int64)
    xr = 0
    xi = 0
    hr = np.zeros(len(ra), np.int64)
    hi = np.zeros(len(ra), np.int64)
    phi_prev = 0
    for k in range(len(ra)):
        aa = T1A[ra[k]]
        la = T1L[ra[k]]
        ab = T1A[rb[k]]
        lb = T1L[rb[k]]
        # registered state in use for this sample
        mea = me[0] >> 6
        meb = me[1] >> 6
        vea = min(ve[0] >> 6, 63)
        veb = min(ve[1] >> 6, 63)
        j0 = k - 2
        nx, ny = _norm5(hr[j0] if j0 >= 0 else 0, hi[j0] if j0 >= 0 else 0)
        th = TH[((nx & 31) << 5) | (ny & 31)]
        if en_b:
            d = ((ab + (th << 2) - aa) & 1023) >> 3
            r = ((G[veb] - meb + lb) - (G[vea] - mea + la)) >> 1
            r = -8 if r < -8 else (7 if r > 7 else r)
            phi = (aa + 2 * T2[(d << 4) | (r + 8)]) & 1023
        else:
            phi = aa
        dp = (phi - phi_prev) & 1023
        if dp >= 512:
            dp -= 1024
        phi_prev = phi
        code = (dp * 168 + 25805 + 512) >> 10
        out[k] = 0 if code < 0 else (63 if code > 63 else code)
        # state updates
        for j in range(2):
            l = la if j == 0 else lb
            m = mea if j == 0 else meb
            dev = l - m
            sq = dev * dev
            ve[j] += (sq if sq < 255 else 255) - (ve[j] >> 6)
            me[j] += l - (me[j] >> 6)
        dd = ((aa - ab) & 1023) >> 4
        xr += C6[dd] - (xr >> 7)
        xi += S6[dd] - (xi >> 7)
        hr[k] = xr
        hi[k] = xi


def run(raw_a, raw_b=None, enable_b=True):
    use_b = bool(enable_b) and raw_b is not None
    ra = np.asarray(raw_a, np.int64)
    rb = np.zeros_like(ra) if raw_b is None else np.asarray(raw_b, np.int64)
    out = np.zeros(len(ra), np.int64)
    _run(ra, rb, use_b, T1_ANG, T1_LG, TH, COS6, SIN6, T2, g_table(), out)
    return out.astype(np.uint8)

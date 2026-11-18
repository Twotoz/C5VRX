"""C5VRX by Twotoz and the C5VRX contributors: bit-exact integer model of the
TANG9K_DUAL FPGA combiner/demodulator (fpga/rtl/mrc_core.v).

Extends the dual-C5 host study (pre-detection MRC with a cross-product
phase tracker) to the integer datapath the Tang Nano 9K runs at the C5 A
sample clock (40 MS/s). Inputs are the raw IQ bytes of both C5s (low
nibble Q, high nibble I, two's complement sign bit 3: ultrafine
{9,6,5,4}), already aligned sample for sample (fpga/rtl/align_b.v does
that in hardware). Output: one six-bit DAC code per sample, the
-6..+9 MHz window of the C5VRX-5 decoders (research/sim.py to_codes).

Per sample n, per branch k (c = 2v+1, odd, -15..15):
  p      = cI^2 + cQ^2
  m2acc += p   - (m2acc >> 6)            ~ 64 E[p]
  m4acc += p^2 - (m4acc >> 6)            ~ 64 E[p^2]
Every 128 samples (after sample k % 128 == 127) both weights are
recomputed from snapshots and take effect one period later, after the next
sample k % 128 == 127 (the RTL computes them sequentially in between):
  rq  = min((m4acc << 14) // m2acc^2, 511), r = rq / 256 = E[p^2]/E[p]^2
  w   = max(1, (G[rq - 256] * ISQ[m2acc >> 6]) >> 8)  (8 bit)
  with a = sqrt(2 - r) = signal share of the power, h = sqrt(a)/(1 - a)
  (= sqrt(s)/n relative to sqrt(m2)): the MRC weight a_k / sigma_k^2.
Phase tracker (modulation cancels in the cross product):
  xacc += cA * conj(cB) - (xacc >> 7)    (tau 128 samples = 3.2 us)
  theta = CORDIC angle of xacc >> 4, 8 bit turns, taken after every 16th
          sample (k % 16 == 15) and used from the end of the next block
  zB'   = cB * (C + jS)[theta]           (Q1.6 cos/sin table)
Combine and demodulate:
  z     = wA * cA * 64 + wB * zB'
  phi   = CORDIC angle of z >> 6, 16 bit turns (10 steps); dphi = phi - phi[n-1] (wrap)
  code  = clamp((dphi * 168 + 1651507 + 32768) >> 16, 0, 63)
B absent (enable_b = 0): wB = 0, a single-branch float-precision
discriminator on C5 A.
"""
import math
import numpy as np
from numba import njit

M_SHIFT = 6          # envelope moment low-pass, 64 samples
X_SHIFT = 7          # cross-product tracker, 128 samples
W_PERIOD = 128       # weight update cadence
W_APPLY = 127        # new weights apply after sample k % 128 == W_APPLY (one period later)
CORDIC_ITERS = 10    # residual angle error atan(2^-9) ~ 2^-11 turn, far under one code (2^-7.4 turn)
ANGLE_BITS = 16
Z_SHIFT = 6          # combiner output scaled down before the discriminator CORDIC
X_SHIFT_TH = 4       # tracker accumulator scaled down before its CORDIC
TH_BLOCK = 16        # tracker angle update cadence (samples)


def _g_table():
    out = np.zeros(256, np.int64)
    for i in range(256):
        r = 1.0 + i / 256.0
        a = math.sqrt(max(2.0 - r, 0.0))
        a = min(a, 31.0 / 32.0)                 # SNR clamp 31 (15 dB)
        h = math.sqrt(a) / (1.0 - a)
        out[i] = min(255, int(round(h * 7.75)))
    return out


def _isq_table():
    out = np.zeros(512, np.int64)
    for m in range(512):
        out[m] = int(round(255.0 * 4.0 / math.sqrt(max(m, 16))))
    return out


def _trig_table():
    c = np.zeros(256, np.int64)
    s = np.zeros(256, np.int64)
    for t in range(256):
        a = 2 * math.pi * t / 256
        c[t] = int(round(63 * math.cos(a)))
        s[t] = int(round(63 * math.sin(a)))
    return c, s


def _atan_table():
    # atan(2^-i) in units of 2^-16 turns (1 turn = 65536)
    return np.array([int(round(math.atan(2.0 ** -i) / (2 * math.pi) * (1 << ANGLE_BITS)))
                     for i in range(CORDIC_ITERS)], np.int64)


G = _g_table()
ISQ = _isq_table()
COS, SIN = _trig_table()
ATAN = _atan_table()


@njit(cache=True)
def cordic_angle(x, y, atan):
    """Angle of (x, y) in 2^-16 turns, 0..65535. Integer vectoring CORDIC:
    quadrant pre-rotation, then len(atan) shift-add steps. (0, 0) -> 0."""
    ang = 0
    if x < 0:                      # rotate by 180 degrees
        x, y = -x, -y
        ang = 32768
    for i in range(len(atan)):
        if y > 0:
            x, y = x + (y >> i), y - (x >> i)
            ang += atan[i]
        else:
            x, y = x - (y >> i), y + (x >> i)
            ang -= atan[i]
    return ang & 0xFFFF


@njit(cache=True)
def _run(raw_a, raw_b, enable_b, G, ISQ, COS, SIN, ATAN, out, dbg):
    n = len(raw_a)
    m2 = np.zeros(2, np.int64)
    m4 = np.zeros(2, np.int64)
    w = np.ones(2, np.int64)
    pend = np.ones(2, np.int64)
    have_pend = False
    xr = 0
    xi = 0
    th_cur = 0
    th_pend = 0
    phi_prev = 0
    for k in range(n):
        ba = raw_a[k]
        bb = raw_b[k]
        aq = ((ba & 15) ^ 8) - 8
        ai = (((ba >> 4) & 15) ^ 8) - 8
        bq = ((bb & 15) ^ 8) - 8
        bi = (((bb >> 4) & 15) ^ 8) - 8
        ca_i = 2 * ai + 1
        ca_q = 2 * aq + 1
        cb_i = 2 * bi + 1
        cb_q = 2 * bq + 1
        # weights in use for this sample (registered values)
        wa = w[0]
        wb = w[1] if enable_b else 0
        # tracker angle from the registered accumulator
        c = COS[th_cur]
        s = SIN[th_cur]
        rb_i = cb_i * c - cb_q * s
        rb_q = cb_i * s + cb_q * c
        zi = wa * ca_i * 64 + wb * rb_i
        zq = wa * ca_q * 64 + wb * rb_q
        phi = cordic_angle(zi >> Z_SHIFT, zq >> Z_SHIFT, ATAN)
        d = (phi - phi_prev) & 0xFFFF
        if d >= 32768:
            d -= 65536
        phi_prev = phi
        code = (d * 168 + 1651507 + 32768) >> 16
        out[k] = 0 if code < 0 else (63 if code > 63 else code)
        # state updates
        pa = ca_i * ca_i + ca_q * ca_q
        pb = cb_i * cb_i + cb_q * cb_q
        m2[0] += pa - (m2[0] >> M_SHIFT)
        m2[1] += pb - (m2[1] >> M_SHIFT)
        m4[0] += pa * pa - (m4[0] >> M_SHIFT)
        m4[1] += pb * pb - (m4[1] >> M_SHIFT)
        pr = ca_i * cb_i + ca_q * cb_q
        pi = ca_q * cb_i - ca_i * cb_q
        xr += pr - (xr >> X_SHIFT)
        xi += pi - (xi >> X_SHIFT)
        if (k & (TH_BLOCK - 1)) == TH_BLOCK - 1:
            th_cur = th_pend
            th_pend = (cordic_angle(xr >> X_SHIFT_TH, xi >> X_SHIFT_TH, ATAN) + 128) >> 8 & 255
        if (k & (W_PERIOD - 1)) == W_APPLY and have_pend:
            w[0] = pend[0]
            w[1] = pend[1]
        if (k & (W_PERIOD - 1)) == W_PERIOD - 1:
            have_pend = True
            for j in range(2):
                den = m2[j] * m2[j]
                if den == 0:
                    pend[j] = 1
                    continue
                rq = (m4[j] << 14) // den
                if rq > 511:
                    rq = 511
                if rq < 256:
                    rq = 256
                mi = m2[j] >> M_SHIFT
                if mi > 511:
                    mi = 511
                pend[j] = max(1, (G[rq - 256] * ISQ[mi]) >> 8)   # never 0: z stays defined
            dbg[k // W_PERIOD, 0] = pend[0]
            dbg[k // W_PERIOD, 1] = pend[1]


def run(raw_a, raw_b=None, enable_b=True):
    use_b = bool(enable_b) and raw_b is not None
    raw_a = np.asarray(raw_a, np.int64)
    raw_b = np.zeros_like(raw_a) if raw_b is None else np.asarray(raw_b, np.int64)
    out = np.zeros(len(raw_a), np.int64)
    dbg = np.zeros((len(raw_a) // W_PERIOD + 1, 2), np.int64)
    _run(raw_a, raw_b, use_b, G, ISQ, COS, SIN, ATAN, out, dbg)
    return out.astype(np.uint8), dbg


def tables_verilog():
    """ROM contents for the RTL ($readmemh files)."""
    return {
        'g_lut.hex': [f'{v:02x}' for v in G],
        'isq_lut.hex': [f'{v:02x}' for v in ISQ],
        'cos_lut.hex': [f'{v & 0xff:02x}' for v in COS],
        'sin_lut.hex': [f'{v & 0xff:02x}' for v in SIN],
        'atan.hex': [f'{v:04x}' for v in ATAN],
    }

#!/usr/bin/env python3
"""Measure consecutive jump distributions:
|D[n] - D[n-1]| >= 8, >= 16, >= 32
for:
1. Golden Phase5
2. Old True40
3. Candidate G (corrected register & pipeline mapping)
on exact same captures.
"""

from pathlib import Path
import sys
import numpy as np
import math

ROOT = Path("C:/Users/leonb/Twotoz/C5VRX-issue11-output")
sys.path.append(str(ROOT / "tools"))

def signed_bucket_center(code: int, bits: int) -> float:
    width = 1 << (10 - bits)
    center = code * width + (width - 1) * 0.5
    return center - 1024.0 if center >= 512.0 else center

def exact_phase(packed: int) -> float:
    q = signed_bucket_center(packed & 0x0F, 4)
    i = signed_bucket_center(packed >> 4, 4)
    return math.atan2(q, i)

def phase5(packed: int) -> int:
    return round(exact_phase(packed) * 32.0 / (2.0 * math.pi)) & 0x1F

centroids = []
for state in range(32):
    members = [exact_phase(p) for p in range(256) if phase5(p) == state]
    s = sum(math.sin(v) for v in members)
    c = sum(math.cos(v) for v in members)
    centroids.append(math.atan2(s, c))

p8 = [round(v * 256.0 / (2.0 * math.pi)) for v in centroids]

def centroid_delta_phase8(prev: int, curr: int) -> int:
    return (p8[curr] - p8[prev] + 128) % 256 - 128

def scale_real_sum(val: int) -> int:
    num = val * 3
    return -((-num + 2) // 4) if num < 0 else (num + 2) // 4

golden_matrix = np.zeros((256, 256), dtype=np.uint8)
for p in range(256):
    p_ph = phase5(p)
    for c in range(256):
        c_ph = phase5(c)
        d = centroid_delta_phase8(p_ph, c_ph)
        golden_matrix[p, c] = max(0, min(63, 20 + scale_real_sum(d)))

cap1 = np.fromfile(ROOT / "measurements/issue-11-cvbs/raw-vtx-on.bin", dtype=np.uint8)
cap2 = np.fromfile(ROOT / "measurements/issue-11-cvbs/vtx_real_capture_v3.bin", dtype=np.uint8)
cap = np.concatenate([cap1, cap2])
N = len(cap)

# 1. Golden Phase5 stream:
# Samples pairs at 20 MS/s on even samples: raw[2k-2] -> raw[2k]
# Emits [D, D] at 40 MS/s
golden_stream = np.zeros(N, dtype=np.uint8)
p = 0
for k in range(0, N - 1, 2):
    c = phase5(cap[k])
    d = centroid_delta_phase8(p, c)
    code = max(0, min(63, 20 + scale_real_sum(d)))
    golden_stream[k] = code
    golden_stream[k+1] = code
    p = c

# 2. Old True40 stream:
# Wire5: (1, 2, 3, 6, 7)
def wire5(b):
    return ((b >> 1) & 0x07) | (((b >> 6) & 0x03) << 3)

# Load old True40 LUT
from train_true40_lut import build_true40_clean_lut
old_true40_lut = build_true40_clean_lut()

true40_stream = np.zeros(N, dtype=np.uint8)
even_w5 = 0
odd_w5 = 0
for k in range(N):
    w = wire5(cap[k])
    if k % 2 == 0:
        a = (even_w5 << 5) | w
        even_w5 = w
    else:
        a = (odd_w5 << 5) | w
        odd_w5 = w
    true40_stream[k] = old_true40_lut[a]

# 3. Candidate G stream:
# Curr bits: 1, 2, 3 (Q3) and 5, 6, 7 (I3) -> 6 bits
# Prev bits: 2, 3 (Q2) and 5, 6, 7 (I3) -> 5 bits
curr_table = np.zeros(256, dtype=np.uint16)
prev_table = np.zeros(256, dtype=np.uint16)
for b in range(256):
    # curr 6 bits: b1, b2, b3, b5, b6, b7
    c6 = ((b >> 1) & 7) | (((b >> 5) & 7) << 3)
    curr_table[b] = c6
    # prev 5 bits: b2, b3, b5, b6, b7
    p5 = ((b >> 2) & 3) | (((b >> 5) & 7) << 2)
    prev_table[b] = p5

# Train Candidate G median LUT
from test_lut_training_objectives import lut_median
cand_g_lut = lut_median

cand_g_stream = np.zeros(N, dtype=np.uint8)
even_p5 = prev_table[cap[0]]
odd_p5 = prev_table[cap[1]]

cand_g_stream[0] = 20
cand_g_stream[1] = 20

for k in range(2, N):
    c6 = curr_table[cap[k]]
    if k % 2 == 0:
        p5 = even_p5
        even_p5 = prev_table[cap[k]]
    else:
        p5 = odd_p5
        odd_p5 = prev_table[cap[k]]
    addr = (int(p5) << 6) | int(c6)
    cand_g_stream[k] = cand_g_lut[addr]

def measure_jumps(stream, name):
    diffs = np.abs(np.diff(stream.astype(np.int32)))
    n_diffs = len(diffs)
    mean_jump = np.mean(diffs)
    p95_jump = np.percentile(diffs, 95)
    p99_jump = np.percentile(diffs, 99)
    j_ge8 = np.sum(diffs >= 8)
    j_ge16 = np.sum(diffs >= 16)
    j_ge32 = np.sum(diffs >= 32)
    
    # Also consecutive jumps specifically on odd transitions (between 25ns steps)
    # in Golden, every even-to-odd transition is 0 jump because [D, D]
    print(f"=== {name} ===")
    print(f"  Mean consecutive jump: {mean_jump:.2f} codes")
    print(f"  P95 jump: {p95_jump:.1f} | P99 jump: {p99_jump:.1f}")
    print(f"  Jumps >= 8:  {j_ge8:6d} ({j_ge8/n_diffs*100:5.2f}%)")
    print(f"  Jumps >= 16: {j_ge16:6d} ({j_ge16/n_diffs*100:5.2f}%)")
    print(f"  Jumps >= 32: {j_ge32:6d} ({j_ge32/n_diffs*100:5.2f}%)")
    print()

print(f"Total samples analyzed: {N}\n")
measure_jumps(golden_stream, "1. GOLDEN PHASE5 (with [D, D] hold)")
measure_jumps(true40_stream, "2. OLD TRUE40 (5-wire Q3/I2)")
measure_jumps(cand_g_stream, "3. CANDIDATE G (11-bit Direct Span50)")

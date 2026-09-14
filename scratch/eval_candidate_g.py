#!/usr/bin/env python3
"""Evaluate Candidate G on unseen capture for direct comparison."""

from pathlib import Path
import numpy as np

ROOT = Path("C:/Users/leonb/Twotoz/C5VRX-issue11-output")
from compare_h_configs import golden_matrix, cap2

# Candidate G feature mappings
# curr 6 bits: Q3 (bits 1..3) + I3 (bits 5..7)
# prev 5 bits: Q2 (bits 2..3) + I3 (bits 5..7)

def g_curr(b):
    q3 = (b >> 1) & 7
    i3 = (b >> 5) & 7
    return (i3 << 3) | q3

def g_prev(b):
    q2 = (b >> 2) & 3
    i3 = (b >> 5) & 7
    return (i3 << 2) | q2

# Read Candidate G lut from main/c5vrx2_wbfm_interleaved40_direct11.bsasm
text = (ROOT / "main/c5vrx2_wbfm_interleaved40_direct11.bsasm").read_text()
lut_line = [l for l in text.splitlines() if l.startswith("lut ")][0]
lut_g = np.array([int(x) for x in lut_line[4:].split()], dtype=np.uint8)

p_test = cap2[:-2]
c_test = cap2[2:]
t_test = golden_matrix[p_test, c_test]
N = len(c_test)

out_stream = np.zeros(N, dtype=np.uint8)
even_p = g_prev(cap2[0])
odd_p = g_prev(cap2[1])

for k in range(N):
    raw_c = c_test[k]
    c6 = g_curr(raw_c)
    if k % 2 == 0:
        pst = even_p
        even_p = g_prev(raw_c)
    else:
        pst = odd_p
        odd_p = g_prev(raw_c)
    addr = (int(pst) << 6) | int(c6)
    out_stream[k] = lut_g[addr]

err = np.abs(out_stream.astype(np.int32) - t_test.astype(np.int32))
diffs = np.abs(np.diff(out_stream.astype(np.int32)))

print("=== CANDIDATE G (PREVIOUS FAILED EXPERIMENT) ===")
print(f"  Target MAE: {np.mean(err):.3f} | P95: {np.percentile(err, 95):.1f} | P99: {np.percentile(err, 99):.1f}")
print(f"  Target Error >= 16: {np.sum(err >= 16)} ({np.sum(err >= 16)/N*100:.2f}%)")
print(f"  Target Error >= 32: {np.sum(err >= 32)} ({np.sum(err >= 32)/N*100:.2f}%)")
print(f"  Output Mean Jump:  {np.mean(diffs):.2f} | P95 Jump: {np.percentile(diffs, 95):.1f}")
print(f"  Output Jump >= 16: {np.sum(diffs >= 16)} ({np.sum(diffs >= 16)/len(diffs)*100:.2f}%)")
print(f"  Output Jump >= 32: {np.sum(diffs >= 32)} ({np.sum(diffs >= 32)/len(diffs)*100:.2f}%)")
sync_bad = np.sum((out_stream > 10) & (t_test <= 4))
print(f"  Sync Corruption (>10): {sync_bad} ({sync_bad/np.sum(t_test <= 4)*100:.2f}%)")
even_m = np.mean(out_stream[::2])
odd_m = np.mean(out_stream[1::2])
print(f"  Even/Odd Parity Bias: {abs(even_m - odd_m):.4f} codes")

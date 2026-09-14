#!/usr/bin/env python3
"""Verify Candidate H-7+4 on all 32,834 samples of unseen real VTX capture."""

import sys
from pathlib import Path
import numpy as np

ROOT = Path("C:/Users/leonb/Twotoz/C5VRX-issue11-output")
sys.path.append(str(ROOT / "tools"))
sys.path.append(str(ROOT / "scratch"))
from bs_model import simulate

from compare_h_configs import build_and_test_h74, cap2, golden_matrix

r74 = build_and_test_h74()
lut = r74['lut']

asm_text = (ROOT / "main/c5vrx2_wbfm_candidate_h.bsasm").read_text()

N_TOTAL = len(cap2) - 2 # 32,832 samples
print(f"Simulating Candidate H-7+4 assembly on ALL {N_TOTAL} samples of unseen capture...")
sim_out = np.array(simulate(asm_text, list(cap2), N_TOTAL), dtype=np.uint8)

# Offline model stream
def to_curr7(b):
    return (((b >> 4) & 0xF) << 3) | ((b & 0xE) >> 1)
raw_to_c7 = np.array([to_curr7(b) for b in range(256)], dtype=np.uint8)

offline_out = np.zeros(N_TOTAL, dtype=np.uint8)
e_st = 0
o_st = 0
for k in range(N_TOTAL):
    c = cap2[k]
    c7 = raw_to_c7[c]
    sq = (c >> 3) & 1
    si = (c >> 7) & 1
    pst = e_st if k % 2 == 0 else o_st
    addr = (int(pst) << 7) | int(c7)
    val = lut[addr]
    l67 = (val >> 6) & 3
    if k % 2 == 0: e_st = (si << 3) | (sq << 2) | l67
    else: o_st = (si << 3) | (sq << 2) | l67
    offline_out[k] = val & 0x3F

diff = np.abs(sim_out.astype(int) - offline_out.astype(int))
print(f"Exact Matches across ALL {N_TOTAL} samples: {np.sum(diff == 0)} / {N_TOTAL} ({np.sum(diff == 0)/N_TOTAL*100:.4f}%)")

# Compare with Golden Target
t_sim = golden_matrix[cap2[:N_TOTAL-2], cap2[2:N_TOTAL]]
err = np.abs(sim_out[2:].astype(int) - t_sim.astype(int))
diffs = np.abs(np.diff(sim_out.astype(int)))

print("\n=======================================================")
print(f" COMPLETE UNSEEN REAL VTX CAPTURE GATES ({len(err)} samples)")
print("=======================================================")
print(f"  Target MAE: {np.mean(err):.3f} | P95: {np.percentile(err, 95):.1f} | P99: {np.percentile(err, 99):.1f}")
print(f"  Target Errors >= 16: {np.sum(err >= 16)} ({np.sum(err >= 16)/len(err)*100:.2f}%)")
print(f"  Target Errors >= 32: {np.sum(err >= 32)} ({np.sum(err >= 32)/len(err)*100:.4f}%)")
print(f"  Output Mean Jump:   {np.mean(diffs):.2f} | P95 Jump: {np.percentile(diffs, 95):.1f}")
print(f"  Output Jumps >= 16:  {np.sum(diffs >= 16)} ({np.sum(diffs >= 16)/len(diffs)*100:.2f}%)")
print(f"  Output Jumps >= 32:  {np.sum(diffs >= 32)} ({np.sum(diffs >= 32)/len(diffs)*100:.4f}%)")

sync_mask = t_sim <= 4
n_sync = np.sum(sync_mask)
sync_bad = np.sum((sim_out[2:] > 10) & sync_mask)
print(f"  Sync Corruption (>10 on <=4): {sync_bad} / {n_sync} ({sync_bad/n_sync*100:.2f}%)")

even_m = np.mean(sim_out[2::2])
odd_m = np.mean(sim_out[3::2])
print(f"  Even/Odd Parity Bias: {abs(even_m - odd_m):.4f} codes")

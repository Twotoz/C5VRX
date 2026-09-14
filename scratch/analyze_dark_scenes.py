#!/usr/bin/env python3
"""Evaluate dark scene / low RF magnitude behavior of:
1. Candidate G (Cartesian Q2/I3 state)
2. Candidate H-8+3 (Polar phase state)
3. Candidate H-7+4 (Polar phase state)
"""

from pathlib import Path
import numpy as np

ROOT = Path("C:/Users/leonb/Twotoz/C5VRX-issue11-output")
from compare_h_configs import golden_matrix, cap1, cap2, all_angles, build_and_test_h83, build_and_test_h74
from eval_candidate_g import g_curr, g_prev, lut_g

# Compute magnitude of each byte in cap2
def byte_mag(b):
    q = (b & 0xF)
    q = q - 16 if q >= 8 else q
    i = (b >> 4) & 0xF
    i = i - 16 if i >= 8 else i
    return np.sqrt(q*q + i*i)

mags = np.array([byte_mag(b) for b in cap2[2:]])
low_mag_mask = mags < np.percentile(mags, 25)
print(f"Total samples: {len(mags)}, Low-magnitude (dark scene) samples: {np.sum(low_mag_mask)}")

p_test = cap2[:-2]
c_test = cap2[2:]
t_test = golden_matrix[p_test, c_test]
N = len(c_test)

# Candidate G simulation
out_g = np.zeros(N, dtype=np.uint8)
ep = g_prev(cap2[0])
op = g_prev(cap2[1])
for k in range(N):
    c = c_test[k]
    c6 = g_curr(c)
    pst = ep if k % 2 == 0 else op
    if k % 2 == 0: ep = g_prev(c)
    else: op = g_prev(c)
    out_g[k] = lut_g[(pst << 6) | c6]

err_g_low = np.abs(out_g[low_mag_mask].astype(int) - t_test[low_mag_mask].astype(int))

# Run H-8+3 and H-7+4
r83 = build_and_test_h83()
r74 = build_and_test_h74()

# Candidate H-8+3 simulation
lut83 = r83['lut']
out_h83 = np.zeros(N, dtype=np.uint8)
# Compute state_table for H-8+3:
st83 = np.zeros(256, dtype=np.uint8)
for b in range(256):
    si = (b >> 7) & 1
    ang = all_angles[b]
    if si == 0:
        fine = int(np.clip((ang + np.pi/2.0) / np.pi * 4.0, 0, 3))
        st83[b] = fine
    else:
        fine = int(np.clip((ang - np.pi/2.0) / (np.pi/2.0) * 2.0, 0, 1)) if ang >= 0 else 2 + int(np.clip((ang + np.pi) / (np.pi/2.0) * 2.0, 0, 1))
        st83[b] = 4 + fine

e_st = st83[cap2[0]]
o_st = st83[cap2[1]]
for k in range(N):
    c = c_test[k]
    si = (c >> 7) & 1
    pst = e_st if k % 2 == 0 else o_st
    addr = (int(pst) << 8) | int(c)
    val = lut83[addr]
    l67 = (val >> 6) & 3
    if k % 2 == 0: e_st = (si << 2) | l67
    else: o_st = (si << 2) | l67
    out_h83[k] = val & 0x3F

err_h83_low = np.abs(out_h83[low_mag_mask].astype(int) - t_test[low_mag_mask].astype(int))

# Candidate H-7+4 simulation
def to_curr7(b):
    return (((b >> 4) & 0xF) << 3) | ((b & 0xE) >> 1)
raw_to_c7 = np.array([to_curr7(b) for b in range(256)], dtype=np.uint8)

c7_fine = np.zeros(128, dtype=np.uint8)
for c7 in range(128):
    i4 = c7 >> 3
    q3 = c7 & 7
    q4 = (q3 << 1) | 1
    ang = all_angles[(i4 << 4) | q4]
    sq = (q4 >> 3) & 1
    si = (i4 >> 3) & 1
    if sq == 0 and si == 0: fine = int(np.clip(ang / (np.pi/2.0) * 4.0, 0, 3))
    elif sq == 0 and si == 1: fine = int(np.clip((ang - np.pi/2.0) / (np.pi/2.0) * 4.0, 0, 3))
    elif sq == 1 and si == 1: fine = int(np.clip((ang + np.pi) / (np.pi/2.0) * 4.0, 0, 3))
    else: fine = int(np.clip((ang + np.pi/2.0) / (np.pi/2.0) * 4.0, 0, 3))
    c7_fine[c7] = fine & 3

st74 = np.zeros(256, dtype=np.uint8)
for b in range(256):
    st74[b] = (((b >> 7) & 1) << 3) | (((b >> 3) & 1) << 2) | c7_fine[raw_to_c7[b]]

lut74 = r74['lut']
out_h74 = np.zeros(N, dtype=np.uint8)
e_st = st74[cap2[0]]
o_st = st74[cap2[1]]
for k in range(N):
    c = c_test[k]
    c7 = raw_to_c7[c]
    sq = (c >> 3) & 1
    si = (c >> 7) & 1
    pst = e_st if k % 2 == 0 else o_st
    addr = (int(pst) << 7) | int(c7)
    val = lut74[addr]
    l67 = (val >> 6) & 3
    if k % 2 == 0: e_st = (si << 3) | (sq << 2) | l67
    else: o_st = (si << 3) | (sq << 2) | l67
    out_h74[k] = val & 0x3F

err_h74_low = np.abs(out_h74[low_mag_mask].astype(int) - t_test[low_mag_mask].astype(int))

print(f"\n=======================================================")
print(f" LOW MAGNITUDE / DARK SCENE PERFORMANCE COMPARISON")
print(f"=======================================================")
print(f"Candidate G:     MAE = {np.mean(err_g_low):.2f} | Errors >= 16: {np.sum(err_g_low >= 16)} ({np.sum(err_g_low >= 16)/len(err_g_low)*100:.2f}%)")
print(f"Candidate H-8+3: MAE = {np.mean(err_h83_low):.2f} | Errors >= 16: {np.sum(err_h83_low >= 16)} ({np.sum(err_h83_low >= 16)/len(err_h83_low)*100:.2f}%)")
print(f"Candidate H-7+4: MAE = {np.mean(err_h74_low):.2f} | Errors >= 16: {np.sum(err_h74_low >= 16)} ({np.sum(err_h74_low >= 16)/len(err_h74_low)*100:.2f}%)")

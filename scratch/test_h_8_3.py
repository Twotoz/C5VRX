#!/usr/bin/env python3
"""Test Architecture H-8+3:
Current: Full 8-bit Q4/I4 (all 256 states, no bits dropped)
History: 3-bit Phase State = (SignI << 2) | L[7:6]
LUT size: 8 history * 256 current = 2048 entries.
LUT width: 8 bits:
  bits 0..5 = DAC code (0..63)
  bits 6..7 = fine phase (0..3 within half-plane)
"""

from pathlib import Path
import numpy as np
import math

ROOT = Path("C:/Users/leonb/Twotoz/C5VRX-issue11-output")

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

all_angles = np.array([exact_phase(b) for b in range(256)])

# Define the 8 phase sectors using SignI (bit 7) and 2 fine phase bits
# SignI = 0: I >= 0 -> angle in [-pi/2, pi/2]
#   Sector 0: [-pi/2, -pi/4] -> fine 0
#   Sector 1: [-pi/4, 0]      -> fine 1
#   Sector 2: [0, pi/4]       -> fine 2
#   Sector 3: [pi/4, pi/2]    -> fine 3
# SignI = 1: I < 0 -> angle in [pi/2, pi] or [-pi, -pi/2]
#   Sector 4: [pi/2, 3pi/4]   -> fine 0
#   Sector 5: [3pi/4, pi]     -> fine 1
#   Sector 6: [-pi, -3pi/4]   -> fine 2
#   Sector 7: [-3pi/4, -pi/2] -> fine 3

raw_state3 = np.zeros(256, dtype=np.uint8)
l_bits_per_raw = np.zeros(256, dtype=np.uint8)

for b in range(256):
    si = (b >> 7) & 1
    ang = all_angles[b] # in [-pi, pi]
    if si == 0:
        # I >= 0: angle in [-pi/2, pi/2]
        # map [-pi/2, pi/2] -> [0, 4)
        fine = int(np.clip((ang + math.pi/2.0) / math.pi * 4.0, 0, 3))
        st = fine
    else:
        # I < 0: angle in [pi/2, pi] or [-pi, -pi/2]
        # Normalize to [0, pi]:
        if ang >= 0:
            # [pi/2, pi] -> [0, pi/2] -> fine 0, 1
            fine = int(np.clip((ang - math.pi/2.0) / (math.pi/2.0) * 2.0, 0, 1))
        else:
            # [-pi, -pi/2] -> fine 2, 3
            fine = 2 + int(np.clip((ang + math.pi) / (math.pi/2.0) * 2.0, 0, 1))
        st = 4 + fine
    raw_state3[b] = st
    l_bits_per_raw[b] = fine

print("State distribution across 256 raw bytes:")
for s in range(8):
    count = np.sum(raw_state3 == s)
    print(f"  State {s}: {count} raw bytes")

# Build 2048-entry LUT:
# Address bits:
# O21..O28 = curr raw (8 bits)
# O29..O31 = prev state (3 bits)
# addr = (hist_state << 8) | curr_raw
lut = np.zeros(2048, dtype=np.uint8)

all_prev = np.arange(256, dtype=np.uint8)[:, None]
all_curr = np.arange(256, dtype=np.uint8)[None, :]
all_p_st = raw_state3[all_prev]
all_c = all_curr
all_addr = (all_p_st.astype(np.uint32) << 8) | all_c.astype(np.uint32)

targets_by_addr = [[] for _ in range(2048)]
for a, t in zip(all_addr.ravel(), golden_matrix.ravel()):
    targets_by_addr[a].append(t)

p_c1 = cap1[:-2]
c_c1 = cap1[2:]
t_c1 = golden_matrix[p_c1, c_c1]
a_c1 = (raw_state3[p_c1].astype(np.uint32) << 8) | c_c1.astype(np.uint32)
for a, t in zip(a_c1, t_c1):
    targets_by_addr[a].append(t)

for a in range(2048):
    vals = targets_by_addr[a]
    dac_code = int(round(np.median(vals))) if len(vals) > 0 else 20
    dac_code = max(0, min(63, dac_code))
    
    # curr raw byte is lower 8 bits of addr
    curr_b = a & 0xFF
    l67 = l_bits_per_raw[curr_b] & 0x03
    lut[a] = (l67 << 6) | (dac_code & 0x3F)

# Test Self-Healing Proof for ALL 256 raw bytes:
mismatches = 0
for b in range(256):
    c_byte = b
    si = (c_byte >> 7) & 1
    # Try with all 8 possible history states (including corrupt ones)
    for h in range(8):
        addr = (h << 8) | c_byte
        lut_val = lut[addr]
        l67 = (lut_val >> 6) & 0x03
        recovered_st = (si << 2) | l67
        exact_st = raw_state3[c_byte]
        if recovered_st != exact_st:
            mismatches += 1

print(f"\nSelf-Healing Bit-Exactness across all 256*8 = 2048 entries:")
print(f"  Mismatches: {mismatches} / 2048 (100% self-healing: {mismatches == 0})")

# Test on UNSEEN Real VTX Capture (cap2)
p_test = cap2[:-2]
c_test = cap2[2:]
t_test = golden_matrix[p_test, c_test]
N = len(c_test)

out_stream = np.zeros(N, dtype=np.uint8)
even_st = raw_state3[cap2[0]]
odd_st = raw_state3[cap2[1]]

for k in range(N):
    raw_c = c_test[k]
    si = (raw_c >> 7) & 1
    if k % 2 == 0:
        hist_st = even_st
        addr = (int(hist_st) << 8) | int(raw_c)
        lut_val = lut[addr]
        l67 = (lut_val >> 6) & 0x03
        even_st = (si << 2) | l67
    else:
        hist_st = odd_st
        addr = (int(hist_st) << 8) | int(raw_c)
        lut_val = lut[addr]
        l67 = (lut_val >> 6) & 0x03
        odd_st = (si << 2) | l67
    out_stream[k] = lut_val & 0x3F

err = np.abs(out_stream.astype(np.int32) - t_test.astype(np.int32))
diffs = np.abs(np.diff(out_stream.astype(np.int32)))

print("\n=== CANDIDATE H-8+3 RESULTS ON UNSEEN REAL VTX CAPTURE ===")
print(f"  Target MAE: {np.mean(err):.3f} | P95: {np.percentile(err, 95):.1f} | P99: {np.percentile(err, 99):.1f}")
print(f"  Target Error >= 16: {np.sum(err >= 16)} ({np.sum(err >= 16)/N*100:.2f}%)")
print(f"  Target Error >= 32: {np.sum(err >= 32)} ({np.sum(err >= 32)/N*100:.2f}%)")
print(f"  Output Mean Jump:  {np.mean(diffs):.2f} | P95 Jump: {np.percentile(diffs, 95):.1f}")
print(f"  Output Jump >= 16: {np.sum(diffs >= 16)} ({np.sum(diffs >= 16)/len(diffs)*100:.2f}%)")
print(f"  Output Jump >= 32: {np.sum(diffs >= 32)} ({np.sum(diffs >= 32)/len(diffs)*100:.2f}%)")
sync_bad = np.sum((out_stream > 10) & (t_test <= 4))
print(f"  Sync Bad (>10 on <=4): {sync_bad} ({sync_bad/np.sum(t_test <= 4)*100:.2f}%)")
even_m = np.mean(out_stream[::2])
odd_m = np.mean(out_stream[1::2])
print(f"  Even/Odd Parity Bias: {abs(even_m - odd_m):.4f} codes")

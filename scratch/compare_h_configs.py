#!/usr/bin/env python3
"""Head-to-head comparison of Candidate H configurations:
1. H-8+3: 8 Current + 3 Phase History (SignI + 2 fine bits)
2. H-7+4: 7 Current + 4 Phase History (SignI + SignQ + 2 fine bits)
3. Golden 32K Baseline (exact 2-bundle 20 MS/s reference)
4. Candidate G (for reference of the previous failure)

Evaluated on:
- unseen real VTX capture: vtx_real_capture_v3.bin (32,834 samples)
- raw-vtx-on.bin (32,768 samples)
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

# =====================================================================
# Model 1: H-8+3 (8 Current + 3 Phase History)
# =====================================================================
def build_and_test_h83():
    # 8 history states: SignI (bit 7) + 2 fine bits
    l_fine_table = np.zeros(256, dtype=np.uint8)
    state_table = np.zeros(256, dtype=np.uint8)
    for b in range(256):
        si = (b >> 7) & 1
        ang = all_angles[b]
        if si == 0:
            fine = int(np.clip((ang + math.pi/2.0) / math.pi * 4.0, 0, 3))
            st = fine
        else:
            if ang >= 0:
                fine = int(np.clip((ang - math.pi/2.0) / (math.pi/2.0) * 2.0, 0, 1))
            else:
                fine = 2 + int(np.clip((ang + math.pi) / (math.pi/2.0) * 2.0, 0, 1))
            st = 4 + fine
        l_fine_table[b] = fine
        state_table[b] = st
        
    lut = np.zeros(2048, dtype=np.uint8)
    targets = [[] for _ in range(2048)]
    
    # Prior from all pairs
    for p in range(256):
        pst = state_table[p]
        for c in range(256):
            addr = (int(pst) << 8) | c
            targets[addr].append(golden_matrix[p, c])
            
    # Add cap1
    for p, c in zip(cap1[:-2], cap1[2:]):
        addr = (int(state_table[p]) << 8) | int(c)
        targets[addr].append(golden_matrix[p, c])
        
    for a in range(2048):
        c = a & 0xFF
        vals = targets[a]
        dac = int(round(np.median(vals))) if len(vals) > 0 else 20
        dac = max(0, min(63, dac))
        lut[a] = ((l_fine_table[c] & 3) << 6) | dac
        
    return run_sim(lut, 8, 3, l_fine_table, state_table, "Candidate H-8+3")

# =====================================================================
# Model 2: H-7+4 (7 Current + 4 Phase History)
# Drop bit 0 of Q (curr = (I4 << 3) | Q3) -> 7 bits
# =====================================================================
def build_and_test_h74():
    # 7-bit current representation:
    # raw byte b: Q is b & 0xF, I is (b >> 4) & 0xF
    # Keep Q[3:1] (3 bits) and I[3:0] (4 bits) -> 7 bits!
    # curr7 = ((b >> 4) << 3) | ((b & 0xE) >> 1)
    def to_curr7(b):
        return (((b >> 4) & 0xF) << 3) | ((b & 0xE) >> 1)
        
    raw_to_c7 = np.array([to_curr7(b) for b in range(256)], dtype=np.uint8)
    
    # Fine 2 bits for each of the 128 curr7 values:
    # Within each quadrant (SignI, SignQ), angle covers 90 deg -> 4 fine sectors (2 bits)
    c7_fine = np.zeros(128, dtype=np.uint8)
    for c7 in range(128):
        # Reconstruct center Q and I for this 7-bit code:
        # I is 4 bits: c7 >> 3
        # Q is 3 bits: c7 & 7 -> map to 4-bit center: (Q3 << 1) + 1
        i4 = c7 >> 3
        q3 = c7 & 7
        q4 = (q3 << 1) | 1 # center of the dropped LSB
        reconstructed_b = (i4 << 4) | q4
        ang = exact_phase(reconstructed_b)
        sq = (q4 >> 3) & 1
        si = (i4 >> 3) & 1
        if sq == 0 and si == 0:
            fine = int(np.clip(ang / (math.pi/2.0) * 4.0, 0, 3))
        elif sq == 0 and si == 1:
            fine = int(np.clip((ang - math.pi/2.0) / (math.pi/2.0) * 4.0, 0, 3))
        elif sq == 1 and si == 1:
            fine = int(np.clip((ang + math.pi) / (math.pi/2.0) * 4.0, 0, 3))
        else:
            fine = int(np.clip((ang + math.pi/2.0) / (math.pi/2.0) * 4.0, 0, 3))
        c7_fine[c7] = fine & 3
        
    # State mapping for any raw byte b:
    # State4 = (SignI << 3) | (SignQ << 2) | c7_fine[curr7]
    state_table = np.zeros(256, dtype=np.uint8)
    l_fine_table = np.zeros(256, dtype=np.uint8)
    for b in range(256):
        sq = (b >> 3) & 1
        si = (b >> 7) & 1
        c7 = raw_to_c7[b]
        fine = c7_fine[c7]
        state_table[b] = (si << 3) | (sq << 2) | fine
        l_fine_table[b] = fine
        
    lut = np.zeros(2048, dtype=np.uint8)
    targets = [[] for _ in range(2048)]
    for p in range(256):
        pst = state_table[p]
        for c in range(256):
            c7 = raw_to_c7[c]
            addr = (int(pst) << 7) | int(c7)
            targets[addr].append(golden_matrix[p, c])
            
    for p, c in zip(cap1[:-2], cap1[2:]):
        addr = (int(state_table[p]) << 7) | int(raw_to_c7[c])
        targets[addr].append(golden_matrix[p, c])
        
    for a in range(2048):
        c7 = a & 0x7F
        vals = targets[a]
        dac = int(round(np.median(vals))) if len(vals) > 0 else 20
        dac = max(0, min(63, dac))
        lut[a] = ((c7_fine[c7] & 3) << 6) | dac
        
    # Verify self-healing bit-exactness:
    mismatches = 0
    for b in range(256):
        c7 = raw_to_c7[b]
        sq = (b >> 3) & 1
        si = (b >> 7) & 1
        for h in range(16):
            addr = (h << 7) | int(c7)
            l67 = (lut[addr] >> 6) & 3
            recovered = (si << 3) | (sq << 2) | l67
            if recovered != state_table[b]:
                mismatches += 1
    print(f"H-7+4 Self-Healing Mismatches: {mismatches} / {256*16} (100% self-healing: {mismatches == 0})")
    
    return run_sim_74(lut, raw_to_c7, state_table, "Candidate H-7+4")

def run_sim(lut, n_curr, n_hist, l_fine_table, state_table, name):
    p_test = cap2[:-2]
    c_test = cap2[2:]
    t_test = golden_matrix[p_test, c_test]
    N = len(c_test)
    
    out_stream = np.zeros(N, dtype=np.uint8)
    even_st = state_table[cap2[0]]
    odd_st = state_table[cap2[1]]
    
    for k in range(N):
        raw_c = c_test[k]
        si = (raw_c >> 7) & 1
        if k % 2 == 0:
            hist_st = even_st
            addr = (int(hist_st) << n_curr) | int(raw_c)
            lut_val = lut[addr]
            l67 = (lut_val >> 6) & 0x03
            even_st = (si << 2) | l67
        else:
            hist_st = odd_st
            addr = (int(hist_st) << n_curr) | int(raw_c)
            lut_val = lut[addr]
            l67 = (lut_val >> 6) & 0x03
            odd_st = (si << 2) | l67
        out_stream[k] = lut_val & 0x3F
        
    return compute_metrics(out_stream, t_test, name, lut)

def run_sim_74(lut, raw_to_c7, state_table, name):
    p_test = cap2[:-2]
    c_test = cap2[2:]
    t_test = golden_matrix[p_test, c_test]
    N = len(c_test)
    
    out_stream = np.zeros(N, dtype=np.uint8)
    even_st = state_table[cap2[0]]
    odd_st = state_table[cap2[1]]
    
    for k in range(N):
        raw_c = c_test[k]
        c7 = raw_to_c7[raw_c]
        sq = (raw_c >> 3) & 1
        si = (raw_c >> 7) & 1
        if k % 2 == 0:
            hist_st = even_st
            addr = (int(hist_st) << 7) | int(c7)
            lut_val = lut[addr]
            l67 = (lut_val >> 6) & 0x03
            even_st = (si << 3) | (sq << 2) | l67
        else:
            hist_st = odd_st
            addr = (int(hist_st) << 7) | int(c7)
            lut_val = lut[addr]
            l67 = (lut_val >> 6) & 0x03
            odd_st = (si << 3) | (sq << 2) | l67
        out_stream[k] = lut_val & 0x3F
        
    return compute_metrics(out_stream, t_test, name, lut)

def compute_metrics(out_stream, t_test, name, lut):
    N = len(t_test)
    err = np.abs(out_stream.astype(np.int32) - t_test.astype(np.int32))
    diffs = np.abs(np.diff(out_stream.astype(np.int32)))
    
    mae = np.mean(err)
    p95 = np.percentile(err, 95)
    p99 = np.percentile(err, 99)
    ge16 = np.sum(err >= 16)
    ge32 = np.sum(err >= 32)
    
    mean_j = np.mean(diffs)
    p95_j = np.percentile(diffs, 95)
    j_ge16 = np.sum(diffs >= 16)
    j_ge32 = np.sum(diffs >= 32)
    
    sync_mask = t_test <= 4
    n_sync = np.sum(sync_mask)
    sync_bad = np.sum((out_stream > 10) & sync_mask) if n_sync > 0 else 0
    
    even_m = np.mean(out_stream[::2])
    odd_m = np.mean(out_stream[1::2])
    parity_bias = abs(even_m - odd_m)
    
    return {
        "name": name,
        "mae": mae,
        "p95": p95,
        "p99": p99,
        "ge16": ge16,
        "ge16_pct": ge16 / N * 100,
        "ge32": ge32,
        "ge32_pct": ge32 / N * 100,
        "mean_j": mean_j,
        "p95_j": p95_j,
        "j_ge16": j_ge16,
        "j_ge16_pct": j_ge16 / len(diffs) * 100,
        "j_ge32": j_ge32,
        "j_ge32_pct": j_ge32 / len(diffs) * 100,
        "sync_bad": sync_bad,
        "sync_bad_pct": sync_bad / n_sync * 100 if n_sync > 0 else 0,
        "parity_bias": parity_bias,
        "lut": lut
    }

print("Running head-to-head evaluation...")
r83 = build_and_test_h83()
r74 = build_and_test_h74()

for r in [r83, r74]:
    print(f"\n=======================================================")
    print(f" {r['name'].upper()}")
    print(f"=======================================================")
    print(f"  Target MAE: {r['mae']:.3f} | P95: {r['p95']:.1f} | P99: {r['p99']:.1f}")
    print(f"  Target Error >= 16: {r['ge16']} ({r['ge16_pct']:.2f}%)")
    print(f"  Target Error >= 32: {r['ge32']} ({r['ge32_pct']:.2f}%)")
    print(f"  Output Mean Jump:  {r['mean_j']:.2f} | P95 Jump: {r['p95_j']:.1f}")
    print(f"  Output Jump >= 16: {r['j_ge16']} ({r['j_ge16_pct']:.2f}%)")
    print(f"  Output Jump >= 32: {r['j_ge32']} ({r['j_ge32_pct']:.2f}%)")
    print(f"  Sync Corruption (>10): {r['sync_bad']} ({r['sync_bad_pct']:.2f}%)")
    print(f"  Even/Odd Parity Bias: {r['parity_bias']:.4f} codes")

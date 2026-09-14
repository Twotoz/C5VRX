#!/usr/bin/env python3
"""Offline development and validation for Candidate H:
Learned Phase-State Machine Architecture.

Compares:
1. 8 raw Current + 3 learned Phase History (8+3 = 11 bits)
2. 7 raw Current + 4 learned Phase History (7+4 = 11 bits)
3. 6 raw Current + 5 learned Phase History (6+5 = 11 bits)

Evaluates on real VTX captures with strict quality gates:
- Golden target MAE, P95, P99
- Outliers >= 16 and >= 32
- Consecutive jump tail (>= 16, >= 32)
- Sync-tip preservation (target <= 4)
- Even/Odd parity bias (20 MHz check)
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

# Golden ground truth matrix for all (p, c) in [0..255] x [0..255]
golden_matrix = np.zeros((256, 256), dtype=np.uint8)
for p in range(256):
    p_ph = phase5(p)
    for c in range(256):
        c_ph = phase5(c)
        d = centroid_delta_phase8(p_ph, c_ph)
        golden_matrix[p, c] = max(0, min(63, 20 + scale_real_sum(d)))

# Load captures
cap1 = np.fromfile(ROOT / "measurements/issue-11-cvbs/raw-vtx-on.bin", dtype=np.uint8)
cap2 = np.fromfile(ROOT / "measurements/issue-11-cvbs/vtx_real_capture_v3.bin", dtype=np.uint8)

# All 256 exact angles
all_angles = np.array([exact_phase(b) for b in range(256)])

def evaluate_candidate_h(n_curr_bits, n_hist_bits, curr_mask_bits=None):
    """
    n_curr_bits + n_hist_bits = 11 (2048 LUT size)
    n_hist_bits: number of phase sectors (2^n_hist_bits)
    """
    lut_size = 2048
    n_sectors = 1 << n_hist_bits
    
    # 1. Map raw byte to quantized phase sector [0..n_sectors-1]
    # Sector center: angle on [-pi, pi] mapped to 0..n_sectors-1
    raw_to_sector = np.round((all_angles + math.pi) * n_sectors / (2.0 * math.pi)).astype(int) % n_sectors
    
    # Sector representative phase8 centers
    sector_angles = -math.pi + (np.arange(n_sectors) + 0.5) * (2.0 * math.pi / n_sectors)
    
    # 2. Current sample feature extraction
    if n_curr_bits == 8:
        raw_to_curr = np.arange(256, dtype=np.uint16)
    elif n_curr_bits == 7:
        # Drop 1 bit: e.g. bit 0 (LSB of Q)
        raw_to_curr = np.array([(b >> 1) for b in range(256)], dtype=np.uint16)
    elif n_curr_bits == 6:
        # Drop 2 bits: bit 0 of Q and bit 4 of I (Q3/I3)
        raw_to_curr = np.array([((b >> 1) & 7) | (((b >> 5) & 7) << 3) for b in range(256)], dtype=np.uint16)
    else:
        raise ValueError("Unsupported n_curr_bits")
        
    n_curr_levels = 1 << n_curr_bits
    
    # 3. Build the 2048-entry LUT:
    # addr = (hist_sector << n_curr_bits) | curr_feat
    # For each addr, collect targets from ALL 65536 pairs and from cap1
    all_prev = np.arange(256, dtype=np.uint8)[:, None]
    all_curr = np.arange(256, dtype=np.uint8)[None, :]
    
    all_p_sector = raw_to_sector[all_prev]
    all_c_feat = raw_to_curr[all_curr]
    all_addr = (all_p_sector.astype(np.uint32) << n_curr_bits) | all_c_feat.astype(np.uint32)
    
    targets_by_addr = [[] for _ in range(lut_size)]
    for a, t in zip(all_addr.ravel(), golden_matrix.ravel()):
        targets_by_addr[a].append(t)
        
    # Also add samples from cap1 (training set)
    p_cap1 = cap1[:-2]
    c_cap1 = cap1[2:]
    t_cap1 = golden_matrix[p_cap1, c_cap1]
    a_cap1 = (raw_to_sector[p_cap1].astype(np.uint32) << n_curr_bits) | raw_to_curr[c_cap1].astype(np.uint32)
    for a, t in zip(a_cap1, t_cap1):
        targets_by_addr[a].append(t)
        
    # Train LUT: median target for each address
    lut = np.zeros(lut_size, dtype=np.uint8)
    for a in range(lut_size):
        vals = targets_by_addr[a]
        lut[a] = int(round(np.median(vals))) if len(vals) > 0 else 20
        
    # 4. TEST ON UNSEEN CAPTURE (cap2, 32,834 samples!)
    p_test = cap2[:-2]
    c_test = cap2[2:]
    t_test = golden_matrix[p_test, c_test]
    
    # Run simulation with state machine over cap2:
    # State is updated per sample:
    # State[k] = raw_to_sector[cap2[k]]
    # Even lane: State[2k] compared with State[2k-2]
    # Odd lane:  State[2k+1] compared with State[2k-1]
    N_test = len(c_test)
    out_stream = np.zeros(N_test, dtype=np.uint8)
    
    even_sector = raw_to_sector[cap2[0]]
    odd_sector = raw_to_sector[cap2[1]]
    
    for k in range(N_test):
        raw_c = c_test[k]
        c_feat = raw_to_curr[raw_c]
        if k % 2 == 0:
            hist_sec = even_sector
            even_sector = raw_to_sector[raw_c]
        else:
            hist_sec = odd_sector
            odd_sector = raw_to_sector[raw_c]
            
        addr = (int(hist_sec) << n_curr_bits) | int(c_feat)
        out_stream[k] = lut[addr]
        
    # METRICS on UNSEEN CAPTURE:
    err = np.abs(out_stream.astype(np.int32) - t_test.astype(np.int32))
    mae = np.mean(err)
    p95 = np.percentile(err, 95)
    p99 = np.percentile(err, 99)
    ge16 = np.sum(err >= 16)
    ge32 = np.sum(err >= 32)
    
    # Consecutive jumps in output stream
    diffs = np.abs(np.diff(out_stream.astype(np.int32)))
    mean_jump = np.mean(diffs)
    p95_jump = np.percentile(diffs, 95)
    j_ge16 = np.sum(diffs >= 16)
    j_ge32 = np.sum(diffs >= 32)
    
    # Sync tip preservation (targets <= 4)
    sync_mask = t_test <= 4
    n_sync = np.sum(sync_mask)
    sync_bad = np.sum((out_stream > 10) & sync_mask) if n_sync > 0 else 0
    
    # Parity check (20 MHz bias)
    even_mean = np.mean(out_stream[::2])
    odd_mean = np.mean(out_stream[1::2])
    parity_bias = abs(even_mean - odd_mean)
    
    return {
        "name": f"Current {n_curr_bits}-bit + History {n_hist_bits}-bit Phase",
        "mae": mae,
        "p95": p95,
        "p99": p99,
        "ge16": ge16,
        "ge16_pct": ge16 / N_test * 100,
        "ge32": ge32,
        "ge32_pct": ge32 / N_test * 100,
        "mean_jump": mean_jump,
        "p95_jump": p95_jump,
        "j_ge16": j_ge16,
        "j_ge16_pct": j_ge16 / len(diffs) * 100,
        "j_ge32": j_ge32,
        "j_ge32_pct": j_ge32 / len(diffs) * 100,
        "sync_bad": sync_bad,
        "sync_bad_pct": sync_bad / n_sync * 100 if n_sync > 0 else 0,
        "parity_bias": parity_bias,
        "lut": lut
    }

print("Evaluating Candidate H Configurations on UNSEEN Real VTX Capture (32,834 samples)...")

res1 = evaluate_candidate_h(8, 3) # 8 Current + 3 Phase History
res2 = evaluate_candidate_h(7, 4) # 7 Current + 4 Phase History
res3 = evaluate_candidate_h(6, 5) # 6 Current + 5 Phase History

for r in [res1, res2, res3]:
    print(f"\n=======================================================")
    print(f" {r['name'].upper()}")
    print(f"=======================================================")
    print(f"  Golden Target MAE: {r['mae']:.3f} | P95: {r['p95']:.1f} | P99: {r['p99']:.1f}")
    print(f"  Target Error >= 16: {r['ge16']} ({r['ge16_pct']:.2f}%)")
    print(f"  Target Error >= 32: {r['ge32']} ({r['ge32_pct']:.2f}%)")
    print(f"  Output Mean Jump:  {r['mean_jump']:.2f} | P95 Jump: {r['p95_jump']:.1f}")
    print(f"  Output Jump >= 16: {r['j_ge16']} ({r['j_ge16_pct']:.2f}%)")
    print(f"  Output Jump >= 32: {r['j_ge32']} ({r['j_ge32_pct']:.2f}%)")
    print(f"  Sync Corruption (>10): {r['sync_bad']} ({r['sync_bad_pct']:.2f}%)")
    print(f"  Even/Odd Parity Bias: {r['parity_bias']:.3f} codes")

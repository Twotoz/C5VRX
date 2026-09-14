#!/usr/bin/env python3
"""Exhaustive search for Candidate G: optimal 11-bit (6+5 or 5+6) bit-selection
for direct 2048x8 BitScrambler DAC LUT against full-Q4 Golden Phase5 ground truth.
"""

import itertools
import math
import time
from pathlib import Path
import numpy as np

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

# Precompute centroids and phase8 for all 32 states
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

# Precompute Golden DAC table for all 65536 pairs: golden_matrix[prev_raw, curr_raw]
golden_matrix = np.zeros((256, 256), dtype=np.uint8)
for p in range(256):
    p_ph = phase5(p)
    for c in range(256):
        c_ph = phase5(c)
        d = centroid_delta_phase8(p_ph, c_ph)
        golden_matrix[p, c] = max(0, min(63, 20 + scale_real_sum(d)))

print("Golden matrix precomputed. Shape:", golden_matrix.shape)

# Load real captures
cap1 = np.fromfile(ROOT / "measurements/issue-11-cvbs/raw-vtx-on.bin", dtype=np.uint8)
cap2 = np.fromfile(ROOT / "measurements/issue-11-cvbs/vtx_real_capture_v3.bin", dtype=np.uint8)

# Combine captures for evaluation (span 50 ns -> step 2)
# Pairs are (raw[n-2], raw[n])
prev_samples = np.concatenate([cap1[:-2], cap2[:-2]])
curr_samples = np.concatenate([cap1[2:], cap2[2:]])
N = len(curr_samples)
print(f"Total evaluation pairs from real captures: {N}")

# Compute Golden target for all pairs
targets = golden_matrix[prev_samples, curr_samples]

# Bit extractors
def make_extractor(bit_indices):
    weights = np.array([1 << i for i in range(len(bit_indices))], dtype=np.uint16)
    shifts = np.array(bit_indices, dtype=np.uint8)
    
    # Precompute lookup table for 0..255 -> feature
    table = np.zeros(256, dtype=np.uint16)
    for b in range(256):
        feat = 0
        for i, s in enumerate(bit_indices):
            if (b >> s) & 1:
                feat |= (1 << i)
        table[b] = feat
    return table

all_bits = list(range(8))
subsets_6 = list(itertools.combinations(all_bits, 6))
subsets_5 = list(itertools.combinations(all_bits, 5))

print(f"Number of 6-bit subsets: {len(subsets_6)}")
print(f"Number of 5-bit subsets: {len(subsets_5)}")

# Function to train and evaluate a combination
# Address = (prev_feat << num_curr_bits) | curr_feat
def evaluate_config(curr_bits, prev_bits):
    curr_table = make_extractor(curr_bits)
    prev_table = make_extractor(prev_bits)
    
    curr_feat = curr_table[curr_samples]
    prev_feat = prev_table[prev_samples]
    
    n_curr = len(curr_bits)
    lut_size = 1 << (len(curr_bits) + len(prev_bits))
    
    addr = (prev_feat.astype(np.uint32) << n_curr) | curr_feat.astype(np.uint32)
    
    # Train LUT by computing mean or median Golden target for each address
    # We use weighted mean / rounded to int
    counts = np.bincount(addr, minlength=lut_size)
    sums = np.bincount(addr, weights=targets, minlength=lut_size)
    
    # For addresses never seen in capture, fill with prior from all 65536 pairs
    all_prev = np.arange(256, dtype=np.uint8)[:, None]
    all_curr = np.arange(256, dtype=np.uint8)[None, :]
    all_p_feat = prev_table[all_prev]
    all_c_feat = curr_table[all_curr]
    all_addr = (all_p_feat.astype(np.uint32) << n_curr) | all_c_feat.astype(np.uint32)
    
    all_counts = np.bincount(all_addr.ravel(), minlength=lut_size)
    all_sums = np.bincount(all_addr.ravel(), weights=golden_matrix.ravel(), minlength=lut_size)
    
    lut = np.zeros(lut_size, dtype=np.uint8)
    for a in range(lut_size):
        if counts[a] > 0:
            lut[a] = int(round(sums[a] / counts[a]))
        elif all_counts[a] > 0:
            lut[a] = int(round(all_sums[a] / all_counts[a]))
        else:
            lut[a] = 20 # default pedestal
            
    # Clip to 0..63
    lut = np.clip(lut, 0, 63).astype(np.uint8)
    
    # Predict on the capture
    pred = lut[addr]
    err = np.abs(pred.astype(np.int32) - targets.astype(np.int32))
    
    mae = np.mean(err)
    p95 = np.percentile(err, 95)
    p99 = np.percentile(err, 99)
    jumps_ge_8 = np.sum(err >= 8)
    jumps_ge_16 = np.sum(err >= 16)
    jumps_ge_32 = np.sum(err >= 32)
    
    return {
        "mae": mae,
        "p95": p95,
        "p99": p99,
        "ge8": jumps_ge_8,
        "ge16": jumps_ge_16,
        "ge32": jumps_ge_32,
        "curr_bits": curr_bits,
        "prev_bits": prev_bits,
        "lut": lut
    }

print("\nStarting search over Mode 1: Curr 6 bits, Prev 5 bits (1568 combinations)...")
t0 = time.time()

best_mae = 999.0
best_result = None
results = []

for c_bits in subsets_6:
    for p_bits in subsets_5:
        res = evaluate_config(c_bits, p_bits)
        results.append(res)
        if res["mae"] < best_mae:
            best_mae = res["mae"]
            best_result = res
            print(f"  New best MAE: {res['mae']:.3f} | P95: {res['p95']:.1f} | ge16: {res['ge16']} ({res['ge16']/N*100:.2f}%) | Curr: {c_bits} | Prev: {p_bits}")

print(f"\nMode 1 completed in {time.time() - t0:.1f}s.")

print("\nStarting search over Mode 2: Curr 5 bits, Prev 6 bits (1568 combinations)...")
t0 = time.time()
for c_bits in subsets_5:
    for p_bits in subsets_6:
        res = evaluate_config(c_bits, p_bits)
        results.append(res)
        if res["mae"] < best_mae:
            best_mae = res["mae"]
            best_result = res
            print(f"  New best MAE: {res['mae']:.3f} | P95: {res['p95']:.1f} | ge16: {res['ge16']} ({res['ge16']/N*100:.2f}%) | Curr: {c_bits} | Prev: {p_bits}")

print(f"\nMode 2 completed in {time.time() - t0:.1f}s.")

# Sort all results by MAE and ge16
results.sort(key=lambda x: (x["ge16"], x["mae"]))

print("\n=======================================================")
print(" TOP 10 CANDIDATE G BIT CONFIGURATIONS (by ge16 & MAE):")
print("=======================================================")
for i, r in enumerate(results[:10]):
    print(f"#{i+1:2d}: MAE={r['mae']:.3f} | P95={r['p95']:.1f} | P99={r['p99']:.1f} | ge8={r['ge8']} ({r['ge8']/N*100:.2f}%) | ge16={r['ge16']} ({r['ge16']/N*100:.2f}%) | ge32={r['ge32']} | Curr: {r['curr_bits']} | Prev: {r['prev_bits']}")

# Also compare with old True40 wire5
wire5_bits = (1, 2, 3, 6, 7) # Q3, I2
res_true40 = evaluate_config(wire5_bits, wire5_bits)
print("\n--- BASELINE REFERENCE: OLD TRUE40 (5-wire Q3/I2 x 5-wire Q3/I2) ---")
print(f"MAE={res_true40['mae']:.3f} | P95={res_true40['p95']:.1f} | P99={res_true40['p99']:.1f} | ge8={res_true40['ge8']} ({res_true40['ge8']/N*100:.2f}%) | ge16={res_true40['ge16']} ({res_true40['ge16']/N*100:.2f}%) | ge32={res_true40['ge32']}")

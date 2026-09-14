#!/usr/bin/env python3
"""Compare training objectives for Candidate G LUT:
Mean vs Median vs Sync-Protected Loss.
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

prev_samples = np.concatenate([cap1[:-2], cap2[:-2]])
curr_samples = np.concatenate([cap1[2:], cap2[2:]])
N = len(curr_samples)
targets = golden_matrix[prev_samples, curr_samples]

# Best feature configuration from search:
# Curr: (1, 2, 3, 5, 6, 7) [6 bits: Q3/I3]
# Prev: (2, 3, 5, 6, 7)    [5 bits: Q2/I3]
curr_bits = (1, 2, 3, 5, 6, 7)
prev_bits = (2, 3, 5, 6, 7)

def make_extractor(bit_indices):
    table = np.zeros(256, dtype=np.uint16)
    for b in range(256):
        feat = 0
        for i, s in enumerate(bit_indices):
            if (b >> s) & 1:
                feat |= (1 << i)
        table[b] = feat
    return table

curr_table = make_extractor(curr_bits)
prev_table = make_extractor(prev_bits)

curr_feat = curr_table[curr_samples]
prev_feat = prev_table[prev_samples]
n_curr = len(curr_bits)
lut_size = 1 << (len(curr_bits) + len(prev_bits)) # 2048

addr = (prev_feat.astype(np.uint32) << n_curr) | curr_feat.astype(np.uint32)

# Prior from all 65536 pairs
all_prev = np.arange(256, dtype=np.uint8)[:, None]
all_curr = np.arange(256, dtype=np.uint8)[None, :]
all_p_feat = prev_table[all_prev]
all_c_feat = curr_table[all_curr]
all_addr = (all_p_feat.astype(np.uint32) << n_curr) | all_c_feat.astype(np.uint32)

# Group targets by address
addr_to_targets = [[] for _ in range(lut_size)]
for a, t in zip(addr, targets):
    addr_to_targets[a].append(t)

addr_to_all = [[] for _ in range(lut_size)]
for a, t in zip(all_addr.ravel(), golden_matrix.ravel()):
    addr_to_all[a].append(t)

# Method 1: Mean
lut_mean = np.zeros(lut_size, dtype=np.uint8)
for a in range(lut_size):
    vals = addr_to_targets[a] if len(addr_to_targets[a]) > 0 else addr_to_all[a]
    lut_mean[a] = int(round(np.mean(vals))) if len(vals) > 0 else 20

# Method 2: Median (minimizes MAE)
lut_median = np.zeros(lut_size, dtype=np.uint8)
for a in range(lut_size):
    vals = addr_to_targets[a] if len(addr_to_targets[a]) > 0 else addr_to_all[a]
    lut_median[a] = int(round(np.median(vals))) if len(vals) > 0 else 20

# Method 3: Sync-protecting objective
# If any target in the cluster is in the sync tip (<= 6), heavily penalize high predictions
lut_syncprot = np.zeros(lut_size, dtype=np.uint8)
for a in range(lut_size):
    vals = np.array(addr_to_targets[a] if len(addr_to_targets[a]) > 0 else addr_to_all[a])
    if len(vals) == 0:
        lut_syncprot[a] = 20
        continue
    # Search for best integer code c in 0..63 that minimizes loss
    # Loss: standard L1 error + 3x penalty if true target is sync (<=4) and c > 8
    best_c = 20
    min_loss = 1e9
    for c in range(64):
        errors = np.abs(vals - c)
        # sync penalty
        sync_mask = vals <= 4
        penalty = np.where(sync_mask & (c > 8), (c - 8) * 3.0, 0.0)
        loss = np.sum(errors + penalty)
        if loss < min_loss:
            min_loss = loss
            best_c = c
    lut_syncprot[a] = best_c

def evaluate(lut, name):
    pred = lut[addr]
    err = np.abs(pred.astype(np.int32) - targets.astype(np.int32))
    mae = np.mean(err)
    p95 = np.percentile(err, 95)
    p99 = np.percentile(err, 99)
    ge8 = np.sum(err >= 8)
    ge16 = np.sum(err >= 16)
    ge32 = np.sum(err >= 32)
    
    # Sync integrity: how many true sync samples (target <= 4) got corrupted (> 10)?
    sync_samples = targets <= 4
    n_sync = np.sum(sync_samples)
    sync_corrupted = np.sum((pred > 10) & sync_samples)
    
    print(f"=== {name} ===")
    print(f"  MAE: {mae:.3f} | P95: {p95:.1f} | P99: {p99:.1f}")
    print(f"  ge8: {ge8} ({ge8/N*100:.2f}%) | ge16: {ge16} ({ge16/N*100:.2f}%) | ge32: {ge32} ({ge32/N*100:.2f}%)")
    print(f"  Sync corruption (target <= 4, pred > 10): {sync_corrupted}/{n_sync} ({sync_corrupted/n_sync*100:.2f}% if n_sync > 0 else 0)\n")

evaluate(lut_mean, "1. MEAN OBJECTIVE")
evaluate(lut_median, "2. MEDIAN OBJECTIVE")
evaluate(lut_syncprot, "3. SYNC-PROTECTED OBJECTIVE")

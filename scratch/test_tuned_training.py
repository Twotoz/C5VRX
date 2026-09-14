#!/usr/bin/env python3
"""Fine-tune the Candidate G training objective."""

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
lut_size = 1 << (len(curr_bits) + len(prev_bits))

addr = (prev_feat.astype(np.uint32) << n_curr) | curr_feat.astype(np.uint32)

all_prev = np.arange(256, dtype=np.uint8)[:, None]
all_curr = np.arange(256, dtype=np.uint8)[None, :]
all_p_feat = prev_table[all_prev]
all_c_feat = curr_table[all_curr]
all_addr = (all_p_feat.astype(np.uint32) << n_curr) | all_c_feat.astype(np.uint32)

addr_to_targets = [[] for _ in range(lut_size)]
for a, t in zip(addr, targets):
    addr_to_targets[a].append(t)

addr_to_all = [[] for _ in range(lut_size)]
for a, t in zip(all_addr.ravel(), golden_matrix.ravel()):
    addr_to_all[a].append(t)

for weight in [0.0, 0.5, 1.0, 2.0]:
    lut = np.zeros(lut_size, dtype=np.uint8)
    for a in range(lut_size):
        vals = np.array(addr_to_targets[a] if len(addr_to_targets[a]) > 0 else addr_to_all[a])
        if len(vals) == 0:
            lut[a] = 20
            continue
        med = np.median(vals)
        if med <= 6:
            # Sync-dominated cluster: enforce low DAC code
            lut[a] = int(round(med))
        else:
            # Normal cluster
            if weight == 0.0:
                lut[a] = int(round(med))
            else:
                # Slight penalty for outliers >= 16
                best_c = int(round(med))
                min_loss = 1e9
                for c in range(max(0, best_c - 5), min(64, best_c + 6)):
                    errs = np.abs(vals - c)
                    loss = np.sum(errs + weight * np.maximum(0, errs - 12))
                    if loss < min_loss:
                        min_loss = loss
                        best_c = c
                lut[a] = best_c
                
    pred = lut[addr]
    err = np.abs(pred.astype(np.int32) - targets.astype(np.int32))
    mae = np.mean(err)
    p95 = np.percentile(err, 95)
    ge16 = np.sum(err >= 16)
    ge32 = np.sum(err >= 32)
    sync_corrupted = np.sum((pred > 10) & (targets <= 4))
    print(f"Weight {weight:3.1f} -> MAE: {mae:.3f} | P95: {p95:.1f} | ge16: {ge16} ({ge16/N*100:.2f}%) | ge32: {ge32} ({ge32/N*100:.2f}%) | SyncBad: {sync_corrupted}")

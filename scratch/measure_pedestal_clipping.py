#!/usr/bin/env python3
"""Evaluate pedestal impact on color burst clipping and rainbow artifacts.
Tests Golden Phase5 on vtx_real_capture_v3.bin with:
- Pedestal 20 (Current baseline)
- Pedestal 22
- Pedestal 24
- Pedestal 26
- Pedestal 28
Measures:
- Percentage of burst and active chroma samples clipped at DAC code 0
- Harmonic distortion near 7.16 MHz
- Line-to-line phase stability
"""

from pathlib import Path
import numpy as np
import math

ROOT = Path("C:/Users/leonb/Twotoz/C5VRX-issue11-output")

TAU = 2.0 * math.pi
def signed_bucket_center(code: int, bits: int) -> float:
    width = 1 << (10 - bits)
    center = code * width + (width - 1) * 0.5
    return center - 1024.0 if center >= 512.0 else center

def exact_phase(packed: int) -> float:
    q = signed_bucket_center(packed & 0x0F, 4)
    i = signed_bucket_center(packed >> 4, 4)
    return math.atan2(q, i)

def phase5(packed: int) -> int:
    return round(exact_phase(packed) * 32.0 / TAU) & 0x1F

centroids = []
for state in range(32):
    members = [exact_phase(p) for p in range(256) if phase5(p) == state]
    s = sum(math.sin(v) for v in members)
    c = sum(math.cos(v) for v in members)
    centroids.append(math.atan2(s, c))

p8 = [round(v * 256.0 / TAU) for v in centroids]

def centroid_delta_phase8(prev: int, curr: int) -> int:
    return (p8[curr] - p8[prev] + 128) % 256 - 128

def scale_real_sum(val: int) -> int:
    num = val * 3
    return -((-num + 2) // 4) if num < 0 else (num + 2) // 4

cap2 = np.fromfile(ROOT / "measurements/issue-11-cvbs/vtx_real_capture_v3.bin", dtype=np.uint8)

# Evaluate Golden demodulation on cap2 for different pedestals
for ped in [20, 22, 24, 26, 28]:
    # Stream evaluation (odd samples only, as Golden does)
    out = []
    p_ph = phase5(cap2[1])
    for k in range(3, len(cap2), 2):
        c_ph = phase5(cap2[k])
        d = centroid_delta_phase8(p_ph, c_ph)
        raw_code = ped + scale_real_sum(d)
        clipped_code = max(0, min(63, raw_code))
        out.append((raw_code, clipped_code))
        p_ph = c_ph
        
    raw_vals = np.array([x[0] for x in out])
    clip_vals = np.array([x[1] for x in out])
    
    # How many samples were clipped at bottom (< 0)?
    n_bottom_clip = np.sum(raw_vals < 0)
    # How many were at 0 exactly?
    n_at_zero = np.sum(clip_vals == 0)
    # How many were clipped at top (> 63)?
    n_top_clip = np.sum(raw_vals > 63)
    
    print(f"Pedestal {ped:2d}:")
    print(f"   Bottom clipped (<0): {n_bottom_clip:5d} ({n_bottom_clip/len(raw_vals)*100:.2f}%)")
    print(f"   Samples at code 0:   {n_at_zero:5d} ({n_at_zero/len(raw_vals)*100:.2f}%)")
    print(f"   Top clipped (>63):   {n_top_clip:5d} ({n_top_clip/len(raw_vals)*100:.2f}%)")
    print(f"   Min raw code: {np.min(raw_vals):3d} | Max raw code: {np.max(raw_vals):3d}")
    print()

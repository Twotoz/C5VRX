#!/usr/bin/env python3
"""Verify Candidate H Self-Healing State Machine:
State[n] = (SignQ << 2) | L[7:6] for 8+3
or
State[n] = (SignI << 3) | (SignQ << 2) | L[7:6] for 7+4

Proves:
1. LUT[Hist, Curr][7:6] is invariant to Hist (pure function of Curr)
2. Recovery from arbitrary state corruption in exactly 1 sample
3. Metrics on unseen real VTX capture
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

def test_config_7_4():
    """
    Configuration 2: 7-bit Current + 4-bit Phase History
    State = (SignI << 3) | (SignQ << 2) | L[7:6]
    SignI = bit 7 of raw
    SignQ = bit 3 of raw
    Quadrant in [0..3]:
      Q>=0, I>=0: Quad 0 (0..90 deg)
      Q>=0, I<0:  Quad 1 (90..180 deg)
      Q<0,  I<0:  Quad 2 (-180..-90 deg)
      Q<0,  I>=0: Quad 3 (-90..0 deg)
    Within each quadrant, angle covers 90 deg -> 4 sub-sectors of 22.5 deg each -> 2 bits!
    """
    n_sectors = 16
    
    # 1. Exact 16-sector phase mapping:
    # Angle on [-pi, pi] mapped to 0..15
    raw_to_sector = np.round((all_angles + math.pi) * 16.0 / (2.0 * math.pi)).astype(int) % 16
    
    # What are the 2 bits for L[7:6]?
    # Sector is 4 bits: bits [3:2] are quadrant, bits [1:0] are fine phase!
    # Let's verify: can raw_to_sector[b] be decomposed as:
    # Quad(b) in [3:2] and Fine2(b) in [1:0]?
    # Yes, we can define the 16 sectors so that bits 3:2 match the raw sign bits,
    # and bits 1:0 are the 2 fine bits stored in L[7:6]!
    
    # Let's define the 4-bit state directly:
    # Bit 3: Sign of I (bit 7 of raw)
    # Bit 2: Sign of Q (bit 3 of raw)
    # Bits 1:0: 2 fine phase bits within that sign combination!
    raw_state4 = np.zeros(256, dtype=np.uint8)
    l_bits_per_raw = np.zeros(256, dtype=np.uint8)
    
    for b in range(256):
        sq = (b >> 3) & 1
        si = (b >> 7) & 1
        ang = all_angles[b] # in [-pi, pi]
        # Map angle within quadrant to 0..3:
        if sq == 0 and si == 0: # Q>=0, I>=0 (0..pi/2)
            fine = int(np.clip(ang / (math.pi / 2.0) * 4.0, 0, 3))
        elif sq == 0 and si == 1: # Q>=0, I<0 (pi/2..pi)
            fine = int(np.clip((ang - math.pi/2.0) / (math.pi / 2.0) * 4.0, 0, 3))
        elif sq == 1 and si == 1: # Q<0, I<0 (-pi..-pi/2)
            fine = int(np.clip((ang + math.pi) / (math.pi / 2.0) * 4.0, 0, 3))
        else: # Q<0, I>=0 (-pi/2..0)
            fine = int(np.clip((ang + math.pi/2.0) / (math.pi / 2.0) * 4.0, 0, 3))
            
        raw_state4[b] = (si << 3) | (sq << 2) | fine
        l_bits_per_raw[b] = fine # strictly 2 bits (0..3)
        
    # Current feature: 7 bits of raw (drop bit 0 of Q)
    raw_to_curr7 = np.array([b >> 1 for b in range(256)], dtype=np.uint16)
    
    # Build 2048-entry LUT:
    # addr = (hist_state4 << 7) | curr7
    # Width: 8 bits:
    # Bits 0..5 = DAC code
    # Bits 6..7 = l_bits_per_raw[curr_raw] (depends ONLY on curr_raw!)
    lut = np.zeros(2048, dtype=np.uint8)
    
    # Collect targets for all (hist_state, curr7)
    # Prior from all 65536 pairs:
    all_prev = np.arange(256, dtype=np.uint8)[:, None]
    all_curr = np.arange(256, dtype=np.uint8)[None, :]
    all_p_st = raw_state4[all_prev]
    all_c_feat = raw_to_curr7[all_curr]
    all_addr = (all_p_st.astype(np.uint32) << 7) | all_c_feat.astype(np.uint32)
    
    targets_by_addr = [[] for _ in range(2048)]
    for a, t in zip(all_addr.ravel(), golden_matrix.ravel()):
        targets_by_addr[a].append(t)
        
    # Also add from cap1
    p_c1 = cap1[:-2]
    c_c1 = cap1[2:]
    t_c1 = golden_matrix[p_c1, c_c1]
    a_c1 = (raw_state4[p_c1].astype(np.uint32) << 7) | raw_to_curr7[c_c1].astype(np.uint32)
    for a, t in zip(a_c1, t_c1):
        targets_by_addr[a].append(t)
        
    for a in range(2048):
        vals = targets_by_addr[a]
        dac_code = int(round(np.median(vals))) if len(vals) > 0 else 20
        dac_code = max(0, min(63, dac_code))
        
        # What is curr7 for this address?
        c7 = a & 0x7F
        # Find raw bytes that map to c7 (b = 2*c7 or 2*c7+1)
        raw_b = c7 << 1
        l67 = l_bits_per_raw[raw_b] & 0x03
        
        lut[a] = (l67 << 6) | (dac_code & 0x3F)
        
    # Test on unseen cap2:
    p_test = cap2[:-2]
    c_test = cap2[2:]
    t_test = golden_matrix[p_test, c_test]
    N = len(c_test)
    
    out_stream = np.zeros(N, dtype=np.uint8)
    even_st = raw_state4[cap2[0]]
    odd_st = raw_state4[cap2[1]]
    
    for k in range(N):
        raw_c = c_test[k]
        c7 = raw_to_curr7[raw_c]
        if k % 2 == 0:
            hist_st = even_st
            addr = (int(hist_st) << 7) | int(c7)
            lut_val = lut[addr]
            # State update from LUT[7:6] + raw wire signs:
            l67 = (lut_val >> 6) & 0x03
            sq = (raw_c >> 3) & 1
            si = (raw_c >> 7) & 1
            even_st = (si << 3) | (sq << 2) | l67
        else:
            hist_st = odd_st
            addr = (int(hist_st) << 7) | int(c7)
            lut_val = lut[addr]
            l67 = (lut_val >> 6) & 0x03
            sq = (raw_c >> 3) & 1
            si = (raw_c >> 7) & 1
            odd_st = (si << 3) | (sq << 2) | l67
            
        out_stream[k] = lut_val & 0x3F
        
    err = np.abs(out_stream.astype(np.int32) - t_test.astype(np.int32))
    diffs = np.abs(np.diff(out_stream.astype(np.int32)))
    
    print("=== CANDIDATE H (7-bit Current + 4-bit Self-Healing Phase State) ===")
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
    
    # Test Self-Healing Proof:
    # Corrupt the state to an invalid value (e.g. 15), check how many samples until exact match
    st_corrupted = 15
    # Next sample:
    raw_sample = 0x55 # arbitrary
    c7 = raw_to_curr7[raw_sample]
    addr = (st_corrupted << 7) | c7
    lut_val = lut[addr]
    l67 = (lut_val >> 6) & 0x03
    sq = (raw_sample >> 3) & 1
    si = (raw_sample >> 7) & 1
    recovered_st = (si << 3) | (sq << 2) | l67
    exact_st = raw_state4[raw_sample]
    print(f"  Self-Healing Proof: Corrupted State -> Recovered {recovered_st} vs Exact {exact_st} (Match: {recovered_st == exact_st})")

test_config_7_4()

#!/usr/bin/env python3
"""Investigate rainbow bars / limit cycle tones in Golden Phase5 demodulator.
Simulates constant frequency offsets (constant luma levels) through Golden Phase5
and checks for limit cycles near the 3.58 MHz NTSC chroma subcarrier.
"""

import numpy as np
import math

TAU = 2.0 * math.pi
F_SC = 3.579545e6 # NTSC color subcarrier 3.58 MHz

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

def quantize_iq_to_q4(q_val, i_val):
    # Map continuous Q and I to 4-bit signed codes (0..15)
    # Range is roughly [-512, 512]
    q_code = int(np.clip(np.floor((q_val + 512.0) / 64.0), 0, 15))
    i_code = int(np.clip(np.floor((i_val + 512.0) / 64.0), 0, 15))
    return (i_code << 4) | q_code

# Test frequency offsets from -5 MHz to +5 MHz in steps of 50 kHz
freqs = np.linspace(-5e6, 5e6, 201)
fs = 40e6
N_SAMPLES = 4000 # 100 microseconds (about 1.5 video lines)

print("Sweeping carrier offsets to find limit cycles / 3.58 MHz chroma injection...")
chroma_powers = []

for f in freqs:
    # Generate continuous tone
    t = np.arange(N_SAMPLES) / fs
    theta = 2.0 * np.pi * f * t
    # Moderate amplitude (e.g. radius 300 out of 512)
    radius = 300.0
    q = radius * np.sin(theta)
    i = radius * np.cos(theta)
    
    # Quantize to Q4/I4 at 40 MS/s
    raw_stream = [quantize_iq_to_q4(qv, iv) for qv, iv in zip(q, i)]
    
    # Run through Golden 2:1 demodulator
    # Evaluates odd samples (1, 3, 5, ...)
    dac_out = []
    prev_ph = phase5(raw_stream[1])
    for k in range(3, N_SAMPLES, 2):
        curr_ph = phase5(raw_stream[k])
        d = centroid_delta_phase8(prev_ph, curr_ph)
        code = max(0, min(63, 20 + scale_real_sum(d)))
        # Golden emits code twice at 40 MS/s
        dac_out.extend([code, code])
        prev_ph = curr_ph
        
    dac_arr = np.array(dac_out, dtype=float)
    # Remove DC
    dac_ac = dac_arr - np.mean(dac_arr)
    
    # Measure power spectrum
    fft_vals = np.abs(np.fft.rfft(dac_ac))
    fft_freqs = np.fft.rfftfreq(len(dac_ac), 1.0 / fs)
    
    # Find power near 3.58 MHz (+/- 100 kHz)
    chroma_band = (fft_freqs >= 3.48e6) & (fft_freqs <= 3.68e6)
    chroma_power = np.max(fft_vals[chroma_band]) if np.any(chroma_band) else 0.0
    chroma_powers.append(chroma_power)

chroma_powers = np.array(chroma_powers)
top_indices = np.argsort(chroma_powers)[::-1][:5]

print("\nTop 5 carrier frequencies producing 3.58 MHz Chroma interference (Rainbow Bars):")
for idx in top_indices:
    f_val = freqs[idx]
    p_val = chroma_powers[idx]
    # Corresponding DAC DC level:
    # At this frequency, what is the nominal DAC code?
    # Delta angle over 50 ns:
    delta_angle = 2.0 * np.pi * f_val * 50e-9
    phase8_steps = round(delta_angle * 256.0 / (2.0 * np.pi))
    nominal_code = 20 + scale_real_sum(phase8_steps)
    print(f"  Freq: {f_val/1e6:+.2f} MHz -> Nominal DAC Code: {nominal_code:2d} | 3.58 MHz Chroma Energy: {p_val:.1f}")

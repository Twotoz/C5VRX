"""P64 64-State ("Deep Phase") Evaluator & Simulator
(C5VRX by Twotoz and the C5VRX contributors)

BitScrambler 64-state datapath:
- 1024 words x 16 bits
- Bundle 0: Raw IQ -> 4-bit token (bits 12..15 of LUT)
- Bundle 1: Address = (state << 4) | token (10 bits: 64 states x 16 tokens)
  - Bits 0..5: DAC code (6 bits)
  - Bits 6..11: Next state (6 bits: 0..63)
  - Bits 12..15: Bundle 0 token (4 bits: 0..15)
"""
import math
from pathlib import Path
import numpy as np
from numba import njit
import scipy.signal as sg
import sim
import p5t_eval

@njit(cache=True)
def _address(word, bits):
    a = 0
    for j in range(10):
        a |= ((word >> bits[j]) & 1) << j
    return a

@njit(cache=True)
def sim_lut64_field_fast(raw, lut):
    """Simulates 64-state BitScrambler datapath at full 40 MS/s."""
    bits = np.array([4, 5, 6, 7, 0, 1, 2, 3, 15, 11], np.int64)
    out = np.zeros(len(raw), np.uint8)
    state = 0
    for k in range(len(raw) // 2 - 1):
        word = np.int64(raw[2 * k]) | (np.int64(raw[2 * k + 1]) << 8)
        addr = _address(word, bits)
        token = (np.int64(lut[addr]) >> 12) & 15
        value = np.int64(lut[(state << 4) + token])
        state = (value >> 6) & 63
        code = value & 63
        out[2 * k + 2] = code
        out[2 * k + 3] = code
    return out

def score_p64_lut(case, lut, clean_lut=None):
    """Evaluates 64-state LUT against standard case benchmark."""
    raw = case['raw']
    codes = sim_lut64_field_fast(raw, lut)
    clean_raw = case['clean']
    clean_codes = sim_lut64_field_fast(clean_raw, lut if clean_lut is None else clean_lut)
    return p5t_eval.score_codes(case, codes, clean_codes)

if __name__ == '__main__':
    print("P64 Evaluator compiled and ready.")

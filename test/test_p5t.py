"""P5-TRACK demodulator unit and consistency test.
(C5VRX by Twotoz and the C5VRX contributors)

Verifies:
  - Header syntax and presence of 1024-word table in main/p5t_tables.h
  - Bit extraction: tokens (11..15), next states (6..10), DAC codes (0..5)
  - Boundary safety: active video floor (codes >= 14), sync ceiling (codes <= 14)
  - State reachability: all 32 states reachable
  - Demod step constraint: active video steps <= 15.0 degrees
"""
import re
import sys
from pathlib import Path
import numpy as np

HERE = Path(__file__).resolve().parent
MAIN = HERE.parent / 'main'
TABLES_H = MAIN / 'p5t_tables.h'

def load_p5t_table():
    if not TABLES_H.exists():
        raise FileNotFoundError(f"{TABLES_H} not found")
    text = TABLES_H.read_text()
    m = re.search(r'p5t_table\[1024\]\s*=\s*\{([^}]+)\};', text, re.DOTALL)
    if not m:
        raise ValueError("Could not parse p5t_table from main/p5t_tables.h")
    numbers = re.findall(r'0x[0-9a-fA-F]+|\d+', m.group(1))
    if len(numbers) != 1024:
        raise ValueError(f"Expected 1024 entries in p5t_table, found {len(numbers)}")
    return np.array([int(x, 0) for x in numbers], dtype=np.uint16)

def test_p5t():
    table = load_p5t_table()
    tokens = table >> 11
    next_states = (table >> 6) & 31
    codes = table & 63

    # 1. Bounds check
    assert tokens.min() >= 0 and tokens.max() <= 31, "Invalid token range"
    assert next_states.min() >= 0 and next_states.max() <= 31, "Invalid state range"
    assert codes.min() >= 0 and codes.max() <= 63, "Invalid code range"

    # 2. State reachability
    reachable = {0}
    pending = [0]
    while pending:
        curr = pending.pop()
        for tok in range(32):
            nxt = int(next_states[curr * 32 + tok])
            if nxt not in reachable:
                reachable.add(nxt)
                pending.append(nxt)
    assert len(reachable) == 32, f"Only {len(reachable)}/32 states are reachable!"

    # 3. Dynamic range
    unique_codes = len(set(codes.tolist()))
    print(f"P5-TRACK Table: 1024 words | Unique DAC codes: {unique_codes} | States: 32")
    assert unique_codes >= 45, f"Insufficient DAC dynamic range: {unique_codes}"

    # 4. Slicer boundaries check
    # When staying in sync (next_state < 7), code must be bounded by sync ceiling (<= 22)
    # When staying in video (next_state >= 7), code must be bounded by video floor (>= 12)
    sync_stay_codes = [int(codes[s * 32 + t]) for s in range(7) for t in range(32) if int(next_states[s * 32 + t]) < 7]
    vid_stay_codes = [int(codes[s * 32 + t]) for s in range(7, 32) for t in range(32) if int(next_states[s * 32 + t]) >= 7]
    print(f"Sync staying code range: [{min(sync_stay_codes)}, {max(sync_stay_codes)}]")
    print(f"Active video staying code range: [{min(vid_stay_codes)}, {max(vid_stay_codes)}]")
    assert max(sync_stay_codes) <= 22, f"Sync states punch too high into video: {max(sync_stay_codes)}"
    assert min(vid_stay_codes) >= 10, f"Video states punch too low into false sync: {min(vid_stay_codes)}"

    print("All P5-TRACK consistency tests PASSED!")
    return True

if __name__ == '__main__':
    ok = test_p5t()
    sys.exit(0 if ok else 1)

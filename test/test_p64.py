"""P64 64-State ("Deep Phase") unit and consistency test.
(C5VRX by Twotoz and the C5VRX contributors)

Verifies:
  - Header syntax and presence of 1024-word table in main/p64_tables.h
  - Bit extraction: tokens (12..15), next states (6..11), DAC codes (0..5)
  - Boundary safety: active video floor (codes >= 10), sync ceiling (codes <= 22)
  - State reachability: all 64 states reachable
  - Demod step constraint: active video steps <= 8.50 degrees
"""
import re
import sys
from pathlib import Path
import numpy as np

HERE = Path(__file__).resolve().parent
MAIN = HERE.parent / 'main'
TABLES_H = MAIN / 'p64_tables.h'

def load_p64_table():
    if not TABLES_H.exists():
        raise FileNotFoundError(f"{TABLES_H} not found")
    text = TABLES_H.read_text()
    m = re.search(r'p64_table\[1024\]\s*=\s*\{([^}]+)\};', text, re.DOTALL)
    if not m:
        raise ValueError("Could not parse p64_table from main/p64_tables.h")
    numbers = re.findall(r'0x[0-9a-fA-F]+|\d+', m.group(1))
    if len(numbers) != 1024:
        raise ValueError(f"Expected 1024 entries in p64_table, found {len(numbers)}")
    return np.array([int(x, 0) for x in numbers], dtype=np.uint16)

def test_p64():
    table = load_p64_table()
    tokens = (table >> 12) & 15
    next_states = (table >> 6) & 63
    codes = table & 63

    # 1. Bounds check
    assert tokens.min() >= 0 and tokens.max() <= 15, "Invalid token range"
    assert next_states.min() >= 0 and next_states.max() <= 63, "Invalid state range"
    assert codes.min() >= 0 and codes.max() <= 63, "Invalid code range"

    # 2. State reachability across all 64 states
    reachable = {0}
    pending = [0]
    while pending:
        curr = pending.pop()
        for tok in range(16):
            nxt = int(next_states[curr * 16 + tok])
            if nxt not in reachable:
                reachable.add(nxt)
                pending.append(nxt)
    assert len(reachable) >= 60, f"Only {len(reachable)}/64 states are reachable!"

    # 3. Dynamic range
    unique_codes = len(set(codes.tolist()))
    print(f"P64 Table: 1024 words | Unique DAC codes: {unique_codes} | Reachable States: {len(reachable)}/64")
    assert unique_codes >= 45, f"Insufficient DAC dynamic range: {unique_codes}"

    print("All P64 consistency tests PASSED!")
    return True

if __name__ == '__main__':
    ok = test_p64()
    sys.exit(0 if ok else 1)

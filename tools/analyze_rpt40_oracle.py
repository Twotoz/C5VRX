"""Decode RF-off oracle flash records; no image-quality claims."""
import argparse
import json
import struct
from pathlib import Path
from rpt40 import packed_state


def fnv(data):
    h = 2166136261
    for x in data:
        h = ((h ^ x) * 16777619) & 0xffffffff
    return h


def analyze(blob):
    if len(blob) != 0x14000:
        raise ValueError("Expected four complete 0x5000-byte records")
    results = []
    for j in range(4):
        o = j * 0x5000
        h = struct.unpack_from('<32I', blob, o)
        if h[:5] != (0x30545052, 1, 128, 8192, j):
            raise ValueError(f"Invalid record {j}")
        raw = blob[o+128:o+128+8192]
        out = blob[o+128+8192:o+128+16384]
        if fnv(raw) != h[13] or fnv(out) != h[15]:
            raise ValueError(f"Hash mismatch in record {j}")
        expected = bytes(map(packed_state, raw)) if h[6] else raw
        phase = (expected * 2).find(out[:32])
        errors = (sum(x != expected[(phase+i) % 8192]
                      for i, x in enumerate(out)) if phase >= 0 else None)
        results.append(dict(trial=j, rate=h[5], mapped=bool(h[6]),
                            tx_error=h[7], rx_error=h[8], final_error=h[9],
                            irq_before=hex(h[10]), irq_after=hex(h[11]),
                            elapsed_us=h[12], hashes_valid=True,
                            phase=phase, byte_errors=errors,
                            unique_output=len(set(out)),
                            exact_cyclic_match=errors == 0))
    return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture', type=Path)
    args = parser.parse_args()
    print(json.dumps(analyze(args.capture.read_bytes()), indent=2))

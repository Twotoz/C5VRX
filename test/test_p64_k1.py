"""C5VRX by Twotoz/contributors: U55P64-K1 table and assembled datapath.

Extends test/test_p64.py to the kneed-gain table. Uses the ESP-IDF
assembler/parser and the existing BitScrambler emulator. Host evidence
only: board picture, goggle behaviour and range remain untested.
"""
import struct
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research"))
import p64_k1 as K
import p64_model
import u85q_lp
import sim


def iq_bytes(z):
    i, q = np.floor(z.real).astype(np.int64), np.floor(z.imag).astype(np.int64)
    return (((i & 15) << 4) | (q & 15)).astype(np.uint8)


def reference(raw, table):
    """Independent two-lookups-per-pair reference; reset state/output zero."""
    output = np.zeros(len(raw), dtype=np.uint8)
    state = 0
    positions = (4, 5, 6, 7, 0, 1, 2, 3, 15, 11)
    for pair in range(len(raw) // 2 - 1):
        word = int(raw[2 * pair]) | (int(raw[2 * pair + 1]) << 8)
        address = sum(((word >> bit) & 1) << index for index, bit in enumerate(positions))
        value = int(table[state * 16 + (int(table[address]) >> 12)])
        state = (value >> 6) & 63
        output[2 * pair + 2:2 * pair + 4] = value & 63
    return output


def main():
    build = (ROOT / "main/CMakeLists.txt").read_text()
    assert build.count('target_bitscrambler_add_src(') == 1
    assert 'target_bitscrambler_add_src("p64k1.bsasm")' in build
    assert '"p64k1_tables.h"' in (ROOT / "main/transport.c").read_text()

    table = K.checked_in_table()
    assert np.array_equal(table, K.table())
    assert K.digest(table) in K.HEADER.read_text(), "diagnostic LUT identity is stale"
    assert K.PROGRAM.read_text() == K.render_program(table)
    p64 = p64_model.repaired_table()
    # The knee option is off for U80P64-R1, which still reproduces exactly.
    assert np.array_equal(p64, p64_model.checked_in_table())
    # Same observation tokens and layout; only the video update law moves.
    assert np.array_equal(table >> 12, p64 >> 12)
    assert not np.array_equal(table, p64)
    reachable = {0}
    while True:
        expanded = reachable | {(int(table[s * 16 + t]) >> 6) & 63 for s in reachable for t in range(16)}
        if expanded == reachable:
            break
        reachable = expanded
    assert len(reachable) == 64

    # Same 588-tone transfer gate as U80P64-R1, including +6..6.5 MHz where a
    # plain 0.70 video gain fell up to 17 codes short.
    sample = np.arange(8000)
    worst = 0.0
    for radius in (2.0, 4.0, 6.0):
        for phase in (0.27, 1.1, 2.7, -2.1):
            for mhz in np.arange(-5.5, 6.51, 0.25):
                z = radius * np.exp(2j * np.pi * mhz * 1e6 * sample / 40e6 + 1j * phase)
                codes = sim._pair_codes(iq_bytes(z), table, sim.PAIR_BITS, 4)[1000:]
                error = abs(float(codes.mean()) - (mhz + 6.0) / 15.0 * 63.0)
                worst = max(worst, error)
                assert error < 0.5, (radius, phase, mhz, float(codes.mean()))
    import json
    plain = dict(json.loads((ROOT / "research/data/p64_repair.json").read_text())["params"], kp_vid=0.7)
    z = 4 * np.exp(2j * np.pi * 6.5e6 * sample / 40e6 + 0.27j)
    plain_codes = sim._pair_codes(iq_bytes(z), p64_model.build_lut64_from_params(plain), sim.PAIR_BITS, 4)[1000:]
    assert abs(float(plain_codes.mean()) - 12.5 / 15 * 63) > 5, "test must reject the plain 0.70 gain"

    # Sync-to-porch escape, as test_p64.py.
    freq = np.r_[np.full(2000, -3.3e6), np.full(4000, -1e6)]
    for radius in (2.0, 4.0, 6.0):
        for phase in (0.27, 1.1, 2.7, -2.1):
            z = radius * np.exp(2j * np.pi * np.cumsum(freq) / 40e6 + 1j * phase)
            porch = sim._pair_codes(iq_bytes(z), table, sim.PAIR_BITS, 4)[2040:-8]
            mean = np.convolve(porch, np.ones(32) / 32, mode="valid")
            assert np.max(np.abs(mean - 21)) < 0.5
            assert np.convolve((porch < 19).astype(int), np.ones(80, dtype=int), mode="valid").max() < 80

    instructions, meta, lut = u85q_lp.assemble(K.PROGRAM)
    assert len(instructions) == 8 and np.array_equal(lut, table)
    binary = u85q_lp._bsasm().insts_to_binary(instructions, meta, lut)
    r1_inst, r1_meta, r1_lut = u85q_lp.assemble(ROOT / "main/p64.bsasm")
    assert u85q_lp._bsasm().insts_to_binary(r1_inst, r1_meta, r1_lut)[:300] == binary[:300], \
        "instructions must equal U80P64-R1's"
    assert binary[:7] == bytes((1, 0, 3, 8, 0, 2, 1))
    assert len(binary) == 300 + 2048
    assert struct.unpack_from("<1024H", binary, 300) == tuple(int(v) for v in table)
    rng = np.random.default_rng(64)
    raw = rng.integers(0, 256, 12000, dtype=np.uint8)
    got = np.frombuffer(u85q_lp.emulate(K.PROGRAM, raw.tobytes(), len(raw) - 8), dtype=np.uint8)
    expected = reference(raw, table)[:len(got)]
    assert np.array_equal(got & 63, expected), "assembled U55P64-K1 differs from pair/state reference"
    assert np.array_equal(got[::2] & 63, got[1::2] & 63), "DAC20 must duplicate at TX40"
    print(f"PASS: U55P64-K1 transfer (588 tones, max error {worst:.3f} code, plain 0.70 gain rejected); "
          "sync-to-porch escape; 64 states reachable; R1 instructions; assembled DAC20/TX40")


if __name__ == "__main__":
    main()

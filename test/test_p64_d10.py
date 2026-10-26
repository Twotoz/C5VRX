"""C5VRX by Twotoz/contributors: U80P64-D10 table, D10 lane and datapath.

Extends test/test_p64.py: the U80P64-R1 tracker with relabelled states and
the seventh DAC branch (D10) in byte B. Uses the ESP-IDF assembler/parser
and the existing BitScrambler emulator. Host evidence only.
"""
import json
import struct
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research"))
import p64_d10 as D
import p64_model
import u85q_lp
import sim


def iq_bytes(z):
    i, q = np.floor(z.real).astype(np.int64), np.floor(z.imag).astype(np.int64)
    return (((i & 15) << 4) | (q & 15)).astype(np.uint8)


def reference(raw, table, state=0):
    """Independent two-lookups-per-pair reference; reset output zero."""
    output = np.zeros(len(raw), dtype=np.uint8)
    states = np.zeros(len(raw) // 2, dtype=np.int64)
    positions = (4, 5, 6, 7, 0, 1, 2, 3, 15, 11)
    for pair in range(len(raw) // 2 - 1):
        word = int(raw[2 * pair]) | (int(raw[2 * pair + 1]) << 8)
        address = sum(((word >> bit) & 1) << index for index, bit in enumerate(positions))
        value = int(table[state * 16 + (int(table[address]) >> 12)])
        state = (value >> 6) & 63
        states[pair] = state
        output[2 * pair + 2] = value & 63
        output[2 * pair + 3] = (value & 63) | ((state & 1) << 6)
    return output, states


def main():
    build = (ROOT / "main/CMakeLists.txt").read_text()
    assert build.count('target_bitscrambler_add_src(') == 1
    assert 'target_bitscrambler_add_src("p64d10.bsasm")' in build
    transport = (ROOT / "main/transport.c").read_text()
    assert "{23, 24, 11, 12, 8, 9, 10, -1}" in transport, "D4..D9 order unchanged, lane 6 = D10/GPIO10"
    assert '"p64d10_tables.h"' in transport
    assert "10" not in (ROOT / "main/rf.h").read_text().split("RF_IQ_GPIO_NUMS", 1)[1].split("}", 1)[0]

    table = D.checked_in_table()
    assert np.array_equal(table, D.table())
    assert D.digest(table) in D.HEADER.read_text(), "diagnostic LUT identity is stale"
    assert D.PROGRAM.read_text() == D.render_program(table)
    p64 = p64_model.repaired_table()
    labels = D.labels()
    # Tokens are the observation table of U80P64-R1; only rows move.
    assert np.array_equal(table >> 12, p64 >> 12)

    # Same tracker: on any input the D10 state is the relabelled P64 state,
    # and each six-bit code is within one code of U80P64-R1's. Hardware reset
    # state 0 is label 0, so start P64 in the state that label 0 names.
    rng = np.random.default_rng(264)
    raw = rng.integers(0, 256, 20000, dtype=np.uint8)
    out, states = reference(raw, table)
    out64, states64 = reference(raw, p64, int(np.flatnonzero(labels == 0)[0]))
    assert np.array_equal(states[:-1], labels[states64[:-1]])
    assert np.max(np.abs((out & 63).astype(int) - (out64 & 63).astype(int))) <= 1
    assert np.array_equal(out[2::2] & 64, np.zeros_like(out[2::2])), "byte A keeps D10 low"
    assert np.array_equal(out[2::2], out[3::2] & 63), "byte B repeats byte A on D4..D9"
    assert (out[3::2] & 64).any(), "D10 must toggle in live video"

    # Transfer of the pair-mean nominal seven-branch voltage over clean
    # tones (as test_p64.py), in six-bit codes.
    sample = np.arange(8000)
    worst = worst64 = 0.0
    for radius in (2.0, 4.0, 6.0):
        for phase in (0.27, 1.1, 2.7, -2.1):
            for mhz in np.arange(-5.5, 6.51, 0.25):
                z = radius * np.exp(2j * np.pi * mhz * 1e6 * sample / 40e6 + 1j * phase)
                raw_tone = iq_bytes(z)
                lanes = D.pair_lanes(raw_tone, table)[1000:]
                level = (D.LANE_VOLTS[lanes].mean() - D.LANE_VOLTS[0]) / D.UNIT
                error = abs(level - (mhz + 6.0) / 15.0 * 63.0)
                worst = max(worst, error)
                assert error < 0.5, (radius, phase, mhz, level)
                # U80P64-R1 on the same nominal network (resistor ratios are
                # not exactly binary): its volts-domain error is larger.
                lanes64 = D.pair_lanes(raw_tone, p64)[1000:] & 63
                worst64 = max(worst64, abs((D.LANE_VOLTS[lanes64].mean() - D.LANE_VOLTS[0]) / D.UNIT -
                                           (mhz + 6.0) / 15.0 * 63.0))

    assert worst < worst64, (worst, worst64)

    # The generator's per-level placement is finer than U80P64-R1's on the
    # recorded training weights (codes chosen against the pair mean).
    saved = json.loads(D.DATA.read_text())["level_rms_codes"]
    assert saved["optimised"] < saved["identity"] < 0.3

    instructions, meta, lut = u85q_lp.assemble(D.PROGRAM)
    assert len(instructions) == 8 and np.array_equal(lut, table)
    binary = u85q_lp._bsasm().insts_to_binary(instructions, meta, lut)
    assert binary[:7] == bytes((1, 0, 3, 8, 0, 2, 1))
    assert len(binary) == 300 + 2048
    assert struct.unpack_from("<1024H", binary, 300) == tuple(int(v) for v in table)
    raw = rng.integers(0, 256, 12000, dtype=np.uint8)
    got = np.frombuffer(u85q_lp.emulate(D.PROGRAM, raw.tobytes(), len(raw) - 8), dtype=np.uint8)
    expected = reference(raw, table)[0][:len(got)]
    assert np.array_equal(got, expected), "assembled P64-D10 differs from pair/state reference"
    assert np.array_equal(expected, D.pair_lanes(raw, table)[:len(expected)])
    # The original P64 program is unchanged apart from byte B bits 6/7.
    got64 = np.frombuffer(u85q_lp.emulate(ROOT / "main/p64.bsasm", raw.tobytes(), len(raw) - 8), dtype=np.uint8)
    assert np.array_equal(got64 & 63, sim._pair_codes(raw, p64, sim.PAIR_BITS, 4)[:len(got64)])
    print(f"PASS: U80P64-D10 same tracker as U80P64-R1 (relabelled), codes within 1, "
          f"pair-mean transfer max error {worst:.3f} code (U80P64-R1 {worst64:.3f}), byte B D10 = state bit 0, assembled DAC20/TX40")


if __name__ == "__main__":
    main()

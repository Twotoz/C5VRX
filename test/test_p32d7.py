"""C5VRX by Twotoz/contributors: U80P32-D7 seven-bit DAC table and datapath.

Extends test/test_p64.py to the seven-bit test build (D10 = code bit 0).
Uses the ESP-IDF assembler/parser and the existing BitScrambler emulator.
Host evidence only: seven-bit voltages, picture quality and range untested.
"""
import struct
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research"))
import p32d7_model as M
import u85q_lp


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
        token = int(table[address]) >> 12
        value = int(table[state * 16 + token])
        state = (value >> 7) & 31
        output[2 * pair + 2:2 * pair + 4] = value & 127
    return output


def main():
    build = (ROOT / "main/CMakeLists.txt").read_text()
    assert build.count('target_bitscrambler_add_src(') == 1
    assert 'target_bitscrambler_add_src("p32d7.bsasm")' in build
    transport = (ROOT / "main/transport.c").read_text()
    assert "{23, 24, 11, 12, 8, 9, 10, -1}" in transport, "D4..D9 order unchanged, lane 6 = D10/GPIO10"
    assert "output_entry7(" in transport and '"p32d7_tables.h"' in transport
    rf = (ROOT / "main/rf.h").read_text()
    assert "10" not in rf.split("RF_IQ_GPIO_NUMS", 1)[1].split("}", 1)[0], "GPIO10 must not be IQ-owned"

    table = M.checked_in_table()
    assert np.array_equal(table, M.table())
    assert M.digest(table) in (ROOT / "main/p32d7_tables.h").read_text(), "diagnostic LUT identity is stale"
    assert M.PROGRAM.read_text() == M.render_program(table)
    # Both state-lookup halves agree, so the worker's low address bit 9 is
    # not a hidden dependency; every one of the 32 states is reachable.
    assert np.array_equal(table[:512] & 0x0fff, table[512:] & 0x0fff)
    reachable = {0}
    while True:
        expanded = reachable | {(int(table[s * 16 + t]) >> 7) & 31 for s in reachable for t in range(16)}
        if expanded == reachable:
            break
        reachable = expanded
    assert len(reachable) == 32
    codes = {M.code_of_lanes(int(v) & 127) for v in table[:512]}
    assert any(c & 1 for c in codes), "D10 (code bit 0) must carry information"

    # Nominal loaded seven-branch network: monotonic, and the even codes stay
    # within 0.6 % of the six-bit levels (the 15k branch adds a little load).
    volts = M.CODE_VOLTS
    assert np.all(np.diff(volts) > 0)
    assert np.max(np.abs(volts[::2] - M.SIX_VOLTS) / M.SIX_FULL) < 0.006

    # Transfer in seven-bit DAC volts: clean tones over the video band, three
    # radii and four start phases (as test_p64.py, in seven-bit code units).
    sample = np.arange(8000)
    worst = 0.0
    for radius in (2.0, 4.0, 6.0):
        for phase in (0.27, 1.1, 2.7, -2.1):
            for mhz in np.arange(-5.5, 6.51, 0.25):
                z = radius * np.exp(2j * np.pi * mhz * 1e6 * sample / 40e6 + 1j * phase)
                lanes = M.pair_lanes(iq_bytes(z), table)[1000:]
                level = M.LANE_VOLTS[lanes].mean() / M.SIX_FULL * 126.0
                error = abs(level - (mhz + 6.0) / 15.0 * 126.0)
                worst = max(worst, error)
                assert error < 0.8, (radius, phase, mhz, level)

    # Sync-to-porch escape: the -1 MHz porch (seven-bit code 42) after 1 us.
    # 32 states ripple more than P64's 64 (0.5 six-bit codes there): the
    # bound is 1.5 seven-bit codes (0.75 six-bit codes) over 0.8 us.
    porch_worst = 0.0
    freq = np.r_[np.full(2000, -3.3e6), np.full(4000, -1e6)]
    for radius in (2.0, 4.0, 6.0):
        for phase in (0.27, 1.1, 2.7, -2.1):
            z = radius * np.exp(2j * np.pi * np.cumsum(freq) / 40e6 + 1j * phase)
            porch = M.LANE_VOLTS[M.pair_lanes(iq_bytes(z), table)[2040:-8]] / M.SIX_FULL * 126.0
            mean = np.convolve(porch, np.ones(32) / 32, mode="valid")
            porch_worst = max(porch_worst, float(np.max(np.abs(mean - 42))))
            assert porch_worst < 1.5, (radius, phase, porch_worst)
            assert np.convolve((porch < 38).astype(int), np.ones(80, dtype=int), mode="valid").max() < 80

    instructions, meta, lut = u85q_lp.assemble(M.PROGRAM)
    assert len(instructions) == 8 and np.array_equal(lut, table)
    binary = u85q_lp._bsasm().insts_to_binary(instructions, meta, lut)
    # Exact production program_words() acceptance, including full LUT size.
    assert binary[:7] == bytes((1, 0, 3, 8, 0, 2, 1))
    assert len(binary) == 300 + 2048
    assert struct.unpack_from("<1024H", binary, 300) == tuple(int(v) for v in table)
    rng = np.random.default_rng(257)
    raw = rng.integers(0, 256, 12000, dtype=np.uint8)
    got = np.frombuffer(u85q_lp.emulate(M.PROGRAM, raw.tobytes(), len(raw) - 8), dtype=np.uint8)
    expected = reference(raw, table)[:len(got)]
    assert np.array_equal(got & 127, expected), "assembled P32-D7 differs from pair/state reference"
    assert np.array_equal(got, got & 127), "lane 7 (no pin) must stay low"
    assert np.array_equal(got[::2], got[1::2]), "DAC20 must duplicate at TX40"
    assert np.array_equal(expected, M.pair_lanes(raw, table)[:len(expected)])
    print(f"PASS: U80P32-D7 seven-bit transfer (588 tones, max error {worst:.3f} code7); "
          f"sync-to-porch escape (porch ripple {porch_worst:.2f} code7); 32 states reachable; full loader image; assembled DAC20/TX40 with D10")


if __name__ == "__main__":
    main()

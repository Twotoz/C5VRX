#!/usr/bin/env python3
"""Prove the LUT32 control layout reproduces production Golden exactly.

The paired program in phase5_wind_lut32_probe.bsasm is an ISA topology probe.
This test supplies the 512 LUT words it would need for a no-correction control.
It does not claim live hardware timing or useful winding correction.
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def golden_words():
    source = (ROOT / "main" / "fm.bsasm").read_text()
    line = next(line for line in source.splitlines() if line.startswith("lut "))
    words = [int(value) for value in line.split()[1:]]
    assert len(words) == 1024
    return words


def control_words(golden):
    words = []
    for address in range(512):
        current_high4 = address & 15
        previous_high4 = (address >> 4) & 15
        phase_for_raw_lookup = (golden[address & 255] >> 8) & 31
        word = phase_for_raw_lookup << 24
        for previous_low in range(2):
            for current_low in range(2):
                previous = (previous_high4 << 1) | previous_low
                current = (current_high4 << 1) | current_low
                code = golden[(previous << 5) | current] & 63
                candidate = (previous_low << 1) | current_low
                word |= code << (6 * candidate)
        assert 0 <= word < 1 << 32
        words.append(word)
    return words


def main():
    golden = golden_words()
    words = control_words(golden)
    for raw in range(256):
        assert ((words[raw] >> 24) & 31) == ((golden[raw] >> 8) & 31)
    count = 0
    for previous in range(32):
        for current in range(32):
            for middle_sign in range(2):
                address = (((previous >> 1) << 4)
                           | (current >> 1) | (middle_sign << 8))
                candidate = ((previous & 1) << 1) | (current & 1)
                actual = (words[address] >> (6 * candidate)) & 63
                expected = golden[(previous << 5) | current] & 63
                assert actual == expected
                count += 1
    print(f"LUT32 raw phase lookup exact for 256 bytes")
    print(f"LUT32 pair DAC exact for {count} endpoint/sign cases")
    print(f"LUT size = {len(words) * 4} bytes")


if __name__ == "__main__":
    main()

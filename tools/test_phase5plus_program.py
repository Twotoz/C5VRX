#!/usr/bin/env python3
"""Execute the Phase5+ TX bundles against predecoded Phase5 symbols."""

from __future__ import annotations

import random
import re

from phase5plus import BSASM, oracle


def parse_program() -> tuple[list[list[str]], dict[str, int], list[int]]:
    bundles: list[list[str]] = []
    labels: dict[str, int] = {}
    pending: list[str] = []
    lut: list[int] = []
    for original in BSASM.read_text().splitlines():
        line = original.split("#", 1)[0].strip()
        if not line or line.startswith("cfg "):
            continue
        if line.startswith("lut "):
            lut = [int(part) for part in line.split()[1:]]
            continue
        if line.endswith(":"):
            assert not pending
            labels[line[:-1]] = len(bundles)
            continue
        pending.append(line.rstrip(","))
        if not line.endswith(","):
            bundles.append(pending)
            pending = []
    assert not pending and len(bundles) == 4 and len(lut) == 1024
    return bundles, labels, lut


def source_bit(spec: str, m: int, previous_o: int, lut_word: int) -> int:
    if spec == "L":
        return 0
    if spec == "H":
        return 1
    match = re.fullmatch(r"([OL]?)(\d+)", spec)
    assert match, spec
    prefix, number = match.groups()
    bit = int(number)
    value = m if not prefix else previous_o if prefix == "O" else lut_word
    return (value >> bit) & 1


def run(raw: bytes) -> list[int]:
    bundles, labels, lut = parse_program()
    m = int.from_bytes(raw[:8], "little")
    next_byte = 8
    previous_o = 0
    pc = 0
    output: list[int] = []
    while len(output) < (len(raw) - 4) // 2:
        current_lut = lut[(previous_o >> 16) & 1023]
        o = 0
        read_bits = 0
        write_bits = 0
        jump = None
        for op in bundles[pc]:
            parts = op.split()
            if parts[0] == "set":
                first, last = (int(n) for n in parts[1].split("..")) if ".." in parts[1] else (int(parts[1]), int(parts[1]))
                start, *end = parts[2].split("..")
                for offset, dest in enumerate(range(first, last + 1)):
                    source = start if not end else re.sub(r"\d+$", lambda match: str(int(match.group()) + offset), start)
                    o |= source_bit(source, m, previous_o, current_lut) << dest
            elif parts[0] == "read":
                read_bits = int(parts[1])
            elif parts[0] == "write":
                write_bits = int(parts[1])
            elif parts[0] == "jmp":
                jump = labels[parts[1]]
            else:
                raise AssertionError(op)
        if write_bits:
            assert write_bits == 16
            output.append(o & 0xFFFF)
        if read_bits:
            assert read_bits == 16
            incoming = int.from_bytes(raw[next_byte:next_byte + 2].ljust(2, b"\0"), "little")
            next_byte += 2
            m = (m >> 16) | (incoming << 48)
        previous_o = o
        pc = jump if jump is not None else pc + 1
    return output


def main() -> None:
    rng = random.Random(0xC5)
    raw = bytes(rng.randrange(32) for _ in range(4096))
    output = run(raw)
    for index, word in enumerate(output):
        p, m, c = raw[1 + index * 2:4 + index * 2]
        expected = oracle(p, m, c)
        assert word == (expected | (expected << 8)), (index, word, expected, p, m, c)
    print(f"Phase5+ TX program: {len(output)} phase-symbol output pairs byte-exact")


if __name__ == "__main__":
    main()

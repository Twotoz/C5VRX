#!/usr/bin/env python3
"""Execute RX BSASM to verify 40 MS/s byte order and LUT addresses."""

from __future__ import annotations

import random
import re

from phase5plus_rx import BSASM, ring_code


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


def source_bit(spec: str, m: int, lut_word: int) -> int:
    if spec == "L":
        return 0
    match = re.fullmatch(r"(L?)(\d+)", spec)
    assert match, spec
    prefix, number = match.groups()
    value = lut_word if prefix else m
    return (value >> int(number)) & 1


def run(raw: bytes, count: int) -> list[int]:
    bundles, labels, lut = parse_program()
    m = 0  # cfg prefetch false
    a = 0
    previous_o = 0
    next_byte = 0
    pc = 0
    output: list[int] = []
    while len(output) < count:
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
                    o |= source_bit(source, m, current_lut) << dest
            elif parts[0] == "read":
                read_bits = int(parts[1])
            elif parts[0] == "write":
                write_bits = int(parts[1])
            elif parts[0] == "LDCTDA":
                a = int(parts[1])
            elif parts[0] == "LOOPA":
                if a < int(parts[1]):
                    a += int(parts[2])
                    jump = labels[parts[3]]
            elif parts[0] == "jmp":
                jump = labels[parts[1]]
            else:
                raise AssertionError(op)
        if write_bits:
            assert write_bits == 8
            output.append(o & 255)
        if read_bits:
            assert read_bits == 8 and next_byte < len(raw)
            m = (m >> 8) | (raw[next_byte] << 56)
            next_byte += 1
        previous_o = o
        pc = jump if jump is not None else pc + 1
    return output


def main() -> None:
    rng = random.Random(0xC5)
    raw = bytes(range(256)) + bytes(rng.randrange(256) for _ in range(4096))
    count = len(raw) - 9
    got = run(raw, count)
    expected = [ring_code(value) for value in raw[:count]]
    assert got == expected, next((i, got[i], expected[i]) for i in range(count) if got[i] != expected[i])
    print(f"Phase5+ RX program: {count} raw bytes converted in exact order")


if __name__ == "__main__":
    main()

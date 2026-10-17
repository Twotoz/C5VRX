#!/usr/bin/env python3
"""C5VRX by Twotoz/contributors: pinned PBUS BB-prefix construction evidence.

Extends native_agc_evidence.py. Checks actual vendor instructions, not a copied
firmware normalizer. Readback at G83 supplied the independent 0x17f witness.
"""
import hashlib
import os
from pathlib import Path
import re
import subprocess
from native_agc_evidence import PIN


def disassemble(archive, symbol):
    raw = subprocess.check_output(['riscv32-esp-elf-objdump', '-dr',
                                   f'--disassemble={symbol}', str(archive)], text=True)
    blocks = re.split(r'(?=Disassembly of section )', raw)
    matches = [b for b in blocks if f'<{symbol}>:' in b]
    if len(matches) != 1:
        raise SystemExit(f'{symbol}: expected exactly one definition')
    return matches[0]


def main():
    archive = Path(os.environ['IDF_PATH']) / 'components/esp_phy/lib/esp32c5/libphy.a'
    if hashlib.sha256(archive.read_bytes()).hexdigest() != PIN:
        raise SystemExit('Refused unverified PHY archive')
    pbus = disassemble(archive, 'phy_set_pbus_mem_5g')
    chan = disassemble(archive, 'phy_chip_set_chan')
    # Preserve exact register/dataflow facts behind the claimed prefix.
    checks = [
        (pbus, r'mv\s+s1,a0', 'RX-list argument saved'),
        (pbus, r'slli\s+s7,a0,0x10', 'argument truncated to uint16'),
        (pbus, r'srli\s+s7,s7,0x10', 'argument recovered'),
        (pbus, r'lui\s+s10,0x80', 'BB command base 0x00080000'),
        (pbus, r'addi\s+a5,s10,511', 'BB command base OR mask 0x1ff'),
        (pbus, r'slli\s+s7,s7,0x11', 'RX flag placed at command bit 17'),
        (pbus, r'or\s+s7,s7,a5', 'RX flag combined with BB command'),
        (chan, r'snez\s+a5,s5', 'vendor channel-mode nonzero flag'),
        (chan, r'sb\s+a5,290\(s1\)', 'flag stored at phy_param+0x122'),
        (chan, r'lbu\s+a0,290\(s0\)', 'flag loaded for PBUS-list builder'),
        (chan, r'R_RISCV_CALL\s+phy_set_pbus_mem', 'vendor channel path calls PBUS builder'),
    ]
    for text, pattern, why in checks:
        if not re.search(pattern, text):
            raise SystemExit(f'Refused missing construction evidence: {why}')
    print(f'PASS: pinned PBUS BB-prefix construction; SHA256={PIN}')
    for flag in (0, 1):
        command = 0x000801ff | (flag << 17)
        value = (command >> 9) & 511
        print(f'RX-list flag={flag}: BB command=0x{command:08x}, base value=0x{value:03x}')
    print('Board G83: raw RF=0x1e7 BB=0x17f fine=0x0d0; table BB=0x7f; extra bit=0x100')
    print('Scope: existing 5865-MHz/BW40/zero-offset bench only; flag is vendor channel-mode, not arbitrary filter-BW identity')


if __name__ == '__main__':
    main()

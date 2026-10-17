#!/usr/bin/env python3
"""C5VRX by Twotoz/contributors: prove inline native PBUS read fields.

Executes the actual pinned address-helper instructions from its actual switch
relocations, then verifies the getter's mask/extraction instruction contract.
This is static address equivalence; not a measured speed or acquisition proof.
"""
import hashlib
import os
from pathlib import Path
import re
import subprocess
from native_agc_evidence import PIN
from native_pbus_contract import disassemble


def instructions(text):
    result = {}
    for line in text.splitlines():
        m = re.match(r'\s*([0-9a-f]+):\s+[0-9a-f]+\s+([a-z][a-z0-9_.]*)\s*(.*)', line)
        if m:
            operands = re.sub(r'\s*<[^>]+>', '', m[3].split('#')[0]).strip()
            result[int(m[1], 16)] = (m[2], operands.replace(' ', ''))
    return result


def read_address(code, start, bank):
    """Small interpreter for the real selected switch case, never C replicas."""
    pc = start
    regs = dict(a0=0, a1=bank, a2=0x100, a4=0, a5=0)
    memory = {}
    order = sorted(code)
    after = dict(zip(order, order[1:]))
    for _ in range(32):
        op, text = code[pc]; args = text.split(',') if text else []
        next_pc = after.get(pc)
        if op == 'li': regs[args[0]] = int(args[1], 0)
        elif op == 'lui': regs[args[0]] = int(args[1], 0) << 12
        elif op == 'addi': regs[args[0]] = (regs[args[1]] + int(args[2], 0)) & 0xffffffff
        elif op == 'sw':
            m = re.fullmatch(r'(-?\d+)\((\w+)\)', args[1]); assert m
            memory[regs[m[2]] + int(m[1])] = regs[args[0]]
        elif op in ('beq', 'bne'):
            take = (regs[args[0]] == regs[args[1]]) == (op == 'beq')
            if take: next_pc = int(args[2], 16)
        elif op == 'j': next_pc = int(args[0], 16)
        elif op == 'ret': return regs['a0'], memory[0x100]
        else: raise AssertionError(f'Unsupported actual address instruction {pc:x}: {op} {text}')
        assert next_pc is not None
        pc = next_pc
    raise AssertionError('Address helper did not return within 32 instructions')


def main():
    archive = Path(os.environ['IDF_PATH']) / 'components/esp_phy/lib/esp32c5/libphy.a'
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == PIN, 'Refused unverified PHY archive'
    address_text = disassemble(archive, 'phy_pbus_rd_addr')
    getter_text = disassemble(archive, 'phy_pbus_rd')
    code = instructions(address_text)
    labels = {name: int(pc, 16) for pc, name in re.findall(r'^([0-9a-f]+) <([^>]+)>:', address_text, re.M)}
    reloc = subprocess.check_output(['riscv32-esp-elf-objdump', '-r', '-j',
                                   '.rodata.phy_pbus_rd_addr', str(archive)], text=True)
    switch = {int(offset, 16) // 4: label for offset, label in
              re.findall(r'^([0-9a-f]{8})\s+R_RISCV_32\s+(.L\d+)\s*$', reloc, re.M)}
    assert len(switch) == 11 and sorted(switch) == list(range(11))
    expected = {
        (8, 1): (0x600a08a8, 9), (8, 2): (0x600a08a8, 0),
        (0, 1): (0x600a0894, 9), (0, 2): (0x600a0894, 0),
        (1, 1): (0x600a0898, 18), (1, 2): (0x600a0898, 9),
    }
    for (block, bank), want in expected.items():
        got = read_address(code, labels[switch[block]], bank)
        assert got == want, (block, bank, got, want)

    # After its resolved-address call the actual getter must perform exactly
    # this masked extraction, with only stack/return instructions interleaved.
    getter = list(instructions(getter_text).values())
    post_call = getter[next(k for k, value in enumerate(getter) if value[0] == 'jalr') + 1:]
    wanted = [('lw', 'a5,12(sp)'), ('lw', 'a4,0(a0)'), ('li', 'a0,511'),
              ('sll', 'a0,a0,a5'), ('lw', 'ra,28(sp)'), ('and', 'a0,a0,a4'),
              ('srl', 'a0,a0,a5'), ('slli', 'a0,a0,0x10'), ('srli', 'a0,a0,0x10'),
              ('addi', 'sp,sp,32'), ('ret', '')]
    assert post_call == wanted, ('Unexpected actual getter extraction', post_call)
    print(f'PASS: actual pinned PBUS getter/address switch/extraction; SHA256={PIN}')
    for name, pair in [('RF', (8, 1)), ('BB', (0, 2)), ('fine', (1, 2))]:
        address, shift = expected[pair]
        print(f'{name} block/bank={pair}: (REG(0x{address:08x}) >> {shift}) & 0x1ff')
    print('Both banks checked; no address-helper writes except the caller shift output; mask is exactly nine bits')


if __name__ == '__main__':
    main()

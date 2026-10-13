"""C5VRX by Twotoz/contributors: static RV32 sync cost inspection.

Cross-compiles the current host candidate with the pinned IDF toolchain.
Instruction counts/disassembly are not elapsed C5 timing or a WCET bound.
"""
from pathlib import Path
import json
import hashlib
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
IMAGE = 'espressif/idf:v6.0.2'


def main():
    inputs = ('main/sync_flywheel.c', 'main/sync_flywheel.h', 'main/sync_synth.h', 'main/sync_repair_ledger.h')
    hashes = {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in inputs}
    command = ('riscv32-esp-elf-gcc -O2 -std=c11 -I/src/main '
               '-c /src/main/sync_flywheel.c -o /tmp/sfw.o && '
               'riscv32-esp-elf-objdump -d /tmp/sfw.o')
    run = subprocess.run(['docker', 'run', '--rm', '-v', f'{ROOT.as_posix()}:/src:ro',
                          IMAGE, 'bash', '-c', command], capture_output=True, text=True)
    if run.returncode:
        raise RuntimeError(run.stdout + run.stderr)
    assert hashes == {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in inputs}, 'source changed during compile'
    start = run.stdout.find('/tmp/sfw.o:')
    assert start >= 0
    asm = run.stdout[start:]
    output = ROOT / 'research/data/sync_stream_cost.disasm'
    output.write_text(asm, encoding='utf-8')
    symbols = []
    for match in re.finditer(r'^([0-9a-f]+) <([^>]+)>:\n', asm, re.M):
        if not match.group(2).startswith('.L'):
            symbols.append({'address': match.group(1), 'symbol': match.group(2)})
    result = {'toolchain': IMAGE, 'optimization': '-O2',
              'source_sha256': hashes,
              'note': 'Static assembly only; no C5 timing, instruction CPI or WCET assumption',
              'cycle_budget_per_PAL_line_at_240MHz': 15360,
              'cycle_budget_per_NTSC_line_at_240MHz': 15253.333333333334,
              'synthesized_H_plus_front_porch_bytes_per_second': 236 * 15625,
              'symbols': symbols}
    (ROOT / 'research/data/sync_stream_cost.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()

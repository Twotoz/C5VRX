#!/usr/bin/env python3
"""Assemble frozen C5VRX range finalists with the actual ESP32-C5 target.

C5VRX by Twotoz/contributors. This proves assembler acceptance, not RF/video
performance or live timing. Requires ESP-IDF, but no numerical dependencies.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from compile_overlay import build, cost
from generate_range_options import identity


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('frozen', type=Path, nargs='+')
    ap.add_argument('--idf', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    a = ap.parse_args()
    assembler = a.idf / 'tools/bsasm.py'
    target = a.idf / 'components/esp_driver_bitscrambler/bsasm_targets/esp32c5.json'
    if not assembler.is_file() or not target.is_file():
        ap.error('ESP-IDF assembler and ESP32-C5 target required')
    if a.output.exists():
        ap.error('fresh proof directory required')
    prepared = []
    for frozen in a.frozen:
        for row in json.loads(frozen.read_text())['finalists']:
            model = row['model']
            source = build(model).encode('utf-8')
            if identity(model) != row['id']:
                raise ValueError(f'{frozen}: frozen model digest mismatch')
            p = model['params']
            prepared.append((source, dict(model_id=row['id'], frozen=str(frozen),
                source_sha256=hashlib.sha256(source).hexdigest(),
                cost=cost(p['token_bits'], p.get('context_bits', 0), p.get('counter_phase', False), p.get('pair_layout')))))
    if not prepared:
        ap.error('no frozen finalists supplied')
    a.output.mkdir(parents=True)
    rows = []
    for index, (source, row) in enumerate(prepared):
        path = a.output / f"{index:03d}-{row['model_id'][:12]}.bsasm"
        path.write_bytes(source)
        binary = path.with_suffix('.bsbin')
        subprocess.run([sys.executable, str(assembler), str(path), str(binary),
                        '-c', str(target)], check=True, stdout=subprocess.DEVNULL)
        row.update(binary_bytes=binary.stat().st_size,
                   binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest())
        rows.append(row)
    proof = dict(target='esp32c5', assembled=len(rows),
                 assembler_sha256=hashlib.sha256(assembler.read_bytes()).hexdigest(),
                 target_sha256=hashlib.sha256(target.read_bytes()).hexdigest(), models=rows,
                 scope='assembler acceptance; not physical timing, picture or range validation')
    (a.output / 'proof.json').write_text(json.dumps(proof, indent=2), encoding='utf-8')
    print(f'PASS ESP32-C5 assembler: {len(rows)} frozen finalists')


if __name__ == '__main__':
    main()

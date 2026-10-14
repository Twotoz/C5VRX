"""C5VRX by Twotoz/contributors: controlled first-EQ criterion experiment.

Compiles a temporary one-line adaptation of the actual candidate. Token
ranking uses the original 1x worker DAC code; all decoder-state evolution and
independent output tests still use the actual scaled LUT. No firmware edits.
This does NOT validate reference/context admission, noisy prefixes or LCDs.
Run with a C11 compiler, e.g. inside the pinned IDF image.
"""
from pathlib import Path
import argparse
import csv
import hashlib
import io
import json
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    core_path = ROOT / 'main/sync_flywheel.c'
    matrix_path = ROOT / 'research/sync_output_transition.c'
    baseline_path = ROOT / 'research/data/sync_output_transition.csv'
    core, matrix = core_path.read_text(), matrix_path.read_text()
    needle = 'unsigned code = r->decoder_lut[states[parity][j].phase * 32u + token] & 63u;'
    assert core.count(needle) == 1, 'actual first-tip source changed: review this experiment'
    core = core.replace(needle, 'unsigned code = experiment_reference[states[parity][j].phase * 32u + token] & 63u;')
    matrix = matrix.replace('#include "../main/sync_flywheel.c"',
                            'static const uint16_t *experiment_reference;\n#include "core.c"')
    needle = 'uint16_t lut[1024]; unsigned clipped = 0;'
    assert matrix.count(needle) == 1
    matrix = matrix.replace(needle, 'experiment_reference=kp_tables[kp];\n        ' + needle)
    with tempfile.TemporaryDirectory() as directory:
        folder = Path(directory)
        (folder / 'core.c').write_text(core)
        (folder / 'matrix.c').write_text(matrix)
        binary = folder / 'matrix'
        subprocess.run(['cc', '-O2', '-std=c11', '-Wall', '-Wextra', '-Werror',
                        '-I', str(ROOT / 'main'), str(folder / 'matrix.c'), '-o', str(binary)], check=True)
        output = subprocess.check_output([str(binary)], text=True)
    rows = list(csv.DictReader(io.StringIO(output[output.index('kp,'):])))
    old = list(csv.DictReader(io.StringIO(baseline_path.read_text())))
    key = lambda r: tuple(r[k] for k in ('kp', 'lp', 'output', 'pal', 'kind'))
    previous = {key(row): row for row in old}
    assert len(rows) == len(old) == 1280
    unity = [row for row in rows if row['output'] == '0']
    assert all(row == previous[key(row)] for row in unity), '1x result changed'
    groups = []
    for lp in range(2):
        for level in range(10):
            group = [row for row in rows if row['lp'] == str(lp) and row['output'] == str(level)]
            groups.append(dict(lp=bool(lp), output_level=level,
                **{name: sum(int(row[name]) for row in group)
                   for name in ('cases', 'normalized_width_ok', 'bootstrap_refused', 'tail_changed')},
                baseline_bootstrap_refused=sum(int(previous[key(row)]['bootstrap_refused']) for row in group),
                max_tail_span=max(int(row['max_tail_span']) for row in group)))
    result = dict(
        scope='Temporary reference-code criterion; actual mapped decoder; deterministic strong prefixes only',
        source_sha256={str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                       for path in (core_path, matrix_path, baseline_path, ROOT / 'main/output_level.h')},
        rows=len(rows), decode_cases=sum(int(row['cases']) for row in rows),
        unity_rows_exact=len(unity), groups=groups,
        production_changed=False, context_admission_validated=False,
        non_lp_supported=False, physical_lcd_validated=False,
        conclusion='Reference-code ranking removes the half-scale absolute-code rejection in these prefixes; production requires explicit verified reference/program/output binding and wider scene tests')
    if args.output:
        args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({name: result[name] for name in ('rows', 'decode_cases', 'unity_rows_exact', 'groups')}, indent=2))


if __name__ == '__main__':
    main()

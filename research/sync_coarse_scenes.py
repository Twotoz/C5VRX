"""C5VRX by Twotoz/contributors: full keeper gates for coarse acquisition.

Reuse the exact temporary variant builder, without its optional IQ simulation
dependencies. Never changes the production detector or weakens its gates.
"""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    parser.add_argument('--kp', nargs='+', type=int, default=list(range(8)))
    args = parser.parse_args()
    builder = ROOT / 'research/sync_acquire_stride.py'
    tree = ast.parse(builder.read_text())
    helpers = ast.Module(body=[node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in
        ('replace_once', 'acquire_block', 'variants')], type_ignores=[])
    namespace = {}
    exec(compile(helpers, str(builder), 'exec'), namespace)
    source = (ROOT / 'main/sync_flywheel.c').read_text()
    _, coarse = namespace['variants'](source)
    report = {'original_sha256': hashlib.sha256(source.encode()).hexdigest(),
        'temporary_sha256': hashlib.sha256(coarse.encode()).hexdigest(),
        'builder_sha256': hashlib.sha256(builder.read_bytes()).hexdigest(),
        'hardware_timing_claim': False, 'rows': []}
    with tempfile.TemporaryDirectory() as directory:
        folder = Path(directory)
        core = folder / 'core.c'
        core.write_text(coarse)
        binary = folder / 'scenes'
        subprocess.run(['cc', '-O2', '-std=gnu11', '-Wall', '-Wextra', '-Werror',
            '-I'+str(ROOT/'main'), str(ROOT/'test/test_sync_flywheel.c'), str(core),
            '-lm', '-o', str(binary)], check=True)
        for kp in args.kp:
            env = dict(os.environ, SFW_KP_LEVEL=str(kp), SFW_COMMIT='1')
            run = subprocess.run(['stdbuf', '-oL', str(binary)], env=env,
                text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            report['rows'].append({'kp': kp, 'returncode': run.returncode, 'stdout': run.stdout})
            print('kp', kp, 'return', run.returncode, run.stdout[-600:], flush=True)
    Path(args.output).write_text(json.dumps(report, indent=2)+'\n')
    return int(any(row['returncode'] for row in report['rows']))


if __name__ == '__main__':
    raise SystemExit(main())

"""C5VRX by Twotoz/contributors: current production keeper OUTPUT extremes.

Run the real test/core with callback commits, retain complete stdout, and
reject acceptance if source/test/header inputs change during the matrix.
Host waveform validation is separate from live C5 timing/publication proof.
"""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def hashes():
    files = [ROOT / 'main/sync_flywheel.c', ROOT / 'test/test_sync_flywheel.c']
    files += sorted((ROOT / 'main').glob('*.h'))
    return {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in files}


def main():
    before = hashes()
    rows = []
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    with tempfile.TemporaryDirectory(prefix='c5-production-extremes-') as directory:
        folder = Path(directory)
        docker = ['docker', 'run', '--rm', '-v', ROOT.as_posix() + ':/project:ro',
                  '-v', folder.as_posix() + ':/data', '-w', '/project']
        compiler = ['gcc', '-O2', '-std=c11', '-D_DEFAULT_SOURCE', '-Wall', '-Wextra', '-Werror',
                    '-Imain', 'test/test_sync_flywheel.c', 'main/sync_flywheel.c',
                    '-lm', '-o', '/data/keeper']
        subprocess.run(docker + ['gcc:14'] + compiler, check=True)
        for kp in (0, 7):
            for output in (8, 9):
                result = subprocess.run(docker + ['-e', 'SFW_COMMIT=1', '-e', f'SFW_KP_LEVEL={kp}',
                    '-e', f'SFW_OUTPUT_LEVEL={output}', 'gcc:14', '/data/keeper'],
                    text=True, capture_output=True)
                rows.append(dict(kp_level=kp, kp_x100=85 if kp == 0 else 50,
                    output_index=output, output_multiplier=5.0 if output == 8 else 0.5,
                    callback_commits=True, exit_code=result.returncode,
                    stdout=result.stdout, stderr=result.stderr,
                    stdout_sha256=hashlib.sha256(result.stdout.encode()).hexdigest()))
                print(f'kp={kp} output={output} exit={result.returncode} '
                      f'stdout_bytes={len(result.stdout.encode())}', flush=True)
    after = hashes()
    unchanged = before == after
    passed = unchanged and all(row['exit_code'] == 0 and
        'PASS sync_flywheel:' in row['stdout'] for row in rows)
    report = dict(provenance='C5VRX by Twotoz/contributors; https://github.com/Twotoz/C5VRX; https://twotoz.github.io/C5VRX/',
        head=head, source_hashes_before=before, source_hashes_after=after,
        inputs_unchanged=unchanged, compiler_image='gcc:14', compiler_args=compiler,
        passed=passed, hardware_cycles_proven=False, rows=rows)
    path = ROOT / 'research/data/sync_production_extremes.json'
    path.write_text(json.dumps(report, separators=(',', ':')) + '\n')
    print(f'wrote {path}; inputs_unchanged={unchanged} passed={passed}', flush=True)
    if not passed:
        raise SystemExit(1)


if __name__ == '__main__':
    main()

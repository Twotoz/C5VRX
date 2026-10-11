"""Audit archived C5VRX/Twotoz research bytes, not receiver performance.

GPL-3.0-only; https://github.com/Twotoz/C5VRX
Older protocols without frozen sources remain documented limitations.
"""
import hashlib
import json
from pathlib import Path


def audit(root):
    checked = []
    reports = list(root.rglob('*.json'))

    def check(path, expected):
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise AssertionError(f'hash mismatch: {path}')
        checked.append(str(path.relative_to(root)))

    for path in reports:
        report = json.loads(path.read_text())
        if not isinstance(report, dict):
            continue
        if path.name == 'results.json' and 'source_sha256' in report:
            check(path.parent / 'source.py', report['source_sha256'])
        if path.name == 'c-proof.json':
            check(path.parent / 'local_iq_clock_stream.c', report['source_sha256'])
            check(path.parent / 'stream-rv32imc.s', report['c5_assembly_sha256'])
            assert len(report['cases']) == 14
            assert all(case['byte_exact'] for case in report['cases'])
        for name, expected in report.get('source_hashes', {}).items():
            snapshot = path.parent / Path(name).name
            if not snapshot.is_file():
                snapshot = path.parent / 'sources' / Path(name).name
            if snapshot.is_file():
                check(snapshot, expected)
        for program in report.get('programs', []):
            check(path.parent / (program['name'] + '.bsasm'), program['source_sha256'])
            if program['exit_code'] == 0:
                check(path.parent / (program['name'] + '.bsbin'), program['binary_sha256'])
    return dict(json_reports=len(reports), verified_snapshot_hashes=len(checked),
                scope='archive_integrity_not_independent_DSP_or_hardware_validation')


if __name__ == '__main__':
    root = Path(__file__).resolve().parents[2] / 'docs/data/local_joint'
    print(json.dumps(audit(root), indent=2))

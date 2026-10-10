"""Verify complete paired results and source/input fingerprints, not RF range."""
import argparse
import hashlib
import json
from pathlib import Path
import types

from iq_sequence_study import study_cases
from replay_iq_sequence import load_capture


def same_source(path, digest):
    # Existing Windows checkouts and Linux CI use different source newlines.
    # Accept equivalent Python text; IQ checksums remain strictly byte-exact.
    raw = path.read_bytes()
    lf = raw.replace(b'\r\n', b'\n')
    return digest in {hashlib.sha256(data).hexdigest() for data in
                      (raw, lf, lf.replace(b'\n', b'\r\n'))}


def validate_study(directory):
    protocol = json.loads((directory / 'protocol.json').read_text())
    results = json.loads((directory / 'results.json').read_text())
    expected_rows = protocol['per'] * len(protocol['cnr']) * len(protocol['amplitudes']) * len(protocol['decoders'])
    if len(results['rows']) != expected_rows:
        raise ValueError(f'{directory.name}: incomplete results ({len(results["rows"])} != {expected_rows})')
    rows = {}
    for row in results['rows']:
        key = (row['seed'], row['rms'], row['standard'])
        rows.setdefault(key, {})
        if row['model'] in rows[key]:
            raise ValueError('duplicate model/case identity')
        rows[key][row['model']] = row
    cfg = types.SimpleNamespace(**{k: protocol[k] for k in ('seed', 'size', 'per', 'cnr', 'amplitudes')},
                                scenario=protocol.get('scenario', 'static'))
    for c in study_cases(cfg):
        key = (c['seed'], c['rms'], c['standard'])
        paired = rows.pop(key)
        if set(paired) != set(protocol['decoders']):
            raise ValueError('missing or unexpected comparator')
        digest = hashlib.sha256(c['raw'].tobytes()).hexdigest()
        if any(row['iq_sha256'] != digest for row in paired.values()):
            raise ValueError('paired input does not reproduce')
        if any(not row['metrics']['scored_samples'] > 0 for row in paired.values()):
            raise ValueError('empty score')
    if rows:
        raise ValueError('unexpected case identities')
    tools = Path(__file__).resolve().parent
    for filename, digest in protocol.get('source_sha256', {}).items():
        if not same_source(tools / filename, digest):
            archive = directory.parent / 'source_versions' / f'{Path(filename).stem}-{digest[:12]}.py.txt'
            if not archive.exists() or not same_source(archive, digest):
                raise ValueError(f'unavailable source revision: {filename} {digest}')
    return len(results['rows'])


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--evidence', type=Path, required=True)
    args = ap.parse_args()
    total = 0
    for name in ('development512', 'confirmation512', 'convergence2048', 'transient512'):
        count = validate_study(args.evidence / name)
        total += count
        print(f'{name}: {count} complete paired rows; IQ hashes reproduced', flush=True)
    replay = json.loads((args.evidence / 'physical_replay512/replay.json').read_text())
    for row in replay['rows']:
        raw, metadata, _ = load_capture(args.evidence / 'captures' / row['name'])
        if hashlib.sha256(raw.tobytes()).hexdigest() != row['iq_sha256'] or metadata != row['metadata']:
            raise ValueError('physical replay metadata/hash mismatch')
    print(f'PASS: {total} synthetic comparator rows and {len(replay["rows"])} separately checksummed physical inputs; no range claim')


if __name__ == '__main__':
    main()

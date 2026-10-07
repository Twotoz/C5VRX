#!/usr/bin/env python3
"""High-C/N veto tests for C5VRX's frozen offline demod hypotheses.

No reselection/refit. 24/30/40-dB C/N tests expose detail losses that 14/18-dB
guards can miss. This extends, rather than replaces, the frozen final tests.
"""
import argparse
import json
from pathlib import Path
import mega_demod_bench as M


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--frozen-theories', type=Path, required=True)
    ap.add_argument('--frozen-lut', type=Path, required=True)
    a = ap.parse_args()
    if (a.output/'protocol.json').exists():
        ap.error('use a fresh evidence directory')
    a.output.mkdir(parents=True, exist_ok=True)
    frozen = json.loads(a.frozen_theories.read_text())
    models = frozen['winners']+[M.F.load_reference('OVP56'), M.F.load_reference('VLP56'),
                               dict(name='HC50'), json.loads(a.frozen_lut.read_text())['winner']]
    seeds = [6601, 6602, 6603]
    M.S.write_json(a.output/'protocol.json', dict(seeds=seeds, cnrs=[24, 30, 40],
                  policy='frozen-family high-C/N veto only; no candidate reselection or training'))
    cases = M.F.dataset(seeds, (24, 30, 40), (.75, 1.5, 3, 5), n=131072)
    rows = M.evaluate(models, cases)
    M.S.write_rows(a.output/'measurements.csv', rows)
    stats = M.summarize(rows, strong_screen=True)
    for s in stats.values():
        for k in ('weak_objective', 'weak_sinad', 'weak_clicks', 'weak_contrast'):
            del s[k]
    M.S.write_json(a.output/'summary.json', stats)
    print('HIGH_CNR_GUARDS', json.dumps(stats), flush=True)


if __name__ == '__main__':
    main()

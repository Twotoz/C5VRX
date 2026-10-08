#!/usr/bin/env python3
"""Compare frozen C5VRX estimators on an actual contiguous Q4/I4 capture.

C5VRX by Twotoz and contributors; see docs/MEGA_DEMOD_STUDY.md for provenance.
One byte per acquired IQ40 sample: high signed nibble I, low signed nibble Q.
This host replay never claims live board throughput or known-picture SINAD.
"""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import mega_demod_bench as M


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--input', type=Path, required=True, help='headerless contiguous Q4/I4 bytes, 40 MS/s')
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--hypotheses', type=Path, default=Path(__file__).parent/'models/theory_hypotheses.json')
    a = ap.parse_args()
    if a.output.exists():
        ap.error('use a new output directory')
    raw = np.frombuffer(a.input.read_bytes(), dtype=np.uint8)
    if len(raw) < 8190 or len(raw) % 2:
        ap.error('need at least 8190 bytes and complete input pairs')
    models = json.loads(a.hypotheses.read_text())['winners']
    # Frozen before independent finals. Never tune on the captured test data.
    models = [M.F.load_reference('OVP56')]+[m for m in models if m['family'] in ('pll', 'ml')]
    a.output.mkdir(parents=True)
    report = dict(input_sha256=hashlib.sha256(raw.tobytes()).hexdigest(),
                  hypotheses_sha256=hashlib.sha256(a.hypotheses.read_bytes()).hexdigest(),
                  samples=len(raw), sample_rate_hz=40000000,
                  scope='host replay; state resets at capture start; no known-picture quality score',
                  models=models)
    # Each array is the common passive-DAC + 5-MHz goggle-model output at
    # physical DAC40 timing. Nominal units are preserved, no noisy auto-fit.
    for model in models:
        np.save(a.output/(model['name']+'.npy'), M.decode(raw, model))
    (a.output/'manifest.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(dict(output=str(a.output), samples=len(raw), models=[m['name'] for m in models])))


if __name__ == '__main__':
    main()

"""C5VRX by Twotoz/contributors: synthetic probe fixtures and C-gate report.
Extends cn_envelope.py; the C helper runs the real firmware measurement.
No sync/AFC/held-gain oracle proof: these are upper bounds for eligible probes.
"""
from pathlib import Path
import csv
import json
import struct
import sys
import numpy as np
import sim
from cn_envelope import probes

def generate(folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    cases = []
    with (folder / 'probes.bin').open('wb') as f:
        for dev in (.69, 1., 1.4, 1.7, 2.):
            for filt in (12, 16):
                for cn in (20, 30):
                    for seed in (71, 72):
                        case_id = len(cases)
                        cases.append(dict(id=case_id, dev=dev, filter_mhz=filt, cn=cn, seed=seed))
                        c = sim.make_case(seed, cn, ('PAL', 'NTSC')[seed % 2], dev, filter_hz=filt * 1e6, fields=1)
                        for raw in probes(c['raw']):
                            f.write(struct.pack('<II', case_id, filt) + raw.tobytes())
    (folder / 'cases.json').write_text(json.dumps(cases, indent=2), encoding='utf-8')
    print(f'{len(cases)} cases saved in {folder}')

def report(folder, output):
    folder = Path(folder)
    cases = json.loads((folder / 'cases.json').read_text(encoding='utf-8'))
    probes_by_id = {c['id']: [] for c in cases}
    with (folder / 'probe_results.csv').open(encoding='utf-8') as f:
        for row in csv.DictReader(f):
            d = {k: int(v) for k, v in row.items()}
            probes_by_id[d['case_id']].append(d)
    rows = []
    for c in cases:
        rs = probes_by_id[c['id']]
        r = dict(c, probes=len(rs), cn_median=float(np.median([p['cn_x10'] for p in rs])) / 10,
                 coherence_median=float(np.median([p['coherence'] for p in rs])),
                 old_gate_pct=100 * np.mean([p['old_gate'] for p in rs]),
                 new_gate_pct=100 * np.mean([p['new_gate'] for p in rs]))
        rows.append(r)
        print(f"dev{r['dev']:g} {r['filter_mhz']}MHz true{r['cn']}dB: C/N {r['cn_median']:.1f}, "
              f"coherence {r['coherence_median']:.0f}%, old/new gate {r['old_gate_pct']:.1f}/{r['new_gate_pct']:.1f}%")
    Path(output).write_text(json.dumps(rows, indent=2), encoding='utf-8')

if __name__ == '__main__':
    if sys.argv[1] == 'generate': generate(sys.argv[2])
    elif sys.argv[1] == 'report': report(sys.argv[2], sys.argv[3])
    else: raise SystemExit('generate FOLDER | report FOLDER OUTPUT.json')

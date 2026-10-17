"""C5VRX by Twotoz/contributors: preserve the PAL V/active overlap failure.

Evaluation oracle only: raster region labels never enter the real keeper.
Run python research/output_abc_pal_v_repro.py [--kp 0..7]. No hardware access.
This is an expected counterexample, not an acceptance test to hide or fix by
zeroing a source region. Uses the same actual C replay as output_abc.py.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

import numpy as np
import sim
from u85q_lp import lp_codes

ROOT = Path(__file__).resolve().parent.parent


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--kp', type=int, choices=range(8), default=0)
    ap.add_argument('--output', type=Path, default=ROOT / 'research/data/output_abc_pal_v_failure.json')
    args = ap.parse_args()
    c = sim.make_case(213, 30., 'PAL', 1., filter_hz=12e6, fields=3, physical_adc=True,
                      legacy_raster=True)
    raw = c['raw'].copy()
    lo, hi = len(raw) * 19 // 30, len(raw) * 22 // 30
    raw[lo:hi] = np.random.default_rng(212).integers(0, 256, hi - lo, dtype=np.uint8)
    with tempfile.TemporaryDirectory(prefix='c5-output-pal-v-') as folder:
        w = Path(folder); (w / 'source.bin').write_bytes(raw.tobytes())
        subprocess.run(['docker', 'run', '--rm', '-v', ROOT.as_posix() + ':/project:ro',
            '-v', w.as_posix() + ':/data', '-w', '/project', 'gcc:14', 'gcc', '-O2', '-std=c11',
            '-Wall', '-Wextra', '-Werror', '-Imain', 'research/output_abc_replay.c',
            'main/sync_flywheel.c', '-o', '/data/replay'], check=True)
        keeper = json.loads(subprocess.check_output(['docker', 'run', '--rm',
            '-v', w.as_posix() + ':/data', 'gcc:14', '/data/replay',
            '/data/source.bin', '/data/repaired.bin', str(args.kp)], text=True))
        repaired = np.frombuffer((w / 'repaired.bin').read_bytes(), np.uint8)
        lut = np.frombuffer((w / 'repaired.bin.lut').read_bytes(), '<u2').reshape(2, 1024)[0]
        active = c['region'] == 1
        overwritten = np.flatnonzero(active & (raw != repaired))
        assert len(overwritten), 'Counterexample disappeared: review the physical/source change explicitly'
        first = int(overwritten[0]); last = int(overwritten[-1])
        source_line = first // 2560; source_half = first // 1280
        parity_rows = []
        for parity in (0, 1):
            before, after = lp_codes(raw[parity:], lut), lp_codes(repaired[parity:], lut)
            changed = np.flatnonzero(active[parity:] & (before != after)) + parity
            earliest = int(changed[0])
            same_raw = raw[earliest] == repaired[earliest]
            parity_rows.append(dict(parity=parity, changed_active_dac_samples=len(changed),
                first_active_dac_change=earliest, raw_equal_at_first_dac_change=bool(same_raw),
                changed_iq_in_previous_40=int(np.count_nonzero(
                    raw[max(0, earliest - 40):earliest + 1] != repaired[max(0, earliest - 40):earliest + 1]))))
        result = dict(evidence='expected counterexample; source oracle used only after actual keeper output',
            provenance='C5VRX by Twotoz and contributors; https://github.com/Twotoz/C5VRX; https://twotoz.github.io/C5VRX/',
            source=dict(seed=213, cnr_db=30., standard='PAL', deviation=1., filter_hz=12e6,
                fields=3, physical_adc=True, replacement_rng_seed=212,
                replacement='uniform uint8 IQ noise', replacement_begin=lo, replacement_end=hi,
                iq_sha256=hashlib.sha256(raw.tobytes()).hexdigest(), samples=len(raw)),
            keeper=keeper, active_iq_overwritten=len(overwritten),
            first_active_iq_overwrite=first, last_active_iq_overwrite=last,
            first_field_coordinate=first / 800000., source_full_line=source_line,
            offset_in_full_line_samples=first % 2560, source_half_line=source_half,
            offset_in_half_line_samples=first % 1280, parity_rows=parity_rows,
            causal_boundary=dict(nominal_first_pre_equalizing_sample=1593600,
                nominal_half_line=1245, source_normal_line_half_index=1244,
                source_full_line_end_exclusive=1594880,
                explanation='sim.raster normal h1244 fills both half-lines and increments h by2, skipping the odd-field pre-EQ slot h1245; its active source labels extend to1594800. Keeper coasts its real prior V anchor and paints that predicted slot during noise.'),
            limitation='This source/geometry conflict requires an explicit repair admission decision. It is not only LP state tail. Do not relabel or zero active input to force preservation.')
        args.output.write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()

"""C5VRX by Twotoz/contributors: identical-IQ OUTPUT A/B/C experiment.

Uses actual C output_entry/kp tables and real-source-only sync_flywheel via
output_abc_replay.c; decodes the verified U85Q and U85Q-LP arithmetic models.
C is the existing 1x keeper candidate, not a static per-code amplitude map.
Synthetic truth labels evaluate results only; no labels enter the keeper.
Nominal75-ohm voltages are a circuit model, not measured or sensed feedback.
Run: python research/output_abc.py [--quick] [--output path.json]
Requires local Python numpy/scipy/numba and Docker gcc:14. No hardware access.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

import numpy as np
from scipy import ndimage

import sim
from output_level import VOLTS
from u85q_lp import lp_codes

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def auto_candidate(sync_depth, white_span, floor=1., ceiling=2.5, usable=False,
                   acquisition=False, settled=False):
    """Research bound only: multiplier floor does not guarantee sync voltage.

    No adjustments in fades, rescue, invalid context or ordinary video TRACK.
    Model target18codes,3code tip/white reserves. Physical voltages require
    independently measured loaded levels, not this function's predictions.
    """
    if not usable or not acquisition or not settled:
        return {'reason': 'hold_last_verified'}
    if not 0 < sync_depth < white_span or not .5 <= floor <= ceiling <= 5:
        return {'reason': 'invalid_bounds'}
    safe_max = 57. / white_span
    if safe_max < floor:
        return {'reason': 'floor_conflicts_with_headroom', 'safe_max': safe_max}
    value = min(ceiling, safe_max, max(floor, 18. / sync_depth))
    return {'reason': 'candidate_only', 'gain': value,
            'predicted_depth_codes': value * sync_depth,
            'target_reached': value * sync_depth >= 18.}


def checks():
    assert auto_candidate(12, 55, 2, 5, True, True, True)['reason'] == 'floor_conflicts_with_headroom'
    assert auto_candidate(2, 60, .5, 5, True, True, True)['target_reached'] is False
    for usable, acq, settled in ((False, True, True), (True, False, True), (True, True, False)):
        assert auto_candidate(12, 45, 1, 2.5, usable, acq, settled)['reason'] == 'hold_last_verified'


def sources(quick):
    specs = [('normal', 1., 6.)] if quick else [
        ('narrow_strong', .69, 30.), ('normal_weak', 1., 6.),
        ('wide_strong', 1.7, 30.), ('wide_weak', 1.7, 6.)]
    for standard in ('PAL', 'NTSC'):
        for name, dev, cn in specs:
            c = sim.make_case(212, cn, standard, dev, filter_hz=16e6 if dev > 1.6 else 12e6,
                              fields=2, physical_adc=True, legacy_raster=True)
            yield dict(name=standard + '_' + name, synthetic=True, standard=standard,
                       deviation=dev, cnr_db=cn), c
        # Same original source arrays for all modes; substitute independent
        # fresh noise during one bounded fade without changing the policy.
        c = sim.make_case(213, 30., standard, 1., filter_hz=12e6, fields=3, physical_adc=True,
                          legacy_raster=True)
        c['raw'] = c['raw'].copy()
        n = len(c['raw']); rng = np.random.default_rng(212)
        lo, hi = n * 19 // 30, n * 22 // 30
        c['raw'][lo:hi] = rng.integers(0, 256, hi - lo, dtype=np.uint8)
        yield dict(name=standard + '_sync_loss', synthetic=True, standard=standard,
                   deviation=1., cnr_db=30., fade='field1.9..2.2 independent IQ noise'), c
    for path in sorted((HERE / 'data/walk_2026-10-13').glob('*.bin')):
        yield dict(name=path.stem, synthetic=False, source=str(path.relative_to(ROOT)),
                   scope='one204.75us snapshot; no known vertical phase or picture truth'), {
                       'raw': np.frombuffer(path.read_bytes(), np.uint8)}


def decode(raw, lut, lp, parity):
    # Restart both configurations from identical state and pair alignment.
    return (lp_codes if lp else sim.u85q_codes)(raw[parity:], lut)


def metrics(codes, baseline, worker, c, parity):
    valid = slice(3000, -3000) if len(codes) > 10000 else slice(128, -128)
    x = codes[valid]; b = baseline[valid]
    result = dict(code_min=int(x.min()), code_max=int(x.max()), unique_codes=int(len(np.unique(x))),
                  dac_rail_fraction=float(np.mean((x == 0) | (x == 63))),
                  worker_rail_fraction=float(np.mean((worker[valid] == 0) | (worker[valid] == 63))),
                  changed_output_fraction=float(np.mean(x != b)),
                  model_p2p_v=float(np.ptp(VOLTS[x])))
    if 'region' not in c:
        result['sync_depth_codes'] = result['active_difference_rms_codes'] = None
        return result
    region = c['region'][parity:]
    # Evaluate stable interior samples, excluding transition latency and the
    # first field needed for real-source H/V qualification. Truth is not a
    # segmentation input; this mask only scores the already-produced output.
    safe = np.arange(len(codes)) >= len(codes) // 2
    masks = {k: ndimage.binary_erosion(region == k, iterations=40) & safe for k in (0, 1, 2, 3)}
    tip = float(np.median(codes[masks[2]])); porch = float(np.median(codes[masks[0]]))
    white = float(np.percentile(codes[masks[1]], 99))
    low = codes < (tip + porch) / 2.
    active_crossings = np.count_nonzero(masks[1][1:] & masks[1][:-1] & low[1:] & ~low[:-1])
    derivative_mask = masks[1][1:] & masks[1][:-1]
    detail = np.diff(codes.astype(float))[derivative_mask]
    base_detail = np.diff(baseline.astype(float))[derivative_mask]
    base_energy = float(np.mean(base_detail ** 2))
    result.update(sync_depth_codes=porch - tip, tip_code=tip, porch_code=porch,
                  white_p99_code=white, white_headroom_codes=63. - white,
                  model_sync_depth_v=float(np.median(VOLTS[codes[masks[0]]]) -
                                           np.median(VOLTS[codes[masks[2]]])),
                  active_difference_rms_codes=float(np.sqrt(np.mean(
                      (codes[masks[1]].astype(float) - baseline[masks[1]]) ** 2))),
                  active_changed_fraction=float(np.mean(codes[masks[1]] != baseline[masks[1]])),
                  burst_changed_fraction=float(np.mean(codes[masks[3]] != baseline[masks[3]])),
                  active_false_sync_crossings=int(active_crossings),
                  active_false_sync_fraction=float(np.mean(low[masks[1]])),
                  active_slope_rms_ratio=float(np.sqrt(np.mean(detail ** 2) / base_energy))
                      if base_energy else None)
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--quick', action='store_true')
    ap.add_argument('--output', type=Path, default=HERE / 'data/output_abc.json')
    ap.add_argument('--check', action='store_true')
    args = ap.parse_args(); checks()
    if args.check:
        print('PASS AUTO research bounds: missing evidence holds, floor conflicts refuse, finite swing limits depth')
        return
    rows, manifests = [], []
    with tempfile.TemporaryDirectory(prefix='c5vrx-output-abc-') as folder:
        work = Path(folder)
        compile_cmd = ['docker', 'run', '--rm', '-v', ROOT.as_posix() + ':/project:ro',
                       '-v', work.as_posix() + ':/data', '-w', '/project', 'gcc:14',
                       'gcc', '-O2', '-std=c11', '-Wall', '-Wextra', '-Werror', '-Imain',
                       'research/output_abc_replay.c', 'main/sync_flywheel.c', '-o', '/data/replay']
        subprocess.run(compile_cmd, check=True)
        for spec, case in sources(args.quick):
            raw = case['raw']; (work / 'source.bin').write_bytes(raw.tobytes())
            sha = hashlib.sha256(raw.tobytes()).hexdigest()
            manifests.append(dict(**spec, iq_sha256=sha, samples=len(raw)))
            # One isolated compiler container per IQ input; each execution
            # starts fresh keeper state and writes its own output/table.
            command = ('for kp in 0 1 2 3 4 5 6 7; do '
                       '/data/replay /data/source.bin /data/repaired-$kp.bin "$kp" '
                       '> /data/keeper-$kp.json || exit; done')
            subprocess.run(['docker', 'run', '--rm', '-v', work.as_posix() + ':/data',
                            'gcc:14', 'bash', '-c', command], check=True)
            for kp in range(8):
                keeper = json.loads((work / f'keeper-{kp}.json').read_text())
                assert not keeper['bootstrap_generated'] and not keeper['commit_refused']
                repaired = np.frombuffer((work / f'repaired-{kp}.bin').read_bytes(), np.uint8)
                base_lut, half_lut = np.frombuffer((work / f'repaired-{kp}.bin.lut').read_bytes(), '<u2').reshape(2, 1024)
                assert np.array_equal(base_lut & 0xffc0, half_lut & 0xffc0)
                assert not keeper['half_lut_clipped']
                for lp in (False, True):
                    for parity in (0, 1):
                        baseline = decode(raw, base_lut, lp, parity)
                        for mode, iq, lut in (('1x', raw, base_lut), ('0.5x', raw, half_lut),
                                               ('sync_priority', repaired if lp else raw, base_lut)):
                            y = decode(iq, lut, lp, parity)
                            worker = decode(iq, lut, False, parity)
                            m = metrics(y, baseline, worker, case, parity)
                            preservation = None if not spec['synthetic'] else m['active_changed_fraction'] == 0.
                            rows.append(dict(case=spec['name'], iq_sha256=sha, kp_x100=85 - 5 * kp,
                                lp=lp, parity=parity, mode=mode,
                                eligible=mode != 'sync_priority' or lp and keeper['real_v_fields'] > 0,
                                active_preservation_pass=preservation,
                                eligibility_reason='actual1xLP_realVqualified' if mode == 'sync_priority' and lp and keeper['real_v_fields'] else
                                    'real_source_V_clock_unqualified' if mode == 'sync_priority' and lp else
                                    'nonLP_keeper_unqualified_passthrough' if mode == 'sync_priority' else 'actual_output_entry',
                                keeper=keeper if mode == 'sync_priority' else None, **m))
            print(spec['name'], sha[:12], 'all8kp/LP/bothparities done', flush=True)
    artifact = dict(evidence='offline same-IQ comparison; no LCD/RF range/loaded-voltage acceptance',
        source_comments=[6156589968, 6166374209, 6167109293],
        provenance='C5VRX by Twotoz and contributors; https://github.com/Twotoz/C5VRX; https://twotoz.github.io/C5VRX/',
        limitations=['nominal3.3V resistor/200ohm/75ohm model only, no voltage sensor',
                     'Sync-Priority repairs only actual1xU85Q-LP; nonLP refuses to passthrough',
                     'real8190byte windows cannot establish a full H/V clock',
                     'synthetic truth regions score outputs but never authorize keeper repairs',
                     'no optional active limiter: preserve active baseline exactly'],
        sources=manifests, rows=rows,
        implementation_sha256={p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in (
            'main/output_level.h', 'main/kp_tables.h', 'main/u85q.bsasm', 'main/u85q_lp.bsasm',
            'main/sync_flywheel.c', 'research/u85q_lp.py', 'research/sim.py',
            'research/output_abc.py', 'research/output_abc_replay.c')},
        auto_examples=[auto_candidate(12, 55, 2, 5, True, True, True),
                       auto_candidate(2, 60, .5, 5, True, True, True)])
    args.output.write_text(json.dumps(artifact, indent=2) + '\n')
    print('wrote', args.output, 'rows', len(rows), flush=True)


if __name__ == '__main__':
    main()

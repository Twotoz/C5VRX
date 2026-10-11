"""C5VRX/Twotoz oracle retuning bound, not a received-only controller.

GPL-3.0-only. Tests whether ideal known-direction bounded retuning could
restore U85Q in the target stress class. Fresh channel simulation places the
new carrier before the analog filter, not by rotating already filtered IQ.
No simulated PHY transient, AGC response, safe write or physical range claim.
"""
import argparse
import hashlib
import json
from pathlib import Path
import local_joint_observation as J


def study(seed, repeats, out):
    import waveforms as V
    import goggle_lock as G
    rows = []
    for n in range(repeats):
        for k, standard in enumerate(('PAL', 'NTSC')):
            case_seed = seed+101*n+17*k
            for correction_khz in (0, -1000, -1500):
                case = V.make_case(standard, case_seed, 6, 4.2,
                    lane_model='ultrafine', deviation=1.2,
                    cfo_hz=-1e6-correction_khz*1000,
                    stress='both', stimulus_seed=case_seed+1,
                    pattern=('zoneplate', 'checker')[k])
                model = J.baseline()
                metrics = G.measure(J.O.decode(case['raw'], model),
                                    J.O.decode(case['clean'], model), case)
                row = dict(seed=case_seed, standard=standard,
                    ideal_correction_khz=correction_khz,
                    input_sha256=hashlib.sha256(case['raw'].tobytes()).hexdigest(),
                    U85Q=metrics)
                rows.append(row)
                print(json.dumps(row), flush=True)
    target = J.ROOT/'docs/data/local_joint'/out
    target.mkdir(parents=True, exist_ok=True)
    report = dict(seed=seed, repeats=repeats, rows=rows,
        scope='oracle_direction_settled_RF_bound_not_a_demodulator_or_controller',
        calibration='inherited_per_case_clean_oracle_goggle',
        limits='1000kHz_current_four_step_budget_1500kHz_RF_clamp_no_transient_or_settling_model',
        source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    (target/'results.json').write_text(json.dumps(report, indent=1))
    (target/'source.py').write_bytes(Path(__file__).read_bytes())
    print('DONE', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--seed', type=int, default=8400190)
    parser.add_argument('--repeats', type=int, default=4)
    parser.add_argument('--out', default='iq-recenter-oracle-bound')
    args = parser.parse_args()
    study(args.seed, args.repeats, args.out)

"""C5VRX/Twotoz received-only sync-frequency protection ablation.

GPL-3.0-only; https://github.com/Twotoz/C5VRX
Heuristic selected after seed8700190, not a physical safe-frequency theorem.
No firmware integration, sample-rate CPU implementation or board acceptance.
"""
import argparse
import hashlib
import json
from pathlib import Path
import local_iq_acquisition_loop as L


def protection(observation):
    # U85Q's coarse .5 innovation gain has a +/-5MHz instantaneous bound
    # at its20MS/s state rate. A10% interior margin is a design heuristic,
    # not a proved boundary of lock, aliasing or a physical C5 measurement.
    sync = observation['frequency_estimates']['sync']['long_lag']['frequency_hz']
    request = observation['decision']['proposed_single_step_khz']
    permit = (sync < -4500000 and request < 0) or (sync > 4500000 and request > 0)
    return dict(permit_correction=bool(permit), sync_frequency_hz=sync,
                interior_margin_hz=4500000,
                reason='outside_sync_frequency_window_and_matching_correction_direction'
                    if permit else 'retain_current_RF_setting')


def study(seed, out):
    # Frozen before the fresh seed9000190 run. Includes missing-target risks
    # inside the guard and stronger CFO outside the original stress class.
    conditions = [(6,1.2,-1e6,'both'), (6,1.2,-1e6,'both'),
        (6,1.2,-2e6,'both'), (6,1.2,-.5e6,'both'),
        (6,1.2,0.,'both'), (6,1.2,1e6,'both'),
        (4,.69,1e6,False), (6,1.4,-1e6,'both'),
        (30,1.2,3e6,False), (6,1.2,-1e6,False)]
    rows=[]
    for n,(cnr,deviation,cfo,stress) in enumerate(conditions):
        for k,standard in enumerate(('PAL','NTSC')):
            spec=dict(seed=seed+101*n+17*k,standard=standard,cnr=cnr,
                deviation=deviation,source_cfo_hz=cfo,stress=stress)
            result=L.run_case(spec,3,protection=protection)
            rows.append(result)
            print(json.dumps(dict(seed=spec['seed'],standard=standard,
                source_cfo_hz=cfo,deviation=deviation,
                offset=result['final_configured_offset_khz'],
                before_h=min(result['matched_uncorrected_U85Q']['h_ok'],
                             result['matched_uncorrected_U85Q']['peak_h_ok']),
                after_h=min(result['final_U85Q']['h_ok'],result['final_U85Q']['peak_h_ok']))),
                flush=True)
    target=L.C.J.ROOT/'docs/data/local_joint'/out
    report=dict(seed=seed,rows=rows,retries=3,
        scope='fresh_settled_controller_ablation_not_C5_acceptance_or_global_benefit',
        threshold_provenance='4.5MHz_heuristic_selected_after_seed8700190_before_seed9000190',
        missing='current_Phase8_MAD_burst_TRACK_transient_AGC_DMA_drift_CPU',
        source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        source_hashes={Path(m.__file__).name:hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest()
                       for m in (L,L.C)})
    (target/'results.json').write_text(json.dumps(report,indent=1))
    print('DONE',flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--seed',type=int,default=9000190)
    p.add_argument('--out',default='iq-sync-guard-validation')
    a=p.parse_args()
    target=L.C.J.ROOT/'docs/data/local_joint'/a.out
    target.mkdir(parents=True,exist_ok=True)
    # Freeze sources before generating any result; do not choose the guard
    # with validation labels. Labelled waveforms only enter final evaluation.
    (target/'source.py').write_bytes(Path(__file__).read_bytes())
    for m in (L,L.C):
        (target/Path(m.__file__).name).write_bytes(Path(m.__file__).read_bytes())
    study(a.seed,a.out)

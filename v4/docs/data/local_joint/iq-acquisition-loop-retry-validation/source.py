"""C5VRX/Twotoz settled-snapshot IQ acquisition-loop reference.

GPL-3.0-only. No true CFO/CNR/standard/levels reach the decision function.
This tests a proposed replacement acquisition decision, not the existing AFC
controller: its16 Phase8 observations/MAD, burst and sticky TRACK are NOT
implemented here. RF step/count/clamp and100ms post-write delay are retained.
No physical transient, AGC, DMA ownership, camera drift or CPU timing proof.
"""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
import local_iq_carrier as C


def observation(raw):
    clock, _ = C.R.acquire(raw[:160000], stride=127)
    next_clock = dict(clock)
    next_clock['phase'] = (clock['phase']-160000) % clock['period']
    frequency = C.estimate(raw[160000:], next_clock, full_pulse=True)
    gate = C.decision(clock, frequency)
    # Existing target/deadband. No repeated 16-observation stability gate.
    if abs(frequency['porch']['colored_covariance']['frequency_hz']+436000) <= 50000:
        gate['proposed_single_step_khz'] = 0
    return dict(clock=clock, frequency_estimates=frequency, decision=gate,
                input_sha256=hashlib.sha256(raw.tobytes()).hexdigest())


def run_case(spec, retries=1):
    import waveforms as V
    import goggle_lock as G
    events = []
    configured_khz = 0
    last_write_us = None
    clock_us = 0
    model = C.J.baseline()

    def case(noise_seed, correction):
        return V.make_case(spec['standard'], noise_seed, spec['cnr'], 4.2,
            lane_model='ultrafine', deviation=spec['deviation'],
            cfo_hz=spec['source_cfo_hz']-correction*1000,
            stress=spec['stress'], stimulus_seed=spec['seed']+1,
            pattern='zoneplate' if spec['standard']=='PAL' else 'checker')

    for epoch in range(5):
        if last_write_us is not None:
            clock_us = max(clock_us, last_write_us+100000)
        # Fresh noise, and a fresh phase hypothesis after every PHY change.
        for attempt in range(retries):
            noise_seed = spec['seed']+10007*epoch+193*attempt
            current = case(noise_seed, configured_khz)
            crop = int(np.random.default_rng(noise_seed+3).integers(0, 240000))
            raw = current['raw'][crop:crop+320000]
            estimate = observation(raw)
            if estimate['decision']['admitted'] or attempt==retries-1:
                break
            events.append(dict(epoch=epoch, attempt=attempt, noise_seed=noise_seed,
                crop_raw_bytes=crop, start_us=clock_us, end_us=clock_us+8000,
                configured_offset_khz=configured_khz, hypothetical_write_step_khz=0,
                observation=estimate))
            clock_us += 8000
        requested = estimate['decision']['proposed_single_step_khz'] if epoch < 4 else 0
        bounded_next = int(np.clip(configured_khz+requested, -1500, 1500))
        step = bounded_next-configured_khz
        event = dict(epoch=epoch, attempt=attempt, noise_seed=noise_seed, crop_raw_bytes=crop,
            start_us=clock_us, end_us=clock_us+8000,
            configured_offset_khz=configured_khz,
            hypothetical_write_step_khz=step, observation=estimate)
        events.append(event)
        clock_us += 8000
        if not step:
            break
        configured_khz = bounded_next
        last_write_us = clock_us
    assert len(events) <= 5*retries
    writes = [e for e in events if e['hypothetical_write_step_khz']]
    assert len(writes) <= 4 and all(abs(e['hypothetical_write_step_khz']) <= 250 for e in writes)
    assert abs(configured_khz) <= 1500
    for previous, following in zip(events, events[1:]):
        if previous['hypothetical_write_step_khz']:
            assert following['start_us']-previous['end_us'] >= 100000
    # Labels and clean calibration are introduced only for final evaluation.
    # Matched final noise/scene: comparison is not initial noise vs final noise.
    untouched = case(noise_seed, 0)
    def measure(c):
        return G.measure(C.J.O.decode(c['raw'], model), C.J.O.decode(c['clean'], model), c)
    before, after = measure(untouched), measure(current)
    return dict(spec=spec, events=events, final_configured_offset_khz=configured_khz,
                elapsed_schedule_us=clock_us, matched_uncorrected_U85Q=before,
                final_U85Q=after,
                final_raw_sha256=hashlib.sha256(current['raw'].tobytes()).hexdigest(),
                uncorrected_raw_sha256=hashlib.sha256(untouched['raw'].tobytes()).hexdigest())


def study(seed, repeats, out, retries=1):
    conditions = [(6, 1.2, -1e6, 'both')]*repeats + [
        (6, 1.2, 1e6, 'both'), (4, .69, 1e6, False), (30, 1.2, 3e6, False)]
    rows = []
    for n, (cnr, deviation, cfo, stress) in enumerate(conditions):
        for k, standard in enumerate(('PAL', 'NTSC')):
            spec = dict(seed=seed+101*n+17*k, standard=standard, cnr=cnr,
                        deviation=deviation, source_cfo_hz=cfo, stress=stress)
            result = run_case(spec, retries)
            rows.append(result)
            print(json.dumps(dict(seed=spec['seed'], standard=standard,
                offset=result['final_configured_offset_khz'],
                writes=sum(bool(e['hypothetical_write_step_khz']) for e in result['events']),
                before_h=min(result['matched_uncorrected_U85Q']['h_ok'],result['matched_uncorrected_U85Q']['peak_h_ok']),
                after_h=min(result['final_U85Q']['h_ok'],result['final_U85Q']['peak_h_ok']))), flush=True)
    target = C.J.ROOT/'docs/data/local_joint'/out
    target.mkdir(parents=True, exist_ok=True)
    report = dict(seed=seed, repeats=repeats, retries=retries, rows=rows,
        scope='settled_snapshot_proposed_controller_not_existing_AFC_or_hardware_acceptance',
        calibration='per_case_clean_oracle_only_final_goggle_evaluation',
        retained_constraints='250kHz_steps_four_writes_1500kHz_clamp_100ms_post_write_delay',
        missing='16_Phase8_observations_MAD_burst_sticky_TRACK_transient_AGC_DMA_camera_drift_CPU_deadlines',
        source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        observer_source_sha256=hashlib.sha256(Path(C.__file__).read_bytes()).hexdigest())
    (target/'results.json').write_text(json.dumps(report, indent=1))
    (target/'source.py').write_bytes(Path(__file__).read_bytes())
    (target/'local_iq_carrier.py').write_bytes(Path(C.__file__).read_bytes())
    print('DONE', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--seed', type=int, default=8600190)
    parser.add_argument('--repeats', type=int, default=2)
    parser.add_argument('--out', default='iq-acquisition-loop-development')
    parser.add_argument('--retries', type=int, choices=(1,3), default=1)
    args = parser.parse_args()
    study(args.seed, args.repeats, args.out, args.retries)

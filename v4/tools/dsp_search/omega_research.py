#!/usr/bin/env python3
"""Reproducible Ω belief-FSM study for C5VRX by Twotoz/contributors.

Run from repository root with PYTHONPATH=v4/tools:v4/tools/dsp_search.
Truth is used only for offline ranking/scoring. Runtime models receive IQ.
Fits are noisy surrogate AutoFit measurements, explicitly not blind estimates.
Reports corrected legacy clamp AND a per-case clean-counterpart frozen lag.
"""
import argparse
import hashlib
import json
import time
from pathlib import Path
import numpy as np
import omega_bayes as B
import omega_student as S
import overlay_fsm as O
import edge_fsm as E
import search_edge as SE
import ladder_edge as LE
import compile_overlay as C
from pair_autofit import remap
from video_metrics import waveform, detail
import waveforms as V

ROOT = Path(__file__).resolve().parents[2]


def controls():
    opts = json.loads((ROOT / 'tools/range_options.json').read_text())['options']
    edge = next(o['model'] for o in opts if o['label'] == 'EDGE RANGE LAB')
    pair = next(o['model'] for o in opts if o['label'] == 'PAIR RANGE LAB')
    return {'EDGE+AF': lambda c: O.decode(c['raw'], E.synthesize(dict(edge['params'], **c['fit']))),
            'PAIR+AF': lambda c: O.decode(c['raw'], remap(pair, c['fit']['fit_deviation'], c['fit']['fit_centre_hz'])),
            'RANGE32': lambda c: O.decode(c['raw'], json.loads((ROOT / 'tools/range32_model.json').read_text()))}


def measure(c, decode):
    y = decode(c); clean = decode(dict(c, raw=c['clean']))
    # Choose ONE lag on the noiseless counterpart and freeze it for the
    # corresponding noisy/stressed stream; no noisy-truth lag selection.
    cc = SE.clamp(c, clean); lag, gain, offset = cc['calibration']
    a, t, region = V.calibrated(y, cc); porch = region == 3
    if porch.any(): offset += float(np.mean(t[porch] - a[porch]))
    frozen = dict(c, calibration=(lag, gain, offset))
    r = waveform(y, frozen); r['detail'] = detail(y, frozen)
    corrected = waveform(y, SE.clamp(c, y))
    r['corrected_sinad'] = corrected['sinad']; r['frozen_lag'] = lag
    r['corrected_missing'] = corrected['h_missing'] + corrected['v_missing']
    a, t, region = V.calibrated(y, frozen)
    r['clicks_per_1000'] = float(1000 * np.mean(abs(a-t) > 40))
    r['click_p99_ire'] = float(np.percentile(abs(a-t), 99))
    large = abs(a-t) > 40
    events = np.count_nonzero(np.diff(np.r_[False,large].astype(np.int8)) == 1)
    r['error_excursions_per_ms'] = float(events / (len(a)/40000.))
    return {k: v.item() if isinstance(v, np.generic) else v for k, v in r.items()}


def utility(rows):
    # Predeclared training-only scalar. Independent results never retune it.
    return float(np.mean([r['sinad'] - 3*(r['h_missing']+r['v_missing']) -
                         10*r['false_sync_per_line'] for r in rows]))


def summarize(rows):
    out = {}
    for r in rows:
        out.setdefault(f"{r['cnr']}/{r['model']}", []).append(r['metrics'])
    return {k:dict(cases=len(v), missed=sum(x['h_missing']+x['v_missing'] for x in v),
                  corrected_missed=sum(x['corrected_missing'] for x in v),
                  sinad=float(np.mean([x['sinad'] for x in v])),
                  corrected_sinad=float(np.mean([x['corrected_sinad'] for x in v])),
                  detail=float(np.mean([x['detail'] for x in v])),
                  false=float(np.mean([x['false_sync_per_line'] for x in v])),
                  clicks=float(np.mean([x['clicks_per_1000'] for x in v]))) for k,v in out.items()}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--train-seed', type=int, default=610190)
    ap.add_argument('--test-seeds', type=int, nargs='+', default=[710190,810190])
    ap.add_argument('--per', type=int, default=2)
    ap.add_argument('--rounds', type=int, default=6)
    ap.add_argument('--teacher-jumps', type=float, nargs='+', default=[.06,.18,.35])
    ap.add_argument('--replay', type=Path, nargs='*', default=[])
    args = ap.parse_args()
    if args.output.exists(): ap.error('fresh evidence directory required')
    args.output.mkdir(parents=True)
    train = LE.cases(args.train_seed, per=args.per, afc=True)
    # Full-range training, no C/N-specific runtime gain or LUT selection.
    teachers = []; t0 = time.time()
    for jump in args.teacher_jumps:
        import lane_profile as L
        cfg = B.setup(jump=jump, lane=L.LANE, rms=L.RMS)
        rows=[]; sequences=[]
        for c in train:
            y, features, hz = B.decode(c['raw'], cfg)
            rows.append(measure(c, lambda cc:B.decode(cc['raw'],cfg,cc['fit']['fit_deviation'],cc['fit']['fit_centre_hz'])[0]))
            sequences.append(dict(raw=c['raw'],features=features,hz=hz))
        score=utility(rows); teachers.append((score,cfg,sequences,rows))
        print('teacher',jump,'training utility',score,'elapsed',round(time.time()-t0),flush=True)
        (args.output/f'teacher_jump{jump}.json').write_text(json.dumps(rows,indent=1))
    _,cfg,sequences,_=max(teachers,key=lambda v:v[0])
    teacher_cfg={k:v for k,v in cfg.items() if k in ('phases','frequencies','lane','rms','jump','contamination')}
    (args.output/'teacher_config.json').write_text(json.dumps(teacher_cfg,indent=1))
    candidates=[]
    for bits in (2,3,4):
        for layout in ('4411','3322','2233'):
            model,fit=S.fit(sequences,bits,layout,args.train_seed+bits,args.rounds)
            rows=[measure(c,lambda cc:O.decode(cc['raw'],remap(model,cc['fit']['fit_deviation'],cc['fit']['fit_centre_hz']))) for c in train]
            score=utility(rows); candidates.append((score,model))
            key=f'student_b{bits}_{layout}'
            (args.output/f'{key}.json').write_text(json.dumps(dict(training_utility=score,fit=fit,metrics=rows,model=model),indent=1))
            print(key,'training utility',score,flush=True)
    score,selected=max(candidates,key=lambda v:v[0])
    selected['training_utility']=score
    (args.output/'selected_model.json').write_text(json.dumps(selected,indent=1))
    (args.output/'selected.bsasm').write_text(C.build(selected))
    controls_ = controls()
    controls_['OMEGA']=lambda c:O.decode(c['raw'],remap(selected,c['fit']['fit_deviation'],c['fit']['fit_centre_hz']))
    controls_['Bayes teacher']=lambda c:B.decode(c['raw'],cfg,c['fit']['fit_deviation'],c['fit']['fit_centre_hz'])[0]
    rows=[]
    for seed in args.test_seeds:
        for c in LE.cases(seed,per=args.per,afc=True):
            for name,decode in controls_.items():
                r=measure(c,decode); rows.append(dict(seed=c['seed'],cnr=c['cnr'],standard=c['standard'],pattern=c['pattern'],model=name,metrics=r,
                    iq_sha256=hashlib.sha256(c['raw'].tobytes()).hexdigest()))
            # Only a labelled oracle policy comparator, not simulated deployed
            # Fusion hysteresis, phase-C/N estimator, reload or continuity.
            pick='EDGE+AF' if c['cnr']<13 else 'PAIR+AF'
            rows.append(dict(rows[-5+list(controls_).index(pick)],model='Fusion oracle policy'))
            (args.output/'heldout.json').write_text(json.dumps(dict(protocol=vars(args)|{'output':str(args.output),'replay':[str(p) for p in args.replay]},
                teacher=teacher_cfg,summary=summarize(rows),rows=rows),indent=1))
            print('heldout',seed,c['cnr'],c['standard'],flush=True)
    replay=[]
    for path in args.replay:
        raw=np.fromfile(path,np.uint8)
        code=O.model_codes(raw,selected)
        replay.append(dict(path=path.name,sha256=hashlib.sha256(raw).hexdigest(),samples=len(raw),
                           large_steps=int(np.sum(abs(np.diff(code.astype(int)))>20))))
    (args.output/'replay.json').write_text(json.dumps(dict(available=bool(args.replay),rows=replay),indent=1))
    print(json.dumps(summarize(rows),indent=1),flush=True)

if __name__=='__main__':main()

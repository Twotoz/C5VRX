#!/usr/bin/env python3
"""Freeze individually confirmed range options for a fresh matched comparison.

C5VRX by Twotoz/contributors. Run validate_range.py on the resulting directory;
its selection and final datasets are fresh and shared across these candidates.
"""
import argparse
import hashlib
import json
from pathlib import Path
import engine as S
import overlay_fsm as O
import refine_overlay as F
import range_objective as J


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--confirmed',type=Path,nargs='+',required=True)
    ap.add_argument('--output',type=Path,required=True)
    a=ap.parse_args()
    if a.output.exists():ap.error('fresh comparison directory required')
    models=[];seen=set();parents=[]
    for directory in a.confirmed:
        summary=json.loads((directory/'summary.json').read_text())
        if summary['profile']!='safe_range':ap.error('only safe_range confirmations allowed')
        parents.append(dict(summary_sha256=hashlib.sha256((directory/'summary.json').read_bytes()).hexdigest(),
                            confirmed=summary['confirmed_range_improvement'],selected=summary['selected']))
        if not summary['confirmed_range_improvement']:continue
        model=json.loads((directory/'confirmed_model.json').read_text());O.compile_model(model)
        digest=F.digest(model)
        if model['name']!='RNG-'+digest[:12] or model['name']!=summary['selected']:
            raise ValueError('confirmed model identity mismatch')
        if digest not in seen:models.append(dict(id=digest,model=model));seen.add(digest)
    a.output.mkdir(parents=True)
    S.save(a.output/'protocol.json',dict(profile='safe_range',guards=J.PROFILES['safe_range'],
        registered_utc='2026-10-08 09:24:45',
        scope='fresh shared-input comparison of individually confirmed options; not a new million-model search',
        selection_seed=200301,final_seeds=[200401,200402],stress_seed=200501,
        parents=parents,unique_finalists=len(models),
        decision='freeze once; independent final veto; no runner-up after veto; no automatic flash/promotion',
        source_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob('*.py')}))
    S.save(a.output/'frozen.json',dict(finalists=models,no_reselection=True))
    print(f'Frozen {len(models)} individually confirmed unique options; validate only if nonzero')


if __name__=='__main__':main()

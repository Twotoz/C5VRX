#!/usr/bin/env python3
"""Read-only verification of C5VRX/Twotoz PHY lab raw/derived evidence.

Checks all archived IQ byte counts, SHA-256/FNV hashes, CSV identity and
profile completeness. Hardware PASS and intact host transport are separate:
damaged captures must keep their derived profile INCOMPLETE. This verifies
artifact consistency; it does not independently validate closed PHY semantics
or measure RF sensitivity. Source: https://github.com/Twotoz/C5VRX
Official website and Discord invite: https://twotoz.github.io/C5VRX/
"""
import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import re
import math
import numpy as np
import datetime as dt
import importlib.metadata

from phy_noise_lab import fnv,captures_complete,entry_checks


def verify_run(path,expected,spectra=False):
    raw_hash=hashlib.sha256((path/'console.raw.log').read_bytes()).hexdigest()
    manifest_bytes=(path/'iq/manifest.json').read_bytes()
    if raw_hash!=expected['raw_log_sha256']:raise ValueError(f'{path.name}: raw log changed')
    if hashlib.sha256(manifest_bytes).hexdigest()!=expected['manifest_sha256']:raise ValueError(f'{path.name}: manifest changed')
    manifest=json.loads(manifest_bytes)
    csv_rows=list(csv.DictReader((path/'captures.csv').open()))
    if len(csv_rows)!=len(manifest['captures']):raise ValueError(f'{path.name}: CSV count mismatch')
    csv_by_file={row['file']:row for row in csv_rows}
    if len(csv_by_file)!=len(csv_rows):raise ValueError(f'{path.name}: duplicate CSV identity')
    seen=set();counts=Counter();profiles={};windows={};moments={};psds={}
    if spectra:
        from scipy.signal import welch
    for meta in manifest['captures']:
        name=meta['file']
        if not re.fullmatch(r'p\d+_s[0-3]_r\d+\.iq',name):raise ValueError(f'unsupported IQ filename {name}')
        raw=(path/'iq'/name).read_bytes()
        digest=hashlib.sha256(raw).hexdigest()
        if len(raw)!=8190 or digest!=meta['sha256'] or fnv(raw)!=meta['fnv1a']:
            raise ValueError(f'{path.name}/{name}: raw IQ integrity failure')
        if digest in seen:raise ValueError(f'{path.name}: duplicate raw noise snapshot')
        seen.add(digest)
        row=csv_by_file.get(name)
        if row is None or any(row.get(key)!=value for key,value in meta.items()):
            raise ValueError(f'{path.name}/{name}: CSV metadata mismatch')
        if meta['freq']!=expected['context']['freq'] or meta['bytes']!='8190' or meta['rate_hz']!='40000000':
            raise ValueError(f'{path.name}/{name}: snapshot context/geometry mismatch')
        b=np.frombuffer(raw,dtype=np.uint8);i=(b>>4).astype(float);q=(b&15).astype(float)
        i[i>=8]-=16;q[q>=8]-=16
        z=(i-i.mean())+1j*(q-q.mean());m2=float(np.mean(abs(z)**2))
        derived=dict(m2_host=m2,kurt_host=float(np.mean(abs(z)**4)/m2**2),dc_i=float(i.mean()),dc_q=float(q.mean()),
            signed_rails=float(np.mean((i==7)|(i==-8)|(q==7)|(q==-8))))
        for key,value in derived.items():
            if not math.isclose(value,float(row[key]),rel_tol=1e-10,abs_tol=1e-12):
                raise ValueError(f'{path.name}/{name}: derived {key} mismatch')
        key=(meta['profile'],meta['stage']);moments.setdefault(key,[]).append(m2)
        if spectra:
            frequency,power=welch(z,fs=40e6,window='hann',nperseg=1024,noverlap=512,detrend='constant',return_onesided=False,scaling='density')
            order=np.argsort(frequency);frequency=frequency[order];power=power[order]
            if key not in psds:psds[key]=[power,1]
            else:psds[key][0]+=power;psds[key][1]+=1
        identity=meta['id'];counts[identity]+=1;profiles[identity]=meta['profile']
        windows[identity]=4*int(meta.get('windows_per_stage','4'))
    summary={s['profile']:s for s in json.loads((path/'summary.json').read_text())}
    for profile,result in summary.items():
        if result['status']!='PASS':continue
        if not captures_complete([r for r in manifest['captures'] if r['profile']==profile]):
            raise ValueError('accepted profile has an incomplete stage')
        stock=float(np.mean(moments[(profile,'STOCK')]));test=float(np.mean(moments[(profile,'TEST')]))
        delta=10*math.log10(test/stock)
        if not math.isclose(delta,result['delta_db'],rel_tol=1e-9,abs_tol=1e-10):raise ValueError('profile delta mismatch')
        if 'entries' in result:
            computed,entry_flags=entry_checks([r for r in csv_rows if r['profile']==profile])
            if len(computed)!=len(result['entries']):raise ValueError('entry count mismatch')
            for actual,saved in zip(computed,result['entries']):
                if actual['id']!=saved['id'] or any(not math.isclose(actual[k],saved[k],rel_tol=1e-9,abs_tol=1e-10)
                        for k in ('delta_db','stock_drift_db','test_repeat_db')):raise ValueError('entry statistic mismatch')
            if not set(entry_flags)<=set(result['quality_flags']):raise ValueError('entry quality flags missing')
        if spectra:
            with np.load(path/f'psd_{profile}.npz',allow_pickle=False) as stored:
                if not np.array_equal(frequency,stored['frequency_hz']):raise ValueError('PSD frequency mismatch')
                for stage,label in (('STOCK','stock'),('TEST','test')):
                    power,n=psds[(profile,stage)];power=power/n
                    if not np.allclose(power,stored[label],rtol=1e-10,atol=1e-30):raise ValueError('PSD power mismatch')
                    reference=(abs(frequency)>=.5e6)&(abs(frequency)<=2e6)
                    nbw=float(np.sum(power)*(frequency[1]-frequency[0])/np.mean(power[reference])/1e6)
                    if not math.isclose(nbw,result[label+'_nbw_mhz'],rel_tol=1e-10):raise ValueError('PSD NBW mismatch')
    incomplete=[]
    missing=[identity for identity,result in manifest['results'].items() if result.get('status')=='PASS' and identity not in counts]
    if missing:raise ValueError(f'{path.name}: complete execution missing from capture manifest: {missing}')
    for identity,count in counts.items():
        result=manifest['results'].get(identity,{})
        if result.get('status')=='PASS' and count!=windows[identity]:
            incomplete.append(dict(id=identity,profile=profiles[identity],valid=count,expected=windows[identity]))
            if summary[profiles[identity]]['status']=='PASS':
                raise ValueError(f'{path.name}: incomplete transport accepted as complete')
    if len(csv_rows)!=expected['valid_captures']:raise ValueError(f'{path.name}: aggregate count mismatch')
    return dict(run=path.name,valid_captures=len(csv_rows),rejected_attempts=len(manifest.get('rejected_captures',[])),
        incomplete_host_exports=incomplete,raw_log_sha256=raw_hash)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    root=Path(__file__).resolve().parents[1]
    parser.add_argument('--raw',type=Path,default=root/'research/data/nf_phy_2026-10-24')
    parser.add_argument('--summary',type=Path,default=root/'research/data/phy_audit_2026-10-24_summary.json')
    parser.add_argument('--output',type=Path)
    parser.add_argument('--spectra',action='store_true',help='also recompute every Welch PSD and equivalent noise bandwidth')
    args=parser.parse_args()
    aggregate=json.loads(args.summary.read_text(encoding='utf-8'))
    for field in ('additional_static_evidence','auxiliary_static_evidence'):
        evidence=aggregate.get(field)
        if evidence and hashlib.sha256((args.raw/evidence['file']).read_bytes()).hexdigest()!=evidence['sha256']:
            raise ValueError(f'{field}: static evidence changed')
    results=[verify_run(args.raw/run['name'],run,args.spectra) for run in aggregate['runs']]
    total=sum(r['valid_captures'] for r in results)
    if total!=aggregate['total_valid_captures']:raise ValueError('aggregate total mismatch')
    live_count=0
    for run in aggregate.get('live_gain_witnesses',[]):
        path=args.raw/run['name'];manifest_bytes=(path/'manifest.json').read_bytes()
        if hashlib.sha256(manifest_bytes).hexdigest()!=run['manifest_sha256']:raise ValueError('live manifest changed')
        manifest=json.loads(manifest_bytes)
        if hashlib.sha256((path/'console.raw.log').read_bytes()).hexdigest()!=manifest['raw_log_sha256']:
            raise ValueError('live log changed')
        for capture in manifest['captures']:
            if Path(capture['file']).name!=capture['file']:raise ValueError('invalid live filename')
            raw=(path/capture['file']).read_bytes()
            if len(raw)!=8190 or hashlib.sha256(raw).hexdigest()!=capture['sha256'] or fnv(raw)!=capture['metadata']['fnv1a']:
                raise ValueError('live IQ integrity failure')
            live_count+=1
    if live_count!=aggregate.get('total_live_witness_captures',live_count):raise ValueError('live count mismatch')
    attempts=aggregate.get('attempts_without_derived_measurement',[])
    for attempt in attempts:
        path=args.raw/attempt['name']
        if hashlib.sha256((path/'console.raw.log').read_bytes()).hexdigest()!=attempt['raw_log_sha256']:
            raise ValueError('no-measurement attempt log changed')
        if attempt.get('manifest_sha256') and hashlib.sha256((path/'iq/manifest.json').read_bytes()).hexdigest()!=attempt['manifest_sha256']:
            raise ValueError('no-measurement attempt manifest changed')
    report=dict(scope=__doc__,status='PASS',audit_runs=len(results),audit_captures=total,live_captures=live_count,
        no_measurement_attempts=len(attempts),
        verified_utc=dt.datetime.now(dt.timezone.utc).isoformat(),verifier_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        numpy_version=np.__version__,scipy_version=importlib.metadata.version('scipy') if args.spectra else None,
        recomputed_spectra=args.spectra,aggregate_sha256=hashlib.sha256(args.summary.read_bytes()).hexdigest(),runs=results)
    if args.output:args.output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8',newline='\n')
    print(f"PASS: {len(results)} audit runs / {total} raw IQ captures / {live_count} live captures; hashes, metadata, moments, deltas and incomplete-export labels verified; recomputed spectra={args.spectra}")


if __name__=='__main__':main()

#!/usr/bin/env python3
"""C5VRX/Twotoz: analyse fixed-gain, separately booted calibration witnesses."""
import csv
import gzip
import hashlib
import io
import json
from pathlib import Path
import numpy as np
from scipy.signal import welch

ROOT=Path(__file__).resolve().parents[1]
RAW=ROOT/'research/data/nf_phy_2026-10-24'


def main():
    runs=[];rows=[];spectra={}
    for p in sorted(RAW.glob('cal_*')):
        if not p.is_dir() or not (p/'manifest.json').exists():continue
        m=json.loads((p/'manifest.json').read_text())
        rr=[]
        for capture in m['captures']:
            raw=(p/capture['file']).read_bytes()
            if hashlib.sha256(raw).hexdigest()!=capture['sha256']:raise ValueError('SHA256 mismatch')
            b=np.frombuffer(raw,dtype=np.uint8);i=(b>>4).astype(float);q=(b&15).astype(float)
            i[i>=8]-=16;q[q>=8]-=16;z=(i-i.mean())+1j*(q-q.mean());power=abs(z)**2
            m2=float(np.mean(power));kurt=float(np.mean(power**2)/m2**2)
            meta=capture['metadata'];gain=int(meta['gain'])
            f,psd=welch(z,fs=40e6,nperseg=1024,noverlap=512,window='hann',detrend='constant',return_onesided=False,scaling='density')
            order=np.argsort(f);f,psd=f[order],psd[order]
            spectra.setdefault((p.name,gain),[]).append(psd)
            r=dict(run=p.name,file=capture['file'],sha256=capture['sha256'],gain=gain,freq=int(meta['freq_mhz']),
                m2=m2,kurtosis=kurt,dc_i=float(i.mean()),dc_q=float(q.mean()),
                signed_rails=float(np.mean((i==7)|(i==-8)|(q==7)|(q==-8))),
                quant_limited=m2<.67)
            rr.append(r);rows.append(r)
        results=[]
        for gain in (78,72):
            group=[r for r in rr if r['gain']==gain]
            if len(group)!=16:raise ValueError('incomplete fixed-gain witness')
            flags=[]
            if max(r['signed_rails'] for r in group)>.002:flags.append('rails_above_0.2pct')
            if any(r['quant_limited'] for r in group):flags.append('some_captures_quant_limited')
            if any(r['kurtosis']<1.8 or r['kurtosis']>2.3 for r in group):flags.append('non_gaussian_or_distorted')
            psd=np.mean(spectra[p.name,gain],axis=0);band=(abs(f)>=.5e6)&(abs(f)<=2e6)
            results.append(dict(gain=gain,mean_m2=float(np.mean([r['m2'] for r in group])),
                median_m2=float(np.median([r['m2'] for r in group])),
                std_m2=float(np.std([r['m2'] for r in group],ddof=1)),
                max_signed_rails=max(r['signed_rails'] for r in group),
                nbw_mhz=float(np.sum(psd)*(f[1]-f[0])/np.mean(psd[band])/1e6),quality_flags=flags))
        runs.append(dict(name=p.name,results=results,rejected=m.get('rejected',[]),
            raw_log_sha256=m['raw_log_sha256'],manifest_sha256=hashlib.sha256((p/'manifest.json').read_bytes()).hexdigest(),
            started_utc=m['started_utc'],ended_utc=m['ended_utc']))
    comparisons=[]
    def result(name,gain):return next(g for r in runs if r['name']==name for g in r['results'] if g['gain']==gain)
    for freq in (5805,5865):
        for gain in (78,72):
            a=result(f'cal_partial_before_{freq}',gain);b=result(f'cal_full_{freq}',gain);c=result(f'cal_partial_after_{freq}',gain)
            stock=(a['mean_m2']+c['mean_m2'])/2
            comparisons.append(dict(frequency_mhz=freq,gain=gain,partial_before_m2=a['mean_m2'],full_m2=b['mean_m2'],
                partial_after_m2=c['mean_m2'],full_vs_bracket_stock_db=float(10*np.log10(b['mean_m2']/stock)),
                stock_drift_db=float(10*np.log10(c['mean_m2']/a['mean_m2'])),
                flags=list(dict.fromkeys(a['quality_flags']+b['quality_flags']+c['quality_flags']))))
    archive=dict(source='C5VRX by Twotoz and contributors',source_url='https://github.com/Twotoz/C5VRX',
        website_and_discord='https://twotoz.github.io/C5VRX/',scope='Noise-only boot comparisons; physical gain/desired RF transfer not calibrated',
        provenance_fullcal=json.loads((RAW/'provenance-fullcal.json').read_text()),runs=runs,comparisons=comparisons)
    (ROOT/'research/data/phy_calibration_2026-10-24_summary.json').write_text(json.dumps(archive,indent=2)+'\n',encoding='utf-8',newline='\n')
    buf=io.StringIO(newline='');writer=csv.DictWriter(buf,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    (ROOT/'research/data/phy_calibration_2026-10-24_captures.csv.gz').write_bytes(gzip.compress(buf.getvalue().encode(),mtime=0))
    lines=['# RF calibration policy: fixed-gain witnesses, 2026-10-24','',
        'C5VRX by Twotoz and contributors. [Source](https://github.com/Twotoz/C5VRX); '
        '[official website and Discord invite](https://twotoz.github.io/C5VRX/).','',
        'The VTX remained off. This tests whether the current partial-calibration '
        'boot policy is leaving a visible noise difference compared with a fresh '
        'full RF calibration. It does not measure noise figure or RF sensitivity.','',
        '## Controlled changes','',
        '- Partial → full → partial boot sequence; both 5805 and 5865 MHz at fixed G78 and G72.',
        '- Full-calibration build: PHY calibration/data storage disabled, full calibration selected. IDF 6.0.2 then calls full calibration without replacing the stored PHY NVS calibration record. NVS was not erased.',
        '- Application firmware, pinned PHY, dfilt0, filter profile, IQ lanes and output remained the same. Ordinary C5VRX startup/DC repairs and passive observers still run and can update application calibration, so this is not an identical-state RF-gain comparison.',
        '- Existing transient gain presets and checksum-verified I snapshots were used, 16 independent contiguous 8190-byte captures per gain/boot/frequency. AUTO gain was restored by the tool after each witness.',
        '- The original partial-calibration policy was flashed back before the final control measurements. The full-calibration policy is not a production change.',
        '', '## Results','',
        '| Frequency | Fixed gain | Partial before m2 | Full m2 | Partial after m2 | Full vs bracket stock | Stock drift | Flags |',
        '|---|---:|---:|---:|---:|---:|---:|---|']
    for r in comparisons:
        lines.append(f"| {r['frequency_mhz']} | {r['gain']} | {r['partial_before_m2']:.5f} | {r['full_m2']:.5f} | {r['partial_after_m2']:.5f} | {r['full_vs_bracket_stock_db']:+.3f} dB | {r['stock_drift_db']:+.3f} dB | {', '.join(r['flags']) or 'none'} |")
    lines.extend(['',
        'At G78 the full-calibration boot has slightly lower captured variance '
        '(about −0.13 dB at 5805 and −0.08 dB at 5865) than the average of the '
        'two partial controls. G72 does not show a consistent improvement and '
        'sits close to the four-bit quantization threshold. A gain-index match '
        'does not prove identical analog RF gain after recalibration; desired '
        'carrier response and usable video are unmeasured. This is not a '
        'universal extra 0.1 dB sensitivity claim, and must not be added to 11p '
        'without a matched combined experiment.','',
        'All 192 integrity-valid captures are retained; no noise outlier is removed. '
        'Boot-to-boot environment, temperature, per-gain DC tables, converter '
        'calibration and quantization limit interpretation. The local boot logs '
        'record startup repairs as well as completion. NBW uses the same complex '
        'Welch/±0.5..2 MHz normalization as the other PR254 noise reports.','',
        '## Artifacts and reproduction','',
        '- [All boot summaries, policy provenance and quality flags](../research/data/phy_calibration_2026-10-24_summary.json).',
        '- [Per-capture derived data and raw SHA-256 hashes](../research/data/phy_calibration_2026-10-24_captures.csv.gz).',
        '- `tools/phy_calibration_witness.py` records an existing firmware boot; `tools/analyse_phy_calibration.py` builds this archive. Both gain rows are restored to AUTO after acquisition.',
        '- Full local raw artifacts: `research/data/nf_phy_2026-10-24/cal_*`, plus boot/flash logs and `provenance-fullcal.json`. Raw board files are ignored by git; their hashes are committed.',
        '- Local full build used a copy of sdkconfig with `CONFIG_ESP_PHY_CALIBRATION_AND_DATA_STORAGE` disabled, `CONFIG_ESP_PHY_RF_CAL_FULL=y`, `CONFIG_ESP_PHY_CALIBRATION_MODE=2`, and a separate `build-fullcal` output directory. Production sdkconfig.defaults was not edited.',
        '- The original partial-calibration build was restored and acquired again; see the after-control metadata for exact firmware identity.',
        ''])
    (ROOT/'docs/PHY_CALIBRATION_BENCH_2026-10-24.md').write_text('\n'.join(lines),encoding='utf-8',newline='\n')
    print(json.dumps(comparisons,indent=2))


if __name__=='__main__':main()

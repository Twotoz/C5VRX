#!/usr/bin/env python3
"""C5VRX by Twotoz/contributors: record and analyse noise-only PHY ABBA.

Raw data have FNV transport integrity plus SHA256 archival hashes. PSD uses
contiguous snapshots separately: gaps between captures are never FFT input.
Requires numpy/scipy/matplotlib for analysis, pyserial for acquisition.
"""
import argparse
from collections import Counter
import csv
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import time
import math


def fields(line):
    return dict(re.findall(r'(\w+)=([^\s]+)', line))


def fnv(raw):
    h = 2166136261
    for b in raw:
        h = ((h ^ b) * 16777619) & 0xffffffff
    return f'{h:08x}'


def entry_checks(rows):
    """Keep opposite drift in repeated executions from cancelling in quality."""
    entries=[];flags=[]
    for identity in sorted(set(r['id'] for r in rows),key=int):
        rr=[r for r in rows if r['id']==identity]
        steps=[]
        for step in range(4):
            values=[float(r['m2_host']) for r in rr if int(r['step'])==step]
            if not values:raise ValueError('missing entry stage')
            steps.append(sum(values)/len(values))
        entries.append(dict(id=identity,delta_db=10*math.log10((steps[1]+steps[2])/(steps[0]+steps[3])),
            stock_drift_db=10*math.log10(steps[3]/steps[0]),test_repeat_db=10*math.log10(steps[2]/steps[1])))
    if len(entries)>1:
        if any(abs(e['stock_drift_db'])>.15 for e in entries):flags.append('entry_stock_drift_above_0.15db')
        if any(abs(e['test_repeat_db'])>.15 for e in entries):flags.append('entry_test_disagreement_above_0.15db')
    return entries,flags


def captures_complete(rows):
    expected={r['id']:int(r.get('windows_per_stage','4')) for r in rows}
    counts=Counter((r['id'],int(r['step'])) for r in rows)
    return bool(expected) and all(n>0 and all(counts[(identity,step)]==n for step in range(4))
                                  for identity,n in expected.items())


class Receipt:
    """ACK only a complete snapshot with both matching transport hashes."""
    def __init__(self):
        self.meta=None;self.raw=bytearray();self.valid=False

    def feed(self,line):
        line=line[line.find('PHYLAB_'):] if 'PHYLAB_' in line else line
        if line.startswith('PHYLAB_IQ_BEGIN '):
            self.meta=fields(line);self.raw=bytearray();self.valid=True
        elif line.startswith('PHYLAB_IQ_DATA ') and self.meta is not None:
            match=re.fullmatch(r'PHYLAB_IQ_DATA offset=(\d+) ([0-9a-f]+)',line)
            if not match or int(match[1])!=len(self.raw):self.valid=False
            elif self.valid:self.raw.extend(bytes.fromhex(match[2]))
        elif line.startswith('PHYLAB_IQ_END ') and self.meta is not None:
            meta=self.meta;self.meta=None
            checksum=fields(line).get('fnv1a')
            if (self.valid and len(self.raw)==8190 and meta.get('bytes')=='8190'
                and meta.get('rate_hz')=='40000000' and checksum==meta.get('fnv1a')==fnv(self.raw)
                and re.fullmatch('[0-9a-f]{8}',meta.get('tx_token',''))):
                return b'\x06'+(meta['tx_token']+checksum+'\n').encode('ascii')
        return None


def decode(text, output, tolerate_corrupt=False):
    output.mkdir(parents=True, exist_ok=True)
    rows, names, results = [], {}, {}
    meta, raw, corrupt = None, bytearray(), None
    rejected = [];stage_windows=4;retransmissions=[];accepted={}
    for line in text.splitlines():
        at = line.find('PHYLAB_')
        if at < 0:
            continue
        line = line[at:]
        f = fields(line)
        if line.startswith('PHYLAB_BEGIN '):
            stage_windows=int(f.get('windows_per_stage','4'))
        elif line.startswith('PHYLAB_PROFILE '):
            names.setdefault(f['id'], f['name'])
        elif line.startswith('PHYLAB_PATTERN '):
            names[f['id']] = 'NF_' + f['pattern']
        elif line.startswith('PHYLAB_RESULT '):
            results[f['id']] = f
        elif line.startswith('PHYLAB_IQ_BEGIN '):
            if meta is not None:
                if not tolerate_corrupt: raise ValueError('truncated preceding capture')
                rejected.append(dict(metadata=meta, reason='truncated preceding capture'))
            meta, raw, corrupt = f, bytearray(), None
            meta['windows_per_stage']=str(stage_windows)
            if f['bytes'] != '8190' or f['rate_hz'] != '40000000':
                raise ValueError('unsupported snapshot geometry')
        elif line.startswith('PHYLAB_IQ_DATA '):
            if corrupt is not None: continue
            m = re.fullmatch(r'PHYLAB_IQ_DATA offset=(\d+) ([0-9a-f]+)', line)
            if meta is None or not m or int(m[1]) != len(raw):
                if not tolerate_corrupt: raise ValueError('missing or reordered snapshot bytes')
                corrupt='missing or reordered snapshot bytes';continue
            raw.extend(bytes.fromhex(m[2]))
        elif line.startswith('PHYLAB_IQ_END '):
            if meta is None or len(raw) != 8190 or fnv(raw) != f['fnv1a'] or f['fnv1a'] != meta['fnv1a']:
                if not tolerate_corrupt: raise ValueError('snapshot checksum or length mismatch')
                rejected.append(dict(metadata=meta,reason=corrupt or 'snapshot checksum or length mismatch'))
                meta,raw,corrupt=None,bytearray(),None;continue
            name = f"p{int(meta['id']):02d}_s{meta['step']}_r{meta['rep']}.iq"
            if (output / name).exists():
                previous=accepted[name]
                if (meta.get('tx_token') and meta.get('tx_token')==previous.get('tx_token')
                    and int(meta.get('tx_attempt','0'))>int(previous.get('tx_attempt','0'))
                    and all(meta.get(k)==previous.get(k) for k in ('id','step','rep','stage','t_us','gain','freq','offset','fnv1a'))
                    and (output/name).read_bytes()==raw):
                    retransmissions.append(dict(metadata=meta,reason='verified identical retry'))
                    previous['tx_attempt']=meta['tx_attempt'];meta=None;continue
                raise ValueError('conflicting duplicate capture: use separate directories per run')
            (output / name).write_bytes(raw)
            meta.update(file=name, sha256=hashlib.sha256(raw).hexdigest())
            rows.append(meta)
            accepted[name]=meta
            meta = None
    if meta is not None:
        if not tolerate_corrupt: raise ValueError('unfinished capture')
        rejected.append(dict(metadata=meta, reason='unfinished capture'))
    for row in rows:
        row['profile'] = names[row['id']]
        row['profile_result'] = results.get(row['id'], {}).get('status', 'INCOMPLETE')
        row['restored'] = results.get(row['id'], {}).get('restored', 'unknown')
    manifest = dict(captures=rows, results=results, rejected_captures=rejected,retransmissions=retransmissions,
                    scope='noise only; no input-referred C/N, sensitivity or video claim')
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2))
    return manifest


def acquire(port, output, key='B', expected_frequency=None):
    import serial
    output.mkdir(parents=True, exist_ok=False)
    s = serial.Serial()
    s.port, s.baudrate, s.timeout, s.write_timeout = port, 115200, .1, 2
    s.dtr = s.rts = False
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    context_errors=[]
    with (output / 'console.raw.log').open('wb') as log:
        try:
            s.open()
            s.write(b'T')
            end = time.monotonic() + 3
            status_data=b''
            while time.monotonic() < end:
                data=s.read(32768);log.write(data);status_data+=data
            if expected_frequency is not None:
                tuned=re.findall(rb'CONTROL channel=\S+ freq=(\d+)',status_data)
                if not tuned or int(tuned[-1])!=expected_frequency:
                    raise RuntimeError(f'frequency guard: wanted {expected_frequency}, status reports {tuned[-1:]}; no lab command sent')
            s.write(key.encode())
            end = time.monotonic() + 300
            buf = b''
            receipt=Receipt()
            done = False
            outer_required=False;ended=False
            while time.monotonic() < end:
                data = s.read(32768)
                log.write(data)
                log.flush()
                buf += data
                while b'\n' in buf:
                    line, buf = buf.split(b'\n', 1)
                    decoded=line.decode(errors='replace').rstrip('\r')
                    ack=receipt.feed(decoded)
                    if ack:s.write(ack)
                    if expected_frequency is not None and (b'PHYLAB_BEGIN ' in line or b'PHYLAB_IQ_BEGIN ' in line):
                        actual=fields(decoded).get('freq')
                        if actual!=str(expected_frequency):
                            error=f'frequency changed after request: expected {expected_frequency}, capture reports {actual}'
                            if error not in context_errors:context_errors.append(error);print(error,flush=True)
                    if b'PHYLAB_BEGIN ' in line:outer_required=b'outer_restore=required' in line
                    if any(tag in line for tag in (b'PHYLAB_BEGIN', b'PHYLAB_PROFILE', b'PHYLAB_RESULT', b'PHYLAB_SKIP', b'PHYLAB_END', b'PHYLAB refused', b'PHYLAB_FATAL')):
                        print(line.decode(errors='replace'), flush=True)
                    if b'PHYLAB_END ' in line:
                        ended=True;done=not outer_required
                    if b'PHYLAB_OUTER_RESTORE verified=1 ' in line and ended:done=True
                    if b'PHYLAB refused=' in line or b'PHYLAB_FATAL' in line:
                        done = True
                if done:
                    break
            if not done:
                raise TimeoutError('suite timeout; log retained, inspect board restoration before continuing')
        finally:
            s.close()
            (output / 'acquisition.json').write_text(json.dumps(dict(port=port, started_utc=started,
                ended_utc=dt.datetime.now(dt.timezone.utc).isoformat(),requested_frequency=expected_frequency,
                key=key,context_errors=context_errors), indent=2))
    manifest=decode((output / 'console.raw.log').read_text(errors='replace'), output / 'iq', tolerate_corrupt=True)
    if context_errors:raise RuntimeError('; '.join(context_errors)+'; raw data retained; suite restored before reporting mismatch')
    return manifest


def analyse(output, plots=True):
    import numpy as np
    from scipy.signal import welch
    if plots:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    manifest = json.loads((output / 'iq/manifest.json').read_text())
    rows, spectra = [], {}
    for meta in manifest['captures']:
        raw = (output / 'iq' / meta['file']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != meta['sha256']:
            raise ValueError('archived SHA256 mismatch')
        b = np.frombuffer(raw, dtype=np.uint8)
        i, q = (b >> 4).astype(float), (b & 15).astype(float)
        i[i >= 8] -= 16
        q[q >= 8] -= 16
        z = (i - i.mean()) + 1j * (q - q.mean())
        m2 = np.mean(abs(z)**2)
        kurt = np.mean(abs(z)**4) / m2**2
        f, psd = welch(z, fs=40e6, window='hann', nperseg=1024, noverlap=512,
                       detrend='constant', return_onesided=False, scaling='density')
        order = np.argsort(f)
        f, psd = f[order], psd[order]
        spectra.setdefault(meta['profile'], {}).setdefault(meta['stage'], []).append(psd)
        row = dict(meta, m2_host=float(m2), kurt_host=float(kurt), dc_i=float(i.mean()), dc_q=float(q.mean()),
                   rails_host=float(np.mean((abs(i) >= 7) | (abs(q) >= 7))),
                   signed_rails=float(np.mean((i == 7) | (i == -8) | (q == 7) | (q == -8))),
                   fold_proxy=float(np.mean((abs(np.diff(i)) > 8) | (abs(np.diff(q)) > 8))),
                   iq_variance_ratio=float(np.var(i)/max(np.var(q), 1e-15)),
                   iq_correlation=float(np.mean((i-i.mean())*(q-q.mean()))/max(np.std(i)*np.std(q), 1e-15)))
        rows.append(row)
    if not rows:
        raise ValueError('no captures')
    with (output / 'captures.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    summary = []
    for profile, stages in spectra.items():
        rr = [r for r in rows if r['profile'] == profile]
        complete=captures_complete(rr)
        if set(stages) != {'STOCK', 'TEST'} or not complete or any(r['profile_result'] != 'PASS' or r['restored'] != '1' for r in rr):
            status = 'REJECTED_ENTRY' if any(r['profile_result']=='REJECTED_ENTRY' for r in rr) else 'INCOMPLETE'
            summary.append(dict(profile=profile, status=status)); continue
        means = {s: np.mean([r['m2_host'] for r in rr if r['stage'] == s]) for s in stages}
        p = {s: np.mean(v, axis=0) for s, v in stages.items()}
        # Equivalent rectangular noise bandwidth, referenced to mean central
        # 0.5..2 MHz PSD on both sides. State this reference, not a claimed
        # calibrated transfer function. A spur can invalidate this estimate.
        band = (abs(f) >= .5e6) & (abs(f) <= 2e6)
        nbw = {s: float(np.sum(v)*(f[1]-f[0])/np.mean(v[band])/1e6) for s, v in p.items()}
        steps = [np.mean([r['m2_host'] for r in rr if int(r['step']) == k]) for k in range(4)]
        delta = 10*np.log10(means['TEST']/means['STOCK'])
        drift = 10*np.log10(steps[3]/steps[0])
        repeat = float(10*np.log10(steps[2]/steps[1]))
        max_rails = max(r['signed_rails'] for r in rr)
        max_kurt, min_kurt = max(r['kurt_host'] for r in rr), min(r['kurt_host'] for r in rr)
        flags = []
        if max_rails > .002: flags.append('signed_rails_above_0.2pct')
        if max_kurt > 2.3 or min_kurt < 1.8: flags.append('non_gaussian_or_distorted_noise')
        if abs(drift) > .15: flags.append('stock_drift_above_0.15db')
        if abs(repeat) > .15: flags.append('test_disagreement_above_0.15db')
        entries,entry_flags=entry_checks(rr);flags.extend(entry_flags)
        median_delta = float(10*np.log10(np.median([r['m2_host'] for r in rr if r['stage']=='TEST']) /
                                       np.median([r['m2_host'] for r in rr if r['stage']=='STOCK'])))
        summary.append(dict(profile=profile, status='PASS', stock_m2=float(means['STOCK']), test_m2=float(means['TEST']),
            delta_db=float(delta), stock_drift_db=float(drift), test_repeat_db=repeat, median_delta_db=median_delta,
            stock_nbw_mhz=nbw['STOCK'], test_nbw_mhz=nbw['TEST'],
            max_signed_rails=max_rails, max_kurtosis=max_kurt, min_kurtosis=min_kurt,
            quality_flags=flags, quality='FLAGGED' if flags else 'NO_FLAGS',
            entries=entries,
            carrier_delta_db=None, input_referred_cn=None, video_sinad=None))
        np.savez_compressed(output / f'psd_{profile}.npz', frequency_hz=f, stock=p['STOCK'], test=p['TEST'])
        if plots:
            fig, ax = plt.subplots(figsize=(7, 3.5))
            for s in stages:
                ax.plot(f/1e6, 10*np.log10(np.maximum(p[s], 1e-30)), label=s)
            ax.set(xlabel='Baseband frequency (MHz)', ylabel='PSD (dB cells²/Hz)', title=f'{profile}: noise {delta:+.3f} dB; stock drift {drift:+.3f} dB')
            ax.legend(); ax.grid(alpha=.3); fig.tight_layout(); fig.savefig(output / f'psd_{profile}.png', dpi=160); plt.close(fig)
    (output / 'summary.json').write_text(json.dumps(summary, indent=2))
    lines = ['# C5VRX PHY noise-only measurements', '',
        'C5VRX by Twotoz and contributors. [Source](https://github.com/Twotoz/C5VRX); '
        '[official website and Discord invite](https://twotoz.github.io/C5VRX/).', '',
        'Noise-only evidence. C/N, sensitivity, video SINAD and usable attenuation remain unmeasured.', '',
        'NBW is integrated complex Welch PSD divided by mean PSD at ±0.5..2 MHz, not a calibrated RF passband.', '',
        'PASS means capture/rollback succeeded; quality flags still qualify the noise result. Means retain every capture, including outliers.', '',
        '| Profile | IQ noise Δ dB | Stock drift dB | Test repeat dB | Stock/test NBW MHz | Max rails | Quality |',
        '|---|---:|---:|---:|---:|---:|---|']
    for s in summary:
        if s['status'] != 'PASS':
            lines.append(f"| {s['profile']} | {s['status']} | | | | | |"); continue
        lines.append(f"| {s['profile']} | {s['delta_db']:+.3f} | {s['stock_drift_db']:+.3f} | {s['test_repeat_db']:+.3f} | {s['stock_nbw_mhz']:.3f}/{s['test_nbw_mhz']:.3f} | {s['max_signed_rails']:.4f} | {s['quality']} |")
    (output / 'REPORT.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    print('\n'.join(lines).replace('Δ','delta'))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest='command', required=True)
    p = sub.add_parser('record'); p.add_argument('port'); p.add_argument('output', type=Path); p.add_argument('--key',choices=['B','C','H','V','E','S','P','O','K','k','%','+','&','(',')','[',']','{'],default='B');p.add_argument('--frequency',type=int)
    p = sub.add_parser('analyse'); p.add_argument('output', type=Path); p.add_argument('--no-plots',action='store_true')
    p = sub.add_parser('decode'); p.add_argument('log', type=Path); p.add_argument('output', type=Path)
    a = ap.parse_args()
    if a.command == 'record':
        acquire(a.port, a.output,a.key,a.frequency); analyse(a.output)
    elif a.command == 'decode':
        decode(a.log.read_text(errors='replace'), a.output)
    else:
        analyse(a.output,not a.no_plots)


if __name__ == '__main__':
    main()

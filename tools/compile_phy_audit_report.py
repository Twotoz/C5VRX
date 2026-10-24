#!/usr/bin/env python3
"""C5VRX by Twotoz/contributors: archive extended noise-only PHY audits."""
import csv
import gzip
import hashlib
import io
import json
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[1]
RAW=ROOT/'research/data/nf_phy_2026-10-24'


def main():
    runs=[];captures=[];live=[]
    for p in sorted(RAW.glob('audit_*')):
        if not p.is_dir() or not (p/'summary.json').exists():continue
        manifest=json.loads((p/'iq/manifest.json').read_text())
        text=(p/'console.raw.log').read_text(errors='replace')
        begin=next(line for line in text.splitlines() if line.startswith('PHYLAB_BEGIN'))
        meta=dict(re.findall(r'(\w+)=([^\s]+)',begin))
        rows=list(csv.DictReader((p/'captures.csv').open()))
        captures.extend(dict(run=p.name,**r) for r in rows)
        runs.append(dict(name=p.name,context=meta,summary=json.loads((p/'summary.json').read_text()),
            valid_captures=len(rows),rejected_captures=manifest.get('rejected_captures',[]),
            raw_log_sha256=hashlib.sha256((p/'console.raw.log').read_bytes()).hexdigest(),
            manifest_sha256=hashlib.sha256((p/'iq/manifest.json').read_bytes()).hexdigest(),
            dc_selected=[dict(re.findall(r'(\w+)=([^\s]+)',line)) for line in text.splitlines() if line.startswith('PHYLAB_DCO_SELECTED ')],
            retransmissions=manifest.get('retransmissions',[]),
            bias_readbacks=[dict(re.findall(r'(\w+)=([^\s]+)',line)) for line in text.splitlines() if line.startswith('PHYLAB_BIAS ')],
            acquisition=json.loads((p/'acquisition.json').read_text())))
    for p in sorted(RAW.glob('live_*')):
        if not (p/'manifest.json').exists():continue
        manifest=json.loads((p/'manifest.json').read_text());stats=[]
        for capture in manifest['captures']:
            raw=(p/capture['file']).read_bytes()
            if hashlib.sha256(raw).hexdigest()!=capture['sha256']:raise ValueError('live witness hash mismatch')
            i=[(b>>4)-16 if b>>4>=8 else b>>4 for b in raw]
            q=[(b&15)-16 if b&15>=8 else b&15 for b in raw]
            mi=sum(i)/len(i);mq=sum(q)/len(q)
            m2=sum((x-mi)**2+(y-mq)**2 for x,y in zip(i,q))/len(i)
            rails=sum(x in (7,-8) or y in (7,-8) for x,y in zip(i,q))/len(i)
            stats.append(dict(file=capture['file'],sha256=capture['sha256'],metadata=capture['metadata'],
                m2=m2,dc_i_cellcentre=mi+.5,dc_q_cellcentre=mq+.5,signed_rails=rails))
        live.append(dict(name=p.name,manifest=manifest,capture_statistics=stats,
            manifest_sha256=hashlib.sha256((p/'manifest.json').read_bytes()).hexdigest()))
    prov=[json.loads(p.read_text()) for p in sorted(RAW.glob('provenance*.json'))]
    aggregate=dict(source='C5VRX by Twotoz and contributors',source_url='https://github.com/Twotoz/C5VRX',
        website_and_discord='https://twotoz.github.io/C5VRX/',
        scope='VTX off, noise only, no calibrated carrier or video; no sensitivity/range claim',
        provenance=prov,total_valid_captures=len(captures),runs=runs,live_gain_witnesses=live,
        total_live_witness_captures=sum(len(r['capture_statistics']) for r in live))
    evidence=RAW/'bias-extra-evidence.txt'
    if evidence.exists():aggregate['additional_static_evidence']=dict(file=evidence.name,sha256=hashlib.sha256(evidence.read_bytes()).hexdigest())
    (ROOT/'research/data/phy_audit_2026-10-24_summary.json').write_text(json.dumps(aggregate,indent=2)+'\n',encoding='utf-8',newline='\n')
    buf=io.StringIO(newline='');keys=list(dict.fromkeys(k for r in captures for k in r))
    writer=csv.DictWriter(buf,fieldnames=keys);writer.writeheader();writer.writerows(captures)
    (ROOT/'research/data/phy_audit_2026-10-24_captures.csv.gz').write_bytes(gzip.compress(buf.getvalue().encode(),mtime=0))
    clean=[r for r in runs if r['name'] in ('audit_5865_headroom','audit_5805_headroom')]
    lines=['# Extended PHY audit: board measurements, 2026-10-24','',
        'C5VRX by Twotoz and contributors. [Source](https://github.com/Twotoz/C5VRX); '
        '[official website and Discord invite](https://twotoz.github.io/C5VRX/).','',
        'This extends [the first NF/11p bench](NF_PHY_BENCH_2026-10-24.md) and '
        '[the static feature inventory](PHY_FEATURE_AUDIT_2026-10-24.md). '
        'All tests used the same USB-connected C5 with the VTX off. No new default '
        'or RF sensitivity/range improvement is established by these noise-only probes.','',
        '## Method and quality','',
        'Each entry uses STOCK → TEST → TEST → STOCK, four separately contiguous '
        '8,190-byte IQ40 captures per stage (16 per stage for the large joint probes), same gain/lane/frequency/offset, exact '
        'saved-mask rollback. H/V test individual RX/TX/correction/filter/packet '
        'profiles; E repeats output ON→OFF→OFF→ON three times at one fixed gain.','',
        'The first audit at 5865 MHz selected G82 under the earlier four-window '
        '0.2% rail screen. Later DC variation/interference caused substantial '
        'signed-rail flags. Its complete derived data remain in the archive; '
        'the apparent −0.600 dB PKDADC result had −1.057 dB stock drift and '
        'does not establish a gain. One FFT profile lost a USB capture and is '
        'marked incomplete despite successful hardware rollback.','',
        'The strengthened screen requires zero observed signed rails and '
        '`abs(mean)+6*sigma <= 6 cells` on each axis in all four qualification '
        'captures, with m2 > 0.67 cells². It reserves headroom to the ultrafine '
        'slice rails; it cannot guarantee freedom from later interference or '
        'folding. The lower selected gain increases the relative quantization '
        'contribution: compare within-run deltas, not absolute powers across '
        'different gains. The mean still retains all integrity-valid outliers.','',
        '## Results with strengthened headroom','',
        '| Profile | 5865 MHz delta / quality | 5805 MHz delta / quality |',
        '|---|---:|---:|']
    def lookup(name,profile):
        rr=next((r for r in clean if r['name']==name),None)
        if not rr:return 'pending'
        s=next((s for s in rr['summary'] if s['profile']==profile),None)
        if not s:return 'not measured'
        if s['status']!='PASS':return s['status']
        return f"{s['delta_db']:+.3f} dB / {s['quality']}"
    profiles=list(dict.fromkeys(s['profile'] for r in clean for s in r['summary']))
    for profile in profiles:lines.append(f"| {profile} | {lookup('audit_5865_headroom',profile)} | {lookup('audit_5805_headroom',profile)} |")
    lines.extend(['',
        'Positive means higher captured IQ noise than stock. The RX enable '
        'register has a clear variance effect; the TX enables and zeroed RX '
        'coefficient fields are much smaller. That does not tell whether '
        'wanted analog FM phase, image rejection or colour survives either '
        'state. No RX calibration is promoted or disabled in production.','',
        'Packet/CCA fields have no reproducible noise benefit here. Digital '
        'modes 4/8/12 are comparisons against existing production mode0; '
        'lower noise in mode0 is already included in stock and is not an '
        'additional gain earned by this PR. Converter selector is preserved.','',
        'FFT_FORCE_OFF is a **no-op control** in the measured baseline: '
        '`600A7800=0f07a2d8` has bit19 already clear and is unchanged during '
        'that profile. Its fluctuating delta is estimator/environment variation, '
        'not an extra filter benefit. MAC_CCA_ENABLE also matches the baseline '
        'zero masked value. Do not add no-op or sign-changing deltas to 11p.','',
        '## Video-output self-noise','',
        '| Run | Frequency / gain | OFF minus ON IQ noise | Quality |',
        '|---|---|---:|---|'])
    for r in runs:
        if not r['name'].endswith('_dac'):continue
        for s in r['summary']:
            if s['status']=='PASS':lines.append(f"| {r['name']} | {r['context']['freq']} / G{r['context']['gain']} | {s['delta_db']:+.3f} dB | {s['quality']} |")
    lines.extend(['',
        'These are three separately restored ON/OFF comparisons per run. The '
        'output resumes with the same program/LUT/output level through the '
        'existing verified restart. No physical loaded-CVBS or picture-quality '
        'measurement was possible. A quiet powered-off output is not an '
        'operating receiver configuration or a gain to stack onto 11p.','',
        '## Coupled ADC selector','',
        '| Run | Frequency / gain | Selector 0 vs incoming stock | Selector 1 vs incoming stock |',
        '|---|---|---:|---:|'])
    for r in runs:
        if '_adc' not in r['name']:continue
        cols=[]
        for name in ('ADC_SELECTOR0','ADC_SELECTOR1'):
            s=next((s for s in r['summary'] if s['profile']==name),None)
            cols.append(f"{s['delta_db']:+.3f} dB / {s['quality']}" if s and s['status']=='PASS' else 'incomplete')
        lines.append(f"| {r['name']} | {r['context']['freq']} / G{r['context']['gain']} | {cols[0]} | {cols[1]} |")
    lines.extend(['',
        'The pinned `phy_adc_rate_set` writes analog block66/host0/reg4[2] '
        '= !selector plus digital `600A0448[1:0]` = duplicated selector. '
        'S applies only the documented-by-disassembly selector values 0/1 '
        'and restores both original owned fields, preserving unrelated bits. '
        'Digital filter mode, gain, calibration flags and tuning stay fixed. '
        'Every capture checks both digital fields and the analog bit.','',
        'At 5865 MHz the incoming vendor selector is 1; at 5805 MHz it is '
        '0. Thus selector0 at 5805 and selector1 at 5865 are no-op controls. '
        'At 5865 the selector0 deltas −0.142/−0.079/−0.040 dB do not '
        'convincingly separate from no-op selector1 deltas '
        '+0.055/−0.060/−0.082 dB. The initial apparent reduction did not '
        'hold up as a robust independent benefit. Keep the vendor selection; '
        'no fixed selector0 production change is justified. Capture sample '
        'rate metadata refers to PARLIO RX40, not a separately measured RF '
        'ADC clock or proof of desired FM transfer.','',
        'Read-only gain-tuple records confirm RF stage8/code487 at G73/G74; '
        'the highest RF stage starts at G54 and the current table max is G83. '
        'The listening receiver is not accidentally using a lower RF stage '
        'and compensating solely with BB gain. Those labels come from the '
        'pinned generator and hardware-matched AGC table; they do not '
        'calibrate LNA noise figure or authorize unlisted RF bias codes.','',
        '## Calibration-policy comparisons','',
        'Effective production policy is partial calibration with NVS data '
        'storage. A local full-calibration build disables only the PHY '
        'calibration-and-storage option and selects full RF calibration; '
        'IDF `phy_init.c` then uses its full-calibration branch without '
        'loading/storing a replacement PHY NVS record. No NVS erase is used. '
        'This is a boot experiment, never an in-place MMIO toggle. Its results '
        'must be listed separately from within-boot ABBA deltas and the '
        'partial-calibration build must be restored afterwards. '
        'The complete [fixed-gain before/full/after measurements](PHY_CALIBRATION_BENCH_2026-10-24.md) '
        'are now archived separately; partial policy was restored.','',
        '## Joint probes excluding additional 11p changes','',
        'Two predeclared combinations apply ADC selector0, packet ADC/CCA controls '
        'and channel-B arg1, plus either RX-sense0 or RX11b0. Each uses '
        '16 captures per stage, forward/reverse profile order and an unchanged '
        'stock sham. Historical NO11P names mean that the probe makes no 11p '
        'writes; they must not be read as evidence that an incoming baseline '
        'has 11p disabled. No small single-control deltas are added together.','',
        '| Run | Profile | Noise delta dB | Stock drift dB | Quality/status |',
        '|---|---|---:|---:|---|'])
    for r in runs:
        if '_combo_' not in r['name']:continue
        for s in r['summary']:
            if s['status']=='PASS':
                lines.append(f"| {r['name']} | {s['profile']} | {s['delta_db']:+.3f} | {s['stock_drift_db']:+.3f} | {s['quality']} |")
            else:lines.append(f"| {r['name']} | {s['profile']} | | | {s['status']} |")
    lines.extend(['',
        'The initial pre-merge 5865 MHz run showed no established combined improvement: approximately '
        '-0.015/-0.007 dB versus a -0.067 dB sham. At 5805 MHz two damaged '
        'USB captures invalidate both candidate comparisons; the sham alone '
        'is not an intervention benefit. All integrity-valid samples and '
        'quality flags remain included. Exact hardware rollback succeeded.','',
        'Complete ACK-enabled repeats on the integrated 11p baseline give '
        'sense/RX11b combinations -0.010/+0.001 dB at 5805 MHz '
        '(sham +0.001) and +0.001/-0.037 dB at 5865 MHz '
        '(sham +0.023), with no quality flags. The small RX11b-combination '
        'reduction changes sign across frequencies; no reproducible 0.4 dB '
        'joint benefit or input-referred improvement is established.','',
        '## Fixed physical-gain sweep','',
        '| Frequency MHz | Gain | Mean complex variance cells² | Maximum signed-rail occupancy |',
        '|---|---:|---:|---:|'])
    for r in runs:
        if not r['name'].endswith('_gaincurve'):continue
        rr=[c for c in captures if c['run']==r['name']]
        for g in sorted(set(int(c['gain']) for c in rr)):
            rows=[c for c in rr if int(c['gain'])==g]
            m2=sum(float(c['m2_host']) for c in rows)/len(rows)
            rails=max(float(c['signed_rails']) for c in rows)
            lines.append(f"| {r['context']['freq']} | {g} | {m2:.4f} | {100*rails:.3f}% |")
    lines.extend(['',
        'Each gain has 32 captures across forward/reverse order. G83 has '
        'approximately 2.8 dB more output noise than G80, consistent with '
        'the extra gain also applied to a wanted signal. This is not an '
        'input-referred noise or SNR comparison. Higher signed-rail occupancy '
        'does warrant a same-G83 DC-centering experiment. This historical '
        'sweep changed gain while the receiver was paused: its normal '
        'post-gain DC hook intentionally returns early, leaving the held '
        'DC calibration released. Thus these rail figures describe vendor '
        'DC at forced gain, not the corrected live receiver. Other historical '
        'within-run probes also used this paused forced-gain baseline. '
        'The new DC bench explicitly applies a matching loaded calibration '
        'row and restores the original outer gain/DC hold before resume. '
        'Rails/jump proxies '
        'do not directly count folding errors in the sign-preserving '
        'ultrafine lane. Production max remains unchanged: a lower maximum '
        'requires desired-signal/weak-carrier evidence.','',
        '## Same-gain calibrated DC comparison','',
        'The new bench applies a matching, already-loaded production DC row '
        'before each fixed-gain comparison. Its bounded local search uses '
        'separate probe samples, restores entry codes, then acquires 16 '
        'fresh IQ windows per ABBA stage. No gain/filter/IQ-correction changes '
        'occur inside a comparison. Entry codes and the held mode are restored.','',
        '| Run | Gain | Noise delta dB | Mean signed rails STOCK / TEST | Quality |',
        '|---|---:|---:|---:|---|'])
    for r in runs:
        if not r.get('dc_selected'):continue
        for s in r['summary']:
            rr=[c for c in captures if c['run']==r['name'] and c['profile']==s['profile']]
            if s['status']!='PASS':
                lines.append(f"| {r['name']} | {s['profile']} | | | {s['status']} |");continue
            rails=[]
            for stage in ('STOCK','TEST'):
                vals=[float(c['signed_rails']) for c in rr if c['stage']==stage]
                rails.append(100*sum(vals)/len(vals))
            lines.append(f"| {r['name']} | {rr[0]['gain']} | {s['delta_db']:+.3f} | {rails[0]:.4f}% / {rails[1]:.4f}% | {s['quality']} |")
    lines.extend(['',
        'Lower signed-rail occupancy is a headroom observation, not a direct '
        'count of folding errors, a noise-figure improvement or a range '
        'measurement. Equal target/entry codes make a sham comparison. '
        'Stock/test means retain every valid capture, including outliers. '
        'A lower raw mean is not itself more sensitivity. Hardware-code '
        'readback and full raw-capture hashes are retained in the archive.','',
        'At 5805 MHz G83 mean signed rails fell from 0.3072% to 0.0527%, '
        'with -0.005 dB variance change. At 5865 MHz the first complete '
        'comparison gave 0.1107% to 0.0424% (+0.009 dB), but the later '
        'complete repeat gave 0.0240% to 0.0233% (-0.005 dB). Thus there '
        'is evidence for correcting an off-centre starting pair, not a '
        'repeatable extra noise reduction to add to 11p. The incomplete '
        'G83 repeat is retained. The first ACK-enabled run recovered one '
        'corrupted transport attempt and retained all 192 intended snapshots '
        'with matching capture identity; the 5805 MHz run needed no retry. '
        'The committed Windows helper was also exercised with a full-system '
        'reset and the console answered afterwards.','',
        '## Read-only bias witness','',
        'Pinned phy_bias_reg_set initializes block0x6a/host1 reg0/reg1 to '
        '0x9f/0x7f. The measured operating bytes are listed below. '
        'phy_freq_start_cal_5g_code subsequently writes reg0[7:4] while '
        'iterating frequency calibration, so a different operating high '
        'nibble is not evidence of a forgotten LNA optimization. No bias '
        'bytes were overridden. See the static feature audit for exact '
        'call/write evidence.','',
        '| Run | Observed reg0 / reg1 bytes |',
        '|---|---|'])
    for r in runs:
        if not r.get('bias_readbacks'):continue
        values={reg:sorted(set(b['value'] for b in r['bias_readbacks'] if b['reg']==reg)) for reg in ('0','1')}
        lines.append(f"| {r['name']} | {','.join(values['0'])} / {','.join(values['1'])} |")
    lines.extend(['',
        '## Live fixed-MAX witnesses','',
        'These separate captures leave the normal DC servo running. Console '
        'G selects transient MAX (G83); uppercase I provides independent '
        'contiguous snapshots, separated by a one-second collection interval. '
        'The tool restores AUTO. These are operating-state witnesses, '
        'not an ABBA intervention, weak-carrier test or noise-figure measurement.','',
        '| Run | Captures | Mean variance cells² | Mean / max signed rails | Mean cell-centre I/Q |',
        '|---|---:|---:|---:|---:|'])
    for r in live:
        stats=r['capture_statistics'];n=len(stats)
        if not n:continue
        mean=lambda k:sum(s[k] for s in stats)/n
        lines.append(f"| {r['name']} | {n} | {mean('m2'):.4f} | {100*mean('signed_rails'):.4f}% / {100*max(s['signed_rails'] for s in stats):.4f}% | {mean('dc_i_cellcentre'):+.3f}/{mean('dc_q_cellcentre'):+.3f} |")
    lines.extend(['',
        'With its ordinary DC servo running, G83 averaged only '
        '0.0244%/0.0275% signed-rail occupancy on 5865/5805 MHz, '
        'with worst snapshots 0.0611%/0.0855%. Mean centring was within '
        '0.07 cell per axis. This differs substantially from the old '
        'unheld vendor-DC sweep. Together with the expected higher output '
        'noise at higher gain, these observations do not justify lowering '
        'the no-carrier maximum to G80. They also do not prove that G83 '
        'is optimal for every wanted-signal amplitude or interference case.','',
        '## Archived evidence','',
        f"- {len(captures)} valid audit IQ captures plus {sum(len(r['capture_statistics']) for r in live)} live fixed-MAX witnesses; all rejected transports are separately recorded.",
        '- [All run/profile results, quality and provenance](../research/data/phy_audit_2026-10-24_summary.json).',
        '- [Derived capture statistics and raw SHA-256 hashes](../research/data/phy_audit_2026-10-24_captures.csv.gz).',
        '- [All 503 pinned-library code symbols with name-based triage](../research/data/esp_phy_inventory_2026-10-24.json). A name category or linked definition is not functional or runtime evidence.',
        '- Raw IQ/logs, per-run PSD arrays/plots, firmware provenance and build logs remain local under `research/data/nf_phy_2026-10-24/`; raw-log and manifest hashes are in the summary.',
        '- NBW normalization, kurtosis/rails/drift flags and transport checks are exactly as described in [NF_AUTO_LAB.md](NF_AUTO_LAB.md). No calibrated antenna noise temperature, input carrier power, video SINAD or usable attenuation was measured.',
        ''])
    (ROOT/'docs/PHY_AUDIT_BENCH_2026-10-24.md').write_text('\n'.join(lines),encoding='utf-8',newline='\n')
    print(f'Archived {len(runs)} audit runs / {len(captures)} valid captures')


if __name__=='__main__':main()

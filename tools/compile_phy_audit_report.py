#!/usr/bin/env python3
"""C5VRX by Twotoz/contributors: archive extended noise-only PHY audits."""
import csv
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import statistics

ROOT=Path(__file__).resolve().parents[1]
RAW=ROOT/'research/data/nf_phy_2026-10-24'
POLICY_FOOTPRINTS={
    **{f'LLTF_MASK_{a}{b}':[('600a790c',0x3000,(a<<13)|(b<<12))] for a,b in ((0,0),(1,0),(0,1),(1,1))},
    **{f'RIFS_ARG{x}':[('600a70f4',1,x)] for x in (0,1)},
    **{f'VHT_ARG{x}':[('600a7ce4',0x20,x<<5)] for x in (0,1)},
    **{f'RX11BLR_ARG{x}':[('600a8060',0xc00,0xc00*x),('600a807c',0x800,0x800*x)] for x in (0,1)},
    'POLICY_SHAM':[]}


def stock_registers(text):
    result={}
    for line in text.splitlines():
        if line.startswith('PHYLAB_REG ') and 'stage=STOCK ' in line:
            f=dict(re.findall(r'(\w+)=([^\s]+)',line))
            result.setdefault(f['addr'],f['value'])
    return result


def noop_reason(run,profile):
    if profile.startswith(('STOCK_SHAM_','GAIN_ROW_')) or profile=='POLICY_SHAM':return 'explicit unchanged-state profile'
    owned=POLICY_FOOTPRINTS.get(profile)
    if owned is None:
        owned={
            'FFT_FORCE_OFF':[('600a7800',0x80000,0)],
            'MAC_CCA_ENABLE':[('600a4c5c',0xf0000000,0)],
            'TXCLK_ARG0':[('600a0890',0x30000,0)],
            'TXCLK_ARG1':[('600a0890',0x30000,0x30000)],
            'WIFI_ENABLE_ARG1':[('600a9c18',2,2)]}.get(profile)
    baseline=run['baseline_registers']
    if owned is not None and all(baseline.get(addr) is not None and int(baseline[addr],16)&mask==value for addr,mask,value in owned):
        return 'tested MMIO fields already match baseline'
    if profile in ('ADC_SELECTOR0','ADC_SELECTOR1') and run['adc_baseline']:
        state=run['adc_baseline'];mode=int(profile[-1])
        if int(state['selector_bits'])==(3 if mode else 0) and int(state['value'],16)&4==(0 if mode else 4):
            return 'both ADC domains already match baseline'
    for dc in run['dc_selected']:
        if profile==f"DCO_CENTER_G{dc['gain']}" and dc['original_i']==dc['target_i'] and dc['original_q']==dc['target_q']:
            return 'selected DC pair equals entry pair'
    for cap in run['lo_cap_targets']:
        if profile==f"LO_CAP_CODE_{cap['target']}" and cap['target']==cap['saved']:return 'LO-cap target equals entry code'
    return None


def main():
    runs=[];captures=[];live=[]
    for p in sorted(RAW.glob('audit_*')):
        if not p.is_dir() or not (p/'summary.json').exists():continue
        manifest=json.loads((p/'iq/manifest.json').read_text())
        text=(p/'console.raw.log').read_text(errors='replace')
        begin=next(line for line in text.splitlines() if line.startswith('PHYLAB_BEGIN'))
        meta=dict(re.findall(r'(\w+)=([^\s]+)',begin))
        rows=list(csv.DictReader((p/'captures.csv').open()))
        baseline=stock_registers(text)
        captures.extend(dict(run=p.name,**r) for r in rows)
        runs.append(dict(name=p.name,context=meta,summary=json.loads((p/'summary.json').read_text()),
            valid_captures=len(rows),rejected_captures=manifest.get('rejected_captures',[]),
            raw_log_sha256=hashlib.sha256((p/'console.raw.log').read_bytes()).hexdigest(),
            manifest_sha256=hashlib.sha256((p/'iq/manifest.json').read_bytes()).hexdigest(),
            dc_selected=[dict(re.findall(r'(\w+)=([^\s]+)',line)) for line in text.splitlines() if line.startswith('PHYLAB_DCO_SELECTED ')],
            retransmissions=manifest.get('retransmissions',[]),
            bias_readbacks=[dict(re.findall(r'(\w+)=([^\s]+)',line)) for line in text.splitlines() if line.startswith('PHYLAB_BIAS ')],
            lo_cap_targets=[dict(re.findall(r'(\w+)=([^\s]+)',line)) for line in text.splitlines() if line.startswith('PHYLAB_LOCAP_TARGET ')],
            lo_cap_readbacks=[dict(re.findall(r'(\w+)=([^\s]+)',line)) for line in text.splitlines() if line.startswith('PHYLAB_LOCAP ')],
            baseline_registers=baseline,
            adc_baseline=next((dict(re.findall(r'(\w+)=([^\s]+)',line)) for line in text.splitlines() if line.startswith('PHYLAB_ADC ') and 'stage=STOCK ' in line),None),
            policy_baseline_registers={addr:baseline.get(addr) for addr in ('600a790c','600a70f4','600a7ce4','600a8060','600a807c')} if meta.get('suite') in ('9','11') else {},
            context_note=json.loads((p/'context-note.json').read_text()) if (p/'context-note.json').exists() else None,
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
        '## Isolated LLTF/RIFS/VHT/11b-LR policy fields','',
        'These profiles use a matching calibrated DC hold at the screened '
        'fixed gain, 1200 ms dwell and the same verified transport and '
        'rollback. The initial suite has four windows per stage; the LLTF '
        'follow-up has 16. A profile matching the incoming masked register '
        'values is explicitly a no-op control.','',
        '| Run | Profile | Noise delta dB | Role | Quality/status |',
        '|---|---|---:|---|---|'])
    footprints=POLICY_FOOTPRINTS
    for r in runs:
        if r['context'].get('suite') not in ('9','11'):continue
        baseline=r['policy_baseline_registers']
        for s in r['summary']:
            owned=footprints[s['profile']]
            no_op=all(baseline.get(addr) is not None and int(baseline[addr],16)&mask==value for addr,mask,value in owned)
            role='no-op control' if no_op else 'changed bits'
            if s['status']=='PASS':lines.append(f"| {r['name']} | {s['profile']} | {s['delta_db']:+.3f} | {role} | {s['quality']} |")
            else:lines.append(f"| {r['name']} | {s['profile']} | | {role} | {s['status']} |")
    lines.extend(['',
        'The first policy request saw 5865 MHz in early boot status, but '
        'saved-channel restore completed before the queued suite started '
        'at 5805 MHz. The immutable header and captures identify 5805; '
        'the folder was corrected to audit_5805_policy1 and a context note '
        'retained. The 5865 run was then deliberately retuned and repeated. '
        'Host acquisition now checks actual suite/capture frequency after '
        'the request as well, drains through outer restoration, and rejects '
        'a mismatch instead of silently accepting the early status.','',
        'LLTF00 at5805 MHz gives -0.064/-0.026/-0.040 dB across the '
        'initial and two larger runs, versus corresponding unchanged '
        'LLTF10 controls +0.042/+0.026/+0.023 dB. At5865 MHz LLTF00 '
        'gives -0.039/+0.036/+0.004 dB. The small 5805-specific clue '
        'therefore remains unconfirmed: LLTF00 occurs early in each '
        'batch, so a counterbalanced order with front/end shams is still '
        'needed. It is not promoted or counted as a new noise/sensitivity '
        'gain. Complete results, including opposite-sign repeats and '
        'quality flags, are retained.','',
        '## Adjacent LO-capacitor codes','',
        'Only block0x63/host1/reg21[4:0] changes; upper bits, bias, '
        'crystal/VCO codes, ADC selection, 11p, fixed gain and held DC pair '
        'are retained. The incoming code is a sham and adjacent codes stay '
        'inside the vendor helper interval10..31. Sixteen snapshots per '
        'ABBA stage are independently verified and the original owned field '
        'is restored before resume.','',
        '| Run | Saved / target code | Noise delta dB | Stock drift dB | Quality/status |',
        '|---|---:|---:|---:|---|'])
    for r in runs:
        if not r.get('lo_cap_targets'):continue
        for s in r['summary']:
            target=next(t for t in r['lo_cap_targets'] if s['profile']==f"LO_CAP_CODE_{t['target']}")
            label=f"{target['saved']} / {target['target']}"
            if s['status']=='PASS':lines.append(f"| {r['name']} | {label} | {s['delta_db']:+.3f} | {s['stock_drift_db']:+.3f} | {s['quality']} |")
            else:lines.append(f"| {r['name']} | {label} | | | {s['status']} |")
    lines.extend(['',
        'The measured stock codes match the formula:10 at5865 MHz and12 '
        'at5805 MHz. Code9 at5865 is skipped as outside the vendor interval. '
        'Candidate deltas -0.013/+0.014/+0.007 dB are comparable to '
        'shams +0.017/-0.014 dB. There is no established additional noise '
        'improvement or reason to override the stock cap formula. No RF '
        'conversion-gain, PLL phase-noise or usable-video result is claimed.','',
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
        '## Empirical unchanged-state variation','',
        'Controls are selected from explicit shams, unchanged gain-row '
        'profiles, identical entry/target DC or LO-cap settings, and exact '
        'baseline register fingerprints for documented no-op MMIO/ADC '
        'profiles. They are not selected by whether their measured delta '
        'is near zero. Every complete control remains in the JSON; the '
        'table separates the pre-existing quality flags and groups the '
        'NO_FLAGS controls by actual capture count. These mixed-context '
        'descriptive quantiles are not calibrated confidence bounds for '
        'an individual intervention or an RF sensitivity threshold.','',
        '| Captures/control | NO_FLAGS controls | Median signed delta dB | Median absolute delta dB | 95th percentile absolute delta dB |',
        '|---|---:|---:|---:|---:|'])
    controls=[]
    for run in runs:
        for summary in run['summary']:
            reason=noop_reason(run,summary['profile'])
            if reason and summary['status']=='PASS':
                count=sum(c['run']==run['name'] and c['profile']==summary['profile'] for c in captures)
                controls.append(dict(run=run['name'],profile=summary['profile'],reason=reason,captures=count,
                    delta_db=summary['delta_db'],quality=summary['quality'],quality_flags=summary['quality_flags']))
    groups=[]
    for count in sorted(set(c['captures'] for c in controls)):
        values=[c['delta_db'] for c in controls if c['captures']==count and c['quality']=='NO_FLAGS']
        if not values:continue
        absolute=sorted(abs(v) for v in values)
        q95=statistics.quantiles(absolute,n=100,method='inclusive')[94] if len(values)>1 else absolute[0]
        group=dict(captures_per_control=count,no_flags_controls=len(values),median_delta_db=statistics.median(values),
            median_absolute_delta_db=statistics.median(absolute),p95_absolute_delta_db=q95)
        groups.append(group)
        lines.append(f"| {count} | {len(values)} | {group['median_delta_db']:+.3f} | {group['median_absolute_delta_db']:.3f} | {q95:.3f} |")
    aggregate['unchanged_state_controls']=dict(controls=controls,no_flags_groups=groups,
        flagged_complete_controls=sum(c['quality']!='NO_FLAGS' for c in controls))
    lines.extend(['',
        f"{len(controls)} complete controls were classified; {aggregate['unchanged_state_controls']['flagged_complete_controls']} have quality flags and are retained separately.",
        'A small favourable point estimate must repeat against these controls; '
        'adding selected negative fluctuations does not establish an extra '
        'combined gain. The actual joint comparisons remain the relevant '
        'noise-only evidence.','',
        '## Archived evidence','',
        f"- {len(captures)} valid audit IQ captures plus {sum(len(r['capture_statistics']) for r in live)} live fixed-MAX witnesses; all rejected transports are separately recorded.",
        '- [All run/profile results, quality and provenance](../research/data/phy_audit_2026-10-24_summary.json).',
        '- [Derived capture statistics and raw SHA-256 hashes](../research/data/phy_audit_2026-10-24_captures.csv.gz).',
        '- [Full raw/PSD replay verification](../research/data/phy_audit_2026-10-24_verification.json). Its aggregate SHA-256 identifies the exact summary checked; compare it before treating a later archive as verified.',
        '- [All 503 pinned-library code symbols with name-based triage](../research/data/esp_phy_inventory_2026-10-24.json). A name category or linked definition is not functional or runtime evidence.',
        '- Raw IQ/logs, per-run PSD arrays/plots, firmware provenance and build logs remain local under `research/data/nf_phy_2026-10-24/`; raw-log and manifest hashes are in the summary.',
        '- NBW normalization, kurtosis/rails/drift flags and transport checks are exactly as described in [NF_AUTO_LAB.md](NF_AUTO_LAB.md). No calibrated antenna noise temperature, input carrier power, video SINAD or usable attenuation was measured.',
        ''])
    (ROOT/'docs/PHY_AUDIT_BENCH_2026-10-24.md').write_text('\n'.join(lines),encoding='utf-8',newline='\n')
    (ROOT/'research/data/phy_audit_2026-10-24_summary.json').write_text(json.dumps(aggregate,indent=2)+'\n',encoding='utf-8',newline='\n')
    print(f'Archived {len(runs)} audit runs / {len(captures)} valid captures')


if __name__=='__main__':main()

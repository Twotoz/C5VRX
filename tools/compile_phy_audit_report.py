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
    runs=[];captures=[]
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
            acquisition=json.loads((p/'acquisition.json').read_text())))
    prov=[json.loads(p.read_text()) for p in sorted(RAW.glob('provenance-audit*.json'))]
    aggregate=dict(source='C5VRX by Twotoz and contributors',source_url='https://github.com/Twotoz/C5VRX',
        website_and_discord='https://twotoz.github.io/C5VRX/',
        scope='VTX off, noise only, no calibrated carrier or video; no sensitivity/range claim',
        provenance=prov,total_valid_captures=len(captures),runs=runs)
    (ROOT/'research/data/phy_audit_2026-10-24_summary.json').write_text(json.dumps(aggregate,indent=2)+'\n',encoding='utf-8')
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
        '8,190-byte IQ40 captures per stage, same gain/lane/frequency/offset, exact '
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
        '## Calibration-policy comparisons','',
        'Effective production policy is partial calibration with NVS data '
        'storage. A local full-calibration build disables only the PHY '
        'calibration-and-storage option and selects full RF calibration; '
        'IDF `phy_init.c` then uses its full-calibration branch without '
        'loading/storing a replacement PHY NVS record. No NVS erase is used. '
        'This is a boot experiment, never an in-place MMIO toggle. Its results '
        'must be listed separately from within-boot ABBA deltas and the '
        'partial-calibration build must be restored afterwards.','',
        '## Archived evidence','',
        f"- {len(captures)} valid IQ captures in this audit archive; all rejected transports are separately recorded.",
        '- [All run/profile results, quality and provenance](../research/data/phy_audit_2026-10-24_summary.json).',
        '- [Derived capture statistics and raw SHA-256 hashes](../research/data/phy_audit_2026-10-24_captures.csv.gz).',
        '- [All 503 pinned-library code symbols with name-based triage](../research/data/esp_phy_inventory_2026-10-24.json). A name category or linked definition is not functional or runtime evidence.',
        '- Raw IQ/logs, per-run PSD arrays/plots, firmware provenance and build logs remain local under `research/data/nf_phy_2026-10-24/`; raw-log and manifest hashes are in the summary.',
        '- NBW normalization, kurtosis/rails/drift flags and transport checks are exactly as described in [NF_AUTO_LAB.md](NF_AUTO_LAB.md). No calibrated antenna noise temperature, input carrier power, video SINAD or usable attenuation was measured.',
        ''])
    (ROOT/'docs/PHY_AUDIT_BENCH_2026-10-24.md').write_text('\n'.join(lines),encoding='utf-8')
    print(f'Archived {len(runs)} audit runs / {len(captures)} valid captures')


if __name__=='__main__':main()

#!/usr/bin/env python3
"""Archive derived C5VRX bench results; raw board captures remain local."""
import csv
import gzip
import hashlib
import io
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
RAW=ROOT/'research/data/nf_phy_2026-10-24'
RUNS=['5865_run1','5865_run2','5805_run1','5805_long','5865_long','5865_reboot']
OUT=ROOT/'research/data/nf_phy_2026-10-24_summary.json'


def main():
    aggregate={'source':'C5VRX by Twotoz and contributors',
        'source_url':'https://github.com/Twotoz/C5VRX',
        'website_and_discord':'https://twotoz.github.io/C5VRX/',
        'scope':'VTX off, USB-connected ESP32-C5; noise only; no RF sensitivity/range claim',
        'provenance':json.loads((RAW/'provenance.json').read_text()),'runs':[]}
    captures=[]
    for name in RUNS:
        folder=RAW/name
        manifest=json.loads((folder/'iq/manifest.json').read_text())
        begin=next(line for line in (folder/'console.raw.log').read_text(errors='replace').splitlines() if line.startswith('PHYLAB_BEGIN'))
        summary=json.loads((folder/'summary.json').read_text())
        rows=list(csv.DictReader((folder/'captures.csv').open()))
        captures.extend(dict(run=name,**r) for r in rows)
        aggregate['runs'].append(dict(name=name,begin=begin,summary=summary,
            valid_captures=len(rows),rejected_captures=manifest.get('rejected_captures',[]),
            raw_log_sha256=hashlib.sha256((folder/'console.raw.log').read_bytes()).hexdigest(),
            manifest_sha256=hashlib.sha256((folder/'iq/manifest.json').read_bytes()).hexdigest(),
            acquisition=json.loads((folder/'acquisition.json').read_text())))
    aggregate['total_valid_captures']=len(captures)
    aggregate['total_iq_bytes']=sum(int(r['bytes']) for r in captures)
    OUT.write_text(json.dumps(aggregate,indent=2)+'\n',encoding='utf-8')
    buf=io.StringIO(newline='')
    fields=list(dict.fromkeys(k for row in captures for k in row))
    writer=csv.DictWriter(buf,fieldnames=fields);writer.writeheader();writer.writerows(captures)
    (OUT.parent/'nf_phy_2026-10-24_captures.csv.gz').write_bytes(gzip.compress(buf.getvalue().encode(),mtime=0))
    lines=['# PR #254: ESP32-C5 noise-only bench, 2026-10-24','',
        'C5VRX by Twotoz and the C5VRX contributors. '
        '[Source](https://github.com/Twotoz/C5VRX); '
        '[official website and Discord invite](https://twotoz.github.io/C5VRX/).','',
        '11p is the most promising noise-bandwidth candidate, but this USB-only bench '
        'does **not** establish improved noise figure, input-referred C/N, weak-video '
        'quality, tolerated attenuation or range. The VTX stayed off.','',
        '## Setup and evidence','',
        '- ESP32-C5 revision 1.0, MAC `38:44:be:1f:46:4c`, COM10, 48 MHz crystal; antenna/termination and external RF environment were not independently established.',
        '- ESP-IDF v6.0.2, pinned `libphy.a` SHA-256 `dbf33c418c8d408d4005c849d12a1432deea82e2e5e57de3c8ddf914d104fffb`.',
        '- PR head `eed163246ba9671c0da8f8c8037635957cb1ac36` plus the lab changes in this PR; measured extended build label `eed163246ba9-lab3`, application SHA-256 `'+aggregate['provenance']['application_sha256']+'`.',
        '- MODEM_DIAG signed ultrafine Q4/I4, PARLIO RX40, raw32K, U80P64-R1, DAC20 at TX40, digital filter mode 0; no realtime dataplane changes.',
        '- 5805 MHz / A4 and 5865 MHz / A1, offset 0; same gain within each suite, automatically screened before the suite. Different suites can select different gain rows: compare paired deltas, not their absolute noise power.',
        '- Each candidate: STOCK → TEST → TEST → STOCK; four independent contiguous 8,190-byte snapshots per stage; 300 ms or 1200 ms settling, full masked readback and saved-state restoration.',
        '- Original stock here means the current C5VRX configured/calibrated state (including dfilt0), not an untouched vendor Wi-Fi default.',
        '- The initial older PR251 image was replaced by the PR254 lab. The user cancelled the in-progress flash backup; no further backups were made. Original NVS was not erased. The lab itself persists no profiles.',
        '', '## Measured results','',
        '| Run | Frequency / gain | Dwell | 11p noise delta | Stock/test NBW MHz | Stock drift | Test repeat | Quality |',
        '|---|---|---:|---:|---:|---:|---:|---|']
    import re
    for run in aggregate['runs'][1:]:
        meta=dict(re.findall(r'(\w+)=([^\s]+)',run['begin']))
        s=next(r for r in run['summary'] if r['profile']=='11P')
        lines.append(f"| {run['name']} | {meta['freq']} / G{meta['gain']} | {meta['dwell_ms']} ms | {s['delta_db']:+.3f} dB | {s['stock_nbw_mhz']:.3f}/{s['test_nbw_mhz']:.3f} | {s['stock_drift_db']:+.3f} dB | {s['test_repeat_db']:+.3f} dB | {s['quality']} |")
    lines.extend(['',
        'All five 11p comparisons completed entry and exact rollback. Four have negative '
        'mean noise deltas. The 5805-long mean (+0.105 dB) includes a single test capture '
        'with m2=4.865 and kurtosis=2.447 versus the neighbouring test captures around '
        'm2=3.1–3.3. It is retained, not removed to manufacture a win. Its test-block '
        'disagreement is 0.599 dB; treat that comparison as inconclusive. The separate '
        'median statistic is diagnostic only. Some runs also carry signed-rail or '
        'stock-drift flags; consult the complete JSON and capture CSV.','',
        '| Feature | Board observation | Assessment |',
        '|---|---|---|',
        '| 11p | Narrower effective noise bandwidth in all five paired runs; 5865 deltas −0.291/−0.392/−0.223 dB | Candidate for a later fixed-input VTX/attenuator test; no production default changed |',
        '| IQ correction disabled | +0.821..+0.992 dB IQ noise; increased rails in several runs | Keep calibrated stock correction enabled. It already was on: this is not a new 1 dB improvement over stock |',
        '| Channel A/B controls | Small sign-changing differences, approximately −0.09..+0.06 dB across frequencies/repetitions | No robust universal benefit established |',
        '| NF-auto patterns 0..7 | Small inconsistent deltas; no reproducible bandwidth/noise benefit | No promotion; retain negative results and the isolated low rows |',
        '| NF-auto patterns 8..F | `600A7C50[0]` did not latch in any attempted entry; the other recovered fields were readable | REJECTED_ENTRY, no candidate IQ scored; exact original bits restored before any later independent profile |',
        '| Spur | Verified vendor-used slot0 at `600A7C14` has bit13 clear; raw adjacent words saved | Slot0 test skipped. `600A7C18` has bit13 set, but valid nonzero-slot semantics were not independently verified; do not claim all cancellation is inactive |',
        '| RX IQ scale | Selector 0 and `600A043C=0` recorded read-only | No scale write or guessed calibration override |',
        '', '## Data quality and restoration','',
        f"The archive contains **{aggregate['total_valid_captures']} integrity-checked IQ captures**, "
        f"{aggregate['total_iq_bytes']:,} raw bytes. Five full suites attempted all 16 NF patterns, "
        '11p, both states of channel A/B, IQ enables and inspected spur state; an initial '
        'partial suite is retained separately. A rejected entry is not a successful '
        'hardware mode. Firmware PASS means lifecycle and readback succeeded, not '
        'that noise was clean or RF sensitivity improved.','',
        'One 5865-long CHAN_A_ARG1 capture lost USB bytes (expected offset 3328, '
        'next received offset 4608). The checksum/sequence decoder rejected it. That '
        'profile is incomplete and has no scored mean. Other profiles retain their '
        'own independently verified captures. No missing samples were synthesized.','',
        'All board restore markers reported verified=1. No fatal restore, panic or '
        'watchdog reboot occurred during a suite. An intentional reboot was used '
        'for the final repeat. Final status is A1 / 5865 MHz, offset0, dfilt0, '
        'AUTO gain resumed, startup=0, busy=0; experimental firmware remains installed '
        'with every profile restored. Normal software observers may continue their '
        'ordinary baseline calibration after release of the pause.','',
        'Noise PSD uses complex Welch (1024 Hann, 50% overlap), calculated separately '
        'on each contiguous snapshot and then averaged. NBW is integrated PSD divided '
        'by mean PSD at ±0.5..2 MHz. This is a stated normalization, not a measured '
        'calibrated RF passband or a signal-preservation guarantee. Large signed jumps '
        'are only a folding proxy. Quantization, DC movement, interference and '
        'correlated samples limit confidence.','',
        '## Reproduction and artifacts','',
        '- [Full per-profile summaries and provenance](../research/data/nf_phy_2026-10-24_summary.json).',
        '- [All derived capture statistics, raw filenames and SHA-256 hashes](../research/data/nf_phy_2026-10-24_captures.csv.gz).',
        '- Local raw logs, `.iq` files, per-capture manifests, PSD `.npz`/PNG, build logs and final status: `research/data/nf_phy_2026-10-24/` (ignored by git as raw board data). The summary records raw-log and manifest hashes. Those raw files are local artifacts, not downloadable from this PR.',
        '- `tools/phy_noise_lab.py` verifies and analyses each run; `tools/compile_phy_lab_report.py` builds these derived archives. See [NF_AUTO_LAB.md](NF_AUTO_LAB.md) for protocol, masks, failure handling and commands.',
        '- ESP-IDF firmware build, repository CI host-C/diagnostic regression commands, and actual-source C lifecycle fault-injection tests passed. Transport tests cover missing/corrupt capture rejection; hardware outcomes above remain separately labelled.',
        '', '## Remaining physical acceptance','',
        'Only a C5 was available. There is no calibrated carrier/attenuator, loaded '
        'CVBS measurement or PAL/NTSC signal in this bench. Before promoting 11p, '
        'measure carrier response together with noise at identical input/gain, '
        'input-referred C/N, usable attenuation and sync/chroma/detail on both '
        'standards. Smaller noise bandwidth can attenuate wanted WBFM sidebands.',
        ''])
    (ROOT/'docs/NF_PHY_BENCH_2026-10-24.md').write_text('\n'.join(lines),encoding='utf-8')
    print(f'Archived {len(captures)} captures into derived CSV and {OUT.name}')


if __name__=='__main__':main()

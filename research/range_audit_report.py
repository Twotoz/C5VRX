"""C5VRX by Twotoz/contributors: reproducible summary/figure of the audit.
Synthetic C/N thresholds, not measured RF range. Higher threshold is worse.
"""
from pathlib import Path
import json
import numpy as np
from best_combo import limit

HERE = Path(__file__).resolve().parent

def summaries(rows):
    result = []
    for family, dev, label in sorted({(r['family'], r['dev'], r['label']) for r in rows}):
        rs = [r for r in rows if (r['family'], r['dev'], r['label']) == (family, dev, label)]
        cns = sorted({r['cn'] for r in rs})
        sinad = [float(np.mean([r['sinad'] for r in rs if r['cn'] == cn])) for cn in cns]
        lock = [float(np.mean([r['picture'] for r in rs if r['cn'] == cn])) for cn in cns]
        threshold = limit(cns, sinad, lock)
        result.append(dict(family=family, dev=dev, label=label,
                           threshold_db=float(threshold) if np.isfinite(threshold) else None,
                           seeds=sorted({r['seed'] for r in rs}), cns=cns,
                           sinad=sinad, lock=lock))
    return result

def main():
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    first = summaries(json.loads((HERE / 'data/range_audit.json').read_text(encoding='utf-8')))
    held = summaries(json.loads((HERE / 'data/range_audit_wide_heldout.json').read_text(encoding='utf-8')))
    offsets = summaries(json.loads((HERE / 'data/range_audit_offset_heldout.json').read_text(encoding='utf-8')))
    gates = json.loads((HERE / 'data/range_gate_audit.json').read_text(encoding='utf-8'))
    (HERE / 'data/range_audit_summary.json').write_text(json.dumps(dict(exploratory=first, held_out=held, offset_held_out=offsets), indent=2), encoding='utf-8')
    fig, axes = plt.subplots(2, 2, figsize=(12, 8), constrained_layout=True)
    colors = {1.: '#2274a5', 1.7: '#e67e22', 2.: '#b5394a', .69: '#23866b'}
    ax = axes[0, 0]
    for dev in (1., 2.):
        rs = sorted((r for r in offsets if r['dev'] == dev), key=lambda r: float(r['label'][6:]))
        ax.plot([float(r['label'][6:]) for r in rs], [r['threshold_db'] for r in rs], 'o-', color=colors[dev], label=f'deviation {dev:g}')
        for r in rs:
            if r['threshold_db'] is None:
                ax.plot(float(r['label'][6:]), 10.5, '^', color=colors[dev])
                ax.annotate('>10 dB', (float(r['label'][6:]), 10.5), xytext=(-40,5), textcoords='offset points')
    ax.set(title='Residual offset: physical ADC, 4 held-out seeds', xlabel='Offset from current porch target (MHz)', ylabel='Required C/N (dB)', ylim=(0,11.5))
    ax.legend()
    ax = axes[0, 1]
    for dev in (1.7, 2.):
        for gain, style in ((.85, '-'), (.8, '--'), (.75, ':')):
            rs = [next(r for r in held if r['dev'] == dev and r['label'] == f'{f}MHz_kp{gain:.2f}') for f in (12,16)]
            ax.plot((12,16), [r['threshold_db'] for r in rs], 'o', ls=style, color=colors[dev], label=f'dev {dev:g}, kp {gain:.2f}')
    ax.set(title='Wide VTX + LP: physical ADC, 4 held-out seeds', xlabel='Model filter width (MHz)', ylabel='Required C/N (dB)', xticks=(12,16))
    ax.legend(fontsize=8)
    ax = axes[1, 0]
    devs = (.69, 1., 1.4, 1.7, 2.)
    for key, label, style in (('old_gate_pct', 'With coherence gate', '--'), ('new_gate_pct', 'Envelope/gain gates', '-')):
        values = [np.mean([r[key] for r in gates if r['dev'] == d and r['filter_mhz'] == 12 and r['cn'] == 30]) for d in devs]
        ax.plot(devs, values, 'o', ls=style, label=label)
    ax.set(title='Eligible probes at true 30 dB C/N, 12 MHz', xlabel='VTX deviation', ylabel='Eligible probes (%)', ylim=(0,105))
    ax.legend()
    ax = axes[1, 1]
    for dev in (.69, 1., 1.7):
        rs = sorted((r for r in first if r['family'] == 'quantizer' and r['dev'] == dev), key=lambda r: float(r['label'][3:]))
        ax.plot([float(r['label'][3:]) for r in rs], [r['threshold_db'] for r in rs], 'o-', color=colors[dev], label=f'deviation {dev:g}')
    ax.axvline(4.2, color='#777777', lw=1, ls=':', label='simulation baseline')
    ax.set(title='Gain placement: physical ADC, 2 seeds, oracle gain', xlabel='Total IQ RMS (ultrafine cells)', ylabel='Required C/N (dB)')
    ax.legend(fontsize=8)
    for ax in axes.flat: ax.grid(alpha=.2)
    fig.suptitle('C5VRX firmware audit — synthetic losses, no measured range claim', fontsize=14)
    fig.savefig(HERE / 'data/range_audit_summary.png', dpi=160)
    print('Wrote research/data/range_audit_summary.json and .png')

if __name__ == '__main__': main()

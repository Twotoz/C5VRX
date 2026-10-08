#!/usr/bin/env python3
"""Staged multi-theory FM benchmark for C5VRX by Twotoz and contributors.

100k parameter configurations are not 100k theories or complete hardware
receivers. Short proxy screens, longer selection tests and frozen independent
final tests are counted separately. Nothing is flashed or promoted here.
"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import time

import numpy as np
from scipy import signal as sg
import demod_theories as T
import weak_signal_search as S

F, W, D = T.F, T.F.W, T.F.D
SEEDS = dict(configuration=2601, screen=2701, selection=[2801, 2802],
             final=[2901, 2902, 2903], channel=[3001, 3002, 3003])


def clean_calibration(y, truth, burn, maxlag=24):
    """Vectorized clean-only delay/level calibration; no noisy-truth fitting."""
    end = len(y)-burn
    windows = np.lib.stride_tricks.sliding_window_view(y[burn-maxlag:end+maxlag], end-burn)
    target = truth[burn:end]
    mean = windows.mean(1)
    var = (windows*windows).mean(1)-mean*mean
    cov = windows@(target-target.mean())/len(target)
    gain = cov/np.maximum(var, 1e-20)
    loss = np.var(target)-cov*gain
    best = int(np.argmin(loss))
    return best-maxlag, gain[best], target.mean()-gain[best]*mean[best]


def metrics(y, truth, calibration, burn):
    lag, gain, offset = calibration
    end = len(y)-burn
    x, t = y[burn+lag:end+lag], truth[burn:end]
    err = gain*x+offset-t
    sinad = 10*np.log10(max(np.var(t), 1e-20)/max(np.mean(err*err), 1e-20))
    contrast = 100*gain*np.mean((x-x.mean())*(t-t.mean()))/max(np.var(t), 1e-20)
    return dict(sinad=float(sinad), clicks=float(1000*np.mean(abs(err) > 40)),
                contrast_percent=float(contrast))


def decode(raw, model):
    return T.decode(raw, model) if 'params' in model else W.decode(raw, model)


def measure(model, c, short=False):
    if short:
        sl, burn = slice(4096, 5120), 128
    else:
        sl, burn = slice(None), 3000
    y = decode(c['raw'][sl], model)
    clean = decode(c['clean'][sl], model)
    truth = c['truth'][sl]
    cal = clean_calibration(clean, truth, burn)
    return dict(model=model['name'], case_id=c.get('case_id', f"channel/{c['seed']}/{c['kind']}/{c['cnr']}/{c['rms']}"), seed=c['seed'], kind=c['kind'], cnr=c['cnr'],
                rms=c['rms'], **metrics(y, truth, cal, burn))


def objective(rows):
    return float(np.mean([r['sinad']-.003*r['clicks']-.015*abs(100-r['contrast_percent']) for r in rows]))


def evaluate(models, cases):
    rows = []
    for j, m in enumerate(models):
        rows.extend(measure(m, c) for c in cases)
        print('FULL', j+1, '/', len(models), m['name'], flush=True)
    return rows


def summarize(rows, strong_screen=False):
    result = S.summarize(rows)
    if not strong_screen:
        return result
    # Improved contrast toward 100% is allowed; distortion/overshoot is not.
    refs = {S.case_key(r): r
            for r in rows if r['model'] == 'OVP56'}
    for name, stats in result.items():
        pairs = [(r, refs[S.case_key(r)])
                 for r in rows if r['model'] == name and r['cnr'] >= 14]
        stats['max_strong_contrast_error_increase'] = max(abs(100-a['contrast_percent'])-
                                                        abs(100-b['contrast_percent']) for a, b in pairs)
        stats['strong_ok'] = (stats['max_strong_sinad_loss'] <= .25 and
                              stats['max_strong_click_increase'] <= .5 and
                              stats['max_strong_contrast_error_increase'] <= 1)
    return result


def channel_cases(seeds):
    """Additional falsification: static echoes and time-varying envelope.

    Post-channel echo commutes with the fixed linear channel. These are declared
    synthetic stresses, not a fitted model of the operator's RF environment.
    """
    result = []
    for seed in seeds:
        for kind, cfo, dev, echo_delay, echo, depth in [
            ('random', .25e6, 5.5e6, 3, .35*np.exp(.7j), .5),
            ('bars', 2e6, 8e6, 8, .5*np.exp(1.8j), .25),
            ('multitone', .7e6, 7e6, 15, .25*np.exp(-1j), .75)]:
            sig, noise, _, ire = F.P.stream(seed, n=131072, kind=kind, cfo=cfo, deviation=dev)
            delayed = np.pad(sig[:-echo_delay], (echo_delay, 0))
            t = np.arange(len(sig))/40e6
            envelope = 1-depth*(.5+.5*np.sin(2*np.pi*t/150e-6))
            signal = (sig+echo*delayed)*envelope
            for rms in (1.5, 3):
                for cnr in (0, 2, 4, 6, 8, 14, 18):
                    c = dict(seed=seed, kind=kind, cnr=cnr, rms=rms,
                             truth=D.goggle(ire), echo_delay=echo_delay, echo_real=echo.real,
                             echo_imag=echo.imag, fade_depth=depth)
                    def raw(n):
                        z = W.iq_at(signal, n, cnr, rms)
                        # Unseen DC/skew stress remains in both clean and noisy IQ.
                        return D.raw_bytes(z.real*1.05+1j*z.imag+.15-.1j).astype(np.uint8)
                    c['raw'], c['clean'] = raw(noise), raw(np.zeros_like(noise))
                    result.append(c)
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--screen-only', action='store_true', help='write the proxy screen and stop before selection')
    ap.add_argument('--configurations', type=int, default=100000)
    ap.add_argument('--lut-winner', type=Path, required=True)
    ap.add_argument('--strong-screen', action='store_true',
                    help='add strong proxy cases and permit contrast improvements')
    ap.add_argument('--seed-offset', type=int, default=0,
                    help='fresh signal seeds; parameter configurations stay fixed')
    ap.add_argument('--reuse-screen', type=Path,
                    help='reuse an unchanged, validated proxy screen; new later-stage seeds still apply')
    a = ap.parse_args()
    if a.configurations < 100:
        ap.error('--configurations must be >=100 for the family quotas')
    if (a.output/'protocol.json').exists():
        ap.error('use a fresh output directory to preserve frozen evidence')
    a.output.mkdir(parents=True, exist_ok=True)
    seeds = {k: (v if k == 'configuration' else [s+a.seed_offset for s in v]
                 if isinstance(v, list) else v+a.seed_offset) for k, v in SEEDS.items()}
    reused = None
    if a.reuse_screen:
        old_protocol = json.loads((a.reuse_screen.parent/'protocol.json').read_text())
        if old_protocol['configurations'] != a.configurations or old_protocol['strong_proxy_screen'] != a.strong_screen:
            ap.error('reused screen configuration count or strong-screen policy differs')
        if old_protocol['code_sha256']['demod_theories.py'] != hashlib.sha256(Path(T.__file__).read_bytes()).hexdigest():
            ap.error('theory kernels changed since the reusable screen')
        seeds['screen'] = old_protocol['seeds']['screen']
        reused = hashlib.sha256(a.reuse_screen.read_bytes()).hexdigest()
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in
              [Path(__file__), Path(T.__file__), Path(F.__file__), Path(W.__file__), a.lut_winner]}
    S.write_json(a.output/'protocol.json', dict(seeds=seeds, configurations=a.configurations,
                 objective=S.OBJECTIVE, strong_guards=S.GUARDS, code_sha256=hashes,
                 strong_proxy_screen=a.strong_screen,
                 reused_screen_sha256=reused,
                 contrast_guard='error increase relative to 100%' if a.strong_screen else 'absolute change from baseline',
                 screen='three 1024-sample IQ windows; fixed received inputs; clean-only calibration',
                 selection='best three per theory; nominal + DC/CFO/deviation/skew stress',
                 final='one selection winner per theory, frozen before final/channel seeds; no reselection',
                 feasibility='all floating/recursive theory kernels are offline unique40 references; LUT candidates are pair20 source-compiled'))
    refs = [F.load_reference('OVP56'), F.load_reference('VLP56'), dict(name='HC50')]
    lut = json.loads(a.lut_winner.read_text())['winner']
    if lut['name'] != 'OVP56':
        refs.append(lut)
    proxy = [F.case(seeds['screen'], kind, cnr, rms) for kind, cnr, rms in
             [('random', 2, 1.5), ('bars', 4, 3), ('multitone', 6, 5)]]
    if a.strong_screen:
        proxy += [F.case(seeds['screen'], kind, cnr, rms) for kind, cnr, rms in
                  [('random', 18, 3), ('bars', 14, 3), ('multitone', 14, 1.5)]]
    proxy_reference = [measure(refs[0], c, short=True) for c in proxy]
    leaders = {name: [] for name in T.FAMILIES}
    counts = {name: 0 for name in T.FAMILIES}
    proxy_eligible_counts = {name: 0 for name in T.FAMILIES}
    start = time.monotonic()
    # Stream every configuration and score to disk; do not retain 100k large
    # objects or silently report proxy cases as complete final evaluations.
    with (a.output/'screen.csv').open('w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['name', 'family', 'objective', 'proxy_strong_ok', 'strong_sinad_loss', 'parameters'])
        saved = list(csv.DictReader(a.reuse_screen.open())) if a.reuse_screen else None
        if saved is not None and len(saved) != a.configurations:
            ap.error('reused screen has missing rows')
        for j, model in enumerate(T.configurations(a.configurations, SEEDS['configuration'])):
            if saved is not None:
                row = saved[j]
                if (row['name'] != model['name'] or row['family'] != model['family'] or
                        json.loads(row['parameters']) != model['params']):
                    ap.error('reused configuration manifest differs')
                value, loss, eligible = float(row['objective']), float(row['strong_sinad_loss']), row['proxy_strong_ok'] == 'True'
            else:
                rows = [measure(model, c, short=True) for c in proxy]
                value = objective([r for r in rows if r['cnr'] <= 6])
                strong_pairs = [(r, b) for r, b in zip(rows, proxy_reference) if r['cnr'] >= 14]
                loss = max((b['sinad']-r['sinad'] for r, b in strong_pairs), default=0.)
                errors = max((r['clicks']-b['clicks'] for r, b in strong_pairs), default=0.)
                contrast_error = max((abs(100-r['contrast_percent'])-abs(100-b['contrast_percent'])
                                      for r, b in strong_pairs), default=0.)
                eligible = loss <= .25 and errors <= .5 and contrast_error <= 1
            rank = (eligible, value-2*max(0, loss-.25)) if a.strong_screen else (True, value)
            writer.writerow([model['name'], model['family'], value, eligible, loss, json.dumps(model['params'])])
            counts[model['family']] += 1
            proxy_eligible_counts[model['family']] += int(eligible)
            group = leaders[model['family']]
            group.append((rank, model))
            group.sort(key=lambda pair: pair[0], reverse=True)
            del group[3:]
            if (j+1) % 1000 == 0:
                f.flush()
                print('SCREEN', j+1, '/', a.configurations, 'elapsed_s', round(time.monotonic()-start), flush=True)
    shortlisted = [m for group in leaders.values() for _, m in group]
    S.write_json(a.output/'screen_shortlist.json', shortlisted)
    if a.screen_only:
        return
    selection_cases = F.dataset(seeds['selection'], (0, 2, 4, 6, 8, 14, 18), (1.5, 3))
    selection_cases += F.dataset(seeds['selection'], (2, 6, 14, 18), (1.5, 3), stress=True)
    selection = evaluate(shortlisted+refs, selection_cases)
    S.write_rows(a.output/'selection.csv', selection)
    summary = summarize(selection, a.strong_screen)
    winners = []
    for family in T.FAMILIES:
        group = [m for m in shortlisted if m['family'] == family]
        eligible = [m for m in group if summary[m['name']]['strong_ok']]
        # A diagnostic family winner is still recorded when every model fails
        # the strong gate; it is explicitly ineligible for promotion.
        win = max(eligible or group, key=lambda m: summary[m['name']]['weak_objective'])
        winners.append(dict(win, selection_eligible=bool(eligible)))
    S.write_json(a.output/'frozen.json', dict(winners=winners, selection=summary,
                                            seed_policy=seeds, no_reselection=True))
    final_cases = F.dataset(seeds['final'], (0, 2, 4, 6, 8, 10, 14, 18), (.75, 1.5, 3, 5), n=131072)
    final = evaluate(winners+refs, final_cases)
    channel = evaluate(winners+refs, channel_cases(seeds['channel']))
    S.write_rows(a.output/'final.csv', final)
    S.write_rows(a.output/'channel.csv', channel)
    fs, cs = summarize(final, a.strong_screen), summarize(channel, a.strong_screen)
    result = dict(configurations_screened=a.configurations, theory_families=len(T.FAMILIES),
                  counts=counts, proxy_eligible_counts=proxy_eligible_counts,
                  short_proxy_tests=a.configurations*len(proxy), strong_screen=a.strong_screen,
                  full_selection_tests=len(selection), full_final_tests=len(final),
                  full_channel_tests=len(channel), seeds=seeds, final=fs, channel=cs,
                  frozen_family_winners=winners, elapsed_s=time.monotonic()-start,
                  scope='synthetic clipped-Q4/AWGN/echo/fade/DC/skew; same passive DAC and goggle model; clean-only calibration; offline unique40 theory references versus compiled pair20 LUTs; no physical acceptance or global optimum')
    S.write_json(a.output/'summary.json', result)
    print('RESULT', json.dumps(result), flush=True)


if __name__ == '__main__':
    main()

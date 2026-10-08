#!/usr/bin/env python3
"""Weak-video demod search for C5VRX by Twotoz and contributors.

Builds on PR185 and the PR186 occupancy sweep. Research only; hardware bounds
are compiled, and independent confirmation can veto a winner. Never writes
firmware tables/defaults. Does not enumerate all legal hardware architectures.
"""
import argparse
import csv
import hashlib
import json
from pathlib import Path

import numpy as np
import weak_signal_fit as F

W, B, P, D = F.W, F.B, F.P, F.D
SEEDS = dict(training=[2101, 2102], screen=[2201], selection=[2301, 2302],
             final=[2401, 2402, 2403], stress=[2501, 2502, 2503])
OBJECTIVE = 'mean weak SINAD - .003*errors/1000 - .015*abs(100-contrast_percent), C/N 0/2/4/6'
GUARDS = dict(strong_sinad_loss_db=.25, strong_error_increase_per1000=.5,
              strong_contrast_change_percent=1, clean_level_error=1)


def digest(m):
    keys = ('current_tokens', 'previous_shift', 'middle_bits', 'encoder', 'map')
    return hashlib.sha256(json.dumps([m[k] for k in keys]).encode()).hexdigest()


def fit_table(m, h, strength):
    w, s, _ = h
    e = np.array(m['encoder'])
    n, contexts = m['current_tokens'], len(w)
    ix = (((e[:, None] >> m['previous_shift'])*contexts +
           np.arange(contexts)[:, None, None])*n + e[None, :]).ravel()
    k = m['previous_tokens']*n
    cw = np.bincount(ix, weights=w.ravel(), minlength=k)
    cs = np.bincount(ix, weights=s.ravel(), minlength=k)
    # Analytic phase prior only for empty/sparsely trained bins.
    i, q = D.cells(np.arange(256))
    phase = np.angle(i+.5+1j*(q+.5))*128/np.pi
    phase_codes = np.rint(np.clip((D.wrap(phase[None, :]-phase[:, None])-P.OFFSET)*P.SCALE, 0, 63)).astype(int)
    prior_sum = np.bincount(ix, weights=np.broadcast_to(F.LEVELS[phase_codes], w.shape).ravel(), minlength=k)
    prior_count = np.bincount(ix, minlength=k)
    prior = np.divide(prior_sum, prior_count, out=np.full(k, F.LEVELS[32]), where=prior_count > 0)
    value = np.divide(cs+strength*prior, cw+strength, out=prior.copy(), where=cw+strength > 0)
    return F.nearest(value).reshape(m['previous_tokens'], n).tolist()


def evaluate(models, cases):
    rows = []
    for j, m in enumerate(models):
        for c in cases:
            y = W.decode(c['raw'], m)
            sinad, clicks = W.score(y, c['truth'], c['calibration'])
            rows.append(dict(model=m['name'], seed=c['seed'], kind=c['kind'],
                             cnr=c['cnr'], rms=c['rms'], sinad=sinad, clicks=clicks,
                             contrast_percent=W.contrast(y, c['truth'], c['calibration'])))
        if j % 64 == 0:
            print('EVALUATED', j+1, '/', len(models), flush=True)
    return rows


def case_key(r):
    return r.get('case_id', (r['seed'], r['kind'], r['cnr'], r['rms']))


def summarize(rows):
    refs = {case_key(r): r
            for r in rows if r['model'] == 'OVP56'}
    if len(refs) != sum(r['model'] == 'OVP56' for r in rows):
        raise ValueError('Ambiguous reference cases: provide unique case_id including channel conditions')
    result = {}
    for name in sorted({r['model'] for r in rows}):
        rr = [r for r in rows if r['model'] == name]
        weak = [r for r in rr if r['cnr'] <= 6]
        strong = [r for r in rr if r['cnr'] >= 14]
        pairs = [(r, refs[case_key(r)]) for r in strong]
        guard = dict(max_strong_sinad_loss=max(b['sinad']-a['sinad'] for a, b in pairs),
                     max_strong_click_increase=max(a['clicks']-b['clicks'] for a, b in pairs),
                     max_strong_contrast_change=max(abs(a['contrast_percent']-b['contrast_percent']) for a, b in pairs))
        result[name] = dict(weak_objective=float(np.mean([r['sinad']-.003*r['clicks']-
                                                        .015*abs(100-r['contrast_percent']) for r in weak])) if weak else None,
                            weak_sinad=float(np.mean([r['sinad'] for r in weak])) if weak else None,
                            weak_clicks=float(np.mean([r['clicks'] for r in weak])) if weak else None,
                            weak_contrast=float(np.mean([r['contrast_percent'] for r in weak])) if weak else None,
                            strong_ok=guard['max_strong_sinad_loss'] <= GUARDS['strong_sinad_loss_db'] and
                                      guard['max_strong_click_increase'] <= GUARDS['strong_error_increase_per1000'] and
                                      guard['max_strong_contrast_change'] <= GUARDS['strong_contrast_change_percent'],
                            **guard)
    return result


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def write_rows(path, rows):
    with path.open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--variants', type=int, default=512)
    args = ap.parse_args()
    if args.variants < 248:
        ap.error('--variants must be >=248 to cover the 62 layouts and four initializer families')
    if (args.output/'protocol.json').exists():
        ap.error('use a fresh output directory; frozen experiment evidence must not be overwritten')
    args.output.mkdir(parents=True, exist_ok=True)
    write_json(args.output/'protocol.json', dict(seeds=SEEDS, objective=OBJECTIVE, guards=GUARDS,
                                               variants=args.variants, refinement_regularizations=[.1, 1.],
                                               refinement_level_weight=100, final_policy='one frozen selection winner; confirmation veto only; no reselection'))
    refs = [F.load_reference('OVP56'), F.load_reference('VLP56'), dict(name='HC50')]
    train = F.dataset(SEEDS['training'], (0, 2, 4, 6, 14), (1.5, 3))
    layouts = list(B.layouts())
    hist = {}
    for bits in {tuple(m['middle_bits']) for m in layouts}:
        for profile in ('edge', 'balanced'):
            seq = [(c['raw'], c['endpoint'], (4 if profile == 'edge' else 2) if c['cnr'] <= 6 else 4)
                   for c in train]
            hist[bits, profile] = B.histogram(bits, seq)
    rng = np.random.default_rng(2001)
    families = ('phase', 'polar', 'cartesian', 'random-clusters')
    models, seen = [], set()
    for j in range(args.variants):
        m = dict(layouts[(j//4) % len(layouts)])
        family, profile = families[j % 4], ('edge', 'balanced')[(j//248) % 2]
        m.update(name=f'weak-{j:04d}-{family}', family=family, profile=profile,
                 encoder=B.initialize(m['current_tokens'], family, rng).tolist())
        m['map'] = fit_table(m, hist[tuple(m['middle_bits']), profile], (0, 4, 16, 64)[(j//16) % 4])
        d = digest(m)
        if d not in seen:
            seen.add(d)
            models.append(m)
    # Include the retained VLP/OVP encoder, with new occupancy-aware fits and
    # conservative blends. Different tables count as models, not architectures.
    for profile in ('edge', 'balanced'):
        for strength in (0, 4, 16, 64):
            m = dict(refs[0], family='retained-encoder', profile=profile)
            fitted = np.array(fit_table(m, hist[(), profile], strength))
            for alpha in (.125, .25, .5, 1.):
                volts = (1-alpha)*F.LEVELS[np.array(m['map'])]+alpha*F.LEVELS[fitted]
                v = dict(m, name=f'weak-retained-{profile}-{strength}-{alpha}', map=F.nearest(volts).tolist())
                d = digest(v)
                if d not in seen:
                    seen.add(d)
                    models.append(v)
    print('GENERATED', len(models), 'distinct candidates', flush=True)
    write_json(args.output/'candidates.json', models)
    screen = evaluate(models+refs, F.dataset(SEEDS['screen'], (2, 6, 14), (1.5, 3)))
    write_rows(args.output/'screen.csv', screen)
    stats = summarize(screen)
    # Family/layout quotas preserve context architectures before the expensive
    # filtered solve. Proxy/strong losses are recorded, not silently discarded.
    groups = {}
    for m in models:
        key = m['family'], tuple(m['middle_bits'])
        groups.setdefault(key, []).append(m)
    shortlisted = []
    for group in groups.values():
        shortlisted.extend(sorted(group, key=lambda m: stats[m['name']]['weak_objective'], reverse=True)[:2])
    screen_order = sorted(shortlisted, key=lambda m: stats[m['name']]['weak_objective']-
                          2*max(0, stats[m['name']]['max_strong_sinad_loss']-.25), reverse=True)
    # Refine OVP56 plus three distinct top encoders/layouts from screening.
    bases = [refs[0]]
    for m in screen_order:
        if not any(m['encoder'] == b['encoder'] and m['middle_bits'] == b['middle_bits'] and
                   m['previous_shift'] == b['previous_shift'] for b in bases):
            bases.append(m)
        if len(bases) == 4:
            break
    refined = []
    for base in bases:
        for reg in (.1, 1.):
            m = F.refine(base, train, reg)
            m['name'] = base['name']+f'-video-{reg}'
            B.source_check(m)
            refined.append(m)
            print('REFINED', m['name'], m['fit'], flush=True)
            write_json(args.output/'refined.json', refined)
    # Near-baseline integer blends offer conservative candidates if a complete
    # refit trades too much strong-signal quality for weak-signal performance.
    for m in refined.copy():
        if m['encoder'] == refs[0]['encoder'] and m['middle_bits'] == []:
            for alpha in (.125, .25, .5, .75):
                volts = (1-alpha)*F.LEVELS[np.array(refs[0]['map'])]+alpha*F.LEVELS[np.array(m['map'])]
                refined.append(dict(m, name=m['name']+f'-blend-{alpha}', map=F.nearest(volts).tolist()))
    selection_models = shortlisted+refined+refs
    selection = evaluate(selection_models, F.dataset(SEEDS['selection'], (0, 2, 4, 6, 8, 14, 18), (1.5, 3, 5)))
    write_rows(args.output/'selection.csv', selection)
    sm = summarize(selection)
    eligible = [refs[0]]
    level_checks = {}
    for m in selection_models:
        if m['name'] in ('OVP56', 'VLP56', 'HC50') or not sm[m['name']]['strong_ok']:
            continue
        C, levels = F.clean_constraints(m, refs[0])
        error = float(np.max(abs(C@F.LEVELS[np.array(m['map']).ravel()]-levels)))
        level_checks[m['name']] = error
        if error <= GUARDS['clean_level_error']:
            eligible.append(m)
    win = max(eligible, key=lambda m: sm[m['name']]['weak_objective'])
    write_json(args.output/'frozen.json', dict(winner=win, eligible=[m['name'] for m in eligible],
                                             selection=sm, clean_level_errors=level_checks))
    source = B.source_check(win)
    (args.output/'winner.bsasm').write_text(source, encoding='utf-8')
    print('FROZEN', win['name'], sm[win['name']], flush=True)
    final_models = [win]+[r for r in refs if r['name'] != win['name']]
    final = evaluate(final_models, F.dataset(SEEDS['final'], (0, 2, 4, 6, 8, 10, 14, 18), (.75, 1.5, 3, 5), n=131072))
    stress = evaluate(final_models, F.dataset(SEEDS['stress'], (0, 2, 4, 6, 8, 14, 18), (1.5, 3, 5), n=131072, stress=True))
    write_rows(args.output/'final.csv', final)
    write_rows(args.output/'stress.csv', stress)
    fs, ss = summarize(final), summarize(stress)
    C, levels = F.clean_constraints(win, refs[0], (1.4, 2.2, 3.2, 4.1, 5.2),
                                             (.35e6, .7e6, 1.3e6, 1.9e6), (-40, -10, 10, 30, 70, 100))
    holdout_level_error = float(np.max(abs(C@F.LEVELS[np.array(win['map']).ravel()]-levels)))
    accepted = (win['name'] != 'OVP56' and fs[win['name']]['strong_ok'] and ss[win['name']]['strong_ok'] and
                fs[win['name']]['weak_objective'] > fs['OVP56']['weak_objective'] and
                ss[win['name']]['weak_objective'] > ss['OVP56']['weak_objective'] and holdout_level_error <= 1)
    summary = dict(attempted_architecture_variants=args.variants, distinct_endpoint_candidates=len(models),
                   layouts=len(layouts), screened=len(models), shortlisted=len(shortlisted),
                   filtered_refits=len(bases)*2, selection_models=len(selection_models),
                   winner=win['name'], confirmed=accepted, final=fs, stress=ss,
                   holdout_level_error=holdout_level_error, seeds=SEEDS, objective=OBJECTIVE, guards=GUARDS,
                   scope='two implemented LUT8 endpoint/middle-context schedule families; local fits; synthetic clipped-Q4/AWGN; no global optimum or physical RF/video acceptance')
    write_json(args.output/'summary.json', summary)
    print('RESULT', json.dumps(summary), flush=True)


if __name__ == '__main__':
    main()

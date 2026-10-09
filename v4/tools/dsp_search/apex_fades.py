#!/usr/bin/env python3
"""Sub-ms fade recovery per demod (C5VRX; PR #190 review, 2026-10-09).

FusionDemod's supervisor runs on 50 ms windows, so 0.1-1 ms fades must be
survived inside each program. Cases are AFC-centred randomized VTX/boards
with a carrier outage (noise remains) at 1700 us (at least 4 lines left in the window); the score is missed H/V
sync beyond the lines inside the outage itself (64 us lines), i.e. the
recovery cost, plus the teacher as an information reference.
"""
import json
import sys
from pathlib import Path
import numpy as np


def main():
    import waveforms as V
    import lane_profile as L
    import engine as S
    import edge_fsm as F
    import overlay_fsm as O
    import search_edge as E
    import apex_tiers as T
    from video_metrics import waveform
    from pair_autofit import remap
    root = Path(__file__).resolve().parents[2]
    opts = json.loads((root / 'tools/range_options.json').read_text())['options']
    edge = next(o for o in opts if o['label'] == 'EDGE RANGE LAB')['model']['params']
    pair = next(o for o in opts if o['label'] == 'PAIR RANGE LAB')['model']
    r32 = json.loads((root / 'tools/range32_model.json').read_text())
    ep = {k: v for k, v in edge.items() if k not in ('fit_deviation', 'fit_centre_hz')}
    hyp = json.loads((root / 'tools/detector_study/models/theory_hypotheses.json').read_text())
    p0 = np.array(next(m for m in hyp['winners'] if m['family'] == 'pll')['params'], float)
    rng = np.random.default_rng(481000); out = {}
    for fade in (0, 50, 100, 300, 500):
        for cnr in (10, 16, 30):
            for k in range(6):
                std = ('PAL', 'NTSC')[k % 2]; dev = float(rng.uniform(.75, 1.35))
                cfo = -436e3 + dev * 1436e3; rms = L.scale(3) * float(rng.uniform(.85, 1.15))
                s = 481000 + 97 * cnr + 13 * k + fade
                win = ((1700., 1700. + fade),) if fade else ()
                c = V.make_case(std, s, cnr, rms, short=True, cfo_hz=cfo, stimulus_seed=s + 1, lane_model=L.LANE,
                                deviation=dev, loss_windows_us=win, pattern='bars')
                n = V.make_case(std, s, cnr, rms, short=True, cfo_hz=cfo, stimulus_seed=s + 1, lane_model=L.LANE,
                                pattern='bars')
                for key in ('raw', 'clean', 'truth', 'region'): c[key] = c[key][65536:98304]
                c['calibration'] = V.M.clean_calibration(S.decode(n['clean'][65536:98304], S.F.load_reference('OVP56')),
                                                         n['truth'][65536:98304], 3000)
                fit = dict(fit_deviation=dev, fit_centre_hz=cfo)
                inside = fade / 64.
                for name, y in (('RANGE32', O.decode(c['raw'], r32)), ('PAIR+AF', O.decode(c['raw'], remap(pair, dev, cfo))),
                                ('EDGE+AF', O.decode(c['raw'], F.synthesize(dict(ep, **fit)))),
                                ('teacher', T.teacher_decode(c['raw'], p0, dev, cfo))):
                    try:
                        r = waveform(y, E.clamp(c, y)); miss = r['h_missing'] + r['v_missing']
                    except Exception:
                        miss = 30
                    out.setdefault(f'{fade}us/C/N{cnr}/{name}', []).append(max(0., miss - inside))
    table = {k: float(np.mean(v)) for k, v in out.items()}
    Path(sys.argv[1]).write_text(json.dumps(table, indent=1))
    names = ('RANGE32', 'PAIR+AF', 'EDGE+AF', 'teacher')
    print('fade  C/N | ' + ' | '.join(names) + '   (extra missed sync per case after the outage)')
    for fade in (0, 50, 100, 300, 500):
        for cnr in (10, 16, 30):
            print(f'{fade:4d}us {cnr:3d} | ' + ' | '.join('%5.1f' % table[f'{fade}us/C/N{cnr}/{n}'] for n in names), flush=True)


if __name__ == '__main__':
    main()

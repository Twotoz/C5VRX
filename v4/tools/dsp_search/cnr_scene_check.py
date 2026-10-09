#!/usr/bin/env python3
"""Is the phase-C/N control input scene-dependent? (PR #190 review, Twotoz.)

The second phase difference also grows with real FM video (fast edges,
PAL/NTSC chroma), so at a fixed RF C/N the estimate could move with picture
content and VTX deviation and steer the CVT wrongly. Same C/N, amplitude
and CFO; only the pattern, standard and deviation change.
"""
import itertools
import numpy as np


def main():
    import waveforms as V
    import lane_profile as L
    import phase_cnr as PC
    pats = ('bars', 'zoneplate', 'checker', 'texture')
    print('C/N | ' + ' | '.join(f'{p:>9s}' for p in pats) + ' | spread over content+std+dev (dB)')
    for cnr in (4, 8, 12, 16, 20, 30):
        vals = {}
        for pat, std, dev in itertools.product(pats, ('PAL', 'NTSC'), (.8, 1., 1.3)):
            c = V.make_case(std, 1234 + cnr, cnr, L.scale(3), short=True, cfo_hz=-436e3 + dev * 1436e3,
                            stimulus_seed=77, lane_model=L.LANE, deviation=dev, pattern=pat)
            raw = c['raw'][65536:98304]
            vals[(pat, std, dev)] = np.median([PC.phase_cnr_x10(raw[j:j + 4096]) for j in range(0, len(raw), 4096)]) / 10
        allv = list(vals.values())
        print(f'{cnr:3d} | ' + ' | '.join('%9.1f' % np.mean([v for k, v in vals.items() if k[0] == p]) for p in pats)
              + f' | {min(allv):.1f}..{max(allv):.1f} ({max(allv) - min(allv):.1f})', flush=True)


if __name__ == '__main__':
    main()

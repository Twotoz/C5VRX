"""C5VRX by Twotoz/contributors: synthetic native sync-slicer comparison.

Creates input for the ACTUAL sync_native_track.h qualifier. Ideal inertial
filtering is a model, not RMT silicon/latency or monitor-lock proof. The flag
is a proposed hardware comparator; this script does not modify firmware/LUTs.
"""
import argparse
import json
from pathlib import Path

import numpy as np
from numba import njit

import sim
from u85q_lp import lp_codes
from sync_lut_feasibility import tables


@njit(cache=True)
def pulse_runs(bits, dwell):
    """A changed level must persist dwell samples before output changes.

    Return complete HIGH runs with real observed beginnings; drop the initial
    partial run, like the firmware parser. Shift to20MHz RMT-local ticks.
    """
    state = candidate = bool(bits[0])
    since = 0
    start = -1
    runs = []
    for i in range(1, len(bits)):
        bit = bool(bits[i])
        if bit != candidate:
            candidate = bit
            since = i
        if candidate != state and i - since >= dwell:
            state = candidate
            if state:
                start = i
            elif start >= 0:
                tick = i // 2
                begin = start // 2
                runs.append((begin, tick - begin))
                start = -1
    return runs


RUNNER = r'''
#include <stdio.h>
#include <inttypes.h>
#include "sync_native_track.h"
static void report(int id,const sync_native_track_t *s) {
 if(id>=0)printf("%d %u %u %u %u\n",id,(unsigned)s->valid,
   (unsigned)s->standard,(unsigned)s->qualified_fields,(unsigned)s->h_standard);
}
int main(void) {
 sync_native_track_t track={0};int id,prior=-1;uint64_t tick;uint32_t width;
 while(scanf("%d %"SCNu64" %"SCNu32,&id,&tick,&width)==3) {
  if(id!=prior){report(prior,&track);sync_native_reset(&track);prior=id;}
  if(width)sync_native_low(&track,tick+1000000u,width);
 }
 report(prior,&track);return 0;
}
'''


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--cn', type=float, default=24.)
    ap.add_argument('--rms', type=float, default=3.2)
    ap.add_argument('--clean', action='store_true')
    ap.add_argument('--lp', action='store_true', help='Post-LP comparator is a theoretical upper bound, not the proposed raw flag.')
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    luts = [np.asarray(x, np.uint16) for x in tables()]
    rows = []
    with (args.out / 'pulses.txt').open('w') as stream:
        for standard in ('PAL', 'NTSC'):
            for deviation in (.69, 1.):
                for seed in (41, 137):
                    case = sim.make_case(seed, args.cn, standard, deviation,
                                         filter_hz=12e6, fields=3,rms=args.rms)
                    for level in (0, 7):
                        decode=lp_codes if args.lp else sim.u85q_codes
                        codes = decode(case['clean' if args.clean else 'raw'], luts[level])
                        variants = {
                            'bit4_low': (codes & 16) == 0,
                            'bit4_high': (codes & 16) != 0,
                            'code_below19': codes < 19,
                        }
                        for name, bits in variants.items():
                            ident = len(rows)
                            runs = pulse_runs(bits, 40)
                            rows.append(dict(id=ident, standard=standard,
                                             deviation=deviation, seed=seed,
                                             kp_level=level, cn_db=args.cn,rms=args.rms,clean=args.clean,
                                             variant=name, runs=len(runs),
                                             model=('post-LP theoretical comparator' if args.lp else 'normal U85Q raw comparator')+', ideal1us inertial filter'))
                            stream.write(f'{ident} 0 0\n')
                            for tick, width in runs:
                                stream.write(f'{ident} {tick} {width}\n')
                        print(standard, deviation, seed, level, flush=True)
    (args.out / 'cases.json').write_text(json.dumps(rows, indent=2) + '\n')
    (args.out / 'runner.c').write_text(RUNNER)


if __name__ == '__main__':
    main()

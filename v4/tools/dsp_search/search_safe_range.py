#!/usr/bin/env python3
"""Fresh range search after failed MAX board video; retain fine observations."""
import argparse
import sys
import numpy as np
import search_range as R
import range_objective as J
from search_compact_range import compact

original_propose, original_mutate = R.propose, R.mutate


def constrain(p, rng, policy):
    p = dict(p)
    b = p['token_bits'] if p['token_bits'] >= 4 else int(rng.choice([4, 5, 6]))
    counter = bool(p.get('counter_phase', False)) and policy == 'plain'
    if counter:
        b = 5
    if policy == 'confidence':
        b = max(5, b)
        groups = int(rng.choice([g for g in (2, 4) if (1 << b) // g >= 16]))
    else:
        groups = p['confidence_groups']
        if (1 << b) // groups < 16:
            groups = 1
    if policy == 'compact' and b == 6:
        b, groups = 5, 1
    states = 1 << (10 - b)
    phases = 32 if counter else max(16, min(p['phases'], states))
    p.update(token_bits=b, confidence_groups=groups, phases=phases,
             counter_phase=counter, context_bits=2 if policy == 'context' else 0)
    if policy == 'context':
        p.update(context_weight=min(.15, p.get('context_weight', .1)),
                 context_radius=min(4, p.get('context_radius', 2)))
    if policy == 'compact':
        p = compact(p, rng)
    assert p['phases'] >= 16 and (1 << p['token_bits']) // p['confidence_groups'] >= 16
    return p


def main():
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument('--policy', choices=('plain', 'context', 'confidence', 'compact'), required=True)
    args, rest = ap.parse_known_args()
    if '--profile' in rest:
        ap.error('profile is fixed to safe_range')
    J.PROFILES['safe_range']['policy'] = args.policy
    R.propose = lambda rng: constrain(original_propose(rng), rng, args.policy)
    R.mutate = lambda p, rng: constrain(original_mutate(p, rng), rng, args.policy)
    sys.argv = [sys.argv[0], *rest, '--profile', 'safe_range']
    R.main()


if __name__ == '__main__':
    main()

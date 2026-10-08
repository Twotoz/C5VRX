#!/usr/bin/env python3
"""Range-first compact-frequency memory; same compiled shared-word C5 schedule.

This hypothesis stores a narrow carrier predictor, while innovation reconstructs
wide video excursions. The frequency grid is not instantaneous DAC resolution,
RF tuning resolution or proof of a better PLL. Independent video gates decide.
Uses the search_range CLI, with an independent seed/output directory.
"""
import numpy as np
import search_range as R

original_propose=R.propose
original_mutate=R.mutate


def compact(p,rng):
    p=dict(p);p['counter_phase']=False
    states=1<<(10-p['token_bits'])
    p['phases']=min(p['phases'],states//2)
    frequencies=states//p['phases']
    target=float(rng.choice([25000,50000,100000,250000,500000]))
    span=min((frequencies-1)*target,2*min(p['centre_hz']+9.9e6,9.9e6-p['centre_hz']))
    p.update(low_hz=p['centre_hz']-span/2,high_hz=p['centre_hz']+span/2,
             compact_frequency_memory=True,frequency_step_target_hz=target)
    return p


def propose(rng):return compact(original_propose(rng),rng)
def mutate(p,rng):return compact(original_mutate(p,rng),rng)


if __name__=='__main__':
    # Pure proposal-policy hooks; execution/cost/data/guards are unchanged.
    R.propose=propose;R.mutate=mutate;R.main()

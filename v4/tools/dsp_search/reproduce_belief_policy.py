#!/usr/bin/env python3
"""Run or print the predeclared closed-loop belief ROM experiment matrix."""
import argparse,subprocess,sys,shlex
from pathlib import Path

def commands(parent):
    data=Path('v4/docs/data')
    for name,seed,flags,context in [
        ('belief_policy_rom',3310190,[],False),
        ('belief_context_policy_rom',3310190,[],True),
        ('belief_sync_policy_rom',3610190,['--sync-risk-weight','8'],True),
        ('belief_particle_policy_rom',3910190,['--teacher','particle','--sync-risk-weight','8'],True),
        ('belief_particle_split_rom',4110190,['--teacher','particle','--sync-risk-weight','8','--reseed-unused'],False),
        ('belief_adaptive_policy_rom',4310190,['--teacher','particle','--sync-risk-weight','8','--reseed-unused','--projection-geometry','adaptive'],False),
        ('belief_endpoint_policy_rom',4510190,['--teacher','particle','--sync-risk-weight','8','--reseed-unused','--projection-geometry','adaptive','--teacher-endpoint','A'],False),
        ('belief_history_policy_rom',4710190,['--teacher','particle','--sync-risk-weight','8','--reseed-unused','--projection-geometry','adaptive','--teacher-endpoint','A','--history-observation'],False),
        ('belief_quality_policy_rom',4910190,['--teacher','particle','--sync-risk-weight','8','--reseed-unused','--projection-geometry','adaptive','--teacher-endpoint','A','--observation-quality','--frequency-memory-weight','8'],False),
        ('belief_quality_split_rom',5110190,['--teacher','particle','--sync-risk-weight','8','--reseed-unused','--projection-geometry','adaptive','--teacher-endpoint','A','--observation-quality','--frequency-memory-weight','8','--joint-quality-reseed'],False),
    ]:
        initial=[data/'belief_rom/b3_4411.json',data/'belief_rom/b5_4411.json']
        if context:
            initial=[data/'belief_context_rom/b3.json',data/'belief_context_rom/b4.json']
            if name in ('belief_sync_policy_rom','belief_particle_policy_rom'):
                initial[1]=data/'belief_rom/b5_4411.json'
        yield [sys.executable,'v4/tools/dsp_search/refine_belief_rom.py','--output',str(parent/name),'--initial',*map(str,initial),'--train-seed',str(seed),'--confirmation-seed',str(seed+100000),*flags]

def main():
    p=argparse.ArgumentParser();p.add_argument('--output-parent',type=Path,default=Path('/tmp/c5-belief-reproduction'));p.add_argument('--print-only',action='store_true');a=p.parse_args()
    for cmd in commands(a.output_parent):
        print(shlex.join(cmd),flush=True)
        if not a.print_only:subprocess.run(cmd,check=True)

if __name__=='__main__':main()

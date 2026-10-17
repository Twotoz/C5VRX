"""C5VRX by Twotoz/contributors: offline universal first-EQ candidate.

Construction may settle for12 samples; actual output width/edge, all initial
decoder states, active preservation and recovery gates remain unchanged.
No production edits and no hardware timing claim. A usable sequence would be
prepared before streaming, never searched in the pulse deadline.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def variant(source, prefix_bytes=None):
    def replace(old, new):
        nonlocal source
        assert source.count(old) == 1, old
        source = source.replace(old, new)
    if prefix_bytes is not None:
        replace('last[parity] - 126u', 'last[parity] - '+str(prefix_bytes-2)+'u')
        return source
    replace('static SFW_HOT bool decoder_prefix(',
            'static SFW_HOT __attribute__((unused)) bool decoder_prefix(')
    replace('bool prefix_ok = decoder_prefix(f, r, a, states, counts, last);', '''bool prefix_ok = true;
    (void)last;
    for (unsigned p=0;p<2;++p) {
        counts[p]=32;
        for(unsigned j=0;j<32;++j)states[p][j]=(decoder_bound_t)((j<<6)|63u);
    }''')
    replace('unsigned previous_phase = ph_at(r, a - 1u);', 'unsigned previous_phase = 0;')
    replace('if (worst_best > 8u)', 'if (k >= 12u && worst_best > 8u)')
    return source


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    parser.add_argument('--kp', nargs='+', type=int, default=[0, 7])
    parser.add_argument('--transition', action='store_true')
    parser.add_argument('--h-bank', action='store_true')
    parser.add_argument('--prefix-bytes', type=int, choices=range(2,129,2))
    args = parser.parse_args()
    source = (ROOT/'main/sync_flywheel.c').read_text()
    changed = variant(source, args.prefix_bytes)
    if args.h_bank:
        from sync_h_bank import variant as h_bank_variant
        changed = h_bank_variant(changed, h50_step=-7430)
    report = {'original_sha256': hashlib.sha256(source.encode()).hexdigest(),
        'temporary_sha256': hashlib.sha256(changed.encode()).hexdigest(),
        'settle_samples': 0 if args.prefix_bytes else 12,
        'prefix_bytes': args.prefix_bytes, 'h_bank': args.h_bank,
        'hardware_timing_claim': False, 'rows': []}
    with tempfile.TemporaryDirectory() as directory:
        folder = Path(directory)
        core = folder/'core.c'
        core.write_text(changed)
        binary = folder/'scenes'
        subprocess.run(['cc', '-O2', '-std=gnu11', '-Wall', '-Wextra', '-Werror',
            '-I'+str(ROOT/'main'), str(ROOT/'test/test_sync_flywheel.c'), str(core),
            '-lm', '-o', str(binary)], check=True)
        for kp in args.kp:
            run = subprocess.run(['stdbuf', '-oL', str(binary)],
                env=dict(os.environ, SFW_KP_LEVEL=str(kp), SFW_COMMIT='1'), text=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            report['rows'].append({'kp': kp, 'returncode': run.returncode, 'stdout': run.stdout})
            print('kp', kp, 'return', run.returncode, run.stdout[-1000:], flush=True)
        if args.transition:
            audit = (ROOT/'research/sync_output_transition.c').read_text().replace(
                '#include "../main/sync_flywheel.c"', '#include "'+str(core)+'"')
            runner = folder/'transition.c'
            runner.write_text(audit)
            subprocess.run(['cc', '-O2', '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                '-I'+str(ROOT/'main'), str(runner), '-o', str(folder/'transition')], check=True)
            run = subprocess.run([str(folder/'transition')], text=True, stdout=subprocess.PIPE, check=True)
            Path(args.output).with_suffix('.csv').write_text(run.stdout)
    Path(args.output).write_text(json.dumps(report, indent=2)+'\n')
    return int(any(row['returncode'] for row in report['rows']))


if __name__ == '__main__':
    raise SystemExit(main())

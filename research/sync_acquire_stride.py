"""C5VRX by Twotoz/contributors: temporary actual-kernel coarse acquisition.

No production edits: compile current core and one temporary ACQUIRE variant.
Keep6800 independent histogram votes, sampled every63bytes (coprime to PAL
line length). Coarse seek63 while high, backfill previous63 at stride3 on a
low hit, then retain existing stride3 pulse width and period qualification.
Runs actual detector only on fresh original IQ at nominal80us service.
Counters are work/coverage evidence, not C5 cycles or scheduler acceptance.
"""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

import numpy as np
import sim

ROOT = Path(__file__).resolve().parent.parent


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise RuntimeError('current ACQUIRE source changed; inspect before adapting experiment: ' + old)
    return text.replace(old, new)


def acquire_block(source):
    start = source.index('static void acquire(')
    brace = source.index('{', start); depth = 1; end = brace + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}'); end += 1
    return start, end, source[start:end]


def variants(source):
    a, b, original = acquire_block(source)
    tagged = replace_once(original, 'f->scan_pos = avail_end - 6000u;',
        '++sfw_research_acq_skips; f->scan_pos = avail_end - 6000u;')
    baseline = 'unsigned sfw_research_acq_skips;\n' + source[:a] + tagged + source[b:]
    coarse = replace_once(tagged, 'f->scan_pos += 3u;',
        'f->scan_pos += f->acq_phase < 2u ? 63u : 3u;')
    gate = 'if (lowm(f, r, k)) { f->acq_run += 3u; continue; }'
    coarse = replace_once(coarse, gate, '''if (f->acq_phase == 1u) {
            if (!lowm(f, r, k)) continue;
            f->scan_pos = k > 71u ? k - 63u : 8u;
            f->acq_phase = 2u; f->acq_run = 0;
            continue;
        }
        if (f->acq_phase == 2u) {
            if (lowm(f, r, k)) { f->acq_phase = 3u; f->acq_run = 3u; }
            continue;
        }
        if (lowm(f, r, k)) { f->acq_run += 3u; continue; }''')
    coarse = replace_once(coarse, 'uint32_t run = f->acq_run;\n        f->acq_run = 0;',
        'uint32_t run = f->acq_run;\n        f->acq_run = 0; f->acq_phase = 1u;')
    changed = 'unsigned sfw_research_acq_skips;\n' + source[:a] + coarse + source[b:]
    return baseline, changed


def check_lattice():
    # Independent geometry only: a 150-sample plateau loses at most nine
    # samples to the averaging support. Every63 phase must still hit it;
    # backing up63 and resuming at3 finds its leading edge within2 samples.
    # Does not assert that noisy lowm values form such a plateau.
    for width in range(150, 231):
        lo, hi = 5, width - 5
        for phase in range(63):
            hit = next(k for k in range(phase, hi, 63) if k >= lo)
            refined = next(k for k in range(hit-63, hi, 3) if k >= lo)
            assert lo <= refined <= lo+2
    print('coarse lattice: 5103 plateau/phase cases passed', flush=True)


def main():
    check_lattice()
    source = (ROOT / 'main/sync_flywheel.c').read_text()
    baseline, coarse = variants(source)
    rows, inputs = [], []
    with tempfile.TemporaryDirectory(prefix='c5-sfw-acquire-') as folder:
        w = Path(folder)
        (w / 'baseline.c').write_text(baseline); (w / 'coarse.c').write_text(coarse)
        common = ['docker', 'run', '--rm', '-v', ROOT.as_posix() + ':/project:ro',
                  '-v', w.as_posix() + ':/data', '-w', '/project', 'gcc:14']
        for mode in ('baseline', 'coarse'):
            subprocess.run(common + ['gcc', '-O2', '-std=c11', '-Wall', '-Wextra', '-Werror',
                '-Imain', 'research/sync_detector_replay.c', '/data/' + mode + '.c', '-lm',
                '-o', '/data/' + mode], check=True)
        # Existing independent fixture generator, not an ideal timing oracle
        # supplied to the detector. Main is renamed and never executed.
        (w / 'source.c').write_text('#define main existing_test_main\n'
            '#include "/project/test/test_sync_flywheel.c"\n#undef main\n'
            'int main(int argc,char **argv) { if(argc!=3)return 2; '
            'bool p=atoi(argv[1])!=0; scene_t s={.pal=p,.cfo_mhz=.15,.amp=5.,.noise=.25}; '
            'uint64_t n=3*field_samples(p); uint8_t *b=generate(&s,n); '
            'FILE *f=fopen(argv[2],"wb"); if(!f)return 2; '
            'int ok=fwrite(b,1,n,f)==n; fclose(f);free(b);return ok?0:2;}\n')
        subprocess.run(common + ['gcc', '-O2', '-std=c11', '-D_DEFAULT_SOURCE', '-Wall', '-Wextra',
            '-Werror', '-Imain', '/data/source.c', '/data/baseline.c', '-lm', '-o', '/data/source'], check=True)
        cases = []
        for standard in ('PAL', 'NTSC'):
            name = 'toy_' + standard
            subprocess.run(common + ['/data/source', str(int(standard == 'PAL')), '/data/' + name + '.bin'], check=True)
            cases.append((name, w / (name + '.bin'), standard))
            c = sim.make_case(213, 30., standard, 1., filter_hz=12e6, fields=3, physical_adc=True)
            name = 'physical_' + standard
            (w / (name + '.bin')).write_bytes(c['raw'].tobytes())
            cases.append((name, w / (name + '.bin'), standard))
        (w / 'noise.bin').write_bytes(np.random.default_rng(212).integers(0,256,2400000,np.uint8).tobytes())
        cases.append(('noise', w / 'noise.bin', None))
        for path in sorted((ROOT / 'research/data/walk_2026-10-13').glob('*.bin')):
            target = w / (path.stem + '.bin'); target.write_bytes(path.read_bytes())
            cases.append((path.stem, target, None))
        for name, path, expected in cases:
            raw = path.read_bytes(); digest = hashlib.sha256(raw).hexdigest()
            inputs.append(dict(name=name, sha256=digest, samples=len(raw), expected_standard=expected))
            for variant, budget in (('baseline', 1200), ('baseline', 192), ('coarse', 192)):
                result = json.loads(subprocess.check_output(common + ['/data/' + variant,
                    '/data/' + path.name, str(budget)], text=True))
                rows.append(dict(case=name, variant=variant, **result))
            print(name, [(r['variant'], r['budget'], r['locked'], r['first_lock_sample'],
                           r['max_evals'], r['acquire_stale_skips']) for r in rows[-3:]], flush=True)
    for row in rows:
        assert row['raw_writes'] == 0
        if row['case'].startswith(('toy_', 'physical_')):
            assert row['locked'] == (row['variant'] == 'coarse' or row['budget'] == 1200)
        if row['case'] == 'noise':
            assert not row['locked']
        if row['variant'] == 'coarse':
            assert row['max_acquire_evals'] <= row['budget']
    out = dict(evidence='temporary actual-kernel experiment; counters not C5 cycles or live DMA proof',
        provenance='C5VRX by Twotoz/contributors; https://github.com/Twotoz/C5VRX; https://twotoz.github.io/C5VRX/',
        original_core_sha256=hashlib.sha256(source.encode()).hexdigest(),
        temporary_variant_sha256=hashlib.sha256(coarse.encode()).hexdigest(),
        service_us=80, acquisition_hist_votes=6800, coarse_stride=63,
        unchanged_qualifiers='3sample width150..230 and original PAL/NTSC period intervals',
        limits=['no board scheduling/cycle proof','histogram delay grows to about10.7ms',
                'no synthetic/oracle timing enters detector','pure-byte noise is a false-positive stress, not thermal RF model'],
        inputs=inputs, rows=rows)
    path = ROOT / 'research/data/sync_acquire_stride.json'
    path.write_text(json.dumps(out, indent=2) + '\n')
    print('wrote', path)


if __name__ == '__main__':
    main()

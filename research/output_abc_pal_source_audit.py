"""C5VRX by Twotoz/contributors: independent PAL source-boundary audit.

Preserves the old malformed fixture. A separate source supplies the missing
pre-EQ half-line BEFORE FM/filter/quantization. Compares fixed sim.py against
its explicit historical source; no production firmware or hardware access.
ITU-R BT.470-5 Figure2-1/Table1-2: 64us lines,20ms fields, 2.5H pre/broad/post,
half-line pulse spacing, alternating line/half-line field anchors.
Run python research/output_abc_pal_source_audit.py. No hardware access.
"""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

import numpy as np
from scipy import ndimage
import sim
from u85q_lp import lp_codes

ROOT = Path(__file__).resolve().parent.parent
ORIGINAL_RASTER = sim.raster_legacy
FIXED_RASTER = sim.raster


def expected_half(h):
    # Origin is first broad pulse in sim.py; the existing C test generator
    # uses first pre-EQ as origin. Shift by5half-lines to compare its v_kind.
    k = (h + 5) % 625
    if k < 5 or 10 <= k < 15:
        return 1
    return 2 if k < 10 else 0


def audited_raster(standard, seed, fields=2):
    return FIXED_RASTER(standard, seed, fields)


def main():
    old_ire, old_region = ORIGINAL_RASTER('PAL', 220, 3)
    valid_ire, valid_region = audited_raster('PAL', 220, 3)
    conflicts = []
    for h in range(1875):
        if not expected_half(h):
            continue
        a = h * 1280; b = a + 1280
        if not np.array_equal(old_ire[a:b], valid_ire[a:b]) or not np.array_equal(old_region[a:b], valid_region[a:b]):
            conflicts.append(dict(half_line=h, begin=a, end=b, kind=expected_half(h),
                waveform_changed_samples=int(np.count_nonzero(old_ire[a:b] != valid_ire[a:b])),
                old_active_labels=int(np.count_nonzero(old_region[a:b] == 1))))
    assert [r['half_line'] for r in conflicts] == [1245]
    # Generated waveform and labels change before FM. The original case and
    # its SHA remain in output_abc.json/output_abc_pal_v_failure.json.
    c = sim.make_case(213, 30., 'PAL', 1., filter_hz=12e6, fields=3, physical_adc=True)
    raw = c['raw'].copy(); lo, hi = len(raw) * 19 // 30, len(raw) * 22 // 30
    raw[lo:hi] = np.random.default_rng(212).integers(0, 256, hi - lo, dtype=np.uint8)
    rows = []
    with tempfile.TemporaryDirectory(prefix='c5-pal-source-audit-') as folder:
        w = Path(folder); (w / 'source.bin').write_bytes(raw.tobytes())
        # Call the existing C fixture's actual v_kind(), rather than checking
        # the Python boundary formula against another copy of itself.
        (w / 'geometry.c').write_text('#define main existing_test_main\n'
            '#include "/project/test/test_sync_flywheel.c"\n#undef main\n'
            'int main(void) { for (unsigned h=0; h<1875; ++h) '
            'printf("%d", v_kind(true, h+5)); return 0; }\n')
        subprocess.run(['docker', 'run', '--rm', '-v', ROOT.as_posix() + ':/project:ro',
            '-v', w.as_posix() + ':/data', '-w', '/project', 'gcc:14', 'gcc', '-O2', '-std=c11',
            '-D_DEFAULT_SOURCE', '-Wall', '-Wextra', '-Werror', '-Imain', '/data/geometry.c',
            'main/sync_flywheel.c', '-lm', '-o', '/data/geometry'], check=True)
        kinds = subprocess.check_output(['docker', 'run', '--rm', '-v', w.as_posix() + ':/data',
            'gcc:14', '/data/geometry'], text=True)
        assert len(kinds) == 1875 and all(int(kind) == expected_half(h) for h, kind in enumerate(kinds))
        subprocess.run(['docker', 'run', '--rm', '-v', ROOT.as_posix() + ':/project:ro',
            '-v', w.as_posix() + ':/data', '-w', '/project', 'gcc:14', 'gcc', '-O2', '-std=c11',
            '-Wall', '-Wextra', '-Werror', '-Imain', 'research/output_abc_replay.c',
            'main/sync_flywheel.c', '-o', '/data/replay'], check=True)
        command = ('for kp in 0 1 2 3 4 5 6 7; do '
            '/data/replay /data/source.bin /data/out-$kp.bin "$kp" > /data/meta-$kp.json || exit; done')
        subprocess.run(['docker', 'run', '--rm', '-v', w.as_posix() + ':/data',
                        'gcc:14', 'bash', '-c', command], check=True)
        for kp in range(8):
            out = np.frombuffer((w / f'out-{kp}.bin').read_bytes(), np.uint8)
            lut = np.frombuffer((w / f'out-{kp}.bin.lut').read_bytes(), '<u2').reshape(2, 1024)[0]
            iq_changes = int(np.count_nonzero((raw != out) & (c['region'] == 1)))
            for parity in (0, 1):
                before, after = lp_codes(raw[parity:], lut), lp_codes(out[parity:], lut)
                active = ndimage.binary_erosion(c['region'][parity:] == 1, iterations=40)
                changed = int(np.count_nonzero((before != after) & active))
                rows.append(dict(kp_x100=85 - 5 * kp, parity=parity,
                    active_iq_overwrites=iq_changes, stable_active_dac_changes=changed,
                    pass_active_preservation=iq_changes == 0 and changed == 0,
                    keeper=json.loads((w / f'meta-{kp}.json').read_text())))
    result = dict(evidence='separate corrected PAL source; original malformed-source counterexample retained',
        provenance='C5VRX by Twotoz and contributors; https://github.com/Twotoz/C5VRX; https://twotoz.github.io/C5VRX/',
        standard_source='https://www.itu.int/dms_pubrec/itu-r/rec/bt/R-REC-BT.470-5-199802-S!!PDF-E.pdf',
        source_geometry=dict(line_samples=2560, field_samples=800000, field_half_lines=625,
            first_pre_EQ_samples=[793600, 1593600], broad_samples=[800000, 1600000],
            pre_eq_half_lines=5, broad_half_lines=5, post_eq_half_lines=5,
            existing_C_test_origin_difference_half_lines=5,
            actual_C_test_v_kind_checked_half_lines=1875),
        old_source_conflicts=conflicts,
        waveform_delta_samples=int(np.count_nonzero(old_ire != valid_ire)),
        region_delta_samples=int(np.count_nonzero(old_region != valid_region)),
        corrected_iq_sha256=hashlib.sha256(raw.tobytes()).hexdigest(),
        source_config=dict(seed=213, cnr_db=30., deviation=1., filter_hz=12e6, fields=3,
            physical_adc=True, replacement_rng_seed=212, replacement_begin=lo, replacement_end=hi),
        rows=rows,
        conclusion='The old fixture skips a mandated odd-field pre-EQ half-line, a source-generator defect. Keep it as a malformed-source case: the keeper still cannot promise to preserve arbitrary active content occupying predicted VBI.')
    path = ROOT / 'research/data/output_abc_pal_source_audit.json'
    path.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'rows'}, indent=2))
    print('corrected preservation passes', sum(r['pass_active_preservation'] for r in rows), '/', len(rows))


if __name__ == '__main__':
    main()

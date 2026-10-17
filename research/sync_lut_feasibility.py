"""C5VRX by Twotoz and contributors: exact root LUT sync feasibility.

Uses immutable main/kp_tables.h and root U85Q-LP arithmetic. Synthetic,
not board throughput, terminated CVBS amplitude or LCD acceptance.
No live LUT changes. Writes research/data/sync_lut_feasibility.json.
"""
import json
import math
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KPS = (.85, .80, .75, .70, .65, .60, .55, .50)


def tables():
    words = [int(v, 16) for v in re.findall(r"0x([0-9a-f]{4})", (ROOT / 'main/kp_tables.h').read_text())]
    assert len(words) == 8192
    return [words[k:k + 1024] for k in range(0, 8192, 1024)]


def raw_phase(raw):
    i, q = raw >> 4, raw & 15
    i = (i if i < 8 else i - 16) + .5
    q = (q if q < 8 else q - 16) + .5
    return math.atan2(q, i), i * i + q * q


def strong_cells():
    # Same radius and phase-error priorities as main/sync_flywheel.c.
    text = (ROOT / 'main/phase8_gain_lut.h').read_text().split('{', 1)[1].split('}', 1)[0]
    phases = [int(v) for v in re.findall(r'\d+', text)]
    assert len(phases) == 256
    cells = []
    for target in range(256):
        best = None
        for raw in range(256):
            ang, r2 = raw_phase(raw)
            if not 16 <= r2 <= 42.25:
                continue
            phase = phases[raw]
            error = ((phase - target + 128) & 255) - 128
            score = abs(error) * 1024 + abs(4 * r2 - 100)
            if best is None or score < best[0]:
                best = score, raw
        cells.append(best[1])
    return cells


def address(a, b):
    return (a >> 4) | ((a & 15) << 4) | ((b & 128) << 1) | ((b & 8) << 6)


def decode(raw, lut, state=0, parity=0):
    x = counter = 0
    out = []
    states = []
    for k in range(parity, len(raw) - 1, 2):
        y = (counter >> 10) & 63
        out.append(y)
        adr = address(raw[k], raw[k + 1])
        counter = ((y >> 1) << 10) | adr
        token = lut[adr] >> 11
        hi = ((counter >> 8) + (((x >> 1) << 2) | (state >> 3))) & 255
        counter = (hi << 8) | (counter & 255)
        val = lut[(state << 5) + token]
        x, state = val & 63, (val >> 6) & 31
        states.append(state)
    return out, states


def carrier(cells, mhz, initial=0, count=4096):
    return [cells[int(math.floor(initial + k * mhz / 40 * 256 + .5)) & 255] for k in range(count)]


def scaled(lut, level):
    # main/output_level.h: preserve token/phase bits and C negative division.
    pivot = (21, 13, 16, 19)[level]
    out = []
    for value in lut:
        twice = 2 * pivot + (level + 2) * ((value & 63) - 21)
        code = (twice + 1) // 2 if twice >= 0 else -((-twice) // 2)
        out.append((value & 0xffc0) | min(63, max(0, code)))
    return out


def convergence(lut, raw):
    possible = set(range(32))
    for pair, k in enumerate(range(0, len(raw) - 1, 2), 1):
        tok = lut[address(raw[k], raw[k + 1])] >> 11
        possible = {(lut[(s << 5) + tok] >> 6) & 31 for s in possible}
        if len(possible) == 1:
            return pair
    return None


def main():
    cells = strong_cells()
    result = {'model': 'exact root LUT and U85Q-LP, synthetic constant carriers', 'levels': []}
    for kp, lut in zip(KPS, tables()):
        reachable = [lut[s * 32 + t] & 63 for s in range(32) for t in range(32)]
        sweep = []
        for mhz in (-6., -5.5, -5., -4.5, -4., -3.5, -1.):
            outputs = []
            for phase in (0, 37, 109):
                for parity in (0, 1):
                    for state in (0, 7, 21):
                        decoded, _ = decode(carrier(cells, mhz, phase), lut, state, parity)
                        outputs.extend(decoded[256:])
            sweep.append({'mhz': mhz, 'min': min(outputs), 'max': max(outputs),
                          'mean': sum(outputs) / len(outputs),
                          'above_sync_threshold_8_fraction': sum(v > 8 for v in outputs) / len(outputs)})
        remap = ((28, 3), (29, 10), (30, 17), (31, 24))
        origin_worker_mismatch = sum((lut[s * 32 + q] & 2047) != (lut[s * 32 + t] & 2047)
                                    for s in range(32) for q, t in remap)
        convergence_samples = {str(mhz): convergence(lut, carrier(cells, mhz)) for mhz in (-6., -4.5, -4., -1., 0., 4.)}
        output_levels = []
        for level in range(4):
            mapped = scaled(lut, level)
            sync = decode(carrier(cells, -4.5), mapped)[0][256:]
            porch = decode(carrier(cells, -1.), mapped)[0][256:]
            output_levels.append({'level': level, 'sync_min': min(sync), 'sync_max': max(sync),
                                  'porch_min': min(porch), 'porch_max': max(porch),
                                  'note': 'digital codes only; no terminated CVBS amplitude inference'})
        row = {'kp': kp, 'minimum_reachable_discriminator_code': min(reachable),
               'frequency_sweep': sweep, 'origin_to_regular_worker_mismatches_of_128': origin_worker_mismatch,
               'all_32_initial_states_convergence_pairs': convergence_samples,
               'minus_4p5MHz_synth_output_levels': output_levels}
        result['levels'].append(row)
        print(f"kp={kp:.2f} minimum code={min(reachable)} origin-remap mismatches={origin_worker_mismatch}")
        for r in sweep:
            print(f"  {r['mhz']:4.1f} MHz: LP {r['min']:2d}..{r['max']:2d}, mean={r['mean']:.2f}, >8={r['above_sync_threshold_8_fraction']:.3f}")
        print('  32-state convergence in pairs:', convergence_samples)
        safe = next(v for v in sweep if v['mhz'] == -4.5)
        assert safe['above_sync_threshold_8_fraction'] == 0
    assert result['levels'][-1]['minimum_reachable_discriminator_code'] == 4
    assert result['levels'][-1]['origin_to_regular_worker_mismatches_of_128'] == 0
    assert result['levels'][5]['frequency_sweep'][0]['above_sync_threshold_8_fraction'] > .5
    output = ROOT / 'research/data/sync_lut_feasibility.json'
    output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()

"""C5VRX by Twotoz/contributors: OFFLINE sync-flag codec feasibility.

Explore a hardware-timed flag replacing I bit0, retaining the existing two
BitScrambler bundles. Sixteen phase tokens plus one flag fit the existing
five-bit token; worker phase still advances during forced sync. No firmware,
GPIO or hardware timing claim. Oracle sync flags qualify only waveform/data
path behavior, never acquisition, holdover or real monitor lock.
"""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

import sim
import u85q_lp


def flag_lut(reference):
    """Merge32 original tokens into16 medoid groups over ALL32 states."""
    worker = (reference.reshape(32, 32) & 2047).astype(np.int64)
    phase, dac = (worker >> 6) & 31, worker & 63
    # Reserve I_A bit0 (PAIR4411 address bit0) for the candidate flag.
    counts = np.bincount((reference[np.arange(1024) & ~1] >> 11), minlength=32)
    weights = counts.astype(float) + 1.0
    groups = [[i] for i in range(32)]

    def best(group):
        g = np.array(group)
        dp = (phase[:, g, None] - phase[:, None, g] + 16) % 32 - 16
        dd = dac[:, g, None] - dac[:, None, g]
        costs = ((dd * dd + 4 * dp * dp) * weights[g][None, :, None]).sum(axis=(0, 1))
        index = int(np.argmin(costs))
        return float(costs[index]), int(g[index])

    while len(groups) > 16:
        prior = [best(g)[0] for g in groups]
        choice = min((best(groups[a] + groups[b])[0] - prior[a] - prior[b], a, b)
                     for a in range(len(groups)) for b in range(a + 1, len(groups)))
        _, a, b = choice
        groups[a] += groups.pop(b)
    mapping = np.zeros(32, dtype=np.uint16)
    prototypes = []
    for i, g in enumerate(groups):
        mapping[g] = i
        prototypes.append(best(g)[1])
    result = np.zeros(1024, dtype=np.uint16)
    for address in range(1024):
        old_token = int(reference[address & ~1]) >> 11
        result[address] = int(mapping[old_token] | ((address & 1) << 4)) << 11
    for state in range(32):
        for group, old_token in enumerate(prototypes):
            payload = int(worker[state, old_token])
            result[state * 32 + group] |= payload
            # Flag controls DAC only; tracker sees the same measured token.
            result[state * 32 + group + 16] |= payload & ~63
    for state in range(32):
        for group in range(16):
            a, b = int(result[state * 32 + group]), int(result[state * 32 + group + 16])
            assert (a & 1984) == (b & 1984) and (b & 63) == 0
    return result, groups, prototypes


def raw_flag(raw, flags):
    return (raw & np.uint8(239)) | (np.asarray(flags, dtype=np.uint8) << 4)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--quick', action='store_true')
    args = parser.parse_args()
    reference = sim.u85q_lut()
    candidate, groups, prototypes = flag_lut(reference)
    report = dict(provenance='C5VRX by Twotoz/contributors',
                  firmware_enabled=False, hardware_timing_proven=False,
                  oracle_flags_are_not_source_evidence=True,
                  reference_sha256=hashlib.sha256(reference.tobytes()).hexdigest(),
                  candidate_sha256=hashlib.sha256(candidate.tobytes()).hexdigest(),
                  groups=groups, prototypes=prototypes, rows=[])
    for standard in ('PAL', 'NTSC'):
        for cnr in ((4,) if args.quick else (0, 4, 12)):
            case = sim.make_case(213, cnr, standard=standard, fields=1, filter_hz=12e6)
            oracle = case['region'] == 2
            for name in ('baseline', 'masked_i0', 'flag_off', 'oracle_flag'):
                if name in ('baseline', 'masked_i0'):
                    noisy_raw, clean_raw = case['raw'], case['clean']
                    if name == 'masked_i0':
                        # Isolate the input-bit loss from the token compression.
                        noisy_raw = raw_flag(noisy_raw, np.zeros(len(oracle), bool))
                        clean_raw = raw_flag(clean_raw, np.zeros(len(oracle), bool))
                    noisy = u85q_lp.lp_codes(noisy_raw, reference)
                    clean = u85q_lp.lp_codes(clean_raw, reference)
                else:
                    flags = oracle if name == 'oracle_flag' else np.zeros(len(oracle), bool)
                    noisy = u85q_lp.lp_codes(raw_flag(case['raw'], flags), candidate)
                    clean = u85q_lp.lp_codes(raw_flag(case['clean'], flags), candidate)
                metrics = sim.measure(case, codes=noisy, clean_codes=clean)
                row = dict(standard=standard, cnr_db=cnr, variant=name,
                           picture=metrics['picture'], sinad_db=metrics['sinad'])
                report['rows'].append(row)
                print(row, flush=True)
    out = Path(__file__).parent / 'data' / ('sync_flag_codec_quick.json' if args.quick else 'sync_flag_codec.json')
    out.write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    main()

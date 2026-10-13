"""C5VRX by Twotoz/contributors: oracle DMA-IQ sync mux feasibility.

Extends sim.py, U85Q-LP and archived sync flywheel. NOT firmware or an LCD
lock proof. True source field timing is supplied; compare +/- phase error.
All active-picture IQ remains real; tracker transition effects are measured.
"""
import json
import math
from pathlib import Path
import numpy as np
import sim
from kp_morph import build_lut
from u85q_lp import lp_codes

HERE = Path(__file__).resolve().parent

def study():
    lut = build_lut(.85)
    rows = []
    for standard in ('PAL', 'NTSC'):
        for cn in (0, 4):
            c = sim.make_case(213, cn, standard, 1., filter_hz=12e6, fields=1, physical_adc=True)
            ire, region = sim.raster(standard, 220, fields=1)
            freq = np.full(len(ire), -1e6)
            freq[region == 2] = -5.3e6
            freq[region == 3] += ire[region == 3] * sim.DEV_PP / 140
            phase = 2 * np.pi * np.cumsum(freq) / sim.FS
            i = np.floor(4.2 * np.cos(phase)).astype(int)
            q = np.floor(4.2 * np.sin(phase)).astype(int)
            synthetic = (((i & 15) << 4) | (q & 15)).astype(np.uint8)
            active = region == 1
            interior = active.copy()
            for edge in np.flatnonzero(active[1:] & ~active[:-1]) + 1:
                interior[edge:edge + 80] = False # exclude first 2 us of each active line
            baseline = lp_codes(c['raw'], lut)
            for error_us in (0., .5, 2.):
                offset = int(error_us * 40)
                mask = np.roll(~active, offset)
                replacement = np.roll(synthetic, offset)
                mixed = c['raw'].copy()
                mixed[mask] = replacement[mask]
                out = lp_codes(mixed, lut)
                raw_changed = mixed != c['raw']
                row = dict(standard=standard, cn_db=cn, phase_error_us=error_us,
                           oracle_source_timing=True, hardware_streaming=False,
                           active_iq_changed_pct=float(100 * np.mean(raw_changed[active])),
                           interior_dac_changed_pct=float(100 * np.mean(out[interior] != baseline[interior])),
                           interior_dac_mean_abs_change=float(np.mean(abs(out[interior].astype(float) - baseline[interior]))))
                if offset == 0: assert not raw_changed[active].any()
                rows.append(row)
                print(row, flush=True)
    # Immutable full-frame descriptor schedule cannot stay phase-aligned to
    # the 32 KiB RX ring in a single frame. At least one node per full line.
    plans = []
    for standard, frame_bytes, lines in (('PAL', 1600000, 625), ('NTSC', 1334668, 525)):
        frames = 32768 // math.gcd(32768, frame_bytes)
        plans.append(dict(standard=standard, repeat_frames=frames,
                          descriptor_lower_bound_bytes=frames * lines * 12,
                          three_nodes_per_line_estimate_bytes=frames * lines * 36))
    result = dict(baseline='574d155', assumptions='one seed, physical ADC, oracle source timing, no CPU/DMA/LCD proof',
                  rows=rows, static_rx_ring_schedule=plans)
    path = HERE / 'data/sync_iq_mux.json'
    path.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(path)

if __name__ == '__main__': study()

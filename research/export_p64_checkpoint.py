"""Exports the latest P64 checkpoint into p64_champion.json and main/p64_tables.h
(C5VRX by Twotoz and contributors)
"""
import json
from pathlib import Path
from p64_optimizer import build_lut64_from_params

HERE = Path(__file__).resolve().parent
CKPT = HERE / 'data' / 'p64_checkpoint.json'
CHAMP = HERE / 'data' / 'p64_champion.json'
HEADER = HERE.parent / 'main' / 'p64_tables.h'

def export():
    if not CKPT.exists():
        print(f"No checkpoint found at {CKPT}")
        return
    data = json.loads(CKPT.read_text())
    candidate = data['best_candidate']
    metrics = data['best_metrics']
    n_vid = metrics['n_vid']
    n_sync = metrics['n_sync']
    print(f"Exporting Checkpoint Gen {data['generation']}: -3dB Lock: {metrics['lock_m3']:.3f} | +4dB SINAD: {metrics['sinad_4']:+.2f} dB | Step: {metrics['demod_step_deg']:.2f}° ({n_vid} vid / {n_sync} sync)")

    CHAMP.write_text(json.dumps(data, indent=2))
    print(f"[+] Wrote {CHAMP}")

    lut = build_lut64_from_params(candidate)
    body = ',\n'.join('    ' + ', '.join(f'0x{v:04x}' for v in lut[i:i + 12]) for i in range(0, 1024, 12))
    header_text = f"""/* C5VRX by Twotoz and contributors: P64 Deep-Phase Demodulator Table.
 * 64 States (4-bit tokens, 6-bit states, 6-bit DAC codes).
 * Features:
 *   - Phase step: {360.0 / n_vid:.2f} degrees
 *   - Active video states: {n_vid}
 *   - Sync corridor states: {n_sync}
 *   - Simulated -3 dB C/N linelock: {metrics['lock_m3']:0.3f}
 *   - Simulated +4 dB C/N SINAD: {metrics['sinad_4']:+5.2f} dB
 *   - Unique DAC levels: {metrics['unique_dac_levels']}
 */
#pragma once
#include <stdint.h>

#define P64_TABLE_ENTRIES 1024
static const uint16_t p64_table[1024] = {{
{body}
}};
"""
    HEADER.write_text(header_text)
    print(f"[+] Wrote {HEADER}")

if __name__ == '__main__':
    export()

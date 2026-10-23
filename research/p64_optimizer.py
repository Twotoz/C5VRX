"""P64 64-State ("Deep Phase") Hyper-Optimizer
(C5VRX by Twotoz and the C5VRX contributors)

Explores the 64-state space with 4-bit tokens:
- Searches N_sync in [10, 22] (N_vid in [42, 54] -> phase step 6.67 to 8.57 degrees)
- 16 tokens: 12 phase sectors (30 deg) + 4 noise-suppression tokens
- Hard hardware boundary clamps
- Dual decoupled loop gains in a single fixed 1024-word table
"""
import argparse
import json
import math
import multiprocessing as mp
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import kp_morph
import p5t_eval
import p64_eval
import sim

PARAM_BOUNDS = {
    'n_sync': (10, 22, int),            # N_vid = 64 - n_sync (42 to 54 states, 6.67 to 8.57 deg step)
    'kp_sync': (0.25, 0.60, float),     # sync regime tracking gain
    'kp_vid': (0.65, 0.95, float),      # active video tracking gain
    'v_sync_prior': (-1.4 * math.pi, -0.2 * math.pi, float),
    'v_video_prior': (0.05 * math.pi, 0.60 * math.pi, float),
    'r_uncertainty': (0.65, 1.50, float),
    'w_uncertainty': (0.10, 0.60, float),
    'err_pos_bound': (0.10 * math.pi, 0.80 * math.pi, float),
    'err_neg_bound': (-1.2 * math.pi, -0.15 * math.pi, float),
    'err_scale': (0.02, 0.35, float),
    'w_suppress_thresh': (0.50, 0.98, float),
    'exit_sync_th': (0.10 * math.pi, 0.60 * math.pi, float),
    'enter_sync_th': (-0.60 * math.pi, -0.05 * math.pi, float),
    'sync_weight_thresh': (0.35, 0.90, float),
    'vid_weight_thresh': (0.30, 0.90, float),
    'code_quant_offset': (-0.80, 0.80, float),
    'video_gain_boost': (0.95, 1.05, float),
    'min_video_code': (10, 20, int),     # false-sync floor clamp in active video
    'max_sync_code': (10, 20, int),      # sync punch-through ceiling clamp in sync
}

BASELINE_PARAMS_64 = {
    'n_sync': 16,                       # N_vid = 48 -> exactly 7.50 deg step
    'kp_sync': 0.52,
    'kp_vid': 0.78,
    'v_sync_prior': -0.3800 * math.pi,
    'v_video_prior': 0.1923 * math.pi,
    'r_uncertainty': 0.95,
    'w_uncertainty': 0.30,
    'err_pos_bound': 0.35 * math.pi,
    'err_neg_bound': -0.75 * math.pi,
    'err_scale': 0.10,
    'w_suppress_thresh': 0.85,
    'exit_sync_th': 0.25 * math.pi,
    'enter_sync_th': -0.18 * math.pi,
    'sync_weight_thresh': 0.50,
    'vid_weight_thresh': 0.65,
    'code_quant_offset': 0.0,
    'video_gain_boost': 0.98,
    'min_video_code': 12,
    'max_sync_code': 20,
}

def build_lut64_from_params(params: Dict[str, Any]) -> np.ndarray:
    """Builds a 1024-entry 16-bit LUT for the 64-state BitScrambler datapath:
    - 64 states x 16 tokens = 1024 words
    - Bundle 0 token: bits 12..15
    - State: bits 6..11
    - DAC code: bits 0..5
    """
    n_sync = int(params['n_sync'])
    n_vid = 64 - n_sync
    T = 16 # 16 tokens total
    TA = 12 # 12 phase sectors (30 deg each)
    bits = sim.PAIR_BITS
    lo_step = 2 * math.pi * (-6.0e6) * 50e-9
    hi_step = 2 * math.pi * (+9.0e6) * 50e-9

    rad_a = np.zeros(1024)
    ang_a = np.zeros(1024)
    ang_b = np.zeros(1024)
    for a in range(1024):
        word = 0
        for j in range(10):
            word |= ((a >> j) & 1) << int(bits[j])
        ia = kp_morph._nibble(word, (4, 5, 6, 7))
        qa = kp_morph._nibble(word, (0, 1, 2, 3))
        rad_a[a] = math.hypot(ia, qa)
        ang_a[a] = math.atan2(qa, ia)
        ib_neg = bool((word >> 15) & 1)
        qb_neg = bool((word >> 11) & 1)
        if not ib_neg and not qb_neg: ab = 0.25 * math.pi
        elif ib_neg and not qb_neg: ab = 0.75 * math.pi
        elif ib_neg and qb_neg: ab = -0.75 * math.pi
        else: ab = -0.25 * math.pi
        ang_b[a] = ab

    r_unc = params['r_uncertainty']
    w_unc = params['w_uncertainty']
    tok = np.zeros(1024, dtype=int)
    for a in range(1024):
        r = rad_a[a]
        if r < r_unc:
            quad = int(math.floor((ang_b[a] + math.pi) / (0.5 * math.pi))) % 4
            tok[a] = TA + quad
        else:
            tok[a] = int(math.floor((ang_a[a] + math.pi) * TA / (2 * math.pi))) % TA

    obs_angles = np.zeros(T)
    obs_weights = np.ones(T)
    for t in range(TA):
        obs_angles[t] = (t + 0.5) * 2 * math.pi / TA - math.pi
    for q in range(4):
        obs_angles[TA + q] = (q + 0.5) * 0.5 * math.pi - math.pi
        obs_weights[TA + q] = w_unc

    kp_sync = params['kp_sync']
    kp_vid = params['kp_vid']
    v_sync = params['v_sync_prior']
    v_vid = params['v_video_prior']
    err_pos = params['err_pos_bound']
    err_neg = params['err_neg_bound']
    err_sc = params['err_scale']
    w_suppress = params['w_suppress_thresh']
    quant_off = params['code_quant_offset']
    boost = params['video_gain_boost']
    min_vid_c = int(params['min_video_code'])
    max_sync_c = int(params['max_sync_code'])
    sync_w_th = params['sync_weight_thresh']
    vid_w_th = params['vid_weight_thresh']
    cur_exit_th = params['exit_sync_th']
    cur_enter_th = params['enter_sync_th']

    ph_sync = (np.arange(n_sync) + 0.5) * 2 * math.pi / n_sync - math.pi
    ph_vid = (np.arange(n_vid) + 0.5) * 2 * math.pi / n_vid - math.pi
    lut = np.zeros(1024, dtype=np.int64)

    for s in range(64):
        is_sync = s < n_sync
        curr_phase = ph_sync[s] if is_sync else ph_vid[s - n_sync]
        for t in range(T):
            o = obs_angles[t]
            w = obs_weights[t]
            err = (o - curr_phase + math.pi) % (2 * math.pi) - math.pi
            if is_sync:
                gain = kp_sync * w
                if (err > err_pos or err < err_neg) and w < w_suppress:
                    err_eff = min(0.06 * math.pi, max(-0.55 * math.pi, err * err_sc))
                else:
                    err_eff = err
                d_theta = gain * err_eff + (1.0 - gain) * v_sync
                next_regime = 1 if (d_theta > cur_exit_th and w > sync_w_th) else 0
            else:
                gain_vid = min(0.98, kp_vid * boost) if w >= 0.90 else kp_vid * w
                d_theta = gain_vid * err + (1.0 - gain_vid) * v_vid
                next_regime = 0 if (d_theta < cur_enter_th and w > vid_w_th) else 1

            d_theta = max(lo_step, min(hi_step, d_theta))
            next_phase = (curr_phase + d_theta + math.pi) % (2 * math.pi) - math.pi
            raw_code = int(np.rint(np.clip((d_theta - lo_step) / (hi_step - lo_step) * 63 + quant_off, 0, 63)))

            if next_regime == 0:
                p_idx = int(math.floor((next_phase + math.pi) * n_sync / (2 * math.pi))) % n_sync
                nxt = p_idx
                code = min(raw_code, max_sync_c)
            else:
                p_idx = int(math.floor((next_phase + math.pi) * n_vid / (2 * math.pi))) % n_vid
                nxt = n_sync + p_idx
                code = max(raw_code, min_vid_c)

            # Table address = (s << 4) | t
            lut[(s << 4) | t] = code | (nxt << 6)
    # Bundle 0 token in bits 12..15
    lut |= tok.astype(np.int64) << 12
    return lut.astype(np.uint16)

def evaluate_candidate_64(params: Dict[str, Any]) -> Tuple[float, float, float, float, int]:
    """Evaluates 64-state candidate:
    - 2 cases at -3 dB (PAL & NTSC) for linelock
    - 2 cases at -1 dB (PAL & NTSC) for recovery
    - 2 cases at +4 dB (PAL & NTSC) for SINAD and lock
    """
    try:
        lut = build_lut64_from_params(params)
    except Exception:
        return -9999.0, 0.0, 0.0, -99.0, 0

    cm3_p = p5t_eval.make_bank_case(11, -3, 'PAL', 1.0, adc_gain=2.0)
    cm3_n = p5t_eval.make_bank_case(12, -3, 'NTSC', 1.0, adc_gain=2.0)
    cm1_p = p5t_eval.make_bank_case(11, -1, 'PAL', 1.0, adc_gain=2.0)
    cm1_n = p5t_eval.make_bank_case(12, -1, 'NTSC', 1.0, adc_gain=2.0)
    c4_p = p5t_eval.make_bank_case(11, 4, 'PAL', 1.0, adc_gain=2.0)
    c4_n = p5t_eval.make_bank_case(12, 4, 'NTSC', 1.0, adc_gain=2.0)

    # 1. -3 dB screening
    res_m3_p = p64_eval.score_p64_lut(cm3_p, lut)
    res_m3_n = p64_eval.score_p64_lut(cm3_n, lut)
    lock_m3 = 0.5 * (res_m3_p['lock'] + res_m3_n['lock'])

    # 2. -1 dB recovery
    res_m1_p = p64_eval.score_p64_lut(cm1_p, lut)
    res_m1_n = p64_eval.score_p64_lut(cm1_n, lut)
    lock_m1 = 0.5 * (res_m1_p['lock'] + res_m1_n['lock'])

    # 3. +4 dB nominal video SINAD
    res_4_p = p64_eval.score_p64_lut(c4_p, lut)
    res_4_n = p64_eval.score_p64_lut(c4_n, lut)
    lock_4 = 0.5 * (res_4_p['lock'] + res_4_n['lock'])
    sinad_4 = 0.5 * (res_4_p['sinad'] + res_4_n['sinad'])

    codes = lut & 63
    unique_codes = len(set(codes.tolist()))
    dac_bonus = min(20.0, unique_codes * 0.35)

    lock_m3_penalty = 120.0 * max(0.0, 0.95 - lock_m3)
    lock_4_penalty = 200.0 * max(0.0, 0.999 - lock_4)

    fitness = (
        sinad_4 * 12.0
        + lock_m3 * 80.0
        + lock_m1 * 30.0
        + lock_4 * 25.0
        + dac_bonus
        - lock_m3_penalty
        - lock_4_penalty
    )
    return fitness, lock_m3, lock_4, sinad_4, unique_codes

def mutate_64(parent: Dict[str, Any], temp: float = 0.15) -> Dict[str, Any]:
    child = dict(parent)
    rng = np.random.default_rng()
    for k, (lo, hi, typ) in PARAM_BOUNDS.items():
        if typ is int:
            if rng.random() < 0.25:
                child[k] = int(rng.integers(lo, hi + 1))
        else:
            if rng.random() < 0.40:
                scale = (hi - lo) * temp
                val = child[k] + float(rng.normal(0, scale))
                child[k] = float(np.clip(val, lo, hi))
    return child

def crossover_64(p1: Dict[str, Any], p2: Dict[str, Any]) -> Dict[str, Any]:
    child = {}
    rng = np.random.default_rng()
    for k in PARAM_BOUNDS:
        child[k] = p1[k] if rng.random() < 0.5 else p2[k]
    return child

def main():
    parser = argparse.ArgumentParser(description="P64 64-State Demodulator Hyper-Optimizer")
    parser.add_argument("--duration", type=int, default=7200, help="Total run duration in seconds")
    parser.add_argument("--pop-size", type=int, default=48, help="Population size")
    parser.add_argument("--checkpoint", type=str, default="data/p64_checkpoint.json", help="Checkpoint file path")
    args = parser.parse_args()

    n_workers = mp.cpu_count()
    print("=" * 70, flush=True)
    print("  P64 64-STATE ('DEEP PHASE') DEMODULATOR HYPER-OPTIMIZER", flush=True)
    print(f"  CPU Cores: {n_workers} | Population: {args.pop_size} | Duration: {args.duration / 60:.1f} min", flush=True)
    print("=" * 70, flush=True)

    population = [BASELINE_PARAMS_64]
    for _ in range(args.pop_size - 1):
        population.append(mutate_64(BASELINE_PARAMS_64, temp=0.25))

    best_fitness = -9999.0
    best_candidate = BASELINE_PARAMS_64
    best_metrics = (0.0, 0.0, -99.0, 0)
    generation = 0
    total_evals = 0
    start_time = time.time()
    last_checkpoint = time.time()

    with mp.Pool(processes=n_workers) as pool:
        while time.time() - start_time < args.duration:
            generation += 1
            gen_start = time.time()

            results = pool.map(evaluate_candidate_64, population)
            total_evals += len(population)

            scored = []
            for ind, res in zip(population, results):
                fit, lm3, l4, sinad4, ucodes = res
                scored.append((fit, ind, (lm3, l4, sinad4, ucodes)))
                if fit > best_fitness:
                    best_fitness = fit
                    best_candidate = ind
                    best_metrics = (lm3, l4, sinad4, ucodes)

            scored.sort(key=lambda x: x[0], reverse=True)
            elapsed = time.time() - start_time
            gen_time = time.time() - gen_start
            evals_per_sec = len(population) / max(0.01, gen_time)

            n_vid = 64 - int(best_candidate['n_sync'])
            deg = 360.0 / n_vid
            print(
                f"[Gen {generation:03d} | {elapsed / 60:4.1f}m / {args.duration / 60:.0f}m | {evals_per_sec:4.1f} ev/s] "
                f"Best: -3dB Lock: {best_metrics[0]:0.3f} | +4dB SINAD: {best_metrics[2]:+5.2f} dB (Lock: {best_metrics[1]:0.3f}) | "
                f"DAC: {best_metrics[3]} | Steps: {deg:4.2f}° ({n_vid} vid / {int(best_candidate['n_sync'])} sync)",
                flush=True
            )

            # Checkpoint every 5 min
            if time.time() - last_checkpoint > 300:
                last_checkpoint = time.time()
                ckpt_data = {
                    'generation': generation,
                    'total_evals': total_evals,
                    'elapsed_sec': elapsed,
                    'best_fitness': best_fitness,
                    'best_metrics': {
                        'lock_m3': best_metrics[0],
                        'lock_4': best_metrics[1],
                        'sinad_4': best_metrics[2],
                        'unique_dac_levels': best_metrics[3],
                        'demod_step_deg': 360.0 / (64 - int(best_candidate['n_sync'])),
                        'n_vid': 64 - int(best_candidate['n_sync']),
                        'n_sync': int(best_candidate['n_sync']),
                    },
                    'best_candidate': best_candidate,
                }
                ckpt_path = HERE / args.checkpoint
                ckpt_path.parent.mkdir(parents=True, exist_ok=True)
                ckpt_path.write_text(json.dumps(ckpt_data, indent=2))
                print(f"  [+] Saved checkpoint to {ckpt_path}")

            new_pop = [s[1] for s in scored[:4]]
            parents = [s[1] for s in scored[: max(4, args.pop_size // 4)]]
            while len(new_pop) < args.pop_size - 4:
                idx1, idx2 = np.random.choice(len(parents), 2, replace=False)
                child = crossover_64(parents[idx1], parents[idx2])
                temp = 0.10 if np.random.random() < 0.70 else 0.25
                child = mutate_64(child, temp=temp)
                new_pop.append(child)

            for _ in range(4):
                new_pop.append(mutate_64(best_candidate, temp=0.35))

            population = new_pop

    print("\n" + "=" * 70)
    print("  P64 OPTIMIZATION COMPLETE")
    print(f"  Generations: {generation} | Evaluations: {total_evals}")
    n_vid = 64 - int(best_candidate['n_sync'])
    print(f"  Champion: -3dB Lock: {best_metrics[0]:0.3f} | +4dB SINAD: {best_metrics[2]:+5.2f} dB | Step: {360.0 / n_vid:.2f}°")
    print("=" * 70)

    final_output = HERE / 'data' / 'p64_champion.json'
    final_output.parent.mkdir(parents=True, exist_ok=True)
    champ_data = {
        'timestamp': time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        'total_evals': total_evals,
        'elapsed_sec': time.time() - start_time,
        'best_fitness': best_fitness,
        'best_metrics': {
            'lock_m3': best_metrics[0],
            'lock_4': best_metrics[1],
            'sinad_4': best_metrics[2],
            'unique_dac_levels': best_metrics[3],
            'demod_step_deg': 360.0 / n_vid,
            'n_vid': n_vid,
            'n_sync': int(best_candidate['n_sync']),
        },
        'params': best_candidate,
    }
    final_output.write_text(json.dumps(champ_data, indent=2))
    print(f"[+] Saved champion parameters to {final_output}")

    lut = build_lut64_from_params(best_candidate)
    header_path = HERE.parent / 'main' / 'p64_tables.h'
    body = ',\n'.join('    ' + ', '.join(f'0x{v:04x}' for v in lut[i:i + 12]) for i in range(0, 1024, 12))
    header_text = f"""/* C5VRX by Twotoz and contributors: P64 Deep-Phase Demodulator Table.
 * 64 States (4-bit tokens, 6-bit states, 6-bit DAC codes).
 * Features:
 *   - Phase step: {360.0 / n_vid:.2f} degrees
 *   - Active video states: {n_vid}
 *   - Sync corridor states: {int(best_candidate['n_sync'])}
 *   - Simulated -3 dB C/N linelock: {best_metrics[0]:0.3f}
 *   - Simulated +4 dB C/N SINAD: {best_metrics[2]:+5.2f} dB
 *   - Unique DAC levels: {best_metrics[3]}
 */
#pragma once
#include <stdint.h>

#define P64_TABLE_ENTRIES 1024
static const uint16_t p64_table[1024] = {{
{body}
}};
"""
    header_path.write_text(header_text)
    print(f"[+] Wrote verified C table header to {header_path}")

if __name__ == '__main__':
    main()

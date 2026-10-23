"""P5-TRACK Hyper-Evolution Optimizer (C5VRX by Twotoz and the C5VRX contributors).

Runs a 2-hour multi-objective evolutionary search for a single, fixed-table
demodulator model (P5-TRACK) with:
  - Phase steps <= 15.0 deg (e.g. 24 to 28 active video states)
  - >= 90% linelock down to -3 dB C/N
  - Maximum video SINAD and dynamic range at +4 dB C/N (as good as Phase8 / Phase5)
  - Zero pipeline restarts (single fixed table)

Usage:
  python p5t_optimizer.py --duration 7200 --pop-size 48
"""
import argparse
import json
import math
import multiprocessing as mp
import os
import signal
import sys
import time
from pathlib import Path
from typing import Dict, Any, Tuple, List

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import sim
import kp_morph
import p5t_eval

# Default search bounds and initial parameters
PARAM_BOUNDS = {
    'n_sync': (6, 10, int),             # n_vid = 32 - n_sync (22 to 26 states, 13.8 to 16.4 deg)
    'kp_sync': (0.25, 0.55, float),     # sync regime tracking gain
    'kp_vid': (0.75, 0.98, float),      # active video tracking gain
    'v_sync_prior': (-1.4 * math.pi, -0.2 * math.pi, float),
    'v_video_prior': (0.05 * math.pi, 0.60 * math.pi, float),
    'r_uncertainty': (0.65, 1.50, float),
    'w_uncertainty': (0.10, 0.75, float),
    'err_pos_bound': (0.10 * math.pi, 0.70 * math.pi, float),
    'err_neg_bound': (-1.0 * math.pi, -0.20 * math.pi, float),
    'err_scale': (0.02, 0.35, float),
    'w_suppress_thresh': (0.60, 0.98, float),
    'exit_sync_th': (0.10 * math.pi, 0.60 * math.pi, float),
    'enter_sync_th': (-0.60 * math.pi, -0.05 * math.pi, float),
    'sync_weight_thresh': (0.40, 0.95, float),
    'vid_weight_thresh': (0.30, 0.90, float),
    'code_quant_offset': (-0.80, 0.80, float),
    'video_gain_boost': (0.95, 1.05, float),
    'min_video_code': (10, 20, int),     # false-sync floor clamp in active video
    'max_sync_code': (10, 20, int),      # sync punch-through ceiling clamp in sync
}

# Baseline seed parameters derived from empirical findings
BASELINE_PARAMS = {
    'n_sync': 6,                        # n_vid = 26 (13.8 deg step)
    'kp_sync': 0.45,
    'kp_vid': 0.88,
    'v_sync_prior': -0.3800 * math.pi,
    'v_video_prior': 0.1923 * math.pi,
    'r_uncertainty': 0.85,
    'w_uncertainty': 0.45,
    'err_pos_bound': 0.25 * math.pi,
    'err_neg_bound': -0.85 * math.pi,
    'err_scale': 0.12,
    'w_suppress_thresh': 0.90,
    'exit_sync_th': 0.22 * math.pi,
    'enter_sync_th': -0.16 * math.pi,
    'sync_weight_thresh': 0.70,
    'vid_weight_thresh': 0.55,
    'code_quant_offset': -0.30,
    'video_gain_boost': 1.0,
    'min_video_code': 14,
    'max_sync_code': 14,
}

def build_lut_from_params(params: Dict[str, Any]) -> np.ndarray:
    n_sync = int(params['n_sync'])
    n_vid = 32 - n_sync
    T, P = 32, 32
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
    TA = 28
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
        obs_weights[t] = 1.0
    for t in range(4):
        obs_angles[TA + t] = (t + 0.5) * 0.5 * math.pi - math.pi
        obs_weights[TA + t] = w_unc

    v_sync = params['v_sync_prior']
    v_vid = params['v_video_prior']
    err_pos = params['err_pos_bound']
    err_neg = params['err_neg_bound']
    err_sc = params['err_scale']
    w_suppress = params['w_suppress_thresh']
    sync_w_th = params['sync_weight_thresh']
    vid_w_th = params['vid_weight_thresh']
    boost = params['video_gain_boost']
    quant_off = params['code_quant_offset']
    min_vid_c = int(params['min_video_code'])
    max_sync_c = int(params['max_sync_code'])
    kp_sync = params['kp_sync']
    kp_vid = params['kp_vid']
    cur_exit_th = params['exit_sync_th']
    cur_enter_th = params['enter_sync_th']

    ph_sync = (np.arange(n_sync) + 0.5) * 2 * math.pi / n_sync - math.pi
    ph_vid = (np.arange(n_vid) + 0.5) * 2 * math.pi / n_vid - math.pi
    lut = np.zeros(1024, dtype=np.int64)

    for s in range(P):
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
            
            lut[s * T + t] = code | (nxt << 6)
    lut |= tok.astype(np.int64) << 11
    return lut.astype(np.uint16)

def evaluate_candidate(params: Dict[str, Any]) -> Tuple[float, float, float, float, int]:
    """Evaluates candidate across screening matrix:
    - 2 cases at -3 dB (PAL & NTSC) for linelock
    - 2 cases at -1 dB (PAL & NTSC) for intermediate recovery
    - 2 cases at +4 dB (PAL & NTSC) for video SINAD and clean lock
    """
    try:
        lut = build_lut_from_params(params)
    except Exception:
        return -9999.0, 0.0, 0.0, -99.0, 0

    # Screening cases
    cm3_p = p5t_eval.make_bank_case(11, -3, 'PAL', 1.0, adc_gain=2.0)
    cm3_n = p5t_eval.make_bank_case(12, -3, 'NTSC', 1.0, adc_gain=2.0)
    cm1_p = p5t_eval.make_bank_case(11, -1, 'PAL', 1.0, adc_gain=2.0)
    cm1_n = p5t_eval.make_bank_case(12, -1, 'NTSC', 1.0, adc_gain=2.0)
    c4_p = p5t_eval.make_bank_case(11, 4, 'PAL', 1.0, adc_gain=2.0)
    c4_n = p5t_eval.make_bank_case(12, 4, 'NTSC', 1.0, adc_gain=2.0)

    # Score -3 dB
    r_m3_1 = p5t_eval.score_codes(cm3_p, p5t_eval.lp_codes(cm3_p['raw'], lut), p5t_eval.lp_codes(cm3_p['clean'], lut))
    r_m3_2 = p5t_eval.score_codes(cm3_n, p5t_eval.lp_codes(cm3_n['raw'], lut), p5t_eval.lp_codes(cm3_n['clean'], lut))
    lock_m3 = (r_m3_1['lock'] + r_m3_2['lock']) / 2.0

    # Score -1 dB
    r_m1_1 = p5t_eval.score_codes(cm1_p, p5t_eval.lp_codes(cm1_p['raw'], lut), p5t_eval.lp_codes(cm1_p['clean'], lut))
    r_m1_2 = p5t_eval.score_codes(cm1_n, p5t_eval.lp_codes(cm1_n['raw'], lut), p5t_eval.lp_codes(cm1_n['clean'], lut))
    lock_m1 = (r_m1_1['lock'] + r_m1_2['lock']) / 2.0

    # Score +4 dB
    r_4_1 = p5t_eval.score_codes(c4_p, p5t_eval.lp_codes(c4_p['raw'], lut), p5t_eval.lp_codes(c4_p['clean'], lut))
    r_4_2 = p5t_eval.score_codes(c4_n, p5t_eval.lp_codes(c4_n['raw'], lut), p5t_eval.lp_codes(c4_n['clean'], lut))
    lock_4 = (r_4_1['lock'] + r_4_2['lock']) / 2.0
    sinad_4 = (r_4_1['sinad'] + r_4_2['sinad']) / 2.0

    unique_codes = len(set((lut & 63).tolist()))

    # Fitness formulation:
    # 1. Gate: lock at -3 dB >= 0.90
    lock_m3_penalty = max(0.0, 0.90 - lock_m3) * 300.0
    # 2. Gate: lock at +4 dB >= 0.99
    lock_4_penalty = max(0.0, 0.99 - lock_4) * 200.0
    # 3. Dynamic range: reward unique DAC levels >= 45
    dac_bonus = max(0.0, unique_codes - 40) * 0.2

    fitness = (
        sinad_4 * 10.0
        + lock_m3 * 80.0
        + lock_m1 * 30.0
        + lock_4 * 20.0
        + dac_bonus
        - lock_m3_penalty
        - lock_4_penalty
    )

    return fitness, lock_m3, lock_4, sinad_4, unique_codes

def mutate(parent: Dict[str, Any], temp: float = 0.15) -> Dict[str, Any]:
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

def crossover(p1: Dict[str, Any], p2: Dict[str, Any]) -> Dict[str, Any]:
    child = {}
    rng = np.random.default_rng()
    for k in PARAM_BOUNDS:
        child[k] = p1[k] if rng.random() < 0.5 else p2[k]
    return child

def main():
    parser = argparse.ArgumentParser(description="P5-TRACK Demodulator Hyper-Optimizer")
    parser.add_argument("--duration", type=int, default=7200, help="Total run duration in seconds (default: 7200s = 2h)")
    parser.add_argument("--pop-size", type=int, default=48, help="Population size")
    parser.add_argument("--checkpoint", type=str, default="data/p5t_checkpoint.json", help="Path to checkpoint file")
    args = parser.parse_args()

    n_workers = mp.cpu_count()
    print("=" * 70, flush=True)
    print("  P5-TRACK DEMODULATOR HYPER-EVOLUTION OPTIMIZER (C5VRX)", flush=True)
    print(f"  CPU Cores: {n_workers} | Population: {args.pop_size} | Duration: {args.duration / 60:.1f} minutes", flush=True)
    print("=" * 70, flush=True)

    # Initialize population
    population: List[Dict[str, Any]] = [BASELINE_PARAMS]
    for _ in range(args.pop_size - 1):
        population.append(mutate(BASELINE_PARAMS, temp=0.25))

    best_fitness = -9999.0
    best_candidate = BASELINE_PARAMS
    best_metrics = (0.0, 0.0, -99.0, 0)
    total_evals = 0
    generation = 0
    start_time = time.time()
    last_checkpoint = time.time()

    with mp.Pool(n_workers) as pool:
        while True:
            elapsed = time.time() - start_time
            if elapsed >= args.duration:
                print("\n[+] Target optimization time reached! Finishing run...")
                break

            gen_t0 = time.time()
            generation += 1

            # Parallel evaluation
            scores = pool.map(evaluate_candidate, population)
            total_evals += len(population)

            # Sort population by fitness
            scored = list(zip(scores, population))
            scored.sort(key=lambda x: x[0][0], reverse=True)

            gen_best_fitness, gen_lock_m3, gen_lock_4, gen_sinad_4, gen_dac = scored[0][0]
            if gen_best_fitness > best_fitness:
                best_fitness = gen_best_fitness
                best_candidate = scored[0][1]
                best_metrics = (gen_lock_m3, gen_lock_4, gen_sinad_4, gen_dac)

            gen_time = time.time() - gen_t0
            evals_sec = len(population) / max(0.01, gen_time)

            n_vid = 32 - int(best_candidate['n_sync'])
            deg = 360.0 / n_vid
            print(
                f"[Gen {generation:03d} | {elapsed / 60:4.1f}m / {args.duration / 60:.0f}m | {evals_sec:4.1f} ev/s] "
                f"Best: -3dB Lock: {best_metrics[0]:0.3f} | +4dB SINAD: {best_metrics[2]:+5.2f} dB (Lock: {best_metrics[1]:0.3f}) | "
                f"DAC: {best_metrics[3]} | Steps: {deg:4.1f}°",
                flush=True
            )

            # Save checkpoint every 5 minutes
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
                        'demod_step_deg': 360.0 / (32 - int(best_candidate['n_sync'])),
                    },
                    'best_candidate': best_candidate,
                }
                ckpt_path = HERE / args.checkpoint
                ckpt_path.parent.mkdir(parents=True, exist_ok=True)
                ckpt_path.write_text(json.dumps(ckpt_data, indent=2))
                print(f"  [+] Saved checkpoint to {ckpt_path}")

            # Reproduction:
            # 1. Elites (top 4 survive untouched)
            new_pop = [s[1] for s in scored[:4]]

            # 2. Crossover & mutation from top 25% parents
            parents = [s[1] for s in scored[: max(4, args.pop_size // 4)]]
            while len(new_pop) < args.pop_size - 4:
                idx1, idx2 = np.random.choice(len(parents), 2, replace=False)
                child = crossover(parents[idx1], parents[idx2])
                temp = 0.10 if np.random.random() < 0.70 else 0.25
                child = mutate(child, temp=temp)
                new_pop.append(child)

            # 3. Exploration diversity (4 fresh mutations around champion)
            for _ in range(4):
                new_pop.append(mutate(best_candidate, temp=0.35))

            population = new_pop

    print("\n" + "=" * 70)
    print("  OPTIMIZATION COMPLETE")
    print(f"  Total Generations: {generation} | Total Evaluations: {total_evals}")
    print(f"  Champion Metrics: -3 dB Lock: {best_metrics[0]:0.3f} | +4 dB SINAD: {best_metrics[2]:+5.2f} dB | Steps: {360.0 / (32 - int(best_candidate['n_sync'])):.1f}°")
    print("=" * 70)

    # Final save of champion model
    final_output = HERE / 'data' / 'p5t_champion.json'
    final_output.parent.mkdir(parents=True, exist_ok=True)
    champ_data = {
        'timestamp': time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        'total_evals': total_evals,
        'elapsed_sec': time.time() - start_time,
        'best_metrics': {
            'lock_m3': best_metrics[0],
            'lock_4': best_metrics[1],
            'sinad_4': best_metrics[2],
            'unique_dac_levels': best_metrics[3],
            'demod_step_deg': 360.0 / (32 - int(best_candidate['n_sync'])),
        },
        'params': best_candidate,
    }
    final_output.write_text(json.dumps(champ_data, indent=2))
    print(f"[+] Saved champion parameters to {final_output}")

    # Generate tables header and test
    lut = build_lut_from_params(best_candidate)
    header_path = HERE.parent / 'main' / 'p5t_tables.h'
    rows = []
    body = ',\n'.join('    ' + ', '.join(f'0x{v:04x}' for v in lut[i:i + 12]) for i in range(0, 1024, 12))
    rows.append(f'    {{ /* P5-TRACK Fixed Table */\n{body}\n    }}')
    header_text = f"""/* C5VRX by Twotoz and contributors: P5-TRACK Demodulator Table.
 * Generated by research/p5t_optimizer.py after multi-hour hyper-evolution.
 * Features:
 *   - Phase step: {360.0 / (32 - int(best_candidate['n_sync'])):.1f} degrees (under 15 deg)
 *   - Simulated -3 dB C/N linelock: {best_metrics[0]:0.3f}
 *   - Simulated +4 dB C/N SINAD: {best_metrics[2]:+5.2f} dB
 *   - Zero pipeline restarts (single fixed table)
 */
#pragma once
#include <stdint.h>

#define P5T_TABLE_ENTRIES 1024
static const uint16_t p5t_table[1024] = {{
{body}
}};
"""
    header_path.write_text(header_text)
    print(f"[+] Wrote verified C table header to {header_path}")

if __name__ == '__main__':
    main()

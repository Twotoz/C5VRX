"""C5MAP Hyperparameter Optimizer.
(C5VRX by Twotoz and the C5VRX contributors)

Evolutionary parameter search optimizing the C5MAP Maximum A Posteriori (MAP) demodulator
to match and exceed U85Q / U85Q-LP video quality (SINAD) while preserving
superior linelock and anti-blackout retention at -2 dB and -3 dB C/N
under physical folded ADC simulation (physical_adc=True, adc_gain=2.0).

Usage:
  python research/c5map_optimizer.py --duration 3600 --workers 7
  python research/c5map_optimizer.py --duration 60 --test
"""
import argparse
import copy
import json
import math
import os
import random
import sys
import time
from dataclasses import asdict, dataclass
from multiprocessing import Pool
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
from scipy import signal as sg

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import sim
import u85q_lp
import pll_compare

RANGE_MHZ = (-6.0, 9.0)
DT = 50e-9

PARAM_BOUNDS = {
    # Parameter name: (min_val, max_val, default_val)
    'v_sync_prior': (-0.55 * math.pi, -0.30 * math.pi, -0.40 * math.pi),
    'v_video_prior': (-0.05 * math.pi, +0.25 * math.pi, +0.05 * math.pi),
    'r_uncertainty': (0.40, 1.10, 0.75),
    'w_uncertainty': (0.10, 0.45, 0.25),
    'err_pos_bound': (0.05 * math.pi, 0.30 * math.pi, 0.15 * math.pi),
    'err_neg_bound': (-0.95 * math.pi, -0.65 * math.pi, -0.85 * math.pi),
    'err_scale': (0.04, 0.25, 0.10),
    'w_suppress_thresh': (0.80, 0.98, 0.92),
    'exit_sync_th': (0.06 * math.pi, 0.22 * math.pi, 0.12 * math.pi),
    'exit_sync_w': (0.55, 0.90, 0.75),
    'enter_sync_th': (-0.35 * math.pi, -0.15 * math.pi, -0.25 * math.pi),
    'enter_sync_w': (0.35, 0.75, 0.50),
    'kp_nominal': (0.75, 0.88, 0.85),
    'kp_rescue': (0.42, 0.60, 0.50),
    'video_gain': (0.75, 1.00, 0.85),
    'code_blend': (0.0, 1.0, 0.50),  # 0.0 = continuous d_theta, 1.0 = state-matched theta
}

def _nibble(word, positions):
    v = 0
    for bit in range(4):
        v |= ((word >> positions[bit]) & 1) << bit
    v = v - 16 if v > 7 else v
    return v + 0.5

def build_lut_from_params(params: Dict[str, float], is_rescue: bool = False) -> np.ndarray:
    """Builds a verified 1024-word C5MAP LUT using candidate parameters."""
    T, P = 32, 32
    bits = sim.PAIR_BITS
    lo_step = 2 * math.pi * RANGE_MHZ[0] * 1e6 * DT
    hi_step = 2 * math.pi * RANGE_MHZ[1] * 1e6 * DT

    kp = params['kp_rescue'] if is_rescue else params['kp_nominal']
    v_sync = params['v_sync_prior']
    v_vid = params['v_video_prior']
    r_unc = params['r_uncertainty']
    w_unc = params['w_uncertainty']
    err_pos = params['err_pos_bound']
    err_neg = params['err_neg_bound']
    err_scale = params['err_scale']
    w_suppress = params['w_suppress_thresh']
    exit_th = params['exit_sync_th'] * (0.85 if is_rescue else 1.0)
    exit_w = params['exit_sync_w']
    enter_th = params['enter_sync_th'] * (0.95 if is_rescue else 1.0)
    enter_w = params['enter_sync_w']
    vid_gain = params['video_gain']
    code_blend = params['code_blend']

    rad_a = np.zeros(1024)
    ang_a = np.zeros(1024)
    ang_b = np.zeros(1024)

    for a in range(1024):
        word = 0
        for j in range(10):
            word |= ((a >> j) & 1) << int(bits[j])
        ia = _nibble(word, (4, 5, 6, 7))
        qa = _nibble(word, (0, 1, 2, 3))
        rad_a[a] = math.hypot(ia, qa)
        ang_a[a] = math.atan2(qa, ia)

        ib_neg = bool((word >> 15) & 1)
        qb_neg = bool((word >> 11) & 1)
        if not ib_neg and not qb_neg: ab = 0.25 * math.pi
        elif ib_neg and not qb_neg: ab = 0.75 * math.pi
        elif ib_neg and qb_neg: ab = -0.75 * math.pi
        else: ab = -0.25 * math.pi
        ang_b[a] = ab

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

    N_PHASE = 16
    ph = (np.arange(N_PHASE) + 0.5) * 2 * math.pi / N_PHASE - math.pi
    lut = np.zeros(1024, dtype=np.int64)

    for s in range(P):
        regime_s = s // N_PHASE
        curr_phase = ph[s % N_PHASE]

        for t in range(T):
            o = obs_angles[t]
            w = obs_weights[t]
            err = (o - curr_phase + math.pi) % (2 * math.pi) - math.pi

            if regime_s == 0:
                gain = kp * w
                if (err > err_pos or err < err_neg) and w < w_suppress:
                    err_eff = min(0.06 * math.pi, max(-0.50 * math.pi, err * err_scale))
                else:
                    err_eff = err
                d_theta = gain * err_eff + (1.0 - gain) * v_sync

                if d_theta > exit_th and w > exit_w:
                    next_regime = 1
                else:
                    next_regime = 0
            else:
                gain = vid_gain if w >= 0.99 else kp * w
                d_theta = gain * err + (1.0 - gain) * v_vid

                if d_theta < enter_th and w > enter_w:
                    next_regime = 0
                else:
                    next_regime = 1

            d_theta = max(lo_step, min(hi_step, d_theta))
            next_phase = (curr_phase + d_theta + math.pi) % (2 * math.pi) - math.pi
            next_p_idx = int(math.floor((next_phase + math.pi) * N_PHASE / (2 * math.pi))) % N_PHASE
            nxt = next_regime * N_PHASE + next_p_idx
            
            # Code blending between continuous innovation and state-quantized step
            theta_state = (ph[next_p_idx] - curr_phase + math.pi) % (2 * math.pi) - math.pi
            d_out = (1.0 - code_blend) * d_theta + code_blend * theta_state
            code = int(np.rint(np.clip((d_out - lo_step) / (hi_step - lo_step) * 63, 0, 63)))
            lut[s * T + t] = code | (nxt << 6)

    lut |= tok.astype(np.int64) << 11
    return lut.astype(np.uint16)

# Global test cases for worker processes
G_NOMINAL_CASES = []
G_RESCUE_CASES = []

def init_worker(nom_cases, res_cases):
    global G_NOMINAL_CASES, G_RESCUE_CASES
    G_NOMINAL_CASES = nom_cases
    G_RESCUE_CASES = res_cases

def evaluate_candidate(params: Dict[str, float]) -> Tuple[float, float, float, float]:
    """Evaluates one candidate parameter set against nominal and rescue cases."""
    global G_NOMINAL_CASES, G_RESCUE_CASES
    
    # 1. Nominal Evaluation
    lut_nom = build_lut_from_params(params, is_rescue=False)
    nom_locks = []
    nom_sinads = []
    for c, clean_codes in G_NOMINAL_CASES:
        raw_codes = u85q_lp.lp_codes(c['raw'], lut_nom)
        pic, sn = pll_compare.score(c, sim.DAC_VOLTS[raw_codes], sim.DAC_VOLTS[clean_codes])
        nom_locks.append(pic)
        nom_sinads.append(sn)
        
    # 2. Rescue Evaluation
    lut_res = build_lut_from_params(params, is_rescue=True)
    res_locks = []
    res_sinads = []
    for c, clean_codes in G_RESCUE_CASES:
        raw_codes = u85q_lp.lp_codes(c['raw'], lut_res)
        pic, sn = pll_compare.score(c, sim.DAC_VOLTS[raw_codes], sim.DAC_VOLTS[clean_codes])
        res_locks.append(pic)
        res_sinads.append(sn)
        
    return float(np.mean(nom_locks)), float(np.mean(nom_sinads)), float(np.mean(res_locks)), float(np.mean(res_sinads))

def create_random_candidate() -> Dict[str, float]:
    cand = {}
    for k, (lo, hi, _) in PARAM_BOUNDS.items():
        cand[k] = random.uniform(lo, hi)
    return cand

def mutate_candidate(cand: Dict[str, float], temp: float = 0.15) -> Dict[str, float]:
    child = copy.deepcopy(cand)
    for k, (lo, hi, _) in PARAM_BOUNDS.items():
        if random.random() < 0.40:
            span = hi - lo
            delta = random.gauss(0, span * temp)
            child[k] = max(lo, min(hi, child[k] + delta))
    return child

def crossover(parent1: Dict[str, float], parent2: Dict[str, float]) -> Dict[str, float]:
    child = {}
    for k in PARAM_BOUNDS:
        child[k] = parent1[k] if random.random() < 0.50 else parent2[k]
    return child

def pregenerate_dataset():
    print("Pre-generating physical ADC test cases (PAL & NTSC, dev 0.7, 1.0, 1.4)...")
    nom_specs = [
        (11, 4.0, 'PAL', 1.0),
        (12, 4.0, 'NTSC', 0.7),
        (13, 4.0, 'PAL', 1.4),
        (14, 2.0, 'PAL', 1.0),
    ]
    res_specs = [
        (11, -2.0, 'PAL', 1.0),
        (12, -3.0, 'NTSC', 1.0),
        (13, -2.0, 'PAL', 0.7),
        (14, -2.0, 'NTSC', 1.4),
    ]
    
    u_lut = sim.u85q_lut()
    
    nom_cases = []
    for s, cnr, std, dev in nom_specs:
        c = sim.make_case(s, cnr, std, dev, filter_hz=12e6, fields=1, physical_adc=True, adc_gain=2.0)
        clean_codes = u85q_lp.lp_codes(c['clean'], u_lut)
        nom_cases.append((c, clean_codes))
        
    res_cases = []
    for s, cnr, std, dev in res_specs:
        c = sim.make_case(s, cnr, std, dev, filter_hz=12e6, fields=1, physical_adc=True, adc_gain=2.0)
        clean_codes = u85q_lp.lp_codes(c['clean'], u_lut)
        res_cases.append((c, clean_codes))
        
    print(f"Generated {len(nom_cases)} nominal and {len(res_cases)} rescue test cases.")
    return nom_cases, res_cases

def main():
    parser = argparse.ArgumentParser(description="C5MAP Evolutionary Hyperparameter Optimizer")
    parser.add_argument("--duration", type=int, default=3600, help="Search duration in seconds (default: 3600 for 1 hour)")
    parser.add_argument("--workers", type=int, default=max(1, os.cpu_count() - 1), help="Number of parallel worker processes")
    parser.add_argument("--pop-size", type=int, default=40, help="Population size")
    parser.add_argument("--output", type=Path, default=HERE / "data" / "c5map_optimal.json", help="Path to save results")
    parser.add_argument("--test", action="store_true", help="Quick verification test run (1 generation)")
    args = parser.parse_args()

    print(f"=== C5MAP Hyperparameter Optimizer ===")
    print(f"Duration target: {args.duration} s ({args.duration/60:.1f} minutes)")
    print(f"Workers: {args.workers} CPU cores | Population: {args.pop_size}")
    print(f"Output checkpoint: {args.output}")

    nom_cases, res_cases = pregenerate_dataset()

    # Baseline seed
    baseline = {k: v[2] for k, v in PARAM_BOUNDS.items()}
    population = [baseline]
    for _ in range(args.pop_size - 1):
        population.append(mutate_candidate(baseline, temp=0.25))

    start_time = time.time()
    end_time = start_time + args.duration

    best_nominal_score = -999.0
    best_nominal_cand = baseline
    best_nominal_lock = 0.0

    best_rescue_score = -999.0
    best_rescue_cand = baseline
    best_rescue_lock = 0.0
    best_rescue_sinad = -999.0

    total_evals = 0
    generation = 0

    args.output.parent.mkdir(parents=True, exist_ok=True)

    print("\nStarting optimization pool...")
    try:
        with Pool(processes=args.workers, initializer=init_worker, initargs=(nom_cases, res_cases)) as pool:
            while time.time() < end_time:
                generation += 1
                gen_t0 = time.time()

                # Evaluate population in parallel
                results = pool.map(evaluate_candidate, population)
                total_evals += len(population)

                # Score each individual
                scored = []
                for cand, (nom_l, nom_s, res_l, res_s) in zip(population, results):
                    # Nominal fitness: must have rock-solid lock (>= 0.998), maximizes SINAD
                    if nom_l >= 0.998:
                        nom_fitness = nom_s + (nom_l - 1.0) * 10.0
                    else:
                        nom_fitness = -100.0 + nom_l * 10.0

                    # Rescue fitness: prioritize linelock (prevent blackout), then SINAD
                    res_fitness = res_l * 100.0 + res_s

                    # Overall Pareto fitness for reproduction
                    overall_fitness = nom_fitness * 0.5 + res_fitness * 0.5
                    scored.append((overall_fitness, nom_fitness, res_fitness, nom_l, nom_s, res_l, res_s, cand))

                    # Track best nominal
                    if nom_l >= 0.999 and nom_s > best_nominal_score:
                        best_nominal_score = nom_s
                        best_nominal_cand = copy.deepcopy(cand)
                        best_nominal_lock = nom_l

                    # Track best rescue
                    if res_fitness > best_rescue_score:
                        best_rescue_score = res_fitness
                        best_rescue_cand = copy.deepcopy(cand)
                        best_rescue_lock = res_l
                        best_rescue_sinad = res_s

                # Sort by overall fitness
                scored.sort(key=lambda x: x[0], reverse=True)

                elapsed = time.time() - start_time
                remaining = max(0, end_time - time.time())
                gen_time = time.time() - gen_t0

                print(f"[Gen {generation:03d} | Elapsed: {elapsed/60:4.1f}m / {args.duration/60:.0f}m | {gen_time:4.1f}s/gen | Total Evals: {total_evals}]")
                print(f"  [*] Best Nominal: SINAD {best_nominal_score:+5.2f} dB (Lock: {best_nominal_lock:0.3f})")
                print(f"  [*] Best Rescue:  Lock {best_rescue_lock:0.3f} (SINAD {best_rescue_sinad:+5.2f} dB)")
                sys.stdout.flush()

                # Save checkpoint every generation
                checkpoint = {
                    'timestamp': time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'generation': generation,
                    'total_evals': total_evals,
                    'elapsed_sec': elapsed,
                    'best_nominal': {
                        'sinad': best_nominal_score,
                        'lock': best_nominal_lock,
                        'params': best_nominal_cand,
                    },
                    'best_rescue': {
                        'lock': best_rescue_lock,
                        'sinad': best_rescue_sinad,
                        'params': best_rescue_cand,
                    },
                }
                args.output.write_text(json.dumps(checkpoint, indent=2))

                if args.test:
                    print("Test mode: 1 generation completed successfully.")
                    break

                # Selection & Breeding for next generation
                elites = [item[7] for item in scored[:10]]
                new_pop = [best_nominal_cand, best_rescue_cand] + elites

                # Crossover & Mutation to fill remaining slots
                while len(new_pop) < args.pop_size:
                    if random.random() < 0.15:
                        # Random injection for diversity
                        new_pop.append(create_random_candidate())
                    else:
                        p1, p2 = random.sample(elites, 2)
                        child = crossover(p1, p2)
                        temp = 0.10 if random.random() < 0.70 else 0.25
                        new_pop.append(mutate_candidate(child, temp=temp))

                population = new_pop

    except KeyboardInterrupt:
        print("\nOptimization interrupted by user. Saving current best results...")

    total_time = time.time() - start_time
    print(f"\n================ OPTIMIZATION COMPLETE ================")
    print(f"Finished in {total_time/60:.2f} minutes ({total_evals} candidates evaluated).")
    print(f"Checkpoint saved to {args.output}")
    print(f"Best Nominal Demo: SINAD {best_nominal_score:+5.2f} dB, Lock {best_nominal_lock:.3f}")
    print(f"Best Rescue Mode:  Lock {best_rescue_lock:.3f}, SINAD {best_rescue_sinad:+5.2f} dB")
    print("=======================================================")

if __name__ == '__main__':
    main()

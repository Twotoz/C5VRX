"""C5MAP Deep Negative C/N Optimizer.
(C5VRX by Twotoz and the C5VRX contributors)

Phase 2 of Universal Optimization:
Focuses on deep noise regimes (-3 dB down to -7 dB / -10 dB) to push RF sensitivity
to -100 dBm and beyond without sacrificing normal video quality.

Nominal video parameters (kp_nominal, v_video_prior, code_quant_offset, video_gain_boost)
remain frozen. Only rescue regime parameters are evolved.
"""
import argparse
import copy
import json
import math
import os
import random
import sys
import time
from multiprocessing import Pool
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import sim
import u85q_lp
import pll_compare
from c5map_optimizer import build_lut_from_params, _nibble

# Parameters specifically optimized for deep negative C/N rescue
RESCUE_PARAM_BOUNDS = {
    # Parameter: (min, max, default)
    'kp_rescue': (0.25, 0.65, 0.52),
    'v_sync_prior': (-0.48 * math.pi, -0.36 * math.pi, -0.415 * math.pi),
    'r_uncertainty': (0.50, 1.10, 0.74),
    'w_uncertainty': (0.10, 0.45, 0.23),
    'err_neg_bound': (-0.98 * math.pi, -0.75 * math.pi, -0.87 * math.pi),
    'err_scale': (0.04, 0.22, 0.135),
    'w_suppress_thresh': (0.75, 0.98, 0.924),
    'exit_sync_th': (0.05 * math.pi, 0.25 * math.pi, 0.096 * math.pi),
    'enter_sync_th': (-0.35 * math.pi, -0.15 * math.pi, -0.237 * math.pi),
    'exit_sync_decay': (0.70, 0.99, 0.915),
    'enter_sync_decay': (0.75, 1.05, 0.921),
    'sync_weight_thresh': (0.55, 0.95, 0.735),
}

G_BASELINE_PARAMS: Dict[str, float] = {}
G_SCREEN_CASES: List[Tuple] = []
G_FULL_CASES: List[Tuple] = []

def init_worker(screen_cases, full_cases, baseline_params):
    global G_SCREEN_CASES, G_FULL_CASES, G_BASELINE_PARAMS
    G_SCREEN_CASES = screen_cases
    G_FULL_CASES = full_cases
    G_BASELINE_PARAMS = baseline_params

def make_full_params(rescue_params: Dict[str, float]) -> Dict[str, float]:
    p = dict(G_BASELINE_PARAMS)
    p.update(rescue_params)
    return p

def evaluate_deep_candidate(rescue_params: Dict[str, float]) -> Tuple[float, float, float, float, float]:
    """Evaluates candidate rescue parameters across deep negative C/N matrix."""
    global G_SCREEN_CASES, G_FULL_CASES
    full_p = make_full_params(rescue_params)
    lut_res = build_lut_from_params(full_p, is_rescue=True)
    
    # 1. Fast screen (3 cases: -3dB, -4.5dB, -6dB)
    screen_locks = []
    for c, clean_codes in G_SCREEN_CASES:
        raw_codes = u85q_lp.lp_codes(c['raw'], lut_res)
        lock, _ = pll_compare.score(c, sim.DAC_VOLTS[raw_codes], sim.DAC_VOLTS[clean_codes])
        screen_locks.append(lock)
        
    l_m3, l_m45, l_m6 = screen_locks[0], screen_locks[1], screen_locks[2]
    
    # Early reject if -3 dB or -4.5 dB degraded significantly
    if l_m3 < 0.88 or l_m45 < 0.70:
        score = (l_m3 * 1.0 + l_m45 * 2.0 + l_m6 * 4.0) * 0.5 - 10.0
        return score, l_m3, l_m45, l_m6, 0.0
        
    # 2. Full deep negative cases
    full_locks = []
    for c, clean_codes in G_FULL_CASES:
        raw_codes = u85q_lp.lp_codes(c['raw'], lut_res)
        lock, _ = pll_compare.score(c, sim.DAC_VOLTS[raw_codes], sim.DAC_VOLTS[clean_codes])
        full_locks.append(lock)
        
    # Combined metrics
    all_m3 = [l_m3] + [full_locks[i] for i in [0, 1]]
    all_m45 = [l_m45] + [full_locks[i] for i in [2, 3]]
    all_m6 = [l_m6] + [full_locks[i] for i in [4, 5]]
    l_m7 = full_locks[6]
    
    mean_m3 = float(np.mean(all_m3))
    mean_m45 = float(np.mean(all_m45))
    mean_m6 = float(np.mean(all_m6))
    
    # Fitness: Heavily reward lifting -6.0 dB and -7.0 dB while preserving -3.0 dB
    score = (mean_m3 * 1.5) + (mean_m45 * 2.5) + (mean_m6 * 6.0) + (l_m7 * 5.0)
    if mean_m3 < 0.92:
        score -= (0.92 - mean_m3) * 50.0
        
    return score, mean_m3, mean_m45, mean_m6, l_m7

def mutate_candidate(cand: Dict[str, float], temp: float = 0.15) -> Dict[str, float]:
    child = dict(cand)
    for k, (lo, hi, _) in RESCUE_PARAM_BOUNDS.items():
        if random.random() < 0.35:
            span = hi - lo
            delta = random.gauss(0, span * temp)
            child[k] = max(lo, min(hi, child[k] + delta))
    return child

def crossover(parent1: Dict[str, float], parent2: Dict[str, float]) -> Dict[str, float]:
    child = {}
    for k in RESCUE_PARAM_BOUNDS:
        child[k] = parent1[k] if random.random() < 0.50 else parent2[k]
    return child

def pregenerate_deep_dataset():
    print("Pre-generating Deep Negative C/N test matrix (-3 dB to -7 dB, physical folded ADC)...")
    u_lut = sim.u85q_lut()
    
    # Fast screen
    screen_specs = [
        (11, -3.0, 'PAL', 1.0),
        (11, -4.5, 'PAL', 1.0),
        (11, -6.0, 'PAL', 1.0),
    ]
    screen_cases = []
    for s, cnr, std, dev in screen_specs:
        c = sim.make_case(s, cnr, std, dev, filter_hz=12e6, fields=1, physical_adc=True, adc_gain=2.0)
        clean_codes = u85q_lp.lp_codes(c['clean'], u_lut)
        screen_cases.append((c, clean_codes))
        
    # Full deep specs
    full_specs = [
        (12, -3.0, 'NTSC', 0.7),
        (13, -3.0, 'PAL', 1.4),
        (14, -4.5, 'NTSC', 1.0),
        (15, -4.5, 'PAL', 1.7),
        (12, -6.0, 'NTSC', 0.7),
        (13, -6.0, 'PAL', 1.4),
        (11, -7.0, 'PAL', 1.0),
    ]
    full_cases = []
    for s, cnr, std, dev in full_specs:
        c = sim.make_case(s, cnr, std, dev, filter_hz=12e6, fields=1, physical_adc=True, adc_gain=2.0)
        clean_codes = u85q_lp.lp_codes(c['clean'], u_lut)
        full_cases.append((c, clean_codes))
        
    print(f"Deep C/N dataset ready: {len(screen_cases)} screening cases, {len(screen_cases)+len(full_cases)} total cases.")
    return screen_cases, full_cases

def apply_rescue_parameters(params: Dict[str, float]):
    """Applies optimal rescue parameters to research/c5map.py and regenerates tables."""
    import re
    c5map_py = HERE / "c5map.py"
    text = c5map_py.read_text(encoding="utf-8")
    for k, v in params.items():
        if k in ('v_sync_prior', 'v_video_prior', 'err_pos_bound', 'err_neg_bound', 'exit_sync_th', 'enter_sync_th'):
            val_str = f"{v/math.pi:.4f} * math.pi"
        else:
            val_str = f"{v:.4f}" if isinstance(v, float) else str(v)
        pattern = rf"('{k}':\s*)[^,]+(,?)"
        text = re.sub(pattern, rf"\g<1>{val_str}\g<2>", text)
    c5map_py.write_text(text, encoding="utf-8")
    print(f"Updated {c5map_py} with optimal rescue parameters.")
    import c5map
    c5map.generate_all()
    ok = c5map.check()
    print("BitScrambler check:", "PASSED" if ok else "FAILED")

def main():
    parser = argparse.ArgumentParser(description="C5MAP Deep Negative C/N Optimizer")
    parser.add_argument("--duration", type=int, default=900, help="Search duration in seconds (default: 900 for 15 min)")
    parser.add_argument("--workers", type=int, default=max(1, os.cpu_count() - 1), help="CPU cores")
    parser.add_argument("--pop-size", type=int, default=40, help="Population size")
    parser.add_argument("--apply", action="store_true", help="Apply best parameters when done")
    args = parser.parse_args()

    # Load baseline params from Stage 1
    base_file = HERE / "data" / "c5map_optimal.json"
    if base_file.exists():
        with open(base_file) as f:
            base_data = json.load(f)
        baseline_params = base_data['best_model']['params']
        print(f"Loaded Stage 1 champion parameters from {base_file}")
    else:
        from c5map import DEFAULT_PARAMS
        baseline_params = dict(DEFAULT_PARAMS)
        print("Using default params as baseline")

    screen_cases, full_cases = pregenerate_deep_dataset()

    # Initial seed from baseline
    initial_seed = {k: baseline_params[k] for k in RESCUE_PARAM_BOUNDS}
    population = [initial_seed]
    for _ in range(args.pop_size - 1):
        population.append(mutate_candidate(initial_seed, temp=0.20))

    generation = 0
    total_evals = 0
    best_score = -9999.0
    best_cand = copy.deepcopy(initial_seed)
    best_metrics = (0.0, 0.0, 0.0, 0.0)

    print(f"\nStarting Deep Negative C/N Evolutionary Optimizer ({args.duration}s target, {args.workers} workers)...")

    with Pool(processes=args.workers, initializer=init_worker, initargs=(screen_cases, full_cases, baseline_params)) as pool:
        start_time = time.time()
        try:
            while generation == 0 or time.time() - start_time < args.duration:
                gen_start = time.time()
                results = pool.map(evaluate_deep_candidate, population)
                gen_duration = time.time() - gen_start
                total_evals += len(population)
                generation += 1

                for cand, (score, m3, m45, m6, m7) in zip(population, results):
                    if score > best_score:
                        best_score = score
                        best_cand = copy.deepcopy(cand)
                        best_metrics = (m3, m45, m6, m7)
                        print(f"  [+] New Best [Gen {generation:03d}]: Score={best_score:.2f} | -3dB: {m3:.3f} | -4.5dB: {m45:.3f} | -6.0dB: {m6:.3f} | -7.0dB: {m7:.3f}")

                elapsed = time.time() - start_time
                print(f"[Gen {generation:03d} | Elapsed: {elapsed/60:.1f}m / {args.duration/60:.1f}m | {gen_duration:.1f}s/gen | Total Evals: {total_evals}]")
                print(f"  [*] Best Model: -3dB: {best_metrics[0]:.3f} | -4.5dB: {best_metrics[1]:.3f} | -6.0dB: {best_metrics[2]:.3f} | -7.0dB: {best_metrics[3]:.3f}")

                # Selection & Breeding
                ranked = sorted(zip(population, results), key=lambda x: x[1][0], reverse=True)
                survivors = [c for c, _ in ranked[:max(2, args.pop_size // 4)]]

                new_pop = [copy.deepcopy(best_cand)]
                for s in survivors:
                    new_pop.append(copy.deepcopy(s))

                while len(new_pop) < args.pop_size:
                    if random.random() < 0.40 and len(survivors) >= 2:
                        p1, p2 = random.sample(survivors, 2)
                        new_pop.append(mutate_candidate(crossover(p1, p2), temp=0.15))
                    else:
                        p = random.choice(survivors)
                        new_pop.append(mutate_candidate(p, temp=0.20))

                population = new_pop

        except KeyboardInterrupt:
            print("\nOptimization interrupted by user. Preserving best solution...")

    print(f"\nOptimization completed in {time.time() - start_time:.1f}s ({generation} generations, {total_evals} evaluations).")
    print(f"Final Champion: Score={best_score:.2f} | -3dB: {best_metrics[0]:.3f} | -4.5dB: {best_metrics[1]:.3f} | -6.0dB: {best_metrics[2]:.3f} | -7.0dB: {best_metrics[3]:.3f}")

    # Save checkpoint
    out_file = HERE / "data" / "c5map_deep_optimal.json"
    full_best = dict(baseline_params)
    full_best.update(best_cand)
    checkpoint = {
        'timestamp': time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        'generation': generation,
        'total_evals': total_evals,
        'elapsed_sec': time.time() - start_time,
        'overall_score': best_score,
        'best_metrics': {
            'lock_minus_3db': best_metrics[0],
            'lock_minus_4_5db': best_metrics[1],
            'lock_minus_6db': best_metrics[2],
            'lock_minus_7db': best_metrics[3],
        },
        'rescue_params': best_cand,
        'full_params': full_best,
    }
    with open(out_file, "w") as f:
        json.dump(checkpoint, f, indent=2)
    print(f"Saved optimal deep model to {out_file}")

    if args.apply:
        apply_rescue_parameters(best_cand)

if __name__ == "__main__":
    main()

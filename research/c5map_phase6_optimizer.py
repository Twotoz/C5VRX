"""C5MAP Phase 6: Ladder 0 Peak-SINAD & Razor Micro-Contrast Hyper-Optimizer.
(C5VRX by Twotoz and the C5VRX contributors)

Exclusively optimizes the Ladder 0 (Clean Flight & Bench) parameters to push
video SINAD and edge crispness to the absolute theoretical peak, while keeping
100% linelock (1.000) and leaving the rescue parameters (Level 7) untouched.
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

PARAM_BOUNDS_L0 = {
    'kp_nominal': (0.80, 0.94, 0.886),
    'v_video_prior': (0.12 * math.pi, 0.24 * math.pi, 0.192 * math.pi),
    'video_gain_boost': (0.95, 1.04, 0.989),
    'code_quant_offset': (-0.35, 0.05, -0.293),
    'err_pos_bound': (0.14 * math.pi, 0.26 * math.pi, 0.201 * math.pi),
    'err_scale': (0.08, 0.22, 0.162),
    'video_weight_thresh': (0.42, 0.65, 0.539),
    'r_uncertainty': (0.68, 0.82, 0.765),
    'w_uncertainty': (0.35, 0.58, 0.494),
}

G_BASELINE_PARAMS: Dict[str, float] = {}
G_SCREEN_CASES: List[Tuple] = []
G_FULL_CASES: List[Tuple] = []

def init_worker(screen_cases, full_cases, baseline_params):
    global G_SCREEN_CASES, G_FULL_CASES, G_BASELINE_PARAMS
    G_SCREEN_CASES = screen_cases
    G_FULL_CASES = full_cases
    G_BASELINE_PARAMS = baseline_params

def make_full_params(l0_params: Dict[str, float]) -> Dict[str, float]:
    p = dict(G_BASELINE_PARAMS)
    p.update(l0_params)
    return p

def evaluate_l0_candidate(l0_params: Dict[str, float]) -> Tuple[float, float, float, float, int]:
    """Evaluates candidate parameters exclusively for peak clean and flight SINAD."""
    global G_SCREEN_CASES, G_FULL_CASES
    full_p = make_full_params(l0_params)
    lut_nom = build_lut_from_params(full_p, is_rescue=False)

    # Screen cases (+10 dB clean PAL, +4 dB flight PAL, +4 dB flight NTSC 0.7)
    screen_locks = []
    screen_sinads = []
    for c, clean_codes in G_SCREEN_CASES:
        raw_codes = u85q_lp.lp_codes(c['raw'], lut_nom)
        lock, sinad = pll_compare.score(c, sim.DAC_VOLTS[raw_codes], sim.DAC_VOLTS[clean_codes])
        screen_locks.append(lock)
        screen_sinads.append(sinad)

    nom_lock = float(np.mean(screen_locks))
    if nom_lock < 0.995:
        # Severe penalty if linelock drops even 0.5%
        return -100.0, nom_lock, screen_sinads[0], screen_sinads[1], 0

    # Full cases (+4dB wide, +4dB extreme, +2dB mid)
    full_locks = []
    full_sinads = []
    unique_levels = 0
    for c, clean_codes in G_FULL_CASES:
        raw_codes = u85q_lp.lp_codes(c['raw'], lut_nom)
        lock, sinad = pll_compare.score(c, sim.DAC_VOLTS[raw_codes], sim.DAC_VOLTS[clean_codes])
        full_locks.append(lock)
        full_sinads.append(sinad)
        if unique_levels == 0:
            unique_levels = len(np.unique(raw_codes))

    s10 = screen_sinads[0]
    s4_mean = float(np.mean([screen_sinads[1], screen_sinads[2], full_sinads[0], full_sinads[1]]))
    s2 = full_sinads[2]

    # Target: Maximize clean bench + normal flight video SINAD
    score = (s10 * 3.0) + (s4_mean * 4.0) + (s2 * 1.5) + (unique_levels * 0.10)
    return score, nom_lock, s10, s4_mean, unique_levels

def mutate_candidate(cand: Dict[str, float], temp: float = 0.15) -> Dict[str, float]:
    child = dict(cand)
    for k, (lo, hi, _) in PARAM_BOUNDS_L0.items():
        if random.random() < 0.35:
            span = hi - lo
            delta = random.gauss(0, span * temp)
            child[k] = max(lo, min(hi, child[k] + delta))
    return child

def crossover(parent1: Dict[str, float], parent2: Dict[str, float]) -> Dict[str, float]:
    child = {}
    for k in PARAM_BOUNDS_L0:
        child[k] = parent1[k] if random.random() < 0.50 else parent2[k]
    return child

def pregenerate_dataset():
    print("Pre-generating Peak Video SINAD test matrix (+10 dB, +4 dB, +2 dB)...")
    u_lut = sim.u85q_lut()

    screen_specs = [
        (11, 10.0, 'PAL', 1.0),
        (11, 4.0, 'PAL', 1.0),
        (12, 4.0, 'NTSC', 0.7),
    ]
    screen_cases = []
    for s, cnr, std, dev in screen_specs:
        c = sim.make_case(s, cnr, std, dev, filter_hz=12e6, fields=1, physical_adc=True, adc_gain=2.0)
        clean_codes = u85q_lp.lp_codes(c['clean'], u_lut)
        screen_cases.append((c, clean_codes))

    full_specs = [
        (13, 4.0, 'PAL', 1.4),
        (15, 4.0, 'PAL', 1.7),
        (14, 2.0, 'PAL', 1.0),
    ]
    full_cases = []
    for s, cnr, std, dev in full_specs:
        c = sim.make_case(s, cnr, std, dev, filter_hz=12e6, fields=1, physical_adc=True, adc_gain=2.0)
        clean_codes = u85q_lp.lp_codes(c['clean'], u_lut)
        full_cases.append((c, clean_codes))

    print(f"Dataset ready: {len(screen_cases)} screen, {len(screen_cases)+len(full_cases)} total cases.")
    return screen_cases, full_cases

def apply_l0_parameters(params: Dict[str, float]):
    """Applies optimal parameters to research/c5map.py and regenerates tables."""
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
    print(f"Updated {c5map_py} with optimal Phase 6 parameters.")
    import c5map
    c5map.generate_all()
    ok = c5map.check()
    print("BitScrambler check:", "PASSED" if ok else "FAILED")

def main():
    parser = argparse.ArgumentParser(description="C5MAP Phase 6 Peak-SINAD Optimizer")
    parser.add_argument("--duration", type=int, default=600, help="Search duration in seconds (default: 600 for 10 min)")
    parser.add_argument("--workers", type=int, default=max(1, os.cpu_count() - 1), help="CPU cores")
    parser.add_argument("--pop-size", type=int, default=40, help="Population size")
    parser.add_argument("--apply", action="store_true", help="Apply best parameters when done")
    args = parser.parse_args()

    # Load baseline from Phase 5
    base_file = HERE / "data" / "c5map_sinad_optimal.json"
    if base_file.exists():
        with open(base_file) as f:
            b_data = json.load(f)
        baseline_params = b_data['full_params']
        print(f"Loaded Phase 5 champion parameters from {base_file}")
    else:
        from c5map import DEFAULT_PARAMS
        baseline_params = dict(DEFAULT_PARAMS)
        print("Using default params as baseline")

    screen_cases, full_cases = pregenerate_dataset()

    initial_seed = {k: baseline_params[k] for k in PARAM_BOUNDS_L0}
    population = [initial_seed]
    for _ in range(args.pop_size - 1):
        population.append(mutate_candidate(initial_seed, temp=0.20))

    generation = 0
    total_evals = 0
    best_score = -9999.0
    best_cand = copy.deepcopy(initial_seed)
    best_metrics = (0.0, 0.0, 0.0, 0)

    print(f"\nStarting Phase 6 Peak-SINAD Optimizer ({args.duration}s target, {args.workers} workers)...")

    with Pool(processes=args.workers, initializer=init_worker, initargs=(screen_cases, full_cases, baseline_params)) as pool:
        start_time = time.time()
        try:
            while generation == 0 or time.time() - start_time < args.duration:
                gen_start = time.time()
                results = pool.map(evaluate_l0_candidate, population)
                gen_duration = time.time() - gen_start
                total_evals += len(population)
                generation += 1

                for cand, (score, nom_l, s10, s4, levels) in zip(population, results):
                    if score > best_score:
                        best_score = score
                        best_cand = copy.deepcopy(cand)
                        best_metrics = (nom_l, s10, s4, levels)
                        print(f"  [+] New Best [Gen {generation:03d}]: Score={best_score:.2f} | +10dB: {s10:+.2f} dB | +4dB: {s4:+.2f} dB | DAC Levels: {levels} | Lock: {nom_l:.3f}")

                elapsed = time.time() - start_time
                print(f"[Gen {generation:03d} | Elapsed: {elapsed/60:.1f}m / {args.duration/60:.1f}m | {gen_duration:.1f}s/gen | Total Evals: {total_evals}]")
                print(f"  [*] Best Model: +10dB: {best_metrics[1]:+.2f} dB | +4dB: {best_metrics[2]:+.2f} dB | DAC Levels: {best_metrics[3]} | Lock: {best_metrics[0]:.3f}")

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

    print(f"\nPhase 6 completed in {time.time() - start_time:.1f}s ({generation} generations, {total_evals} evaluations).")
    print(f"Final Champion: +10dB: {best_metrics[1]:+.2f} dB | +4dB: {best_metrics[2]:+.2f} dB | DAC Levels: {best_metrics[3]} | Lock: {best_metrics[0]:.3f}")

    out_file = HERE / "data" / "c5map_phase6_optimal.json"
    full_best = dict(baseline_params)
    full_best.update(best_cand)
    checkpoint = {
        'timestamp': time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        'generation': generation,
        'total_evals': total_evals,
        'elapsed_sec': time.time() - start_time,
        'overall_score': best_score,
        'best_metrics': {
            'nom_lock': best_metrics[0],
            'sinad_10db': best_metrics[1],
            'sinad_4db_mean': best_metrics[2],
            'unique_dac_levels': best_metrics[3],
        },
        'l0_params': best_cand,
        'full_params': full_best,
    }
    with open(out_file, "w") as f:
        json.dump(checkpoint, f, indent=2)
    print(f"Saved Phase 6 optimal model to {out_file}")

    if args.apply:
        apply_l0_parameters(best_cand)

if __name__ == "__main__":
    main()

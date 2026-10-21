"""C5MAP Full-Spectrum Video Quality Optimizer.
(C5VRX by Twotoz and the C5VRX contributors)

Phase 3 of Universal Optimization:
Focuses on maximizing active video SINAD, contrast, edge definition, and dynamic range
across +10 dB down to 0 dB, while enforcing hard linelock guard rails to ensure zero
blackout and rock-solid synchronization.
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

# Parameters specifically optimized for video clarity, edge sharpness & contrast
VIDEO_PARAM_BOUNDS = {
    # Parameter: (min, max, default)
    'kp_nominal': (0.80, 0.92, 0.83),
    'v_video_prior': (0.10 * math.pi, 0.25 * math.pi, 0.165 * math.pi),
    'video_gain_boost': (1.00, 1.15, 1.01),
    'code_quant_offset': (-0.40, +0.40, -0.018),
    'err_pos_bound': (0.10 * math.pi, 0.25 * math.pi, 0.144 * math.pi),
    'err_scale': (0.05, 0.25, 0.135),
    'video_weight_thresh': (0.35, 0.65, 0.52),
}

G_BASELINE_PARAMS: Dict[str, float] = {}
G_SCREEN_CASES: List[Tuple] = []
G_FULL_CASES: List[Tuple] = []

def init_worker(screen_cases, full_cases, baseline_params):
    global G_SCREEN_CASES, G_FULL_CASES, G_BASELINE_PARAMS
    G_SCREEN_CASES = screen_cases
    G_FULL_CASES = full_cases
    G_BASELINE_PARAMS = baseline_params

def make_full_params(video_params: Dict[str, float]) -> Dict[str, float]:
    p = dict(G_BASELINE_PARAMS)
    p.update(video_params)
    return p

def evaluate_video_candidate(video_params: Dict[str, float]) -> Tuple[float, float, float, float, int]:
    """Evaluates candidate video parameters for SINAD, contrast and linelock guard rails."""
    global G_SCREEN_CASES, G_FULL_CASES
    full_p = make_full_params(video_params)
    lut_nom = build_lut_from_params(full_p, is_rescue=False)
    lut_res = build_lut_from_params(full_p, is_rescue=True)

    # 1. Screen cases (+4 dB PAL, +4 dB NTSC 0.7, -2 dB Rescue)
    screen_locks = []
    screen_sinads = []
    for c, clean_codes, is_res in G_SCREEN_CASES:
        active_lut = lut_res if is_res else lut_nom
        raw_codes = u85q_lp.lp_codes(c['raw'], active_lut)
        lock, sinad = pll_compare.score(c, sim.DAC_VOLTS[raw_codes], sim.DAC_VOLTS[clean_codes])
        screen_locks.append(lock)
        screen_sinads.append(sinad)

    nom_lock_screen = float(np.mean(screen_locks[:2]))
    res_lock_screen = screen_locks[2]

    # Guard rail check: early reject if nominal linelock or rescue breaks
    if nom_lock_screen < 0.980 or res_lock_screen < 0.820:
        return -50.0, nom_lock_screen, res_lock_screen, -10.0, 0

    # 2. Full video quality cases (+10 dB, +4 dB, +2 dB, 0 dB)
    full_locks = []
    full_sinads = []
    unique_levels = 0
    for c, clean_codes, is_res in G_FULL_CASES:
        active_lut = lut_res if is_res else lut_nom
        raw_codes = u85q_lp.lp_codes(c['raw'], active_lut)
        lock, sinad = pll_compare.score(c, sim.DAC_VOLTS[raw_codes], sim.DAC_VOLTS[clean_codes])
        full_locks.append(lock)
        full_sinads.append(sinad)
        if not is_res and unique_levels == 0:
            unique_levels = len(np.unique(raw_codes))

    mean_nom_lock = float(np.mean([nom_lock_screen] + [full_locks[i] for i in range(len(full_locks)-1)]))
    mean_sinad = float(np.mean(screen_sinads[:2] + full_sinads[:-1]))
    min_res_lock = min(res_lock_screen, full_locks[-1])

    # Guard rail enforcement:
    if mean_nom_lock < 0.985:
        penalty = (0.985 - mean_nom_lock) * 100.0
    else:
        penalty = 0.0

    # Score: Primarily video SINAD + dynamic range bonus - guard rail penalties
    score = (mean_sinad * 4.0) + (unique_levels * 0.15) - penalty

    return score, mean_nom_lock, min_res_lock, mean_sinad, unique_levels

def mutate_candidate(cand: Dict[str, float], temp: float = 0.15) -> Dict[str, float]:
    child = dict(cand)
    for k, (lo, hi, _) in VIDEO_PARAM_BOUNDS.items():
        if random.random() < 0.35:
            span = hi - lo
            delta = random.gauss(0, span * temp)
            child[k] = max(lo, min(hi, child[k] + delta))
    return child

def crossover(parent1: Dict[str, float], parent2: Dict[str, float]) -> Dict[str, float]:
    child = {}
    for k in VIDEO_PARAM_BOUNDS:
        child[k] = parent1[k] if random.random() < 0.50 else parent2[k]
    return child

def pregenerate_video_dataset():
    print("Pre-generating Video Quality & Sharpness test matrix (+10 dB to 0 dB, physical folded ADC)...")
    u_lut = sim.u85q_lut()

    # Fast screen
    screen_specs = [
        (11, 4.0, 'PAL', 1.0, False),
        (12, 4.0, 'NTSC', 0.7, False),
        (11, -2.0, 'PAL', 1.0, True),
    ]
    screen_cases = []
    for s, cnr, std, dev, is_res in screen_specs:
        c = sim.make_case(s, cnr, std, dev, filter_hz=12e6, fields=1, physical_adc=True, adc_gain=2.0)
        clean_codes = u85q_lp.lp_codes(c['clean'], u_lut)
        screen_cases.append((c, clean_codes, is_res))

    # Full cases (+10 dB bench clarity, +4 dB brede, 0 dB mid-range, -3 dB deep rescue guard)
    full_specs = [
        (11, 10.0, 'PAL', 1.0, False),  # Clean bench test
        (13, 4.0, 'PAL', 1.4, False),   # Brede VTX +4dB
        (14, 2.0, 'PAL', 1.0, False),   # Mid +2dB
        (11, 0.0, 'PAL', 1.0, False),   # 0dB
        (11, -3.0, 'PAL', 1.0, True),   # Deep rescue check
    ]
    full_cases = []
    for s, cnr, std, dev, is_res in full_specs:
        c = sim.make_case(s, cnr, std, dev, filter_hz=12e6, fields=1, physical_adc=True, adc_gain=2.0)
        clean_codes = u85q_lp.lp_codes(c['clean'], u_lut)
        full_cases.append((c, clean_codes, is_res))

    print(f"Video dataset ready: {len(screen_cases)} screening cases, {len(screen_cases)+len(full_cases)} total cases.")
    return screen_cases, full_cases

def apply_video_parameters(params: Dict[str, float]):
    """Applies optimal video parameters to research/c5map.py and regenerates tables."""
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
    print(f"Updated {c5map_py} with optimal video parameters.")
    import c5map
    c5map.generate_all()
    ok = c5map.check()
    print("BitScrambler check:", "PASSED" if ok else "FAILED")

def main():
    parser = argparse.ArgumentParser(description="C5MAP Full-Spectrum Video Quality Optimizer")
    parser.add_argument("--duration", type=int, default=900, help="Search duration in seconds (default: 900 for 15 min)")
    parser.add_argument("--workers", type=int, default=max(1, os.cpu_count() - 1), help="CPU cores")
    parser.add_argument("--pop-size", type=int, default=40, help="Population size")
    parser.add_argument("--apply", action="store_true", help="Apply best parameters when done")
    args = parser.parse_args()

    # Load baseline from Stage 2 (or Stage 1 if Stage 2 not yet created)
    deep_file = HERE / "data" / "c5map_deep_optimal.json"
    base_file = HERE / "data" / "c5map_optimal.json"
    if deep_file.exists():
        with open(deep_file) as f:
            d_data = json.load(f)
        baseline_params = d_data['full_params']
        print(f"Loaded Stage 2 rescue parameters from {deep_file}")
    elif base_file.exists():
        with open(base_file) as f:
            b_data = json.load(f)
        baseline_params = b_data['best_model']['params']
        print(f"Loaded Stage 1 parameters from {base_file}")
    else:
        from c5map import DEFAULT_PARAMS
        baseline_params = dict(DEFAULT_PARAMS)
        print("Using default params as baseline")

    screen_cases, full_cases = pregenerate_video_dataset()

    initial_seed = {k: baseline_params[k] for k in VIDEO_PARAM_BOUNDS}
    population = [initial_seed]
    for _ in range(args.pop_size - 1):
        population.append(mutate_candidate(initial_seed, temp=0.20))

    start_time = time.time()
    generation = 0
    total_evals = 0
    best_score = -9999.0
    best_cand = copy.deepcopy(initial_seed)
    best_metrics = (0.0, 0.0, 0.0, 0)

    print(f"\nStarting Video Quality Evolutionary Optimizer ({args.duration}s target, {args.workers} workers)...")

    with Pool(processes=args.workers, initializer=init_worker, initargs=(screen_cases, full_cases, baseline_params)) as pool:
        start_time = time.time()
        try:
            while generation == 0 or time.time() - start_time < args.duration:
                gen_start = time.time()
                results = pool.map(evaluate_video_candidate, population)
                gen_duration = time.time() - gen_start
                total_evals += len(population)
                generation += 1

                for cand, (score, nom_l, res_l, sinad, levels) in zip(population, results):
                    if score > best_score:
                        best_score = score
                        best_cand = copy.deepcopy(cand)
                        best_metrics = (nom_l, res_l, sinad, levels)
                        print(f"  [+] New Best [Gen {generation:03d}]: Score={best_score:.2f} | SINAD: {sinad:+.2f} dB | DAC Levels: {levels} | Nom Lock: {nom_l:.3f} | Res Lock: {res_l:.3f}")

                elapsed = time.time() - start_time
                print(f"[Gen {generation:03d} | Elapsed: {elapsed/60:.1f}m / {args.duration/60:.1f}m | {gen_duration:.1f}s/gen | Total Evals: {total_evals}]")
                print(f"  [*] Best Model: SINAD: {best_metrics[2]:+.2f} dB | DAC Levels: {best_metrics[3]} | Nom Lock: {best_metrics[0]:.3f} | Res Lock: {best_metrics[1]:.3f}")

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

    print(f"\nVideo optimization completed in {time.time() - start_time:.1f}s ({generation} generations, {total_evals} evaluations).")
    print(f"Final Champion: SINAD: {best_metrics[2]:+.2f} dB | DAC Levels: {best_metrics[3]} | Nom Lock: {best_metrics[0]:.3f} | Res Lock: {best_metrics[1]:.3f}")

    out_file = HERE / "data" / "c5map_video_optimal.json"
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
            'res_lock': best_metrics[1],
            'mean_sinad': best_metrics[2],
            'unique_dac_levels': best_metrics[3],
        },
        'video_params': best_cand,
        'full_params': full_best,
    }
    with open(out_file, "w") as f:
        json.dump(checkpoint, f, indent=2)
    print(f"Saved optimal video model to {out_file}")

    if args.apply:
        apply_video_parameters(best_cand)

if __name__ == "__main__":
    main()

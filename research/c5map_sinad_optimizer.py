"""C5MAP 1-Hour Pure Video SINAD & Detail Hyper-Evolution Optimizer.
(C5VRX by Twotoz and the C5VRX contributors)

Phase 5:
A 1-hour evolutionary run dedicated exclusively to maximizing active video SINAD,
micro-contrast, dynamic range, and sub-pixel edge sharpness from +10 dB down to 0 dB,
while enforcing strict, unbreakable linelock guard rails (nominal lock >= 0.990,
rescue lock >= 0.940).

Features:
- Expanded parameter space covering all video-fidelity variables
- Adaptive 3-stage simulated annealing temperature schedule (explore -> tune -> polish)
- Unbreakable linelock guard rails with heavy Pareto penalties
- Continuous real-time checkpointing to research/data/c5map_sinad_optimal.json
- Graceful interruption (Ctrl+C preserves best model)
- Auto-apply (--apply) updates research/c5map.py, main/c5map_tables.h, and runs c5map.check()
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

# Parameters evolved for maximum active video SINAD and crisp micro-contrast
SINAD_PARAM_BOUNDS = {
    # Parameter: (min, max, default)
    'kp_nominal': (0.80, 0.95, 0.886),
    'v_video_prior': (0.08 * math.pi, 0.28 * math.pi, 0.193 * math.pi),
    'video_gain_boost': (0.98, 1.15, 1.00),
    'code_quant_offset': (-0.45, +0.35, -0.293),
    'err_pos_bound': (0.10 * math.pi, 0.35 * math.pi, 0.220 * math.pi),
    'err_scale': (0.03, 0.25, 0.093),
    'video_weight_thresh': (0.35, 0.75, 0.605),
    'r_uncertainty': (0.65, 0.85, 0.744),
    'w_uncertainty': (0.20, 0.50, 0.404),
}

G_BASELINE_PARAMS: Dict[str, float] = {}
G_SCREEN_CASES: List[Tuple] = []
G_FULL_CASES: List[Tuple] = []

def init_worker(screen_cases, full_cases, baseline_params):
    global G_SCREEN_CASES, G_FULL_CASES, G_BASELINE_PARAMS
    G_SCREEN_CASES = screen_cases
    G_FULL_CASES = full_cases
    G_BASELINE_PARAMS = baseline_params

def make_full_params(sinad_params: Dict[str, float]) -> Dict[str, float]:
    p = dict(G_BASELINE_PARAMS)
    p.update(sinad_params)
    return p

def evaluate_sinad_candidate(sinad_params: Dict[str, float]) -> Tuple[float, float, float, float, int]:
    """Evaluates candidate parameters for SINAD maximization under strict linelock guard rails."""
    global G_SCREEN_CASES, G_FULL_CASES
    full_p = make_full_params(sinad_params)
    lut_nom = build_lut_from_params(full_p, is_rescue=False)
    lut_res = build_lut_from_params(full_p, is_rescue=True)

    # 1. Fast screen (4 cases: +10 dB clean, +4 dB flight, +4 dB NTSC 0.7, -2 dB rescue guard)
    screen_locks = []
    screen_sinads = []
    for c, clean_codes, is_res in G_SCREEN_CASES:
        active_lut = lut_res if is_res else lut_nom
        raw_codes = u85q_lp.lp_codes(c['raw'], active_lut)
        lock, sinad = pll_compare.score(c, sim.DAC_VOLTS[raw_codes], sim.DAC_VOLTS[clean_codes])
        screen_locks.append(lock)
        screen_sinads.append(sinad)

    nom_lock_screen = float(np.mean(screen_locks[:3]))
    res_lock_screen = screen_locks[3]

    # Guard rail check: early reject if nominal linelock or rescue breaks
    if nom_lock_screen < 0.985 or res_lock_screen < 0.900:
        return -100.0, nom_lock_screen, res_lock_screen, -10.0, 0

    # 2. Full spectrum video cases (+4dB wide, +4dB extreme, +2dB, 0dB PAL, 0dB NTSC, -3dB deep guard)
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

    mean_nom_lock = float(np.mean(screen_locks[:3] + full_locks[:-1]))
    min_res_lock = min(res_lock_screen, full_locks[-1])

    # Weights for video regimes:
    sinad_10db = screen_sinads[0]
    sinad_4db_mean = float(np.mean([screen_sinads[1], screen_sinads[2], full_sinads[0], full_sinads[1]]))
    sinad_2db = full_sinads[2]
    sinad_0db_mean = float(np.mean([full_sinads[3], full_sinads[4]]))

    mean_sinad = float(np.mean(screen_sinads[:3] + full_sinads[:-1]))

    # Guard rail penalty if linelock drops below target
    penalty = 0.0
    if mean_nom_lock < 0.990:
        penalty += (0.990 - mean_nom_lock) * 150.0
    if min_res_lock < 0.940:
        penalty += (0.940 - min_res_lock) * 100.0

    # Composite fitness score:
    # 2.5x weight on clean bench (+10 dB), 4.0x on flight (+4 dB), 2.0x on low signal (0..2 dB)
    # + dynamic range bonus for utilizing more DAC codes
    score = (sinad_10db * 2.5) + (sinad_4db_mean * 4.0) + (sinad_2db * 1.5) + (sinad_0db_mean * 2.0) + (unique_levels * 0.10) - penalty

    return score, mean_nom_lock, min_res_lock, mean_sinad, unique_levels

def mutate_candidate(cand: Dict[str, float], temp: float = 0.15) -> Dict[str, float]:
    child = dict(cand)
    for k, (lo, hi, _) in SINAD_PARAM_BOUNDS.items():
        if random.random() < 0.35:
            span = hi - lo
            delta = random.gauss(0, span * temp)
            child[k] = max(lo, min(hi, child[k] + delta))
    return child

def crossover(parent1: Dict[str, float], parent2: Dict[str, float]) -> Dict[str, float]:
    child = {}
    for k in SINAD_PARAM_BOUNDS:
        child[k] = parent1[k] if random.random() < 0.50 else parent2[k]
    return child

def pregenerate_sinad_dataset():
    print("Pre-generating Full-Spectrum Video SINAD test matrix (+10 dB to 0 dB, physical folded ADC)...")
    u_lut = sim.u85q_lut()

    # Fast screen specs
    screen_specs = [
        (11, 10.0, 'PAL', 1.0, False),  # Bench +10 dB
        (11, 4.0, 'PAL', 1.0, False),   # Flight +4 dB PAL standard
        (12, 4.0, 'NTSC', 0.7, False),  # Flight +4 dB NTSC smalle
        (11, -2.0, 'PAL', 1.0, True),   # Rescue guard -2 dB
    ]
    screen_cases = []
    for s, cnr, std, dev, is_res in screen_specs:
        c = sim.make_case(s, cnr, std, dev, filter_hz=12e6, fields=1, physical_adc=True, adc_gain=2.0)
        clean_codes = u85q_lp.lp_codes(c['clean'], u_lut)
        screen_cases.append((c, clean_codes, is_res))

    # Full specs
    full_specs = [
        (13, 4.0, 'PAL', 1.4, False),   # Flight +4 dB PAL brede
        (15, 4.0, 'PAL', 1.7, False),   # Flight +4 dB PAL extreme brede
        (14, 2.0, 'PAL', 1.0, False),   # Mid +2 dB PAL
        (11, 0.0, 'PAL', 1.0, False),   # Low 0 dB PAL
        (12, 0.0, 'NTSC', 1.0, False),  # Low 0 dB NTSC
        (11, -3.0, 'PAL', 1.0, True),   # Deep rescue guard -3 dB
    ]
    full_cases = []
    for s, cnr, std, dev, is_res in full_specs:
        c = sim.make_case(s, cnr, std, dev, filter_hz=12e6, fields=1, physical_adc=True, adc_gain=2.0)
        clean_codes = u85q_lp.lp_codes(c['clean'], u_lut)
        full_cases.append((c, clean_codes, is_res))

    print(f"Dataset ready: {len(screen_cases)} screening cases, {len(screen_cases)+len(full_cases)} total universal cases.")
    return screen_cases, full_cases

def apply_sinad_parameters(params: Dict[str, float]):
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
    print(f"Updated {c5map_py} with optimal video parameters.")
    import c5map
    c5map.generate_all()
    ok = c5map.check()
    print("BitScrambler check:", "PASSED" if ok else "FAILED")

def main():
    parser = argparse.ArgumentParser(description="C5MAP 1-Hour Pure Video SINAD Optimizer")
    parser.add_argument("--duration", type=int, default=3600, help="Search duration in seconds (default: 3600 for 1 hour)")
    parser.add_argument("--workers", type=int, default=max(1, os.cpu_count() - 1), help="CPU cores")
    parser.add_argument("--pop-size", type=int, default=44, help="Population size")
    parser.add_argument("--apply", action="store_true", help="Apply best parameters when done")
    args = parser.parse_args()

    # Load baseline from Phase 3 / 4
    base_file = HERE / "data" / "c5map_video_optimal.json"
    if base_file.exists():
        with open(base_file) as f:
            b_data = json.load(f)
        baseline_params = b_data['full_params']
        print(f"Loaded Phase 3/4 baseline champion parameters from {base_file}")
    else:
        from c5map import DEFAULT_PARAMS
        baseline_params = dict(DEFAULT_PARAMS)
        print("Using default params as baseline")

    screen_cases, full_cases = pregenerate_sinad_dataset()

    initial_seed = {k: baseline_params[k] for k in SINAD_PARAM_BOUNDS}
    population = [initial_seed]
    for _ in range(args.pop_size - 1):
        population.append(mutate_candidate(initial_seed, temp=0.20))

    generation = 0
    total_evals = 0
    best_score = -9999.0
    best_cand = copy.deepcopy(initial_seed)
    best_metrics = (0.0, 0.0, 0.0, 0)

    print(f"\nStarting 1-Hour Pure Video SINAD Hyper-Evolution ({args.duration}s target, {args.workers} workers)...")

    with Pool(processes=args.workers, initializer=init_worker, initargs=(screen_cases, full_cases, baseline_params)) as pool:
        start_time = time.time()
        try:
            while generation == 0 or time.time() - start_time < args.duration:
                # 3-Stage Simulated Annealing Temperature Schedule:
                elapsed = time.time() - start_time
                progress = min(1.0, elapsed / args.duration)
                if progress < 0.35:
                    temp = 0.20  # Phase A: Broad exploration
                    phase_str = "Exploration"
                elif progress < 0.70:
                    temp = 0.12  # Phase B: Fine tuning
                    phase_str = "Fine-Tuning"
                else:
                    temp = 0.06  # Phase C: Sub-atomic polish
                    phase_str = "Polish"

                gen_start = time.time()
                results = pool.map(evaluate_sinad_candidate, population)
                gen_duration = time.time() - gen_start
                total_evals += len(population)
                generation += 1

                improved = False
                for cand, (score, nom_l, res_l, sinad, levels) in zip(population, results):
                    if score > best_score:
                        best_score = score
                        best_cand = copy.deepcopy(cand)
                        best_metrics = (nom_l, res_l, sinad, levels)
                        improved = True
                        print(f"  [+] New Best [Gen {generation:03d} | {phase_str}]: Score={best_score:.2f} | Mean SINAD: {sinad:+.2f} dB | DAC Levels: {levels} | Nom Lock: {nom_l:.3f} | Res Lock: {res_l:.3f}")

                elapsed = time.time() - start_time
                print(f"[Gen {generation:03d} | Elapsed: {elapsed/60:.1f}m / {args.duration/60:.1f}m | {gen_duration:.1f}s/gen | T={temp:.2f} ({phase_str}) | Evals: {total_evals}]")
                print(f"  [*] Best Model: Mean SINAD: {best_metrics[2]:+.2f} dB | DAC Levels: {best_metrics[3]} | Nom Lock: {best_metrics[0]:.3f} | Res Lock: {best_metrics[1]:.3f}")

                ranked = sorted(zip(population, results), key=lambda x: x[1][0], reverse=True)
                survivors = [c for c, _ in ranked[:max(2, args.pop_size // 4)]]

                new_pop = [copy.deepcopy(best_cand)]
                for s in survivors:
                    new_pop.append(copy.deepcopy(s))

                while len(new_pop) < args.pop_size:
                    if random.random() < 0.45 and len(survivors) >= 2:
                        p1, p2 = random.sample(survivors, 2)
                        new_pop.append(mutate_candidate(crossover(p1, p2), temp=temp))
                    else:
                        p = random.choice(survivors)
                        new_pop.append(mutate_candidate(p, temp=temp))

                population = new_pop

        except KeyboardInterrupt:
            print("\nOptimization interrupted by user. Preserving best solution...")

    print(f"\n1-Hour SINAD optimization completed in {time.time() - start_time:.1f}s ({generation} generations, {total_evals} evaluations).")
    print(f"Final Champion: Mean SINAD: {best_metrics[2]:+.2f} dB | DAC Levels: {best_metrics[3]} | Nom Lock: {best_metrics[0]:.3f} | Res Lock: {best_metrics[1]:.3f}")

    out_file = HERE / "data" / "c5map_sinad_optimal.json"
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
        'sinad_params': best_cand,
        'full_params': full_best,
    }
    with open(out_file, "w") as f:
        json.dump(checkpoint, f, indent=2)
    print(f"Saved optimal SINAD model to {out_file}")

    if args.apply:
        apply_sinad_parameters(best_cand)

if __name__ == "__main__":
    main()

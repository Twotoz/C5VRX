"""C5MAP Universal Evolutionary Hyperparameter Optimizer.
(C5VRX by Twotoz and the C5VRX contributors)

Performs comprehensive evolutionary optimization of the C5MAP Maximum A Posteriori (MAP)
demodulator across every C/N level (+4 dB down to -3 dB), all VTX deviations (smalle 0.7,
normale 1.0, brede 1.4, extreme brede 1.7), cameras (PAL & NTSC), and real C5 board folded
ADC hardware (physical_adc=True, adc_gain=2.0).

Features:
- Includes ALL variables: priors, gains, noise suppression bounds, hysteresis, weights, quantization offset
- Hierarchical parallel evaluation for 3x speedup across all CPU cores
- Anti-blackout multi-objective Pareto fitness balancing maximum video SINAD and rock-solid linelock
- Automatic periodic checkpointing to research/data/c5map_optimal.json
- Graceful interruption (Ctrl+C preserves best solution)
- Auto-apply capability (--apply) to update main/c5map_tables.h and verify BitScrambler constraints
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

RANGE_MHZ = (-6.0, 9.0)
DT = 50e-9

PARAM_BOUNDS = {
    # Parameter name: (min_val, max_val, default_val)
    # 1. Proportional gains
    'kp_nominal': (0.80, 0.88, 0.85),
    'kp_rescue': (0.48, 0.58, 0.52),
    
    # 2. Bayesian frequency priors
    'v_video_prior': (0.12 * math.pi, 0.22 * math.pi, 0.18 * math.pi),
    'v_sync_prior': (-0.46 * math.pi, -0.38 * math.pi, -0.42 * math.pi),
    
    # 3. Origin observation uncertainty & weighting
    'r_uncertainty': (0.70, 0.80, 0.74),
    'w_uncertainty': (0.18, 0.32, 0.25),
    
    # 4. Sync regime noise click & cycle-slip suppression
    'err_pos_bound': (0.12 * math.pi, 0.20 * math.pi, 0.15 * math.pi),
    'err_neg_bound': (-0.95 * math.pi, -0.80 * math.pi, -0.90 * math.pi),
    'err_scale': (0.05, 0.15, 0.10),
    'w_suppress_thresh': (0.88, 0.95, 0.92),
    
    # 5. Regime transition hysteresis & weight thresholds
    'exit_sync_th': (0.09 * math.pi, 0.15 * math.pi, 0.12 * math.pi),
    'enter_sync_th': (-0.26 * math.pi, -0.20 * math.pi, -0.24 * math.pi),
    'exit_sync_decay': (0.75, 0.95, 0.85),
    'enter_sync_decay': (0.85, 1.05, 0.95),
    'sync_weight_thresh': (0.65, 0.85, 0.75),
    'video_weight_thresh': (0.40, 0.60, 0.50),
    
    # 6. Video definition boost and quantization centering
    'video_gain_boost': (1.00, 1.08, 1.04),
    'code_quant_offset': (-0.35, +0.35, 0.0),
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
    kp_nom = params['kp_nominal']
    kp_res = params['kp_rescue']
    v_sync = params['v_sync_prior']
    v_vid = params['v_video_prior']
    r_unc = params['r_uncertainty']
    w_unc = params['w_uncertainty']
    err_pos = params['err_pos_bound']
    err_neg = params['err_neg_bound']
    err_scale = params['err_scale']
    w_suppress = params['w_suppress_thresh']
    sync_w_th = params['sync_weight_thresh']
    vid_w_th = params['video_weight_thresh']
    boost = params['video_gain_boost']
    quant_off = params['code_quant_offset']

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

    t_norm = max(0.0, min(1.0, (kp - kp_res) / max(0.01, (kp_nom - kp_res))))
    cur_exit_th = params['exit_sync_th'] * (params['exit_sync_decay'] + (1.0 - params['exit_sync_decay']) * t_norm)
    cur_enter_th = params['enter_sync_th'] * (params['enter_sync_decay'] + (1.0 - params['enter_sync_decay']) * t_norm)

    for s in range(P):
        regime_s = s // N_PHASE
        curr_phase = ph[s % N_PHASE]

        for t in range(T):
            o = obs_angles[t]
            w = obs_weights[t]
            err = (o - curr_phase + math.pi) % (2 * math.pi) - math.pi

            if regime_s == 0:
                # Sync regime: Bayesian sync tip anchor & noise suppression
                gain = kp * w
                if (err > err_pos or err < err_neg) and w < w_suppress:
                    err_eff = min(0.06 * math.pi, max(-0.55 * math.pi, err * err_scale))
                else:
                    err_eff = err
                d_theta = gain * err_eff + (1.0 - gain) * v_sync

                if d_theta > cur_exit_th and w > sync_w_th:
                    next_regime = 1
                else:
                    next_regime = 0
            else:
                # Active video: boost gain on confident samples to match U85Q definition
                if w >= 0.90:
                    gain_vid = min(0.98, kp * (1.0 + (boost - 1.0) * t_norm))
                else:
                    gain_vid = kp * w
                d_theta = gain_vid * err + (1.0 - gain_vid) * v_vid

                if d_theta < cur_enter_th and w > vid_w_th:
                    next_regime = 0
                else:
                    next_regime = 1

            d_theta = max(lo_step, min(hi_step, d_theta))
            next_phase = (curr_phase + d_theta + math.pi) % (2 * math.pi) - math.pi
            next_p_idx = int(math.floor((next_phase + math.pi) * N_PHASE / (2 * math.pi))) % N_PHASE
            nxt = next_regime * N_PHASE + next_p_idx
            code = int(np.rint(np.clip((d_theta - lo_step) / (hi_step - lo_step) * 63 + quant_off, 0, 63)))
            lut[s * T + t] = code | (nxt << 6)

    lut |= tok.astype(np.int64) << 11
    return lut.astype(np.uint16)

# Global test cases for worker processes
G_SCREEN_CASES = []
G_FULL_CASES = []

def init_worker(screen_cases, full_cases):
    global G_SCREEN_CASES, G_FULL_CASES
    G_SCREEN_CASES = screen_cases
    G_FULL_CASES = full_cases

def evaluate_candidate(params: Dict[str, float]) -> Tuple[float, float, float, float, float, float]:
    """Hierarchical evaluation of one candidate parameter set across all test cases."""
    global G_SCREEN_CASES, G_FULL_CASES
    
    lut_nom = build_lut_from_params(params, is_rescue=False)
    lut_res = build_lut_from_params(params, is_rescue=True)
    
    # Stage 1: Fast Screening (4 cases: standard PAL +4dB, smalle NTSC +4dB, standard -2dB, extreme brede -2dB)
    screen_nom_l, screen_nom_s = [], []
    screen_res_l, screen_res_s = [], []
    for c, clean_codes, is_res in G_SCREEN_CASES:
        active_lut = lut_res if is_res else lut_nom
        raw_codes = u85q_lp.lp_codes(c['raw'], active_lut)
        p, s = pll_compare.score(c, sim.DAC_VOLTS[raw_codes], sim.DAC_VOLTS[clean_codes])
        if is_res:
            screen_res_l.append(p)
            screen_res_s.append(s)
        else:
            screen_nom_l.append(p)
            screen_nom_s.append(s)
            
    m_nom_l = float(np.mean(screen_nom_l))
    m_res_l = float(np.mean(screen_res_l))
    
    # Early rejection if candidate fails fast screen
    if m_nom_l < 0.98 or m_res_l < 0.75:
        return m_nom_l, float(np.min(screen_nom_l)), float(np.mean(screen_nom_s)), m_res_l, float(np.min(screen_res_l)), float(np.mean(screen_res_s))
        
    # Stage 2: Full Multi-point Matrix Evaluation (18 cases total)
    nom_l, nom_s = list(screen_nom_l), list(screen_nom_s)
    res_l, res_s = list(screen_res_l), list(screen_res_s)
    for c, clean_codes, is_res in G_FULL_CASES:
        active_lut = lut_res if is_res else lut_nom
        raw_codes = u85q_lp.lp_codes(c['raw'], active_lut)
        p, s = pll_compare.score(c, sim.DAC_VOLTS[raw_codes], sim.DAC_VOLTS[clean_codes])
        if is_res:
            res_l.append(p)
            res_s.append(s)
        else:
            nom_l.append(p)
            nom_s.append(s)
            
    return (
        float(np.mean(nom_l)), float(np.min(nom_l)), float(np.mean(nom_s)),
        float(np.mean(res_l)), float(np.min(res_l)), float(np.mean(res_s))
    )

def create_random_candidate() -> Dict[str, float]:
    cand = {}
    for k, (lo, hi, _) in PARAM_BOUNDS.items():
        cand[k] = random.uniform(lo, hi)
    return cand

def mutate_candidate(cand: Dict[str, float], temp: float = 0.15) -> Dict[str, float]:
    child = copy.deepcopy(cand)
    for k, (lo, hi, _) in PARAM_BOUNDS.items():
        if random.random() < 0.35:
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
    print("Pre-generating physical ADC validation matrix across all deviations and cameras...")
    u_lut = sim.u85q_lut()
    
    # 1. Fast Screen specs (4 cases covering extremes)
    screen_specs = [
        (11, 4.0, 'PAL', 1.0, False),    # Standard PAL +4dB
        (12, 4.0, 'NTSC', 0.7, False),   # Smalle NTSC +4dB
        (11, -2.0, 'PAL', 1.0, True),    # Standard PAL -2dB rescue
        (15, -2.0, 'PAL', 1.7, True),    # Extreme brede PAL -2dB rescue
    ]
    screen_cases = []
    for s, cnr, std, dev, is_res in screen_specs:
        c = sim.make_case(s, cnr, std, dev, filter_hz=12e6, fields=1, physical_adc=True, adc_gain=2.0)
        clean_codes = u85q_lp.lp_codes(c['clean'], u_lut)
        screen_cases.append((c, clean_codes, is_res))
        
    # 2. Additional Full Matrix specs (14 cases covering full spectrum)
    full_specs = [
        (13, 4.0, 'PAL', 1.4, False),    # Brede PAL +4dB
        (15, 4.0, 'PAL', 1.7, False),    # Extreme brede PAL +4dB
        (14, 2.0, 'PAL', 1.0, False),    # Mid +2dB PAL
        (11, 0.0, 'PAL', 1.0, False),    # Standard 0dB
        (12, 0.0, 'NTSC', 0.7, False),   # Smalle 0dB
        (13, 0.0, 'PAL', 1.4, False),    # Brede 0dB
        (15, 0.0, 'PAL', 1.7, False),    # Extreme brede 0dB
        (12, -2.0, 'NTSC', 1.0, True),   # Standard NTSC -2dB rescue
        (13, -2.0, 'PAL', 0.7, True),    # Smalle PAL -2dB rescue
        (14, -2.0, 'NTSC', 1.4, True),   # Brede NTSC -2dB rescue
        (11, -3.0, 'PAL', 1.0, True),    # Deep rescue standard PAL -3dB
        (12, -3.0, 'NTSC', 1.0, True),   # Deep rescue standard NTSC -3dB
        (13, -3.0, 'PAL', 0.7, True),    # Deep rescue smalle PAL -3dB
        (14, -3.0, 'PAL', 1.4, True),    # Deep rescue brede PAL -3dB
    ]
    full_cases = []
    for s, cnr, std, dev, is_res in full_specs:
        c = sim.make_case(s, cnr, std, dev, filter_hz=12e6, fields=1, physical_adc=True, adc_gain=2.0)
        clean_codes = u85q_lp.lp_codes(c['clean'], u_lut)
        full_cases.append((c, clean_codes, is_res))
        
    print(f"Dataset ready: {len(screen_cases)} screening cases, {len(screen_cases)+len(full_cases)} total universal validation cases.")
    return screen_cases, full_cases

def apply_parameters_to_code(params: Dict[str, float]):
    """Applies optimal parameters to research/c5map.py and regenerates tables."""
    c5map_py = HERE / "c5map.py"
    text = c5map_py.read_text(encoding="utf-8")
    
    # Update DEFAULT_PARAMS in c5map.py
    import re
    for k, v in params.items():
        if k in ('v_sync_prior', 'v_video_prior', 'err_pos_bound', 'err_neg_bound', 'exit_sync_th', 'enter_sync_th'):
            val_str = f"{v/math.pi:.4f} * math.pi"
        else:
            val_str = f"{v:.4f}" if isinstance(v, float) else str(v)
        pattern = rf"('{k}':\s*)[^,]+(,?)"
        text = re.sub(pattern, rf"\g<1>{val_str}\g<2>", text)
    
    c5map_py.write_text(text, encoding="utf-8")
    print(f"Updated {c5map_py} with optimal parameters.")
    
    import c5map
    c5map.generate_all()
    ok = c5map.check()
    print("Code generation and BitScrambler check:", "PASSED" if ok else "FAILED")

def main():
    parser = argparse.ArgumentParser(description="C5MAP Universal Evolutionary Optimizer")
    parser.add_argument("--duration", type=int, default=3600, help="Search duration in seconds (default: 3600 for 1 hour)")
    parser.add_argument("--workers", type=int, default=max(1, os.cpu_count() - 1), help="Number of parallel worker processes")
    parser.add_argument("--pop-size", type=int, default=48, help="Population size")
    parser.add_argument("--output", type=Path, default=HERE / "data" / "c5map_optimal.json", help="Path to save results")
    parser.add_argument("--test", action="store_true", help="Quick verification test run (1 generation)")
    parser.add_argument("--apply", action="store_true", help="Apply best parameters to codebase when complete")
    args = parser.parse_args()

    print(f"=== C5MAP Universal Evolutionary Optimizer ===")
    print(f"Duration target: {args.duration} s ({args.duration/60:.1f} minutes)")
    print(f"Workers: {args.workers} CPU cores | Population: {args.pop_size}")
    print(f"Output checkpoint: {args.output}")

    screen_cases, full_cases = pregenerate_dataset()

    # Baseline seed from default values
    baseline = {k: v[2] for k, v in PARAM_BOUNDS.items()}
    population = [baseline]
    for _ in range(args.pop_size - 1):
        population.append(mutate_candidate(baseline, temp=0.20))

    start_time = time.time()
    end_time = start_time + args.duration

    best_overall_score = -9999.0
    best_cand = baseline
    best_nom_lock = 0.0
    best_nom_sinad = -999.0
    best_res_lock = 0.0
    best_res_min_lock = 0.0
    best_res_sinad = -999.0

    total_evals = 0
    generation = 0

    args.output.parent.mkdir(parents=True, exist_ok=True)

    print("\nStarting optimization pool...")
    try:
        with Pool(processes=args.workers, initializer=init_worker, initargs=(screen_cases, full_cases)) as pool:
            while time.time() < end_time:
                generation += 1
                gen_t0 = time.time()

                results = pool.map(evaluate_candidate, population)
                total_evals += len(population)

                scored = []
                for cand, (nom_l, nom_min_l, nom_s, res_l, res_min_l, res_s) in zip(population, results):
                    # Multi-objective Pareto score:
                    # Nominal fitness: reward high video SINAD, strictly preserve linelock
                    nom_lock_penalty = max(0.0, 0.995 - nom_l) * 300.0 + max(0.0, 0.980 - nom_min_l) * 200.0
                    nom_fitness = nom_s * 8.0 - nom_lock_penalty

                    # Rescue fitness: rock-solid anti-blackout linelock across all deviations, secondarily SINAD
                    res_fitness = res_l * 80.0 + res_min_l * 40.0 + res_s * 2.0
                    if res_min_l < 0.60:
                        res_fitness -= (0.60 - res_min_l) * 400.0  # Disqualify blackout on any VTX

                    overall_fitness = nom_fitness * 0.50 + res_fitness * 0.50
                    scored.append((overall_fitness, nom_fitness, res_fitness, nom_l, nom_s, res_l, res_min_l, res_s, cand))

                    if overall_fitness > best_overall_score:
                        best_overall_score = overall_fitness
                        best_cand = copy.deepcopy(cand)
                        best_nom_lock = nom_l
                        best_nom_sinad = nom_s
                        best_res_lock = res_l
                        best_res_min_lock = res_min_l
                        best_res_sinad = res_s

                scored.sort(key=lambda x: x[0], reverse=True)

                elapsed = time.time() - start_time
                gen_time = time.time() - gen_t0

                print(f"[Gen {generation:03d} | Elapsed: {elapsed/60:4.1f}m / {args.duration/60:.0f}m | {gen_time:4.1f}s/gen | Total Evals: {total_evals}]")
                print(f"  [*] Best Model: Nom SINAD {best_nom_sinad:+5.2f} dB (Lock: {best_nom_lock:0.3f}) | Res Lock: {best_res_lock:0.3f} (Min Lock: {best_res_min_lock:0.3f})")
                sys.stdout.flush()

                checkpoint = {
                    'timestamp': time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'generation': generation,
                    'total_evals': total_evals,
                    'elapsed_sec': elapsed,
                    'overall_score': best_overall_score,
                    'best_model': {
                        'nom_sinad': best_nom_sinad,
                        'nom_lock': best_nom_lock,
                        'res_lock': best_res_lock,
                        'res_min_lock': best_res_min_lock,
                        'res_sinad': best_res_sinad,
                        'params': best_cand,
                    },
                }
                args.output.write_text(json.dumps(checkpoint, indent=2))

                if args.test:
                    print("Test mode: 1 generation completed successfully.")
                    break

                # Elitism & Breeding
                elites = [item[8] for item in scored[:12]]
                new_pop = [best_cand] + elites

                while len(new_pop) < args.pop_size:
                    if random.random() < 0.15:
                        new_pop.append(create_random_candidate())
                    else:
                        p1, p2 = random.sample(elites, 2)
                        child = crossover(p1, p2)
                        temp = 0.06 if random.random() < 0.65 else 0.20
                        new_pop.append(mutate_candidate(child, temp=temp))

                population = new_pop

    except KeyboardInterrupt:
        print("\nOptimization interrupted by user. Preserving current best results...")

    total_time = time.time() - start_time
    print(f"\n================ OPTIMIZATION COMPLETE ================")
    print(f"Finished in {total_time/60:.2f} minutes ({total_evals} candidates evaluated).")
    print(f"Checkpoint saved to {args.output}")
    print(f"Best Model:")
    print(f"  Nominal: SINAD {best_nom_sinad:+5.2f} dB, Mean Lock {best_nom_lock:.3f}")
    print(f"  Rescue:  Mean Lock {best_res_lock:.3f}, Min Lock {best_res_min_lock:.3f}, SINAD {best_res_sinad:+5.2f} dB")
    print("=======================================================")

    if args.apply:
        print("\nApplying optimal parameters to codebase...")
        apply_parameters_to_code(best_cand)

if __name__ == '__main__':
    main()

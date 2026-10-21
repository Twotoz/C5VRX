"""Benchmark U85Q + Level 7 Rescue Table vs Standard Main across seeds.
Tests linelock and SINAD under physical ADC gain across C/N +10 to -3 dB.
"""
import sys
import numpy as np
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import sim
from kp_morph import build_lut, build_rescue_lut
from u85q_lp import lp_codes

def benchmark():
    cn_levels = [10.0, 4.0, 2.0, 1.0, 0.0, -1.0, -2.0, -3.0]
    seeds = [1, 2, 3, 4]  # alternating PAL and NTSC
    
    lut_std_085 = build_lut(0.85)
    lut_std_050 = build_lut(0.50)
    tok_std = lut_std_085 >> 11
    lut_rescue = build_rescue_lut(tok_std)
    
    results = {}
    for cn in cn_levels:
        results[cn] = {
            'main_u85q_085_lock': [],
            'main_u85q_050_lock': [],
            'main_lp_085_lock': [],
            'main_lp_050_lock': [],
            'rescue_u85q_lock': [],
            'rescue_lp_lock': [],
            'main_u85q_085_sinad': [],
            'rescue_u85q_sinad': [],
        }
        for seed in seeds:
            std = 'PAL' if seed % 2 else 'NTSC'
            c = sim.make_case(seed, cn, standard=std, deviation=1.0, filter_hz=12e6, fields=1, physical_adc=True, adc_gain=2.0)
            
            # 1. Main standard U85Q kp 0.85
            raw_main_085 = sim.u85q_codes(c['raw'], lut_std_085)
            clean_main_085 = sim.u85q_codes(c['clean'], lut_std_085)
            m_main_085 = sim.measure(c, raw_main_085, clean_main_085)
            
            # 2. Main standard U85Q kp 0.50
            raw_main_050 = sim.u85q_codes(c['raw'], lut_std_050)
            clean_main_050 = sim.u85q_codes(c['clean'], lut_std_050)
            m_main_050 = sim.measure(c, raw_main_050, clean_main_050)

            # 3. Main LP kp 0.85
            lp_main_085 = lp_codes(c['raw'], lut_std_085)
            lp_clean_085 = lp_codes(c['clean'], lut_std_085)
            m_lp_085 = sim.measure(c, lp_main_085, lp_clean_085)

            # 4. Main LP kp 0.50
            lp_main_050 = lp_codes(c['raw'], lut_std_050)
            lp_clean_050 = lp_codes(c['clean'], lut_std_050)
            m_lp_050 = sim.measure(c, lp_main_050, lp_clean_050)

            # 5. Rescue Table Raw U85Q
            raw_rescue = sim.u85q_codes(c['raw'], lut_rescue)
            clean_rescue = sim.u85q_codes(c['clean'], lut_rescue)
            m_rescue_u85q = sim.measure(c, raw_rescue, clean_rescue)

            # 6. Rescue Table LP
            lp_rescue = lp_codes(c['raw'], lut_rescue)
            lp_clean_rescue = lp_codes(c['clean'], lut_rescue)
            m_rescue_lp = sim.measure(c, lp_rescue, lp_clean_rescue)

            results[cn]['main_u85q_085_lock'].append(m_main_085['picture'])
            results[cn]['main_u85q_050_lock'].append(m_main_050['picture'])
            results[cn]['main_lp_085_lock'].append(m_lp_085['picture'])
            results[cn]['main_lp_050_lock'].append(m_lp_050['picture'])
            results[cn]['rescue_u85q_lock'].append(m_rescue_u85q['picture'])
            results[cn]['rescue_lp_lock'].append(m_rescue_lp['picture'])

            results[cn]['main_u85q_085_sinad'].append(m_main_085['sinad'])
            results[cn]['rescue_u85q_sinad'].append(m_rescue_u85q['sinad'])

    print("\n### Multi-Seed Linelock Benchmark (Seeds 1..4, PAL+NTSC, dev=1.0, physical_adc=True, adc_gain=2.0)")
    print("| C/N (dB) | Main U85Q (kp 0.85) | Main U85Q (kp 0.50) | Main LP (kp 0.85) | Level 7 RESCUE (Raw U85Q) | Level 7 RESCUE (LP) |")
    print("| :---: | :---: | :---: | :---: | :---: | :---: |")
    for cn in cn_levels:
        r = results[cn]
        print(f"| {cn:+5.1f} dB | {np.mean(r['main_u85q_085_lock']):.3f} | {np.mean(r['main_u85q_050_lock']):.3f} | {np.mean(r['main_lp_085_lock']):.3f} | **{np.mean(r['rescue_u85q_lock']):.3f}** | **{np.mean(r['rescue_lp_lock']):.3f}** |")

if __name__ == '__main__':
    benchmark()

"""C5VRX/Twotoz: U55P64-K1 versus U80P64-R1, coloured dots and lock; host only.

Seed 2903 (the K1 parameters were searched on seeds 311/991). Two inputs:

- flat colour patches (six hues, 50 IRE luma, 20 IRE chroma) at 40/12 dB
  C/N: coloured dots (per mille of patch samples whose demodulated chroma,
  1 MHz around the subcarrier, deviates > 10 IRE from the patch mean),
  colour grain SNR (that fluctuation against the true chroma power) and
  colour saturation (patch-mean chroma amplitude / true amplitude);
- the normal textured raster at 30/4/0 dB C/N: goggle-model lock and SINAD.

PAL/NTSC, deviation 0.7/1.4/1.7 (1.7 with the 16 MHz filter). These are
proxies, not a goggle colour decoder (no burst PLL, chroma AGC or killer).
Run with --check in CI; --write refreshes research/data/p64_k1_validation.json.
"""
import argparse
import json
from pathlib import Path

import numpy as np
from scipy import signal as sg

import sim
import p5t_eval
import p64_model
import p64_k1

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "research/data/p64_k1_validation.json"
FS = sim.FS
SIX = np.array([sum(1 / r for b, r in enumerate((8200, 3900, 2000, 1000, 470, 240)) if v & (1 << b))
                for v in range(64)])
CHROMA_LP = sg.butter(2, 1.0e6, fs=FS, output="sos")


def patch_raster(standard, seed, fields=2):
    ire, region = sim._raster(standard, seed, fields, False)
    pal = standard == "PAL"
    fsc = 4433618.75 if pal else 3579545.454545
    picture = np.flatnonzero(region == 1)
    breaks = np.flatnonzero(np.diff(picture) > 1)
    starts = np.r_[picture[0], picture[breaks + 1]]
    ends = np.r_[picture[breaks], picture[-1]] + 1
    for line, (a, b) in enumerate(zip(starts, ends)):
        n = b - a
        hue = np.minimum((np.arange(n) * 6) // n, 5) * np.pi / 3
        ire[a:b] = 50 + 20 * np.sin(2 * np.pi * fsc * np.arange(a, b) / FS + ((-1) ** line if pal else 1) * hue)
    return ire, region


def colour(case, codes, clean_codes):
    truth, region = case["truth"], case["region"]
    lag, g, off = p5t_eval.calibrate_nb(sg.sosfilt(sim.GOGGLE, SIX[clean_codes]), truth, region, p5t_eval.BURN)
    y = p5t_eval.apply_cal_nb(sg.sosfilt(sim.GOGGLE, SIX[codes]), lag, g, off)
    fsc = 4433618.75 if case["standard"] == "PAL" else 3579545.454545
    n = min(len(y), len(truth))
    ref = np.exp(-2j * np.pi * fsc * np.arange(n) / FS)
    c_out = sg.sosfiltfilt(CHROMA_LP, y[:n] * ref) * 2
    c_true = sg.sosfiltfilt(CHROMA_LP, truth[:n] * ref) * 2
    core = np.convolve((region[:n] == 1).astype(int), np.ones(81, int), mode="same") == 81
    core[:p5t_eval.BURN] = False
    core[n - p5t_eval.BURN:] = False
    edge = np.diff(np.r_[0, core.astype(int), 0])
    dev, sat, signal = [], [], 0.0
    for a, b in zip(np.flatnonzero(edge == 1), np.flatnonzero(edge == -1)):
        cuts = np.linspace(a, b, 7).astype(int)
        for p0, p1 in zip(cuts[:-1] + 20, cuts[1:] - 20):
            if p1 - p0 < 20:
                continue
            err = c_out[p0:p1] - c_true[p0:p1]
            dev.append(np.abs(err - err.mean()))
            sat.append(abs(c_out[p0:p1].mean()) / abs(c_true[p0:p1].mean()))
            signal += float(np.sum(np.abs(c_true[p0:p1]) ** 2))
    dev = np.concatenate(dev)
    return dict(dots=float(np.mean(dev > 10.0) * 1e3), grain=float(10 * np.log10(signal / np.sum(dev ** 2))),
                saturation=float(np.mean(sat)))


def run():
    models = (("U80P64-R1", p64_model.repaired_table()), ("U55P64-K1", p64_k1.table()))
    codes = lambda raw, t: sim._pair_codes(raw, t, sim.PAIR_BITS, 4)
    results = []
    textured = sim.raster
    for standard in ("PAL", "NTSC"):
        for deviation, filter_hz in ((0.7, 12e6), (1.4, 12e6), (1.7, 16e6)):
            sim.raster = patch_raster
            for cn in (40.0, 12.0):
                case = sim.make_case(2903, cn, standard, deviation, filter_hz=filter_hz, fields=1,
                                     physical_adc=True, adc_gain=3.0)
                scores = {name: {k: round(v, 4) for k, v in colour(case, codes(case["raw"], t), codes(case["clean"], t)).items()}
                          for name, t in models}
                results.append(dict(input="patches", standard=standard, dev=deviation, cn=cn, scores=scores))
            sim.raster = textured
            for cn in (30.0, 4.0, 0.0):
                case = sim.make_case(2903, cn, standard, deviation, filter_hz=filter_hz, fields=1,
                                     physical_adc=True, adc_gain=3.0)
                case["hs0"] = p5t_eval.hs0_of(case["truth"])
                scores = {}
                for name, t in models:
                    s = p5t_eval.score_codes(case, codes(case["raw"], t), codes(case["clean"], t))
                    scores[name] = {k: round(float(s[k]), 4) for k in ("lock", "sinad")}
                results.append(dict(input="textured", standard=standard, dev=deviation, cn=cn, scores=scores))
            print(standard, deviation, "done", flush=True)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    results = run()
    if args.write:
        REPORT.write_text(json.dumps(dict(model_id=p64_k1.MODEL_ID, table_sha256=p64_k1.digest(p64_k1.table()),
                                          evidence="host simulation only; board test pending",
                                          results=results), indent=1) + "\n")
    if args.check:
        saved = json.loads(REPORT.read_text())
        assert saved["table_sha256"] == p64_k1.digest(p64_k1.table())
        assert len(saved["results"]) == len(results)
        for actual, old in zip(results, saved["results"]):
            assert {k: actual[k] for k in ("input", "standard", "dev", "cn")} == \
                   {k: old[k] for k in ("input", "standard", "dev", "cn")}
            for name, score in actual["scores"].items():
                for key, value in score.items():
                    tol = {"dots": 0.5, "grain": 0.05, "saturation": 0.01, "lock": 0.02, "sinad": 0.05}[key]
                    assert abs(value - old["scores"][name][key]) <= tol, (actual, key)
        print(f"PASS: {len(results)} U55P64-K1 / U80P64-R1 dot, colour and lock cases reproduce (host only)")


if __name__ == "__main__":
    main()

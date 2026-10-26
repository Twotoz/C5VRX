"""C5VRX/Twotoz: U80P32-D7 seven-bit DAC validation against U80P64-R1.

Same 12 PAL/NTSC cases, seed and physical-ADC model as p64_validation.py
(seed 2903, not used by the p32d7 parameter search). Three outputs:
U80P64-R1 on the six-bit DAC, U80P32-D7 on the seven-bit DAC (nominal
15k D10 branch) and U80P32-D7 on a six-bit board without R8 (lane 6 open).
Host evidence only. Run with --check in CI.
"""
import argparse
import json
from pathlib import Path

import numpy as np

import sim
import p5t_eval
import p64_model
import p32d7_model as M

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "research/data/p32d7_validation.json"


def run():
    p64 = p64_model.repaired_table()
    d7 = M.table()
    six_of_lanes = M.SIX_VOLTS[np.arange(128) & 63]
    models = (("U80P64-R1 6bit", lambda raw: sim._pair_codes(raw, p64, sim.PAIR_BITS, 4), sim.DAC_VOLTS),
              ("U80P32-D7 7bit", lambda raw: M.pair_lanes(raw, d7), M.LANE_VOLTS),
              ("U80P32-D7 6bit-board", lambda raw: M.pair_lanes(raw, d7), six_of_lanes))
    results = []
    for standard in ("PAL", "NTSC"):
        for deviation in (0.7, 1.4):
            for cn in (30.0, 4.0, 0.0):
                case = sim.make_case(2903, cn, standard, deviation, filter_hz=12e6,
                                     fields=1, physical_adc=True, adc_gain=3.0)
                case["hs0"] = p5t_eval.hs0_of(case["truth"])
                scores = {}
                for name, decode, volts in models:
                    score = p5t_eval.score_codes(case, decode(case["raw"]), decode(case["clean"]), volts=volts)
                    scores[name] = {key: round(float(score[key]), 4) for key in ("lock", "sinad")}
                results.append(dict(standard=standard, dev=deviation, cn=cn, scores=scores))
                print(standard, deviation, cn,
                      [(name, score["lock"], round(score["sinad"], 2)) for name, score in scores.items()], flush=True)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    results = run()
    if args.write:
        REPORT.write_text(json.dumps(dict(model_id=M.MODEL_ID, table_sha256=M.digest(M.table()),
                                          evidence="host simulation only; seven-bit board test pending",
                                          results=results), indent=1) + "\n")
    if args.check:
        saved = json.loads(REPORT.read_text())
        assert saved["table_sha256"] == M.digest(M.table())
        expected = saved["results"]
        assert len(expected) == len(results) == 12
        for actual, old in zip(results, expected):
            assert (actual["standard"], actual["dev"], actual["cn"]) == (old["standard"], old["dev"], old["cn"])
            for name in actual["scores"]:
                assert abs(actual["scores"][name]["lock"] - old["scores"][name]["lock"]) < 0.02
                assert abs(actual["scores"][name]["sinad"] - old["scores"][name]["sinad"]) < 0.05
            base, d7 = actual["scores"]["U80P64-R1 6bit"], actual["scores"]["U80P32-D7 7bit"]
            if actual["cn"] == 30:
                # 32 instead of 64 phase states cost 0.6-1.6 dB clean SINAD here.
                assert d7["lock"] == 1.0 and d7["sinad"] >= base["sinad"] - 2.0
            elif actual["cn"] == 4:
                assert d7["lock"] >= base["lock"] - 0.03
        print("PASS: 12 PAL/NTSC U80P32-D7 cases reproduce; clean and 4 dB lock gates pass (host only)")


if __name__ == "__main__":
    main()

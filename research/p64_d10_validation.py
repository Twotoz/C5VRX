"""C5VRX/Twotoz: U80P64-D10 validation against U80P64-R1; host evidence only.

Same 12 PAL/NTSC cases, seed and physical-ADC model as p64_validation.py
(seed 2903; the D10 labels were chosen on seeds 311/991). Outputs:
U80P64-R1 on the six-bit board, U80P64-D10 with R8 (15k D10 branch) and
U80P64-D10 on a six-bit board without R8, plus U80P64-R1 with its codes
picked for the nominal six-branch voltages (no D10) to separate the two
effects. Run with --check in CI.
"""
import argparse
import json
from pathlib import Path

import numpy as np

import sim
import p5t_eval
import p64_model
import p64_d10 as D

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "research/data/p64_d10_validation.json"
SIX_BOARD = D.SIX_VOLTS[np.arange(128) & 63]


def network_aware_p64():
    """U80P64-R1 with each code nearest the target on the six-branch network."""
    want = D.SIX_VOLTS[0] + D.TARGET * (D.SIX_VOLTS[63] - D.SIX_VOLTS[0]) / 63
    code = np.argmin(np.abs(D.SIX_VOLTS[None, :] - want[:, None]), axis=1)
    return ((D.P64 & ~63) | code).astype(np.uint16)


def run():
    p64, d10 = p64_model.repaired_table(), D.table()
    models = (("U80P64-R1 6bit", p64, SIX_BOARD), ("U80P64-D10 7bit", d10, D.LANE_VOLTS),
              ("U80P64-D10 6bit-board", d10, SIX_BOARD),
              ("U80P64-R1 network-aware 6bit", network_aware_p64(), SIX_BOARD))
    results = []
    for standard in ("PAL", "NTSC"):
        for deviation in (0.7, 1.4):
            for cn in (30.0, 4.0, 0.0):
                case = sim.make_case(2903, cn, standard, deviation, filter_hz=12e6,
                                     fields=1, physical_adc=True, adc_gain=3.0)
                case["hs0"] = p5t_eval.hs0_of(case["truth"])
                scores = {}
                for name, table, volts in models:
                    score = p5t_eval.score_codes(case, D.pair_lanes(case["raw"], table),
                                                 D.pair_lanes(case["clean"], table), volts=volts)
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
        REPORT.write_text(json.dumps(dict(model_id=D.MODEL_ID, table_sha256=D.digest(D.table()),
                                          evidence="host simulation only; board test pending",
                                          results=results), indent=1) + "\n")
    if args.check:
        saved = json.loads(REPORT.read_text())
        assert saved["table_sha256"] == D.digest(D.table())
        assert len(saved["results"]) == len(results) == 12
        for actual, old in zip(results, saved["results"]):
            assert (actual["standard"], actual["dev"], actual["cn"]) == (old["standard"], old["dev"], old["cn"])
            for name in actual["scores"]:
                assert abs(actual["scores"][name]["lock"] - old["scores"][name]["lock"]) < 0.02
                assert abs(actual["scores"][name]["sinad"] - old["scores"][name]["sinad"]) < 0.05
            base = actual["scores"]["U80P64-R1 6bit"]
            for name in ("U80P64-D10 7bit", "U80P64-D10 6bit-board"):
                # Same tracker: never worse in lock; levels may move a little.
                assert actual["scores"][name]["lock"] >= base["lock"] - 0.02
                assert actual["scores"][name]["sinad"] >= base["sinad"] - 0.2
        print("PASS: 12 PAL/NTSC U80P64-D10 cases reproduce; no lock or SINAD regression (host only)")


if __name__ == "__main__":
    main()

"""C5VRX by Twotoz/contributors: reproducible output/template transition audit.

Small deterministic offline waveforms; no hardware, CPU timing or LCD claim.
"""
from pathlib import Path
from fractions import Fraction
import csv
import hashlib
import io
import json
import subprocess

ROOT = Path(__file__).resolve().parents[1]
INPUTS = ("research/sync_output_transition.c", "main/sync_flywheel.c",
          "main/sync_flywheel.h", "main/sync_private_bench.h", "main/sync_synth.h", "main/sync_repair_ledger.h",
          "main/output_level.h", "main/kp_tables.h", "main/phase8_gain_lut.h",
          "main/u85q.bsasm", "main/u85q_lp.bsasm")


def budgets():
    result = {}
    for name, line, fields, slots in (
        ("PAL", Fraction(2560), Fraction(50), 15),
        ("NTSC", Fraction(22880, 9), Fraction(60000, 1001), 18),
    ):
        line_rate = Fraction(40_000_000) / line
        vertical_rate = fields * slots
        horizontal_rate = line_rate - vertical_rate / 2
        payload = horizontal_rate * 236 + vertical_rate * line / 2
        # Worst rounded64byte footprint, including misaligned whole patches.
        cache = horizontal_rate * 320 + vertical_rate * 1344
        result[name] = {
            "regular_h_per_second": float(horizontal_rate),
            "vertical_slots_per_second": float(vertical_rate),
            "full_loss_repair_payload_bytes_per_second": float(payload),
            "max_rounded64_commit_bytes_per_second": float(cache),
            "scratch_write_read_plus_rounded_raw_write_bytes_per_second": float(2 * payload + cache),
        }
    result["topology"] = {
        "nominal_rx_write_plus_tx_read_bytes_per_second": 80_000_000,
        "full_shadow_copy_extra_read_plus_write_bytes_per_second": 80_000_000,
        "nominal_40MHz_32bit_AHB_bytes_per_second": 160_000_000,
        "observed_e913_internal_heap_bytes": 33636,
        "full_shadow_ring_bytes": 32768,
        "remaining_before_stack_and_workspace_bytes": 33636 - 32768,
        "one_context_32phase_H_EQ_broad_firstEQ_bank_bytes": 32 * (236 + 3 * 1280),
        "one_context_256phase_H_EQ_broad_firstEQ_bank_bytes": 256 * (236 + 3 * 1280),
        "note": "Traffic arithmetic, not measured utilization/WCET or a complete worst-case bus proof",
    }
    return result


def main():
    hashes = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in INPUTS}
    run = subprocess.run([
        "docker", "run", "--rm", "-v", f"{ROOT.as_posix()}:/src:ro", "-w", "/src",
        "espressif/idf:v6.0.2", "bash", "-c",
        "cc -O2 -std=c11 -Wall -Wextra -Werror -I main research/sync_output_transition.c "
        "-o /tmp/sync_output_transition && /tmp/sync_output_transition",
    ], capture_output=True, text=True, check=True)
    assert hashes == {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in INPUTS}
    header = "kp,lp,output,pal,kind,cases,"
    output = run.stdout[run.stdout.index(header):]
    rows = [{key: int(value) for key, value in row.items()} for row in csv.DictReader(io.StringIO(output))]
    assert len(rows) == 8 * 2 * 10 * 2 * 4
    groups = []
    for lp in range(2):
        for level in range(10):
            group = [row for row in rows if row["lp"] == lp and row["output"] == level]
            groups.append({"lp": bool(lp), "output_level": level,
                **{key: sum(row[key] for row in group) for key in (
                    "cases", "relative_width_ok", "lp_bound_width_ok", "normalized_width_ok",
                    "legacy_width_ok", "bootstrap_refused", "tail_changed")},
                "max_tail_span": max(row["max_tail_span"] for row in group)})
    result = {
        "source_sha256": hashes,
        "scope": "Actual candidate synthesis/private arrays and actual output-mapped LUT recurrence; matched -1MHz original prefixes,8 phases,all32 initial tracker states,2 initial levels,both parities; not a complete scene/LCD/CPU acceptance matrix",
        "non_lp_scope": "Diagnostic only: candidate firstEQ state predictor is currently LP-specific",
        "gates": {
            "legacy": "absolute DAC<=8, existing1x fixture criterion",
            "relative": "output_entry(8,level), independent exact configured code map",
            "lp_bound": "relative+1 forLP from integer carry/rounding invariant; not a pulse-edge guarantee",
            "normalized": "map0+round(8*(map21-map0)/21), fixed baseline8/21 sync-to-blank crossing fraction; comparison metric, not measured monitor threshold",
        },
        "rows": len(rows), "decode_cases": sum(row["cases"] for row in rows),
        "groups": groups, "budgets": budgets(),
    }
    (ROOT / "research/data/sync_output_transition.csv").write_text(output, encoding="utf-8")
    (ROOT / "research/data/sync_output_transition.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"rows": result["rows"], "decode_cases": result["decode_cases"],
        "normalized_width_ok": sum(row["normalized_width_ok"] for row in rows),
        "bootstrap_refused": sum(row["bootstrap_refused"] for row in rows),
        "budgets": result["budgets"]}, indent=2))


if __name__ == "__main__":
    main()

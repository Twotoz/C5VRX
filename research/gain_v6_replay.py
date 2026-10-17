#!/usr/bin/env python3
"""C5VRX by Twotoz/contributors: evidence-limited V5/V6 capture replay.

Run inside the pinned IDF image. Archived original Q4 bytes are measured by
actual gain.c. Each window is an independent decision at its recorded gain;
no counterfactual trajectory, synthetic attenuation or RF-range claim.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import statistics
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "research/data/walk_2026-10-13"


def fnv(data):
    value = 2166136261
    for byte in data:
        value = ((value ^ byte) * 16777619) & 0xffffffff
    return f"{value:08x}"


def log_summary(path):
    rows = []
    for line in path.read_text(encoding="utf8").splitlines():
        if "FIELD_DIAG t=" not in line:
            continue
        fields = {key: int(value) for key, value in re.findall(r"(\w+)=(-?\d+)", line)}
        if {"t_ms", "gain", "gain_writes", "sync_valid_pct"} <= fields.keys():
            rows.append(fields)
    rates = []
    for before, after in zip(rows, rows[1:]):
        dt = after["t_ms"] - before["t_ms"]
        dw = after["gain_writes"] - before["gain_writes"]
        if dt > 0 and dw >= 0:
            rates.append(dw * 1000 / dt)
    return dict(file=path.name, sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                rows=len(rows), gain59_60_rows=sum(row["gain"] in (59, 60) for row in rows),
                zero_sync_rows=sum(row["sync_valid_pct"] == 0 for row in rows),
                max_observed_writes_per_s=max(rates, default=None),
                median_observed_writes_per_s=statistics.median(rates) if rates else None,
                usable_for_counterfactual_gain=False,
                limitation="Aggregate log, no per-transition original IQ/DC readback or controlled RF level")


def recorded_map():
    path = DATA / "range_diag_vtx_off.txt"
    line = next(row for row in path.read_text(encoding="utf8").splitlines() if row.startswith("GAINMAP "))
    entries = [dict(gain=int(g), power_q10=int(p), confidence=int(c), uncertainty_pm=int(u))
               for g, p, c, u in re.findall(r"(\d+):(\d+):(\d+):(\d+)", line)]
    return dict(file=path.name, entries=entries, covers_weak_capture_gains=False,
                limitations="G20-33 only, no controlled RF/transition IQ, no filter/DC epoch; not a weak/floor counterfactual model")


def run():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--source-revision", default="unrecorded")
    args = parser.parse_args()
    captures = []
    with tempfile.TemporaryDirectory() as directory:
        binary = Path(directory) / "gain_v6"
        subprocess.run(["cc", "-O2", "-std=c11", "-Wall", "-Wextra", "-Werror",
                        "-I", str(ROOT / "main"), "-I", str(ROOT / "research"),
                        str(ROOT / "test/test_gain_v6.c"), str(ROOT / "main/gain.c"),
                        str(ROOT / "main/arc_phy.c"), "-o", str(binary)], check=True)
        subprocess.run([str(binary)], check=True)
        for path in sorted(DATA.glob("*.bin")):
            raw = path.read_bytes()
            header = path.with_suffix(".bin.txt").read_text(encoding="utf8")
            gain = int(re.search(r"gain=(\d+)", header)[1])
            checksum = re.search(r"fnv1a=([0-9a-f]+)", header)[1]
            assert fnv(raw) == checksum, path
            result = subprocess.check_output([str(binary), "--capture", str(path), str(gain)], text=True)
            captures.append(dict(file=path.name, gain=gain, bytes=len(raw), fnv1a=checksum,
                                 sha256=hashlib.sha256(raw).hexdigest(), **json.loads(result)))
    result = dict(scope="Offline independent decisions at recorded gain, original Q4/I4; not closed-loop counterfactual replay",
                  source_revision=args.source_revision,
                  source_sha256={name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in
                                 ["main/gain.c", "main/arc_phy.c", "research/gain_v6_policy.h", "test/test_gain_v6.c"]},
                  default_v6_enabled=False, production_callers=0,
                  missing_capture_context=["synchronized actual DCO pair and residual", "gain/control/PHY/filter epochs",
                                           "genuine-sync/noise confirmation", "per-gain same-RF input captures"],
                  recorded_gain_map=recorded_map(), captures=captures, logs=[log_summary(DATA / name) for name in
                                          ["walk_serial.log", "walk2_stuck_misfire.log", "walk3_gain_loop.log"]],
                  conclusion="Stuck captures diagnose DC without max-gain pumping; unknown context cannot certify a safe gain action or range gain")
    encoded = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.write_text(encoded, encoding="utf8")
    else:
        print(encoded, end="")


if __name__ == "__main__":
    run()

#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Summarize CRC-valid finite-IQ captures in a Receiver Console session."""

from __future__ import annotations

import argparse
import statistics
from dataclasses import dataclass
from pathlib import Path

from C5VRX_Flasher import C5VRXApp, VIDEO_LINE_RATES_HZ
from c5vrx_usb_protocol import (
    PACKET_IQ_U32_BLOCK,
    PACKET_IQ_U32_CHUNK,
    StreamDecoder,
    decode_iq_block,
    decode_iq_chunk,
)


@dataclass
class Capture:
    capture_id: int
    timestamp_us: int
    words: list[int]


@dataclass
class Metrics:
    capture: Capture
    average_power: float
    clipped_percent: float
    pal_score: float
    ntsc_score: float


def latest_session_log() -> Path:
    root = Path.home() / "Documents" / "C5VRX Sessions"
    logs = [path for path in root.glob("*/raw-serial.bin") if path.stat().st_size]
    if not logs:
        raise FileNotFoundError(f"no non-empty Receiver Console log below {root}")
    return max(logs, key=lambda path: path.stat().st_mtime)


def read_captures(path: Path) -> tuple[list[Capture], list[str]]:
    decoder = StreamDecoder()
    complete: list[Capture] = []
    chunk_groups: dict[int, dict[int, tuple[int, ...]]] = {}
    chunk_totals: dict[int, int] = {}
    chunk_timestamps: dict[int, int] = {}
    errors: list[str] = []
    with path.open("rb") as stream:
        while data := stream.read(1024 * 1024):
            for kind, value in decoder.feed(data):
                if kind == "error":
                    errors.append(str(value))
                    continue
                if kind != "packet":
                    continue
                packet = value
                if packet.packet_type == PACKET_IQ_U32_BLOCK:
                    complete.append(Capture(
                        -1, packet.timestamp_us, decode_iq_block(packet)))
                    continue
                if packet.packet_type != PACKET_IQ_U32_CHUNK:
                    continue
                chunk = decode_iq_chunk(packet)
                chunk_groups.setdefault(chunk.capture_id, {})[
                    chunk.offset_words] = chunk.words
                chunk_totals[chunk.capture_id] = chunk.total_words
                chunk_timestamps.setdefault(chunk.capture_id, packet.timestamp_us)

    for capture_id, chunks in chunk_groups.items():
        words: list[int] = []
        for offset in sorted(chunks):
            if offset != len(words):
                words = []
                break
            words.extend(chunks[offset])
        if words and len(words) == chunk_totals[capture_id]:
            complete.append(Capture(
                capture_id, chunk_timestamps[capture_id], words))
    complete.sort(key=lambda capture: capture.timestamp_us)
    return complete, errors


def capture_metrics(capture: Capture, sample_rate_msps: float,
                    include_sync: bool = True) -> Metrics:
    power = 0
    clipped = 0
    for word in capture.words:
        i, q = C5VRXApp._decode_iq(word)
        power += i * i + q * q
        if abs(i) >= 511 or abs(q) >= 511:
            clipped += 1
    if not include_sync:
        return Metrics(
            capture,
            power / len(capture.words),
            clipped * 100.0 / len(capture.words),
            0.0,
            0.0,
        )
    fm = C5VRXApp._fm_discriminator(capture.words)
    bin_size = 16
    binned = [
        statistics.fmean(fm[offset:offset + bin_size])
        for offset in range(0, len(fm) - bin_size + 1, bin_size)
    ]
    scores: dict[str, float] = {}
    for standard, line_rate in VIDEO_LINE_RATES_HZ.items():
        period = sample_rate_msps * 1_000_000.0 / line_rate
        scores[standard] = C5VRXApp._sync_fold_candidate(
            binned, max(8, int(round(period / bin_size))))[1]
    return Metrics(
        capture,
        power / len(capture.words),
        clipped * 100.0 / len(capture.words),
        scores["PAL"],
        scores["NTSC"],
    )


def split_runs(captures: list[Capture], gap_us: int) -> list[list[Capture]]:
    runs: list[list[Capture]] = []
    for capture in captures:
        if (not runs or
                capture.timestamp_us - runs[-1][-1].timestamp_us > gap_us):
            runs.append([])
        runs[-1].append(capture)
    return runs


def median(values: list[float]) -> float:
    return statistics.median(values) if values else 0.0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("session", nargs="?", type=Path)
    parser.add_argument("--sample-rate-msps", type=float, default=80.0)
    parser.add_argument("--run-gap-seconds", type=float, default=2.0)
    parser.add_argument("--max-sync-captures-per-run", type=int, default=16)
    parser.add_argument("--max-captures-per-run", type=int, default=64)
    parser.add_argument("--last-runs", type=int, default=4)
    args = parser.parse_args()
    path = args.session or latest_session_log()
    captures, errors = read_captures(path)
    runs = split_runs(captures, int(args.run_gap_seconds * 1_000_000.0))
    print(f"session: {path}")
    print(f"complete captures: {len(captures)}; decoder errors: {len(errors)}")
    selected_runs = runs[-max(1, args.last_runs):]
    first_run_number = len(runs) - len(selected_runs) + 1
    for index, run in enumerate(selected_runs, first_run_number):
        metric_limit = max(1, args.max_captures_per_run)
        metric_stride = max(1, len(run) // metric_limit)
        metric_captures = run[::metric_stride][:metric_limit]
        metrics = [capture_metrics(capture, args.sample_rate_msps, False)
                   for capture in metric_captures]
        powers = [item.average_power for item in metrics]
        clipping = [item.clipped_percent for item in metrics]
        sync_limit = max(1, args.max_sync_captures_per_run)
        sync_stride = max(1, len(run) // sync_limit)
        sync_captures = run[::sync_stride][:sync_limit]
        sync_metrics = [capture_metrics(capture, args.sample_rate_msps)
                        for capture in sync_captures]
        pal = [item.pal_score for item in sync_metrics]
        ntsc = [item.ntsc_score for item in sync_metrics]
        standard = "NTSC" if median(ntsc) > median(pal) else "PAL"
        duration = (run[-1].timestamp_us - run[0].timestamp_us) / 1_000_000.0
        print(
            f"run {index}: ids={run[0].capture_id}..{run[-1].capture_id} "
            f"captures={len(run)} duration={duration:.1f}s "
            f"power median={median(powers):.0f} range={min(powers):.0f}..{max(powers):.0f} "
            f"clipped median={median(clipping):.2f}% "
            f"PAL score median={median(pal):.2f} "
            f"NTSC score median={median(ntsc):.2f} candidate={standard} "
            f"metric_samples={len(metrics)} sync_samples={len(sync_metrics)}"
        )
    if errors:
        print("decoder errors:")
        for error in errors:
            print(f"  {error}")


if __name__ == "__main__":
    main()

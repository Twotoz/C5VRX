#!/usr/bin/env python3
"""Static safety contract for the fixed-A1 autonomous analog-output build."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def require(path: str, *needles: str) -> str:
    text = (ROOT / path).read_text(encoding="utf-8")
    for needle in needles:
        if needle not in text:
            raise SystemExit(f"{path}: missing required contract: {needle}")
    return text


auto = require(
    "main/c5vrx_auto_av.c",
    "LP_COMMAND_CONTINUOUS",
    "ulp_c5vrx_duration_us = 0u",
    "owner=LP_CORE duration=UNBOUNDED",
    "FALLBACK_RETRY_MS",
    "c5vrx_cvbs_direct_rf_prepare",
    "c5vrx_cvbs_direct_rf_finish",
    "previous_runs",
)
lp = require(
    "main/lp_core/c5vrx_lp_av.c",
    "COMMAND_CONTINUOUS",
    "trigger_writer();",
    "c5vrx_rearms_succeeded",
    "c5vrx_expected_block_cycles",
    "c5vrx_phase_error_cycles",
    "c5vrx_gap_cycles_max",
)
control = require(
    "main/c5vrx_control.c",
    "owner=AUTO_A1_AV",
    "realtime_path_blocked_us=0",
    "C5VRX_AUTO_AV_STATUS",
    "continuity_uptime_ms",
    "usb_realtime_interference=0",
    'sscanf(line, "CAPTURE %u"',
)
main = require(
    "main/c5vrx_main.c",
    "c5vrx_auto_av_start()",
    "usb_required=0",
    "controls_required=0",
)
require(
    "sdkconfig.defaults.xiao_receiver_console",
    "CONFIG_ULP_COPROC_ENABLED=y",
    "CONFIG_ULP_COPROC_TYPE_LP_CORE=y",
    "CONFIG_RTC_FAST_CLK_SRC_XTAL=y",
)

if "C5VRX_DIRECT_AV_PROBE_MS" in auto:
    raise SystemExit("autonomous runtime must not contain the old 100-ms probe")
if "duration limit" not in lp or "no duration limit" not in lp:
    raise SystemExit("LP source must document its unbounded continuous contract")

print("autonomous A1 AV contract: PASS")

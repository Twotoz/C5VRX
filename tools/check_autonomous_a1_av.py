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
    "apm_hal_set_master_sec_mode",
    "APM_TEE_HP_PERIPH_MODEM",
    "APM_TEE_HP_PERIPH_SYSTEM_REG",
    "APM_TEE_HP_PERIPH_PCR_REG",
    "APM_TEE_HP_PERIPH_GDMA",
    "c5vrx_auto_av_restore_hp_boot_access",
    "APM_MASTER_HPCORE",
    "APM_SEC_MODE_REE0",
    "hp_gdma_permission=RW",
    "policy=THREE_ACTIVE_WINDOWS",
    "const uint32_t output_rate_hz = rate_min_hz / 4u",
    "ulp_c5vrx_run_cycles",
    "C5VRX_AUTO_AV_CALIBRATION",
    "c5vrx_cvbs_direct_rf_dma_info",
    "run_lp_parked",
    "usb=PAUSED_UNTIL_SIGNAL_LOSS",
    "esp_task_wdt_delete(idle_task)",
    "esp_task_wdt_add(idle_task)",
    "RF_SCAN_TIMEOUT_US 20000u",
    "session=HELD",
)
lp = require(
    "main/lp_core/c5vrx_lp_av.c",
    "COMMAND_CONTINUOUS",
    "trigger_writer();",
    "c5vrx_rearms_succeeded",
    "c5vrx_expected_block_cycles",
    "c5vrx_phase_error_cycles",
    "c5vrx_gap_cycles_max",
    "ulp_lp_core_panic_handler",
    "c5vrx_fault_address",
    "c5vrx_stage",
    "HP_SRAM_USAGE",
    "ACTIVITY_TIMEOUT_US",
    "observe_consumer",
    "c5vrx_consumer_lead_min_words",
    "c5vrx_consumer_pointer_changes",
    "c5vrx_consumer_wraps",
    "LEAD_TIMEOUT_US  20000u",
    "c5vrx_run_cycles",
    "if (current > previous)",
    "PARLIO_PREFILL_CYCLES 512u",
    "REG32(PARLIO_INT_CLR) = PARLIO_TX_FIFO_EMPTY_INT",
    "REG32(PARLIO_INT_ENA) |= PARLIO_TX_FIFO_EMPTY_INT",
)
control = require(
    "main/c5vrx_control.c",
    "owner=AUTO_A1_AV",
    "realtime_path_blocked_us=0",
    "C5VRX_AUTO_AV_STATUS",
    "continuity_uptime_ms",
    "usb_realtime_interference=0",
    "lp_fault_address=%08x",
    "consumer_lead_words=%u",
    "pacing_guard=MONITOR_ONLY",
    'sscanf(line, "CAPTURE %u"',
)
main = require(
    "main/c5vrx_main.c",
    "c5vrx_auto_av_start()",
    "c5vrx_auto_av_restore_hp_boot_access()",
    "usb_required=0",
    "controls_required=0",
)
require(
    "sdkconfig.defaults.xiao_receiver_console",
    "CONFIG_ULP_COPROC_ENABLED=y",
    "CONFIG_ULP_COPROC_TYPE_LP_CORE=y",
    "CONFIG_RTC_FAST_CLK_SRC_XTAL=y",
    "CONFIG_C5VRX_EXPERIMENTAL_RF_DUMP_PRODUCER=y",
    "CONFIG_C5VRX_AUTO_A1_AV=y",
    "# CONFIG_ESP_INT_WDT is not set",
)
require(
    "tools/Build_XIAO_A1_Receiver_Exe.ps1",
    'Require-BuildConfig "CONFIG_C5VRX_EXPERIMENTAL_RF_DUMP_PRODUCER"',
    'Require-BuildConfig "CONFIG_C5VRX_EXPERIMENTAL_CVBS_PARLIO"',
    'Require-BuildConfig "CONFIG_C5VRX_AUTO_A1_AV"',
)
require(
    "tools/C5VRX_Flasher.py",
    "C5VRX_HOST_USB_MODE mode=PASSIVE_RX",
    "C5VRX_HOST_USB_PAUSED",
    "self.autonomous_a1_appliance = True",
    "USB_COMMAND_DEFERRED_DIRECT",
    'fields.get("continuity_uptime_ms", "0")',
    'fields.get("rearm_failures", "0")',
)
xiao = require(
    "tools/C5VRX_XIAO_Flasher.py",
    "C5VRX_HOST_USB_RECOVERED mode=PASSIVE_RX commands_sent=0",
    "_schedule_passive_reconnect",
)

flasher = (ROOT / "tools/C5VRX_Flasher.py").read_text(encoding="utf-8")
poll_body = flasher.split("    def _poll_av_status", 1)[1].split(
    "    def ", 1)[0]
if "send_command(" in poll_body or "_write_serial_bytes(" in poll_body:
    raise SystemExit("autonomous AV polling must remain passive/RX-only")
connect_body = flasher.split("    def connect_serial", 1)[1].split(
    "    def ", 1)[0]
if "send_command(" in connect_body or "_write_serial_bytes(" in connect_body:
    raise SystemExit("autonomous connect handshake must remain passive/RX-only")
ready_body = xiao.split("    def _parse_device_line", 1)[1].split(
    "    def ", 1)[0]
if 'send_command("STATUS")' in ready_body:
    raise SystemExit("XIAO runtime-ready path must not auto-send STATUS")

if "C5VRX_DIRECT_AV_PROBE_MS" in auto:
    raise SystemExit("autonomous runtime must not contain the old 100-ms probe")
if "duration limit" not in lp or "no duration limit" not in lp:
    raise SystemExit("LP source must document its unbounded continuous contract")
if "(current - previous) & POINTER_MASK" in lp:
    raise SystemExit("LP writer must not count stale pointer regressions as full wraps")

print("autonomous A1 AV contract: PASS")

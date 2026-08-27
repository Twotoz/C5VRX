#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Static/disassembly contract for the guarded C5 native-ring experiment."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import re
import shutil
import subprocess


EXPECTED = "0f9680b41612762d854accf2334412e3e01b206a3ec290bbbb232842fdaec7ba"
ROOT = Path(__file__).resolve().parents[1]


def require(text: str, needle: str, failures: list[str]) -> None:
    if needle not in text:
        failures.append(needle)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--idf", required=True)
    args = parser.parse_args()

    archive = Path(args.idf) / "components/esp_phy/lib/esp32c5/librftest.a"
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if digest != EXPECTED:
        raise SystemExit(f"refusing unreviewed librftest.a SHA-256 {digest}")
    objdump = shutil.which("riscv32-esp-elf-objdump")
    if not objdump:
        raise SystemExit("riscv32-esp-elf-objdump missing")
    disassembly = subprocess.run(
        [objdump, "-dr", "-C", str(archive)],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout
    match = re.search(r"(?ms)^0+ <adctrig>:\n(.*?)(?=^0+ <dactrig>:)" ,
                      disassembly)
    if not match:
        raise SystemExit("adctrig disassembly not found")
    adctrig = match.group(1)

    producer = (ROOT / "main/c5vrx_rf_dump_producer.c").read_text(
        encoding="utf-8"
    )
    lp = (ROOT / "main/lp_core/c5vrx_lp_av.c").read_text(encoding="utf-8")
    control = (ROOT / "main/c5vrx_control.c").read_text(encoding="utf-8")
    native = (ROOT / "main/c5vrx_native_ring.c").read_text(encoding="utf-8")
    header = (ROOT / "main/c5vrx_native_ring.h").read_text(encoding="utf-8")
    failures: list[str] = []

    # C5 retains the nine-slot calling shape, but a4 (historic fifth arg) is
    # neither saved nor read: offset 0x26 overwrites it from a3. This exact
    # fact prevents the probe from pretending the historic ABI still works.
    prefix = adctrig[: adctrig.find("00000030 <.L132>")]
    if "26:\t4016d713          \tsrai\ta4,a3,0x1" not in prefix:
        failures.append("a4-not-overwritten-at-0x26")
    prior_to_overwrite = prefix.split("26:", 1)[0]
    if re.search(r"(?:^|[\s,])a4(?:[\s,]|$)", prior_to_overwrite):
        failures.append("historic-fifth-argument-consumed-before-overwrite")
    for fact in ("a:\t84be", "mv\ts1,a5", "24:\t4986", "lw\ts3,64(sp)"):
        if fact not in prefix:
            failures.append("nine-argument-shape:" + fact)

    # Vendor branch .L142 (trig_mode 6 from the recovered jump table) sets
    # only control bit 17 before joining the common enable path.
    bit17 = re.search(
        r"(?ms)^00000442 <\.L142>:(.*?)(?=^0000046e <\.L141>:)", adctrig
    )
    if not bit17 or not all(token in bit17.group(1) for token in (
        "lw\ta5,4(a4)", "lui\ta3,0x20", "or\ta5,a5,a3", "sw\ta5,4(a4)"
    )):
        failures.append("vendor-trig-mode6-bit17-write")

    require(producer, "C5VRX_RF_DUMP_MODE_NATIVE_RING", failures)
    require(producer, "REG32(DUMP_CTRL) |= CTRL_MODE_BIT", failures)
    require(producer, "#define NATIVE_SAMPLE_FIELD_MASK 0x00e00000u", failures)
    require(producer, "#define NATIVE_TRIGGER_FIELD_MASK 0x001e0000u", failures)
    require(producer, "#define NATIVE_TX_END_SELECTOR 0x00080000u", failures)
    require(producer, "REG32(DUMP_PTR_MODE) &= ~NATIVE_SAMPLE_FIELD_MASK", failures)
    require(producer, "~NATIVE_TRIGGER_FIELD_MASK) |", failures)
    require(producer, "NATIVE_TX_END_SELECTOR", failures)
    require(lp, "COMMAND_NATIVE_RING", failures)
    require(lp, "c5vrx_native_software_triggers = 0u", failures)
    require(lp, "c5vrx_native_software_rearms = 0u", failures)
    require(lp, "const bool enable_parlio = c5vrx_enable_parlio != 0u", failures)
    require(native, "pointer_rate_hz=80000000 output_hz=20000000", failures)
    require(native, "software_triggers=0 software_rearms=0", failures)
    require(native, "iq_freshness=PHYSICAL_AV_PENDING", failures)
    require(native, "hp=AVAILABLE usb=PASSIVE_RX_DIAGNOSTICS", failures)
    require(native, "C5VRX_NATIVE_AV_STATUS", failures)
    require(native, "hp_alive=1 usb=PASSIVE_RX_DIAGNOSTICS", failures)
    require(control, "classification=%s", failures)
    for field in (
        "physical_writer_pointer", "absolute_writer_samples",
        "hardware_wrap_count", "software_trigger_pulses", "software_rearms",
        "phase_boundary_residual_abs_mean", "content_changes", "fault_reason",
        "start_pointer_mode", "final_pointer_mode",
    ):
        require(header, field, failures)

    native_body = lp.split("static void run_native_ring(void)", 1)[-1].split(
        "\n#endif", 1
    )[0]
    if "trigger_writer();" in native_body:
        failures.append("native-path-calls-software-rearm")
    if re.search(r"REG32\(DUMP_CTRL\).*CTRL_SW_TRIGGER", native_body):
        failures.append("native-path-writes-software-trigger")
    if native_body.count("REG32(DUMP_CTRL) = control | CTRL_ENABLE;") != 1:
        failures.append("native-enable-assertion-not-exactly-once")
    if "TOADCDUMP" in producer + lp:
        failures.append("foreign-chip-toadcdump-register")
    if "NATIVE_RING_PROVEN" not in native or "INCONCLUSIVE" not in native:
        failures.append("acceptance-classifications")
    native_av_body = native.split("static void native_av_task(void *arg)", 1)[-1]
    native_av_body = native_av_body.split("\n#endif", 1)[0]
    if "portENTER_CRITICAL" in native_av_body:
        failures.append("native-av-hot-parks-hp-core")
    require(native_av_body, "vTaskDelay(1);", failures)

    print(
        "native ring probe audit",
        "PASS" if not failures else "FAIL",
        "librftest_sha256=" + digest,
        "dump_trig_arg=IGNORED",
        "bit17_vendor_write=TRIG_MODE_6",
        "failures=" + ",".join(failures),
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

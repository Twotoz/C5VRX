#!/usr/bin/env python3
"""C5VRX by Twotoz/contributors: inspect native gain handoff prerequisites.

Extends legacy/c5vrx4/tools/extract_phy_rx_evidence.py. Static evidence only;
PBUS readback and a gate transition do not prove one completed acquisition.
Run inside the pinned ESP-IDF v6.0.2 image; print only target definitions.
"""
import hashlib
import os
from pathlib import Path
import re
import subprocess

PIN = "dbf33c418c8d408d4005c849d12a1432deea82e2e5e57de3c8ddf914d104fffb"
SYMBOLS = (
    "phy_pbus_set_rxgain", "phy_pbus_rd", "phy_pbus_set_dco",
    "phy_force_rx_gain", "phy_enable_agc", "phy_disable_agc", "phy_rfagc_disable",
)


def main():
    archive = Path(os.environ["IDF_PATH"]) / "components/esp_phy/lib/esp32c5/libphy.a"
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if digest != PIN:
        raise SystemExit(f"Refused unverified archive: {digest}")
    print(f"C5VRX native-AGC static evidence; libphy SHA256={digest}")
    for symbol in SYMBOLS:
        raw = subprocess.check_output(
            ["riscv32-esp-elf-objdump", "-dr", f"--disassemble={symbol}", str(archive)],
            text=True,
        )
        blocks = re.split(r"(?=Disassembly of section )", raw)
        matches = [b.strip() for b in blocks if f"<{symbol}>:" in b]
        if len(matches) != 1:
            raise SystemExit(f"{symbol}: expected one definition, got {len(matches)}")
        print(f"\nSYMBOL {symbol}\n{matches[0]}")


if __name__ == "__main__":
    main()

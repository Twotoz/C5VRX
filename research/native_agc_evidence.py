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
    "phy_pbus_set_rxgain", "phy_pbus_rd", "phy_pbus_rd_addr", "phy_pbus_force_test",
    "phy_pbus_debugmode", "phy_pbus_workmode", "phy_pbus_set_dco",
    "phy_force_rx_gain", "phy_enable_agc", "phy_disable_agc", "phy_rfagc_disable",
    "phy_chip_set_chan_offset", "phy_set_freq",
    "phy_freq_correct", "phy_set_channel_rfpll_freq_new", "phy_chip_set_chan",
    "phy_band_change", "phy_set_rf_freq_offset", "phy_wait_rfpll_cal_end_new",
    "phy_freq_set_reg", "phy_get_pwdet_offset_new", "phy_reset_ckgen",
    "phy_band_reg", "phy_band_i2c_set", "phy_tatget_pwr_band",
    "phy_set_rx_pbus_freq", "phy_nrx_freq_set", "phy_bb_cfo_cfg", "phy_iq_swap_set",
    "phy_ckgen_5g_cal", "phy_freq_mem_change_5g_", "phy_set_rfpll_freq",
    "phy_freq_band_reg_set", "phy_pbus_force_mode",
    "phy_agc_reg_init_new", "bb_agc_reg_update", "phy_set_pbus_reg",
    "phy_rx_gain_force", "rx_gain_force", "phy_wr_rx_gain_mem",
    "phy_set_pbus_mem", "phy_set_pbus_mem_5g", "phy_set_pbus_mem_2g", "phy_write_pbus_mem",
    "phy_wifi_fbw_sel", "phy_bb_cbw_chan_cfg",
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
    raw = subprocess.check_output(
        ["riscv32-esp-elf-objdump", "-r", "-j", ".rodata.phy_pbus_rd_addr", str(archive)],
        text=True,
    )
    # The switch destinations establish which read register belongs to each
    # PBUS block; disassembly without this table does not prove that mapping.
    targets = re.findall(r"^([0-9a-f]{8})\s+R_RISCV_32\s+(.L\d+)\s*$", raw, re.M)
    expected = [f".L{k}" for k in (34, 33, 32, 31, 30, 29, 28, 27, 26, 25, 23)]
    if targets != [(f"{4*k:08x}", label) for k, label in enumerate(expected)]:
        raise SystemExit("Refused unexpected PBUS read-address switch")
    print("\nPBUS READ ADDRESS SWITCH (.rodata.phy_pbus_rd_addr)")
    for block, (_, label) in enumerate(targets):
        print(f"block {block}: {label}")


if __name__ == "__main__":
    main()

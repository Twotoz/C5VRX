import subprocess
import sys
import time
from pathlib import Path
import serial.tools.list_ports

ROOT = Path("C:/Users/leonb/Twotoz/C5VRX-issue11-output")

BUILDS = {
    "golden_128k":  ("Seamless Golden 128K (POS edge, trailing=0, eof=downstream, no tel)", ROOT / "build-golden-128k-notel"),
    "golden_32k":   ("Seamless Golden 32K (trailing=0, eof=downstream, no tel)", ROOT / "build-golden-32k-notel"),
    "golden_notel": ("Seamless Golden 16K (trailing=0, eof=downstream, no tel)", ROOT / "build-golden-notel"),
    "golden_8k":    ("Golden 8K (Clean / No Telemetry)", ROOT / "build-golden-8k-notel"),
    "interleaved40":("40->40 Interleaved Phase5 @ 40 MS/s", ROOT / "build-interleaved40-notel"),
    "phase5_oracle":("Phase5 @ 40M RX Premapper Oracle", ROOT / "build-phase5-40m-oracle"),
}

def find_esp_port():
    ports = serial.tools.list_ports.comports()
    for p in ports:
        if "303A" in (p.hwid or "").upper() or "ESPRESSIF" in (p.description or "").upper() or "USB JTAG" in (p.description or "").upper() or "USB-SERIAL" in (p.description or "").upper():
            return p.device
    for p in ports:
        if not (p.hwid or "").startswith("BTHENUM"):
            return p.device
    return "COM10"

def flash(target_key):
    if target_key not in BUILDS:
        print(f"Unknown target '{target_key}'! Choices: {list(BUILDS.keys())}")
        sys.exit(1)
    
    label, build_dir = BUILDS[target_key]
    bootloader = build_dir / "bootloader/bootloader.bin"
    ptable = build_dir / "partition_table/partition-table.bin"
    app = build_dir / "c5vrx2_realtime_iq.bin"

    if not app.exists():
        print(f"Error: {app} does not exist!")
        sys.exit(1)

    print(f"\n=======================================================")
    print(f" FLASHING CANDIDATE: [{target_key.upper()}]")
    print(f" Description: {label}")
    print(f" App Binary:  {app}")
    print(f"=======================================================")
    print("Waiting for ESP32-C5 (Hold B, tap R if in download mode)...")

    port = find_esp_port()
    cmd = [
        sys.executable, "-m", "esptool",
        "--chip", "esp32c5",
        "-p", port,
        "-b", "460800",
        "--before", "default-reset",
        "--after", "hard-reset",
        "write-flash",
        "--flash-mode", "dio",
        "--flash-size", "8MB",
        "--flash-freq", "40m",
        "0x2000", str(bootloader),
        "0x8000", str(ptable),
        "0x10000", str(app)
    ]

    for attempt in range(1, 15):
        res = subprocess.run(cmd)
        if res.returncode == 0:
            print(f"\n*** FLASH [{target_key.upper()}] SUCCEEDED! ***")
            print("Tap physical Reset (R) button on XIAO to start.")
            return True
        print(f"\nAttempt {attempt} failed, retrying in 2s (Hold B, tap R on XIAO)...")
        time.sleep(2)
        port = find_esp_port()
        cmd[6] = port

    print(f"\nFlash failed on {port} after retries.")
    return False

if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "golden_notel"
    success = flash(target)
    sys.exit(0 if success else 1)

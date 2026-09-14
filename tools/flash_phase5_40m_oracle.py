import subprocess
import sys
import time
from pathlib import Path
import serial.tools.list_ports

ROOT = Path("C:/Users/leonb/Twotoz/C5VRX-issue11-output")
build_dir = ROOT / "build-phase5-40m-oracle"
bootloader = build_dir / "bootloader/bootloader.bin"
ptable = build_dir / "partition_table/partition-table.bin"
app = build_dir / "c5vrx2_realtime_iq.bin"

def find_esp_port():
    ports = serial.tools.list_ports.comports()
    for p in ports:
        if "303A" in (p.hwid or "").upper() or "ESPRESSIF" in (p.description or "").upper() or "USB JTAG" in (p.description or "").upper() or "USB-SERIAL" in (p.description or "").upper():
            return p.device
    for p in ports:
        if not (p.hwid or "").startswith("BTHENUM"):
            return p.device
    return None

def main():
    if not app.exists():
        print(f"Error: {app} does not exist!")
        sys.exit(1)

    print("=== Ready to flash Phase5 @ 40M Premapper Oracle ===")
    print("Waiting for ESP32-C5 port...")

    port = find_esp_port()
    if not port:
        print("No port found! Retrying...")
        for _ in range(60):
            port = find_esp_port()
            if port:
                break
            time.sleep(0.5)

    if not port:
        print("Timeout waiting for port.")
        sys.exit(1)

    print(f"Flashing on {port}...")
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
    res = subprocess.run(cmd)
    if res.returncode != 0:
        print(f"Flash failed on {port}!")
        sys.exit(res.returncode)

    print("\n*** FLASH PHASE5 40M ORACLE SUCCEEDED! ***")

if __name__ == "__main__":
    main()

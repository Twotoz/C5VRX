import argparse
import subprocess
import sys
import time
from pathlib import Path
import serial.tools.list_ports

ROOT = Path("C:/Users/leonb/Twotoz/C5VRX-issue11-output")

parser = argparse.ArgumentParser(description="Flash Issue 12 build")
parser.add_argument("--build-dir", default="build-issue12-int-neg", help="Build directory name")
args = parser.parse_args()

build_dir = ROOT / args.build_dir
bootloader = build_dir / "bootloader/bootloader.bin"
ptable = build_dir / "partition_table/partition-table.bin"
app = build_dir / "c5vrx2_realtime_iq.bin"

if not app.exists():
    print(f"Error: {app} does not exist!")
    sys.exit(1)

def find_esp_port():
    ports = serial.tools.list_ports.comports()
    for p in ports:
        if "303A" in (p.hwid or "").upper() or "ESPRESSIF" in (p.description or "").upper() or "USB JTAG" in (p.description or "").upper() or "USB-SERIAL" in (p.description or "").upper():
            return p.device
    for p in ports:
        if not (p.hwid or "").startswith("BTHENUM"):
            return p.device
    return None

print(f"=== Ready to flash Issue 12 Build: {args.build_dir} ===")
print("Waiting for ESP32-C5 (Hold B, tap R on XIAO if needed)...")

for attempt in range(1, 300):
    port = find_esp_port()
    if port:
        print(f"Detected device on {port}! Starting flash...")
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
        if res.returncode == 0:
            print(f"\n*** FLASH {args.build_dir} SUCCEEDED! ***")
            sys.exit(0)
        print(f"Flash attempt failed on {port}, retrying...")
    time.sleep(1)

print("Timeout waiting for ESP32-C5.")
sys.exit(1)

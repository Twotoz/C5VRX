import subprocess
import sys
import time
from pathlib import Path
import serial.tools.list_ports

ROOT = Path("C:/Users/leonb/Twotoz/C5VRX-issue11-output")
bootloader = ROOT / "build-live/bootloader/bootloader.bin"
ptable = ROOT / "build-live/partition_table/partition-table.bin"
app = ROOT / "build-live/c5vrx2_realtime_iq.bin"

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

print("=== Ready to flash Original Golden Phase5 Build ===")
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
            print("\n*** FLASH GOLDEN PHASE5 SUCCEEDED! ***")
            sys.exit(0)
        print(f"Flash attempt failed on {port}, retrying...")
    time.sleep(1)

print("Timeout waiting for ESP32-C5.")
sys.exit(1)

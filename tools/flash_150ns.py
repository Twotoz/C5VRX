import subprocess
import sys
import time
from pathlib import Path

ROOT = Path("C:/Users/leonb/Twotoz/C5VRX-issue11-output")
bootloader = ROOT / "build-150ns/bootloader/bootloader.bin"
ptable = ROOT / "build-150ns/partition_table/partition-table.bin"
app = ROOT / "build-150ns/c5vrx2_realtime_iq.bin"

if not app.exists():
    print(f"Error: {app} does not exist!")
    sys.exit(1)

cmd = [
    sys.executable, "-m", "esptool",
    "--chip", "esp32c5",
    "-p", "COM10",
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

print("Ready to flash 150ns Live build.")
print("Waiting for ESP32-C5 on COM10 (Hold B, tap R on XIAO)...")

for attempt in range(1, 60):
    res = subprocess.run(cmd)
    if res.returncode == 0:
        print("\n*** FLASH 150ns SUCCEEDED! ***")
        sys.exit(0)
    print(f"Waiting for download mode (attempt {attempt}/60)...")
    time.sleep(1)

print("Timeout waiting for download mode.")
sys.exit(1)

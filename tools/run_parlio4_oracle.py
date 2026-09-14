import serial
import time
import subprocess
import sys
from pathlib import Path

ROOT = Path("C:/Users/leonb/Twotoz/C5VRX-issue11-output")

bootloader = ROOT / "build-parlio4/bootloader/bootloader.bin"
ptable = ROOT / "build-parlio4/partition_table/partition-table.bin"
app = ROOT / "build-parlio4/c5vrx2_realtime_iq.bin"

if not app.exists():
    print(f"Error: {app} does not exist yet!")
    sys.exit(1)

print("Resetting COM10 into bootloader mode...")
try:
    s = serial.Serial('COM10', 1200)
    s.dtr = False
    s.rts = True
    time.sleep(0.1)
    s.close()
except Exception as e:
    print(f"Notice during 1200-baud touch: {e}")

time.sleep(1.0)

cmd = [
    sys.executable, "-m", "esptool",
    "--chip", "esp32c5",
    "-p", "COM10",
    "-b", "460800",
    "write-flash",
    "0x2000", str(bootloader),
    "0x8000", str(ptable),
    "0x10000", str(app)
]

print("Running esptool:", " ".join(cmd))
res = subprocess.run(cmd)
if res.returncode != 0:
    print(f"Flashing failed with exit code {res.returncode}")
    sys.exit(res.returncode)

print("Flashing succeeded. Resetting device and monitoring serial output...")
try:
    s = serial.Serial('COM10', 115200, timeout=0.1)
    s.dtr = False
    s.rts = False
    time.sleep(0.1)
    s.rts = True
    time.sleep(0.1)
    s.rts = False
    
    print("\n--- SERIAL MONITOR STARTED ---")
    start_time = time.time()
    while time.time() - start_time < 15.0:
        line = s.readline()
        if line:
            try:
                text = line.decode('utf-8', errors='replace').rstrip()
                print(text)
                if "OVERALL ORACLE RESULT:" in text:
                    # Capture a bit more output then finish
                    time.sleep(1.0)
                    while True:
                        extra = s.readline()
                        if not extra:
                            break
                        print(extra.decode('utf-8', errors='replace').rstrip())
                    break
            except Exception:
                pass
    s.close()
except Exception as e:
    print(f"Serial monitoring error: {e}")

print("\n--- SERIAL MONITOR COMPLETE ---")

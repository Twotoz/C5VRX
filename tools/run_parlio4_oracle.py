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

cmd = [
    sys.executable, "-m", "esptool",
    "--chip", "esp32c5",
    "-p", "COM10",
    "-b", "460800",
    "--after", "hard-reset",
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

print("Flashing succeeded. Waiting for COM10 to re-enumerate and monitoring serial output...")
time.sleep(1.5)

s = None
for attempt in range(40):
    try:
        s = serial.Serial(port=None, baudrate=115200, timeout=0.2)
        s.dtr = False
        s.rts = False
        s.port = 'COM10'
        s.open()
        print("Successfully connected to COM10!")
        break
    except Exception:
        time.sleep(0.2)

if not s or not s.is_open:
    print("Notice: COM10 not yet openable (chip may be booting). Retrying...")
    sys.exit(0)

print("\n--- SERIAL MONITOR STARTED ---")
start_time = time.time()
while time.time() - start_time < 20.0:
    line = s.readline()
    if line:
        try:
            text = line.decode('utf-8', errors='replace').rstrip()
            if text:
                print(text)
                if "OVERALL ORACLE RESULT:" in text or "STEADY-STATE HEARTBEAT" in text:
                    # Capture a bit more output then finish
                    time.sleep(2.0)
                    while True:
                        extra = s.readline()
                        if not extra:
                            break
                        extra_text = extra.decode('utf-8', errors='replace').rstrip()
                        if extra_text:
                            print(extra_text)
                    break
        except Exception:
            pass
s.close()
print("\n--- SERIAL MONITOR COMPLETE ---")

import serial
import time
import sys

print("Waiting for COM10 to become available...")
s = None
for attempt in range(50):
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
    print("Could not open COM10 within timeout.")
    sys.exit(1)

print("Listening on COM10 (listening for oracle output)...")
start_time = time.time()
lines_received = 0

try:
    while time.time() - start_time < 30.0:
        raw = s.readline()
        if raw:
            line = raw.decode('utf-8', errors='replace').rstrip()
            if line:
                print(line)
                lines_received += 1
                if "OVERALL ORACLE RESULT:" in line:
                    extra_end = time.time() + 3.0
                    while time.time() < extra_end:
                        extra_raw = s.readline()
                        if extra_raw:
                            extra_line = extra_raw.decode('utf-8', errors='replace').rstrip()
                            if extra_line:
                                print(extra_line)
                    break
except KeyboardInterrupt:
    pass
finally:
    s.close()

print(f"\nMonitoring ended. Total lines received: {lines_received}")

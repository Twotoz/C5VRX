import serial
import time
import sys

print("=== PARLIO 4-BIT @ 80 MHz SERIAL MONITOR ===")
print("Listening on COM10... Press button R on XIAO to boot the chip!")

start_time = time.time()
lines_received = 0
s = None

while time.time() - start_time < 60.0:
    if s is None or not s.is_open:
        try:
            s = serial.Serial(port=None, baudrate=115200, timeout=0.1)
            s.dtr = False
            s.rts = False
            s.port = 'COM10'
            s.open()
            print("Connected to COM10! Streaming logs...")
        except Exception:
            time.sleep(0.1)
            continue
    try:
        raw = s.readline()
        if raw:
            line = raw.decode('utf-8', errors='replace').rstrip()
            if line:
                print(line)
                lines_received += 1
                if "OVERALL ORACLE RESULT:" in line or "STEADY-STATE HEARTBEAT" in line:
                    extra_end = time.time() + 4.0
                    while time.time() < extra_end:
                        try:
                            extra_raw = s.readline()
                            if extra_raw:
                                extra_line = extra_raw.decode('utf-8', errors='replace').rstrip()
                                if extra_line:
                                    print(extra_line)
                        except Exception:
                            pass
                    break
    except serial.SerialException:
        # USB disconnected during reset, try reconnecting
        try:
            s.close()
        except Exception:
            pass
        s = None
        time.sleep(0.2)
    except Exception as e:
        time.sleep(0.1)

if s and s.is_open:
    s.close()

print(f"\nMonitoring ended. Total lines received: {lines_received}")

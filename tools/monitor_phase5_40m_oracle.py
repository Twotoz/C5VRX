import serial
import time
import sys
import serial.tools.list_ports

def find_esp_port():
    ports = serial.tools.list_ports.comports()
    for p in ports:
        if "303A" in (p.hwid or "").upper() or "ESPRESSIF" in (p.description or "").upper() or "USB JTAG" in (p.description or "").upper() or "USB-SERIAL" in (p.description or "").upper():
            return p.device
    for p in ports:
        if not (p.hwid or "").startswith("BTHENUM"):
            return p.device
    return "COM10"

def main():
    port = find_esp_port()
    print(f"=== Listening on {port} (Tap R on XIAO to boot) ===")
    
    t_end = time.time() + 90.0
    while time.time() < t_end:
        try:
            ser = serial.Serial(port, 115200, timeout=0.2)
            ser.dtr = True
            ser.rts = False
            while time.time() < t_end:
                line = ser.readline().decode("utf-8", errors="replace").strip()
                if line:
                    print(line)
                    sys.stdout.flush()
                time.sleep(0.01)
        except Exception:
            time.sleep(0.2)

if __name__ == "__main__":
    main()

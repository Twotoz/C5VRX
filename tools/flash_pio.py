#!/usr/bin/env python3
"""Flash a PlatformIO build of C5VRX to the XIAO ESP32-C5 without the BOOT button.

usage: python tools/flash_pio.py [ENV] [PORT]      (default env xiao_c5_link, port auto)

Order of attempts, each with DIO at 80 MHz (the XIAO's flash does not boot
in QIO) and --after watchdog-reset (the application starts by itself):

1. The chip is already in the ROM bootloader (BOOT held at plug-in, or a
   previous '!' key): write with --before no-reset.
2. The application runs: send the console key '!', which makes a firmware
   with that key reboot into ROM download mode, wait for the port to come
   back and write with --before no-reset.
3. esptool's own --before default-reset (USB-Serial/JTAG reset lines).

If all three fail: unplug, hold BOOT, plug in, release, run again.
Never asserts DTR/RTS when talking to the console.
"""
import subprocess
import sys
import time
from pathlib import Path

import serial
import serial.tools.list_ports

ROOT = Path(__file__).resolve().parent.parent
env = sys.argv[1] if len(sys.argv) > 1 else "xiao_c5_link"
port_arg = sys.argv[2] if len(sys.argv) > 2 else None
build = ROOT / ".pio" / "build" / env

images = {
    "0x2000": build / "bootloader.bin",
    "0x8000": build / "partitions.bin",
    "0x10000": build / "firmware.bin",
}
for path in images.values():
    if not path.is_file():
        raise SystemExit(f"missing {path}; run: pio run -e {env}")


def find_port(timeout_s=0.0):
    t_end = time.time() + timeout_s
    while True:
        if port_arg:
            if port_arg in [p.device for p in serial.tools.list_ports.comports()]:
                return port_arg
        else:
            for p in serial.tools.list_ports.comports():
                if "303A" in (p.hwid or "").upper():
                    return p.device
        if time.time() >= t_end:
            return None
        time.sleep(0.2)


def esptool(port, before):
    cmd = [sys.executable, "-m", "esptool", "--chip", "esp32c5", "-p", port, "-b", "460800",
           "--before", before, "--after", "watchdog-reset", "--connect-attempts", "2",
           "write-flash", "--flash-mode", "dio", "--flash-size", "8MB", "--flash-freq", "80m"]
    for offset, path in images.items():
        cmd += [offset, str(path)]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    ok = res.returncode == 0 and "Hash of data verified" in res.stdout
    last = [l for l in res.stdout.splitlines() if l.strip()][-1:] or [""]
    print(f"  esptool --before {before}: {'OK' if ok else 'failed: ' + last[0][:100]}")
    return ok


def reboot_to_download(port):
    try:
        ser = serial.Serial()
        ser.port = port
        ser.baudrate = 115200
        ser.dtr = False
        ser.rts = False
        ser.timeout = 0.2
        ser.open()
        ser.write(b"!")
        ser.flush()
        time.sleep(0.3)
        ser.close()
        return True
    except serial.SerialException as exc:
        print(f"  could not send '!': {exc}")
        return False


port = find_port(10.0)
if port is None:
    raise SystemExit("no Espressif USB-Serial/JTAG port found")
print(f"flashing {build} via {port}")
if esptool(port, "no-reset"):
    sys.exit(0)
if reboot_to_download(port):
    time.sleep(1.0)
    port = find_port(10.0) or port
    if esptool(port, "no-reset"):
        sys.exit(0)
if esptool(port, "default-reset"):
    sys.exit(0)
raise SystemExit("all attempts failed: unplug, hold BOOT, plug in, release BOOT, run again")

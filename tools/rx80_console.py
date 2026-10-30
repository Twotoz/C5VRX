"""C5VRX by Twotoz/contributors: bounded, no-reset bench console capture."""
import argparse
from pathlib import Path
import time
from flash import open_quiet

p = argparse.ArgumentParser()
p.add_argument("port")
p.add_argument("keys")
p.add_argument("output", type=Path)
p.add_argument("--seconds", type=float, default=5)
a = p.parse_args()
if a.output.exists():
    p.error("use a fresh output filename")
with open_quiet(a.port) as s:
    s.reset_input_buffer()
    s.write(a.keys.encode("ascii"))
    end = time.monotonic() + a.seconds
    data = bytearray()
    while time.monotonic() < end:
        data.extend(s.read(8192))
a.output.parent.mkdir(parents=True, exist_ok=True)
a.output.write_bytes(data)
print(data.decode("utf-8", errors="replace"))

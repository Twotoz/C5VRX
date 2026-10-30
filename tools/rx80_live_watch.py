"""C5VRX by Twotoz/contributors: bounded observation of the current live mode.

Run after the operator confirms VTX power. No clock/rate changes, no reset,
and no legacy prototype bench key. Save all console bytes and retry a few
IQ snapshots while gain settles. This does not establish visual acceptance.
"""
from pathlib import Path
import argparse
import time
from flash import open_quiet

ap=argparse.ArgumentParser(description=__doc__)
ap.add_argument("port")
ap.add_argument("output",type=Path)
ap.add_argument("--seconds",type=float,default=12)
a=ap.parse_args()
if a.output.exists(): ap.error("use a fresh log path")
if not 3<=a.seconds<=20: ap.error("measurement must be between 3 and 20 seconds")
a.output.parent.mkdir(parents=True,exist_ok=True)
with open_quiet(a.port) as s, a.output.open("wb") as log:
    s.reset_input_buffer()
    pending=b""
    start=time.monotonic()
    next_status=start
    snapshots=iter((start+3,start+6,start+9))
    next_snapshot=next(snapshots)
    end=start+a.seconds
    print(f"WATCH observing current live mode for {a.seconds:g} seconds",flush=True)
    while time.monotonic()<end:
        now=time.monotonic()
        if now>=next_status:
            s.write(b"T"); next_status=now+2
        if next_snapshot is not None and now>=next_snapshot:
            s.write(b"I"); next_snapshot=next(snapshots,None)
        chunk=s.read(4096)
        if not chunk: continue
        log.write(chunk); log.flush(); pending+=chunk
        while b"\n" in pending:
            line,pending=pending.split(b"\n",1)
            text=line.decode("utf-8",errors="replace").strip()
            if text.startswith(("SAMPLE_RATE","PICTURE ","VIDEO_Q","IQSNAP_BEGIN","IQSNAP_END","IQSNAP_REFUSED")):
                print(text,flush=True)
    print("WATCH finished: VTX CAN BE TURNED OFF NOW",flush=True)

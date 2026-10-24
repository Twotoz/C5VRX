"""Bounded USB console capture for C5VRX bench work, no reset lines."""
import argparse
from pathlib import Path
import time
import serial

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('port');p.add_argument('keys');p.add_argument('output',type=Path)
p.add_argument('--seconds',type=float,default=4)
p.add_argument('--until',default='')
a=p.parse_args()
s=serial.Serial();s.port=a.port;s.baudrate=115200;s.timeout=.1;s.write_timeout=2;s.dtr=False;s.rts=False
s.open()
try:
    s.write(a.keys.encode())
    end=time.monotonic()+a.seconds;tail=b''
    with a.output.open('wb') as out:
        while time.monotonic()<end:
            data=s.read(16384);out.write(data);out.flush();tail=(tail+data)[-32768:]
            if a.until and a.until.encode() in tail:break
    print(tail.decode(errors='replace'))
finally:s.close()

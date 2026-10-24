#!/usr/bin/env python3
"""C5VRX/Twotoz: fixed-row IQ witnesses across separate calibration boots.

Uses existing transient G presets and I snapshots. Does not erase NVS or
write PHY registers. Always attempts to return gain mode to AUTO.
"""
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import time
import serial
from capture_iq_snapshot import decode

MODES=['AUTO','MAX','G78','G72','G66','G60','G54','G48','G42','G36','G30','G24']


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('port');p.add_argument('output',type=Path);p.add_argument('--frequency',type=int,required=True)
    p.add_argument('--count',type=int,default=16)
    p.add_argument('--gains',type=int,nargs='+',choices=[24,30,36,42,48,54,60,66,72,78,83],default=[78,72])
    p.add_argument('--interval',type=float,default=0,help='seconds between live snapshots')
    a=p.parse_args()
    if a.count<1 or a.interval<0:p.error('count must be positive and interval nonnegative')
    a.output.mkdir(parents=True,exist_ok=False)
    s=serial.Serial();s.port=a.port;s.baudrate=115200;s.timeout=.05;s.write_timeout=2;s.dtr=False;s.rts=False
    manifest={'started_utc':dt.datetime.now(dt.timezone.utc).isoformat(),'captures':[],'rejected':[]}
    log=(a.output/'console.raw.log').open('wb')
    def collect(seconds,until=None):
        data=b'';end=time.monotonic()+seconds
        while time.monotonic()<end:
            part=s.read(16384);log.write(part);log.flush();data+=part
            if until and until in data:break
        return data.decode(errors='replace')
    def status():
        s.write(b'T');return collect(1)
    def gain(target):
        text=status()
        m=re.search(r'GAIN owner=\S+ mode=(\S+)',text)
        if not m or m[1] not in MODES:raise RuntimeError('cannot establish current gain mode')
        n=(MODES.index(target)-MODES.index(m[1]))%len(MODES)
        if n:s.write(b'G'*n);collect(1.0)
        text=status()
        if f'mode={target} ' not in text:raise RuntimeError('gain-mode readback failed')
        return text
    try:
        s.open()
        for row in a.gains:
            st=gain('MAX' if row==83 else f'G{row}');manifest[f'gain{row}_status']=st
            if f'freq={a.frequency} ' not in st:raise RuntimeError('wrong frequency')
            for rep in range(a.count):
                for attempt in range(6):
                    s.write(b'I');text=collect(3,until=b'IQSNAP_END ')
                    # END may have arrived in a partial USB chunk; collect its newline.
                    if text and not text.endswith('\n'):text+=collect(.1)
                    try:
                        raw,meta=decode(text.splitlines())
                        if int(meta['gain'])!=row or int(meta['freq_mhz'])!=a.frequency:raise ValueError('snapshot context mismatch')
                    except ValueError as e:
                        manifest['rejected'].append(dict(gain=row,rep=rep,attempt=attempt,reason=str(e)))
                        collect(.1);continue
                    name=f'g{row}_r{rep:02d}.iq';(a.output/name).write_bytes(raw)
                    manifest['captures'].append(dict(file=name,sha256=hashlib.sha256(raw).hexdigest(),metadata=meta))
                    print(f'gain={row} capture={rep+1}/{a.count}',flush=True)
                    if a.interval:collect(a.interval)
                    break
                else:raise RuntimeError('six failed captures')
        manifest['final_status']=gain('AUTO')
    finally:
        try:
            if s.is_open:manifest['cleanup_status']=gain('AUTO')
        finally:
            s.close();log.close()
            manifest['ended_utc']=dt.datetime.now(dt.timezone.utc).isoformat()
            manifest['raw_log_sha256']=hashlib.sha256((a.output/'console.raw.log').read_bytes()).hexdigest()
            (a.output/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')


if __name__=='__main__':main()

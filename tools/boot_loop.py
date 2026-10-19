#!/usr/bin/env python3
"""Boot-to-boot receiver check (C5VRX by Twotoz and the C5VRX contributors).

"Some boots are great, some terrible": every channel setup at boot gives the
receiver DC another offset, and a boot is only as good as the live correction
that follows. This reboots the board N times (VTX off) and records, per boot,
the IQ right after startup and after a settle time, plus the T counters, so
research/sensitivity_model.py can turn every boot into a range limit in dB.

    python tools/boot_loop.py COM10 out_dir [--boots 6] [--settle 60]
    python research/sensitivity_model.py "out_dir/b0_late_*.bin" "out_dir/b1_late_*.bin" ...

Reboot route as tools/enter_download.py documents (esptool's own reset does
not work on a running image). No range claim without the model run.
"""
import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import serial

HERE = Path(__file__).resolve().parent


def port_open(port):
    s = serial.Serial()
    s.port, s.baudrate, s.timeout, s.dtr, s.rts = port, 115200, .1, False, False
    s.open()
    return s


def reboot(port):
    subprocess.run([sys.executable, '-I', str(HERE / 'enter_download.py'), port], capture_output=True, timeout=60)
    time.sleep(2)
    esptool = [sys.executable, '-m', 'esptool', '--chip', 'esp32c5', '-p', port, '--before', 'no-reset']
    subprocess.run(esptool + ['--after', 'no-reset', 'write-mem', '0x600b1034', '0x0', '0x60000000'],
                   capture_output=True, timeout=60)
    subprocess.run(esptool + ['--after', 'hard-reset', 'chip-id'], capture_output=True, timeout=60)


def read_until(port, needle, seconds):
    end, b, s = time.monotonic() + seconds, b'', None
    while time.monotonic() < end:
        try:
            if s is None:
                s = port_open(port)
            b += s.read(8192)
            if needle in b:
                break
        except Exception:
            try:
                s and s.close()
            except Exception:
                pass
            s = None
            time.sleep(.2)
    if s:
        s.close()
    return b.decode(errors='replace')


def command(port, key, seconds=3.):
    s = port_open(port)
    s.reset_input_buffer()
    s.write(key.encode())
    end, b = time.monotonic() + seconds, b''
    while time.monotonic() < end:
        b += s.read(8192)
    s.close()
    return b.decode(errors='replace')


def snapshots(port, out, tag, count):
    files = []
    for k in range(count):
        path = out / f'{tag}_{k}.bin'
        r = subprocess.run([sys.executable, '-I', str(HERE / 'capture_iq_snapshot.py'), port, str(path)],
                           capture_output=True, text=True, timeout=60)
        if path.exists():
            files.append(path.name)
        else:
            print('   snapshot refused:', (r.stdout + r.stderr).strip().splitlines()[-1:])
    return files


def status(text):
    out = {}
    for key, pat in (('gain', r'GAIN owner=\S+ mode=\S+ gain=(\d+)'),
                     ('drift_avg', r'drift_avg_mcells=(-?\d+)/(-?\d+)'),
                     ('nudges', r'DC_LIVE trials=\d+ accepted=(\d+)'),
                     ('trials', r'DC_LIVE trials=(\d+)'),
                     ('servo', r'DC_SERVO dc_mcells=(-?\d+)/(-?\d+) slope_mcells_per_code=(-?\d+)/(-?\d+) '
                               r'offset=(-?\d+)/(-?\d+) own_row=\d+ steps=(\d+) writes=(\d+)'),
                     ('bank1', r'DCO_BANKS bank1=(\d+)/(\d+)'),
                     ('bank2', r'DCO_BANKS bank1=\d+/\d+ bank2=(\d+)/(\d+)'),
                     ('bank_gain', r'DCO_BANKS .* gain=(\d+)'),
                     ('row_codes', r'row_codes=(-?\d+)/(-?\d+)'),
                     ('sphase', r'SPHASE state=(\S+)'),
                     ('profile', r'PROFILE active=(\S+ \S+)')):
        m = re.search(pat, text)
        if m:
            out[key] = [int(g) if re.fullmatch(r'-?\d+', g) else g for g in m.groups()]
            if len(out[key]) == 1:
                out[key] = out[key][0]
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('port')
    ap.add_argument('out', type=Path)
    ap.add_argument('--boots', type=int, default=6)
    ap.add_argument('--settle', type=float, default=60.)
    ap.add_argument('--snaps', type=int, default=3)
    ap.add_argument('--channel-round', action='store_true', help='after the settle: 8 x next channel (back), measure')
    ap.add_argument('--recal', action='store_true', help='after the settle: hard calibration (F), measure')
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    runs = []
    for boot in range(a.boots):
        print(f'boot {boot}: rebooting')
        reboot(a.port)
        log = read_until(a.port, b'STARTUP done', 600)
        done = re.search(r'STARTUP done .*', log)
        print('  ', done.group(0)[:150] if done else 'no STARTUP done line')
        time.sleep(3)
        run = dict(boot=boot, startup=done.group(0) if done else None,
                   early=status(command(a.port, 'T')), early_snaps=snapshots(a.port, a.out, f'b{boot}_early', a.snaps))
        time.sleep(a.settle)
        run['late'] = status(command(a.port, 'T'))
        run['late_snaps'] = snapshots(a.port, a.out, f'b{boot}_late', a.snaps)
        if a.channel_round:
            for _ in range(8):
                command(a.port, 'c', 1.5)
            time.sleep(10)
            run['round'] = status(command(a.port, 'T'))
            run['round_snaps'] = snapshots(a.port, a.out, f'b{boot}_round', a.snaps)
            print('   round', run['round'])
        if a.recal:
            command(a.port, 'F', 1.)
            log = read_until(a.port, b'STARTUP done', 600)
            time.sleep(10)
            run['recal'] = status(command(a.port, 'T'))
            run['recal_snaps'] = snapshots(a.port, a.out, f'b{boot}_recal', a.snaps)
            print('   recal', run['recal'])
        print('   early', run['early'])
        print('   late ', run['late'])
        runs.append(run)
        (a.out / 'boots.json').write_text(json.dumps(runs, indent=1))


if __name__ == '__main__':
    main()

"""C5VRX by Twotoz and the C5VRX contributors: channel link check. Simulates
fpga/rtl/channel_buttons.v and channel_link_tx.v in Icarus Verilog, decodes
the UART line and feeds the bytes to the C5 parser rules of
main/channel_link.h: silent before the first press, then index 1 (R2) after
a short press, then index 9 (A2) after a hold. Exit status 1 on failure.

    python hardware_experiments/tang9k_dual/fpga/sim/run_link_tb.py
"""
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
RTL = HERE.parent / 'rtl'


def frames(byts):
    out, st, idx = [], 0, 0
    for b in byts:                        # main/channel_link.h, transcribed
        if st == 0:
            st = 1 if b == 0xC5 else 0
        elif st == 1:
            st = 2 if b == 0x43 else (1 if b == 0xC5 else 0)
        elif st == 2:
            idx, st = b, 3
        else:
            st = 1 if b == 0xC5 else 0
            if b == (0x79 ^ idx) and idx < 48:
                out.append(idx)
                st = 0
    return out


def main():
    with tempfile.TemporaryDirectory() as tmp:
        w = Path(tmp)
        subprocess.run(['iverilog', '-g2012', '-o', str(w / 'tb'), str(HERE / 'tb_channel_link.v'),
                        str(RTL / 'channel_buttons.v'), str(RTL / 'channel_link_tx.v')], check=True)
        out = subprocess.run(['vvp', '-n', str(w / 'tb')], check=True, capture_output=True, text=True).stdout
    byts = [int(l.split()[1], 16) for l in out.splitlines() if l.startswith('BYTE')]
    fr = frames(byts)
    framing = out.count('FRAMING')
    seq = [f for i, f in enumerate(fr) if i == 0 or f != fr[i - 1]]
    print(f'{len(byts)} bytes, {len(fr)} frames, framing errors {framing}, channel sequence {seq}')
    ok = framing == 0 and seq == [1, 9] and len(fr) >= 10
    print('PASS' if ok else 'FAIL')
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()

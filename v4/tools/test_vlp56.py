#!/usr/bin/env python3
"""Source-driven native pair-demod tests. No NumPy/IDF dependency.

Host dataflow evidence only; board LUT8 addressing, throughput, waveform,
physical continuity and RF threshold must still be measured.
"""
from pathlib import Path
import hashlib
import bs_model as bs
from generate_vlp56 import build, load, TARGET
ROOT = Path(__file__).resolve().parents[1]


def main():
    data = load(); enc, table = data['encoder'], data['map']
    source = TARGET.read_text()
    assert source == build(), 'stale VLP56 program'
    cfg, lut, blocks, labels = bs.parse(source)
    assert cfg['lut_width_bits'] == '8' and len(lut) == 2048
    assert cfg['trailing_bytes'] == '0' and cfg['eof_on'] == 'downstream'
    assert len(blocks) == 8 and len(labels) == 8
    assert lut[1792:] == enc
    assert all(not any(line.startswith(('jmp','if ')) for line in b) for b in blocks)
    raw = bytearray()
    for prev in range(256):
        for current in range(256):
            raw.extend((prev, 0, current, 0))
    raw.extend(bytes(8))
    stats = {}; out = bs.simulate(source, raw, len(raw), stats=stats, wrap_rom=True)
    assert stats['bundles'] == len(out) - 1 # Final output emits in the controller.
    assert all(a == b and a < 64 for a,b in zip(out[::2],out[1::2]))
    for pair in range(65536):
        p,c = pair >> 8,pair & 255
        assert out[pair * 4 + 4] == table[enc[p] >> 1][enc[c]]
    # Arbitrary initial counter state self-primes from the first encoder token.
    for b in (0, 0xffff, 0xcccc):
        out = bs.simulate(source, raw[:256], 256, initial=(0,0,b,0), wrap_rom=True)
        tokens = [enc[x] for x in raw[:256:2]]
        assert all(out[2*k+4] == table[tokens[k]>>1][tokens[k+1]]
                   for k in range(len(tokens)-2))
    hc = (ROOT/'firmware/programs/c5vrx4_hc50.bsasm').read_text()
    _, hlut, _, _ = bs.parse(hc)
    raw = bytearray((i * 73 + 41) & 255 for i in range(8192))
    out = bs.simulate(hc, raw, len(raw), wrap_rom=True)
    prev = None; bank = 0; expected = []
    for r in raw[1::2]:
        phase = hlut[bank*256+r] >> 8
        if prev is not None: expected.append(((128+phase-prev)&255)>>2)
        bank,prev = phase//64,phase
    assert out[4::2] == expected[:len(out[4::2])]
    transport=(ROOT/'main/video_transport.c').read_text()
    assert 'case C5VRX4_DEMOD_VLP56: return s_vlp56_program;' in transport
    assert 'case C5VRX4_DEMOD_HC50: return s_hc50_program;' in transport
    menu=(ROOT/'main/video_menu.c').read_text()
    assert 'SETUP_ITEM_DEMOD' in menu and "c5vrx4_console('g')" in menu
    pipeline=(ROOT/'firmware/pipeline.c').read_text()
    assert 'uint8_t value = C5VRX4_DEMOD_VLP56;' in pipeline
    assert 'mode = value < C5VRX4_DEMOD_COUNT' in pipeline
    assert 'return true;' in pipeline.split('bool c5vrx4_reference_demod(void)',1)[1].split('}',1)[0]
    assert 's_menu_timeout_ticks' not in '\n'.join(p.read_text() for p in (ROOT/'main').glob('video*'))
    print('PASS VLP56: all 65536 endpoint pairs, LUT8/2KiB, two bundles, [D,D], startup recovery, native selection/menu; HC50 recursion')

if __name__ == '__main__':
    main()

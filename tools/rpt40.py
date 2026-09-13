"""Golden-preserving RPT40 reference. No trained LUT or live-quality claim."""
from dataclasses import dataclass, field
from validate_phase5_quality import build_lut, phase5_state


def packed_state(raw):
    # Signed doubled Q4/I4 bucket centres: preserve all eight source bits.
    q=2*(raw&15)+1
    i=2*(raw>>4)+1
    q=q-32 if q>=16 else q
    i=i-32 if i>=16 else i
    radius2=q*q+i*i
    confidence=sum(radius2>edge for edge in (18,50,98,162,242,338,450))
    return phase5_state(raw)|(confidence<<5)


@dataclass
class Tracker:
    gain: int = 2
    pedestal: int = 20
    suppress: bool = False
    phases: list = field(default_factory=lambda:[0,0])
    confidence: list = field(default_factory=lambda:[0,0])
    codes: list = field(default_factory=list)
    index: int = 0
    suppressed: int = 0

    def __post_init__(self):
        self.lut=build_lut(self.gain,self.pedestal)
        if not self.codes: self.codes=[self.pedestal,self.pedestal]

    def feed(self, raw):
        result=[]
        for byte in raw:
            state=packed_state(byte); phase=state&31; confidence=state>>5
            parity=self.index&1
            normal=self.lut[(self.phases[parity]<<5)|phase]&63
            # Experimental local erasure policy. Never use an unscaled
            # longer-span phase delta: reacquire only on two good endpoints.
            bad=confidence==0 or self.confidence[parity]==0
            if self.suppress and bad:
                code=self.codes[parity]
                self.suppressed+=1
            else:
                code=normal
            self.phases[parity]=phase
            self.confidence[parity]=confidence
            self.codes[parity]=code
            self.index+=1
            result.append(code)
        return result


def rx_source():
    lut=[packed_state(i) for i in range(256)]
    return ('# Generated from full Golden Q4/I4 mapping; confidence in bits 7:5.\n'
            '# One prime, one repeating bundle/byte. Physical RX40 gate required.\n'
            'cfg prefetch true\ncfg eof_on upstream\ncfg trailing_bytes 9\n'
            'cfg lut_width_bits 16\nlut '+' '.join(map(str,lut))+'\n\n'
            'prime:\n    set 16..23 0..7,\n    read 8\n\n'
            'map:\n    set 0..7 L0..L7,\n    set 16..23 0..7,\n'
            '    read 8,\n    write 8,\n    jmp map\n')


def tx_source():
    # The input register itself retains the two interleaved histories:
    # p[n-2] at byte 0 and p[n] at byte 2; advance one byte per output.
    lut=[v&63 for v in build_lut()]
    return ('# Cadence-only RPT40 kernel: unchanged Golden 50 ns transfer.\n'
            '# Confidence is carried by RX but NOT suppressed by this kernel.\n'
            '# First output corresponds to input index 2; no reset at ring wrap.\n'
            'cfg prefetch true\ncfg eof_on upstream\ncfg trailing_bytes 9\n'
            'cfg lut_width_bits 16\nlut '+' '.join(map(str,lut))+'\n\n'
            'prime:\n    set 16..20 16..20,\n    set 21..25 0..4,\n    read 8\n\n'
            'demod:\n    set 0..5 L0..L5,\n    set 16..20 16..20,\n'
            '    set 21..25 0..4,\n    read 8,\n    write 8,\n    jmp demod\n')

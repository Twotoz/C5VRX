"""C5VRX/Twotoz immutable synthetic-IQ insertion geometry, no firmware.

Conditional geometry only: assumes stable nominal camera clock, four-byte
descriptor boundaries and uninterrupted transport. Does not establish those
assumptions, live descriptor ownership, acquisition, V timing or colour.
"""
from fractions import Fraction
from math import gcd
import json
from pathlib import Path


def analyze(name, period, ring=32768):
    # Quantize each boundary to four input bytes (100ns). No growing drift:
    # ties use deterministic floor rounding, not Python bankers rounding.
    units=period/4
    block_lines=units.denominator
    block_bytes=int(block_lines*period)
    repeats=ring//gcd(ring,block_bytes)
    lines=block_lines*repeats
    total=block_bytes*repeats
    assert total%ring==0
    bounds=[4*((i*units.numerator+units.denominator//2)//units.denominator)
            for i in range(block_lines+1)]
    errors=[abs(Fraction(bounds[i])-i*period) for i in range(block_lines+1)]
    # Each replaced sync needs a template descriptor and at least one live
    # segment. Raw-ring crossings can add further descriptors.
    return dict(standard=name,ring_bytes=ring,nominal_line_bytes=str(period),
        aligned_line_pattern=[bounds[i+1]-bounds[i] for i in range(block_lines)],
        maximum_rounding_ns=float(max(errors))*25,
        immutable_repeat_lines=lines,immutable_repeat_bytes=total,
        repeat_ms=total/40000,descriptor_lower_bound=2*lines,
        descriptor_bytes_lower_bound_at_12_bytes=24*lines,
        caveat='H_only_geometry_not_safe_DMA_or_valid_CVBS')


if __name__=='__main__':
    pal=Fraction(40000000,15625);ntsc=Fraction(40000000*1001,15750000)
    result=[analyze('PAL',pal),analyze('NTSC',ntsc),
            analyze('PAL-commensurate-experimental',pal,30720),
            analyze('NTSC-commensurate-experimental',ntsc,22880)]
    target=Path(__file__).resolve().parents[2]/'docs/data/local_joint/descriptor-geometry.json'
    target.write_text(json.dumps(result,indent=1))
    print(json.dumps(result))

"""Signed-Q10 ADC to actual C5VRX sign-preserving capture lanes.

See main/rf.c: coarse{9,8,7,6},fine{9,7,6,5},ultrafine{9,6,5,4}.
RMS is expressed in local near-origin quantization cells, not RF gain/dBm.
Analog ADC calibration and noise are not established by this bit model.
"""
import numpy as np


def quantize(z,lane):
    shifts={'coarse':6,'fine':5,'ultrafine':4}
    if lane not in shifts:raise ValueError('unknown capture lane')
    shift=shifts[lane]
    def component(x):
        adc=np.clip(np.floor(x*(1<<shift)),-512,511).astype(np.int32)&1023
        return (((adc>>9)<<3)|((adc>>shift)&7)).astype(np.uint8)
    return (component(z.real)<<4)|component(z.imag)

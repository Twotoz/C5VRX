"""Explicit capture-lane profile for C5VRX range searches and confirmation.

Default is the fixed fine lane {9,7,6,5} at nominal RMS 3 cells, unchanged and
bit-identical. C5VRX4_SEARCH_LANE / C5VRX4_NOMINAL_RMS select another lane and
amplitude scale (for example ultrafine at the board's edge noise). Protocols
record the profile; validation refuses a search made with another profile.
"""
import os

LANE=os.environ.get('C5VRX4_SEARCH_LANE','fine')
RMS=float(os.environ.get('C5VRX4_NOMINAL_RMS','3'))
if LANE not in ('coarse','fine','ultrafine') or not 0.5<=RMS<=8:
    raise ValueError('unsupported lane profile')


def scale(rms):
    # Keep the default exactly unchanged rather than merely numerically close.
    return rms if RMS==3 else rms*RMS/3


def record():
    return dict(lane=LANE,nominal_rms=RMS)

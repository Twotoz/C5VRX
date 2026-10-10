# Archived physical IQ fixtures

Original C5VRX recordings by Twotoz / the C5VRX contributors, exported with
the existing guarded IQ40 snapshot protocol on 2026-10-08.
[Source project](https://github.com/Twotoz/C5VRX),
[official website and Discord invite](https://twotoz.github.io/C5VRX/).

`strong-01/02` retain original strong-static snapshots `iq-1/2` from the
20:40 UTC session. `weak-02/03` retain original weak-static snapshots `iq-2/3`
from the 20:41 UTC session. These labels are recording groups, not calibrated
C/N or RF attenuation. Metadata are copied unchanged. Raw binary bytes use
the `.iq` suffix to distinguish research inputs from ignored firmware `.bin`.

Each input is 8,190 acquired I-high/Q-low bytes, 40 MS/s, ultrafine lane 2,
5865 MHz. The FNV-1a values and timestamps are in the sibling JSON files;
the replay manifest additionally records SHA-256. Capture-start filter state,
video truth and inter-capture gaps are unknown. These pre-DCO-correction
fixtures are reproducible replay checks, not current-board range measurements
or a continuous sequence of an actual feed dropout.

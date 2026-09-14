import math

s_phase5_centroid_phase8 = [
       0,    8,   15,   24,   32,   40,   49,   56,
      64,   72,   79,   87,   96,  104,  113,  120,
    -128, -120, -113, -104,  -96,  -88,  -79,  -72,
     -64,  -56,  -49,  -40,  -32,  -23,  -15,   -8,
]

def get_synclock_dac_code(delta_phase8):
    # delta_phase8 is in -128..+127
    # delta_f in MHz: delta_phase8 * (20.0 / 256.0)
    delta_f = delta_phase8 * (20.0 / 256.0)
    
    if delta_f >= 0:
        # Smooth luminance saturation up to ~58
        val = 20.0 + 38.0 * math.tanh(delta_f / 2.2)
    else:
        # Resonance curve centered around sync frequency (-2.1 MHz):
        # We want the minimum to reach 0 at delta_f = -2.1 MHz
        # Width sigma around 1.1 MHz
        # dev reaches -20 at delta_f = -2.1
        # At delta_f = 0: dev is around 0
        # At delta_f < -4.0 MHz: dev drops back towards 0 (val -> 20)
        # Resonance Gaussian:
        gauss = math.exp(-((delta_f + 2.1) / 1.1) ** 2)
        # Also ensure at delta_f = 0 it matches 20 smoothly:
        g0 = math.exp(-((0.0 + 2.1) / 1.1) ** 2) # approx exp(-3.64) = 0.026
        dev = -20.0 * (gauss - g0) / (1.0 - g0)
        val = 20.0 + dev

    code = int(round(val))
    return max(0, min(63, code))

print("Delta (phase8) | Delta (MHz) | DAC Code | Slicer State (< 10 is SYNC)")
for d8 in range(-60, 60, 4):
    df = d8 * (20.0 / 256.0)
    c = get_synclock_dac_code(d8)
    state = "SYNC" if c < 10 else "VIDEO"
    print(f"{d8:14d} | {df:10.2f} | {c:8d} | {state}")

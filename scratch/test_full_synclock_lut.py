import math

s_phase5_centroid_phase8 = [
       0,    8,   15,   24,   32,   40,   49,   56,
      64,   72,   79,   87,   96,  104,  113,  120,
    -128, -120, -113, -104,  -96,  -88,  -79,  -72,
     -64,  -56,  -49,  -40,  -32,  -23,  -15,   -8,
]

def get_synclock_dac_code(delta_phase8):
    delta_f = delta_phase8 * (20.0 / 256.0)
    if delta_f >= 0:
        val = 20.0 + 38.0 * math.tanh(delta_f / 2.2)
    else:
        gauss = math.exp(-((delta_f + 2.1) / 1.1) ** 2)
        g0 = math.exp(-((0.0 + 2.1) / 1.1) ** 2)
        dev = -20.0 * (gauss - g0) / (1.0 - g0)
        val = 20.0 + dev
    code = int(round(val))
    return max(0, min(63, code))

codes = []
for prev_ph in range(32):
    for curr_ph in range(32):
        delta = s_phase5_centroid_phase8[curr_ph] - s_phase5_centroid_phase8[prev_ph]
        if delta >= 128:
            delta -= 256
        if delta < -128:
            delta += 256
        c = get_synclock_dac_code(delta)
        codes.append(c)

print(f"Total delta entries: {len(codes)}")
print(f"Code 0 count: {codes.count(0)} ({codes.count(0)/len(codes)*100:.1f}%) [Old was 40.6%]")
print(f"Code < 10 count: {sum(1 for c in codes if c < 10)} ({sum(1 for c in codes if c < 10)/len(codes)*100:.1f}%) [Old was 46.6%]")
print(f"Code 63 count: {codes.count(63)} ({codes.count(63)/len(codes)*100:.1f}%) [Old was 28.4%]")

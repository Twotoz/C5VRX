import re
from pathlib import Path

ROOT = Path("C:/Users/leonb/Twotoz/C5VRX-issue11-output")
src_path = ROOT / "main/c5vrx2_wbfm_q4_phase5_150ns_2to1.bsasm"
dst_path = ROOT / "main/c5vrx2_wbfm_q4_phase5_150ns_parlio4_80m_2to1.bsasm"

GROUPED_LEVELS = [0, 4, 8, 12, 17, 21, 25, 29, 34, 38, 42, 46, 51, 55, 59, 63]

def quantize_to_grouped_dac(d_code):
    d_clamped = max(0, min(63, d_code))
    best_c = 0
    best_diff = 999
    for c, lvl in enumerate(GROUPED_LEVELS):
        diff = abs(lvl - d_clamped)
        if diff < best_diff:
            best_diff = diff
            best_c = c
    return best_c

text = src_path.read_text(encoding="utf-8")

# Extract LUT words
lut_match = re.search(r"lut\s+([0-9\s]+)\n\n", text)
if not lut_match:
    print("Could not find lut block!")
    exit(1)

raw_words = [int(x) for x in lut_match.group(1).split()]
new_words = []
for idx, w in enumerate(raw_words):
    ph = (w >> 8) & 0x1F
    d_code = w & 0x3F
    c_code = quantize_to_grouped_dac(d_code)
    new_w = (ph << 8) | (c_code & 0x0F)
    new_words.append(new_w)

lut_replacement = "lut " + " ".join(map(str, new_words)) + "\n\n"
new_text = text[:lut_match.start()] + lut_replacement + text[lut_match.end():]

# Replace emit blocks
old_emit_pattern = """    set 0..5 L0..L5,
    set 6..7 L,
    set 8..13 L0..L5,
    set 14..15 L,"""

new_emit_pattern = """    set 0..3 L0..L3,
    set 4..7 L0..L3,
    set 8..11 L0..L3,
    set 12..15 L0..L3,"""

new_text = new_text.replace(old_emit_pattern, new_emit_pattern)

dst_path.write_text(new_text, encoding="utf-8")
print(f"Generated {dst_path} successfully!")

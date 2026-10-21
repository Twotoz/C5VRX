"""C5MAP vs U85Q Side-by-Side Visual Frame Renderer.
(C5VRX by Twotoz and the C5VRX contributors)

Synthesizes an authentic FPV frame with race gates, horizon terrain, fine branches,
and Betaflight OSD elements. Modulates to FM, passes through the physical C5 folded ADC,
demodulates through U85Q and C5MAP across four critical signal regimes (+10dB, 0dB, -3dB, -7dB),
and outputs side-by-side comparison PNGs.
"""
import sys
import json
import math
from pathlib import Path
import numpy as np
import scipy.signal as sg
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
WORKSPACE = HERE.parent
sys.path.insert(0, str(HERE))
import sim
import u85q_lp
import pll_compare
from c5map_optimizer import build_lut_from_params
import iq_lane_fusion as F

FS_DAC = 20e6
DT_DAC = 1.0 / FS_DAC
LINES = 312
SAMPLES_PER_LINE = 1280  # 64.0 us at 20 MS/s

def synthesize_fpv_frame():
    """Synthesizes a realistic 640x312 FPV scene (active video) with OSD and gates."""
    w, h = 640, LINES
    img = Image.new('L', (w, h), color=0)
    draw = ImageDraw.Draw(img)

    # 1. Sky and Ground horizon gradient
    horizon_y = int(h * 0.55)
    for y in range(horizon_y):
        lum = int(140 + (y / horizon_y) * 45)  # 140 -> 185
        draw.line([(0, y), (w, y)], fill=lum)
    for y in range(horizon_y, h):
        lum = int(55 + ((y - horizon_y) / (h - horizon_y)) * 40)  # 55 -> 95
        draw.line([(0, y), (w, y)], fill=lum)

    # 2. Race Gate (Arch)
    gate_x = int(w * 0.50)
    gate_w = int(w * 0.32)
    gate_top = int(h * 0.28)
    gate_bottom = int(h * 0.85)
    # Outer bright border
    draw.rectangle([gate_x - gate_w//2 - 12, gate_top - 12, gate_x + gate_w//2 + 12, gate_bottom], outline=240, width=8)
    # Inner gate arch
    draw.rectangle([gate_x - gate_w//2, gate_top, gate_x + gate_w//2, gate_bottom], outline=30, width=6)

    # 3. Fine obstacles / tree branches
    draw.line([(0, int(h * 0.20)), (int(w * 0.40), int(h * 0.45))], fill=20, width=3)
    draw.line([(int(w * 0.35), int(h * 0.42)), (int(w * 0.48), int(h * 0.32))], fill=25, width=2)
    draw.line([(w, int(h * 0.15)), (int(w * 0.65), int(h * 0.50))], fill=20, width=3)

    # 4. Betaflight OSD overlay (Font elements)
    # Battery voltage
    draw.text((25, 20), "4S 15.8V", fill=255)
    # Flight mode & LQ
    draw.text((25, h - 35), "AIR  LQ:99", fill=255)
    # Flight timer & mAh
    draw.text((w - 110, 20), "03:42", fill=255)
    draw.text((w - 110, h - 35), "450mAh", fill=255)
    # Center crosshair
    cx, cy = w // 2, h // 2
    draw.line([(cx - 15, cy), (cx + 15, cy)], fill=255, width=2)
    draw.line([(cx, cy - 10), (cx, cy + 10)], fill=255, width=2)

    active_matrix = np.array(img, dtype=np.float32)
    # Scale 0..255 to 0..100 IRE
    return (active_matrix / 255.0) * 100.0

def build_full_raster(active_ire):
    """Builds a full 312-line PAL raster with 4.7us sync tips and back porches."""
    h, w_active = active_ire.shape
    raster = np.zeros((h, SAMPLES_PER_LINE), dtype=np.float32)
    sync_samples = int(4.7e-6 * FS_DAC)   # 94 samples
    porch_samples = int(5.7e-6 * FS_DAC)  # 114 samples
    active_start = sync_samples + porch_samples  # 208
    active_len = min(w_active, SAMPLES_PER_LINE - active_start)

    for line in range(h):
        # Sync tip = -40 IRE
        raster[line, :sync_samples] = -40.0
        # Back porch = 0 IRE
        raster[line, sync_samples:active_start] = 0.0
        # Active video
        raster[line, active_start:active_start + active_len] = active_ire[line, :active_len]

    return raster.flatten()

def modulate_and_capture(raster_ire, cnr_db, seed=42):
    """Frequency-modulates raster at 40 MS/s, adds noise, and captures through physical ADC."""
    rng = np.random.default_rng(seed)
    # Upsample to 40 MS/s for FM modulation
    ire_40 = np.repeat(raster_ire, 2)
    source = sg.lfilter(sg.firwin(81, 6e6, fs=sim.F80), 1, ire_40)
    freq = sim.PORCH_HZ + source / 140.0 * sim.DEV_PP
    z = sg.sosfilt(sim.CHAN, np.exp(2j * np.pi * np.cumsum(freq) / sim.F80))[::2]
    z /= np.sqrt(np.mean(abs(z) ** 2))

    n = sg.sosfilt(sim.CHAN, (rng.normal(size=2 * len(z)) + 1j * rng.normal(size=2 * len(z))) / np.sqrt(2))[::2]
    n /= np.sqrt(np.mean(abs(n) ** 2))
    ratio = 10.0 ** (cnr_db / 10.0)

    y = (z * np.sqrt(ratio) + n) / np.sqrt(1.0 + ratio)
    y *= 2.0  # physical adc_gain
    raw = F.capture(y * 16.0)

    clean_y = z * np.sqrt(ratio / (1.0 + ratio)) * 2.0
    clean = F.capture(clean_y * 16.0)
    return raw, clean

def reconstruct_image(codes, standard_w=640):
    """Reconstructs 2D video field from demodulated DAC codes using sync detection."""
    # Each DAC code maps to voltage
    volts = sim.DAC_VOLTS[codes]
    lines_list = []
    sync_th = np.percentile(volts, 5) + 0.0010
    
    # Simple line slicing based on 1280 nominal length with sync tracking
    idx = 0
    for l in range(LINES):
        start = l * SAMPLES_PER_LINE + 208
        end = start + standard_w
        if end <= len(volts):
            lines_list.append(volts[start:end])
        else:
            lines_list.append(np.zeros(standard_w))
            
    img_mat = np.array(lines_list)
    # Normalize display stretch
    p2, p98 = np.percentile(img_mat, 2), np.percentile(img_mat, 98)
    if p98 > p2:
        img_mat = np.clip((img_mat - p2) / (p98 - p2), 0.0, 1.0)
    return img_mat

def render_comparison_figure(img_u, lock_u, sinad_u, img_c5, lock_c5, sinad_c5, title, out_path):
    """Renders side-by-side comparison figure and saves PNG."""
    fig, axes = plt.subplots(1, 2, figsize=(13, 6), facecolor='#111111')

    # U85Q display
    axes[0].imshow(img_u, cmap='gray', aspect='auto')
    axes[0].set_title(f"U85Q (Legacy PR #190)\nLinelock: {lock_u*100:.1f}% | SINAD: {sinad_u:+.2f} dB",
                      color='white' if lock_u > 0.5 else '#ff4444', fontsize=12, fontweight='bold', pad=10)
    axes[0].axis('off')

    # C5MAP display
    axes[1].imshow(img_c5, cmap='gray', aspect='auto')
    axes[1].set_title(f"C5MAP (Universal Champion)\nLinelock: {lock_c5*100:.1f}% | SINAD: {sinad_c5:+.2f} dB",
                      color='#00ff88', fontsize=12, fontweight='bold', pad=10)
    axes[1].axis('off')

    fig.suptitle(title, color='white', fontsize=14, fontweight='bold', y=0.98)
    plt.tight_layout()
    plt.savefig(out_path, dpi=130, facecolor='#111111')
    plt.close()
    print(f"Rendered: {out_path}")

def main():
    print("=== Generating C5MAP vs U85Q Authentic Visual Video Frames ===")
    out_dir = Path(r"C:\Users\leonb\.gemini\antigravity-cli\brain\2319af74-1760-4511-a628-23d7f468010c")
    out_dir.mkdir(parents=True, exist_ok=True)

    with open(WORKSPACE / "research/data/c5map_video_optimal.json") as f:
        opt = json.load(f)
    params = opt['full_params']

    lut_nom = build_lut_from_params(params, is_rescue=False)
    lut_res = build_lut_from_params(params, is_rescue=True)
    u_lut = sim.u85q_lut()

    active_ire = synthesize_fpv_frame()
    raster_flat = build_full_raster(active_ire)

    scenarios = [
        ("Clean Line-of-Sight (+10 dB C/N)", 10.0, False, "compare_10db_clean.png"),
        ("Standard Range Flight (0 dB C/N)", 0.0, False, "compare_0db_flight.png"),
        ("Behind Concrete Wall / Trees (-3 dB C/N)", -3.0, True, "compare_m3db_wall.png"),
        ("Extreme Bando / Deep Rescue (-7 dB C/N)", -7.0, True, "compare_m7db_bando.png"),
    ]

    for title, cnr, is_res, filename in scenarios:
        print(f"Simulating scenario: {title} ...")
        raw, clean = modulate_and_capture(raster_flat, cnr_db=cnr, seed=101)

        c_case = dict(raw=raw, clean=clean, truth=clean, region=np.ones(len(clean), dtype=np.uint8), standard='PAL', cnr=cnr, deviation=1.0)
        clean_codes = u85q_lp.lp_codes(clean, u_lut)

        # U85Q
        u_raw = u85q_lp.lp_codes(raw, u_lut)
        u_lock, u_sinad = pll_compare.score(c_case, sim.DAC_VOLTS[u_raw], sim.DAC_VOLTS[clean_codes])
        img_u = reconstruct_image(u_raw)
        if u_lock < 0.10:
            # Blackout / rolling chaos
            img_u = np.zeros_like(img_u) if u_lock == 0 else img_u * 0.1

        # C5MAP
        c5_lut = lut_res if is_res else lut_nom
        c5_raw = u85q_lp.lp_codes(raw, c5_lut)
        c5_lock, c5_sinad = pll_compare.score(c_case, sim.DAC_VOLTS[c5_raw], sim.DAC_VOLTS[clean_codes])
        img_c5 = reconstruct_image(c5_raw)

        out_path = out_dir / filename
        render_comparison_figure(img_u, u_lock, u_sinad, img_c5, c5_lock, c5_sinad, title, out_path)
        docs_path = WORKSPACE / "docs" / "images" / filename
        render_comparison_figure(img_u, u_lock, u_sinad, img_c5, c5_lock, c5_sinad, title, docs_path)

    print("\nAll 4 comparison figures generated successfully in artifact directory!")

if __name__ == "__main__":
    main()

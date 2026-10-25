# HDZero CVBS compatibility experiment

C5VRX by Twotoz and the C5VRX contributors. Extends the manual OUTPUT work
from issue #200, the native waveform path and flywheel witness work in PR #218.
Based on main `78ff43a`. This is a targeted test build, not measured HDZero
acceptance or a receiver sensitivity improvement.

HDZero users still report rolling/flickering even with higher OUTPUT settings.
Issue #200 established that increased output helped one low-deviation VTX;
`FINDINGS.md` also records a separate case where flywheel OFF stopped rolling.
These clues do not establish one cause for all goggles/cameras.

## Changes

- First boot selects **OUTPUT HDZERO** and **FLYWHEEL OFF**, even if a previous
  normal build saved another multiplier or flywheel ON. OFF keeps the ordinary
  six-bit native waveform path; there is no flywheel observer, repair alarm or
  comparator-format transition until the user explicitly turns it ON.
- HDZERO uses a **fixed three-times slope around sync, blanking and burst**.
  Canonical code 21 is the existing AFC -1 MHz porch target. The map sends
  15/21/27 to 6/24/42. A six-code sync depth becomes eighteen codes; frequency
  centring remains the job of the existing AFC, not a new output servo.
- Above canonical code 27 a continuous highlight knee maps 27..63 to 42..63.
  This retains bright-scene variation where a plain 3x multiplier would clip.
  It changes highlight contrast and quantizes some adjacent codes together.
- The ten existing 0.5x..5x multipliers keep their exact mappings and indices.
  HDZERO is appended as output ID 10 (also in flight logs). Menu OUTPUT and
  console `g` still cycle all levels. All updates use the existing guarded,
  verified halted restart and rollback; no writes under the running DSP.

Test settings use `c5vrx5/hdz_output` and `c5vrx5/hdz_flywheel`. Old `output`
and `flywheel` blobs are neither read nor overwritten. A deliberately selected
experimental level or flywheel ON survives a reboot **of this build**. Returning
to ordinary main restores its original saved choices. Channel/standard and RF
calibration persistence are shared as before. These isolated keys/defaults are
for this experiment; remove them or decide an explicit opt-in policy before
promoting the experiment to general production behavior.

The curve only changes DAC6 bits in the LUT; tracker metadata, p64.bsasm,
raw-ring format, two lookups, unique DAC20/TX40 cadence and DAC pins/resistors
are unchanged. It does not detect or regenerate sync. A point transform cannot
repair missing pulses or a malformed camera vertical interval. Normal PAL and
NTSC timing comes from the camera. There is no periodic menu/black raster or
automatic LUT retuning which could itself cause a repeated relock.

## Limitations

A stronger digital sync step is **not** a guaranteed 0.3 V physical pulse.
The resistor network, GPIO impedance, cable and actual 75-ohm load still limit
voltage. Do not infer physical compliance from DAC codes. Negative excursions
below code 13 saturate at zero; wide-deviation sync may flatten/broaden. An
unusually large burst or residual CFO can cross the knee and distort colour.
Highlights are intentionally compressed. Noise below the pedestal is amplified
along with sync; weak RF may still fail to lock. The patch cannot recover RF
information or change sensitivity. A continuously monotone map preserves clean
sample-slot boundaries, not guaranteed analog decoder threshold crossings.

## Bench comparison on the affected goggles

1. Record HDZero model/firmware, camera/VTX, PAL/NTSC, load/cable, and `T` output.
   First flash should show `OUTPUT level=HDZERO` and
   `SYNC_FLYWHEEL requested=0 boot=saved_or_default_off`. If reusing this test
   build's settings, select HDZERO/OFF manually. Wait for startup to finish.
2. With a strong VTX, compare HDZERO/OFF, 1x/OFF, and the previously useful
   multiplier/OFF on the same scene. Test a moving bright object, dark scene,
   saturated colours and menu entry/exit. Observe rolling, flicker, colour loss
   and clipping separately. Live changes briefly interrupt video; wait at least
   five seconds between them and exclude those deliberate gaps from the result.
3. Compare the best curve with flywheel OFF versus an explicit ON, then OFF.
   Do this on the bench: prior rolling is documented; do not use flight as the
   first test. A saved test ON remains ON until changed back.
4. Repeat with gradual RF attenuation, short fades, VTX OFF/ON, reboot and
   channel changes; repeat for both PAL and NTSC and narrow/wide-deviation VTXs.
5. Scope the DAC into the actual 75-ohm load: sync tip, blanking pedestal,
   white, burst, H pulse width and vertical interval before/after. Compare
   simultaneous capture or another display with HDZero to separate input loss
   from a goggle decoder relock. Attach `T`/flight logs and video of the result.

Host tests cover all 65,536 LUT words, unchanged legacy maps, nominal burst
linearity, monotonicity, bright-level separation, clean H/V sample slots,
settings isolation, save/reboot/errors, and the existing actual-source
publication/rollback fixtures across all eleven output choices. SDK/CI build
and software checks do not prove physical HDZero lock; the above board tests
are required before calling the problem fixed.

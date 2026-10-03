# HDZero AV-in: CVBS swing regression

Report: current firmware produced only 0.2–0.3 Vpp at HDZero Goggles AV-in.
The same setup displayed and recorded video with v3.18.1. The reporter did
not provide the exact newer version, camera/VTX, scope termination or resistor
measurements; a physical voltage fix is not yet hardware-confirmed.

## Source evidence and fix

v3.18.1 selects the Golden Phase5 endpoint transfer. Later production builds
enabled `C5VRX_PHASE8_HR_LIVE_TEST` by default. This selects
`fm_phase8_hr_live.bsasm` before the Golden branch in
`start_flight_demodulator()`. Its generated output is
`((128 + wrapped_phase8_delta) & 255) >> 2`: pedestal 32 and slope
0.25 DAC codes per Phase8 bin. Golden's normal transfer has pedestal 20 and
approximately 6 codes per Phase5 bin (one Phase5 bin is eight Phase8 bins):
0.75 codes per Phase8 bin, approximately three times the slope. The
historical P20/G2 label alone does not describe the embedded LUT's slope.

Thus using all 64 codes over +/-180 degrees does not mean normal CVBS uses
all 64 codes. The smaller sync-to-white excursion explains an amplitude
regression, although its precise loaded Vpp remains a board measurement.

Production now disables that experiment in both Kconfig and sdkconfig.defaults.
It selects the same `fm_phase5_360.bsasm` Golden transfer as v3.18.1, with
Direct Gain V5 retained. The two worker branches already emit identical Golden
DAC values. Pin order, resistor network, 40 MHz TX, 20 MS/s unique output and
Zero-EOF transport remain unchanged. C5VRX-4 is a separate experiment and is
not amplitude-qualified by this fix.

Full-range Phase8 can still be built explicitly using menuconfig. Do not
simply multiply its arithmetic phase terms by three: the 8-bit counter mapping
would wrap outside the smaller interval and corrupt sync/bright pixels.
A future high-resolution replacement needs a saturating calibrated transfer
and loaded-output validation before becoming the default.

## Validation

The build validator checks production selection and fingerprints all 1,024
Golden DAC entries against v3.18.1, then checks the selected Phase5 LUT and
both worker output routes. Existing hardware-program host models check
transport/state invariants. These checks establish source-level restoration,
not measured volts or HDZero lock.

Required hardware A/B: same board, cable, camera/VTX, channel and RF level;
compare v3.18.1, the affected newer release and this PR using full firmware.
Measure sync tip, blanking and white at the AV connector under the actual
75-ohm input load (avoid an additional parallel 75-ohm termination). Verify
PAL and NTSC picture lock and recording, menu entry/exit and a power cycle.
Record exact firmware SHA, output mode and scope setup. Check the resistor
network/loading if swing remains low with the restored transfer.

This extends the Golden/DAC work by Leon Beekveldt (Twotoz) and the C5VRX
contributors, recorded in `AGENTS.md`, `docs/pr-derived-findings.md` and the
archived `legacy/c5vrx1/research/video-output.md`.

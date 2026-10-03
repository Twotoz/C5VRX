# C5VRX-3 experimental CVBS level regulation

This ports the voltage-domain slew and evidence gates from C5VRX-4 PR #162
(`f96ad94adb6a65bf62812f03f2090f3f195d45ba`,
`experiments/c5vrx-4/cvbs_level.c`, `cvbs_level_hw.c`, `cvbs_monitor.c` and
`cvbs_tables.h`) into the root `main/` application. It extends the Golden and
DAC research by Leon Beekveldt (Twotoz) and the C5VRX contributors.

Send lowercase `u` on USB to enable/disable and reboot. This stores a separate
`c5vrx/level_lab` NVS setting; it is **off by default**. `T` reports
`C5V3_LEVEL` status, successful updates, refused evidence and register faults.

Enabling selects the existing `fm.bsasm` Golden two-bundle path and fixes
6BIT@40. Root main currently defaults to Phase8 HR, whose output is a fixed
Counter-A bit slice rather than a DAC lookup; applying the C5VRX-4 LUT patch
to it would corrupt phase decoding without regulating output. This port
therefore deliberately makes the demodulator change explicit. Disabling `u`
restores the usual compiled demodulator, including the saved HC choice.
There is no H/V flywheel, invented sync or CPU replacement of live video.

The source remains Q4/I4 MODEM_DIAG at 40 MS/s in the unchanged cyclic raw ring.
TX-only Golden retains phase across descriptor boundaries; two bundles produce
20 MS/s unique DAC codes duplicated as `[D,D]` at the physical 40 MHz output.
The 50 ms control task copies an 8192-byte completed window, checking both
forward DMA progress and a deadline calculated from actual ring/active bytes.
It discards copies or analysis spanning tracked gain/PHY/profile changes,
waits at least 2 ms after tracked writes, and refuses native-AGC windows.
The local odd-endpoint alignment is semantic evidence, not tagged TX output.

The observer models the original Golden LUT, finds negative sync plateaus and
a short pre-burst porch, and requires repeated PAL/NTSC line spacing, suitable
sync depth, low plateau MAD and bounded origin/ADC clipping. Three consistent
consecutive windows are required. Gaps over 200 ms, duplicate/reversed times,
context changes and bad evidence reset that chain. An output scale update is
allowed at most once per 100 ms. It targets nominal sync 0 V and blank 0.3 V,
with each code moving just one adjacent **loaded voltage** step toward its
target. Code number is not assumed to be voltage order. Signal loss holds the
last map; a pipeline restart/menu exit reloads the original map and reacquires.

A stopped-engine differential probe checks all 1024 LUT16 views before admitting
live updates. A failed probe reloads the pristine program. Updates replace only
Golden's low six DAC bits at every pair address, preserving all phase bits,
including the overlapping raw decoder addresses. Menu and TX self-noise stops
withdraw update permission before disabling TX. Width/readback failures stop
further updates. No RF gain writes are made by this controller.

## Evidence and limits

The ladder table is nominal for the documented resistor network and 75-ohm
load, **not measured connector voltage**. Golden's phase/DAC quantization and
clipping are already baked into its original waveform. Clipped sync evidence
is refused; this cannot reconstruct missing depth. It is a slow level servo,
not a guarantee that physical gain-change spikes or motion-induced static
are gone. RF gain can change during sequential live writes; the map is not
an atomic bank switch. Live LUT arbitration, FIFO continuity, colour and
connector levels still require physical A/B acceptance, so the option stays
experimental and off by default. Extra memory is required in the lab mode.

Host validation:

- `tools/test_cvbs_level.c`: voltage-order slew, duplicated voltage values,
  timing/quality/context gates and signal-loss hold.
- `tools/test_cvbs_level.py`: actual Golden LUT synthetic PAL/NTSC plateaus,
  noise rejection, all 65,536 endpoint pair remaps, phase-bit preservation and
  the existing BitScrambler model's continuous two-slot duplicated output.
- Production architecture validation and the normal ESP-IDF CI build.

Hardware acceptance: compare `u` off/on at fixed gain and during gain changes,
measure sync/blank into 75 ohms, exercise menu entry/exit and self-noise restore,
and correlate `T` faults/updates with a raw IQ capture and a scope trace. Host
models or a successful firmware build do not establish those physical results.

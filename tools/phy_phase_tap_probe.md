# C5 MODEM_DIAG phase-tap boot probe

This optional diagnostic samples the existing Q4/I4 lane mapping and all 32
MODEM_DIAG lanes in eight-bit groups under four selector states already used
in `legacy/c5vrx2/main/diagnostics.c`. It restores all three selector registers
and the production Q4/I4 GPIO routing before starting the normal video path.
Production builds leave `CONFIG_C5VRX_PHY_PHASE_TAP_PROBE=n`.

Enable the option in `idf.py menuconfig` under **C5VRX diagnostic probes**,
or build from a fresh sdkconfig with:

```sh
idf.py -D SDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.phy-phase-tap.defaults" build
```

An already generated `sdkconfig` takes precedence over defaults; enable the
option in menuconfig if reusing an existing build directory.
Flash the experimental image, run the usual USB serial monitor, and save its
startup log. The bounded `PHY_TAP` block contains one hex trace for the eight
raw-IQ reference lanes and four 8-lane debug groups per selector. A start and
end marker delimit the run; the end marker confirms restored routing.

Analyze the captured serial text with:

```sh
python tools/analyze_phy_phase_tap.py capture.log
```

The analyzer lists five-bit lane groups with enough distinct states and phase
movement statistics resembling the reference. Its results are **candidates**,
not a detected phase tap. This boot probe samples GPIO from the CPU at an
asynchronous rate and captures reference and candidates in separate batches.
It cannot verify per-sample alignment, sampling rate, bus bit order, or that a
candidate equals Phase5(raw). Confirm a candidate under controlled RF with
PARLIO sampling against simultaneous known IQ before routing it into the
realtime BitScrambler.

Boot scanning briefly changes modem diagnostic selector registers before
video starts; the option is intended only for an experimental flash. No CPU
preprocessing is introduced into the live IQ path.

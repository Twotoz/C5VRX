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

## Live Hardware Probe Results (ESP32-C5 revision v1.0)

Tested on a Seeed Studio XIAO ESP32-C5 on `COM10` on 2026-09-27 using automated
serial boot capture (`tools/run_probe_capture.py`) and sensitivity comparison
(`tools/compare_vtx_on_off.py`) across four diagnostic selector states.

### 1. Carrier Observation (VTX ON, A1 5865 MHz)

- Reference IQ: `unique = 32`, `entropy = 4.31`, `step = 4.84`. Active carrier
  modulation verified across the full phase circle.
- Ranked candidates:
  - `lanes=9..13`: `unique = 32`, `entropy = 3.98`, `step = 5.79` (Rank #1 across all 4 selectors).
  - `lanes=8..12`: `unique = 20`, `entropy = 3.97`, `step = 6.35`.
  - `lanes=10..14`: `unique = 32`, `entropy = 3.44`, `step = 5.14`.
  - `lanes=0..4` / `1..5` / `2..6`: `unique = 31..32`, `entropy = 2.96..3.18`.

### 2. A/B Carrier Elimination (VTX OFF vs. VTX ON)

Turning off the VTX collapsed the reference IQ to `unique = 4..5` (`entropy = 1.11..1.39`).
All candidate groups with $\ge 16$ observed states disappeared.

Per-lane transition sensitivity analysis revealed the physical nature of the bus:

| Lanes | VTX ON Transitions | VTX OFF Transitions | Sensitivity ($\Delta$) | Physical Bus Function |
| :--- | :--- | :--- | :--- | :--- |
| `DIAG[4..7]` | ~240..250 | **0 (Silent)** | +245 (Infinite) | **RF carrier-dependent** |
| `DIAG[8..9]` | ~290..300 | **0 (Silent)** | +295 (Infinite) | **RF carrier-dependent** (known Q-high) |
| `DIAG[6..9]` (IQ_REF) | ~295 | **0 (Silent)** | +295 (Infinite) | **RF carrier-dependent** (Q4) |
| `DIAG[16..19]` (I4) | ~390 | ~180 | +210 (2.2x) | RF carrier-dependent (I4) |
| `DIAG[10..15]` | ~280 | ~250 | ~0 (1.0x) | **Internal digital clock / BB counter** |
| `DIAG[0..1]` | ~240 | ~260 | ~0 (1.0x) | Internal digital clock divider |
| `DIAG[20..31]` (12 lanes) | 0 | 0 | 0 (Static `0x23`) | Inactive in these 4 selector configs (earlier full-modem probes under other conditions saw activity) |

### 3. Conclusions and Next Steps

1. **Lanes 9..13 Are Not a Convincing Phase Tap in this Configuration:**
   The apparent candidate `lanes 9..13` was a composite artifact mixing 1 carrier-sensitive bit
   (`lane 9` = Q[6]) with 4 free-running internal clock/counter bits (`lanes 10..13`) that remain
   active at ~250 transitions with VTX OFF. Because this boot probe reads lane groups sequentially
   via asynchronous CPU snapshots, it cannot demonstrate simultaneous phase correlation or rule
   out unprobed modem selector configurations. However, under the tested states, no standalone
   demodulated phase bus is evident.
2. **`DIAG[4:5]` as a Strong Q Candidate (Requires Dump-Q Verification):**
   The transition shutdown with VTX OFF proves that `DIAG[4:5]` are genuinely RF-carrier
   dependent in this configuration, making them candidate lower bits for a 6-bit Q bus (`Q[4:9]`).
   However, transition counts alone do not establish bit identity or ordering. As established in
   `docs/continuous-iq-findings.md`, `DIAG[6:9]` was previously confirmed as `Q[6:9]` via bit-by-bit
   correlation against aligned modem SRAM dump data. The decisive follow-up experiment is to
   perform the same aligned comparison between `DIAG[4:5]` and dump `Q[4:5]` with the VTX ON.
3. **Architectural Implications:**
   Als `Q[4:9]` bevestigd wordt, krijg je viermaal zoveel Q-codewaarden als met `Q[6:9]`.
   Dat is nog geen bewezen viervoudige nuttige resolutie of winst voor Phase5-360: I heeft
   ook extra precisie nodig, en de huidige live ingang gebruikt acht lijnen voor Q4/I4.
   De eerstvolgende hardwaretest is dus de uitgelijnde `DIAG[4:5]` ↔ dump-`Q[4:5]`-vergelijking.
   Daarmee weten we of deze ontdekking echt nieuwe IQ-informatie oplevert.




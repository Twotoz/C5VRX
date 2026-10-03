# C5VRX-4 issue audit — 2026-10-03

C5VRX by Twotoz (Leon Beekveldt) and the C5VRX contributors.
[Canonical source](https://github.com/Twotoz/C5VRX),
[official website and Discord invite](https://twotoz.github.io/C5VRX/).

Reviewed main `e15c6ee` after PR #159, the complete two-page GitHub
issue collection (`state=all`, 100/page), issue bodies and the discussions
of the eleven directly actionable open issues below. PRs were excluded from the
issue count and reviewed separately through INTEGRATION_SOURCES.json.
49 issues exist in this snapshot, 17 open.
Only #144 is specifically titled C5VRX-4; a title-only search misses the shared
Phase8, lanes, AFC, gain and PHY work. GitHub state is recorded separately from
software or physical completion. This audit does not close any issue.

## Open issues applying directly to the integrated receiver

| Issue | Topic | Current disposition and remaining work |
|---|---|---|
| [#155](https://github.com/Twotoz/C5VRX/issues/155) | Research phy_11p_set(1, 0) for improved 5.75–5.99 GHz reception | Reversible 11p lab retained with @Ready4Sushi credit; frequency/BW/attenuation comparison remains. |
| [#153](https://github.com/Twotoz/C5VRX/issues/153) | Analog Lock: stop Espressif packet PHY from re-taking AGC/RX ownership during analog FPV | Serialized ownership restoration and reversible packet/NF/CCA labs retained; range benefit and full physical ownership need validation. |
| [#152](https://github.com/Twotoz/C5VRX/issues/152) | Instrument hidden ESP32-C5 PHY RX gating, BB watchdog and receive-state blockers | Read-only invariant monitor retained; short-pulse coverage and correlated weak-signal/retune captures remain. |
| [#151](https://github.com/Twotoz/C5VRX/issues/151) | Reverse-engineer the complete ESP32-C5 RX filter stack and test a true matched narrow-band analog-FPV profile | Filter controls/research retained; complete tap/passband identification and true matched analog profile remain research. |
| [#150](https://github.com/Twotoz/C5VRX/issues/150) | Audit firmware RF/baseband filters and calibration policy for analog-FPV range | Pinned filter and receive-policy lab integrated; matched physical A/B and calibration policy still require evidence. |
| [#144](https://github.com/Twotoz/C5VRX/issues/144) | c5vrx4: unwrap 75 ns Phase8 with middle-sample trajectory | Unwrap75 implemented in main; still needs FIFO, colour and attenuation A/B. Bounded host winding proof is not RF proof. |
| [#139](https://github.com/Twotoz/C5VRX/issues/139) | Patch native C5 AGC for analog FPV: reversible sample-and-hold, packetless HOLD and video-aware re-acquisition | Reversible native HOLD/resume lab integrated; acquisition witness and event-qualified automatic rearm remain unproven. |
| [#118](https://github.com/Twotoz/C5VRX/issues/118) | Range V3: push usable video to the true ESP32-C5 sensitivity limit | V5, protected lanes and diagnostics integrated; distinguish usable-video, Q4 and RF limits with attenuation measurements. |
| [#115](https://github.com/Twotoz/C5VRX/issues/115) | AFC V2: fix scene-biased CFO, stale state, settle contamination and back-porch color-burst error | Burst-free AFC V2 integrated, AUTO off by default; known-offset sign/reference and scene/colour hardware tests remain. |
| [#114](https://github.com/Twotoz/C5VRX/issues/114) | Phase8-native observer + physical-envelope confidence: fix V3 coherence, stale Phase5 sync logic and CI gaps | Phase8 stride-3 semantic observer and host cases integrated; snapshot alignment/history and physical video validation remain. |
| [#113](https://github.com/Twotoz/C5VRX/issues/113) | Phase8 post-PR110: prove modulo-cliff static root cause and test safe saturating higher-slope mapping | Overflow-safe C5VRX-4 transfer integrated. Historical two-bundle PR104/110 frozen-capture root-cause work remains separate. |

## Other shared work, inherited fixes and historical proposals

Every remaining issue is listed so closed reports and older designs are not
mistaken for missing C5VRX-4 patches. “Inherited” refers to the corresponding
software/control lineage in INTEGRATION.md; it does not mean every historical
checkbox or hardware gate is complete.

| Issue | GitHub state | Topic | C5VRX-4 relationship |
|---|---|---|---|
| [#158](https://github.com/Twotoz/C5VRX/issues/158) | closed | Report (PR #157 (Golden demod + Direct Gain V5) | Closed overload report on PR157; severe coarse-clipping escape is already integrated in PR159. |
| [#147](https://github.com/Twotoz/C5VRX/issues/147) | open | YouTube demo video? | Project outreach / alternative hardware; no C5VRX-4 firmware fix. |
| [#138](https://github.com/Twotoz/C5VRX/issues/138) | open | Research high-leverage C5 PHY patches: interposer, packetless RX, gain-table shaping and one-shot AGC latch | Research or older architecture; no automatic promotion to the current three-bundle path. |
| [#137](https://github.com/Twotoz/C5VRX/issues/137) | open | Build a C5VRX Analog PHY overlay: raw PBUS RX, tunable pre-Q4 filter, hardware IQ/power observer and analog gain policy | Research or older architecture; no automatic promotion to the current three-bundle path. |
| [#135](https://github.com/Twotoz/C5VRX/issues/135) | open | Analogize the ESP32-C5 Wi-Fi PHY: patch filtering, CFO, power/SNR, AGC and IQ/DC policy for analog FPV | Research or older architecture; no automatic promotion to the current three-bundle path. |
| [#134](https://github.com/Twotoz/C5VRX/issues/134) | open | Research continuous Wi-Fi PHY subcarrier / FFT / CFO taps for analog-FPV demodulation | Research or older architecture; no automatic promotion to the current three-bundle path. |
| [#128](https://github.com/Twotoz/C5VRX/issues/128) | closed | Auto channel scanner can false-lock on Wi-Fi and fall into L-band | Inherited software/control lineage; see INTEGRATION.md and original hardware limits. |
| [#125](https://github.com/Twotoz/C5VRX/issues/125) | closed | Phase8: automatic IQ lane CVT with a shared phase domain and verified fold protection | Inherited software/control lineage; see INTEGRATION.md and original hardware limits. |
| [#124](https://github.com/Twotoz/C5VRX/issues/124) | closed | Native analog AGC: investigate ~1 ms normal tracking cadence with fast overload protection | Inherited software/control lineage; see INTEGRATION.md and original hardware limits. |
| [#123](https://github.com/Twotoz/C5VRX/issues/123) | closed | Phase8: test sign-preserving finer I/Q taps within the C5 eight-line capture limit | Inherited software/control lineage; see INTEGRATION.md and original hardware limits. |
| [#121](https://github.com/Twotoz/C5VRX/issues/121) | closed | Native AGC: hold a larger Q4 sweet annulus and eliminate mid-line gain switching for Phase8 | Inherited software/control lineage; see INTEGRATION.md and original hardware limits. |
| [#119](https://github.com/Twotoz/C5VRX/issues/119) | closed | Phase8 Range: CVT-style constant-envelope AGC to eliminate Q4 origin collapse | Inherited software/control lineage; see INTEGRATION.md and original hardware limits. |
| [#117](https://github.com/Twotoz/C5VRX/issues/117) | closed | Native ESP32-C5 hardware AGC for continuous analog-FM receive | Inherited software/control lineage; see INTEGRATION.md and original hardware limits. |
| [#111](https://github.com/Twotoz/C5VRX/issues/111) | closed | Phase8 static root cause: preserve PR110 full signed mapping; investigate safe amplitude only after proving wrap-free behavior | Inherited software/control lineage; see INTEGRATION.md and original hardware limits. |
| [#109](https://github.com/Twotoz/C5VRX/issues/109) | closed | Direct Gain V3 implementation: hybrid predictive + virtual AGC with RF/BB/Fine quantizer | Inherited software/control lineage; see INTEGRATION.md and original hardware limits. |
| [#107](https://github.com/Twotoz/C5VRX/issues/107) | closed | Direct Gain V3 core: calibration-free predictive Q4 AGC with BB/Fine coarse-fine tuning | Inherited software/control lineage; see INTEGRATION.md and original hardware limits. |
| [#105](https://github.com/Twotoz/C5VRX/issues/105) | closed | Direct Gain V3: predictive envelope AGC with calibrated signal-strength and µs settle timing | Inherited software/control lineage; see INTEGRATION.md and original hardware limits. |
| [#103](https://github.com/Twotoz/C5VRX/issues/103) | closed | Phase8-HR live test: 2-bundle direct video path with overflow-safe scaling | Inherited software/control lineage; see INTEGRATION.md and original hardware limits. |
| [#101](https://github.com/Twotoz/C5VRX/issues/101) | closed | Phase8-HR: endpoint-only high-resolution demodulator in 2 BitScrambler bundles | Inherited software/control lineage; see INTEGRATION.md and original hardware limits. |
| [#98](https://github.com/Twotoz/C5VRX/issues/98) | closed | Golden Hard Guard: reject >physical phase travel with HOLD/RESEED | Research or older architecture; no automatic promotion to the current three-bundle path. |
| [#97](https://github.com/Twotoz/C5VRX/issues/97) | closed | Golden Guard: confidence HOLD/RESEED + physical-envelope outlier rejection | Research or older architecture; no automatic promotion to the current three-bundle path. |
| [#95](https://github.com/Twotoz/C5VRX/issues/95) | closed | Direct Gain V2: physical RF/BB/Fine target control with millisecond fast observer | Inherited software/control lineage; see INTEGRATION.md and original hardware limits. |
| [#93](https://github.com/Twotoz/C5VRX/issues/93) | closed | Investigate close-range layer tearing: fixed max RF stage with controlled baseband headroom | Inherited software/control lineage; see INTEGRATION.md and original hardware limits. |
| [#84](https://github.com/Twotoz/C5VRX/issues/84) | closed | Investigate residual static/desync: live Phase5-360 path and double gain slew limiting | Inherited software/control lineage; see INTEGRATION.md and original hardware limits. |
| [#51](https://github.com/Twotoz/C5VRX/issues/51) | closed | Add selectable 16 KiB / 32 KiB HP-SRAM ring buffer option | Inherited software/control lineage; see INTEGRATION.md and original hardware limits. |
| [#47](https://github.com/Twotoz/C5VRX/issues/47) | open | Possibility to use ESP32P4C5 modules | Project outreach / alternative hardware; no C5VRX-4 firmware fix. |
| [#28](https://github.com/Twotoz/C5VRX/issues/28) | closed | Investigate intermittent video lag spikes: PHY transients, transport starvation, CVBS sync corruption and decoder re-lock | Inherited software/control lineage; see INTEGRATION.md and original hardware limits. |
| [#27](https://github.com/Twotoz/C5VRX/issues/27) | closed | Characterize and exploit the ESP32-C5 RX gain chain for maximum analog-FPV range | Inherited software/control lineage; see INTEGRATION.md and original hardware limits. |
| [#23](https://github.com/Twotoz/C5VRX/issues/23) | closed | Root cause: replace 50 ns endpoint FM with exact adjacent-FM + 2:1 decimation | Research or older architecture; no automatic promotion to the current three-bundle path. |
| [#21](https://github.com/Twotoz/C5VRX/issues/21) | closed | Investigate periodic vertical frame jump / black bar in LIVE (possible RX→TX ring phase drift or wrap discontinuity) | Inherited software/control lineage; see INTEGRATION.md and original hardware limits. |
| [#20](https://github.com/Twotoz/C5VRX/issues/20) | closed | Evaluate 4-bit PARLIO @80 MHz packing as a 40 MB/s path to 80 MS/s DAC reconstruction | Research or older architecture; no automatic promotion to the current three-bundle path. |
| [#17](https://github.com/Twotoz/C5VRX/issues/17) | closed | Exploit full 40 MS/s DAC cadence: prefer true 40→40 demod over [D,D] holds | Research or older architecture; no automatic promotion to the current three-bundle path. |
| [#14](https://github.com/Twotoz/C5VRX/issues/14) | closed | Explore stateful confidence-aware demodulation and CVBS-informed error suppression | Research or older architecture; no automatic promotion to the current three-bundle path. |
| [#13](https://github.com/Twotoz/C5VRX/issues/13) | closed | Explore race-aware adaptive RF control: AFC, AGC, filtering and antenna selection | Research or older architecture; no automatic promotion to the current three-bundle path. |
| [#12](https://github.com/Twotoz/C5VRX/issues/12) | closed | Prove/fix MODEM_DIAG → PARLIO sampling integrity and startup separation | Inherited software/control lineage; see INTEGRATION.md and original hardware limits. |
| [#11](https://github.com/Twotoz/C5VRX/issues/11) | closed | Fix remaining static/desync: capture integrity, robust trajectory LUT and measured pipeline/output validation | Inherited software/control lineage; see INTEGRATION.md and original hardware limits. |
| [#9](https://github.com/Twotoz/C5VRX/issues/9) | closed | Implement 2-bundle middle-sample trajectory LUT for branch-aware WBFM demod | Research or older architecture; no automatic promotion to the current three-bundle path. |
| [#6](https://github.com/Twotoz/C5VRX/issues/6) | closed | PR5: verify static root causes and ESP32-C5-feasible recovery/filtering | Inherited software/control lineage; see INTEGRATION.md and original hardware limits. |

## Fixes from this follow-up

The opt-in `u` CVBS level lab now discards its consecutive-evidence chain
when observations are duplicated, time reverses, or a gap exceeds 200 ms
(four normal 50-ms supervisor intervals). After a gap, three new consistent
windows are needed. Clock reversal cannot underflow the 100-ms write limit.
Invalid evidence continues to hold the applied transfer. This is timestamp
hygiene, not proof of unique DMA samples or physical sync validity.

Its slew now follows adjacent entries in loaded-voltage order rather than
incrementing binary code. The generator explicitly supports measured,
nonmonotonic calibration tables; raw +/-1 could previously move voltage away
from the target or cross an unrelated voltage jump. Tied voltages converge
without oscillation. Nominal ladder progression remains unchanged. One voltage
step is not a guarantee of an invisible transition or a fixed millivolt limit.

These changes extend the output-confidence/loaded-transfer goals of #114,
#113 and #144. They do not complete their remaining hardware criteria.
No demodulator, RF default or speculative PHY setting is enabled by this PR.

## Validation

- Full local `python3 verify.py`: 20 C regressions, 48 synthetic PAL/NTSC AFC
  cases, 524,386,048 bounded unwrap trajectories and source/routing/DSP tests.
- New regression: long gaps, repeated timestamps and clock reversal hold output
  until renewed evidence/cadence permits it.
- New regression: all 4,096 start/target pairs of a permuted loaded-voltage table
  with equal-voltage entries move toward the target through adjacent voltages
  and converge; original nominal amplitude/CFO sweeps still pass.
- Exact-head ESP-IDF 6.0.2 alpha build is checked in the PR workflow.
- Physical LUT arbitration, timing/FIFO continuity, loaded CVBS PAL/NTSC output,
  camera/goggle behaviour and attenuation comparisons remain required.

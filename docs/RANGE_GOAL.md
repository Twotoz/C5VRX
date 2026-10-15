# PR 212 completion goal

Requested by the operator on 2026-10-13. This work extends C5VRX by Twotoz
and the C5VRX contributors, including the archived native-AGC and sync-flywheel
investigations. See `FIRMWARE_RANGE_AUDIT.md` and `SYNC_KEEPER.md` for evidence.

## Decision rule

For each proposed change, ask: can this be better, smarter and simpler?
Compare against the working receiver, remove unnecessary assumptions, and
prefer fewer owners, timing stages and allocations. Preserve negative results.
Distinguish simulation, firmware implementation and physical validation.

## Work remaining

- [ ] Address every PR comment in [PR212_REVIEW_ACTIONS.md](PR212_REVIEW_ACTIONS.md).
      The 2026-10-14 priority is Direct Gain V6 investigation with V5 retained
      as default, physical AFC centering and wide-profile reliability before
      further output experiments. Include optional AUTO OUTPUT minimum/ceiling,
      identical-IQ 1x/0.5x/sync-priority comparison, full kp/program/output
      keeper matrix, prewarmed templates and controlled AUTO-versus-fixed A/B/A.
      Review round: V5 map-context bug fixed (`50ca13e`); V6 stays research-only.
      Identical-IQ replay and full output/program transition evidence are
      checked in. Passive PROFILE/AFC diagnostics and manual private-RAM `J`
      timing instrumentation support the remaining physical tests; they do
      not enable live flywheel writes. The C5 returned on 2026-10-15 and
      both private J runs passed correctness with unchanged error counters.
      Measured H preparation reached602us, regular V1817us and first
      EQ31373us: dynamic synthesis is still too slow for a live keeper.
      See SYNC_TRANSPORT_CHOICES.md and board_j_2026-10-15 evidence.

- [ ] Operator addition (2026-10-14): find the simplest effective DC-bias
      workaround without repeated quiet-channel calibration. Compare existing
      hardware DC tracking, offset-insensitive discrimination and safely
      qualified live estimation. Preserve real weak video; quantify the noise,
      near-zero-CFO and quantizer-floor limits instead of promising immunity.
      Carrier DC collection now rejects tiny/partial false circles while
      preserving the noise mean and envelope diagnostic. Token-centering
      remains a tested host candidate without an automatic live controller.
      `fd32d8b` replaces the polarity-assuming thermal nudge with a verified
      one-code live-carrier DAC trial (readback, fresh improvement check,
      verified rollback; RAM-only). Built and host-tested; real-carrier
      improvement and display visibility of a trial remain board gates.
- [ ] Operator addition (2026-10-14): monitor sampling timing and adjust it
      when needed. Establish observable evidence, avoid confusing RF/DC noise
      with timing faults, and prove a bounded correction that preserves live
      video. Review the existing startup sampling-phase search and report-only
      in-flight monitor before selecting an automatic actuator. Inspection
      found that the existing live monitor also starts a disruptive three-
      position scan; its divider pulse lacks a proven phase rollback contract.
      Live scans are now removed; monitoring reports an explicit deferred
      adjustment. Automatic adjustment remains incomplete.

- [ ] Finish observer CPU/freshness review, including gain-settling windows;
      verify build/CI and subsequently startup/IDLE behavior on the board.
      Software guards and pinned build/CI pass. Short current-board captures
      show ready heartbeats without watchdog output. Board application
      `287eeee` is checksum-verified, flashed to the C5, and confirmed ready/live
      at A2 and OUTPUT 2.0x, with `DC_LIVE` running (0 faults) and `SYNC_PROBE`
      verifying the 2062-byte byte-granular RX tails (`geometry=1`, `geom_error=0`).
      A full startup/attenuation recording remains unverified.
- [ ] Investigate gradual range loss: distinguish flat RX IQ, stopped/flat
      CVBS, gain/DC changes and the monitor muting an otherwise live waveform.
      The supplied startup log does not settle this question.
- [ ] Build bounded periodic native-AGC acquisition using verified legacy
      semantics. Ensure one gain owner, coherent adopted gain/DC state,
      retune/calibration handoff, timeout recovery and meaningful counters.
      An opened gate is not proof that exactly one acquisition completed.
      Software bench implementation: `~`, nominal 20 ms, exclusive observer
      owner, PBUS tuple readback, bounded window and gain/DC rollback. Boot RF
      context only; arbitrary context and physical single-scan proof pending.
      On `b0b4147`, A1,
      gain 83, the refusal is BB readback `0x17f` versus a seven-bit table
      value `0x7f`: the pinned vendor RX command supplies channel-mode bit
      eight. The fix verifies that context bit before normalizing the gain;
      physical `f3e6931` passes preflight and opens for 8 us, then rejects
      an observation gap. `9720407` uses cycle-exact IRAM polling and obtains
      102 valid observations within 60 us, no start-proxy matches, then times
      out and restores Direct Gain. No landing is proved. Normal boot
      after software download was recovered by clearing only the documented
      LP_AON forced-download field; no physical reset is now pending.
- [ ] Select and implement a feasible PAL/NTSC sync keeper that preserves
      actual received snow and weak picture fragments. Establish camera timing,
      horizontal/vertical holdover, source freshness and DMA safety. Compare
      alternatives before choosing; the current host IQ-mux oracle is not
      streaming firmware.
      The tracked root candidate now passes all eight kp levels at nominal
      and +/-50-ppm PAL/NTSC
      pulse/content tests, source provenance and loss-counter/saturation tests.
      Digital synthesis across other kp levels has separate evidence; full
      pulse transitions, measured CPU/DMA timing and LCD acceptance remain gates.
      `1d55060` adds cold-start generated H/V timing (no camera ever seen),
      admitted only by independent absent-source windows and cancelled by any
      source candidate; snow outside generated blanking is untouched (host).
      `28b7df7` adds a bracketed DMA memory-frontier readback; the physical
      e9130ae probe confirmed 2062-byte RX tails. Writes stay unauthorized
      (`mem_writes_safe=0`) until publication/prefix/WCET/reader ownership
      are proved; a physical source qualifier is still required.
- [ ] Reassess remaining range losses using actual firmware policy and held-out
      evidence, especially envelope C/N bias, deviation/filter matching,
      sampling phase and gain/DC behavior. Implement supported improvements;
      record rejected ideas and unresolved physical questions.
- [ ] Run relevant host/research checks and the pinned firmware build, verify
      exact-head CI/publication, and update PR 212 to describe its final scope.
- [ ] Complete board/display validation when accessible and explicitly
      authorized to flash: startup, native-AGC A/B, gradual attenuation, actual
      CVBS sync/levels, camera return and bounded latency/CPU behavior.
- [ ] Update issues 201/213 with literal evidence. Close only issues whose
      requested behavior is actually complete; monitor sync preservation is
      not complete merely because black fallback works.
- [ ] Verify the operator's additional output request: 1.0..5.0x in 0.5x
      steps, persisted only after a verified change and restored at boot.
      Production settings save/reboot and all DAC/kp-map host tests pass;
      a physical reboot into `b0b4147` preserved A2 and OUTPUT 2.0x. Loaded
      output measurements across all levels remain unverified.

## Acceptance

Real received content remains visible during weak reception. Black NO SIGNAL
fallback remains off by default. Native AGC cannot fight Direct Gain or leave
PHY/DC state inconsistent. Timing claims are supported by measurements rather
than nominal timer periods. No simulation-derived number is presented as
measured RF range. Outstanding board checks remain explicit.

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

- [ ] Operator addition (2026-10-14): find the simplest effective DC-bias
      workaround without repeated quiet-channel calibration. Compare existing
      hardware DC tracking, offset-insensitive discrimination and safely
      qualified live estimation. Preserve real weak video; quantify the noise,
      near-zero-CFO and quantizer-floor limits instead of promising immunity.
- [ ] Operator addition (2026-10-14): monitor sampling timing and adjust it
      when needed. Establish observable evidence, avoid confusing RF/DC noise
      with timing faults, and prove a bounded correction that preserves live
      video. Review the existing startup sampling-phase search and report-only
      in-flight monitor before selecting an automatic actuator. Inspection
      found that the existing live monitor also starts a disruptive three-
      position scan; its divider pulse lacks a proven phase rollback contract.

- [ ] Finish observer CPU/freshness review, including gain-settling windows;
      verify build/CI and subsequently startup/IDLE behavior on the board.
      Software guards and pinned build/CI pass. Short current-board captures
      show ready heartbeats without watchdog output. Board application
      `b0b4147` is checksum-verified; a full startup/attenuation recording
      remains unverified.
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
      Actual board toggles still refuse before opening. On `b0b4147`, A1,
      gain 83, the refusal is BB readback `0x17f` versus a seven-bit table
      value `0x7f`: the pinned vendor RX command supplies channel-mode bit
      eight. The fix verifies that context bit before normalizing the gain;
      physical acquisition after this fix remains unmeasured. Normal boot
      after software download was recovered by clearing only the documented
      LP_AON forced-download field; no physical reset is now pending.
- [ ] Select and implement a feasible PAL/NTSC sync keeper that preserves
      actual received snow and weak picture fragments. Establish camera timing,
      horizontal/vertical holdover, source freshness and DMA safety. Compare
      alternatives before choosing; the current host IQ-mux oracle is not
      streaming firmware.
      The tracked root candidate now passes nominal and +/-50-ppm PAL/NTSC
      pulse/content tests, source provenance and loss-counter/saturation tests.
      Digital synthesis across other kp levels has separate evidence; full
      pulse transitions, measured CPU/DMA timing and LCD acceptance remain gates.
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

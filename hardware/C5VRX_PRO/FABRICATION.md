# PCBWay quotation and release requirements

**Engineering review only — do not place a production order from this revision.**
The following is the intended process, not an accepted quotation or a claim
that the current routing already satisfies its impedance targets.

| Parameter | Intended requirement |
|---|---|
| Outline | Exact inherited 42.51 × 28.80 mm / 2 mm corners |
| Thickness | 1.60 mm, tolerance to be agreed for the bay mating interface |
| Layers | 4: F.Cu signals/components, In1.Cu continuous GND reference, In2.Cu signals, B.Cu signals/components |
| Copper | 35 µm external; internal copper and dielectrics agreed with PCBWay |
| Finish | ENIG, green mask, white silk; no via exposed in RF connector contact surfaces |
| Signal rules | 0.127 mm minimum width/clearance, 0.20 mm copper-to-hole clearance |
| Small vias | 0.40 mm pad / 0.20 mm finished drill; capability and annular ring review required |
| RF/USB | 50 Ω RF single-ended; 90 Ω USB differential; final geometries not yet approved |
| Via-in-pad | Resin filled and copper capped for thermal and small ground-pad vias; do not substitute ordinary open vias |
| Assembly | Both sides SMD, secondary insertion/soldering of vertical SMA and nine bay contacts |
| First assembly | Six-bit DAC, filter DNP, buzzer and its support parts DNP, seventh DAC resistor DNP |

The [PCBWay capability table](https://www.pcbway.com/capabilities.html) was
checked for the proposed process. The 0.20 mm drill at 1.60 mm thickness is
an 8:1 aspect ratio. The combination of RF control, filled/capped vias and
small annular rings must be quoted explicitly; this is not a standard cheap
two-layer prototype order. Supplier process approval does not replace design
verification. Do not silently enlarge the inherited bay holes or alter their
pitch/position to make assembly easier.

## Release blockers

1. Agree a specific material, copper and dielectric stackup with PCBWay.
   Reroute/tune RF and USB to that stackup. Keep the 5.8 GHz path on F.Cu,
   short and referenced to uninterrupted In1.Cu, with deliberate launch and
   dense return vias. The present general routing pass is a connectivity
   checkpoint, not a validated RF route. Examine the RF path audit.
2. Verify each custom land pattern, especially pSemi pin-1 orientation,
   Murata BPF terminal geometry, exact ESP32-C5HR2 package and LDO EP, against
   the current supplier drawing. Check paste apertures against filled vias.
3. Add adequate panel rails/fiducials/tooling in the fabrication panel, clear
   part designators in assembly drawings, approved centroid rotations and
   exact purchasable BOM. The archived DIY jack and edge-launch SMA are not
   part of this assembly. Board-level test fixtures need a reviewed pogo interface.
4. Review power widths and thermal pours for worst-case ESP32-C5 current,
   LNA drain current, input diode drop, enclosed ambient, LDO loss and USB
   current/inrush. USB CC pull-downs alone are not a power negotiation policy.
5. Integrate and test the board-specific firmware contract from README:
   one-ADC joystick, optional buzzer, fail-safe RF switching, channel limits
   for the filter variant, and calibration provenance. Keep seven-bit DNP
   until actual seven-bit code and loaded CVBS validation exist.
6. Approve antenna/USB/joystick access and component heights in the intended
   Fat Shark housing using vendor mechanical CAD and physical fit checks.

## Prototype acceptance before production

- Inspect/QC PCB and assembly, X-ray QFN/EP solder joints as appropriate;
  verify all power rail resistances and SMA-to-ground isolation.
- Current-limited 5 V bring-up; measure 3.3 V, RF supply, startup timing,
  brownout/reset behavior and diode isolation with USB and bay power together.
- BOOT + RESET must enter USB download; repeat cold boots, warm resets and
  held-joystick starts. Confirm the exact flash and PSRAM device behavior.
- Enumerate, flash and run console over USB-C in both cable orientations.
  Check signal quality, ESD grounding and host current/inrush behavior.
- Measure 48 MHz clock load/frequency and local power transients. Verify
  the internal 40 MS/s IQ GPIO loopback on this bare-chip PCB.
- VNA-test RF launch and C5 matching; measure LNA stability, gain, input/output
  match, noise and bypass loss over the intended bands, supplies and temperature.
  Check reverse-TX/calibration leakage before ever enabling the LNA path.
- Compare controlled-attenuation reception against the XIAO baseline, including
  nearby strong VTX, blockers, channel edges and filtered/unfiltered variants.
- Scope loaded CVBS into one 75 Ω receiving load: sync, blanking, white,
  burst, bandwidth, glitch energy, PAL/NTSC lock and real goggles. Do not
  add an extra 75 Ω on-board shunt to the retained DAC network.
- Log ADC joystick thresholds/noise in all directions and press, validate
  debounce and menu behavior. If fitted, check piezo current and RF coupling.
- Thermal soak in the intended housing and verify fit/connector strain.

Only after these items are signed off should Gerbers/drills, IPC netlist,
reviewed BOM and centroid, assembly drawings, panel data, programming image,
test limits and release checksums be issued together for PCBWay production.

# C5VRX_JACK_DIY Fat Shark Carrier + 3.5mm AV Output (Through-Hole DIY)

<div align="center">
  <img src="exports/C5VRX_JACK_DIY-3D-top-iso.png" alt="C5VRX_JACK_DIY 3D Top View" width="49%" />
  <img src="exports/C5VRX_JACK_DIY-3D-bottom-iso.png" alt="C5VRX_JACK_DIY 3D Bottom View" width="49%" />
</div>

A DIY-friendly KiCad carrier board for the **Seeed Studio XIAO ESP32-C5** running **C5VRX**, designed for standard Fat Shark goggle module bays with an integrated 3.5 mm TRRS AV output jack.

---

## Highlights & Features

- **Standard Module Bay Dimensions:** Exactly **42.51 × 28.80 mm** with 2.0 mm corner radiuses, engineered to fit standard Fat Shark goggle module bays.
- **Easy Through-Hole (THT) Soldering:** The 6-bit R-2R DAC ladder (R1–R7) and optional filter capacitor (C1) use standard axial ¼W resistors (`DIN0207`, 10.16 mm pitch) and a 2.5 mm disc capacitor. Each resistor footprint has clear silkscreen markings with reference and nominal value (`R4 1k`, `R5 470`, `R6 240`, `R1 8.2k`, `R2 3.9k`, `R3 2k`, `R7 200`).
- **Seeed Studio XIAO ESP32-C5 Socket:**
  - Positioned in the upper-left corner on two 7-pin 2.54 mm female headers so the XIAO remains easily removable/reusable.
  - The USB-C port faces left outwards through the board edge for easy firmware updates and serial monitoring.
- **Integrated 3.5mm TRRS AV Jack (J2 / PJ-320D):**
  - Horizontal 4-pole TRRS SMD jack positioned directly beneath the XIAO, facing left.
  - Universal pinout: Tip (T) and Ring 1 (R1) are routed to `/VIDEO`, Sleeve (S) is tied to ground `/GND`.
- **Fat Shark Bay Connector (J1):**
  - Standard 9-pin right-angle male header (2.54 mm pitch) on the right edge.
- **Power Solder Jumper (SJ1):**
  - Located beside the audio jack. Bridge with solder to route the goggle bay 5V rail to the XIAO 5V input.
- **KiCad DRC & ERC Verified:**
  - 0 DRC errors, 0 warnings, 0 unrouted nets.
  - 0 ERC schematic errors or warnings.

---

## Schematic

<div align="center">
  <img src="exports/C5VRX_JACK_DIY-schematic.png" alt="C5VRX_JACK_DIY Schematic" width="100%" />
</div>

---

## Bill of Materials (Assembly Template)

Ready for automated Turnkey / Consigned PCB assembly quoting (PCBWay, JLCPCB, Elecrow):

| S/N | Designator * | Quantity * | Manufacturer Part Number * | Procurement Type | Customer Note |
|:---:|:---|:---:|:---|:---:|:---|
| 1 | R1 | 1 | `MFR-25FTE52-8K2` | `--` | 8.2 kΩ 1/4W 1% metal film axial resistor (DIN0207, P10.16mm) |
| 2 | R2 | 1 | `MFR-25FTE52-3K9` | `--` | 3.9 kΩ 1/4W 1% metal film axial resistor (DIN0207, P10.16mm) |
| 3 | R3 | 1 | `MFR-25FTE52-2K` | `--` | 2.0 kΩ 1/4W 1% metal film axial resistor (DIN0207, P10.16mm) |
| 4 | R4 | 1 | `MFR-25FTE52-1K` | `--` | 1.0 kΩ 1/4W 1% metal film axial resistor (DIN0207, P10.16mm) |
| 5 | R5 | 1 | `MFR-25FTE52-470R` | `--` | 470 Ω 1/4W 1% metal film axial resistor (DIN0207, P10.16mm) |
| 6 | R6 | 1 | `MFR-25FTE52-240R` | `--` | 240 Ω 1/4W 1% metal film axial resistor (DIN0207, P10.16mm) |
| 7 | R7 | 1 | `MFR-25FTE52-200R` | `--` | 200 Ω 1/4W 1% metal film axial resistor (DIN0207, P10.16mm) |
| 8 | C1 | 1 | `K471K15C0GF53L2` | `DNP` | 470 pF 50V ceramic disc capacitor (P2.5mm); DNP (optional filter) |
| 9 | J1 | 1 | `61300911021` | `--` | 9-pin 1x09 right-angle 2.54mm male header for Fat Shark goggle bay |
| 10 | J2 | 1 | `PJ-320D` | `--` | 4-pole TRRS SMD 3.5mm phone jack facing left (PJ-320D) |
| 11 | U1 | 2 | `PPTC071LFBN-RC` | `--` | 2x 1x07 2.54mm female header sockets for Seeed XIAO ESP32-C5 |

*Note: Solder bridge `SJ1` is copper-only (no part). The XIAO ESP32-C5 module plugs into `U1` sockets and is customer supplied.*

---

## Production Files (`exports/`)

- [`exports/C5VRX_JACK_DIY-gerbers.zip`](exports/C5VRX_JACK_DIY-gerbers.zip): Gerber X2 and Excellon drill package ready for PCB fab (JLCPCB, PCBWay, etc.).
- [`exports/gerbers/`](exports/gerbers/): Uncompressed individual Gerber and Excellon drill files.
- [`BOM-assembly.csv`](BOM-assembly.csv) & [`exports/C5VRX_JACK_DIY-assembly-bom.xlsx`](exports/C5VRX_JACK_DIY-assembly-bom.xlsx): Direct turnkey assembly BOM following PCBWay/JLCPCB template (CSV & Excel formats).
- [`exports/C5VRX_JACK_DIY-centroid.xlsx`](exports/C5VRX_JACK_DIY-centroid.xlsx) & [`exports/C5VRX_JACK_DIY-centroid.zip`](exports/C5VRX_JACK_DIY-centroid.zip): Assembly centroid / CPL position file ready for direct upload (Excel & ZIP formats).
- [`exports/C5VRX_JACK_DIY-centroid.csv`](exports/C5VRX_JACK_DIY-centroid.csv): Assembly centroid CSV (`Designator,Val,Package,Mid X,Mid Y,Rotation,Layer`).
- [`exports/C5VRX_JACK_DIY-all-pos.csv`](exports/C5VRX_JACK_DIY-all-pos.csv) & [`exports/C5VRX_JACK_DIY-all.pos`](exports/C5VRX_JACK_DIY-all.pos): KiCad native centroid position files (CSV & ASCII in mm).
- [`exports/C5VRX_JACK_DIY-schematic.pdf`](exports/C5VRX_JACK_DIY-schematic.pdf): Vector schematic PDF.
- [`exports/C5VRX_JACK_DIY-fit-check-1to1.pdf`](exports/C5VRX_JACK_DIY-fit-check-1to1.pdf): 1:1 scale printable PDF for mechanical bay fit-checks.
- [`exports/C5VRX_JACK_DIY.step`](exports/C5VRX_JACK_DIY.step) & [`exports/C5VRX_JACK_DIY.glb`](exports/C5VRX_JACK_DIY.glb): 3D CAD models.

---

## Nederlandse Beschrijving

KiCad-project voor een Seeed Studio XIAO ESP32-C5 carrier board met:
- **Fat Shark Dominator 9-pins moduleconnector (J1)** aan de rechterzijde.
- **3.5mm AV headphone jack (J2 / PJ-320D)** aan de linkerzijde, wijzend naar links (dezelfde richting als de USB-C poort).
- Volledige **through-hole (THT) axiale ¼W-weerstanden (R1–R7)** en **doorsteekcondensator (C1)** voor maximale zelfbouw- en soldeervriendelijkheid!
- De XIAO ESP32-C5 module (`U1`) is geplaatst in de linkerbovenhoek op headers, direct boven de 3.5mm jack.
- **Soldeerbrug (SJ1):** Verbind met een drupje tin om de 5V van de bril naar de XIAO te leiden.

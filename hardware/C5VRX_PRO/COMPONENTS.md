# C5VRX PRO â€” component selection, revision A

Design for review, not a fabrication release. Same 42.51 Ã— 28.80 mm rounded
outline and nine bay contacts as `C5VRX_JACK_DIY_V3`, with a bare ESP32-C5.
This extends **C5VRX by Twotoz and the C5VRX contributors**;
[source](https://github.com/Twotoz/C5VRX),
[official website and Discord invite](https://twotoz.github.io/C5VRX/).

| Function | Selected part | Reason / assembly constraint |
|---|---|---|
| Receiver MCU | Espressif ESP32-C5HR2 | QFN48, in-package 2 MB PSRAM; no XIAO or radio module |
| Flash | Winbond W25Q64JVSSIQ | 8 MB, 3.3 V, SOIC8; fits current partition table, unlike 4 MB HF4 |
| Reference clock | Abracon ABM8-48.000MHZ-B2-T | 48 MHz, 3.2 Ã— 2.5 mm, 18 pF load; initial 27 pF load capacitors require tuning |
| LNA | Qorvo QPL9504TR7 | 5â€“6 GHz evaluation network; typical NF 0.76 dB at 5.5 GHz at 5 V, **not a measured board NF** |
| RF bypass | 2 Ã— pSemi PE42422 | 6 GHz SPDT, default bypass while booting/flashing/calibrating; switch loss contributes to system NF |
| Optional BPF | Murata LFB215G37SG8A180 | 4.90â€“5.85 GHz passband; DNP by default, incompatible with full 48-channel coverage |
| SMA | Amphenol RF 132134 | 50 Î© female edge launch for 1.6 mm PCB; protrudes outside the inherited outline |
| USB-C | HRO TYPE-C-31-M-12 | USB 2.0 receptacle, independent 5.1 kÎ© CC resistors |
| USB ESD | ST USBLC6-2SC6 | Two protected data lines, SOT23-6; clamp supply goes to USB VBUS |
| 3.3 V regulator | TI TLV75733PDRVR | 1 A WSON6 with exposed thermal pad; quieter than a switching converter, thermal validation required |
| Input isolation | 2 Ã— Nexperia PMEG2010ER,115 | 1 A Schottky OR, bay and USB cannot back-feed each other |
| Input fuse | Littelfuse 1206L075/13.2WR | 0.75 A hold PTC on bay input; power budget must include temperature derating |
| Joystick | ALPS Alpine SKRHABE010 | Five switches, one GPIO6 / ADC1_CH5 using resistance coding |
| BOOT / RESET | 2 Ã— XKB TS-1187A-B-A-B | Separate low-profile SMD pushbuttons, GPIO28 / CHIP_PU |
| Optional buzzer | Murata PKLCS1212E4001-R1 + Nexperia 2N7002,215 | Passive piezo, GPIO7 via MOSFET; DNP by default |
| Video DAC | Yageo RT0402 precision thin-film series | Existing 8.2k / 3.9k / 2k / 1k / 470Î© / 240Î© plus 200Î© shunt, 0.1%; seventh 15kÎ© branch DNP |
| CVBS test point | TP1 copper pad | USB-C replaces the DIY TRRS jack; bay pin 6 retains CVBS |
| Bay | 1 Ã— 9, 2.54 mm contact strip | Exact existing coordinates and pin numbers; retain unused audio/RSSI/band contacts as NC |

The QPL9504 was selected over QPL9058/QPL9547 because its manufacturer
provides a 5â€“6 GHz application network and noise data in the intended band.
It is a strong candidate, not a claim of a universally best LNA. Its supply
is the isolated 5 V rail, approximately 4.5â€“5.1 V after a diode; the 5 V
datasheet numbers must not be advertised as achieved at this voltage.

Populate either the optional filter or its bypass resistor R42, never both. With the
filter fitted, restrict channel selection to its passband, allowing margin for
the occupied analog video bandwidth at both edges. Without the filter the
ESP32-C5 itself is characterized only through 5885 MHz; the existing firmware's
out-of-spec channels do not become qualified by adding a wideband LNA.

GPIO allocation: IQ `{1,0,25,2,27,5,3,4}`; DAC `{23,24,11,12,8,9}`;
future seventh DAC low bit `10`; joystick `6`; buzzer `7`; RF path `26`;
USB `13/14`; BOOT `28`; RESET `CHIP_PU`. Flash/PSRAM pins remain reserved.
No GPIO-selection bridges are needed. Six-bit is the initial assembly;
the seventh resistor and buzzer can be fitted independently, with matching firmware.

## Primary references

- [Espressif C5 datasheet](https://www.espressif.com/sites/default/files/documentation/esp32-c5_datasheet_en.pdf)
- [Espressif schematic and layout guide](https://docs.espressif.com/projects/esp-hardware-design-guidelines/en/latest/esp32c5/index.html)
- [Qorvo QPL9504](https://www.qorvo.com/products/p/QPL9504), Rev F application circuit, pages 5 and 9
- [pSemi PE42422 datasheet](https://www.psemi.com/pdf/datasheets/pe42422ds.pdf), truth table and package drawing
- [Murata BPF specification](https://www.murata.com/products/productdata/8802370387998/LFB215G37SG8A180.pdf)
- [TI TLV757P datasheet](https://www.ti.com/lit/ds/symlink/tlv757p.pdf)
- [ALPS joystick CAD and source](https://openhardware.antmicro.com/components/alps-alpine-skrhabe010/)

Manufacturer order codes, stock, prices and substitutions must be reconfirmed
with PCBWay at quotation; no quote or order has been placed.

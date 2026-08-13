# XIAO ESP32-C5 PAL CVBS proof

Target: Seeed Studio XIAO ESP32-C5. This is a separate pin profile; do not use
the DevKitC wiring.

The mapping follows [Seeed's official XIAO ESP32-C5 pin table](https://wiki.seeedstudio.com/xiao_esp32c5_getting_started/)
and the [official ESP32-C5 datasheet](https://www.espressif.com/sites/default/files/documentation/esp32-c5_datasheet_en.pdf). It avoids native
USB GPIO13/14, battery measurement GPIO6/26, boot GPIO28, ADC header D0/GPIO1,
and strapping GPIO7/GPIO25. The selected header pins are ordinary digital GPIOs
and are routed through the C5 GPIO matrix to PARLIO.

| DAC bit | XIAO pin | GPIO | Series resistor |
|---|---:|---:|---:|
| D0 / LSB | D4 | 23 | 7.87 kΩ |
| D1 | D5 | 24 | 3.92 kΩ |
| D2 | D6 | 11 | 1.96 kΩ |
| D3 | D7 | 12 | 976 Ω |
| D4 | D8 | 8 | 487 Ω |
| D5 / MSB | D9 | 9 | 243 Ω |

This mapping has been checked against Seeed's current official pin table.
GPIO23/24/11/12/8/9 are header-accessible GPIO-matrix outputs and none is an
ESP32-C5 strapping pin. GPIO11 and GPIO12 are also the default UART0 TX/RX
functions; the proof image uses native USB Serial/JTAG and PARLIO takes over
those pins when the application starts. A short reset/ROM transient on
D6/GPIO11 is therefore possible and is not valid CVBS evidence.

Join the six resistor outputs at `VIDEO`:

```text
VIDEO node -> 191 ohm -> GND
VIDEO node -> composite video input
XIAO GND   -> video ground
```

Flash the `c5vrx-xiao-pal-cvbs-proof-firmware` artifact over USB-C. No UART
commands are required. On power-up the output holds PAL timing continuously,
shows the C5VRX splash for 12 seconds, then switches content (not timing) to the
diagnostic screen.

The resistor values are calculated nominal targets. Measure sync, blank, black
and white levels into the exact 75-ohm AV input before treating them as a final
BOM. Parallel input termination and resistor tolerances change the result.

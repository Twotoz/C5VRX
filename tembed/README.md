# T-Embed display for the C5VRX video link

Firmware for the LilyGo T-Embed CC1101 (ESP32-S3, 320x170 ST7789) that shows
the frames a XIAO ESP32-C5 running C5VRX link mode grabs from an analog FPV
transmitter. The C5 does all the radio and video work: it FM-demodulates the
5.8 GHz signal in software, locks onto the PAL/NTSC sync and streams 224x168
luma frames over UART at 4 Mbaud (packet format: `../main/uart_link.h`). This
board only receives, converts to RGB565 and draws, with the link status in
the left margin (VIDEO / NO SIG / NO LINK, frequency, standard, RF gain, grab
error, frames per second, CRC errors).

## Wiring

| XIAO ESP32-C5 | T-Embed UART connector |
|---|---|
| D6 (GPIO11, TX) | RXD (GPIO44) |
| D7 (GPIO12, RX) | TXD (GPIO43), for commands later |
| GND | GND |

Keep the wires short (under 20 cm) at 4 Mbaud. Power both boards over USB.

## Build and flash

```bash
pio run -d tembed                    # official espressif32 6.12.0 + Arduino-ESP32 2.0.17
pio run -d tembed -t upload          # native USB
```

`platformio.ini` points `packages_dir` at `~/.platformio/packages-s3`: the
official platform and the pioarduino platform of the C5 project use the same
package names in different versions, and sharing one package store made each
build replace the other's esptool and RISC-V toolchain. On the development
host the large packages in that store are directory junctions into
`~/.platformio/packages`:

```bat
mklink /J %USERPROFILE%\.platformio\packages-s3\framework-arduinoespressif32 %USERPROFILE%\.platformio\packages\framework-arduinoespressif32
mklink /J %USERPROFILE%\.platformio\packages-s3\toolchain-xtensa-esp32s3 %USERPROFILE%\.platformio\packages\toolchain-xtensa-esp32s3
```

Without them PlatformIO simply downloads the packages into the separate store.

## Controls

- Encoder button: rotate the picture by 180 degrees.

## C5 side

Build the repository root with the `xiao_c5_link` environment. It boots on
Raceband R6 (5843 MHz, `CONFIG_C5VRX_LINK_CHANNEL`) with the wide analog
filter and starts streaming at once (`CONFIG_C5VRX_LINK_STREAM`). Console
keys on its USB port: `S` stops/starts streaming, `P` switches to 4-bit rows
(about twice the frame rate), `g` dumps one frame for `tools/link_frame.py`.

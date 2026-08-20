# XIAO ESP32-C5 A1 IQ/USB hardware session

Date: 2026-08-20. Hardware: Seeed Studio XIAO ESP32-C5 rev v1.0,
8 MB flash, native USB Serial/JTAG. ESP-IDF: v6.0.2.

## Proven on the physical board

- A complete verified flash boots the normal receiver firmware and answers
  `PING` with `C5VRX_PONG` after the native USB endpoint has re-enumerated.
- `SET A 1` selects 5865 MHz through Wi-Fi channel 173. `STATUS` readback has
  reported band A, channel 1, 5865 MHz, channel 173 and 40 MHz bandwidth.
- The minimal direct USB diagnostic proved application USB RX and TX on the
  board. The earlier silent normal console was therefore a stdio/VFS software
  issue, not a different Windows driver requirement.
- Earlier finite A1 captures gave a repeatable VTX OFF/ON/OFF energy change and
  a ring-like IQ distribution with the VTX on. This is strong RF-dependent IQ
  evidence, but not proof of decoded analog video.

## Hard limits and honest classifications

- One recovered FE/ADC dump is at most 65536 bytes: 16384 packed `uint32_t`
  words. Firmware must never ask this producer for more than 16384 words.
- Multiple finite dumps are separately retriggered. They are not gapless and
  must not be presented as one continuous PAL frame.
- The producer sample rate is not yet calibrated. `finite_fill_msps` is logged
  only as a finite-fill timing estimate when the hardware done bit is seen.
- Native USB Serial/JTAG is Full Speed. Continuous raw 32-bit IQ at a possible
  tens-of-MS/s producer rate does not fit. A final continuous design needs
  on-device phase difference/WBFM, anti-alias filtering, decimation and compact
  packing before USB; PC-side sync and image rendering can then do the heavier
  video work.

## Capture safety changes

The recovered RF dump handoff can temporarily give the MAC ownership of HP
SRAM used by normal application code and stacks. The bounded capture kernel now
runs entirely from LP RAM, switches to an LP-RAM stack, pauses the hardware
stack guard around that deliberate switch, polls with a CPU-cycle deadline,
disables the producer and restores CPU SRAM ownership before returning.

This path remains experimental until `C5VRX_CAPTURE_KERNEL` plus a complete IQ
payload are observed repeatedly on hardware. RF capture is command-triggered;
it no longer starts automatically during boot, so USB control remains available
for diagnosis.

## USB transport and Receiver Console

Packet type 4 (legacy) contains one large IQ block. Packet type 5 fragments a
finite capture into independently CRC-protected chunks. Its little-endian
descriptor is five `uint32_t` values:

1. capture id;
2. total words;
3. word offset;
4. words in this chunk;
5. flags (bit 0 first, bit 1 last).

The current chunk size is 256 IQ words (1024 data bytes). The host validates
each chunk, rejects mixed/conflicting captures and reassembles only contiguous
offsets. CRC errors include packet type, sequence, payload size and both CRCs.

The combined XIAO Receiver Console exe includes an A1 IQ-to-PC-video mode. It
repeatedly requests safe 16K blocks, performs an FM discriminator on the PC,
finds candidate horizontal line spacing, resamples active line portions to 160
pixels and updates a 160x120 grayscale image row by row. It reports USB Mbit/s,
blocks/s and the finite-fill MS/s estimate. Until a continuous producer exists,
the UI explicitly labels this mode `RETRIGGERED, NOT GAPLESS`.

## Windows flashing/USB behavior

An esptool `SerialTimeoutException: Write timeout` occurs during bootloader sync,
before firmware is written. A fresh manual flash action often succeeds because
the failed in-process invocation has finally released COM10. The XIAO flasher
therefore runs every esptool attempt in a new OS process, waits for exclusive
port access and retries only after that process and its Win32 handle are gone.

After flashing or an abnormal capture/console close, Windows can show COM10 as
present while the endpoint returns write timeouts or no bytes. A physical USB
disconnect/reconnect has been the reliable recovery. Do not misclassify that
host endpoint state as an RF or firmware result.

The normal app was later observed by JTAG at `esp_cpu_wait_for_intr` while the
VFS console still returned zero bytes. This proved the silent state was not the
ROM downloader or an application crash. The control reader, control replies,
ESP logs and framed IQ/video packets were therefore moved onto one shared,
direct `usb_serial_jtag_*` transport. The new control reader assembles command
lines itself instead of using `fgets(stdin)`.

That direct transport was then proven on the physical XIAO. After a verified
flash and the required USB power-cycle, the normal receiver emitted
`C5VRX_READY`, the Windows console classified `C5VRX_RUNTIME_READY
handshake=PASS`, `PING` returned `C5VRX_PONG`, and Wi-Fi completed with
`C5VRX_BOOT stage=WIFI5_READY`. A1 boot tuning read back channel 173 at 40 MHz.
Early `STATUS` calls returned code 259 only while Wi-Fi initialization was still
in progress; this is the intended fail-open control-before-RF startup order.

The first repeated type-5 hardware run transferred 43 complete 16384-word
blocks with zero protocol errors and zero device write failures. Every capture
timed out at the former 10 ms LP-kernel deadline and every reconstructed block
had the identical CRC32 `023ecbba`. This proves the chunked USB transport while
also proving those words were stale SRAM, not 43 fresh RF captures. Timeout data
is now rejected instead of published, and the CPU-cycle deadline was increased
to 50 ms while remaining below the configured 300 ms interrupt watchdog.

With that deadline, the first physical capture attempt still timed out after
51.701 ms with `final_control=80004000` and no done bit. A following attempt
completed after 38.631 ms with `final_control=80044000`, `done=1`, and a
0.424115 MS/s finite-fill estimate. This is the first direct proof that this
bounded kernel reached a fresh hardware completion. The estimate includes
trigger/fill overhead and is not a calibrated producer sample rate.

That successful payload fell back to ASCII because the preview lifecycle had
already returned `STOP code=263`; a later `START` consequently returned code
257 while the old task still existed. The stop loop used
`pdMS_TO_TICKS(1)`, which can round to zero at the configured tick rate. The
preview lifecycle now uses real scheduler ticks, has idempotent START/STOP
commands, and avoids a potentially blocking STREAM_END write during teardown.
The Receiver Console waits for `START code=0` before its first capture and
retries a bounded RF timeout without tearing down the binary transport.

Follow-up hardware testing showed that 4096-word captures and repeated
16384-word captures all failed identically in the LP register clone: the
length field changed correctly, but the completion bit never asserted. This
rules out the 64 KiB block size as the cause. The capture path therefore uses
the complete, hash-pinned Espressif `adctrig()` as a fallback after the first
bounded LP miss. Before either attempt, CPU-owned dump RAM is filled with a
per-word sentinel. Vendor output is accepted only when it returns before the
recovered vendor timeout and at least 99 percent of the requested words have
actually replaced their sentinel. This retains fail-closed stale-RAM rejection
while returning to the vendor path that produced the earlier RF-dependent IQ.

The first hybrid hardware run proved binary transport and full sentinel
replacement, but also exposed two stricter requirements. Three consecutive
blocks were perfectly constant (`I=101`, `Q=40`) despite all 16384 sentinel
words being replaced, so replacement alone is not proof of usable IQ. On the
fourth call a tick interrupt entered `int_wdt.c:reconfigure_ticks` while
`adctrig()` owned SRAM and caused a store-access panic. The decoded backtrace
placed `adctrig` below the interrupt handler and watchdog tick hook.

The LP clone is therefore no longer run before the vendor capture. The complete
vendor path is called inside a short critical section (successful hardware
calls measured about 2 ms), preventing the tick hook from running during SRAM
ownership changes. Acceptance additionally requires at least one percent of
adjacent words to differ. A fully overwritten but constant block now fails
closed and is never transported as video IQ.

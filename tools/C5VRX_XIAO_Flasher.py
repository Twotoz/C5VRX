#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""XIAO ESP32-C5 Receiver Console with hardened native-USB flashing.

The XIAO uses the ESP32-C5 USB-Serial/JTAG peripheral directly.  On Windows a
runtime reader which has only just been closed can briefly keep I/O pending on
the COM handle, and the generic DTR/RTS reset path is not the best fit for the
native USB transport.  This wrapper keeps the common Receiver Console UI while
making the XIAO flash transition explicit and retryable.
"""

from __future__ import annotations

import threading
import time
import traceback
from contextlib import redirect_stderr, redirect_stdout

import esptool
import serial

from C5VRX_Flasher import C5VRXApp, load_flash_plan


class C5VRXXIAOApp(C5VRXApp):
    """Receiver Console with XIAO/native-USB specific flash handling."""

    def disconnect_serial(self) -> None:
        """Close runtime USB and wait until the reader no longer owns the port."""
        self.serial_stop.set()
        self.preview_sequence = None

        ser = self.ser
        self.ser = None
        reader = self.serial_thread
        self.serial_thread = None

        if ser:
            # Cancel any overlapped Win32 I/O before close so esptool does not
            # race a still-pending ReadFile/ClearCommError on the same COM port.
            try:
                ser.cancel_read()
            except Exception:
                pass
            try:
                ser.cancel_write()
            except Exception:
                pass
            try:
                ser.close()
            except Exception:
                pass

        if (reader is not None and reader is not threading.current_thread()
                and reader.is_alive()):
            reader.join(timeout=1.0)

        self.connection_var.set("Disconnected")
        self.connect_btn.configure(text="Connect")

    @staticmethod
    def _replace_esptool_option(args: list[str], name: str, value: str) -> list[str]:
        out: list[str] = []
        i = 0
        while i < len(args):
            if args[i] == name and i + 1 < len(args):
                i += 2
                continue
            out.append(args[i])
            i += 1
        out += [name, value]
        return out

    def _flash_worker(self, port: str) -> None:
        try:
            # Let Windows finish retiring the runtime COM handle before the
            # ROM-loader connection opens the same device again.
            time.sleep(0.30)

            base_args, files = load_flash_plan()
            base_args = self._replace_esptool_option(
                base_args, "--before", "usb-reset")
            base_args = self._replace_esptool_option(
                base_args, "--after", "watchdog-reset")

            argv = base_args + ["--port", port, "write-flash"]
            for offset, path in files:
                argv += [str(offset), str(path)]

            self.sink.write("Firmware image set:\n")
            for offset, path in files:
                self.sink.write(f"  {offset}: {path.name}\n")
            self.sink.write(
                "\nConnecting to ESP32-C5 bootloader "
                "(XIAO USB reset mode)...\n")

            max_attempts = 3
            for attempt in range(1, max_attempts + 1):
                if attempt > 1:
                    self.sink.write(
                        f"\nC5VRX_FLASH_RETRY attempt={attempt}/{max_attempts} "
                        "reason=SERIAL_WRITE_TIMEOUT\n")
                    time.sleep(0.75)
                try:
                    with redirect_stdout(self.sink), redirect_stderr(self.sink):
                        esptool.main(argv)
                    self.after(0, self._done_ok)
                    return
                except serial.SerialTimeoutException:
                    if attempt == max_attempts:
                        raise
                    continue
                except SystemExit as exc:
                    if exc.code in (0, None):
                        self.after(0, self._done_ok)
                    else:
                        self.after(
                            0, self._done_error,
                            f"esptool exited with code {exc.code}")
                    return

        except Exception as exc:
            self.sink.write("\n" + traceback.format_exc() + "\n")
            self.after(0, self._done_error, str(exc))


if __name__ == "__main__":
    C5VRXXIAOApp().mainloop()

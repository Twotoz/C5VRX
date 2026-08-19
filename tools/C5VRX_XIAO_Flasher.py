#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""XIAO ESP32-C5 Receiver Console with hardened native-USB flashing.

The XIAO uses the ESP32-C5 USB-Serial/JTAG peripheral directly. On Windows a
runtime reader which has only just been closed can briefly keep I/O pending on
the COM handle. This wrapper keeps the common Receiver Console UI while making
XIAO flash/reset transitions explicit, retryable, and runtime-readiness aware.
"""

from __future__ import annotations

import threading
import time
import traceback
from contextlib import redirect_stderr, redirect_stdout

import esptool
import serial
from esptool.util import FatalError

from C5VRX_Flasher import C5VRXApp, load_flash_plan


class C5VRXXIAOApp(C5VRXApp):
    """Receiver Console with XIAO/native-USB specific flash handling."""

    def __init__(self) -> None:
        super().__init__()
        self._runtime_seen = False
        self._runtime_probe_remaining = 0

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
            reader.join(timeout=1.5)

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

    @staticmethod
    def _retryable_port_error(exc: BaseException) -> bool:
        text = str(exc).lower()
        return any(token in text for token in (
            "port is busy",
            "doesn't exist",
            "access is denied",
            "toegang geweigerd",
            "permissionerror",
            "could not open",
        ))

    def _flash_worker(self, port: str) -> None:
        try:
            # Let Windows finish retiring the runtime COM handle before the
            # ROM-loader connection opens the same device again.
            time.sleep(0.45)

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

            max_attempts = 4
            for attempt in range(1, max_attempts + 1):
                if attempt > 1:
                    delay = 1.5 + (attempt - 2) * 0.75
                    self.sink.write(
                        f"\nC5VRX_FLASH_RETRY attempt={attempt}/{max_attempts} "
                        f"delay_s={delay:.2f} reason=USB_COM_TRANSITION\n")
                    time.sleep(delay)
                try:
                    with redirect_stdout(self.sink), redirect_stderr(self.sink):
                        esptool.main(argv)
                    self.after(0, self._done_ok)
                    return
                except serial.SerialTimeoutException:
                    if attempt == max_attempts:
                        raise
                    self.sink.write(
                        "C5VRX_FLASH_TRANSIENT reason=SERIAL_WRITE_TIMEOUT\n")
                    continue
                except FatalError as exc:
                    if attempt < max_attempts and self._retryable_port_error(exc):
                        self.sink.write(
                            f"C5VRX_FLASH_TRANSIENT reason=PORT_BUSY detail={exc}\n")
                        continue
                    raise
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

    def _done_ok(self) -> None:
        self._finish_flash()
        self.sink.write("\n=== FLASH COMPLETE ===\n")
        self.connection_var.set("Firmware flashed — waiting for C5VRX runtime...")
        self._runtime_seen = False
        # Native USB can become openable before app_main/control is ready.
        self.after(3500, self._auto_reconnect_after_flash)

    def _auto_reconnect_after_flash(self) -> None:
        self.refresh_ports()
        if self.connect_serial(show_errors=False):
            self.sink.write(
                "C5VRX USB port reopened; probing runtime until PONG/READY...\n")
            self.connection_var.set("USB open — waiting for C5VRX_PONG...")
            self._runtime_probe_remaining = 12
            self.after(650, self._runtime_probe)
        else:
            self.connection_var.set(
                "Flashed. USB runtime not open yet; press Connect after it reappears.")

    def _runtime_probe(self) -> None:
        if self._runtime_seen:
            return
        ser = self.ser
        if not ser or not ser.is_open:
            self.connection_var.set("Runtime USB disconnected before PONG")
            return
        if self._runtime_probe_remaining <= 0:
            self.sink.write(
                "C5VRX_RUNTIME_TIMEOUT no=PONG/READY duration_s~10 "
                "classification=FIRMWARE_BOOT_OR_CONTROL_NOT_READY\n")
            self.connection_var.set("USB open, but C5VRX runtime did not answer")
            return
        self._runtime_probe_remaining -= 1
        try:
            ser.write(b"PING\n")
            ser.flush()
            self.sink.write(
                f"> PING [runtime probe {12 - self._runtime_probe_remaining}/12]\n")
        except Exception as exc:
            self.sink.write(f"C5VRX_RUNTIME_PROBE_WRITE_FAILED error={exc}\n")
            return
        self.after(800, self._runtime_probe)

    def _parse_device_line(self, line: str) -> None:
        if (line.startswith("C5VRX_READY") or line.startswith("C5VRX_PONG")
                or line.startswith("C5VRX_STATUS")):
            first = not self._runtime_seen
            self._runtime_seen = True
            if first:
                self.connection_var.set(
                    f"Connected: {self.selected_port()} — C5VRX runtime ready")
                self.sink.write("C5VRX_RUNTIME_READY handshake=PASS\n")
                if not line.startswith("C5VRX_STATUS"):
                    self.after(100, lambda: self.send_command("STATUS"))
        super()._parse_device_line(line)


if __name__ == "__main__":
    C5VRXXIAOApp().mainloop()

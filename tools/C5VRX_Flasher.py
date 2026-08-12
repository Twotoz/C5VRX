#!/usr/bin/env python3
"""Tiny Windows GUI flasher for C5VRX.

The executable built from this file is intended for the first physical C5VRX
proof test. It bundles the firmware image set and lets the user select a serial
port, then flashes the ESP32-C5 automatically.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import traceback
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

import esptool
from serial.tools import list_ports

APP_TITLE = "C5VRX Flasher"


def resource_dir() -> Path:
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / "firmware"
    return Path(__file__).resolve().parent.parent / "firmware"


def load_flash_plan() -> tuple[list[str], list[tuple[str, Path]]]:
    fw = resource_dir()
    manifest = fw / "flasher_args.json"
    if not manifest.exists():
        raise FileNotFoundError(f"Firmware manifest not found: {manifest}")

    data = json.loads(manifest.read_text(encoding="utf-8"))
    extra = data.get("extra_esptool_args", {})

    args: list[str] = ["--chip", extra.get("chip", "esp32c5")]
    if extra.get("before"):
        args += ["--before", str(extra["before"])]
    if extra.get("after"):
        args += ["--after", str(extra["after"])]
    if extra.get("baud"):
        args += ["--baud", str(extra["baud"])]

    files: list[tuple[str, Path]] = []
    flash_files = data.get("flash_files", {})
    for offset, rel in flash_files.items():
        p = fw / rel
        if not p.exists():
            # ESP-IDF sometimes stores nested paths in flasher_args.json while
            # our packaging flattens the copied firmware files.
            p = fw / Path(rel).name
        if not p.exists():
            raise FileNotFoundError(f"Firmware file missing for {offset}: {rel}")
        files.append((offset, p))

    if not files:
        raise RuntimeError("No flash files found in flasher_args.json")
    return args, files


class TextSink:
    def __init__(self, text: tk.Text):
        self.text = text

    def write(self, s: str) -> int:
        if s:
            self.text.after(0, self._append, s)
        return len(s)

    def flush(self) -> None:
        pass

    def _append(self, s: str) -> None:
        self.text.configure(state="normal")
        self.text.insert("end", s)
        self.text.see("end")
        self.text.configure(state="disabled")


class Flasher(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("720x480")
        self.minsize(620, 420)
        self._busy = False

        root = ttk.Frame(self, padding=16)
        root.pack(fill="both", expand=True)

        ttk.Label(root, text="C5VRX", font=("Segoe UI", 24, "bold")).pack(anchor="w")
        ttk.Label(root, text="ESP32-C5 analog FPV research flasher").pack(anchor="w", pady=(0, 14))

        row = ttk.Frame(root)
        row.pack(fill="x")
        ttk.Label(row, text="USB / COM port:").pack(side="left")
        self.port_var = tk.StringVar()
        self.port_combo = ttk.Combobox(row, textvariable=self.port_var, state="readonly", width=48)
        self.port_combo.pack(side="left", padx=8, fill="x", expand=True)
        ttk.Button(row, text="Refresh", command=self.refresh_ports).pack(side="left")

        self.flash_btn = ttk.Button(root, text="FLASH C5VRX", command=self.flash)
        self.flash_btn.pack(fill="x", pady=14, ipady=8)

        self.progress = ttk.Progressbar(root, mode="indeterminate")
        self.progress.pack(fill="x", pady=(0, 10))

        self.log = tk.Text(root, height=15, wrap="word", state="disabled", font=("Consolas", 9))
        self.log.pack(fill="both", expand=True)
        self.sink = TextSink(self.log)

        ttk.Label(
            root,
            text="First test firmware: A4 / 5805 MHz, HT40, direct retune OFF, finite IQ dump ON.",
        ).pack(anchor="w", pady=(10, 0))

        self.refresh_ports()

    def refresh_ports(self) -> None:
        ports = list(list_ports.comports())
        values = [f"{p.device}  —  {p.description}" for p in ports]
        self.port_combo["values"] = values
        if values:
            self.port_combo.current(0)
        else:
            self.port_var.set("")

    def selected_port(self) -> str | None:
        value = self.port_var.get().strip()
        if not value:
            return None
        return value.split()[0]

    def flash(self) -> None:
        if self._busy:
            return
        port = self.selected_port()
        if not port:
            messagebox.showerror(APP_TITLE, "No USB/COM port selected.")
            return
        self._busy = True
        self.flash_btn.configure(state="disabled")
        self.port_combo.configure(state="disabled")
        self.progress.start(10)
        self.sink.write(f"\n=== C5VRX flash start: {port} ===\n")
        threading.Thread(target=self._flash_worker, args=(port,), daemon=True).start()

    def _flash_worker(self, port: str) -> None:
        try:
            base_args, files = load_flash_plan()
            argv = base_args + ["--port", port, "write-flash"]
            for offset, path in files:
                argv += [str(offset), str(path)]

            self.sink.write("Firmware image set:\n")
            for offset, path in files:
                self.sink.write(f"  {offset}: {path.name}\n")
            self.sink.write("\nConnecting to ESP32-C5...\n")

            with redirect_stdout(self.sink), redirect_stderr(self.sink):
                esptool.main(argv)

            self.after(0, self._done_ok)
        except SystemExit as e:
            if e.code in (0, None):
                self.after(0, self._done_ok)
            else:
                self.after(0, self._done_error, f"esptool exited with code {e.code}")
        except Exception as e:
            self.sink.write("\n" + traceback.format_exc() + "\n")
            self.after(0, self._done_error, str(e))

    def _finish(self) -> None:
        self._busy = False
        self.progress.stop()
        self.flash_btn.configure(state="normal")
        self.port_combo.configure(state="readonly")
        self.refresh_ports()

    def _done_ok(self) -> None:
        self._finish()
        self.sink.write("\n=== FLASH COMPLETE ===\n")
        messagebox.showinfo(APP_TITLE, "C5VRX flashed successfully.\n\nOpen a serial monitor at 115200 baud to capture the IQ log.")

    def _done_error(self, msg: str) -> None:
        self._finish()
        self.sink.write(f"\n=== FLASH FAILED: {msg} ===\n")
        messagebox.showerror(APP_TITLE, f"Flash failed:\n\n{msg}\n\nTry another COM port or hold BOOT while connecting.")


if __name__ == "__main__":
    Flasher().mainloop()

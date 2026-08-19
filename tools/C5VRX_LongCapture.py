#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""C5VRX safe long IQ capture helper.

The ESP32-C5 vendor RF-test dump RAM is fixed at 0x10000 bytes, i.e. 16384
packed 32-bit IQ words. This host tool never asks firmware for more than that
in a single CAPTURE command. Larger captures are collected as multiple finite
blocks and saved into one text file with explicit block-boundary markers.

Important: blocks are separately re-triggered and are NOT guaranteed gapless.
This tool is for longer RF/video evidence captures, not a claim of continuous
baseband streaming.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import serial
from serial.tools import list_ports

APP_TITLE = "C5VRX Long IQ Capture"
MAX_DEVICE_BLOCK = 16384
TOTAL_CHOICES = [16384, 65536, 262144, 524288, 1048576]


class LongCaptureApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("760x520")
        self.minsize(680, 460)

        self.ser: serial.Serial | None = None
        self.reader_thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.rx_buffer = bytearray()

        self.capture_active = False
        self.waiting_for_done = False
        self.total_target = 0
        self.total_received = 0
        self.block_index = 0
        self.block_requested = 0
        self.block_received = 0
        self.output_path: Path | None = None
        self.output_file = None
        self.capture_started_at = 0.0

        root = ttk.Frame(self, padding=14)
        root.pack(fill="both", expand=True)

        ttk.Label(root, text="C5VRX Long IQ Capture", font=("Segoe UI", 20, "bold")).pack(anchor="w")
        ttk.Label(
            root,
            text=(
                "Collect 64K / 256K / 512K / 1M IQ words safely as repeated 16K RF-dump blocks. "
                "Blocks are finite and may contain gaps; the ESP32-C5 dump RAM itself remains capped at 16K words."
            ),
            wraplength=720,
        ).pack(anchor="w", pady=(4, 14))

        port_row = ttk.Frame(root)
        port_row.pack(fill="x")
        ttk.Label(port_row, text="USB / COM port:").pack(side="left")
        self.port_var = tk.StringVar()
        self.port_combo = ttk.Combobox(port_row, textvariable=self.port_var, state="readonly", width=45)
        self.port_combo.pack(side="left", padx=8, fill="x", expand=True)
        ttk.Button(port_row, text="Refresh", command=self.refresh_ports).pack(side="left", padx=(0, 6))
        self.connect_btn = ttk.Button(port_row, text="Connect", command=self.toggle_connect)
        self.connect_btn.pack(side="left")

        self.connection_var = tk.StringVar(value="Disconnected")
        ttk.Label(root, textvariable=self.connection_var).pack(anchor="w", pady=(6, 14))

        capture_box = ttk.LabelFrame(root, text="Long finite capture", padding=12)
        capture_box.pack(fill="x")

        row = ttk.Frame(capture_box)
        row.pack(fill="x")
        ttk.Label(row, text="Total IQ words").pack(side="left")
        self.total_var = tk.StringVar(value="262144")
        ttk.Combobox(
            row,
            textvariable=self.total_var,
            state="readonly",
            values=[str(v) for v in TOTAL_CHOICES],
            width=12,
        ).pack(side="left", padx=8)
        self.capture_btn = ttk.Button(row, text="CAPTURE", command=self.start_capture)
        self.capture_btn.pack(side="left", padx=(8, 0), ipady=4)
        self.cancel_btn = ttk.Button(row, text="Cancel", command=self.cancel_capture, state="disabled")
        self.cancel_btn.pack(side="left", padx=8)

        self.progress = ttk.Progressbar(capture_box, mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=(12, 4))
        self.progress_var = tk.StringVar(value="Idle")
        ttk.Label(capture_box, textvariable=self.progress_var).pack(anchor="w")

        ttk.Label(
            capture_box,
            text=(
                "The output is plain text with IQ:xxxxxxxx records and C5VRX_HOST_BLOCK_BEGIN/END markers. "
                "For 256K this is 16 separate 16K captures, not one gapless 256K hardware buffer."
            ),
            wraplength=700,
        ).pack(anchor="w", pady=(8, 0))

        self.log = tk.Text(root, height=13, wrap="word", state="disabled", font=("Consolas", 9))
        self.log.pack(fill="both", expand=True, pady=(12, 0))

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.refresh_ports()

    def log_line(self, text: str) -> None:
        def append() -> None:
            self.log.configure(state="normal")
            self.log.insert("end", text + "\n")
            self.log.see("end")
            self.log.configure(state="disabled")
        self.after(0, append)

    def refresh_ports(self) -> None:
        selected = self.selected_port()
        ports = list(list_ports.comports())
        values = [f"{p.device}  —  {p.description}" for p in ports]
        self.port_combo["values"] = values
        if selected:
            for i, value in enumerate(values):
                if value.split()[0] == selected:
                    self.port_combo.current(i)
                    return
        if values:
            self.port_combo.current(0)
        else:
            self.port_var.set("")

    def selected_port(self) -> str | None:
        value = self.port_var.get().strip()
        return value.split()[0] if value else None

    def toggle_connect(self) -> None:
        if self.ser and self.ser.is_open:
            self.disconnect()
        else:
            self.connect()

    def connect(self) -> None:
        port = self.selected_port()
        if not port:
            messagebox.showerror(APP_TITLE, "No USB/COM port selected.")
            return
        self.disconnect()
        try:
            self.ser = serial.Serial(port, 115200, timeout=0.1, write_timeout=1)
            self.stop_event.clear()
            self.rx_buffer.clear()
            self.reader_thread = threading.Thread(target=self.reader_loop, daemon=True)
            self.reader_thread.start()
            self.connection_var.set(f"Connected: {port}")
            self.connect_btn.configure(text="Disconnect")
            self.send("PING")
            self.after(200, lambda: self.send("STATUS"))
        except Exception as exc:
            self.ser = None
            messagebox.showerror(APP_TITLE, f"Could not open {port}:\n\n{exc}")

    def disconnect(self) -> None:
        if self.capture_active:
            self.finish_capture(False, "serial disconnected")
        self.stop_event.set()
        ser = self.ser
        self.ser = None
        if ser:
            try:
                ser.close()
            except Exception:
                pass
        self.connection_var.set("Disconnected")
        self.connect_btn.configure(text="Connect")

    def send(self, command: str) -> None:
        ser = self.ser
        if not ser or not ser.is_open:
            return
        ser.write((command + "\n").encode("ascii"))
        ser.flush()
        self.log_line(f"> {command}")

    def start_capture(self) -> None:
        if self.capture_active:
            return
        if not self.ser or not self.ser.is_open:
            messagebox.showwarning(APP_TITLE, "Connect to C5VRX first.")
            return

        total = int(self.total_var.get())
        if total < 1:
            return
        path = filedialog.asksaveasfilename(
            title="Save long C5VRX IQ capture",
            defaultextension=".txt",
            initialfile=f"c5vrx-{total}-iq.txt",
            filetypes=[("Text capture", "*.txt"), ("All files", "*.*")],
        )
        if not path:
            return

        try:
            self.output_path = Path(path)
            self.output_file = self.output_path.open("w", encoding="ascii", buffering=1024 * 1024)
            self.output_file.write("# C5VRX long finite IQ capture\n")
            self.output_file.write(f"# total_requested={total}\n")
            self.output_file.write(f"# max_device_block={MAX_DEVICE_BLOCK}\n")
            self.output_file.write("# continuity=NOT_GUARANTEED blocks_are_separately_retriggered\n")
        except Exception as exc:
            self.output_file = None
            messagebox.showerror(APP_TITLE, f"Could not create capture file:\n\n{exc}")
            return

        self.capture_active = True
        self.waiting_for_done = False
        self.total_target = total
        self.total_received = 0
        self.block_index = 0
        self.block_requested = 0
        self.block_received = 0
        self.capture_started_at = time.monotonic()
        self.capture_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self.progress["value"] = 0
        self.progress_var.set("Starting...")
        self.log_line(f"=== LONG CAPTURE START total={total} ===")
        self.start_next_block()

    def start_next_block(self) -> None:
        if not self.capture_active:
            return
        remaining = self.total_target - self.total_received
        if remaining <= 0:
            self.finish_capture(True, "complete")
            return
        self.block_index += 1
        self.block_requested = min(MAX_DEVICE_BLOCK, remaining)
        self.block_received = 0
        self.waiting_for_done = True
        assert self.output_file is not None
        self.output_file.write(
            f"C5VRX_HOST_BLOCK_BEGIN index={self.block_index} requested={self.block_requested} total_before={self.total_received}\n"
        )
        self.output_file.flush()
        self.send(f"CAPTURE {self.block_requested}")
        self.update_progress()

    def cancel_capture(self) -> None:
        if self.capture_active:
            self.finish_capture(False, "cancelled by user")

    def reader_loop(self) -> None:
        ser = self.ser
        if not ser:
            return
        try:
            while not self.stop_event.is_set() and ser.is_open:
                chunk = ser.read(8192)
                if not chunk:
                    continue
                self.rx_buffer.extend(chunk)
                while b"\n" in self.rx_buffer:
                    raw_line, _, rest = self.rx_buffer.partition(b"\n")
                    self.rx_buffer = bytearray(rest)
                    line = raw_line.rstrip(b"\r").decode("ascii", errors="replace")
                    self.handle_line(line)
        except Exception as exc:
            if not self.stop_event.is_set():
                self.log_line(f"Serial reader stopped: {exc}")
                self.after(0, self.finish_capture, False, f"serial error: {exc}")

    def handle_line(self, line: str) -> None:
        if line.startswith("IQ:") and self.capture_active:
            value = line[3:].strip()
            if len(value) == 8:
                try:
                    int(value, 16)
                except ValueError:
                    return
                if self.output_file is not None:
                    self.output_file.write(f"IQ:{value}\n")
                self.block_received += 1
                self.total_received += 1
                if (self.total_received & 0x3ff) == 0:
                    self.after(0, self.update_progress)
            return

        # Avoid flooding the Tk log with every low-level status line, but retain
        # the important control markers for debugging.
        if (line.startswith("C5VRX_") or line.startswith("I (") or
                line.startswith("W (") or line.startswith("E (")):
            self.log_line(line)

        if line == "C5VRX_IQ_END" and self.capture_active:
            if self.output_file is not None:
                self.output_file.write(
                    f"C5VRX_HOST_BLOCK_END index={self.block_index} received={self.block_received}\n"
                )
                self.output_file.flush()
            return

        if line.startswith("C5VRX_CAPTURE_DONE") and self.capture_active:
            code = None
            for part in line.split():
                if part.startswith("code="):
                    try:
                        code = int(part.split("=", 1)[1])
                    except ValueError:
                        pass
            if code != 0:
                self.after(0, self.finish_capture, False, f"device capture failed: {line}")
                return
            if self.block_received != self.block_requested:
                self.after(
                    0,
                    self.finish_capture,
                    False,
                    f"short block {self.block_index}: received {self.block_received}/{self.block_requested}",
                )
                return
            self.waiting_for_done = False
            self.after(20, self.start_next_block)

    def update_progress(self) -> None:
        if not self.total_target:
            return
        pct = min(100.0, self.total_received * 100.0 / self.total_target)
        self.progress["value"] = pct
        elapsed = max(0.001, time.monotonic() - self.capture_started_at)
        rate = self.total_received / elapsed if self.total_received else 0.0
        remaining = self.total_target - self.total_received
        eta = remaining / rate if rate else 0.0
        self.progress_var.set(
            f"{self.total_received:,}/{self.total_target:,} words  •  block {self.block_index}  •  {pct:.1f}%"
            + (f"  •  ETA ~{eta:.0f}s" if rate else "")
        )

    def finish_capture(self, success: bool, reason: str) -> None:
        if not self.capture_active and self.output_file is None:
            return
        self.capture_active = False
        self.waiting_for_done = False
        self.capture_btn.configure(state="normal")
        self.cancel_btn.configure(state="disabled")
        self.update_progress()

        path = self.output_path
        f = self.output_file
        self.output_file = None
        if f is not None:
            try:
                f.write(
                    f"# capture_result={'COMPLETE' if success else 'INCOMPLETE'} received={self.total_received} requested={self.total_target} reason={reason}\n"
                )
                f.close()
            except Exception:
                pass

        if success:
            self.progress["value"] = 100
            self.progress_var.set(
                f"Complete: {self.total_received:,} IQ words in {self.block_index} finite blocks"
            )
            self.log_line(f"=== LONG CAPTURE COMPLETE file={path} ===")
            if path:
                messagebox.showinfo(
                    APP_TITLE,
                    f"Capture complete.\n\n{self.total_received:,} IQ words\n{self.block_index} finite blocks\n\nSaved to:\n{path}\n\nBlocks are not guaranteed gapless.",
                )
        else:
            self.progress_var.set(f"Stopped: {reason}")
            self.log_line(f"=== LONG CAPTURE STOPPED reason={reason} file={path} ===")
            if path:
                messagebox.showwarning(
                    APP_TITLE,
                    f"Capture stopped: {reason}\n\nPartial data was kept at:\n{path}",
                )

    def on_close(self) -> None:
        if self.capture_active:
            if not messagebox.askyesno(APP_TITLE, "A capture is running. Stop it and close?"):
                return
            self.finish_capture(False, "application closed")
        self.disconnect()
        self.destroy()


if __name__ == "__main__":
    LongCaptureApp().mainloop()

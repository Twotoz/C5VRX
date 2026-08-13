#!/usr/bin/env python3
"""C5VRX Windows flasher + runtime USB control panel.

The one-file executable bundles the ESP32-C5 firmware, flashes it, reconnects
through USB Serial/JTAG, allows FPV band/channel selection without reflashing,
and can trigger finite IQ captures for the reverse-engineering workflow.
"""

from __future__ import annotations

import json
import math
import sys
import threading
import time
import traceback
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

import esptool
import serial
from serial.tools import list_ports

from c5vrx_usb_protocol import (
    FRAME_DESCRIPTOR,
    PACKET_GRAY8_FRAME,
    PACKET_STREAM_END,
    PACKET_STREAM_INFO,
    PIXEL_FORMAT_GRAY8,
    Packet,
    StreamDecoder,
)

APP_TITLE = "C5VRX Receiver Console"
C5_RX_MAX_MHZ = 5885

FPV_BANDS = {
    "A": [5865, 5845, 5825, 5805, 5785, 5765, 5745, 5725],
    "B": [5733, 5752, 5771, 5790, 5809, 5828, 5847, 5866],
    "E": [5705, 5685, 5665, 5645, 5885, 5905, 5925, 5945],
    "F": [5740, 5760, 5780, 5800, 5820, 5840, 5860, 5880],
    "R": [5658, 5695, 5732, 5769, 5806, 5843, 5880, 5917],
}


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
    for offset, rel in data.get("flash_files", {}).items():
        p = fw / rel
        if not p.exists():
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


class C5VRXApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("860x650")
        self.minsize(760, 580)

        self._busy = False
        self.ser: serial.Serial | None = None
        self.serial_thread: threading.Thread | None = None
        self.serial_stop = threading.Event()
        self.iq_words: list[int] = []
        self.iq_capture_active = False
        self.preview_frame: bytes | None = None
        self.preview_width = 160
        self.preview_height = 120
        self.preview_image: tk.PhotoImage | None = None
        self.preview_sequence: int | None = None
        self.first_test_active = False
        self.first_test_fine_sent = False
        self.first_test_center = 5805

        root = ttk.Frame(self, padding=14)
        root.pack(fill="both", expand=True)

        header = ttk.Frame(root)
        header.pack(fill="x")
        ttk.Label(header, text="C5VRX", font=("Segoe UI", 25, "bold")).pack(side="left")
        ttk.Label(header, text="ESP32-C5 analog FPV receiver & first-hardware console").pack(side="left", padx=12, pady=(8, 0))

        port_row = ttk.Frame(root)
        port_row.pack(fill="x", pady=(12, 8))
        ttk.Label(port_row, text="USB / COM port:").pack(side="left")
        self.port_var = tk.StringVar()
        self.port_combo = ttk.Combobox(port_row, textvariable=self.port_var, state="readonly", width=48)
        self.port_combo.pack(side="left", padx=8, fill="x", expand=True)
        ttk.Button(port_row, text="Refresh", command=self.refresh_ports).pack(side="left", padx=(0, 6))
        self.connect_btn = ttk.Button(port_row, text="Connect", command=self.toggle_connect)
        self.connect_btn.pack(side="left")

        self.connection_var = tk.StringVar(value="Disconnected")
        ttk.Label(root, textvariable=self.connection_var).pack(anchor="w", pady=(0, 8))

        notebook = ttk.Notebook(root)
        notebook.pack(fill="both", expand=True)

        control_tab = ttk.Frame(notebook, padding=12)
        preview_tab = ttk.Frame(notebook, padding=12)
        notebook.add(control_tab, text="Flash & Control")
        notebook.add(preview_tab, text="USB Preview")

        self._build_control_tab(control_tab)
        self._build_preview_tab(preview_tab)

        self.log = tk.Text(root, height=12, wrap="word", state="disabled", font=("Consolas", 9))
        self.log.pack(fill="both", expand=False, pady=(10, 0))
        self.sink = TextSink(self.log)

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.refresh_ports()
        self.update_channel_label()

    def _build_control_tab(self, tab: ttk.Frame) -> None:
        flash_box = ttk.LabelFrame(tab, text="Firmware", padding=10)
        flash_box.pack(fill="x")

        self.flash_btn = ttk.Button(flash_box, text="FLASH C5VRX", command=self.flash)
        self.flash_btn.pack(side="left", fill="x", expand=True, ipady=7)
        self.progress = ttk.Progressbar(flash_box, mode="indeterminate", length=170)
        self.progress.pack(side="left", padx=(10, 0))

        ttk.Label(
            flash_box,
            text="One firmware image. Band/channel changes happen live over USB after flashing.",
        ).pack(anchor="w", pady=(8, 0))

        channel_box = ttk.LabelFrame(tab, text="Analog FPV channel", padding=10)
        channel_box.pack(fill="x", pady=(12, 0))

        row = ttk.Frame(channel_box)
        row.pack(fill="x")

        ttk.Label(row, text="Band").pack(side="left")
        self.band_var = tk.StringVar(value="A")
        band_combo = ttk.Combobox(row, textvariable=self.band_var, state="readonly", values=list(FPV_BANDS), width=6)
        band_combo.pack(side="left", padx=(6, 16))
        band_combo.bind("<<ComboboxSelected>>", lambda _e: self.update_channel_label())

        ttk.Label(row, text="Channel").pack(side="left")
        self.channel_var = tk.StringVar(value="4")
        channel_combo = ttk.Combobox(row, textvariable=self.channel_var, state="readonly", values=[str(i) for i in range(1, 9)], width=6)
        channel_combo.pack(side="left", padx=(6, 16))
        channel_combo.bind("<<ComboboxSelected>>", lambda _e: self.update_channel_label())

        ttk.Label(row, text="RX bandwidth").pack(side="left")
        self.bw_var = tk.StringVar(value="40")
        ttk.Combobox(row, textvariable=self.bw_var, state="readonly", values=["40", "20"], width=7).pack(side="left", padx=(6, 16))

        self.apply_btn = ttk.Button(row, text="Apply channel", command=self.apply_channel)
        self.apply_btn.pack(side="right")

        self.channel_info_var = tk.StringVar()
        ttk.Label(channel_box, textvariable=self.channel_info_var).pack(anchor="w", pady=(8, 0))

        capture_box = ttk.LabelFrame(tab, text="RF / IQ research", padding=10)
        capture_box.pack(fill="x", pady=(12, 0))

        cap_row = ttk.Frame(capture_box)
        cap_row.pack(fill="x")
        ttk.Label(cap_row, text="Finite IQ samples").pack(side="left")
        self.samples_var = tk.StringVar(value="16384")
        ttk.Combobox(
            cap_row,
            textvariable=self.samples_var,
            state="readonly",
            values=["1024", "2048", "4096", "8192", "16384"],
            width=10,
        ).pack(side="left", padx=8)
        self.capture_btn = ttk.Button(cap_row, text="Capture IQ", command=self.capture_iq)
        self.capture_btn.pack(side="left")
        ttk.Button(cap_row, text="Status", command=lambda: self.send_command("STATUS")).pack(side="left", padx=8)

        ttk.Label(
            capture_box,
            text="CAPTURE uses the recovered Espressif RF-test dump path. It is a finite diagnostic capture, not continuous video yet.",
        ).pack(anchor="w", pady=(8, 0))

        diag_box = ttk.LabelFrame(tab, text="First hardware diagnostics", padding=10)
        diag_box.pack(fill="x", pady=(12, 0))
        ttk.Button(
            diag_box,
            text="FIRST HARDWARE TEST",
            command=self.first_hardware_test,
        ).pack(side="left", ipady=5)
        ttk.Label(
            diag_box,
            text="Runs fail-closed cadence, wrap, phase, BitScrambler and staged soak tests. Use a coherent RF tone for phase evidence.",
            wraplength=610,
        ).pack(side="left", padx=12)

    def _build_preview_tab(self, tab: ttk.Frame) -> None:
        ttk.Label(tab, text="USB-C signal preview", font=("Segoe UI", 14, "bold")).pack(anchor="w")
        ttk.Label(
            tab,
            text=(
                "Displays framed 160×120 GRAY8 video reduced on the C5 from the CVBS sample stream. "
                "Finite IQ waveform preview remains available as a diagnostic fallback."
            ),
            wraplength=760,
        ).pack(anchor="w", pady=(4, 10))

        self.preview_canvas = tk.Canvas(tab, height=280, background="black", highlightthickness=1)
        self.preview_canvas.pack(fill="both", expand=True)
        self.preview_canvas.bind("<Configure>", lambda _e: self.render_iq_preview())

        self.preview_status_var = tk.StringVar(value="No IQ capture yet")
        ttk.Label(tab, textvariable=self.preview_status_var).pack(anchor="w", pady=(8, 0))

        preview_controls = ttk.Frame(tab)
        preview_controls.pack(fill="x", pady=(8, 0))
        ttk.Button(preview_controls, text="Capture 16K IQ", command=self.capture_iq_16k).pack(side="left")
        ttk.Button(preview_controls, text="Start live preview", command=self.start_usb_preview).pack(side="left", padx=8)
        ttk.Button(preview_controls, text="Stop live preview", command=lambda: self.send_command("USB PREVIEW STOP")).pack(side="left")
        ttk.Button(preview_controls, text="Clear", command=self.clear_preview).pack(side="left", padx=8)

    def refresh_ports(self) -> None:
        selected_device = self.selected_port()
        ports = list(list_ports.comports())
        values = [f"{p.device}  —  {p.description}" for p in ports]
        self.port_combo["values"] = values
        if selected_device:
            for i, value in enumerate(values):
                if value.split()[0] == selected_device:
                    self.port_combo.current(i)
                    return
        if values:
            self.port_combo.current(0)
        else:
            self.port_var.set("")

    def selected_port(self) -> str | None:
        value = self.port_var.get().strip()
        return value.split()[0] if value else None

    def update_channel_label(self) -> None:
        try:
            band = self.band_var.get()
            ch = int(self.channel_var.get())
            mhz = FPV_BANDS[band][ch - 1]
        except Exception:
            return
        if mhz <= C5_RX_MAX_MHZ:
            exact_note = "inside ESP32-C5 specified RX window"
        else:
            exact_note = "OUTSIDE ESP32-C5 specified RX window"
        self.channel_info_var.set(f"{band}{ch}  •  {mhz} MHz  •  {exact_note}")

    def toggle_connect(self) -> None:
        if self.ser and self.ser.is_open:
            self.disconnect_serial()
        else:
            self.connect_serial(show_errors=True)

    def connect_serial(self, show_errors: bool = False) -> bool:
        port = self.selected_port()
        if not port:
            if show_errors:
                messagebox.showerror(APP_TITLE, "No USB/COM port selected.")
            return False

        self.disconnect_serial()
        try:
            self.ser = serial.Serial(port, 115200, timeout=0.15, write_timeout=1)
            self.serial_stop.clear()
            self.serial_thread = threading.Thread(target=self._serial_reader, daemon=True)
            self.serial_thread.start()
            self.connection_var.set(f"Connected: {port}")
            self.connect_btn.configure(text="Disconnect")
            self.sink.write(f"\n=== CONNECTED: {port} ===\n")
            self.after(250, lambda: self.send_command("PING"))
            self.after(450, lambda: self.send_command("STATUS"))
            return True
        except Exception as exc:
            self.ser = None
            self.connection_var.set("Disconnected")
            self.connect_btn.configure(text="Connect")
            if show_errors:
                messagebox.showerror(APP_TITLE, f"Could not open {port}:\n\n{exc}")
            return False

    def disconnect_serial(self) -> None:
        self.serial_stop.set()
        self.preview_sequence = None
        ser = self.ser
        self.ser = None
        if ser:
            try:
                ser.close()
            except Exception:
                pass
        self.connection_var.set("Disconnected")
        self.connect_btn.configure(text="Connect")

    def _serial_reader(self) -> None:
        ser = self.ser
        if not ser:
            return
        decoder = StreamDecoder()
        try:
            while not self.serial_stop.is_set() and ser.is_open:
                raw = ser.read(4096)
                if not raw:
                    continue
                for kind, value in decoder.feed(raw):
                    if kind == "line":
                        line = str(value)
                        self.sink.write(line + "\n")
                        self._parse_device_line(line)
                    elif kind == "packet":
                        assert isinstance(value, Packet)
                        self._handle_usb_packet(value)
                    else:
                        self.sink.write(
                            f"C5VRX_PREVIEW_RESYNC reason={value}\n")
        except Exception as exc:
            if not self.serial_stop.is_set():
                self.sink.write(f"\nSerial reader stopped: {exc}\n")
                self.after(0, self._serial_lost)

    def _serial_lost(self) -> None:
        self.disconnect_serial()

    def _parse_device_line(self, line: str) -> None:
        if (self.first_test_active and not self.first_test_fine_sent
                and line.startswith("C5VRX_PRODUCER_CADENCE mode=0 ")):
            fields = self._fields(line)
            if fields.get("classification") == "MEASURED":
                rate = int(fields.get("complex_samples_per_sec", "0"))
                if rate:
                    center = self.first_test_center
                    tone = center + 2
                    self.first_test_fine_sent = True
                    try:
                        assert self.ser is not None
                        self.ser.write(f"FINE TUNE VERIFY {center} {tone} {rate}\n".encode("ascii"))
                        self.ser.flush()
                        self.sink.write(f"> FINE TUNE VERIFY {center} {tone} {rate}\n")
                    except Exception as exc:
                        self.sink.write(f"C5VRX_FINE_TUNE_AUTOMATION_FAILED error={exc}\n")
        if line.startswith("C5VRX_IQ_BEGIN"):
            self.iq_words = []
            self.iq_capture_active = True
            self.after(0, self.preview_status_var.set, "Receiving IQ samples over USB-C...")
            return
        if line.startswith("IQ:") and self.iq_capture_active:
            try:
                self.iq_words.append(int(line[3:], 16))
            except ValueError:
                pass
            return
        if line == "C5VRX_IQ_END":
            self.iq_capture_active = False
            words = len(self.iq_words)
            self.after(0, self.preview_status_var.set, f"Captured {words} complex IQ samples — rendering FM discriminator")
            self.after(0, self.render_iq_preview)
            return
        if line.startswith("C5VRX_STATUS") or line.startswith("C5VRX_OK set"):
            self.after(0, self._apply_status_line, line)

    @staticmethod
    def _fields(line: str) -> dict[str, str]:
        fields: dict[str, str] = {}
        for part in line.split():
            if "=" in part:
                key, value = part.split("=", 1)
                fields[key] = value
        return fields

    def _handle_usb_packet(self, packet: Packet) -> None:
        if self.preview_sequence is not None and packet.sequence != (
                self.preview_sequence + 1) & 0xFFFFFFFF:
            self.sink.write(
                "C5VRX_PREVIEW_SEQUENCE_GAP "
                f"expected={(self.preview_sequence + 1) & 0xFFFFFFFF} "
                f"received={packet.sequence}\n")
        self.preview_sequence = packet.sequence

        if packet.packet_type == PACKET_STREAM_END:
            dropped = int.from_bytes(packet.payload[:8], "little") \
                if len(packet.payload) >= 8 else 0
            self.after(0, self.preview_status_var.set,
                       f"USB preview stopped; device dropped {dropped} frame(s)")
            return
        if packet.packet_type not in {PACKET_STREAM_INFO, PACKET_GRAY8_FRAME}:
            self.sink.write(
                f"C5VRX_PREVIEW_SKIP packet_type={packet.packet_type}\n")
            return
        if len(packet.payload) < FRAME_DESCRIPTOR.size:
            self.sink.write("C5VRX_PREVIEW_DROP reason=SHORT_DESCRIPTOR\n")
            return

        width, height, stride, pixel_format, flags = \
            FRAME_DESCRIPTOR.unpack_from(packet.payload)
        if (not width or not height or width > 640 or height > 480 or
                stride < width or pixel_format != PIXEL_FORMAT_GRAY8):
            self.sink.write("C5VRX_PREVIEW_DROP reason=UNSUPPORTED_FORMAT\n")
            return
        if packet.packet_type == PACKET_STREAM_INFO:
            self.preview_sequence = packet.sequence
            self.after(0, self.preview_status_var.set,
                       f"USB preview v1: {width}×{height}, waiting for H/V lock")
            return

        pixels = packet.payload[FRAME_DESCRIPTOR.size:]
        if len(pixels) != stride * height:
            self.sink.write("C5VRX_PREVIEW_DROP reason=PAYLOAD_SIZE\n")
            return
        if stride != width:
            pixels = b"".join(
                pixels[row * stride:row * stride + width]
                for row in range(height)
            )
        self.after(0, self._show_gray_frame, pixels, width, height)
        if not (flags & 1):
            self.sink.write("C5VRX_PREVIEW_WARNING reason=SYNC_UNLOCKED\n")

    def _apply_status_line(self, line: str) -> None:
        fields = self._fields(line)
        band = fields.get("band")
        ch = fields.get("channel")
        bw = fields.get("bw")
        if band in FPV_BANDS:
            self.band_var.set(band)
        if ch in {str(i) for i in range(1, 9)}:
            self.channel_var.set(ch)
        if bw in {"20", "40"}:
            self.bw_var.set(bw)
        self.update_channel_label()

    def send_command(self, command: str) -> None:
        ser = self.ser
        if not ser or not ser.is_open:
            messagebox.showwarning(APP_TITLE, "Connect to C5VRX first.")
            return
        try:
            ser.write((command.strip() + "\n").encode("ascii"))
            ser.flush()
            self.sink.write(f"> {command}\n")
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"USB command failed:\n\n{exc}")
            self.disconnect_serial()

    def apply_channel(self) -> None:
        band = self.band_var.get()
        ch = int(self.channel_var.get())
        mhz = FPV_BANDS[band][ch - 1]
        if mhz > C5_RX_MAX_MHZ:
            messagebox.showwarning(
                APP_TITLE,
                f"{band}{ch} is {mhz} MHz, above the current ESP32-C5 specified RX limit of {C5_RX_MAX_MHZ} MHz.",
            )
            return
        self.send_command(f"BW {self.bw_var.get()}")
        self.after(100, lambda: self.send_command(f"SET {band} {ch}"))

    def capture_iq(self) -> None:
        self.send_command(f"CAPTURE {int(self.samples_var.get())}")

    def capture_iq_16k(self) -> None:
        self.samples_var.set("16384")
        self.capture_iq()

    def start_usb_preview(self) -> None:
        self.send_command("USB PREVIEW START")
        self.after(100, lambda: self.send_command("LIVE START"))

    def first_hardware_test(self) -> None:
        if not self.ser or not self.ser.is_open:
            messagebox.showwarning(APP_TITLE, "Connect to C5VRX first.")
            return
        center = FPV_BANDS[self.band_var.get()][int(self.channel_var.get()) - 1]
        tone = center + 2
        if not messagebox.askokcancel(
            APP_TITLE,
            f"Set a coherent RF generator to {tone} MHz at a safe, attenuated level. "
            f"The receiver baseline is {center} MHz. The suite takes about 45 seconds and never enables unbounded capture. Continue?",
        ):
            return
        self.first_test_active = True
        self.first_test_fine_sent = False
        self.first_test_center = center
        commands = [
            "STATUS",
            "WBFM HWTEST",
            "PRODUCER CADENCE PROBE ALL",
            "WRAP FLAG PROBE 0",
            "PHASE CONTINUITY PROBE 0",
            "PRODUCER SOAK 0 30000",
            "BENCH SPARSE 2",
            "BENCH SPARSE 4",
            "BENCH SPARSE 8",
            "BENCH BITSCRAMBLER",
            "BENCH PARLIO",
            "BENCH PIPELINE",
            "BENCH USB PREVIEW",
            "BENCH RING PIPELINE 0 1000",
            "USB PREVIEW STOP",
            "CAPABILITIES",
            "STATUS",
        ]
        try:
            payload = "".join(command + "\n" for command in commands).encode("ascii")
            self.ser.write(payload)
            self.ser.flush()
            self.sink.write("\n=== FIRST HARDWARE TEST QUEUED ===\n")
            for command in commands:
                self.sink.write(f"> {command}\n")
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Could not start diagnostics:\n\n{exc}")

    @staticmethod
    def _decode_iq(raw: int) -> tuple[int, int]:
        def s10(v: int) -> int:
            v &= 0x3FF
            return v - 0x400 if v & 0x200 else v
        return s10(raw >> 10), s10(raw)

    def render_iq_preview(self) -> None:
        canvas = getattr(self, "preview_canvas", None)
        if not canvas:
            return
        canvas.delete("all")
        width = max(40, canvas.winfo_width())
        height = max(80, canvas.winfo_height())

        if len(self.iq_words) < 3:
            canvas.create_text(width / 2, height / 2, text="Capture IQ to see the FM discriminator waveform", fill="white")
            return

        target_points = max(100, min(width, 1200))
        step = max(1, (len(self.iq_words) - 1) // target_points)
        values: list[float] = []

        pi, pq = self._decode_iq(self.iq_words[0])
        for idx in range(1, len(self.iq_words), step):
            ci, cq = self._decode_iq(self.iq_words[idx])
            real = ci * pi + cq * pq
            imag = cq * pi - ci * pq
            values.append(math.atan2(imag, real))
            pi, pq = ci, cq

        if not values:
            return
        scale = (height * 0.42) / math.pi
        mid = height / 2
        pts: list[float] = []
        denom = max(1, len(values) - 1)
        for i, value in enumerate(values):
            x = i * (width - 1) / denom
            y = mid - value * scale
            pts.extend((x, y))

        canvas.create_line(0, mid, width, mid, fill="#555")
        canvas.create_line(*pts, fill="#35a7ff", width=1)
        canvas.create_text(8, 8, anchor="nw", text="WBFM discriminator — diagnostic preview, not decoded video yet", fill="white")

    def _show_gray_frame(self, payload: bytes, width: int, height: int) -> None:
        self.preview_frame = payload
        self.preview_width = width
        self.preview_height = height
        pgm = f"P5\n{width} {height}\n255\n".encode("ascii") + payload
        try:
            self.preview_image = tk.PhotoImage(data=pgm, format="PGM")
        except tk.TclError:
            self.preview_status_var.set("Valid GRAY8 frame received; Tk cannot render PGM on this system")
            return
        canvas = self.preview_canvas
        canvas.delete("all")
        canvas.create_image(
            max(0, canvas.winfo_width() // 2),
            max(0, canvas.winfo_height() // 2),
            image=self.preview_image,
            anchor="center",
        )
        self.preview_status_var.set(f"Live USB preview: {width}×{height} GRAY8, CRC valid")

    def clear_preview(self) -> None:
        self.iq_words = []
        self.preview_frame = None
        self.preview_image = None
        self.preview_status_var.set("No IQ capture yet")
        self.render_iq_preview()

    def flash(self) -> None:
        if self._busy:
            return
        port = self.selected_port()
        if not port:
            messagebox.showerror(APP_TITLE, "No USB/COM port selected.")
            return

        self.disconnect_serial()
        self._busy = True
        self.flash_btn.configure(state="disabled")
        self.port_combo.configure(state="disabled")
        self.progress.start(10)
        self.sink.write(f"\n=== C5VRX FLASH START: {port} ===\n")
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
            self.sink.write("\nConnecting to ESP32-C5 bootloader...\n")

            with redirect_stdout(self.sink), redirect_stderr(self.sink):
                esptool.main(argv)

            self.after(0, self._done_ok)
        except SystemExit as exc:
            if exc.code in (0, None):
                self.after(0, self._done_ok)
            else:
                self.after(0, self._done_error, f"esptool exited with code {exc.code}")
        except Exception as exc:
            self.sink.write("\n" + traceback.format_exc() + "\n")
            self.after(0, self._done_error, str(exc))

    def _finish_flash(self) -> None:
        self._busy = False
        self.progress.stop()
        self.flash_btn.configure(state="normal")
        self.port_combo.configure(state="readonly")
        self.refresh_ports()

    def _done_ok(self) -> None:
        self._finish_flash()
        self.sink.write("\n=== FLASH COMPLETE ===\n")
        self.connection_var.set("Firmware flashed — waiting for USB console...")
        self.after(1800, self._auto_reconnect_after_flash)

    def _auto_reconnect_after_flash(self) -> None:
        self.refresh_ports()
        if self.connect_serial(show_errors=False):
            self.sink.write("C5VRX runtime control connected automatically.\n")
        else:
            self.connection_var.set("Flashed. Select the C5 USB console port and press Connect.")

    def _done_error(self, msg: str) -> None:
        self._finish_flash()
        self.sink.write(f"\n=== FLASH FAILED: {msg} ===\n")
        messagebox.showerror(
            APP_TITLE,
            f"Flash failed:\n\n{msg}\n\nTry another COM port or put the C5 into download mode (BOOT + RESET).",
        )

    def on_close(self) -> None:
        self.disconnect_serial()
        time.sleep(0.03)
        self.destroy()


if __name__ == "__main__":
    C5VRXApp().mainloop()

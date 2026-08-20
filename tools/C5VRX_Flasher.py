#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""C5VRX Windows flasher + runtime USB control panel.

The one-file executable bundles the ESP32-C5 firmware, flashes it, reconnects
through USB Serial/JTAG, allows FPV band/channel selection without reflashing,
and can trigger finite IQ captures for the reverse-engineering workflow.
"""

from __future__ import annotations

import json
import math
import statistics
import sys
import threading
import time
import traceback
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import esptool
import serial
from serial.tools import list_ports

from c5vrx_lab import SessionRecorder

from c5vrx_usb_protocol import (
    FRAME_DESCRIPTOR,
    PACKET_GRAY8_FRAME,
    PACKET_IQ_U32_BLOCK,
    PACKET_IQ_U32_CHUNK,
    PACKET_STREAM_END,
    PACKET_STREAM_INFO,
    PIXEL_FORMAT_GRAY8,
    Packet,
    StreamDecoder,
    decode_iq_block,
    decode_iq_chunk,
)

APP_TITLE = "C5VRX Receiver Console"
APP_BUILD = "video-proof-3"
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


def load_firmware_profile() -> dict[str, object]:
    """Load the identity of the firmware bundled into this executable."""
    profile_path = resource_dir() / "profile.json"
    if not profile_path.exists():
        return {
            "schema_version": 0,
            "profile_id": "legacy-unknown",
            "display_name": "legacy/unknown ESP32-C5 profile",
            "av_pin_summary": "Check the firmware bundle documentation before wiring AV",
            "flash_size_mb": 0,
        }

    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    if profile.get("schema_version") != 1:
        raise RuntimeError(f"Unsupported firmware profile schema: {profile_path}")
    for key in ("profile_id", "display_name", "av_pin_summary"):
        if not isinstance(profile.get(key), str) or not profile[key]:
            raise RuntimeError(f"Invalid firmware profile field {key}: {profile_path}")
    if not isinstance(profile.get("flash_size_mb"), int):
        raise RuntimeError(f"Invalid firmware profile flash_size_mb: {profile_path}")
    return profile


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
        self.firmware_profile = load_firmware_profile()
        self.expected_profile = str(self.firmware_profile["profile_id"])
        self.session = SessionRecorder(
            "receiver-console",
            parent=Path.home() / "Documents" / "C5VRX Sessions",
            board_profile=self.firmware_profile,
            test_config={
                "application": APP_TITLE,
                "baud": 115200,
                "rf_safety": {
                    "bounded_capture_only": True,
                    "no_rf_register_overrides": True,
                    "live_start_validation_preserved": True,
                },
            },
        )
        self.profile_mismatch_warned = False
        self.title(f"{APP_TITLE} — {self.firmware_profile['display_name']}")
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
        self.live_iq_active = False
        self.live_iq_capture_done = False
        self.live_iq_packet_done = False
        self.live_iq_capture_id: int | None = None
        self.live_iq_capture_timestamp_us: int | None = None
        self.live_iq_total_words = 0
        self.live_iq_chunks: dict[int, tuple[int, ...]] = {}
        self.live_iq_source_msps: float | None = None
        self.live_iq_usb_started = 0.0
        self.live_iq_usb_bytes = 0
        self.live_iq_blocks = 0
        self.live_video_host_frames = 0
        self.live_iq_request_started = 0.0
        self.live_iq_transport_ready = False
        self.live_iq_capture_retries = 0
        self.live_iq_max_retries = 3
        self.live_video_width = 160
        self.live_video_height = 120
        self.live_video_pixels = bytearray(
            self.live_video_width * self.live_video_height)
        self.live_video_row = 0
        self.live_video_rows_seen: set[int] = set()
        self.first_test_active = False
        self.first_test_fine_sent = False
        self.first_test_center = 5805

        root = ttk.Frame(self, padding=14)
        root.pack(fill="both", expand=True)

        header = ttk.Frame(root)
        header.pack(fill="x")
        ttk.Label(header, text="C5VRX", font=("Segoe UI", 25, "bold")).pack(side="left")
        ttk.Label(header, text="ESP32-C5 analog FPV receiver & first-hardware console").pack(side="left", padx=12, pady=(8, 0))
        ttk.Label(
            root,
            text=(f"Firmware: {self.firmware_profile['display_name']}  •  "
                  f"AV: {self.firmware_profile['av_pin_summary']}  •  "
                  f"GUI: {APP_BUILD}"),
            foreground="#2457a6",
        ).pack(anchor="w", pady=(4, 0))

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

        export_row = ttk.Frame(root)
        export_row.pack(fill="x", pady=(8, 0))
        ttk.Button(
            export_row,
            text="EXPORT CODEX BUNDLE",
            command=self.export_codex_bundle,
        ).pack(side="left")
        ttk.Label(
            export_row,
            text=f"Session: {self.session.path}",
        ).pack(side="left", padx=10)

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
            text=(f"Board-specific {self.firmware_profile['flash_size_mb']} MB firmware. "
                  "Band/channel changes happen live over USB after flashing."),
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
        self.channel_var = tk.StringVar(value="1")
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
                "Displays only the 160×120 grayscale video-proof raster. "
                "The old blue WBFM diagnostic graph is disabled in this build."
            ),
            wraplength=760,
        ).pack(anchor="w", pady=(4, 10))

        self.preview_canvas = tk.Canvas(tab, height=280, background="black", highlightthickness=1)
        self.preview_canvas.pack(fill="both", expand=True)
        self.preview_canvas.bind("<Configure>", self._redraw_preview)

        self.preview_status_var = tk.StringVar(
            value=f"{APP_BUILD}: waiting for video-proof capture")
        ttk.Label(tab, textvariable=self.preview_status_var).pack(anchor="w", pady=(8, 0))

        preview_controls = ttk.Frame(tab)
        preview_controls.pack(fill="x", pady=(8, 0))
        ttk.Button(preview_controls, text="Capture 16K IQ", command=self.capture_iq_16k).pack(side="left")
        ttk.Button(preview_controls, text="Start live preview", command=self.start_usb_preview).pack(side="left", padx=8)
        ttk.Button(preview_controls, text="Stop live preview", command=lambda: self.send_command("USB PREVIEW STOP")).pack(side="left")
        ttk.Button(
            preview_controls,
            text="Measure CVBS lock (5 s)",
            command=lambda: self.send_command("CVBS LOCK PROBE 5000"),
        ).pack(side="left", padx=8)
        ttk.Button(preview_controls, text="Clear", command=self.clear_preview).pack(side="left", padx=8)

        iq_live_controls = ttk.Frame(tab)
        iq_live_controls.pack(fill="x", pady=(8, 0))
        self.live_iq_start_btn = ttk.Button(
            iq_live_controls,
            text="ULTRA-SLOW VIDEO PROOF (A1)",
            command=self.start_live_iq_video,
        )
        self.live_iq_start_btn.pack(side="left")
        self.live_iq_stop_btn = ttk.Button(
            iq_live_controls,
            text="STOP IQ VIDEO",
            command=self.stop_live_iq_video,
            state="disabled",
        )
        self.live_iq_stop_btn.pack(side="left", padx=8)
        ttk.Label(iq_live_controls, text="Raster clock").pack(side="left", padx=(8, 3))
        self.video_sample_rate_var = tk.StringVar(value="40")
        ttk.Combobox(
            iq_live_controls,
            textvariable=self.video_sample_rate_var,
            state="readonly",
            values=["20", "40", "80"],
            width=5,
        ).pack(side="left")
        ttk.Label(
            iq_live_controls,
            text="MS/s hypothesis; slowly fills a PAL raster from retriggered strips.",
        ).pack(side="left", padx=8)

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
        self.profile_mismatch_warned = False
        candidate: serial.Serial | None = None
        try:
            # Configure native USB-Serial/JTAG control lines before opening.
            # serial.Serial(port, ...) opens first with the pyserial defaults;
            # on ESP32-C5 that DTR/RTS transition can reset the device and make
            # the just-opened Windows handle stale before the first PING.
            candidate = serial.Serial()
            candidate.port = port
            candidate.baudrate = 115200
            candidate.timeout = 0.15
            candidate.write_timeout = 1
            candidate.dtr = False
            candidate.rts = False
            candidate.open()
            self.ser = candidate
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
            if candidate is not None:
                try:
                    candidate.close()
                except Exception:
                    pass
            self.ser = None
            self.connection_var.set("Disconnected")
            self.connect_btn.configure(text="Connect")
            if show_errors:
                messagebox.showerror(APP_TITLE, f"Could not open {port}:\n\n{exc}")
            return False

    def disconnect_serial(self) -> None:
        self.serial_stop.set()
        self.live_iq_active = False
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
                self.session.record_raw(raw)
                for kind, value in decoder.feed(raw):
                    if kind == "line":
                        line = str(value)
                        self.session.record_line(line)
                        self.sink.write(line + "\n")
                        self._parse_device_line(line)
                    elif kind == "packet":
                        assert isinstance(value, Packet)
                        self.session.record_packet(value)
                        self._handle_usb_packet(value)
                    else:
                        self.session.record_error("USB_PROTOCOL", str(value))
                        self.sink.write(
                            f"C5VRX_PREVIEW_RESYNC reason={value}\n")
        except Exception as exc:
            if not self.serial_stop.is_set():
                self.session.record_error("SERIAL_READER", str(exc))
                self.sink.write(f"\nSerial reader stopped: {exc}\n")
                self.after(0, self._serial_lost)

    def _serial_lost(self) -> None:
        self.disconnect_serial()

    def _parse_device_line(self, line: str) -> None:
        if (line.startswith("C5VRX_USB_PREVIEW state=START") and
                self.live_iq_active):
            fields = self._fields(line)
            if fields.get("code") == "0":
                if not self.live_iq_transport_ready:
                    self.live_iq_transport_ready = True
                    self.after(0, self.preview_status_var.set,
                               "Binary IQ transport ready; requesting fresh A1 capture")
                    self.after(1, self._request_live_iq_capture)
            else:
                self.after(0, self.stop_live_iq_video,
                           f"binary transport failed ({line})")
            return
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
                        fine_command = f"FINE TUNE VERIFY {center} {tone} {rate}"
                        self.session.record_command(fine_command)
                        self.ser.write((fine_command + "\n").encode("ascii"))
                        self.ser.flush()
                        self.sink.write(f"> {fine_command}\n")
                    except Exception as exc:
                        self.sink.write(f"C5VRX_FINE_TUNE_AUTOMATION_FAILED error={exc}\n")
        if line.startswith("C5VRX_IQ_BEGIN"):
            self.iq_words = []
            self.iq_capture_active = True
            self.after(0, self.preview_status_var.set, "Receiving IQ samples over USB-C...")
            return
        if line.startswith("C5VRX_CAPTURE_KERNEL"):
            fields = self._fields(line)
            try:
                if fields.get("done") == "1":
                    self.live_iq_source_msps = float(fields["finite_fill_msps"])
            except (KeyError, ValueError):
                pass
        if line.startswith("C5VRX_CAPTURE_DONE") and self.live_iq_active:
            self.live_iq_capture_done = "code=0" in line
            if not self.live_iq_capture_done:
                fields = self._fields(line)
                if (fields.get("code") == "263" and
                        self.live_iq_capture_retries < self.live_iq_max_retries):
                    self.live_iq_capture_retries += 1
                    retry = self.live_iq_capture_retries
                    self.after(
                        0, self.preview_status_var.set,
                        f"RF capture timeout; retry {retry}/{self.live_iq_max_retries}")
                    self.after(100, self._request_live_iq_capture)
                else:
                    self.after(0, self.stop_live_iq_video,
                               f"device capture failed after {self.live_iq_capture_retries} retries")
            else:
                self.live_iq_capture_retries = 0
                self._live_iq_maybe_continue()
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
        if packet.packet_type in {PACKET_IQ_U32_BLOCK, PACKET_IQ_U32_CHUNK}:
            self._handle_iq_usb_packet(packet)
            return
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

    def _handle_iq_usb_packet(self, packet: Packet) -> None:
        self.live_iq_usb_bytes += 32 + len(packet.payload) + 4
        if packet.packet_type == PACKET_IQ_U32_BLOCK:
            try:
                words = decode_iq_block(packet)
            except ValueError as exc:
                self.sink.write(f"C5VRX_IQ_PACKET_DROP reason={exc}\n")
                return
            self._finish_iq_usb_block(words, packet.timestamp_us)
            return

        try:
            chunk = decode_iq_chunk(packet)
        except ValueError as exc:
            self.sink.write(f"C5VRX_IQ_CHUNK_DROP reason={exc}\n")
            return
        if self.live_iq_capture_id != chunk.capture_id:
            self.live_iq_capture_id = chunk.capture_id
            self.live_iq_capture_timestamp_us = packet.timestamp_us
            self.live_iq_total_words = chunk.total_words
            self.live_iq_chunks = {}
        if chunk.total_words != self.live_iq_total_words:
            self.sink.write("C5VRX_IQ_CHUNK_DROP reason=MIXED_TOTAL\n")
            return
        existing = self.live_iq_chunks.get(chunk.offset_words)
        if existing is not None and existing != chunk.words:
            self.sink.write("C5VRX_IQ_CHUNK_DROP reason=CONFLICTING_DUPLICATE\n")
            return
        self.live_iq_chunks[chunk.offset_words] = chunk.words

        if sum(len(words) for words in self.live_iq_chunks.values()) != chunk.total_words:
            return
        assembled: list[int] = []
        expected_offset = 0
        for offset in sorted(self.live_iq_chunks):
            if offset != expected_offset:
                return
            words = self.live_iq_chunks[offset]
            assembled.extend(words)
            expected_offset += len(words)
        if expected_offset == chunk.total_words:
            self._finish_iq_usb_block(
                assembled, self.live_iq_capture_timestamp_us)

    def _finish_iq_usb_block(
            self, words: list[int], timestamp_us: int | None = None) -> None:
        self.iq_words = words
        self.iq_capture_active = False
        self.live_iq_packet_done = True
        if self.live_iq_active:
            self.live_iq_blocks += 1
            self._process_live_iq_block(words, timestamp_us)
            self._live_iq_maybe_continue()
        else:
            self.after(0, self.preview_status_var.set,
                       f"Captured {len(words)} CRC-valid IQ words")
            self.after(0, self.render_iq_preview)

    def _apply_status_line(self, line: str) -> None:
        fields = self._fields(line)
        device_profile = fields.get("profile")
        profile_error = None
        if (line.startswith("C5VRX_STATUS") and not device_profile and
                self.expected_profile != "legacy-unknown"):
            profile_error = "device did not report a board profile"
        elif (line.startswith("C5VRX_STATUS") and device_profile and
              self.expected_profile not in {"legacy-unknown", device_profile}):
            profile_error = f"device reports {device_profile}"

        if profile_error:
            self.connection_var.set(
                f"PROFILE MISMATCH: console={self.expected_profile}; {profile_error}")
            if not self.profile_mismatch_warned:
                self.profile_mismatch_warned = True
                messagebox.showwarning(
                    APP_TITLE,
                    "The connected firmware uses a different board profile.\n\n"
                    f"Console bundle: {self.expected_profile}\n"
                    f"Device: {profile_error}\n\n"
                    "Do not connect the AV resistor network until the firmware "
                    "and physical board mapping match.",
                )
        elif line.startswith("C5VRX_STATUS") and device_profile:
            self.connection_var.set(
                f"Connected: {self.selected_port()} — profile verified: {device_profile}")
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
            self.session.record_command(command.strip())
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
        self.session.next_iq_label("receiver-console-capture")
        self.send_command(f"CAPTURE {int(self.samples_var.get())}")

    def capture_iq_16k(self) -> None:
        self.samples_var.set("16384")
        self.capture_iq()

    def start_usb_preview(self) -> None:
        self.send_command("USB PREVIEW START")
        self.after(100, lambda: self.send_command("LIVE START"))

    def start_live_iq_video(self) -> None:
        if not self.ser or not self.ser.is_open:
            messagebox.showwarning(APP_TITLE, "Connect to C5VRX first.")
            return
        self.live_iq_active = True
        self.live_iq_capture_done = False
        self.live_iq_packet_done = False
        self.live_iq_capture_id = None
        self.live_iq_capture_timestamp_us = None
        self.live_iq_chunks = {}
        self.live_iq_source_msps = None
        self.live_iq_transport_ready = False
        self.live_iq_capture_retries = 0
        self.live_iq_usb_started = time.monotonic()
        self.live_iq_usb_bytes = 0
        self.live_iq_blocks = 0
        self.live_video_host_frames = 0
        self.live_video_pixels[:] = bytes(len(self.live_video_pixels))
        self.live_video_row = 0
        self.live_video_rows_seen.clear()
        self.live_iq_start_btn.configure(state="disabled")
        self.live_iq_stop_btn.configure(state="normal")
        # Replace any previous diagnostic drawing immediately. This also gives
        # an unmistakable visual indication that the video-only GUI build is
        # running before the first RF block arrives.
        self._show_gray_frame(
            bytes(self.live_video_pixels),
            self.live_video_width,
            self.live_video_height,
        )
        self.preview_status_var.set(
            f"{APP_BUILD}: starting A1 IQ -> PAL raster; VTX must be A1 (5865 MHz)")
        self.send_command("BW 40")
        self.after(100, lambda: self.send_command("SET A 1"))
        self.after(220, lambda: self.send_command("USB PREVIEW START"))

    def stop_live_iq_video(self, reason: str = "user") -> None:
        was_active = self.live_iq_active
        self.live_iq_active = False
        self.live_iq_transport_ready = False
        if hasattr(self, "live_iq_start_btn"):
            self.live_iq_start_btn.configure(state="normal")
            self.live_iq_stop_btn.configure(state="disabled")
        if was_active and self.ser and self.ser.is_open:
            self.send_command("USB PREVIEW STOP")
        self.preview_status_var.set(f"IQ video stopped: {reason}")

    def _request_live_iq_capture(self) -> None:
        if not self.live_iq_active or not self.live_iq_transport_ready:
            return
        self.live_iq_capture_done = False
        self.live_iq_packet_done = False
        self.live_iq_capture_id = None
        self.live_iq_capture_timestamp_us = None
        self.live_iq_total_words = 0
        self.live_iq_chunks = {}
        self.live_iq_request_started = time.monotonic()
        self.send_command("CAPTURE 16384")

    def _live_iq_maybe_continue(self) -> None:
        if (self.live_iq_active and self.live_iq_capture_done and
                self.live_iq_packet_done):
            self.after(1, self._request_live_iq_capture)

    @staticmethod
    def _fm_discriminator(words: list[int]) -> list[float]:
        if len(words) < 2:
            return []
        decoded = [C5VRXApp._decode_iq(raw) for raw in words]
        # The recovered dump has a sizeable, block-dependent I/Q DC offset.
        # Removing it before phase differencing prevents the carrier circle
        # from orbiting an artificial origin and makes CVBS sync plateaus much
        # easier to detect.
        mean_i = statistics.fmean(value[0] for value in decoded)
        mean_q = statistics.fmean(value[1] for value in decoded)
        values: list[float] = []
        pi = decoded[0][0] - mean_i
        pq = decoded[0][1] - mean_q
        for raw_i, raw_q in decoded[1:]:
            ci = raw_i - mean_i
            cq = raw_q - mean_q
            values.append(math.atan2(cq * pi - ci * pq,
                                     ci * pi + cq * pq))
            pi, pq = ci, cq
        return values

    def _process_live_iq_block(
            self, words: list[int], timestamp_us: int | None = None) -> None:
        fm = self._fm_discriminator(words)
        if len(fm) < 512:
            return

        # Work in 16-sample bins. The vendor call duration is not the physical
        # sample clock, so the user-selectable RF-raster hypothesis below is
        # deliberately independent from finite_fill_msps.
        bin_size = 16
        binned = [
            statistics.fmean(fm[offset:offset + bin_size])
            for offset in range(0, len(fm) - bin_size + 1, bin_size)
        ]
        raster_msps = float(self.video_sample_rate_var.get())
        period = raster_msps * 1_000_000.0 / 15_625.0
        period_bins = max(8, int(round(period / bin_size)))
        sync_bins = max(2, int(round(period_bins * 0.073)))

        # Fold all complete line periods in this finite block. CVBS horizontal
        # sync is the most repeatable ~7.3% plateau in a PAL line. Test both FM
        # polarities because the dump tap's spectral inversion is not known.
        phase_means: list[float] = []
        for phase in range(period_bins):
            samples: list[float] = []
            cursor = phase
            while cursor + sync_bins <= len(binned):
                samples.extend(binned[cursor:cursor + sync_bins])
                cursor += period_bins
            phase_means.append(
                statistics.fmean(samples) if samples else 0.0)
        baseline = statistics.fmean(binned)
        low_phase = min(range(len(phase_means)), key=phase_means.__getitem__)
        high_phase = max(range(len(phase_means)), key=phase_means.__getitem__)
        low_contrast = baseline - phase_means[low_phase]
        high_contrast = phase_means[high_phase] - baseline
        sync_phase = low_phase if low_contrast >= high_contrast else high_phase
        noise = max(1e-9, statistics.pstdev(binned))
        sync_score = max(low_contrast, high_contrast) / noise

        line_starts: list[int] = []
        cursor = sync_phase * bin_size
        while cursor - period >= 0:
            cursor -= period
        while cursor + period <= len(fm):
            line_starts.append(int(cursor))
            cursor += period

        active_lines: list[tuple[int, list[float]]] = []
        for start_sample in line_starts:
            active_begin = int(start_sample + period * 0.16)
            active_end = int(start_sample + period * 0.96)
            if active_begin < 0 or active_end > len(fm) or active_end <= active_begin:
                continue
            source = fm[active_begin:active_end]
            row: list[float] = []
            for x in range(self.live_video_width):
                left = x * len(source) // self.live_video_width
                right = max(left + 1, (x + 1) * len(source) // self.live_video_width)
                row.append(statistics.fmean(source[left:right]))
            active_lines.append((start_sample, row))

        if not active_lines:
            self.after(0, self.preview_status_var.set,
                       "IQ received; no complete video-line candidate in this block")
            return
        scale_values = sorted(
            value for _start_sample, row in active_lines for value in row)
        black = scale_values[len(scale_values) // 20]
        white = scale_values[(len(scale_values) * 19) // 20]
        span = max(1e-6, white - black)
        physical_rate_hz = raster_msps * 1_000_000.0
        for start_sample, row in active_lines:
            if timestamp_us is not None:
                line_time_us = timestamp_us - (
                    len(fm) - start_sample) * 1_000_000.0 / physical_rate_hz
                field_phase = line_time_us % 20_000.0
                target_row = int(
                    field_phase * self.live_video_height / 20_000.0)
            else:
                target_row = self.live_video_row
                self.live_video_row = (
                    self.live_video_row + 1) % self.live_video_height
            base = target_row * self.live_video_width
            for x, value in enumerate(row):
                self.live_video_pixels[base + x] = max(
                    0, min(255, int((value - black) * 255.0 / span)))
            self.live_video_rows_seen.add(target_row)

        elapsed = max(1e-6, time.monotonic() - self.live_iq_usb_started)
        usb_mbit = self.live_iq_usb_bytes * 8.0 / elapsed / 1_000_000.0
        block_rate = self.live_iq_blocks / elapsed
        source_text = (f"{self.live_iq_source_msps:.3f} MS/s finite-fill estimate"
                       if self.live_iq_source_msps else "source MS/s pending")
        coverage = 100.0 * len(self.live_video_rows_seen) / self.live_video_height
        lock_text = (
            f"H-sync fold score {sync_score:.2f}; "
            f"raster {raster_msps:.0f} MS/s; {coverage:.0f}% rows")
        self.after(0, self._show_live_iq_frame, bytes(self.live_video_pixels),
                   source_text, usb_mbit, block_rate, lock_text)

    def _show_live_iq_frame(self, pixels: bytes, source_text: str,
                            usb_mbit: float, block_rate: float,
                            lock_text: str) -> None:
        self._show_gray_frame(
            pixels, self.live_video_width, self.live_video_height)
        self.live_video_host_frames += 1
        if self.live_video_host_frames == 1 or self.live_video_host_frames % 10 == 0:
            self.sink.write(
                f"C5VRX_HOST_VIDEO_FRAME build={APP_BUILD} "
                f"frame={self.live_video_host_frames} {lock_text}\n")
        self.preview_status_var.set(
            f"{APP_BUILD} | A1 IQ->PC PAL raster | {source_text} | USB {usb_mbit:.2f} Mbit/s | "
            f"{block_rate:.2f} blocks/s | {lock_text} | RETRIGGERED, NOT GAPLESS")

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
            for command in commands:
                self.session.record_command(command)
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
        if self.preview_image is not None:
            self._redraw_preview()
            return
        canvas.delete("all")
        width = max(40, canvas.winfo_width())
        height = max(80, canvas.winfo_height())
        canvas.create_text(
            width / 2,
            height / 2,
            text=(f"{APP_BUILD}\n\nNo waveform display in this build.\n"
                  "Press ULTRA-SLOW VIDEO PROOF (A1)."),
            fill="white",
            justify="center",
        )

    def _redraw_preview(self, _event: object | None = None) -> None:
        """Keep decoded video visible when the preview canvas is resized."""
        if self.preview_image is None:
            if not self.live_iq_active:
                self.render_iq_preview()
            return
        canvas = self.preview_canvas
        canvas.delete("all")
        canvas.create_image(
            max(0, canvas.winfo_width() // 2),
            max(0, canvas.winfo_height() // 2),
            image=self.preview_image,
            anchor="center",
        )

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
        self._redraw_preview()
        self.preview_status_var.set(f"Live USB preview: {width}×{height} GRAY8, CRC valid")

    def clear_preview(self) -> None:
        self.iq_words = []
        self.preview_frame = None
        self.preview_image = None
        self.preview_status_var.set(f"{APP_BUILD}: video raster cleared")
        self.render_iq_preview()

    def export_codex_bundle(self) -> None:
        self.session.update_test_config(
            port=self.selected_port(),
            band=self.band_var.get(),
            channel=int(self.channel_var.get()),
            bandwidth_mhz=int(self.bw_var.get()),
            finite_iq_samples=int(self.samples_var.get()),
        )
        suggested = self.session.path.name + "-codex-bundle.zip"
        selected = filedialog.asksaveasfilename(
            title="Export C5VRX Codex bundle",
            defaultextension=".zip",
            initialfile=suggested,
            filetypes=[("ZIP archive", "*.zip")],
        )
        if not selected:
            return
        try:
            bundle = self.session.create_bundle(Path(selected))
            self.sink.write(f"\n=== CODEX BUNDLE EXPORTED: {bundle} ===\n")
            messagebox.showinfo(APP_TITLE, f"Codex bundle exported:\n\n{bundle}")
        except Exception as exc:
            self.session.record_error("BUNDLE_EXPORT", str(exc))
            messagebox.showerror(APP_TITLE, f"Could not export Codex bundle:\n\n{exc}")

    def flash(self) -> None:
        if self._busy:
            return
        port = self.selected_port()
        if not port:
            messagebox.showerror(APP_TITLE, "No USB/COM port selected.")
            return
        if not messagebox.askokcancel(
            APP_TITLE,
            f"Flash {self.firmware_profile['display_name']} firmware to {port}?\n\n"
            f"AV mapping: {self.firmware_profile['av_pin_summary']}\n"
            f"Expected flash: {self.firmware_profile['flash_size_mb']} MB\n\n"
            "Use this image only on the named board/profile.",
        ):
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
        self.session.finalize({
            "status": "CONSOLE_SESSION_COMPLETE",
            "passed": None,
            "reason": "interactive Receiver Console session",
        })
        self.session.close()
        self.destroy()


if __name__ == "__main__":
    C5VRXApp().mainloop()

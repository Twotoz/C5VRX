#!/usr/bin/env python3
"""Desktop build-and-flash application for the C5VRX production firmware.

Run from the repository root with:
    python tools/c5vrx_gui.py

The application deliberately uses the project-pinned Docker image for builds
and delegates flashing to tools/flash.py, so its workflow matches the documented
command-line procedure.
"""

from __future__ import annotations

import queue
import shutil
import subprocess
import sys
import threading
from importlib.util import find_spec
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:
    from serial.tools import list_ports as serial_list_ports
except ImportError:  # Reported cleanly in the GUI's diagnostic output.
    serial_list_ports = None

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except ImportError:
    DND_FILES = None
    TkinterDnD = None


ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
BUILD = ROOT / "build"
IMAGE = "espressif/idf:v6.0.2"
VALIDATOR = TOOLS / "validate_build.py"
FLASHER = TOOLS / "flash.py"
APP_BINARY = BUILD / "c5vrx3.bin"
REQUIRED_ARTIFACTS = (
    BUILD / "bootloader" / "bootloader.bin",
    BUILD / "partition_table" / "partition-table.bin",
    APP_BINARY,
)
WINDOW = TkinterDnD.Tk if TkinterDnD is not None else tk.Tk


def console_python() -> str:
    """Use python.exe for console tools when this GUI runs under pythonw.exe."""
    executable = Path(sys.executable)
    if executable.name.lower() == "pythonw.exe":
        console_executable = executable.with_name("python.exe")
        if console_executable.exists():
            return str(console_executable)
    return sys.executable


class C5VRXGui(WINDOW):
    """Small, dependency-free UI around the documented C5VRX workflow."""

    def __init__(self) -> None:
        super().__init__()
        self.title("C5VRX Firmware Builder & Flasher")
        self.minsize(880, 620)
        self.geometry("980x720")

        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.busy = False
        self.port_lookup: dict[str, str] = {}
        self.port_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Ready")
        self.artifact_var = tk.StringVar()
        self.external_bin_var = tk.StringVar(value="No external app binary selected")
        self.flash_source_var = tk.StringVar(value="built")
        self.external_binary: Path | None = None
        self._build_ui()
        self.refresh_ports()
        self.refresh_artifacts()
        if TkinterDnD is None:
            self.write(
                "Drag-and-drop support is unavailable. Start this tool through "
                "tools\\Launch C5VRX GUI.bat to install its bundled GUI dependency.\n"
            )
        self.after(75, self.process_events)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self, padding=14)
        outer.grid(sticky="nsew")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(3, weight=1)

        heading = ttk.Label(
            outer,
            text="C5VRX Firmware Builder & Flasher",
            font=("Segoe UI", 16, "bold"),
        )
        heading.grid(row=0, column=0, sticky="w")
        ttk.Label(
            outer,
            text=f"Project: {ROOT}   •   Docker image: {IMAGE}",
        ).grid(row=1, column=0, sticky="w", pady=(2, 12))

        actions = ttk.LabelFrame(outer, text="Build and flash", padding=10)
        actions.grid(row=2, column=0, sticky="ew")
        actions.columnconfigure(3, weight=1)

        self.build_button = ttk.Button(
            actions, text="Build + Validate", command=self.start_build
        )
        self.build_button.grid(row=0, column=0, padx=(0, 8), pady=(0, 8))
        self.docker_button = ttk.Button(
            actions, text="Check Docker", command=self.start_docker_check
        )
        self.docker_button.grid(row=0, column=1, padx=(0, 16), pady=(0, 8))

        ttk.Label(actions, text="ESP32-C5 port:").grid(
            row=1, column=0, sticky="w"
        )
        self.port_box = ttk.Combobox(
            actions, textvariable=self.port_var, state="readonly", width=54
        )
        self.port_box.grid(row=1, column=1, columnspan=2, sticky="ew", padx=(8, 8))
        self.refresh_button = ttk.Button(
            actions, text="Refresh ports", command=self.refresh_ports
        )
        self.refresh_button.grid(row=1, column=3, sticky="w")
        self.flash_button = ttk.Button(
            actions, text="Flash selected port", command=self.start_flash
        )
        self.flash_button.grid(row=2, column=0, pady=(10, 0), sticky="w")
        self.open_build_button = ttk.Button(
            actions, text="Open build folder", command=self.open_build_folder
        )
        self.open_build_button.grid(row=2, column=1, pady=(10, 0), sticky="w")
        ttk.Label(actions, textvariable=self.artifact_var).grid(
            row=2, column=2, columnspan=2, pady=(10, 0), sticky="w"
        )

        ttk.Separator(actions).grid(row=3, column=0, columnspan=4, sticky="ew", pady=12)
        ttk.Radiobutton(
            actions,
            text="Flash current validated build (bootloader + partitions + app)",
            variable=self.flash_source_var,
            value="built",
            command=self.refresh_artifacts,
        ).grid(row=4, column=0, columnspan=4, sticky="w")
        ttk.Radiobutton(
            actions,
            text="Flash collaborator app .bin only at 0x10000 (preserves bootloader + partitions)",
            variable=self.flash_source_var,
            value="external",
            command=self.refresh_artifacts,
        ).grid(row=5, column=0, columnspan=4, sticky="w", pady=(5, 0))

        self.drop_zone = ttk.Label(
            actions,
            text="Drop a collaborator .bin here, or choose one",
            anchor="center",
            relief="groove",
            padding=(8, 10),
        )
        self.drop_zone.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Button(actions, text="Choose .bin…", command=self.choose_external_binary).grid(
            row=6, column=2, sticky="ew", padx=(8, 8), pady=(8, 0)
        )
        ttk.Label(actions, textvariable=self.external_bin_var).grid(
            row=6, column=3, sticky="w", pady=(8, 0)
        )
        if TkinterDnD is not None:
            self.drop_zone.drop_target_register(DND_FILES)
            self.drop_zone.dnd_bind("<<Drop>>", self.drop_external_binary)

        console_frame = ttk.LabelFrame(outer, text="Activity log", padding=6)
        console_frame.grid(row=3, column=0, sticky="nsew", pady=(12, 10))
        console_frame.columnconfigure(0, weight=1)
        console_frame.rowconfigure(0, weight=1)
        self.console = tk.Text(
            console_frame,
            wrap="word",
            font=("Cascadia Mono", 9),
            state="disabled",
        )
        scrollbar = ttk.Scrollbar(
            console_frame, orient="vertical", command=self.console.yview
        )
        self.console.configure(yscrollcommand=scrollbar.set)
        self.console.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        footer = ttk.Frame(outer)
        footer.grid(row=4, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=self.status_var).grid(row=0, column=0, sticky="w")
        ttk.Button(footer, text="Clear log", command=self.clear_log).grid(row=0, column=1)

    def write(self, text: str) -> None:
        self.console.configure(state="normal")
        self.console.insert("end", text)
        self.console.see("end")
        self.console.configure(state="disabled")

    def clear_log(self) -> None:
        self.console.configure(state="normal")
        self.console.delete("1.0", "end")
        self.console.configure(state="disabled")

    @staticmethod
    def is_esp_port(port: object) -> bool:
        description = str(getattr(port, "description", "")).upper()
        hwid = str(getattr(port, "hwid", "")).upper()
        return "303A" in hwid or "ESPRESSIF" in description or "USB JTAG" in description

    def refresh_ports(self) -> None:
        if serial_list_ports is None:
            self.port_lookup = {}
            self.port_box["values"] = []
            self.status_var.set("pyserial is missing; install it with: python -m pip install pyserial")
            return

        ports = list(serial_list_ports.comports())
        ports.sort(key=lambda item: (not self.is_esp_port(item), item.device))
        self.port_lookup = {
            f"{port.device} — {port.description or 'Unknown serial device'}": port.device
            for port in ports
        }
        labels = list(self.port_lookup)
        self.port_box["values"] = labels
        if labels:
            esp_label = next(
                (label for label, port in zip(labels, ports) if self.is_esp_port(port)),
                labels[0],
            )
            if self.port_var.get() not in self.port_lookup:
                self.port_var.set(esp_label)
            self.status_var.set(f"Found {len(labels)} serial device(s).")
        else:
            self.port_var.set("")
            self.status_var.set("No serial devices found. Connect the XIAO ESP32-C5 and refresh.")

    def refresh_artifacts(self) -> None:
        built_ready = all(path.is_file() for path in REQUIRED_ARTIFACTS)
        external_ready = self.external_binary is not None and self.external_binary.is_file()
        flash_ready = built_ready if self.flash_source_var.get() == "built" else external_ready
        self.flash_button.configure(state="normal" if flash_ready and not self.busy else "disabled")
        if built_ready:
            built = datetime.fromtimestamp(APP_BINARY.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            size_kib = APP_BINARY.stat().st_size / 1024
            self.artifact_var.set(f"{APP_BINARY} ({size_kib:.0f} KiB, built {built})")
        else:
            self.artifact_var.set("No complete firmware build found. Build before flashing.")

    def open_build_folder(self) -> None:
        if not BUILD.is_dir():
            messagebox.showerror("Build folder missing", "Build the firmware before opening its output folder.")
            return
        try:
            subprocess.Popen(["explorer", str(BUILD)])
        except OSError as error:
            messagebox.showerror("Could not open build folder", str(error))

    def choose_external_binary(self) -> None:
        selected = filedialog.askopenfilename(
            title="Choose a C5VRX application binary",
            initialdir=ROOT,
            filetypes=(("Firmware binary", "*.bin"), ("All files", "*.*")),
        )
        if selected:
            self.set_external_binary(Path(selected))

    def drop_external_binary(self, event) -> None:
        paths = [Path(item) for item in self.tk.splitlist(event.data)]
        if len(paths) != 1:
            messagebox.showerror("Drop one file", "Drop exactly one C5VRX application .bin file.")
            return
        self.set_external_binary(paths[0])

    def set_external_binary(self, path: Path) -> None:
        if not path.is_file() or path.suffix.lower() != ".bin":
            messagebox.showerror(
                "Invalid firmware file",
                "Choose a single existing .bin application image.",
            )
            return
        self.external_binary = path.resolve()
        size_kib = self.external_binary.stat().st_size / 1024
        self.external_bin_var.set(f"{self.external_binary.name} ({size_kib:.0f} KiB)")
        self.flash_source_var.set("external")
        self.refresh_artifacts()
        self.status_var.set("External app binary selected. It will be written only at 0x10000.")

    def set_busy(self, busy: bool, message: str) -> None:
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.build_button.configure(state=state)
        self.docker_button.configure(state=state)
        self.refresh_button.configure(state=state)
        self.port_box.configure(state="disabled" if busy else "readonly")
        self.status_var.set(message)
        self.refresh_artifacts()

    def command_label(self, command: list[str]) -> str:
        return subprocess.list2cmdline(command)

    def run_command(self, command: list[str], label: str, on_success) -> None:
        def worker() -> None:
            self.events.put(("log", f"\n=== {label} ===\n$ {self.command_label(command)}\n"))
            try:
                with subprocess.Popen(
                    command,
                    cwd=ROOT,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                ) as process:
                    assert process.stdout is not None
                    for line in process.stdout:
                        self.events.put(("log", line))
                    exit_code = process.wait()
            except FileNotFoundError:
                self.events.put(("finished", (False, f"{command[0]} was not found on PATH.")))
                return
            except OSError as error:
                self.events.put(("finished", (False, str(error))))
                return

            if exit_code == 0:
                self.events.put(("success", on_success))
            else:
                self.events.put(("finished", (False, f"{label} failed with exit code {exit_code}.")))

        threading.Thread(target=worker, daemon=True).start()

    def start_docker_check(self) -> None:
        self.set_busy(True, "Checking Docker...")
        self.run_command(
            ["docker", "version", "--format", "Client {{.Client.Version}} | Server {{.Server.Version}}"],
            "Docker check",
            self.docker_check_complete,
        )

    def docker_check_complete(self) -> None:
        self.write("Docker is ready.\n")
        self.set_busy(False, "Docker is ready.")

    def start_build(self) -> None:
        if shutil.which("docker") is None:
            messagebox.showerror(
                "Docker not found",
                "Docker Desktop must be installed and running before building.",
            )
            return
        self.set_busy(True, "Building firmware with Docker...")
        docker_mount = f"{ROOT}:/workspace"
        self.run_command(
            [
                "docker", "run", "--rm", "-v", docker_mount, "-w", "/workspace",
                IMAGE, "idf.py", "build",
            ],
            "Firmware build",
            self.start_validation,
        )

    def start_validation(self) -> None:
        self.write("\nBuild completed; running architectural validation.\n")
        self.status_var.set("Validating build constraints...")
        self.run_command(
            [sys.executable, str(VALIDATOR)],
            "Architecture validation",
        self.build_complete,
        )

    def build_complete(self) -> None:
        self.refresh_artifacts()
        self.set_busy(False, "Build and validation completed successfully.")
        self.write(
            "\n=== SUCCESS: firmware is built and validated ===\n"
            f"Firmware: {APP_BINARY}\n"
            f"Build folder: {BUILD}\n"
        )
        messagebox.showinfo(
            "Build complete",
            "Firmware built successfully and all architectural validation checks passed.\n\n"
            f"Firmware:\n{APP_BINARY}\n\n"
            f"Build folder:\n{BUILD}",
        )

    def start_flash(self) -> None:
        selected = self.port_var.get()
        port = self.port_lookup.get(selected)
        if not port:
            messagebox.showerror("No port selected", "Select the ESP32-C5 serial port, then try again.")
            return
        if find_spec("esptool") is None:
            messagebox.showerror(
                "esptool is missing",
                "The flashing dependency is not installed for this Python version. "
                "Close the GUI and start it again through tools\\Launch C5VRX GUI.bat.",
            )
            return
        external_binary = self.external_binary if self.flash_source_var.get() == "external" else None
        if external_binary is not None:
            if not external_binary.is_file():
                messagebox.showerror("Firmware file missing", f"The selected file no longer exists:\n{external_binary}")
                return
            confirmation = (
                f"Flash {external_binary.name} to {port}?\n\n"
                "It will be written as an application image at 0x10000. The board's bootloader "
                "and partition table will be preserved.\n\n"
                "Only use an application .bin built for this ESP32-C5 C5VRX board."
            )
        else:
            confirmation = (
                f"Flash the validated build to {port}?\n\n"
                "This overwrites the board's bootloader, partition table, and application firmware."
            )
        if not messagebox.askyesno("Flash C5VRX firmware", confirmation, icon="warning"):
            return
        self.set_busy(True, f"Flashing firmware to {port}...")
        if external_binary is not None:
            self.run_command(
                [
                    console_python(), "-m", "esptool", "--chip", "esp32c5", "-p", port,
                    "-b", "460800", "--before", "usb-reset", "--after", "watchdog-reset",
                    "write-flash", "--flash-mode", "dio", "--flash-size", "8MB",
                    "--flash-freq", "80m", "0x10000", str(external_binary),
                ],
                f"Flash external application to {port}",
                lambda: self.flash_complete(port),
            )
        else:
            self.run_command(
                [console_python(), str(FLASHER), port],
                f"Flash full validated build to {port}",
                lambda: self.flash_complete(port),
            )

    def flash_complete(self, port: str) -> None:
        self.set_busy(False, f"Firmware flashed successfully to {port}.")
        self.write(f"\n=== SUCCESS: firmware flashed to {port} ===\n")
        messagebox.showinfo("Flash complete", f"C5VRX firmware was flashed successfully to {port}.")

    def process_events(self) -> None:
        try:
            while True:
                event, value = self.events.get_nowait()
                if event == "log":
                    self.write(str(value))
                elif event == "success":
                    value()
                elif event == "finished":
                    _, message = value
                    self.set_busy(False, str(message))
                    self.write(f"\n=== ERROR: {message} ===\n")
                    messagebox.showerror("Operation failed", str(message))
        except queue.Empty:
            pass
        self.after(75, self.process_events)


def main() -> None:
    if "--check" in sys.argv:
        print(f"Project root: {ROOT}")
        print(f"Docker command: {'found' if shutil.which('docker') else 'not found'}")
        print(f"pyserial: {'found' if serial_list_ports is not None else 'not found'}")
        print(f"esptool: {'found' if find_spec('esptool') is not None else 'not found'}")
        print(f"drag-and-drop: {'found' if TkinterDnD is not None else 'not found'}")
        sys.exit(
            0
            if (
                shutil.which("docker")
                and serial_list_ports is not None
                and find_spec("esptool") is not None
                and TkinterDnD is not None
            )
            else 1
        )
    C5VRXGui().mainloop()


if __name__ == "__main__":
    main()

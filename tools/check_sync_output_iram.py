"""C5VRX/Twotoz: reject direct flash calls in the live sync interrupt path.

Checks the linked target, including debug optimization builds where plain
`static inline` does not guarantee inlining. Indirect callback ownership,
SDK internals and data residency still require their separate contracts.
"""
import argparse
import re
import subprocess

ROOTS = {
    "sec_alarm", "output_alarm",
    "sync_edge_clock_read", "sync_edge_clock_alarm_at", "sync_edge_clock_alarm_off",
    "transport_sync_live_valid", "transport_sync_native_source",
    "sync_output_live_source", "sop_received",
}
HELPERS = {"flag_native", "fail", "flag_pin_clamped", "sop_received_inner"}  # may be fully inlined


def check(disassembly):
    functions = {}
    current = None
    for line in disassembly.splitlines():
        header = re.match(r"^([0-9a-f]+) <([^>]+)>:$", line)
        if header:
            current = header[2]
            functions[current] = (int(header[1], 16), [])
        elif current:
            # Includes direct calls, tail calls and split-function branches.
            # Ordinary local branches retain the function's IRAM address;
            # strip objdump '# ...' comments on constant/address loads.
            code = line.split("#", 1)[0]
            for target in re.finditer(r"\b([0-9a-f]{8}) <([^>]+)>", code):
                functions[current][1].append((int(target[1], 16), target[2]))
    errors = []
    for name in sorted(ROOTS | (HELPERS & functions.keys())):
        if name not in functions:
            errors.append(f"missing interrupt-path function: {name}")
            continue
        address, calls = functions[name]
        if not 0x40800000 <= address < 0x40900000:
            errors.append(f"{name} is outside internal instruction RAM: {address:#x}")
        for target, callee in calls:
            if 0x42000000 <= target < 0x44000000:
                errors.append(f"{name} directly reaches flash: {callee} at {target:#x}")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("elf")
    parser.add_argument("--objdump", default="riscv32-esp-elf-objdump")
    args = parser.parse_args()
    result = subprocess.run([args.objdump, "-d", args.elf], check=True,
                            capture_output=True, text=True)
    errors = check(result.stdout)
    if errors:
        raise SystemExit("\n".join(errors))
    print("PASS: linked sync interrupt functions reside in IRAM; no direct flash calls")


if __name__ == "__main__":
    main()

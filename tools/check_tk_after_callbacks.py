#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Reject Tk ``after`` callbacks that incorrectly pass keyword arguments."""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GUI_SOURCES = (
    ROOT / "tools" / "C5VRX_Flasher.py",
    ROOT / "tools" / "C5VRX_LongCapture.py",
    ROOT / "tools" / "C5VRX_XIAO_Flasher.py",
)


def main() -> int:
    failures: list[str] = []
    calls_checked = 0
    for path in GUI_SOURCES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute) or node.func.attr != "after":
                continue
            calls_checked += 1
            if node.keywords:
                names = ",".join(keyword.arg or "**kwargs" for keyword in node.keywords)
                failures.append(f"{path.relative_to(ROOT)}:{node.lineno}: {names}")

    if failures:
        print("C5VRX_TK_AFTER_CHECK result=FAIL")
        for failure in failures:
            print(f"  Tk after() does not accept callback keywords: {failure}")
        return 1
    print(f"C5VRX_TK_AFTER_CHECK result=PASS calls={calls_checked}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

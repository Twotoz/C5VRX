#!/usr/bin/env python3
"""Check the Golden Phase5 pipeline after omitting its one-time prime slot.

This is a source-driven dataflow proof, not a silicon timing/EOF proof.
"""

import importlib.util
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODEL_PATH = ROOT / "legacy/c5vrx2/tools/bs_model.py"
spec = importlib.util.spec_from_file_location("bs_model", MODEL_PATH)
model = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(model)

original = (ROOT / "main/fm.bsasm").read_text()
before, rest = original.split("address_phase:\n", 1)
_, tail = rest.split("address_delta:\n", 1)
two_slot = before + "address_delta:\n" + tail
assert len(model.parse(original)[2]) == 3
assert len(model.parse(two_slot)[2]) == 2

for seed in range(256):
    rng = random.Random(seed)
    raw = bytes(rng.randrange(256) for _ in range(512))
    # The first LUT result and O state can be arbitrary at program start.
    initial = (rng.getrandbits(32), 0, 0, rng.getrandbits(16))
    gold = model.simulate(original, raw, 128, initial=initial)
    no_prime = model.simulate(two_slot, raw, 130, initial=initial)
    # The new loop needs two output pairs to establish a real previous phase.
    assert no_prime[4:] == gold[2:], seed

print("PASS: 2-slot loop matches Golden from its second real IQ pair onward")
print("Startup cost: 2 extra untrusted output pairs (100 ns), not 1")
artifact = ROOT / "build/golden_two_slot.bsasm"
artifact.write_text(two_slot)
print("Generated assembler candidate:", artifact)

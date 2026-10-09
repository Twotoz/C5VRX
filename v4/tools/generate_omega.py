#!/usr/bin/env python3
"""Compile the frozen C5VRX posterior-belief LAB; no research dependencies."""
import json
from pathlib import Path
from compile_overlay import build
from generate_range_options import identity
ROOT=Path(__file__).resolve().parents[1]

def generate():
    model=json.loads((ROOT/'tools/omega_model.json').read_text())
    words=model['lut']; digest=identity(model)
    (ROOT/'firmware/programs/c5vrx4_omega.bsasm').write_text(build(model))
    text='#pragma once\n#include <stdint.h>\n/* C5VRX by Twotoz/contributors. Generated, board acceptance pending. */\n'
    text+=f'#define OMEGA_MODEL_ID "{digest[:12]}"\n'
    text+='static const uint16_t omega_pristine[1024] = {\n'
    text+='\n'.join('    '+', '.join(str(w) for w in words[k:k+16])+',' for k in range(0,1024,16))+'\n};\n'
    (ROOT/'firmware/include/omega_table.h').write_text(text)
    print('OMEGA generated',digest[:12])
if __name__=='__main__':generate()

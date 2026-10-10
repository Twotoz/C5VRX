#!/usr/bin/env python3
"""Compile explicitly pinned C5VRX range LAB options without research packages."""
import hashlib
import json
from pathlib import Path
from compile_overlay import build, cost

ROOT = Path(__file__).resolve().parents[1]
MAX_OPTIONS = 7


def identity(model):
    p = model['params']
    words = model['lut']
    # Validate words and the actual supported schedule before hashing.
    build(model)
    payload = b''.join(v.to_bytes(2, 'little') for v in words)
    payload += bytes([p['token_bits'], p.get('context_bits', 0)])
    if p.get('counter_phase', False):
        payload += b'counter_phase'
    if p.get('pair_layout'):
        # Same LUT words with another address schedule are another model.
        payload += b'pair' + p['pair_layout'].encode()
    return hashlib.sha256(payload).hexdigest()


def render(options):
    if len(options) > MAX_OPTIONS:
        raise ValueError('at most seven explicit LAB options')
    entries, programs, seen = [], {}, set()
    for index, option in enumerate(options):
        label, model = option['label'], option['model']
        if (not isinstance(label, str) or not 1 <= len(label) <= 20 or
                any(ord(c) < 32 or ord(c) > 126 for c in label)):
            raise ValueError('short printable ASCII label required')
        digest = identity(model)
        if option['sha256'] != digest or digest in seen:
            raise ValueError('model identity mismatch or duplicate option')
        seen.add(digest)
        status = option.get('status')
        if status not in ('independent_synthetic_confirmation', 'independent_range_tradeoff'):
            raise ValueError('short proxy models cannot become selectable options')
        if status == 'independent_range_tradeoff' and not option.get('vetoes'):
            raise ValueError('experimental trade-off requires disclosed confirmation vetoes')
        p = model['params']
        resource = cost(p['token_bits'], p.get('context_bits', 0), p.get('counter_phase', False), p.get('pair_layout'))
        phases, groups = p['phases'], p['confidence_groups']
        if (type(phases) is not int or phases < 4 or phases & (phases - 1) or
                phases > resource['states'] or groups not in (1, 2, 4)):
            raise ValueError('invalid tracking/observation allocation')
        tokens = (1 << p['token_bits']) // groups
        if tokens < 4:
            raise ValueError('insufficient angular observation states')
        selectable = option.get('selectable', True)
        if type(selectable) is not bool:
            raise ValueError('explicit boolean selection gate required')
        entries.append('    {' + ', '.join([
            json.dumps(label), json.dumps(digest[:12]), str(0 if p.get('belief_fsm') else phases.bit_length() - 1),
            str(0 if p.get('belief_fsm') else phases), str(tokens), str(0 if p.get('belief_fsm') else resource['states'] // phases),
            str(int(selectable)),
        ]) + '},')
        programs[f'c5vrx4_range_option{index}.bsasm'] = build(model)
    header = ('#pragma once\n#include <stdint.h>\n'
              '/* Generated C5VRX by Twotoz/contributors. Pinned LAB; physical acceptance separate. */\n'
              f'#define C5VRX4_RANGE_OPTION_COUNT {len(options)}\n')
    if entries:
        header += ('typedef struct {\n'
                   '    const char *label, *model_id;\n'
                   '    uint8_t phase_bits;\n'
                   '    uint16_t phase_states, observation_tokens, frequency_states;\n'
                   '    uint8_t selectable;\n'
                   '} c5vrx4_range_option_t;\n'
                   'static const c5vrx4_range_option_t c5vrx4_range_options[] = {\n'
                   + '\n'.join(entries) + '\n};\n')
    return header, programs


def generate():
    manifest = ROOT / 'tools/range_options.json'
    options = json.loads(manifest.read_text(encoding='utf-8'))['options'] if manifest.exists() else []
    header, programs = render(options)
    (ROOT / 'firmware/include/range_options.h').write_text(header, encoding='utf-8')
    directory = ROOT / 'firmware/programs'
    for index in range(MAX_OPTIONS):
        path = directory / f'c5vrx4_range_option{index}.bsasm'
        if path.name in programs:
            path.write_text(programs[path.name], encoding='utf-8')
        elif path.exists():
            path.unlink()
    print(f'Generated {len(options)} pinned range LAB options; consult each option evidence/status')


if __name__ == '__main__':
    generate()

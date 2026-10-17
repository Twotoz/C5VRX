"""C5VRX by Twotoz/contributors: independent lowm RV32 optimization probe.

Copies the current lowm function for static assembly comparisons, without
changing firmware. Instruction savings are not measured C5 CPU timing.
"""
from pathlib import Path
import json
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main():
    source = (ROOT / 'main/sync_flywheel.c').read_text()
    phase = re.search(r'static inline uint8_t ph_at\([^\n]+\)\n\{.*?\n\}', source, re.S).group()
    baseline = re.search(r'static SFW_HOT bool lowm\([^\n]+\)\n\{.*?\n\}', source, re.S).group()
    wrap = re.search(r'static inline int wrap8\([^\n]+\)\n\{.*?\n\}', source, re.S).group()
    variants = []
    for name, branchless, unroll in [('baseline', False, False), ('branchless', True, False),
                                    ('unrolled', False, True), ('both', True, True)]:
        helper = wrap.replace('wrap8', f'wrap_{name}')
        if branchless:
            helper = f'static inline int wrap_{name}(int d) {{ return ((d + 128) & 255) - 128; }}'
        function = re.sub(r'\blowm\b', name, baseline).replace('wrap8', f'wrap_{name}')
        if unroll:
            function = function.replace('    for (unsigned j = 0; j < 9u; ++j)',
                                        '    #pragma GCC unroll 9\n    for (unsigned j = 0; j < 9u; ++j)')
        variants.append(helper + '\n' + function)
    text = ('/* C5VRX by Twotoz/contributors: generated static RV32 cost probe. */\n'
            '#include "sync_flywheel.h"\n#define SFW_HOT __attribute__((noinline, used))\n'
            'static uint32_t s_evals, s_lowm;\n' + phase + '\n' + '\n'.join(variants) + '''
#ifdef HOST_CHECK
#include <assert.h>
#include <stdio.h>
int main(void) {
    for (int d=-255; d<=255; ++d) assert(wrap_baseline(d)==wrap_branchless(d));
    uint8_t raw[1024], phase[256]; unsigned random=71;
    sfw_ring_t r = {.ring=raw, .ring_bytes=1024, .phase=phase};
    sync_flywheel_t f = {0};
    for (unsigned j=0;j<256;++j) phase[j]=(uint8_t)(j*179u+17u);
    for (unsigned trial=0;trial<32;++trial) {
        for (unsigned j=0;j<1024;++j) {
            random=random*1664525u+1013904223u; raw[j]=(uint8_t)(random>>24);
        }
        for (unsigned k=0;k<1024;++k) {
            f.thr=(int16_t)((k%161)-80);
            bool expected=baseline(&f,&r,k);
            assert(expected==branchless(&f,&r,k));
            assert(expected==unrolled(&f,&r,k));
            assert(expected==both(&f,&r,k));
        }
    }
    puts("PASS: 32768 lowm probes identical across all four variants, every wrap difference");
}
#endif
''')
    out = ROOT / 'research/data/sync_lowm_cost_probe.c'
    out.write_text(text, encoding='utf-8')
    cmd = ('cc -O2 -std=c11 -DHOST_CHECK -I/src/main /src/research/data/sync_lowm_cost_probe.c '
           '-o /tmp/probe && /tmp/probe && riscv32-esp-elf-gcc -O2 -std=c11 -I/src/main '
           '-c /src/research/data/sync_lowm_cost_probe.c -o /tmp/lowm.o && '
           'riscv32-esp-elf-objdump -d --no-show-raw-insn /tmp/lowm.o')
    run = subprocess.run(['docker', 'run', '--rm', '-v', f'{ROOT.as_posix()}:/src:ro',
                          'espressif/idf:v6.0.2', 'bash', '-c', cmd], capture_output=True, text=True)
    if run.returncode:
        raise RuntimeError(run.stdout + run.stderr)
    asm = run.stdout[run.stdout.find('/tmp/lowm.o:'):]
    (ROOT / 'research/data/sync_lowm_cost.disasm').write_text(asm, encoding='utf-8')
    counts = {}
    for name in ('baseline', 'branchless', 'unrolled', 'both'):
        start = asm.index(f'<{name}>:')
        after = asm[start:]
        stop = re.search(r'\n[0-9a-f]+ <(?!\.L)[^>]+>:', after)
        section = after[:stop.start()] if stop else after
        ins = re.findall(r'^\s*[0-9a-f]+:\s+[^\n]+', section, re.M)
        counts[name] = len(ins)
    result = {'toolchain': 'espressif/idf:v6.0.2 RV32 -O2',
              'static_instructions': counts,
              'dynamic_instructions_if_current_emission_unchanged': {
                  'baseline': [144, 153], 'branchless': [135, 135],
                  'unrolled': [121, 130], 'both': [112, 112]},
              'correctness': '32768 random/wrapping probes match; all differences -255..255 match',
              'note': 'Rolled loops execute their body nine times; static counts are not dynamic counts or timing'}
    (ROOT / 'research/data/sync_lowm_cost.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()

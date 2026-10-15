"""C5VRX by Twotoz/contributors: temporary actual rolling-average equivalence.

Primitive differential plus the unchanged full keeper's scene/output gates.
This is host validation, never C5 WCET or source-publication proof.
"""
import json
import hashlib
from pathlib import Path
import subprocess
import tempfile
import sync_track_slice as experiment


def main():
    root = experiment.ROOT
    source = experiment.load_core()
    rolling = experiment.variant(source, False, True).replace('    assert(!allow_repair);', '')
    with tempfile.TemporaryDirectory(prefix='c5-rolling-proof-') as directory:
        folder = Path(directory)
        (folder / 'rolling.c').write_text(rolling)
        (folder / 'differential.c').write_text(r'''
#include "/data/rolling.c"
#include <stdio.h>
static uint32_t seed=212;
static uint32_t next(void){seed^=seed<<13;seed^=seed>>17;seed^=seed<<5;return seed;}
int main(void){
 uint8_t raw[32768],phase[256];sync_flywheel_t f={0};
 for(unsigned j=0;j<256;++j)phase[j]=j;
 for(unsigned j=0;j<32768;++j)raw[j]=(uint8_t)next();
 sfw_ring_t r={.ring=raw,.ring_bytes=32768,.phase=phase};
 uint64_t k=65536;unsigned continuous=0,discontinuous=0;
 for(unsigned j=0;j<1000000;++j){
   if((next()&7u)!=0){k+=3;++continuous;}
   else{k=65536u+(next()%32768u);++discontinuous;}
   if((j%128u)==0u){raw[(uint32_t)k&32767u]^=(uint8_t)next();roll_valid=false;}
   if((j%257u)==0u){phase[next()%256u]=(uint8_t)next();roll_valid=false;}
   if((j%1024u)==0u){k=UINT64_MAX-63u;roll_valid=false;}
   if(k<roll_last)roll_valid=false; /* source epoch rollover is explicit */
   f.thr=(int16_t)((int)(next()%256u)-128);
   bool actual=lowm(&f,&r,k),reference=lowm_reference(&f,&r,k);
   assert(actual==reference);
 }
 roll_valid=true;sfw_run(&f,&r,0,0,0,false,0);assert(!roll_valid);
 roll_valid=true;assert(!synth(&f,&r,0,0,0,0,0,0,false,0,0));assert(!roll_valid);
 printf("PASS rolling exact differential:1000000 cases continuous=%u discontinuous=%u seed=212 fullphase/fullthreshold mutations+invalidation+uint64wrap\n",continuous,discontinuous);
}
''')
        common = ['docker', 'run', '--rm', '-v', root.as_posix() + ':/project:ro',
                  '-v', folder.as_posix() + ':/data', '-w', '/project', 'gcc:14']
        subprocess.run(common + ['gcc', '-O2', '-std=c11', '-Wall', '-Wextra', '-Werror',
            '-Wno-unused-function', '-Imain', '/data/differential.c', '-o', '/data/differential'], check=True)
        subprocess.run(common + ['/data/differential'], check=True)
        subprocess.run(common + ['gcc', '-O2', '-std=c11', '-D_DEFAULT_SOURCE', '-Wall', '-Wextra',
            '-Werror', '-Wno-unused-function', '-Imain', 'test/test_sync_flywheel.c',
            '/data/rolling.c', '-lm', '-o', '/data/keeper'], check=True)
        for level in (0, 7):
            subprocess.run(common + ['bash', '-lc', f'SFW_COMMIT=1 SFW_KP_LEVEL={level} /data/keeper'], check=True)
    data = json.loads((root / 'research/data/sync_track_rolling.json').read_text())
    for index in range(0, len(data['rows']), 2):
        base, roll = data['rows'][index:index + 2]
        assert base['case'] == roll['case']
        assert base['state_trace'] == roll['state_trace']
        assert base['raw_writes'] == roll['raw_writes'] == 0
        for key in ('first_lock_sample', 'first_v_sample', 'acquisitions', 'real_v_fields'):
            assert base[key] == roll[key]
    print('PASS all6 full-scene per-turn FSM hashes and raw byte invariance')
    report=dict(provenance='C5VRX by Twotoz/contributors; https://github.com/Twotoz/C5VRX; https://twotoz.github.io/C5VRX/',
        core_sha256=hashlib.sha256(source.encode()).hexdigest(),
        temporary_rolling_sha256=hashlib.sha256(rolling.encode()).hexdigest(),
        primitive_cases=1000000,seed=212,mutations_and_rollover=True,
        explicit_entry_and_synth_invalidation=True,full_keeper_kp_levels=[0,7],
        full_scene_trace_pairs=6,passed=True,hardware_cycles_proven=False)
    (root/'research/data/sync_rolling_verification.json').write_text(json.dumps(report,indent=2)+'\n')


if __name__ == '__main__':
    main()

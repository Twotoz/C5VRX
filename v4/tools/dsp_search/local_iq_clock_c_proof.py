"""C5VRX/Twotoz local standalone C streaming accumulator equivalence.

Docker gcc:14 host execution verifies semantics, not ESP32-C5 cycle timing.
C5 cross-assembly is recorded separately using cached Espressif IDF6.0.2.
"""
import hashlib
import json
from pathlib import Path
import subprocess
import numpy as np
import local_iq_clock_integer as I
import local_joint_observation as J


if __name__=='__main__':
    import argparse
    ap=argparse.ArgumentParser()
    ap.add_argument('--stride',type=int,choices=(63,127),default=63)
    ap.add_argument('--out',default='iq-clock-integer-proof')
    args=ap.parse_args()
    repo=J.ROOT.parent
    target=J.ROOT/'docs/data/local_joint'/args.out
    source=J.ROOT/'tools/dsp_search/local_iq_clock_stream.c'
    rel=source.relative_to(repo).as_posix()
    binary=(target/'stream-host-proof').relative_to(repo).as_posix()
    mount=f'{repo}:/research'
    subprocess.run(['docker','run','--rm','-v',mount,'-w','/research','gcc:14',
        'gcc','-O3','-Wall','-Wextra','-Werror','-DIQ_CLOCK_HOST_PROOF',
        f'-DIQ_CLOCK_STRIDE={args.stride}',rel,'-o',binary],check=True)
    cases=[]
    arrays=[('random',np.random.default_rng(7900190).integers(0,256,80000,dtype=np.uint8)),
            ('maximum_product',np.full(80000,0x77,np.uint8))]
    for name,raw in arrays:
        _,_,hist,sums=I.acquire(raw,stride=args.stride)
        initial=np.array([838861,844727],np.uint64)
        step=np.array([round(args.stride/p*2**32) for p in I.R.PERIODS.values()],np.uint64)
        final=((initial+step*int(sums[3]))&0xffffffff).astype('<u4')
        expected=hist.astype('<i4').tobytes()+sums.astype('<i4').tobytes()+final.tobytes()
        for chunk in (1,2,63,64,4092,8191,80000):
            r=subprocess.run(['docker','run','--rm','-i','-v',mount,'gcc:14',
                '/research/'+binary,str(chunk)],input=raw.tobytes(),capture_output=True,check=True)
            assert r.stdout==expected,(name,chunk,len(r.stdout),r.stderr)
            cases.append(dict(case=name,chunk=chunk,byte_exact=True,
                output_sha256=hashlib.sha256(r.stdout).hexdigest()))
    result=dict(stride=args.stride,cases=cases,source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        c5_assembly_sha256=hashlib.sha256((target/'stream-rv32imc.s').read_bytes()).hexdigest(),
        scope='host_C_semantics_and_C5_cross_compile_not_CPU_cycles_or_DMA_acceptance')
    (target/'c-proof.json').write_text(json.dumps(result,indent=1))
    (target/source.name).write_bytes(source.read_bytes())
    print(json.dumps(dict(cases=len(cases),byte_exact=True)),flush=True)

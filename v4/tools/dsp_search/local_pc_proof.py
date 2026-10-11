"""C5VRX/Twotoz local PC-prediction proof using official C5 assembler.

Assembler downloaded separately from Espressif v6.0.2, retains its license.
Passing this proof does not establish branch timing on a physical C5.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import local_pc_prediction as P


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


if __name__=='__main__':
    ap=argparse.ArgumentParser()
    ap.add_argument('--assembler',type=Path,required=True)
    ap.add_argument('--target',type=Path,required=True)
    ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--family',choices=('pc','complex'),default='pc')
    a=ap.parse_args();a.out.mkdir(parents=True,exist_ok=True)
    if a.family=='complex':import local_complex_counter as P
    result=dict(source_dataflow=P.verify(),assembler_sha256=sha(a.assembler),
        target_sha256=sha(a.target),official_version='espressif/esp-idf v6.0.2',
        physical_acceptance=False,programs=[])
    for m in P.candidates():
        src=a.out/(m['name']+'.bsasm');binary=src.with_suffix('.bsbin')
        src.write_text(P.source(m))
        r=subprocess.run([sys.executable,str(a.assembler),'-c',str(a.target),
            str(src),str(binary)],capture_output=True,text=True)
        row=dict(name=m['name'],source_sha256=sha(src),exit_code=r.returncode,
            assembler_output=r.stdout+r.stderr)
        if r.returncode==0:row.update(binary_bytes=binary.stat().st_size,binary_sha256=sha(binary))
        result['programs'].append(row)
    result['assembler_pass']=all(r['exit_code']==0 for r in result['programs'])
    (a.out/'proof.json').write_text(json.dumps(result,indent=1))
    print(json.dumps(result))
    if not result['assembler_pass']:sys.exit(1)

#!/usr/bin/env python3
"""Validate every archived belief ROM with the official C5 assembler.

Run from repository root, with v4/tools and dsp_search on PYTHONPATH.
This establishes source/dataflow and assembler legality, not board timing.
"""
import argparse, hashlib, json, subprocess, sys, tempfile
from pathlib import Path
import numpy as np
import bs_model as BS
import compile_overlay as C
import overlay_fsm as O


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--idf',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args()
    assembler=args.idf/'tools/bsasm.py'
    target=args.idf/'components/esp_driver_bitscrambler/bsasm_targets/esp32c5.json'
    roots=sorted(Path('v4/docs/data').glob('belief*rom*'))
    result=dict(assembler_sha256=sha(assembler),target_sha256=sha(target),programs=[],selected=[],confirmation_files=[],independent_iq_count=0,benchmark_rows=0)
    hashes=set()
    with tempfile.TemporaryDirectory() as tmp:
        for root in roots:
            for p in sorted(root.glob('*.bsasm')):
                binary=Path(tmp)/'program.bin'
                subprocess.run([sys.executable,str(assembler),'-c',str(target),str(p),str(binary)],check=True,capture_output=True)
                cfg,lut,blocks,_=BS.parse(p.read_text())
                assert len(lut)==1024 and int(cfg['lut_width_bits'])==16
                assert len(blocks)==8
                assert binary.stat().st_size==2348
                result['programs'].append(dict(path=str(p),source_sha256=sha(p),binary_sha256=sha(binary),binary_bytes=2348,lut_bytes=2048))
            selected=root/'selected_model.json'
            if selected.exists():
                m=json.loads(selected.read_text());source=C.build(m)
                assert source==(root/'selected.bsasm').read_text()
                raw=np.random.default_rng(190).integers(0,256,70000,dtype=np.uint8)
                np.testing.assert_array_equal(np.asarray(BS.simulate(source,raw,len(raw),wrap_rom=True))&63,O.model_codes(raw,m))
                result['selected'].append(dict(path=str(selected),sha256=sha(selected),tested_raw_samples=len(raw),dac6_equivalent=True))
            for name in ('confirmation.json','ablation_b3.json'):
                p=root/name
                if p.exists():
                    x=json.loads(p.read_text());rows=x['rows']
                    result['benchmark_rows']+=len(rows)
                    hashes.update(r['iq_sha256'] for r in rows)
                    result['confirmation_files'].append(dict(path=str(p),sha256=sha(p),seed=x.get('seed'),rows=len(rows)))
    result['independent_iq_count']=len(hashes)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:len(v) if isinstance(v,list) else v for k,v in result.items()}))

if __name__=='__main__':main()

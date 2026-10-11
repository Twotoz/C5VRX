#!/usr/bin/env python3
"""RF tap folding cross-check; C5VRX by Twotoz and the contributors.

Reuses the frozen U85G candidate; no tuning on these cases. Limited AWGN
validation, separate from the larger inherited clipping-quantizer study.
"""
import json
import multiprocessing as mp
import os
from pathlib import Path
import sys
os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
import pair_validate as V

if __name__ == '__main__':
    work=[]
    for d,f,k in [(1.6,16e6,.85),(1.7,16e6,.85),(2.,16e6,.85)]:
        for seed in [191001,191002]:
            for cn in [2.,4.,6.,25.]:
                work.append(dict(deviation=d,filter_hz=f,kp=k,seed=seed,cnr=cn,
                    standard=('PAL','NTSC')[seed%2],rms=4.2,offset_hz=0.,fields=2,
                    echoes=[],fades=[],kind='awgn',quantizer='ultrafine'))
    rows=[]
    out=Path(sys.argv[1])
    with mp.get_context('spawn').Pool(4) as pool:
        for rr in pool.imap(V.job,work):
            rows+=rr
            out.write_text(json.dumps(dict(protocol=dict(quantizer='ultrafine bits 9,6,5,4',
                baseline_sha='8819b695c7a9983bda22de4ff7647b5ecd4f0601',
                limitations='48 metric rows, AWGN only; not an echo regression gate'),rows=rows)))
            print(len(rows),'/48',flush=True)
    V.report(rows)

"""Compare rejected and IQ-corrected PLL96 without candidate-specific fitting."""
import json
import argparse
import numpy as np
from pathlib import Path
import engine as S
import validate_search as Q
import waveforms as V


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--repair',type=Path)
    ap.add_argument('--output',type=Path,default=Path('pll96-iq-audit.json'))
    a=ap.parse_args()
    rows=[]
    baseline=S.F.load_reference('OVP56')
    methods=[('OVP56',lambda raw:S.decode(raw,baseline))]+Q.controls()
    if a.repair:
        params=json.loads(a.repair.read_text())['leaders'][0]['params']
        table,enc=Q.G.tables(params);lut=np.array(table+enc,np.int64)
        methods.append(('PLL96-repaired',lambda raw:Q.H.B.D.goggle(Q.H.B.DAC_VOLTS[Q.phase_loop(raw,lut)])))
    for standard in ('PAL','NTSC'):
        for cnr in (30,2):
            c=V.make_case(standard,9801,cnr,3)
            y=S.decode(c['clean'],baseline)
            c['calibration']=V.M.clean_calibration(y[:131072],c['truth'][:131072],3000)
            reference=V.waveform_metrics(S.decode(c['raw'],baseline),c)
            for name,fn in methods:
                stats=V.waveform_metrics(fn(c['raw']),c)
                stats.update(model=name,standard=standard,cnr=cnr,
                             failures=V.gate(stats,reference,cnr>=14))
                rows.append(stats)
                print(json.dumps(stats),flush=True)
    a.output.write_text(json.dumps(rows,indent=2))


if __name__=='__main__':main()

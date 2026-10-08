"""Fixed-level output reconstruction sweep for the frozen PLL96 repair pool."""
import json
from pathlib import Path
import numpy as np
import engine as S
import validate_search as Q
import waveforms as V


def main():
    pool=json.loads(Path('pll96-repair-selection.json').read_text())['leaders']
    baseline=S.F.load_reference('OVP56');cases=[]
    for standard in ('PAL','NTSC'):
        c=V.make_case(standard,9811,30,3,short=True)
        c['reference']=V.waveform_metrics(S.decode(c['raw'],baseline),c);cases.append(c)
    records=[]
    for model in pool:
        for direct in np.linspace(0,1,11):
            p=list(model['params']);p[2]=float(direct)
            table,enc=Q.G.tables(p);lut=np.array(table+enc,np.int64);rr=[]
            for c in cases:
                stats=V.waveform_metrics(Q.H.B.D.goggle(Q.H.B.DAC_VOLTS[Q.phase_loop(c['raw'],lut)]),c)
                rr.append(dict(standard=c['standard'],metrics=stats,failures=V.gate(stats,c['reference'])))
            records.append(dict(params=p,validation=rr,
                                missing=sum(r['metrics']['h_missing']+r['metrics']['v_missing'] for r in rr),
                                sinad=float(np.mean([r['metrics']['sinad'] for r in rr]))))
    records.sort(key=lambda x:(x['missing'],-x['sinad']))
    Path('pll96-reconstruction-selection.json').write_text(json.dumps(dict(evaluations=len(records),seed=9811,leaders=records[:32]),indent=2))
    print(json.dumps(records[0]),flush=True)


if __name__=='__main__':main()

#!/usr/bin/env python3
"""Independent amplitude-failure audit; no candidate fitting or C/N oracle."""
import argparse,json,hashlib
from pathlib import Path
from amplitude_cases import cases
from omega_research import controls,measure
from pair_autofit import remap
import overlay_fsm as O

def main():
 ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--output',type=Path,required=True);a=ap.parse_args()
 if a.output.exists():ap.error('fresh results path required')
 m=json.loads((Path(__file__).resolve().parents[1]/'omega_model.json').read_text())
 fns=controls();fns['OMEGA']=lambda c:O.decode(c['raw'],remap(m,c['fit']['fit_deviation'],c['fit']['fit_centre_hz']))
 rows=[]
 for c in cases():
  for name,fn in fns.items():
   rows.append(dict(seed=c['seed'],standard=c['standard'],pattern=c['pattern'],cnr=c['cnr'],rms=c['rms'],model=name,
     fit=c['fit'],iq_sha256=hashlib.sha256(c['raw'].tobytes()).hexdigest(),metrics=measure(c,fn)))
  print(c['cnr'],c['rms'],c['standard'],flush=True)
 a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(dict(seed=1310190,rows=rows),indent=1))
if __name__=='__main__':main()

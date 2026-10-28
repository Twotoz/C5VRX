"""Export source-linked review models, schematic and ray-traced gallery.
SPDX-License-Identifier: GPL-3.0-only
Python 3.13; KiCad 10 CLI must be installed. No AI-generated product imagery.
"""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import os,subprocess,json,zipfile
ROOT=Path(__file__).resolve().parents[1]
CLI=os.environ.get('KICAD_CLI',r'C:\Program Files\KiCad\10.0\bin\kicad-cli.exe')
PCB=ROOT/'C5VRX_PRO.kicad_pcb'
OUT=ROOT/'exports';OUT.mkdir(exist_ok=True)
GALLERY=OUT/'renders';GALLERY.mkdir(exist_ok=True)
def run(args):
    result=subprocess.run([CLI,*map(str,args)],capture_output=True,text=True)
    if result.returncode:raise RuntimeError(result.stdout+result.stderr)
    print('Exported',args[-2] if len(args)>2 else args,flush=True)
run(['sch','export','pdf','-o',OUT/'C5VRX_PRO-schematic.pdf',ROOT/'C5VRX_PRO.kicad_sch'])
for fmt in ('step','glb'):
    run(['pcb','export',fmt,'--force','--no-dnp','--include-pads','--include-silkscreen','-o',OUT/('C5VRX_PRO.'+fmt),PCB])
views=[('01-front-perspective.png','top',['--rotate','-35,0,-25','--perspective','--floor','--zoom','.84']),
       ('02-rear-perspective.png','bottom',['--rotate','-35,0,25','--perspective','--floor','--zoom','.84']),
       ('03-top.png','top',['--zoom','.98']),
       ('04-front-low-angle.png','top',['--rotate','-55,0,12','--perspective','--floor','--zoom','.86'])]
def render(v):
    name,side,args=v
    run(['pcb','render','--side',side,'--width','3000','--height','2200','--quality','high','--background','opaque',
         '--light-side-elevation','50','--light-camera','.25',*args,'-o',GALLERY/name,PCB])
with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(render,views))
(GALLERY/'README.txt').write_text('Native KiCad 10 ray-traced renders of the committed PCB.\nDefault six-bit assembly. DNP BPF/seventh resistor/buzzer omitted.\nCustom STEP package envelopes are illustrative, not vendor-certified fit models.\n',encoding='utf8')
with zipfile.ZipFile(OUT/'C5VRX_PRO-renders.zip','w',zipfile.ZIP_DEFLATED) as z:
    for name,_,_ in views:z.write(GALLERY/name,name)
print('Review export complete',flush=True)

"""Parametric STEP package envelopes, CadQuery 2.8.0.
SPDX-License-Identifier: GPL-3.0-only
These are review envelopes, not vendor-certified mechanical models.
"""
from pathlib import Path
import cadquery as cq
OUT=Path(__file__).resolve().parents[1]/'models'
OUT.mkdir(exist_ok=True)
BLACK=cq.Color(.055,.06,.065)
SILVER=cq.Color(.72,.73,.75)
GOLD=cq.Color(.84,.66,.25)
def box(w,h,z,x=0,y=0,base=0):
    return cq.Workplane('XY').box(w,h,z,centered=(True,True,False)).translate((x,y,base))
def save(name,body,extra=()):
    a=cq.Assembly(name=name)
    a.add(body,name='package',color=BLACK)
    for i,(solid,color) in enumerate(extra):a.add(solid,name='part'+str(i),color=color)
    a.export(str(OUT/(name+'.step')))

save('ESP32-C5HR2',box(6,6,.85,base=.05),[(box(4.6,4.6,.05),SILVER)])
save('QPL9504',box(2,2,.8,base=.05),[(box(.8,1.6,.05),SILVER)])
save('PE42422',box(2,2,.5,base=.05),[(box(1.1,1.1,.05),SILVER)])
save('LFB215G37',box(2,1.25,.95),[(box(.3,1.25,.4,x=-.85),SILVER),(box(.3,1.25,.4,x=.85),SILVER)])
save('TLV75733',box(2,2,.75,base=.05))
body=box(5.9,5.9,1.45,base=.15)
metal=box(5.7,5.7,.25,base=1.6)
cap=cq.Workplane('XY').circle(1.4).extrude(1.8).translate((0,0,1.85))
save('SKRHABE010',body,[(metal,SILVER),(cap,BLACK)])
save('B3U1000P',box(3,2.5,.8),[(box(2.7,2.2,.2,base=.8),SILVER),(cq.Workplane('XY').circle(.75).extrude(.6).translate((0,0,1)),BLACK)])
# USB shell, opening on negative local Y; top-of-board reference is Z=0.
shell=box(8.9,7.3,3.15,y=.6,base=.1).cut(box(8.2,7.5,2.5,y=.3,base=.4))
tongue=box(6.7,5.5,.6,y=1.0,base=1.3)
a=cq.Assembly(name='USB-C');a.add(shell,name='shield',color=SILVER);a.add(tongue,name='tongue',color=BLACK)
for i in range(12):a.add(box(.22,3,.025,x=-2.75+i*.5,y=.5,base=1.9),name='contact'+str(i),color=GOLD)
a.export(str(OUT/'USB-C.step'))
# Bay contacts point below the board, matching the inherited orientation.
a=cq.Assembly(name='FatSharkBay')
a.add(box(2.54,22.86,1.5,y=-10.16,base=-3.1),name='insulator',color=BLACK)
for i in range(9):a.add(box(.64,.64,8,y=-i*2.54,base=-7),name='pin'+str(i),color=GOLD)
a.export(str(OUT/'FatSharkBay.step'))
# Vertical through-hole female SMA envelope, with visible thread rings.
a=cq.Assembly(name='SMA-Vertical')
a.add(box(7,7,2.0),name='base',color=GOLD)
barrel=cq.Workplane('XY').circle(2.95).circle(2.05).extrude(7.5).translate((0,0,2))
a.add(barrel,name='barrel',color=GOLD)
for i in range(9):
    a.add(cq.Workplane('XY').circle(3.175).circle(2.95).extrude(.26).translate((0,0,3.2+i*.635)),name='thread'+str(i),color=GOLD)
a.add(cq.Workplane('XY').circle(2.05).circle(.55).extrude(.7).translate((0,0,8)),name='dielectric',color=cq.Color(.94,.92,.84))
a.add(cq.Workplane('XY').circle(.52).circle(.3).extrude(.5).translate((0,0,8.2)),name='socket',color=GOLD)
for x in (-2.54,2.54):
    for y in (-2.54,2.54):a.add(box(.9,.9,3.4,x,y,-3.4),name='leg'+str(x)+str(y),color=SILVER)
a.add(cq.Workplane('XY').circle(.6).extrude(-3.4),name='center-pin',color=GOLD)
a.export(str(OUT/'SMA-Vertical.step'))
print('Wrote',len(list(OUT.glob('*.step'))),'STEP models')

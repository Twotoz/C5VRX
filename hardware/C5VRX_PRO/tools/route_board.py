"""Prepare or import routing. Run in KiCad 10 Python.
SPDX-License-Identifier: GPL-3.0-only
Usage: route_board.py prepare | import
Routing does not qualify RF geometry, USB impedance or thermal performance.
"""
from pathlib import Path
import sys
import pcbnew as p
ROOT=Path(__file__).resolve().parents[1]
PCB=ROOT/'C5VRX_PRO.kicad_pcb'
b=p.LoadBoard(str(PCB))
def pt(x,y):return p.VECTOR2I(p.FromMM(x),p.FromMM(y))
def zone(layer):
    z=p.ZONE(b);z.SetLayer(layer);z.SetNet(b.FindNet('GND'));z.SetLocalClearance(p.FromMM(.20));z.SetThermalReliefGap(p.FromMM(.20));z.SetThermalReliefSpokeWidth(p.FromMM(.25))
    z.SetPadConnection(p.ZONE_CONNECTION_FULL)
    outline=z.Outline();outline.NewOutline()
    for x,y in [(100.25,100.25),(142.26,100.25),(142.26,128.55),(100.25,128.55)]:outline.Append(int(p.FromMM(x)),int(p.FromMM(y)))
    b.Add(z)
def via(x,y,net='GND'):
    size,drill=(.5,.25) if net=='GND' else (.4,.2)
    v=p.PCB_VIA(b);v.SetPosition(pt(x,y));v.SetWidth(p.FromMM(size));v.SetDrill(p.FromMM(drill));v.SetViaType(p.VIATYPE_THROUGH);v.SetLayerPair(p.F_Cu,p.B_Cu);v.SetNet(b.FindNet(net));b.Add(v)
def escape(pad,x,y):
    net=pad.GetNetname()
    t=p.PCB_TRACK(b);t.SetStart(pad.GetPosition());t.SetEnd(pt(x,y));t.SetWidth(p.FromMM(.127));t.SetLayer(p.F_Cu);t.SetNet(pad.GetNet());b.Add(t)
    via(x,y,net)
if sys.argv[1]=='prepare':
    for x in (117.7,119,120.3):
        for y in (113.7,115,116.3):via(x,y)
    for x,y in [(115,105.8),(115,106.6),(110.8,105.5),(123,105.5),(121,125)]:via(x,y)
    soc=b.FindFootprintByReference('U1')
    for i in range(13,24):
        pad=soc.FindPadByNumber(str(i));x,y=p.ToMM(pad.GetPosition())
        escape(pad,x,118.7 if i%2 else 119.4)
    for i in range(26,37):
        pad=soc.FindPadByNumber(str(i));x,y=p.ToMM(pad.GetPosition())
        escape(pad,122.9 if i%2 else 123.7,y)
    escape(soc.FindPadByNumber('7'),115.2,115.2)
    zone(p.In1_Cu)
    p.ZONE_FILLER(b).Fill(b.Zones())
    p.SaveBoard(str(PCB),b)
    if not p.ExportSpecctraDSN(b,str(ROOT/'validation/C5VRX_PRO.dsn')):raise RuntimeError('DSN export failed')
    # Keep layer 2 as a ground reference rather than routing signals across it.
    dsn=ROOT/'validation/C5VRX_PRO.dsn'
    s=dsn.read_text();s=s.replace('(layer In1.Cu\n      (type signal)', '(layer In1.Cu\n      (type power)')
    dsn.write_text(s)
    print('Prepared routing with dedicated ground reference on In1.Cu')
elif sys.argv[1]=='import':
    if not p.ImportSpecctraSES(b,str(ROOT/'validation/C5VRX_PRO.ses')):raise RuntimeError('SES import failed')
    for layer in (p.F_Cu,p.B_Cu):zone(layer)
    p.ZONE_FILLER(b).Fill(b.Zones())
    p.SaveBoard(str(PCB),b)
    print('Imported and filled routed board')
else:raise SystemExit('Expected prepare or import')

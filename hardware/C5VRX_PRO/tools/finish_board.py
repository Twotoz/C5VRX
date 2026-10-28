"""Apply manufacturing widths and stitch the GND pours after routing.
SPDX-License-Identifier: GPL-3.0-only
Run with KiCad Python; inspect DRC again afterwards.
"""
from pathlib import Path
import math,json,xml.etree.ElementTree as ET
import pcbnew as p
ROOT=Path(__file__).resolve().parents[1]
pcb=ROOT/'C5VRX_PRO.kicad_pcb'
b=p.LoadBoard(str(pcb))
def pt(x,y):return p.VECTOR2I(p.FromMM(x),p.FromMM(y))
data=json.loads((ROOT/'design.json').read_text())
for c in data['parts']:
    fp=b.FindFootprintByReference(c['ref'])
    fp.SetField('MPN',c['mpn']);fp.GetField('MPN').SetVisible(False)
    fp.SetField('Datasheet',c['url']);fp.GetField('Datasheet').SetVisible(False)
    fp.SetAttributes(fp.GetAttributes() & ~p.FP_EXCLUDE_FROM_BOM)
# KiCad gives explicit NC pins unique net names. Preserve those singleton
# nets for exact schematic/PCB parity; they acquire no copper routes.
xml=ROOT/'validation/schematic.xml'
if xml.exists():
    for net in ET.parse(xml).getroot().findall('./nets/net'):
        name=net.attrib['name']
        if not name.startswith('unconnected-'):continue
        board_net=b.FindNet(name)
        if board_net is None:
            board_net=p.NETINFO_ITEM(b,name);b.Add(board_net)
        for node in net.findall('node'):
            fp=b.FindFootprintByReference(node.attrib['ref']);pad=fp.FindPadByNumber(node.attrib['pin'])
            if pad is not None:pad.SetNet(board_net)
for t in list(b.GetTracks()):
    # MODEM_DIAG is an internal GPIO-matrix loopback: never fan it out into
    # an external antenna stub. GPIO27 alone has the required boot pull-up.
    if t.GetNetname().startswith('IQ_') and t.GetNetname()!='IQ_I0':b.Delete(t)
    elif isinstance(t,p.PCB_VIA) and t.GetNetname()=='FLASH_CS' and p.ToMM(t.GetPosition())==(123.7,116.8):b.Delete(t)
for t in b.GetTracks():
    if not isinstance(t,p.PCB_VIA) and t.GetWidth()<p.FromMM(.127):t.SetWidth(p.FromMM(.127))
for g in b.GetDrawings():
    if isinstance(g,p.PCB_TEXT):
        if g.GetLayer()==p.B_SilkS:g.SetMirrored(True)
        if g.GetLayer()==p.F_SilkS and g.GetText() not in ('REV A','BOOT','RESET'):
            g.SetLayer(p.Dwgs_User)
        if g.GetLayer()==p.F_SilkS and g.GetText()=='RESET':g.SetPosition(pt(115.9,127.7))
obstacles=[]
for fp in b.GetFootprints():
    for pad in fp.Pads():
        if pad.GetNetname()=='GND' and pad.GetDrillSize().x==0:continue
        bb=pad.GetBoundingBox()
        margin=.20 if pad.GetNetname()=='GND' else .327
        obstacles.append((p.ToMM(bb.GetX())-margin,p.ToMM(bb.GetY())-margin,
                          p.ToMM(bb.GetRight())+margin,p.ToMM(bb.GetBottom())+margin))
segments=[];vias=[]
for t in b.GetTracks():
    if isinstance(t,p.PCB_VIA):
        x,y=p.ToMM(t.GetPosition());vias.append((x,y,p.ToMM(t.GetWidth(p.F_Cu))/2))
    elif t.GetNetname()!='GND':
        a=p.ToMM(t.GetStart());c=p.ToMM(t.GetEnd());segments.append((*a,*c,.327+p.ToMM(t.GetWidth())/2))
def distseg(x,y,ax,ay,bx,by):
    dx,dy=bx-ax,by-ay
    f=max(0,min(1,((x-ax)*dx+(y-ay)*dy)/(dx*dx+dy*dy))) if dx or dy else 0
    return math.hypot(x-ax-f*dx,y-ay-f*dy)
def clear(x,y):
    for lx,ly,rx,ry in obstacles:
        if lx<=x<=rx and ly<=y<=ry:return False
    for vx,vy,r in vias:
        if math.hypot(x-vx,y-vy)<r+.327:return False
    for ax,ay,bx,by,r in segments:
        if x<min(ax,bx)-r or x>max(ax,bx)+r or y<min(ay,by)-r or y>max(ay,by)+r:continue
        if distseg(x,y,ax,ay,bx,by)<r:return False
    return True
count=0
def stitch(x,y):
    global count
    v=p.PCB_VIA(b);v.SetPosition(pt(x,y));v.SetWidth(p.FromMM(.4));v.SetDrill(p.FromMM(.2));v.SetLayerPair(p.F_Cu,p.B_Cu);v.SetViaType(p.VIATYPE_THROUGH);v.SetNet(b.FindNet('GND'));b.Add(v)
    vias.append((x,y,.2));count+=1
for iy in range(23):
    y=100.9+iy*1.2
    for ix in range(34):
        x=100.9+ix*1.2
        if (x<102 and y<102) or (x>140.5 and y<102) or (x<102 and y>126.8) or (x>140.5 and y>126.8):continue
        if not clear(x,y):continue
        stitch(x,y)
# Filled/capped via-in-pad is explicitly required in the fabrication notes.
# Stitch small ground islands that are enclosed by signal escape traces.
for fp in b.GetFootprints():
    for pad in fp.Pads():
        if pad.GetNetname()!='GND' or pad.GetDrillSize().x:continue
        x,y=p.ToMM(pad.GetPosition())
        if clear(x,y):stitch(x,y)
# Explicit under-package ground bonds to the exposed pad; do not ask a
# general-purpose router to escape RF grounds through the RF pin fanout.
for ref,ep in [('U1','49'),('U3','13'),('U4','9'),('U5','13'),('U7','7')]:
    fp=b.FindFootprintByReference(ref);center=fp.FindPadByNumber(ep).GetPosition()
    for old in list(b.GetTracks()):
        if not isinstance(old,p.PCB_VIA) and old.GetNetname()=='GND' and old.GetEnd()==center:b.Delete(old)
    for pad in fp.Pads():
        if pad.GetNetname()!='GND' or pad.GetNumber()==ep:continue
        pos=pad.GetPosition();cx,cy=p.ToMM(center);x,y=p.ToMM(pos)
        end=pt(cx,y) if abs(x-cx)>abs(y-cy) else pt(x,cy)
        t=p.PCB_TRACK(b);t.SetStart(pos);t.SetEnd(end);t.SetWidth(p.FromMM(.127));t.SetLayer(p.F_Cu);t.SetNet(b.FindNet('GND'));b.Add(t)
p.FootprintSave(str(ROOT/'PRO.pretty'),b.FindFootprintByReference('J1'))
anchors=[t.GetPosition() for t in b.GetTracks() if isinstance(t,p.PCB_VIA) and t.GetNetname()=='GND']
anchors += [pad.GetPosition() for fp in b.GetFootprints() for pad in fp.Pads() if pad.GetNetname()=='GND' and pad.GetDrillSize().x]
for z in b.Zones():
    poly=z.GetFilledPolysList(z.GetLayer())
    for index in range(poly.OutlineCount()):
        if any(poly.Contains(v,index) for v in anchors):continue
        box=poly.Outline(index).BBox();lx,ly=p.ToMM(box.GetPosition());w,h=p.ToMM(box.GetSize())
        found=False
        for iy in range(int(h/.08)+1):
            if found:break
            for ix in range(int(w/.08)+1):
                x,y=lx+ix*.08,ly+iy*.08
                if poly.Contains(pt(x,y),index) and clear(x,y):
                    stitch(x,y);anchors.append(pt(x,y));found=True;break
        if not found:print('Review ground island',z.GetLayerName(),index,(lx,ly,w,h))
for z in b.Zones():z.SetIslandRemovalMode(p.ISLAND_REMOVAL_MODE_ALWAYS)
seen=set()
for t in list(b.GetTracks()):
    if isinstance(t,p.PCB_VIA):continue
    a=tuple(p.ToMM(t.GetStart()));c=tuple(p.ToMM(t.GetEnd()))
    key=(t.GetNetCode(),t.GetLayer(),t.GetWidth(),*sorted((a,c)))
    if key in seen:b.Delete(t)
    else:seen.add(key)
p.ZONE_FILLER(b).Fill(b.Zones())
p.SaveBoard(str(pcb),b)
print('Added',count,'ground stitching vias; minimum track width 0.127 mm')

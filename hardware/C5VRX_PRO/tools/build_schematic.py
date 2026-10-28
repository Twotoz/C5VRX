"""Generate readable KiCad hierarchical schematic from the reviewed pin table.
SPDX-License-Identifier: GPL-3.0-only
"""
from pathlib import Path
import json, uuid, math
ROOT=Path(__file__).resolve().parents[1]
data=json.loads((ROOT/'design.json').read_text())
ROOT_ID=data['root_uuid']
NAME=data['name']
def uid(s):return str(uuid.uuid5(uuid.NAMESPACE_URL,'https://github.com/Twotoz/C5VRX/PRO/'+s))
def q(s):return json.dumps(str(s),ensure_ascii=False)
def effects(size=1,extra=''):return f'(effects (font (size {size} {size})) {extra})'
def prop(name,val,x,y,hide=False):
    return f'(property {q(name)} {q(val)} (at {x} {y} 0) {effects(1,"(hide yes)" if hide else "")})'
def head(id,title):
    return f'(kicad_sch (version 20260306) (generator "eeschema") (generator_version "10.0") (uuid {id}) (paper "A3") (title_block (title {q(title)}) (rev "A - ENGINEERING REVIEW") (company "C5VRX / Twotoz and contributors") (comment 1 "Not released for fabrication; see README and validation reports"))'
def geometry(c):
    nums=list(c['nets'])
    if len(nums)==2:
        return [('1',-7.62,0,0),('2',7.62,0,180)],3.81,2.54
    half=math.ceil(len(nums)/2)
    h=max(5.08,half*2.54/2+1.27)
    out=[]
    for i,num in enumerate(nums):
        right=i>=half
        j=i-half if right else i
        out.append((num,17.78 if right else -17.78,(half-1)*1.27-j*2.54,180 if right else 0))
    return out,15.24,h
def symbol(c):
    pins,w,h=geometry(c)
    out=f'(symbol "PRO:{c["ref"]}" (pin_names (offset 0.6)) (exclude_from_sim no) (in_bom yes) (on_board yes)'
    out+=prop('Reference',c['ref'],0,h+2)+prop('Value',c['value'],0,-h-2)
    out+=f'(symbol "{c["ref"]}_0_1" (rectangle (start {-w} {h}) (end {w} {-h}) (stroke (width 0.254) (type default)) (fill (type background))))'
    out+=f'(symbol "{c["ref"]}_1_1"'
    for num,x,y,angle in pins:
        name=c.get('pins',{}).get(str(num),str(num))
        # Physical pin table is kept in design.json; the symbols intentionally
        # use passive pins. ERC cannot replace vendor pin/function review.
        out+=f'(pin passive line (at {x} {y} {angle}) (length {2.54 if len(pins)>2 else 3.81}) (name {q(name)} {effects(.85)}) (number {q(num)} {effects(.85)}))'
    return out+'))'
def placed(c,x,y,sheetid):
    x=round(x/1.27)*1.27;y=round(y/1.27)*1.27
    pins,w,h=geometry(c)
    out=f'(symbol (lib_id "PRO:{c["ref"]}") (at {x} {y} 0) (unit 1) (exclude_from_sim no) (in_bom yes) (on_board yes) (dnp {"yes" if c["dnp"] else "no"}) (uuid {c["uuid"]})'
    out+=prop('Reference',c['ref'],x,y-h-3)+prop('Value',c['value'],x,y+h+3)
    out+=prop('Footprint',c['footprint'],x,y,True)+prop('Datasheet',c['url'],x,y,True)+prop('MPN',c['mpn'],x,y,True)
    for num,*_ in pins:out+=f'(pin {q(num)} (uuid {uid(c["ref"]+"pin"+str(num))}))'
    out+=f'(instances (project "{NAME}" (path "/{ROOT_ID}/{sheetid}" (reference "{c["ref"]}") (unit 1)))))'
    for num,dx,dy,angle in pins:
        px,py=round(x+dx,4),round(y-dy,4)
        net=c['nets'][str(num)]
        if net is None:
            out+=f'(no_connect (at {px} {py}) (uuid {uid(c["ref"]+str(num)+"nc")}))'
            continue
        ex=round(px+(-5.08 if dx<0 else 5.08),4)
        out+=f'(wire (pts (xy {px} {py}) (xy {ex} {py})) (stroke (width 0) (type default)) (uuid {uid(c["ref"]+str(num)+"wire")}))'
        out+=f'(global_label {q(net)} (shape input) (at {ex} {py} {0 if dx<0 else 180}) {effects(.8,"(justify "+("left" if dx<0 else "right")+")")} (uuid {uid(c["ref"]+str(num)+"label")})'
        out+=f'(property "Intersheetrefs" "${{INTERSHEET_REFS}}" (at {ex} {py} 0) {effects(.8,"(hide yes)")}))'
    return out

groups=[('01','CORE',['SOC']),('02','MEMORY AND CLOCK',['FLASH','CLOCK']),('03','RF FRONT END',['RF','MATCH']),
        ('04','POWER AND USB',['POWER','USB']),('05','USER CONTROLS',['RESET','JOY','BUZZ']),('06','VIDEO AND BAY',['VIDEO','BAY'])]
root=head(ROOT_ID,'C5VRX PRO — stand-alone Fat Shark receiver')+'(lib_symbols)'
for index,(number,title,blocks) in enumerate(groups):
    sid=uid('sheet'+number);items=[c for c in data['parts'] if c['block'] in blocks]
    content=head(sid,title)+'(lib_symbols '+''.join(symbol(c) for c in items)+')'
    if number=='01':
        for i,c in enumerate(items):
            if c['ref']=='U1':x,y=70,100
            else:x,y=190+(i-1)%3*80,45+(i-1)//3*42
            content+=placed(c,x,y,sid)
    else:
        for i,c in enumerate(items):
            content+=placed(c,52+(i%4)*94,46+(i//4)*38,sid)
    content+=')'
    (ROOT/(number+'_'+title.replace(' ','_')+'.kicad_sch')).write_text(content,encoding='utf8')
    x,y=35+(index%3)*125,65+(index//3)*80
    file=number+'_'+title.replace(' ','_')+'.kicad_sch'
    root+=f'(sheet (at {x} {y}) (size 100 45) (stroke (width 0) (type default)) (fill (color 0 0 0 0)) (uuid {sid})'
    root+=prop('Sheetname',title,x+50,y-3)+prop('Sheetfile',file,x+50,y+48)
    root+=f'(instances (project "{NAME}" (path "/{ROOT_ID}" (page "{index+2}")))))'
rootnote='BARE ESP32-C5 / 8 MB FLASH / QPL9504 / 6+1 BIT CVBS\n42.51 x 28.80 mm - inherited DIY V3 outline and bay contacts\nEngineering review: component pin tables and RF matching require sign-off'
root+=f'(text {q(rootnote)} (at 210 35 0) {effects(1.5)} (uuid {uid("rootnote")}))'
root+=')'
(ROOT/(NAME+'.kicad_sch')).write_text(root,encoding='utf8')
library='(kicad_symbol_lib (version 20251024) (generator "kicad_symbol_editor") '
for c in data['parts']:
    library+=symbol(c).replace('(symbol "PRO:'+c['ref']+'"','(symbol "'+c['ref']+'"',1)
library+=')'
(ROOT/'PRO.kicad_sym').write_text(library,encoding='utf8')
(ROOT/'sym-lib-table').write_text('(sym_lib_table (version 7) (lib (name "PRO") (type "KiCad") (uri "${KIPRJMOD}/PRO.kicad_sym") (options "") (descr "Reviewed physical pin tables")))\n')
(ROOT/'fp-lib-table').write_text('(fp_lib_table (version 7) (lib (name "PRO") (type "KiCad") (uri "${KIPRJMOD}/PRO.pretty") (options "") (descr "C5VRX PRO local footprints")))\n')
print('Generated seven schematic sheets')

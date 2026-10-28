"""Independent electrical/mechanical consistency audit. KiCad Python 3.11.
SPDX-License-Identifier: GPL-3.0-only
CAD consistency is not RF, firmware or physical qualification.
"""
from pathlib import Path
from collections import Counter
import csv,json,math,xml.etree.ElementTree as ET
import pcbnew as p
ROOT=Path(__file__).resolve().parents[1]
d=json.loads((ROOT/'design.json').read_text());parts={c['ref']:c for c in d['parts']}
b=p.LoadBoard(str(ROOT/'C5VRX_PRO.kicad_pcb'))
source=p.LoadBoard(str(ROOT.parent/'C5VRX_JACK_DIY_V3/C5VRX_JACK_DIY_V3.kicad_pcb'))
checks=[]
def check(name,ok):
    checks.append({'check':name,'passed':bool(ok)})
    if not ok:raise AssertionError(name)
expected={}
for n in ET.parse(ROOT/'validation/schematic.xml').getroot().findall('./nets/net'):
    for node in n.findall('node'):expected[(node.attrib['ref'],node.attrib['pin'])]=n.attrib['name']
actual={}
for fp in b.GetFootprints():
    for pad in fp.Pads():
        if pad.GetNumber():actual[(fp.GetReference(),pad.GetNumber())]=pad.GetNetname()
check('Every schematic net matches its physical PCB pad',all(actual.get(k)==v for k,v in expected.items()))
check('No unexpected connected physical pads',all(not v or k in expected for k,v in actual.items()))
check('BOM and PCB contain the same component references',set(parts)=={f.GetReference() for f in b.GetFootprints()})
check('BOM is complete and names actual intended order codes',all(c['mpn'] for c in parts.values()))
def edges(board):
    return sorted((g.GetShapeStr(),tuple(p.ToMM(g.GetStart())),tuple(p.ToMM(g.GetEnd()))) for g in board.GetDrawings() if g.GetLayer()==p.Edge_Cuts)
check('Exact DIY V3 outline primitives preserved',edges(b)==edges(source))
src=source.FindFootprintByReference('J1');dst=b.FindFootprintByReference('J1')
check('All nine bay pad centres and drills preserved',all(
    dst.FindPadByNumber(str(i)).GetPosition()==src.FindPadByNumber(str(i)).GetPosition() and
    dst.FindPadByNumber(str(i)).GetDrillSize()==src.FindPadByNumber(str(i)).GetDrillSize() for i in range(1,10)))
check('Bay 5V/GND/CVBS unchanged; other contacts remain isolated',all(actual['J1',str(i)]==v for i,v in [(1,'5V_BAY'),(3,'GND'),(6,'VIDEO')]) and
      all(actual['J1',str(i)].startswith('unconnected-') for i in [2,4,5,7,8,9]))
roles={'iq':[1,0,25,2,27,5,3,4],'dac':[23,24,11,12,8,9],'dac7':[10],'joystick':[6],'buzzer':[7],'rf':[26],'usb':[13,14],'boot':[28]}
all_gpio=[x for xs in roles.values() for x in xs]
check('All 21 active/reserved GPIO roles are unique',len(all_gpio)==21 and len(set(all_gpio))==21)
check('No flash/PSRAM GPIO used by a user peripheral',not set(all_gpio)&set(range(15,23)))
check('Native USB pins match C5 datasheet',actual['U1','22']=='USB_DM' and actual['U1','23']=='USB_DP')
check('Joystick uses actual ADC GPIO6 and not digital-only GPIO7',actual['U1','15']=='JOY_ADC')
check('BOOT and CHIP_PU are separate',actual['U1','38']=='BOOT' and actual['U1','7']=='EN')
check('RF select has a reset pull-down',set(parts['R15']['nets'].values())=={'RF_ACTIVE','GND'})
check('USB-C has two independent 5.1k CC pull-downs',all(parts[r]['value']=='5.1k' and set(parts[r]['nets'].values())=={net,'GND'} for r,net in [('R20','CC1'),('R21','CC2')]))
check('USB/bay input diodes have cathodes on the common rail',all(parts[r]['nets']['1']=='5V_SYS' for r in ('D1','D2')))
check('Seventh DAC branch and all buzzer parts are DNP',all(parts[r]['dnp'] for r in ['R8','BZ1','Q1','R37','R38','R39']))
check('Filter variant defaults to bypass only',parts['FL1']['dnp'] and not parts['R42']['dnp'])
weights=[8200,3900,2000,1000,470,240]
g=sum(1/r for r in weights)
white=3.3*g/(g+1/200+1/75)
check('Retained DAC loaded full-scale is within 0.9–1.1 V at 3.3 V',.9<white<1.1)
levels={}
for name,r in [('PRESS',22),('UP',1000),('DOWN',2200),('LEFT',4700),('RIGHT',10000),('RELEASED',math.inf)]:
    low=22000 if math.isinf(r) else 1/(1/22000+1/r)
    levels[name]=3.3*low/(10000+low)
check('Joystick levels are distinct and released voltage is below 2.5 V',min(abs(a-b) for i,a in enumerate(levels.values()) for j,b in enumerate(levels.values()) if i!=j)>.20 and levels['RELEASED']<2.5)
drc=json.loads((ROOT/'validation/drc.json').read_text())
check('KiCad DRC has no violations',not drc['violations'])
check('PCB is fully connected',not drc['unconnected_items'])
check('Schematic and PCB parity is exact',not drc['schematic_parity'])
erc=json.loads((ROOT/'validation/erc.json').read_text())
violations=[x for s in erc['sheets'] for x in s['violations']]
check('ERC has only seven expected singleton IQ-label warnings',len(violations)==7 and all(x['type']=='isolated_pin_label' and x['severity']=='warning' for x in violations))
rf_names={'ANTENNA','LNA_IN_AC','LNA_IN','LNA_DRAIN','LNA_OUT_AC','FILTER_OUT','RF_SOC','RF_BYPASS','RF_MATCH','ANT_5G'}
rf_layers=Counter(t.GetLayerName() for t in b.GetTracks() if not isinstance(t,p.PCB_VIA) and t.GetNetname() in rf_names)
rf_vias=sum(isinstance(t,p.PCB_VIA) and t.GetNetname() in rf_names for t in b.GetTracks())
report={'status':'CAD consistency passed; fabrication release blocked','checks':checks,'component_count':len(parts),
        'gpio_roles':roles,'loaded_dac_white_volts':white,'joystick_nominal_volts':levels,
        'rf_route_audit':{'segments_by_layer':dict(rf_layers),'signal_vias':rf_vias,'impedance_approved':False},
        'usb_impedance_approved':False,'firmware_board_integration_complete':False,'physically_tested':False,'order_approved':False}
(ROOT/'validation/design-audit.json').write_text(json.dumps(report,indent=2)+'\n')
print(len(checks),'CAD consistency checks passed; RF/USB/fabrication/firmware qualification remains open')

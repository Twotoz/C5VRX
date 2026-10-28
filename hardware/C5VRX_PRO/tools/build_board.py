"""Rebuild C5VRX PRO review CAD. Run with KiCad 10's Python (pcbnew).

SPDX-License-Identifier: GPL-3.0-only
Extends C5VRX by Twotoz and the C5VRX contributors. Vendor dimensions and
third-party footprint provenance are recorded in ../SOURCES.md.
"""
from pathlib import Path
import csv
import json
import math
import os
import uuid
import pcbnew as p

ROOT = Path(__file__).resolve().parents[1]
NAME = 'C5VRX_PRO'
KICAD = Path(os.environ.get('KICAD_INSTALL', r'C:\Program Files\KiCad\10.0'))
LIB = KICAD / 'share/kicad/footprints'
PRETTY = ROOT / 'PRO.pretty'
PRETTY.mkdir(exist_ok=True)
for folder in ('exports', 'validation', 'models'):
    (ROOT / folder).mkdir(exist_ok=True)

def uid(key):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, 'https://github.com/Twotoz/C5VRX/PRO/' + key))

def q(s):
    return json.dumps(str(s))

def pt(x,y):
    return p.VECTOR2I(p.FromMM(x), p.FromMM(y))

def custom(name, pads, body, model=None):
    """Pads are physical top-view coordinates in mm, no inferred numbering."""
    w,h = body
    text = f'(footprint {q(name)} (version 20260206) (generator "pcbnew") (layer "F.Cu") (attr smd)'
    text += f'(property "Reference" "REF**" (at 0 {-h/2-1} 0) (layer "F.SilkS") (effects (font (size 0.65 0.65) (thickness 0.1))))'
    text += f'(property "Value" {q(name)} (at 0 {h/2+1} 0) (layer "F.Fab") (effects (font (size 0.65 0.65) (thickness 0.1))))'
    for layer, margin, thick in [('F.Fab',0,0.1),('F.CrtYd',0.25,0.05)]:
        text += f'(fp_rect (start {-w/2-margin} {-h/2-margin}) (end {w/2+margin} {h/2+margin}) (stroke (width {thick}) (type solid)) (fill none) (layer "{layer}"))'
    for num,x,y,sx,sy in pads:
        text += f'(pad {q(num)} smd rect (at {x} {y}) (size {sx} {sy}) (layers "F.Cu" "F.Paste" "F.Mask"))'
    if model:
        text += f'(model "${{KIPRJMOD}}/models/{model}.step" (offset (xyz 0 0 0)) (scale (xyz 1 1 1)) (rotate (xyz 0 0 0)))'
    text += ')'
    (PRETTY / (name+'.kicad_mod')).write_text(text, encoding='utf8')

# Qorvo Rev F page 9: 0.43 x 0.25 lands, 0.5 pitch, EP 0.8 x 1.6.
custom('QPL9504_DFN8', [(str(i+1),-.915,-.75+i*.5,.43,.25) for i in range(4)] +
       [(str(8-i),.915,-.75+i*.5,.43,.25) for i in range(4)] + [('9',0,0,.8,1.6)], (2,2), 'QPL9504')
# pSemi drawing DOC-01882: pin 1 is BOTTOM LEFT in top view.
custom('PE42422_QFN12',
       [(str(i+1),-.5+i*.5, .9625,.25,.475) for i in range(3)] +
       [(str(i+4),.9625,.5-i*.5,.475,.25) for i in range(3)] +
       [(str(i+7),.5-i*.5,-.9625,.25,.475) for i in range(3)] +
       [(str(i+10),-.9625,-.5+i*.5,.475,.25) for i in range(3)] + [('13',0,0,1.1,1.1)], (2.4,2.4), 'PE42422')
# Murata CA180-01: two end RF electrodes and two side ground electrodes.
custom('LFB215G37', [('1',-1,0,.6,.6),('2',0,-.625,.8,.45),('3',1,0,.6,.6),('4',0,.625,.8,.45)], (2.4,1.7), 'LFB215G37')

parts=[]
def add(ref,value,fp,nets,x,y,rot=0,side='F',mpn='',maker='',dnp=False,note='',block='SOC',pins=None,url=''):
    obj=dict(ref=ref,value=value,footprint=fp,nets={str(k):v for k,v in nets.items()},x=x,y=y,rotation=rot,side=side,
             mpn=mpn,manufacturer=maker,dnp=dnp,note=note,block=block,pins=pins or {},url=url,uuid=uid(ref))
    parts.append(obj)
    return obj

RFP='Resistor_SMD:R_0402_1005Metric'
CFP='Capacitor_SMD:C_0402_1005Metric'
def res(ref,val,a,b,x,y,rot=0,dnp=False,block='SOC',precision=False,note=''):
    code={'8.2k':'8K2','3.9k':'3K9','2k':'2K','1k':'1K','470':'470R','240':'240R','200':'200R','15k':'15K',
          '10k':'10K','5.1k':'5K1','100k':'100K','1M':'1M','0':'0R','22':'22R','100':'100R','2.2k':'2K2',
          '4.7k':'4K7','22k':'22K','47k':'47K'}[val]
    mpn=('RT0402BRD07'+code+'L') if precision else ('RC0402'+('JR' if val=='0' else 'FR')+'-07'+code+'L')
    return add(ref,val,RFP,{'1':a,'2':b},x,y,rot,mpn=mpn,maker='Yageo',dnp=dnp,block=block,
               note=('0.1% thin film; ' if precision else '1%; ')+note)

def cap(ref,val,a,b,x,y,rot=0,dnp=False,fp=CFP,block='SOC',mpn='',note=''):
    return add(ref,val,fp,{'1':a,'2':b},x,y,rot,mpn=mpn,maker='Murata',dnp=dnp,block=block,note=note)

soc_nets={1:'3V3',2:'GND',3:'3V3',4:'XTAL_N',5:'XTAL_P',6:'3V3',7:'EN',8:'3V3',9:'IQ_Q1',10:'IQ_Q0',
          11:'IQ_Q3',12:'IQ_I2',13:'IQ_I3',14:'IQ_I1',15:'JOY_ADC',16:'BUZZ_PWM',17:'DAC4',18:'DAC5',19:'DAC7',
          20:'DAC2',21:'DAC3',22:'USB_DM',23:'USB_DP',24:'3V3',25:None,26:'FLASH_CS',27:'FLASH_D1',28:'FLASH_D2',
          29:'VDD_SPI',30:'FLASH_D3',31:'FLASH_CLK',32:'FLASH_D0',33:'DAC0',34:'DAC1',35:'IQ_Q2',36:'RF_ACTIVE',
          37:'IQ_I0',38:'BOOT',39:'3V3',40:'3V3_RF',41:'3V3_RF',42:'ANT_2G',43:'GND',44:'3V3',45:'3V3',46:'3V3',
          47:'GND',48:'ANT_5G',49:'GND'}
soc_pins={1:'VDDA6',2:'GND',3:'VDDA7',4:'XTAL_N',5:'XTAL_P',6:'VDDA8',7:'CHIP_PU',8:'VDDPST1',
          **{i+9:('GPIO'+str(i)) for i in range(15)},24:'VDDPST2',25:'SPICS1_INTERNAL',26:'SPICS0',27:'SPIQ',28:'SPIWP',
          29:'VDD_SPI',30:'SPIHD',31:'SPICLK',32:'SPID',**{i+10:'GPIO'+str(i) for i in range(23,29)},
          39:'VDDPST3',40:'VDDA1',41:'VDDA2',42:'ANT_2G',43:'GND',44:'VDDA3',45:'VDDA4',46:'VDDA5',47:'GND',48:'ANT_5G',49:'EP'}
# Correct IO numbering 9..23: GPIO0..14, including UART and USB.
add('U1','ESP32-C5HR2','Package_DFN_QFN:QFN-48-1EP_6x6mm_P0.4mm_EP4.6x4.6mm',soc_nets,119,115,
    mpn='ESP32-C5HR2',maker='Espressif',pins=soc_pins,url='https://www.espressif.com/sites/default/files/documentation/esp32-c5_datasheet_en.pdf')
add('U2','W25Q64JVSSIQ','Package_SO:SOIC-8_3.9x4.9mm_P1.27mm',
    {1:'FLASH_CS',2:'FLASH_D1',3:'FLASH_D2',4:'GND',5:'FLASH_D0',6:'FLASH_CLK',7:'FLASH_D3',8:'VDD_SPI'},128,117,
    mpn='W25Q64JVSSIQ',maker='Winbond',block='FLASH',pins={1:'CS#',2:'DO/IO1',3:'WP#/IO2',4:'GND',5:'DI/IO0',6:'CLK',7:'HOLD#/IO3',8:'VCC'})
res('R10','10k','FLASH_CS','VDD_SPI',126,112,block='FLASH')
cap('C10','100n','VDD_SPI','GND',128,112,block='FLASH',mpn='GRM155R71C104KA88D')
cap('C11','1u','VDD_SPI','GND',130,112,block='FLASH',mpn='GRM155R61A105KE15D')

add('Y1','48MHz / CL18pF','Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm',
    {1:'XTAL_P',2:'GND',3:'XTAL_N',4:'GND'},110.8,113.7,mpn='ABM8-48.000MHZ-B2-T',maker='Abracon',block='CLOCK')
cap('C12','27p','XTAL_P','GND',108.5,111.5,mpn='GRM1555C1H270JA01D',block='CLOCK',note='Initial CL value; tune with measured stray capacitance')
cap('C13','27p','XTAL_N','GND',113.2,113.5,90,mpn='GRM1555C1H270JA01D',block='CLOCK')
res('R11','10k','3V3','EN',115,119.7,block='RESET')
cap('C14','1u','EN','GND',115,121.1,block='RESET',mpn='GRM155R61A105KE15D')
res('R12','10k','3V3','BOOT',121,120,block='RESET')
res('R13','10k','3V3','IQ_I0',121,121.5,block='RESET',note='GPIO27 high for USB download boot; keep loopback loading small')
res('R14','100k','BUZZ_PWM','GND',135,113,block='RESET',note='GPIO7 JTAG strap, no LED loading')
res('R15','100k','RF_ACTIVE','GND',123,109.5,block='RF',note='Fail to passive bypass during reset')

# Local decoupling at every supply group. RF supply pins 40/41 use a series inductor.
for i,(x,y) in enumerate([(114.9,116.2),(114.9,114.8),(114.9,112),(119.6,119.5),(122.9,112.7),(121.8,110.5),(120.4,110.5)]):
    cap('C'+str(20+i),'100n','3V3','GND',x,y,90 if x==114.9 else 0,mpn='GRM155R71C104KA88D')
cap('C27','10u','3V3','GND',123.3,121.3,90,fp='Capacitor_SMD:C_0603_1608Metric',mpn='GRM188R61A106KE69D')
add('L2','2nH','Inductor_SMD:L_0402_1005Metric',{1:'3V3',2:'3V3_RF'},124,113.5,90,mpn='LQG15HS2N0S02D',maker='Murata',note='ESP RF supply initial tuning value')
cap('C28','1u','3V3_RF','GND',124,115,90,mpn='GRM155R61A105KE15D')
cap('C29','100n','3V3_RF','GND',124,116.5,90,mpn='GRM155R71C104KA88D')

add('J1','FATSHARK BAY','PRO:FatShark_Header_1x09',
    {1:'5V_BAY',2:None,3:'GND',4:None,5:None,6:'VIDEO',7:None,8:None,9:None},140.95,126.26,180,
    mpn='CUSTOM_1x09_P2.54_BAY',maker='PCBWay sourcing',block='BAY',pins={1:'5V',2:'RSSI_NC',3:'GND',4:'AUDIO_R_NC',5:'AUDIO_L_NC',6:'CVBS',7:'B2_NC',8:'B1_NC',9:'B0_NC'})
add('J2','PJ-320D','Connector_Audio:Jack_3.5mm_PJ320D_Horizontal',{'T':'VIDEO','R1':'VIDEO','R2':None,'S':'GND'},108.5,123.3,
    mpn='PJ-320D',maker='CUI-compatible sourcing',block='VIDEO',note='Use supplied mating/dimension drawing; qualify vendor before purchase')
add('J3','SMA 50R','Connector_Coaxial:SMA_Amphenol_132289_EdgeMount',{'1':'ANTENNA','2':'GND'},104.445,105,180,
    mpn='132289',maker='Amphenol RF',block='RF')
add('J4','USB-C','Connector_USB:USB_C_Receptacle_HRO_TYPE-C-31-M-12',
    {'A1':'GND','A4':'USB_5V','A5':'CC1','A6':'USB_CONN_DP','A7':'USB_CONN_DM','A8':None,'A9':'USB_5V','A12':'GND',
     'B1':'GND','B4':'USB_5V','B5':'CC2','B6':'USB_CONN_DP','B7':'USB_CONN_DM','B8':None,'B9':'USB_5V','B12':'GND','SH':'GND'},103,115,270,
    mpn='TYPE-C-31-M-12',maker='HRO',block='USB')
res('R20','5.1k','CC1','GND',111,116.5,block='USB')
res('R21','5.1k','CC2','GND',111,118,block='USB')
res('R22','22','USB_CONN_DM','USB_DM',113.8,118.2,block='USB')
res('R23','22','USB_CONN_DP','USB_DP',113.8,119.6,block='USB')
add('U6','USBLC6-2SC6','Package_TO_SOT_SMD:SOT-23-6',
    {1:'USB_CONN_DM',2:'GND',3:'USB_CONN_DP',4:'USB_CONN_DP',5:'USB_5V',6:'USB_CONN_DM'},111,114,
    mpn='USBLC6-2SC6',maker='STMicroelectronics',block='USB')
add('F1','750mA PTC','Fuse:Fuse_1206_3216Metric',{1:'5V_BAY',2:'5V_FUSED'},135.5,126.2,
    mpn='1206L075/13.2WR',maker='Littelfuse',block='POWER')
for ref,a,x,y in [('D1','5V_FUSED',132,126),('D2','USB_5V',117,125)]:
    add(ref,'PMEG2010ER','Diode_SMD:Nexperia_CFP3_SOD-123W',{1:'5V_SYS',2:a},x,y,mpn='PMEG2010ER,115',maker='Nexperia',block='POWER',pins={1:'K',2:'A'})
add('U7','TLV75733PDRVR','Package_DFN_QFN:DFN-6-1EP_2x2mm_P0.65mm_EP1x1.6mm',
    {1:'3V3',2:None,3:'GND',4:'5V_SYS',5:None,6:'5V_SYS',7:'GND'},121,125,
    mpn='TLV75733PDRVR',maker='Texas Instruments',block='POWER',pins={1:'OUT',2:'NC',3:'GND',4:'EN',5:'NC',6:'IN',7:'EP'})
cap('C30','10u','5V_SYS','GND',118.7,127,fp='Capacitor_SMD:C_0603_1608Metric',mpn='GRM188R61A106KE69D',block='POWER')
cap('C31','10u','3V3','GND',123.5,125,90,fp='Capacitor_SMD:C_0603_1608Metric',mpn='GRM188R61A106KE69D',block='POWER')
cap('C32','100n','3V3','GND',125,125,90,mpn='GRM155R71C104KA88D',block='POWER')
cap('C33','1u','USB_5V','GND',108,110.9,mpn='GRM155R61A105KE15D',block='POWER')

for ref,net,x,y in [('SW1','BOOT',125,102.4),('SW2','EN',125,106.5)]:
    add(ref,'BOOT' if net=='BOOT' else 'RESET','Button_Switch_SMD:SW_SPST_B3U-1000P',
        {1:net,2:'GND'},x,y,mpn='B3U-1000P',maker='Omron',block='RESET')
add('SW3','5-WAY JOYSTICK','PRO:SW_SKRHABE010_SMD',
    {1:'JOY_UP',2:'JOY_PRESS',3:'JOY_DOWN',4:'JOY_LEFT',5:'GND',6:'JOY_RIGHT','MP':'GND'},133.4,105.7,
    mpn='SKRHABE010',maker='ALPS Alpine',block='JOY')
# Bias at 2.2 V when released: below C5 ADC 12 dB full scale. Distinct levels.
res('R30','10k','3V3','JOY_ADC',129.7,112.5,block='JOY')
res('R31','22k','JOY_ADC','GND',129.7,114,block='JOY')
for ref,val,net,x,y in [('R32','1k','JOY_UP',132,112.5),('R33','2.2k','JOY_DOWN',132,114),
                          ('R34','4.7k','JOY_LEFT',134.3,112.5),('R35','10k','JOY_RIGHT',134.3,114),('R36','22','JOY_PRESS',136.6,112.5)]:
    res(ref,val,'JOY_ADC',net,x,y,block='JOY')
cap('C34','100n','JOY_ADC','GND',136.6,114,mpn='GRM155R71C104KA88D',block='JOY')

for i,val in enumerate(['8.2k','3.9k','2k','1k','470','240']):
    res('R'+str(i+1),val,'DAC'+str(i),'VIDEO',132.5+(i%2)*3,118.0+(i//2)*1.5,precision=True,block='VIDEO')
res('R7','200','VIDEO','GND',137.3,123.2,90,precision=True,block='VIDEO')
res('R8','15k','DAC7','VIDEO',132.5,122.5,dnp=True,precision=True,block='VIDEO',note='Future new LSB; current firmware is six bit')
cap('C1','470p','VIDEO','GND',135.5,122.5,dnp=True,block='VIDEO',mpn='GRM1555C1H471JA01D',note='Optional, verify video bandwidth before fitting')

add('BZ1','PASSIVE PIEZO','Buzzer_Beeper:Buzzer_Murata_PKLCS1212E',{1:'3V3',2:'BUZZ_LOW'},132.5,119,side='B',
    mpn='PKLCS1212E4001-R1',maker='Murata',dnp=True,block='BUZZ',note='Optional reverse side SMD part; verify bay depth')
add('Q1','2N7002','Package_TO_SOT_SMD:SOT-23',{1:'BUZZ_GATE',2:'GND',3:'BUZZ_LOW'},136,126,side='B',
    mpn='2N7002,215',maker='Nexperia',dnp=True,block='BUZZ')
res('R37','100','BUZZ_PWM','BUZZ_GATE',128,125,dnp=True,block='BUZZ')
res('R38','100k','BUZZ_GATE','GND',128,126.5,dnp=True,block='BUZZ')

# RF: two switches provide a passive calibration/strong-signal path.
for ref,common,x,rot in [('U3','ANTENNA',109,180),('U5','RF_SOC',121,0)]:
    add(ref,'PE42422','PRO:PE42422_QFN12',{1:'GND',2:'LNA_IN_AC' if ref=='U3' else 'FILTER_OUT',3:'GND',4:'GND',5:common,
        6:'GND',7:'GND',8:'RF_BYPASS',9:'GND',10:'RF_ACTIVE',11:'3V3',12:'3V3',13:'GND'},x,105.5,rot,
        mpn='PE42422A-X',maker='pSemi',block='RF',pins={2:'RF2_LNA',5:'RFC',8:'RF1_BYPASS',10:'V1',11:'LS',12:'VDD',13:'EP'})
add('U4','QPL9504','PRO:QPL9504_DFN8',{1:'LNA_BIAS',2:'LNA_IN',3:'GND',4:'GND',5:'GND',6:'GND',7:'LNA_DRAIN',8:'GND',9:'GND'},
    115,106.2,mpn='QPL9504TR7',maker='Qorvo',block='RF',pins={1:'VBIAS',2:'RFIN',5:'SHUTDOWN',7:'RFOUT/VDD',9:'EP'})
cap('C40','5.6p','LNA_IN_AC','LNA_IN',111.8,106.1,block='RF',mpn='GJM1555C1H5R6CB01D')
cap('C41','0.2p','LNA_IN','GND',113.4,107.9,90,block='RF',mpn='GJM1555C1HR20BB01D')
cap('C42','2.7p','LNA_DRAIN','LNA_OUT_AC',117.4,105.95,block='RF',mpn='GJM1555C1H2R7CB01D')
cap('C43','0.5p','LNA_DRAIN','GND',116.6,107.9,90,block='RF',mpn='GJM1555C1HR50BB01D')
add('L3','18nH','Inductor_SMD:L_0603_1608Metric',{1:'5V_LNA',2:'LNA_DRAIN'},116.7,103.6,90,
    mpn='0603CS-18NXJRU',maker='Coilcraft',block='RF')
res('R40','3.9k','5V_LNA','LNA_BIAS',113.7,103.5,90,block='RF')
res('R41','0','5V_SYS','5V_LNA',111.7,102.2,block='RF')
for ref,val,x in [('C44','1n',112),('C45','100n',114),('C46','1n',118)]:
    cap(ref,val,'5V_LNA','GND',x,101.2,block='RF',mpn='GRM155R71H102KA01D' if val=='1n' else 'GRM155R71C104KA88D')
cap('C47','100n','3V3','GND',109,102,block='RF',mpn='GRM155R71C104KA88D')
cap('C48','100n','3V3','GND',121,102,block='RF',mpn='GRM155R71C104KA88D')
# Optional filter is isolated by two DNP links. Default bypass remains short.
add('FL1','4.90-5.85GHz BPF','PRO:LFB215G37',{1:'FILTER_IN',2:'GND',3:'FILTER_TERM',4:'GND'},118,109,
    mpn='LFB215G37SG8A180',maker='Murata',block='RF',dnp=True,note='Optional restricted-band assembly; never fit with R42')
res('R42','0','LNA_OUT_AC','FILTER_OUT',119,106.5,block='RF',note='Remove when FL1/R43/R44 are fitted')
res('R43','0','LNA_OUT_AC','FILTER_IN',116,109.5,dnp=True,block='RF')
res('R44','0','FILTER_TERM','FILTER_OUT',120,109.5,dnp=True,block='RF')
# 5G chip-side matching: board-dependent, initial values only, must VNA tune.
cap('C50','1p','ANT_5G','GND',115.7,110.9,90,block='MATCH',mpn='GRM0335C1H1R0CA01D',fp='Capacitor_SMD:C_0201_0603Metric',note='Chip-side stub/match requires RF review')
add('L4','1nH','Inductor_SMD:L_0201_0603Metric',{1:'ANT_5G',2:'RF_MATCH'},117.1,110.4,0,mpn='LQP03TN1N0B02D',maker='Murata',block='MATCH')
cap('C51','0.5p','RF_MATCH','GND',118.5,111.1,90,block='MATCH',mpn='GRM0335C1HR50BA01D',fp='Capacitor_SMD:C_0201_0603Metric')
res('R45','0','RF_MATCH','RF_SOC',122.8,107.4,90,block='MATCH')
# 2.4 GHz port remains matched and terminated for vendor PHY calibration.
cap('C52','2p','ANT_2G','GND',126,110,block='MATCH',fp='Capacitor_SMD:C_0201_0603Metric',mpn='GRM0335C1H2R0CA01D')
add('L5','2.7nH','Inductor_SMD:L_0201_0603Metric',{1:'ANT_2G',2:'RF_2G_TERM'},126,108.8,mpn='LQP03TN2N7B02D',maker='Murata',block='MATCH')
add('R46','49.9','Resistor_SMD:R_0603_1608Metric',{1:'RF_2G_TERM',2:'GND'},128,109,
    mpn='RC0603FR-0749R9L',maker='Yageo',block='MATCH',note='0.1 W; only incidental 2G calibration, not continuous TX')

# USB-C replaces the large DIY AV jack; bay pin 6 remains the video interface.
parts=[c for c in parts if c['ref'] not in ('J2','R43','R44')]
add('TP1','CVBS','TestPoint:TestPoint_Pad_D1.5mm',{1:'VIDEO'},103,121,mpn='PCB_FEATURE',maker='PCBWay',block='VIDEO')
locations={
 'J3':(104.8,104.8,0),'J4':(108,125.5,0),'U3':(110.8,105.5,180),'U5':(123,105.5),'SW1':(126,102.4),'SW2':(115,121.1,90),
 'U6':(108.5,118),'Y1':(110.5,114.5),'C12':(106.7,113),'C13':(110.5,112,0),
 'R20':(103.5,118.5),'R21':(105.5,116.3),'R22':(112,118),'R23':(112,119.7),'C33':(103.5,114.5),
 'C20':(114.2,116.7),'C21':(114.2,114.6),'C22':(114.2,112.4),'C24':(123.8,113,90),
 'C25':(121,110.4),'C26':(123,110.4),'L2':(124,115,0),'C28':(123.8,117,90),'C29':(123.8,119,90),
 'U2':(129,117),'C10':(128,112.5),'C11':(130,112.5),'R10':(126,112.5),
 'R11':(116.5,121),'C14':(116.5,122.7),'R12':(119.5,122),'R13':(121.8,122),'R14':(138,116),
 'R30':(132,112.5),'R31':(132,114.2),'R32':(134.2,112.5),'R33':(134.2,114.2),
 'R34':(136.4,112.5),'R35':(136.4,114.2),'R36':(138,112.5),'C34':(138,114.2),'R31':(134.2,115.8),
 'R1':(134.5,118),'R2':(137.3,118),'R3':(134.5,119.7),'R4':(137.3,119.7),
 'R5':(134.5,121.4),'R6':(137.3,121.4),'R7':(137.8,123.6),'R8':(134.5,123.1),'C1':(132,123.1),
 'D1':(130.8,126),'F1':(135.5,126.5),
 'C40':(112.8,103.3,90),'C41':(113.3,108.4),'C42':(117.3,105.95),'C43':(116.7,108.5),'L3':(116.7,103,90),'C46':(119,101.2),'C47':(109.8,101),
 'R40':(113.7,103,90),'FL1':(119.9,106.1),'R42':(119.9,108.1),'R45':(124.5,109,90),
 'C50':(115.7,110.6,90),'L4':(117.1,110.4,0),'C51':(118.6,110.5,0),'R15':(126.5,110),
 'C52':(128.5,110),'L5':(126.5,111.5),'R46':(131,111.1),
 'Q1':(121,105),'BZ1':(130,117),'C27':(125.2,121.8),'R37':(127.5,122.5),'R38':(127.5,124),
 'TP1':(101.5,119.7),'R34':(136.4,111.2),'R35':(136.4,115.8),'R14':(138,117),
 'R11':(118.5,120.3),'C14':(118.5,123.6),'C40':(112.3,102.2,90),'C44':(113.5,100.9),
 'C45':(116,100.8),'R41':(112,110.3),'C47':(110,108.8),
 'C14':(118.4,122.9),'R11':(117.2,120.3),'C44':(114,100.8),
 'R11':(117.7,119.8),'R12':(119.5,121.1),'C23':(121.8,120.2),
 'C24':(119,111,0),'C28':(121,111,0),'C29':(121,121,0),'L2':(119,109,0),'BZ1':(132.5,119),
 'R11':(116,119.8),
}
res('R39','1k','3V3','BUZZ_LOW',130,123.5,dnp=True,block='BUZZ',note='Piezo discharge path; fit with optional buzzer')
for c in parts:
    if c['ref'] in locations:
        pos=locations[c['ref']];c['x'],c['y']=pos[:2]
        if len(pos)==3:c['rotation']=pos[2]
    if c['ref']=='FL1':c['nets'].update({'1':'LNA_OUT_AC','3':'FILTER_OUT'})
    if c['ref']=='J3':
        c['footprint']='Connector_Coaxial:SMA_Amphenol_132134_Vertical'
        c['mpn']='132134';c['note']='Vertical SMA female, through-hole, antenna normal to PCB'
    if c['ref'] in ('C24','C28','C29','L2','R11'):c['side']='B'
    if c['ref'] in ('U6','J4'):
        c['nets'].update({k:v for k,v in c['nets'].items()})

# Board and shared inherited mechanical interfaces.
source=p.LoadBoard(str(ROOT.parent/'C5VRX_JACK_DIY_V3/C5VRX_JACK_DIY_V3.kicad_pcb'))
board=p.BOARD()
board.SetCopperLayerCount(4)
board.GetDesignSettings().SetBoardThickness(p.FromMM(1.6))
for shape in source.GetDrawings():
    if shape.GetLayer()==p.Edge_Cuts:
        board.Add(shape.Duplicate())
header=source.FindFootprintByReference('J1')
p.FootprintSave(str(PRETTY),header)
# FootprintSave names by FPID, so explicitly save the inherited footprint.
header.SetFPID(p.LIB_ID('PRO','FatShark_Header_1x09'))
p.FootprintSave(str(PRETTY),header)

nets={}
for name in sorted({v for c in parts for v in c['nets'].values() if v}):
    n=p.NETINFO_ITEM(board,name)
    board.Add(n)
    nets[name]=n

root_uuid=uid('schematic')
fps={}
for c in parts:
    lib,name=c['footprint'].split(':')
    base=PRETTY if lib=='PRO' else LIB/(lib+'.pretty')
    fp=p.FootprintLoad(str(base),name)
    if fp is None:
        raise RuntimeError('Missing footprint '+c['footprint'])
    fp.SetReference(c['ref']); fp.SetValue(c['value'])
    fp.SetFPID(p.LIB_ID(lib,name))
    fp.SetUuid(p.KIID(c['uuid']))
    path=p.KIID_PATH(); path.push_back(p.KIID(root_uuid)); path.push_back(p.KIID(c['uuid']))
    fp.SetPath(path)
    # No hidden stale nets in copied footprints. Require exact pad coverage.
    physical={pad.GetNumber() for pad in fp.Pads() if pad.GetNumber()}
    missing=physical-set(c['nets'])
    if missing:
        raise RuntimeError(c['ref']+' unmapped physical pads '+str(missing))
    for pad in fp.Pads():
        net=c['nets'].get(pad.GetNumber())
        if net:
            pad.SetNet(nets[net])
        else:
            pad.SetNetCode(0)
    fp.SetPosition(pt(c['x'],c['y']))
    fp.SetOrientation(p.EDA_ANGLE(c['rotation'],p.DEGREES_T))
    board.Add(fp)
    if c['side']=='B': fp.Flip(fp.GetPosition(),False)
    fp.SetDNP(c['dnp'])
    fp.Reference().SetVisible(False); fp.Value().SetVisible(False)
    own_model={'U1':'ESP32-C5HR2','U3':'PE42422','U4':'QPL9504','U5':'PE42422','U7':'TLV75733',
               'SW1':'B3U1000P','SW2':'B3U1000P','SW3':'SKRHABE010','J1':'FatSharkBay','J4':'USB-C','J3':'SMA-Vertical'}.get(c['ref'])
    if own_model:
        fp.Models().clear()
        m=p.FP_3DMODEL();m.m_Filename='${KIPRJMOD}/models/'+own_model+'.step'
        if c['ref']=='J4':m.m_Rotation=p.VECTOR3D(0,0,180)
        fp.Models().push_back(m)
    if c['ref']=='J2':
        fp.Models().clear()
        m=p.FP_3DMODEL();m.m_Filename='${KIPRJMOD}/models/PJ-320D.step';fp.Models().push_back(m)
    if c['ref']=='SW3':
        fp.Models().clear()
        m=p.FP_3DMODEL();m.m_Filename='${KIPRJMOD}/models/SKRHABE010.step';fp.Models().push_back(m)
        # Antmicro footprint includes dense cosmetic graphics; retain fab,
        # courtyard and pads, omit imported production text outside the body.
        for g in list(fp.GraphicalItems()):
            if isinstance(g,p.PCB_TEXT) or g.GetLayer()==p.F_SilkS: g.SetLayer(p.F_Fab)
    for g in list(fp.GraphicalItems()):
        if g.GetLayer()==p.F_SilkS:g.SetLayer(p.F_Fab)
        if g.GetLayer()==p.B_SilkS:g.SetLayer(p.B_Fab)
    # Vendor copies carry our silkscreen/model changes in a local library,
    # so the board does not silently diverge from the referenced footprint.
    if lib!='PRO':
        local_name=lib+'__'+name
        c['source_footprint']=c['footprint'];c['footprint']='PRO:'+local_name
        fp.SetFPID(p.LIB_ID('PRO',local_name))
        p.FootprintSave(str(PRETTY),fp)
    elif c['ref']=='SW3':
        p.FootprintSave(str(PRETTY),fp)
    fps[c['ref']]=fp

def line(a,b,net,width=.18,layer=p.F_Cu):
    t=p.PCB_TRACK(board);t.SetStart(pt(*a));t.SetEnd(pt(*b));t.SetWidth(p.FromMM(width));t.SetLayer(layer);t.SetNet(nets[net]);board.Add(t)

def padxy(ref,num):
    pad=fps[ref].FindPadByNumber(str(num));return tuple(p.ToMM(pad.GetPosition()))

def wire(ref,num,other,onum,net,via_points=(),width=.2,layer=p.F_Cu):
    points=[padxy(ref,num),*via_points,padxy(other,onum)]
    for a,b in zip(points,points[1:]):line(a,b,net,width,layer)

def text(s,x,y,size=.7,layer=p.F_SilkS,angle=0):
    t=p.PCB_TEXT(board);t.SetText(s);t.SetPosition(pt(x,y));t.SetTextSize(pt(size,size));t.SetTextThickness(p.FromMM(.12));t.SetLayer(layer);t.SetTextAngle(p.EDA_ANGLE(angle,p.DEGREES_T));board.Add(t)

text('REV A',126,127.5,.6)
text('BOOT',126,100.6,.6)
text('RESET',116.5,127,.6)
text('USB-C',108,119.7,.6)
text('5V',138.5,126.2,.6)
text('VIDEO',138,113.5,.55,angle=90)
text('6 BIT + DNP 7',134,124.5,.6)
text('C5VRX / Twotoz',120,113,1,p.B_SilkS)
text('PRO REV A - REVIEW',121,115,.8,p.B_SilkS)
text('42.51 x 28.80 mm',120,117,.7,p.B_SilkS)
text('OPTIONAL BUZZER',132.5,125,.55,p.B_SilkS)

# The RF launch starts straight on F.Cu; subsequent RF routing needs dedicated
# impedance/layout review, not a blanket autorouter approval.
wire('J3',1,'U3',5,'ANTENNA',width=.30)

# Write an explicit, reproducible review source. Routing and release artifacts
# are generated separately; this script never fabricates a passing DRC report.
p.SaveBoard(str(ROOT/(NAME+'.kicad_pcb')),board)
project={'meta':{'filename':NAME+'.kicad_pro','version':1},'board':{'design_settings':{'rules':{
    'min_copper_clearance':.127,'min_track_width':.127,'min_via_diameter':.4,'min_through_hole_diameter':.2,
    'min_hole_clearance':.2,'min_copper_edge_clearance':.25,'min_silk_clearance':.15,'min_silk_text_height':.5,
    'min_silk_text_thickness':.08,'min_text_height':.5,'min_text_thickness':.08},'defaults':{'board_outline_line_width':.05}}},
    'net_settings':{'meta':{'version':3},'classes':[{'name':'Default','clearance':.127,'track_width':.1524,
       'via_diameter':.4,'via_drill':.2,'diff_pair_width':.1524,'diff_pair_gap':.15,'diff_pair_via_gap':.25,'wire_width':6,'bus_width':12}]}}
(ROOT/(NAME+'.kicad_pro')).write_text(json.dumps(project,indent=2)+'\n')
(ROOT/'design.json').write_text(json.dumps(dict(name=NAME,root_uuid=root_uuid,parts=parts),indent=2)+'\n',encoding='utf8')
with (ROOT/'BOM.csv').open('w',newline='',encoding='utf8') as f:
    fields=['Reference','Value','Manufacturer','MPN','Footprint','Populate','Note','Datasheet']
    w=csv.DictWriter(f,fields);w.writeheader()
    for c in parts:
        w.writerow(dict(Reference=c['ref'],Value=c['value'],Manufacturer=c['manufacturer'],MPN=c['mpn'],Footprint=c['footprint'],Populate='DNP' if c['dnp'] else 'YES',Note=c['note'],Datasheet=c['url']))
print('Generated',len(parts),'components,',len(nets),'nets;',ROOT/(NAME+'.kicad_pcb'))

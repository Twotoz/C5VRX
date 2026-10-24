#!/usr/bin/env python3
"""C5VRX/Twotoz: reproducible symbol triage, not functional PHY proof.

Input: riscv32-esp-elf-nm --defined-only exact-libphy.a and optional final ELF.
Library names are hints only; a linked definition does not prove execution.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re

RULES=[
    ('bluetooth_or_154',r'(^|_)(bt|ble|154|zb)(_|$)'),
    ('transmit_or_loopback',r'(^|_)(tx|txiq|txdc|dpd|pa|pacal|loopback|lb)(_|$)'),
    ('receive_iq_dc',r'rxiq|rxdc|dco|dc_iq|rxbb_dc|rx_dco'),
    ('receive_gain',r'agc|rxgain|rx_gain|rfrx_gain|bb_gain'),
    ('filter_adc_sampling',r'filt|adc|spur|rc_cal|dcap|iq_swap|rxclk|fft_scale'),
    ('packet_detection_or_protocol',r'cca|pkd|pkdet|sense|11b|11p|he_|vht|lltf|ht20|cbw|rifs|sifs|bss|noisefloor|noise_floor'),
    ('clock_pll_frequency',r'pll|freq|clk|xtal|ckgen|chan|rfcal'),
    ('power_tracking_temperature',r'temp|track|power|pwr|xpd|sleep|tsens'),
    ('bus_register_calibration_helper',r'pbus|i2c|reg|mem|cal|init|param|store|read|write|check|print|debug|enable|disable'),
]


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('symbols',type=Path);p.add_argument('output',type=Path)
    p.add_argument('--elf',type=Path);p.add_argument('--main',type=Path,default=Path('main'))
    a=p.parse_args();member='';rows=[]
    elf=set(re.findall(r'^[0-9a-f]+ [Tt] (\S+)$',a.elf.read_text(),re.M)) if a.elf else set()
    source='\n'.join(f.read_text(errors='replace') for f in a.main.glob('*.[ch]'))
    for line in a.symbols.read_text().splitlines():
        if line.endswith(':'):member=line[:-1]
        m=re.fullmatch(r'([0-9a-f]+) ([Tt]) (\S+)',line)
        if not m:continue
        name=m[3]
        category=next((category for category,pattern in RULES if re.search(pattern,name)),'other_unclassified')
        rows.append(dict(symbol=name,archive_member=member,visibility='global' if m[2]=='T' else 'local',
            triage_by_name=category,defined_in_measured_elf=name in elf,
            mentioned_in_main=bool(re.search(r'\b'+re.escape(name)+r'\b',source))))
    result=dict(scope='All defined code symbols in pinned archive. Name triage is not ABI, execution or noise-path evidence.',
        phy_sha256='dbf33c418c8d408d4005c849d12a1432deea82e2e5e57de3c8ddf914d104fffb',
        symbol_input_sha256=hashlib.sha256(a.symbols.read_bytes()).hexdigest(),
        count=len(rows),triage_counts=dict(Counter(r['triage_by_name'] for r in rows)),symbols=rows)
    a.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(count=result['count'],triage_counts=result['triage_counts']),indent=2))


if __name__=='__main__':main()

"""C5VRX by Twotoz/contributors: canonical Phase8 carrier periods.

Adapted from main/sync_flywheel.c build_cells and sync_synth.h. This generator
never touches hardware or enables repairs. --check rejects stale generated data.
"""
from pathlib import Path
import argparse, hashlib, math, re
ROOT=Path(__file__).resolve().parents[1]
DEST=ROOT/'main/sync_period_bank.h'
STEPS=(-9830,-9011,-8602,-7782,-7373,-7430,-1632)

def inputs():
    phase_source=(ROOT/'main/phase8_gain_lut.h').read_text()
    body=re.search(r'c5vrx_phase8_gain_lut\[256\]\s*=\s*\{(.*?)\};',phase_source,re.S).group(1)
    phase=[int(value,0) for value in re.findall(r'0x[0-9a-fA-F]+|[0-9]+',body)]
    assert len(phase)==256 and all(0<=v<256 for v in phase)
    core=(ROOT/'main/sync_flywheel.c').read_text();start=core.index('static void build_cells(')
    first=core.index('{',start);depth=0
    for end in range(first,len(core)):
        depth+=(core[end]=='{')-(core[end]=='}')
        if not depth:break
    return phase,hashlib.sha256(phase_source.encode()).hexdigest(),hashlib.sha256(core[start:end+1].encode()).hexdigest()

def cells_for(phase):
    result=[]
    for target in range(256):
        best=None;chosen=None
        for raw in range(256):
            i=raw>>4;q=raw&15;i-=16 if i>=8 else 0;q-=16 if q>=8 else 0
            radius=(2*i+1)**2+(2*q+1)**2
            if not 64<=radius<=169:continue
            error=((phase[raw]-target+128)&255)-128
            score=abs(error)*1024+abs(radius-100)
            if best is None or score<best:best=score;chosen=raw
        assert chosen is not None
        result.append(chosen)
    return result

def generate():
    phase,phase_hash,cell_hash=inputs();cells=cells_for(phase);data=list(cells);descriptors=[]
    for step in STEPS:
        gcd=math.gcd(step,65536);period=65536//gcd;inverse=pow((step&65535)//gcd,-1,period)
        descriptors.append((step,gcd.bit_length()-1,inverse,period,len(data)))
        data.extend(cells[((k*step+128)&65535)>>8] for k in range(period))
    assert len(data)==264448
    output=['''/* C5VRX by Twotoz and contributors. Generated: tools/gen_sync_period_bank.py.
 * Canonical strong cells and packed complete Q8 phase periods, extending
 * main/sync_flywheel.c build_cells and main/sync_synth.h. Include this data in
 * ONE firmware translation unit. This table does not authorize live writes.
 * H kp.50 uses -7430; ordinary V kp.50 remains -7373. V blank is canonical
 * -1632 only. Native masks/other phase maps must retain another constructor.
 * No claim of loaded DAC, physical WCET or unconditional low-kp V admission.
 */
#pragma once
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include "phase8_gain_lut.h"
''',f'#define SFW_PERIOD_PHASE_SOURCE_SHA256 "{phase_hash}"\n',
        f'#define SFW_PERIOD_CELL_SOURCE_SHA256 "{cell_hash}"\n',
        '#define SFW_PERIOD_DATA_BYTES 264448u\n#define SFW_PERIOD_BLANK_BYTES 2048u\n',
        'static const uint8_t sfw_period_data[SFW_PERIOD_DATA_BYTES] = {\n']
    for start in range(0,len(data),32):output.append('    '+','.join(f'0x{v:02x}' for v in data[start:start+32])+',\n')
    output.append('};\n')
    output.append('''
typedef struct { int16_t step; uint8_t shift; uint8_t reserved; uint32_t inverse,period,offset; } sfw_period_descriptor_t;
static const sfw_period_descriptor_t sfw_period_descriptors[7] = {
''')
    for step,shift,inverse,period,offset in descriptors:output.append(f'    {{{step},{shift},0,{inverse}u,{period}u,{offset}u}},\n')
    output.append('''};
typedef struct {
    const uint8_t *first,*second;
    uint32_t first_bytes,second_bytes;
} sfw_period_slice_t;

/* Preflight, outside pulse timing. Caller separately binds the fully verified
 * U85Q-LP/kp/OUTPUT epoch; this only certifies the unmasked canonical phase map. */
static inline bool sfw_period_phase_compatible(const uint8_t *phase,bool mask_bit0)
{
    return phase && !mask_bit0 && !memcmp(phase,c5vrx_phase8_gain_lut,256u);
}

/* Two contiguous, read-only spans of samples AFTER previous_phase. Exact phase
 * is required; unrepresentable phases, unknown steps, zero/overlong runs fail.
 * No allocations, hardware access, or per-sample arithmetic. Output belongs to
 * caller; bank bytes remain immutable. Video use still requires context proof. */
static inline bool sfw_period_slice(int32_t step,uint16_t previous_phase,uint32_t bytes,sfw_period_slice_t *out)
{
    if(!out)return false;
    *out=(sfw_period_slice_t){0};
    for(unsigned j=0;j<7u;++j) {
        const sfw_period_descriptor_t *d=&sfw_period_descriptors[j];
        if(d->step!=step)continue;
        if(!bytes || bytes>d->period || (previous_phase&((UINT32_C(1)<<d->shift)-1u)))return false;
        uint32_t position=((((uint32_t)previous_phase>>d->shift)*d->inverse)+1u)&(d->period-1u);
        uint32_t first=d->period-position;if(first>bytes)first=bytes;
        *out=(sfw_period_slice_t){sfw_period_data+d->offset+position,sfw_period_data+d->offset,first,bytes-first};
        return true;
    }
    return false;
}

/* Exact64-byte known-blank predicate, NOT a source-sync detector or lifetime
 * guard. Caller supplies a stable, contiguous64-byte actual-transmitted
 * history and an offset from successful emission metadata (or equivalent
 * independently proved source context). No offset scan/inference occurs.
 * Recheck RX age/ownership/context separately. Refused commits and a taint
 * ledger do not establish successful emission. Source evidence must continue
 * to exclude repairs. Passing this matcher alone does not qualify all low-kp
 * V waveform states or make firstEQ construction unnecessary. */
static inline bool sfw_period_blank64_matches(const uint8_t *actual,uint32_t first_offset)
{
    if(!actual || first_offset>=SFW_PERIOD_BLANK_BYTES)return false;
    const uint8_t *blank=sfw_period_data+'''+str(descriptors[-1][-1])+'''u;
    uint32_t first=SFW_PERIOD_BLANK_BYTES-first_offset;if(first>64u)first=64u;
    return !memcmp(actual,blank+first_offset,first) && !memcmp(actual+first,blank,64u-first);
}
''')
    return ''.join(output)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--check',action='store_true');args=ap.parse_args();text=generate()
    if args.check:
        if not DEST.exists() or DEST.read_text()!=text:raise SystemExit('stale sync_period_bank.h; run tools/gen_sync_period_bank.py')
        print('PASS sync period bank: canonical cells, seven packed periods, exact metadata and reproducible header')
    else:DEST.write_text(text);print(f'wrote {DEST}:264448 data bytes')
if __name__=='__main__':main()

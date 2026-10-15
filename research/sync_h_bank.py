"""C5VRX by Twotoz and contributors: temporary H-tip bank experiment.
Never modifies production sources.

The 48-sample front porch absorbs endpoint residual; constant-frequency 188-byte
sync tips are cached by kp and requested final phase. No hardware timing claim.
"""
from pathlib import Path
import argparse, csv, hashlib, io, json, os, subprocess, tempfile
ROOT = Path(__file__).resolve().parents[1]
HELPER = r'''
#include "phase8_gain_lut.h"
static uint8_t h_banks[8][256][188];
static bool h_ready[8];
static bool bank_h(sync_flywheel_t *f,const sfw_ring_t *r,uint64_t a,
                   int32_t porch,int32_t step,int32_t next_step)
{
    const unsigned levels[8]={85,80,75,70,65,60,55,50};
    unsigned kp=0;while(kp<8 && levels[kp]!=f->repair_kp_x100)++kp;
    if(kp==8)return false;
    if(!h_ready[kp]) {
        for(unsigned end=0;end<256;++end) {
            sync_synth_t s;if(!sync_synth_init(&s,(uint16_t)(end*256u-188u*(uint32_t)step),0,188))return false;
            for(unsigned j=0;j<188;++j)h_banks[kp][end][j]=s_cell[sync_synth_next(&s,step)];
        }
        h_ready[kp]=true;
    }
    uint64_t b=a+236u;
    uint16_t target=(uint16_t)(ph_at(r,b)*256u-(uint32_t)next_step);
    unsigned end=((target+128u)>>8)&255u;
    int32_t lead=2*(step-porch)*(100-f->repair_kp_x100)/f->repair_kp_x100;
    uint16_t initial=(uint16_t)(end*256u-188u*(uint32_t)step);
    int64_t p0=(int64_t)ph_at(r,a-1u)*256;
    int32_t residual=(int32_t)(((uint32_t)initial-(uint32_t)lead-(uint32_t)p0-48u*(uint32_t)porch)&65535u);
    if(residual>=32768)residual-=65536;
    sync_synth_t bridge;if(!sync_synth_init(&bridge,(uint16_t)p0,residual,48))return false;
    uint8_t pulse[236];
    for(unsigned j=0;j<48;++j)pulse[j]=s_cell[sync_synth_next(&bridge,porch)];
    memcpy(pulse+48,h_banks[kp][end],188);
    if(r->commit) {memcpy(r->workspace,pulse,236);return commit_prepared(f,r,a,b);}
    if(!sync_repair_ledger_record(&f->repairs,a,b))return false;
    for(unsigned j=0;j<236;++j)r->ring[(uint32_t)(a+j)&(r->ring_bytes-1u)]=pulse[j];
    return true;
}
'''

def variant(source, h50_step=-7373):
    marker="static SFW_HOT bool synth(sync_flywheel_t *f"
    assert source.count(marker)==1
    helper=HELPER.replace('if(kp==8)return false;', 'if(kp==8)return false;\n    if(kp==7)step='+str(h50_step)+';')
    source=source.replace(marker,helper+"\n"+marker)
    marker="    if (bootstrap) {\n        uint8_t tip[128];"
    assert source.count(marker)==1
    source=source.replace(marker,'''    if (!bootstrap && n1==PORCH && n2==HSYNC && step2==f->repair_step_q8 &&
        !r->mask_bit0 && !memcmp(r->phase,c5vrx_phase8_gain_lut,256))
        return bank_h(f,r,a,step1,step2,next_step);
'''+marker)
    return source

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--kp',nargs='+',type=int,default=list(range(8)))
    ap.add_argument('--h50-step',type=int,default=-7373)
    ap.add_argument('--output',default='/tmp/sync_h_bank.json');ap.add_argument('--transition',action='store_true');ap.add_argument('--phase-sweep',action='store_true')
    args=ap.parse_args();source=(ROOT/'main/sync_flywheel.c').read_text()
    result={'core_sha256':hashlib.sha256(source.encode()).hexdigest(),'bank_bytes':8*256*188,
            'h50_step':args.h50_step,
            'algorithm':'constant tip; quantized received endpoint; all residual in48 porch; unchanged kp lead',
            'hardware_timing_claim':False,'scenes':[],
            'inputs':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in
                ['main/sync_flywheel.c','main/sync_flywheel.h','main/sync_synth.h',
                 'main/kp_tables.h','main/phase8_gain_lut.h','main/output_level.h',
                 'test/test_sync_flywheel.c','research/sync_output_transition.c',
                 'research/sync_h_bank_phase.c']}}
    with tempfile.TemporaryDirectory() as directory:
        folder=Path(directory);core=folder/'sync_flywheel.c';core.write_text(variant(source,args.h50_step))
        binary=folder/'scenes'
        subprocess.run(['cc','-O2','-std=gnu11','-Wall','-Wextra','-Werror','-I'+str(ROOT/'main'),
            str(ROOT/'test/test_sync_flywheel.c'),str(core),'-lm','-o',str(binary)],check=True)
        for kp in args.kp:
            env=dict(os.environ,SFW_KP_LEVEL=str(kp),SFW_COMMIT='1')
            run=subprocess.run(['stdbuf','-oL',str(binary)],env=env,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
            result['scenes'].append({'kp_index':kp,'returncode':run.returncode,'stdout':run.stdout})
            print('kp',kp,'return',run.returncode,run.stdout[-800:],flush=True)
        if args.transition:
            audit=(ROOT/'research/sync_output_transition.c').read_text().replace('#include "../main/sync_flywheel.c"','#include "'+str(core)+'"')
            runner=folder/'transition.c';runner.write_text(audit)
            subprocess.run(['cc','-O2','-std=gnu11','-Wall','-Wextra','-Werror','-I'+str(ROOT/'main'),str(runner),'-o',str(folder/'transition')],check=True)
            run=subprocess.run([str(folder/'transition')],text=True,stdout=subprocess.PIPE,check=True)
            out=Path(args.output).with_suffix('.csv');out.write_text(run.stdout);result['transition_csv']=str(out)
        if args.phase_sweep:
            result['phase_fixture']='independent16x16 endpoints; unknown state atfirst generated pair; all32decoder phases, both parities and0/63levels'
            for label,selected in [('baseline',ROOT/'main/sync_flywheel.c'),('bank',core)]:
                executable=folder/label
                subprocess.run(['cc','-O2','-std=gnu11','-Wall','-Wextra','-Werror','-Wno-misleading-indentation',
                    '-I'+str(ROOT/'main'),'-DSFW_CORE_FILE="'+str(selected)+'"',
                    str(ROOT/'research/sync_h_bank_phase.c'),'-o',str(executable)],check=True)
                run=subprocess.run([str(executable)],text=True,stdout=subprocess.PIPE,check=True)
                out=Path(args.output).with_name(Path(args.output).stem+'_phase_'+label+'.csv');out.write_text(run.stdout)
                rows=list(csv.DictReader(io.StringIO(run.stdout)))
                result.setdefault('phase_results',{})[label]={
                    'file':out.name,'sha256':hashlib.sha256(run.stdout.encode()).hexdigest(),
                    'rows':len(rows),'cases':sum(int(row['cases']) for row in rows),
                    'accepted':sum(int(row['accepted']) for row in rows),
                    'failed_rows':sum(row['cases']!=row['accepted'] for row in rows)}
                print('phase sweep',label,'complete',flush=True)
    Path(args.output).write_text(json.dumps(result,indent=2)+'\n')
    return int(any(row['returncode'] for row in result['scenes']) or
               result.get('phase_results',{}).get('bank',{}).get('failed_rows',0)!=0)
if __name__=='__main__':raise SystemExit(main())

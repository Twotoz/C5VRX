"""C5VRX by Twotoz and contributors: isolated periodic V preparation study.
No production edits, hardware actions, or timing claims. First EQ stays dynamic.
"""
from pathlib import Path
import argparse, gzip, hashlib, json, os, re, subprocess, tempfile
ROOT=Path(__file__).resolve().parents[1]
HELPER=r'''
#include "phase8_gain_lut.h"
#include <stdio.h>
#include <stdlib.h>
#include <inttypes.h>
static struct {int step;bool bootstrap,bank;unsigned reason;uint64_t calls;} v_coverage[64];
static unsigned v_coverage_count;static uint64_t v_bank_accepted,v_bank_refused;
static uint64_t h_attempts,h_accepted,h_refused,h_fallback;static bool cover_registered;
void sfw_v_cover_dump(void)
{
    static bool dumped;if(dumped)return;dumped=true;
    for(unsigned j=0;j<v_coverage_count;++j)
        fprintf(stderr,"V_COVER step=%d bootstrap=%u bank=%u reason=%u calls=%" PRIu64 "\n",v_coverage[j].step,
            v_coverage[j].bootstrap,v_coverage[j].bank,v_coverage[j].reason,v_coverage[j].calls);
    fprintf(stderr,"V_RESULT accepted=%" PRIu64 " refused=%" PRIu64 "\n",v_bank_accepted,v_bank_refused);
    fprintf(stderr,"H_RESULT attempts=%" PRIu64 " accepted=%" PRIu64 " refused=%" PRIu64 " fallback=%" PRIu64 "\n",h_attempts,h_accepted,h_refused,h_fallback);
}
static void v_cover(int step,bool bootstrap,bool bank,unsigned reason)
{
    if(!cover_registered){atexit(sfw_v_cover_dump);cover_registered=true;}
    unsigned i=0;while(i<v_coverage_count&&(v_coverage[i].step!=step||v_coverage[i].bootstrap!=bootstrap||v_coverage[i].bank!=bank||v_coverage[i].reason!=reason))++i;
    if(i==v_coverage_count){if(i==64)abort();v_coverage[i].step=step;v_coverage[i].bootstrap=bootstrap;v_coverage[i].bank=bank;v_coverage[i].reason=reason;++v_coverage_count;}
    ++v_coverage[i].calls;
}
static uint8_t v_banks[6][65536],v_blank[2048];
_Static_assert(((1997u*1285u)&2047u)==1u,"inverse of -1632/32 modulo2048");
static uint32_t v_period[6],v_inverse[6];
static bool v_blank_ready;
static unsigned v_gcd(unsigned a,unsigned b){while(b){unsigned t=a%b;a=b;b=t;}return a;}
static unsigned v_inv(unsigned a,unsigned n){for(unsigned k=1;k<n;k+=2u)if(((uint64_t)k*a)%n==1u)return k;return 0;}
static void v_copy(uint8_t *dest,const uint8_t *bank,unsigned period,unsigned offset,unsigned bytes)
{
    unsigned first=period-offset;if(first>bytes)first=bytes;
    memcpy(dest,bank+offset,first);if(bytes>first)memcpy(dest+first,bank,bytes-first);
}
static bool bank_v(sync_flywheel_t*f,const sfw_ring_t*r,uint64_t a,unsigned low,unsigned high,
                   int32_t step,int32_t blank,int32_t next)
{
    const int32_t steps[6]={-9830,-9011,-8602,-7782,-7373,-7430};
    unsigned idx=0;while(idx<6&&steps[idx]!=step)++idx;if(idx==6||high<48u)return false;
    unsigned gcd=v_gcd((unsigned)-step,65536u),period=65536u/gcd;
    if(!v_period[idx]) {
        v_period[idx]=period;v_inverse[idx]=v_inv(((uint32_t)step&65535u)/gcd,period);
        if(!v_inverse[idx])return false;
        for(unsigned k=0;k<period;++k)v_banks[idx][k]=s_cell[(uint8_t)(((k*(uint32_t)step+128u)&65535u)>>8)];
    }
    if(!v_blank_ready){for(unsigned k=0;k<2048;++k)v_blank[k]=s_cell[(uint8_t)(((k*(uint32_t)-1632+128u)&65535u)>>8)];v_blank_ready=true;}
    uint8_t pulse[1280];unsigned n=low+high;
    int32_t previous_blank=V_LEARNED_LEAD?(int32_t)f->blank_q4*16:blank;
    int64_t lead=(int64_t)2*(step-previous_blank)*(100-f->repair_kp_x100)/f->repair_kp_x100;
    uint32_t start=((uint32_t)(ph_at(r,a-1u)*256u+lead)+gcd/2u)&65535u;start-=start%gcd;
    unsigned index=(((start/gcd)*v_inverse[idx])+1u)&(period-1u);
    v_copy(pulse,v_banks[idx],period,index,low);
    uint32_t tip_end=(start+low*(uint32_t)step)&65535u;
    uint32_t target=(ph_at(r,a+n)*256u-(uint32_t)next)&65535u;
    unsigned tail=high-48u;
    uint32_t tail_start=(target-tail*(uint32_t)blank)&65535u;
    uint32_t bridge_start=tip_end,bridge_target=tail_start;
    if(V_BRIDGE_END){bridge_start=(tip_end+tail*(uint32_t)blank)&65535u;bridge_target=target;}
    int32_t residual=(int32_t)((bridge_target-bridge_start-48u*(uint32_t)blank)&65535u);if(residual>=32768)residual-=65536;
    sync_synth_t bridge;if(!sync_synth_init(&bridge,(uint16_t)bridge_start,residual,48))return false;
    if(V_BRIDGE_END) {
        /* Tip endpoint can occupy any Q8 remainder. This blank bank only has
         * 32-Q8 spacing; exact rounding belongs to a separately tested context. */
        uint32_t quant=(tip_end+16u)&65535u;quant-=quant%32u;
        unsigned off=(((quant/32u)*1285u)+1u)&2047u;
        v_copy(pulse+low,v_blank,2048,off,tail);
        for(unsigned j=0;j<48;++j)pulse[low+tail+j]=s_cell[sync_synth_next(&bridge,blank)];
    } else {
        for(unsigned j=0;j<48;++j)pulse[low+j]=s_cell[sync_synth_next(&bridge,blank)];
        unsigned off=(((tail_start/32u)*1285u)+1u)&2047u;
        v_copy(pulse+low+48,v_blank,2048,off,tail);
    }
    if(r->commit){memcpy(r->workspace,pulse,n);return commit_prepared(f,r,a,a+n);}
    if(!sync_repair_ledger_record(&f->repairs,a,a+n))return false;
    for(unsigned j=0;j<n;++j)r->ring[(uint32_t)(a+j)&(r->ring_bytes-1u)]=pulse[j];
    return true;
}
static bool bank_h_periodic(sync_flywheel_t*f,const sfw_ring_t*r,uint64_t a,int32_t porch,int32_t tip,int32_t next)
{
    if(H_STEP50&&f->repair_kp_x100==50)tip=H_STEP50;
    const int32_t steps[6]={-9830,-9011,-8602,-7782,-7373,-7430};
    unsigned idx=0;while(idx<6&&steps[idx]!=tip)++idx;if(idx==6)return false;
    unsigned gcd=v_gcd((unsigned)-tip,65536u),period=65536u/gcd;
    if(!v_period[idx]) {
        v_period[idx]=period;v_inverse[idx]=v_inv(((uint32_t)tip&65535u)/gcd,period);
        if(!v_inverse[idx])return false;
        for(unsigned k=0;k<period;++k)v_banks[idx][k]=s_cell[(uint8_t)(((k*(uint32_t)tip+128u)&65535u)>>8)];
    }
    uint32_t target=(ph_at(r,a+236u)*256u-(uint32_t)next)&65535u;
    uint32_t begin=(target-188u*(uint32_t)tip)&65535u;
    if(begin%gcd)return false;
    int32_t lead=2*(tip-porch)*(100-f->repair_kp_x100)/f->repair_kp_x100;
    uint32_t p0=ph_at(r,a-1u)*256u;
    int32_t residual=(int32_t)((begin-(uint32_t)lead-p0-48u*(uint32_t)porch)&65535u);
    if(residual>=32768)residual-=65536;
    uint8_t pulse[236];sync_synth_t bridge;
    if(!sync_synth_init(&bridge,(uint16_t)p0,residual,48u))return false;
    for(unsigned j=0;j<48;++j)pulse[j]=s_cell[sync_synth_next(&bridge,porch)];
    unsigned offset=(((begin/gcd)*v_inverse[idx])+1u)&(period-1u);
    v_copy(pulse+48,v_banks[idx],period,offset,188u);
    if(r->commit){memcpy(r->workspace,pulse,236);return commit_prepared(f,r,a,a+236u);}
    if(!sync_repair_ledger_record(&f->repairs,a,a+236u))return false;
    for(unsigned j=0;j<236;++j)r->ring[(uint32_t)(a+j)&(r->ring_bytes-1u)]=pulse[j];
    return true;
}
'''

def variant(source,bridge,canonical=False,learned_lead=False,periodic_h=False,h_step50=0):
    marker='static SFW_HOT bool synth(sync_flywheel_t *f';assert source.count(marker)==1
    source=source.replace(marker,'#define H_STEP50 '+str(h_step50)+'\n#define H_PERIODIC '+str(int(periodic_h))+'\n#define V_LEARNED_LEAD '+str(int(learned_lead))+'\n#define V_BRIDGE_END '+str(int(bridge=='end'))+'\n'+HELPER+'\n'+marker)
    marker='    if (bootstrap) {\n        uint8_t tip[128];';assert source.count(marker)==1
    source=source.replace(marker,'''    bool is_v=step1==f->repair_step_q8 && n1+n2<=1280u;
    bool use_bank=is_v && !bootstrap && step2==-1632 && next_step==-1632 &&
        !r->mask_bit0 && !memcmp(r->phase,c5vrx_phase8_gain_lut,256);
    unsigned reason=bootstrap?1u:(step2!=-1632||next_step!=-1632)?2u:r->mask_bit0?3u:memcmp(r->phase,c5vrx_phase8_gain_lut,256)?4u:0u;
    if(is_v)v_cover(step2,bootstrap,use_bank,reason);
    if(use_bank){bool done=bank_v(f,r,a,n1,n2,step1,step2,next_step);v_bank_accepted+=done;v_bank_refused+=!done;return done;}
    bool is_h=!bootstrap && n1==PORCH && n2==HSYNC && step2==f->repair_step_q8;
    if(H_PERIODIC && is_h) {
        if(!cover_registered){atexit(sfw_v_cover_dump);cover_registered=true;}
        if(!r->mask_bit0&&!memcmp(r->phase,c5vrx_phase8_gain_lut,256)) {
            ++h_attempts;bool done=bank_h_periodic(f,r,a,step1,step2,next_step);
            h_accepted+=done;h_refused+=!done;return done;
        }
        ++h_fallback;
    }
'''+marker)
    if canonical:
        marker='synth(f, r, p, low, tip, h2 - low, blank_step(f), blank_step(f),'
        assert source.count(marker)==1
        source=source.replace(marker,'synth(f, r, p, low, tip, h2 - low, -1632, -1632,')
    return source

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--kp',nargs='*',type=int,default=list(range(8)))
    ap.add_argument('--bridge',choices=['start','end'],default='start');ap.add_argument('--output',default='/tmp/sync_v_bank.json')
    ap.add_argument('--core',type=Path,default=ROOT/'research/data/sync_read_core_cb538a1c.c.gz',help='frozen pre-integration core; .c or .c.gz')
    ap.add_argument('--transition',action='store_true');ap.add_argument('--phase-sweep',action='store_true');ap.add_argument('--history',type=int,choices=[0,64],default=0);ap.add_argument('--prefix-step',type=int,default=-1638);ap.add_argument('--phase-kp-min',type=int,default=0);ap.add_argument('--canonical-v-blank',action='store_true');ap.add_argument('--learned-lead',action='store_true');ap.add_argument('--output-level',type=int,default=0);ap.add_argument('--clock-ppm',type=float,default=0);ap.add_argument('--periodic-h',action='store_true');ap.add_argument('--h-phase-sweep',action='store_true');ap.add_argument('--h-step50',type=int,choices=[0,-7430],default=0);args=ap.parse_args()
    raw=args.core.read_bytes()
    source=(gzip.decompress(raw) if args.core.suffix=='.gz' else raw).decode().replace('\r\n','\n')
    result={'core_sha256':hashlib.sha256(source.encode()).hexdigest(),
        'variant_sha256':hashlib.sha256(variant(source,args.bridge,args.canonical_v_blank,args.learned_lead,args.periodic_h,args.h_step50).encode()).hexdigest(),
        'helper_sha256':hashlib.sha256(HELPER.encode()).hexdigest(),
        'inputs':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in
            ['main/sync_flywheel.h','main/sync_synth.h','main/kp_tables.h','main/phase8_gain_lut.h',
             'main/output_level.h','test/test_sync_flywheel.c','research/sync_output_transition.c','research/sync_v_bank_phase.c']},
        'periodic_h':args.periodic_h,'h_step50_override':args.h_step50,'output_level':args.output_level,'clock_ppm':args.clock_ppm,'canonical_v_blank':args.canonical_v_blank,'lead_basis':'learned' if args.learned_lead else 'emitted',
        'coverage_reasons':{0:'bank',1:'firstEQ_dynamic',2:'noncanonical_blank',3:'native_mask',4:'nonstandard_phase_map'},'history_bytes':args.history,'prefix_step':args.prefix_step,'phase_kp_min':args.phase_kp_min,'bridge':args.bridge,'hardware_timing_claim':False,'tip_period_bytes':229376,'blank_period_bytes':2048,
        'h_additional_period_bytes':32768 if args.h_step50 else 0,'scenes':[]}
    with tempfile.TemporaryDirectory() as directory:
        folder=Path(directory);core=folder/'sync_flywheel.c';core.write_text(variant(source,args.bridge,args.canonical_v_blank,args.learned_lead,args.periodic_h,args.h_step50));binary=folder/'scenes'
        test=folder/'scenes.c'
        test.write_text((ROOT/'test/test_sync_flywheel.c').read_text()+r'''
extern void sfw_v_cover_dump(void);
extern void __real___assert_fail(const char*,const char*,unsigned,const char*) __attribute__((noreturn));
void __wrap___assert_fail(const char *a,const char *b,unsigned c,const char*d){sfw_v_cover_dump();__real___assert_fail(a,b,c,d);}
''')
        subprocess.run(['cc','-O2','-std=gnu11','-Wall','-Wextra','-Werror','-I'+str(ROOT/'main'),str(test),str(core),'-Wl,--wrap=__assert_fail','-lm','-o',str(binary)],check=True)
        for kp in args.kp:
            run=subprocess.run([str(binary)],env=dict(os.environ,SFW_KP_LEVEL=str(kp),SFW_COMMIT='1',SFW_OUTPUT_LEVEL=str(args.output_level),SFW_CLOCK_PPM=str(args.clock_ppm)),text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
            result['scenes'].append({'kp_index':kp,'returncode':run.returncode,'coverage':[{'step':int(m[0]),'bootstrap':bool(int(m[1])),'bank':bool(int(m[2])),'reason':int(m[3]),'calls':int(m[4])} for m in re.findall(r'V_COVER step=(-?\d+) bootstrap=(\d) bank=(\d) reason=(\d) calls=(\d+)',run.stdout)],'bank_results':[{'accepted':int(m[0]),'refused':int(m[1])} for m in re.findall(r'V_RESULT accepted=(\d+) refused=(\d+)',run.stdout)],'h_results':[{'attempts':int(m[0]),'accepted':int(m[1]),'refused':int(m[2]),'fallback':int(m[3])} for m in re.findall(r'H_RESULT attempts=(\d+) accepted=(\d+) refused=(\d+) fallback=(\d+)',run.stdout)],'stdout':run.stdout});print('kp',kp,'return',run.returncode,run.stdout[-1000:],flush=True)
        if args.transition:
            audit=(ROOT/'research/sync_output_transition.c').read_text().replace('#include "../main/sync_flywheel.c"','#include "'+str(core)+'"')
            runner=folder/'transition.c';runner.write_text(audit)
            subprocess.run(['cc','-O2','-std=gnu11','-Wall','-Wextra','-Werror','-I'+str(ROOT/'main'),str(runner),'-o',str(folder/'transition')],check=True)
            run=subprocess.run([str(folder/'transition')],text=True,stdout=subprocess.PIPE,check=True)
            out=Path(args.output).with_suffix('.csv');out.write_text(run.stdout);result['transition_csv']=str(out)
        if args.phase_sweep:
            for label,selected in [('baseline',ROOT/'main/sync_flywheel.c'),('bank',core)]:
                executable=folder/label
                subprocess.run(['cc','-O2','-std=gnu11','-Wall','-Wextra','-Werror','-Wno-misleading-indentation',
                    '-I'+str(ROOT/'main'),'-DV_HISTORY_BYTES='+str(args.history),'-DV_PREFIX_STEP='+str(args.prefix_step),'-DV_KP_MIN='+str(args.phase_kp_min),'-DSFW_CORE_FILE="'+str(selected)+'"',
                    str(ROOT/'research/sync_v_bank_phase.c'),'-o',str(executable)],check=True)
                run=subprocess.run([str(executable)],text=True,stdout=subprocess.PIPE,check=True)
                out=Path(args.output).with_name('sync_v_phase_'+args.bridge+('_history64' if args.history else '')+('_step'+str(-args.prefix_step) if args.prefix_step!=-1638 else '')+'_'+label+'.csv.gz')
                with gzip.open(out,'wt',encoding='utf-8',newline='') as stream:stream.write(run.stdout)
                result['phase_'+label]=str(out);print('phase sweep',label,'complete',flush=True)
        if args.h_phase_sweep:
            for label,selected in [('baseline',ROOT/'main/sync_flywheel.c'),('bank',core)]:
                executable=folder/('h_'+label)
                subprocess.run(['cc','-O2','-std=gnu11','-Wall','-Wextra','-Werror','-Wno-misleading-indentation',
                    '-I'+str(ROOT/'main'),'-DSFW_CORE_FILE="'+str(selected)+'"',
                    str(ROOT/'research/sync_h_bank_phase.c'),'-o',str(executable)],check=True)
                run=subprocess.run([str(executable)],text=True,stdout=subprocess.PIPE,check=True)
                out=Path(args.output).with_name('sync_h_periodic'+('_h'+str(-args.h_step50) if args.h_step50 else '')+'_boundary_'+label+'.csv.gz')
                with gzip.open(out,'wt',encoding='utf-8',newline='') as stream:stream.write(run.stdout)
                result['h_phase_'+label]=str(out);print('H phase sweep',label,'complete',flush=True)
    Path(args.output).write_text(json.dumps(result,indent=2)+'\n')
if __name__=='__main__':main()

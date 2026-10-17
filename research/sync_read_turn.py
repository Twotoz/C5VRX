"""C5VRX by Twotoz/contributors: postvalidated DONE-cadence detector replay.

Frozen actual core, original source scenes, and a modeled8us read span.
This is not a C5 cycle measurement or permission to publish DMA patches.
"""
import gzip
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import sync_acquire_stride as coarse

ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--budget',type=int,default=192)
    parser.add_argument('--current-stride',type=int,choices=(127,))
    args=parser.parse_args()
    frozen=ROOT/'research/data/sync_read_core_cb538a1c.c.gz'
    raw_source=((ROOT/'main/sync_flywheel.c').read_bytes() if args.current_stride
                else gzip.decompress(frozen.read_bytes()))
    source=raw_source.decode().replace('\r\n','\n')
    if args.current_stride:
        baseline='unsigned sfw_research_acq_skips;\n'+coarse.replace_once(source,
            'f->scan_pos = avail_end - 6000u;',
            '++sfw_research_acq_skips; f->scan_pos = avail_end - 6000u;')
        changed=coarse.replace_once(baseline,
            'f->scan_pos += f->acq_phase < 2u ? 63u : 3u;',
            'f->scan_pos += f->acq_phase < 2u ? 127u : 3u;')
        changed=coarse.replace_once(changed,'f->scan_pos = k > 71u ? k - 63u : 8u;',
            'f->scan_pos = k > 135u ? k - 127u : 8u;')
        # A minimum150-sample plateau retains140 sample centers after the
        # nine-step support. All127 coarse phases hit it; fine stride3 can
        # recover the leading edge within2. Not a noisy-IQ detection proof.
        for width in range(150,231):
            for phase in range(127):
                lo,hi=5,width-5
                hit=next(k for k in range(phase,hi,127) if k>=lo)
                refined=next(k for k in range(hit-127,hi,3) if k>=lo)
                assert lo<=refined<=lo+2
    else:
        baseline,changed=coarse.variants(source)
    marker='    return r->phase[r->ring[(uint32_t)k & (r->ring_bytes - 1u)]];'
    cores={}
    for name,body in [('baseline',baseline),('coarse',changed)]:
        assert body.count(marker)==1
        cores[name]='#include <stdint.h>\nvoid sfw_research_raw_read(uint64_t);\n'+body.replace(marker,'    sfw_research_raw_read(k);\n'+marker)
    results=[];inputs=[]
    with tempfile.TemporaryDirectory(prefix='c5-read-turn-') as folder:
        work=Path(folder)
        common=['docker','run','--rm','-v',ROOT.as_posix()+':/project:ro','-v',work.as_posix()+':/data','-w','/project','espressif/idf:v6.0.2']
        for name,body in cores.items():
            (work/(name+'.c')).write_text(body)
            subprocess.run(common+['gcc','-O2','-std=c11','-Wall','-Wextra','-Werror','-Wno-misleading-indentation',
                '-Imain','-Iresearch','research/sync_read_turn_replay.c','/data/'+name+'.c','-lm','-o','/data/'+name],check=True)
        generator='''#define main original_main
#include "/project/test/test_sync_flywheel.c"
#undef main
void sfw_research_raw_read(uint64_t k){(void)k;}
int main(int argc,char **argv){if(argc!=4)return 2;bool p=atoi(argv[1]);unsigned mode=atoi(argv[2]);
 uint64_t F=field_samples(p);scene_t s={.pal=p,.cfo_mhz=.15,.amp=5,.noise=.25};
 if(mode){s.fade[0]=(fade_t){4*F,(uint64_t)(4.6*F),mode==1?.4:0.,1.6,false};s.nfade=1;}
 if(mode==2){s.shift_at=(uint64_t)(4.6*F);s.shift=.37*(p?2560.:22880./9.);}
 uint64_t n=7*F;uint8_t *raw=generate(&s,n);FILE*f=fopen(argv[3],"wb");if(!f)return 2;
 int ok=fwrite(raw,1,n,f)==n;fclose(f);free(raw);return ok?0:2;}
'''
        (work/'source.c').write_text(generator)
        subprocess.run(common+['gcc','-O2','-std=c11','-D_DEFAULT_SOURCE','-Wall','-Wextra','-Werror','-Imain',
            '/data/source.c','/data/baseline.c','-lm','-o','/data/source'],check=True)
        for standard in ('PAL','NTSC'):
            for mode,scene in enumerate(('clean','fade','return_phase')):
                name=standard+'_'+scene
                subprocess.run(common+['/data/source',str(int(standard=='PAL')),str(mode),'/data/source.bin'],check=True,capture_output=True)
                data=(work/'source.bin').read_bytes()
                inputs.append(dict(name=name,sha256=hashlib.sha256(data).hexdigest(),samples=len(data)))
                for variant in cores:
                    result=subprocess.run(common+['/data/'+variant,'/data/source.bin','1',str(args.budget)],text=True,capture_output=True,check=True)
                    row=json.loads(result.stdout[result.stdout.index('{'):])
                    results.append(dict(name=name,variant=variant,**row))
                    print(name,variant,row,flush=True)
                    assert row['raw_writes']==row['readers_left']==row['rejected']==0
                    assert not row['write_permission']
                    if variant=='coarse' and not args.current_stride:
                        assert row['first_lock_us'] and row['first_v_us'] and row['real_v_fields']
    report=dict(provenance='C5VRX by Twotoz/contributors; https://github.com/Twotoz/C5VRX; https://twotoz.github.io/C5VRX/',
        raw_core_sha256=hashlib.sha256(raw_source).hexdigest(),normalized_core_sha256=hashlib.sha256(source.encode()).hexdigest(),
        candidate_core_sha256=hashlib.sha256(changed.encode()).hexdigest(),
        model_read_us=8,service_us=80,budget=args.budget,current_stride=args.current_stride,
        actual_rx_geometry=True,hardware_cycles_proven=False,
        candidate_acquires_all=all(r['first_lock_us'] and r['first_v_us'] and r['real_v_fields']
                                  for r in results if r['variant']=='coarse'),
        inputs=inputs,rows=results)
    name='sync_read_turn.json' if args.budget==192 else f'sync_read_turn_{args.budget}.json'
    if args.current_stride:name=f'sync_read_turn_stride{args.current_stride}_{args.budget}.json'
    (ROOT/'research/data'/name).write_text(json.dumps(report,indent=2)+'\n')

if __name__=='__main__':main()

"""C5VRX by Twotoz/contributors: exact read-only TRACK slice experiment.

The primitive transcript is a research stand-in for compact explicit helper
cursors. All source/width/edge gates execute unchanged. No C5 timing proof.
"""
import hashlib
import gzip
import json
from pathlib import Path
import subprocess
import tempfile
import sys
import sync_acquire_stride as acquisition

ROOT = Path(__file__).resolve().parents[1]


def block(source, signature):
    start = source.index(signature)
    brace = source.index('{', start)
    end, depth = brace + 1, 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return start, end, source[start:end]


STATE = r'''
#include <setjmp.h>
#include <assert.h>
typedef struct {uint16_t offset;int16_t threshold;int8_t answer;uint8_t kind;} slice_entry_t;
static slice_entry_t slice_log[1024];
static sync_flywheel_t slice_saved;
static unsigned slice_count,slice_cursor;
static uint64_t slice_end;
static bool slice_pending,slice_armed;
static jmp_buf slice_jmp;
static unsigned slice_actual_cost;
static bool roll_valid;static uint64_t roll_last;
static int roll_steps[9],roll_index,roll_sum,roll_phase;
unsigned sfw_research_slice_phases,sfw_research_slice_replay;
unsigned sfw_research_slice_yields,sfw_research_slice_expired,sfw_research_slice_max_entries;
unsigned sfw_research_track_stale;uint64_t sfw_research_first_stale;
static bool slice_hit(sync_flywheel_t const *f,uint64_t k,unsigned kind,int *answer)
{
    if(!slice_armed)return false;
    assert(k>=slice_end-32768u && k<slice_end);
    uint16_t offset=(uint16_t)(k-(slice_end-32768u));
    if(slice_cursor<slice_count){
        slice_entry_t e=slice_log[slice_cursor++];
        assert(e.offset==offset&&e.threshold==f->thr&&e.kind==kind);
        *answer=e.answer;++sfw_research_slice_replay;return true;
    }
    unsigned cost=kind?10u:2u;
    if(sfw_research_slice_phases+cost>1920u){
        ++sfw_research_slice_yields;slice_pending=true;longjmp(slice_jmp,1);
    }
    assert(slice_count<1024u);
    return false;
}
static void slice_record(sync_flywheel_t const *f,uint64_t k,unsigned kind,int answer)
{
    sfw_research_slice_phases+=slice_actual_cost;
    if(!slice_armed)return;
    slice_log[slice_count++]=(slice_entry_t){(uint16_t)(k-(slice_end-32768u)),f->thr,(int8_t)answer,(uint8_t)kind};
    ++slice_cursor;
    if(slice_count>sfw_research_slice_max_entries)sfw_research_slice_max_entries=slice_count;
}
'''


def variant(source, sliced, rolling=False):
    _, source = acquisition.variants(source)
    source = source.replace('#include <string.h>', '#include <string.h>\n' + STATE)
    source = source.replace('static uint32_t s_evals;', 'static uint32_t s_evals;\nstatic sync_flywheel_t *slice_f;')
    if rolling:
        a,b,original=block(source,'static SFW_HOT bool lowm(')
        reference=original.replace('lowm(', 'lowm_reference(',1)
        rolled=r'''static SFW_HOT bool lowm(const sync_flywheel_t *f,const sfw_ring_t *r,uint64_t k)
{
    ++s_evals;++s_lowm;
    if(roll_valid && k==roll_last+3u){
        slice_actual_cost=3;
        for(unsigned j=0;j<3;++j){
            int phase=ph_at(r,k+2u+j),step=wrap8(phase-roll_phase);
            roll_sum+=step-roll_steps[roll_index];roll_steps[roll_index]=step;
            roll_index=(roll_index+1)%9;roll_phase=phase;
        }
    }else{
        slice_actual_cost=10;roll_sum=0;roll_index=0;roll_phase=ph_at(r,k-5u);
        for(unsigned j=0;j<9;++j){int phase=ph_at(r,k-4u+j);
            int step=wrap8(phase-roll_phase);roll_steps[j]=step;roll_sum+=step;roll_phase=phase;}
    }
    roll_valid=true;roll_last=k;
    return roll_sum<=9*f->thr;
}'''
        source=source[:a]+reference+'\n'+rolled+source[b:]
        sa,sb,synth=block(source,'static SFW_HOT bool synth(')
        synth=synth.replace('{','{\n    roll_valid=false; /* no cache bridges any possible own source write */',1)
        source=source[:sa]+synth+source[sb:]
    for signature, name, kind in [('static inline int step3(', 'step3', 0),
                                  ('static SFW_HOT bool lowm(', 'lowm', 1)]:
        a, b, body = block(source, signature)
        original = body.replace(name + '(', name + '_original(', 1)
        if kind:
            wrapper = '''
static SFW_HOT bool lowm(const sync_flywheel_t *f,const sfw_ring_t *r,uint64_t k){
 int answer;if(slice_hit(f,k,1,&answer)){++s_evals;++s_lowm;return answer;}
 slice_actual_cost=10;answer=lowm_original(f,r,k);slice_record(f,k,1,answer);return answer;
}
'''
        else:
            wrapper = '''
static inline int step3(const sfw_ring_t *r,uint64_t k){
 int answer;if(slice_hit(slice_f,k,0,&answer)){++s_steps;return answer;}
 slice_actual_cost=2;answer=step3_original(r,k);slice_record(slice_f,k,0,answer);return answer;
}
'''
        source = source[:a] + original + wrapper + source[b:]
    a, b, run = block(source, 'unsigned sfw_run(')
    run=acquisition.replace_once(run,'            f->state = SFW_ACQUIRE;          /* data gone: start over */', '            ++sfw_research_track_stale;if(!sfw_research_first_stale)sfw_research_first_stale=avail_end;\n            f->state = SFW_ACQUIRE;          /* data gone: start over */')
    run = run.replace('{', '''{
    assert(!allow_repair);
    sfw_research_slice_phases=sfw_research_slice_replay=0;
    slice_f=f;(void)slice_saved;roll_valid=false;
    (void)roll_last;(void)roll_steps;(void)roll_index;(void)roll_sum;(void)roll_phase;
''', 1)
    if sliced:
        run = run.replace('uint64_t avail_end,', 'volatile uint64_t avail_end,', 1)
        run = run.replace('    f->evidence_end = avail_end;', '''    if(slice_pending){
        uint64_t oldest=f->repairs.oldest_readable;
        uint64_t first=slice_count?slice_end-32768u+slice_log[0].offset:0;
        for(unsigned i=0;i<slice_count;++i){uint64_t k=slice_end-32768u+slice_log[i].offset;if(k<first)first=k;}
        if(first<oldest+5u || avail_end>slice_end+16384u){
            slice_pending=false;slice_count=0;++sfw_research_slice_expired;
            sfw_init(f);
            (void)sync_repair_ledger_advance(&f->repairs,oldest);
        }else{*f=slice_saved;(void)sync_repair_ledger_advance(&f->repairs,oldest);avail_end=slice_end;}
    }
    f->evidence_end = avail_end;''')
        run = run.replace('    unsigned processed = 0;', '    volatile unsigned processed = 0;')
        anchor = '    while ((f->state == SFW_TRACK || f->state == SFW_GENERATED) && s_evals < budget_evals) {'
        run = acquisition.replace_once(run, anchor, anchor + '''
        if(!slice_pending){slice_saved=*f;slice_count=0;slice_end=avail_end;}
        slice_cursor=0;slice_armed=true;
        if(setjmp(slice_jmp)){
            *f=slice_saved;slice_armed=false;store_evals(f);return processed;
        }
        slice_pending=false;
''')
        run = run.replace('    store_evals(f);\n    return processed;',
                          '    slice_armed=false;slice_pending=false;\n    store_evals(f);\n    return processed;')
    return source[:a] + run + source[b:]


def load_core():
    if '--current' in sys.argv:
        return (ROOT/'main/sync_flywheel.c').read_text()
    return gzip.decompress((ROOT/'research/data/sync_track_core_acdf35c1.c.gz').read_bytes()).decode()


def main():
    source = load_core()
    names=('baseline','sliced') if '--slices' in sys.argv else ('baseline','rolling')
    originals = {name: variant(source, name == 'sliced', name == 'rolling') for name in names}
    runner = (ROOT / 'research/sync_detector_replay.c').read_text()
    runner = runner.replace('extern unsigned sfw_research_acq_skips;', '''extern unsigned sfw_research_acq_skips;
extern unsigned sfw_research_slice_phases,sfw_research_slice_replay,sfw_research_slice_yields,sfw_research_slice_expired,sfw_research_slice_max_entries;
extern unsigned sfw_research_track_stale;extern uint64_t sfw_research_first_stale;''')
    runner = runner.replace('uint64_t first_track=0, first_lock=0, first_v=0;',
                            'uint32_t max_phases=0,max_replay=0;\n    uint64_t first_track=0, first_lock=0, first_v=0;')
    runner = runner.replace('        if (memcmp(before,ring,sizeof(ring))) return 3;', '''        if(sfw_research_slice_phases>max_phases)max_phases=sfw_research_slice_phases;
        if(sfw_research_slice_replay>max_replay)max_replay=sfw_research_slice_replay;
        if (memcmp(before,ring,sizeof(ring))) return 3;''')
    runner = runner.replace('"\\\"max_acquire_evals\\\":', '"\\\"max_phases\\\":%u,\\\"max_replay\\\":%u,\\\"slice_yields\\\":%u,\\\"slice_expired\\\":%u,\\\"max_entries\\\":%u,\\\"max_acquire_evals\\\":')
    runner = runner.replace('        max_acquire_evals,max_track_evals,max_evals,max_steps);',
                            '        max_phases,max_replay,sfw_research_slice_yields,sfw_research_slice_expired,sfw_research_slice_max_entries,max_acquire_evals,max_track_evals,max_evals,max_steps);')
    runner=runner.replace('uint64_t rx=0, total_evals=0', 'uint64_t trace_hash=1469598103934665603ull;\n    uint64_t rx=0, total_evals=0')
    runner=runner.replace('        ++calls; total_evals', '        for(unsigned z=0;z<sizeof(f);++z){trace_hash^=((uint8_t*)&f)[z];trace_hash*=1099511628211ull;}\n        ++calls; total_evals')
    runner=runner.replace('\\\"max_phases\\\":%u', '\\\"state_trace\\\":\\\"%016llx\\\",\\\"max_phases\\\":%u')
    runner=runner.replace('        max_phases,max_replay,', '        (unsigned long long)trace_hash,max_phases,max_replay,')
    runner=runner.replace('\\\"max_entries\\\":%u', '\\\"max_entries\\\":%u,\\\"track_stale\\\":%u,\\\"first_stale\\\":%llu')
    runner=runner.replace('sfw_research_slice_max_entries,max_acquire_evals', 'sfw_research_slice_max_entries,sfw_research_track_stale,(unsigned long long)sfw_research_first_stale,max_acquire_evals')
    rows, inputs = [], []
    with tempfile.TemporaryDirectory(prefix='c5-track-slice-') as folder:
        w = Path(folder)
        (w / 'runner.c').write_text(runner)
        common = ['docker', 'run', '--rm', '-v', ROOT.as_posix() + ':/project:ro',
                  '-v', w.as_posix() + ':/data', '-w', '/project', 'gcc:14']
        for name, text in originals.items():
            (w / (name + '.c')).write_text(text)
            subprocess.run(common + ['gcc', '-O2', '-std=c11', '-Wall', '-Wextra', '-Werror',
                '-Wno-unused-function', '-Imain', '/data/runner.c', '/data/' + name + '.c', '-lm', '-o', '/data/' + name], check=True)
        (w / 'source.c').write_text('#define main old_main\n#include "/project/test/test_sync_flywheel.c"\n#undef main\n' + r'''
int main(int argc,char**argv){if(argc!=4)return 2;bool p=atoi(argv[1]);unsigned mode=atoi(argv[2]);
 uint64_t F=field_samples(p);scene_t s={.pal=p,.cfo_mhz=.15,.amp=5,.noise=.25};
 if(mode){s.fade[0]=(fade_t){4*F,(uint64_t)(4.6*F),mode==1?.4:0.,1.6,false};s.nfade=1;}
 if(mode==2){s.shift_at=(uint64_t)(4.6*F);s.shift=.37*(p?2560.:22880./9.);}
 uint64_t n=7*F;uint8_t *raw=generate(&s,n);FILE*f=fopen(argv[3],"wb");if(!f)return 2;
 int ok=fwrite(raw,1,n,f)==n;fclose(f);free(raw);return ok?0:2;}
''')
        subprocess.run(common + ['gcc', '-O2', '-std=c11', '-D_DEFAULT_SOURCE', '-Wall', '-Wextra',
            '-Werror', '-Imain', '/data/source.c', '/data/baseline.c', '-lm', '-o', '/data/source'], check=True)
        for standard in ('PAL', 'NTSC'):
            for mode, scene in enumerate(('clean', 'fade', 'return_phase')):
                name = standard + '_' + scene
                path = w / (name + '.bin')
                subprocess.run(common + ['/data/source', str(int(standard == 'PAL')), str(mode), '/data/' + path.name], check=True)
                raw = path.read_bytes()
                inputs.append(dict(name=name, sha256=hashlib.sha256(raw).hexdigest(), samples=len(raw)))
                for variant_name in originals:
                    result = json.loads(subprocess.check_output(common + ['/data/' + variant_name,
                        '/data/' + path.name, '192'], text=True))
                    rows.append(dict(case=name, variant=variant_name, **result))
                    print(name, variant_name, result, flush=True)
    if '--slices' not in sys.argv:
        for i in range(0,len(rows),2):
            assert rows[i]['state_trace']==rows[i+1]['state_trace']
            assert rows[i]['raw_writes']==rows[i+1]['raw_writes']==0
    out = dict(provenance='C5VRX by Twotoz/contributors; https://github.com/Twotoz/C5VRX; https://twotoz.github.io/C5VRX/',
        core_sha256=hashlib.sha256(source.encode()).hexdigest(),
        variants={k: hashlib.sha256(v.encode()).hexdigest() for k, v in originals.items()},
        phase_budget=1920, service_us=80, hardware_cycles_proven=False, inputs=inputs, rows=rows)
    (ROOT / ('research/data/sync_track_slice.json' if '--slices' in sys.argv else 'research/data/sync_track_rolling.json')).write_text(json.dumps(out, indent=2) + '\n')


if __name__ == '__main__':
    main()

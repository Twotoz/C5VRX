"""C5VRX/Twotoz: production decoder selection/restart/rollback/persistence.
Compiles the actual transport functions and control/boot selection code;
only ESP-IDF/peripheral/NVS boundaries are mocked. No physical timing proof.
"""
from pathlib import Path
import subprocess
import sys
import tempfile
import re
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'research'))
import u85q_lp


def function(source, signature):
    start = source.index(signature)
    brace = source.index('{',start)
    end,depth=brace+1,1
    while depth:
        depth+=(source[end]=='{')-(source[end]=='}')
        end+=1
    return source[start:end]


PREFIX = r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "transport_recovery.h"
#include "transport_guard.h"
#include "transport_lut.h"
#include "output_level.h"
#include "flightlog_format.h"
#include "p64_tables.h"
#include "u85q_table.h"
#include "u50p64_table.h"
#include "d32_table.h"
#define ESP_OK 0
#define BITSCRAMBLER_DIR_TX 0
#define BITSCRAMBLER_LL_INST_LEN_WORDS 9
static int s_bs,s_rx,BITSCRAMBLER;
typedef int esp_err_t;
static bool s_started,s_transport_fault,s_menu_active,s_lut_known,s_have_verified;
static bool s_tx_enabled,s_bs_enabled,s_rx_enabled;
static uint8_t s_demod=TRANSPORT_DEMOD_DEFAULT,s_verified_demod;
static unsigned s_output,s_affine,s_verified_output,s_output_clipped;
static unsigned s_lut_writes,s_lut_verify_fail,s_prog_verify_fail,s_restart_errors,s_rollback_fail,s_rollbacks;
static uint16_t s_lut_live[1024];
typedef struct {uint8_t prefix[300]; uint16_t lut[1024];} image_t;
static image_t s_program_work,hw;
#define s_lut_work s_program_work.lut
typedef struct {const uint8_t *program;unsigned output,clipped;} restart_t;
static transport_guard_t s_switch_guard;
static int fail_stage=-1,rollback_fail_stage=-1,current_stage=-1,loads,resets,cleans;
static bool rolling,fail_clean;
static int64_t now=10000000;
static int64_t esp_timer_get_time(void){return now;}
static int64_t stage_now(void){return now;}
static void stage_note(int stage,int64_t us){(void)us;current_stage=stage;}
static void stage_hooks(void){tr_hook_now=stage_now;tr_hook_note=stage_note;}
static unsigned output_word(void){return s_output==0&&s_affine?s_affine:s_output;}
static const char *transport_output_name(void){return "mock";}
static const char *esp_err_to_name(int err){(void)err;return "injected";}
static void settings_save_output(unsigned word){(void)word;}
static int failure(void){return current_stage>=0&&current_stage==(rolling?rollback_fail_stage:fail_stage);}
static int bitscrambler_enable(int bs){(void)bs;current_stage=TR_BS_ENABLE;return failure();}
static int bitscrambler_load_program(int bs,const image_t *p){(void)bs;current_stage=TR_PROGRAM_LOAD;++loads;if(failure())return 1;memcpy(&hw,p,sizeof(hw));return 0;}
static uint32_t bitscrambler_ll_instmem_read(int *bs,int dir,unsigned i,unsigned w){
 (void)bs;(void)dir;current_stage=TR_INSTRUCTION_VERIFY;uint32_t v;memcpy(&v,hw.prefix+12+(i*9+w)*4,4);return v^(failure()?1u:0u);
}
static unsigned lut_entry;
static void select_lut_entry(void *ctx,unsigned entry){(void)ctx;lut_entry=entry;}
static uint32_t read_lut_entry(void *ctx){(void)ctx;current_stage=TR_LUT_VERIFY;return hw.lut[lut_entry]^(failure()?1u:0u);}
static int bitscrambler_reset(int bs){(void)bs;current_stage=TR_BS_RESET;++resets;return failure();}
static int bitscrambler_start(int bs){(void)bs;current_stage=TR_BS_START;return failure();}
static int parlio_rx_unit_enable(int rx,bool flag){(void)rx;(void)flag;current_stage=TR_RX_ENABLE;return failure();}
static int enable_tx(void){current_stage=TR_TX_ENABLE;if(failure())return 1;s_tx_enabled=true;return 0;}
static int start_rx(void){current_stage=TR_RX_START;return failure();}
static int start_rings_tail(void){current_stage=TR_TX_START;return failure();}
static bool quiesce(void *ctx){(void)ctx;rolling=cleans++>0;s_started=false;s_tx_enabled=s_bs_enabled=s_rx_enabled=false;return !fail_clean;}
static bool resume_live(const uint16_t *lut);
static unsigned saves;
static bool have_saved;
static uint8_t saved;
static bool blob_store(const char *key,const void *data,size_t n){assert(!strcmp(key,"demod3")&&n==1);saved=*(const uint8_t*)data;have_saved=true;++saves;return true;}
static bool blob_load(const char *key,void *data,size_t n){assert(!strcmp(key,"demod3")&&n==1);if(!have_saved)return false;*(uint8_t*)data=saved;return true;}
'''
SUFFIX = r'''
static void reset(void){
 s_started=s_transport_fault=s_menu_active=s_lut_known=s_have_verified=false;
 s_tx_enabled=s_bs_enabled=s_rx_enabled=false;s_demod=TRANSPORT_DEMOD_DEFAULT;
 s_output=s_affine=s_verified_output=s_output_clipped=0;
 s_lut_writes=s_lut_verify_fail=s_prog_verify_fail=s_restart_errors=s_rollback_fail=s_rollbacks=0;
 s_switch_guard=(transport_guard_t){0};fail_stage=rollback_fail_stage=-1;loads=resets=cleans=0;rolling=fail_clean=false;current_stage=-1;now+=10000000;
}
static void start_attempt(void){loads=resets=cleans=0;rolling=false;}
int main(void){
 assert(TRANSPORT_DEMOD_P64==0&&TRANSPORT_DEMOD_U85Q==1&&TRANSPORT_DEMOD_U50P64==2&&TRANSPORT_DEMOD_D32==3&&TRANSPORT_DEMOD_D32_LP==4);
 assert(TRANSPORT_DEMOD_DEFAULT==TRANSPORT_DEMOD_D32&&FLOG_DEMOD_D32_R2_LP==6);
 for(unsigned d=0;d<TRANSPORT_DEMODS;++d){
  reset();have_saved=false;assert(transport_set_demod(d));assert(resume_live(NULL));
  assert(s_verified_demod==d&&s_lut_known&&s_started&&loads==1&&resets==1);
  assert(!memcmp(s_lut_live,demod_table(d),sizeof(s_lut_live)));
  unsigned bits=0;assert(transport_decoder_lut(&bits)==demod_table(d));assert(bits==(d==0||d==2?4u:5u));
  uint8_t id,level;transport_flog_identity(&id,&level);assert(id==(d==0?3:d==1?1:d==2?4:d==3?5:6));
  start_attempt();now+=TRANSPORT_SWITCH_GAP_US;unsigned old_saves=saves;demod_next();assert(saves==old_saves+1&&saved==(d+1)%TRANSPORT_DEMODS);
  /* A real subsequent boot reads the unchanged demod3 key. */
  reset();boot_restore();assert(s_demod==saved&&!s_started);assert(resume_live(NULL));
 }
 assert(!strcmp(transport_demod_name(4),"D32-R2-LP"));assert(!strcmp(transport_demod_short(4),"D32-LP"));
 /* Both directions; every SDK/readback failure restores program AND LUT. */
 for(int direction=0;direction<2;++direction)for(int stage=0;stage<TR_STAGES;++stage){
  reset();uint8_t from=direction?4:3,to=direction?3:4;assert(transport_set_demod(from)&&resume_live(NULL));
  start_attempt();fail_stage=stage;assert(!transport_set_demod(to));
  assert(!s_transport_fault&&s_demod==from&&s_verified_demod==from&&s_lut_known&&s_started&&s_rollbacks==1);
  assert(!memcmp(hw.prefix,demod_program(from),300));assert(!memcmp(hw.lut,demod_table(from),2048));
  assert(!transport_set_demod(to)); /* shared 5 second guard */
  start_attempt();fail_stage=-1;now+=TRANSPORT_SWITCH_GAP_US;assert(transport_set_demod(to));
 }
 /* Failed console selection does not persist; deferred menu selection does
  * persist the request, as before this PR (menu-exit rollback retries next boot). */
 reset();assert(resume_live(NULL));s_demod=3;start_attempt();fail_stage=TR_PROGRAM_LOAD;unsigned old_saves=saves;demod_next();assert(saves==old_saves&&s_demod==3);
 reset();assert(resume_live(NULL));start_attempt();s_menu_active=true;demod_next();assert(s_demod==4&&saved==4&&!loads);
 s_menu_active=false;assert(resume_live(NULL));assert(s_verified_demod==4);
 reset();assert(resume_live(NULL));start_attempt();s_menu_active=true;demod_next();assert(saved==4);
 s_menu_active=false;fail_stage=TR_PROGRAM_LOAD;assert(!resume_live(NULL));assert(s_demod==3&&saved==4);
 reset();boot_restore();assert(s_demod==4);assert(resume_live(NULL));
 /* Every manual and affine OUTPUT word preserves LUT token/phase fields. */
 for(unsigned word=0;word<OUTPUT_LEVELS+1;++word){
  reset();s_demod=4;
  if(word==OUTPUT_LEVELS){s_affine=output_affine(8,16);}else{s_output=word;}
  assert(resume_live(NULL));
  for(unsigned i=0;i<1024;++i){bool clipped;assert(hw.lut[i]==output_entry(d32_table[i],output_word(),&clipped));assert((hw.lut[i]&0xffc0)==(d32_table[i]&0xffc0));}
 }
 reset();assert(resume_live(NULL));start_attempt();fail_clean=true;assert(!transport_set_demod(4)&&s_transport_fault&&!s_lut_known);
 reset();assert(resume_live(NULL));start_attempt();fail_stage=rollback_fail_stage=TR_PROGRAM_LOAD;
 assert(!transport_set_demod(4)&&s_transport_fault&&!s_lut_known);assert(!transport_set_demod(3));
 reset();saved=255;have_saved=true;boot_restore();assert(s_demod==3);
 assert(!transport_set_demod(255));
 puts("PASS: actual selection, full program/LUT readback, every restart-stage rollback, guard, fault latch, menu, persistence and flight-log identity");
}
'''


def main():
    source=(ROOT/'main/transport.c').read_text()
    header=(ROOT/'main/receiver.h').read_text()
    enum=re.search(r'enum \{ TRANSPORT_DEMOD_P64.*?;',header).group()
    constants=enum+'\n'+ '\n'.join(line for line in header.splitlines() if line.startswith('#define TRANSPORT_DEMOD'))+'\n'
    with tempfile.TemporaryDirectory() as directory:
        path=Path(directory)
        images=''
        for symbol,file in (('s_p64','p64'),('s_u85q','u85q'),('s_u50p64','u50p64'),('s_d32','d32'),('s_d32_lp','d32_lp')):
            inst,meta,lut=u85q_lp.assemble(ROOT/f'main/{file}.bsasm')
            blob=u85q_lp._bsasm().insts_to_binary(inst,meta,lut)
            images+=f'static const uint8_t {symbol}[]={{'+','.join(map(str,blob))+'};\n'
        code='#include <stdint.h>\n'+constants+images+PREFIX
        for sig in ('static const void *demod_program(', 'static const uint16_t *demod_table(',
                    'static const void *tracker_program(', 'static bool program_words(',
                    'const char *transport_demod_name(', 'const char *transport_demod_short(',
                    'const char *transport_program_name(', 'uint8_t transport_demod(',
                    'const uint16_t *transport_decoder_lut(', 'static bool restart_stage(',
                    'static bool resume_live(const uint16_t *restore_lut)\n',
                    'bool transport_set_demod(', 'void transport_flog_identity('):
            code+=function(source,sig)+'\n'
        code+=function((ROOT/'main/control.c').read_text(),'static void demod_next(')+'\n'
        boot=(ROOT/'main/main.c').read_text()
        start=boot.index('        uint8_t demod;')
        end=boot.index('\n    }',start)
        code+='static void boot_restore(void){\n'+boot[start:end]+'\n}\n'+SUFFIX
        (path/'test.c').write_text(code)
        subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-I'+str(ROOT/'main'),str(path/'test.c'),'-o',str(path/'test')],check=True)
        subprocess.run([str(path/'test')],check=True,stdout=subprocess.DEVNULL)
    print('PASS: D32-R2-LP production restart/rollback/persistence runtime')


if __name__=='__main__':
    main()

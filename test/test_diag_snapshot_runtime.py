"""C5VRX/Twotoz: actual diagnostic history snapshot without duplicate records.

Fixture changes the live history immediately at unlock; reports must still use
one coherent snapshot and perform sorting/printing outside the critical section.
"""
from pathlib import Path
import os
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]
PREFIX=r'''
#include <assert.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "diag_logic.h"
#define DIAG_HISTORY 64u
#define DIAG_EVENTS 4u
'''
HARNESS=r'''
static diag_record_t s_hist[DIAG_HISTORY];
static diag_event_t s_events[DIAG_EVENTS],expected_events[DIAG_EVENTS];
static uint32_t s_seq,s_event_seq,s_dropouts[DIAG_CATEGORIES];
static int s_lock;static bool locked;static unsigned event_count,expected_event_count;
static char text[4096];static size_t text_bytes;
static void enter(int *lock){assert(lock==&s_lock&&!locked);locked=true;}
static void leave(int *lock){
 assert(lock==&s_lock&&locked);locked=false;
 /* Model another task updating the recorder as soon as the snapshot ends. */
 memset(s_hist,0,sizeof(s_hist));memset(s_events,0,sizeof(s_events));
 memset(s_dropouts,0,sizeof(s_dropouts));s_seq=s_event_seq=0;
}
#define taskENTER_CRITICAL(lock) enter(lock)
#define taskEXIT_CRITICAL(lock) leave(lock)
static int record_printf(const char *format,...){
 assert(!locked);va_list args;va_start(args,format);
 int result=vsnprintf(text+text_bytes,sizeof(text)-text_bytes,format,args);va_end(args);
 assert(result>=0&&(size_t)result<sizeof(text)-text_bytes);text_bytes+=(size_t)result;return result;
}
static void print_event(const diag_event_t *event){
 assert(!locked&&event_count<expected_event_count);
 assert(!memcmp(event,&expected_events[event_count],sizeof(*event)));++event_count;
}
#define printf record_printf
'''
SUFFIX=r'''
#undef printf
static int compare(const void *a,const void *b){int x=*(const int*)a,y=*(const int*)b;return (x>y)-(x<y);}
int main(void){
 for(unsigned trial=0;trial<516;++trial){
  s_seq=trial<513?trial:UINT32_MAX-(trial-513u);
  s_event_seq=trial%13u;locked=false;event_count=0;text_bytes=0;text[0]=0;
  for(unsigned k=0;k<DIAG_HISTORY;++k){
   s_hist[k]=(diag_record_t){.video_pct=(uint8_t)((k*19u+trial)%101u),
    .gain=(uint8_t)((trial+k*17u)%90u),.gain_max=(uint8_t)(80u+k%4u),
    .cn_x10=(int16_t)(k%7u==0?-990:(int)(k*97u+trial)%450-50)};
  }
  for(unsigned k=0;k<DIAG_EVENTS;++k){
   memset(&s_events[k],0,sizeof(s_events[k]));s_events[k].t_s=trial+k;
   s_events[k].duration_s=trial*3u+k;s_events[k].category=(int)(k%DIAG_CATEGORIES);
   s_events[k].drop=s_hist[k];s_events[k].last_ok=s_hist[k+4u];s_events[k].open=(k&1u)!=0;
  }
  uint32_t events=s_event_seq,drops[DIAG_CATEGORIES];
  for(unsigned k=0;k<DIAG_CATEGORIES;++k)s_dropouts[k]=drops[k]=trial*(k+1u);
  unsigned n=s_seq<60u?s_seq:60u,m=s_seq<10u?s_seq:10u,ok=0,max=0;
  int cn[10],gain[10];
  for(unsigned k=0;k<n;++k){const diag_record_t *r=&s_hist[(s_seq-1u-k)%DIAG_HISTORY];
   ok+=r->video_pct>=50u;max+=r->gain+1u>=r->gain_max;
   if(k<m){cn[k]=r->cn_x10;gain[k]=r->gain;}
  }
  qsort(cn,m,sizeof(int),compare);qsort(gain,m,sizeof(int),compare);
  expected_event_count=0;
  for(uint32_t k=events>DIAG_EVENTS?events-DIAG_EVENTS:0;k<events;++k)
   expected_events[expected_event_count++]=s_events[k%DIAG_EVENTS];
  report_history();
  assert(!locked&&event_count==expected_event_count);
  unsigned got_n,got_ok,got_max;unsigned long got_events;
  char *history=strstr(text,"RANGE_DIAG history seconds=");assert(history);
  assert(sscanf(history,"RANGE_DIAG history seconds=%u sync_evidence_ok_pct=%u at_max_gain_pct=%u dropouts=%lu",
   &got_n,&got_ok,&got_max,&got_events)==4);
  assert(got_n==n&&got_ok==(n?ok*100u/n:0)&&got_max==(n?max*100u/n:0)&&got_events==events);
  if(m){unsigned got_m;int got_median,got_min,got_gain;
   assert(sscanf(text,"RANGE_DIAG last_10s seconds=%u cn_est_x10 median=%d min=%d gain median=%d",
    &got_m,&got_median,&got_min,&got_gain)==4);
   assert(got_m==m&&got_median==cn[m/2]&&got_min==cn[0]&&got_gain==gain[m/2]);
  }else assert(!strstr(text,"last_10s"));
  for(unsigned k=0;k<DIAG_CATEGORIES;++k){char expected[100];
   snprintf(expected,sizeof(expected)," %s=%lu",diag_category_name((int)k),(unsigned long)drops[k]);
   assert(strstr(history,expected));
  }
 }
 puts("PASS: 516 coherent diagnostic snapshots, rolling histories, medians, events and unlocked printing");
}
'''

def main():
    source=(ROOT/'main/diag.c').read_text()
    a=source.index('typedef struct {\n    uint32_t t_s, duration_s;')
    event=source[a:source.index('} diag_event_t;',a)+len('} diag_event_t;')]
    a=source.index('    static diag_event_t ev[DIAG_EVENTS];',source.index('void diag_report(void)'))
    tail=source[a:source.rindex('}')]
    assert 'diag_record_t h[' not in source[source.index('void diag_report(void)'):]
    with tempfile.TemporaryDirectory() as directory:
        path=Path(directory);unit=path/'run.c';binary=path/'run'
        unit.write_text(PREFIX+event+HARNESS+'static void report_history(void){\n'+tail+'}\n'+SUFFIX)
        subprocess.run([os.environ.get('CC','cc'),'-O2','-std=c11','-Wall','-Wextra','-Werror',
            '-I',str(ROOT/'main'),str(unit),'-o',str(binary)],check=True)
        subprocess.run([str(binary)],check=True)

if __name__=='__main__':main()

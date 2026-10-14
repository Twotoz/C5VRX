"""C5VRX by Twotoz/contributors: actual transport raw-copy wrapper ownership."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def function(source, signature):
    start = source.index(signature)
    brace = source.index('{', start)
    depth, end = 1, brace + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end] + '\n'


class RawAccessRuntime(unittest.TestCase):
    def test_actual_wrappers(self):
        source = (ROOT / 'main/transport.c').read_text()
        prefix = r'''
#include <assert.h>
#include <stddef.h>
#include <stdio.h>
#include "raw_access.h"
#define PROBE_BYTES 256
static raw_access_t s_raw_access;
static unsigned depth, calls, mode;
static uint32_t s_stream_generation=1,s_control_epoch=2,s_gain_epoch=3,phy=4;
static bool s_rx_enabled=true,s_stream_generation_overflow;
static uint32_t phy_generation(void) {return phy;}
static void vTaskSuspendAll(void) {++depth;}
static int xTaskResumeAll(void) {assert(depth);--depth;return 0;}
static bool inner(void) {
    ++calls; assert(!depth && raw_access_readers(&s_raw_access)==1);
    raw_access_token_t writer;
    assert(!raw_access_write_begin(&s_raw_access,(raw_access_context_t){0},true,&writer));
    if(mode==1) ++s_gain_epoch;
    if(mode==2) s_rx_enabled=false;
    if(mode==3) ++s_stream_generation;
    return mode!=4;
}
static bool copy_control_window_unleased(uint8_t*d,size_t n){(void)d;(void)n;return inner();}
static bool copy_probe_window_unleased(uint8_t*d,int*n){(void)d;(void)n;return inner();}
static bool copy_sentinel_window_unleased(uint8_t*d){(void)d;return inner();}
static bool copy_snapshot_unleased(uint8_t*d){(void)d;return inner();}
static bool copy_recent_endpoints_unleased(uint8_t*d,size_t n){(void)d;(void)n;return inner();}
'''
        helpers = ''.join(function(source, name) for name in [
            'static raw_access_context_t transport_raw_context(',
            'static bool transport_raw_read_begin(', 'static bool transport_raw_read_end('])
        wrappers = ''.join(function(source, name) for name in [
            'bool copy_control_window(', 'bool copy_probe_window(',
            'bool copy_sentinel_window(', 'bool copy_snapshot(', 'bool copy_recent_endpoints('])
        main = r'''
int main(void) {
    uint8_t data[PROBE_BYTES];
    for(mode=0;mode<5;++mode) for(unsigned which=0;which<5;++which) {
        s_rx_enabled=true; bool result;
        switch(which) {
            case 0:result=copy_control_window(data,64);break;
            case 1:result=copy_probe_window(data,NULL);break;
            case 2:result=copy_sentinel_window(data);break;
            case 3:result=copy_snapshot(data);break;
            default:result=copy_recent_endpoints(data,32);break;
        }
        assert(result==(mode==0)); assert(!depth && !raw_access_readers(&s_raw_access));
    }
    assert(calls==25);
    raw_access_token_t writer;
    assert(raw_access_write_begin(&s_raw_access,transport_raw_context(),true,&writer));
    assert(!copy_control_window(data,64) && calls==25);
    assert(raw_access_write_end(&s_raw_access,&writer,transport_raw_context(),true));
    s_stream_generation_overflow=true;
    assert(!copy_snapshot(data) && calls==25 && !raw_access_readers(&s_raw_access));
    puts("PASS: all five actual raw-copy wrappers release on failures/context changes and refuse an exclusive writer");
}
'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            src, binary = path / 'runtime.c', path / 'runtime'
            src.write_text(prefix + helpers + wrappers + main)
            subprocess.run([os.environ.get('CC', 'cc'), '-O2', '-std=c11', '-Wall', '-Wextra',
                            '-Werror', '-I', str(ROOT / 'main'), str(src), '-o', str(binary)], check=True)
            subprocess.run([str(binary)], check=True)


if __name__ == '__main__':
    unittest.main()

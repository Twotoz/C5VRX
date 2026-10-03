#!/usr/bin/env python3
"""Source/LUT and compiled snapshot-estimator regressions; no RF range claim."""
import ctypes as ct
import math
from pathlib import Path
import random
import subprocess
import tempfile
import generate_phase8 as gen

HERE = Path(__file__).resolve().parent
FIELDS = [('pairs',ct.c_uint),('ambiguous_pm',ct.c_uint),('origin_pm',ct.c_uint),
          ('clip_pm',ct.c_uint),('mean_i_mcell',ct.c_int),('mean_q_mcell',ct.c_int),
          ('pulses',ct.c_uint),('repeated',ct.c_uint),('period_raw',ct.c_uint)]
FIELDS += [(x,ct.c_int) for x in ('sync_bins','blank_bins','span_bins','sync_mad_bins',
             'blank_mad_bins','sync_mv','blank_mv','sync_depth_mv')]
FIELDS += [('suggested_scale_q10',ct.c_uint),('levels_valid',ct.c_bool)]
class Stats(ct.Structure):
    _fields_ = FIELDS


def make_raw(period=2542, radius=4, sigma=0, offset=0, depth=1, dc=0, seed=0):
    rng=random.Random(seed);angle=0;raw=[]
    for k in range(4092):
        in_sync=(k-45)%period<188
        # Full sync separation 2 MHz = 38.4 Phase8 bins over 75 ns.
        frequency=-2e6*depth if in_sync else 0
        angle+=2*math.pi*frequency/40e6
        angle+=2*math.pi*offset/40e6
        i=max(-8,min(7,math.floor(radius*math.cos(angle)+dc+rng.gauss(0,sigma))))
        q=max(-8,min(7,math.floor(radius*math.sin(angle)+rng.gauss(0,sigma))))
        raw.append(((i&15)<<4)|(q&15))
    return bytes(raw)


def main():
    volts=gen.voltages()
    assert 0.95<max(volts)<1.15
    transfers=(('hr100',''),('legacy','_legacy'),('cvbs150','_cvbs150'))
    for history in (False,True):
        fixed=gen.words_for(history)
        mode='history' if history else 'static'
        for transfer,suffix in transfers:
            other=gen.words_for(history,False,transfer)
            # Phase decode, sign winding and all instruction routes must survive.
            for i in range(1024):
                assert (fixed[i]&~63)==(other[i]&~63)
                if (i//256)&1: assert fixed[i]==other[i]
            body=gen.build(history).split('accumulate:',1)[1]
            assert body==gen.build(history,False,transfer).split('accumulate:',1)[1]
            path=HERE/f'c5vrx4_phase8_{mode}{suffix}.bsasm'
            assert path.read_text()==gen.build(history,False,transfer)
    # One Phase8 bin over 75 ns is 52.083 kHz: 1/192 V at 0.100 V/MHz,
    # 1/128 V at 0.150 V/MHz.
    for transfer,blank,per_bin in (('hr100',.42,1/192),('cvbs150',.3,1/128)):
        codes=gen.dac_codes(False,transfer)
        for index in range(256):
            delta=gen.transfer_delta(index)
            target=max(min(volts),min(max(volts),blank+delta*per_bin))
            assert abs(volts[codes[index]]-target)==min(abs(v-target) for v in volts)
            if index>>6==3: assert codes[index]==codes[3<<6]
        pairs=sorted((gen.transfer_delta(i),volts[codes[i]]) for i in range(192))
        assert all(a[1]<=b[1] for a,b in zip(pairs,pairs[1:]))
    # Saturation is final voltage clipping, never modulo wrapping.
    hr,c150=gen.dac_codes(),gen.dac_codes(False,'cvbs150')
    assert all(c150[i]==63 for i in range(256) if gen.transfer_delta(i)>150)
    assert all(c150[i]==0 for i in range(256) if gen.transfer_delta(i)<-60)
    assert all(hr[i]==63 for i in range(256) if gen.transfer_delta(i)>118)
    assert all(hr[i]==0 for i in range(256) if gen.transfer_delta(i)<-84)
    # HR100 headroom: nominal sync (-2 MHz = -38 bins) stays clear of the
    # DAC floor for a 2-MHz downward shift (-77 bins); CVBS150 does not.
    floor=lambda codes,d: min(volts[codes[i]] for i in range(192) if gen.transfer_delta(i)>=d)
    assert floor(hr,-78)>0.0 and floor(c150,-78)==0.0
    # Invalid scope calibration fails instead of producing arbitrary firmware.
    original=gen.CALIBRATION
    with tempfile.TemporaryDirectory() as tmp:
        temp=Path(tmp)
        gen.CALIBRATION=temp/'bad.json'
        gen.CALIBRATION.write_text('{"load_ohms":150,"volts_by_code":[]}')
        try:
            gen.voltages()
        except ValueError: pass
        else: raise AssertionError('unqualified load accepted')
        gen.CALIBRATION=original
        lib=temp/'monitor.so'
        subprocess.run(['gcc','-std=c11','-O2','-Wall','-Wextra','-Werror','-shared','-fPIC',
                        str(HERE/'cvbs_monitor.c'),'-o',str(lib)],check=True)
        analyze=ct.CDLL(str(lib)).c5v4_cvbs_analyze
        analyze.argtypes=[ct.c_void_p,ct.c_size_t,ct.c_bool,ct.c_uint,ct.POINTER(Stats)]
        def inspect(raw,history=False,transfer=0):
            out=Stats();buf=ct.create_string_buffer(raw)
            analyze(buf,len(raw),history,transfer,ct.byref(out));return out
        for period in (2542,2560):
            for history in (False,True):
                for phase_offset in (0,100e3,-100e3):
                    raw=make_raw(period=period,offset=phase_offset)
                    before=bytes(raw)
                    new=inspect(raw,history);old=inspect(raw,history,1)
                    c150=inspect(raw,history,2)
                    assert raw==before
                    assert new.levels_valid, (period,history,phase_offset,new.pulses,new.repeated)
                    assert abs(new.period_raw-period)<=6
                    assert 160<=new.sync_depth_mv<=270, new.sync_depth_mv
                    assert 240<=c150.sync_depth_mv<=400, c150.sync_depth_mv
                    assert old.sync_depth_mv<new.sync_depth_mv*.9
                    assert 768<=new.suggested_scale_q10<=1536
        # Carrier/blank shifted down 1.5 MHz (CFO, drift or an APL-dependent
        # centre): CVBS150 crushes sync against 0 V, HR100 keeps its depth.
        for history in (False,True):
            shifted=make_raw(offset=-1.5e6)
            hr_s,c150_s=inspect(shifted,history),inspect(shifted,history,2)
            assert hr_s.levels_valid and c150_s.levels_valid
            assert hr_s.sync_mv>0 and 160<=hr_s.sync_depth_mv<=270, hr_s.sync_depth_mv
            assert c150_s.sync_mv==0 and c150_s.sync_depth_mv<120, c150_s.sync_depth_mv
        # Smaller phase separation must be visible as smaller sync span,
        # rather than silently applied as an unbounded output gain change.
        weak=inspect(make_raw(depth=.5));strong=inspect(make_raw())
        assert weak.levels_valid and weak.span_bins<strong.span_bins
        assert weak.sync_depth_mv<strong.sync_depth_mv
        assert weak.suggested_scale_q10==1536
        assert not inspect(bytes([0x22])*4092).levels_valid
        assert not inspect(bytes([0])*4092).levels_valid
        assert not inspect(bytes([0x22])*100).levels_valid
        rng=random.Random(157)
        for _ in range(100):
            assert not inspect(bytes(rng.randrange(256) for _ in range(4092))).levels_valid
        # A periodic video-frequency block with no correct H-sync widths
        # cannot be accepted just from its low/high percentiles.
        chopped=bytes(0x22 if (k//20)%2 else 0xDD for k in range(4092))
        assert not inspect(chopped).levels_valid
        # Check diagnostics expose offset dominance; it is not auto-subtracted.
        shifted=inspect(make_raw(radius=1,dc=3))
        assert shifted.mean_i_mcell>2000 and not shifted.levels_valid
    print('PASS: HR100/CVBS150/legacy transfers, saturation and sync headroom, unchanged '
          'Phase8/winding/routes, PAL/NTSC/CFO snapshots, shrink telemetry and 100 noise refusals')

if __name__=='__main__': main()

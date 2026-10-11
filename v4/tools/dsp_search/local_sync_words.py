"""C5VRX/Twotoz U85Q finite-state synchronization feasibility.

Extends historical synthetic-IQ/flywheel/menu work. No live descriptor
splice, raw-ring write or timing acquisition is implemented. This proves
only that a bounded precomputed IQ word can establish a known decoder state
and then emit exact sync/blank DAC codes through the unchanged U85Q engine.
"""
from collections import deque
import json
from pathlib import Path
import numpy as np
import compile_overlay as C
import local_joint_observation as J
import overlay_fsm as O


def find_reset(next_state, tokens):
    start=tuple(range(32));queue=deque([(start,())]);seen={start}
    while queue:
        states,word=queue.popleft()
        for t in tokens:
            result=tuple(sorted(set(next_state[list(states),t].tolist())))
            if len(result)==1:return word+(int(t),),result[0]
            if result not in seen:
                seen.add(result);queue.append((result,word+(int(t),)))
        if len(seen)>100000:raise RuntimeError('bounded reset search exhausted')
    raise RuntimeError('no synchronizing word')


def template():
    m=J.baseline();lut=np.asarray(m['lut'],np.int64)
    trans=(lut&63).reshape(32,32);nxt=((lut>>6)&31).reshape(32,32)
    # Choose a received-cell representative for every reachable token.
    representatives={}
    for raw in range(256):
        addr=(raw>>4)|((raw&15)<<4)
        token=int(lut[addr]>>11)
        representatives.setdefault(token,raw)
    reset,state=find_reset(nxt,sorted(representatives))
    word=list(reset);desired=[None]*len(reset)
    # 34 spans before sync = 1.7us; exactly 94 spans sync = 4.7us.
    # 34+94+8 =136 spans/272 raw bytes, divisible by four for DMA alignment.
    for count,target in ((34-len(reset),25),(94,4),(8,25)):
        for _ in range(count):
            possible=[t for t in representatives if trans[state,t]==target]
            if not possible:raise RuntimeError(('cannot emit exact DAC code',state,target))
            t=min(possible);word.append(t);desired.append(target);state=int(nxt[state,t])
    raw=np.array([v for t in word for v in (representatives[t],0)],np.uint8)
    return m,raw,np.asarray(desired,dtype=object),reset


def verify():
    import bs_model as BS
    from functools import lru_cache
    m,raw,desired,reset=template();source=C.build(m)
    lut=np.asarray(m['lut'],np.int64);nxt=((lut>>6)&31).reshape(32,32)
    old=BS.parse;BS.parse=lru_cache(maxsize=1)(old)
    try:
        for initial in range(32):
            output=np.array(BS.simulate(source,np.r_[raw,[0,0]],len(raw)+2,
                          initial=(0,0,0,initial<<6),wrap_rom=True))&63
            for k,wanted in enumerate(desired):
                if wanted is not None:
                    assert output[2*k+2]==wanted and output[2*k+3]==wanted
        states=np.arange(32)
        for t in reset:states=nxt[states,t]
        assert len(set(states.tolist()))==1
    finally:BS.parse=old
    return dict(initial_states=32,reset_tokens=list(reset),reset_spans=len(reset),
                reset_time_ns=50*len(reset),template_bytes=len(raw),
                sync_width_us=4.7,front_porch_us=1.7,
                exact_sync_dac=4,exact_blank_dac=25,
                status='source_dataflow_only',
                missing=['received_H_and_V_timing_acquisition','DAC_voltage_validation',
                         'safe_continuous_DMA_descriptor_ownership',
                         'PAL_NTSC_field_templates','colour_and_recovery_acceptance'])


if __name__=='__main__':
    result=verify()
    target=J.ROOT/'docs/data/local_joint/sync-word-proof.json'
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(result,indent=1))
    print(json.dumps(result))

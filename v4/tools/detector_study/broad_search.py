#!/usr/bin/env python3
"""Offline architecture/quantizer search for C5VRX by Twotoz/contributors.

No VLP initialization or transfer constraints. Searches a declared family of
TX-only LUT8 schedules, not all possible BitScrambler programs. Thousands of
trained candidates are not a global optimum proof. Firmware is never modified.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bs_model as BS
import optimize_pair as P
import optimize_video as Q
D = P.D
DAC_VOLTS = np.array([sum(1/r for bit,r in enumerate((8200,3900,2000,1000,470,240)) if code&(1<<bit)) for code in range(64)])
SCENARIOS = [(1e6, 6.7e6, 'random', 1, 0j, 1),
             (.25e6, 5.5e6, 'bars', .6, .2-.3j, 1.1),
             (2e6, 8e6, 'multitone', 1.5, 0j, .9)]


def layouts():
    # One decode and one map lookup. The middle raw bits are free wires,
    # not additional LUT lookups. Trade previous precision for middle context.
    for n in (16, 24, 32, 40, 48, 56):
        for shift in (1, 2, 3):
            for bits in ((), (3,), (7,), (3, 7), (2, 6)):
                if n % (1 << shift):
                    continue
                parents = n >> shift
                rows = parents * (1 << len(bits))
                if rows <= 28:
                    yield dict(current_tokens=n, previous_shift=shift,
                               previous_tokens=rows, middle_bits=list(bits),
                               stride=64, span_ns=50)


def context(raw, bits):
    out = np.zeros(np.shape(raw), dtype=int)
    for j, bit in enumerate(bits):
        out |= ((raw.astype(int) >> bit) & 1) << j
    return out


def indices(raw, m):
    e = np.array(m['encoder']); t = e[raw[::2]]
    row = (t[:-1] >> m['previous_shift']) * (1 << len(m['middle_bits']))
    row += context(raw[1::2][:-1], m['middle_bits'])
    return row * m['current_tokens'] + t[1:]


def compile_model(m):
    n, rows = m['current_tokens'], m['previous_tokens']
    shift, bits = m['previous_shift'], m['middle_bits']
    assert n in (16,24,32,40,48,56) and n % (1 << shift) == 0
    assert rows == (n >> shift) * (1 << len(bits)) and rows <= 28
    assert len(bits) <= 2 and len(set(bits)) == len(bits) and all(0 <= b < 8 for b in bits)
    enc, tab = m['encoder'], m['map']
    assert len(enc) == 256 and all(type(v) is int and 0 <= v < n for v in enc)
    assert len(tab) == rows and all(len(r) == n for r in tab)
    assert all(type(v) is int and 0 <= v < 64 for r in tab for v in r)
    lut = [0]*2048
    for k, row in enumerate(tab): lut[k*64:k*64+n] = row
    lut[1792:] = enc
    s = '# C5VRX by Twotoz and contributors: offline broad-search candidate.\n'
    s += '# RX40/raw32K/TX-only; two bundles; DAC6 [D,D]; first two pairs startup.\n'
    s += 'cfg prefetch true\ncfg eof_on downstream\ncfg trailing_bytes 0\ncfg lut_width_bits 8\nlut ' + ' '.join(map(str,lut))+'\n'
    pb = (n >> shift).bit_length()-1
    if (1 << pb) < (n >> shift): pb += 1
    for k in range(4):
        s += f'controller_{k}:\n    set 0..5 L0..L5,\n    set 8..13 L0..L5,\n    set 16..23 0..7,\n    set 24..26 H,\n'
        for j, b in enumerate(bits): s += f'    set {27+j} {8+b},\n'
        s += '    read 16,\n    write 16,\n    nop\n'
        s += f'worker_{k}:\n    set 16..21 L0..L5,\n'
        for j in range(len(bits)):
            s += f'    set {22+j} B{11+j},\n    set {27+j} O{27+j},\n'
        for j in range(pb): s += f'    set {22+len(bits)+j} B{shift+j},\n'
        # Clear unused address bits: leftover decode prefix would select padding.
        for j in range(22+len(bits)+pb,27): s += f'    set {j} L,\n'
        s += '    ldctib\n'
    return s


def initialize(n, family, rng):
    i,q = D.cells(np.arange(256)); x=np.c_[i+.5,q+.5]
    a=np.angle(x[:,0]+1j*x[:,1])%(2*np.pi); r=np.linalg.norm(x,axis=1)
    if family == 'phase':
        enc=np.floor((a/(2*np.pi)*n+rng.uniform(0,1))%n).astype(int)
    elif family == 'polar':
        rings=int(rng.choice((2,4))); sectors=n//rings
        radius=np.quantile(r, rng.uniform(.1,.9,rings-1)); radius.sort()
        enc=np.searchsorted(radius,r)*sectors+np.floor((a/(2*np.pi)*sectors+rng.uniform(0,1))%sectors).astype(int)
    else:
        if family == 'cartesian':
            angle=rng.uniform(0,2*np.pi); x=x@np.array([[np.cos(angle),-np.sin(angle)],[np.sin(angle),np.cos(angle)]])
        x=x*np.array([rng.uniform(.5,2),rng.uniform(.5,2)])
        centres=x[rng.choice(256,n,replace=False)].copy()
        for _ in range(5):
            enc=np.argmin(np.sum((x[:,None]-centres[None,:])**2,axis=2),axis=1)
            for k in range(n):
                if np.any(enc==k): centres[k]=x[enc==k].mean(0)
        # Labels determine which cells share retained previous state. Try both
        # angular ordering and genuinely arbitrary partitions.
        if family=='cartesian':
            order=np.argsort(np.angle(centres[:,0]+1j*centres[:,1])); inv=np.empty(n,int);inv[order]=np.arange(n);enc=inv[enc]
        else: enc=rng.permutation(n)[enc]
    return enc


def histogram(bits, sequences):
    size=(1 << len(bits))*65536;w=np.zeros(size);s=w.copy();q=w.copy()
    for raw,y,weight in sequences:
        t=raw[::2].astype(int)
        ix=context(raw[1::2][:-1],bits)*65536+t[:-1]*256+t[1:]
        ix,y=ix[1500:],y[1500:]
        w+=weight*np.bincount(ix,minlength=size)
        s+=weight*np.bincount(ix,weights=y,minlength=size)
        q+=weight*np.bincount(ix,weights=y*y,minlength=size)
    return tuple(v.reshape(-1,256,256) for v in (w,s,q))


def fit(m,h):
    w,s,q=h;n=m['current_tokens'];e=np.array(m['encoder']);shift=m['previous_shift'];c=len(w)
    ix=(((e[:,None]>>shift)*c+np.arange(c)[:,None,None])*n+e[None,:]).ravel()
    k=m['previous_tokens']*n
    cw=np.bincount(ix,weights=w.ravel(),minlength=k);cs=np.bincount(ix,weights=s.ravel(),minlength=k)
    # No VLP teacher. Empty bins use analytic cell-centre phase difference.
    i,j=D.cells(np.arange(256));phase=np.angle(i+.5+1j*(j+.5))*128/np.pi
    prior=np.clip((D.wrap(phase[None,:]-phase[:,None])-P.OFFSET)*P.SCALE,0,63)
    ps=np.bincount(ix,weights=np.broadcast_to(prior,w.shape).ravel(),minlength=k)
    pc=np.bincount(ix,minlength=k)
    table=np.divide(cs,cw,out=np.divide(ps,pc,out=np.full(k,32.),where=pc>0),where=cw>0)
    table=np.rint(np.clip(table,0,63)).astype(int)
    loss=float(np.sum(w.ravel()*table[ix]**2-2*s.ravel()*table[ix]+q.ravel())/w.sum())
    return table.reshape(m['previous_tokens'],n).tolist(),loss


def source_check(m):
    source=compile_model(m);rng=np.random.default_rng(1601);raw=rng.integers(0,256,8192,dtype=np.uint8)
    cfg,lut,blocks,_=BS.parse(source);assert len(lut)==2048 and len(blocks)==8
    stats={};out=BS.simulate(source,raw.tolist(),len(raw),stats=stats,wrap_rom=True)
    expected=np.array(m['map']).ravel()[indices(raw,m)]
    assert out[4::2]==expected[:len(out[4::2])].tolist(), m['name']
    assert all(a==b and a<64 for a,b in zip(out[::2],out[1::2]))
    assert stats['bundles']==len(out)-1
    return source


def decode(raw,m):
    y=np.array(m['map']).ravel()[indices(raw,m)]
    return D.goggle(np.pad(np.repeat(DAC_VOLTS[y],2),(0,len(raw)-2*len(y))))


def unwrap75(raw):
    # Same generated STD150 LUT and physical resistor DAC as the old firmware.
    gen=D.gen;words=np.array(gen.words_for(False,transfer='std150'));phase=np.array(gen.decoder(False)[0])
    k=(len(raw)-1)//3;p,a,b,c=(raw[j:3*k+j:3].astype(int) for j in range(4))
    sign=lambda x:((x>>7)&1)|(((x>>3)&1)<<1)
    cls=(words[sign(p)|(sign(a)<<2)|(sign(b)<<4)|(sign(c)<<6)]>>6)&3
    e=(((128+phase[c])&254)+((-phase[p])&254))&255
    codes=words[(e>>2)|(cls<<6)]&63
    y=np.repeat(DAC_VOLTS[codes],3)
    return D.goggle(np.pad(y,(0,len(raw)-len(y))))


def evaluate(models,seeds,full=False):
    result=[]
    for seed in seeds:
        for cfo,dev,kind,amp,dc,skew in SCENARIOS:
            sig,noise,_,ire=P.stream(seed,n=131072 if full else 65536,cfo=cfo,deviation=dev,kind=kind);truth=D.goggle(ire)
            for cnr in P.CNRS:
                raw=P.raw_at(sig,noise,cnr,amp,dc,skew)
                for m in models:
                    if m['name']=='Unwrap75': y=unwrap75(raw)
                    elif m['name']=='adjacent40': y=D.DESIGNS['adj40'](raw)
                    elif m['name']=='HC50':
                        phase=P.V.H.endpoint_phases(raw,True,True)
                        codes=((128+phase[1:]-phase[:-1])%256)>>2
                        y=D.goggle(np.pad(np.repeat(DAC_VOLTS[codes],2),(0,len(raw)-2*len(codes))))
                    elif 'middle_bits' in m: y=decode(raw,m)
                    else:
                        ix=Q.indices(raw,m);codes=np.array(m['map']).ravel()[ix]
                        y=D.goggle(np.pad(np.repeat(DAC_VOLTS[codes],2),(0,len(raw)-2*len(codes))))
                    if full: D.truth=truth;sinad,bands,clicks=D.score(y)
                    else: sinad,clicks=P.quick_score(y,truth);bands=[]
                    result.append(dict(model=m['name'],seed=seed,kind=kind,cnr=cnr,sinad=float(sinad),clicks=float(clicks),bands=list(map(float,bands))))
    return result


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,required=True);ap.add_argument('--variants',type=int,default=2048);ap.add_argument('--shortlist',type=int,default=24);a=ap.parse_args()
    assert a.variants>=4 and a.shortlist>=1;a.output.mkdir(parents=True,exist_ok=True)
    rng=np.random.default_rng(1201);sequences=[]
    for seed in (1301,1302):
        for cfo,dev,kind,amp,dc,skew in SCENARIOS:
            sig,noise,y,_=P.stream(seed,n=32768,cfo=cfo,deviation=dev,kind=kind)
            for cnr,weight in zip(P.CNRS,(1,3,3,2,1,1)):
                sequences.append((P.raw_at(sig,noise,cnr,amp,dc,skew),y,weight))
    ls=list(layouts());hist={tuple(l['middle_bits']):None for l in ls}
    for bits in hist:hist[bits]=histogram(bits,sequences)
    candidates=[];seen=set();families=('phase','polar','cartesian','random-clusters')
    for j in range(a.variants):
        m=dict(ls[(j//4)%len(ls)]);family=families[j%4]
        m.update(name=f'broad-{j:05d}-{family}',family=family,encoder=initialize(m['current_tokens'],family,rng).tolist())
        m['map'],m['training_loss']=fit(m,hist[tuple(m['middle_bits'])])
        digest=hashlib.sha256(json.dumps([m['current_tokens'],m['previous_shift'],m['middle_bits'],m['encoder'],m['map']]).encode()).hexdigest()
        if digest in seen: continue
        seen.add(digest);m['digest']=digest;candidates.append(m)
        if j%128==0:print('SCREEN',j,'unique',len(candidates),flush=True)
    # Family quotas keep a cheap endpoint-loss proxy from eliminating all
    # midpoint or non-phase architectures before filtered-video evaluation.
    groups={}
    for m in candidates:
        key=(m['family'],tuple(m['middle_bits']));groups.setdefault(key,[]).append(m)
    picked={}
    for group in groups.values():
        for m in sorted(group,key=lambda x:x['training_loss'])[:2]:picked[m['name']]=m
    refs=[]
    for name,path in [('VLP56','vlp56_codebook.json'),('OVP56','ovp56_codebook.json')]:
        m=json.loads((P.ROOT/'tools'/path).read_text());m.update(name=name,current_tokens=56,previous_shift=1);refs.append(m)
    refs += [dict(name=n) for n in ('Unwrap75','adjacent40','HC50')]
    screened=list(picked.values())
    for m in screened:source_check(m)
    selection=evaluate(screened+refs,(1401,1402));sm=P.summary(selection)
    def objective(m):
        r=sm[m['name']];weak=np.mean([r[str(c)][0]-.002*r[str(c)][1] for c in (0,2,4,6)])
        strong=min(np.mean([x['sinad'] for x in selection if x['model']==m['name'] and x['kind']==kind and x['cnr']==14])-np.mean([x['sinad'] for x in selection if x['model']=='HC50' and x['kind']==kind and x['cnr']==14]) for kind in ('random','bars','multitone'))
        return float(weak-2*max(0,-.8-strong))
    ranked=sorted(screened,key=objective,reverse=True);top=ranked[:min(a.shortlist,8)]
    # Freeze before any final evaluation; no subsequent reselection/refit.
    (a.output/'frozen.json').write_text(json.dumps(top,indent=2)+'\n')
    for m in top:(a.output/(m['name']+'.bsasm')).write_text(source_check(m))
    final=evaluate(top+refs,(1501,1502,1503),full=True)
    out=dict(attempted=a.variants,unique=len(candidates),layouts=len(ls),families=list(families),video_evaluated=len(screened),training_seeds=[1301,1302],selection_seeds=[1401,1402],final_seeds=[1501,1502,1503],selection_winner=ranked[0]['name'],selection=P.summary(selection),final=P.summary(final),objective='mean SINAD minus 0.002*errors/1000 at CNR0/2/4/6; penalty for any strong scenario >0.8dB below HC50',scope='declared LUT8 endpoint/middle-bit architecture family; empirical integer conditional means; no global architecture optimum; synthetic channel/AWGN; no multipath or hardware acceptance')
    (a.output/'candidates.json').write_text(json.dumps(candidates)+'\n')
    (a.output/'selection.json').write_text(json.dumps(selection)+'\n');(a.output/'final.json').write_text(json.dumps(final)+'\n')
    (a.output/'summary.json').write_text(json.dumps(out,indent=2)+'\n');print('FINAL',json.dumps(out),flush=True)

if __name__=='__main__':main()

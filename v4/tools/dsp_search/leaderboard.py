#!/usr/bin/env python3
"""Search C5VRX architecture leaderboards; export non-dominated trade-offs."""
import argparse
import json
import sqlite3
from pathlib import Path

def frontier(rows):
    """Higher weak/strong fidelity and fewer missed pulses are independent axes."""
    front=[]
    def axes(r):return (r['weak_sinad'],-r['weak_missing'],r['strong_sinad'])
    def dominates(a,b):return all(x>=y for x,y in zip(a,b)) and any(x>y for x,y in zip(a,b))
    for row in rows:
        point=axes(row)
        if any(dominates(axes(old),point) for old in front):continue
        front=[old for old in front if not dominates(point,axes(old))]
        front.append(row)
    return front


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('database',type=Path)
    ap.add_argument('--status',default='proxy_eligible')
    ap.add_argument('--family',choices=['pair_context','state4'])
    ap.add_argument('--limit',type=int,default=20)
    ap.add_argument('--output',type=Path,help='write UTF-8 JSON lines instead of stdout')
    ap.add_argument('--pareto',action='store_true',help='all non-dominated overlay proxy candidates; not final validation')
    a=ap.parse_args()
    db=sqlite3.connect('file:'+a.database.resolve().as_posix()+'?mode=ro',uri=True)
    db.row_factory=sqlite3.Row
    columns={r[1] for r in db.execute('PRAGMA table_info(candidates)')}
    overlay='weak_missing' in columns
    if a.family and overlay:ap.error('overlay models use token/phase/confidence parameters, not pair_context families')
    if a.pareto and not overlay:ap.error('--pareto currently requires an overlay leaderboard')
    query='SELECT * FROM candidates WHERE status=?';params=[a.status]
    if a.family:query+=' AND family=?';params.append(a.family)
    query+=' ORDER BY '+('score DESC,strong_sinad DESC' if overlay else 'weak_sinad DESC,strong_loss ASC')
    if not a.pareto:query+=' LIMIT ?';params.append(a.limit)
    rows=[dict(r) for r in db.execute(query,params)]
    if a.pareto:rows=frontier([r for r in rows if r['weak_sinad'] is not None])
    lines=[]
    for row in rows:
        if overlay:row['params']=json.loads(row['params'])
        lines.append(json.dumps(row))
    result='\n'.join(lines)+ ('\n' if lines else '')
    if a.output:a.output.write_text(result,encoding='utf-8')
    else:print(result,end='')


if __name__=='__main__':main()

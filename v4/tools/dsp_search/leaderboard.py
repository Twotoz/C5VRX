#!/usr/bin/env python3
"""Search the local C5VRX DSP experiment leaderboard without a server."""
import argparse
import json
import sqlite3
from pathlib import Path

ap=argparse.ArgumentParser(description=__doc__)
ap.add_argument('database',type=Path)
ap.add_argument('--status',default='proxy_eligible')
ap.add_argument('--family',choices=['pair_context','state4'])
ap.add_argument('--limit',type=int,default=20)
a=ap.parse_args()
db=sqlite3.connect('file:'+a.database.as_posix()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
query='SELECT * FROM candidates WHERE status=?';params=[a.status]
if a.family:query+=' AND family=?';params.append(a.family)
query+=' ORDER BY weak_sinad DESC,strong_loss ASC LIMIT ?';params.append(a.limit)
for row in db.execute(query,params):print(json.dumps(dict(row)))

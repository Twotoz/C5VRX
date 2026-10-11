import json,glob,sys,numpy as np
O=sys.argv[1]; rows={}
for f in glob.glob(O+'/shard*.json'):
  for k,v in json.load(open(f)).items(): rows.setdefault(k,[]).extend(v)
out=[]
for k,v in rows.items():
  SPLIT=int(sys.argv[2]) if len(sys.argv)>2 else 995000
  nom=[r for r in v if not r['spec'][7] and r['spec'][0]<SPLIT]; mix=[r for r in v if r['spec'][0]>=SPLIT]
  out.append((k,np.mean([min(r['h'],r['hp']) for r in nom]),np.mean([r['sinad'] for r in nom]),np.mean([min(r['h'],r['hp']) for r in mix]),np.mean([r['sinad'] for r in mix]),len(nom),len(mix)))
out.sort(key=lambda x:-(x[3]+x[1]+.02*x[2]))
print('candidate'.ljust(70),'nom pic/sinad  mix pic/sinad')
for k,a,b,c,d,n1,n2 in out[:15]+[o for o in out if o[0].startswith('CTRL')]:
  print(k[:70].ljust(70),'%.3f/%.2f   %.3f/%.2f'%(a,b,c,d))

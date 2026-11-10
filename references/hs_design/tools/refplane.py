from common import *
import json
Z=B['zones']
ADJ={'F.Cu':[('In1.Cu',0.1164)],'In2.Cu':[('In1.Cu',0.1),('In3.Cu',0.1164)],'In3.Cu':[('In4.Cu',0.1),('In2.Cu',0.1164)],
     'In5.Cu':[('In6.Cu',0.1),('In4.Cu',0.1164)],'In7.Cu':[('In8.Cu',0.1),('In6.Cu',0.1164)],'B.Cu':[('In8.Cu',0.1164)]}
zl=collections.defaultdict(list)
for k,g in Z.items():
    if k[0]=='_info': continue
    zl[k[0]].append((k[1],g))
from shapely.prepared import prep
zp={l:[(n,prep(g)) for n,g in v] for l,v in zl.items()}
def ref_at(layer,pt):
    for n,pg in zp.get(layer,[]):
        if pg.contains(pt): return n
    return None
res=collections.defaultdict(lambda: collections.defaultdict(float))
trans=collections.defaultdict(list)
for s in SEGS:
    if not is_sig(s['net']) or s['layer'] not in ADJ: continue
    pts,dl=sample(s['geom'],0.05)
    lay=s['layer']; near=ADJ[lay][0][0]
    prev=None
    for p in pts:
        r=ref_at(near,p)
        # for In2/In3 the second neighbour is a signal layer; for In5 both GND; In7 second is In6 GND
        second=ref_at(ADJ[lay][1][0],p) if len(ADJ[lay])>1 and ADJ[lay][1][0] in ('In4.Cu','In6.Cu') else None
        if r=='GND': cat='GND'
        elif r is None: cat='void' if second!='GND' else 'void_near(2nd GND)'
        else: cat='PWR:'+r+('' if second!='GND' else '(2nd GND)')
        res[s['net']][(lay,cat)]+=dl
        if prev is not None and prev!=cat and 'GND' in (prev.split('(')[0],cat.split('(')[0]) and ('PWR' in prev or 'PWR' in cat):
            trans[s['net']].append((lay,round(p.x,2),round(p.y,2),prev,cat))
        prev=cat
tot=collections.Counter(); 
for n,d in res.items():
    for (l,c),v in d.items(): tot[(l,c)]+=v
print('TOTALS by layer/category (mm):'); [print(' ',k,round(v,1)) for k,v in sorted(tot.items())]
print('Non-GND per net (>=1 mm):')
rows=[]
for n,d in sorted(res.items()):
    bad={f'{l}:{c}':round(v,2) for (l,c),v in d.items() if c!='GND' and v>=0.3}
    if bad and sum(bad.values())>=1: rows.append((short(n),bad)); print(' ',short(n),bad)
print('GND<->PWR transitions (split crossings):')
for n,t in trans.items(): print(' ',short(n),len(t),t[:6])
json.dump(dict(totals={f'{k[0]}|{k[1]}':round(v,2) for k,v in tot.items()},per_net={short(n):{f'{l}|{c}':round(v,2) for (l,c),v in d.items()} for n,d in res.items()},split_crossings={short(n):t for n,t in trans.items()}),open('/dev/null','w'),indent=1)

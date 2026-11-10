from common import *
import json
def base(n):
    s=short(n)
    s=re.sub(r'^(IN_A\.|OUT_A\.|A\.|RD_)','',s)
    s=re.sub(r'_[PN]$','',s)
    return s
def grp(n):
    b=base(n); s=short(n)
    if s.startswith('VREF'): return 'VREF'
    if b.startswith(('CA','CS')): return 'CA'
    if b.startswith('CK') or 'CK_DAMP' in s: return 'CK'
    if b.startswith('WCK'): return 'WCK'
    if b.startswith('RDQS'): return 'RDQS'
    m=re.match(r'(DQ|DMI)(\d+)',b)
    if m: 
        i=int(m.group(2)); byte=(i//8) if m.group(1)=='DQ' else i
        return f'BYTE{byte}'
    return 'OTHER'
sig=[s for s in SEGS if is_sig(s['net'])]
LAYERS=['F.Cu','In2.Cu','In3.Cu','In5.Cu','In7.Cu','B.Cu']
by={l:[s for s in sig if s['layer']==l] for l in LAYERS}
tree={l:STRtree([s['geom'] for s in by[l]]) for l in LAYERS}
STEP=0.025
same=collections.defaultdict(lambda: collections.defaultdict(float))  # (victim,aggr) -> bins
minc={}
for l in LAYERS:
    for s in by[l]:
        pts,dl=sample(s['geom'],STEP)
        for p in pts:
            idx=tree[l].query(p.buffer(0.6))
            best={}
            for i in idx:
                a=by[l][i]
                if a['net']==s['net'] or base(a['net'])==base(s['net']): continue
                cc=a['geom'].distance(p)
                if cc<best.get(a['net'],(9,0))[0]: best[a['net']]=(cc,a['w'])
            for an,(cc,aw) in best.items():
                w=max(s['w'],aw)
                k=(s['net'],an)
                for m in (2,3,4,5):
                    if cc<m*w-1e-6: same[k][f'cc<{m}w']+=dl
                if cc< (s['w']+aw)/2+0.3: same[k]['edge<3H(0.3)']+=dl
                minc[k]=min(minc.get(k,9),cc)
# broadside In2<->In3
broad=collections.defaultdict(lambda: collections.defaultdict(float))
for l,o in (('In2.Cu','In3.Cu'),('In3.Cu','In2.Cu')):
    for s in by[l]:
        pts,dl=sample(s['geom'],STEP)
        g=s['geom']; c=list(g.coords); vx,vy=c[-1][0]-c[0][0],c[-1][1]-c[0][1]; vl=math.hypot(vx,vy) or 1
        for p in pts:
            for i in tree[o].query(p.buffer(0.35)):
                a=by[o][i]
                if base(a['net'])==base(s['net']): continue
                d=a['geom'].distance(p)
                ac=list(a['geom'].coords); ax,ay=ac[-1][0]-ac[0][0],ac[-1][1]-ac[0][1]; al=math.hypot(ax,ay) or 1
                cos=abs(vx*ax+vy*ay)/(vl*al)
                k=(s['net'],a['net'])
                if d<0.1: broad[k]['offset<0.10']+=dl
                if d<0.2: broad[k]['offset<0.20']+=dl
                if d<0.3: broad[k]['offset<0.30']+=dl
                if d<0.1 and cos<0.5: broad[k]['cross_like']+=dl
# summarise
def summ(D,keys,lim=40):
    rows=sorted(D.items(),key=lambda kv:-kv[1][keys[0]])
    return [(short(v),short(a),grp(v),grp(a),{k:round(d[k],2) for k in keys if d[k]>0}) for (v,a),d in rows[:lim]]
out={}
print('SAME-LAYER: victim-aggressor coupled length (mm) at centre-to-centre < m*w (w = wider trace)')
out['same_cc<3w']=summ(same,['cc<3w','cc<2w','cc<4w','cc<5w','edge<3H(0.3)'],40)
for r in out['same_cc<3w'][:40]: print(' ',r, 'min cc',round(minc[(('/SODIMM/' if False else '')+'' ,)] if False else 0,3))
# per-victim totals
tot=collections.defaultdict(lambda: collections.defaultdict(float))
for (v,a),d in same.items():
    for k,x in d.items(): tot[v][k]+=x
    if grp(v)!=grp(a):
        for k,x in d.items(): tot[v]['xgrp_'+k]+=x
print('PER-VICTIM totals (mm) top 25 by cc<4w:')
pv=sorted(tot.items(),key=lambda kv:-kv[1]['cc<4w'])
out['per_victim']=[(short(v),{k:round(x,1) for k,x in d.items()}) for v,d in pv]
for v,d in out['per_victim'][:25]: print(' ',v,d)
print('BROADSIDE In2/In3 (victim on first layer), top 30 by offset<0.20:')
out['broadside']=summ(broad,['offset<0.20','offset<0.10','offset<0.30','cross_like'],60)
for r in out['broadside'][:30]: print(' ',r)
bt=collections.defaultdict(float)
for (v,a),d in broad.items(): bt[short(v)]+=d['offset<0.20']
out['broad_per_victim']=sorted(((k,round(x,1)) for k,x in bt.items()),key=lambda t:-t[1])
print('broadside per victim (<0.2 mm offset):',out['broad_per_victim'][:20])
out['min_cc']={f'{short(v)}|{short(a)}':round(x,3) for (v,a),x in minc.items() if x<0.3}
json.dump(out,open('/home/vassilios/code/thermo-dram/lpddr5-testbed/board/references/hs_design/measurements_crosstalk.json','w'),indent=1)

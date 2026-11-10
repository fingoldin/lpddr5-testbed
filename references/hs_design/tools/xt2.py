from common import *
import json
exec(open(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)),'xtalk.py')).read().split("STEP=0.025")[0])
LM=json.load(open('/home/vassilios/code/thermo-dram/lpddr5-testbed/board/sim/si/extract/linemodels.json'))
def kb(layer,w,cc):
    g={'F.Cu':'F.Cu','B.Cu':'F.Cu','In2.Cu':'In2.Cu','In3.Cu':'In2.Cu','In5.Cu':'In5.Cu','In7.Cu':'In5.Cu'}[layer]
    ww=0.15 if (g=='F.Cu' and w>0.12) else 0.09
    pts=sorted((float(k.split('cc')[1]),v['kb']) for k,v in LM['coupled'].items() if k.startswith('%s/%.4f/'%(g,ww)))
    x=np.array([p[0] for p in pts]); y=np.log(np.array([max(p[1],1e-6) for p in pts]))
    return float(np.exp(np.interp(cc,x,y)))
R=collections.defaultdict(lambda: collections.defaultdict(float))
NEXT=collections.defaultdict(lambda: collections.defaultdict(float))
for l in LAYERS:
    for s in by[l]:
        pts,dl=sample(s['geom'],0.025); wv=s['w']
        for p in pts:
            best={}
            for i in tree[l].query(p.buffer(0.6)):
                a=by[l][i]
                if base(a['net'])==base(s['net']): continue
                cc=a['geom'].distance(p)
                if cc<best.get(a['net'],9): best[a['net']]=cc
            v=s['net']
            for an,cc in best.items():
                same=grp(an)==grp(v)
                key='in' if same else 'x'
                if cc<2*wv+1e-3: R[v][key+'_cc<=2w']+=dl
                if cc<3*wv-1e-6: R[v][key+'_cc<3w']+=dl
                if cc<4*wv-1e-6: R[v][key+'_cc<4w']+=dl
                if cc<5*wv-1e-6: R[v][key+'_cc<5w']+=dl
                if cc<0.6: NEXT[(v,an)]['kbL']+=kb(l,wv,cc)*dl
                NEXT[(v,an)]['L']+=dl if cc<0.3 else 0
# saturated NEXT estimate per victim: sum over aggressors of Kb_eff*min(1, 2*Td/tr), Td=7 ps/mm stripline, tr=100 ps
TR=100.0
est=collections.defaultdict(float)
for (v,a),d in NEXT.items():
    L=d['L'] or 1e-9
    kbeff=d['kbL']/max(d['L'],1e-9) if d['L']>0 else 0
    est[v]+=d['kbL']/max(L,1e-9)*min(1,2*7*L/TR) if d['L']>0 else 0
rows=[]
for v,d in R.items():
    rows.append(dict(net=short(v),group=grp(v),**{k:round(x,1) for k,x in d.items()},next_est_pct=round(100*est[v],1)))
rows.sort(key=lambda r:-r.get('in_cc<3w',0))
for r in rows[:30]: print(r)
print('--- cross-group <4w top')
for r in sorted(rows,key=lambda r:-r.get('x_cc<4w',0))[:20]: print(r)
print('--- NEXT est top')
for r in sorted(rows,key=lambda r:-r['next_est_pct'])[:15]: print(r['net'],r['next_est_pct'])
json.dump(rows,open('/home/vassilios/code/thermo-dram/lpddr5-testbed/board/references/hs_design/measurements_spacing_per_net.json','w'),indent=1)

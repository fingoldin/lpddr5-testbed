from common import *
import json
nets=set(s['net'] for s in SEGS)
pairs=sorted(n[:-2] for n in nets if n.endswith('_P') and n[:-2]+'_N' in nets)
out={}
for b in pairs:
    p=[s for s in SEGS if s['net']==b+'_P']; n=[s for s in SEGS if s['net']==b+'_N']
    Lp=sum(s['geom'].length for s in p); Ln=sum(s['geom'].length for s in n)
    vp=sum(1 for v in B['vias'] if v['net']==b+'_P'); vn=sum(1 for v in B['vias'] if v['net']==b+'_N')
    gaps=[]; unc=0; tot=0
    for s in p:
        same=[t for t in n if t['layer']==s['layer']]
        pts,dl=sample(s['geom'],0.05)
        for q in pts:
            tot+=dl
            d=min((t['geom'].distance(q)-(t['w']+s['w'])/2 for t in same),default=9)
            if d<0.35: gaps.append(d)
            else: unc+=dl
    g=np.array(gaps) if gaps else np.array([0])
    med=float(np.median(g))
    within=float(np.mean(np.abs(g-med)<=0.1*med+0.005)) if len(gaps) else 0
    out[short(b)]=dict(len_P=round(Lp,2),len_N=round(Ln,2),dL_mm=round(Lp-Ln,2),skew_ps_at_7ps_per_mm=round(abs(Lp-Ln)*7,1),vias_P=vp,vias_N=vn,
        gap_median=round(med,3),gap_min=round(float(g.min()),3),gap_p95=round(float(np.percentile(g,95)),3),coupled_mm=round(tot-unc,1),uncoupled_mm=round(unc,1),frac_gap_within_10pct=round(within,2))
    print(short(b),out[short(b)])
json.dump(out,open('/home/vassilios/code/thermo-dram/lpddr5-testbed/board/references/hs_design/measurements_diffpairs.json','w'),indent=1)

from common import *
import json
exec(open(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)),'refplane.py')).read().split("res=collections")[0])
caps=[p for p in B['pads'] if p['ref'].startswith('C') and p['net']=='+1V05']
runs=[]; xings=[]
# walk each net's segments in order of file (segments are mostly sequential); compute continuous void runs per segment chain
for s in SEGS:
    if not is_sig(s['net']) or s['layer'] not in ADJ: continue
    pts,dl=sample(s['geom'],0.025)
    lay=s['layer']; near=ADJ[lay][0][0]
    cur=0; last_nonvoid=None
    for p in pts:
        r=ref_at(near,p)
        if r is None: cur+=dl
        else:
            if cur>0: runs.append((round(cur,2),short(s['net']),lay,round(p.x,2),round(p.y,2)))
            cur=0
            if last_nonvoid and last_nonvoid!=r:
                dmin=min(math.hypot(c['x']-p.x,c['y']-p.y) for c in caps)
                xings.append((short(s['net']),lay,near,last_nonvoid,r,round(p.x,2),round(p.y,2),round(dmin,2)))
            last_nonvoid=r
    if cur>0: runs.append((round(cur,2),short(s['net']),lay,-1.0,-1.0))
runs.sort(key=lambda r:-r[0])
print('longest continuous void runs (mm) under a single segment:',runs[:25])
print('n runs >1mm',sum(1 for r in runs if r[0]>1.0),' >0.5mm',sum(1 for r in runs if r[0]>0.5))
print('GND<->PWR reference changes along a trace:',len(xings))
for x in xings: print(' ',x)
json.dump(dict(void_runs=runs[:60],ref_changes=xings),open('/home/vassilios/code/thermo-dram/lpddr5-testbed/board/references/hs_design/measurements_refplane.json','w'),indent=1)

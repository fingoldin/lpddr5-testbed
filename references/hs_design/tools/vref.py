from common import *
import json
P=B['pads']
vr=[s for s in SEGS if s['net']=='/SODIMM/VREF_RD']
print('VREF_RD length by layer/width:',collections.Counter((s['layer'],s['w']) for s in vr), round(sum(s['geom'].length for s in vr),1))
wl=collections.defaultdict(float)
for s in vr: wl[s['w']]+=s['geom'].length
print('length per width',{k:round(v,1) for k,v in wl.items()})
# spacing to other nets on the same layer
others=[s for s in SEGS if s['net']!='/SODIMM/VREF_RD' and s['net']!='' ]
from shapely.strtree import STRtree
res=collections.defaultdict(float); mn={}
for l in set(s['layer'] for s in vr):
    ol=[s for s in others if s['layer']==l]; t=STRtree([s['geom'] for s in ol])
    for s in vr:
        if s['layer']!=l: continue
        pts,dl=sample(s['geom'],0.025)
        for p in pts:
            near={}
            for i in t.query(p.buffer(0.8)):
                o=ol[i]; e=o['geom'].distance(p)-o['w']/2-s['w']/2
                if e<near.get(o['net'],9): near[o['net']]=e
            for n,e in near.items():
                if e<0.381: res[(l,short(n))]+=dl
                mn[(l,short(n))]=min(mn.get((l,short(n)),9),e)
print('VREF_RD trunk edge gap < 0.381 mm (15 mil) to other nets: total',round(sum(res.values()),1),'mm')
for k,v in sorted(res.items(),key=lambda kv:-kv[1])[:20]: print('  ',k,round(v,2),'min gap',round(mn[k],3))
# graph distance from C110 pad 2 to each 39R VREF pad
c110=[p for p in P if p['ref']=='C110' and p['pad']=='2'][0]
r39=[p for p in P if p['net']=='/SODIMM/VREF_RD' and p['val']=='R_39R_0402']
import networkx as nx
G=nx.Graph()
def key(pt): return (round(pt[0],3),round(pt[1],3))
for s in vr:
    c=list(s['geom'].coords); G.add_edge(key(c[0]),key(c[-1]),w=s['geom'].length)
vias=[v for v in B['vias'] if v['net']=='/SODIMM/VREF_RD']
nodes=list(G.nodes)
for v in vias:
    vk=('via',v['x'],v['y'])
    for n in nodes:
        if math.hypot(n[0]-v['x'],n[1]-v['y'])<0.25: G.add_edge(vk,n,w=0)
for p in [c110]+r39:
    pk=('pad',p['ref'])
    for n in nodes:
        if abs(n[0]-p['x'])<=p['sx']/2+0.05 and abs(n[1]-p['y'])<=p['sy']/2+0.05: G.add_edge(pk,n,w=0)
d=nx.single_source_dijkstra_path_length(G,('pad','C110'),weight='w')
dd={p['ref']:round(d.get(('pad',p['ref']),-1),1) for p in r39}
print('trace length C110 -> each 39R (mm):',dd)
json.dump(dict(len_by_width={str(k):round(v,1) for k,v in wl.items()},gap_lt_0p381={f'{k[0]}|{k[1]}':[round(v,2),round(mn[k],3)] for k,v in res.items()},c110_to_39R=dd),open('/home/vassilios/code/thermo-dram/lpddr5-testbed/board/references/hs_design/measurements_vref_rd.json','w'),indent=1)

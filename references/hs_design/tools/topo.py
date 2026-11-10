from netgraph import *
import json
P=B['pads']
res={}
def pads_of(net): return [p for p in P if p['net']==net]
for net in sorted(set(p['net'] for p in P)):
    sn=short(net)
    if not (sn.startswith('OUT_A.') or sn.startswith('A.') or sn.startswith('IN_A.') or sn.startswith('RD_')): continue
    G=net_graph(net)
    pp=pads_of(net)
    keys=[('pad',p['ref'],p['pad']) for p in pp]
    lab={('pad',p['ref'],p['pad']):(p['ref']+':'+p['val'].replace('R_','').replace('_0402','').replace('_array','').replace('_0201','')) for p in pp}
    D={}
    for a in keys:
        if a not in G: continue
        d=nx.single_source_dijkstra_path_length(G,a,weight='w')
        for b in keys:
            if b in d and a<b: D[(lab[a],lab[b])]=round(d[b],2)
    res[sn]=D
for k,v in res.items():
    print(k,v)
json.dump({k:{f'{a}<->{b}':x for (a,b),x in v.items()} for k,v in res.items()},open('/home/vassilios/code/thermo-dram/lpddr5-testbed/board/references/hs_design/measurements_topology.json','w'),indent=1)

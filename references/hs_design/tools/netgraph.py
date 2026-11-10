from common import *
import networkx as nx, json
P=B['pads']
def net_graph(net,tol=0.30):
    G=nx.Graph(); segs=[s for s in SEGS if s['net']==net]
    for s in segs:
        c=list(s['geom'].coords); a,b=c[0],c[-1]
        G.add_edge((s['layer'],)+tuple(a),(s['layer'],)+tuple(b),w=s['geom'].length)
    ends=[n for n in G.nodes]
    # T junctions / near-coincident ends
    for n in ends:
        pt=Point(n[1],n[2])
        for s in segs:
            if s['layer']!=n[0]: continue
            d=s['geom'].distance(pt)
            if d<tol:
                c=list(s['geom'].coords); a,b=c[0],c[-1]; t=s['geom'].project(pt)
                G.add_edge(n,(s['layer'],)+tuple(a),w=t+d); G.add_edge(n,(s['layer'],)+tuple(b),w=s['geom'].length-t+d)
    for v in B['vias']:
        if v['net']!=net: continue
        for n in ends:
            if math.hypot(n[1]-v['x'],n[2]-v['y'])<=v['d']/2+tol: G.add_edge(('via',v['x'],v['y']),n,w=0)
    for p in P:
        if p['net']!=net: continue
        lay='F.Cu' if p['side']=='F' else 'B.Cu'; k=('pad',p['ref'],p['pad'])
        for n in ends:
            if n[0]==lay and abs(n[1]-p['x'])<=p['sx']/2+tol and abs(n[2]-p['y'])<=p['sy']/2+tol: G.add_edge(k,n,w=0)
        for v in B['vias']:
            if v['net']==net and abs(v['x']-p['x'])<=p['sx']/2+tol and abs(v['y']-p['y'])<=p['sy']/2+tol: G.add_edge(k,('via',v['x'],v['y']),w=0)
    return G
if __name__=='__main__':
    G=net_graph('/SODIMM/VREF_RD')
    print('components',nx.number_connected_components(G))
    d=nx.single_source_dijkstra_path_length(G,('pad','C110','2'),weight='w')
    r39=[p for p in P if p['net']=='/SODIMM/VREF_RD' and p['val']=='R_39R_0402']
    dd={p['ref']:round(d.get(('pad',p['ref'],p['pad']),-1),1) for p in r39}
    print(sorted(dd.values())); 
    f='/home/vassilios/code/thermo-dram/lpddr5-testbed/board/references/hs_design/measurements_vref_rd.json'
    J=json.load(open(f)); J['c110_to_39R_trace_mm']=dd; json.dump(J,open(f,'w'),indent=1)

import pickle,re,math,collections
import numpy as np
from shapely.geometry import LineString, Point, Polygon
from shapely.strtree import STRtree
from shapely.ops import unary_union
B=pickle.load(open(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)),'board.pkl'),'rb'))
SIG=re.compile(r'^/(SODIMM|LPDDR5)/(LPDDR5_|RD_|VREF_RD|~\{RESET\}|CK_DAMP)')
def is_sig(n): return bool(SIG.match(n)) or n.startswith('/SODIMM/~') 
def short(n): return n.replace('/SODIMM/','').replace('/LPDDR5/','').replace('LPDDR5_','')
def seg_lines(s):
    pts=s['pts']
    if s['arc'] and len(pts)==3:
        # approximate arc by 2 segments through mid
        return [LineString([pts[0],pts[1]]),LineString([pts[1],pts[2]])]
    return [LineString(pts)]
SEGS=[]
for s in B['segs']:
    for l in seg_lines(s):
        if l.length>1e-6: SEGS.append(dict(net=s['net'],layer=s['layer'],w=s['w'],geom=l))
def sample(line,step=0.05):
    n=max(1,int(math.ceil(line.length/step)))
    return [line.interpolate((i+0.5)/n,normalized=True) for i in range(n)], line.length/n

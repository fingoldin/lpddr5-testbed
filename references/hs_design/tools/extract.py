# Usage: python3 extract.py (pcbnew, writes board.pkl here), then any analysis script; outputs go to ../measurements_*.json
import pcbnew, pickle
from shapely.geometry import Polygon, MultiPolygon
from shapely.ops import unary_union
b=pcbnew.LoadBoard('/home/vassilios/code/thermo-dram/lpddr5-testbed/board/kicad/lpddr5-testbed.kicad_pcb')
mm=lambda v: v/1e6
segs=[];vias=[];pads=[];zones={}
for t in b.GetTracks():
    c=t.GetClass()
    if c=='PCB_VIA':
        v=pcbnew.Cast_to_PCB_VIA(t) if hasattr(pcbnew,'Cast_to_PCB_VIA') else t
        p=t.GetPosition()
        top=b.GetLayerName(v.TopLayer()); bot=b.GetLayerName(v.BottomLayer())
        vias.append(dict(net=t.GetNetname(),x=mm(p.x),y=mm(p.y),d=mm(v.GetWidth(pcbnew.F_Cu)) if hasattr(v,'GetWidth') else None,drill=mm(v.GetDrillValue()),top=top,bot=bot,vt=int(v.GetViaType())))
    elif c=='PCB_ARC':
        a=t
        s=a.GetStart(); m=a.GetMid(); e=a.GetEnd()
        segs.append(dict(net=t.GetNetname(),layer=b.GetLayerName(t.GetLayer()),w=mm(t.GetWidth()),pts=[(mm(s.x),mm(s.y)),(mm(m.x),mm(m.y)),(mm(e.x),mm(e.y))],arc=True))
    else:
        s=t.GetStart(); e=t.GetEnd()
        segs.append(dict(net=t.GetNetname(),layer=b.GetLayerName(t.GetLayer()),w=mm(t.GetWidth()),pts=[(mm(s.x),mm(s.y)),(mm(e.x),mm(e.y))],arc=False))
for f in b.GetFootprints():
    for p in f.Pads():
        pos=p.GetPosition(); sz=p.GetSize()
        ls=[b.GetLayerName(l) for l in p.GetLayerSet().CuStack()]
        pads.append(dict(ref=f.GetReference(),val=f.GetValue(),fp=str(f.GetFPID().GetLibItemName()),pad=p.GetNumber(),net=p.GetNetname(),x=mm(pos.x),y=mm(pos.y),sx=mm(sz.x),sy=mm(sz.y),layers=ls,side='B' if f.IsFlipped() else 'F',attr=int(p.GetAttribute()),zc=int(p.GetLocalZoneConnection()) if hasattr(p,'GetLocalZoneConnection') else None,fzc=int(f.GetLocalZoneConnection()) if hasattr(f,'GetLocalZoneConnection') else None))
for z in b.Zones():
    if not z.IsFilled() or z.GetIsRuleArea(): continue
    for l in z.GetLayerSet().Seq():
        ln=b.GetLayerName(l)
        ps=z.GetFilledPolysList(l)
        polys=[]
        for i in range(ps.OutlineCount()):
            o=ps.Outline(i); ext=[(mm(o.CPoint(j).x),mm(o.CPoint(j).y)) for j in range(o.PointCount())]
            holes=[]
            for h in range(ps.HoleCount(i)):
                hh=ps.Hole(i,h); holes.append([(mm(hh.CPoint(j).x),mm(hh.CPoint(j).y)) for j in range(hh.PointCount())])
            polys.append(Polygon(ext,holes).buffer(0))
        g=unary_union(polys)
        key=(ln,z.GetNetname())
        zones[key]=unary_union([zones[key],g]) if key in zones else g
        zinfo=(z.GetPadConnection(), mm(z.GetThermalReliefGap()), mm(z.GetThermalReliefSpokeWidth()))
        zones.setdefault(('_info',ln,z.GetNetname()),zinfo)
edge=b.GetBoardEdgesBoundingBox()
# board outline polygon
outline=pcbnew.SHAPE_POLY_SET(); b.GetBoardPolygonOutlines(outline)
o=outline.Outline(0); bo=Polygon([(mm(o.CPoint(j).x),mm(o.CPoint(j).y)) for j in range(o.PointCount())])
pickle.dump(dict(segs=segs,vias=vias,pads=pads,zones=zones,outline=bo),open(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)),'board.pkl'),'wb'))
print(len(segs),len(vias),len(pads),[(k,round(v.area,1)) for k,v in zones.items() if k[0]!='_info'], bo.bounds)
print({k:v for k,v in zones.items() if k[0]=='_info'})
print(set((v['top'],v['bot'],v['drill'],v['d'],v['vt']) for v in vias))

from common import *
import json
P=B['pads']; V=B['vias']
u1=[p for p in P if p['ref']=='U1']
ux=[p['x'] for p in u1]; uy=[p['y'] for p in u1]
print('U1 bbox',min(ux),max(ux),min(uy),max(uy), 'side',u1[0]['side'])
def in_pad(v,p): return abs(v['x']-p['x'])<=p['sx']/2+1e-3 and abs(v['y']-p['y'])<=p['sy']/2+1e-3
u1vias=set()
for i,v in enumerate(V):
    for p in u1:
        if p['net']==v['net'] and math.hypot(v['x']-p['x'],v['y']-p['y'])<0.25: u1vias.add(i)
# segments by net for connectivity of pad to via
segs_by=collections.defaultdict(list)
for s in SEGS: segs_by[s['net']].append(s)
def pad_vias(p):
    """vias electrically attached to the pad: inside the pad, or reached via a short trace chain (<=3 mm) on the pad layer"""
    out=[]
    lay='F.Cu' if p['side']=='F' else 'B.Cu'
    padpoly=Polygon([(p['x']-p['sx']/2,p['y']-p['sy']/2),(p['x']+p['sx']/2,p['y']-p['sy']/2),(p['x']+p['sx']/2,p['y']+p['sy']/2),(p['x']-p['sx']/2,p['y']+p['sy']/2)])
    for i,v in enumerate(V):
        if v['net']!=p['net']: continue
        d=math.hypot(v['x']-p['x'],v['y']-p['y'])
        if d>3: continue
        pt=Point(v['x'],v['y'])
        if padpoly.buffer(v['d']/2 if v['d'] else 0.2).contains(pt) and padpoly.distance(pt)<=1e-3:
            out.append((i,0.0,'in-pad')); continue
        # trace from pad edge to via
        tl=0; 
        for s in segs_by[p['net']]:
            if s['layer']!=lay: continue
            g=s['geom']
            if g.distance(padpoly)<1e-3 and g.distance(pt)<1e-3:
                tl=g.length; out.append((i,round(tl,3),'trace w%.2f'%s['w'])); break
        else:
            # touching via ring directly
            if padpoly.distance(pt)<= (v['d'] or 0.4)/2+1e-3: out.append((i,0.0,'butted'))
    return out
rails={'+0V5':'VDDQ','+1V05':'VDD2H','+1V8':'VDD1'}
rows=[]
for ref in sorted(set(p['ref'] for p in P if p['ref'].startswith('C'))):
    pp=[p for p in P if p['ref']==ref and p['pad'] in ('1','2')]
    nets=set(p['net'] for p in pp)
    rail=[n for n in nets if n in rails]
    if not rail: continue
    rail=rail[0]
    pw=[p for p in pp if p['net']==rail][0]; pg=[p for p in pp if p['net']=='GND'][0]
    vw=pad_vias(pw); vg=pad_vias(pg)
    # nearest same-net U1 ball
    balls=[p for p in u1 if p['net']==rail]
    db=min(math.hypot(b['x']-pw['x'],b['y']-pw['y']) for b in balls)
    # shared vias: via used by >1 cap pad, or a U1 ball via
    rows.append(dict(ref=ref,val=pw['val'],side=pw['side'],rail=rails[rail],x=round((pw['x']+pg['x'])/2,2),y=round((pw['y']+pg['y'])/2,2),
        pwr_vias=[(round(V[i]['x'],2),round(V[i]['y'],2),t,k,'U1ball' if i in u1vias else '') for i,t,k in vw],
        gnd_vias=[(round(V[i]['x'],2),round(V[i]['y'],2),t,k,'U1ball' if i in u1vias else '') for i,t,k in vg],
        nearest_ball_mm=round(db,2),vid_pwr=[i for i,_,_ in vw],vid_gnd=[i for i,_,_ in vg]))
cnt=collections.Counter()
for r in rows:
    for i in r['vid_pwr']+r['vid_gnd']: cnt[i]+=1
for r in rows:
    r['shared_with_other_cap']=sum(1 for i in r['vid_pwr']+r['vid_gnd'] if cnt[i]>1)
    r['shares_U1_ball_via']=sum(1 for i in r['vid_pwr']+r['vid_gnd'] if i in u1vias)
    del r['vid_pwr'],r['vid_gnd']
    print(r)
json.dump(rows,open('/home/vassilios/code/thermo-dram/lpddr5-testbed/board/references/hs_design/measurements_decap.json','w'),indent=1)
# summary per rail
for rl in rails.values():
    rr=[r for r in rows if r['rail']==rl]
    print(rl,len(rr),'caps; within 3.81mm(150mil) of a ball:',sum(r['nearest_ball_mm']<=3.81 for r in rr),
      'pads w/o via:',sum((len(r['pwr_vias'])==0)+(len(r['gnd_vias'])==0) for r in rr),
      'max trace to via:',max([t for r in rr for _,_,t,_,_ in r['pwr_vias']+r['gnd_vias']] or [0]),
      'shared vias:',sum(r['shared_with_other_cap']>0 for r in rr),'U1-ball-shared:',sum(r['shares_U1_ball_via']>0 for r in rr))

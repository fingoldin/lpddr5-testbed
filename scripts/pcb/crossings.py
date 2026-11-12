#!/usr/bin/env python3
"""Plan-view crossings, parallel overlap and resistor-group transversals of the high-speed wires.

    crossings.py [--board file.kicad_pcb] [--plan plan.json] [options]

One tool for the board as built and for a routing plan, so that every plan and the final board are judged by
the same count.  READ-ONLY: the board file is never written.

Inputs
    --board   a .kicad_pcb (default: the project board).  Tracks, arcs, vias, footprints and zone fills are read.
    --plan    a plan JSON (format below) laid over the board.  By default the board then contributes only what
              the plan cannot change: every stage except s30_data / s50_power (that is the board
              skeleton, the U1 escape and the whole command stage) and the parts of every sheet except 'write'
              and 'read'.  --drop-stage / --drop-class / --drop-sheet / --keep-all change that.
              Plan wires of class CMD are ignored (the command stage is taken from the board).

What is measured (card frame, mm; origin lower-left corner seen from the front, y up)
    1 CROSSING      two wires of DIFFERENT nets on DIFFERENT layers whose centre lines properly intersect in plan
                    view.  A pair of nets that crosses k times counts k.  Where two wires lie on top of each other
                    for a stretch, the stretch is one crossing if the wire leaves it on the other side than it
                    came from, and none if it leaves on the same side.  The two legs of a differential pair are
                    separate wires; 'pair crossings' count a pair as one wire (leg crossings of the same event,
                    <= --pair-cluster mm apart, are one).  For each crossing: the layers, the reference planes
                    between them and what each plane is at that point (zone hit test with a disc of --plane-disc
                    mm: 'GND' solid, 'GND-edge' an opening within the disc, a net name for an island, 'void').
                    Only crossings with at least one high-speed wire are reported (--all-pairs for the rest).
    2 OVERLAP       length over which two wires of different nets on different layers run within --overlap-dist
                    (0.2 mm) of each other in plan view at less than --overlap-angle (15 deg).  The figure is the
                    shorter of 'A within reach of B' and 'B within reach of A'.
    3 SAME LAYER    centre lines of different nets that intersect or touch on one layer (must be zero).
    4 TRANSVERSAL   for every resistor group: copper of any net on any layer inside the group's bounding box that
                    runs more than --trans-angle (30 deg) off the column axis for more than --trans-len (0.3 mm;
                    connected off-axis pieces of one net on one layer are one run), or that crosses more than one
                    column at any angle.  Copper inside a land of its own net (SMD land or via land) is part of
                    the land and is not counted.  Tiers:
                        foreign-through   a net that is on no pad of the group and passes over a column
                        foreign-in-lane   such a net, off axis, between two columns (crosses none)
                        common-threaded   a net of the group that belongs to several columns (GND, VREF_RD) and
                                          runs sideways from column to column inside the group
                        own-through       a net of one column that passes over another column
                        own-jog           a net of the group, off axis, inside its own column pitch
                    Resistor groups are derived from the footprints: two-pad R / C parts of the sheets command,
                    write and read (the RDQS loads apart; on the board the groups of the spec, floorplan.data.
                    resistor_groups, name which parts belong together), clustered by pad-to-pad distance (--group-link), kept
                    when the cluster reaches down to y <= --group-near.  Bounding box = the pads of the parts
                    (--group-margin grows it, e.g. to take in the GND via row above a divider field);
                    column axis = the pad axis of the parts.  A plan may add groups of its own.
    5 SUMMARY       crossings involving CMD, crossings among the data wires (WR_DQ, RD_DQ, WCK, RDQS), crossings
                    of high-speed wires with VREF / SLOW / PWR / GND, overlap mm, transversal counts; --json
                    writes everything machine-readable, --md a report, --png an annotated picture.

Net classes (design/netlist.json: groups and parts)
    CMD     CK_P / CK_N, CS0, CA0-6 and their finger-side nets (X_F)
    WR_DQ   DQ0-7 on an outer layer where the copper belongs to the write path (reaches a U1 ball, or a link of
            the divider column) and DQx_WF
    RD_DQ   DQ0-7 on In3 and on an outer layer where the copper reaches a card finger
    WCK     WCK_P / WCK_N and WCK_F_P / WCK_F_N        RDQS    RDQS_P / RDQS_N
    VREF    VREF_N0-7, VREF_RD, DAC_OUT, DAC_CAP        GND     GND
    PWR     every other net with five or more pins     SLOW    everything else
    In a plan the wire's own "class" is used (PWR on net GND becomes GND).

Plan JSON
    {"name": ..., "pin_map": {...},
     "parts":  [{"ref": "R201" | "role": "wr_series_3", "x":, "y":, "rot":, "layer": "F.Cu" | "B.Cu",
                 optional for role parts: "fp": "0201" | "0402", "nets": [n1, n2], "group": "wr_div"}],
     "vias":   [{"net":, "x":, "y":}],
     "wires":  [{"net": "DQ3", "class": "WR_DQ", "layer": "F.Cu", "points": [[x, y], ...], optional "width"}],
     "groups": [{"name":, "kind": "resistor_group", "bbox": [x0, y0, x1, y1], "column_axis": "y",
                 optional "columns": [x, ...], "nets": {net: [column index, ...]}}],
     optional "planes": [{"layer": "In4.Cu", "net": "V1P2", "bbox": [x0, y0, x1, y1]}]}   (replaces the islands)

Other
    --export-plan FILE   write the data copper of the board (stage s30_data; parts of the sheets write and
                         read; their vias) in the plan format: the baseline as a plan, and a template
    --selftest           synthetic cases of the crossing test
    --crop name:x0,y0,x1,y1[:px_per_mm]   extra zoom pictures next to --png

Gate use
    --max NAME=N (repeatable) fails (exit 1) when a summary figure exceeds N; same-layer crossings always fail.
    Names: see "summary" in the JSON (cmd_x_data, data_x_data, hs_x_vref, overlap_mm_total, trans_through, ...).
"""
import argparse
import collections
import json
import math
import os
import re
import sys

import shapely
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union
from shapely.prepared import prep

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C      # noqa: E402
import pcbnew           # noqa: E402

HS = ("CMD", "WR_DQ", "RD_DQ", "WCK", "RDQS")
DATA = ("WR_DQ", "RD_DQ", "WCK", "RDQS")
OTHER = ("VREF", "SLOW", "PWR", "GND")
CLASSES = HS + OTHER
CU_ORDER = ["F.Cu", "In1.Cu", "In2.Cu", "In3.Cu", "In4.Cu", "B.Cu"]
PLANES = ("In1.Cu", "In2.Cu", "In4.Cu")
TOL = 5e-6                              # mm: coordinates are on a 1 nm grid
ROLE_FP = {"0201": (0.40, 1.10), "0402": (0.64, 1.58)}      # (across, along the pad axis) of the two lands


# ------------------------------------------------------------------------------------------------ net classes
class Classifier:
    def __init__(self, spec):
        g = {x["name"]: x for x in spec["groups"]}
        self.cmd = set(g["CMD"]["nets"]) | set(g["CMD"]["finger_side_nets"] or [])
        self.dq = set(g["WR"]["nets"])
        self.dq_wf = set(g["WR"]["finger_side_nets"] or [])
        self.wck = set(g["WR_WCK"]["nets"]) | set(g["WR_WCK"]["finger_side_nets"] or [])
        self.rdqs = set(g["RD_RDQS"]["nets"])
        self.vref = set(g["RD_N"]["nets"])
        pins = collections.Counter()
        for p in spec["parts"]:
            for n in p["pins"].values():
                if n:
                    pins[n] += 1
        self.vref |= {n for n in pins if re.match(r"^(VREF_|DAC_)", n)}
        taken = self.cmd | self.dq | self.dq_wf | self.wck | self.rdqs | self.vref | {"GND"}
        self.pwr = {n for n, k in pins.items() if k >= 5 and n not in taken}
        self.sheet = {p["ref"]: p["sheet"] for p in spec["parts"]}

    def net_class(self, net, layer=None, role=None):
        """Class of a wire; for DQ0-7 the role ('WR_DQ' / 'RD_DQ') decides, else In3 = read, outer = write."""
        if net in self.cmd:
            return "CMD"
        if net in self.dq:
            return role or ("RD_DQ" if layer == "In3.Cu" else "WR_DQ")
        if net in self.dq_wf:
            return "WR_DQ"
        if net in self.wck:
            return "WCK"
        if net in self.rdqs:
            return "RDQS"
        if net in self.vref:
            return "VREF"
        if net == "GND":
            return "GND"
        if net in self.pwr:
            return "PWR"
        return "SLOW"

    def table(self):
        return {"CMD": sorted(self.cmd), "WR_DQ / RD_DQ (by copper role)": sorted(self.dq), "WR_DQ": sorted(self.dq_wf),
                "WCK": sorted(self.wck), "RDQS": sorted(self.rdqs), "VREF": sorted(self.vref), "GND": ["GND"],
                "PWR": sorted(self.pwr), "SLOW": ["every other net"]}


def pair_of(net):
    """Name of the differential pair a net is a leg of (KiCad suffix _P / _N), else the net itself."""
    m = re.match(r"^(.*)_([PN])$", net)
    return m.group(1) if m else net


# ------------------------------------------------------------------------------------------------ geometry
def _arc_pts(a, m, b, step_deg=5.0):
    ax, ay, bx, by, cx, cy = a[0], a[1], m[0], m[1], b[0], b[1]
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-12:
        return [a, b]
    ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay) + (cx * cx + cy * cy) * (ay - by)) / d
    uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx) + (cx * cx + cy * cy) * (bx - ax)) / d
    r = math.hypot(ax - ux, ay - uy)
    a0, a1, a2 = (math.atan2(q[1] - uy, q[0] - ux) for q in (a, m, b))
    sweep = (a2 - a0) % (2 * math.pi)
    if (a1 - a0) % (2 * math.pi) > sweep:
        sweep -= 2 * math.pi
    n = max(2, int(math.ceil(abs(math.degrees(sweep)) / step_deg)))
    return [a] + [(ux + r * math.cos(a0 + sweep * k / n), uy + r * math.sin(a0 + sweep * k / n)) for k in range(1, n)] + [b]


def seg_hit(p1, p2, q1, q2):
    """Intersection of two non-parallel segments: (point, t, u) with t, u in [0, 1], or None."""
    rx, ry = p2[0] - p1[0], p2[1] - p1[1]
    sx, sy = q2[0] - q1[0], q2[1] - q1[1]
    lr, ls = math.hypot(rx, ry), math.hypot(sx, sy)
    if lr < 1e-9 or ls < 1e-9:
        return None
    den = rx * sy - ry * sx
    if abs(den) < 1e-7 * lr * ls:
        return None
    qx, qy = q1[0] - p1[0], q1[1] - p1[1]
    t = (qx * sy - qy * sx) / den
    u = (qx * ry - qy * rx) / den
    if -TOL / lr <= t <= 1 + TOL / lr and -TOL / ls <= u <= 1 + TOL / ls:
        return (p1[0] + t * rx, p1[1] + t * ry), t, u
    return None


def common_stretch(p1, p2, q1, q2):
    """End points of the stretch on which two parallel segments lie on top of each other, or None."""
    rx, ry = p2[0] - p1[0], p2[1] - p1[1]
    lr = math.hypot(rx, ry)
    sx, sy = q2[0] - q1[0], q2[1] - q1[1]
    ls = math.hypot(sx, sy)
    if lr < 1e-9 or ls < 1e-9 or abs(rx * sy - ry * sx) >= 1e-7 * lr * ls:
        return None
    if abs((q1[0] - p1[0]) * ry - (q1[1] - p1[1]) * rx) / lr > 1e-5:
        return None
    t1 = ((q1[0] - p1[0]) * rx + (q1[1] - p1[1]) * ry) / (lr * lr)
    t2 = ((q2[0] - p1[0]) * rx + (q2[1] - p1[1]) * ry) / (lr * lr)
    lo, hi = max(0.0, min(t1, t2)), min(1.0, max(t1, t2))
    if (hi - lo) * lr < 1e-4:
        return None
    return (p1[0] + lo * rx, p1[1] + lo * ry), (p1[0] + hi * rx, p1[1] + hi * ry)


def arms_at(seg, t):
    """Directions (unit vectors) in which a segment leaves the point at parameter t."""
    (x1, y1), (x2, y2) = seg["a"], seg["b"]
    l = math.hypot(x2 - x1, y2 - y1)
    d = ((x2 - x1) / l, (y2 - y1) / l)
    out = []
    if t < 1 - TOL / l:
        out.append(d)
    if t > TOL / l:
        out.append((-d[0], -d[1]))
    return out


def _ccw(ref, d):
    """Counter-clockwise angle from direction ref to direction d, in [0, 2 pi)."""
    return (math.atan2(d[1], d[0]) - math.atan2(ref[1], ref[0])) % (2 * math.pi)


def _same_dir(d, e, tol=1e-3):
    return abs((_ccw(e, d) + math.pi) % (2 * math.pi) - math.pi) < tol


def _alternates(labels):
    """True when the cyclic sequence of labels contains A B A B: wire A passes from one side of wire B to the other."""
    comp = [w for i, w in enumerate(labels) if w != labels[i - 1]]
    return len(comp) >= 4


def contact_is_crossing(path, arms_a, arms_b):
    """Do two wires cross at a contact of their centre lines in plan view?

    path     the contact: one point, or the corner points of a common stretch (the wires lie on top of each other
             from path[0] to path[-1])
    arms_x   function(point) -> unit directions in which wire x leaves the point

    The contact is contracted to a point and the arms that leave it are read counter-clockwise around it: the wires
    cross if their arms alternate (A B A B).  An end that touches, a V that touches with its tip, and a wire that joins
    the other, runs on top of it and leaves on the side it came from are not crossings; one that leaves on the other
    side is."""
    n = len(path)
    seq = []

    def outer(k, skip):
        out = []
        for who, fn in (("A", arms_a), ("B", arms_b)):
            for d in fn(path[k]):
                if not any(_same_dir(d, e) for e in skip) and not any(w == who and _same_dir(d, e) for e, w in out):
                    out.append((d, who))
        return out

    def unit(p, q):
        l = math.hypot(q[0] - p[0], q[1] - p[1])
        return ((q[0] - p[0]) / l, (q[1] - p[1]) / l)
    if n == 1:
        arms = outer(0, [])
        seq = [w for _, w in sorted(arms, key=lambda a: math.atan2(a[0][1], a[0][0]))]
        return _alternates(seq)
    f0 = unit(path[0], path[1])
    seq += [w for _, w in sorted(outer(0, [f0]), key=lambda a: _ccw(f0, a[0]))]
    side = []
    for k in range(1, n - 1):                    # along the right-hand side of the stretch
        f, bk = unit(path[k], path[k + 1]), unit(path[k], path[k - 1])
        tb = _ccw(f, bk)
        arms = sorted(outer(k, [f, bk]), key=lambda a: _ccw(f, a[0]))
        seq += [w for d, w in arms if _ccw(f, d) > tb]
        side.append([w for d, w in arms if _ccw(f, d) < tb])
    bn = unit(path[-1], path[-2])
    seq += [w for _, w in sorted(outer(n - 1, [bn]), key=lambda a: _ccw(bn, a[0]))]
    for left in reversed(side):                  # and back along the left-hand side
        seq += left
    return _alternates(seq)


def clip_seg(a, b, bb):
    """Liang-Barsky: the part of segment a-b inside the box (x0, y0, x1, y1), or None."""
    x0, y0, x1, y1 = bb
    dx, dy = b[0] - a[0], b[1] - a[1]
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, a[0] - x0), (dx, x1 - a[0]), (-dy, a[1] - y0), (dy, y1 - a[1])):
        if abs(p) < 1e-12:
            if q < 0:
                return None
        else:
            r = q / p
            if p < 0:
                if r > t1:
                    return None
                t0 = max(t0, r)
            else:
                if r < t0:
                    return None
                t1 = min(t1, r)
    if t1 - t0 <= 1e-9:
        return None
    return (a[0] + t0 * dx, a[1] + t0 * dy), (a[0] + t1 * dx, a[1] + t1 * dy)


def poly_from_set(ps):
    """SHAPE_POLY_SET -> shapely polygons with their holes.  KiCad keeps zone fills 'fractured' (one outline, the
    holes joined to it by zero-width slits), which shapely reads as an invalid polygon: unfracture a copy first."""
    ps = pcbnew.SHAPE_POLY_SET(ps)
    try:
        ps.Unfracture()
    except Exception:       # noqa: BLE001  (API differences: buffer(0) below repairs the polygon as well)
        pass
    out = []
    for i in range(ps.OutlineCount()):
        ol = ps.Outline(i)
        ext = [C.k2c(ol.CPoint(k)) for k in range(ol.PointCount())]
        holes = []
        for h in range(ps.HoleCount(i)):
            hl = ps.Hole(i, h)
            holes.append([C.k2c(hl.CPoint(k)) for k in range(hl.PointCount())])
        if len(ext) >= 3:
            pg = Polygon(ext, holes)
            out.append(pg if pg.is_valid else pg.buffer(0))
    return out


# ------------------------------------------------------------------------------------------------ the scene
class Scene:
    """Everything the measurements need: wire segments, vias, parts (two-pad parts with their lands), planes."""

    def __init__(self):
        self.segs = []          # dict(net, cls, layer, a, b, width, src, stage, wire)
        self.vias = []          # dict(net, x, y, d, drill, src)
        self.parts = {}         # ref -> dict(ref, sheet, side, x, y, pads=[dict(num, net, bbox, c)], func)
        self.pads = []          # every copper pad: dict(ref, net, layer(s), bbox) for the picture and for roles
        self.planes = {}        # layer -> {net: shapely geometry}
        self.plane_src = ""
        self.extra_groups = []
        self.spec_groups = []   # floorplan.data.resistor_groups of the spec: [{"name":, "parts": [refs]}]
        self.notes = []
        self.outline = None
        self._w = 0

    def add_wire(self, net, cls, layer, pts, width, src, stage=None):
        wid = self._w                    # one id per track item / plan wire
        self._w = wid + 1
        for a, b in zip(pts, pts[1:]):
            if math.hypot(b[0] - a[0], b[1] - a[1]) < 1e-7:
                continue
            self.segs.append({"net": net, "cls": cls, "layer": layer, "a": (float(a[0]), float(a[1])), "b": (float(b[0]), float(b[1])),
                              "width": width, "src": src, "stage": stage, "wire": wid})


def _pad_bbox(p):
    bb = p.GetBoundingBox()
    x0, y1 = C.k2c(pcbnew.VECTOR2I(bb.GetLeft(), bb.GetTop()))
    x1, y0 = C.k2c(pcbnew.VECTOR2I(bb.GetRight(), bb.GetBottom()))
    return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))


def load_scene(args, spec, cl):
    board = C.load_board(args.board)
    sc = Scene()
    sc.spec_groups = (spec["floorplan"].get("data") or {}).get("resistor_groups", []) if not args.plan else []
    lname = lambda t: board.GetLayerName(t.GetLayer())      # noqa: E731
    plan = json.load(open(args.plan)) if args.plan else None
    drop_stage = set(args.drop_stage or [])
    drop_class = set(args.drop_class or [])
    drop_sheet = set(args.drop_sheet or [])
    if plan and not args.keep_all:
        drop_stage |= {"s30_data", "s50_power"}
        drop_sheet |= {"write", "read"}
    sc.drop = {"stages": sorted(drop_stage), "classes": sorted(drop_class), "sheets": sorted(drop_sheet)}

    # ---- parts: plan placements are applied to the footprints in memory (never saved)
    plan_refs = {}
    if plan:
        for p in plan.get("parts", []):
            if p.get("ref"):
                plan_refs[p["ref"]] = p
    for ref, p in plan_refs.items():
        fp = board.FindFootprintByReference(ref)
        if fp is None:
            sc.notes.append("plan part %s is not on the board: ignored" % ref)
            continue
        fp.SetOrientationDegrees(0)
        want_b = p.get("layer", "F.Cu") == "B.Cu"
        fp.SetPosition(C.c2k(p["x"], p["y"]))
        if fp.IsFlipped() != want_b:
            fp.Flip(fp.GetPosition(), pcbnew.FLIP_DIRECTION_TOP_BOTTOM)
        fp.SetOrientationDegrees(p.get("rot", 0))
    u1_pads, j1_pads = collections.defaultdict(list), collections.defaultdict(list)
    for fp in board.GetFootprints():
        ref = fp.GetReference()
        sheet = cl.sheet.get(ref, "")
        dropped = sheet in drop_sheet and ref not in plan_refs
        pads = []
        for p in fp.Pads():
            if not p.GetNumber() or not p.IsOnCopperLayer():
                continue
            bb = _pad_bbox(p)
            lay = [l for l in ("F.Cu", "B.Cu") if p.IsOnLayer(C.CU[l])]
            d = {"ref": ref, "num": p.GetNumber(), "net": p.GetNetname(), "bbox": bb, "c": C.k2c(p.GetPosition()), "layers": lay}
            pads.append(d)
            if ref == "U1":
                u1_pads[d["net"]].append(d)
            elif ref == "J1":
                j1_pads[d["net"]].append(d)
        if dropped:
            continue
        sc.pads += pads
        if re.match(r"^[RC]\d+$", ref) and len(pads) == 2:
            x, y = C.k2c(fp.GetPosition())
            sc.parts[ref] = {"ref": ref, "sheet": sheet, "side": "B.Cu" if fp.IsFlipped() else "F.Cu", "x": x, "y": y, "pads": pads}
    if plan:
        for i, p in enumerate(plan.get("parts", [])):
            if p.get("ref"):
                continue
            across, along = ROLE_FP.get(p.get("fp", "0201"), ROLE_FP["0201"])
            ang = math.radians(p.get("rot", 0))
            ux, uy = math.cos(ang), math.sin(ang)
            nets = (p.get("nets") or ["", ""]) + ["", ""]
            pads = []
            for k, sgn in enumerate((-1, 1)):
                cx, cy = p["x"] + sgn * ux * along / 4, p["y"] + sgn * uy * along / 4
                hx = abs(ux) * along / 4 + abs(uy) * across / 2
                hy = abs(uy) * along / 4 + abs(ux) * across / 2
                pads.append({"ref": p.get("role", "part%d" % i), "num": str(k + 1), "net": nets[k], "bbox": (cx - hx, cy - hy, cx + hx, cy + hy),
                             "c": (cx, cy), "layers": [p.get("layer", "F.Cu")]})
            ref = p.get("role", "part%d" % i)
            sc.parts[ref] = {"ref": ref, "sheet": p.get("group") or re.sub(r"[\d_]+$", "", ref), "side": p.get("layer", "F.Cu"),
                             "x": p["x"], "y": p["y"], "pads": pads, "role_part": True}
            sc.pads += pads

    # ---- wires of the board
    tracks, vias = [], []
    for t in board.GetTracks():
        (vias if t.Type() == pcbnew.PCB_VIA_T else tracks).append(t)
    # role of DQ copper on the outer layers: components of one net on one layer, joined at common end points
    role = {}
    by_nl = collections.defaultdict(list)
    for t in tracks:
        if t.GetNetname() in cl.dq and lname(t) != "In3.Cu":
            by_nl[(t.GetNetname(), lname(t))].append(t)
    disagree = 0
    for (net, lay), ts in by_nl.items():
        parent = list(range(len(ts)))

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i
        ends = collections.defaultdict(list)
        for i, t in enumerate(ts):
            for q in (C.k2c(t.GetStart()), C.k2c(t.GetEnd())):
                ends[(round(q[0], 4), round(q[1], 4))].append(i)
        for ii in ends.values():
            for j in ii[1:]:
                parent[find(j)] = find(ii[0])
        comp = collections.defaultdict(list)
        for i in range(len(ts)):
            comp[find(i)].append(i)

        def touches(pads, idx):
            for i in idx:
                for q in (C.k2c(ts[i].GetStart()), C.k2c(ts[i].GetEnd())):
                    for pd in pads:
                        if lay in pd["layers"] and pd["bbox"][0] - 1e-3 <= q[0] <= pd["bbox"][2] + 1e-3 and pd["bbox"][1] - 1e-3 <= q[1] <= pd["bbox"][3] + 1e-3:
                            return True
            return False
        for idx in comp.values():
            at_u1, at_j1 = touches(u1_pads.get(net, []), idx), touches(j1_pads.get(net, []), idx)
            for i in idx:
                st = C.stage_of(ts[i])
                by_stage = None          # one data stage (s30_data): the role of DQ copper on an outer layer follows from what it reaches
                if at_u1 and at_j1:
                    r_ = by_stage or "WR_DQ"
                    if not by_stage:
                        sc.notes.append("%s on %s: one piece of copper reaches both a U1 ball and a finger; taken as WR_DQ" % (net, lay))
                else:
                    r_ = "RD_DQ" if at_j1 else "WR_DQ"
                if by_stage and by_stage != r_:
                    disagree += 1
                role[ts[i].m_Uuid.AsString()] = r_
    if disagree:
        sc.notes.append("%d DQ track items: role by geometry differs from the stage tag (geometry used)" % disagree)
    kept = collections.Counter()
    # plan key "remove_board": [{"net": ..., "layer": ..., "at": [x, y]}]: board tracks of a kept stage that the plan replaces
    # (U1 strap links of s10 that a new neck runs through).  A track is removed if it passes within 0.05 mm of the point.
    removals = [(r["net"], r["layer"], (float(r["at"][0]), float(r["at"][1]))) for r in (plan or {}).get("remove_board", [])]
    removed = collections.Counter()
    for t in tracks:
        st = C.stage_of(t)
        net, lay = t.GetNetname(), lname(t)
        cls = cl.net_class(net, lay, role.get(t.m_Uuid.AsString()))
        if st in drop_stage or cls in drop_class:
            continue
        a, b = C.k2c(t.GetStart()), C.k2c(t.GetEnd())
        hit = None
        for k, (rn, rl, rp_) in enumerate(removals):
            if rn == net and rl == lay and LineString([a, b]).distance(Point(*rp_)) <= 0.05:
                hit = k
                break
        if hit is not None:
            removed[hit] += 1
            continue
        pts = _arc_pts(a, C.k2c(t.GetMid()), b) if t.Type() == pcbnew.PCB_ARC_T else [a, b]
        sc.add_wire(net, cls, lay, pts, C.MM(t.GetWidth()), "board", st)
        kept[st] += 1
    sc.kept = dict(kept)
    for k, (rn, rl, rp_) in enumerate(removals):
        sc.notes.append("plan removes board copper: %s on %s at (%.3f, %.3f): %d track item(s)%s" % (
            rn, rl, rp_[0], rp_[1], removed[k], "" if removed[k] else "  ** NOTHING FOUND **"))
    u1b = spec["floorplan"]["U1"]["body"]
    for v in vias:
        x, y = C.k2c(v.GetPosition())
        net = v.GetNetname()
        in_u1 = u1b["x"][0] <= x <= u1b["x"][1] and u1b["y"][0] <= y <= u1b["y"][1]
        if plan and not args.keep_all and not in_u1 and cl.net_class(net, "In3.Cu") in ("WR_DQ", "RD_DQ", "WCK", "RDQS", "VREF"):
            continue
        sc.vias.append({"net": net, "x": x, "y": y, "d": C.MM(v.GetWidth(pcbnew.F_Cu)), "drill": C.MM(v.GetDrillValue()), "src": "board"})

    # ---- the plan's own copper
    if plan:
        sc.plan_name = plan.get("name", os.path.basename(args.plan))
        for w in plan.get("wires", []):
            lay = w["layer"]
            if lay not in ("F.Cu", "In3.Cu", "B.Cu"):
                raise SystemExit("plan wire on %r: only F.Cu, In3.Cu and B.Cu carry wires" % lay)
            cls = w.get("class") or cl.net_class(w["net"], lay)
            if cls == "PWR" and w["net"] == "GND":
                cls = "GND"
            if cls not in CLASSES:
                raise SystemExit("plan wire %s: unknown class %r" % (w["net"], cls))
            if cls == "CMD":
                sc.notes.append("plan wire of class CMD (%s) ignored: the command stage is taken from the board" % w["net"])
                continue
            sc.add_wire(w["net"], cls, lay, w["points"], w.get("width", 0.15), "plan")
        for v in plan.get("vias", []):
            sc.vias.append({"net": v["net"], "x": v["x"], "y": v["y"], "d": v.get("d", 0.4), "drill": v.get("drill", 0.2), "src": "plan"})
        sc.extra_groups = [g for g in plan.get("groups", []) if g.get("kind", "resistor_group") == "resistor_group"]

    # ---- planes
    zones = [z for z in board.Zones() if not z.GetIsRuleArea()]
    for lay in PLANES:
        lid = C.CU[lay]
        per = collections.defaultdict(list)
        for z in zones:
            if not z.IsOnLayer(lid):
                continue
            if plan:
                per[z.GetNetname()] += poly_from_set(z.Outline())
            elif z.IsFilled():
                per[z.GetNetname()] += poly_from_set(z.GetFilledPolysList(lid))
        if plan:
            isl = [pl_ for pl_ in plan.get("planes", []) if pl_["layer"] == lay]
            if isl or any(pl_["layer"] == lay for pl_ in plan.get("planes", [])):
                per = collections.defaultdict(list, {"GND": per.get("GND", [])})
                for pl_ in isl:
                    per[pl_["net"]].append(box(*pl_["bbox"]))
            gnd = unary_union(per.get("GND", []))
            others = {n: unary_union(v) for n, v in per.items() if n != "GND"}
            for g_ in others.values():
                gnd = gnd.difference(g_.buffer(0.2))
            anti = [Point(v["x"], v["y"]).buffer(v["drill"] / 2 + 0.2) for v in sc.vias if v["net"] != "GND"]
            if anti:
                gnd = gnd.difference(unary_union(anti))
            sc.planes[lay] = dict(others, GND=gnd)
        else:
            sc.planes[lay] = {n: unary_union(v) for n, v in per.items()}
    sc.plane_src = ("plane plan: zone outlines of the board (netlist.json plane_plan)%s, less 0.2 mm around islands and the antipads of "
                    "every non-GND via" % (", islands replaced by the plan" if plan and plan.get("planes") else "")) if plan \
        else "zone fills of the board"
    if not plan and not all(sc.planes.get(l, {}).get("GND") is not None and not sc.planes[l]["GND"].is_empty for l in PLANES):
        sc.notes.append("the board has no zone fill on some plane layer: plane status is 'void' there (run s90_zones first)")
    # outline for the picture
    try:
        ps = pcbnew.SHAPE_POLY_SET()
        board.GetBoardPolygonOutlines(ps)
        pl = poly_from_set(ps)
        sc.outline = pl[0] if pl else None
    except Exception:       # noqa: BLE001  (drawing aid only)
        sc.outline = None
    return sc


# ------------------------------------------------------------------------------------------------ planes
class PlaneTest:
    def __init__(self, sc, disc):
        self.disc = disc
        self.geo = {l: {n: (g, prep(g)) for n, g in d.items() if g is not None and not g.is_empty} for l, d in sc.planes.items()}

    def between(self, la, lb):
        i, j = sorted((CU_ORDER.index(la), CU_ORDER.index(lb)))
        return [l for l in CU_ORDER[i + 1:j] if l in PLANES]

    def status(self, layer, x, y):
        pt = Point(x, y)
        d = self.geo.get(layer, {})
        if "GND" in d:
            g, pg = d["GND"]
            if pg.contains(pt.buffer(self.disc, 12)):
                return {"layer": layer, "status": "GND"}
            if pg.contains(pt):
                return {"layer": layer, "status": "GND-edge", "opening_mm": round(g.boundary.distance(pt), 3)}
        for n, (g, pg) in d.items():
            if n != "GND" and pg.contains(pt):
                return {"layer": layer, "status": n}
        return {"layer": layer, "status": "void"}

    def at(self, la, lb, x, y):
        st = [self.status(l, x, y) for l in self.between(la, lb)]
        return st, bool(st) and all(s["status"] == "GND" for s in st)


# ------------------------------------------------------------------------------------------------ crossings and overlap
def _key(sa, sb):
    """Canonical order of the two wires of a pair: by class rank, then layer (front to back), then net."""
    ka = (CLASSES.index(sa["cls"]), CU_ORDER.index(sa["layer"]), sa["net"])
    kb = (CLASSES.index(sb["cls"]), CU_ORDER.index(sb["layer"]), sb["net"])
    return (sa, sb, False) if ka <= kb else (sb, sa, True)


def category(ca, cb):
    if "CMD" in (ca, cb):
        o = cb if ca == "CMD" else ca
        return "cmd_x_data" if o in DATA else "cmd_x_cmd" if o == "CMD" else "cmd_x_" + o.lower()
    if ca in DATA and cb in DATA:
        return "data_x_data"
    o = cb if ca in DATA else ca
    return "hs_x_" + o.lower()


def find_crossings(sc, args, planes):
    segs = sc.segs
    lines = [LineString([s["a"], s["b"]]) for s in segs]
    tree = shapely.STRtree(lines)
    reach = shapely.buffer(lines, args.overlap_dist + 1e-3)
    ia, ib = tree.query(reach, predicate="intersects")
    hits = collections.defaultdict(list)            # (netA, clsA, layA, netB, clsB, layB) -> [point]
    runs = collections.defaultdict(list)            # same key -> [(q1, q2)]: stretches where the centre lines coincide
    same = collections.defaultdict(list)
    ov_a = collections.defaultdict(lambda: collections.defaultdict(list))      # key -> seg index -> [(t0, t1)]
    ov_b = collections.defaultdict(lambda: collections.defaultdict(list))
    ov_min = {}
    cos_lim = math.cos(math.radians(args.overlap_angle))
    for i, j in zip(ia.tolist(), ib.tolist()):
        if i >= j:
            continue
        si, sj = segs[i], segs[j]
        if si["net"] == sj["net"]:
            continue
        hs = si["cls"] in HS or sj["cls"] in HS
        if not hs and not args.all_pairs:
            continue
        sa, sb, sw = _key(si, sj)
        key = (sa["net"], sa["cls"], sa["layer"], sb["net"], sb["cls"], sb["layer"])
        h = seg_hit(sa["a"], sa["b"], sb["a"], sb["b"])
        if sa["layer"] == sb["layer"]:
            if h or lines[i].distance(lines[j]) < 1e-6:
                p_ = h[0] if h else tuple(lines[i].intersection(lines[j]).representative_point().coords[0])
                same[key].append(p_)
            continue
        if h:
            hits[key].append(h[0])
        else:
            st = common_stretch(sa["a"], sa["b"], sb["a"], sb["b"])
            if st:
                runs[key].append(st)
        # parallel overlap
        da = (sa["b"][0] - sa["a"][0], sa["b"][1] - sa["a"][1])
        db = (sb["b"][0] - sb["a"][0], sb["b"][1] - sb["a"][1])
        la_, lb_ = math.hypot(*da), math.hypot(*db)
        if abs(da[0] * db[0] + da[1] * db[1]) / (la_ * lb_) < cos_lim:
            continue
        ga, gb = (lines[j], lines[i]) if sw else (lines[i], lines[j])
        ka, kb = (j, i) if sw else (i, j)
        for g1, g2, store, k1, l1 in ((ga, gb, ov_a, ka, la_), (gb, ga, ov_b, kb, lb_)):
            part = g1.intersection(g2.buffer(args.overlap_dist, 16))
            if part.is_empty or part.length < 1e-6:
                continue
            for g_ in getattr(part, "geoms", [part]):
                if g_.geom_type != "LineString":
                    continue
                t0, t1 = sorted((g1.project(Point(g_.coords[0])), g1.project(Point(g_.coords[-1]))))
                store[key][k1].append((t0, t1))
        ov_min[key] = min(ov_min.get(key, 9.9), ga.distance(gb))

    by_wire = collections.defaultdict(list)
    for s_ in segs:
        by_wire[(s_["net"], s_["layer"])].append(s_)

    def arms_fn(net, layer):
        def fn(pt):
            out = []
            for s_ in by_wire[(net, layer)]:
                (x1, y1), (x2, y2) = s_["a"], s_["b"]
                l = math.hypot(x2 - x1, y2 - y1)
                t = ((pt[0] - x1) * (x2 - x1) + (pt[1] - y1) * (y2 - y1)) / (l * l)
                if t < -1e-4 / l or t > 1 + 1e-4 / l:
                    continue
                if abs((pt[0] - x1) * (y2 - y1) - (pt[1] - y1) * (x2 - x1)) / l > 1e-4:
                    continue
                for d in arms_at(s_, min(1.0, max(0.0, t)) if 1e-4 / l < t < 1 - 1e-4 / l else (0.0 if t < 0.5 else 1.0)):
                    if not any(_same_dir(d, e) for e in out):
                        out.append(d)
            return out
        return fn

    crossings, touches = [], []
    for key in set(hits) | set(runs):
        # contact nodes: crossing points and the ends of common stretches, merged within 0.1 um
        nodes = []

        def node(pt):
            for k_, q in enumerate(nodes):
                if math.hypot(q[0] - pt[0], q[1] - pt[1]) < 1e-4:
                    return k_
            nodes.append(pt)
            return len(nodes) - 1
        for pt in hits.get(key, []):
            node(pt)
        edges = set()
        stretches = [(node(q1), node(q2)) for q1, q2 in runs.get(key, [])]
        for i_, j_ in stretches:                    # split a stretch at every node that lies inside it
            if i_ == j_:
                continue
            p1, p2 = nodes[i_], nodes[j_]
            l = math.dist(p1, p2)
            on = []
            for k_, q in enumerate(nodes):
                t = ((q[0] - p1[0]) * (p2[0] - p1[0]) + (q[1] - p1[1]) * (p2[1] - p1[1])) / (l * l)
                if -1e-9 <= t <= 1 + 1e-9 and abs((q[0] - p1[0]) * (p2[1] - p1[1]) - (q[1] - p1[1]) * (p2[0] - p1[0])) / l < 1e-4:
                    on.append((t, k_))
            on.sort()
            for (_, u), (_, v) in zip(on, on[1:]):
                if u != v:
                    edges.add((min(u, v), max(u, v)))
        adj = collections.defaultdict(set)
        for u, v in edges:
            adj[u].add(v)
            adj[v].add(u)
        seen = set()
        fa, fb = arms_fn(key[0], key[2]), arms_fn(key[3], key[5])
        for k0 in range(len(nodes)):
            if k0 in seen:
                continue
            comp, todo = [], [k0]
            while todo:
                u = todo.pop()
                if u in seen:
                    continue
                seen.add(u)
                comp.append(u)
                todo += list(adj[u])
            ends = [u for u in comp if len(adj[u]) == 1]
            if len(comp) == 1:
                paths = [[comp[0]]]
            elif len(ends) == 2 and all(len(adj[u]) <= 2 for u in comp):
                path, prev = [ends[0]], None
                while len(path) < len(comp):
                    nxt = [v for v in adj[path[-1]] if v != prev]
                    prev = path[-1]
                    path.append(nxt[0])
                paths = [path]
            else:                                       # a branched or closed common stretch: judge every node by itself
                paths = [[u] for u in comp]
                sc.notes.append("%s (%s) / %s (%s): branched common stretch near (%.3f, %.3f); contacts judged point by point"
                                % (key[0], key[2], key[3], key[5], nodes[comp[0]][0], nodes[comp[0]][1]))
            for path in paths:
                pts = [nodes[u] for u in path]
                ln = sum(math.dist(p_, q_) for p_, q_ in zip(pts, pts[1:]))
                if len(pts) == 1:
                    x, y = pts[0]
                else:
                    x, y = tuple(LineString(pts).interpolate(ln / 2).coords[0])
                rec = {"a": {"net": key[0], "class": key[1], "layer": key[2]}, "b": {"net": key[3], "class": key[4], "layer": key[5]},
                       "x": round(x, 4), "y": round(y, 4), "category": category(key[1], key[4])}
                if len(pts) > 1:
                    rec["common_stretch_mm"] = round(ln, 3)
                    rec["from"], rec["to"] = [round(v, 4) for v in pts[0]], [round(v, 4) for v in pts[-1]]
                if not contact_is_crossing(pts, fa, fb):
                    touches.append(rec)
                    continue
                if len(pts) == 1:
                    da, db = fa(pts[0])[0], fb(pts[0])[0]
                    rec["angle_deg"] = round(math.degrees(math.acos(min(1.0, abs(da[0] * db[0] + da[1] * db[1])))), 1)
                else:
                    rec["angle_deg"] = 0.0
                rec["planes"], rec["solid_gnd_between"] = planes.at(key[2], key[5], x, y)
                crossings.append(rec)
    crossings.sort(key=lambda r: (CLASSES.index(r["a"]["class"]), CLASSES.index(r["b"]["class"]), r["a"]["net"], r["b"]["net"], r["x"], r["y"]))

    overlaps = []
    for key in set(ov_a) | set(ov_b):
        def total(store, k_=key):
            tot, ext = 0.0, []          # ext: the end points of every overlapping stretch, in pairs
            for idx, iv in store.get(k_, {}).items():
                iv.sort()
                cur0, cur1 = iv[0]
                merged = []
                for t0, t1 in iv[1:]:
                    if t0 <= cur1 + 1e-6:
                        cur1 = max(cur1, t1)
                    else:
                        merged.append((cur0, cur1))
                        cur0, cur1 = t0, t1
                merged.append((cur0, cur1))
                for t0, t1 in merged:
                    tot += t1 - t0
                    ext += [tuple(lines[idx].interpolate(t0).coords[0]), tuple(lines[idx].interpolate(t1).coords[0])]
            return tot, ext
        la_, ea = total(ov_a)
        lb_, eb = total(ov_b)
        ln, ext = (la_, ea) if la_ <= lb_ else (lb_, eb)
        if ln < args.overlap_min:
            continue
        far = max(((p, q) for p in ext for q in ext), key=lambda pq: math.dist(pq[0], pq[1]))
        mid = ((far[0][0] + far[1][0]) / 2, (far[0][1] + far[1][1]) / 2)
        pl, solid = planes.at(key[2], key[5], *mid)
        overlaps.append({"a": {"net": key[0], "class": key[1], "layer": key[2]}, "b": {"net": key[3], "class": key[4], "layer": key[5]},
                         "length_mm": round(ln, 3), "length_a_mm": round(la_, 3), "length_b_mm": round(lb_, 3),
                         "from": [round(far[0][0], 3), round(far[0][1], 3)], "to": [round(far[1][0], 3), round(far[1][1], 3)],
                         "min_distance_mm": round(ov_min.get(key, 0.0), 3), "category": category(key[1], key[4]),
                         "planes": pl, "solid_gnd_between": solid,
                         "pieces": [[[round(ext[k][0], 4), round(ext[k][1], 4)], [round(ext[k + 1][0], 4), round(ext[k + 1][1], 4)]]
                                    for k in range(0, len(ext), 2)]})
    overlaps.sort(key=lambda r: -r["length_mm"])
    same_layer = [{"a": {"net": k[0], "class": k[1], "layer": k[2]}, "b": {"net": k[3], "class": k[4], "layer": k[5]},
                   "x": round(p[0], 4), "y": round(p[1], 4)} for k, pp in same.items() for p in pp]
    return crossings, touches, overlaps, same_layer


def pair_events(records, cluster, length_key=None):
    """Crossings (or overlaps) with a differential pair counted as one wire: records of the same pair-level key whose
    points lie within 'cluster' mm of each other are one event."""
    groups = collections.defaultdict(list)
    for r in records:
        k = (pair_of(r["a"]["net"]), r["a"]["class"], r["a"]["layer"], pair_of(r["b"]["net"]), r["b"]["class"], r["b"]["layer"])
        groups[k].append(r)
    out = []
    for k, rr in groups.items():
        if length_key:
            out.append({"a": {"net": k[0], "class": k[1], "layer": k[2]}, "b": {"net": k[3], "class": k[4], "layer": k[5]},
                        "category": rr[0]["category"], length_key: max(r[length_key] for r in rr), "legs": len(rr)})
            continue
        left = list(rr)
        while left:
            seed = [left.pop()]
            grew = True
            while grew:
                grew = False
                for r in left[:]:
                    if any(math.hypot(r["x"] - s["x"], r["y"] - s["y"]) <= cluster for s in seed):
                        seed.append(r)
                        left.remove(r)
                        grew = True
            out.append({"a": {"net": k[0], "class": k[1], "layer": k[2]}, "b": {"net": k[3], "class": k[4], "layer": k[5]},
                        "category": seed[0]["category"], "x": round(sum(s["x"] for s in seed) / len(seed), 3),
                        "y": round(sum(s["y"] for s in seed) / len(seed), 3), "legs": len(seed)})
    return out


# ------------------------------------------------------------------------------------------------ resistor groups
def _rect_gap(a, b):
    dx = max(a[0] - b[2], b[0] - a[2], 0.0)
    dy = max(a[1] - b[3], b[1] - a[3], 0.0)
    return math.hypot(dx, dy)


def _cluster_1d(vals, tol):
    out = []
    for v in sorted(vals):
        if out and v - out[-1][-1] <= tol:
            out[-1].append(v)
        else:
            out.append([v])
    return [sum(c) / len(c) for c in out]


def derive_groups(sc, cl, args):
    spec_group = {r: g["name"] for g in sc.spec_groups for r in g["parts"]}        # floorplan.data.resistor_groups: the groups of the spec

    def func(p):
        if p.get("role_part"):
            return p["sheet"]
        if p["ref"] in spec_group:
            return spec_group[p["ref"]]
        if p["sheet"] == "command":
            return "cmd_div"
        if p["sheet"] == "write":
            return "wr_div"
        if p["sheet"] == "read":
            return "rdqs_term" if any(pd["net"] in cl.rdqs for pd in p["pads"]) else "vref"
        return None
    cand = [p for p in sc.parts.values() if func(p)]
    for p in cand:
        p["func"] = func(p)
        p["bbox"] = (min(pd["bbox"][0] for pd in p["pads"]), min(pd["bbox"][1] for pd in p["pads"]),
                     max(pd["bbox"][2] for pd in p["pads"]), max(pd["bbox"][3] for pd in p["pads"]))
    parent = list(range(len(cand)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i in range(len(cand)):
        for j in range(i + 1, len(cand)):
            if cand[i]["func"] == cand[j]["func"] and _rect_gap(cand[i]["bbox"], cand[j]["bbox"]) <= args.group_link:
                parent[find(j)] = find(i)
    comp = collections.defaultdict(list)
    for i, p in enumerate(cand):
        comp[find(i)].append(p)
    groups, skipped = [], []
    for ps in comp.values():
        bb = (min(p["bbox"][0] for p in ps), min(p["bbox"][1] for p in ps), max(p["bbox"][2] for p in ps), max(p["bbox"][3] for p in ps))
        f = ps[0]["func"]
        if f == "vref":
            f = "n_ref" if any(pd["net"] in cl.vref and pd["net"].startswith("VREF_N") for p in ps for pd in p["pads"]) else "vref_dac"
        refs = sorted(p["ref"] for p in ps)
        if args.group_margin:
            bb = (bb[0] - args.group_margin, bb[1] - args.group_margin, bb[2] + args.group_margin, bb[3] + args.group_margin)
        if len(ps) < 2 or bb[1] > args.group_near:
            skipped.append({"func": f, "refs": refs, "bbox": [round(v, 3) for v in bb],
                            "why": "single part" if len(ps) < 2 else "not at the fingers (lowest land at y = %.2f > %.1f)" % (bb[1], args.group_near)})
            continue
        # column axis: the pad axis of the parts (majority), checked against the direction in which the parts repeat
        ax = collections.Counter()
        for p in ps:
            (x1, y1), (x2, y2) = p["pads"][0]["c"], p["pads"][1]["c"]
            ax["y" if abs(y2 - y1) >= abs(x2 - x1) else "x"] += 1
        axis = ax.most_common(1)[0][0]
        basis = "pad axis of %d / %d parts" % (ax[axis], len(ps))
        g = {"name": f, "func": f, "bbox": [round(v, 3) for v in bb], "column_axis": axis, "axis_basis": basis, "parts": refs,
             "sides": sorted({p["side"] for p in ps}), "source": "footprints"}
        ci = 0 if axis == "y" else 1
        cols = _cluster_1d([(p["x"], p["y"])[ci] for p in ps], 0.2)
        rows = []
        for rc in _cluster_1d([(p["x"], p["y"])[1 - ci] for p in ps], 0.3):
            mem = [p for p in ps if abs((p["x"], p["y"])[1 - ci] - rc) <= 0.6]
            rows.append({"at": round(rc, 3), "from": round(min(p["bbox"][1 - ci] for p in mem), 3), "to": round(max(p["bbox"][3 - ci] for p in mem), 3),
                         "parts": sorted(p["ref"] for p in mem if abs((p["x"], p["y"])[1 - ci] - rc) <= 0.31)})
        g["columns"] = [round(c, 3) for c in cols]
        g["rows"] = rows
        if len(cols) == 1 and len(rows) > 2:
            g["axis_basis"] += "; NOTE: one column only, the parts repeat along the axis - check the axis"
        nets = collections.defaultdict(set)
        for p in ps:
            k = min(range(len(cols)), key=lambda q: abs(cols[q] - (p["x"], p["y"])[ci]))
            for pd in p["pads"]:
                if pd["net"]:
                    nets[pd["net"]].add(k)
        g["nets"] = {n: sorted(v) for n, v in sorted(nets.items())}
        groups.append(g)
    # names: number the groups of one function from west to east
    by_f = collections.defaultdict(list)
    for g in groups:
        by_f[g["func"]].append(g)
    for f, gg in by_f.items():
        gg.sort(key=lambda g: g["bbox"][0])
        if len(gg) > 1:
            for i, g in enumerate(gg):
                g["name"] = "%s_%d" % (f, i + 1)
    for e in sc.extra_groups:
        bb = [float(v) for v in e["bbox"]]
        g = {"name": e.get("name", "plan_group"), "func": e.get("func", "plan"), "bbox": bb, "column_axis": e.get("column_axis", "y"),
             "axis_basis": "given by the plan", "source": "plan", "parts": [], "sides": [], "rows": e.get("rows", [])}
        ci = 0 if g["column_axis"] == "y" else 1
        inside = [p for p in sc.parts.values() if bb[0] - 1e-6 <= p["x"] <= bb[2] + 1e-6 and bb[1] - 1e-6 <= p["y"] <= bb[3] + 1e-6]
        g["parts"] = sorted(p["ref"] for p in inside)
        g["sides"] = sorted({p["side"] for p in inside})
        cols = e.get("columns") or _cluster_1d([(p["x"], p["y"])[ci] for p in inside], 0.2)
        g["columns"] = [round(c, 3) for c in cols]
        nets = collections.defaultdict(set)
        for p in inside:
            if cols:
                k = min(range(len(cols)), key=lambda q: abs(cols[q] - (p["x"], p["y"])[ci]))
                for pd in p["pads"]:
                    if pd["net"]:
                        nets[pd["net"]].add(k)
        for n, v in (e.get("nets") or {}).items():
            nets[n] |= set(v)
        g["nets"] = {n: sorted(v) for n, v in sorted(nets.items())}
        # a derived group that the plan's group covers is replaced by it
        groups = [h for h in groups if h["source"] == "plan" or
                  box(*h["bbox"]).intersection(box(*bb)).area < 0.5 * box(*h["bbox"]).area]
        groups.append(g)
    groups.sort(key=lambda g: (g["bbox"][0], g["bbox"][1]))
    return groups, skipped


def find_transversals(sc, groups, args):
    out = []
    sin_lim = math.sin(math.radians(args.trans_angle))
    for g in groups:
        bb = g["bbox"]
        ci = 0 if g["column_axis"] == "y" else 1          # index of the coordinate ACROSS the columns
        cols = g["columns"]
        pieces = []
        lands = {}

        def own_lands(net, lay):
            """Lands of the net on the layer in or near the box: a track inside its own land is part of the land."""
            if (net, lay) not in lands:
                gg = [box(*p["bbox"]) for p in sc.pads if p["net"] == net and lay in p["layers"] and _rect_gap(p["bbox"], bb) < 0.5]
                gg += [Point(v["x"], v["y"]).buffer(v["d"] / 2, 16) for v in sc.vias if v["net"] == net and _rect_gap((v["x"], v["y"], v["x"], v["y"]), bb) < 0.5]
                lands[(net, lay)] = unary_union(gg) if gg else None
            return lands[(net, lay)]
        for s in sc.segs:
            if max(s["a"][0], s["b"][0]) < bb[0] or min(s["a"][0], s["b"][0]) > bb[2] or max(s["a"][1], s["b"][1]) < bb[1] or min(s["a"][1], s["b"][1]) > bb[3]:
                continue
            c = clip_seg(s["a"], s["b"], bb)
            if not c:
                continue
            parts = [c]
            ol = own_lands(s["net"], s["layer"])
            if ol is not None:
                rest = LineString(c).difference(ol)
                parts = [tuple(g_.coords) for g_ in getattr(rest, "geoms", [rest]) if g_.geom_type == "LineString" and not g_.is_empty]
            for (x1, y1), (x2, y2) in ((q[0], q[-1]) for q in parts):
                ln = math.hypot(x2 - x1, y2 - y1)
                if ln < 1e-6:
                    continue
                across = abs((x2 - x1, y2 - y1)[ci]) / ln            # sine of the angle to the column axis
                pieces.append({"seg": s, "p": (x1, y1), "q": (x2, y2), "len": ln, "off": math.degrees(math.asin(min(1.0, across))), "trans": across > sin_lim})
        # runs: connected off-axis pieces of one net on one layer
        by = collections.defaultdict(list)
        for pc in pieces:
            by[(pc["seg"]["net"], pc["seg"]["layer"], pc["trans"])].append(pc)
        runs = []
        for (net, lay, trans), pcs in by.items():
            if not trans:
                for pc in pcs:                                   # criterion B on its own: a shallow wire across several columns
                    lo, hi = sorted((pc["p"][ci], pc["q"][ci]))
                    if sum(1 for c_ in cols if lo + 0.05 < c_ < hi - 0.05) >= 2:
                        runs.append([pc])
                continue
            parent = list(range(len(pcs)))

            def find(i):
                while parent[i] != i:
                    parent[i] = parent[parent[i]]
                    i = parent[i]
                return i
            for i in range(len(pcs)):
                for j in range(i + 1, len(pcs)):
                    if min(math.dist(a_, b_) for a_ in (pcs[i]["p"], pcs[i]["q"]) for b_ in (pcs[j]["p"], pcs[j]["q"])) < 2e-3:
                        parent[find(j)] = find(i)
            comp = collections.defaultdict(list)
            for i, pc in enumerate(pcs):
                comp[find(i)].append(pc)
            runs += list(comp.values())
        for run in runs:
            ln = sum(pc["len"] for pc in run)
            pts = [q for pc in run for q in (pc["p"], pc["q"])]
            lo, hi = min(q[ci] for q in pts), max(q[ci] for q in pts)
            a_lo, a_hi = min(q[1 - ci] for q in pts), max(q[1 - ci] for q in pts)
            touched = [k for k, c_ in enumerate(cols) if lo - 0.05 <= c_ <= hi + 0.05]
            crossed = [k for k, c_ in enumerate(cols) if lo + 0.05 < c_ < hi - 0.05]
            crit = []
            if run[0]["trans"] and ln > args.trans_len + 1e-6:
                crit.append("A")
            if len(crossed) >= 2:
                crit.append("B")
            if not crit:
                continue
            s = run[0]["seg"]
            home = g["nets"].get(s["net"])
            if home is None:
                owner, tier = "foreign", ("foreign-through" if touched else "foreign-in-lane")
            elif len(home) >= 2:
                owner, tier = "own-common", ("common-threaded" if len(touched) >= 2 else "own-jog")
            else:
                owner, tier = "own-signal", ("own-through" if [k for k in touched if k not in home] else "own-jog")
            mid = (a_lo + a_hi) / 2
            where = ""
            rows = g.get("rows") or []
            for k, r in enumerate(rows):
                if r["from"] - 1e-6 <= mid <= r["to"] + 1e-6:
                    where = "over row %d (%s %.2f..%.2f)" % (k + 1, "y" if ci == 0 else "x", r["from"], r["to"])
            if not where and rows:
                below = [k for k, r in enumerate(rows) if r["to"] < mid]
                above = [k for k, r in enumerate(rows) if r["from"] > mid]
                if below and above:
                    where = "between rows %d and %d" % (below[-1] + 1, above[0] + 1)
                else:
                    where = "outside the rows"
            far = max(((p, q) for p in pts for q in pts), key=lambda pq: math.dist(pq[0], pq[1]))
            out.append({"group": g["name"], "net": s["net"], "class": s["cls"], "layer": s["layer"], "owner": owner, "tier": tier,
                        "criteria": "+".join(crit), "length_mm": round(ln, 3), "off_axis_deg": round(max(pc["off"] for pc in run), 1),
                        "from": [round(far[0][0], 3), round(far[0][1], 3)], "to": [round(far[1][0], 3), round(far[1][1], 3)],
                        "columns_touched": len(touched), "columns_crossed": len(crossed), "where": where,
                        "pieces": [[[round(pc["p"][0], 4), round(pc["p"][1], 4)], [round(pc["q"][0], 4), round(pc["q"][1], 4)]] for pc in run]})
    order = ["foreign-through", "common-threaded", "own-through", "foreign-in-lane", "own-jog"]
    out.sort(key=lambda r: (order.index(r["tier"]), r["group"], r["from"]))
    return out


# ------------------------------------------------------------------------------------------------ summary
TIERS = ["foreign-through", "common-threaded", "own-through", "foreign-in-lane", "own-jog"]


def summarise(cross, pairx, overlaps, pair_ov, same_layer, trans):
    s = collections.OrderedDict()
    cat = collections.Counter(r["category"] for r in cross)
    pcat = collections.Counter(r["category"] for r in pairx)
    s["cmd_total"] = sum(v for k, v in cat.items() if k.startswith("cmd_"))
    for k in ("cmd_x_data", "cmd_x_cmd", "cmd_x_vref", "cmd_x_slow", "cmd_x_pwr", "cmd_x_gnd"):
        s[k] = cat.get(k, 0)
    s["data_x_data"] = cat.get("data_x_data", 0)
    s["hs_other_total"] = sum(v for k, v in cat.items() if k.startswith("hs_x_"))
    for k in ("hs_x_vref", "hs_x_slow", "hs_x_pwr", "hs_x_gnd"):
        s[k] = cat.get(k, 0)
    s["crossings_total"] = len(cross)
    s["crossings_without_solid_gnd_between"] = sum(1 for r in cross if not r["solid_gnd_between"])
    s["pair_cmd_x_data"] = pcat.get("cmd_x_data", 0)
    s["pair_cmd_total"] = sum(v for k, v in pcat.items() if k.startswith("cmd_"))
    s["pair_data_x_data"] = pcat.get("data_x_data", 0)
    s["pair_hs_other_total"] = sum(v for k, v in pcat.items() if k.startswith("hs_x_"))
    s["pair_crossings_total"] = len(pairx)
    ov = collections.Counter()
    for r in overlaps:
        ov[r["category"]] += r["length_mm"]
    s["overlap_mm_cmd"] = round(sum(v for k, v in ov.items() if k.startswith("cmd_")), 3)
    s["overlap_mm_cmd_x_data"] = round(ov.get("cmd_x_data", 0.0), 3)
    s["overlap_mm_data_x_data"] = round(ov.get("data_x_data", 0.0), 3)
    s["overlap_mm_hs_other"] = round(sum(v for k, v in ov.items() if k.startswith("hs_x_")), 3)
    s["overlap_mm_total"] = round(sum(ov.values()), 3)
    s["pair_overlap_mm_total"] = round(sum(r["length_mm"] for r in pair_ov), 3)
    s["same_layer"] = len(same_layer)
    tc = collections.Counter(r["tier"] for r in trans)
    s["trans_total"] = len(trans)
    s["trans_through"] = tc["foreign-through"] + tc["common-threaded"] + tc["own-through"]
    for t in TIERS:
        s["trans_" + t.replace("-", "_")] = tc[t]
    s["trans_foreign"] = tc["foreign-through"] + tc["foreign-in-lane"]
    s["trans_own"] = tc["common-threaded"] + tc["own-through"] + tc["own-jog"]
    return s


def matrix(cross):
    m = collections.Counter()
    for r in cross:
        m[(r["a"]["class"], r["a"]["layer"], r["b"]["class"], r["b"]["layer"])] += 1
    return m


# ------------------------------------------------------------------------------------------------ report
def _pl(r):
    return ", ".join("%s %s" % (p["layer"].replace(".Cu", ""), p["status"] + (" %.2f" % p["opening_mm"] if "opening_mm" in p else "")) for p in r["planes"]) or "none"


def write_md(path, res, args):
    s = res["summary"]
    L = []
    w = L.append
    w("# Crossings report: %s" % res["input"]["label"])
    w("")
    w("- board: `%s`" % res["input"]["board"])
    if res["input"]["plan"]:
        w("- plan: `%s` (%s); dropped from the board: stages %s, classes %s, sheets %s" %
          (res["input"]["plan"], res["input"].get("plan_name", ""), res["input"]["drop"]["stages"] or "-", res["input"]["drop"]["classes"] or "-",
           res["input"]["drop"]["sheets"] or "-"))
    w("- wire segments: %d (%s); vias %d; planes: %s" % (res["input"]["segments"], ", ".join("%s %d" % kv for kv in sorted(res["input"]["segments_by_class"].items())),
                                                        res["input"]["vias"], res["input"]["plane_source"]))
    w("- parameters: overlap within %.2f mm at < %.0f deg (reported from %.2f mm); transversal > %.0f deg off the column axis for > %.2f mm; "
      "plane disc %.2f mm; pair cluster %.1f mm" % (args.overlap_dist, args.overlap_angle, args.overlap_min, args.trans_angle, args.trans_len,
                                                    args.plane_disc, args.pair_cluster))
    for n in res["notes"]:
        w("- note: %s" % n)
    w("")
    w("## Summary")
    w("")
    w("| Measure | Wires | Pairs as one wire |")
    w("|---|---|---|")
    w("| **Crossings involving CMD** | **%d** | %d |" % (s["cmd_total"], s["pair_cmd_total"]))
    w("| - CMD x data (WR_DQ, RD_DQ, WCK, RDQS) | **%d** | %d |" % (s["cmd_x_data"], s["pair_cmd_x_data"]))
    w("| - CMD x CMD (other layer) / VREF / SLOW / PWR / GND | %d / %d / %d / %d / %d | |" % (s["cmd_x_cmd"], s["cmd_x_vref"], s["cmd_x_slow"], s["cmd_x_pwr"], s["cmd_x_gnd"]))
    w("| **Crossings among the data wires** (WR_DQ, RD_DQ, WCK, RDQS) | **%d** | %d |" % (s["data_x_data"], s["pair_data_x_data"]))
    w("| **Data wires x VREF / SLOW / PWR / GND** | **%d** (%d / %d / %d / %d) | %d |" % (s["hs_other_total"], s["hs_x_vref"], s["hs_x_slow"], s["hs_x_pwr"], s["hs_x_gnd"], s["pair_hs_other_total"]))
    w("| Crossings, total | %d | %d |" % (s["crossings_total"], s["pair_crossings_total"]))
    w("| - of these without solid GND on every plane between | %d | |" % s["crossings_without_solid_gnd_between"])
    w("| **Parallel overlap, mm** (CMD-involving / CMD x data / data x data / data x other) | **%.2f** (%.2f / %.2f / %.2f / %.2f) | %.2f |"
      % (s["overlap_mm_total"], s["overlap_mm_cmd"], s["overlap_mm_cmd_x_data"], s["overlap_mm_data_x_data"], s["overlap_mm_hs_other"], s["pair_overlap_mm_total"]))
    w("| **Same-layer crossings** (must be 0) | **%d** | |" % s["same_layer"])
    w("| **Resistor-group transversals** | **%d** | |" % s["trans_total"])
    w("| - through a group: foreign-through / common-threaded / own-through | **%d** (%d / %d / %d) | |"
      % (s["trans_through"], s["trans_foreign_through"], s["trans_common_threaded"], s["trans_own_through"]))
    w("| - foreign-in-lane / own-jog | %d / %d | |" % (s["trans_foreign_in_lane"], s["trans_own_jog"]))
    w("")
    w("## Crossings by class and layer")
    w("")
    w("| A | layer | B | layer | planes between | crossings | solid GND between |")
    w("|---|---|---|---|---|---|---|")
    mm_ = collections.defaultdict(list)
    for r in res["crossings"]:
        mm_[(r["a"]["class"], r["a"]["layer"], r["b"]["class"], r["b"]["layer"])].append(r)
    for k in sorted(mm_, key=lambda k: (CLASSES.index(k[0]), CLASSES.index(k[2]), k[1], k[3])):
        rr = mm_[k]
        w("| %s | %s | %s | %s | %s | %d | %d |" % (k[0], k[1], k[2], k[3], ", ".join(p["layer"].replace(".Cu", "") for p in rr[0]["planes"]) or "none",
                                                   len(rr), sum(1 for r in rr if r["solid_gnd_between"])))
    w("")
    w("## Resistor groups")
    w("")
    w("| Group | bbox x0, y0, x1, y1 | column axis | columns at | rows (along the axis) | faces | parts | source |")
    w("|---|---|---|---|---|---|---|---|")
    for g in res["groups"]:
        w("| %s | %.3f, %.3f, %.3f, %.3f | %s (%s) | %s | %s | %s | %d: %s | %s |"
          % (g["name"], g["bbox"][0], g["bbox"][1], g["bbox"][2], g["bbox"][3], g["column_axis"], g["axis_basis"],
             ", ".join("%.3f" % c for c in g["columns"]), "; ".join("%.2f..%.2f" % (r["from"], r["to"]) for r in g.get("rows", [])),
             " / ".join(g["sides"]), len(g["parts"]), " ".join(g["parts"]), g["source"]))
    for k in res["groups_skipped"]:
        w("")
        w("Not a finger group (ignored): %s %s, bbox %s: %s" % (k["func"], " ".join(k["refs"]), k["bbox"], k["why"]))
    w("")
    w("## Resistor-group transversals (%d)" % len(res["transversals"]))
    w("")
    w("| # | Group | Tier | Net | Class | Layer | From | To | Length mm | Off axis deg | Columns touched / crossed | Where | Crit. |")
    w("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(res["transversals"], 1):
        w("| %d | %s | %s | %s | %s | %s | (%.3f, %.3f) | (%.3f, %.3f) | %.3f | %.0f | %d / %d | %s | %s |"
          % (i, r["group"], r["tier"], r["net"], r["class"], r["layer"], r["from"][0], r["from"][1], r["to"][0], r["to"][1], r["length_mm"],
             r["off_axis_deg"], r["columns_touched"], r["columns_crossed"], r["where"], r["criteria"]))
    w("")

    def table(title, rows):
        w("## %s (%d)" % (title, len(rows)))
        w("")
        if not rows:
            w("none")
            w("")
            return
        w("| # | A | layer | B | layer | x | y | angle deg | planes between | solid GND |")
        w("|---|---|---|---|---|---|---|---|---|---|")
        for i, r in enumerate(rows, 1):
            w("| %d | %s (%s) | %s | %s (%s) | %s | %.3f | %.3f | %s | %s | %s |"
              % (i, r["a"]["net"], r["a"]["class"], r["a"]["layer"], r["b"]["net"], r["b"]["class"], r["b"]["layer"], r["x"], r["y"],
                 "%.0f" % r["angle_deg"] if "common_stretch_mm" not in r else "0: on top of each other for %.2f mm from %s to %s, leaves on the other side"
                 % (r["common_stretch_mm"], r["from"], r["to"]), _pl(r), "yes" if r["solid_gnd_between"] else "NO"))
        w("")
    cx = res["crossings"]
    table("Crossings of the command bundle by data wires (CMD x WR_DQ / RD_DQ / WCK / RDQS)", [r for r in cx if r["category"] == "cmd_x_data"])
    table("Crossings of the command bundle by VREF / SLOW / PWR wires", [r for r in cx if r["category"] in ("cmd_x_vref", "cmd_x_slow", "cmd_x_pwr")])
    table("Crossings of the command bundle by GND links", [r for r in cx if r["category"] == "cmd_x_gnd"])
    table("Command wires over command wires of another net (other layer)", [r for r in cx if r["category"] == "cmd_x_cmd"])
    table("Crossings among the data wires", [r for r in cx if r["category"] == "data_x_data"])
    table("Data wires x VREF / SLOW / PWR", [r for r in cx if r["category"] in ("hs_x_vref", "hs_x_slow", "hs_x_pwr")])
    table("Data wires x GND links", [r for r in cx if r["category"] == "hs_x_gnd"])
    w("## Parallel overlap through the board (%d, %.2f mm)" % (len(res["overlaps"]), s["overlap_mm_total"]))
    w("")
    if res["overlaps"]:
        w("| # | A | layer | B | layer | length mm | from | to | closest mm | planes between | solid GND |")
        w("|---|---|---|---|---|---|---|---|---|---|---|")
        for i, r in enumerate(res["overlaps"], 1):
            w("| %d | %s (%s) | %s | %s (%s) | %s | %.3f | (%.3f, %.3f) | (%.3f, %.3f) | %.3f | %s | %s |"
              % (i, r["a"]["net"], r["a"]["class"], r["a"]["layer"], r["b"]["net"], r["b"]["class"], r["b"]["layer"], r["length_mm"],
                 r["from"][0], r["from"][1], r["to"][0], r["to"][1], r["min_distance_mm"], _pl(r), "yes" if r["solid_gnd_between"] else "NO"))
    else:
        w("none")
    w("")
    w("## Same-layer crossings (%d)" % len(res["same_layer"]))
    w("")
    for r in res["same_layer"]:
        w("- %s x %s on %s at (%.3f, %.3f)" % (r["a"]["net"], r["b"]["net"], r["a"]["layer"], r["x"], r["y"]))
    if not res["same_layer"]:
        w("none")
    w("")
    w("## Contacts that are not crossings (%d, not counted)" % len(res["touches"]))
    w("")
    w("An end that touches a wire of another net in plan view, or a wire that joins another, runs on top of it and leaves on the side it came from "
      "(the common length is in the overlap table).")
    w("")
    for r in res["touches"]:
        w("- %s (%s) x %s (%s) at (%.3f, %.3f)%s" % (r["a"]["net"], r["a"]["layer"], r["b"]["net"], r["b"]["layer"], r["x"], r["y"],
                                                     ": on top of each other for %.2f mm, %s -> %s" % (r["common_stretch_mm"], r["from"], r["to"])
                                                     if "common_stretch_mm" in r else ""))
    if not res["touches"]:
        w("none")
    w("")
    w("## Net classes")
    w("")
    for k, v in res["net_classes"].items():
        w("- %s: %s" % (k, " ".join(v)))
    w("")
    with open(path, "w") as fh:
        fh.write("\n".join(L))


# ------------------------------------------------------------------------------------------------ picture
LAYER_COL = {"F.Cu": "#ff5555", "In3.Cu": "#4da8ff", "B.Cu": "#33d1cc"}
MARK = {    # category -> (marker, face colour, edge colour, size, label)
    "cmd_x_data": ("X", "#ff2bd6", "white", 150, "CMD x data wire"),
    "cmd_x_cmd": ("x", "#ff9de8", None, 40, "CMD x CMD (other layer)"),
    "cmd_x_other": ("P", "#ff9de8", "black", 60, "CMD x VREF / SLOW / PWR"),
    "cmd_x_gnd": (".", "#c7a9c0", None, 30, "CMD x GND link"),
    "data_x_data": ("o", "none", "#ffe600", 70, "data x data"),
    "hs_x_other": ("^", "#ff9a1f", "black", 70, "data x VREF / SLOW / PWR"),
    "hs_x_gnd": (".", "#9aa0a6", None, 30, "data x GND link"),
}
TIER_COL = {"foreign-through": "#ffffff", "common-threaded": "#ffe600", "own-through": "#ff9a1f", "foreign-in-lane": "#b9f27c", "own-jog": "#9a7cff"}


def _mcat(c):
    if c in ("cmd_x_vref", "cmd_x_slow", "cmd_x_pwr"):
        return "cmd_x_other"
    if c in ("hs_x_vref", "hs_x_slow", "hs_x_pwr"):
        return "hs_x_other"
    return c


def draw(path, sc, res, view=None, ppmm=None, title=""):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection, PatchCollection
    from matplotlib.patches import Circle, Rectangle
    if view is None:
        view = (-1.0, -0.5, 70.6, 30.5) if not sc.outline else (sc.outline.bounds[0] - 1, sc.outline.bounds[1] - 0.5, sc.outline.bounds[2] + 1, sc.outline.bounds[3] + 0.5)
    x0, y0, x1, y1 = view
    ppmm = ppmm or min(240.0, max(40.0, 4200.0 / (x1 - x0)))
    dpi = 100.0
    fw, fh = (x1 - x0) * ppmm / dpi, (y1 - y0) * ppmm / dpi + 1.3
    fig = plt.figure(figsize=(fw, fh), dpi=dpi, facecolor="#0e1116")
    ax = fig.add_axes([0, 0, 1, (fh - 1.3) / fh], facecolor="#0e1116")
    ax.set_xlim(x0, x1)
    ax.set_ylim(y0, y1)
    ax.set_aspect("equal")
    pt = 72.0 * ppmm / dpi            # points per mm
    inview = lambda bx: not (bx[2] < x0 or bx[0] > x1 or bx[3] < y0 or bx[1] > y1)      # noqa: E731
    if sc.outline is not None:
        ax.plot(*sc.outline.exterior.xy, color="#e8e84a", lw=1.0, zorder=1)
    step = 5 if (x1 - x0) > 30 else 1
    for gx in range(int(math.floor(x0)), int(math.ceil(x1)) + 1):
        if gx % step == 0:
            ax.axvline(gx, color="#ffffff", alpha=0.07, lw=0.6, zorder=0)
            ax.text(gx, y0 + 0.01 * (y1 - y0), str(gx), color="#9aa0a6", fontsize=8, ha="center", va="bottom", zorder=9)
    for gy in range(int(math.floor(y0)), int(math.ceil(y1)) + 1):
        if gy % step == 0:
            ax.axhline(gy, color="#ffffff", alpha=0.07, lw=0.6, zorder=0)
            ax.text(x0 + 0.003 * (x1 - x0), gy, str(gy), color="#9aa0a6", fontsize=8, ha="left", va="center", zorder=9)
    # pads and vias
    for lay, z in (("B.Cu", 2), ("F.Cu", 4)):
        ax.add_collection(PatchCollection([Rectangle((p["bbox"][0], p["bbox"][1]), p["bbox"][2] - p["bbox"][0], p["bbox"][3] - p["bbox"][1])
                                           for p in sc.pads if lay in p["layers"] and inview(p["bbox"])],
                                          facecolor=LAYER_COL[lay], edgecolor="none", alpha=0.28, zorder=z))
    ax.add_collection(PatchCollection([Circle((v["x"], v["y"]), v["d"] / 2) for v in sc.vias if inview((v["x"], v["y"], v["x"], v["y"]))],
                                      facecolor="#30343c", edgecolor="#8b9099", lw=0.5, zorder=5))
    # wires
    for lay, z in (("B.Cu", 3), ("In3.Cu", 4), ("F.Cu", 5)):
        for hs, alpha in ((False, 0.30), (True, 0.80)):
            ss = [s for s in sc.segs if s["layer"] == lay and (s["cls"] in HS) == hs
                  and inview((min(s["a"][0], s["b"][0]), min(s["a"][1], s["b"][1]), max(s["a"][0], s["b"][0]), max(s["a"][1], s["b"][1])))]
            if ss:
                ax.add_collection(LineCollection([[s["a"], s["b"]] for s in ss], colors=LAYER_COL[lay], alpha=alpha,
                                                 linewidths=[max(0.6, s["width"] * pt) for s in ss], capstyle="round", zorder=z + (1 if hs else 0)))
    # resistor groups
    for g in res["groups"]:
        bb = g["bbox"]
        if not inview(bb):
            continue
        ax.add_patch(Rectangle((bb[0], bb[1]), bb[2] - bb[0], bb[3] - bb[1], fill=False, edgecolor="white", lw=1.6, ls=(0, (5, 3)), zorder=12))
        ax.text(bb[0], bb[3] + 0.06, "%s  (axis %s)" % (g["name"], g["column_axis"]), color="white", fontsize=9, va="bottom", ha="left", zorder=13,
                bbox=dict(facecolor="#0e1116", edgecolor="none", alpha=0.7, pad=1))
        if ppmm >= 100:
            ci = 0 if g["column_axis"] == "y" else 1
            for c in g["columns"]:
                xy = ([c, c], [bb[1], bb[3]]) if ci == 0 else ([bb[0], bb[2]], [c, c])
                ax.plot(*xy, color="white", alpha=0.25, lw=0.7, ls=":", zorder=12)
    # transversals
    for r in res["transversals"]:
        col = TIER_COL[r["tier"]]
        strong = r["tier"] in ("foreign-through", "common-threaded", "own-through")
        for p, q in r["pieces"]:
            ax.plot([p[0], q[0]], [p[1], q[1]], color=col, lw=(3.5 if strong else 2.0), alpha=0.95, solid_capstyle="butt", zorder=14,
                    path_effects=None)
            ax.plot([p[0], q[0]], [p[1], q[1]], color="black", lw=(6.5 if strong else 4.0), alpha=0.6, solid_capstyle="butt", zorder=13.5)
    # overlaps
    for r in res["overlaps"]:
        for p, q in r["pieces"]:
            ax.plot([p[0], q[0]], [p[1], q[1]], color="#39ff14", lw=max(2.0, 0.07 * pt), alpha=0.9, solid_capstyle="butt", zorder=15)
    # crossings
    by = collections.defaultdict(list)
    for r in res["crossings"]:
        by[_mcat(r["category"])].append(r)
    scale = min(1.6, max(0.5, ppmm / 120.0))
    for c in ("hs_x_gnd", "cmd_x_gnd", "cmd_x_cmd", "data_x_data", "hs_x_other", "cmd_x_other", "cmd_x_data"):
        rr = [r for r in by.get(c, []) if x0 <= r["x"] <= x1 and y0 <= r["y"] <= y1]
        if not rr:
            continue
        m, fc, ec, sz, _ = MARK[c]
        kw = dict(marker=m, s=sz * scale, zorder=20, linewidths=1.4)
        if fc == "none":
            ax.scatter([r["x"] for r in rr], [r["y"] for r in rr], facecolors="none", edgecolors=ec, **kw)
        elif ec:
            ax.scatter([r["x"] for r in rr], [r["y"] for r in rr], c=fc, edgecolors=ec, **kw)
        else:
            ax.scatter([r["x"] for r in rr], [r["y"] for r in rr], c=fc, **kw)
        bad = [r for r in rr if not r["solid_gnd_between"]]
        if bad:
            ax.scatter([r["x"] for r in bad], [r["y"] for r in bad], marker="s", s=sz * scale * 3, facecolors="none", edgecolors="#ff3030", linewidths=1.6, zorder=21)
    for r in res["same_layer"]:
        ax.scatter([r["x"]], [r["y"]], marker="*", s=300, c="#ff3030", edgecolors="white", zorder=22)
    ax.set_xticks([])
    ax.set_yticks([])
    for sp in ax.spines.values():
        sp.set_visible(False)
    # header with legend
    s = res["summary"]
    hx = fig.add_axes([0, (fh - 1.3) / fh, 1, 1.3 / fh], facecolor="#05070a")
    hx.set_xlim(0, 1)
    hx.set_ylim(0, 1)
    hx.axis("off")
    hx.text(0.005, 0.80, "%s   x %.1f..%.1f  y %.1f..%.1f" % (title or res["input"]["label"], x0, x1, y0, y1), color="white", fontsize=12, fontweight="bold", va="center")
    hx.text(0.005, 0.50, "CMD x data %d | CMD x other %d | data x data %d | data x VREF/SLOW/PWR %d | x GND links %d | overlap %.2f mm | same layer %d | "
            "transversals %d (through %d, in-lane %d, own jog %d)"
            % (s["cmd_x_data"], s["cmd_total"] - s["cmd_x_data"] - s["cmd_x_gnd"], s["data_x_data"], s["hs_other_total"] - s["hs_x_gnd"],
               s["cmd_x_gnd"] + s["hs_x_gnd"], s["overlap_mm_total"], s["same_layer"], s["trans_total"], s["trans_through"], s["trans_foreign_in_lane"], s["trans_own_jog"]),
            color="#d0d4da", fontsize=9.5, va="center")
    xk = 0.005
    ch = 6.6 / (fw * dpi)             # width of one legend character as a fraction of the picture
    for lay in ("F.Cu", "In3.Cu", "B.Cu"):
        hx.plot([xk, xk + 0.012], [0.18, 0.18], color=LAYER_COL[lay], lw=4)
        hx.text(xk + 0.015, 0.18, lay, color="#d0d4da", fontsize=9, va="center")
        xk += 0.015 + ch * 6 + 0.012
    for c in ("cmd_x_data", "cmd_x_other", "cmd_x_cmd", "cmd_x_gnd", "data_x_data", "hs_x_other", "hs_x_gnd"):
        m, fc, ec, sz, lab = MARK[c]
        if fc == "none":
            hx.scatter([xk + 0.005], [0.18], marker=m, s=60, facecolors="none", edgecolors=ec, linewidths=1.4)
        else:
            hx.scatter([xk + 0.005], [0.18], marker=m, s=60, c=fc, edgecolors=ec if ec else None)
        hx.text(xk + 0.012, 0.18, lab, color="#d0d4da", fontsize=9, va="center")
        xk += 0.012 + ch * len(lab) + 0.012
    hx.plot([xk, xk + 0.012], [0.18, 0.18], color="#39ff14", lw=5)
    hx.text(xk + 0.015, 0.18, "parallel overlap", color="#d0d4da", fontsize=9, va="center")
    xk += 0.015 + ch * 16 + 0.012
    hx.text(xk, 0.18, "transversal:", color="#d0d4da", fontsize=9, va="center")
    xk += ch * 12 + 0.006
    for t in TIERS:
        hx.plot([xk, xk + 0.012], [0.18, 0.18], color=TIER_COL[t], lw=4)
        hx.text(xk + 0.015, 0.18, t, color="#d0d4da", fontsize=9, va="center")
        xk += 0.015 + ch * len(t) + 0.010
    hx.scatter([xk + 0.005], [0.18], marker="s", s=90, facecolors="none", edgecolors="#ff3030", linewidths=1.6)
    hx.text(xk + 0.014, 0.18, "no solid GND between", color="#d0d4da", fontsize=9, va="center")
    fig.savefig(path, dpi=dpi, facecolor=fig.get_facecolor())
    plt.close(fig)


# ------------------------------------------------------------------------------------------------ export, self-test
def export_plan(args, path):
    """Write the data copper of the board (stage s30_data) in the plan format: the baseline as a plan and a
    template for new ones.  Running it back through --plan must give the figures of the board."""
    spec = C.spec()
    cl = Classifier(spec)
    a2 = argparse.Namespace(**vars(args))
    a2.plan, a2.drop_stage, a2.drop_class, a2.drop_sheet, a2.keep_all = "", None, None, None, False
    sc = load_scene(a2, spec, cl)
    board = C.load_board(args.board)
    wires = collections.OrderedDict()
    for sg in sc.segs:
        if sg["stage"] in ("s30_data",):
            wires.setdefault((sg["wire"], sg["net"], sg["cls"], sg["layer"], sg["width"]), []).append(sg)
    out_w = []
    for (_, net, cls, lay, wd), ss in wires.items():
        pts = [list(ss[0]["a"])] + [list(q["b"]) for q in ss]
        out_w.append({"net": net, "class": "PWR" if cls == "GND" else cls, "layer": lay, "width": round(wd, 4), "points": pts})
    parts = []
    for fp in board.GetFootprints():
        ref = fp.GetReference()
        if cl.sheet.get(ref) in ("write", "read"):
            x, y = C.k2c(fp.GetPosition())
            parts.append({"ref": ref, "x": x, "y": y, "rot": round(fp.GetOrientationDegrees(), 3), "layer": "B.Cu" if fp.IsFlipped() else "F.Cu"})
    u1b = spec["floorplan"]["U1"]["body"]
    vias = [{"net": v["net"], "x": v["x"], "y": v["y"], "d": round(v["d"], 3), "drill": round(v["drill"], 3)} for v in sc.vias
            if cl.net_class(v["net"], "In3.Cu") in ("WR_DQ", "RD_DQ", "WCK", "RDQS", "VREF")
            and not (u1b["x"][0] <= v["x"] <= u1b["x"][1] and u1b["y"][0] <= v["y"] <= u1b["y"][1])]
    fing = {f["net"]: f["finger"] for f in spec["fingers"] if f.get("net")}
    pm = {"write": {n: fing.get(n + "_WF") for n in sorted(cl.dq)},
          "read": {n: {"p": fing.get(n), "n": fing.get("VREF_N" + n[2:])} for n in sorted(cl.dq)},
          "wck": {n: fing.get(n.replace("WCK_", "WCK_F_")) for n in ("WCK_P", "WCK_N")},
          "rdqs": {n: fing.get(n) for n in sorted(cl.rdqs)}}
    groups, _ = derive_groups(sc, cl, args)
    plan = {"name": "baseline: " + os.path.basename(args.board), "pin_map": pm, "parts": parts, "vias": vias, "wires": out_w,
            "groups": [{"name": g["name"], "kind": "resistor_group", "bbox": g["bbox"], "column_axis": g["column_axis"]}
                       for g in groups if g["func"] != "cmd_div"]}
    with open(path, "w") as fh:
        json.dump(plan, fh, indent=1)
    print("[crossings] plan of the board written: %s (%d wires, %d vias, %d parts)" % (path, len(out_w), len(vias), len(parts)))


def selftest():
    """Synthetic cases for the contact test and the clipping; raises on a wrong answer."""
    def wire(*pts):
        segs = [{"a": a, "b": b} for a, b in zip(pts, pts[1:])]

        def fn(pt):
            out = []
            for s_ in segs:
                (x1, y1), (x2, y2) = s_["a"], s_["b"]
                l = math.hypot(x2 - x1, y2 - y1)
                t = ((pt[0] - x1) * (x2 - x1) + (pt[1] - y1) * (y2 - y1)) / (l * l)
                if -1e-9 <= t <= 1 + 1e-9 and abs((pt[0] - x1) * (y2 - y1) - (pt[1] - y1) * (x2 - x1)) / l < 1e-9:
                    out += [d for d in arms_at(s_, min(1.0, max(0.0, t))) if not any(_same_dir(d, e) for e in out)]
            return out
        return fn
    h = wire((-2, 0), (2, 0))
    cases = [
        ("X crossing", [(0, 0)], h, wire((0, -1), (0, 1)), True),
        ("crossing at a vertex of both", [(0, 0)], wire((-1, -1), (0, 0), (1, -1)), wire((-1, 1), (0, 0), (0, -1)), True),
        ("end touches (T)", [(0, 0)], h, wire((0, 0), (0, 1)), False),
        ("V touches with its tip", [(0, 0)], h, wire((-1, 1), (0, 0), (1, 1)), False),
        ("on top, leaves on the same side", [(-1, 0), (1, 0)], h, wire((-2, 1), (-1, 0), (1, 0), (2, 1)), False),
        ("on top, leaves on the other side", [(-1, 0), (1, 0)], h, wire((-2, 1), (-1, 0), (1, 0), (2, -1)), True),
        ("on top to the end of A, B goes on", [(-1, 0), (2, 0)], h, wire((-2, 1), (-1, 0), (3, 0)), False),
        ("both bend away at the end, other side", [(-1, 0), (1, 0)], wire((-2, 0), (1, 0), (2, 1)), wire((-2, 1), (-1, 0), (1, 0), (2, 0)), True),
        ("both bend away at the end, same side", [(-1, 0), (1, 0)], wire((-2, 0), (1, 0), (2, -1)), wire((-2, 1), (-1, 0), (1, 0), (2, 0)), False),
        ("common stretch with a corner, same side", [(-1, 0), (0, 0), (0, 1)], wire((-2, 0), (0, 0), (0, 2)), wire((-2, 1), (-1, 0), (0, 0), (0, 1), (-1, 2)), False),
        ("common stretch with a corner, other side", [(-1, 0), (0, 0), (0, 1)], wire((-2, 0), (0, 0), (0, 2)), wire((-2, 1), (-1, 0), (0, 0), (0, 1), (1, 2)), True),
    ]
    bad = 0
    for name, path, fa, fb, want in cases:
        got = contact_is_crossing(path, fa, fb)
        got_r = contact_is_crossing(path[::-1], fa, fb)
        got_s = contact_is_crossing(path, fb, fa)
        ok = got == got_r == got_s == want
        bad += not ok
        print("[crossings] selftest %-45s %s (crossing: %s)" % (name, "ok" if ok else "WRONG", got))
    assert seg_hit((0, 0), (2, 0), (1, -1), (1, 1))[0] == (1.0, 0.0) and seg_hit((0, 0), (2, 0), (3, -1), (3, 1)) is None
    assert common_stretch((0, 0), (2, 0), (1, 0), (5, 0)) == ((1.0, 0.0), (2.0, 0.0)) and common_stretch((0, 0), (2, 0), (1, 0.1), (5, 0.1)) is None
    assert clip_seg((-1, 0.5), (3, 0.5), (0, 0, 1, 1)) == ((0.0, 0.5), (1.0, 0.5)) and clip_seg((-1, 2), (3, 2), (0, 0, 1, 1)) is None
    print("[crossings] selftest %s" % ("FAILED: %d case(s)" % bad if bad else "passed"))
    return 1 if bad else 0


# ------------------------------------------------------------------------------------------------ main
def analyse(args):
    spec = C.spec()
    cl = Classifier(spec)
    sc = load_scene(args, spec, cl)
    planes = PlaneTest(sc, args.plane_disc)
    cross, touches, overlaps, same_layer = find_crossings(sc, args, planes)
    groups, skipped = derive_groups(sc, cl, args)
    trans = find_transversals(sc, groups, args)
    pairx = pair_events(cross, args.pair_cluster)
    pair_ov = pair_events(overlaps, args.pair_cluster, "length_mm")
    seg_cls = collections.Counter(s["cls"] for s in sc.segs)
    res = collections.OrderedDict()
    res["input"] = {"label": getattr(sc, "plan_name", None) or os.path.basename(args.board), "board": os.path.abspath(args.board),
                    "plan": os.path.abspath(args.plan) if args.plan else None, "plan_name": getattr(sc, "plan_name", None),
                    "drop": sc.drop, "board_track_items_kept_by_stage": {str(k): v for k, v in sorted(sc.kept.items(), key=str)},
                    "segments": len(sc.segs), "segments_by_class": dict(seg_cls), "vias": len(sc.vias), "plane_source": sc.plane_src}
    res["parameters"] = {k: getattr(args, k) for k in ("overlap_dist", "overlap_angle", "overlap_min", "trans_angle", "trans_len", "plane_disc",
                                                       "pair_cluster", "group_link", "group_near", "group_margin")}
    res["notes"] = sc.notes
    res["summary"] = summarise(cross, pairx, overlaps, pair_ov, same_layer, trans)
    res["matrix"] = [{"a_class": k[0], "a_layer": k[1], "b_class": k[2], "b_layer": k[3], "crossings": v} for k, v in sorted(matrix(cross).items())]
    res["groups"] = groups
    res["groups_skipped"] = skipped
    res["transversals"] = trans
    res["crossings"] = cross
    res["pair_crossings"] = pairx
    res["overlaps"] = overlaps
    res["pair_overlaps"] = pair_ov
    res["same_layer"] = same_layer
    res["touches"] = touches
    res["net_classes"] = cl.table()
    return sc, res


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog="Full description: the docstring of this file.")
    ap.add_argument("--board", default=C.BOARD)
    ap.add_argument("--plan", default="", help="plan JSON laid over the board")
    ap.add_argument("--drop-stage", action="append", help="drop the board copper of a stage (s30_data, ...); repeatable")
    ap.add_argument("--drop-class", action="append", help="drop the board copper of a net class (WR_DQ, RD_DQ, WCK, RDQS, VREF, ...); repeatable")
    ap.add_argument("--drop-sheet", action="append", help="drop the board parts of a schematic sheet (write, read) unless the plan places them")
    ap.add_argument("--keep-all", action="store_true", help="with --plan: keep every stage and part of the board (the plan only adds)")
    ap.add_argument("--all-pairs", action="store_true", help="also report crossings without a high-speed wire")
    ap.add_argument("--overlap-dist", type=float, default=0.2)
    ap.add_argument("--overlap-angle", type=float, default=15.0)
    ap.add_argument("--overlap-min", type=float, default=0.05, help="shortest overlap that is reported, mm")
    ap.add_argument("--trans-angle", type=float, default=30.0)
    ap.add_argument("--trans-len", type=float, default=0.3)
    ap.add_argument("--plane-disc", type=float, default=0.19, help="radius of the plane test at a crossing (half track width + h, as gate 8)")
    ap.add_argument("--pair-cluster", type=float, default=1.0)
    ap.add_argument("--group-link", type=float, default=1.25, help="largest land-to-land distance inside a resistor group, mm")
    ap.add_argument("--group-near", type=float, default=8.0, help="a resistor group must reach down to this y, mm")
    ap.add_argument("--group-margin", type=float, default=0.0, help="grow the derived group boxes by this much (0: the lands of the parts; "
                    "about 0.6 takes in the GND via row of a divider field)")
    ap.add_argument("--json", default="")
    ap.add_argument("--md", default="")
    ap.add_argument("--png", default="")
    ap.add_argument("--crop", action="append", default=[], help="extra picture: name:x0,y0,x1,y1[:px_per_mm], written next to --png as <png>_<name>.png")
    ap.add_argument("--max", action="append", default=[], help="gate: NAME=N, fail if summary[NAME] > N; repeatable")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--export-plan", default="", help="write the data copper of the board (s30_data) as a plan JSON and stop")
    ap.add_argument("--selftest", action="store_true", help="run the synthetic cases of the contact test and stop")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if args.export_plan:
        export_plan(args, args.export_plan)
        return 0
    sc, res = analyse(args)
    s = res["summary"]
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(res, fh, indent=1)
    if args.md:
        write_md(args.md, res, args)
    if args.png:
        draw(args.png, sc, res)
        for c in args.crop:
            parts = c.split(":")
            v = tuple(float(q) for q in parts[1].split(","))
            draw(os.path.splitext(args.png)[0] + "_" + parts[0] + ".png", sc, res, v, float(parts[2]) if len(parts) > 2 else 200.0,
                 "%s: %s" % (res["input"]["label"], parts[0]))
    if not args.quiet:
        print("[crossings] %s%s: %d wire segments" % (res["input"]["label"], " over " + os.path.basename(args.board) if args.plan else "", len(sc.segs)))
        for n in res["notes"]:
            print("[crossings] note: %s" % n)
        print("[crossings] resistor groups: " + "; ".join("%s x %.2f..%.2f y %.2f..%.2f axis %s (%d parts, %d columns)"
              % (g["name"], g["bbox"][0], g["bbox"][2], g["bbox"][1], g["bbox"][3], g["column_axis"], len(g["parts"]), len(g["columns"])) for g in res["groups"]))
        print("[crossings] by class and layer:")
        for m in sorted(res["matrix"], key=lambda m: (CLASSES.index(m["a_class"]), CLASSES.index(m["b_class"]), m["a_layer"], m["b_layer"])):
            print("[crossings]     %-6s %-6s x %-6s %-6s %4d" % (m["a_class"], m["a_layer"], m["b_class"], m["b_layer"], m["crossings"]))
        print("[crossings] SUMMARY  CMD: %d (x data %d, x CMD other layer %d, x VREF %d, x SLOW %d, x PWR %d, x GND links %d) | data x data: %d | "
              "data x VREF / SLOW / PWR / GND: %d / %d / %d / %d | pairs as one wire: CMD x data %d, data x data %d"
              % (s["cmd_total"], s["cmd_x_data"], s["cmd_x_cmd"], s["cmd_x_vref"], s["cmd_x_slow"], s["cmd_x_pwr"], s["cmd_x_gnd"], s["data_x_data"],
                 s["hs_x_vref"], s["hs_x_slow"], s["hs_x_pwr"], s["hs_x_gnd"], s["pair_cmd_x_data"], s["pair_data_x_data"]))
        print("[crossings] SUMMARY  overlap %.2f mm (CMD x data %.2f, data x data %.2f, data x other %.2f) | same layer %d | crossings without solid "
              "GND between %d | transversals %d: foreign-through %d, common-threaded %d, own-through %d, foreign-in-lane %d, own-jog %d"
              % (s["overlap_mm_total"], s["overlap_mm_cmd_x_data"], s["overlap_mm_data_x_data"], s["overlap_mm_hs_other"], s["same_layer"],
                 s["crossings_without_solid_gnd_between"], s["trans_total"], s["trans_foreign_through"], s["trans_common_threaded"],
                 s["trans_own_through"], s["trans_foreign_in_lane"], s["trans_own_jog"]))
    fail = []
    if s["same_layer"]:
        fail.append("same_layer = %d" % s["same_layer"])
    for m in args.max:
        k, v = m.split("=")
        if k not in s:
            raise SystemExit("--max: unknown summary figure %r (known: %s)" % (k, ", ".join(s)))
        if s[k] > float(v):
            fail.append("%s = %s > %s" % (k, s[k], v))
    if fail:
        print("[crossings] FAIL: " + "; ".join(fail))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Shared helpers of the PCB build pipeline (KiCad 10, pcbnew Python API).

Everything the stages put on the board comes from design/netlist.json (written by scripts/build_netlist.py).
A stage never edits the board by hand-typed coordinates that are not in that file.

Coordinates
    Card frame (used everywhere in the spec): mm, origin = lower-left corner of the card seen from the front,
    x from the pin-1 end, y up from the finger edge; the back side uses the same x.
    KiCad: kicad = (ox + x, oy - y) with (ox, oy) = floorplan.board_origin_kicad_mm.

Stage tags
    Every item a stage creates gets a deterministic UUID  <stage code>-<kind>-4000-8000-<counter>.
    The first eight hex digits identify the stage (STAGES below), so a stage can be ripped up and re-run
    (rip_up) and two builds of the same spec give the same file.  No groups are used: nothing changes for
    interactive editing.

Delay
    net_report() walks the copper of a net and returns length and delay per layer.  ps/mm per layer comes from
    rules.delay_ps_per_mm; a via counts with the barrel length between the outermost layers on which the net
    has copper at that via (stackup thicknesses), at the via figure of the spec.
"""
import json
import math
import os
import sys

import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SPEC = os.path.join(ROOT, "design", "netlist.json")
PROJECT = "lpddr5-testbed"
BOARD = os.path.join(ROOT, PROJECT + ".kicad_pcb")
PRO = os.path.join(ROOT, PROJECT + ".kicad_pro")
DRU = os.path.join(ROOT, PROJECT + ".kicad_dru")
KICAD_FP = "/usr/share/kicad/footprints"
PROJECT_LIB = "lpddr5_testbed"
PROJECT_LIB_DIR = os.path.join(ROOT, "lib", PROJECT_LIB + ".pretty")

sys.path.insert(0, os.path.join(ROOT, "scripts"))
import gen_footprints as edge      # noqa: E402  card-frame helpers of the edge footprint (finger_xy, component_area, ...)
import tuning                      # noqa: E402,F401  tuning geometry and the electrical length of bends (rules.bend_model)

# stage name -> UUID prefix.  Later stages (routing) are reserved here so that the tags stay unique.
STAGES = {
    "s00_board": 0x00, "s10_escape": 0x10, "s20_cmd": 0x20, "s30_data": 0x30,
    "s50_power": 0x50, "s90_zones": 0x90,
    "rehearse": 0xE0,       # planned centre lines drawn on a copy of the board by rehearse.py, never on the project board
}
KIND = {"footprint": 1, "track": 2, "via": 3, "zone": 4, "rule_area": 5, "text": 6, "fpitem": 7, "net": 8}

CU = {"F.Cu": pcbnew.F_Cu, "In1.Cu": pcbnew.In1_Cu, "In2.Cu": pcbnew.In2_Cu, "In3.Cu": pcbnew.In3_Cu,
      "In4.Cu": pcbnew.In4_Cu, "B.Cu": pcbnew.B_Cu}
CU_ORDER = ["F.Cu", "In1.Cu", "In2.Cu", "In3.Cu", "In4.Cu", "B.Cu"]
MM = pcbnew.ToMM


def IU(mm):
    """mm -> KiCad internal units (nm), ROUNDED.  pcbnew.FromMM truncates: 30.7 mm became 30699999 nm, which put pads,
    vias and track ends 1 nm off the 1 um grid and made diagonals 1 nm off 45 degrees."""
    return int(round(mm * 1e6))


# ------------------------------------------------------------------------------------------------ spec
_spec = None


def spec():
    global _spec
    if _spec is None:
        with open(SPEC) as fh:
            _spec = json.load(fh)
    return _spec


def origin():
    return tuple(spec()["floorplan"]["board_origin_kicad_mm"])


def c2k(x, y):
    """Card frame (mm) -> KiCad VECTOR2I."""
    ox, oy = origin()
    return pcbnew.VECTOR2I(IU(round(ox + x, 6)), IU(round(oy - y, 6)))


def k2c(v):
    """KiCad VECTOR2I -> card frame (mm), rounded to 1 nm."""
    ox, oy = origin()
    return (round(MM(v.x) - ox, 6), round(oy - MM(v.y), 6))


def parts():
    return {p["ref"]: p for p in spec()["parts"]}


def nets():
    out = set()
    for p in spec()["parts"]:
        out.update(n for n in p["pins"].values() if n)
    return sorted(out)


def netclass_of(net):
    for name, nc in spec()["rules"]["netclasses"].items():
        if net in nc["nets"]:
            return name
    return "Default"


def u1_ball_xy(ball):
    """Centre of a U1 ball, card frame (same formula as floorplan.U1.ball_xy)."""
    u = spec()["floorplan"]["U1"]
    row = ball.rstrip("0123456789")
    col = int(ball[len(row):])
    r = edge.BGA_ROWS.index(row)
    return (round(u["centre"][0] - (col - 8) * 0.8, 3), round(u["centre"][1] + (r - 10) * 0.7, 3))


def finger_xy(n):
    """Centre of finger pad n, card frame."""
    return edge.finger_xy(n)


def finger_top(n):
    """Top end of finger n (where a trace leaves it), card frame."""
    return edge.finger_top_xy(n)


def finger_layer(n):
    return edge.finger_layer(n)


def stackup_z():
    """z of the middle of every copper layer below the top of F.Cu, mm."""
    z, out = 0.0, {}
    for lay in spec()["stackup"]["layers"]:
        if lay["type"] == "soldermask":
            continue
        if lay["type"] == "copper":
            out[lay["layer"]] = z + lay["thickness_mm"] / 2
        z += lay["thickness_mm"]
    return out


def delay_ps_per_mm(layer, width, net=None):
    d = spec()["rules"]["delay_ps_per_mm"]
    if layer == "In3.Cu":
        return d["In3.Cu"]
    if layer in ("F.Cu", "B.Cu"):
        if net is not None and netclass_of(net) in ("WR_WCK", "RD_RDQS") and width <= 0.105:      # the edge-coupled pair: WCK on B.Cu, RDQS on F.Cu
            return d["F.Cu/B.Cu pair 0.10/0.10 (odd mode)"]
        table = [(0.09, d["F.Cu/B.Cu w0.09"]), (0.15, d["F.Cu/B.Cu w0.15"]), (0.20, d["F.Cu/B.Cu w0.20"])]
        if width <= table[0][0]:
            return table[0][1]
        if width >= table[-1][0]:
            return table[-1][1]
        for (w0, v0), (w1, v1) in zip(table, table[1:]):
            if w0 <= width <= w1:
                return v0 + (v1 - v0) * (width - w0) / (w1 - w0)
    return d["In3.Cu"]          # planes are not routed; any other inner layer is treated as stripline


# ------------------------------------------------------------------------------------------------ board I/O
def load_board(path=BOARD):
    return pcbnew.LoadBoard(path)


def save_board(board, path=BOARD):
    # aSkipSettings=True: the .kicad_pro is written by s00_board.py, never by pcbnew
    pcbnew.SaveBoard(path, board, True)


def layer_name(board, lid):
    return board.GetLayerName(lid)


# ------------------------------------------------------------------------------------------------ stage tags
class Stage:
    """Hands out deterministic UUIDs for one stage and knows how to remove what the stage made."""

    def __init__(self, board, name):
        assert name in STAGES, name
        self.board, self.name, self.code = board, name, STAGES[name]
        self.count = {}

    def uuid(self, kind):
        n = self.count.get(kind, 0) + 1
        self.count[kind] = n
        return pcbnew.KIID("%08x-%04x-4000-8000-%012x" % (self.code, KIND[kind], n))

    def tag(self, item, kind):
        item.SetUuid(self.uuid(kind))
        return item


def stage_of(item):
    """Stage name of a board item, or None for items that no stage made."""
    s = item.m_Uuid.AsString()
    if s[8:9] != "-" or s[14:19] != "4000-" or s[19:24] != "8000-":
        return None
    try:
        code = int(s[:8], 16)
    except ValueError:
        return None
    for name, c in STAGES.items():
        if c == code:
            return name
    return None


def stage_items(board, name):
    out = [t for t in board.GetTracks() if stage_of(t) == name]
    out += [z for z in board.Zones() if stage_of(z) == name]
    out += [f for f in board.GetFootprints() if stage_of(f) == name]
    out += [d for d in board.GetDrawings() if stage_of(d) == name]
    return out


def rip_up(board, name):
    """Remove every track, via, zone, rule area, drawing and footprint the stage created.  Returns the count."""
    items = stage_items(board, name)
    for it in items:
        board.Remove(it)
    return len(items)


# ------------------------------------------------------------------------------------------------ nets
def net(board, name):
    ni = board.FindNet(name)
    if ni is None:
        raise KeyError("net %r is not on the board (s00 creates every net of the spec)" % name)
    return ni


# ------------------------------------------------------------------------------------------------ pads
def footprint(board, ref):
    fp = board.FindFootprintByReference(ref)
    if fp is None:
        raise KeyError("no footprint %s" % ref)
    return fp


def pad(board, refpad):
    """'R101.1', 'U1.H9', 'J1.66' -> PAD (the copper pad with that number closest to the footprint origin)."""
    ref, num = refpad.split(".", 1)
    fp = footprint(board, ref)
    best = None
    for p in fp.Pads():
        if p.GetNumber() == num and p.IsOnCopperLayer():
            d = (p.GetPosition() - fp.GetPosition()).EuclideanNorm()
            if best is None or d < best[0]:
                best = (d, p)
    if best is None:
        raise KeyError("no pad %s" % refpad)
    return best[1]


def pad_xy(board, refpad):
    """Pad centre in the card frame."""
    return k2c(pad(board, refpad).GetPosition())


def ball(board, name):
    return pad_xy(board, "U1." + name)


def finger(board, n):
    return pad_xy(board, "J1.%d" % n)


# ------------------------------------------------------------------------------------------------ geometry
def _dir_ok(a, b, tol=2.5e-6):
    dx, dy = abs(b[0] - a[0]), abs(b[1] - a[1])
    return dx < tol or dy < tol or abs(dx - dy) < tol


def check_polyline(pts, what=""):
    """0 / 45 / 90 / 135 degree segments only, no zero-length segment, no bend sharper than 90 degrees
    (the interior angle between consecutive segments is 90 or 135 degrees; 135 is the normal mitre)."""
    for a, b in zip(pts, pts[1:]):
        if math.hypot(b[0] - a[0], b[1] - a[1]) < 1e-9:
            raise ValueError("%s: zero-length segment at %s" % (what, a))
        if not _dir_ok(a, b):
            raise ValueError("%s: segment %s -> %s is not at a multiple of 45 degrees" % (what, a, b))
    for a, b, c in zip(pts, pts[1:], pts[2:]):
        u = (b[0] - a[0], b[1] - a[1])
        v = (c[0] - b[0], c[1] - b[1])
        cosang = (u[0] * v[0] + u[1] * v[1]) / (math.hypot(*u) * math.hypot(*v))
        if cosang < -1e-6:            # direction change of more than 90 degrees = acute corner
            raise ValueError("%s: acute bend at %s" % (what, b))


def polyline_length(pts):
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:]))


def add_segment(board, stage, net_name, layer, a, b, width):
    t = pcbnew.PCB_TRACK(board)
    t.SetStart(c2k(*a))
    t.SetEnd(c2k(*b))
    t.SetWidth(IU(width))
    t.SetLayer(CU[layer])
    t.SetNet(net(board, net_name))
    stage.tag(t, "track")
    board.Add(t)
    return t


def add_arc(board, stage, net_name, layer, a, mid, b, width):
    t = pcbnew.PCB_ARC(board)
    t.SetStart(c2k(*a))
    t.SetMid(c2k(*mid))
    t.SetEnd(c2k(*b))
    t.SetWidth(IU(width))
    t.SetLayer(CU[layer])
    t.SetNet(net(board, net_name))
    stage.tag(t, "track")
    board.Add(t)
    return t


def add_track(board, stage, net_name, layer, pts, width, radius=None, allow_any_angle=False):
    """Polyline of 0/45/90/135-degree segments in the card frame.

    radius=None  : plain mitred corners (the normal case: every bend is a 135-degree corner, or 90 degrees
                   where the caller asks for it explicitly with two segments).
    radius=r     : every corner is replaced by a tangent arc of radius r (used by tuning meanders, as upstream);
                   r is reduced where a segment is too short.
    Returns the list of created items.
    """
    pts = [(round(p[0], 6), round(p[1], 6)) for p in pts]
    if not allow_any_angle:
        check_polyline(pts, "%s on %s" % (net_name, layer))
    items = []
    if not radius:
        for a, b in zip(pts, pts[1:]):
            items.append(add_segment(board, stage, net_name, layer, a, b, width))
        return items
    cur = pts[0]
    for i in range(1, len(pts) - 1):
        p0, p1, p2 = pts[i - 1], pts[i], pts[i + 1]
        u = (p1[0] - p0[0], p1[1] - p0[1])
        v = (p2[0] - p1[0], p2[1] - p1[1])
        lu, lv = math.hypot(*u), math.hypot(*v)
        u, v = (u[0] / lu, u[1] / lu), (v[0] / lv, v[1] / lv)
        turn = math.acos(max(-1.0, min(1.0, u[0] * v[0] + u[1] * v[1])))
        if turn < 1e-9:
            continue
        t = radius * math.tan(turn / 2)                  # tangent length
        t = min(t, lu / 2, lv / 2)
        r = t / math.tan(turn / 2)
        a = (p1[0] - u[0] * t, p1[1] - u[1] * t)
        b = (p1[0] + v[0] * t, p1[1] + v[1] * t)
        bis = (v[0] - u[0], v[1] - u[1])
        lb = math.hypot(*bis)
        centre_d = r / math.cos(turn / 2)
        c = (p1[0] + bis[0] / lb * centre_d, p1[1] + bis[1] / lb * centre_d)
        mid = (c[0] - bis[0] / lb * r, c[1] - bis[1] / lb * r)
        if math.hypot(a[0] - cur[0], a[1] - cur[1]) > 1e-9:
            items.append(add_segment(board, stage, net_name, layer, cur, a, width))
        items.append(add_arc(board, stage, net_name, layer, a, mid, b, width))
        cur = b
    if math.hypot(pts[-1][0] - cur[0], pts[-1][1] - cur[1]) > 1e-9:
        items.append(add_segment(board, stage, net_name, layer, cur, pts[-1], width))
    return items


def add_path(board, stage, net_name, layer, verts, width):
    """Centre line given as [(x, y, corner radius or None)] (scripts/tuning.py): a vertex with a radius becomes a
    tangent arc of exactly that radius (ValueError if it does not fit: no silent reduction), a vertex without
    one stays a mitre.  Straight parts must be at 0 / 45 / 90 / 135 degrees.  Returns the created items."""
    verts = [(round(v[0], 6), round(v[1], 6), v[2]) for v in verts]
    check_polyline([v[:2] for v in verts], "%s on %s" % (net_name, layer))
    items = []
    cur = verts[0][:2]
    t_prev = 0.0
    for i in range(1, len(verts) - 1):
        p0, p1, p2 = verts[i - 1], verts[i], verts[i + 1]
        r = p1[2]
        u = (p1[0] - p0[0], p1[1] - p0[1])
        v = (p2[0] - p1[0], p2[1] - p1[1])
        lu, lv = math.hypot(*u), math.hypot(*v)
        u, v = (u[0] / lu, u[1] / lu), (v[0] / lv, v[1] / lv)
        turn = math.acos(max(-1.0, min(1.0, u[0] * v[0] + u[1] * v[1])))
        if turn < 1e-9:
            continue
        if not r:
            items.append(add_segment(board, stage, net_name, layer, cur, p1[:2], width))
            cur, t_prev = p1[:2], 0.0
            continue
        t = r * math.tan(turn / 2)
        if t_prev + t > lu + 1e-9:
            raise ValueError("%s: corner radius %.3f does not fit the %.4f mm segment before %s" % (net_name, r, lu, p1[:2]))
        a = (p1[0] - u[0] * t, p1[1] - u[1] * t)
        b = (p1[0] + v[0] * t, p1[1] + v[1] * t)
        bis = (v[0] - u[0], v[1] - u[1])
        lb = math.hypot(*bis)
        centre_d = r / math.cos(turn / 2)
        c = (p1[0] + bis[0] / lb * centre_d, p1[1] + bis[1] / lb * centre_d)
        mid = (c[0] - bis[0] / lb * r, c[1] - bis[1] / lb * r)
        if math.hypot(a[0] - cur[0], a[1] - cur[1]) > 1e-6:
            items.append(add_segment(board, stage, net_name, layer, cur, a, width))
        items.append(add_arc(board, stage, net_name, layer, a, mid, b, width))
        cur, t_prev = b, t
    last = verts[-1][:2]
    if t_prev > math.hypot(last[0] - verts[-2][0], last[1] - verts[-2][1]) + 1e-9:
        raise ValueError("%s: the last corner radius does not fit" % net_name)
    if math.hypot(last[0] - cur[0], last[1] - cur[1]) > 1e-6:
        items.append(add_segment(board, stage, net_name, layer, cur, last, width))
    return items


def rounded_length(pts, radius=None):
    """Centre-line length of the polyline as add_track() draws it (corners replaced by tangent arcs of the given
    radius, reduced where a segment is too short).  radius=None: plain polyline."""
    pts = [(round(p[0], 6), round(p[1], 6)) for p in pts]
    if not radius:
        return polyline_length(pts)
    total = polyline_length(pts)
    for i in range(1, len(pts) - 1):
        p0, p1, p2 = pts[i - 1], pts[i], pts[i + 1]
        u = (p1[0] - p0[0], p1[1] - p0[1])
        v = (p2[0] - p1[0], p2[1] - p1[1])
        lu, lv = math.hypot(*u), math.hypot(*v)
        turn = math.acos(max(-1.0, min(1.0, (u[0] * v[0] + u[1] * v[1]) / (lu * lv))))
        if turn < 1e-9:
            continue
        t = min(radius * math.tan(turn / 2), lu / 2, lv / 2)
        r = t / math.tan(turn / 2)
        total += r * turn - 2 * t
    return total


def add_via(board, stage, net_name, x, y, diameter, drill, in_pad=False, remove_unused_pads=False, inner_diameter=None):
    """Through via F.Cu - B.Cu.  in_pad=True marks a via-in-pad: filled and capped, not tented (it sits in a
    solder pad).  Other vias follow the board setting (tented both sides).
    remove_unused_pads=True: no pad on an inner layer where the via connects to nothing (F.Cu and B.Cu keep theirs);
    copper on that layer then keeps the hole clearance of the board from the barrel instead of the pad clearance."""
    v = pcbnew.PCB_VIA(board)
    v.SetViaType(pcbnew.VIATYPE_THROUGH)
    v.SetPosition(c2k(x, y))
    v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
    v.SetWidth(pcbnew.F_Cu, IU(diameter))
    v.SetDrill(IU(drill))
    v.SetNet(net(board, net_name))
    if in_pad:
        v.SetFillingMode(pcbnew.FILLING_MODE_FILLED)
        v.SetCappingMode(pcbnew.CAPPING_MODE_CAPPED)
    if remove_unused_pads:
        v.SetRemoveUnconnected(True)
        v.SetKeepStartEnd(True)
    if inner_diameter:              # a smaller pad on the inner layers (padstack front / inner / back); F.Cu and B.Cu keep 'diameter'
        v.Padstack().SetMode(pcbnew.PADSTACK.MODE_FRONT_INNER_BACK)
        v.SetWidth(pcbnew.F_Cu, IU(diameter))
        v.SetWidth(pcbnew.In3_Cu, IU(inner_diameter))
        v.SetWidth(pcbnew.B_Cu, IU(diameter))
    stage.tag(v, "via")
    board.Add(v)
    return v


def _outline(zone, pts):
    ol = zone.Outline()
    ol.NewOutline()
    for x, y in pts:
        ol.Append(c2k(x, y))


def add_zone(board, stage, net_name, layers, pts, name="", clearance=0.10, min_thickness=0.10, priority=0,
             connection=pcbnew.ZONE_CONNECTION_FULL, islands=pcbnew.ISLAND_REMOVAL_MODE_ALWAYS, holes=()):
    """Copper zone with the outline pts (card frame) on one or more layers."""
    z = pcbnew.ZONE(board)
    ls = pcbnew.LSET()
    for l in ([layers] if isinstance(layers, str) else layers):
        ls.AddLayer(CU[l])
    z.SetLayerSet(ls)
    z.SetNet(net(board, net_name))
    z.SetZoneName(name)
    z.SetLocalClearance(IU(clearance))
    z.SetMinThickness(IU(min_thickness))
    z.SetAssignedPriority(priority)
    z.SetPadConnection(connection)
    z.SetIslandRemovalMode(islands)
    z.SetThermalReliefGap(IU(0.25))
    z.SetThermalReliefSpokeWidth(IU(0.25))
    z.SetBorderDisplayStyle(pcbnew.ZONE_BORDER_DISPLAY_STYLE_DIAGONAL_EDGE, IU(0.5), True)
    _outline(z, pts)
    for h in holes:
        hi = z.Outline().NewHole(0)
        for x, y in h:
            z.Outline().Append(c2k(x, y), 0, hi)
    stage.tag(z, "zone")
    board.Add(z)
    return z


def add_rule_area(board, stage, layers, pts, name, no_tracks=False, no_vias=False, no_pads=False, no_pour=False,
                  no_footprints=False):
    z = pcbnew.ZONE(board)
    z.SetIsRuleArea(True)
    ls = pcbnew.LSET()
    for l in ([layers] if isinstance(layers, str) else layers):
        ls.AddLayer(CU[l])
    z.SetLayerSet(ls)
    z.SetZoneName(name)
    z.SetDoNotAllowTracks(no_tracks)
    z.SetDoNotAllowVias(no_vias)
    z.SetDoNotAllowPads(no_pads)
    z.SetDoNotAllowZoneFills(no_pour)
    z.SetDoNotAllowFootprints(no_footprints)
    z.SetBorderDisplayStyle(pcbnew.ZONE_BORDER_DISPLAY_STYLE_DIAGONAL_EDGE, IU(0.5), True)
    _outline(z, pts)
    stage.tag(z, "rule_area")
    board.Add(z)
    return z


def fill_zones(board):
    filler = pcbnew.ZONE_FILLER(board)
    zones = board.Zones()
    return filler.Fill(zones)


# ------------------------------------------------------------------------------------------------ outline
def board_polygon(board):
    """Board outline as a shapely polygon in the card frame (holes = cut-outs inside the outline)."""
    from shapely.geometry import Polygon
    ps = pcbnew.SHAPE_POLY_SET()
    if not board.GetBoardPolygonOutlines(ps, True):
        raise RuntimeError("Edge.Cuts does not form a closed outline")
    polys = []
    for i in range(ps.OutlineCount()):
        ol = ps.Outline(i)
        ext = [k2c(ol.CPoint(k)) for k in range(ol.PointCount())]
        holes = []
        for h in range(ps.HoleCount(i)):
            hl = ps.Hole(i, h)
            holes.append([k2c(hl.CPoint(k)) for k in range(hl.PointCount())])
        polys.append(Polygon(ext, holes))
    return polys


# ------------------------------------------------------------------------------------------------ length / delay
def _track_len(t):
    if t.Type() == pcbnew.PCB_ARC_T:
        return MM(t.GetLength())
    return MM((t.GetEnd() - t.GetStart()).EuclideanNorm())


def net_report(board, net_name):
    """Length and delay of the copper of one net.

    Returns {"layers": {layer: {"mm": .., "ps": ..}}, "vias": [{"xy":.., "span_mm":.., "ps":..}],
             "mm": total copper + used barrels, "ps": total delay}.
    A via contributes the barrel between the outermost layers on which the net has a track end or a pad at the
    via position; stubs beyond those layers are not counted.
    """
    code = board.GetNetcodeFromNetname(net_name)
    z = stackup_z()
    d_via = spec()["rules"]["delay_ps_per_mm"]["through via (per mm of barrel)"]
    layers, vias, ends = {}, [], []
    for t in board.GetTracks():
        if t.GetNetCode() != code:
            continue
        if t.Type() == pcbnew.PCB_VIA_T:
            vias.append(t)
            continue
        lname = board.GetLayerName(t.GetLayer())
        ln = _track_len(t)
        w = MM(t.GetWidth())
        e = layers.setdefault(lname, {"mm": 0.0, "ps": 0.0})
        e["mm"] += ln
        e["ps"] += ln * delay_ps_per_mm(lname, w, net_name)
        ends.append((t.GetStart(), lname))
        ends.append((t.GetEnd(), lname))
    for fp in board.GetFootprints():
        for p in fp.Pads():
            if p.GetNetCode() == code:
                for lname, lid in CU.items():
                    if p.IsOnLayer(lid):
                        ends.append((p.GetPosition(), lname))
    vout = []
    for v in vias:
        r = v.GetWidth(pcbnew.F_Cu) / 2 + IU(0.01)
        on = sorted({lname for pos, lname in ends if (pos - v.GetPosition()).EuclideanNorm() <= r}, key=CU_ORDER.index)
        span = (z[on[-1]] - z[on[0]]) if len(on) >= 2 else 0.0
        vout.append({"xy": k2c(v.GetPosition()), "layers": on, "span_mm": round(span, 4), "ps": round(span * d_via, 3)})
    tot_mm = sum(e["mm"] for e in layers.values()) + sum(v["span_mm"] for v in vout)
    tot_ps = sum(e["ps"] for e in layers.values()) + sum(v["ps"] for v in vout)
    for e in layers.values():
        e["mm"], e["ps"] = round(e["mm"], 4), round(e["ps"], 3)
    return {"net": net_name, "layers": layers, "vias": vout, "mm": round(tot_mm, 4), "ps": round(tot_ps, 3)}


def group_report(board, group_name):
    """Card delay of every net of a group of the spec (CMD, WR, RD_P, ...) against its target.

    The card delay of a divider-driven net counts both sides of the series resistor (finger-side net + ball-side
    net), as the spec defines it; 'ps' is that sum, 'parts' holds the net_report of each side."""
    g = next(g for g in spec()["groups"] if g["name"] == group_name)
    fs = g.get("finger_side_nets") or []
    out = {}
    for i, n in enumerate(g["nets"]):
        parts_ = [net_report(board, n)]
        if i < len(fs) and fs[i] and fs[i] != n:
            parts_.append(net_report(board, fs[i]))
        out[n] = {"net": n, "parts": parts_, "mm": round(sum(p["mm"] for p in parts_), 4), "ps": round(sum(p["ps"] for p in parts_), 3),
                  "target_ps": g.get("per_net_target_card_delay_ps", {}).get(n)}
    return out


# ------------------------------------------------------------------------------------------------ misc
def log(stage, msg):
    print("[%s] %s" % (stage, msg))
    sys.stdout.flush()

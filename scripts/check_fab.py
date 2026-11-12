#!/usr/bin/env python3
"""Verify the fabrication package in fab/ against the board, without KiCad's plot code:  check_fab.py [--fab dir] [--out dir]

scripts/make_fab.py writes the package with kicad-cli; this script reads the files back with its own parsers (Gerber RS-274X / X2 with
aperture macros, regions, arcs and polarity; Excellon; IPC-D-356; the CSV files) and compares them with the board file as pcbnew reports
it (items and their geometry, not plots):

  gerber    six copper files with the X2 file functions L1 .. L6; every selected aperture defined and non-zero; the profile is one closed
            contour whose extents are the 69.6 x 30.0 mm outline; each copper layer rasterised from the Gerber equals the same layer
            rasterised from the pcbnew items (tracks, arcs, vias with their per-layer pads, pad polygons, zone fills) to one pixel;
            no copper outside the profile, smallest distance to the edge per layer
  mask      every finger of J1, every test pad and every fiducial lies inside a mask opening; the centre of every plain (tented) via is
            covered on both faces; no legend on exposed copper
  paste     one opening per pad that has paste; none on the fingers, test pads and fiducials
  drill     plated / non-plated Excellon files: every hit is a via or a hole of the board with that diameter, and the reverse; the
            filled-and-capped list equals the vias that carry the attribute in the board file
  netlist   IPC-D-356: one record per via and per numbered pad, net names in one-to-one relation with the board's nets
  position  one row per placed part of that face; X / Y = footprint position in the card frame; rotation = KiCad orientation; pad 1 of
            every part lands where that convention puts it
  bom       references, values, MPN and quantities equal the board and design/netlist.json; nothing fitted that is not in the BOM
  drc       kicad-cli DRC with all severities and schematic parity (KiCad's own check, recorded here)

Writes <out>/report.json, <out>/report.txt and PNG images of the Gerbers (drill hits overlaid) for the eye.  Exit status 1 if a check fails.
"""
import argparse
import csv
import hashlib
import json
import math
import os
import re
import subprocess
import sys

import cairo
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "pcb"))
import common as C      # noqa: E402
import pcbnew           # noqa: E402

PROJECT = C.PROJECT
CU_FILES = [("F.Cu", "F_Cu", "Copper,L1,Top"), ("In1.Cu", "In1_Cu", "Copper,L2,Inr"), ("In2.Cu", "In2_Cu", "Copper,L3,Inr"),
            ("In3.Cu", "In3_Cu", "Copper,L4,Inr"), ("In4.Cu", "In4_Cu", "Copper,L5,Inr"), ("B.Cu", "B_Cu", "Copper,L6,Bot")]
OTHER_FILES = [("F.Mask", "F_Mask", "Soldermask,Top"), ("B.Mask", "B_Mask", "Soldermask,Bot"), ("F.Paste", "F_Paste", "Paste,Top"),
               ("B.Paste", "B_Paste", "Paste,Bot"), ("F.Silkscreen", "F_Silkscreen", "Legend,Top"), ("B.Silkscreen", "B_Silkscreen", "Legend,Bot"),
               ("Edge.Cuts", "Edge_Cuts", "Profile,NP"), ("User.Comments", "User_Comments", "Other,Comment"),
               ("User.Drawings", "User_Drawings", "OtherDrawing,Comment"), ("F.Fab", "F_Fab", "AssemblyDrawing,Top"), ("B.Fab", "B_Fab", "AssemblyDrawing,Bot")]
COL = {"F.Cu": (1.00, 0.33, 0.33), "In1.Cu": (0.36, 0.80, 0.36), "In2.Cu": (0.88, 0.66, 0.22), "In3.Cu": (0.30, 0.66, 1.00),
       "In4.Cu": (0.80, 0.42, 1.00), "B.Cu": (0.20, 0.82, 0.80), "F.Mask": (0.75, 0.45, 0.85), "B.Mask": (0.75, 0.45, 0.85),
       "F.Paste": (0.80, 0.80, 0.80), "B.Paste": (0.80, 0.80, 0.80), "F.Silkscreen": (0.95, 0.95, 0.95), "B.Silkscreen": (0.95, 0.95, 0.95),
       "Edge.Cuts": (0.95, 0.95, 0.25)}
BG = (0.055, 0.065, 0.085)


# ====================================================================================================== Gerber
class Gerber:
    """Minimal RS-274X / X2 reader: FS / MO / AD / AM / LP, G01 / G02 / G03 / G36 / G37 / G75, D01 / D02 / D03, standard apertures
    C / R / O / P and aperture macros with the primitives 1, 4, 5, 20, 21.  Everything KiCad 10 writes for this board."""

    def __init__(self, path):
        self.path = path
        self.file_attr = {}
        self.apertures = {}     # code -> (template, params, attributes)
        self.macros = {}
        self.ops = []           # ("flash", ap, pol, x, y) | ("draw", ap, pol, mode, (x0, y0), (x1, y1), (i, j)) | ("region", pol, contours)
        self.used = set()
        self.problems = []
        self._dec = 6
        self._unit = 1.0
        self._pol = "D"
        self._mode = 1
        self._ap = None
        self._x = self._y = 0.0
        self._op = None
        self._region = None
        self._ta = {}
        self._parse(open(path).read())

    # ------------------------------------------------------------------ reading
    def _parse(self, text):
        i, n = 0, len(text)
        while i < n:
            ch = text[i]
            if ch in "\r\n \t":
                i += 1
            elif ch == "%":
                j = text.index("%", i + 1)
                self._extended([s.strip() for s in text[i + 1:j].split("*") if s.strip()])
                i = j + 1
            else:
                j = text.index("*", i)
                self._statement(text[i:j].strip())
                i = j + 1

    def _extended(self, st):
        s = st[0]
        if s.startswith("FS"):
            m = re.match(r"FSLAX(\d)(\d)Y(\d)(\d)$", s)
            if not m:
                self.problems.append("format statement %r" % s)
            else:
                self._dec = int(m.group(2))
        elif s.startswith("MO"):
            self._unit = 1.0 if s == "MOMM" else 25.4
        elif s.startswith("AM"):
            self.macros[s[2:]] = st[1:]
        elif s.startswith("AD"):
            m = re.match(r"ADD(\d+)([A-Za-z_.$][\w.$]*),?(.*)$", s)
            params = [float(v) for v in m.group(3).split("X")] if m.group(3) else []
            self.apertures[int(m.group(1))] = (m.group(2), params, dict(self._ta))
        elif s.startswith("LP"):
            self._pol = s[2]
        elif s.startswith("TF"):
            k = s[2:].lstrip(".,").split(",")
            self.file_attr[k[0]] = ",".join(k[1:])
            if s[2] != ".":
                self.problems.append("malformed attribute %r" % s)
        elif s.startswith("TA"):
            k = s[3:].split(",")
            self._ta[k[0]] = ",".join(k[1:])
        elif s.startswith("TD"):
            self._ta = {}
        elif s[:2] in ("TO", "IP", "OF", "SF", "IN", "LN"):
            pass
        elif s[:2] in ("LM", "LR", "LS", "SR", "AB"):
            self.problems.append("unsupported statement %r" % s)
        else:
            self.problems.append("unknown statement %r" % s)

    def _coord(self, v):
        return int(v) / (10.0 ** self._dec) * self._unit

    def _statement(self, s):
        if not s or s.startswith("G04"):
            return
        x, y, i, j, op = self._x, self._y, 0.0, 0.0, None
        moved = False
        for code, val in re.findall(r"([GDMXYIJ])([+-]?\d+)", s):
            v = int(val)
            if code == "G":
                if v in (1, 2, 3):
                    self._mode = v
                elif v == 36:
                    self._region = []
                elif v == 37:
                    if self._region:
                        self.ops.append(("region", self._pol, self._region))
                    self._region = None
                elif v == 74:
                    self.problems.append("single-quadrant arcs (G74) are not supported")
                elif v == 91:
                    self.problems.append("incremental coordinates (G91) are not supported")
            elif code == "D":
                if v >= 10:
                    self._ap = v
                    self.used.add(v)
                else:
                    op = v
            elif code == "X":
                x, moved = self._coord(val), True
            elif code == "Y":
                y, moved = self._coord(val), True
            elif code == "I":
                i = self._coord(val)
            elif code == "J":
                j = self._coord(val)
        if op is None and moved:
            op = self._op
        if op is None:
            return
        self._op = op
        if op == 2:
            if self._region is not None:
                self._region.append([("M", x, y)])
        elif op == 1:
            if self._region is not None:
                if not self._region:
                    self._region.append([("M", self._x, self._y)])
                self._region[-1].append(("L", x, y) if self._mode == 1 else ("A", x, y, self._x + i, self._y + j, self._mode))
            else:
                self.ops.append(("draw", self._ap, self._pol, self._mode, (self._x, self._y), (x, y), (i, j)))
        elif op == 3:
            self.ops.append(("flash", self._ap, self._pol, x, y))
        self._x, self._y = x, y

    # ------------------------------------------------------------------ checks on the file itself
    def aperture_problems(self):
        out = list(self.problems)
        for a in sorted(self.used):
            if a not in self.apertures:
                out.append("D%d selected but not defined" % a)
        drawn = {o[1] for o in self.ops if o[0] != "region"}
        for a, (tpl, par, _) in self.apertures.items():
            # KiCad defines a zero-size circle and selects it for regions (G36), which have no aperture: allowed while nothing is drawn with it
            if tpl in ("C", "R", "O", "P") and (not par or min(par[:2 if tpl in "RO" else 1]) <= 0) and a in drawn:
                out.append("D%d %s has a zero dimension and is used to draw" % (a, tpl))
            if tpl not in ("C", "R", "O", "P") and tpl not in self.macros:
                out.append("D%d uses the undefined macro %s" % (a, tpl))
        for op in self.ops:
            if op[0] == "draw" and self.apertures.get(op[1], ("C",))[0] != "C":
                out.append("a draw with the non-circular aperture D%d" % op[1])
                break
        return out

    def counts(self):
        return {"apertures": len(self.apertures), "macros": len(self.macros),
                "flashes": sum(1 for o in self.ops if o[0] == "flash"), "draws": sum(1 for o in self.ops if o[0] == "draw"),
                "regions": sum(1 for o in self.ops if o[0] == "region")}

    def centreline_extents(self):
        """Bounding box of the centre lines of all draws (arcs sampled)."""
        xs, ys = [], []
        for o in self.ops:
            if o[0] != "draw":
                continue
            for p in arc_points(o[4], o[5], (o[4][0] + o[6][0], o[4][1] + o[6][1]), o[3]) if o[3] != 1 else (o[4], o[5]):
                xs.append(p[0])
                ys.append(p[1])
        return (min(xs), min(ys), max(xs), max(ys)) if xs else None

    def polylines(self):
        """Draws as point lists (arcs sampled every 2 degrees or finer)."""
        out = []
        for o in self.ops:
            if o[0] == "draw":
                out.append(list(arc_points(o[4], o[5], (o[4][0] + o[6][0], o[4][1] + o[6][1]), o[3])) if o[3] != 1 else [o[4], o[5]])
        return out

    # ------------------------------------------------------------------ drawing
    def render(self, cv):
        ctx = cv.ctx
        for o in self.ops:
            ctx.set_operator(cairo.OPERATOR_OVER if o[2 if o[0] != "region" else 1] == "D" else cairo.OPERATOR_CLEAR)
            if o[0] == "flash":
                self._flash(ctx, o[1], o[3], o[4])
            elif o[0] == "draw":
                tpl, par, _ = self.apertures[o[1]]
                ctx.set_line_width(par[0])
                ctx.set_line_cap(cairo.LINE_CAP_ROUND)
                ctx.set_line_join(cairo.LINE_JOIN_ROUND)
                ctx.new_path()
                ctx.move_to(*o[4])
                if o[3] == 1:
                    ctx.line_to(*o[5])
                else:
                    path_arc(ctx, o[4], o[5], (o[4][0] + o[6][0], o[4][1] + o[6][1]), o[3])
                ctx.stroke()
            else:
                ctx.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
                for contour in o[2]:
                    ctx.new_path()
                    px, py = contour[0][1], contour[0][2]
                    ctx.move_to(px, py)
                    for seg in contour[1:]:
                        if seg[0] == "L":
                            ctx.line_to(seg[1], seg[2])
                        else:
                            path_arc(ctx, (px, py), (seg[1], seg[2]), (seg[3], seg[4]), seg[5])
                        px, py = seg[1], seg[2]
                    ctx.close_path()
                    ctx.fill()
        ctx.set_operator(cairo.OPERATOR_OVER)

    def _flash(self, ctx, ap, x, y):
        tpl, par, _ = self.apertures[ap]
        ctx.save()
        ctx.translate(x, y)
        ctx.new_path()
        if tpl == "C":
            ctx.arc(0, 0, par[0] / 2, 0, 2 * math.pi)
            ctx.fill()
        elif tpl == "R":
            ctx.rectangle(-par[0] / 2, -par[1] / 2, par[0], par[1])
            ctx.fill()
        elif tpl == "O":
            w, h = par[0], par[1]
            d = min(w, h)
            ctx.set_line_width(d)
            ctx.set_line_cap(cairo.LINE_CAP_ROUND)
            ctx.move_to(-(w - d) / 2, -(h - d) / 2)
            ctx.line_to((w - d) / 2, (h - d) / 2)
            if w == h:
                ctx.arc(0, 0, d / 2, 0, 2 * math.pi)
                ctx.fill()
            else:
                ctx.stroke()
        elif tpl == "P":
            polygon(ctx, 0, 0, par[0] / 2, int(par[1]), par[2] if len(par) > 2 else 0.0)
            ctx.fill()
        else:
            self._macro(ctx, tpl, par)
        ctx.restore()

    def _macro(self, ctx, name, par):
        var = {i + 1: v for i, v in enumerate(par)}

        def ev(expr):
            e = re.sub(r"\$(\d+)", lambda m: "(%r)" % var.get(int(m.group(1)), 0.0), expr.strip()).replace("x", "*").replace("X", "*")
            if not re.match(r"^[\d.+\-*/() e]+$", e):
                raise ValueError("macro expression %r" % expr)
            return float(eval(e, {"__builtins__": {}}))        # digits and operators only (checked above)

        for line in self.macros[name]:
            if line.startswith("0 ") or line == "0":
                continue
            m = re.match(r"\$(\d+)=(.*)$", line)
            if m:
                var[int(m.group(1))] = ev(m.group(2))
                continue
            v = [ev(t) for t in line.split(",")]
            code = int(v[0])
            if code not in (1, 4, 5, 20, 21) or v[1] != 1:
                raise ValueError("macro primitive %r of %s is not supported" % (line, name))
            ctx.save()
            ctx.new_path()
            if code == 1:
                ctx.rotate(math.radians(v[5] if len(v) > 5 else 0.0))
                ctx.arc(v[3], v[4], v[2] / 2, 0, 2 * math.pi)
            elif code == 4:
                nv = int(v[2])
                ctx.rotate(math.radians(v[3 + 2 * (nv + 1)]))
                ctx.move_to(v[3], v[4])
                for k in range(1, nv + 1):
                    ctx.line_to(v[3 + 2 * k], v[4 + 2 * k])
                ctx.close_path()
            elif code == 5:
                ctx.rotate(math.radians(v[6]))
                polygon(ctx, v[3], v[4], v[5] / 2, int(v[2]), 0.0)
            elif code == 20:
                ctx.rotate(math.radians(v[7]))
                dx, dy = v[5] - v[3], v[6] - v[4]
                ln = math.hypot(dx, dy)
                nx, ny = -dy / ln * v[2] / 2, dx / ln * v[2] / 2
                ctx.move_to(v[3] + nx, v[4] + ny)
                ctx.line_to(v[5] + nx, v[6] + ny)
                ctx.line_to(v[5] - nx, v[6] - ny)
                ctx.line_to(v[3] - nx, v[4] - ny)
                ctx.close_path()
            else:
                ctx.rotate(math.radians(v[6]))
                ctx.rectangle(v[4] - v[2] / 2, v[5] - v[3] / 2, v[2], v[3])
            ctx.fill()
            ctx.restore()


def polygon(ctx, cx, cy, r, n, rot):
    for k in range(n):
        a = math.radians(rot) + 2 * math.pi * k / n
        (ctx.move_to if k == 0 else ctx.line_to)(cx + r * math.cos(a), cy + r * math.sin(a))
    ctx.close_path()


def arc_angles(p0, p1, c, mode):
    a0 = math.atan2(p0[1] - c[1], p0[0] - c[0])
    a1 = math.atan2(p1[1] - c[1], p1[0] - c[0])
    if mode == 3:           # counter-clockwise
        if a1 <= a0 + 1e-12:
            a1 += 2 * math.pi
    else:
        if a1 >= a0 - 1e-12:
            a1 -= 2 * math.pi
    return a0, a1


def path_arc(ctx, p0, p1, c, mode):
    """Append the arc p0 -> p1 about c (mode 2 = clockwise, 3 = counter-clockwise; equal points = full circle) to the path.
    The canvas has y up, so cairo's 'arc' is counter-clockwise in user space."""
    r = math.hypot(p0[0] - c[0], p0[1] - c[1])
    a0, a1 = arc_angles(p0, p1, c, mode)
    (ctx.arc if mode == 3 else ctx.arc_negative)(c[0], c[1], r, a0, a1)


def arc_points(p0, p1, c, mode):
    r = math.hypot(p0[0] - c[0], p0[1] - c[1])
    a0, a1 = arc_angles(p0, p1, c, mode)
    n = max(4, int(abs(a1 - a0) / math.radians(2.0)) + 1)
    return [p0] + [(c[0] + r * math.cos(a0 + (a1 - a0) * k / n), c[1] + r * math.sin(a0 + (a1 - a0) * k / n)) for k in range(1, n)] + [p1]


# ====================================================================================================== canvas
class Canvas:
    """A8 raster in the card frame (mm, y up)."""

    def __init__(self, bbox, ppm):
        self.x0, self.y0, self.x1, self.y1 = bbox
        self.ppm = ppm
        self.w = int(round((self.x1 - self.x0) * ppm))
        self.h = int(round((self.y1 - self.y0) * ppm))
        self.surf = cairo.ImageSurface(cairo.FORMAT_A8, self.w, self.h)
        self.ctx = cairo.Context(self.surf)
        self.ctx.translate(0, self.h)
        self.ctx.scale(ppm, -ppm)
        self.ctx.translate(-self.x0, -self.y0)
        self.ctx.set_source_rgba(0, 0, 0, 1)

    def array(self):
        self.surf.flush()
        a = np.frombuffer(self.surf.get_data(), np.uint8).reshape(self.h, self.surf.get_stride())[:, :self.w]
        return a >= 128

    def px(self, x, y):
        return int((x - self.x0) * self.ppm), int((self.y1 - y) * self.ppm)


def dilate(a, n=1):
    from scipy.ndimage import binary_dilation
    return binary_dilation(a, np.ones((3, 3), bool), iterations=n)


# ====================================================================================================== the board as pcbnew reports it
class Board:
    def __init__(self, path):
        self.path = path
        self.b = pcbnew.LoadBoard(path)
        ao = self.b.GetDesignSettings().GetAuxOrigin()
        self.ox, self.oy = ao.x / 1e6, ao.y / 1e6

    def c(self, p):
        return (p.x / 1e6 - self.ox, self.oy - p.y / 1e6)

    def vias(self):
        out = []
        for t in self.b.GetTracks():
            if t.GetClass() == "PCB_VIA":
                x, y = self.c(t.GetPosition())
                out.append({"x": x, "y": y, "drill": t.GetDrill() / 1e6, "pad": t.GetWidth(pcbnew.F_Cu) / 1e6, "net": t.GetNetname(),
                            "filled": bool(t.GetPrimaryDrillFilledFlag()), "capped": bool(t.GetPrimaryDrillCappedFlag()), "item": t})
        return out

    def holes(self):
        """(x, y, diameter, plated) of every pad hole."""
        out = []
        for fp in self.b.GetFootprints():
            for p in fp.Pads():
                d = p.GetDrillSize()
                if d.x > 0:
                    x, y = self.c(p.GetPosition())
                    out.append((x, y, d.x / 1e6, p.GetAttribute() != pcbnew.PAD_ATTRIB_NPTH))
        return out

    def poly_path(self, ctx, ps):
        for i in range(ps.OutlineCount()):
            for chain in [ps.Outline(i)] + [ps.Hole(i, k) for k in range(ps.HoleCount(i))]:
                for j in range(chain.PointCount()):
                    (ctx.move_to if j == 0 else ctx.line_to)(*self.c(chain.CPoint(j)))
                ctx.close_path()

    def render_copper(self, cv, layer_name):
        """Copper of one layer from the board items: no plot code involved."""
        lid = C.CU[layer_name]
        ctx = cv.ctx
        ctx.set_line_cap(cairo.LINE_CAP_ROUND)
        ctx.set_line_join(cairo.LINE_JOIN_ROUND)
        n = {"tracks": 0, "arcs": 0, "via pads": 0, "pads": 0, "zone outlines": 0}
        for t in self.b.GetTracks():
            cls = t.GetClass()
            if cls == "PCB_VIA":
                if t.FlashLayer(lid):
                    x, y = self.c(t.GetPosition())
                    ctx.new_path()
                    ctx.arc(x, y, t.GetWidth(lid) / 2e6, 0, 2 * math.pi)
                    ctx.fill()
                    n["via pads"] += 1
                continue
            if t.GetLayer() != lid:
                continue
            ctx.set_line_width(t.GetWidth() / 1e6)
            ctx.new_path()
            p0, p1 = self.c(t.GetStart()), self.c(t.GetEnd())
            ctx.move_to(*p0)
            if cls == "PCB_ARC":
                cen, mid = self.c(t.GetCenter()), self.c(t.GetMid())
                cross = (mid[0] - p0[0]) * (p1[1] - p0[1]) - (mid[1] - p0[1]) * (p1[0] - p0[0])
                path_arc(ctx, p0, p1, cen, 2 if cross < 0 else 3)      # mid point left of the chord = clockwise (y up)
                n["arcs"] += 1
            else:
                ctx.line_to(*p1)
                n["tracks"] += 1
            ctx.stroke()
        for fp in self.b.GetFootprints():
            for g in fp.GraphicalItems():
                if g.GetLayer() == lid:
                    raise RuntimeError("footprint graphic on %s in %s: not handled" % (layer_name, fp.GetReference()))
            for p in fp.Pads():
                if not p.IsOnLayer(lid) or not p.FlashLayer(lid):
                    continue
                ctx.new_path()
                if p.GetShape(lid) == pcbnew.PAD_SHAPE_CIRCLE:
                    x, y = self.c(p.GetPosition())
                    ctx.arc(x, y, p.GetSize(lid).x / 2e6, 0, 2 * math.pi)
                else:
                    self.poly_path(ctx, p.GetEffectivePolygon(lid, pcbnew.ERROR_INSIDE))
                ctx.fill()
                n["pads"] += 1
        for d in self.b.GetDrawings():
            if d.GetLayer() == lid:
                raise RuntimeError("board graphic on %s: not handled" % layer_name)
        ctx.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
        for z in self.b.Zones():
            if z.GetIsRuleArea() or not z.IsOnLayer(lid):
                continue
            ps = z.GetFilledPolysList(lid)
            ctx.new_path()
            self.poly_path(ctx, ps)
            ctx.fill()
            n["zone outlines"] += ps.OutlineCount()
        return n

    def pad_boxes(self, pred):
        """[(ref, number, face, (x0, y0, x1, y1))] of the copper pads that pred(footprint, pad) selects; for a round pad the box is
        the square inscribed in it."""
        out = []
        for fp in self.b.GetFootprints():
            for p in fp.Pads():
                if not pred(fp, p):
                    continue
                face = "F" if p.IsOnLayer(pcbnew.F_Cu) else "B"
                bb = p.GetBoundingBox()
                x0, y1 = self.c(pcbnew.VECTOR2I(bb.GetLeft(), bb.GetTop()))
                x1, y0 = self.c(pcbnew.VECTOR2I(bb.GetRight(), bb.GetBottom()))
                if p.GetShape(pcbnew.F_Cu if face == "F" else pcbnew.B_Cu) == pcbnew.PAD_SHAPE_CIRCLE:
                    k = (1 - 1 / math.sqrt(2)) / 2
                    x0, x1, y0, y1 = x0 + k * (x1 - x0), x1 - k * (x1 - x0), y0 + k * (y1 - y0), y1 - k * (y1 - y0)
                out.append((fp.GetReference(), p.GetNumber(), face, (x0, y0, x1, y1)))
        return out


# ====================================================================================================== other readers
def read_excellon(path):
    tools, hits, cur, unit = {}, [], None, 1.0
    for line in open(path):
        s = line.strip()
        if s.startswith(";") or not s:
            continue
        if s == "INCH":
            unit = 25.4
        m = re.match(r"T(\d+)C([\d.]+)$", s)
        if m:
            tools[int(m.group(1))] = float(m.group(2)) * unit
            continue
        m = re.match(r"T(\d+)$", s)
        if m:
            cur = int(m.group(1))
            continue
        m = re.match(r"X([+-]?[\d.]+)Y([+-]?[\d.]+)$", s)
        if m:
            hits.append((float(m.group(1)) * unit, float(m.group(2)) * unit, tools[cur]))
    return tools, hits


def key(x, y, d=None):
    return (int(round(x * 1000)), int(round(y * 1000))) + (() if d is None else (int(round(d * 1000)),))


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for blk in iter(lambda: fh.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def run_drc(board_path, out_json):
    """KiCad's own DRC, all severities, with schematic parity, in the project directory (rules and severities of the project file)."""
    subprocess.run(["kicad-cli", "pcb", "drc", "--format", "json", "--severity-all", "--schematic-parity", "--units", "mm", "-o", out_json, board_path],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=os.path.dirname(os.path.abspath(board_path)))
    d = json.load(open(out_json))
    sev = {}
    types = {}
    for v in d.get("violations", []):
        sev[v["severity"]] = sev.get(v["severity"], 0) + 1
        types[(v["severity"], v["type"])] = types.get((v["severity"], v["type"]), 0) + 1
    return {"kicad_version": d.get("kicad_version"), "errors": sev.get("error", 0), "warnings": sev.get("warning", 0),
            "unconnected": len(d.get("unconnected_items", [])), "schematic_parity": len(d.get("schematic_parity", [])),
            "by_type": {"%s:%s" % k: v for k, v in sorted(types.items())},
            "warning_nets": sorted({(v.get("description", ""))[:120] for v in d.get("violations", []) if v["severity"] == "warning"})}


# ====================================================================================================== the checks
class Report:
    def __init__(self):
        self.rows = []

    def check(self, group, name, ok, detail=""):
        self.rows.append({"group": group, "check": name, "ok": bool(ok), "detail": detail})
        print("[%s] %-8s %s%s" % ("PASS" if ok else "FAIL", group, name, (": " + detail) if detail else ""))

    def failed(self):
        return [r for r in self.rows if not r["ok"]]


def save_png(mask, colour, path, holes=(), cv=None, title=None):
    """Binary raster -> colour PNG on the dark background of the board renders; drill hits as background dots."""
    h, w = mask.shape
    img = cairo.ImageSurface(cairo.FORMAT_RGB24, w, h)
    buf = np.frombuffer(img.get_data(), np.uint8).reshape(h, img.get_stride())[:, :w * 4].reshape(h, w, 4)
    for k, ch in enumerate((2, 1, 0)):       # BGRx
        buf[:, :, k] = np.where(mask, int(colour[ch] * 255), int(BG[ch] * 255))
    img.mark_dirty()
    ctx = cairo.Context(img)
    if cv is not None:
        ctx.set_source_rgb(*BG)
        for (x, y, d) in holes:
            px, py = (x - cv.x0) * cv.ppm, (cv.y1 - y) * cv.ppm
            ctx.arc(px, py, d / 2 * cv.ppm, 0, 2 * math.pi)
            ctx.fill()
    if title:
        ctx.set_source_rgb(0.9, 0.9, 0.9)
        ctx.select_font_face("DejaVu Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        ctx.set_font_size(max(12, h / 40))
        ctx.move_to(8, max(14, h / 40) + 4)
        ctx.show_text(title)
    img.write_to_png(path)


def verify(fab, out, board_path=None, ppm=80.0, view_ppm=30.0, drc=True):
    os.makedirs(out, exist_ok=True)
    board_path = board_path or C.BOARD
    R = Report()
    res = {"board": os.path.basename(board_path), "board_sha256": sha256(board_path), "pixels_per_mm": ppm}
    spec = json.load(open(C.SPEC))
    B = Board(board_path)
    gdir, ddir = os.path.join(fab, "gerber"), os.path.join(fab, "drill")
    W, H = spec["floorplan"]["outline"]["width"], spec["floorplan"]["outline"]["height"]
    margin = 1.0
    bbox = (-margin, -margin, W + margin, H + margin)

    # ------------------------------------------------------------------ Gerber files, apertures, file functions
    G = {}
    for name, stem, func in CU_FILES + OTHER_FILES:
        p = os.path.join(gdir, "%s-%s.gbr" % (PROJECT, stem))
        if not os.path.exists(p):
            R.check("gerber", "%s present" % name, False, "missing %s" % p)
            continue
        G[name] = Gerber(p)
    R.check("gerber", "all %d layer files present and readable" % len(CU_FILES + OTHER_FILES), len(G) == len(CU_FILES + OTHER_FILES), "%d files" % len(G))
    ff = {n: G[n].file_attr.get("FileFunction", "") for n in G}
    R.check("gerber", "six copper layers carry the X2 file functions L1 (Top), L2 .. L5 (Inr), L6 (Bot)",
            all(ff.get(n) == f for n, _, f in CU_FILES), "; ".join("%s=%s" % (n, ff.get(n)) for n, _, _ in CU_FILES))
    R.check("gerber", "mask, paste, legend, profile and drawing files carry their X2 file functions",
            all(ff.get(n) == f for n, _, f in OTHER_FILES), "; ".join("%s=%s (expected %s)" % (n, ff.get(n), f) for n, _, f in OTHER_FILES if ff.get(n) != f) or "all as expected")
    probs = {n: G[n].aperture_problems() for n in G}
    R.check("gerber", "every selected aperture is defined, none has a zero dimension, no unsupported statement",
            not any(probs.values()), "; ".join("%s: %s" % (n, p[:3]) for n, p in probs.items() if p) or
            "apertures per file: " + ", ".join("%s %d" % (n, G[n].counts()["apertures"]) for n in G))
    res["gerber_counts"] = {n: G[n].counts() for n in G}
    job = os.path.join(gdir, "%s-job.gbrjob" % PROJECT)
    if os.path.exists(job):
        jb = json.load(open(job))
        gs = jb["GeneralSpecs"]
        R.check("gerber", "job file: 6 layers, board thickness of the stackup, edge connector without bevel",
                gs["LayerNumber"] == 6 and abs(gs["BoardThickness"] - spec["stackup"]["total_thickness_with_mask_mm"]) < 1e-6
                and gs.get("EdgeConnector") is True and gs.get("EdgeConnectorBevelled") is False,
                "layers %d, thickness %.3f mm, finish %s, impedance controlled %s" % (gs["LayerNumber"], gs["BoardThickness"], gs.get("Finish"), gs.get("ImpedanceControlled")))
    else:
        R.check("gerber", "job file present", False, job)

    # ------------------------------------------------------------------ profile
    from shapely.geometry import LineString, Polygon
    from shapely.ops import polygonize, unary_union
    prof = G["Edge.Cuts"]
    ext = prof.centreline_extents()
    R.check("profile", "extents of the profile centre line = 0 .. %.1f x 0 .. %.1f mm, origin at the lower-left card corner" % (W, H),
            max(abs(ext[0]), abs(ext[1]), abs(ext[2] - W), abs(ext[3] - H)) < 1e-6, "x %.4f .. %.4f, y %.4f .. %.4f" % (ext[0], ext[2], ext[1], ext[3]))
    polys = list(polygonize(unary_union([LineString(pl) for pl in prof.polylines()])))
    ref_poly = C.board_polygon(B.b)[0]
    ok = len(polys) == 1
    outline = polys[0] if ok else Polygon()
    R.check("profile", "the profile is ONE closed contour; its area equals the board outline of the board file",
            ok and abs(outline.area - ref_poly.area) < 0.02 and outline.symmetric_difference(ref_poly).area < 0.02,
            "%d contour(s), %.3f mm2 (board %.3f mm2, difference area %.4f mm2)" % (len(polys), outline.area, ref_poly.area,
                                                                                  outline.symmetric_difference(ref_poly).area if ok else -1))
    cvo = Canvas(bbox, ppm)
    cvo.ctx.new_path()
    for k, (x, y) in enumerate(outline.exterior.coords):
        (cvo.ctx.move_to if k == 0 else cvo.ctx.line_to)(x, y)
    cvo.ctx.close_path()
    cvo.ctx.fill()
    inside = cvo.array()
    from scipy.ndimage import distance_transform_edt
    edge_dist = distance_transform_edt(inside) / ppm

    # ------------------------------------------------------------------ drill files
    tools_p, hits_p = read_excellon(os.path.join(ddir, "%s-PTH.drl" % PROJECT))
    tools_n, hits_n = read_excellon(os.path.join(ddir, "%s-NPTH.drl" % PROJECT))
    vias = B.vias()
    holes = B.holes()
    want_p = sorted([key(v["x"], v["y"], v["drill"]) for v in vias] + [key(x, y, d) for x, y, d, pl in holes if pl])
    want_n = sorted(key(x, y, d) for x, y, d, pl in holes if not pl)
    got_p, got_n = sorted(key(*h) for h in hits_p), sorted(key(*h) for h in hits_n)

    def by_dia(keys):
        o = {}
        for k in keys:
            o[k[2] / 1000.0] = o.get(k[2] / 1000.0, 0) + 1
        return ", ".join("%d x %.2f mm" % (n, d) for d, n in sorted(o.items()))
    R.check("drill", "plated file: every hit is a via / plated hole of the board with that diameter, and the reverse", got_p == want_p,
            "file %s; board %s (%d vias)" % (by_dia(got_p), by_dia(want_p), len(vias)))
    R.check("drill", "non-plated file: the two 1.80 mm tooling holes of the card edge", got_n == want_n and len(got_n) == 2, "file %s; board %s" % (by_dia(got_n), by_dia(want_n)))
    vdia = {}
    for v in vias:
        vdia[(v["pad"], v["drill"])] = vdia.get((v["pad"], v["drill"]), 0) + 1
    res["vias"] = {"total": len(vias), "by_pad_drill": {"%.2f/%.2f" % k: n for k, n in sorted(vdia.items())},
                   "filled_and_capped": sum(1 for v in vias if v["filled"] and v["capped"])}
    filled = sorted(key(v["x"], v["y"], v["drill"]) for v in vias if v["filled"] and v["capped"])
    R.check("drill", "filled = capped on every via that carries either attribute", all(v["filled"] == v["capped"] for v in vias),
            "%d filled and capped of %d vias" % (len(filled), len(vias)))
    fpath = os.path.join(ddir, "%s-filled_capped.drl" % PROJECT)
    if os.path.exists(fpath):
        _, hf = read_excellon(fpath)
        R.check("drill", "filled-and-capped drill file = the vias with the attribute in the board file", sorted(key(*h) for h in hf) == filled,
                "file %s" % by_dia(sorted(key(*h) for h in hf)))
    else:
        R.check("drill", "filled-and-capped drill file present", False, fpath)
    for kind in ("filling", "capping"):
        gp = os.path.join(ddir, "gerber_x2", "%s-%s-front-back.gbr" % (PROJECT, kind))
        if os.path.exists(gp):
            g = Gerber(gp)
            fl = sorted(key(o[3], o[4], g.apertures[o[1]][1][0]) for o in g.ops if o[0] == "flash")
            R.check("drill", "X2 %s layer = the same %d vias; header well-formed" % (kind, len(filled)), fl == filled and not g.problems,
                    "%d flashes%s" % (len(fl), ("; " + "; ".join(g.problems)) if g.problems else ""))
        else:
            R.check("drill", "X2 %s layer present" % kind, False, gp)
    csvp = os.path.join(ddir, "filled_capped_vias.csv")
    if os.path.exists(csvp):
        rows = list(csv.DictReader(open(csvp)))
        R.check("drill", "filled_capped_vias.csv lists the same vias", sorted(key(float(r["x_mm"]), float(r["y_mm"]), float(r["drill_mm"])) for r in rows) == filled, "%d rows" % len(rows))

    # ------------------------------------------------------------------ copper: Gerber against the board items
    res["copper"] = {}
    hole_list = [(v["x"], v["y"], v["drill"]) for v in vias] + [(x, y, d) for x, y, d, _ in holes]
    rasters = {}
    for name, _, _ in CU_FILES:
        cg = Canvas(bbox, ppm)
        G[name].render(cg)
        a = cg.array()
        cb = Canvas(bbox, ppm)
        n_items = B.render_copper(cb, name)
        b = cb.array()
        only_g = int((a & ~dilate(b)).sum())
        only_b = int((b & ~dilate(a)).sum())
        xor = int((a ^ b).sum())
        outside = int((a & ~dilate(inside)).sum())
        cu = a & inside
        dmin = float(edge_dist[cu].min()) if cu.any() else float("nan")
        ys = np.nonzero(cu.any(axis=1))[0]
        cover = float(cu.sum()) / float(inside.sum())
        res["copper"][name] = {"gerber": G[name].counts(), "board_items": n_items, "pixels_gerber": int(a.sum()), "pixels_board": int(b.sum()),
                               "xor_pixels": xor, "gerber_only_beyond_1px": only_g, "board_only_beyond_1px": only_b, "outside_profile_pixels": outside,
                               "min_distance_to_edge_mm": round(dmin, 3), "copper_fraction_of_card": round(cover, 4),
                               "lowest_copper_y_mm": round(cg.y1 - (ys.max() + 1) / ppm, 3) if len(ys) else None}
        R.check("copper", "%s: the Gerber image equals the layer drawn from the board items (tolerance one pixel = %.1f um)" % (name, 1000.0 / ppm),
                only_g == 0 and only_b == 0 and a.sum() > 0,
                "%d / %d copper pixels, %d differ at edges (%.3f %%), beyond one pixel: %d only in the Gerber, %d only on the board"
                % (a.sum(), b.sum(), xor, 100.0 * xor / max(1, a.sum()), only_g, only_b))
        R.check("copper", "%s: no copper outside the profile" % name, outside == 0,
                "nearest copper %.3f mm from the edge; %.1f %% of the card area is copper" % (dmin, 100 * cover))
        rasters[name] = a
        cv = Canvas(bbox, view_ppm)
        G[name].render(cv)
        save_png(cv.array(), COL[name], os.path.join(out, "gerber_%s.png" % name.replace(".", "_")), hole_list, cv,
                 "%s from fab/gerber (%s), drill hits from the Excellon files" % (name, ff.get(name)))
        if only_g or only_b:
            save_png(a ^ b, (1, 1, 0), os.path.join(out, "diff_%s.png" % name.replace(".", "_")))
    pull = {n: res["copper"][n]["min_distance_to_edge_mm"] for n in res["copper"]}
    R.check("copper", "edge clearance: outer layers 0.15 mm (the fingers), every inner layer pulled back 0.3 mm or more",
            all(pull[n] >= 0.14 for n in ("F.Cu", "B.Cu")) and all(pull[n] >= 0.29 for n in ("In1.Cu", "In2.Cu", "In3.Cu", "In4.Cu")),
            ", ".join("%s %.2f" % (n, pull[n]) for n in pull))
    # every drill hit lies on copper of an outer layer or is a non-plated hole
    miss = [h for h in hits_p if not (rasters["F.Cu"][Canvas.px(cvo, h[0], h[1])[::-1]] and rasters["B.Cu"][Canvas.px(cvo, h[0], h[1])[::-1]])]
    R.check("drill", "every plated hit lies on copper on F.Cu and on B.Cu (outer via pads are kept)", not miss, "%d of %d hits" % (len(hits_p) - len(miss), len(hits_p)))

    # ------------------------------------------------------------------ mask, paste, legend
    masks = {}
    for face, name in (("F", "F.Mask"), ("B", "B.Mask")):
        cg = Canvas(bbox, ppm)
        G[name].render(cg)
        masks[face] = cg.array()
        cv = Canvas(bbox, view_ppm)
        G[name].render(cv)
        save_png(cv.array(), COL[name], os.path.join(out, "gerber_%s.png" % name.replace(".", "_")), (), cv, "%s openings from fab/gerber (negative layer: drawn = no mask)" % name)

    def boxes_open(boxes, raster_by_face):
        bad = []
        for ref, num, face, (x0, y0, x1, y1) in boxes:
            c0, r1 = Canvas.px(cvo, x0, y0)
            c1, r0 = Canvas.px(cvo, x1, y1)
            win = raster_by_face[face][r0 + 1:r1, c0 + 1:c1]
            if win.size == 0 or not win.all():
                bad.append("%s.%s" % (ref, num))
        return bad
    fingers = B.pad_boxes(lambda fp, p: fp.GetReference() == "J1" and p.GetNumber() != "" and p.GetDrillSize().x == 0)
    bad = boxes_open(fingers, masks)
    R.check("mask", "all %d fingers of J1 are free of solder mask over their whole land" % len(fingers), not bad and len(fingers) == 260, "not open: %s" % bad[:6] if bad else "260 lands inside the openings")
    tpf = B.pad_boxes(lambda fp, p: fp.GetReference().startswith(("TP", "FID")))
    bad = boxes_open(tpf, masks)
    R.check("mask", "test pads and fiducials are open", not bad, "%d pads%s" % (len(tpf), (", not open: %s" % bad) if bad else ""))
    plain = [v for v in vias if not v["filled"]]
    opened = [v for v in plain for face in "FB" if masks[face][Canvas.px(cvo, v["x"], v["y"])[::-1]]]
    R.check("mask", "plain vias are tented: the mask covers the centre of each on both faces", not opened,
            "%d plain vias, %d openings over a via centre%s" % (len(plain), len(opened), "".join(" (%.3f, %.3f)" % (v["x"], v["y"]) for v in opened[:4])))
    fv = [v for v in vias if abs(v["y"] - 3.05) < 1e-6]
    R.check("mask", "the mask-free finger strip ends below the row of finger vias (y = 3.05 mm)", len(fv) > 0 and not any(masks[f][Canvas.px(cvo, v["x"], v["y"])[::-1]] for v in fv for f in "FB"),
            "%d finger vias covered" % len(fv))
    pastes = {}
    for face, name in (("F", "F.Paste"), ("B", "B.Paste")):
        cg = Canvas(bbox, ppm)
        G[name].render(cg)
        pastes[face] = cg.array()
        cv = Canvas(bbox, view_ppm)
        G[name].render(cv)
        save_png(cv.array(), COL[name], os.path.join(out, "gerber_%s.png" % name.replace(".", "_")), (), cv, "%s apertures from fab/gerber" % name)
        lid = pcbnew.F_Paste if face == "F" else pcbnew.B_Paste
        want = sum(1 for fp in B.b.GetFootprints() for p in fp.Pads() if p.IsOnLayer(lid))
        got = G[name].counts()
        R.check("paste", "%s: one aperture per pad that has paste" % name, got["flashes"] + got["regions"] == want and got["draws"] == 0, "%d flashes, %d pads with paste" % (got["flashes"], want))
        outside_mask = int((pastes[face] & ~dilate(masks[face], 2)).sum())
        R.check("paste", "%s: every paste aperture lies inside a mask opening" % name, outside_mask == 0, "%d pixels outside" % outside_mask)
    nopaste = fingers + tpf
    hit = [b_ for b_ in nopaste if pastes[b_[2]][Canvas.px(cvo, (b_[3][0] + b_[3][2]) / 2, (b_[3][1] + b_[3][3]) / 2)[::-1]]]
    R.check("paste", "no paste on the fingers, the test pads and the fiducials", not hit, "%d lands checked" % len(nopaste))
    for face, name in (("F", "F.Silkscreen"), ("B", "B.Silkscreen")):
        cg = Canvas(bbox, ppm)
        G[name].render(cg)
        s = cg.array()
        on = int((s & masks[face]).sum())
        out_px = int((s & ~inside).sum())
        R.check("legend", "%s: no legend ink on exposed copper, none outside the card" % name, on == 0 and out_px == 0,
                "%d pixels of legend, %d over mask openings, %d outside" % (s.sum(), on, out_px))
        cv = Canvas(bbox, view_ppm)
        G[name].render(cv)
        save_png(cv.array(), COL[name], os.path.join(out, "gerber_%s.png" % name.replace(".", "_")), (), cv, "%s from fab/gerber (mask openings subtracted)" % name)
    cv = Canvas(bbox, view_ppm)
    G["Edge.Cuts"].render(cv)
    save_png(dilate(cv.array(), 1), COL["Edge.Cuts"], os.path.join(out, "gerber_Edge_Cuts.png"), [(x, y, d) for x, y, d in hits_n], cv, "Edge.Cuts (profile) and the non-plated holes")

    # ------------------------------------------------------------------ IPC-D-356
    d356 = os.path.join(fab, "netlist", "%s.d356" % PROJECT)
    recs = [ln.rstrip("\n") for ln in open(d356) if ln[:3] in ("317", "327", "367")]
    via_recs = [r for r in recs if r[20:26].strip() == "VIA"]
    pad_recs = {(r[20:26].strip(), r[27:31].strip()): r[3:17].strip() for r in recs if r[20:26].strip() != "VIA" and r[27:31].strip() != ""}
    nopin = [r for r in recs if r[20:26].strip() != "VIA" and r[27:31].strip() == ""]      # fiducials (327) and non-plated holes (367)
    bpads = {}
    for fp in B.b.GetFootprints():
        for p in fp.Pads():
            if p.GetNumber() != "" and (p.IsOnLayer(pcbnew.F_Cu) or p.IsOnLayer(pcbnew.B_Cu)):
                bpads.setdefault((fp.GetReference(), p.GetNumber()), p.GetNetname())
    npth = sum(1 for x, y, d_, pl in holes if not pl)
    n_numbered = sum(1 for fp in B.b.GetFootprints() for p in fp.Pads() if p.GetNumber() != "" and (p.IsOnLayer(pcbnew.F_Cu) or p.IsOnLayer(pcbnew.B_Cu)))
    fwd, back, clash = {}, {}, []
    for k_, nm in pad_recs.items():
        bn = bpads.get(k_)
        if bn is None:
            continue
        if fwd.setdefault(bn, nm) != nm or back.setdefault(nm, bn) != bn:
            clash.append((k_, bn, nm))
    nfid = sum(1 for fp in B.b.GetFootprints() if fp.GetReference().startswith("FID"))
    R.check("netlist", "IPC-D-356: one record per via, per numbered pad, per fiducial and per non-plated hole of the board",
            len(via_recs) == len(vias) and set(pad_recs) == set(bpads) and len(recs) - len(nopin) - len(via_recs) == n_numbered
            and sum(1 for r in nopin if r[:3] == "327") == nfid and sum(1 for r in nopin if r[:3] == "367") == npth,
            "%d via records (%d vias), %d pad records (%d numbered pads, %d distinct pins), %d fiducials, %d non-plated holes" % (len(via_recs), len(vias), len(recs) - len(nopin) - len(via_recs), n_numbered, len(bpads),
                                                                                                    sum(1 for r in nopin if r[:3] == "327"), sum(1 for r in nopin if r[:3] == "367")))
    R.check("netlist", "IPC-D-356: net names are in one-to-one relation with the nets of the board (names are cut to 14 characters)", not clash,
            "%d nets%s" % (len(fwd), ("; clashes %s" % clash[:3]) if clash else ""))
    vn = {}
    for v in vias:
        vn[v["net"]] = vn.get(v["net"], 0) + 1
    dn = {}
    for r in via_recs:
        dn[back.get(r[3:17].strip(), r[3:17].strip())] = dn.get(back.get(r[3:17].strip(), r[3:17].strip()), 0) + 1
    R.check("netlist", "IPC-D-356: vias per net equal the board", dn == vn, "GND %d, %d nets with vias" % (dn.get("GND", 0), len(dn)))
    res["netlist"] = {"records": len(recs), "via_records": len(via_recs), "pad_records": len(pad_recs), "nets": len(fwd), "npth_holes_on_board": npth}

    # ------------------------------------------------------------------ position files
    adir = os.path.join(fab, "assembly")
    placed = {fp.GetReference(): fp for fp in B.b.GetFootprints() if not fp.GetAttributes() & pcbnew.FP_EXCLUDE_FROM_POS_FILES}
    res["position"] = {}
    lib_cache = {}

    def lib_pads(fp):
        fid = fp.GetFPID()
        nick, item = str(fid.GetLibNickname()), str(fid.GetLibItemName())
        if (nick, item) not in lib_cache:
            path = C.PROJECT_LIB_DIR if nick == C.PROJECT_LIB else os.path.join(C.KICAD_FP, nick + ".pretty")
            lf = pcbnew.FootprintLoad(path, item)
            lib_cache[(nick, item)] = {}
            for p in lf.Pads():         # a number may occur on several pads (exposed pad in pieces)
                if p.GetNumber() != "":
                    lib_cache[(nick, item)].setdefault(p.GetNumber(), []).append((p.GetPosition().x / 1e6, -p.GetPosition().y / 1e6))
        return lib_cache[(nick, item)]
    for face, fname, flipped in (("top", "%s-top.pos.csv" % PROJECT, False), ("bottom", "%s-bottom.pos.csv" % PROJECT, True)):
        rows = list(csv.DictReader(open(os.path.join(adir, fname))))
        want = {r_: fp for r_, fp in placed.items() if fp.IsFlipped() == flipped}
        badpos, badrot, badpad = [], [], []
        for r in rows:
            fp = want.get(r["Ref"])
            if fp is None:
                continue
            x, y = B.c(fp.GetPosition())
            if abs(x - float(r["PosX"])) > 1e-6 or abs(y - float(r["PosY"])) > 1e-6 or r["Side"] != face:
                badpos.append(r["Ref"])
            rot = float(r["Rot"])
            if abs(((rot - fp.GetOrientationDegrees()) + 180) % 360 - 180) > 1e-6:
                badrot.append(r["Ref"])
            # convention: Rot counter-clockwise seen from the FRONT of the card for both faces; a back-side footprint is the library
            # footprint mirrored top-to-bottom (KiCad flip).  Pad positions predicted from the row must be the pads of the board.
            th = math.radians(rot)
            lp = lib_pads(fp)
            for p in fp.Pads():
                if p.GetNumber() not in lp:
                    continue
                px, py = B.c(p.GetPosition())
                hit = False
                for lx, ly in lp[p.GetNumber()]:
                    if flipped:
                        ly = -ly
                    ex = float(r["PosX"]) + lx * math.cos(th) - ly * math.sin(th)
                    ey = float(r["PosY"]) + lx * math.sin(th) + ly * math.cos(th)
                    hit = hit or (abs(ex - px) <= 2e-6 and abs(ey - py) <= 2e-6)
                if not hit:
                    badpad.append("%s.%s" % (r["Ref"], p.GetNumber()))
                    break
        R.check("position", "%s: one row per placed part of that face (fingers, test pads and fiducials excluded)" % fname,
                sorted(r["Ref"] for r in rows) == sorted(want), "%d rows, %d parts on the board" % (len(rows), len(want)))
        R.check("position", "%s: X / Y = footprint position in the card frame (origin lower-left corner seen from the front, y up, x not mirrored), side column '%s'" % (fname, face),
                not badpos, "%d rows" % len(rows))
        R.check("position", "%s: Rot = KiCad orientation; every numbered pad lands where 'counter-clockwise seen from the front%s' puts it"
                % (fname, ", footprint mirrored top-to-bottom" if flipped else ""), not badrot and not badpad, "wrong rotation %s, wrong pads %s" % (badrot[:4], badpad[:4]) if badrot or badpad else "%d parts" % len(rows))
        res["position"][face] = len(rows)
    fidp = os.path.join(adir, "%s-fiducials.csv" % PROJECT)
    if os.path.exists(fidp):
        rows = list(csv.DictReader(open(fidp)))
        want = {fp.GetReference(): fp for fp in B.b.GetFootprints() if fp.GetReference().startswith("FID")}
        ok = sorted(r["Ref"] for r in rows) == sorted(want) and all(
            abs(B.c(want[r["Ref"]].GetPosition())[0] - float(r["PosX"])) < 1e-6 and abs(B.c(want[r["Ref"]].GetPosition())[1] - float(r["PosY"])) < 1e-6
            and r["Side"] == ("bottom" if want[r["Ref"]].IsFlipped() else "top") for r in rows if r["Ref"] in want)
        R.check("position", "fiducial list = FID1 .. FID6 of the board, three per face", ok and len(rows) == 6, "%d rows" % len(rows))

    # ------------------------------------------------------------------ BOM
    bom_rows = list(csv.DictReader(open(os.path.join(adir, "%s-bom.csv" % PROJECT))))
    onboard = {fp.GetReference(): fp for fp in B.b.GetFootprints() if not fp.GetAttributes() & pcbnew.FP_EXCLUDE_FROM_BOM}

    def field(fp, name):
        for f in fp.GetFields():
            if f.GetName() == name:
                return f.GetText()
        return ""
    seen, bad = [], []
    for r in bom_rows:
        refs = r["References"].split(" ")
        seen += refs
        if int(r["Quantity"]) != len(refs):
            bad.append("quantity of item %s" % r["Item"])
        nt = nb = 0
        for ref in refs:
            fp = onboard.get(ref)
            if fp is None:
                bad.append("%s not on the board" % ref)
                continue
            nt += 0 if fp.IsFlipped() else 1
            nb += 1 if fp.IsFlipped() else 0
            if fp.GetValue() != r["Value"] or field(fp, "MPN") != r["MPN"] or field(fp, "Manufacturer") != r["Manufacturer"] \
               or str(fp.GetFPID().GetLibItemName()) != r["Footprint"].split(":")[-1] or bool(fp.GetAttributes() & pcbnew.FP_DNP) != (r["DNP"] == "yes"):
                bad.append("%s differs from the board" % ref)
        if nt != int(r["Qty top"]) or nb != int(r["Qty bottom"]):
            bad.append("side counts of item %s" % r["Item"])
    R.check("bom", "BOM: every line agrees with the board (value, footprint, manufacturer, MPN, DNP, quantity, side counts)", not bad, "; ".join(bad[:5]) or "%d lines" % len(bom_rows))
    R.check("bom", "BOM: every BOM part of the board is listed exactly once; nothing else is", sorted(seen) == sorted(onboard),
            "%d references in %d lines; board %d (%d top, %d bottom)" % (len(seen), len(bom_rows), len(onboard), sum(1 for f in onboard.values() if not f.IsFlipped()),
                                                                           sum(1 for f in onboard.values() if f.IsFlipped())))
    sb = spec["bom_summary"]
    R.check("bom", "BOM: lines and quantities equal design/netlist.json (bom_summary)",
            len(sb) == len(bom_rows) and sorted((b_["mpn"], b_["qty"]) for b_ in sb) == sorted((r["MPN"], int(r["Quantity"])) for r in bom_rows),
            "%d lines, %d parts, %d DNP" % (len(bom_rows), sum(int(r["Quantity"]) for r in bom_rows), sum(1 for r in bom_rows if r["DNP"] == "yes")))
    R.check("bom", "position files and BOM list the same parts", sorted(seen) == sorted(placed), "%d / %d" % (len(seen), len(placed)))
    per = os.path.join(adir, "%s-bom-per-part.csv" % PROJECT)
    if os.path.exists(per):
        rows = list(csv.DictReader(open(per)))
        R.check("bom", "per-part list: one row per BOM part with its face", sorted(r["Reference"] for r in rows) == sorted(onboard)
                and all((r["Side"] == "bottom") == onboard[r["Reference"]].IsFlipped() for r in rows if r["Reference"] in onboard), "%d rows" % len(rows))
    res["bom"] = {"lines": len(bom_rows), "parts": len(seen), "top": sum(1 for f in onboard.values() if not f.IsFlipped()),
                  "bottom": sum(1 for f in onboard.values() if f.IsFlipped())}

    # ------------------------------------------------------------------ KiCad's own DRC
    if drc:
        dr = run_drc(board_path, os.path.join(out, "drc.json"))
        res["drc"] = dr
        only_gap = all(k.startswith("warning:diff_pair_gap_out_of_range") for k in dr["by_type"])
        R.check("drc", "kicad-cli DRC, all severities, with schematic parity: 0 errors, 0 unconnected, 0 parity issues; the only warnings are pair gaps where legs part",
                dr["errors"] == 0 and dr["unconnected"] == 0 and dr["schematic_parity"] == 0 and only_gap,
                "KiCad %s: %d errors, %d warnings (%s), %d unconnected, %d parity issues" % (dr["kicad_version"], dr["errors"], dr["warnings"],
                                                                                          ", ".join("%s x %d" % (k.split(":")[1], v) for k, v in dr["by_type"].items()), dr["unconnected"], dr["schematic_parity"]))
    R.check("board", "the board file is unchanged by the verification", sha256(board_path) == res["board_sha256"], res["board_sha256"])

    res["checks"] = R.rows
    res["passed"] = len(R.rows) - len(R.failed())
    res["failed"] = len(R.failed())
    with open(os.path.join(out, "report.json"), "w") as fh:
        json.dump(res, fh, indent=1)
    with open(os.path.join(out, "report.txt"), "w") as fh:
        for r in R.rows:
            fh.write("[%s] %-8s %s%s\n" % ("PASS" if r["ok"] else "FAIL", r["group"], r["check"], (": " + r["detail"]) if r["detail"] else ""))
        fh.write("%d checks, %d failed\n" % (len(R.rows), len(R.failed())))
    print("[check_fab] %d checks, %d failed" % (len(R.rows), len(R.failed())))
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--fab", default=os.path.join(ROOT, "fab"))
    ap.add_argument("--out", default=None, help="directory for report.json, report.txt and the Gerber images (default <fab>/verify)")
    ap.add_argument("--board", default=C.BOARD)
    ap.add_argument("--no-drc", action="store_true")
    a = ap.parse_args()
    res = verify(a.fab, a.out or os.path.join(a.fab, "verify"), a.board, drc=not a.no_drc)
    sys.exit(1 if res["failed"] else 0)


if __name__ == "__main__":
    main()

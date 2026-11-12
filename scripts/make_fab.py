#!/usr/bin/env python3
"""Fabrication and assembly package of the board:  make_fab.py [--out fab] [--no-verify] [--no-step]

Everything is exported from lpddr5-testbed.kicad_pcb / the schematic as they stand (run scripts/pcb/build.sh first); the board file is
only read (its SHA-256 is taken before and after and written to fab/README.md).  No fab vendor is targeted: plain Gerber X2 + Excellon.

  fab/gerber/     Gerber X2: six copper layers, both masks, both pastes, both legends, the profile, the fabrication notes (User.Comments),
                  the mechanical drawing layer (User.Drawings), both assembly (Fab) layers; the Gerber job file
  fab/drill/      Excellon, plated and non-plated holes in separate files, drill map (PDF), drill report; the filled-and-capped vias as a
                  drill file and as a list; gerber_x2/: the same holes as Gerber X2 drill files with KiCad's filling / capping layers
  fab/netlist/    IPC-D-356 bare-board netlist
  fab/assembly/   pick-and-place per face, fiducials, BOM (grouped and per part), assembly drawings
  fab/drawings/   fabrication drawing (outline with dimensions, drill map and table, stackup, impedance, filled vias, notes), schematic
  fab/3d/         STEP model of the assembled card
  fab/verify/     written by scripts/check_fab.py: report and images of the Gerbers read back
  fab/README.md   every file, the order parameters, the conventions, the verification and DRC result

The origin of every coordinate file is the drill / place origin of the board = the lower-left corner of the card seen from the front
(pin-1 end of the finger edge), x to the right, y up, millimetres.
"""
import argparse
import csv
import datetime
import json
import math
import os
import shutil
import subprocess
import sys

import cairo
import gi
gi.require_version("Rsvg", "2.0")
from gi.repository import Rsvg      # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "pcb"))
import common as C          # noqa: E402
import pcbnew               # noqa: E402
import render as RENDER     # noqa: E402  (SVG export of single layers, recolouring)
import check_fab as V       # noqa: E402

PROJECT = C.PROJECT
SCH = os.path.join(ROOT, PROJECT + ".kicad_sch")
GERBER_LAYERS = ["F.Cu", "In1.Cu", "In2.Cu", "In3.Cu", "In4.Cu", "B.Cu", "F.Mask", "B.Mask", "F.Paste", "B.Paste",
                 "F.Silkscreen", "B.Silkscreen", "Edge.Cuts", "User.Comments", "User.Drawings", "F.Fab", "B.Fab"]
SUBDIRS = ["gerber", "drill", "netlist", "assembly", "drawings", "3d", "verify"]
MM = 72.0 / 25.4
A3 = (420.0, 297.0)


def kicad(*args):
    r = subprocess.run(["kicad-cli"] + list(args), cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if r.returncode != 0:
        sys.stderr.write(r.stdout + r.stderr)
        raise RuntimeError("kicad-cli %s failed" % " ".join(args[:3]))
    return r.stdout


def log(msg):
    print("[make_fab] " + msg)
    sys.stdout.flush()


# ====================================================================================================== data files
def export_gerbers(out):
    kicad("pcb", "export", "gerbers", "-o", out + os.sep, "--layers", ",".join(GERBER_LAYERS), "--subtract-soldermask",
          "--use-drill-file-origin", "--no-protel-ext", "--precision", "6", C.BOARD)


def export_drill(out, B):
    kicad("pcb", "export", "drill", "-o", out + os.sep, "--format", "excellon", "--drill-origin", "plot", "--excellon-units", "mm",
          "--excellon-zeros-format", "decimal", "--excellon-separate-th", "--generate-map", "--map-format", "pdf",
          "--generate-report", "--report-path", os.path.join(out, "%s-drill_report.txt" % PROJECT), C.BOARD)
    x2 = os.path.join(out, "gerber_x2")
    os.makedirs(x2, exist_ok=True)
    kicad("pcb", "export", "drill", "-o", x2 + os.sep, "--format", "gerber", "--drill-origin", "plot", "--generate-tenting", C.BOARD)
    fixed = []
    for f in sorted(os.listdir(x2)):          # KiCad 10.0.6 writes "%TF,FileFunction" (a comma) in the filling / capping files: malformed X2
        p = os.path.join(x2, f)
        s = open(p).read()
        if "%TF,FileFunction" in s:
            open(p, "w").write(s.replace("%TF,FileFunction", "%TF.FileFunction"))
            fixed.append(f)
    # the filled-and-capped vias as their own Excellon file and as a list with the reason of each
    vias = [v for v in B.vias() if v["filled"] and v["capped"]]
    pads = []
    for fp in B.b.GetFootprints():
        for p in fp.Pads():
            if p.GetNumber() != "" and fp.GetReference() != "J1":
                pads.append((fp.GetReference(), p.GetNumber(), p))
    rows = []
    for v in sorted(vias, key=lambda v: (v["drill"], round(v["x"], 4), round(v["y"], 4))):
        pos = v["item"].GetPosition()
        hit = sorted("%s.%s" % (r, n) for r, n, p in pads if p.HitTest(pos))
        where = "in the land of " + " and ".join(hit) if hit else "command divider node via (0201 lands 0.05-0.10 mm from its pad)"
        rows.append({"x_mm": "%.3f" % v["x"], "y_mm": "%.3f" % v["y"], "drill_mm": "%.2f" % v["drill"], "pad_mm": "%.2f" % v["pad"],
                     "net": v["net"], "where": where})
    with open(os.path.join(out, "filled_capped_vias.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["x_mm", "y_mm", "drill_mm", "pad_mm", "net", "where"])
        w.writeheader()
        w.writerows(rows)
    tools = sorted({r["drill_mm"] for r in rows})
    with open(os.path.join(out, "%s-filled_capped.drl" % PROJECT), "w") as fh:
        fh.write("M48\n; Filled and capped vias (IPC-4761 type VII): a SUBSET of %s-PTH.drl, written by scripts/make_fab.py from the via attributes of the board\n" % PROJECT)
        fh.write("; FORMAT={-:-/ absolute / metric / decimal}\nFMAT,2\nMETRIC\n")
        for i, t in enumerate(tools):
            fh.write("T%dC%.3f\n" % (i + 1, float(t)))
        fh.write("%\nG90\nG05\n")
        for i, t in enumerate(tools):
            fh.write("T%d\n" % (i + 1))
            for r in rows:
                if r["drill_mm"] == t:
                    fh.write("X%sY%s\n" % (r["x_mm"], r["y_mm"]))
        fh.write("M30\n")
    return rows, fixed


def export_positions(out, B):
    for side, name in (("front", "top"), ("back", "bottom")):
        kicad("pcb", "export", "pos", "--side", side, "--format", "csv", "--units", "mm", "--use-drill-file-origin",
              "-o", os.path.join(out, "%s-%s.pos.csv" % (PROJECT, name)), C.BOARD)
    rows = []
    for fp in sorted(B.b.GetFootprints(), key=lambda f: f.GetReference()):
        if fp.GetReference().startswith("FID"):
            x, y = B.c(fp.GetPosition())
            rows.append({"Ref": fp.GetReference(), "PosX": "%.6f" % x, "PosY": "%.6f" % y, "Side": "bottom" if fp.IsFlipped() else "top",
                         "Copper_mm": "1.0", "Mask_opening_mm": "2.0"})
    with open(os.path.join(out, "%s-fiducials.csv" % PROJECT), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["Ref", "PosX", "PosY", "Side", "Copper_mm", "Mask_opening_mm"])
        w.writeheader()
        w.writerows(rows)


def refkey(r):
    head = r.rstrip("0123456789")
    return (head, int(r[len(head):] or 0))


def export_bom(out, B, spec):
    side = {fp.GetReference(): ("bottom" if fp.IsFlipped() else "top") for fp in B.b.GetFootprints()}
    parts = {p["ref"]: p for p in spec["parts"]}
    lines = sorted(spec["bom_summary"], key=lambda b: refkey(sorted(b["refs"], key=refkey)[0]))
    with open(os.path.join(out, "%s-bom.csv" % PROJECT), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Item", "Quantity", "References", "Value", "Footprint", "Manufacturer", "MPN", "DNP", "Side", "Qty top", "Qty bottom", "Description"])
        for i, b in enumerate(lines):
            refs = sorted(b["refs"], key=refkey)
            nt = sum(1 for r in refs if side[r] == "top")
            nb = len(refs) - nt
            desc = parts[refs[0]].get("description", "")
            desc = desc.split(", ", 1)[1] if len(refs) > 1 and ", " in desc else desc      # the part, not the role of one reference
            w.writerow([i + 1, b["qty"], " ".join(refs), b["value"], b["footprint"], b["manufacturer"], b["mpn"], "yes" if b["dnp"] else "no",
                        "top" if nb == 0 else "bottom" if nt == 0 else "top + bottom", nt, nb, desc])
    with open(os.path.join(out, "%s-bom-per-part.csv" % PROJECT), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Reference", "Value", "Footprint", "Manufacturer", "MPN", "DNP", "Side", "Description"])
        for p in sorted(spec["parts"], key=lambda p: refkey(p["ref"])):
            if p.get("in_bom"):
                w.writerow([p["ref"], p["value"], p["footprint"], p["manufacturer"], p["mpn"], "yes" if p["dnp"] else "no", side[p["ref"]], p.get("description", "")])
    return lines


# ====================================================================================================== drawings (cairo, millimetres)
class Page:
    """One PDF page in millimetres, y down."""

    def __init__(self, ctx, title, sheet, nsheets, spec, sha):
        self.ctx = ctx
        ctx.set_source_rgb(0, 0, 0)
        ctx.set_line_width(0.35)
        ctx.rectangle(8, 8, A3[0] - 16, A3[1] - 16)
        ctx.stroke()
        # title block
        x0, y0, w, h = A3[0] - 8 - 150, A3[1] - 8 - 22, 150, 22
        ctx.rectangle(x0, y0, w, h)
        ctx.stroke()
        self.text(x0 + 2, y0 + 5.5, "LPDDR5-CMD-TESTBED  ZCU104   R%s" % spec["meta"]["revision"], 3.6, bold=True)
        self.text(x0 + 2, y0 + 10.5, title, 3.0, bold=True)
        self.text(x0 + 2, y0 + 15.0, "%s   sheet %d of %d   dimensions in mm   do not scale" % (PROJECT + ".kicad_pcb", sheet, nsheets), 2.2)
        self.text(x0 + 2, y0 + 19.2, "board file SHA-256 %s" % sha[:32] + "...", 2.0, mono=True)

    def text(self, x, y, s, size=2.5, bold=False, mono=False, align="l", colour=(0, 0, 0), angle=0.0):
        ctx = self.ctx
        ctx.save()
        ctx.select_font_face("DejaVu Sans Mono" if mono else "DejaVu Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD if bold else cairo.FONT_WEIGHT_NORMAL)
        ctx.set_font_size(size)
        ctx.set_source_rgb(*colour)
        ext = ctx.text_extents(s)
        ctx.translate(x, y)
        ctx.rotate(angle)
        dx = 0 if align == "l" else -ext.x_advance if align == "r" else -ext.x_advance / 2
        ctx.move_to(dx, 0)
        ctx.show_text(s)
        ctx.restore()
        return ext.x_advance

    def table(self, x, y, cols, rows, size=2.4, row_h=4.4, head=True, mono=False):
        """cols = [(title, width, align)]; returns the y below the table."""
        ctx = self.ctx
        w = sum(c[1] for c in cols)
        n = len(rows) + (1 if head else 0)
        ctx.set_line_width(0.25)
        ctx.set_source_rgb(0, 0, 0)
        if head:
            ctx.set_source_rgb(0.90, 0.90, 0.90)
            ctx.rectangle(x, y, w, row_h)
            ctx.fill()
            ctx.set_source_rgb(0, 0, 0)
        ctx.rectangle(x, y, w, n * row_h)
        for i in range(1, n):
            ctx.move_to(x, y + i * row_h)
            ctx.line_to(x + w, y + i * row_h)
        cx = x
        for c in cols[:-1]:
            cx += c[1]
            ctx.move_to(cx, y)
            ctx.line_to(cx, y + n * row_h)
        ctx.stroke()
        for r, row in enumerate(([[c[0] for c in cols]] if head else []) + rows):
            cx = x
            for c, cell in zip(cols, row):
                al = c[2] if not (head and r == 0) else "l"
                tx = cx + 1.2 if al == "l" else cx + c[1] - 1.2 if al == "r" else cx + c[1] / 2
                self.text(tx, y + r * row_h + row_h * 0.72, str(cell), size, bold=(head and r == 0), align=al, mono=mono and not (head and r == 0))
                cx += c[1]
        return y + n * row_h

    def arrow(self, x, y, ang, size=1.6):
        ctx = self.ctx
        ctx.save()
        ctx.translate(x, y)
        ctx.rotate(ang)
        ctx.move_to(0, 0)
        ctx.line_to(size, size * 0.28)
        ctx.line_to(size, -size * 0.28)
        ctx.close_path()
        ctx.fill()
        ctx.restore()

    def dim_h(self, xa, xb, y_obj, y_dim, label, size=2.5):
        """Horizontal dimension between the page x positions xa, xb; extension lines from y_obj to y_dim."""
        ctx = self.ctx
        ctx.set_line_width(0.18)
        ctx.set_source_rgb(0, 0, 0)
        d = 1.0 if y_dim > y_obj else -1.0
        for x in (xa, xb):
            ctx.move_to(x, y_obj + d * 0.8)
            ctx.line_to(x, y_dim + d * 1.2)
        ctx.move_to(xa, y_dim)
        ctx.line_to(xb, y_dim)
        ctx.stroke()
        self.arrow(xa, y_dim, 0.0)
        self.arrow(xb, y_dim, math.pi)
        if abs(xb - xa) > len(label) * size * 0.62 + 4:
            self.text((xa + xb) / 2, y_dim - 0.8, label, size, align="c")
        else:
            self.text(max(xa, xb) + 1.5, y_dim + size * 0.36, label, size)

    def dim_v(self, ya, yb, x_obj, x_dim, label, size=2.5):
        ctx = self.ctx
        ctx.set_line_width(0.18)
        ctx.set_source_rgb(0, 0, 0)
        d = 1.0 if x_dim > x_obj else -1.0
        for y in (ya, yb):
            ctx.move_to(x_obj + d * 0.8, y)
            ctx.line_to(x_dim + d * 1.2, y)
        ctx.move_to(x_dim, ya)
        ctx.line_to(x_dim, yb)
        ctx.stroke()
        self.arrow(x_dim, ya, math.pi / 2 if yb > ya else -math.pi / 2)
        self.arrow(x_dim, yb, -math.pi / 2 if yb > ya else math.pi / 2)
        self.text(x_dim - 0.8, (ya + yb) / 2, label, size, align="c", angle=-math.pi / 2)

    def leader(self, x, y, tx, ty, label, size=2.4, align="l"):
        ctx = self.ctx
        ctx.set_line_width(0.18)
        ctx.set_source_rgb(0, 0, 0)
        ctx.move_to(x, y)
        ctx.line_to(tx, ty)
        ctx.stroke()
        self.arrow(x, y, math.atan2(ty - y, tx - x), 1.4)
        self.text(tx + (0.8 if align == "l" else -0.8), ty + size * 0.36, label, size, align=align)


def outline_path(ctx, poly, tf):
    for k, (x, y) in enumerate(poly.exterior.coords):
        (ctx.move_to if k == 0 else ctx.line_to)(*tf(x, y))
    ctx.close_path()


def drill_symbol(ctx, kind, x, y, s):
    """kind: vip (0.15 filled, solid dot), fill20 (0.20 filled, solid square), plain (0.20 plain, open circle), npth (cross in a circle)."""
    ctx.new_path()
    if kind == "vip":
        ctx.arc(x, y, s * 0.42, 0, 2 * math.pi)
        ctx.fill()
    elif kind == "fill20":
        ctx.rectangle(x - s * 0.5, y - s * 0.5, s, s)
        ctx.fill()
    elif kind == "plain":
        ctx.set_line_width(0.16)
        ctx.arc(x, y, s * 0.5, 0, 2 * math.pi)
        ctx.stroke()
    else:
        ctx.set_line_width(0.25)
        ctx.arc(x, y, s, 0, 2 * math.pi)
        ctx.move_to(x - s * 1.5, y)
        ctx.line_to(x + s * 1.5, y)
        ctx.move_to(x, y - s * 1.5)
        ctx.line_to(x, y + s * 1.5)
        ctx.stroke()


def fab_drawing(path, B, spec, sha, filled_rows, coverage):
    surf = cairo.PDFSurface(path, A3[0] * MM, A3[1] * MM)
    ctx = cairo.Context(surf)
    ctx.scale(MM, MM)
    st, fpl = spec["stackup"], spec["floorplan"]
    ol = fpl["outline"]
    W, H = ol["width"], ol["height"]
    poly = C.board_polygon(B.b)[0]
    vias = B.vias()
    holes = B.holes()
    nsheets = 3

    # ------------------------------------------------------------------ sheet 1: outline, dimensions, drill map
    pg = Page(ctx, "FABRICATION DRAWING: outline, drill", 1, nsheets, spec, sha)
    s = 4.4
    X0, Y0 = 62.0, 200.0

    def tf(x, y):
        return (X0 + s * x, Y0 - s * y)

    def balloon(letter, bx, by_, tx=None, ty_=None):
        r = 2.6
        ctx.set_source_rgb(0, 0, 0)
        ctx.set_line_width(0.2)
        if tx is not None:
            a_ = math.atan2(ty_ - by_, tx - bx)
            ctx.move_to(bx + r * math.cos(a_), by_ + r * math.sin(a_))
            ctx.line_to(tx, ty_)
            ctx.stroke()
            pg.arrow(tx, ty_, a_ + math.pi, 1.5)
        ctx.new_path()
        ctx.arc(bx, by_, r, 0, 2 * math.pi)
        ctx.stroke()
        pg.text(bx, by_ + 1.1, letter, 3.0, bold=True, align="c")

    pg.text(14, 17, "OUTLINE AND DRILL MAP   scale %.1f : 1   seen from the FRONT (component side of U1, odd fingers, finger 1 at the left)" % s, 3.4, bold=True)
    pg.text(14, 22.5, "Datum: lower-left corner of the card rectangle = origin of the Gerber, drill, netlist and placement files (x right, y up).  "
            "Tolerances of the DDR4 SO-DIMM module drawing (JEDEC MO-310 as reproduced in references/socket, Foxconn 317-0000-2253 sheet 2).", 2.4)
    kn = ol["key_notch"]
    ln = ol["latch_notches"]
    th = ol["tooling_holes"]
    y0f, y1f = ol["finger_pad_y"]
    f1, f2 = C.finger_xy(1), C.finger_xy(2)
    legend = [("A", "CORNER CHAMFER %s" % ol["corner_chamfer"]),
              ("B", "LATCH NOTCH (2x, both ends): y %.2f .. %.2f, %.2f deep, inner corners R%.2f" % (ln["y_from"], ln["y_to"], ln["depth"], ln["radius"])),
              ("C", "TOOLING HOLE (2x): dia %.2f NON-PLATED at (%.2f, %.2f) and (%.2f, %.2f)" % (th[0]["d"], th[0]["x"], th[0]["y"], th[1]["x"], th[1]["y"])),
              ("D", "KEY SLOT: %.2f +/-0.05 wide (x %.2f .. %.2f), %.2f +/-0.10 deep, full radius R%.2f at its end"
               % (kn["width"], kn["x_centre"] - kn["width"] / 2, kn["x_centre"] + kn["width"] / 2, kn["depth"], kn["width"] / 2)),
              ("E", "FINGERS: 260 lands, 130 per face, %.2f +/-0.03 wide, pitch %.2f per face, y = %.2f .. %.2f above the edge; finger 1 (front) centre x = %.3f, "
               "finger 2 (back) x = %.3f: the back-side row is offset 0.25 mm" % (ol["finger_width"], ol["finger_pitch_per_side"], y0f, y1f, f1[0], f2[0]))]
    for i, (k, t) in enumerate(legend):
        pg.text(16, 31.5 + 5.2 * i, k, 2.8, bold=True)
        pg.text(22, 31.5 + 5.2 * i, t, 2.4)
    ctx.set_source_rgb(0.80, 0.80, 0.80)
    for n in range(1, 260, 2):                 # front fingers (odd numbers)
        fx = C.finger_xy(n)[0]
        a, b = tf(fx - ol["finger_width"] / 2, y1f), tf(fx + ol["finger_width"] / 2, y0f)
        ctx.rectangle(a[0], a[1], b[0] - a[0], b[1] - a[1])
    ctx.fill()
    ctx.set_source_rgb(0, 0, 0)
    ctx.set_line_width(0.5)
    outline_path(ctx, poly, tf)
    ctx.stroke()
    sym = 1.4
    kinds = {"vip": 0, "fill20": 0, "plain": 0}
    for v in vias:
        k = "vip" if abs(v["drill"] - 0.15) < 1e-6 else "fill20" if v["filled"] else "plain"
        kinds[k] += 1
        drill_symbol(ctx, k, *tf(v["x"], v["y"]), sym if k != "vip" else sym * 1.1)
    for x, y, d, pl in holes:
        drill_symbol(ctx, "npth", *tf(x, y), d / 2 * s)
    # dimensions
    by, ty, lx, rx = tf(0, 0)[1], tf(0, H)[1], tf(0, 0)[0], tf(W, 0)[0]
    pg.dim_h(lx, tf(th[0]["x"], 0)[0], by, by + 9, "%.2f" % th[0]["x"])
    pg.dim_h(lx, tf(kn["x_centre"], 0)[0], by, by + 16, "%.2f to the centre of the key slot" % kn["x_centre"])
    pg.dim_h(lx, tf(th[1]["x"], 0)[0], by, by + 23, "%.2f" % th[1]["x"])
    pg.dim_h(lx, rx, by, by + 30, "%.2f +/-0.15" % W)
    pg.dim_v(by, ty, rx, rx + 24, "%.2f +/-0.15" % H)
    pg.dim_v(by, tf(0, ln["y_from"])[1], rx, rx + 10, "%.2f +/-0.10" % ln["y_from"])
    pg.dim_v(tf(0, ln["y_from"])[1], tf(0, ln["y_to"])[1], rx, rx + 10, "%.2f +/-0.10" % (ln["y_to"] - ln["y_from"]))
    pg.dim_v(by, tf(0, th[0]["y"])[1], lx, lx - 10, "%.2f" % th[0]["y"])
    pg.dim_h(tf(W - ln["depth"], 0)[0], rx, tf(0, ln["y_to"])[1], ty - 6, "%.2f" % ln["depth"])
    balloon("A", lx - 8, ty - 6, *tf(0.3, H - 0.3))
    balloon("B", lx - 10, tf(0, 20.0)[1], *tf(ln["depth"], 20.0))
    balloon("C", lx - 10, tf(0, 11.0)[1], *tf(th[0]["x"] - 0.75, th[0]["y"] + 0.5))
    balloon("D", tf(kn["x_centre"], 0)[0] + 9, by + 4.5, *tf(kn["x_centre"] + kn["width"] / 2, 1.2))
    balloon("E", tf(12.0, 0)[0], by + 4.5, *tf(12.425, 0.5))
    # edge detail
    dx, dy = 292.0, 33.0
    pg.text(dx - 8, dy - 5, "DETAIL: FINGER EDGE (section, not to scale)", 2.8, bold=True)
    ctx.set_line_width(0.4)
    tw, tl, ch = 14.0, 40.0, 3.0
    ctx.move_to(dx + tl, dy)
    ctx.line_to(dx + ch, dy)
    ctx.line_to(dx, dy + ch)
    ctx.line_to(dx, dy + tw - ch)
    ctx.line_to(dx + ch, dy + tw)
    ctx.line_to(dx + tl, dy + tw)
    ctx.stroke()
    ctx.set_source_rgb(0.75, 0.6, 0.1)
    ctx.rectangle(dx + ch + 1.5, dy - 0.9, 28, 0.9)
    ctx.rectangle(dx + ch + 1.5, dy + tw, 28, 0.9)
    ctx.fill()
    ctx.set_source_rgb(0, 0, 0)
    pg.dim_v(dy - 0.9, dy + tw + 0.9, dx + tl, dx + tl + 8, "%.2f +/-0.10" % st["thickness_at_fingers_with_hard_gold_mm"], 2.4)
    pg.text(dx + tl + 12, dy + 3.5, "FINISHED THICKNESS measured", 2.3, bold=True)
    pg.text(dx + tl + 12, dy + 7.1, "OVER THE GOLD FINGERS:", 2.3, bold=True)
    pg.text(dx + tl + 12, dy + 10.7, "absolute tolerance, not +/-10 %", 2.3, bold=True)
    pg.text(dx - 8, dy + tw + 6.5, "Chamfer 0.25 x 45 deg on both faces, no deep bevel.  Hard gold over nickel on the lands,", 2.2)
    pg.text(dx - 8, dy + tw + 10.0, "which start <= 0.30 mm from the edge.  No solder mask on the finger strip (y < 2.75 mm).", 2.2)
    # drill table
    ty0 = 240.0
    pg.text(14, ty0 - 2.5, "DRILL TABLE (finished hole sizes; files in fab/drill/)", 2.8, bold=True)
    n15 = kinds["vip"]
    rows = [["solid dot", "0.15", "plated", n15, "0.40", "via-in-pad of U1: FILLED AND CAPPED (IPC-4761 type VII), flush"],
            ["solid square", "0.20", "plated", kinds["fill20"], "0.40 / 0.45", "FILLED AND CAPPED: 10 command node vias (y = 5.85), 8 vias in the exposed pads of U2 / U4 / U5"],
            ["open circle", "0.20", "plated", kinds["plain"], "0.40 / 0.45", "plain via, tented on both faces"],
            ["crossed circle", "%.2f" % th[0]["d"], "NON-plated", len(holes), "-", "tooling holes of the card edge"]]
    pg.table(14, ty0, [("Symbol", 24, "l"), ("Dia mm", 15, "c"), ("Plating", 22, "l"), ("Qty", 12, "r"), ("Pad mm", 22, "c"), ("Use / treatment", 150, "l")], rows)
    pg.text(14, ty0 + 5 * 4.4 + 4.5, "Plated holes %d (%d x 0.15 mm, %d x 0.20 mm), non-plated %d.  Filled and capped %d: listed one by one in fab/drill/filled_capped_vias.csv and %s-filled_capped.drl."
            % (len(vias), n15, len(vias) - n15, len(holes), n15 + kinds["fill20"], PROJECT), 2.3)
    pg.text(14, ty0 + 5 * 4.4 + 8.3, "Through holes only: no blind, buried or micro vias.  Smallest annular ring 0.10 mm (0.40 mm pad on 0.20 mm, 0.35 mm In3 pad on 0.15 mm at U1 balls B3 / C4).", 2.3)
    ctx.show_page()

    # ------------------------------------------------------------------ sheet 2: stackup, impedance, filled vias, copper
    pg = Page(ctx, "FABRICATION DRAWING: stackup, impedance, vias", 2, nsheets, spec, sha)
    pg.text(14, 17, "STACKUP (6 layers, top to bottom)", 3.4, bold=True)
    rows = []
    cu_n = 0
    bars = []
    for lay in st["layers"]:
        if lay["type"] == "copper":
            cu_n += 1
            cov = coverage.get(lay["layer"])
            rows.append(["L%d  %s" % (cu_n, lay["layer"]), "copper", "%.3f" % lay["thickness_mm"], "-",
                         "%s%s" % ("finished 0.040 nominal with plating; " if lay["layer"] in ("F.Cu", "B.Cu") else "",
                                   ("%.0f %% copper" % (100 * cov)) if cov is not None else ""),
                         {"F.Cu": "signal + parts (microstrip over L2), no pour", "In1.Cu": "GND plane", "In2.Cu": "GND plane + 1.2 V island under the VDD fingers",
                          "In3.Cu": "high-speed stripline only", "In4.Cu": "GND plane + VDD2H island under U1", "B.Cu": "signal + parts (microstrip over L5), power pours under U1"}[lay["layer"]]])
            bars.append(("cu", lay["thickness_mm"]))
        elif lay["type"] == "soldermask":
            rows.append([lay["layer"], "LPI solder mask", "%.3f" % lay["thickness_mm"], "%.1f" % lay["er"], "0.020 over copper", "black (green allowed, note 12); removed over the fingers"])
            bars.append(("mask", lay["thickness_mm"]))
        else:
            rows.append(["", "%s %s" % (lay["type"], lay["material"]), "%.3f" % lay["thickness_mm"], "%.2f" % lay["er"], "", lay["role"]])
            bars.append((lay["type"], lay["thickness_mm"]))
    yb = pg.table(34, 21, [("Layer", 26, "l"), ("Material", 36, "l"), ("mm", 14, "r"), ("er", 10, "r"), ("Finished / copper density", 86, "l"), ("Role", 150, "l")], rows, size=2.5, row_h=4.6)
    for i, (kind, _t) in enumerate(bars):          # schematic cross-section beside the table
        y = 21 + 4.6 * (i + 1)
        ctx.set_source_rgb(*{"cu": (0.80, 0.45, 0.15), "mask": (0.1, 0.35, 0.15), "prepreg": (0.88, 0.86, 0.62), "core": (0.80, 0.76, 0.45)}[kind])
        hh = {"cu": 1.6, "mask": 1.0, "prepreg": 3.8, "core": 4.6}[kind]
        ctx.rectangle(14, y + (4.6 - hh) / 2, 17, hh)
        ctx.fill()
    ctx.set_source_rgb(0, 0, 0)
    pg.text(14, yb + 5, "Total %.3f mm with mask, %.3f mm at the bare fingers, %.2f mm with hard gold.  REQUIRED: %s."
            % (st["total_thickness_with_mask_mm"], st["thickness_at_fingers_no_mask_mm"], st["thickness_at_fingers_with_hard_gold_mm"], "finished thickness measured over the gold fingers 1.20 +/-0.10 mm"), 2.5, bold=True)
    pg.text(14, yb + 9, "The two FR4 cores are 0.36 mm (14 mil); the three PR2116 prepregs (0.11 mm) set the impedances and must not be changed.  With cap plating of the filled vias the outer copper may reach 0.055 mm: %.2f mm."
            % st["thickness_at_fingers_with_cap_plating_mm"], 2.3)
    y = yb + 19
    pg.text(14, y - 2.5, "CONTROLLED IMPEDANCE (all +/-10 %; the fab may adjust the widths by +/-0.02 mm; TDR coupons on the panel border, without copper flood beside the lines)", 2.8, bold=True)
    nc = spec["rules"]["netclasses"]
    oc = json.load(open(os.path.join(ROOT, "design", "impedance_results.json"))).get("outer_copper_and_mask_cases", {})
    rows = [["In3.Cu", "L3 / L5 (stripline)", "single", "%.2f" % nc["CMD_SE"]["layers"]["In3.Cu"]["width"], "-", "%.1f" % nc["CMD_SE"]["layers"]["In3.Cu"]["z0_ohm"], "46", "CA0-6, CS0; read DQ0-7"],
            ["In3.Cu", "L3 / L5 (stripline)", "differential", "%.2f" % nc["CMD_CK"]["layers"]["In3.Cu"]["width"], "%.2f" % nc["CMD_CK"]["layers"]["In3.Cu"]["gap"],
             "%.1f" % nc["CMD_CK"]["layers"]["In3.Cu"]["zdiff_ohm"], "85", "CK_P / CK_N; RDQS_P / RDQS_N"],
            ["F.Cu, B.Cu", "L2 / L5 (microstrip)", "single", "%.2f" % nc["HOST_SE"]["layers"]["F.Cu"]["width"], "-", "%.1f" % nc["HOST_SE"]["layers"]["F.Cu"]["z0_ohm"], "46", "finger stubs, read finger tracks"],
            ["F.Cu", "L2 (microstrip)", "single", "%.2f" % nc["WR_DQ"]["layers"]["F.Cu"]["width"], "-", "%.1f" % nc["WR_DQ"]["layers"]["F.Cu"]["z0_ohm"], "52", "DQ0-7 lines from U1 to the dividers"],
            ["F.Cu, B.Cu", "L2 / L5 (microstrip)", "differential", "%.2f" % nc["WR_WCK"]["layers"]["B.Cu"]["width"], "%.2f" % nc["WR_WCK"]["layers"]["B.Cu"]["gap"],
             "%.1f" % nc["WR_WCK"]["layers"]["B.Cu"]["zdiff_ohm"], "92", "WCK pair (B.Cu); last 5.8 mm of RDQS (F.Cu)"]]
    yb = pg.table(14, y, [("Layer", 26, "l"), ("Reference planes", 44, "l"), ("Type", 26, "l"), ("Width mm", 20, "r"), ("Gap mm", 18, "r"), ("Design ohm", 24, "r"), ("Order ohm", 22, "r"), ("Nets", 110, "l")],
                  rows, size=2.5, row_h=4.6)
    pg.text(14, yb + 4.5, "Outer-layer design values are for 0.040 mm finished copper under 0.020 mm of mask, er 4.29 (2-D field solver, design/impedance_results.json).", 2.3)
    y = yb + 15
    pg.text(14, y - 2.5, "VIA TREATMENT", 2.8, bold=True)
    n_vip = sum(1 for r in filled_rows if r["drill_mm"] == "0.15")
    other = [r for r in filled_rows if r["drill_mm"] != "0.15"]
    lines = ["FILLED AND CAPPED, IPC-4761 type VII, cap flush within +/-0.025 mm: %d vias = %d x 0.40 / 0.15 mm in the lands of U1 + %d x 0.20 mm drill listed below."
             % (len(filled_rows), n_vip, len(other)),
             "ALL OTHER VIAS (%d) are plain and tented on both faces.  The 0.20 mm filled holes share their drill size with plain vias: they are identified by position."
             % (len(vias) - len(filled_rows)),
             "UNUSED INNER-LAYER PADS ARE REMOVED IN THE DATA (do not add them back) on every via except the ten command node vias (row y = 5.85 mm).",
             "Hole to copper 0.20 mm is used at its limit beside the RDQS pair inside U1 (0.205 mm) and at its two return vias above fingers 219 / 221 (0.225 mm)."]
    for i, ln_ in enumerate(lines):
        pg.text(14, y + 2 + 4.2 * i, ln_, 2.5)
    y += 2 + 4.2 * len(lines) + 2
    rows = [[r["x_mm"], r["y_mm"], r["drill_mm"], r["pad_mm"], r["net"], r["where"]] for r in other]
    half = (len(rows) + 1) // 2
    cols = [("x mm", 16, "r"), ("y mm", 16, "r"), ("Drill", 12, "r"), ("Pad", 12, "r"), ("Net", 18, "l"), ("Where", 118, "l")]
    pg.table(14, y, cols, rows[:half], size=2.2, row_h=4.0)
    pg.table(14 + 198, y, cols, rows[half:], size=2.2, row_h=4.0)
    ctx.show_page()

    # ------------------------------------------------------------------ sheet 3: the notes of the board, verbatim
    pg = Page(ctx, "FABRICATION DRAWING: notes", 3, nsheets, spec, sha)
    notes = []
    for d in B.b.GetDrawings():
        if d.GetClass() == "PCB_TEXT" and d.GetLayerName() == "User.Comments":
            notes.append((d.GetPosition().y, d.GetText()))
    notes.sort()
    pg.text(14, 17, "FABRICATION NOTES (the text of layer User.Comments of the board file, verbatim; also in fab/gerber/%s-User_Comments.gbr)" % PROJECT, 3.0, bold=True)
    y = 24.0
    for _, t in notes[1:]:
        head = t[:1].isdigit()
        if head:
            y += 1.4
        pg.text(14, y, t, 2.55, mono=True, bold=False)
        y += 4.15
    ctx.show_page()
    surf.finish()


def assembly_drawing(path, B, spec, sha, svgs, face, lines):
    """One A3 sheet per face at 4.5 : 1: outline, Fab outlines with references, legend, paste lands; the back as seen from the back."""
    surf = cairo.PDFSurface(path, A3[0] * MM, A3[1] * MM)
    ctx = cairo.Context(surf)
    ctx.scale(MM, MM)
    front = face == "F"
    pg = Page(ctx, "ASSEMBLY DRAWING: %s" % ("TOP (front)" if front else "BOTTOM (back), seen from the back"), 1, 1, spec, sha)
    s = 4.5
    W, H = spec["floorplan"]["outline"]["width"], spec["floorplan"]["outline"]["height"]
    X0, Y0 = 52.0, 30.0
    ox, oy = C.origin()
    m = 1.0
    view = (ox - m, oy - H - m, W + 2 * m, H + 2 * m)
    lay = [("F.Mask" if front else "B.Mask", (0.80, 0.80, 0.80)), ("F.SilkS" if front else "B.SilkS", (0.55, 0.60, 0.75)),
           ("F.Fab" if front else "B.Fab", (0.0, 0.0, 0.0)), ("Edge.Cuts", (0.0, 0.0, 0.0))]
    for name, col in lay:
        h = RENDER.handle(svgs[name], col, view, hole=(1, 1, 1))
        rec = cairo.RecordingSurface(cairo.CONTENT_COLOR_ALPHA, None)      # librsvg refuses a mirrored target: record, then replay mirrored
        vp = Rsvg.Rectangle()
        vp.x, vp.y, vp.width, vp.height = 0.0, 0.0, (W + 2 * m) * s, (H + 2 * m) * s
        h.render_document(cairo.Context(rec), vp)
        ctx.save()
        if front:
            ctx.translate(X0 - m * s, Y0 - m * s)
        else:
            ctx.translate(X0 - m * s + vp.width, Y0 - m * s)
            ctx.scale(-1, 1)
        ctx.set_source_surface(rec, 0, 0)
        ctx.paint()
        ctx.restore()
    side = "top" if front else "bottom"
    placed = [fp for fp in B.b.GetFootprints() if fp.IsFlipped() != front and not fp.GetAttributes() & pcbnew.FP_EXCLUDE_FROM_POS_FILES]
    pg.text(14, 17, "%s SIDE, scale %.1f : 1, %d parts to place.  Black: part outlines and references (Fab layer); blue-grey: legend; grey: mask openings (lands)."
            % (side.upper(), s, len(placed)), 3.0, bold=True)
    pg.text(14, 22, ("Pin 1 of the card (finger 1) is at the lower LEFT; U1 ball A1 is marked by the legend corner." if front else
                     "Seen from the back: finger 2 is at the lower RIGHT.  Card coordinates of the placement file are NOT mirrored (x as seen from the front)."), 2.5)
    y = Y0 + (H + m) * s + 10
    pg.text(14, y, "No polarised two-terminal part.  ICs: pin 1 as drawn on the Fab outline (chamfered corner).  No paste on the fingers, the test pads and the fiducials.  Stencil 0.10 mm.  Fiducials: 3 per face (FID1-3 top, FID4-6 bottom).", 2.4)
    y += 4
    rows = []
    sides = {fp.GetReference(): ("bottom" if fp.IsFlipped() else "top") for fp in B.b.GetFootprints()}
    for i, b in enumerate(lines):
        refs = [r for r in sorted(b["refs"], key=refkey) if sides[r] == side]
        if refs:
            rs = " ".join(refs)
            rows.append([i + 1, len(refs), b["value"], b["footprint"].split(":")[-1][:34], b["mpn"], rs if len(rs) < 150 else rs[:146] + " ..."])
    pg.table(14, y, [("Item", 10, "r"), ("Qty", 9, "r"), ("Value", 22, "l"), ("Footprint", 56, "l"), ("MPN", 42, "l"), ("References on this face", 252, "l")],
             rows, size=1.9, row_h=3.25, mono=False)
    ctx.show_page()
    surf.finish()


# ====================================================================================================== README
def human(n):
    return "%d B" % n if n < 1024 else "%.1f kB" % (n / 1024.0) if n < 1 << 20 else "%.2f MB" % (n / float(1 << 20))


DESCR = [
    ("gerber/%P-F_Cu.gbr", "copper layer 1 (top): parts, the DQ comb, finger stubs, 1.2 V strip; no pour"),
    ("gerber/%P-In1_Cu.gbr", "copper layer 2: GND plane"),
    ("gerber/%P-In2_Cu.gbr", "copper layer 3: GND plane with the 1.2 V island under the VDD fingers"),
    ("gerber/%P-In3_Cu.gbr", "copper layer 4: high-speed stripline (command bundle, read branches, RDQS)"),
    ("gerber/%P-In4_Cu.gbr", "copper layer 5: GND plane with the VDD2H island under U1"),
    ("gerber/%P-B_Cu.gbr", "copper layer 6 (bottom): WCK pair, read finger tracks, decoupling, U3 / U4, power pours under U1"),
    ("gerber/%P-F_Mask.gbr", "solder mask top (negative: drawn = opening); the finger strip is open up to y = 2.75 mm"),
    ("gerber/%P-B_Mask.gbr", "solder mask bottom"),
    ("gerber/%P-F_Paste.gbr", "paste top (stencil)"),
    ("gerber/%P-B_Paste.gbr", "paste bottom (stencil)"),
    ("gerber/%P-F_Silkscreen.gbr", "legend top (mask openings subtracted)"),
    ("gerber/%P-B_Silkscreen.gbr", "legend bottom (mask openings subtracted)"),
    ("gerber/%P-Edge_Cuts.gbr", "board profile: 69.6 x 30.0 mm with key slot, two latch notches, four corner chamfers"),
    ("gerber/%P-User_Comments.gbr", "fabrication notes 1-13 as drawn on the board (the same text is sheet 3 of the fabrication drawing)"),
    ("gerber/%P-User_Drawings.gbr", "mechanical layer of the card-edge footprint: component keep-out and height zones, finger numbers"),
    ("gerber/%P-F_Fab.gbr", "assembly layer top: part outlines and references"),
    ("gerber/%P-B_Fab.gbr", "assembly layer bottom"),
    ("gerber/%P-job.gbrjob", "Gerber job file: layer list, stackup, thickness, finish, design rules"),
    ("drill/%P-PTH.drl", "Excellon, plated holes: every via (0.15 and 0.20 mm)"),
    ("drill/%P-NPTH.drl", "Excellon, non-plated holes: the two 1.80 mm tooling holes"),
    ("drill/%P-PTH-drl_map.pdf", "drill map of the plated holes"),
    ("drill/%P-NPTH-drl_map.pdf", "drill map of the non-plated holes"),
    ("drill/%P-drill_report.txt", "KiCad drill report: tools and hit counts"),
    ("drill/%P-filled_capped.drl", "Excellon subset: the vias to be filled and capped (IPC-4761 type VII)"),
    ("drill/filled_capped_vias.csv", "the same vias as a list: position, drill, pad, net and what each one sits in"),
    ("drill/gerber_x2/%P-PTH-drl.gbr", "the plated holes as a Gerber X2 drill file (same holes as the Excellon file)"),
    ("drill/gerber_x2/%P-NPTH-drl.gbr", "the non-plated holes as a Gerber X2 drill file"),
    ("drill/gerber_x2/%P-filling-front-back.gbr", "KiCad's via-filling layer: the filled vias"),
    ("drill/gerber_x2/%P-capping-front-back.gbr", "KiCad's via-capping layer: the capped vias (the same set)"),
    ("netlist/%P.d356", "IPC-D-356 netlist for the bare-board electrical test (inches x 0.0001, same origin)"),
    ("assembly/%P-top.pos.csv", "pick-and-place, top face"),
    ("assembly/%P-bottom.pos.csv", "pick-and-place, bottom face (x NOT mirrored)"),
    ("assembly/%P-fiducials.csv", "the six card fiducials (not in the placement files)"),
    ("assembly/%P-bom.csv", "bill of materials, one line per part number: item, quantity, references, value, footprint, manufacturer, MPN, DNP, side, quantity per face"),
    ("assembly/%P-bom-per-part.csv", "bill of materials, one row per reference with its face"),
    ("assembly/%P-assembly-top.pdf", "assembly drawing top, 4.5 : 1, with the parts of that face"),
    ("assembly/%P-assembly-bottom.pdf", "assembly drawing bottom, 4.5 : 1, seen from the back"),
    ("drawings/%P-fabrication.pdf", "fabrication drawing, 3 sheets A3: outline with dimensions and drill map; stackup, impedance, via treatment; notes"),
    ("drawings/%P-schematic.pdf", "schematic, 7 sheets"),
    ("3d/%P.step", "STEP model of the card with the parts that have a 3-D model"),
    ("verify/report.txt", "result of scripts/check_fab.py, one line per check"),
    ("verify/report.json", "the same with the measured numbers"),
    ("verify/drc.json", "KiCad DRC report (all severities, schematic parity)"),
]


def write_readme(out, spec, B, sha, res, lines, filled_rows, fixed, step_note, t0):
    st = spec["stackup"]
    ol = spec["floorplan"]["outline"]
    vias = B.vias()
    n15 = sum(1 for v in vias if abs(v["drill"] - 0.15) < 1e-6)
    nfill = len(filled_rows)
    parts = [p for p in spec["parts"] if p.get("in_bom")]
    top = res["bom"]["top"] if res else sum(1 for fp in B.b.GetFootprints() if not fp.IsFlipped() and not fp.GetAttributes() & pcbnew.FP_EXCLUDE_FROM_BOM)
    bot = len(parts) - top
    ver = subprocess.run(["kicad-cli", "--version"], stdout=subprocess.PIPE, text=True).stdout.strip()
    L = []
    w = L.append
    w("# Fabrication and assembly package: LPDDR5-CMD-TESTBED ZCU104 R%s" % spec["meta"]["revision"])
    w("")
    w("Generated by `scripts/make_fab.py` and verified by `scripts/check_fab.py`; do not edit by hand. Regenerate with")
    w("`scripts/pcb/build.sh && python3 scripts/make_fab.py` (or `scripts/pcb/build.sh --fab`).")
    w("")
    w("| | |")
    w("|---|---|")
    w("| Board file | `%s.kicad_pcb` |" % PROJECT)
    w("| SHA-256 of the board file | `%s` |" % sha)
    w("| Generated | %s, KiCad (kicad-cli) %s |" % (t0.strftime("%Y-%m-%d %H:%M:%S %z").strip(), ver))
    if res:
        d = res.get("drc")
        w("| Verification | **%d checks, %d failed** (`verify/report.txt`) |" % (res["passed"] + res["failed"], res["failed"]))
        if d:
            w("| KiCad DRC, all severities, with schematic parity | **%d errors, %d unconnected, %d parity issues**; %d warnings (%s) |"
              % (d["errors"], d["unconnected"], d["schematic_parity"], d["warnings"], ", ".join("%s x %d" % (k.split(":")[1], v) for k, v in d["by_type"].items()) or "none"))
    w("")
    w("No fab vendor is targeted. The data are plain Gerber X2 and Excellon; everything a quotation form asks for is in the next section, and")
    w("everything the Gerbers cannot say is in `drawings/%s-fabrication.pdf` (the notes are also on layer User.Comments)." % PROJECT)
    w("")
    w("## Order parameters")
    w("")
    w("### Bare board")
    w("")
    w("| Parameter | Value |")
    w("|---|---|")
    w("| Size | %.1f x %.1f mm (DDR4 SO-DIMM module outline), one board per unit; must be delivered in a panel (30 mm high) |" % (ol["width"], ol["height"]))
    w("| Layers | 6 |")
    w("| Material | FR-4, Tg >= 170 C |")
    w("| **Finished thickness** | **1.20 +/-0.10 mm measured over the gold fingers** (an absolute tolerance, not +/-10 %%); nominal %.3f mm with mask, %.3f mm at the bare fingers |"
      % (st["total_thickness_with_mask_mm"], st["thickness_at_fingers_no_mask_mm"]))
    w("| Stackup | specified, see below; the three 0.11 mm prepregs set the impedances |")
    w("| Copper | outer 0.035 mm (0.040 mm finished nominal), inner 0.018 mm |")
    w("| Minimum track / gap | 0.09 mm track (inside the BGA field of U1 only; 0.10 mm elsewhere) / 0.10 mm gap |")
    w("| Holes | %d plated (%d x 0.15 mm, %d x 0.20 mm), %d non-plated (1.80 mm); through holes only |" % (len(vias), n15, len(vias) - n15, len(B.holes())))
    w("| Minimum annular ring | 0.10 mm (IPC-6012 class 2; class 3 is not attainable) |")
    w("| Via-in-pad | **%d vias filled and capped, IPC-4761 type VII** (%d x 0.15 mm in the lands of U1, %d x 0.20 mm: `drill/filled_capped_vias.csv`); all other vias tented |"
      % (nfill, n15, nfill - n15))
    w("| Unused inner-layer pads | removed in the data on all vias except the ten command node vias: do not add them back |")
    w("| Surface finish | %s |" % st["copper_finish"].replace("on the fingers", "on the 260 finger lands of J1").replace("ENIG elsewhere", "ENIG on all other exposed copper"))
    w("| Edge connector | 260 gold fingers, %s; no plating tie bars left on the card |" % st["edge"])
    w("| Controlled impedance | yes, five geometries, all +/-10 %, table below; TDR coupons on the panel border |")
    w("| Solder mask | LPI, both faces, black (green allowed if the 0.08 mm dam of the 0201 lands cannot be held in black) |")
    w("| Legend | white, both faces |")
    w("| Copper thieving | **none inside the card outline** (outer layers are 16-17 % copper by design; balance with robber bars on the panel border) |")
    w("| Acceptance | IPC-6012 class 2; 100 %% netlist test against `netlist/%s.d356`; bow and twist <= 0.5 %%, flatness over the finger row <= 0.15 mm |" % PROJECT)
    w("| Panel | routed outline, break-off tabs on the top edge or on the sides above the latch notches only (y > 22 mm), no V-score; three fiducials per face on the rails |")
    w("")
    w("Stackup (top to bottom):")
    w("")
    w("| Layer | Material | Thickness mm | er |")
    w("|---|---|---|---|")
    for lay in st["layers"]:
        w("| %s | %s | %.3f | %s |" % (lay["layer"] if lay["type"] in ("copper", "soldermask") else "", lay["material"] if lay["type"] in ("copper", "soldermask") else "%s %s" % (lay["type"], lay["material"]),
                                    lay["thickness_mm"], ("%.2f" % lay["er"]) if lay["er"] else ""))
    w("")
    nc = spec["rules"]["netclasses"]
    w("Impedance classes (design values from the 2-D field solver, `design/impedance_results.json`; outer layers for 0.040 mm copper under 0.020 mm mask):")
    w("")
    w("| Layer | Type | Width / gap mm | Design | Order |")
    w("|---|---|---|---|---|")
    w("| In3.Cu (stripline between In2 and In4) | single | %.2f | %.1f ohm | 46 ohm |" % (nc["CMD_SE"]["layers"]["In3.Cu"]["width"], nc["CMD_SE"]["layers"]["In3.Cu"]["z0_ohm"]))
    w("| In3.Cu | differential | %.2f / %.2f | %.1f ohm | 85 ohm |" % (nc["CMD_CK"]["layers"]["In3.Cu"]["width"], nc["CMD_CK"]["layers"]["In3.Cu"]["gap"], nc["CMD_CK"]["layers"]["In3.Cu"]["zdiff_ohm"]))
    w("| F.Cu / B.Cu (microstrip over In1 / In4) | single | %.2f | %.1f ohm | 46 ohm |" % (nc["HOST_SE"]["layers"]["F.Cu"]["width"], nc["HOST_SE"]["layers"]["F.Cu"]["z0_ohm"]))
    w("| F.Cu | single | %.2f | %.1f ohm | 52 ohm |" % (nc["WR_DQ"]["layers"]["F.Cu"]["width"], nc["WR_DQ"]["layers"]["F.Cu"]["z0_ohm"]))
    w("| F.Cu / B.Cu | differential | %.2f / %.2f | %.1f ohm | 92 ohm |" % (nc["WR_WCK"]["layers"]["B.Cu"]["width"], nc["WR_WCK"]["layers"]["B.Cu"]["gap"], nc["WR_WCK"]["layers"]["B.Cu"]["zdiff_ohm"]))
    w("")
    w("### Assembly")
    w("")
    w("| Parameter | Value |")
    w("|---|---|")
    w("| Parts | %d placements, %d BOM lines, none DNP: %d on the top face, %d on the bottom face (double-sided reflow) |" % (len(parts), len(lines), top, bot))
    w("| Smallest part | 0201 (0603 metric): %d of them |" % sum(1 for p in parts if "0201" in p["footprint"]))
    w("| Fine pitch | U1: 315-ball FBGA, 0.80 x 0.70 mm pitch, lands 0.41 mm, via-in-pad filled and capped; U2 VSON-10 0.5 mm; U5 WSON-8 0.5 mm; U4 MLF-6 0.65 mm |")
    w("| Tallest part | 1.10 mm on either face (module limit 1.20 mm per face); see the open item on the body height of U1 in `design/DESIGN.md` section 8 |")
    w("| Fiducials | three per face on the card (`assembly/%s-fiducials.csv`): 1.0 mm copper dot in a 2.0 mm mask opening |" % PROJECT)
    w("| Stencil | 0.10 mm; no paste on the fingers, the test pads and the fiducials |")
    w("| Not placed | J1 (the card edge), TP1-TP5 (test pads), FID1-FID6 |")
    w("")
    w("## Conventions of the coordinate files")
    w("")
    w("- **Origin**: the drill / place origin of the board = the lower-left corner of the card rectangle seen from the front (the finger-1 end of the")
    w("  finger edge). **x to the right, y up, millimetres** in the Gerber, Excellon and placement files; the IPC-D-356 file uses 0.0001 inch, same origin.")
    w("  The profile therefore spans x 0 .. %.1f, y 0 .. %.1f." % (ol["width"], ol["height"]))
    w("- **Gerber**: X2 attributes, format 4.6, aperture macros enabled, one file per layer, KiCad file names (`.gbr`); the mask files are negative")
    w("  (drawn = opening); the legend files have the mask openings subtracted.")
    w("- **Excellon**: metric, decimal coordinates, plated and non-plated holes in separate files, finished hole sizes.")
    w("- **Placement files** (`Ref, Val, Package, PosX, PosY, Rot, Side`): PosX / PosY are the footprint origin (the centre of the part) in the frame above.")
    w("  **The bottom face is NOT mirrored**: x is as seen from the front, through the card. **Rot** is the KiCad footprint orientation: degrees")
    w("  counter-clockwise **seen from the front (top) of the card for both faces**; at Rot = 0 a top-side part has its library orientation (pin 1 as in the")
    w("  KiCad footprint), and a bottom-side part is the library footprint mirrored top-to-bottom (KiCad's flip). Seen from the back, a bottom-side part is")
    w("  therefore rotated by (180 - Rot) degrees counter-clockwise from its library orientation. `check_fab.py` recomputes every numbered pad of every part")
    w("  from the row and this convention and compares it with the board.")
    w("")
    w("## Files")
    w("")
    w("| File | Size | Content |")
    w("|---|---|---|")
    listed = set()
    for rel, desc in DESCR:
        rel = rel.replace("%P", PROJECT)
        p = os.path.join(out, rel)
        if os.path.exists(p):
            w("| `%s` | %s | %s |" % (rel, human(os.path.getsize(p)), desc))
            listed.add(rel)
    imgs = sorted(f for f in os.listdir(os.path.join(out, "verify")) if f.endswith(".png")) if os.path.isdir(os.path.join(out, "verify")) else []
    if imgs:
        w("| `verify/gerber_*.png` | %d images | the Gerber files read back and rasterised by `check_fab.py` (30 px/mm): six copper layers with the drill hits, masks, pastes, legends, profile |" % len(imgs))
        listed.update("verify/" + f for f in imgs)
    for dp, _, fs in os.walk(out):
        for f in sorted(fs):
            rel = os.path.relpath(os.path.join(dp, f), out)
            if rel not in listed and rel != "README.md":
                w("| `%s` | %s | |" % (rel, human(os.path.getsize(os.path.join(out, rel)))))
    w("")
    w("Notes on single files:")
    w("")
    w("- %s" % step_note)
    if fixed:
        w("- `drill/gerber_x2/`: KiCad 10.0.6 writes the file-function attribute of its filling and capping layers as `%TF,FileFunction` (a comma instead of")
        w("  the dot); `make_fab.py` corrects that one character in %s. Nothing else in any exported file is altered." % " and ".join("`%s`" % f for f in fixed))
    w("- The filled-and-capped set is given three ways (Excellon subset, CSV list, KiCad's X2 filling / capping layers) and in fabrication note 6; they are")
    w("  checked against each other and against the via attributes of the board file.")
    w("- `.gitignore` of the repository ignores `*.csv`: the BOM, placement and via-list files of this directory are hidden from git unless `!fab/**/*.csv` is added.")
    w("")
    if res:
        w("## Verification (`scripts/check_fab.py`)")
        w("")
        w("The files are read back with parsers written for this purpose (Gerber with aperture macros, regions, arcs and polarity; Excellon; IPC-D-356; CSV)")
        w("and compared with the board as the pcbnew API reports its items, not with another plot. Copper layers are rasterised at %.0f px/mm from the Gerber"
          % res["pixels_per_mm"])
        w("file and, separately, from the tracks, arcs, via pads per layer, pad polygons and zone fills of the board; the two images must agree to one pixel.")
        w("")
        w("| Group | Check | Result | Measured |")
        w("|---|---|---|---|")
        for r in res["checks"]:
            w("| %s | %s | %s | %s |" % (r["group"], r["check"].replace("|", "/"), "pass" if r["ok"] else "**FAIL**", r["detail"].replace("|", "/")))
        w("")
        w("Copper per layer, measured on the Gerber images:")
        w("")
        w("| Layer | Flashes / draws / regions | Copper, % of the card | Nearest copper to the edge mm | Pixels that differ from the board items beyond one pixel |")
        w("|---|---|---|---|---|")
        for n, c in res["copper"].items():
            g = c["gerber"]
            w("| %s | %d / %d / %d | %.1f | %.2f | %d |" % (n, g["flashes"], g["draws"], g["regions"], 100 * c["copper_fraction_of_card"], c["min_distance_to_edge_mm"],
                                                           c["gerber_only_beyond_1px"] + c["board_only_beyond_1px"]))
        w("")
    with open(os.path.join(out, "README.md"), "w") as fh:
        fh.write("\n".join(L) + "\n")


# ====================================================================================================== main
def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=os.path.join(ROOT, "fab"))
    ap.add_argument("--no-verify", action="store_true", help="do not run scripts/check_fab.py on the result")
    ap.add_argument("--no-step", action="store_true")
    a = ap.parse_args()
    out = os.path.abspath(a.out)
    t0 = datetime.datetime.now().astimezone()
    if not os.path.exists(C.BOARD) or not os.path.exists(SCH):
        sys.exit("board or schematic missing: run scripts/pcb/build.sh first")
    guard = [C.BOARD, C.PRO, C.DRU, SCH]
    before = {p: V.sha256(p) for p in guard}
    sha = before[C.BOARD]
    spec = json.load(open(C.SPEC))
    os.makedirs(out, exist_ok=True)
    for sub in SUBDIRS:                       # the package is rebuilt as a whole: stale files of an earlier board must not survive
        p = os.path.join(out, sub)
        if os.path.isdir(p):
            shutil.rmtree(p)
        os.makedirs(p)
    B = V.Board(C.BOARD)
    log("board %s, SHA-256 %s" % (os.path.basename(C.BOARD), sha))

    export_gerbers(os.path.join(out, "gerber"))
    log("gerber: %d files" % len(os.listdir(os.path.join(out, "gerber"))))
    filled_rows, fixed = export_drill(os.path.join(out, "drill"), B)
    log("drill: plated + non-plated Excellon, map, report; %d filled-and-capped vias listed" % len(filled_rows))
    kicad("pcb", "export", "ipcd356", "-o", os.path.join(out, "netlist", "%s.d356" % PROJECT), C.BOARD)
    export_positions(os.path.join(out, "assembly"), B)
    lines = export_bom(os.path.join(out, "assembly"), B, spec)
    log("netlist, placement files, BOM (%d lines)" % len(lines))

    # copper density per layer for the stackup table: from the Gerbers just written
    coverage = {}
    try:
        import numpy as np
        ol = spec["floorplan"]["outline"]
        area = C.board_polygon(B.b)[0].area
        for name, stem, _ in V.CU_FILES:
            cv = V.Canvas((0, 0, ol["width"], ol["height"]), 40.0)
            V.Gerber(os.path.join(out, "gerber", "%s-%s.gbr" % (PROJECT, stem))).render(cv)
            coverage[name] = float(np.count_nonzero(cv.array())) / (area * 40.0 * 40.0)
    except Exception as e:      # the drawing is still valid without the density column
        log("copper density not computed: %s" % e)
    fab_drawing(os.path.join(out, "drawings", "%s-fabrication.pdf" % PROJECT), B, spec, sha, filled_rows, coverage)
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        svgs = RENDER.export_svgs(C.BOARD, tmp)
    for face, name in (("F", "top"), ("B", "bottom")):
        assembly_drawing(os.path.join(out, "assembly", "%s-assembly-%s.pdf" % (PROJECT, name)), B, spec, sha, svgs, face, lines)
    kicad("sch", "export", "pdf", "-o", os.path.join(out, "drawings", "%s-schematic.pdf" % PROJECT), SCH)
    log("drawings: fabrication (3 sheets), assembly top / bottom, schematic")

    step_note = "`3d/`: not exported (--no-step)."
    if not a.no_step:
        sp = os.path.join(out, "3d", "%s.step" % PROJECT)
        kicad("pcb", "export", "step", "-f", "--drill-origin", "--subst-models", "-o", sp, C.BOARD)
        have = sorted({fp.GetReference() for fp in B.b.GetFootprints() if len(fp.Models()) > 0})
        without = sorted((fp.GetReference() for fp in B.b.GetFootprints() if len(fp.Models()) == 0), key=refkey)
        step_note = ("`3d/%s.step`: board body with the %d parts whose footprint has a 3-D model (stock KiCad models). **No model**: %s "
                     "(project footprints, test pads, fiducials, the card edge): the BGA U1, the tallest part, is NOT in the model." % (PROJECT, len(have), ", ".join(without)))
        log("STEP: %d parts with a model, %d without" % (len(have), len(without)))

    res = None
    if not a.no_verify:
        res = V.verify(out, os.path.join(out, "verify"), C.BOARD)
    after = {p: V.sha256(p) for p in guard}
    if after != before:
        sys.exit("a project file changed during the export: %s" % [os.path.basename(p) for p in guard if after[p] != before[p]])
    write_readme(out, spec, B, sha, res, lines, filled_rows, fixed, step_note, t0)
    n = sum(len(fs) for _, _, fs in os.walk(out))
    log("%d files in %s; board file unchanged" % (n, out))
    if res and res["failed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()

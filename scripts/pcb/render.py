#!/usr/bin/env python3
"""Headless renders of the board for review:  render.py <outdir> [--board file] [--stage label]

Per copper layer (F.Cu, In1, In2, In3, In4, B.Cu) with pads, vias and the outline, a top and a bottom assembly
view and an all-copper overlay at 60 px/mm; zoom crops (BGA, command field, the data side: U1 corner, comb,
divider row, In3 read bundle, read end, reference networks, RDQS; regulators, DAC) at 90 .. 300 px/mm.  Dark background, one colour per layer, title and legend in the image, a 5 mm
grid labelled in card coordinates (origin lower-left corner seen from the front, y up).

Method: kicad-cli pcb export svg (one black-and-white SVG per layer, page coordinates), recoloured and
composited with librsvg + cairo (python3-gi).  Inner layers and B.Cu are drawn as seen from the front
(through the board) so that x matches the card frame; only asm_bottom is mirrored (seen from the back).
"""
import argparse
import os
import re
import subprocess
import sys
import tempfile

import cairo
import gi
gi.require_version("Rsvg", "2.0")
from gi.repository import Rsvg      # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C      # noqa: E402

BG = (0.055, 0.065, 0.085)
COL = {
    "F.Cu": (1.00, 0.33, 0.33), "In1.Cu": (0.36, 0.80, 0.36), "In2.Cu": (0.88, 0.66, 0.22), "In3.Cu": (0.30, 0.66, 1.00),
    "In4.Cu": (0.80, 0.42, 1.00), "B.Cu": (0.20, 0.82, 0.80), "Edge.Cuts": (0.95, 0.95, 0.25),
    "F.SilkS": (0.95, 0.95, 0.95), "B.SilkS": (0.95, 0.95, 0.95), "F.Fab": (0.70, 0.70, 0.76), "B.Fab": (0.70, 0.70, 0.76),
    "F.CrtYd": (0.95, 0.35, 0.85), "B.CrtYd": (0.95, 0.35, 0.85), "F.Mask": (0.55, 0.30, 0.65), "B.Mask": (0.55, 0.30, 0.65),
    "Dwgs.User": (0.45, 0.50, 0.55),
}
LAYERS = ["F.Cu", "In1.Cu", "In2.Cu", "In3.Cu", "In4.Cu", "B.Cu", "Edge.Cuts", "F.SilkS", "B.SilkS", "F.Fab", "B.Fab",
          "F.CrtYd", "B.CrtYd", "F.Mask", "B.Mask", "Dwgs.User"]
CROPS = {       # name: (x0, y0, x1, y1) card frame [, pixels per mm]
    "bga": (11.0, 10.4, 28.0, 27.2),
    "cmd": (14.6, 0.0, 25.4, 12.6),
    "write": (26.4, 0.0, 44.8, 11.6),               # WCK columns and the comb field with the finger rows
    "read": (43.6, 0.0, 61.6, 17.6),                # read end, reference networks, RDQS drop
    "whole_east": (20.0, 2.0, 62.6, 18.8, 90.0),    # the whole data side: corner, bundle, comb, In3 bundle, read end, RDQS loop
    "corner": (21.4, 9.2, 29.6, 17.6, 240.0),       # byte-0 corner of U1: the eight necks, the WCK diagonal (B.Cu), the RDQS pair (In3)
    "comb": (26.0, 2.2, 45.0, 17.6, 150.0),         # the F.Cu bundle, its 45-degree fan and the eight teeth; WCK pair; RDQS turn north
    "divider": (33.8, 2.2, 44.8, 8.4, 240.0),       # divider row: node vias (the T), 240R / 150R back to back, shunt GND vias, WCK columns
    "in3bundle": (36.4, 5.2, 59.2, 15.2, 160.0),    # In3 read bundle: risers, bundle across the teeth, ramp, runs to the read vias
    "readend": (43.4, 2.0, 61.4, 18.6, 180.0),      # read vias on their staircase, return vias, finger tracks, RDQS drop east of them and its loads
    "refnet": (48.0, 2.2, 61.4, 9.6, 240.0),        # reference networks in two rows, VREF_RD bus, bus via, feed, RESET_n parts
    "rdqscross": (33.6, 10.4, 41.6, 16.8, 240.0),   # RDQS on In3: the one 45-degree run across the six diagonals of the F.Cu fan (90 degrees)
    "rdqsbump": (26.2, 10.2, 31.4, 14.0, 240.0),    # RDQS on In3 beside U1: the skew bump of RDQS_P, 1.1 mm east of the land field
    "rdqsvia": (53.4, 6.4, 61.4, 16.6, 200.0),      # RDQS: In3 east above the read vias, the 45-degree run down, the via pair just above its fingers with its two return vias
    "rdqspath": (26.0, 2.0, 61.0, 18.8, 110.0),     # the whole RDQS pair: In3 from U1 across the fan and down to the via pair above fingers 219 / 221, the short F.Cu drop
    "rdqsterm": (55.4, 2.2, 61.0, 9.6, 240.0),      # RDQS via pair and F.Cu drop into fingers 219 / 221: the two loads on the VSS finger axes; VREF_RD bus via and feed
    "reg": (27.0, 17.0, 52.4, 29.0, 150.0),         # regulators: U2 on F.Cu, U3 / U4 on B.Cu, the 1.2 V arm, PG, the 3.3 V trunk, VDD1 / VDDQ lanes
    "strip": (43.4, 2.2, 49.4, 22.6, 150.0),        # the 1.2 V strip: island vias, C512 / C513 / C502, the strip over the In3 read lines, the mitre into the arm
    "reset": (25.6, 2.2, 63.4, 18.6, 100.0),        # RESET_n: ball H1, its one via, B.Cu east and down to R403 / R402 at finger 232
    "dac": (59.4, 2.0, 68.6, 15.2, 200.0),          # DAC: U5, its capacitors, SCL / SDA / 3.3 V from fingers 253 .. 255 west of the tooling hole, RESET_n parts
    "u1pwr": (13.4, 10.8, 28.6, 27.8, 200.0),       # U1 on B.Cu: VDDQ pours and rails, VDD2H centre pour and links, VDD1 rails; F.Cu: the VDD1 north link
    "u1cmdB": (16.6, 14.4, 25.2, 18.8, 240.0),      # B.Cu ends of the ten command via-in-pads (rows G / H / J): pour edges, the neck of the F6 - K6 link
    "wckexit": (22.2, 11.2, 27.6, 16.8, 240.0),     # WCK pair leaving U1 on B.Cu between the two parts of the VDDQ east flank
    "west": (9.0, 9.8, 15.6, 17.4, 200.0),          # west of U1: empty since the pull-down arrays RN401 / RN402 of the unused byte and their eight escapes were removed
    "fcmd": (16.4, 2.0, 23.6, 5.6),                 # finger rows: stubs, finger vias, ties
    "fwrite": (34.0, 2.0, 45.0, 6.4),
    "fread": (48.0, 2.0, 58.6, 8.8),
    "cksplit": (18.4, 15.2, 21.6, 18.4),            # the CK pair at rows G / H / J
    "cmdtune": (15.2, 5.4, 25.0, 12.4, 300.0),      # In3 tuning zone of the command bundle: node vias, fence vias, shifts and bumps
}


def export_svgs(board_path, tmp):
    subprocess.run(["kicad-cli", "pcb", "export", "svg", "--mode-multi", "-o", tmp, "--layers", ",".join(LAYERS),
                    "--page-size-mode", "1", "--exclude-drawing-sheet", "--black-and-white", "--drill-shape-opt", "2",
                    board_path], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = os.path.splitext(os.path.basename(board_path))[0]
    out = {}
    alias = {"F.SilkS": "F_Silkscreen", "B.SilkS": "B_Silkscreen", "F.CrtYd": "F_Courtyard", "B.CrtYd": "B_Courtyard",
             "Dwgs.User": "User_Drawings"}
    for l in LAYERS:
        f = os.path.join(tmp, "%s-%s.svg" % (base, alias.get(l, l.replace(".", "_"))))
        if not os.path.exists(f):
            raise RuntimeError("kicad-cli did not write %s" % f)
        out[l] = open(f).read()
    return out


def hexcol(c):
    return "#%02X%02X%02X" % tuple(int(round(v * 255)) for v in c)


def handle(svg, colour, view, hole=BG):
    """Black -> layer colour, white (drill marks) -> background; the root viewBox is set to the wanted part of
    the page (x, y, w, h in page mm) so that librsvg never has to rasterise the whole sheet."""
    s = re.sub(r"#000000", hexcol(colour), svg, flags=re.I)
    s = re.sub(r"#FFFFFF", hexcol(hole), s, flags=re.I)
    s = re.sub(r'width="[\d.]+mm" height="[\d.]+mm" viewBox="[^"]*"',
               'width="%.4fmm" height="%.4fmm" viewBox="%.4f %.4f %.4f %.4f"' % (view[2], view[3], view[0], view[1], view[2], view[3]),
               s, count=1)
    return Rsvg.Handle.new_from_data(s.encode())


class Canvas:
    def __init__(self, svgs, area, ppm, mirror=False, margin=1.2, title_h=64):
        """area = (x0, y0, x1, y1) in the card frame; mirror = seen from the back."""
        self.svgs, self.ppm, self.mirror = svgs, ppm, mirror
        self.x0, self.y0, self.x1, self.y1 = area[0] - margin, area[1] - margin, area[2] + margin, area[3] + margin
        self.w = int(round((self.x1 - self.x0) * ppm))
        self.h = int(round((self.y1 - self.y0) * ppm))
        self.title_h = title_h
        self.surf = cairo.ImageSurface(cairo.FORMAT_RGB24, self.w, self.h + title_h)
        self.ctx = cairo.Context(self.surf)
        self.ctx.set_source_rgb(*BG)
        self.ctx.paint()
        self.ox, self.oy = C.origin()
        self.legend = []
        self.flipped = False

    def px(self, x, y):
        """Card frame -> pixel."""
        u = (x - self.x0) if not self.mirror else (self.x1 - x)
        return (u * self.ppm, self.title_h + (self.y1 - y) * self.ppm)

    def layer(self, name, alpha=1.0, colour=None, label=None):
        if name not in self.svgs:
            return
        col = colour or COL[name]
        # page mm: card x = page_x - ox ; card y = oy - page_y.  A mirrored view is drawn unmirrored and flipped
        # once (flip()) before the grid and the labels go on.
        view = (self.ox + self.x0, self.oy - self.y1, self.x1 - self.x0, self.y1 - self.y0)
        h = handle(self.svgs[name], col, view)
        ctx = self.ctx
        ctx.save()
        ctx.rectangle(0, self.title_h, self.w, self.h)
        ctx.clip()
        ctx.push_group()
        vp = Rsvg.Rectangle()
        vp.x, vp.y, vp.width, vp.height = 0, self.title_h, self.w, self.h
        h.render_document(ctx, vp)
        ctx.pop_group_to_source()
        ctx.paint_with_alpha(alpha)
        ctx.restore()
        self.legend.append((label or name, col))

    def flip(self):
        if not self.mirror or self.flipped:
            return
        self.flipped = True
        new = cairo.ImageSurface(cairo.FORMAT_RGB24, self.w, self.h + self.title_h)
        c = cairo.Context(new)
        c.translate(self.w, 0)
        c.scale(-1, 1)
        c.set_source_surface(self.surf, 0, 0)
        c.paint()
        self.surf, self.ctx = new, cairo.Context(new)

    def grid(self, step=5.0, minor=1.0):
        self.flip()
        ctx = self.ctx
        ctx.save()
        ctx.rectangle(0, self.title_h, self.w, self.h)
        ctx.clip()
        ctx.select_font_face("monospace", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        fs = max(11, min(22, self.ppm * 0.16 if self.ppm > 100 else 13))
        ctx.set_font_size(fs)
        import math
        for st, a, lw in ((minor, 0.07, 1.0), (step, 0.20, 1.0)):
            if st * self.ppm < 12:
                continue
            ctx.set_source_rgba(1, 1, 1, a)
            ctx.set_line_width(lw)
            x = math.ceil(self.x0 / st) * st
            while x <= self.x1:
                px = self.px(x, 0)[0]
                ctx.move_to(round(px) + 0.5, self.title_h)
                ctx.line_to(round(px) + 0.5, self.title_h + self.h)
                x += st
            y = math.ceil(self.y0 / st) * st
            while y <= self.y1:
                py = self.px(0, y)[1]
                ctx.move_to(0, round(py) + 0.5)
                ctx.line_to(self.w, round(py) + 0.5)
                y += st
            ctx.stroke()
        lab = step if step * self.ppm >= 60 else step * 2
        ctx.set_source_rgba(1, 1, 1, 0.55)
        x = math.ceil(self.x0 / lab) * lab
        while x <= self.x1:
            px = self.px(x, 0)[0]
            ctx.move_to(px + 3, self.title_h + self.h - 5)
            ctx.show_text("%g" % x)
            x += lab
        y = math.ceil(self.y0 / lab) * lab
        while y <= self.y1:
            py = self.px(0, y)[1]
            ctx.move_to(3, py - 3)
            ctx.show_text("%g" % y)
            y += lab
        ctx.restore()

    def rect(self, x0, y0, x1, y1, colour, label=None, dash=(6, 4)):
        self.flip()
        ctx = self.ctx
        ctx.save()
        ctx.rectangle(0, self.title_h, self.w, self.h)
        ctx.clip()
        a, b = self.px(x0, y0), self.px(x1, y1)
        ctx.set_source_rgba(*colour, 0.9)
        ctx.set_line_width(1.5)
        ctx.set_dash(dash)
        ctx.rectangle(min(a[0], b[0]), min(a[1], b[1]), abs(b[0] - a[0]), abs(b[1] - a[1]))
        ctx.stroke()
        if label:
            ctx.set_dash([])
            ctx.select_font_face("monospace")
            ctx.set_font_size(12)
            ctx.move_to(min(a[0], b[0]) + 3, min(a[1], b[1]) + 13)
            ctx.show_text(label)
        ctx.restore()

    def finish(self, path, title):
        self.flip()
        ctx = self.ctx
        ctx.set_source_rgb(0.02, 0.025, 0.035)
        ctx.rectangle(0, 0, self.w, self.title_h)
        ctx.fill()
        ctx.select_font_face("sans-serif", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        ctx.set_font_size(20)
        ctx.set_source_rgb(0.92, 0.92, 0.92)
        ctx.move_to(10, 25)
        ctx.show_text(title)
        ctx.select_font_face("sans-serif", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        ctx.set_font_size(15)
        x = 10
        for name, col in self.legend:
            ctx.set_source_rgb(*col)
            ctx.rectangle(x, 38, 22, 14)
            ctx.fill()
            ctx.set_source_rgb(0.85, 0.85, 0.85)
            ctx.move_to(x + 28, 51)
            ctx.show_text(name)
            x += 28 + ctx.text_extents(name).x_advance + 22
        self.surf.write_to_png(path)
        return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("outdir")
    ap.add_argument("--board", default=C.BOARD)
    ap.add_argument("--stage", default="")
    ap.add_argument("--ppm", type=float, default=60.0, help="pixels per mm of the whole-board views (>= 60)")
    ap.add_argument("--zoom", type=float, default=200.0, help="pixels per mm of the crops (>= 200)")
    ap.add_argument("--only", default="", help="comma separated list of image name prefixes to render")
    ap.add_argument("--prefix", default="", help="file name prefix (e.g. plan_ for the rehearsal board)")
    a = ap.parse_args()
    os.makedirs(a.outdir, exist_ok=True)
    spec = C.spec()
    W, H = spec["floorplan"]["outline"]["width"], spec["floorplan"]["outline"]["height"]
    whole = (0.0, 0.0, W, H)
    tag = (" [%s]" % a.stage) if a.stage else ""
    with tempfile.TemporaryDirectory() as tmp:
        svgs_full = export_svgs(a.board, tmp)
    svgs = svgs_full            # the outer layers carry no GND pour: the copper as fabricated IS the wires, pads and the power pours under U1
    out = []
    only = [o for o in a.only.split(",") if o]

    def want(name):
        return not only or any(name.startswith(o) for o in only)

    def path(name):
        return os.path.join(a.outdir, a.prefix + name + ".png")

    def regions(cv, layer):
        for r in spec["floorplan"]["regions"]:
            if layer in r["layers"]:
                cv.rect(r["x"][0], r["y"][0], r["x"][1], r["y"][1], (0.6, 0.6, 0.6), r["name"])

    # per copper layer
    for l in C.CU_ORDER:
        n = "layer_%s" % l.replace(".", "_")
        if not want(n):
            continue
        cv = Canvas(svgs_full, whole, a.ppm)
        cv.layer(l)
        cv.layer("Edge.Cuts")
        cv.grid()
        out.append(cv.finish(path(n),
                             "%s  copper as fabricated (zones filled), pads, vias, outline%s   (seen from the front, card frame mm)" % (l, tag)))
    # assembly views
    if want("asm_top"):
        cv = Canvas(svgs, whole, a.ppm)
        cv.layer("F.Cu", alpha=0.55)
        cv.layer("F.CrtYd", alpha=0.8)
        cv.layer("F.Fab")
        cv.layer("F.SilkS")
        cv.layer("Edge.Cuts")
        cv.grid()
        out.append(cv.finish(path("asm_top"), "Top assembly (front view, pin 1 left)%s" % tag))
    if want("asm_bottom"):
        cv = Canvas(svgs, whole, a.ppm, mirror=True)
        cv.layer("B.Cu", alpha=0.55)
        cv.layer("B.CrtYd", alpha=0.8)
        cv.layer("B.Fab")
        cv.layer("B.SilkS")
        cv.layer("Edge.Cuts")
        cv.grid()
        out.append(cv.finish(path("asm_bottom"),
                             "Bottom assembly (seen from the back: mirrored, pin 2 at the right; x labels are card x)%s" % tag))
    if want("all_copper"):
        cv = Canvas(svgs, whole, a.ppm)
        for l, al in (("In1.Cu", 0.10), ("In2.Cu", 0.10), ("In4.Cu", 0.22), ("B.Cu", 0.85), ("In3.Cu", 0.9), ("F.Cu", 0.85)):
            cv.layer(l, alpha=al)
        cv.layer("Edge.Cuts")
        cv.grid()
        regions(cv, "F.Cu")
        out.append(cv.finish(path("all_copper"),
                             "All copper layers (planes faint), F.Cu regions of the floorplan dashed%s" % tag))
    # crops
    for name, area in CROPS.items():
        zoom = area[4] if len(area) > 4 else a.zoom
        area = area[:4]
        views = (
            ("F", [("F.Cu", 0.9), ("F.CrtYd", 0.6), ("F.Fab", 0.9), ("F.SilkS", 0.9)], "F.Cu + courtyard, fab, silk"),
            ("In3", [("F.Cu", 0.18), ("B.Cu", 0.18), ("In3.Cu", 1.0)], "In3.Cu (F.Cu / B.Cu faint)"),
            ("In4", [("In4.Cu", 1.0)], "In4.Cu"),
            ("In2", [("In2.Cu", 1.0)], "In2.Cu"),
            ("B", [("B.Cu", 0.9), ("B.CrtYd", 0.6), ("B.Fab", 0.9), ("B.SilkS", 0.9)], "B.Cu + courtyard, fab, silk (seen through from the front)"),
            ("all", [("In4.Cu", 0.16), ("B.Cu", 0.8), ("In3.Cu", 0.9), ("F.Cu", 0.8)], "F.Cu, In3.Cu, B.Cu (In4 faint)"),
            ("Fcu", [("F.Cu", 1.0)], "F.Cu as fabricated (copper only)"),
            ("Bcu", [("B.Cu", 1.0)], "B.Cu as fabricated (copper only, seen through from the front)"),
        )
        for vn, layers, what in views:
            n = "crop_%s_%s" % (name, vn)
            if not want(n):
                continue
            if vn in ("In4", "In2") and name not in ("bga", "cmd", "write", "read", "reg", "strip", "u1pwr"):
                continue
            if vn in ("Fcu", "Bcu") and name not in ("reg", "strip", "dac", "u1pwr", "u1cmdB", "wckexit", "west", "read", "cmd"):
                continue
            if name in ("u1cmdB", "wckexit") and vn not in ("B", "Bcu", "all"):
                continue
            if name == "west" and vn not in ("F", "Fcu", "all"):
                continue
            if name in ("fcmd", "fwrite", "fread") and vn not in ("F", "B", "all"):
                continue
            if name == "cksplit" and vn not in ("In3", "B", "all"):
                continue
            if name == "cmdtune" and vn not in ("In3", "all"):
                continue
            if name in ("corner", "comb", "divider", "in3bundle", "readend", "refnet", "rdqsvia", "rdqsterm", "whole_east") and vn in ("In4", "In2"):
                continue
            cv = Canvas(svgs, area, zoom, margin=0.0)
            for l, al in layers:
                cv.layer(l, alpha=al)
            cv.layer("Edge.Cuts")
            cv.grid(step=1.0, minor=0.1)
            out.append(cv.finish(path(n),
                                 "%s: %s%s   x %.1f..%.1f  y %.1f..%.1f" % (name, what, tag, area[0], area[2], area[1], area[3])))
    for p in out:
        print(p)


if __name__ == "__main__":
    main()

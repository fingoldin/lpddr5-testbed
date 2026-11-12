#!/usr/bin/env python3
"""Gates on lpddr5-testbed.kicad_pcb.  Exit status 0 only if every gate passes.

  1  parity      every footprint, every pad -> net, footprint names, positions and pad centres against
                 design/netlist.json; nets; U1 via-in-pads and field vias against the floorplan
  2  drc         kicad-cli pcb drc, all severities, with the .kicad_dru; counts per violation type;
                 'unconnected items' are reported separately (expected until the routing stages exist);
                 every violation of severity 'error' must be absent or listed in JUSTIFIED below
  3  outline     one closed Edge.Cuts contour, 69.6 x 30.0 mm, key slot, latch notches, thickness = stackup
  4  courtyards  no overlaps, every part inside the DDR4 component area, height limits (0.8 mm below y 5.10,
                 1.2 mm above)
  5  fingers     no solder mask on the finger pads, no copper pour over them on any layer nearer than the
                 pull-back table, no foreign copper on a finger
  6  command     (once s20_cmd has run) the command bus as it is on the board: connectivity, delay of every net
                 measured along its copper path against the target, vertex angles, crossings and spacing, the
                 accordions, the CK pair (gap, coupled length, skew, symmetry), layer purity, via structure and the
                 reference planes under the In3 traces
  7  data        (once s30_data has run) the data side as it is on the board ('T at the divider', comb): connectivity of DQ0-7 / WCK / RDQS /
                 the reference nets / RESET_n; T compactness (copper from the node via to both divider lands, lands to the card edge); the delay of
                 every write path, read path and pair leg measured along its copper with the bend model against its target (the write group is not
                 matched on the card: each net against its own planned total, the spread reported, the per-bit deskew table written to
                 design/gateware_deskew.json); vertex angles, self-overlap, same-layer crossings, spacings, the pairs, the skew bump, widths, layer
                 purity, the reserved corridor, vias and return vias, reference planes, the quiet reference
  8  crossings   scripts/pcb/crossings.py on the built board against the crossing budget of the spec; the seven command crossings one by one

  check_pcb.py [--board file] [--drc-json out.json] [--allow-unconnected N]
"""
import argparse
import collections
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile

import pcbnew
import shapely
from shapely.geometry import Point, Polygon, box
from shapely.ops import unary_union

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C      # noqa: E402

# DRC violations of severity 'error' that are accepted, with the reason.  type -> (max count, reason)
JUSTIFIED = {}
# DRC warnings that are accepted, with the reason.  type -> (max count, reason).  Any other warning fails the gate.
WARN_JUSTIFIED = {
    "diff_pair_gap_out_of_range": (9, "the places where the two legs of a pair must part (the gap range only tells the DRC which segments count as coupled): "
                                      "CK 5 (the links of its two divider columns on F.Cu / B.Cu, the lead-out of the node vias, the split around ball H9), "
                                      "WCK 3 (the two node links of its columns, the balls D5 / E4), RDQS 1 (the skew bump)"),
}

# body heights (mm, maximum) by footprint, for the DDR4 height limits
HEIGHT = {
    "Resistor_SMD:R_0201_0603Metric": 0.26,
    "Resistor_SMD:R_0402_1005Metric": 0.40,
    "Resistor_SMD:R_Array_Convex_4x0402": 0.45,
    "lpddr5_testbed:Fiducial_1mm_Mask2mm": 0.0,
    "Capacitor_SMD:C_0201_0603Metric": 0.33,
    "Capacitor_SMD:C_0402_1005Metric": 0.55,
    "Capacitor_SMD:C_0603_1608Metric": 0.90,
    "Package_SON:VSON-10-1EP_3x3mm_P0.5mm_EP1.65x2.4mm": 1.00,
    "Package_SON:WSON-8-1EP_2x2mm_P0.5mm_EP0.9x1.6mm": 0.80,
    "Package_TO_SOT_SMD:TSOT-23-5": 1.10,         # TLV70018DDC (thin SOT, 1.1 mm max; lands identical to SOT-23-5, 3-D model of the thin body)
    "lpddr5_testbed:MLF-6-1EP_2x2mm_P0.65mm_EP0.8x1.4mm": 0.90,
    "lpddr5_testbed:BGA-315_12.4x15mm_P0.8x0.7mm": 1.10,
    "TestPoint:TestPoint_Pad_D1.0mm": 0.0,
    "lpddr5_testbed:DDR4_SODIMM_260_Edge": 0.0,
}

results = []


def check(gate, name, ok, detail=""):
    results.append((gate, name, bool(ok), detail))
    print("%s  [%s] %s%s" % ("PASS" if ok else "FAIL", gate, name, ("\n        " + detail) if (detail and not ok) else ""))
    sys.stdout.flush()


def poly_from_set(ps):
    out = []
    for i in range(ps.OutlineCount()):
        ol = ps.Outline(i)
        ext = [C.k2c(ol.CPoint(k)) for k in range(ol.PointCount())]
        holes = []
        for h in range(ps.HoleCount(i)):
            hl = ps.Hole(i, h)
            holes.append([C.k2c(hl.CPoint(k)) for k in range(hl.PointCount())])
        if len(ext) >= 3:
            out.append(Polygon(ext, holes))
    return out


# ------------------------------------------------------------------------------------------------ 1 parity
def gate_parity(board, spec):
    G = "1 parity"
    parts = C.parts()
    fpl = spec["floorplan"]
    fps = {f.GetReference(): f for f in board.GetFootprints()}
    check(G, "footprints on the board = parts of the spec (%d)" % len(parts),
          set(fps) == set(parts) and len(list(board.GetFootprints())) == len(parts),
          "missing %s, extra %s" % (sorted(set(parts) - set(fps))[:8], sorted(set(fps) - set(parts))[:8]))
    bad_fp, bad_val, bad_attr = [], [], []
    pad_total, pad_bad, pad_missing, pad_unnumbered_cu = 0, [], [], []
    for ref, part in parts.items():
        fp = fps.get(ref)
        if fp is None:
            continue
        if fp.GetFPIDAsString() != part["footprint"]:
            bad_fp.append((ref, fp.GetFPIDAsString()))
        if fp.GetValue() != part["value"]:
            bad_val.append((ref, fp.GetValue()))
        if fp.IsDNP() != bool(part.get("dnp")) or fp.IsExcludedFromBOM() != (not part.get("in_bom", True)):
            bad_attr.append(ref)
        seen = set()
        for p in fp.Pads():
            n = p.GetNumber()
            if n == "":
                # the one unnumbered copper pad that is allowed: the dot of a fiducial (a part without pins)
                if p.IsOnCopperLayer() and p.GetAttribute() != pcbnew.PAD_ATTRIB_NPTH and not (ref.startswith("FID") and not part["pins"] and not p.GetNetname()):
                    pad_unnumbered_cu.append(ref)
                continue
            pad_total += 1
            seen.add(n)
            if n not in part["pins"]:
                pad_bad.append((ref, n, "not in the spec"))
            elif p.GetNetname() != part["pins"][n] and not (part["pins"][n] == "" and p.GetNetname().startswith("unconnected-(")):
                pad_bad.append((ref, n, "%r != %r" % (p.GetNetname(), part["pins"][n])))
        for n in part["pins"]:
            if n not in seen:
                pad_missing.append((ref, n))
    check(G, "footprint library names match the spec", not bad_fp, str(bad_fp[:6]))
    check(G, "values, DNP and BOM attributes match the spec", not bad_val and not bad_attr, "%s %s" % (bad_val[:6], bad_attr[:6]))
    check(G, "pad -> net of every numbered pad (%d pads) equals the spec; no pin without a pad; no stray copper pad" % pad_total,
          not pad_bad and not pad_missing and not pad_unnumbered_cu,
          "wrong: %s; missing: %s; unnumbered copper pads: %s" % (pad_bad[:6], pad_missing[:6], pad_unnumbered_cu[:6]))
    want_nets = set(C.nets())
    have_all = {n.GetNetname() for n in board.GetNetInfo().NetsByName().values() if n.GetNetname()}
    nc_nets = {n for n in have_all if n.startswith("unconnected-(")}     # KiCad's names for the nets of unconnected pins (schematic parity): pads only
    have = have_all - nc_nets
    used = {t.GetNetname() for t in board.GetTracks()} | {z.GetNetname() for z in board.Zones() if not z.GetIsRuleArea()}
    check(G, "nets on the board = nets of the spec (%d) plus the %d single-pad nets KiCad names for unconnected pins; tracks, vias and zones use only the nets of the spec"
          % (len(want_nets), len(nc_nets)),
          have == want_nets and used <= want_nets, "missing %s, extra %s, foreign %s"
          % (sorted(want_nets - have)[:6], sorted(have - want_nets)[:6], sorted(used - want_nets)[:6]))

    # positions
    pos_bad, pad_pos_bad, n_pos = [], [], 0
    worst = 0.0
    for ref, fp in fps.items():
        pl = fpl["placements"].get(ref) or fpl["placements_other"].get(ref)
        if pl is None:
            pos_bad.append((ref, "no placement in the spec"))
            continue
        x, y = C.k2c(fp.GetPosition())
        side = "B" if fp.IsFlipped() else "F"
        if abs(x - pl["x"]) > 1e-5 or abs(y - pl["y"]) > 1e-5 or side != pl["side"]:
            pos_bad.append((ref, (x, y, side)))
        if "rot_deg" in pl and abs(((fp.GetOrientationDegrees() - pl["rot_deg"] + 180) % 360) - 180) > 1e-6:
            pos_bad.append((ref, "rot %g" % fp.GetOrientationDegrees()))
        if ref in fpl["placements"]:
            want = {"1": pl["pad1"], "2": pl["pad2"]}
        elif isinstance(pl["pads"], dict):
            want = pl["pads"]
        elif ref == "U1":
            want = {b: C.u1_ball_xy(b) for b in C.edge.ball_names()}
        else:
            want = {str(f["finger"]): (f["x_mm"], C.finger_xy(f["finger"])[1]) for f in spec["fingers"]}
        for n, w in want.items():
            px, py = C.pad_xy(board, "%s.%s" % (ref, n))
            n_pos += 1
            e = max(abs(px - w[0]), abs(py - w[1]))
            worst = max(worst, e)
            if e > 1.5e-3:
                pad_pos_bad.append((ref, n, (px, py), tuple(w)))
        if ref == "J1":
            for f in spec["fingers"]:
                p = C.pad(board, "J1.%d" % f["finger"])
                lay = pcbnew.F_Cu if f["side"] == "F" else pcbnew.B_Cu
                if not p.IsOnLayer(lay) or p.IsOnLayer(pcbnew.B_Cu if f["side"] == "F" else pcbnew.F_Cu):
                    pad_pos_bad.append(("J1", f["finger"], "wrong side"))
    check(G, "every footprint at the spec position, side and rotation", not pos_bad, str(pos_bad[:6]))
    check(G, "pad centres match the spec (%d pads incl. 315 balls and 260 fingers, worst deviation %.4f mm)" % (n_pos, worst),
          not pad_pos_bad, str(pad_pos_bad[:4]))
    a1 = C.ball(board, "A1")
    aa15 = C.ball(board, "AA15")
    check(G, "U1: ball A1 at the lower right (%.3f, %.3f), AA15 at the upper left (%.3f, %.3f), rotation 180" % (a1 + aa15),
          a1[0] > aa15[0] and a1[1] < aa15[1] and abs(fps["U1"].GetOrientationDegrees() - 180) < 1e-6)
    f1, f2, f259, f260 = (C.finger(board, n) for n in (1, 2, 259, 260))
    check(G, "fingers seen from the front: pin 1 front-left x %.3f, pin 2 back x %.3f, pin 259 front x %.3f, pin 260 back x %.3f"
          % (f1[0], f2[0], f259[0], f260[0]),
          abs(f1[0] - 1.425) < 1e-6 and abs(f2[0] - 1.675) < 1e-6 and abs(f259[0] - 67.925) < 1e-6 and abs(f260[0] - 68.175) < 1e-6
          and C.pad(board, "J1.1").IsOnLayer(pcbnew.F_Cu) and C.pad(board, "J1.2").IsOnLayer(pcbnew.B_Cu))

    # vias
    vias = [t for t in board.GetTracks() if t.Type() == pcbnew.PCB_VIA_T]
    sizes = collections.Counter((round(C.MM(v.GetWidth(pcbnew.F_Cu)), 3), round(C.MM(v.GetDrill()), 3)) for v in vias)
    allowed = {(v["diameter"], v["drill"]) for v in spec["rules"]["vias"].values()}
    thru = all(v.GetViaType() == pcbnew.VIATYPE_THROUGH for v in vias)
    check(G, "vias: %d, all through, sizes %s" % (len(vias), dict(sizes)), thru and set(sizes) <= allowed)
    vip_size = (fpl["U1"]["via_in_pad"]["size"]["diameter"], fpl["U1"]["via_in_pad"]["size"]["drill"])
    at = {}
    for v in vias:
        at.setdefault(tuple(round(c, 3) for c in C.k2c(v.GetPosition())), []).append(v)
    balls = {b: C.u1_ball_xy(b) for b in C.edge.ball_names()}
    want_vip = fpl["U1"]["via_in_pad"]["balls"]
    vip_bad = []
    for b, xy in balls.items():
        vs = at.get(tuple(xy), [])
        if b in want_vip:
            if len(vs) != 1 or vs[0].GetNetname() != want_vip[b]["net"] \
                    or (round(C.MM(vs[0].GetWidth(pcbnew.F_Cu)), 3), round(C.MM(vs[0].GetDrill()), 3)) != vip_size \
                    or vs[0].GetFillingMode() != pcbnew.FILLING_MODE_FILLED or vs[0].GetCappingMode() != pcbnew.CAPPING_MODE_CAPPED \
                    or vs[0].GetRemoveUnconnected() != (fpl["U1"]["via_in_pad"].get("unused_inner_pads") == "removed") \
                    or (vs[0].GetRemoveUnconnected() and not vs[0].GetKeepStartEnd()):
                vip_bad.append((b, "missing or wrong"))
        else:
            near = [v for v in vias if math.hypot(C.k2c(v.GetPosition())[0] - xy[0], C.k2c(v.GetPosition())[1] - xy[1]) < 0.35]
            if near:
                vip_bad.append((b, "must not have a via"))
    trim = fpl["U1"]["via_in_pad"].get("in3_pad_trim") or {"balls": [], "inner_diameter": vip_size[0]}
    for b, xy in balls.items():
        vs = at.get(tuple(xy), [])
        if b in want_vip and len(vs) == 1:
            want_in = trim["inner_diameter"] if b in trim["balls"] else vip_size[0]
            if abs(C.MM(vs[0].GetWidth(pcbnew.In3_Cu)) - want_in) > 1e-6 or abs(C.MM(vs[0].GetWidth(pcbnew.B_Cu)) - vip_size[0]) > 1e-6:
                vip_bad.append((b, "inner / back pad size"))
    omitted = fpl["U1"]["via_in_pad"]["omitted"]
    check(G, "U1 via-in-pads (In3 pad of %s trimmed to %g mm, every other pad %g mm):" % (" / ".join(trim["balls"]) or "none", trim["inner_diameter"], vip_size[0]) + " %d balls of the spec have a filled + capped %g / %g via on their net (unused inner pads: %s, outer pads kept); "
          "the other %d balls (%d omitted supply balls, DQ, straps, NC) have none"
          % (len(want_vip), vip_size[0], vip_size[1], fpl["U1"]["via_in_pad"].get("unused_inner_pads", "kept"), 315 - len(want_vip), len(omitted)),
          not vip_bad, str(vip_bad[:8]))
    fv_bad = []
    for v in fpl["vias"]:
        vs = at.get((round(v["x"], 3), round(v["y"], 3)), [])
        if len(vs) != 1 or vs[0].GetNetname() != v["net"] or abs(C.MM(vs[0].GetWidth(pcbnew.F_Cu)) - v["diameter"]) > 1e-6 \
                or abs(C.MM(vs[0].GetDrill()) - v["drill"]) > 1e-6:
            fv_bad.append((v["net"], v["x"], v["y"]))
    known = {tuple(xy) for b, xy in balls.items() if b in want_vip} | {(round(v["x"], 3), round(v["y"], 3)) for v in fpl["vias"]} \
        | {(round(v["x"], 3), round(v["y"], 3)) for v in fpl["finger_vias"]}
    # finger vias and ties
    fg_bad = []
    for v in fpl["finger_vias"]:
        vs = at.get((round(v["x"], 3), round(v["y"], 3)), [])
        if len(vs) != 1 or vs[0].GetNetname() != v["net"] or abs(C.MM(vs[0].GetWidth(pcbnew.F_Cu)) - v["diameter"]) > 1e-6 \
                or abs(C.MM(vs[0].GetDrill()) - v["drill"]) > 1e-6:
            fg_bad.append(("via", v["x"]))
    con = board.GetConnectivity()
    con.Build(board)
    j1fp = C.footprint(board, "J1")
    loose = []
    for pd in j1fp.Pads():
        if pd.GetNumber() and pd.GetNetname() in ("GND", "V1P2"):
            if not any(t.Type() == pcbnew.PCB_VIA_T for t in con.GetConnectedTracks(pd)) and \
                    not any(o.Type() == pcbnew.PCB_VIA_T for t in con.GetConnectedTracks(pd) for o in con.GetConnectedTracks(t)):
                loose.append(pd.GetNumber())
    n_pwr = sum(1 for pd in j1fp.Pads() if pd.GetNumber() and pd.GetNetname() in ("GND", "V1P2"))
    check(G, "finger vias: the %d vias of floorplan.finger_vias are on the board; each of the %d VSS / VDD fingers is tied to one by a track on its own layer"
          % (len(fpl["finger_vias"]), n_pwr), not fg_bad and not loose, "wrong %s; fingers without a via: %s" % (fg_bad[:6], loose[:10]))
    stray = [k for k in at if k not in known]
    later = any(C.stage_of(t) in ("s20_cmd", "s30_data", "s50_power") for t in board.GetTracks())
    check(G, "field vias: the %d vias of floorplan.vias are on the board with their net and size%s"
          % (len(fpl["vias"]), "" if later else "; no other via exists at this stage"),
          not fv_bad and (later or not stray), "wrong %s; other vias %s" % (fv_bad[:6], stray[:6]))
    # track angles and widths
    odd, thin = [], []
    for t in board.GetTracks():
        if t.Type() == pcbnew.PCB_TRACE_T:
            a, b = C.k2c(t.GetStart()), C.k2c(t.GetEnd())
            dx, dy = abs(a[0] - b[0]), abs(a[1] - b[1])
            if not (dx < 1e-6 or dy < 1e-6 or abs(dx - dy) < 2.5e-6):
                odd.append((t.GetNetname(), a, b))
            if C.MM(t.GetWidth()) < spec["rules"]["board"]["min_track"] - 1e-9:
                thin.append(t.GetNetname())
    ntr = sum(1 for t in board.GetTracks() if t.Type() in (pcbnew.PCB_TRACE_T, pcbnew.PCB_ARC_T))
    check(G, "tracks: %d segments, all at 0 / 45 / 90 / 135 degrees, none thinner than %g mm" % (ntr, spec["rules"]["board"]["min_track"]),
          not odd and not thin, "%s %s" % (odd[:4], thin[:4]))
    links = fpl["U1"]["f_cu_links"] + fpl["U1"]["b_cu_ties"]
    unl = []
    for l in links:
        pa, pb = C.pad(board, "U1." + l["from"]), C.pad(board, "U1." + l["to"])
        tr = [t for t in board.GetTracks() if t.Type() == pcbnew.PCB_TRACE_T and t.GetNetname() == l["net"]
              and t.GetLayer() == C.CU[l["layer"]] and (t.HitTest(pa.GetPosition()) or t.HitTest(pb.GetPosition()))]
        ends = [tuple(l["path"][0]), tuple(l["path"][-1])]
        if not tr or not any(math.hypot(C.k2c(t.GetStart())[0] - ends[0][0], C.k2c(t.GetStart())[1] - ends[0][1]) < 1e-6 for t in tr):
            unl.append((l["from"], l["to"]))
    check(G, "U1 links: the %d F.Cu links / straps and %d B.Cu tie of the spec are on the board" % (len(fpl["U1"]["f_cu_links"]), len(fpl["U1"]["b_cu_ties"])),
          not unl, str(unl[:6]))
    del con


# ------------------------------------------------------------------------------------------------ 2 DRC
def gate_drc(board_path, out_json, allow_unconnected):
    G = "2 drc"
    tmp = None
    if not out_json:
        tmp = tempfile.mkdtemp()
        out_json = os.path.join(tmp, "drc.json")
    r = subprocess.run(["kicad-cli", "pcb", "drc", "--severity-all", "--all-track-errors", "--schematic-parity", "--format", "json", "--units", "mm", "-o", out_json, board_path],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if r.returncode != 0 or not os.path.exists(out_json):
        check(G, "kicad-cli pcb drc ran", False, r.stdout[-400:])
        return
    d = json.load(open(out_json))
    by = collections.Counter((v["type"], v["severity"]) for v in d["violations"])
    errors = {t: n for (t, s), n in by.items() if s == "error"}
    warns = {t: n for (t, s), n in by.items() if s != "error"}
    unc = len(d.get("unconnected_items", []))
    print("        KiCad %s, rules: board setup + net classes + %s" % (d.get("kicad_version"), os.path.basename(C.DRU)))
    print("        violations by type: errors %s" % (dict(sorted(errors.items())) or "none"))
    print("                            warnings %s" % (dict(sorted(warns.items())) or "none"))
    print("        unconnected items: %d" % unc)
    unjust = {t: n for t, n in errors.items() if t not in JUSTIFIED or n > JUSTIFIED[t][0]}
    detail = ""
    if unjust:
        ex = []
        for v in d["violations"]:
            if v["severity"] == "error" and v["type"] in unjust and len(ex) < 8:
                ox, oy = C.origin()
                ex.append("%s: %s @ %s" % (v["type"], v["description"][:110],
                                           [(round(i["pos"]["x"] - ox, 3), round(oy - i["pos"]["y"], 3)) for i in v["items"]][:2]))
        detail = "\n        ".join(ex)
    check(G, "no DRC violation of severity error (%d errors, %d justified types)" % (sum(errors.values()), len(JUSTIFIED)), not unjust, detail)
    for t, (n, why) in JUSTIFIED.items():
        print("        justified: %s x%d: %s" % (t, errors.get(t, 0), why))
    for t, (n, why) in WARN_JUSTIFIED.items():
        print("        justified warning: %s x%d (limit %d): %s" % (t, warns.get(t, 0), n, why))
    unj_w = {t: n for t, n in warns.items() if t not in WARN_JUSTIFIED or n > WARN_JUSTIFIED[t][0]}
    check(G, "every DRC warning is one of the justified ones (%d warnings: %s)" % (sum(warns.values()), dict(sorted(warns.items())) or "none"), not unj_w, str(unj_w))
    par = d.get("schematic_parity", [])
    pby = collections.Counter(v["type"] for v in par)
    # the comparison is only a test if the schematic is there: kicad-cli reports 0 issues when the root sheet is missing
    sch = os.path.splitext(board_path)[0] + ".kicad_sch"
    have_sch = os.path.exists(sch) and "schematic_parity" in d
    check(G, "KiCad's own schematic <-> board comparison (kicad-cli pcb drc --schematic-parity): %d issues%s"
          % (len(par), "" if have_sch else " - NOT RUN: %s is missing (run scripts/gen_schematic.py before the board stages: scripts/pcb/build.sh does)" % os.path.basename(sch)),
          not par and have_sch, "%s; e.g. %s" % (dict(pby), [v["description"][:90] for v in par[:3]]))
    if allow_unconnected is not None:
        check(G, "unconnected items <= %d" % allow_unconnected, unc <= allow_unconnected, "%d" % unc)
    return d


# ------------------------------------------------------------------------------------------------ 3 outline
def gate_outline(board, spec):
    G = "3 outline"
    o = spec["floorplan"]["outline"]
    try:
        polys = C.board_polygon(board)
    except RuntimeError as e:
        check(G, "Edge.Cuts is one closed outline", False, str(e))
        return None
    check(G, "Edge.Cuts is ONE closed outline without inner cut-outs", len(polys) == 1 and not polys[0].interiors,
          "%d outline(s), %d hole(s)" % (len(polys), sum(len(p.interiors) for p in polys)))
    p = polys[0]
    b = p.bounds
    check(G, "bounding box %.3f x %.3f mm at the card origin (spec %.1f x %.1f)" % (b[2] - b[0], b[3] - b[1], o["width"], o["height"]),
          abs(b[0]) < 1e-3 and abs(b[1]) < 1e-3 and abs(b[2] - o["width"]) < 1e-3 and abs(b[3] - o["height"]) < 1e-3)
    k = o["key_notch"]
    kx, kw, kd = k["x_centre"], k["width"], k["depth"]
    inside = lambda x, y: p.contains(Point(x, y))      # noqa: E731
    key_ok = (not inside(kx, kd - 0.05) and inside(kx, kd + 0.05) and not inside(kx - kw / 2 + 0.03, 1.0) and inside(kx - kw / 2 - 0.03, 1.0)
              and not inside(kx + kw / 2 - 0.03, 1.0) and inside(kx + kw / 2 + 0.03, 1.0) and not inside(kx, 0.1))
    check(G, "key slot: centre x %.2f, width %.2f, depth %.2f (full radius)" % (kx, kw, kd), key_ok)
    ln = o["latch_notches"]
    n_ok = all((not inside(x0, (ln["y_from"] + ln["y_to"]) / 2)) and inside(x1, (ln["y_from"] + ln["y_to"]) / 2)
               and inside(x0, ln["y_from"] - 0.05) and inside(x0, ln["y_to"] + 0.05)
               for x0, x1 in ((ln["depth"] - 0.05, ln["depth"] + 0.05), (o["width"] - ln["depth"] + 0.05, o["width"] - ln["depth"] - 0.05)))
    check(G, "latch notches: y %.1f .. %.1f, %.2f deep, both sides" % (ln["y_from"], ln["y_to"], ln["depth"]), n_ok)
    holes = sorted((round(C.k2c(pd.GetPosition())[0], 3), round(C.k2c(pd.GetPosition())[1], 3), round(C.MM(pd.GetDrillSize().x), 3))
                   for pd in C.footprint(board, "J1").Pads() if pd.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH)
    check(G, "tooling holes %s" % holes, holes == sorted((h["x"], h["y"], h["d"]) for h in o["tooling_holes"]))
    st = spec["stackup"]
    thick = C.MM(board.GetDesignSettings().GetBoardThickness())
    # the stackup table is not exposed by the Python API: read it from the board file
    txt = open(board.GetFileName()).read()
    blk = txt[txt.index("(stackup"):txt.index("(pad_to_mask_clearance")]
    rows = [(m.group(1), float(m.group(2))) for m in re.finditer(r'\(layer "([^"]+)"(?:(?!\(layer ).)*?\(thickness ([\d.]+)\)', blk, re.S)]
    mats = re.findall(r'\(material "([^"]+)"\)\s*\(epsilon_r ([\d.]+)\)', blk)
    want = [round(l["thickness_mm"], 4) for l in st["layers"]]
    check(G, "stackup: %d layers, thicknesses equal the spec row by row, sum %.3f mm = board thickness %.3f mm (spec %.3f; %.3f at the bare fingers)"
          % (len(rows), sum(t for _, t in rows), thick, st["total_thickness_with_mask_mm"], st["thickness_at_fingers_no_mask_mm"]),
          [t for _, t in rows] == want and abs(thick - st["total_thickness_with_mask_mm"]) < 1e-6
          and abs(sum(want) - thick) < 1e-6 and [n for n, _ in rows] == [l["layer"] for l in st["layers"]]
          and mats == [(l["material"], "%g" % l["er"]) for l in st["layers"] if l["type"] in ("prepreg", "core")], "%s %s" % (rows, mats))
    names = [board.GetLayerName(l) for l in C.CU.values()]
    types = [pcbnew.LAYER.ShowType(board.GetLayerType(l)) for l in C.CU.values()]
    check(G, "copper layers %s, types %s (as upstream)" % (names, types),
          board.GetCopperLayerCount() == 6 and names == C.CU_ORDER and types == ["signal", "power", "power", "signal", "signal", "signal"])
    return p


# ------------------------------------------------------------------------------------------------ 4 courtyards
def gate_courtyards(board, spec):
    G = "4 courtyards"
    area = Polygon(C.edge.component_area())
    cy = {}
    for fp in board.GetFootprints():
        ref = fp.GetReference()
        side = "B" if fp.IsFlipped() else "F"
        ps = fp.GetCourtyard(pcbnew.B_CrtYd if side == "B" else pcbnew.F_CrtYd)
        polys = poly_from_set(ps)
        if polys:
            cy[ref] = (side, unary_union(polys))
    no_cy = sorted(set(f.GetReference() for f in board.GetFootprints()) - set(cy))
    check(G, "every part except the card edge has a courtyard (%d courtyards)" % len(cy), no_cy == ["J1"], str(no_cy))
    refs = sorted(cy)
    ov = []
    closest = (9.9, None)
    for i, a in enumerate(refs):
        for b in refs[i + 1:]:
            if cy[a][0] != cy[b][0]:
                continue
            if cy[a][1].bounds[2] < cy[b][1].bounds[0] - 1 or cy[b][1].bounds[2] < cy[a][1].bounds[0] - 1:
                continue
            inter = cy[a][1].intersection(cy[b][1]).area
            if inter > 1e-6:
                ov.append((a, b, round(inter, 4)))
            else:
                d = cy[a][1].distance(cy[b][1])
                if d < closest[0]:
                    closest = (d, (a, b))
    check(G, "no two courtyards on one face overlap (closest pair %s, %.3f mm apart)" % (closest[1], closest[0]), not ov, str(ov[:8]))
    out = [(r, [round(v, 2) for v in g.bounds]) for r, (s, g) in cy.items() if not area.buffer(1e-6).contains(g)]
    low = min(g.bounds[1] for s, g in cy.values())
    check(G, "every courtyard inside the DDR4 component area of its face (y >= 4.00, side margins 3.0 / 2.0 mm, key zone); lowest edge y = %.3f" % low,
          not out, str(out[:6]))
    parts = C.parts()
    tall = []
    for r, (s, g) in cy.items():
        h = HEIGHT[parts[r]["footprint"]]
        lim = C.edge.max_component_height(g.bounds[1] + 1e-6)
        if h > lim + 1e-9:
            tall.append((r, h, lim))
    front = max(HEIGHT[parts[r]["footprint"]] for r, (s, g) in cy.items() if s == "F")
    back = max(HEIGHT[parts[r]["footprint"]] for r, (s, g) in cy.items() if s == "B")
    thick = spec["stackup"]["thickness_at_fingers_with_cap_plating_mm"]
    check(G, "component heights: <= 0.80 mm for bodies starting below y 5.10, <= 1.20 mm above; module %.2f + %.2f + %.2f = %.2f mm <= %.1f"
          % (front, thick, back, front + thick + back, spec["floorplan"]["keepouts"]["max_module_thickness"]),
          not tall and front + thick + back <= spec["floorplan"]["keepouts"]["max_module_thickness"], str(tall[:6]))
    # assembly fiducials: nothing but the dot inside the 2.0 mm mask opening and a clear ring around it
    from shapely.geometry import Point as _Pt
    fid_rows, fid_bad = [], []
    for fp in board.GetFootprints():
        ref = fp.GetReference()
        if not ref.startswith("FID"):
            continue
        lid = pcbnew.B_Cu if fp.IsFlipped() else pcbnew.F_Cu
        sid = pcbnew.B_SilkS if fp.IsFlipped() else pcbnew.F_SilkS
        c = C.k2c(fp.GetPosition())
        near = 9.9
        for t in board.GetTracks():
            if t.Type() == pcbnew.PCB_VIA_T or t.GetLayer() == lid:
                x_, y_ = C.k2c(t.GetPosition() if t.Type() == pcbnew.PCB_VIA_T else t.GetStart())
                x2_, y2_ = C.k2c(t.GetPosition() if t.Type() == pcbnew.PCB_VIA_T else t.GetEnd())
                from shapely.geometry import LineString as _Ls
                g_ = _Pt(x_, y_).buffer(t.GetWidth(lid) / 2e6) if t.Type() == pcbnew.PCB_VIA_T else _Ls([(x_, y_), (x2_, y2_)]).buffer(t.GetWidth() / 2e6)
                near = min(near, g_.distance(_Pt(c)))
        for f2 in board.GetFootprints():
            if f2 is fp or f2.GetReference() == ref:
                continue
            for p_ in f2.Pads():
                if p_.IsOnLayer(lid):
                    near = min(near, _pad_box(p_).distance(_Pt(c)))
        for z in board.Zones():
            if not z.GetIsRuleArea() and z.IsOnLayer(lid):
                pl = z.GetFilledPolysList(lid)
                for i in range(pl.OutlineCount()):
                    o = pl.Outline(i)
                    near = min(near, Polygon([C.k2c(o.CPoint(k)) for k in range(o.PointCount())]).distance(_Pt(c)))
        silk = 9.9
        for g in list(board.GetDrawings()) + [g2 for f2 in board.GetFootprints() for g2 in list(f2.GraphicalItems()) + list(f2.GetFields())]:
            if g.GetLayer() == sid and (not hasattr(g, "IsVisible") or g.IsVisible()):
                bb = g.GetBoundingBox()
                lo, hi = C.k2c(pcbnew.VECTOR2I(bb.GetLeft(), bb.GetBottom())), C.k2c(pcbnew.VECTOR2I(bb.GetRight(), bb.GetTop()))
                silk = min(silk, box(lo[0], lo[1], hi[0], hi[1]).distance(_Pt(c)))
        edge_d = _Pt(c).distance(C.board_polygon(board)[0].exterior)
        fid_rows.append("%s %s (%.1f, %.1f): copper %.2f, silk %.2f, edge %.2f" % (ref, "B" if fp.IsFlipped() else "F", c[0], c[1], near, silk, edge_d))
        if near < 1.5 or silk < 1.5 or edge_d < 3.0:
            fid_bad.append(ref)
    check(G, "assembly fiducials (1.0 mm dot, 2.0 mm mask opening): nearest foreign copper on the face, nearest silk and the card edge, mm from the centre "
          "(limits 1.5 / 1.5 / 3.0): " + "; ".join(fid_rows), len(fid_rows) == 6 and not fid_bad, str(fid_bad))
    # parts inside their declared region
    regs = spec["floorplan"]["regions"]
    outside = []
    for r, (s, g) in cy.items():
        lay = "F.Cu" if s == "F" else "B.Cu"
        b = g.bounds
        if r == "U1":                      # the U1 region is the package body; its courtyard is 0.25 mm larger
            b = (b[0] + 0.25, b[1] + 0.25, b[2] - 0.25, b[3] - 0.25)
        if not any(lay in q["layers"] and q["x"][0] - 1e-6 <= b[0] and b[2] <= q["x"][1] + 1e-6 and q["y"][0] - 1e-6 <= b[1]
                   and b[3] <= q["y"][1] + 1e-6 for q in regs):
            outside.append(r)
    check(G, "every part lies inside a floorplan region of its face", not outside, str(sorted(outside)[:12]))


# ------------------------------------------------------------------------------------------------ 5 fingers and planes
def gate_fingers(board, spec, outline):
    G = "5 fingers"
    j1 = C.footprint(board, "J1")
    fpl = spec["floorplan"]
    y0, y1 = fpl["outline"]["finger_pad_y"]
    bad_mask, n = [], 0
    for p in j1.Pads():
        if p.GetNumber() == "":
            continue
        n += 1
        f = int(p.GetNumber())
        cu, mask = (pcbnew.F_Cu, pcbnew.F_Mask) if f % 2 else (pcbnew.B_Cu, pcbnew.B_Mask)
        bb = p.GetBoundingBox()
        lo = C.k2c(pcbnew.VECTOR2I(bb.GetLeft(), bb.GetBottom()))
        hi = C.k2c(pcbnew.VECTOR2I(bb.GetRight(), bb.GetTop()))
        margin = p.GetLocalSolderMaskMargin()
        if not (p.IsOnLayer(cu) and p.IsOnLayer(mask)) or (margin is not None and margin < 0) \
                or abs(lo[1] - y0) > 1e-3 or abs(hi[1] - y1) > 1e-3 or abs((hi[0] - lo[0]) - fpl["outline"]["finger_width"]) > 1e-3:
            bad_mask.append(f)
    strips = {}
    for g in j1.GraphicalItems():
        if g.GetLayer() in (pcbnew.F_Mask, pcbnew.B_Mask) and g.Type() == pcbnew.PCB_SHAPE_T:
            bb = g.GetBoundingBox()
            lo = C.k2c(pcbnew.VECTOR2I(bb.GetLeft(), bb.GetBottom()))
            hi = C.k2c(pcbnew.VECTOR2I(bb.GetRight(), bb.GetTop()))
            strips[board.GetLayerName(g.GetLayer())] = (round(lo[0], 2), round(lo[1], 2), round(hi[0], 2), round(hi[1], 2))
    strip_ok = all(k in strips and strips[k][0] <= 0 and strips[k][2] >= fpl["outline"]["width"] and strips[k][1] <= 0
                   and strips[k][3] >= y1 for k in ("F.Mask", "B.Mask"))
    check(G, "%d finger pads 0.35 x 2.55 mm (y %.2f .. %.2f), each with a solder-mask opening on its own face; mask-free strip over the whole "
          "finger row on both faces %s" % (n, y0, y1, strips), n == 260 and not bad_mask and strip_ok, str(bad_mask[:8]))
    # copper pours
    pb = fpl["plane_plan"]["pull_back"]
    limit = {"F.Cu": pb["F.Cu / B.Cu pours"]["finger_edge"], "B.Cu": pb["F.Cu / B.Cu pours"]["finger_edge"]}
    for l in ("In1.Cu", "In2.Cu", "In3.Cu", "In4.Cu"):
        limit[l] = pb[l]["finger_edge"]
    low = {}
    edge_min = {}
    inner = outline.buffer(-0.3 + 2e-3, join_style=2) if outline is not None else None
    zinfo = []
    for z in board.Zones():
        if z.GetIsRuleArea():
            continue
        for lid in z.GetLayerSet().Seq():
            lname = board.GetLayerName(lid)
            polys = poly_from_set(z.GetFilledPolysList(lid))
            if not polys:
                zinfo.append((z.GetZoneName(), lname, z.GetNetname(), 0.0))
                continue
            u = unary_union(polys)
            zinfo.append((z.GetZoneName(), lname, z.GetNetname(), round(u.area, 1)))
            low[lname] = min(low.get(lname, 99), u.bounds[1])
            if inner is not None and not inner.contains(u):
                out_ = u.difference(inner).area
                if out_ > 0.005:            # the filler approximates the arcs of the outline with segments (max error 0.005 mm): slivers of a few 0.0001 mm2
                    edge_min[lname] = round(out_, 4)
    over = {l: round(v, 3) for l, v in low.items() if v < limit[l] - 2e-3}
    check(G, "no copper pour over the fingers: lowest filled copper per layer %s against the pull-back table %s"
          % ({k: round(v, 3) for k, v in sorted(low.items())}, {k: limit[k] for k in sorted(low)}), not over, str(over))
    check(G, "every pour keeps 0.3 mm from the outline, key slot included", not edge_min, "area outside the 0.3 mm inset per layer: %s" % edge_min)
    ra = [z for z in board.Zones() if z.GetIsRuleArea() and C.stage_of(z) == "s90_zones"]
    named = {a_["name"]: a_ for a_ in spec["floorplan"].get("rule_areas", [])}       # areas that restrict nothing: named for a rule of the .kicad_dru
    keep = [z for z in ra if z.GetZoneName() not in named]
    names = sorted(z.GetZoneName() for z in keep)
    check(G, "finger-area keep-outs present: %s" % names, len(keep) == 2 and all(z.GetDoNotAllowZoneFills() for z in keep))
    nm_bad = []
    dru = open(C.DRU).read()
    for nm, a_ in named.items():
        zz = [z for z in ra if z.GetZoneName() == nm]
        if len(zz) != 1:
            nm_bad.append((nm, "%d on the board" % len(zz)))
            continue
        z = zz[0]
        bb = z.GetBoundingBox()
        (x0, y1), (x1, y0) = C.k2c(bb.GetOrigin()), C.k2c(bb.GetEnd())
        free = not (z.GetDoNotAllowTracks() or z.GetDoNotAllowVias() or z.GetDoNotAllowPads() or z.GetDoNotAllowZoneFills() or z.GetDoNotAllowFootprints())
        if not free or [board.GetLayerName(l) for l in z.GetLayerSet().Seq()] != a_["layers"] or max(abs(x0 - a_["x"][0]), abs(x1 - a_["x"][1]),
                                                                                                       abs(y0 - a_["y"][0]), abs(y1 - a_["y"][1])) > 2e-3 \
                or ("intersectsArea('%s')" % nm not in dru and "enclosedByArea('%s')" % nm not in dru):
            nm_bad.append((nm, "restrictions / layers / extent / not used by a rule", (x0, y0, x1, y1)))
    check(G, "named rule areas of the spec (%s): on the board where the spec has them, restricting nothing, each used by a rule of the .kicad_dru"
          % ", ".join(sorted(named)), not nm_bad, str(nm_bad))
    # foreign copper on a finger: any track or via of another net touching a finger pad
    foreign = []
    for p in j1.Pads():
        if p.GetNumber() == "":
            continue
        bb = p.GetBoundingBox()
        for t in board.GetTracks():
            # the pad's own copper layer (PAD.GetPrincipalLayer() answers F.Cu for a back-side finger)
            if t.GetNetCode() != p.GetNetCode() and t.IsOnLayer(pcbnew.F_Cu if p.IsOnLayer(pcbnew.F_Cu) else pcbnew.B_Cu) \
                    and t.GetBoundingBox().Intersects(bb) and t.HitTest(bb, False):
                foreign.append((p.GetNumber(), t.GetNetname()))
    check(G, "no track or via of another net on a finger pad", not foreign, str(foreign[:6]))
    # planes
    P = "5 planes"
    zmap = collections.defaultdict(list)
    for name, lname, netname, a in zinfo:
        zmap[lname].append((netname, a))
    want = {"In1.Cu": {"GND"}, "In2.Cu": {"GND", "V1P2"}, "In4.Cu": {"GND", "VDD2H"}}
    ok = all({nn for nn, a in zmap[l] if a > 0} == want[l] for l in want)
    check(P, "plane zones filled: %s" % {l: sorted(zmap[l]) for l in sorted(zmap)}, ok)
    isl_bad = []
    isl_pieces = {}
    for key, netname in (("vdd2h_island", "VDD2H"), ("v1p2_island", "V1P2")):
        isl = fpl["power"][key]
        lid_ = C.CU[isl["layer"]]
        allowed = box(isl["x"][0], isl["y"][0], isl["x"][1], isl["y"][1])
        for e_ in (isl.get("extensions") or {}).values():
            allowed = allowed.union(box(e_["x"][0], e_["y"][0], e_["x"][1], e_["y"][1]))
        zs_ = [z for z in board.Zones() if not z.GetIsRuleArea() and z.GetNetname() == netname and z.IsOnLayer(lid_)]
        u = unary_union([p_ for z in zs_ for p_ in poly_from_set(z.GetFilledPolysList(lid_))]) if zs_ else None
        isl_pieces[key] = 0 if u is None or u.is_empty else (len(u.geoms) if u.geom_type == "MultiPolygon" else 1)
        if u is None or u.is_empty or u.difference(allowed.buffer(1e-3)).area > 1e-4:
            isl_bad.append((netname, [round(v, 2) for v in (u.bounds if u is not None and not u.is_empty else (0, 0, 0, 0))]))
    v1i = fpl["power"]["v1p2_island"]
    check(P, "islands stay inside their spec shapes and are one piece each: VDD2H on %s (x %s y %s); V1P2 on %s (x %s y %s, with the bridge north of the "
          "key slot x %s to y = %.2f and the feed tab x %s to y = %.2f): the two halves beside the key slot are joined (%d piece)"
          % (fpl["power"]["vdd2h_island"]["layer"], fpl["power"]["vdd2h_island"]["x"], fpl["power"]["vdd2h_island"]["y"], v1i["layer"], v1i["x"], v1i["y"],
             v1i["extensions"]["bridge"]["x"], v1i["extensions"]["bridge"]["y"][1], v1i["extensions"]["feed_tab"]["x"], v1i["extensions"]["feed_tab"]["y"][1],
             isl_pieces["v1p2_island"]),
          not isl_bad and isl_pieces["v1p2_island"] == 1 and isl_pieces["vdd2h_island"] == 1 and v1i["layer"] == "In2.Cu", str(isl_bad))
    in2 = [t for t in board.GetTracks() if t.Type() != pcbnew.PCB_VIA_T and t.GetLayer() in (pcbnew.In1_Cu, pcbnew.In2_Cu, pcbnew.In4_Cu)]
    check(P, "no tracks on the plane layers In1 / In2 / In4", not in2, "%d" % len(in2))


# ------------------------------------------------------------------------------------------------ routing helpers
# Used by the gates of the routing stages.  Everything is taken from the board file (tracks, arcs, vias, pads, zone
# fills); only targets, host delays and ps/mm come from the spec.
from shapely.geometry import LineString      # noqa: E402
import tuning                                # noqa: E402  (scripts/tuning.py: bend_shortfall, the bend model of the spec)


def stage_tracks(board, stage):
    return [t for t in board.GetTracks() if t.Type() != pcbnew.PCB_VIA_T and C.stage_of(t) == stage]


def _arc_centre(a, m, b):
    d = 2 * (a[0] * (m[1] - b[1]) + m[0] * (b[1] - a[1]) + b[0] * (a[1] - m[1]))
    ux = ((a[0] ** 2 + a[1] ** 2) * (m[1] - b[1]) + (m[0] ** 2 + m[1] ** 2) * (b[1] - a[1]) + (b[0] ** 2 + b[1] ** 2) * (a[1] - m[1])) / d
    uy = ((a[0] ** 2 + a[1] ** 2) * (b[0] - m[0]) + (m[0] ** 2 + m[1] ** 2) * (a[0] - b[0]) + (b[0] ** 2 + b[1] ** 2) * (m[0] - a[0])) / d
    return (ux, uy)


def track_pts(t, step=0.004):
    """Centre line of a track or arc as points in the card frame (arcs sampled every 'step' mm)."""
    a, b = C.k2c(t.GetStart()), C.k2c(t.GetEnd())
    if t.Type() != pcbnew.PCB_ARC_T:
        return [a, b]
    m = C.k2c(t.GetMid())
    c = _arc_centre(a, m, b)
    r = math.hypot(a[0] - c[0], a[1] - c[1])
    a0, a1, a2 = (math.atan2(q[1] - c[1], q[0] - c[0]) for q in (a, m, b))
    sweep = (a2 - a0) % (2 * math.pi)
    if (a1 - a0) % (2 * math.pi) > sweep:           # the mid point is on the other way round
        sweep -= 2 * math.pi
    n = max(6, int(abs(sweep) * r / step))
    return [a] + [(c[0] + r * math.cos(a0 + sweep * k / n), c[1] + r * math.sin(a0 + sweep * k / n)) for k in range(1, n)] + [b]


def track_len(t):
    if t.Type() == pcbnew.PCB_ARC_T:
        return C.MM(t.GetLength())
    return C.MM((t.GetEnd() - t.GetStart()).EuclideanNorm())


def end_dirs(t):
    """Unit vectors that point from each end of the item into it: (at the start, at the end)."""
    pts = track_pts(t, 0.0005) if t.Type() == pcbnew.PCB_ARC_T else track_pts(t)
    def unit(p, q):
        d = math.hypot(q[0] - p[0], q[1] - p[1])
        return ((q[0] - p[0]) / d, (q[1] - p[1]) / d)
    return unit(pts[0], pts[1]), unit(pts[-1], pts[-2])


def nkey(xy, layer):
    return (round(xy[0], 4), round(xy[1], 4), layer)


def vertex_angles(board, tracks):
    """Interior angle at every point where two track items of one net meet on one layer.  180 = straight on.
    Returns (number of vertices, smallest angle, list of (net, layer, xy, angle) below 90 degrees, histogram)."""
    at = collections.defaultdict(list)
    for t in tracks:
        lay = board.GetLayerName(t.GetLayer())
        d0, d1 = end_dirs(t)
        at[(t.GetNetname(),) + nkey(C.k2c(t.GetStart()), lay)].append(d0)
        at[(t.GetNetname(),) + nkey(C.k2c(t.GetEnd()), lay)].append(d1)
    n, worst, bad, hist = 0, 180.0, [], collections.Counter()
    for k, dirs in at.items():
        for i in range(len(dirs)):
            for j in range(i + 1, len(dirs)):
                n += 1
                cosang = max(-1.0, min(1.0, dirs[i][0] * dirs[j][0] + dirs[i][1] * dirs[j][1]))
                ang = math.degrees(math.acos(cosang))
                hist[int(round(ang / 5.0) * 5)] += 1
                worst = min(worst, ang)
                if ang < 90 - 0.05:
                    bad.append((k[0], k[3], k[1:3], round(ang, 1)))
    return n, worst, bad, dict(sorted(hist.items()))


def copper_path(board, spec, nets, start, end, bridges=()):
    """Walks the copper of 'nets' from start to end (each (x, y, layer)) and returns what the signal passes:
    {"ps", "mm", "layers": {layer: [mm, ps]}, "vias": [...], "bridges": [...]} or None if there is no path.
    Tracks and arcs count with their centre-line length and the ps/mm of their layer and width; a via counts with
    the barrel between the copper layers it is entered and left on (mid-copper heights of the stackup);
    bridges = [(node a, node b, mm, ps, label)] are the parts a signal passes through (pad centre to pad centre)."""
    import heapq
    z = C.stackup_z()
    d_via = spec["rules"]["delay_ps_per_mm"]["through via (per mm of barrel)"]
    adj = collections.defaultdict(list)
    codes = {board.GetNetcodeFromNetname(n): n for n in nets}
    for t in board.GetTracks():
        if t.GetNetCode() not in codes or t.Type() == pcbnew.PCB_VIA_T:
            continue
        lay = board.GetLayerName(t.GetLayer())
        ln = track_len(t)
        ps = ln * C.delay_ps_per_mm(lay, C.MM(t.GetWidth()), codes[t.GetNetCode()])
        a, b = nkey(C.k2c(t.GetStart()), lay), nkey(C.k2c(t.GetEnd()), lay)
        adj[a].append((b, ln, ps, ("track", lay, t)))
        adj[b].append((a, ln, ps, ("track", lay, t)))
    for a, b, mm, ps, label in bridges:
        a, b = nkey(a[:2], a[2]), nkey(b[:2], b[2])
        adj[a].append((b, mm, ps, ("bridge", label)))
        adj[b].append((a, mm, ps, ("bridge", label)))
    start, end = nkey(start[:2], start[2]), nkey(end[:2], end[2])
    for k in (start, end):
        adj.setdefault(k, [])
    for t in board.GetTracks():
        if t.GetNetCode() not in codes or t.Type() != pcbnew.PCB_VIA_T:
            continue
        xy = C.k2c(t.GetPosition())
        here = [l for l in C.CU_ORDER if nkey(xy, l) in adj]
        size = (round(C.MM(t.GetWidth(pcbnew.F_Cu)), 3), round(C.MM(t.GetDrill()), 3))
        for i, la in enumerate(here):
            for lb in here[i + 1:]:
                span = abs(z[la] - z[lb])
                info = ("via", (round(xy[0], 3), round(xy[1], 3)), la, lb, round(span, 4), size)
                adj[nkey(xy, la)].append((nkey(xy, lb), span, span * d_via, info))
                adj[nkey(xy, lb)].append((nkey(xy, la), span, span * d_via, info))
    best = {start: (0.0, None, None)}
    heap = [(0.0, start)]
    while heap:
        d, u = heapq.heappop(heap)
        if d > best[u][0] + 1e-12:
            continue
        if u == end:
            break
        for v, mm, ps, what in adj[u]:
            if v not in best or d + ps < best[v][0] - 1e-12:
                best[v] = (d + ps, u, (mm, ps, what))
                heapq.heappush(heap, (d + ps, v))
    if end not in best:
        return None
    out = {"ps": 0.0, "mm": 0.0, "layers": {}, "vias": [], "bridges": [], "items": []}
    u = end
    while best[u][1] is not None:
        mm, ps, what = best[u][2]
        out["ps"] += ps
        out["mm"] += mm
        if what[0] == "track":
            e = out["layers"].setdefault(what[1], [0.0, 0.0])
            e[0] += mm
            e[1] += ps
            out["items"].append((what[2], u))          # the item and the node at which the signal leaves it
        elif what[0] == "via":
            out["vias"].append({"xy": what[1], "layers": (what[2], what[3]), "span_mm": what[4], "ps": ps, "size": what[5]})
        else:
            out["bridges"].append((what[1], mm, ps))
        if what[0] != "track":
            out["items"].append((None, u))
        u = best[u][1]
    out["vias"].reverse()
    out["items"].reverse()
    return out


def bend_corrections(board, spec, items, layer="In3.Cu"):
    """Electrical shortening of the bends on one layer along a copper path (items of copper_path, in signal order),
    with rules.bend_model (scripts/tuning.py), everything read from the board: the angle turned by every arc and at
    every vertex between two consecutive straight items, and the coupling between adjacent antiparallel legs
    (horizontal straight items between two arcs; a leg reaches from the far end of the arc before it to the far end
    of the arc after it).  Returns (mm, number of arcs, number of mitred vertices, arc radii, turn in rad, leg mm)."""
    model = spec["rules"]["bend_model"]
    mm, n_arc, n_vtx, radii, turned = 0.0, 0, 0, set(), 0.0
    seq = []                    # (item, entry point, exit point, travel direction in, travel direction out) or None
    for t, node_out in items:
        if t is None or board.GetLayerName(t.GetLayer()) != layer:
            seq.append(None)
            continue
        a, b = C.k2c(t.GetStart()), C.k2c(t.GetEnd())
        out_is_end = math.dist(b, node_out[:2]) < 1e-3
        d0, d1 = end_dirs(t)                                    # unit vectors pointing from each end into the item
        if out_is_end:
            seq.append((t, a, b, d0, (-d1[0], -d1[1])))
        else:
            seq.append((t, b, a, d1, (-d0[0], -d0[1])))
    legs, w_leg = [], None
    for i, e in enumerate(seq):
        if e is None:
            continue
        t, p_in, p_out, t_in, t_out = e
        w = C.MM(t.GetWidth())
        prev = seq[i - 1] if i > 0 else None
        nxt = seq[i + 1] if i + 1 < len(seq) else None
        if prev is not None:
            turn = math.acos(max(-1.0, min(1.0, prev[4][0] * t_in[0] + prev[4][1] * t_in[1])))
            if turn > math.radians(0.5):
                mm += tuning.bend_shortfall(model, w, turn)
                turned += turn
                n_vtx += 1
        if t.Type() == pcbnew.PCB_ARC_T:
            a, b = C.k2c(t.GetStart()), C.k2c(t.GetEnd())
            c = _arc_centre(a, C.k2c(t.GetMid()), b)
            r = math.hypot(a[0] - c[0], a[1] - c[1])
            ang = abs(t.GetAngle().AsRadians())
            mm += tuning.bend_shortfall(model, w, ang, r)
            turned += ang
            n_arc += 1
            radii.add(round(r, 3))
        elif abs(p_in[1] - p_out[1]) < 1e-6 and prev is not None and nxt is not None \
                and prev[0].Type() == pcbnew.PCB_ARC_T and nxt[0].Type() == pcbnew.PCB_ARC_T:
            legs.append((prev[1][0], nxt[2][0], p_in[1]))
            w_leg = w
    leg_mm = tuning.leg_coupling(model, w_leg, legs) if legs else 0.0
    return mm + leg_mm, n_arc, n_vtx, sorted(radii), turned, leg_mm


def gnd_fill(board, layer, area, net="GND"):
    """Zone fill of one net (GND unless given) on one layer inside area (shapely box), as one geometry."""
    lid = C.CU[layer]
    polys = []
    for zn in board.Zones():
        if zn.GetIsRuleArea() or zn.GetNetname() != net or not zn.IsOnLayer(lid):
            continue
        polys += [p.intersection(area) for p in poly_from_set(zn.GetFilledPolysList(lid)) if p.intersects(area)]
    return unary_union(polys) if polys else Polygon()


# ------------------------------------------------------------------------------------------------ 6 command bus
def gate_cmd(board, spec, drc):
    G = "6 command"
    STAGE = "s20_cmd"
    if not stage_tracks(board, STAGE):
        print("        [%s] stage %s has not run: gate skipped" % (G, STAGE))
        return
    fpl = spec["floorplan"]
    cmd = fpl["command"]
    g = next(q for q in spec["groups"] if q["name"] == "CMD")
    # every track of the group's nets, whichever stage or hand drew it, and the GND links of the stage
    gn = set(g["nets"]) | set(g["finger_side_nets"])
    tracks = [t for t in board.GetTracks() if t.Type() != pcbnew.PCB_VIA_T and (t.GetNetname() in gn or C.stage_of(t) == STAGE)]
    ncl = spec["rules"]["netclasses"]
    cols = cmd["columns"]
    order = sorted(cols, key=lambda n: cols[n]["x"])
    fside = dict(zip(g["nets"], g["finger_side_nets"]))
    group_nets = set(g["nets"]) | set(g["finger_side_nets"])
    parts = C.parts()
    h_in2 = next(l["thickness_mm"] for i, l in enumerate(spec["stackup"]["layers"])
                 if spec["stackup"]["layers"][i + 1].get("layer") == "In3.Cu")          # dielectric between In2 and In3
    w_se = ncl["CMD_SE"]["layers"]["In3.Cu"]["width"]
    w_ck = ncl["CMD_CK"]["layers"]["In3.Cu"]["width"]
    finger_top_y = fpl["outline"]["finger_pad_y"][1]
    by_net = collections.defaultdict(list)
    for t in tracks:
        by_net[t.GetNetname()].append(t)

    # ---- connectivity ---------------------------------------------------------------------------------------
    unc = []
    for u in (drc or {}).get("unconnected_items", []):
        nn = {m for i in u["items"] for m in re.findall(r"\[([^\]]+)\]", i["description"])}
        if nn & group_nets:
            unc.append(sorted(nn & group_nets))
    check(G, "connectivity: no unconnected item on the %d nets of the group (%d ball-side, %d finger-side) in the DRC report"
          % (len(group_nets), len(g["nets"]), len(g["finger_side_nets"])), drc is not None and not unc, str(unc[:6]))
    vias = [t for t in board.GetTracks() if t.Type() == pcbnew.PCB_VIA_T]
    gnd_at = {(round(C.k2c(v.GetPosition())[0], 3), round(C.k2c(v.GetPosition())[1], 3)) for v in vias if v.GetNetname() == "GND"}
    shunt_bad = []
    res = {}
    for net in order:
        ser = sh = None
        for p in parts.values():
            pins = set(p["pins"].values())
            if len(p["pins"]) == 2 and net in pins:
                if fside[net] in pins:
                    ser = p["ref"]
                elif "GND" in pins:
                    sh = p["ref"]
        res[net] = (ser, sh)
        pg = next(p for p in C.footprint(board, sh).Pads() if p.GetNumber() and p.GetNetname() == "GND")
        lay = "F.Cu" if pg.IsOnLayer(pcbnew.F_Cu) else "B.Cu"
        pos = C.k2c(pg.GetPosition())
        via_xy = (round(cols[net]["gnd_via_x"], 3), round(cmd["rows_y"]["gnd_via"], 3))
        pth = copper_path(board, spec, ["GND"], pos + (lay,), via_xy + (lay,)) if via_xy in gnd_at else None
        if pth is None or pth["mm"] > 1.0:
            shunt_bad.append((net, sh))
    check(G, "every shunt resistor reaches its fence via (a GND via on the board) by a track of less than 1 mm on its own face",
          not shunt_bad, str(shunt_bad))

    # ---- delay: the copper path of every net, measured on the board ---------------------------------------------
    hp, tgt = g["per_net_host_plus_pkg_ps"], g["per_net_target_card_delay_ps"]
    rep, no_path = {}, []
    for net in order:
        c = cols[net]
        ser, sh = res[net]
        fp = C.footprint(board, ser)
        p1 = next(p for p in fp.Pads() if p.GetNumber() and p.GetNetname() == fside[net])
        p2 = next(p for p in fp.Pads() if p.GetNumber() and p.GetNetname() == net)
        lay = "F.Cu" if p1.IsOnLayer(pcbnew.F_Cu) else "B.Cu"
        a, b = C.k2c(p1.GetPosition()), C.k2c(p2.GetPosition())
        jf = C.pad(board, "J1.%d" % c["finger"])
        bb = jf.GetBoundingBox()
        # the track end inside the finger pad
        starts = [q for t in by_net[fside[net]] for q in (t.GetStart(), t.GetEnd()) if bb.Contains(q)]
        if len(starts) != 1:
            no_path.append((net, "stub start on finger %d" % c["finger"]))
            continue
        s0 = C.k2c(starts[0])
        stub_w = C.MM(by_net[fside[net]][0].GetWidth())
        d_outer = C.delay_ps_per_mm(lay, stub_w, fside[net])
        body = math.dist(a, b)
        pth = copper_path(board, spec, [net, fside[net]], s0 + (lay,), C.ball(board, c["ball"]) + ("F.Cu",),
                          bridges=[(a + (lay,), b + (lay,), body, body * d_outer, ser)])
        if pth is None:
            no_path.append((net, "no copper path from finger %d to ball %s" % (c["finger"], c["ball"])))
            continue
        inpad = finger_top_y - s0[1]            # the stub starts inside the finger pad; the delay counts from the pad top
        pth["ps"] -= inpad * d_outer
        pth["mm"] -= inpad
        pth["layers"][lay][0] -= inpad
        pth["layers"][lay][1] -= inpad * d_outer
        pth["face"] = lay
        # In3 bends count with their electrical length (rules.bend_model), read from the arcs and vertices on the board
        corr, n_arc, n_vtx, radii, turned, leg_mm = bend_corrections(board, spec, pth["items"])
        # ... plus what HFSS found for this net's own tuning feature beyond the formula (spec: per_net[net].hfss)
        pn = g["tuning"]["per_net"].get(net, {})
        pth["hfss_mm"] = pn.get("hfss_residual_mm", 0.0)
        corr -= pth["hfss_mm"]
        pth["bend_mm"], pth["arcs"], pth["vertices"], pth["radii"], pth["turn"], pth["leg_mm"] = corr, n_arc, n_vtx, radii, turned, leg_mm
        pth["ps"] -= corr * C.delay_ps_per_mm("In3.Cu", 0.15)
        rep[net] = pth
    check(G, "every net has one copper path finger -> series resistor -> node via -> In3 -> via-in-pad -> ball", not no_path, str(no_path))
    if no_path:
        return
    err = {n: rep[n]["ps"] - tgt[n] for n in order}
    total = {n: hp[n] + rep[n]["ps"] for n in order}
    tol, tol_pair = g["tolerance_ps"], g["intra_pair"]["tolerance_ps"]
    bm = spec["rules"]["bend_model"]
    d_in3 = C.delay_ps_per_mm("In3.Cu", 0.15)
    print("        delay measured on the board (tracks and arcs x ps/mm of layer and width, series resistor pad to pad, via barrels")
    print("        between mid-copper heights; In3 bends by their electrical length: arcs, mitred vertices and antiparallel legs read from")
    print("        the board, rules.bend_model k_mitre %.4f + %.3f w, k_arc0 %.3f, k_arc1 %.2f, k_leg %.2f, plus the HFSS residual of each tuning feature),"
          % (bm["k_mitre0"], bm["k_mitre1"], bm["k_arc0"], bm["k_arc1"], bm["k_leg"]))
    print("        from the finger top to the ball.  Target = %.2f ps total (FPGA die pad -> ball):" % g["total_delay_target_ps"])
    print("        net   finger ball  face  outer mm  In3 mm  vias mm  bends: arcs (r) / mitres  turned  HFSS mm  corr ps | card ps  target   error | host+pkg   total ps  to group mean")
    mean_total = sum(total.values()) / len(total)
    for n in order:
        r = rep[n]
        outer = sum(v[0] for k, v in r["layers"].items() if k != "In3.Cu") + sum(b_[1] for b_ in r["bridges"])
        print("        %-5s %4d   %-4s  %s  %7.3f  %7.3f  %6.3f   %2d %-11s / %d      %4.0f deg  %+7.4f  %6.2f | %7.2f %7.2f  %+6.2f | %7.1f   %8.2f   %+6.2f"
              % (n, cols[n]["finger"], cols[n]["ball"], r["face"][0], outer, r["layers"]["In3.Cu"][0], sum(v["span_mm"] for v in r["vias"]),
                 r["arcs"], "(%s)" % "/".join("%.2f" % q for q in r["radii"]) if r["radii"] else "", r["vertices"], math.degrees(r["turn"]),
                 r["hfss_mm"], -r["bend_mm"] * d_in3,
                 r["ps"], tgt[n], err[n], hp[n], total[n], total[n] - mean_total))
    spread = max(total.values()) - min(total.values())
    worst_corr = max(order, key=lambda n: rep[n]["bend_mm"])
    check(G, "delay: every net within +/-%.1f ps of its target with no common offset (largest error %+.2f ps on %s); spread of the total "
          "delay over the ten nets %.2f ps (electrical lengths; the largest bend correction in it is %.2f ps, %s)"
          % (tol, max(err.values(), key=abs), max(err, key=lambda n: abs(err[n])), spread, rep[worst_corr]["bend_mm"] * d_in3, worst_corr),
          all(abs(e) <= tol for e in err.values()) and spread <= tol)
    # the same without the bend model (centre-line lengths only): how far the result depends on it
    raw = {n: total[n] + rep[n]["bend_mm"] * d_in3 for n in order}
    print("        without the bend model (centre lines only) the totals would read: " + ", ".join("%s %.2f" % (n, raw[n]) for n in order))
    corr_ps = {n: rep[n]["bend_mm"] * d_in3 for n in order}
    dcorr = max(corr_ps.values()) - min(corr_ps.values())
    check(G, "delay: bend corrections %.2f .. %.2f ps per net (CK pair %.2f / %.2f ps); what matters for the match is their difference, "
          "%.2f ps: even an error of 20 %% in them would move no net against another by more than %.2f ps (limit 0.5 ps, a quarter of the "
          "group tolerance); the formula is fitted to HFSS and every tuning feature has an HFSS run of its own (below); coupling between "
          "legs is at most %.2f ps of it"
          % (min(corr_ps.values()), max(corr_ps.values()), corr_ps["CK_P"], corr_ps["CK_N"], dcorr, 0.2 * dcorr,
             max(rep[n]["leg_mm"] for n in order) * d_in3), 0.2 * dcorr <= 0.5)
    skew_ps = total["CK_P"] - total["CK_N"]
    check(G, "delay: CK_P to CK_N %+.2f ps at the balls including the host (limit +/-%.1f ps); card alone CK_P %.2f ps, CK_N %.2f ps"
          % (skew_ps, tol_pair, rep["CK_P"]["ps"], rep["CK_N"]["ps"]), abs(skew_ps) <= tol_pair)

    # ---- via structure ----------------------------------------------------------------------------------------
    vstruct = {n: [(v["size"], v["layers"], v["span_mm"]) for v in rep[n]["vias"]] for n in order}
    vip = (fpl["U1"]["via_in_pad"]["size"]["diameter"], fpl["U1"]["via_in_pad"]["size"]["drill"])
    thr = (spec["rules"]["vias"]["through_signal"]["diameter"], spec["rules"]["vias"]["through_signal"]["drill"])
    vbad = [n for n in order if len(vstruct[n]) != 2 or vstruct[n][0][0] != thr or vstruct[n][1][0] != vip
            or set(vstruct[n][1][1]) != {"F.Cu", "In3.Cu"} or "In3.Cu" not in vstruct[n][0][1] or rep[n]["face"] not in vstruct[n][0][1]]
    faces = collections.Counter(rep[n]["face"] for n in order)
    zz = C.stackup_z()
    check(G, "vias: every net passes exactly two, a %g / %g node via (series face -> In3: %.3f mm from F.Cu on %d nets, %.3f mm from B.Cu on %d nets; "
          "the rest of the barrel leads to the shunt on the other face, no open stub) and the %g / %g via-in-pad (In3 -> ball, %.3f mm; "
          "open stub In3 -> B.Cu %.3f mm); CK_P and CK_N have the same structure (both from B.Cu)"
          % (thr[0], thr[1], zz["In3.Cu"] - zz["F.Cu"], faces["F.Cu"], zz["B.Cu"] - zz["In3.Cu"], faces["B.Cu"], vip[0], vip[1],
             zz["In3.Cu"] - zz["F.Cu"], zz["B.Cu"] - zz["In3.Cu"]),
          not vbad and vstruct["CK_P"] == [(s_, l_, m_) for s_, l_, m_ in vstruct["CK_N"]], str(vbad))

    # ---- angles -----------------------------------------------------------------------------------------------
    nv, worst, bad, hist = vertex_angles(board, tracks)
    odd = []
    for t in tracks:
        if t.Type() == pcbnew.PCB_TRACE_T:
            a, b = C.k2c(t.GetStart()), C.k2c(t.GetEnd())
            dx, dy = abs(a[0] - b[0]), abs(a[1] - b[1])
            if not (dx < 1e-6 or dy < 1e-6 or abs(dx - dy) < 2.5e-6):
                odd.append((t.GetNetname(), a, b))
    arcs = [t for t in tracks if t.Type() == pcbnew.PCB_ARC_T]
    arc_ang = sorted({round(abs(math.degrees(t.GetAngle().AsRadians()))) for t in arcs})
    check(G, "angles: %d vertices between consecutive track items, smallest interior angle %.1f degrees (%d below 90); histogram %s; "
          "%d straight segments at 0 / 45 / 90 / 135 degrees (%d at other angles); %d arcs (sweep %s degrees), tangent to their neighbours"
          % (nv, worst, len(bad), hist, len(tracks) - len(arcs), len(odd), len(arcs), arc_ang),
          not bad and not odd and all(abs(k - 180) <= 0 or k in (90, 135) for k in hist), "%s %s" % (bad[:6], odd[:4]))

    # ---- crossings and spacing --------------------------------------------------------------------------------
    lines = []          # (net, layer, width, LineString, item)
    for t in tracks:
        lines.append((t.GetNetname(), board.GetLayerName(t.GetLayer()), C.MM(t.GetWidth()), LineString(track_pts(t)), t))
    cross, selfx = [], []
    gap_min = {}        # (layer, where, class pair) -> (gap, description)
    from shapely.geometry import box as _box
    from shapely.ops import nearest_points
    y_field = C.ball(board, "A1")[1] - 0.35         # south edge of the via field of U1 (row A via pads less the clearance)
    parts_of = {"outside the via field": _box(0, 0, 70, y_field), "inside the via field": _box(0, y_field, 70, 40)}

    def cls(n):
        return "CK" if n in ("CK_P", "CK_N") else ("SE" if n in g["nets"] else ("GND" if n == "GND" else "finger side"))
    for i, (na, la, wa, ga, ta) in enumerate(lines):
        for nb, lb, wb, gb, tb in lines[i + 1:]:
            if la != lb:
                continue
            if na == nb:
                if ga.crosses(gb):
                    selfx.append((na, la))
                continue
            if "GND" in (na, nb):
                continue
            if ga.distance(gb) < 1e-9:
                cross.append((na, nb, la))
            if ga.distance(gb) > 1.5:
                continue
            for where, bx in parts_of.items():
                pa, pb = ga.intersection(bx), gb.intersection(bx)
                if pa.is_empty or pb.is_empty or pa.geom_type == "Point" or pb.geom_type == "Point":
                    continue
                gap = pa.distance(pb) - wa / 2 - wb / 2
                key = (la, where) + tuple(sorted((cls(na), cls(nb))))
                if key not in gap_min or gap < gap_min[key][0]:
                    pt = nearest_points(pa, pb)[0]
                    gap_min[key] = (gap, "%s / %s at (%.2f, %.2f)" % (na, nb, pt.x, pt.y))
    check(G, "crossings: no track of the group crosses or touches a track of another net of the group on any layer (%d track items, "
          "%d nets); no net crosses itself" % (len(lines), len(by_net)), not cross and not selfx, "%s %s" % (cross[:6], selfx[:6]))
    gi = lambda *k: gap_min.get(k, (9.9, "-"))      # noqa: E731
    o, f = "outside the via field", "inside the via field"
    se_se, se_ck, ck_ck = gi("In3.Cu", o, "SE", "SE"), gi("In3.Cu", o, "CK", "SE"), min(gi("In3.Cu", o, "CK", "CK"), gi("In3.Cu", f, "CK", "CK"))
    check(G, "spacing on In3 south of the via field (y < %.2f, copper edge to copper edge): CA / CS to CA / CS >= %.3f mm (%s), limit %.2f = 3 h; "
          "CA / CS to the CK pair >= %.3f mm (%s), limit %.2f; CK_P to CK_N >= %.4f mm (pair gap %.2f)"
          % (y_field, se_se[0], se_se[1], g["tuning"]["min_gap_to_neighbour"], se_ck[0], se_ck[1], ncl["CMD_CK"]["pair_to_other_track_min"],
             ck_ck[0], ncl["CMD_CK"]["layers"]["In3.Cu"]["gap"]),
          se_se[0] >= g["tuning"]["min_gap_to_neighbour"] - 1e-6 and se_ck[0] >= ncl["CMD_CK"]["pair_to_other_track_min"] - 1e-6
          and ck_ck[0] >= ncl["CMD_CK"]["layers"]["In3.Cu"]["gap"] - 2e-6)
    fs, fc = gi("In3.Cu", f, "SE", "SE"), gi("In3.Cu", f, "CK", "SE")
    check(G, "spacing on In3 inside the via field (one trace per 0.8 mm channel): CA / CS to CA / CS >= %.3f mm (%s: the 0.4 mm jog into "
          "a ball), CA / CS to a CK leg >= %.3f mm (%s: the split around H9); limit: the class clearance %.2f mm"
          % (fs[0], fs[1], fc[0], fc[1], ncl["CMD_SE"]["clearance"]),
          min(fs[0], fc[0]) >= ncl["CMD_SE"]["clearance"] - 1e-6)
    outer = {k: v for k, v in gap_min.items() if k[0] != "In3.Cu"}
    print("        smallest gaps on the outer layers: " + "; ".join("%s %s to %s %.3f mm (%s)" % (k[0], k[2], k[3], v[0], v[1]) for k, v in sorted(outer.items())))

    # ---- tuning features (parallel shifts and rounded bumps) ------------------------------------------------------
    zone = cmd["in3_tuning_zone"]
    tun = g["tuning"]
    grid = zone["rows_y"]
    acc_bad, acc_rows, drawn = [], [], {}
    for net in order:
        its = [t for t in by_net[net] if board.GetLayerName(t.GetLayer()) == "In3.Cu"]
        a_ = [t for t in its if t.Type() == pcbnew.PCB_ARC_T]
        if net in ("CK_P", "CK_N"):
            if a_:
                acc_bad.append((net, "the pair must not be meandered"))
            continue
        plan = tun["per_net"][net]
        xc = cols[net]["x"]
        rows_y = set()
        for t in its:
            if t.Type() == pcbnew.PCB_ARC_T:
                (d0, d1) = end_dirs(t)
                for d, q in ((d0, t.GetStart()), (d1, t.GetEnd())):
                    if abs(d[1]) < 0.02:            # horizontal tangent: the end of a leg
                        rows_y.add(round(C.k2c(q)[1], 3))
            else:
                p_, q_ = C.k2c(t.GetStart()), C.k2c(t.GetEnd())
                if abs(p_[1] - q_[1]) < 1e-6:
                    rows_y.add(round(p_[1], 3))
        rows_y = sorted(rows_y)
        leg_gap = min((b_ - a__ for a__, b_ in zip(rows_y, rows_y[1:])), default=9.9) - w_se
        pts = [q for t in its for q in track_pts(t)]
        # the shifted run: the vertical straight items off the channel axis; the bumps are measured from it
        vert = [(C.k2c(t.GetStart()), C.k2c(t.GetEnd())) for t in its if t.Type() != pcbnew.PCB_ARC_T
                and abs(C.k2c(t.GetStart())[0] - C.k2c(t.GetEnd())[0]) < 1e-6]
        in_zone = [v for v in vert if min(v[0][1], v[1][1]) > tun["shift_y"][0] - 1e-6 and max(v[0][1], v[1][1]) < tun["shift_y"][1] + 1e-6]
        xs_v = sorted({round(v[0][0], 4) for v in in_zone})
        if plan["bumps"] and xs_v:
            x_run, x_tip = xs_v[0], xs_v[-1]          # all bumps point east
            shift, amp = x_run - xc, x_tip - x_run
        else:
            far = max(xs_v, key=lambda x: abs(x - xc)) if xs_v else xc
            shift, amp = far - xc, 0.0
        radii = sorted({round(math.hypot(*(C.k2c(t.GetStart())[k] - _arc_centre(C.k2c(t.GetStart()), C.k2c(t.GetMid()), C.k2c(t.GetEnd()))[k] for k in (0, 1))), 3)
                        for t in a_})
        sweeps = sorted({round(abs(math.degrees(t.GetAngle().AsRadians()))) for t in a_})
        ya = [q[1] for t in a_ for q in track_pts(t)]
        off = [q[1] for q in pts if abs(q[0] - xc) > 1e-4 and q[1] < C.ball(board, "A1")[1] - 0.45]
        why = []
        if ya and (min(ya) - w_se / 2 < zone["y"][0] - 1e-6 or max(ya) + w_se / 2 > zone["y"][1] + 1e-6):
            why.append("arc copper outside the zone: y %.3f .. %.3f" % (min(ya) - w_se / 2, max(ya) + w_se / 2))
        if off and (min(off) < tun["shift_y"][0] - 1e-6 or max(off) > tun["shift_y"][1] + 1e-6):
            why.append("off the channel axis outside y %s: %.3f .. %.3f" % (tun["shift_y"], min(off), max(off)))
        if leg_gap < 3 * h_in2 - 1e-6 or len(rows_y) != 2 * plan["bumps"] or any(min(abs(y - gy) for gy in grid) > 1e-3 for y in rows_y):
            why.append("legs %s (gap %.3f) are not %d legs on the common rows" % (rows_y, leg_gap, 2 * plan["bumps"]))
        if len(a_) != plan["arcs"] or (radii and (radii[0] < tun["radius_min"] - 1e-6 or radii != [plan["shape"]["radius"]])) or (sweeps and sweeps != [90]):
            why.append("%d arcs, radii %s, sweeps %s (plan: %d arcs of r %s)" % (len(a_), radii, sweeps, plan["arcs"], plan["shape"]["radius"]))
        if abs(shift - plan["shape"]["shift"]) > 0.02 or (plan["bumps"] and (abs(amp - plan["shape"]["amplitude"]) > 0.02 or amp < tun["amplitude_min"] - 1e-6)):
            why.append("shift %.3f / amplitude %.3f against the plan %.3f / %.3f" % (shift, amp, plan["shape"]["shift"], plan["shape"]["amplitude"]))
        drawn[net] = (shift, amp)
        if why:
            acc_bad.append((net, why))
        acc_rows.append("%s shift %+.3f%s" % (net, shift, ", %d bumps x %.3f r %s on rows %.2f..%.2f" % (plan["bumps"], amp, "/".join("%.2f" % r_ for r_ in radii),
                                                                                                   rows_y[0], rows_y[-1]) if plan["bumps"] and rows_y else ""))
    check(G, "tuning features: only on CA / CS; parallel shifts inside y %.2f .. %.2f, bump copper inside the zone y %.2f .. %.2f; legs on the "
          "common rows, %.2f mm apart = gap %.2f mm >= 3 h (h = %.2f mm to In2); arcs of 90 degrees with centre radius >= %.2f mm (as planned, "
          "none reduced); amplitude >= %.2f mm; shift and amplitude within 0.02 mm of the plan"
          % (tun["shift_y"][0], tun["shift_y"][1], zone["y"][0], zone["y"][1], tun["leg_pitch"], tun["leg_pitch"] - w_se, h_in2, tun["radius_min"],
             tun["amplitude_min"]), not acc_bad, str(acc_bad))
    print("        " + "; ".join(acc_rows))
    hf_bad, hf_rows = [], []
    for net in order:
        if net in ("CK_P", "CK_N"):
            continue
        plan = tun["per_net"][net]
        hf = plan.get("hfss")
        if not hf:
            hf_bad.append((net, "no HFSS run of its feature"))
            continue
        d_s, d_a = abs(drawn[net][0] - hf["simulated_shift"]), abs(drawn[net][1] - hf["simulated_amplitude"]) if plan["bumps"] else 0.0
        if d_s > 0.03 or d_a > 0.03 or (plan["bumps"] and (hf["simulated_radius"] != plan["shape"]["radius"] or hf["simulated_rows"] != plan["shape"]["rows"])) \
                or abs(hf["residual_mm"]) > 0.08:
            hf_bad.append((net, round(d_s, 3), round(d_a, 3), hf["residual_mm"]))
        hf_rows.append("%s %+.4f mm (drawn %.3f / %.3f mm from the simulated shift / amplitude)" % (net, hf["residual_mm"], d_s, d_a))
    check(G, "HFSS of every tuning feature: each CA / CS net has a run of its own feature; the copper differs from the simulated shape by at most "
          "0.03 mm in shift and amplitude (same rows, same radius), and HFSS differs from the formula by at most 0.08 mm (0.55 ps)", not hf_bad, str(hf_bad))
    print("        HFSS - formula per feature: " + "; ".join(hf_rows))
    # the ten node vias are filled and capped (their pads are next to 0201 solder lands)
    node_bad = []
    for net in order:
        vv = [v for v in vias if v.GetNetname() == net and math.dist(C.k2c(v.GetPosition()), (cols[net]["x"], cmd["rows_y"]["node_via"])) < 1e-3]
        if len(vv) != 1 or vv[0].GetFillingMode() != pcbnew.FILLING_MODE_FILLED or vv[0].GetCappingMode() != pcbnew.CAPPING_MODE_CAPPED:
            node_bad.append(net)
    link_w = sorted({round(C.MM(t.GetWidth()), 3) for net in order for t in by_net[net] if board.GetLayerName(t.GetLayer()) != "In3.Cu"})
    check(G, "divider columns: the ten node vias are filled and capped (pad 0.05 - 0.10 mm from a 0201 land); the links land -> via -> land "
          "are drawn %s mm wide (the land width), leaving no slot between land and via pad" % link_w, not node_bad and link_w == [0.4], str(node_bad))

    # ---- CK pair ----------------------------------------------------------------------------------------------
    def in3_line(net):
        segs = sorted(([q for q in track_pts(t)] for t in by_net[net] if board.GetLayerName(t.GetLayer()) == "In3.Cu"),
                      key=lambda p_: min(p_[0][1], p_[-1][1]))
        node = (cols[net]["x"], cmd["rows_y"]["node_via"])
        pts, cur = [node], node
        todo = list(segs)
        while todo:
            k = next((i for i, s_ in enumerate(todo) if math.dist(s_[0], cur) < 1e-5 or math.dist(s_[-1], cur) < 1e-5), None)
            if k is None:
                return None
            s_ = todo.pop(k)
            if math.dist(s_[-1], cur) < 1e-5:
                s_ = s_[::-1]
            pts += s_[1:]
            cur = s_[-1]
        return LineString(pts)
    lp, ln_ = in3_line("CK_P"), in3_line("CK_N")
    pitch_ck = w_ck + ncl["CMD_CK"]["layers"]["In3.Cu"]["gap"]
    step = 0.005
    coupled, gaps = {"CK_P": 0.0, "CK_N": 0.0}, []
    for me, other, name in ((lp, ln_, "CK_P"), (ln_, lp, "CK_N")):
        k = 0
        while k * step <= me.length:
            d = other.distance(me.interpolate(k * step))
            if abs(d - pitch_ck) <= 0.0005:           # parallel at the nominal pitch
                coupled[name] += step
                gaps.append(d - w_ck)
            k += 1
    unc_mm = {n: l.length - coupled[n] for n, l in (("CK_P", lp), ("CK_N", ln_))}
    xk = C.ball(board, "H9")[0]
    ysplit = cmd["ck_split"]["split_y"]
    clip = _box(0, 0, 70, ysplit)
    mirror = LineString([(2 * xk - x, y) for x, y in ln_.coords]).intersection(clip)
    sym = lp.intersection(clip).hausdorff_distance(mirror)
    fP = LineString([q for t in by_net["CK_F_P"] for q in track_pts(t)])
    fN = LineString([(2 * xk - q[0], q[1]) for t in by_net["CK_F_N"] for q in track_pts(t)])
    sym_f = fP.hausdorff_distance(fN)
    check(G, "CK pair on In3: coupled over %.2f mm of %.2f / %.2f mm (%.0f %%) with a gap of %.4f .. %.4f mm (0.10 +/- 0.01); uncoupled on In3 "
          "CK_P %.2f mm, CK_N %.2f mm (limit 2.5 mm each; the DRC limit %.1f mm counts the outer-layer links too); In3 length difference "
          "CK_P - CK_N %+.3f mm" % (coupled["CK_P"], lp.length, ln_.length, 100 * coupled["CK_P"] / lp.length, min(gaps), max(gaps),
                                   unc_mm["CK_P"], unc_mm["CK_N"], ncl["CMD_CK"]["max_uncoupled_mm"], lp.length - ln_.length),
          min(gaps) >= 0.09 - 1e-6 and max(gaps) <= 0.11 + 1e-6 and max(unc_mm.values()) <= 2.5 and coupled["CK_P"] / lp.length >= 0.75)
    check(G, "CK pair symmetry about x = %.3f (ball column 9): the two In3 legs are mirror images from the node vias to the split at y = %.2f "
          "(largest deviation %.4f mm), and so are the two finger stubs (%.4f mm); the divider columns, node vias and fence vias are "
          "placed symmetrically by the floorplan" % (xk, ysplit, sym, sym_f), sym < 1e-3 and sym_f < 1e-3)

    # ---- layer purity -----------------------------------------------------------------------------------------
    region = (cmd["region"]["x"][0] - 0.4, 2.7, cmd["region"]["x"][1] + 0.5, C.ball(board, "J9")[1] + 0.6)
    in_region = lambda q: region[0] <= q[0] <= region[2] and region[1] <= q[1] <= region[3]      # noqa: E731
    on = collections.defaultdict(set)
    for t in board.GetTracks():
        if t.Type() == pcbnew.PCB_VIA_T:
            continue
        if in_region(C.k2c(t.GetStart())) or in_region(C.k2c(t.GetEnd())):
            on[board.GetLayerName(t.GetLayer())].add(t.GetNetname())
    foreign_in3 = sorted(on["In3.Cu"] - set(g["nets"]))
    # One exception, stated in the spec (floorplan.data.command_zone_exception) and tied to its evidence: the RDQS_N In3 leg leaves the
    # via-in-pad of ball C4, which stands inside this region east of the CA0 channel.  Allowed only for that net, only for the stated
    # length, and only while the HFSS result of the CA0 corner is on file with a passing verdict.
    exc = (fpl.get("data") or {}).get("command_zone_exception")
    exc_txt = ""
    if exc and foreign_in3 == [exc["net"]]:
        mm_in = 0.0
        x_min = 99.0
        for t in board.GetTracks():
            if t.Type() != pcbnew.PCB_VIA_T and t.GetNetname() == exc["net"] and board.GetLayerName(t.GetLayer()) == "In3.Cu":
                a_, b_ = C.k2c(t.GetStart()), C.k2c(t.GetEnd())
                seg = LineString([a_, b_]).intersection(box(region[0], region[1], region[2], region[3]))
                if not seg.is_empty:
                    mm_in += seg.length
                    x_min = min(x_min, seg.bounds[0])
        ev = os.path.join(C.ROOT, exc["evidence"])
        verdict = json.load(open(ev)).get("verdict", {}) if os.path.exists(ev) else {}
        if mm_in <= exc["max_mm_inside"] + 1e-9 and x_min >= cols["CA0"]["x"] + 0.35 and verdict.get("pass") is True:
            foreign_in3 = []
            exc_txt = ("; exception by the spec: %.2f mm of the %s In3 leg where it leaves the via-in-pad of C4, east of the CA0 channel (HFSS, %s: CA0 delay %+.3f ps, "
                       "coupling %.2f %%)" % (mm_in, exc["net"], exc["evidence"], verdict.get("ca0_delay_change_ps", float("nan")), verdict.get("coupling_percent", float("nan"))))
    mm_layer = collections.defaultdict(lambda: collections.defaultdict(float))
    for t in tracks:
        mm_layer[board.GetLayerName(t.GetLayer())][t.GetNetname()] += track_len(t)
    check(G, ("layer purity: In3 carries only the ten command nets in x %.1f .. %.1f, y %.1f .. %.1f (%s); In1 / In2 / In4 carry no track there"
              % (region[0], region[2], region[1], region[3], ", ".join(sorted(on["In3.Cu"])))) + exc_txt,
          not foreign_in3 and set(g["nets"]) == on["In3.Cu"] - ({exc["net"]} if exc_txt else set()) and not (on["In1.Cu"] | on["In2.Cu"] | on["In4.Cu"]), str(foreign_in3))
    for lay in C.CU_ORDER:
        if on[lay]:
            mine = sorted(n for n in on[lay] if n in group_nets)
            others = sorted(n for n in on[lay] if n not in group_nets)
            print("        %-6s command group: %s%s" % (lay, ", ".join("%s %.2f" % (n, mm_layer[lay][n]) for n in mine) or "-",
                                                    ("; other nets with tracks in the region: " + ", ".join(others)) if others else ""))

    # ---- reference planes under the In3 traces ------------------------------------------------------------------
    area = _box(region[0] - 1, region[1], region[2] + 1, region[3] + 1)
    plane = {l: gnd_fill(board, l, area) for l in ("In2.Cu", "In4.Cu")}
    all_vias = [(C.k2c(v.GetPosition()), v.GetNetname(), C.MM(v.GetWidth(pcbnew.F_Cu))) for v in vias if area.contains(Point(C.k2c(v.GetPosition())))]
    y_bga = y_field
    rows_, pbad, worst_cov = [], [], 100.0
    for net in order:
        w = w_ck if net in ("CK_P", "CK_N") else w_se
        line = lp if net == "CK_P" else ln_ if net == "CK_N" else in3_line(net)
        own = [Point(cols[net]["x"], cmd["rows_y"]["node_via"]), Point(*C.ball(board, cols[net]["ball"]))]
        own_holes = unary_union([p.buffer(0.2 + 0.1 + 0.02) for p in own])
        shadow = line.buffer(w / 2, cap_style=2)
        wide = line.buffer(w / 2 + h_in2, cap_style=2)
        field = _box(0, y_bga, 70, 40)
        r_ = [net]
        for l in ("In2.Cu", "In4.Cu"):
            miss = shadow.difference(plane[l]).difference(own_holes)
            cov = 100 * (1 - miss.area / shadow.difference(own_holes).area)
            wide_u = wide.difference(own_holes)
            cov_w = 100 * wide_u.intersection(plane[l]).area / wide_u.area
            wf = wide_u.intersection(field)
            cov_f = 100 * wf.intersection(plane[l]).area / wf.area
            worst_cov = min(worst_cov, cov)
            if miss.area > 1e-6:
                pbad.append((net, l, round(miss.area, 4), [round(v, 2) for v in miss.bounds]))
            r_ += [cov, cov_w, cov_f]
        near = [(xy, n_) for xy, n_, dia in all_vias if n_ not in ("GND", net) and line.distance(Point(xy)) <= 0.4 + 1e-6]
        gnd_beside = sum(1 for xy, n_, dia in all_vias if n_ == "GND" and line.distance(Point(xy)) <= 0.4 + 1e-6)
        dn = min(math.dist(xy, (own[0].x, own[0].y)) for xy, n_, dia in all_vias if n_ == "GND")
        db = min(math.dist(xy, (own[1].x, own[1].y)) for xy, n_, dia in all_vias if n_ == "GND")
        rows_.append(tuple(r_) + (len(near), gnd_beside, dn, db))
    check(G, "reference planes: the copper of every In3 trace lies over GND fill on In2 (0.11 mm above) and on In4 (0.36 mm below) along its "
          "whole length, the antipads of its own two vias excepted (smallest coverage %.1f %%); In4 is GND, not an island, under all of it" % worst_cov,
          not pbad, str(pbad[:6]))
    nfp = fpl["U1"]["via_in_pad"].get("unused_inner_pads") == "removed"
    antipad = (vip[1] + 2 * spec["rules"]["board"]["hole_clearance"]) if nfp else (vip[0] + 2 * 0.10)
    web = 0.8 - antipad
    print("        plane perforation by the via field (antipad = %s = %.2f mm: %.2f mm web between two ball columns, %.2f mm trace on it):"
          % ("%.2f mm hole + 2 x %.2f mm hole clearance, unused pads removed" % (vip[1], spec["rules"]["board"]["hole_clearance"]) if nfp
             else "%.2f mm pad + 2 x 0.10 mm" % vip[0], antipad, web, w_se))
    print("        'trace' = under the copper; '+/-h' = under the copper widened by h = %.2f mm on both sides; 'BGA' = the same inside the via field (y > %.2f)" % (h_in2, y_bga))
    print("        net    In2 trace  In2 +/-h  In2 BGA | In4 trace  In4 +/-h  In4 BGA | antipads / GND vias beside the trace | nearest GND via: node, ball")
    for r_ in rows_:
        print("        %-5s   %6.1f %%  %6.1f %%  %5.1f %% |  %6.1f %%  %6.1f %%  %5.1f %% |        %2d / %2d                      |   %.2f mm, %.2f mm" % r_)
    # what stands beside the In3 traces: via pads that are flashed on In3 (connected there or not removed) and barrels
    pad_min, hole_min = (9.9, "-"), (9.9, "-")
    for net in order:
        w = w_ck if net in ("CK_P", "CK_N") else w_se
        line = lp if net == "CK_P" else ln_ if net == "CK_N" else in3_line(net)
        for v in vias:
            if v.GetNetname() == net:
                continue
            xy = C.k2c(v.GetPosition())
            if not area.contains(Point(xy)):
                continue
            dist = line.distance(Point(xy))
            what = "%s to the %s via at (%.3f, %.3f)" % (net, v.GetNetname(), xy[0], xy[1])
            if v.FlashLayer(pcbnew.In3_Cu):
                pad_min = min(pad_min, (dist - C.MM(v.GetWidth(pcbnew.In3_Cu)) / 2 - w / 2, what))
            hole_min = min(hole_min, (dist - C.MM(v.GetDrill()) / 2 - w / 2, what))
    check(G, "copper beside the In3 traces: nearest via pad on In3 %.3f mm (%s; limit: the class clearance %.2f mm; the unused In3 pads of the "
          "U1 via-in-pads are %s), nearest via barrel %.3f mm (%s; limit: hole clearance %.2f mm)"
          % (pad_min[0], pad_min[1], ncl["CMD_SE"]["clearance"], "removed" if nfp else "kept", hole_min[0], hole_min[1],
             spec["rules"]["board"]["hole_clearance"]),
          pad_min[0] >= ncl["CMD_SE"]["clearance"] - 1e-6 and hole_min[0] >= spec["rules"]["board"]["hole_clearance"] - 1e-6)
    dn_max, db_max = max(r_[-2] for r_ in rows_), max(r_[-1] for r_ in rows_)
    check(G, "return vias at the layer changes: a GND via within %.2f mm of every node via (fence vias, limit 2.0 mm) and within %.2f mm of every "
          "ball via (limit 1.0 mm)" % (dn_max, db_max), dn_max <= 2.0 and db_max <= 1.0)

    # ---- DRC warnings on the group --------------------------------------------------------------------------------
    ox, oy = C.origin()
    wl = collections.defaultdict(list)
    for v in (drc or {}).get("violations", []):
        nn = {m for i in v["items"] for m in re.findall(r"\[([^\]]+)\]", i["description"])}
        if nn & group_nets:
            wl[(v["type"], v["severity"])].append([(round(i["pos"]["x"] - ox, 3), round(oy - i["pos"]["y"], 3), i["description"]) for i in v["items"]])
    y_pair0 = cols["CK_P"]["in3_route"][2][1]
    odd_w = []
    for (typ, sev), lst in wl.items():
        for items in lst:
            if typ == "diff_pair_gap_out_of_range" and all(("In3" not in d_) or y <= y_pair0 + 1e-6 or y >= ysplit + 0.29 for x, y, d_ in items):
                continue
            odd_w.append((typ, sev, items[0][:2]))
    n_gap = len(wl.get(("diff_pair_gap_out_of_range", "warning"), []))
    check(G, "DRC items on the command nets: %s; the %d pair-gap warnings are the places where the CK legs must part (outer-layer links "
          "of the two divider columns, the 0.2 mm lead-out of the node vias, the split around H9), none inside the coupled run y %.2f .. %.2f"
          % ({"%s (%s)" % k: len(v) for k, v in wl.items()} or "none", n_gap, y_pair0, ysplit), not odd_w, str(odd_w[:6]))


# ------------------------------------------------------------------------------------------------ 7 data side
DESKEW = os.path.join(C.ROOT, "design", "gateware_deskew.json")


def path_bends(board, spec, items, net):
    """Electrical shortening, in ps, of the bends along a copper path (items of copper_path, in signal order) on EVERY layer,
    read from the board: each mitred vertex between two consecutive track items of one layer loses (k_mitre0 + k_mitre1 x w)
    x turn of length at the ps / mm of the item after it; two antiparallel straight items of the path on one layer (the legs of
    a bump) lose 2 k_leg exp(-pi gap / decay) x their common length.  Returns (ps vertices, ps legs, vertices, degrees turned)."""
    model = spec["rules"]["bend_model"]
    seq = []
    for t, node_out in items:
        if t is None:
            seq.append(None)
            continue
        a, b = C.k2c(t.GetStart()), C.k2c(t.GetEnd())
        p_in, p_out = (a, b) if math.dist(b, node_out[:2]) < 1e-3 else (b, a)
        seq.append((board.GetLayerName(t.GetLayer()), C.MM(t.GetWidth()), p_in, p_out))
    ps = turned = 0.0
    nv = 0
    for prev, cur in zip(seq, seq[1:]):
        if prev is None or cur is None or prev[0] != cur[0]:
            continue
        u = (prev[3][0] - prev[2][0], prev[3][1] - prev[2][1])
        v = (cur[3][0] - cur[2][0], cur[3][1] - cur[2][1])
        turn = math.acos(max(-1.0, min(1.0, (u[0] * v[0] + u[1] * v[1]) / (math.hypot(*u) * math.hypot(*v)))))
        if turn > math.radians(0.5):
            ps += (model["k_mitre0"] + model["k_mitre1"] * cur[1]) * turn * C.delay_ps_per_mm(cur[0], cur[1], net)
            turned += turn
            nv += 1
    legs = 0.0
    tr = [s for s in seq if s is not None]
    for i in range(len(tr)):
        for j in range(i + 1, len(tr)):
            a, b = tr[i], tr[j]
            if a[0] != b[0]:
                continue
            u = (a[3][0] - a[2][0], a[3][1] - a[2][1])
            v = (b[3][0] - b[2][0], b[3][1] - b[2][1])
            lu, lv = math.hypot(*u), math.hypot(*v)
            if (u[0] * v[0] + u[1] * v[1]) / (lu * lv) > -0.999:
                continue
            ex, ey = u[0] / lu, u[1] / lu
            dist = abs((b[2][0] - a[2][0]) * (-ey) + (b[2][1] - a[2][1]) * ex)
            if dist > 1.0:
                continue
            tb = sorted([(b[2][0] - a[2][0]) * ex + (b[2][1] - a[2][1]) * ey, (b[3][0] - a[2][0]) * ex + (b[3][1] - a[2][1]) * ey])
            ov = min(lu, tb[1]) - max(0.0, tb[0])
            gap = dist - (a[1] + b[1]) / 2
            if ov > 1e-9 and gap > 0:
                legs += 2 * model["k_leg"] * math.exp(-math.pi * gap / model["leg_decay_mm"]) * ov * C.delay_ps_per_mm(a[0], a[1], net)
    return ps, legs, nv, math.degrees(turned)


def gate_data(board, spec, drc, write_deskew=True):
    G = "7 data"
    STAGE = "s30_data"
    if not stage_tracks(board, STAGE):
        print("        [%s] stage %s has not run: gate skipped" % (G, STAGE))
        return
    from shapely.geometry import LineString, Point, box as _box
    fpl = spec["floorplan"]
    D = fpl["data"]
    grp = {q["name"]: q for q in spec["groups"]}
    gw, gk, gr, gq = grp["WR"], grp["WR_WCK"], grp["RD_P"], grp["RD_RDQS"]
    ncl = spec["rules"]["netclasses"]
    cols = D["columns"]
    dq = sorted(cols, key=lambda n: cols[n]["x"])
    parts = C.parts()
    z = C.stackup_z()
    d_via = spec["rules"]["delay_ps_per_mm"]["through via (per mm of barrel)"]
    ms20 = spec["rules"]["delay_ps_per_mm"]["F.Cu/B.Cu w0.20"]
    ftop = fpl["outline"]["finger_pad_y"][1]
    lname = lambda t: board.GetLayerName(t.GetLayer())      # noqa: E731
    fingers = {f["finger"]: f for f in spec["fingers"]}
    fin = {}
    for f in spec["fingers"]:
        if f["net"]:
            fin.setdefault(f["net"], []).append(f["finger"])
    data_nets = dq + [n + "_WF" for n in dq] + ["WCK_P", "WCK_N", "WCK_F_P", "WCK_F_N", "RDQS_P", "RDQS_N", "VREF_RD", "DAC_OUT"] \
        + ["VREF_N%d" % i for i in range(8)]
    hs_nets = set(dq) | {n + "_WF" for n in dq} | {"WCK_P", "WCK_N", "WCK_F_P", "WCK_F_N", "RDQS_P", "RDQS_N"}
    all_tracks = [t for t in board.GetTracks() if t.Type() != pcbnew.PCB_VIA_T]
    vias = [t for t in board.GetTracks() if t.Type() == pcbnew.PCB_VIA_T]
    st_tracks = stage_tracks(board, STAGE)
    tracks = [t for t in all_tracks if t.GetNetname() in data_nets]
    by_net = collections.defaultdict(list)
    for t in tracks:
        by_net[t.GetNetname()].append(t)
    u1c = _box(fpl["U1"]["body"]["x"][0] - 0.25, fpl["U1"]["body"]["y"][0] - 0.25, fpl["U1"]["body"]["x"][1] + 0.25, fpl["U1"]["body"]["y"][1] + 0.25)
    areas = {a_["name"]: _box(a_["x"][0], a_["y"][0], a_["x"][1], a_["y"][1]) for a_ in fpl["rule_areas"]}

    def cl(t):
        return LineString([C.k2c(t.GetStart()), C.k2c(t.GetEnd())])

    def cu(t):
        return cl(t).buffer(C.MM(t.GetWidth()) / 2, 16)

    def two_pin(a, b):
        return [p["ref"] for p in parts.values() if len(p["pins"]) == 2 and set(p["pins"].values()) == {a, b}]

    def pad_of(ref, net):
        p = next(q for q in C.footprint(board, ref).Pads() if q.GetNumber() and q.GetNetname() == net)
        return C.k2c(p.GetPosition()), ("F.Cu" if p.IsOnLayer(pcbnew.F_Cu) else "B.Cu"), p

    def pad_box(p):
        bb = p.GetBoundingBox()
        lo, hi = C.k2c(pcbnew.VECTOR2I(bb.GetLeft(), bb.GetBottom())), C.k2c(pcbnew.VECTOR2I(bb.GetRight(), bb.GetTop()))
        return (lo[0], lo[1], hi[0], hi[1])

    def finger_end(net):
        """(node, finger, layer, y of the track end) of the track of 'net' that ends inside its finger pad."""
        f = fin[net][0]
        lay = fingers[f]["side"] + ".Cu"
        best = None
        for t in by_net[net]:
            if lname(t) != lay:
                continue
            for q in (C.k2c(t.GetStart()), C.k2c(t.GetEnd())):
                if abs(q[0] - fingers[f]["x_mm"]) <= 0.176 and 0.15 <= q[1] <= ftop and (best is None or q[1] < best[0][1]):
                    best = (q, C.MM(t.GetWidth()))
        return (best[0] + (lay,), f, lay, best[0][1], best[1]) if best else (None, f, lay, None, None)

    # ---- 1. connectivity -------------------------------------------------------------------------------------------------
    unc = []
    for u in (drc or {}).get("unconnected_items", []):
        nn = {m for i in u["items"] for m in re.findall(r"\[([^\]]+)\]", i["description"])}
        if nn & set(data_nets):
            unc.append(sorted(nn & set(data_nets)))
    pend = [u for u in unc if u == ["VREF_RD"] or u == ["DAC_OUT"]]
    check(G, "connectivity: no unconnected item on the %d nets of the stage (DQ0-7 from the ball to both fingers, DQ0-7_WF, WCK, RDQS, VREF_N0-7, "
          "VREF_RD, DAC_OUT) in the DRC report; RESET_n is a slow net of the power stage (gate 9)" % len(data_nets), drc is not None and not unc, str(unc[:6]))
    # independent walk: on every net each pad is reached from the first one along tracks and vias of the net
    walk_bad = []
    for net in data_nets:
        code = board.GetNetcodeFromNetname(net)
        nodes = collections.defaultdict(set)
        for t in by_net[net]:
            a, b = nkey(C.k2c(t.GetStart()), lname(t)), nkey(C.k2c(t.GetEnd()), lname(t))
            nodes[a].add(b)
            nodes[b].add(a)
        for v in vias:
            if v.GetNetCode() == code:
                xy = C.k2c(v.GetPosition())
                here = [nkey(xy, l) for l in C.CU_ORDER if nkey(xy, l) in nodes]
                for a in here:
                    nodes[a] |= set(here) - {a}
        pads_ = []
        for fp in board.GetFootprints():
            for p in fp.Pads():
                if p.GetNetCode() == code and p.GetNumber():
                    bb = pad_box(p)
                    lays = [l for l in ("F.Cu", "In3.Cu", "B.Cu") if p.IsOnLayer(C.CU[l])]
                    on = [k for k in nodes if k[2] in lays and bb[0] - 1e-3 <= k[0] <= bb[2] + 1e-3 and bb[1] - 1e-3 <= k[1] <= bb[3] + 1e-3]
                    if fp.GetReference() == "U1":       # a ball with a via-in-pad: the via carries the net to the other layers
                        c = C.k2c(p.GetPosition())
                        on += [k for k in nodes if abs(k[0] - c[0]) < 1e-3 and abs(k[1] - c[1]) < 1e-3]
                    pads_.append(("%s.%s" % (fp.GetReference(), p.GetNumber()), on))
        if not nodes or any(not on for _n, on in pads_):
            if not (net == "VREF_RD" and [n for n, on in pads_ if not on] == []):
                walk_bad.append((net, [n for n, on in pads_ if not on]))
            continue
        seen, todo = set(pads_[0][1]), list(pads_[0][1])
        while todo:
            c = todo.pop()
            for o in nodes[c]:
                if o not in seen:
                    seen.add(o)
                    todo.append(o)
        miss = [n for n, on in pads_ if not (set(on) & seen)]
        if miss or len(seen) != len(nodes):
            walk_bad.append((net, miss or "copper island"))
    check(G, "connectivity: on every net of the stage each pad is reached from the first one along tracks and vias of that net, and no copper of the net "
          "is left over (%d nets walked on the board, independent of the DRC ratsnest)" % len(data_nets), not walk_bad, str(walk_bad[:6]))

    # ---- 2. the T at the divider ---------------------------------------------------------------------------------------------
    via_at = {(round(C.k2c(v.GetPosition())[0], 3), round(C.k2c(v.GetPosition())[1], 3)): v for v in vias}
    lim_t = ncl["WR_DQ"]["t_to_resistor_max_mm"]
    lim_e = ncl["WR_DQ"]["divider_to_card_edge_max_mm"]
    t_rows, t_bad, top_land, n_div_parts = [], [], 0.0, 0
    for n in dq:
        nv = (round(cols[n]["x"], 3), round(D["rows_y"]["node_via"], 3))
        ser, sh = two_pin(n + "_WF", n), two_pin(n, "GND")
        others = [r_ for r_, p in parts.items() if n in p["pins"].values() and r_ not in ser + sh + ["U1", "J1"]]
        if nv not in via_at or via_at[nv].GetNetname() != n or len(ser) != 1 or len(sh) != 1 or others:
            t_bad.append((n, "node via / parts"))
            continue
        row = []
        for ref, what in ((ser[0], "series"), (sh[0], "shunt")):
            pxy, play, pad = pad_of(ref, n)
            p_ = copper_path(board, spec, [n], nv + (play,), pxy + (play,))
            barrel = abs(z[play] - z["F.Cu"])                       # the tooth arrives on F.Cu: through the barrel to a land on B.Cu
            copper = (p_["mm"] if p_ else 9.9) + barrel
            bb = pad_box(pad)
            gap = max(nv[1] - 0.20 - bb[3], 0.0)                    # via pad edge to land edge
            n_div_parts += 1
            for q in C.footprint(board, ref).Pads():
                if q.GetNumber():
                    top_land = max(top_land, pad_box(q)[3])
            if p_ is None or copper > lim_t + 1e-9 or abs(pxy[0] - nv[0]) > 1e-6:
                t_bad.append((n, ref, round(copper, 3)))
            row.append("%s %s on %s %.2f mm (land %.2f mm from the via pad)" % (what, ref, play[0], copper, gap))
        sx = [C.k2c(C.footprint(board, r_).GetPosition()) for r_ in (ser[0], sh[0])]
        if math.dist(sx[0], sx[1]) > 1e-6 or C.footprint(board, ser[0]).IsFlipped() == C.footprint(board, sh[0]).IsFlipped():
            t_bad.append((n, "not back to back"))
        t_rows.append("%s T at (%.3f, %.2f): %s" % (n, nv[0], nv[1], "; ".join(row)))
    check(G, "T compactness: for every DQ the F.Cu tooth, the In3 read branch and both divider resistors meet on ONE via (the node via on the column axis); "
          "the 240 ohm (finger's face) and the 150 ohm (other face) sit back to back; copper from the T to the series land and to the shunt land "
          "(through the barrel for the land on B.Cu) <= %.1f mm on all eight; all lands of the %d divider parts within %.2f mm of the card edge "
          "(limit %.1f)" % (lim_t, n_div_parts, top_land, lim_e), not t_bad and n_div_parts == 16 and top_land <= lim_e, str(t_bad))
    for r_ in t_rows:
        print("        " + r_)

    # ---- 3. delays, measured on the board ---------------------------------------------------------------------------------
    def measure(nets, start, end, net_for_bends, bridges=(), extra_ps=0.0, finger=None):
        p_ = copper_path(board, spec, nets, start, end, bridges)
        if p_ is None:
            return None
        b_ps, l_ps, nv_, deg = path_bends(board, spec, p_["items"], net_for_bends)
        cut = 0.0
        if finger is not None:                                   # the track ends inside the finger pad: card delay counts from the finger top
            cut = (ftop - finger[0]) * C.delay_ps_per_mm(finger[1], finger[2], net_for_bends)
        card = p_["ps"] - b_ps - l_ps - cut + extra_ps
        return dict(card=card, centre=p_["ps"] - cut + extra_ps, bends=b_ps, legs=l_ps, vertices=nv_, deg=deg, layers=p_["layers"], vias=p_["vias"])
    hp = lambda f: fingers[f]["host_plus_pkg_delay_ps"]      # noqa: E731
    wtab, rtab, bad_path = {}, {}, []
    for n in dq:
        ser = two_pin(n + "_WF", n)[0]
        (p1, l1, _a), (p2, l2, _b) = pad_of(ser, n + "_WF"), pad_of(ser, n)
        fe, f, lay, ye, we = finger_end(n + "_WF")
        m = measure([n, n + "_WF"], fe, C.ball(board, cols[n]["ball"]) + ("F.Cu",), n, bridges=[(p1 + (l1,), p2 + (l2,), math.dist(p1, p2), math.dist(p1, p2) * ms20, ser)],
                    finger=(ye, lay, we)) if fe else None
        if m is None:
            bad_path.append((n, "write"))
        else:
            wtab[n] = dict(m, finger=f, host=hp(f), total=m["card"] + hp(f))
        fe, f, lay, ye, we = finger_end(n)
        m = measure([n], C.ball(board, cols[n]["ball"]) + ("F.Cu",), fe, n, finger=(ye, lay, we)) if fe else None
        if m is None:
            bad_path.append((n, "read"))
        else:
            rtab[n] = dict(m, finger=f, host=hp(f), total=m["card"] + hp(f))
    ptab = {}
    for n in ("RDQS_P", "RDQS_N"):
        fe, f, lay, ye, we = finger_end(n)
        ball = {v_: k_ for k_, v_ in parts["U1"]["pins"].items()}[n]
        m = measure([n], C.ball(board, ball) + ("In3.Cu",), fe, n, extra_ps=(z["In3.Cu"] - z["F.Cu"]) * d_via, finger=(ye, lay, we))
        ptab[n] = dict(m, finger=f, host=hp(f), total=m["card"] + hp(f)) if m else None
    for n in ("WCK_P", "WCK_N"):
        fn_ = "WCK_F_" + n[-1]
        ser = two_pin(fn_, n)[0]
        (p1, l1, _a), (p2, l2, _b) = pad_of(ser, fn_), pad_of(ser, n)
        fe, f, lay, ye, we = finger_end(fn_)
        ball = {v_: k_ for k_, v_ in parts["U1"]["pins"].items()}[n]
        m = measure([n, fn_], fe, C.ball(board, ball) + ("B.Cu",), n, bridges=[(p1 + (l1,), p2 + (l2,), math.dist(p1, p2), math.dist(p1, p2) * ms20, ser)],
                    extra_ps=(z["B.Cu"] - z["F.Cu"]) * d_via, finger=(ye, lay, we))
        ptab[n] = dict(m, finger=f, host=hp(f), total=m["card"] + hp(f)) if m else None
    check(G, "every DQ has one copper path finger -> stub -> 240 ohm -> node via -> tooth -> line -> neck -> ball (write) and one ball -> neck -> line -> tooth -> "
          "node via -> In3 branch -> read via -> finger track -> finger (read), each RDQS leg ball -> via-in-pad -> In3 -> via -> F.Cu -> finger and each "
          "WCK leg finger -> stub -> 91 ohm -> node via -> B.Cu -> via-in-pad -> ball", not bad_path and all(ptab.values()), str(bad_path))
    if bad_path or not all(ptab.values()):
        return
    print("        delay measured on the board, independent of the plan: every track item of the path x ps/mm of its layer and width, every via with the barrel")
    print("        between the layers it is entered and left on (mid-copper heights), the series resistor as 0.64 mm of 0.20 mm line, every mitred vertex of the")
    print("        path on any layer by rules.bend_model, counted from the finger top (y = %.2f).  Host + package from the spec." % ftop)
    # write group: not matched on the card; each net against its own planned total
    tol_w = gw["tolerance_ps"]
    print("        WRITE (FPGA die pad -> ball): target = the planned total of the net (the group is NOT matched on the card), tolerance +/-%.1f ps" % tol_w)
    print("        net  finger  stub+R+link mm  barrel mm  tooth+line mm  neck mm  bends ps | card ps  host ps | target   achieved   error | to centre  ODELAY")
    wt = {n: wtab[n]["total"] for n in dq}
    w_lo, w_hi = min(wt.values()), max(wt.values())
    w_bad = []
    for n in dq:
        m = wtab[n]
        flay = fingers[m["finger"]]["side"] + ".Cu"
        tgt = gw["per_net_planned_total_ps"][n]
        tr_mm = sum(track_len(t) for t in by_net[n] if lname(t) == "F.Cu" and abs(C.MM(t.GetWidth()) - 0.15) < 1e-6)
        nk_mm = sum(track_len(t) for t in by_net[n] if lname(t) == "F.Cu" and abs(C.MM(t.GetWidth()) - 0.09) < 1e-6)
        fx = m["layers"].get(flay, [0, 0])[0] - (tr_mm + nk_mm if flay == "F.Cu" else 0.0)
        if abs(m["total"] - tgt) > tol_w:
            w_bad.append((n, round(m["total"] - tgt, 2)))
        print("        %-4s %3d %s      %6.3f       %6.4f      %7.3f     %6.3f   %5.2f  | %7.2f  %6.1f  | %7.2f  %7.2f   %+5.2f | %+7.2f    %5.1f"
              % (n, m["finger"], flay[0], fx + 0.64, m["vias"][0]["span_mm"] if m["vias"] else 0.0, tr_mm, nk_mm, m["bends"], m["card"], m["host"], tgt, m["total"],
                 m["total"] - tgt, m["total"] - (w_hi + w_lo) / 2, w_hi - m["total"]))
    check(G, "delay, write group: every net within +/-%.1f ps of its planned total (largest deviation %.2f ps)" % (tol_w, max(abs(wtab[n]["total"] - gw["per_net_planned_total_ps"][n]) for n in dq)),
          not w_bad, str(w_bad))
    w_half = (w_hi - w_lo) / 2
    check(G, "delay, write group NOT matched on the card (reported, not hidden): totals %.1f .. %.1f ps = +/-%.1f ps about %.1f (group rule +/-%.0f ps); a comb on one "
          "layer has one possible order and no knob. Closed per bit in the FPGA: the output delay table below, written to design/gateware_deskew.json "
          "(status: %s)" % (w_lo, w_hi, w_half, (w_hi + w_lo) / 2, gw["group_rule_ps"], gw["deskew"]["status"]),
          gw["matched_on_the_card"] is False and abs(w_half - gw["spread_half_ps"]) <= 1.0
          and gw["deskew"]["status"].startswith("accepted by the user"))
    # read group: not matched on the card either (read end without crossings); each net against its own planned total
    tol_r = gr["tolerance_ps"]
    rq_lay = D["rdqs"]["outer_layer"]
    print("        READ (ball -> FPGA die pad): target = the planned total of the net (the group is NOT matched on the card: read end without crossings), tolerance +/-%.1f ps" % tol_r)
    print("        net  finger  F.Cu mm  In3 mm  level y  finger track mm  barrels mm       bends ps | card ps  host ps | target   achieved   error | to centre  IDELAY")
    rtot = {n: rtab[n]["total"] for n in dq}
    r_lo, r_hi_dq = min(rtot.values()), max(rtot.values())
    r_cen, r_half = (r_lo + r_hi_dq) / 2, (r_hi_dq - r_lo) / 2
    r_bad = []
    for n in dq:
        m = rtab[n]
        flay = fingers[m["finger"]]["side"] + ".Cu"
        ft = sum(track_len(t) for t in by_net[n] if lname(t) == flay and abs(C.k2c(t.GetStart())[0] - D["read"][n]["read_via"][0]) < 0.5
                 and abs(C.k2c(t.GetEnd())[0] - D["read"][n]["read_via"][0]) < 0.5 and C.MM(t.GetWidth()) > 0.19 and min(C.k2c(t.GetStart())[1], C.k2c(t.GetEnd())[1]) < 12.0) - (ftop - 2.6)
        tgt = gr["per_net_planned_total_ps"][n]
        if abs(m["total"] - tgt) > tol_r:
            r_bad.append((n, round(m["total"] - tgt, 2)))
        print("        %-4s %3d %s   %6.3f  %6.3f  %6.3f      %6.3f       %s  %5.2f  | %7.2f  %6.1f  | %7.2f  %7.2f   %+5.2f | %+7.2f    %5.1f"
              % (n, m["finger"], flay[0], m["layers"].get("F.Cu", [0, 0])[0] - (ft if flay == "F.Cu" else 0.0), m["layers"]["In3.Cu"][0], D["read"][n]["level_y"], ft,
                 " / ".join("%.4f" % v_["span_mm"] for v_ in m["vias"]), m["bends"], m["card"], m["host"], tgt, m["total"], m["total"] - tgt,
                 m["total"] - r_cen, r_hi_dq - m["total"]))
    rt = [rtab[n]["total"] for n in dq]
    check(G, "delay, read group: every net within +/-%.1f ps of its planned total (largest deviation %.2f ps)"
          % (tol_r, max(abs(rtab[n]["total"] - gr["per_net_planned_total_ps"][n]) for n in dq)), not r_bad, str(r_bad))
    check(G, "delay, read group NOT matched on the card (reported, not hidden): totals %.1f .. %.1f ps = +/-%.1f ps about %.1f (group rule +/-%.0f ps). The read "
          "fingers are taken in the order of the In3 bundle so that no finger track crosses an In3 line; no level set closes that order (the late lines are the "
          "top ones). Closed per bit in the FPGA: the input delay table below (0 .. %.1f ps of the %d ps IDELAY range), written to design/gateware_deskew.json "
          "(status: %s)" % (r_lo, r_hi_dq, r_half, r_cen, gr["group_rule_ps"], r_hi_dq - r_lo, gr["deskew"]["idelay_range_ps"][1], gr["deskew"]["status"]),
          gr["matched_on_the_card"] is False and abs(r_half - gr["spread_half_ps"]) <= 1.0 and r_hi_dq - r_lo <= 0.25 * gr["deskew"]["idelay_range_ps"][1])
    for n in ("RDQS_P", "RDQS_N"):
        m = ptab[n]
        tgt = gq["per_net_planned_total_ps"][n]
        print("        %-6s %3d %s   In3 %.3f mm, %s %.3f mm, barrels 0.6425 (via-in-pad) / %s, %d vertices (%.2f ps), bump legs %.2f ps | %7.2f  %6.1f  | %7.2f  %7.2f   %+5.2f | %+7.2f"
              % (n, m["finger"], rq_lay[0], m["layers"]["In3.Cu"][0], rq_lay, m["layers"][rq_lay][0], " / ".join("%.4f" % v_["span_mm"] for v_ in m["vias"]), m["vertices"],
                 m["bends"], m["legs"], m["card"], m["host"], tgt, m["total"], m["total"] - tgt, m["total"] - r_cen))
    sk_r = ptab["RDQS_P"]["total"] - ptab["RDQS_N"]["total"]
    rq_tot = (ptab["RDQS_P"]["total"] + ptab["RDQS_N"]["total"]) / 2
    check(G, "delay, RDQS pair: RDQS_P to RDQS_N %+.2f ps at the FPGA die pads (limit +/-%.0f ps), each leg within +/-1.0 ps of its planned total (%+.2f / %+.2f). The "
          "pair is NOT matched to the read group (nothing to match it to): it arrives %+.2f ps from the centre of the group (%.1f ps), %.1f ps after the earliest "
          "and %.1f ps before the latest bit; with the input delays of the table every bit arrives %.1f ps after the strobe"
          % (sk_r, gq["tolerance_ps"], ptab["RDQS_P"]["total"] - gq["per_net_planned_total_ps"]["RDQS_P"], ptab["RDQS_N"]["total"] - gq["per_net_planned_total_ps"]["RDQS_N"],
             rq_tot - r_cen, r_cen, rq_tot - r_lo, r_hi_dq - rq_tot, r_hi_dq - rq_tot),
          abs(sk_r) <= gq["tolerance_ps"] and all(abs(ptab[n]["total"] - gq["per_net_planned_total_ps"][n]) <= 1.0 for n in ("RDQS_P", "RDQS_N"))
          and abs((rq_tot - r_cen) - gq["strobe_minus_dq_group_ps"]) <= 1.0 and r_lo < rq_tot < r_hi_dq)
    for n in ("WCK_P", "WCK_N"):
        m = ptab[n]
        print("        %-6s %3d F   F.Cu %.3f mm + 0.64, B.Cu %.3f mm, barrels %s + 1.1570 (via-in-pad), %d vertices (%.2f ps) | %7.2f  %6.1f  | planned %7.2f   achieved %7.2f"
              % (n, m["finger"], m["layers"]["F.Cu"][0] - 0.64, m["layers"]["B.Cu"][0], " / ".join("%.4f" % v_["span_mm"] for v_ in m["vias"]), m["vertices"], m["bends"],
                 m["card"], m["host"], gk["per_net_planned_total_ps"][n], m["total"]))
    sk_w = ptab["WCK_P"]["total"] - ptab["WCK_N"]["total"]
    check(G, "delay, WCK pair: WCK_P to WCK_N %+.2f ps at the balls including the host (limit +/-%.0f ps), no skew bump; WCK is %.1f ps later than the centre of the "
          "DQ write totals (not matched to DQ: write timing is trained)" % (sk_w, gk["tolerance_ps"], (ptab["WCK_P"]["total"] + ptab["WCK_N"]["total"]) / 2 - (w_hi + w_lo) / 2),
          abs(sk_w) <= gk["tolerance_ps"] and all(abs(ptab[n]["total"] - gk["per_net_planned_total_ps"][n]) <= 1.0 for n in ("WCK_P", "WCK_N")))
    print("        net     target   achieved   error")
    for n in dq:
        print("        %-7s %7.2f  %7.2f   %+5.2f   (write: its own planned total)" % (n + " w", gw["per_net_planned_total_ps"][n], wtab[n]["total"], wtab[n]["total"] - gw["per_net_planned_total_ps"][n]))
    for n in dq:
        print("        %-7s %7.2f  %7.2f   %+5.2f   (read: its own planned total)" % (n + " r", gr["per_net_planned_total_ps"][n], rtab[n]["total"], rtab[n]["total"] - gr["per_net_planned_total_ps"][n]))
    for n in ("RDQS_P", "RDQS_N"):
        print("        %-7s %7.2f  %7.2f   %+5.2f" % (n, gq["per_net_planned_total_ps"][n], ptab[n]["total"], ptab[n]["total"] - gq["per_net_planned_total_ps"][n]))
    for n in ("WCK_P", "WCK_N"):
        print("        %-7s %7.2f  %7.2f   %+5.2f" % (n, gk["per_net_planned_total_ps"][n], ptab[n]["total"], ptab[n]["total"] - gk["per_net_planned_total_ps"][n]))
    # per-bit deskew table for the gateware, from the delays measured on the board
    dk = collections.OrderedDict()
    dk["status"] = gw["deskew"]["status"]
    dk["generated_by"] = "scripts/pcb/check_pcb.py, gate 7, from the delays measured on lpddr5-testbed.kicad_pcb"
    dk["what"] = ("NEITHER data group is delay-matched on the card. WRITE (comb with the T at the divider: +/-%.1f ps): each DQ output is delayed in the FPGA by the "
                  "latest total of the group minus its own total, so that all eight arrive at the balls together; ODELAYE3 of the HP bank, DELAY_FORMAT = TIME, "
                  "DELAY_VALUE in ps. READ (read end without crossings, the read fingers in the order of the In3 bundle: +/-%.1f ps): each DQ input is delayed by the "
                  "latest total of the group minus its own total (IDELAYE3, DELAY_FORMAT = TIME, DELAY_VALUE in ps; range 0 .. 1100 ps, 512 taps of 2.1 .. 12 ps), so "
                  "that all eight reach the capture flops together, %.1f ps after the strobe edge that launched with them (strobe.dq_after_strobe_with_the_table_ps; the "
                  "capture delay of the strobe, a quarter of the bit time at DDR, is added to that by the gateware as usual). All values are start values: both "
                  "directions are refined by training. Host + package figures are good to about +/-5 ps" % (w_half, r_half, r_hi_dq - rq_tot))
    dk["read_status"] = gr["deskew"]["status"]
    dk["write"] = collections.OrderedDict(
        latest_total_ps=round(w_hi, 2), spread_half_ps=round(w_half, 2),
        per_bit=collections.OrderedDict((n, collections.OrderedDict(
            finger=wtab[n]["finger"], fpga_ball=fingers[wtab[n]["finger"]]["fpga_ball"], fpga_pin=fingers[wtab[n]["finger"]]["fpga_pin_name"], bank=fingers[wtab[n]["finger"]]["bank"],
            byte_group=fingers[wtab[n]["finger"]]["bytegroup"], card_ps=round(wtab[n]["card"], 2), host_plus_package_ps=wtab[n]["host"], total_ps=round(wtab[n]["total"], 2),
            output_delay_ps=round(w_hi - wtab[n]["total"], 1))) for n in sorted(dq)))
    dk["read"] = collections.OrderedDict(
        matched_on_the_card=False, read_end_crossings=D["knobs"]["read_end_crossings"],
        latest_total_ps=round(r_hi_dq, 2), centre_ps=round(r_cen, 2), spread_half_ps=round(r_half, 2),
        idelay=collections.OrderedDict(primitive="IDELAYE3", delay_format="TIME", range_ps=gr["deskew"]["idelay_range_ps"], taps=gr["deskew"]["idelay_taps"],
                                       tap_ps=gr["deskew"]["idelay_tap_ps"], largest_value_ps=round(r_hi_dq - r_lo, 1)),
        per_bit=collections.OrderedDict((n, collections.OrderedDict(
            p_finger=rtab[n]["finger"], p_fpga_ball=fingers[rtab[n]["finger"]]["fpga_ball"], p_fpga_pin=fingers[rtab[n]["finger"]]["fpga_pin_name"],
            n_finger=D["read"][n]["n_finger"],
            n_fpga_ball=fingers[D["read"][n]["n_finger"]]["fpga_ball"], bank=fingers[rtab[n]["finger"]]["bank"], byte_group=fingers[rtab[n]["finger"]]["bytegroup"],
            card_ps=round(rtab[n]["card"], 2), host_plus_package_ps=rtab[n]["host"],
            total_ps=round(rtab[n]["total"], 2), to_centre_ps=round(rtab[n]["total"] - r_cen, 2), to_strobe_ps=round(rtab[n]["total"] - rq_tot, 2),
            input_delay_ps=round(r_hi_dq - rtab[n]["total"], 1))) for n in sorted(dq)),
        strobe=collections.OrderedDict(
            [(n, collections.OrderedDict(finger=ptab[n]["finger"], fpga_ball=fingers[ptab[n]["finger"]]["fpga_ball"], fpga_pin=fingers[ptab[n]["finger"]]["fpga_pin_name"],
                                         bank=fingers[ptab[n]["finger"]]["bank"], byte_group=fingers[ptab[n]["finger"]]["bytegroup"],
                                         card_ps=round(ptab[n]["card"], 2), host_plus_package_ps=ptab[n]["host"], total_ps=round(ptab[n]["total"], 2)))
             for n in ("RDQS_P", "RDQS_N")]
            + [("to_centre_of_the_dq_group_ps", round(rq_tot - r_cen, 2)), ("to_latest_dq_ps", round(rq_tot - r_hi_dq, 2)), ("to_earliest_dq_ps", round(rq_tot - r_lo, 2)),
               ("dq_after_strobe_with_the_table_ps", round(r_hi_dq - rq_tot, 2)), ("input_delay_ps", 0.0),
               ("minus_each_dq_before_the_table_ps", collections.OrderedDict((n, round(rq_tot - rtab[n]["total"], 2)) for n in sorted(dq))),
               ("route", "In3 from the balls B3 / C4 to a via pair just above fingers 219 / 221 (y = %.2f mm), then %.2f mm of F.Cu into the fingers: the same "
                         "form as a read DQ line. The strobe is NOT on the centre of the read group (it need not be: the group is closed per bit); it "
                         "arrives inside the span of the eight bits" % (D["rdqs"]["via_pair"][0][1], D["rdqs"]["via_pair"][0][1] - 2.7)),
               ("note", "the strobe is not delayed by this table: it arrives before the latest DQ, and the DQ are aligned to the latest DQ")]),
        strobe_polarity="inverted on the host pair DQS6: RDQS_P (RDQS_t) on the N pin (finger 219, AL8), RDQS_N (RDQS_c) on the P pin (finger 221, AK8)")
    dk["wck"] = collections.OrderedDict(
        per_leg=collections.OrderedDict((n, collections.OrderedDict(finger=ptab[n]["finger"], fpga_ball=fingers[ptab[n]["finger"]]["fpga_ball"],
                                                                  total_ps=round(ptab[n]["total"], 2))) for n in ("WCK_P", "WCK_N")),
        polarity="inverted on the host pair CK0: WCK_P (WCK_t) on the N pin, WCK_N (WCK_c) on the P pin",
        later_than_the_latest_dq_ps=round((ptab["WCK_P"]["total"] + ptab["WCK_N"]["total"]) / 2 - w_hi, 1))
    dk["reset_n"] = collections.OrderedDict(finger=D["reset_n"]["finger"], fpga_ball=fingers[D["reset_n"]["finger"]]["fpga_ball"], bank=fingers[D["reset_n"]["finger"]]["bank"],
                                            io="static LVCMOS12 output")
    plan_tab = gw["deskew"]["per_bit_output_delay_ps"]
    plan_rd = gr["deskew"]["per_bit_input_delay_ps"]
    print("        gateware deskew, write (%s):" % dk["status"])
    print("        bit   finger  FPGA ball  bank  group  total ps   output delay ps (plan)")
    for n in sorted(dq):
        q = dk["write"]["per_bit"][n]
        print("        %-4s   %3d     %-5s     %s   %-4s  %7.2f     %5.1f  (%5.1f)" % (n, q["finger"], q["fpga_ball"], q["bank"], q["byte_group"], q["total_ps"], q["output_delay_ps"], plan_tab[n]))
    print("        gateware deskew, read (%s):" % dk["read_status"])
    print("        bit   P / N finger  FPGA ball P  bank  group  card ps  host ps  total ps  to centre  to strobe   input delay ps (plan)")
    for n in sorted(dq):
        q = dk["read"]["per_bit"][n]
        print("        %-4s   %3d / %3d     %-5s       %s   %-4s  %7.2f   %6.1f   %7.2f   %+7.2f    %+7.2f     %5.1f  (%5.1f)"
              % (n, q["p_finger"], q["n_finger"], q["p_fpga_ball"], q["bank"], q["byte_group"], q["card_ps"], q["host_plus_package_ps"], q["total_ps"], q["to_centre_ps"],
                 q["to_strobe_ps"], q["input_delay_ps"], plan_rd[n]))
    print("        RDQS   %3d / %3d     %-5s       %s   %-4s  %7.2f   %6.1f   %7.2f   %+7.2f               0.0   (strobe: P / N legs, total of RDQS_N)"
          % (ptab["RDQS_P"]["finger"], ptab["RDQS_N"]["finger"], fingers[ptab["RDQS_P"]["finger"]]["fpga_ball"] + "/" + fingers[ptab["RDQS_N"]["finger"]]["fpga_ball"],
             fingers[ptab["RDQS_N"]["finger"]]["bank"], fingers[ptab["RDQS_N"]["finger"]]["bytegroup"], ptab["RDQS_N"]["card"], ptab["RDQS_N"]["host"], ptab["RDQS_N"]["total"],
             rq_tot - r_cen))
    if write_deskew:
        with open(DESKEW, "w") as fh:
            json.dump(dk, fh, indent=1)
            fh.write("\n")
    check(G, "gateware deskew table: per-bit output delay 0 .. %.1f ps and per-bit input delay 0 .. %.1f ps = latest total of the group - own total, equal to the plan "
          "within 1 ps on every bit; after them the eight write totals and the eight read totals are equal by construction, and the read data arrive %.1f ps after "
          "the strobe; written to design/gateware_deskew.json with the pin map and the pair polarities"
          % (w_hi - w_lo, r_hi_dq - r_lo, r_hi_dq - rq_tot),
          all(abs(dk["write"]["per_bit"][n]["output_delay_ps"] - plan_tab[n]) <= 1.0 for n in dq)
          and all(abs(dk["read"]["per_bit"][n]["input_delay_ps"] - plan_rd[n]) <= 1.0 for n in dq)
          and min(q["input_delay_ps"] for q in dk["read"]["per_bit"].values()) == 0.0
          and max(q["input_delay_ps"] for q in dk["read"]["per_bit"].values()) <= 0.25 * gr["deskew"]["idelay_range_ps"][1]
          and (not write_deskew or os.path.exists(DESKEW)))

    # ---- 4. angles, self-overlap, same-layer crossings -----------------------------------------------------------------------
    sig_tracks = [t for t in tracks if t.GetNetname() in hs_nets]
    n_v, worst, bad_v, hist = vertex_angles(board, tracks + [t for t in st_tracks if t.GetNetname() not in data_nets])
    odd = [t.GetNetname() for t in st_tracks if not (abs(t.GetStart().x - t.GetEnd().x) < 3 or abs(t.GetStart().y - t.GetEnd().y) < 3
                                                     or abs(abs(t.GetStart().x - t.GetEnd().x) - abs(t.GetStart().y - t.GetEnd().y)) < 3)]
    arcs = [t for t in st_tracks if t.Type() == pcbnew.PCB_ARC_T]
    check(G, "angles: %d vertices and junctions between track items of the nets of the stage (every pair of items that meet in a point on one layer: bends, "
          "the necks into the lines, bus taps, the load links on the RDQS legs, the GND links), smallest interior angle %.1f degrees, none below 90 "
          "(histogram %s); %d straight items at 0 / 45 / 90 / 135 degrees (%d at other angles); no arc" % (n_v, worst, hist, len(st_tracks), len(odd)),
          not bad_v and not odd and not arcs, str(bad_v[:6]))
    # a wire lying over itself: two items of one net on different layers that run parallel within 0.20 mm of each other in plan view
    self_ov, hair = [], {}
    for n in sorted(hs_nets):
        ts = by_net[n]
        tot = 0.0
        for i, a in enumerate(ts):
            for b in ts[i + 1:]:
                if lname(a) == lname(b):
                    continue
                la, lb = cl(a), cl(b)
                if la.distance(lb) > 0.2:
                    continue
                (ax0, ay0), (ax1, ay1) = la.coords
                (bx0, by0), (bx1, by1) = lb.coords
                if min(math.dist(p_, q_) for p_ in la.coords for q_ in lb.coords) < 1e-6:
                    continue        # the two items meet on a via: one node (the back-to-back links of a divider column, a leg into its node via)
                ua, ub = (ax1 - ax0, ay1 - ay0), (bx1 - bx0, by1 - by0)
                if la.length < 1e-6 or lb.length < 1e-6:
                    continue
                cosang = abs(ua[0] * ub[0] + ua[1] * ub[1]) / (la.length * lb.length)
                if cosang < math.cos(math.radians(15)):
                    continue
                ov = la.intersection(lb.buffer(0.2)).length
                if ov > 0.05:
                    tot += ov
        if tot > 0:
            self_ov.append((n, round(tot, 2)))
    for n in dq:        # hairpin: the In3 branch running north beside its own F.Cu tooth (0.40 mm beside it, In1 and In2 between)
        tooth = [cl(t) for t in by_net[n] if lname(t) == "F.Cu" and abs(C.k2c(t.GetStart())[0] - cols[n]["x"]) < 1e-6 and abs(C.k2c(t.GetEnd())[0] - cols[n]["x"]) < 1e-6]
        riser = [cl(t) for t in by_net[n] if lname(t) == "In3.Cu" and abs(C.k2c(t.GetStart())[0] - C.k2c(t.GetEnd())[0]) < 1e-6]
        hair[n] = sum(r_.intersection(unary_union(tooth).buffer(0.45)).length for r_ in riser) if tooth and riser else 0.0
    selfx = []
    for n in sorted(hs_nets):
        ts = by_net[n]
        for i, a in enumerate(ts):
            for b in ts[i + 1:]:
                if lname(a) != lname(b):
                    continue
                la, lb = cl(a), cl(b)
                if la.touches(lb) or la.intersects(lb):
                    pt = la.intersection(lb)
                    ends = [tuple(q) for q in list(la.coords) + list(lb.coords)]
                    if pt.geom_type != "Point" or not any(math.dist((pt.x, pt.y), e) < 1e-6 for e in ends):
                        selfx.append((n, lname(a)))
                elif la.distance(lb) < 0.10 + (C.MM(a.GetWidth()) + C.MM(b.GetWidth())) / 2 - 1e-6 and not (
                        min(math.dist(p, q) for p in la.coords for q in lb.coords) < 0.9):
                    selfx.append((n, lname(a), "comes back to itself"))
    check(G, "no wire lies over itself: no two items of one high-speed net on different layers run parallel within 0.20 mm of each other in plan view "
          "(0 mm on %d nets; not counted: two items that start on the same via, i.e. the back-to-back links of a divider column on the two faces and "
          "the WCK_N leg under its own shunt link, 0.68 mm each with three solid planes between), and no net crosses or comes back onto itself on a "
          "layer.  What remains is the hairpin of the comb, stated: the In3 read "
          "branch runs back north 0.40 mm beside its own F.Cu tooth with In1 and In2 between (%s; sum %.1f mm)"
          % (len(hs_nets), ", ".join("%s %.2f" % (n, hair[n]) for n in dq), sum(hair.values())), not self_ov and not selfx, "%s %s" % (self_ov, selfx[:4]))
    # same-layer: no track of the stage crosses or touches a track of another net
    shapes = [(t, lname(t), cu(t)) for t in all_tracks]
    st_ids = {t.m_Uuid.AsString() for t in st_tracks}
    xing, gaps = [], collections.defaultdict(lambda: (9.9, ""))

    def upd(key, val, a, b, sa, sb):
        if val < gaps[key][0]:
            p = sa.centroid if sa.distance(sb) == 0 else None
            from shapely.ops import nearest_points
            q = nearest_points(sa, sb)[0]
            gaps[key] = (val, "%s / %s at (%.2f, %.2f)" % (a.GetNetname(), b.GetNetname(), q.x, q.y))

    def kind(t):
        n, lay = t.GetNetname(), lname(t)
        if re.fullmatch(r"DQ\d", n):
            if lay == "In3.Cu":
                return "RD"
            fx = [abs(C.k2c(q)[0] - D["read"][n]["read_via"][0]) for q in (t.GetStart(), t.GetEnd())]
            return "RD" if lay == D["read"][n]["finger_track_layer"] and max(fx) < 0.5 and abs(C.k2c(t.GetStart())[0] - cols[n]["x"]) > 1.0 else "WR"
        if n.endswith("_WF") or n.startswith("WCK_F") or n == "RESET_N_F":
            return "STUB"
        if n.startswith("WCK"):
            return "WCK"
        if n.startswith("RDQS"):
            return "RDQS"
        if n.startswith("VREF") or n == "DAC_OUT":
            return "VREF"
        return "GND" if n == "GND" else "OTHER"
    for i, (a, la, sa) in enumerate(shapes):
        for b, lb, sb in shapes[i + 1:]:
            if la != lb or a.GetNetCode() == b.GetNetCode():
                continue
            if a.m_Uuid.AsString() not in st_ids and b.m_Uuid.AsString() not in st_ids:
                continue
            ba, bb_ = sa.bounds, sb.bounds
            if ba[0] > bb_[2] + 1.6 or bb_[0] > ba[2] + 1.6 or ba[1] > bb_[3] + 1.6 or bb_[1] > ba[3] + 1.6:
                continue
            dd = sa.distance(sb)
            if dd < 1e-6:
                xing.append((a.GetNetname(), b.GetNetname(), la))
                continue
            ka, kb = sorted((kind(a), kind(b)))
            sa_, sb_ = sa, sb
            if sa.intersects(u1c) or sb.intersects(u1c):
                if (ka, kb) == ("WR", "WR") and la == "F.Cu":
                    upd("F.Cu DQ to DQ inside the U1 courtyard (necks)", dd, a, b, sa, sb)
                sa_, sb_ = sa.difference(u1c), sb.difference(u1c)
                if sa_.is_empty or sb_.is_empty:
                    upd("inside U1 (any two nets)", dd, a, b, sa, sb)
                    continue
                dd = sa_.distance(sb_)
                if dd > 1.6:
                    continue
            if (ka, kb) == ("WR", "WR") and la == "F.Cu":
                east = _box(27.2, 0, 70, 30)            # the necks end 0.025 mm outside the courtyard line, 0.226 mm apart; the bundle starts here
                s1, s2 = sa_.intersection(east), sb_.intersection(east)
                if not s1.is_empty and not s2.is_empty:
                    upd("F.Cu DQ bundle (lines, fan, teeth)", s1.distance(s2), a, b, s1, s2)
                else:
                    upd("F.Cu DQ lines beside U1, before the bundle (x < 27.2)", dd, a, b, sa_, sb_)
            elif (ka, kb) == ("RD", "RD") and la == "In3.Cu":
                upd("In3 DQ - DQ", dd, a, b, sa_, sb_)
            elif "WCK" in (ka, kb) and ka != kb and la == "B.Cu":
                upd("B.Cu WCK pair to another track", dd, a, b, sa_, sb_)
            elif "WCK" in (ka, kb) and ka != kb and la == "F.Cu" and "GND" not in (ka, kb):
                upd("F.Cu WCK column links to %s" % ("the DQ4 tooth and series link" if "WR" in (ka, kb) else "a finger stub"), dd, a, b, sa_, sb_)
            elif "RDQS" in (ka, kb) and ka != kb and la == D["rdqs"]["outer_layer"] and "GND" not in (ka, kb) and "VREF" not in (ka, kb):
                rq = sa_ if kind(a) == "RDQS" else sb_
                upd("F.Cu RDQS load link to a read track (area rdqs_termination)" if rq.within(areas["rdqs_termination"])
                    else "F.Cu RDQS step from its via to the pair, to a read track (area rdqs_via_pair)" if rq.within(areas["rdqs_via_pair"])
                    else "F.Cu RDQS pair to another track", dd, a, b, sa_, sb_)
            elif "RDQS" in (ka, kb) and ka != kb and la == "In3.Cu":
                upd("In3 RDQS pair to another In3 track", dd, a, b, sa_, sb_)
            elif "VREF" in (ka, kb) and ka != kb and "GND" not in (ka, kb) and "OTHER" not in (ka, kb):
                vr = sa_ if kind(a) == "VREF" else sb_
                upd("VREF to a switching track" + (" (area vref_east_end)" if vr.intersects(areas["vref_east_end"]) else ""), dd, a, b, sa_, sb_)
            elif "GND" in (ka, kb) or "OTHER" in (ka, kb):
                upd("%s other" % la, dd, a, b, sa_, sb_)
            else:
                upd("%s %s / %s" % (la, ka, kb), dd, a, b, sa_, sb_)
    check(G, "crossings on one layer: no track of the stage crosses or touches a track of another net on its layer (%d track items of the stage against "
          "all %d track items of the board)" % (len(st_tracks), len(all_tracks)), not xing, str(xing[:6]))

    # ---- 5. spacings (upstream figures and the project's own) ------------------------------------------------------------------
    # In3 lines against the In3 pads of foreign vias, and against the holes of vias that carry no In3 pad
    pad_gap, hole_gap = (9.9, ""), (9.9, "")
    for t in tracks:
        if lname(t) != "In3.Cu" or not re.fullmatch(r"DQ\d", t.GetNetname()):
            continue
        st = cu(t)
        for v in vias:
            if v.GetNetCode() == t.GetNetCode():
                continue
            x, y = C.k2c(v.GetPosition())
            dd = st.distance(Point(x, y))
            if dd > 1.0:
                continue
            flashed = v.FlashLayer(pcbnew.In3_Cu)
            if flashed and dd - C.MM(v.GetWidth(pcbnew.In3_Cu)) / 2 < pad_gap[0]:
                pad_gap = (dd - C.MM(v.GetWidth(pcbnew.In3_Cu)) / 2, "%s to the %s via at (%.2f, %.2f)" % (t.GetNetname(), v.GetNetname(), x, y))
            if not flashed and dd - C.MM(v.GetDrill()) / 2 < hole_gap[0]:
                hole_gap = (dd - C.MM(v.GetDrill()) / 2, "%s to the %s via at (%.2f, %.2f)" % (t.GetNetname(), v.GetNetname(), x, y))
    # F.Cu lines and teeth against the pads of foreign vias (outside U1)
    fpad_gap = (9.9, "")
    for t in tracks:
        if lname(t) != "F.Cu" or not re.fullmatch(r"DQ\d", t.GetNetname()) or abs(C.MM(t.GetWidth()) - 0.15) > 1e-6:
            continue
        st = cu(t).difference(u1c)
        if st.is_empty:
            continue
        for v in vias:
            if v.GetNetCode() == t.GetNetCode():
                continue
            x, y = C.k2c(v.GetPosition())
            dd = st.distance(Point(x, y)) - C.MM(v.GetWidth(pcbnew.F_Cu)) / 2
            if dd < fpad_gap[0]:
                fpad_gap = (dd, "%s to the %s via at (%.2f, %.2f)" % (t.GetNetname(), v.GetNetname(), x, y))
    rdc, wk_, rq_, vrf = ncl["RD_DQ"], ncl["WR_WCK"], ncl["RD_RDQS"], ncl["VREF"]
    lim = collections.OrderedDict([
        ("F.Cu DQ bundle (lines, fan, teeth)", ncl["WR_DQ"]["gap_min_outside_u1"]), ("In3 DQ - DQ", rdc["in3_gap_min"]),
        ("B.Cu WCK pair to another track", wk_["pair_to_other_min"]), ("F.Cu WCK column links to the DQ4 tooth and series link", wk_["f_cu_links_to_other_tracks_min"]),
        ("F.Cu RDQS pair to another track", rq_["pair_to_other_min"]), ("F.Cu RDQS load link to a read track (area rdqs_termination)", 0.40),
        ("F.Cu RDQS step from its via to the pair, to a read track (area rdqs_via_pair)", 0.60),
        ("In3 RDQS pair to another In3 track", rq_["pair_to_other_min"]),
        ("VREF to a switching track", vrf["keep_from_switching_nets"]), ("VREF to a switching track (area vref_east_end)", vrf["keep_from_switching_nets_east_end"])])
    low = [(k, round(gaps[k][0], 4)) for k, v in lim.items() if gaps[k][0] < v - 1.5e-3]
    check(G, "spacings, measured copper to copper on the board (upstream figures: bundle gap 0.40, In3 gap 0.40 where upstream has 0.25, pairs 0.80): "
          + "; ".join("%s >= %.2f mm: %.3f" % (k, v, gaps[k][0]) for k, v in lim.items() if gaps[k][1])
          + "; In3 DQ line to the In3 pad of a foreign via >= %.2f: %.3f; to the hole of a via without In3 pad >= 0.20: %.3f; F.Cu DQ line or tooth to the pad of a "
          "foreign via >= %.2f: %.3f" % (rdc["in3_to_foreign_via_pad_min"], pad_gap[0], hole_gap[0], rdc["in3_to_foreign_via_pad_min"], fpad_gap[0]),
          not low and pad_gap[0] >= rdc["in3_to_foreign_via_pad_min"] - 1.5e-3 and hole_gap[0] >= 0.20 - 1.5e-3 and fpad_gap[0] >= rdc["in3_to_foreign_via_pad_min"] - 1.5e-3
          and all(gaps[k][1] for k in ("F.Cu DQ bundle (lines, fan, teeth)", "In3 DQ - DQ", "B.Cu WCK pair to another track", "F.Cu RDQS pair to another track",
                                       "In3 RDQS pair to another In3 track")), str(low))
    print("        smallest gaps between a track of the stage and a track of another net, with the place:")
    for k in sorted(gaps):
        print("          %-62s %.3f mm  (%s)" % (k, gaps[k][0], gaps[k][1]))
    print("          %-62s %.3f mm  (%s)" % ("In3 DQ line to the In3 pad of a foreign via", pad_gap[0], pad_gap[1]))
    print("          %-62s %.3f mm  (%s)" % ("In3 DQ line to the hole of a via without In3 pad", hole_gap[0], hole_gap[1]))
    print("          %-62s %.3f mm  (%s)" % ("F.Cu DQ line or tooth to the pad of a foreign via", fpad_gap[0], fpad_gap[1]))
    print("        known spots below the upstream / own figure, recorded: DQ necks inside the U1 courtyard %.3f mm apart (upstream 0.10); F.Cu WCK finger stub to the"
          % gaps["F.Cu DQ to DQ inside the U1 courtyard (necks)"][0])
    print("        DQ4 stub %.3f mm (HOST_SE to HOST_SE, not a pair rule); the western RDQS load link %.3f mm from the read track of finger 215 inside the area rdqs_termination (limit 0.40; "
          "the legs themselves keep %.3f mm)"
          % (gaps["F.Cu STUB / STUB"][0] if "F.Cu STUB / STUB" in gaps else float("nan"), gaps["F.Cu RDQS load link to a read track (area rdqs_termination)"][0],
             gaps["F.Cu RDQS pair to another track"][0]))

    # ---- 6. the pairs: gap, coupled length -----------------------------------------------------------------------------------------
    def coupling(np_, nn_, lay):
        tp, tn = [t for t in by_net[np_] if lname(t) == lay], [t for t in by_net[nn_] if lname(t) == lay]
        out = {}
        for a_, b_, name in ((tp, tn, np_), (tn, tp, nn_)):
            other = unary_union([cl(t) for t in b_])
            tot = cpl = 0.0
            gmin, gmax = 9.9, 0.0
            for t in a_:
                if C.MM(t.GetWidth()) > 0.105:
                    continue
                l_ = cl(t)
                n_ = max(1, int(l_.length / 0.02))
                for i in range(n_):
                    p = l_.interpolate((i + 0.5) / n_, normalized=True)
                    dd = p.distance(other)
                    tot += l_.length / n_
                    if abs(dd - 0.20) <= 0.0101:
                        cpl += l_.length / n_
                        gmin, gmax = min(gmin, dd - 0.10), max(gmax, dd - 0.10)
            out[name] = (tot, cpl, gmin, gmax)
        return out
    cw = coupling("WCK_P", "WCK_N", "B.Cu")
    cq3 = coupling("RDQS_P", "RDQS_N", "In3.Cu")
    cqb = coupling("RDQS_P", "RDQS_N", D["rdqs"]["outer_layer"])
    unc_w = {n: cw[n][0] - cw[n][1] for n in cw}
    unc_q = {n: (cq3[n][0] - cq3[n][1]) + (cqb[n][0] - cqb[n][1]) for n in cq3}
    gmin = min(c[n][2] for c in (cw, cq3, cqb) for n in c)
    gmax = max(c[n][3] for c in (cw, cq3, cqb) for n in c)
    check(G, "pairs: WCK on B.Cu coupled over %.1f of %.1f / %.1f mm, uncoupled per leg %.2f / %.2f mm (limit %.1f); RDQS coupled over %.1f mm of In3 and %.1f mm "
          "of F.Cu, uncoupled per leg %.2f (P, with the skew bump) / %.2f mm (N) (limit %.2f, the DRC value); gap where coupled %.4f .. %.4f mm "
          "(0.10 +/- 0.01); nothing on a pair net is wider than 0.10 mm except the 0.20 mm F.Cu links of the two WCK columns"
          % (cw["WCK_P"][1], cw["WCK_P"][0], cw["WCK_N"][0], unc_w["WCK_P"], unc_w["WCK_N"], wk_["max_uncoupled_b_cu_mm"], cq3["RDQS_P"][1], cqb["RDQS_P"][1],
             unc_q["RDQS_P"], unc_q["RDQS_N"], rq_["max_uncoupled_mm"], gmin, gmax),
          max(unc_w.values()) <= wk_["max_uncoupled_b_cu_mm"] and max(unc_q.values()) <= rq_["max_uncoupled_mm"] and gmin >= 0.09 - 1e-4 and gmax <= 0.11 + 1e-4
          and not [t for n in ("RDQS_P", "RDQS_N") for t in by_net[n] if C.MM(t.GetWidth()) > 0.1001]
          and not [t for n in ("WCK_P", "WCK_N") for t in by_net[n] if C.MM(t.GetWidth()) > 0.1001 and lname(t) != "F.Cu"])
    # the skew bump as built
    bp = D["rdqs"]["bump"]
    legs_ = [t for t in by_net[bp["net"]] if lname(t) == bp["layer"] and abs(C.k2c(t.GetStart())[0] - C.k2c(t.GetEnd())[0]) < 1e-6
             and bp["x"][0] - 1e-3 <= C.k2c(t.GetStart())[0] <= bp["x"][1] + 1e-3
             and bp["bottom_y"] - 1e-3 <= min(C.k2c(t.GetStart())[1], C.k2c(t.GetEnd())[1]) and max(C.k2c(t.GetStart())[1], C.k2c(t.GetEnd())[1]) <= D["knobs"]["rdqs_low_run_y"]]
    other_bump = [t for t in by_net["RDQS_N"] + by_net["WCK_P"] + by_net["WCK_N"] if lname(t) != "F.Cu" and track_len(t) > 0.05
                  and any(track_len(o) > 0.05 and lname(o) == lname(t) and o is not t and cl(o).distance(cl(t)) < 0.9 and cl(o).distance(cl(t)) > 0.15
                          and abs(abs((C.k2c(o.GetEnd())[0] - C.k2c(o.GetStart())[0]) * (C.k2c(t.GetEnd())[0] - C.k2c(t.GetStart())[0])
                                      + (C.k2c(o.GetEnd())[1] - C.k2c(o.GetStart())[1]) * (C.k2c(t.GetEnd())[1] - C.k2c(t.GetStart())[1])) / (track_len(o) * track_len(t)) - 1) < 1e-6
                          and ((C.k2c(o.GetEnd())[0] - C.k2c(o.GetStart())[0]) * (C.k2c(t.GetEnd())[0] - C.k2c(t.GetStart())[0])
                               + (C.k2c(o.GetEnd())[1] - C.k2c(o.GetStart())[1]) * (C.k2c(t.GetEnd())[1] - C.k2c(t.GetStart())[1])) < 0 for o in by_net[t.GetNetname()])]
    leg_gap = abs(C.k2c(legs_[0].GetStart())[0] - C.k2c(legs_[1].GetStart())[0]) - 0.10 if len(legs_) == 2 else 0.0
    bump_poly = unary_union([cu(t) for t in legs_]).buffer(0.25) if legs_ else None
    near_bump = min([s_.distance(bump_poly) + 0.25 for t, l_, s_ in shapes if l_ == bp["layer"] and t.GetNetname() not in ("RDQS_P", "RDQS_N")] or [9.9]) if bump_poly else 0.0
    u1_e = next(r_["x"][1] for r_ in fpl["regions"] if r_["name"] == "U1")          # east edge of the land field
    check(G, "tuning as built: no serpentine and no accordion on any net; ONE skew bump, on %s only, on In3 %.2f .. %.2f mm east of the land field of U1 (where the "
          "skew of the ball rows arises: the pair is in step over the rest of its length): two legs %.3f mm deep, copper gap %.3f mm between them (>= %.2f = 3 h), "
          "pointing south, away from RDQS_N; nearest other copper on In3 %.2f mm; RDQS_N and both WCK legs have no leg that turns back"
          % (bp["net"], bp["x"][0] - u1_e, bp["x"][1] - u1_e, track_len(legs_[0]) if legs_ else 0, leg_gap, rq_["bump_leg_gap_min"], near_bump),
          len(legs_) == 2 and bp["layer"] == "In3.Cu" and leg_gap >= rq_["bump_leg_gap_min"] - 1e-6 and abs(track_len(legs_[0]) - bp["depth_mm"]) < 2e-3
          and not other_bump and near_bump >= 0.40 and bp["x"][1] - u1_e <= 2.0)

    # ---- 7. widths and layer purity ------------------------------------------------------------------------------------------
    want_seg = collections.Counter()
    for w_ in D["wires"]:
        for a, b in zip(w_["path"], w_["path"][1:]):
            want_seg[(w_["net"], w_["layer"], round(w_["width"], 4), tuple(sorted([(round(a[0], 4), round(a[1], 4)), (round(b[0], 4), round(b[1], 4))])))] += 1
    have_seg = collections.Counter((t.GetNetname(), lname(t), round(C.MM(t.GetWidth()), 4),
                                    tuple(sorted([tuple(round(c, 4) for c in C.k2c(t.GetStart())), tuple(round(c, 4) for c in C.k2c(t.GetEnd()))]))) for t in st_tracks)
    wd = collections.Counter((kind(t), lname(t), round(C.MM(t.GetWidth()), 3)) for t in st_tracks)
    foreign = [t.GetNetname() for t in tracks if C.stage_of(t) != STAGE and t.GetNetname() in hs_nets]
    check(G, "widths: every track item of the stage has the net, layer and width the spec gives it (%d items = the %d centre lines of floorplan.data.wires): necks 0.09, "
          "lines and teeth 0.15, links and finger tracks 0.20 (0.15 through the finger-via row), In3 branches 0.15, pairs 0.10, WCK column links 0.40, reference "
          "0.15, GND links 0.25; no copper of another stage on a high-speed net of the data side" % (len(st_tracks), len(D["wires"])),
          want_seg == have_seg and not foreign, "missing %s; extra %s; foreign %s" % (list((want_seg - have_seg).items())[:3], list((have_seg - want_seg).items())[:3], foreign[:4]))
    print("        track items of the stage by kind, layer and width: %s" % ", ".join("%s %s %.2f x%d" % (k[0], k[1][:-3], k[2], v) for k, v in sorted(wd.items())))
    in3_other = sorted({t.GetNetname() for t in all_tracks if lname(t) == "In3.Cu" and max(C.k2c(t.GetStart())[0], C.k2c(t.GetEnd())[0]) > 26.95
                        and not re.fullmatch(r"DQ\d|RDQS_[PN]", t.GetNetname())})
    plane_tr = [t for t in st_tracks if lname(t) in ("In1.Cu", "In2.Cu", "In4.Cu")]
    per_layer = {}
    for lay in ("F.Cu", "In3.Cu", "B.Cu"):
        mm = collections.Counter()
        for t in st_tracks:
            if lname(t) == lay:
                mm[t.GetNetname()] += track_len(t)
        per_layer[lay] = mm
    f_region = _box(27.2, 5.6, 44.3, 17.0)
    f_foreign = sorted({t.GetNetname() for t in all_tracks if lname(t) == "F.Cu" and cl(t).intersects(f_region) and not re.fullmatch(r"DQ\d|GND", t.GetNetname())
                        and not (t.GetNetname().startswith("WCK") and C.k2c(t.GetStart())[0] < 36.4)})
    rq_b = [t for n in ("RDQS_P", "RDQS_N") for t in by_net[n] if lname(t) == "B.Cu"]
    rd_b = sorted({t.GetNetname() for n in dq for t in by_net[n] if lname(t) == "B.Cu" and kind(t) == "RD"})
    # RDQS: In3 from U1 to its via pair just above the fingers, then a short F.Cu drop only (user: stay on In3 until just above the fingers)
    yv_rq = D["rdqs"]["via_pair"][0][1]
    rq_f = [t for n in ("RDQS_P", "RDQS_N") for t in by_net[n] if lname(t) == "F.Cu"]
    rq_f_out = [t.GetNetname() for t in rq_f if max(C.k2c(t.GetStart())[1], C.k2c(t.GetEnd())[1]) > yv_rq + 1e-6
                or min(C.k2c(t.GetStart())[0], C.k2c(t.GetEnd())[0]) < D["rdqs"]["down_x"] - 0.7 or max(C.k2c(t.GetStart())[0], C.k2c(t.GetEnd())[0]) > D["rdqs"]["down_x"] + 0.7]
    rq_f_mm = {n: sum(track_len(t) for t in by_net[n] if lname(t) == "F.Cu") for n in ("RDQS_P", "RDQS_N")}
    rq_vias = [v for v in board.GetTracks() if v.Type() == pcbnew.PCB_VIA_T and v.GetNetname() in ("RDQS_P", "RDQS_N")]
    rd_drop = [q["read_via"][1] - 2.7 for q in D["read"].values()]
    check(G, "layer purity: In3 east of U1 carries only the eight read branches and the RDQS pair (other nets: %s; RESET_n is no longer on "
          "In3: it runs on B.Cu, gate 9); "
          "F.Cu between U1 and the corridor (x 27.2 .. 44.3, y 5.6 .. 17.0) carries only the eight DQ lines, teeth and links (other nets: %s); B.Cu carries the WCK "
          "pair, %d read finger tracks, the back-side links and stubs and the back-side reference network, and nothing of the RDQS pair.  RDQS changes layer "
          "ONCE (2 vias per leg: the via-in-pad at the ball and one via above its finger): In3 from U1 to the via pair at y = %.2f, then only the drop on F.Cu, "
          "%.2f / %.2f mm per leg with its load link (finger top to via %.2f mm; the read DQ finger tracks are %.2f .. %.2f mm); In1 / In2 / In4 carry no track"
          % (in3_other or "none", f_foreign or "none", len(rd_b), yv_rq, rq_f_mm["RDQS_P"], rq_f_mm["RDQS_N"], yv_rq - 2.7, min(rd_drop), max(rd_drop)),
          not in3_other and not plane_tr and not f_foreign and not rq_b and len(rd_b) == 7 and not rq_f_out and len(rq_vias) == 4
          and max(rq_f_mm.values()) <= 7.0 and yv_rq - 2.7 <= min(rd_drop) + 0.5, str(rq_f_out))
    for lay in ("F.Cu", "In3.Cu", "B.Cu"):
        print("        %-6s mm per net: %s" % (lay, ", ".join("%s %.2f" % kv for kv in sorted(per_layer[lay].items()))))
    cor = D["reserved"]["v1p2_feed_corridor"]
    cbx = _box(cor["x"][0], cor["y"][0], cor["x"][1], cor["y"][1])
    in_cor = sorted({t.GetNetname() for t in all_tracks if lname(t) == cor["layer"] and cu(t).intersects(cbx) and C.stage_of(t) != "s50_power"
                     and not (t.GetNetname() == "GND" and C.stage_of(t) == "s10_escape")})
    via_cor = [v.GetNetname() for v in vias if cbx.buffer(-0.01).contains(Point(*C.k2c(v.GetPosition()))) and C.stage_of(v) not in ("s50_power",)
               and not (C.stage_of(v) == "s10_escape" and C.k2c(v.GetPosition())[1] < 3.2)]
    pw_cor = sorted({t.GetNetname() for t in all_tracks if lname(t) == cor["layer"] and cu(t).intersects(cbx) and C.stage_of(t) == "s50_power"})
    check(G, "corridor reserved for the 1.2 V feed (F.Cu, x %.1f .. %.1f, y %.1f .. %.1f): no track and no via of the data side inside it; the power stage has "
          "put there: %s (the 1.2 V strip, the GND links of C512 / C502 to VSS finger vias, PG and the 3.3 V trunk passing north of the strip's end); the finger "
          "vias of the VSS pairs at y = 3.05 stand at its foot" % (cor["x"][0], cor["x"][1], cor["y"][0], cor["y"][1], ", ".join(pw_cor) or "nothing yet"),
          not in_cor and not via_cor and (not pw_cor or set(pw_cor) <= {"V1P2", "GND", "PG_VDD2H", "V3P3"}), "%s %s %s" % (in_cor, via_cor, pw_cor))

    # ---- 8. vias ----------------------------------------------------------------------------------------------------------
    v_bad = []
    for n in dq:
        vs = [v for v in vias if v.GetNetname() == n]
        if len(vs) != 2 or len(rtab[n]["vias"]) != 2 or len(wtab[n]["vias"]) != (0 if fingers[wtab[n]["finger"]]["side"] == "F" else 1):
            v_bad.append((n, len(vs)))
        for v in vs:
            if not v.FlashLayer(pcbnew.In3_Cu) or v.FlashLayer(pcbnew.In1_Cu) or v.FlashLayer(pcbnew.In2_Cu) or v.FlashLayer(pcbnew.In4_Cu):
                v_bad.append((n, "inner pads"))
    st_vias = [v for v in vias if C.stage_of(v) == STAGE]
    check(G, "vias: every DQ net has exactly two 0.40 / 0.20 vias, the node via (the T) and the read via; a write path passes none (front finger) or the node via "
          "from B.Cu to F.Cu (back finger), a read path both (F.Cu to In3, In3 to the finger's face); they carry a pad on In3 and none on In1 / In2 / In4; "
          "the stage added no via (all are field vias of floorplan.vias)", not v_bad and not st_vias, str(v_bad[:6]))
    gnd_v = [C.k2c(v.GetPosition()) for v in vias if v.GetNetname() == "GND"]
    rv = {n: min(math.dist(tuple(D["read"][n]["read_via"]), g_) for g_ in gnd_v) for n in dq}
    nd = {n: min(math.dist((cols[n]["x"], D["rows_y"]["node_via"]), g_) for g_ in gnd_v) for n in dq}
    rqd = [min(math.dist(tuple(p), g_) for g_ in gnd_v) for p in D["rdqs"]["via_pair"]]
    wkd = {n: min(math.dist((D["wck"]["columns_x"][n], D["wck"]["node_via_y"]), g_) for g_ in gnd_v) for n in ("WCK_P", "WCK_N")}
    far = [n for n in dq if nd[n] > rdc["return_via_max_mm"]]
    exc_ = rdc["node_return_via_exception"]
    rv_side = {n: min(gnd_v, key=lambda g_: math.dist(tuple(D["read"][n]["read_via"]), g_)) for n in dq}        # the nearest GND via of each read via
    north = [n for n in dq if rv_side[n][1] > D["read"][n]["read_via"][1] + 0.25]      # in the lane of the next In3 line (0.55 mm north of the read via)
    n_ret = len({tuple(rv_side[n]) for n in dq})
    check(G, "return vias at the layer changes (rule: within %.2f mm of %s): read vias %s; no return via stands north of its read via, in the lane of the next In3 line (%d do): each stands "
          "0.64 mm to the south-east of its read via, or to the south-west where that spot is taken; %d return vias serve "
          "the eight read vias (two of them stand between two read vias, 0.56 .. 0.72 mm from each, and serve both); RDQS via pair %.2f / %.2f (mirror images); node vias of the outer columns %s; WCK node vias "
          "%.2f / %.2f.  STATED EXCEPTION of the spec (rules.netclasses.RD_DQ.node_return_via_exception, limit %.1f mm): the node vias of %s have only the "
          "shunt GND vias south of the row (%s): a through via does not fit between the teeth (0.8 mm pitch, tooth on the axis, In3 riser in the lane)"
          % (rdc["return_via_max_mm"], rdc["return_via_applies_to"], ", ".join("%s %.2f" % (n, rv[n]) for n in dq), len(north), n_ret, rqd[0], rqd[1],
             ", ".join("%s %.2f" % (n, nd[n]) for n in dq if n not in exc_["nets"]), wkd["WCK_P"], wkd["WCK_N"], exc_["max_mm"], " ".join(exc_["nets"]),
             ", ".join("%s %.2f" % (n, nd[n]) for n in exc_["nets"])),
          max(rv.values()) <= rdc["return_via_max_mm"] + 1e-9 and max(rqd) <= 1.0 and sorted(far) == sorted(exc_["nets"])
          and max(nd[n] for n in exc_["nets"]) <= exc_["max_mm"] and not north and min(rv.values()) >= rdc["return_via_min_mm"] - 1e-9)

    # ---- 9. reference planes -----------------------------------------------------------------------------------------------
    card = _box(-1, -1, 80, 40)
    fills = {l_: gnd_fill(board, l_, card) for l_ in ("In1.Cu", "In2.Cu", "In4.Cu")}
    v1p2 = gnd_fill(board, fpl["power"]["v1p2_island"]["layer"], card, net="V1P2")
    own_via = collections.defaultdict(list)
    for v in vias:
        own_via[v.GetNetname()].append(Point(*C.k2c(v.GetPosition())).buffer(0.45))

    def cover(ts, plane_layers, clip=None):
        """Fraction of the copper of the items (outside the antipads of the net's own vias) that lies over GND fill on each given plane layer."""
        out = []
        for lay in plane_layers:
            a_tot = a_on = 0.0
            for t in ts:
                s_ = cu(t)
                if clip is not None:
                    s_ = s_.intersection(clip)
                if own_via[t.GetNetname()]:
                    s_ = s_.difference(unary_union(own_via[t.GetNetname()]))
                if s_.is_empty:
                    continue
                a_tot += s_.area
                a_on += s_.intersection(fills[lay]).area
            out.append(100.0 * a_on / a_tot if a_tot else 100.0)
        return out
    outside_u1 = card.difference(_box(fpl["U1"]["body"]["x"][0], fpl["U1"]["body"]["y"][0], fpl["U1"]["body"]["x"][1], fpl["U1"]["body"]["y"][1]))
    f_dq = [t for n in dq for t in by_net[n] if lname(t) == "F.Cu"]
    in3_dq = [t for n in dq for t in by_net[n] if lname(t) == "In3.Cu"]
    in3_rq = [t for n in ("RDQS_P", "RDQS_N") for t in by_net[n] if lname(t) == "In3.Cu"]
    b_rd = [t for n in dq for t in by_net[n] if lname(t) == "B.Cu" and kind(t) == "RD"]
    b_pairs = [t for n in ("WCK_P", "WCK_N", "RDQS_P", "RDQS_N") for t in by_net[n] if lname(t) == "B.Cu"]
    f_rq = [t for n in ("RDQS_P", "RDQS_N") for t in by_net[n] if lname(t) == "F.Cu"]
    f_rd = [t for n in dq for t in by_net[n] if lname(t) == "F.Cu" and kind(t) == "RD"]
    c_f = cover(f_dq, ["In1.Cu"], outside_u1)[0]
    c_f_u1 = cover(f_dq, ["In1.Cu"], _box(fpl["U1"]["body"]["x"][0], fpl["U1"]["body"]["y"][0], fpl["U1"]["body"]["x"][1], fpl["U1"]["body"]["y"][1]))[0]
    c_in3 = cover(in3_dq, ["In2.Cu", "In4.Cu"])
    c_rq = cover(in3_rq, ["In2.Cu", "In4.Cu"], outside_u1)
    c_rq_u1 = cover(in3_rq, ["In2.Cu", "In4.Cu"], _box(fpl["U1"]["body"]["x"][0], fpl["U1"]["body"]["y"][0], fpl["U1"]["body"]["x"][1], fpl["U1"]["body"]["y"][1]))
    c_brd = cover(b_rd, ["In4.Cu"])[0]
    c_bp = cover(b_pairs, ["In4.Cu"], outside_u1)[0]
    c_frq = cover(f_rq, ["In1.Cu"], outside_u1)[0]
    c_frd = cover(f_rd, ["In1.Cu"])[0]
    # back-finger stubs over the V1P2 island
    isl_rows = []
    for n in dq + ["WCK_P", "WCK_N"]:
        fn_ = n + "_WF" if n.startswith("DQ") else "WCK_F_" + n[-1]
        f = fin[fn_][0]
        if fingers[f]["side"] != "B":
            continue
        ln_ = unary_union([cl(t) for t in by_net[fn_]]).intersection(_box(0, ftop, 80, 40))
        isl_rows.append((fn_, f, ln_.length, ln_.intersection(v1p2).length))
    # the island is on In2: only In3 refers to that plane (0.11 mm); how near does any In3 copper of the data side come to it in plan
    isl_edge = min([cu(t).distance(v1p2) for t in in3_dq + in3_rq] or [9.9])
    in3_all = [t for t in all_tracks if lname(t) == "In3.Cu"]
    isl_edge_all = min([cu(t).distance(v1p2) for t in in3_all] or [9.9])
    in3_via_pads = min([Point(*C.k2c(v.GetPosition())).distance(v1p2) - C.MM(v.GetWidth(pcbnew.In3_Cu)) / 2 for v in vias if v.FlashLayer(pcbnew.In3_Cu) and v.GetNetname() not in ("GND", "V1P2")
                        and C.k2c(v.GetPosition())[1] > 3.5] or [9.9])        # the finger vias (y = 3.05) stand in the In3 keep-out of the finger area
    v2h = gnd_fill(board, "In4.Cu", card, net="VDD2H")
    v2_gap = min(cu(t).distance(v2h) for t in in3_dq + in3_rq + b_rd + b_pairs + f_rq)
    check(G, "reference planes: the F.Cu lines and teeth outside U1 over GND fill on In1 (%.1f %%; inside the land field the necks run over the perforated plane: "
          "%.1f %%); every In3 read branch between GND on In2 (%.1f %%) and In4 (%.1f %%), the antipads of its own two vias excepted; the RDQS In3 legs outside U1 "
          "between GND on In2 / In4 (%.1f / %.1f %%; inside U1 over the webs of the via field: %.1f / %.1f %%); the B.Cu read finger tracks over GND on In4 "
          "(%.1f %%: where they pass the row of the finger vias), the F.Cu read finger track of finger 215 over GND on In1 (%.1f %%), the B.Cu pair (WCK) outside U1 "
          "over GND on In4 (%.1f %%) and the F.Cu part of the RDQS pair, its drop and its two load links over solid GND on In1 (%.1f %%).  The V1P2 island is on In2, "
          "which only In3 refers to: no In3 track of the data side comes nearer than %.2f mm to it in plan (any In3 track of the board %.2f mm, the In3 pad "
          "of any signal via %.2f mm; limit 0.33 = 3 h), and no In3 line, B.Cu read track or pair nearer than %.2f mm to the VDD2H island on In4: nothing follows an island edge"
          % (c_f, c_f_u1, c_in3[0], c_in3[1], c_rq[0], c_rq[1], c_rq_u1[0], c_rq_u1[1], c_brd, c_frd, c_bp, c_frq, isl_edge, isl_edge_all, in3_via_pads, v2_gap),
          c_f >= 99.5 and min(c_in3) >= 99.5 and min(c_rq) >= 99.5 and c_brd >= 97.0 and c_frd >= 97.0 and c_bp >= 99.0 and c_frq >= 99.0 and len(f_rq) >= 8
          and min(isl_edge, isl_edge_all) >= 0.33
          and in3_via_pads >= 0.25 and v2_gap >= 0.9)
    stub_tr = [t for r_ in isl_rows for t in by_net[r_[0]] if lname(t) == "B.Cu"]
    c_stub = cover(stub_tr, ["In4.Cu"], _box(0, ftop, 80, 40))[0]
    f_stub = [t for n in dq + ["WCK_P", "WCK_N"] for t in by_net[n + "_WF" if n.startswith("DQ") else "WCK_F_" + n[-1]] if lname(t) == "F.Cu"]
    c_fstub = cover(f_stub, ["In1.Cu"], _box(0, ftop, 80, 40))[0]
    check(G, "reference of the write finger stubs: the back-finger stubs (fingers %s) run on B.Cu over GND on In4 (%.1f %% north of the finger top; no island "
          "on In4 under the finger row any more: the V1P2 island is on In2, two planes away), the front stubs over GND on In1 (%.1f %%); no stub crosses a plane split"
          % (", ".join(str(r_[1]) for r_ in isl_rows), c_stub, c_fstub),
          len(isl_rows) == 3 and c_stub >= 97.0 and c_fstub >= 97.0, str([(r_[0], round(r_[2], 2)) for r_ in isl_rows]))

    # ---- 9b. what the data side puts beside the command bus inside U1 (stage s20 is not touched) ----------------------------------------
    ca_tr = {n: [t for t in all_tracks if t.GetNetname() == n and lname(t) == "In3.Cu"] for n in ("CA0", "CA1")}
    c4 = via_at[tuple(round(c, 3) for c in C.ball(board, "C4"))]
    c4_pad = C.MM(c4.GetWidth(pcbnew.In3_Cu))
    pad_gap_ca0 = min(cu(t).distance(Point(*C.k2c(c4.GetPosition()))) for t in ca_tr["CA0"]) - c4_pad / 2
    new_b = {}
    for b_, cmd_n in (("A4", "CA0"), ("C5", "CA1"), ("C5", "CA0")):
        v_ = via_at.get(tuple(round(c, 3) for c in C.ball(board, b_)))
        new_b["%s to %s" % (b_, cmd_n)] = (min(cu(t).distance(Point(*C.k2c(v_.GetPosition()))) for t in ca_tr[cmd_n]) - C.MM(v_.GetDrill()) / 2, v_.GetNetname(), v_.FlashLayer(pcbnew.In3_Cu)) if v_ else None
    ca0_u = unary_union([cl(t) for t in ca_tr["CA0"]])
    leg_gap = min(cu(t).distance(unary_union([cu(q) for q in ca_tr["CA0"]])) for t in by_net["RDQS_N"] if lname(t) == "In3.Cu")
    near_mm = 0.0
    for t in by_net["RDQS_N"]:
        if lname(t) == "In3.Cu":
            l_ = cl(t)
            n_ = max(2, int(l_.length / 0.01))
            near_mm += sum(l_.length / n_ for i in range(n_) if l_.interpolate((i + 0.5) / n_, normalized=True).distance(ca0_u) < 0.6)
    ev = os.path.join(C.ROOT, D["command_zone_exception"]["evidence"])
    vd = json.load(open(ev)).get("verdict", {}) if os.path.exists(ev) else {}
    check(G, "command bus protection (the priority bus; stage s20 untouched): the In3 pad of the C4 via-in-pad (RDQS_N) is %.2f mm (trimmed) and stands %.3f mm from "
          "the CA0 trace (class clearance %.2f); the new GND barrels A4 / C5 stand %.3f / %.3f mm (hole wall) from CA0 / CA1, without In3 pad; the RDQS_N In3 leg "
          "leaves the pad away from CA0: %.3f mm copper to copper at its nearest, within 0.6 mm (centre lines) of CA0 for %.2f mm only.  HFSS on file (%s), "
          "geometry as built: CA0 delay %+.3f ps (limit %.1f), |S11| <= %.4f, RDQS -> CA0 coupling %.2f %% of the RDQS swing at the fastest edge (%.2f %% at 80 ps; "
          "limit %.0f %%): no re-tuning of the command bus"
          % (c4_pad, pad_gap_ca0, ncl["CMD_SE"]["clearance"], new_b["A4 to CA0"][0], new_b["C5 to CA1"][0], leg_gap, near_mm, D["command_zone_exception"]["evidence"],
             vd.get("ca0_delay_change_ps", float("nan")), vd.get("ca0_delay_limit_ps", float("nan")), vd.get("ca0_s11_max", float("nan")),
             vd.get("coupling_percent", float("nan")), vd.get("coupling_percent_at_80_ps", float("nan")), vd.get("coupling_limit_percent", float("nan"))),
          abs(c4_pad - 0.35) < 1e-6 and pad_gap_ca0 >= 0.15 - 1e-3 and all(v_ and v_[0] >= 0.20 - 1e-3 and v_[1] == "GND" and not v_[2] for v_ in new_b.values())
          and leg_gap >= 0.27 and near_mm <= D["command_zone_exception"]["max_mm_inside"] and vd.get("pass") is True
          and abs(vd.get("ca0_delay_change_ps", 9)) <= 0.3 and vd.get("coupling_percent", 9) <= 1.0)

    # ---- 10. quiet reference, DC branches, DRC ---------------------------------------------------------------------------------
    vref_in3 = [t.GetNetname() for t in tracks if kind(t) == "VREF" and lname(t) == "In3.Cu"]
    dac_path = copper_path(board, spec, ["DAC_OUT"], C.pad_xy(board, "U5.8") + ("F.Cu",), C.pad_xy(board, "R331.1") + ("F.Cu",)) is not None
    s50_ran = any(C.stage_of(t) == "s50_power" for t in board.GetTracks()) or any(C.stage_of(zn) == "s50_power" for zn in board.Zones())
    vddq_open = not any(t.GetNetname() == "VDDQ" and cu(t).distance(Point(*C.pad_xy(board, "R332.1"))) < 0.3 for t in all_tracks) and not any(
        (not zn.GetIsRuleArea()) and zn.GetNetname() == "VDDQ" and zn.IsOnLayer(pcbnew.F_Cu) for zn in board.Zones())
    check(G, "quiet reference: no VREF copper on In3; VREF tracks keep %s from every switching track on their layer (limit %.2f) and %.3f mm inside the area "
          "vref_east_end (limit %.2f: the bus changes side between the read tracks of fingers 212 / 215).  DC branches of VREF_RD: DAC (U5 - DAC_OUT - R331) routed; "
          "GND returns of R333 / C331 on own vias; VDDQ (R332 pad 1): %s"
          % ("%.3f mm" % gaps["VREF to a switching track"][0] if gaps["VREF to a switching track"][1] else "more than 1.6 mm", vrf["keep_from_switching_nets"],
             gaps["VREF to a switching track (area vref_east_end)"][0],
             vrf["keep_from_switching_nets_east_end"],
             "connected" if not vddq_open else ("OPEN - stage s50_power has run without connecting it" if s50_ran else "PENDING - stage s50_power has not run; this check fails once s50 has run without bringing VDDQ to R332.1")),
          not vref_in3 and dac_path and not (vddq_open and s50_ran))
    mine = collections.Counter()
    gap_out = []
    ox, oy = C.origin()
    for v in (drc or {}).get("violations", []):
        txt = " ".join(i["description"] for i in v["items"])
        nn = set(re.findall(r"\[([^\]]+)\]", txt))
        if nn & set(data_nets):
            mine["%s (%s)" % (v["type"], v["severity"])] += 1
            if v["type"] == "diff_pair_gap_out_of_range":
                gap_out.append([(round(i["pos"]["x"] - ox, 2), round(oy - i["pos"]["y"], 2)) for i in v["items"]][0])
    allowed = {"diff_pair_gap_out_of_range (warning)"}
    check(G, "DRC items on the nets of the stage: %s; the pair-gap warnings are the places where the legs of a pair must part: at the balls, at the node vias "
          "of the two WCK columns (F.Cu links 1.2 mm apart), at the RDQS via pair, at the skew bump and at the two load links (%s); no dangling via or "
          "track end on these nets, no error" % (dict(mine) or "none", gap_out), set(mine) <= allowed and mine.get("diff_pair_gap_out_of_range (warning)", 0) <= 9)


# ------------------------------------------------------------------------------------------------ 8 crossings
def gate_crossings(board_path, spec):
    G = "8 crossings"
    D = spec["floorplan"].get("data")
    if not D:
        return
    bud = D["crossing_budget"]
    tmp = tempfile.mkdtemp()
    out = os.path.join(tmp, "crossings.json")
    r = subprocess.run([sys.executable, os.path.join(C.HERE, "crossings.py"), "--board", board_path, "--json", out, "--quiet"],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if not os.path.exists(out):
        check(G, "scripts/pcb/crossings.py ran on the board", False, r.stdout[-400:])
        return
    res = json.load(open(out))
    s = res["summary"]
    print("        scripts/pcb/crossings.py on the built board (plan view, two different layers; pair legs one by one):")
    print("        command bundle, all %d (x data %d, x slow nets %d [the escapes of the unused byte, removed: 15 before], x supply straps %d, x GND links %d); data x data %d (pairs as one wire %d); "
          "high-speed x VREF %d; all crossings %d; without solid GND on every plane between %d"
          % (s["cmd_total"], s["cmd_x_data"], s["cmd_x_slow"], s["cmd_x_pwr"], s["cmd_x_gnd"], s["data_x_data"], s["pair_data_x_data"], s["hs_x_vref"], s["crossings_total"],
             s["crossings_without_solid_gnd_between"]))
    print("        overlap (parallel within 0.2 mm on two layers), mm: command x data %.2f, data x data %.2f, data x other %.2f; same-layer crossings %d; wires "
          "through a resistor group %d (own jogs off the column axis: %d)" % (s["overlap_mm_cmd_x_data"], s["overlap_mm_data_x_data"], s["overlap_mm_hs_other"], s["same_layer"],
                                                                             s["trans_through"], s["trans_own_jog"]))
    mx = collections.Counter()
    for m in res["matrix"]:
        if "CMD" not in (m["a_class"], m["b_class"]):
            mx["%s %s x %s %s" % (m["a_class"], m["a_layer"][:-3], m["b_class"], m["b_layer"][:-3])] += m["crossings"]
    print("        by class and layer: %s" % ", ".join("%s %d" % kv for kv in sorted(mx.items())))
    check(G, "crossing budget: command x data %d (limit %d); data x data %d (limit %d); high-speed x VREF %d (limit %d); same-layer %d (limit %d); wires through a "
          "resistor group %d (limit %d); overlap command x data %.2f mm (limit %.1f)"
          % (s["cmd_x_data"], bud["cmd_x_data"], s["data_x_data"], bud["data_x_data"], s["hs_x_vref"], bud["hs_x_vref"], s["same_layer"], bud["same_layer"],
             s["trans_through"], bud["trans_through"], s["overlap_mm_cmd_x_data"], bud["overlap_cmd_x_data_mm"]),
          s["cmd_x_data"] <= bud["cmd_x_data"] and s["data_x_data"] <= bud["data_x_data"] and s["hs_x_vref"] <= bud["hs_x_vref"] and s["same_layer"] <= bud["same_layer"]
          and s["trans_through"] <= bud["trans_through"] and s["overlap_mm_cmd_x_data"] <= bud["overlap_cmd_x_data_mm"] + 1e-9)
    # the command crossings by data wires, one by one
    data_cls = ("WR_DQ", "RD_DQ", "WCK", "RDQS")
    cx = [c for c in res["crossings"] if "CMD" in (c["a"]["class"], c["b"]["class"]) and (c["a"]["class"] in data_cls or c["b"]["class"] in data_cls)]
    u1b = spec["floorplan"]["U1"]["body"]
    rows, inside, solid = [], True, True
    for c in sorted(cx, key=lambda c: (c["y"], c["x"])):
        cm, dt = (c["a"], c["b"]) if c["a"]["class"] == "CMD" else (c["b"], c["a"])
        inside &= u1b["x"][0] <= c["x"] <= u1b["x"][1] and u1b["y"][0] <= c["y"] <= u1b["y"][1]
        solid &= bool(c.get("solid_gnd_between", True))
        rows.append((cm["net"], dt["net"], dt["layer"], (c["x"], c["y"]), c.get("angle_deg")))
    want = sorted((q["cmd"], q["by"], q["layer"], (round(q["at"][0], 2), round(q["at"][1], 2))) for q in D["command_crossings"])
    have = sorted((r_[0], r_[1], r_[2], (round(r_[3][0], 2), round(r_[3][1], 2))) for r_ in rows)
    check(G, "the %d crossings of the command bundle by data wires are the seven the spec lists, every one inside the land field of U1 (x %.3f .. %.3f, y %.1f .. "
          "%.1f), where the ball it serves is enclosed by the command traces" % (len(rows), u1b["x"][0], u1b["x"][1], u1b["y"][0], u1b["y"][1]),
          len(rows) == 7 and inside and solid and have == want, "have %s" % have)
    for r_ in rows:
        why = next((q["why"] for q in D["command_crossings"] if q["cmd"] == r_[0] and q["by"] == r_[1]), "")
        print("          %-4s (In3) x %-6s (%s) at (%.3f, %.2f)%s   %s" % (r_[0], r_[1], r_[2], r_[3][0], r_[3][1], " %g deg" % r_[4] if r_[4] is not None else "", why))
    # ---- the power stage: crossings of a high-speed wire by a supply, slow or GND-link track
    PWB = spec["floorplan"]["power"].get("build")
    if PWB and any(c_["a"]["net"] == "V1P2" or c_["b"]["net"] == "V1P2" for c_ in res["crossings"]):
        pb = spec["floorplan"]["power"]["crossing_budget"]
        check(G, "power stage, crossing budget (tracks; pours are planes): high-speed x supply %d (limit %d), command x supply %d (limit %d), high-speed x slow %d "
              "(limit %d), high-speed x GND link %d (limit %d), command x slow %d (limit %d), command x GND link %d (limit %d), without solid GND on every plane "
              "between %d (limit %d); overlap command %.2f mm (limit %.1f), data x other %.2f mm (limit %.1f); wires through a resistor group %d"
              % (s["hs_x_pwr"], pb["hs_x_pwr"], s["cmd_x_pwr"], pb["cmd_x_pwr"], s["hs_x_slow"], pb["hs_x_slow"], s["hs_x_gnd"], pb["hs_x_gnd"], s["cmd_x_slow"],
                 pb["cmd_x_slow"], s["cmd_x_gnd"], pb["cmd_x_gnd"], s["crossings_without_solid_gnd_between"], pb["without_solid_gnd"], s["overlap_mm_cmd"],
                 pb["overlap_cmd_mm"], s["overlap_mm_hs_other"], pb["overlap_hs_other_mm"], s["trans_through"]),
              s["hs_x_pwr"] <= pb["hs_x_pwr"] and s["cmd_x_pwr"] <= pb["cmd_x_pwr"] and s["hs_x_slow"] <= pb["hs_x_slow"] and s["hs_x_gnd"] <= pb["hs_x_gnd"]
              and s["cmd_x_slow"] <= pb["cmd_x_slow"] and s["cmd_x_gnd"] <= pb["cmd_x_gnd"] and s["crossings_without_solid_gnd_between"] <= pb["without_solid_gnd"]
              and s["overlap_mm_cmd"] <= pb["overlap_cmd_mm"] and s["overlap_mm_hs_other"] <= pb["overlap_hs_other_mm"] and s["trans_through"] == 0)
        hs_c = ("CMD", "WR_DQ", "RD_DQ", "WCK", "RDQS")
        stage_nets = {w["net"] for w in PWB["wires"]} - {"GND", "VDD2H"}
        got = collections.defaultdict(set)
        rows_ = []
        for c_ in res["crossings"]:
            for o_, h_ in ((c_["a"], c_["b"]), (c_["b"], c_["a"])):
                if h_["class"] in hs_c and (o_["net"] in stage_nets or (o_["net"] in ("GND", "VDD2H") and o_["layer"] == "B.Cu" and o_["class"] in ("PWR", "GND"))) \
                        and o_["class"] not in hs_c:
                    got["%s %s" % (o_["net"], o_["layer"])].add(h_["net"])
                    rows_.append((o_["net"], o_["layer"], h_["net"], h_["layer"], c_["x"], c_["y"], c_.get("angle_deg"), c_.get("solid_gnd_between"),
                                  "/".join("%s %s" % (p_["layer"][:3], p_["status"]) for p_ in c_.get("planes", []))))
        exp = {k: set(v["crosses"]) for k, v in PWB["crossings_expected"].items()}
        check(G, "power stage: every crossing of a high-speed wire by a wire of the stage is one the spec lists with its reason (%s = %d); RESET_n crosses none"
              % ("; ".join("%s x %d" % (k, len(v)) for k, v in exp.items()), sum(len(v) for v in exp.values())), dict(got) == exp,
              "board %s" % {k: sorted(v) for k, v in got.items()})
        for r_ in sorted(rows_):
            print("          %-5s (%s) x %-6s (%s) at (%.3f, %.3f) %s deg, planes between: %s%s" % (r_[0], r_[1][:-3], r_[2], r_[3][:-3], r_[4], r_[5], r_[6], r_[8],
                                                                                              "" if r_[7] else "  <- not solid on every plane"))
        for k, v in PWB["crossings_expected"].items():
            print("          why %s: %s" % (k, v["why"]))
    groups = {g["name"]: g for g in res["groups"]}
    spec_groups = {g["name"] for g in D["resistor_groups"]}
    thr = [t for t in res["transversals"] if t["tier"] in ("foreign-through", "own-through", "common-threaded")]
    check(G, "resistor groups of the tool = the groups of the spec (%s: the DQ columns and the WCK columns are two groups) plus the command field; no wire of any "
          "net runs through a group across its columns; the %d jogs off a column axis are the two CK lead-outs of the command field and the 45-degree run of WCK_P "
          "into its node" % (", ".join("%s y %.2f .. %.2f" % (n, groups[n]["bbox"][1], groups[n]["bbox"][3]) for n in sorted(spec_groups & set(groups))), s["trans_own_jog"]),
          spec_groups <= set(groups) and not thr and s["trans_own_jog"] <= 3, str([(t["group"], t["net"], t["tier"]) for t in thr][:6]))
    shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------------------------------------------ 9 power, slow nets, pours
HOP_DOCUMENTED = {("DQ%d" % i, "In3.Cu") for i in range(8)}     # the read branch: node via - In3 - read via (DESIGN.md section 14)
IN3_NETS = {"CK_P", "CK_N", "CS0"} | {"CA%d" % i for i in range(7)} | {"DQ%d" % i for i in range(8)} | {"RDQS_P", "RDQS_N"}
# V1P2: fingers -> In2 island -> strip -> arm -> U2 / U4.  15 mV: at the worst corner (host rail 1.14 V, 0.52 A, pass element 68 mV) U2 still
# delivers 1.14 - 0.015 - 0.068 = 1.057 V, 47 mV above the JEDEC minimum of VDD2H (DESIGN.md section 4)
RAIL_BUDGET_MV = {"V1P2": 15.0, "VDDQ": 5.0, "V3P3": 10.0, "VDD1": 5.0, "VDD2H": 5.0}
OUTER_ZONE_LIMIT_PCT = 5.0          # all zone copper on one outer layer, of the board area (upstream: GND zones 4 % / 3 %, all zones 11 %)
UPSTREAM_COVERAGE = {"F.Cu": {"total": 28.1, "GND zones": 4.0, "GND": 7.1}, "B.Cu": {"total": 24.9, "GND zones": 2.8, "GND": 7.0}}   # the upstream board,
#                    measured with outer_coverage() (check_pcb.py --coverage <its board file>); the first build of this stage: F.Cu 73.2 / 57.9 / 61.3, B.Cu 79.6 / 64.0 / 67.4


def island_sheet_drop(board, spec, h=0.05):
    """DC drop in the V1P2 island of In2, solved on the filled copper (finite differences, square cells of h mm): every VDD finger via injects
    its share of the rail current, the four vias of the F.Cu strip are held at 0 V.  Returns (mean mV weighted by current, [(x, mV)] per finger via,
    mW, mohm per square)."""
    import numpy as np
    import shapely
    from scipy.sparse import coo_matrix
    from scipy.sparse.linalg import spsolve
    fpl = spec["floorplan"]
    PW = fpl["power"]
    t = next(l["thickness_mm"] for l in spec["stackup"]["layers"] if l.get("layer") == "In2.Cu")
    rs = 1.72e-5 / t                                         # ohm per square
    fill = gnd_fill(board, "In2.Cu", box(-1, -1, 80, 40), net="V1P2")
    x0, y0, x1, y1 = fill.bounds
    xs = np.arange(x0 + h / 2, x1, h)
    ys = np.arange(y0 + h / 2, y1, h)
    X, Y = np.meshgrid(xs, ys)
    inside = shapely.contains_xy(fill, X, Y)
    idx = -np.ones(inside.shape, dtype=int)
    idx[inside] = np.arange(inside.sum())
    n = int(inside.sum())
    i_tot = PW["v1p2_feed"]["sizing"]["currents_a"]["V1P2"]
    src = [(v["x"], v["y"], len(v["fingers"])) for v in fpl["finger_vias"] if v["net"] == "V1P2"]
    n_f = sum(q[2] for q in src)
    sink = [(v["x"], v["y"]) for v in PW["build"]["vias"] if v["net"] == "V1P2" and v["y"] < 6.0]
    r_via = 0.125
    fixed = np.zeros(n, dtype=bool)
    for sx, sy in sink:
        m = inside & ((X - sx) ** 2 + (Y - sy) ** 2 <= r_via ** 2)
        fixed[idx[m]] = True
    rhs = np.zeros(n)
    cells = []
    for sx, sy, k in src:
        m = inside & ((X - sx) ** 2 + (Y - sy) ** 2 <= r_via ** 2)
        ids = idx[m]
        cells.append(ids)
        rhs[ids] += i_tot * k / n_f / max(1, len(ids))
    rows, cols, vals = [], [], []
    g = 1.0 / rs
    diag = np.zeros(n)
    for a, b in ((idx[:, :-1], idx[:, 1:]), (idx[:-1, :], idx[1:, :])):
        m = (a >= 0) & (b >= 0)
        ia, ib = a[m], b[m]
        rows += [ia, ib]
        cols += [ib, ia]
        vals += [-g * np.ones(len(ia)), -g * np.ones(len(ia))]
        np.add.at(diag, ia, g)
        np.add.at(diag, ib, g)
    rows.append(np.arange(n))
    cols.append(np.arange(n))
    vals.append(diag)
    A = coo_matrix((np.concatenate(vals), (np.concatenate(rows), np.concatenate(cols))), shape=(n, n)).tocsr()
    free = ~fixed
    v = np.zeros(n)
    v[free] = spsolve(A[free][:, free].tocsc(), rhs[free])
    per = [(sx, 1e3 * float(v[ids].mean()) if len(ids) else float("nan"), k) for (sx, sy, k), ids in zip(src, cells)]
    mean = sum(mv * k for _, mv, k in per) / n_f
    mw = 1e3 * float((rhs * v).sum())
    return mean, per, mw, rs * 1e3, len(sink), n


def outer_coverage(board, card_frame=True):
    """Copper of F.Cu / B.Cu per net as a share of the board area: {layer: {"total": %, "zones": {net: %}, "nets": {net: %}}}.
    Also run on the upstream board (python3 check_pcb.py --coverage <board>) for the figures of UPSTREAM_COVERAGE."""
    outline = unary_union(C.board_polygon(board))
    out = {}
    for lay in ("F.Cu", "B.Cu"):
        lid = C.CU[lay]
        per = collections.defaultdict(list)
        for t in board.GetTracks():
            if t.Type() == pcbnew.PCB_VIA_T:
                per[t.GetNetname()].append(Point(C.k2c(t.GetPosition())).buffer(C.MM(t.GetWidth(lid)) / 2))
            elif t.GetLayer() == lid:
                per[t.GetNetname()].append(LineString(track_pts(t, 0.05)).buffer(C.MM(t.GetWidth()) / 2))
        for fp in board.GetFootprints():
            for p in fp.Pads():
                if p.IsOnLayer(lid):
                    ps = pcbnew.SHAPE_POLY_SET()
                    p.TransformShapeToPolygon(ps, lid, 0, 5000, pcbnew.ERROR_INSIDE)       # the pad's own shape (a ball is a circle, not its box)
                    per[p.GetNetname() or "(no net)"] += poly_from_set(ps)
        zones = collections.defaultdict(float)
        for z in board.Zones():
            if z.GetIsRuleArea() or not z.IsOnLayer(lid):
                continue
            for q in poly_from_set(z.GetFilledPolysList(lid)):
                per[z.GetNetname()].append(q)
                zones[z.GetNetname()] += q.area
        A = outline.area
        nets = {n: 100 * unary_union(g).intersection(outline).area / A for n, g in per.items()}
        out[lay] = {"total": 100 * unary_union([x for g in per.values() for x in g]).intersection(outline).area / A,
                    "zones": {n: 100 * a / A for n, a in zones.items()}, "nets": nets}
    return out


def gnd_pad_audit(board, spec):
    """Every GND pad of every part: how it reaches the planes without any pour.  Copper graph per outer layer (GND tracks, GND pads, GND vias; two
    items are joined when their copper touches).  Returns (rows, idle vias): rows = (ref.pad, layer, how, via xy, mm) where how is 'via in the pad'
    (a GND via whose centre lies in the pad), 'via on the pad' (its land overlaps the pad), 'track' (tracks and pads of the net on the pad's own
    layer lead to a GND via; mm = straight distance pad centre -> nearest such via) or 'NONE'; idle vias = GND vias whose copper touches no GND pad
    on either outer layer, directly or through tracks."""
    vias = [(Point(C.k2c(v.GetPosition())), C.MM(v.GetWidth(pcbnew.F_Cu)) / 2) for v in board.GetTracks()
            if v.Type() == pcbnew.PCB_VIA_T and v.GetNetname() == "GND"]
    rows = {}
    via_has_pad = [False] * len(vias)
    for lay in ("F.Cu", "B.Cu"):
        lid = C.CU[lay]
        trs = [LineString(track_pts(t, 0.05)).buffer(C.MM(t.GetWidth()) / 2) for t in board.GetTracks()
               if t.Type() != pcbnew.PCB_VIA_T and t.GetNetname() == "GND" and t.GetLayer() == lid]
        pads = [(fp.GetReference(), p.GetNumber(), _pad_box(p), Point(C.k2c(p.GetPosition()))) for fp in board.GetFootprints() for p in fp.Pads()
                if p.GetNetname() == "GND" and p.GetNumber() and p.IsOnLayer(lid)]
        geoms = trs + [q[2] for q in pads] + [pt.buffer(r) for pt, r in vias]
        nt, npd = len(trs), len(pads)
        par = list(range(len(geoms)))

        def find(i):
            while par[i] != i:
                par[i] = par[par[i]]
                i = par[i]
            return i
        tree = shapely.STRtree(geoms)
        for i, g in enumerate(geoms):
            for j in tree.query(g, predicate="intersects"):
                if j > i:
                    par[find(i)] = find(int(j))
        comp_vias = collections.defaultdict(list)
        comp_pad = set()
        for k in range(npd):
            comp_pad.add(find(nt + k))
        for k in range(len(vias)):
            c_ = find(nt + npd + k)
            comp_vias[c_].append(k)
            if c_ in comp_pad:
                via_has_pad[k] = True
        for k, (ref, num, bx, ctr) in enumerate(pads):
            name = "%s.%s" % (ref, num)
            inpad = [pt for pt, r in vias if bx.contains(pt)]
            onpad = [pt for pt, r in vias if bx.intersects(pt.buffer(r - 0.05))]
            reach = [vias[i][0] for i in comp_vias.get(find(nt + k), [])]
            if inpad:
                row = (name, lay, "via in the pad", (inpad[0].x, inpad[0].y), 0.0)
            elif onpad:
                pt = min(onpad, key=ctr.distance)
                row = (name, lay, "via on the pad", (pt.x, pt.y), ctr.distance(pt))
            elif reach:
                pt = min(reach, key=ctr.distance)
                row = (name, lay, "track", (pt.x, pt.y), ctr.distance(pt))
            else:
                row = (name, lay, "NONE", None, None)
            rank = {"via in the pad": 0, "via on the pad": 1, "track": 2, "NONE": 3}
            if name not in rows or rank[row[2]] < rank[rows[name][2]]:        # a pad drawn as several shapes (exposed pads) counts once, by its best shape
                rows[name] = row
    idle = [(round(vias[k][0].x, 3), round(vias[k][0].y, 3)) for k in range(len(vias)) if not via_has_pad[k]]
    return list(rows.values()), idle


def _pad_box(p):
    bb = p.GetBoundingBox()
    lo = C.k2c(pcbnew.VECTOR2I(bb.GetLeft(), bb.GetBottom()))
    hi = C.k2c(pcbnew.VECTOR2I(bb.GetRight(), bb.GetTop()))
    return box(lo[0], lo[1], hi[0], hi[1])


def layer_hops(board, spec):
    """Every stretch of track that touches no pad and lies between two (or more) vias, on any net of the board.
    A pad = an SMD pad on that layer, or a via-in-pad of U1 (the ball itself) on every layer.  Returns [(net, layer, mm, n vias)]."""
    from shapely.geometry import LineString
    vip = {tuple(v["xy"]) for v in spec["floorplan"]["U1"]["via_in_pad"]["balls"].values()}
    by = collections.defaultdict(lambda: {"tr": collections.defaultdict(list), "via": [], "pad": collections.defaultdict(list)})
    for t in board.GetTracks():
        n = t.GetNetname()
        if t.Type() == pcbnew.PCB_VIA_T:
            xy = C.k2c(t.GetPosition())
            if (round(xy[0], 3), round(xy[1], 3)) in vip:
                for l in C.CU_ORDER:
                    by[n]["pad"][l].append(Point(xy).buffer(0.2))
            else:
                by[n]["via"].append(Point(xy).buffer(C.MM(t.GetWidth(pcbnew.F_Cu)) / 2))
        else:
            by[n]["tr"][board.GetLayerName(t.GetLayer())].append((LineString(track_pts(t, 0.05)).buffer(C.MM(t.GetWidth()) / 2), track_len(t)))
    for fp in board.GetFootprints():
        for p in fp.Pads():
            if p.GetNetname():
                for l in ("F.Cu", "B.Cu"):
                    if p.IsOnLayer(C.CU[l]):
                        by[p.GetNetname()]["pad"][l].append(_pad_box(p))
    out = []
    for net, q in by.items():
        for lay, trs in q["tr"].items():
            par = list(range(len(trs)))

            def find(i):
                while par[i] != i:
                    par[i] = par[par[i]]
                    i = par[i]
                return i
            for i in range(len(trs)):
                for j in range(i + 1, len(trs)):
                    if trs[i][0].intersects(trs[j][0]):
                        par[find(i)] = find(j)
            comps = collections.defaultdict(list)
            for i in range(len(trs)):
                comps[find(i)].append(i)
            for idx in comps.values():
                shp = unary_union([trs[i][0] for i in idx])
                if any(shp.intersects(pd) for pd in q["pad"][lay]):
                    continue
                nv = sum(1 for v in q["via"] if shp.intersects(v))
                if nv >= 2:
                    out.append((net, lay, round(sum(trs[i][1] for i in idx), 2), nv))
    return sorted(out)


def gate_power(board, spec, drc):
    G = "9 power"
    from shapely.geometry import LineString
    fpl = spec["floorplan"]
    PW = fpl["power"]
    B = PW.get("build")
    st_all = [t for t in board.GetTracks() if C.stage_of(t) == "s50_power"]
    if not B or not st_all:
        print("        [9 power] stage s50_power has not run: gate skipped")
        return
    lname = lambda t: board.GetLayerName(t.GetLayer())      # noqa: E731
    st_tr = [t for t in st_all if t.Type() != pcbnew.PCB_VIA_T]
    st_via = [t for t in st_all if t.Type() == pcbnew.PCB_VIA_T]
    all_tr = [t for t in board.GetTracks() if t.Type() != pcbnew.PCB_VIA_T]
    all_via = [t for t in board.GetTracks() if t.Type() == pcbnew.PCB_VIA_T]
    hs_cls = set(PW["rules"]["high_speed_classes"])
    cls = {n: C.netclass_of(n) for n in C.nets()}
    rho_s = 1.72e-5 / next(l["thickness_mm"] for l in spec["stackup"]["layers"] if l.get("layer") == "F.Cu") * 1e3
    t_cu = next(l["thickness_mm"] for l in spec["stackup"]["layers"] if l.get("layer") == "F.Cu")

    # ---- 1. the plan is on the board
    want = collections.Counter()
    for w in B["wires"]:
        for a, b in zip(w["path"], w["path"][1:]):
            want[(w["net"], w["layer"], round(w["width"], 3))] += 1
    have = collections.Counter((t.GetNetname(), lname(t), round(C.MM(t.GetWidth()), 3)) for t in st_tr)
    vat = {(round(C.k2c(v.GetPosition())[0], 3), round(C.k2c(v.GetPosition())[1], 3)): v for v in st_via}
    vbad = [(v["net"], v["x"], v["y"]) for v in B["vias"] if (round(v["x"], 3), round(v["y"], 3)) not in vat
            or vat[(round(v["x"], 3), round(v["y"], 3))].GetNetname() != v["net"]
            or abs(C.MM(vat[(round(v["x"], 3), round(v["y"], 3))].GetWidth(pcbnew.F_Cu)) - v["diameter"]) > 1e-6]
    plan_pos = {(round(v["x"], 3), round(v["y"], 3)) for v in B["vias"]}
    extra = [k for k in vat if k not in plan_pos]           # a via of the stage that is not in the plan (a stitching via would be one)
    zones = {z.GetZoneName(): z for z in board.Zones() if not z.GetIsRuleArea() and C.stage_of(z) == "s50_power"}
    check(G, "the plan is on the board: %d centre lines of floorplan.power.build.wires = %d track items with the net, layer and width of the spec; its %d vias "
          "(0.45 / 0.20 supply and GND, 0.40 / 0.20 slow nets) and no other via of the stage (NO stitching via); %d power pours on B.Cu under U1 and no other zone of the stage"
          % (len(B["wires"]), len(st_tr), len(B["vias"]), len(B["pours"])),
          want == have and not vbad and not extra and set(zones) == {z["name"] for z in B["pours"]} and not PW["gnd_pours"],
          "tracks %s / %s; vias %s; extra %s; zones %s" % (list((want - have).items())[:3], list((have - want).items())[:3], vbad[:4], extra[:4], sorted(zones)))

    # ---- 2. connectivity: the whole board
    unc = collections.Counter()
    for u in (drc or {}).get("unconnected_items", []):
        for m in set(m_ for i in u["items"] for m_ in re.findall(r"\[([^\]]+)\]", i["description"])):
            unc[m] += 1
    nets_stage = sorted({t.GetNetname() for t in st_all})
    check(G, "connectivity: ZERO unconnected items on the whole board (KiCad DRC); the %d nets this stage routes (%s) are complete"
          % (len(nets_stage), ", ".join(nets_stage)), drc is not None and not unc, str(dict(unc)))

    # ---- 3. supply copper measured on the board (this stage matches no delay: the table is the DC budget of every rail)
    cur = PW["v1p2_feed"]["sizing"]["currents_a"]
    seg = collections.defaultdict(float)
    for t in st_tr:
        seg[(t.GetNetname(), lname(t), round(C.MM(t.GetWidth()), 3))] += track_len(t)
    print("        supply copper as built (measured: sum of the track lengths of the stage per net, layer and width; %.3f mohm per square at %.3f mm copper;"
          % (rho_s, t_cu))
    print("        current per piece: floorplan.power.build.dc_budget, worst case):")
    print("        rail    layer  width  length mm  squares   mohm       A     mV   A/mm2  +K (IPC-2221)  what")
    tot = collections.defaultdict(float)
    rows_ok = True
    for q in B["dc_budget"]:
        ln = seg.get((q["net"], q["layer"], round(q["width"], 3)), 0.0)
        w, i = q["width"], q["current_a"]
        r = rho_s * ln / w
        a_mil2 = (w / 0.0254) * (t_cu / 0.0254)
        rise = (i / (0.048 * a_mil2 ** 0.725)) ** (1 / 0.44)
        tot[q["net"]] += r * i
        rows_ok &= rise < 10.0 and ln > 0.3
        print("        %-6s  %-5s  %5.2f  %9.2f  %7.1f  %5.1f  %6.3f  %5.2f  %6.1f  %5.2f          %s" % (q["net"], q["layer"][:-3], w, ln, ln / w, r, i, r * i, i / (w * t_cu), rise, q["what"]))
    sz = PW["v1p2_feed"]["sizing"]
    isl_mean, isl_per, isl_mw, isl_rs, isl_sinks, isl_cells = island_sheet_drop(board, spec)
    isl_max = max(mv for _, mv, _ in isl_per)
    iv = PW["v1p2_feed"]["sizing"]["island_vias"]
    tot["V1P2"] += isl_max + iv["drop_mv"]
    print("        V1P2    In2    island (sheet solve on the filled copper, %d cells of 0.05 mm, %.3f mohm per square, %d strip vias at 0 V): mean %.2f mV, worst finger via %.2f mV, "
          "%.1f mW;  per finger via (x: mV): %s" % (isl_cells, isl_rs, isl_sinks, isl_mean, isl_max, isl_mw, ", ".join("%.1f: %.2f" % (x_, mv) for x_, mv, _ in isl_per)))
    print("        V1P2    via    the %d island vias of the strip: %.2f mohm each, %.2f mV" % (iv["count"], iv["each_mohm"], iv["drop_mv"]))
    stp = PW["v1p2_feed"]["strip"]
    l_strip = seg.get(("V1P2", "F.Cu", round(stp["width"], 3)), 0.0)
    l_arm = seg.get(("V1P2", "F.Cu", round(stp["arm_width"], 3)), 0.0)
    r_strip = rho_s * l_strip / stp["width"]
    u2_drop = isl_max + iv["drop_mv"] + sz["drop_fingers_to_u2_mv"]
    check(G, "supply copper against its budget (sum of the pieces at the full rail current, an upper bound): "
          + ", ".join("%s %.1f mV (limit %.0f)" % (n, tot[n], RAIL_BUDGET_MV[n]) for n in sorted(tot))
          + "; V1P2 INCLUDES the In2 island (worst finger via %.2f mV, mean %.2f mV, solved on the filled copper) and its four vias (%.2f mV): fingers to U2 IN %.1f mV worst, "
          "%.1f mV mean; VDD2H includes the link F6 - K6; no piece warmer than +10 K; the VDD2H island (In4) and the pours under U1 are planes"
          % (isl_max, isl_mean, iv["drop_mv"], u2_drop, u2_drop - isl_max + isl_mean),
          rows_ok and all(tot[n] <= RAIL_BUDGET_MV[n] for n in tot) and isl_sinks == 4 and abs(isl_max - sz["island"]["expected_worst_finger_mv"]) < 0.3
          and abs(isl_mean - sz["island"]["expected_mean_mv"]) < 0.3 and RAIL_BUDGET_MV["V1P2"] == sz["drop_fingers_to_u2_total_mv"]["limit_mv"], str(dict(tot)))
    v_cnt = collections.Counter(v.GetNetname() for v in st_via if (round(C.k2c(v.GetPosition())[0], 3), round(C.k2c(v.GetPosition())[1], 3)) in plan_pos)
    check(G, "1.2 V strip as built: %.2f mm long, %.2f mm wide (spec %.2f; never wider than 1.5), %.2f mohm, %.2f mV and %.1f mW at %.2f A, %.1f A/mm2; arm %.2f mm "
          "at %.2f mm; total V1P2 track copper on F.Cu %.1f mm2 (the rehearsed pour was 51 mm2 without the arm) and NO V1P2 zone on an outer layer; "
          "vias: %d from the In2 island (%.0f mA each), %d at the U4 tap, 1 at R504; 19 fingers at %.0f mA (limit 500); U2 output %d vias into the In4 island (%.0f mA each)"
          % (l_strip, stp["width"], stp["width"], r_strip, r_strip * cur["V1P2"], r_strip * cur["V1P2"] ** 2, cur["V1P2"], cur["V1P2"] / (stp["width"] * t_cu), l_arm,
             stp["arm_width"], sum(track_len(t) * C.MM(t.GetWidth()) for t in st_tr if t.GetNetname() == "V1P2" and lname(t) == "F.Cu"),
             sum(1 for v in B["vias"] if v["net"] == "V1P2" and v["y"] < 6.0), 1e3 * cur["V1P2"] / 4, sum(1 for v in B["vias"] if v["net"] == "V1P2" and "U4" in v["role"]),
             1e3 * cur["V1P2"] / 19, v_cnt["VDD2H"], 1e3 * cur["VDD2H"] / max(1, v_cnt["VDD2H"])),
          abs(l_strip - (stp["y"][1] - stp["y"][0])) < 0.01 and stp["width"] <= 1.5 + 1e-9 and abs(r_strip * cur["V1P2"] - sz["parts"]["strip (corridor)"]["mv"]) < 0.25
          and not any(z.GetNetname() == "V1P2" and (z.IsOnLayer(pcbnew.F_Cu) or z.IsOnLayer(pcbnew.B_Cu)) for z in board.Zones() if not z.GetIsRuleArea())
          and sum(1 for v in B["vias"] if v["net"] == "V1P2" and v["y"] < 6.0) == 4 and v_cnt["VDD2H"] >= 4)

    # ---- 4. angles
    n, worst, bad, hist = vertex_angles(board, st_tr)
    odd = [(t.GetNetname(), C.k2c(t.GetStart())) for t in st_tr if t.Type() == pcbnew.PCB_ARC_T]
    check(G, "angles: %d vertices of the stage, none sharper than 90 degrees (smallest %.0f; histogram %s); no arc; every segment at 0 / 45 / 90 / 135 degrees (gate 1)"
          % (n, worst, hist), not bad and not odd, str(bad[:6]))

    # ---- 5. layer purity
    in3_bad = sorted({t.GetNetname() for t in all_tr if lname(t) == "In3.Cu"} - IN3_NETS)
    plane_tr = sorted({(t.GetNetname(), lname(t)) for t in all_tr if lname(t) in ("In1.Cu", "In2.Cu", "In4.Cu")})
    lay_nets = {l: sorted({t.GetNetname() for t in st_tr if lname(t) == l}) for l in ("F.Cu", "B.Cu")}
    stage_lay = sorted({lname(t) for t in st_tr})
    in3_land = sorted({v.GetNetname() for v in all_via if v.FlashLayer(pcbnew.In3_Cu)} - IN3_NETS)
    check(G, "layer purity: In3 carries only the command group, the eight read branches and the RDQS pair (%d nets; others: %s) - no RESET_n, no supply; "
          "no via of another net has a land on In3 (%s); In1 / In2 / In4 carry no track; the stage is on F.Cu and B.Cu only"
          % (len({t.GetNetname() for t in all_tr if lname(t) == "In3.Cu"}), in3_bad or "none", in3_land or "none"),
          not in3_bad and not in3_land and not plane_tr and stage_lay == ["B.Cu", "F.Cu"], "%s %s %s %s" % (in3_bad, in3_land, plane_tr, stage_lay))
    for l in ("F.Cu", "B.Cu"):
        print("        stage nets on %s: %s" % (l, ", ".join("%s %.1f" % (nn, sum(track_len(t) for t in st_tr if lname(t) == l and t.GetNetname() == nn)) for nn in lay_nets[l])))

    # ---- 6. no layer hop anywhere on the board
    hops = layer_hops(board, spec)
    undoc = [h for h in hops if (h[0], h[1]) not in HOP_DOCUMENTED]
    doc = [h for h in hops if (h[0], h[1]) in HOP_DOCUMENTED]
    check(G, "NO LAYER HOP: on no net of the board does a stretch of track that touches no pad lie between two vias, except the documented topology of the "
          "read branches (DQ0-7: node via - In3 - read via; %d found, %s mm). A via-in-pad of U1 is the ball itself. Slow and supply nets: none "
          "(RESET_n has ONE via, at its U1 end)" % (len(doc), " / ".join("%.1f" % h[2] for h in doc)),
          not undoc and {(h[0], h[1]) for h in doc} == HOP_DOCUMENTED, str(undoc))

    # ---- 7. RESET_n
    rs = [t for t in all_tr if t.GetNetname() == "RESET_N"]
    rs_v = [v for v in all_via if v.GetNetname() == "RESET_N"]
    l_f = sum(track_len(t) for t in rs if lname(t) == "F.Cu")
    l_b = sum(track_len(t) for t in rs if lname(t) == "B.Cu")
    rsf = [t for t in all_tr if t.GetNetname() == "RESET_N_F"]
    cu = lambda t: LineString(track_pts(t, 0.05)).buffer(C.MM(t.GetWidth()) / 2)       # noqa: E731
    rs_b = unary_union([cu(t) for t in rs + rsf if lname(t) == "B.Cu"])
    rdqs_f = unary_union([cu(t) for t in all_tr if t.GetNetname() in ("RDQS_P", "RDQS_N") and lname(t) == "F.Cu"])
    rd_out = unary_union([cu(t) for t in all_tr if re.fullmatch(r"DQ\d", t.GetNetname()) and lname(t) == "B.Cu"])
    vref_b = unary_union([cu(t) for t in all_tr if cls.get(t.GetNetname()) == "VREF" and lname(t) == "B.Cu"]
                         + [Point(C.k2c(v.GetPosition())).buffer(C.MM(v.GetWidth(pcbnew.F_Cu)) / 2) for v in all_via if cls.get(v.GetNetname()) == "VREF"])
    isl = PW["vdd2h_island"]
    d_isl = isl["y"][0] - max((C.k2c(t.GetStart())[1] for t in rs if lname(t) == "B.Cu" and C.k2c(t.GetStart())[0] < isl["x"][1]), default=0)
    fin = spec["floorplan"]["data"]["pin_map"]["reset_n"]
    fr = next(f for f in spec["fingers"] if f["finger"] == fin)
    side = {r: ("B" if C.footprint(board, r).IsFlipped() else "F") for r in ("R402", "R403")}
    in4_gnd = gnd_fill(board, "In4.Cu", box(-1, -1, 80, 40))
    rs_c = rs_b.difference(unary_union([Point(C.k2c(v.GetPosition())).buffer(0.6) for v in rs_v]))
    cover = (rs_c.intersection(in4_gnd).area / rs_c.area) if rs_c.area else 0
    check(G, "RESET_n: ball H1 - %.2f mm of F.Cu - ONE via - %.2f mm of B.Cu - R403 / R402 on the back - finger %d (%s, %s, bank %s; back side): no copper on In3, "
          "no via at the finger end; in plan it keeps %.2f mm from the RDQS pair (>= 0.80), %.2f mm from the read finger tracks on its layer (>= 0.80) and %.2f mm "
          "from every VREF track and via on its layer (>= 0.90); it runs %.2f mm south of the VDD2H island of In4, over GND on In4 for %.1f %% of its B.Cu copper (its own via excepted), "
          "and passes under the F.Cu 1.2 V strip with In1, In2 and In4 between"
          % (l_f, l_b, fin, fr["fpga_ball"], fr["bytegroup"], fr["bank"], rs_b.distance(rdqs_f), rs_b.distance(rd_out), rs_b.distance(vref_b), d_isl, 100 * cover),
          len(rs_v) == 1 and not any(lname(t) == "In3.Cu" for t in rs + rsf) and l_f < 2.5 and side == {"R402": "B", "R403": "B"} and fr["side"] == "B"
          and rs_b.distance(rdqs_f) >= 0.80 and rs_b.distance(rd_out) >= 0.80 and rs_b.distance(vref_b) >= 0.90 and cover > 0.999 and d_isl >= 0.3)

    # ---- 8. outer layers: no ground flood; the power pours under U1
    cmd_cls = {"CMD_SE", "CMD_CK"}
    pour_rows, ok_pour = [], True
    gnd_zone = []
    d_cmd_pour = 9.9
    for lay in ("F.Cu", "B.Cu"):
        lid = C.CU[lay]
        hs_tr = [cu(t) for t in all_tr if lname(t) == lay and cls.get(t.GetNetname()) in hs_cls]
        hs_u = unary_union(hs_tr) if hs_tr else None
        hs_pad = unary_union([Point(C.k2c(v.GetPosition())).buffer(C.MM(v.GetWidth(lid)) / 2) for v in all_via if cls.get(v.GetNetname()) in hs_cls]
                             + [_pad_box(p) for fp in board.GetFootprints() for p in fp.Pads() if p.IsOnLayer(lid) and cls.get(p.GetNetname()) in hs_cls])
        cmd_via = unary_union([Point(C.k2c(v.GetPosition())).buffer(C.MM(v.GetWidth(lid)) / 2) for v in all_via if cls.get(v.GetNetname()) in cmd_cls])
        for z in board.Zones():
            if z.GetIsRuleArea() or not z.IsOnLayer(lid):
                continue
            if z.GetNetname() == "GND":
                gnd_zone.append((z.GetZoneName(), lay))
            polys = poly_from_set(z.GetFilledPolysList(lid))
            area = sum(p.area for p in polys)
            dd = min((p.distance(hs_u) for p in polys), default=9.9) if hs_u is not None else 9.9
            dp = min((p.distance(hs_pad) for p in polys), default=9.9)
            dc = min((p.distance(cmd_via) for p in polys), default=9.9)
            d_cmd_pour = min(d_cmd_pour, dc)
            # every filled outline holds a via or pad of its net (no floating island); narrowest place of the fill (opening by half the minimum width)
            anch = [Point(C.k2c(v.GetPosition())).buffer(0.2) for v in all_via if v.GetNetname() == z.GetNetname()]
            anch += [_pad_box(p) for fp in board.GetFootprints() for p in fp.Pads() if p.GetNetname() == z.GetNetname() and p.IsOnLayer(lid)]
            float_ = [round(p.area, 3) for p in polys if not any(p.buffer(0.02).intersects(a) for a in anch)]
            mw = PW["rules"]["power_pour_min_width_mm"]
            thin = sum(q.difference(q.buffer(-(mw / 2 - 0.01)).buffer(mw / 2 - 0.01 + 1e-3)).area for q in (p.buffer(0) for p in polys))   # buffer(0): KiCad joins holes by slits
            pour_rows.append((z.GetZoneName(), lay, z.GetNetname(), len(polys), round(area, 1), round(dd, 3), round(dp, 3), round(dc, 3), float_, round(thin, 3)))
            ok_pour &= len(polys) == 1 and not float_ and dd >= PW["rules"]["power_pour_to_high_speed_track_mm"] - 2e-3 and dp >= 0.15 - 2e-3 \
                and dc >= PW["rules"]["power_pour_to_command_via_mm"] - 2e-3 and thin < 0.02
    print("        outer-layer pours as filled: name, layer, net, outlines, mm2, nearest high-speed track / high-speed pad or via / command via on the layer mm, floating outlines, "
          "mm2 narrower than the minimum width")
    for r_ in pour_rows:
        print("          %-46s %-5s %-6s %3d  %7.1f  %6.3f %6.3f %6.3f  %s  %s" % r_)
    lowest = {}
    for z in board.Zones():
        if z.GetIsRuleArea() or C.stage_of(z) != "s50_power":
            continue
        for lid in z.GetLayerSet().Seq():
            for p in poly_from_set(z.GetFilledPolysList(lid)):
                lowest[board.GetLayerName(lid)] = min(lowest.get(board.GetLayerName(lid), 99), p.bounds[1])
    d_pwr = min([r_[5] for r_ in pour_rows] or [9.9])
    f_zones = [r_ for r_ in pour_rows if r_[1] == "F.Cu"]
    check(G, "outer layers carry NO ground pour (GND zones on F.Cu / B.Cu: %s) and F.Cu carries no zone at all; the %d power pours under U1 (B.Cu) are filled, each ONE "
          "outline that holds vias of its net, nowhere narrower than %.2f mm; nearest high-speed track on the layer %.3f mm (rule %.2f), nearest high-speed pad or via %.3f mm "
          "(rule 0.15), nearest via of the command bus %.3f mm (rule %.2f); lowest pour copper y = %.2f"
          % (gnd_zone or "none", len(pour_rows), PW["rules"]["power_pour_min_width_mm"], d_pwr, PW["rules"]["power_pour_to_high_speed_track_mm"],
             min([r_[6] for r_ in pour_rows] or [9.9]), d_cmd_pour, PW["rules"]["power_pour_to_command_via_mm"], min(lowest.values())),
          ok_pour and not gnd_zone and not f_zones and len(pour_rows) == len(B["pours"]) and min(lowest.values()) >= 2.85 - 2e-3, str([r_ for r_ in pour_rows if r_[8] or r_[3] != 1 or r_[9] >= 0.02]))

    # ---- 8b. the B.Cu ends of the ten command via-in-pads: what stands within 0.30 mm of each
    u1p = {p["ref"]: p for p in spec["parts"]}["U1"]["pins"]
    cmd_balls = sorted((n, b_) for b_, n in u1p.items() if n and cls.get(n) in cmd_cls)
    b_lid = C.CU["B.Cu"]
    pour_b = [(z.GetZoneName(), z.GetNetname(), unary_union(poly_from_set(z.GetFilledPolysList(b_lid)))) for z in board.Zones()
              if not z.GetIsRuleArea() and z.IsOnLayer(b_lid)]
    tr_b = [(t.GetNetname(), C.stage_of(t), cu(t), C.MM(t.GetWidth())) for t in all_tr if lname(t) == "B.Cu"]
    pad_b = [(fp.GetReference() + "." + p.GetNumber(), p.GetNetname(), _pad_box(p)) for fp in board.GetFootprints() for p in fp.Pads()
             if p.IsOnLayer(b_lid) and fp.GetReference() != "U1"]
    env_rows, env_pour, env_track = [], [], []
    print("        B.Cu end of each command via-in-pad (pad 0.40 mm): copper of another net within 0.30 mm of the pad edge (other via-in-pads at the 0.8 / 0.7 mm pitch stand 0.30 mm or more away)")
    for n, b_ in cmd_balls:
        xy = C.u1_ball_xy(b_)
        ring_ = Point(xy).buffer(0.2)
        near = []
        for zn, znet, g in pour_b:
            d_ = g.distance(ring_)
            if d_ < 0.30 - 1e-6:
                near.append("pour %s %.3f" % (znet, d_))
                env_pour.append((n, znet, round(d_, 3)))
        for tn, stg, g, w_ in tr_b:
            d_ = g.distance(ring_)
            if tn != n and d_ < 0.30 - 1e-6:
                near.append("track %s (%.2f mm wide, %s) %.3f" % (tn, w_, stg, d_))
                if stg == "s50_power":
                    env_track.append((n, tn, round(w_, 2), round(d_, 3), round(g.intersection(ring_.buffer(0.30)).area / w_, 2)))
        for pn, pnet, g in pad_b:
            d_ = g.distance(ring_)
            if pnet != n and d_ < 0.30 - 1e-6:
                near.append("land %s (%s) %.3f" % (pn, pnet, d_))
        env_rows.append((n, b_, near))
        print("          %-5s %-3s (%.3f, %.3f): %s" % (n, b_, xy[0], xy[1], "; ".join(near) or "nothing"))
    doc_neck = [e for e in env_track if e[:3] == ("CA1", "VDD2H", 0.12)]
    neck_d = min([e[3] for e in doc_neck] or [-1])
    neck_l = sum(e[4] for e in doc_neck)
    check(G, "command stub ends on B.Cu (the ten via-in-pads of rows G / H / J): NO pour within 0.30 mm of any of them (%s); tracks of this stage within 0.30 mm: %s - the "
          "one documented exception: the 0.12 mm neck of the VDD2H link F6 - K6 passes the pad of CA1 (H5) at %.3f mm over about %.2f mm (>= 0.135; no other channel "
          "of row H is free of a command via); what else stands there are the lands of the decoupling capacitors of the earlier stage (listed above)"
          % (env_pour or "none", sorted({e[:3] for e in env_track}) or "none", neck_d, neck_l),
          not env_pour and doc_neck and len(doc_neck) == len(env_track) and neck_d >= 0.135, str(env_track))

    # ---- 8c. every GND pad reaches the planes through a via of its own, without any pour
    rows_g, idle_v = gnd_pad_audit(board, spec)
    by_how = collections.Counter()
    worst_tr = ("", 0.0)
    grp = collections.defaultdict(list)
    for name, lay, how, vxy, mm in rows_g:
        ref = name.split(".")[0]
        kind = "U1 balls" if ref == "U1" else "fingers (J1)" if ref == "J1" else "exposed pads and pins of U2 / U3 / U4 / U5" if ref in ("U2", "U3", "U4", "U5") else "other parts"
        by_how[(kind, how)] += 1
        grp[(kind, how)].append(name)
        if how == "track" and mm > worst_tr[1]:
            worst_tr = (name, mm)
    print("        GND pads and how each reaches In1 / In2 / In4 (no pour involved): %d pads" % len(rows_g))
    for (kind, how), cnt in sorted(by_how.items()):
        names = grp[(kind, how)]
        print("          %-44s %-15s %3d   %s" % (kind, how, cnt, ", ".join(names) if kind not in ("U1 balls", "fingers (J1)") else "(%s .. %s)" % (names[0], names[-1])))
    print("        the GND pads that hang on a track, with the via they reach (straight-line distance pad centre -> via centre, mm):")
    line = ["%s %.2f" % (name, mm) for name, lay, how, vxy, mm in rows_g if how == "track" and not name.startswith("J1.")]
    for i in range(0, len(line), 12):
        print("          " + ", ".join(line[i:i + 12]))
    none = [r_[0] for r_ in rows_g if r_[2] == "NONE"]
    gnd_v = [v for v in all_via if v.GetNetname() == "GND"]
    check(G, "GND without a flood: each of the %d GND pads of the board reaches the planes through a GND via of its own net - %d through a via in the pad, %d through a via "
          "whose land sits on the pad, %d through a track on their own layer (longest: %s, %.2f mm to its via); none relies on a pour (without a via: %s); %d GND vias on the board"
          % (len(rows_g), sum(c for (k, h), c in by_how.items() if h == "via in the pad"), sum(c for (k, h), c in by_how.items() if h == "via on the pad"),
             sum(c for (k, h), c in by_how.items() if h == "track"), worst_tr[0], worst_tr[1], none or "none", len(gnd_v)),
          not none and worst_tr[1] <= 3.0, str(none))

    # ---- 8d. no GND via without a job, no isolated copper
    #      a GND via serves a GND pad (in it, on it, or at the end of a GND track) or stands within 2.0 mm of a signal via as its return via
    sig_v = [Point(C.k2c(v.GetPosition())) for v in all_via if cls.get(v.GetNetname()) in hs_cls or cls.get(v.GetNetname()) == "VREF"]
    idle = [q for q in idle_v if not any(Point(q).distance(g_) <= 2.0 for g_ in sig_v)]
    n_ret = len(idle_v) - len(idle)
    iso = []
    for lay in C.CU_ORDER:
        lid = C.CU[lay]
        by_net = collections.defaultdict(list)
        for t in all_tr:
            if lname(t) == lay:
                by_net[t.GetNetname()].append((cu(t), C.k2c(t.GetStart())))
        for n, items in by_net.items():
            anchors_ = [Point(C.k2c(v.GetPosition())).buffer(0.2) for v in all_via if v.GetNetname() == n]
            anchors_ += [_pad_box(p_) for fp in board.GetFootprints() for p_ in fp.Pads() if p_.GetNetname() == n and p_.IsOnLayer(lid)]
            geoms = [g for g, _ in items]
            tree = shapely.STRtree(geoms + anchors_)
            for i, (g, start) in enumerate(items):
                if not any(int(j) != i for j in tree.query(g, predicate="intersects")):
                    iso.append((n, lay, start))
    dang = [v for v in (drc or {}).get("violations", []) if v["type"] in ("via_dangling", "track_dangling", "isolated_copper")]
    check(G, "no copper without a job: every GND via serves a GND pad or is the return via of a signal via within 2.0 mm (fence vias of the command field, return vias "
          "of the read vias and of the RDQS via pair: %d such vias) - GND vias that do neither: %s; no track piece that touches nothing of its net (%s); no dangling via or track and no "
          "isolated zone copper in the DRC report (%d)" % (n_ret, idle or "none", iso or "none", len(dang)), not idle and not iso and not dang, str(idle[:8]))

    # ---- 8d2. no open via hole at a solder land: a plain (unfilled) via whose hole stands in or within 0.15 mm of a land that carries paste takes solder
    #      from the joint; every such via must be filled and capped (found by the mechanical review: a via of the U4 exposed pad beside the land of C509)
    lands = []
    for fp in board.GetFootprints():
        for p_ in fp.Pads():
            for lid, pid in ((pcbnew.F_Cu, pcbnew.F_Paste), (pcbnew.B_Cu, pcbnew.B_Paste)):
                if p_.IsOnLayer(lid) and p_.IsOnLayer(pid):
                    lands.append(("%s.%s" % (fp.GetReference(), p_.GetNumber()), _pad_box(p_)))
    ltree = shapely.STRtree([g for _, g in lands])
    open_at_land, n_filled, near_ok = [], 0, 9.9
    for v in all_via:
        filled = v.GetFillingMode() == pcbnew.FILLING_MODE_FILLED and v.GetCappingMode() == pcbnew.CAPPING_MODE_CAPPED
        n_filled += filled
        hole = Point(C.k2c(v.GetPosition())).buffer(v.GetDrillValue() / 1e6 / 2)
        for j in ltree.query(hole.buffer(0.3)):
            dd = hole.distance(lands[int(j)][1])
            if filled:
                continue
            if dd < 0.15 - 1e-3:
                open_at_land.append((v.GetNetname(), tuple(round(q, 3) for q in C.k2c(v.GetPosition())), lands[int(j)][0], round(dd, 3)))
            else:
                near_ok = min(near_ok, dd)
    want_ep = sorted((v["x"], v["y"]) for v in B["vias"] if v.get("in_pad"))
    got_ep = sorted(tuple(round(q, 4) for q in C.k2c(v.GetPosition())) for v in st_via
                    if v.GetFillingMode() == pcbnew.FILLING_MODE_FILLED and v.GetCappingMode() == pcbnew.CAPPING_MODE_CAPPED)
    check(G, "no open via hole at a solder land: every via whose hole stands in or within 0.15 mm of a land with paste is filled and capped (%d filled vias on the board: "
          "the via-in-pads of U1, the command node vias and the %d vias in the exposed pads of U2 / U4 / U5); nearest plain via hole to a land %.3f mm"
          % (n_filled, len(got_ep), near_ok), not open_at_land and got_ep == [tuple(round(q, 4) for q in w_) for w_ in want_ep] and len(got_ep) == 8, str(open_at_land[:8]))

    # ---- 8e. copper coverage of the outer layers
    cov = outer_coverage(board)
    print("        outer-layer copper as a share of the board area (tracks + pads + vias + zone fill; upstream = the Antmicro board, measured the same way):")
    for lay in ("F.Cu", "B.Cu"):
        c_ = cov[lay]
        top = sorted(c_["nets"].items(), key=lambda kv: -kv[1])[:6]
        print("          %s  total %.1f %% (upstream %.1f)   zones: %s (upstream GND zones %.1f %%)   GND copper %.2f %% (upstream %.1f)   largest nets: %s"
              % (lay, c_["total"], UPSTREAM_COVERAGE[lay]["total"], ", ".join("%s %.2f %%" % kv for kv in sorted(c_["zones"].items())) or "none",
                 UPSTREAM_COVERAGE[lay]["GND zones"], c_["nets"].get("GND", 0.0), UPSTREAM_COVERAGE[lay]["GND"], ", ".join("%s %.2f" % kv for kv in top)))
    check(G, "outer-layer copper density is low, as upstream (no flood, no thieving): F.Cu %.1f %% of the board area (upstream %.1f), B.Cu %.1f %% (upstream %.1f); zone copper "
          "F.Cu %.2f %%, B.Cu %.2f %% (limit %.0f %% per layer; upstream 11.4 / 10.9 %% of which GND 4.0 / 2.8); GND zone copper on the outer layers: 0"
          % (cov["F.Cu"]["total"], UPSTREAM_COVERAGE["F.Cu"]["total"], cov["B.Cu"]["total"], UPSTREAM_COVERAGE["B.Cu"]["total"], sum(cov["F.Cu"]["zones"].values()),
             sum(cov["B.Cu"]["zones"].values()), OUTER_ZONE_LIMIT_PCT),
          all(sum(cov[l]["zones"].values()) <= OUTER_ZONE_LIMIT_PCT and "GND" not in cov[l]["zones"] and cov[l]["total"] < 35.0 for l in cov))

    # ---- 9. vias of the stage against the constraints of the earlier stages
    in3_lines = [(t.GetNetname(), LineString(track_pts(t, 0.05))) for t in all_tr if lname(t) == "In3.Cu"]
    in3_u = unary_union([l for _, l in in3_lines])
    dq_f = unary_union([cu(t) for t in all_tr if lname(t) == "F.Cu" and re.fullmatch(r"DQ\d(_WF)?", t.GetNetname())])
    vref_all = unary_union([cu(t) for t in all_tr if cls.get(t.GetNetname()) == "VREF"]
                           + [_pad_box(p) for fp in board.GetFootprints() for p in fp.Pads() if cls.get(p.GetNetname()) == "VREF"])
    pair_l = {l: unary_union([cu(t) for t in all_tr if lname(t) == l and t.GetNetname() in ("WCK_P", "WCK_N", "RDQS_P", "RDQS_N")]) for l in ("F.Cu", "B.Cu")}
    zones_in3 = [(r["name"], box(r["x"][0], r["y"][0], r["x"][1], r["y"][1])) for r in fpl["regions"] if r["layers"] == ["In3.Cu"]]
    m_in3 = m_dq = m_vref = m_pair = 9.9
    inside = []
    for v in st_via:
        xy = C.k2c(v.GetPosition())
        r = C.MM(v.GetWidth(pcbnew.F_Cu)) / 2
        pt = Point(xy)
        m_in3 = min(m_in3, pt.distance(in3_u))
        m_dq = min(m_dq, pt.distance(dq_f) - r)
        if v.GetNetname() != "GND":                     # the GND vias of the DAC's own parts (U5 pad, C332, C333) stand beside its pins by design
            m_vref = min(m_vref, pt.distance(vref_all) - r)
        m_pair = min(m_pair, min(pt.distance(pair_l[l]) for l in pair_l) - r)
        inside += [(nm, v.GetNetname(), xy) for nm, bx in zones_in3 if bx.contains(pt)]
    check(G, "vias of the stage (%d, all of the plan) against the constraints of the earlier stages: none inside an In3 routing zone (%s); nearest In3 "
          "line %.3f mm from a via centre (>= 0.475: 0.375 from the hole wall); nearest F.Cu DQ track %.3f mm from a via pad (>= 0.27); nearest VREF copper "
          "%.3f mm from a supply or slow-net via (>= 0.90; the GND vias of the DAC's own parts excepted); nearest pair on an outer layer %.3f mm from a via pad (>= 0.50)"
          % (len(st_via), ", ".join(nm for nm, _ in zones_in3), m_in3, m_dq, m_vref, m_pair),
          not inside and m_in3 >= 0.475 and m_dq >= 0.27 and m_vref >= 0.90 and m_pair >= 0.50, str(inside[:6]))

    # ---- 10. inner planes after the stage
    u1 = fpl["U1"]["body"]
    prow, ok_pl = [], True
    for z in board.Zones():
        if z.GetIsRuleArea() or C.stage_of(z) != "s90_zones":
            continue
        for lid in z.GetLayerSet().Seq():
            polys = poly_from_set(z.GetFilledPolysList(lid))
            anch = [Point(C.k2c(v.GetPosition())) for v in all_via if v.GetNetname() == z.GetNetname()]
            float_ = [round(p.area, 3) for p in polys if not any(p.buffer(0.02).contains(a) for a in anch)]
            areas = sorted((round(p.area, 2) for p in polys), reverse=True)
            prow.append((z.GetZoneName(), board.GetLayerName(lid), len(polys), areas[:1], areas[1:6], float_))
            ok_pl &= not float_ and len(polys) == 1
    for r_ in prow:
        print("          %-22s %-7s %2d filled outline(s): main %s mm2, others %s, floating %s" % r_)
    check(G, "inner planes: every plane and island is ONE filled outline; none floating (each holds vias of its net)", ok_pl, str([r_ for r_ in prow if r_[2] != 1]))
    # In3 high-speed lines still between solid GND: nothing of the stage opened In2 / In4 under them
    worst = {}
    for lay in ("In2.Cu", "In4.Cu"):
        g_ = gnd_fill(board, lay, box(-1, -1, 80, 40))
        own = unary_union([Point(C.k2c(v.GetPosition())).buffer(0.45) for v in all_via if v.GetNetname() in IN3_NETS])
        u1b = box(u1["x"][0] - 0.3, u1["y"][0] - 0.3, u1["x"][1] + 0.3, u1["y"][1] + 0.3)
        tot_, cov_ = 0.0, 0.0
        for nn, ln in in3_lines:
            c_ = ln.buffer(0.075).difference(own).difference(u1b)
            tot_ += c_.area
            cov_ += c_.intersection(g_).area
        worst[lay] = 100.0 * cov_ / tot_ if tot_ else 0
    check(G, "reference planes after the stage: the In3 lines outside U1 lie over GND fill on In2 for %.2f %% and on In4 for %.2f %% of their copper "
          "(the antipads of their own vias excepted): no via, island or pour of this stage opened a plane under a high-speed line"
          % (worst["In2.Cu"], worst["In4.Cu"]), worst["In2.Cu"] > 99.95 and worst["In4.Cu"] > 99.95)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--board", default=C.BOARD)
    ap.add_argument("--drc-json", default="")
    ap.add_argument("--allow-unconnected", type=int, default=None)
    ap.add_argument("--coverage", default="", help="print the outer-layer copper coverage of any board file (e.g. the upstream board) and exit")
    a = ap.parse_args()
    if a.coverage:
        cov = outer_coverage(pcbnew.LoadBoard(a.coverage))
        for lay, c_ in cov.items():
            print("%s total %.2f %%; zones %s; nets %s" % (lay, c_["total"], {k: round(v, 2) for k, v in sorted(c_["zones"].items(), key=lambda kv: -kv[1])},
                                                         {k: round(v, 2) for k, v in sorted(c_["nets"].items(), key=lambda kv: -kv[1])[:10]}))
        return
    spec = C.spec()
    board = C.load_board(a.board)
    gate_parity(board, spec)
    s50 = any(C.stage_of(t) == "s50_power" for t in board.GetTracks())
    drc = gate_drc(a.board, a.drc_json, a.allow_unconnected if a.allow_unconnected is not None else (0 if s50 else None))
    outline = gate_outline(board, spec)
    gate_courtyards(board, spec)
    gate_fingers(board, spec, outline)
    gate_cmd(board, spec, drc)
    gate_data(board, spec, drc, write_deskew=os.path.abspath(a.board) == os.path.abspath(C.BOARD))
    gate_crossings(a.board, spec)
    gate_power(board, spec, drc)
    fails = [r for r in results if not r[2]]
    print("RESULT: %d checks, %d failed" % (len(results), len(fails)))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Stage s90: plane zones and the keep-outs of the finger area.  Runs last (after the routing stages).

  In1.Cu  GND, solid
  In2.Cu  GND (upper reference of In3, solid under every In3 trace) with the V1P2 island under the VDD fingers
          (floorplan.power.v1p2_island: the finger rectangle, the bridge north of the key slot and the tab at the foot
          of the feed corridor, one piece)
  In4.Cu  GND with the VDD2H island (floorplan.power.vdd2h_island)
  pull-back from the finger edge per floorplan.plane_plan.pull_back (In1 / In4 1.0 mm, In2 0.5 mm) and from
  every other edge, the key slot included (0.3 mm)
  rule areas: no tracks and no pours on In3 within 3.0 mm of the finger edge; no pours on F.Cu / B.Cu within
  2.85 mm of the finger edge (no pour can ever reach the fingers; the board has no outer-layer GND pour at all)

The power pours under U1 (B.Cu) belong to s50_power.  All zones are (re)filled here.
"""
import os
import sys

import pcbnew
from shapely.geometry import Polygon, box

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C      # noqa: E402

STAGE = "s90_zones"
PLANE_CLEARANCE = 0.10      # antipad = pad + 2 x 0.10: leaves a 0.20 mm web between the via-in-pads of two ball columns
PLANE_MIN_WIDTH = 0.10      # ... and 0.10 mm between two ball rows
# The isolation channel between a power island and the GND plane around it is PLANE_CLEARANCE as well (0.10 mm, the gap of the pair legs and of
# every antipad).  A 0.20 mm channel was tried in the final fix pass and not kept: the GND edge of In4 then lies under the In3 pad of the J9
# via (CK_N), or the island edge becomes tangent to the via pads of row K; a plane-to-plane short is caught by the bare-board netlist test
# (fab note 10)

def ring(poly):
    """Exterior of a shapely polygon as a list of points without the closing duplicate, 1 um grid."""
    pts = [(round(x, 3), round(y, 3)) for x, y in poly.exterior.coords[:-1]]
    out = []
    for p in pts:
        if not out or p != out[-1]:
            out.append(p)
    return out


def single(geom, what):
    if geom.geom_type != "Polygon" or geom.interiors:
        raise RuntimeError("%s: expected one polygon without holes, got %s" % (what, geom.geom_type))
    return geom


def main():
    spec = C.spec()
    fpl = spec["floorplan"]
    pb = fpl["plane_plan"]["pull_back"]
    board = C.load_board()
    removed = C.rip_up(board, STAGE)
    stage = C.Stage(board, STAGE)

    outline = C.board_polygon(board)[0]
    W, H = fpl["outline"]["width"], fpl["outline"]["height"]

    def plane_area(layer):
        p = pb[layer]
        return single(outline.buffer(-p["other_edges"], join_style=2).intersection(box(-1, p["finger_edge"], W + 1, H + 1)),
                      layer)

    zones = []
    # In1, In2, In4: GND planes
    for layer in ("In1.Cu", "In2.Cu", "In4.Cu"):
        zones.append(C.add_zone(board, stage, "GND", layer, ring(plane_area(layer)), name="GND plane %s" % layer[:3],
                                clearance=PLANE_CLEARANCE, min_thickness=PLANE_MIN_WIDTH, priority=0))
    # islands: VDD2H on In4, V1P2 on In2 (the finger rectangle with its extensions: one piece, the bridge joins the two halves beside the key slot)
    for key, netname in (("vdd2h_island", "VDD2H"), ("v1p2_island", "V1P2")):
        isl = fpl["power"][key]
        layer = isl["layer"]
        shape = box(isl["x"][0], isl["y"][0], isl["x"][1], isl["y"][1])
        for e_ in (isl.get("extensions") or {}).values():
            shape = shape.union(box(e_["x"][0], e_["y"][0], e_["x"][1], e_["y"][1]))
        area = single(shape.intersection(plane_area(layer)), key)
        zones.append(C.add_zone(board, stage, netname, layer, ring(area), name="%s island %s" % (netname, layer[:3]),
                                clearance=PLANE_CLEARANCE, min_thickness=PLANE_MIN_WIDTH, priority=1))

    # keep-outs of the finger area
    e = 0.5
    C.add_rule_area(board, stage, "In3.Cu", [(-e, -e), (W + e, -e), (W + e, pb["In3.Cu"]["finger_edge"]), (-e, pb["In3.Cu"]["finger_edge"])],
                    "finger area: no In3 tracks or copper", no_tracks=True, no_pour=True)
    yo = pb["F.Cu / B.Cu pours"]["finger_edge"]
    C.add_rule_area(board, stage, ["F.Cu", "B.Cu"], [(-e, -e), (W + e, -e), (W + e, yo), (-e, yo)],
                    "finger area: no outer-layer pour", no_pour=True)

    # named rule areas that restrict nothing: they exist so that a rule of the .kicad_dru can leave them out
    for ra in fpl.get("rule_areas", []):
        (x0, x1), (y0, y1) = ra["x"], ra["y"]
        C.add_rule_area(board, stage, ra["layers"], [(x0, y0), (x1, y0), (x1, y1), (x0, y1)], ra["name"])

    ok = C.fill_zones(board)
    C.save_board(board)
    C.log(STAGE, "ripped up %d items; %d zones, %d rule areas; fill %s" % (removed, len(zones), 2 + len(fpl.get("rule_areas", [])), "ok" if ok else "FAILED"))
    for z in zones:
        for lid in z.GetLayerSet().Seq():
            polys = z.GetFilledPolysList(lid)
            area = sum(abs(polys.Outline(i).Area()) for i in range(polys.OutlineCount())) / 1e12
            holes = sum(polys.HoleCount(i) for i in range(polys.OutlineCount()))
            C.log(STAGE, "  %-22s %-7s %-6s %d filled outline(s), %.1f mm2" % (z.GetZoneName(), board.GetLayerName(lid), z.GetNetname(),
                                                                         polys.OutlineCount(), area))


if __name__ == "__main__":
    main()

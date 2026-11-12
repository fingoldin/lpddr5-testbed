#!/usr/bin/env python3
"""Stage s50: power and slow nets.  Everything is drawn from design/netlist.json (floorplan.power, written by scripts/build_netlist.py from the
plan in scripts/power_side.py); this stage decides nothing.

  1.2 V     four vias from the In2 island to a 1.5 mm STRIP on F.Cu in the corridor between the comb and the read end, a 0.9 mm arm
            west to U2; U4 (IN) and R504 tap the arm through vias (they stand on B.Cu); C512 / C513 / C502 east of the foot
  U2        (F.Cu) VDD2H: output strap with four vias into the VDD2H island of In4, feedback, soft start, exposed-pad vias
  3.3 V     F.Cu from finger 255 west of the tooling hole, around the east and north of the DAC, north and west to U2 BIAS;
            one tap via each for U3 (IN) and U4 (BIAS)
  U3 / U4   (B.Cu) VDD1 and VDDQ with all their parts; VDD1 west on B.Cu to the east rail of U1 (C423 / C421), from ball W1 on F.Cu
            around the north of U1 to ball W15 and down the west side to ball C15 (one layer change, at the ball W1);
            VDDQ west on B.Cu to the north rail of U1, the rails outside columns 1
            and 15, six pour outlines under U1 (VDDQ flanks, VDD2H centre)
  DAC       U5 with its capacitors, SCL / SDA from fingers 253 / 254
  RESET_n   F.Cu from ball H1 to ONE via beside U1, B.Cu to R403 / R402 at finger 232 (back): no via at the finger end
  GND       every GND pad on a via of its own (in the pad or at the end of a short track).  NO ground pour on F.Cu / B.Cu and NO stitching
            via: as on the upstream board the outer layers carry tracks, pads and local power pours only (floorplan.power.outer_layer_ground)

No wire of the stage changes layer and comes back: every via stands beside a pad of its net or joins a plane.  The stage checks that
every centre line starts and ends on a pad or a via of its net or on the copper of another line of the same net on the same layer.
Zones are filled by s90_zones.  check_pcb.py (gate 9) measures the result.  Re-running the stage removes what it made before.
"""
import math
import os
import sys

import pcbnew
from shapely.geometry import LineString, Point, box

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C      # noqa: E402

STAGE = "s50_power"


def pad_box(p):
    bb = p.GetBoundingBox()
    lo = C.k2c(pcbnew.VECTOR2I(bb.GetLeft(), bb.GetBottom()))
    hi = C.k2c(pcbnew.VECTOR2I(bb.GetRight(), bb.GetTop()))
    return box(lo[0], lo[1], hi[0], hi[1])


def anchors(board, net):
    """Copper a wire of the net may end on, before the stage draws: {layer: [shapely geometry]} of its pads and vias."""
    out = {l: [] for l in C.CU_ORDER}
    code = board.GetNetcodeFromNetname(net)
    for fp in board.GetFootprints():
        for p in fp.Pads():
            if p.GetNetCode() == code and p.GetNumber():
                for lname in ("F.Cu", "B.Cu"):
                    if p.IsOnLayer(C.CU[lname]):
                        out[lname].append(pad_box(p))
    for t in board.GetTracks():
        if t.Type() == pcbnew.PCB_VIA_T and t.GetNetCode() == code:
            x, y = C.k2c(t.GetPosition())
            for lname in C.CU_ORDER:
                out[lname].append(Point(x, y).buffer(C.MM(t.GetWidth(pcbnew.F_Cu)) / 2))
    return out


def main():
    spec = C.spec()
    fpl = spec["floorplan"]
    pw = fpl["power"]["build"]
    board = C.load_board()
    removed = C.rip_up(board, STAGE)
    stage = C.Stage(board, STAGE)

    # 1. vias of the plan
    for v in pw["vias"]:
        # in_pad: a via inside a solder land (the exposed pads of U2 / U4 / U5): filled and capped like the via-in-pads of U1, so that it
        # takes no solder from the land (one of them also touches the GND land of C509 on the other face)
        C.add_via(board, stage, v["net"], v["x"], v["y"], v["diameter"], v["drill"], in_pad=bool(v.get("in_pad")), remove_unused_pads=True)

    # 2. wires; an end is a pad or via of the net, or lies on the copper of another wire of the same net on the same layer
    anch = {}
    drawn = {}
    for w in pw["wires"]:
        drawn.setdefault((w["net"], w["layer"]), []).append(LineString([tuple(q) for q in w["path"]]).buffer(w["width"] / 2 - 1e-6))
    # the tracks of earlier stages on the same net and layer (finger ties, GND links of the data side) count as well
    for t in board.GetTracks():
        if t.Type() == pcbnew.PCB_TRACE_T:
            key = (t.GetNetname(), board.GetLayerName(t.GetLayer()))
            drawn.setdefault(key, []).append(LineString([C.k2c(t.GetStart()), C.k2c(t.GetEnd())]).buffer(C.MM(t.GetWidth()) / 2))
    n_seg = 0
    count = {}
    for i, w in enumerate(pw["wires"]):
        net, layer = w["net"], w["layer"]
        if net not in anch:
            anch[net] = anchors(board, net)
        path = [tuple(q) for q in w["path"]]
        own = LineString(path).buffer(w["width"] / 2 - 1e-6)
        for e in (path[0], path[-1]):
            pt = Point(e).buffer(w["width"] / 2 - 1e-4)            # the round end of the track
            ok = any(g.intersects(pt) for g in anch[net][layer])
            if not ok:
                ok = any(g.intersects(pt) for g in drawn[(net, layer)] if not g.equals(own))
            assert ok, "%s '%s' on %s: the end %s is neither on a pad / via of the net nor on another wire of it" % (net, w["role"], layer, e)
        n_seg += len(C.add_track(board, stage, net, layer, path, w["width"]))
        count[w["group"]] = count.get(w["group"], 0) + 1

    # 3. power pours (B.Cu under U1).  There is no GND pour on an outer layer and no stitching via (floorplan.power.outer_layer_ground)
    assert not fpl["power"]["gnd_pours"], "the spec asks for a GND pour on an outer layer: the plan has none"
    mw = fpl["power"]["rules"]["power_pour_min_width_mm"]
    for z in pw["pours"]:
        C.add_zone(board, stage, z["net"], z["layer"], [tuple(q) for q in z["outline"]], name=z["name"], clearance=0.10, min_thickness=mw,
                   priority=z.get("priority", 3))

    C.save_board(board)
    C.log(STAGE, "ripped up %d items; %d centre lines = %d track items (%s); %d vias of the plan (no stitching via); %d power pours, no GND pour"
          % (removed, len(pw["wires"]), n_seg, ", ".join("%s %d" % kv for kv in sorted(count.items())), len(pw["vias"]), len(pw["pours"])))
    sz = fpl["power"]["v1p2_feed"]["sizing"]
    C.log(STAGE, "  1.2 V feed: strip %.1f mm on F.Cu; %s" % (sz["width_chosen_mm"], "; ".join(
        "%s %.1f mm x %.1f mm at %.2f A: %.2f mohm, %.2f mV, +%.2f K, %.1f A/mm2" % (n, p["length_mm"], p["width_mm"], p["current_a"], p["mohm"], p["mv"], p["rise_k"], p["a_per_mm2"])
        for n, p in sz["parts"].items())))
    C.log(STAGE, "  fingers to U2: %.2f mV; a %.0f mV budget needs %.2f mm (chosen %.1f: %s)"
          % (sz["drop_fingers_to_u2_mv"], 5, sz["width_for_the_budget_mm"], sz["width_chosen_mm"], sz["margin"]))


if __name__ == "__main__":
    main()

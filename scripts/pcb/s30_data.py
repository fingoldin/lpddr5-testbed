#!/usr/bin/env python3
"""Stage s30: the data side.  Write group (DQ0-7 from the write fingers of bank 64 through the comb to the byte-0 balls
of U1), read group (the In3 branches from the node vias to the read fingers of bank 66), the WCK pair, the RDQS pair,
the reference networks of the N fingers with the VREF_RD bus and the DAC network, and RESET_n.

Everything is drawn from design/netlist.json (floorplan.data.wires, written by scripts/build_netlist.py from the plan
in scripts/data_side.py); this stage decides nothing.  Topology ('T at the divider', comb form; DESIGN.md section 14):

  DQ      ball - 0.09 mm neck - F.Cu line (0.15 mm) on its level - one parallel 45-degree fan - tooth straight down the
          column axis into the node via = the T.  At the T: 240 ohm in line on the finger's face, 150 ohm back to back on
          the other face (0.20 mm links), shunt GND via south of the row.  The read branch leaves the node via on In3:
          one 45-degree run into the lane beside the column, north to its level, east in an ordered bundle (0.55 mm
          pitch), one 45-degree ramp, a via on its own level above its read finger, finger track (0.20 mm) straight down.
          The read fingers are taken in the order of the bundle (lowest line, westmost finger): no finger track crosses
          an In3 line, the read vias form a regular staircase; the read group is not matched on the card (IDELAY)
  WCK     B.Cu pair from the via-in-pads of E4 / D5 on the diagonal D3 - C2 - B1, east as the lowest lane, south into
          two columns (0.20 mm F.Cu links like the DQ columns, the two shunt GND vias mirror images about the pair); no skew bump
  RDQS    In3 pair from the via-in-pads of B3 / C4, east between the two lower and the six upper F.Cu lines, one 45-degree
          run north-east across the six diagonals of the fan (90 degrees to them), east above every read via, one
          45-degree run down to the axis of fingers 219 / 221 (host pair DQS6) and south to its via pair just above the
          fingers; 5.8 mm of F.Cu straight down into them, east of every read finger (like a read DQ line: In3, one via
          above the finger, a short outer-layer drop); the two 150 ohm loads on 0.40 mm side links in the row of the 39 ohm resistors,
          GND on the VSS finger vias beside the drop; one skew bump on RDQS_P, on In3 beside U1
  VREF    N finger stubs, 39 ohm - bus, 100 nF - bus, 100 nF - VSS finger via, the bus (F.Cu, via, B.Cu), the feed to
          TP4 and the DAC network
  RESET_n is not a wire of this stage: the power stage routes it (one via beside U1, B.Cu to finger 232)

All vias are field vias (floorplan.vias, placed by s10_escape): the stage adds none.  The stage checks that every
centre line starts and ends on a pad or a via of its net or on a vertex of another line of the same net on the same
layer, that every line is drawn at 0 / 45 / 90 / 135 degrees without an acute bend, and prints the planned delays;
check_pcb.py (gate 7) measures every net again along the copper of the board.

Re-running the stage removes what it made before (UUID tag) and builds it again.
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C      # noqa: E402
import pcbnew           # noqa: E402

STAGE = "s30_data"


def anchors(board, net):
    """Pad centres (by layer) and via positions of a net on the board, card frame."""
    out = []
    code = board.GetNetcodeFromNetname(net)
    for fp in board.GetFootprints():
        for p in fp.Pads():
            if p.GetNetCode() == code and p.GetNumber():
                for lname, lid in (("F.Cu", pcbnew.F_Cu), ("B.Cu", pcbnew.B_Cu)):
                    if p.IsOnLayer(lid):
                        bb = p.GetBoundingBox()
                        lo, hi = C.k2c(pcbnew.VECTOR2I(bb.GetLeft(), bb.GetBottom())), C.k2c(pcbnew.VECTOR2I(bb.GetRight(), bb.GetTop()))
                        out.append((C.k2c(p.GetPosition()), lname, "%s.%s" % (fp.GetReference(), p.GetNumber()), (lo, hi)))
    for t in board.GetTracks():
        if t.Type() == pcbnew.PCB_VIA_T and t.GetNetCode() == code:
            for lname in C.CU_ORDER:
                out.append((C.k2c(t.GetPosition()), lname, "via", None))
    return out


def on_anchor(anch, xy, layer):
    """Name of the pad / via of the net the point sits on: a via or a part pad by its centre, a finger or a ball
    anywhere inside the pad."""
    for pos, lname, name, box in anch:
        if lname != layer:
            continue
        if math.dist(pos, xy) < 1e-4:
            return name
        if box and name.split(".")[0] in ("J1", "U1") and box[0][0] - 1e-6 <= xy[0] <= box[1][0] + 1e-6 and box[0][1] - 1e-6 <= xy[1] <= box[1][1] + 1e-6:
            return name
    return None


def main():
    spec = C.spec()
    fpl = spec["floorplan"]
    data = fpl["data"]
    board = C.load_board()
    removed = C.rip_up(board, STAGE)
    stage = C.Stage(board, STAGE)
    anch = {}

    def A(net):
        if net not in anch:
            anch[net] = anchors(board, net)
        return anch[net]

    # an end of a wire is a pad or a via of its net, or a vertex of another wire of the same net on the same layer
    vertices = {}
    for w in data["wires"]:
        for p in w["path"]:
            vertices.setdefault((w["net"], w["layer"], round(p[0], 4), round(p[1], 4)), []).append(w["role"])
    count = {}
    for w in data["wires"]:
        path = [tuple(p) for p in w["path"]]
        for e in (path[0], path[-1]):
            key = (w["net"], w["layer"], round(e[0], 4), round(e[1], 4))
            ok = on_anchor(A(w["net"]), e, w["layer"]) or len(vertices[key]) > 1
            assert ok, "%s %s on %s: the end %s is neither on a pad / via of the net nor a junction" % (w["net"], w["role"], w["layer"], e)
        C.add_track(board, stage, w["net"], w["layer"], path, w["width"])
        count[w["group"]] = count.get(w["group"], 0) + 1
    C.save_board(board)

    g = {q["name"]: q for q in spec["groups"]}
    kn = data["knobs"]
    C.log(STAGE, "ripped up %d items; %d centre lines = %d track items (%s); no via added, no arc, no serpentine; one skew bump (RDQS_P)"
          % (removed, len(data["wires"]), stage.count.get("track", 0), ", ".join("%s %d" % kv for kv in sorted(count.items()))))
    C.log(STAGE, "  planned (the board is measured again by check_pcb.py, gate 7); card delay from the finger top, bends by rules.bend_model")
    C.log(STAGE, "    WRITE (not matched on the card: per-bit output delay in the FPGA)      READ (not matched on the card: per-bit input delay; centre %.1f ps)"
          % g["RD_P"]["total_delay_centre_ps"])
    C.log(STAGE, "    net  column x  w finger  card ps  host ps  total ps  to centre  ODELAY | r finger  level y  raised  card ps  host ps  total ps  to centre  IDELAY")
    w, r = g["WR"], g["RD_P"]
    for n in data["columns"]:
        c, q = data["columns"][n], data["read"][n]
        C.log(STAGE, "    %-4s %7.3f   %3d %s    %7.2f  %6.1f   %7.2f    %+6.2f   %5.1f |  %3d %s   %7.3f  %5.3f  %7.2f  %6.1f   %7.2f    %+6.2f   %5.1f"
              % (n, c["x"], c["write_finger"], c["series_side"], w["per_net_planned_card_delay_ps"][n], w["per_net_host_plus_pkg_ps"][n],
                 w["per_net_planned_total_ps"][n], w["per_net_to_centre_ps"][n], w["deskew"]["per_bit_output_delay_ps"][n],
                 q["p_finger"], q["finger_track_layer"][0], q["level_y"], q["raised_mm"], r["per_net_planned_card_delay_ps"][n],
                 r["per_net_host_plus_pkg_ps"][n], r["per_net_planned_total_ps"][n], r["per_net_to_centre_ps"][n], r["deskew"]["per_bit_input_delay_ps"][n]))
    C.log(STAGE, "    write group: centre %.1f ps +/-%.1f (rule +/-10: closed by the gateware deskew); read group: +/-%.2f ps about %.1f (closed by the gateware deskew; 0 read-end crossings)"
          % (w["total_delay_centre_ps"], w["spread_half_ps"], kn["read_half_spread_ps"], kn["read_centre_ps"]))
    q, k = g["RD_RDQS"], g["WR_WCK"]
    C.log(STAGE, "    RDQS_P %.2f ps, RDQS_N %.2f ps total (via pair above the fingers at y = %.2f, bump %.3f mm deep): %+.2f ps P to N, %+.2f ps from the centre of the read group"
          % (q["per_net_planned_total_ps"]["RDQS_P"], q["per_net_planned_total_ps"]["RDQS_N"], kn["rdqs_via_y"], kn["rdqs_bump_depth_mm"],
             q["planned_skew_at_the_fpga_ps"], q["strobe_minus_dq_group_ps"]))
    C.log(STAGE, "    WCK_P %.2f ps, WCK_N %.2f ps total: %+.2f ps P to N (no bump)"
          % (k["per_net_planned_total_ps"]["WCK_P"], k["per_net_planned_total_ps"]["WCK_N"], k["planned_skew_at_the_balls_ps"]))


if __name__ == "__main__":
    main()

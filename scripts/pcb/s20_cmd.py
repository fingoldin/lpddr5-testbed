#!/usr/bin/env python3
"""Stage s20: the command bus.  CA0-6, CS0 and the CK pair from fingers 66-84 (bank 65) to U1.

Per net, everything from design/netlist.json:
  finger stub      floorplan.finger_stubs[<net>_F]: 0.20 mm on the finger's own outer layer to pad 1 of the series resistor
  divider          series pad 2 -> node via and node via -> shunt pad 1 (opposite face), drawn at the width of the 0201
                   land (0.40 mm) so that no slot remains between land and via pad; shunt pad 2 -> fence via (GND,
                   0.25 mm, one 45-degree run)
  In3              floorplan.command.columns[net].in3_route: node via -> channel between two ball columns -> via-in-pad
                   CA / CS 0.15 mm, one trace per channel; CK 0.10 / 0.10 mm pair on ball column 9, split at row H,
                   CK_P hooks back into H9 (no meander on the pair)
  length tuning    CA / CS only, in the In3 tuning zone: groups[CMD].tuning.per_net[net].shape (scripts/tuning.py):
                   a parallel shift of the trace between two 45-degree runs and / or rounded bumps on common rows.
                   The free parameter (the shift of a shift-only net, the bump amplitude of the others) is solved here
                   from the copper that is actually drawn, with the ELECTRICAL length of every bend (rules.bend_model)

Delay: card delay = finger top -> stub -> series resistor (pad centre to pad centre) -> node via (barrel between the
resistor's face and In3, mid-copper heights) -> In3 (bends by their electrical length) -> via-in-pad (In3 to F.Cu),
with rules.delay_ps_per_mm.  Every net is tuned to its target; there is no common offset.  check_pcb.py measures the
result again from the board.

RESET_n is not routed here: it is a centre line of the data side (floorplan.data.wires) and is drawn by s30_data.

Re-running the stage removes what it made before (UUID tag) and builds it again.
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C      # noqa: E402
import tuning as T      # noqa: E402  (scripts/tuning.py, on the path through common)
from shapely.geometry import LineString      # noqa: E402

STAGE = "s20_cmd"
W_NODE = 0.40           # series pad 2 -> node via -> shunt pad 1: the width of the 0201 land and of the via pad, so that
                        # no 0.05 - 0.10 mm slot is left between land and via pad (same net, not seen by the DRC)
W_GND = 0.25            # shunt pad 2 -> fence via (POWER class minimum)


def group():
    return next(g for g in C.spec()["groups"] if g["name"] == "CMD")


def resistors(net):
    """(series ref, shunt ref) of a command net, from the pins of the spec."""
    ser = sh = None
    for p in C.spec()["parts"]:
        pins = set(p["pins"].values())
        if len(p["pins"]) == 2 and net in pins:
            if finger_net(net) in pins:
                ser = p["ref"]
            elif "GND" in pins:
                sh = p["ref"]
    assert ser and sh, net
    return ser, sh


def finger_net(net):
    g = group()
    return g["finger_side_nets"][g["nets"].index(net)]


def pad_by_net(board, ref, net):
    fp = C.footprint(board, ref)
    for p in fp.Pads():
        if p.GetNumber() and p.GetNetname() == net:
            return p
    raise KeyError("%s has no pad on %s" % (ref, net))


def face(pad):
    import pcbnew
    return "F.Cu" if pad.IsOnLayer(pcbnew.F_Cu) else "B.Cu"


def main():
    spec = C.spec()
    fpl = spec["floorplan"]
    cmd = fpl["command"]
    g = group()
    tun = g["tuning"]
    zone = cmd["in3_tuning_zone"]
    z = C.stackup_z()
    d_via = spec["rules"]["delay_ps_per_mm"]["through via (per mm of barrel)"]
    d_in3 = C.delay_ps_per_mm("In3.Cu", 0.15)
    ncl = spec["rules"]["netclasses"]
    w_se = ncl["CMD_SE"]["layers"]["In3.Cu"]["width"]
    w_ck = ncl["CMD_CK"]["layers"]["In3.Cu"]["width"]
    min_gap = tun["min_gap_to_neighbour"]
    pitch = tun["leg_pitch"]
    model = spec["rules"]["bend_model"]
    finger_top_y = fpl["outline"]["finger_pad_y"][1]

    board = C.load_board()
    removed = C.rip_up(board, STAGE)
    stage = C.Stage(board, STAGE)
    vias = {(v["net"], round(v["x"], 3)): v for v in fpl["vias"] if abs(v["y"] - cmd["rows_y"]["node_via"]) < 1e-6
            or abs(v["y"] - cmd["rows_y"]["gnd_via"]) < 1e-6}
    cols = cmd["columns"]
    order = sorted(cols, key=lambda n: cols[n]["x"])

    # ---- 1. finger stubs and divider field; the fixed part of every card delay ---------------------------------
    fixed = {}          # net -> ps of everything except the In3 trace
    for net in order:
        c = cols[net]
        fnet = finger_net(net)
        stub = fpl["finger_stubs"][fnet]
        ser, sh = resistors(net)
        p1, p2 = pad_by_net(board, ser, fnet), pad_by_net(board, ser, net)
        q1, q2 = pad_by_net(board, sh, net), pad_by_net(board, sh, "GND")
        lay_s, lay_p = face(p1), face(q1)
        assert lay_s == stub["layer"] == C.finger_layer(c["finger"]) and lay_p != lay_s, net
        node = vias[(net, round(c["x"], 3))]
        gnd = vias[("GND", round(c["gnd_via_x"], 3))]
        xy = lambda p: C.k2c(p.GetPosition())      # noqa: E731
        path = [tuple(q) for q in stub["path"]]
        assert max(abs(path[-1][0] - xy(p1)[0]), abs(path[-1][1] - xy(p1)[1])) < 1e-6, "stub of %s does not end on %s" % (net, ser)
        assert abs(path[0][0] - C.finger(board, c["finger"])[0]) <= 0.076 and path[0][1] < finger_top_y, net
        C.add_track(board, stage, fnet, lay_s, path, stub["width"])
        nxy = (node["x"], node["y"])
        C.add_track(board, stage, net, lay_s, [xy(p2), nxy], W_NODE)
        C.add_track(board, stage, net, lay_p, [nxy, xy(q1)], W_NODE)
        gx, gy = gnd["x"], gnd["y"]
        dx = abs(gx - xy(q2)[0])
        C.add_track(board, stage, "GND", lay_p, [xy(q2), (xy(q2)[0], gy - dx), (gx, gy)], W_GND)
        # delay of the fixed part: stub from the finger top, resistor body, pad 2 -> node via, two barrels
        outer_mm = C.polyline_length(path) - (finger_top_y - path[0][1]) \
            + math.dist(xy(p1), xy(p2)) + math.dist(xy(p2), nxy)
        fixed[net] = outer_mm * C.delay_ps_per_mm(lay_s, stub["width"]) + abs(z["In3.Cu"] - z[lay_s]) * d_via \
            + abs(z["In3.Cu"] - z["F.Cu"]) * d_via

    # ---- 2. CK pair: the planned centre lines, no tuning --------------------------------------------------------
    planned = {}        # net -> card delay as drawn, ps (In3 bends by their electrical length, rules.bend_model)
    tgt = g["per_net_target_card_delay_ps"]
    for net in ("CK_N", "CK_P"):
        route = [tuple(q) for q in cols[net]["in3_route"]]
        assert math.dist(route[-1], C.ball(board, cols[net]["ball"])) < 1e-6
        C.add_track(board, stage, net, "In3.Cu", route, w_ck)
        planned[net] = fixed[net] + T.polyline_lengths(route, w_ck, model)[1] * d_in3
        assert abs(planned[net] - tgt[net]) < 0.25, "%s: %.2f ps as drawn, target %.2f" % (net, planned[net], tgt[net])

    # ---- 3. CA / CS: channel axis, tuning feature (parallel shift and / or rounded bumps), 0.4 mm jog into the ball ----
    rows, lines = [], {}
    y_break = cmd["ck_split"]["courtyard_break_y"]
    for net in order:
        if net in ("CK_N", "CK_P"):
            continue
        c = cols[net]
        route = [tuple(q) for q in c["in3_route"]]
        xc, (xb, yb) = c["x"], C.ball(board, c["ball"])
        assert math.dist(route[-1], (xb, yb)) < 1e-6 and abs(route[0][0] - xc) < 1e-9 and abs(route[-2][0] - xc) < 1e-9, net
        plan = tun["per_net"][net]
        shape = dict(plan["shape"])
        assert abs(shape["x"] - xc) < 1e-9 and abs(shape["y"][0] - route[0][1]) < 1e-9 and abs(shape["y"][1] - route[-2][1]) < 1e-9, net
        jog = math.dist(route[-2], route[-1])
        jog_e = jog - T.bend_shortfall(model, w_se, math.pi / 4)          # the 45-degree vertex where the jog leaves the axis
        residual = plan.get("hfss_residual_mm", 0.0)                      # HFSS of this net's feature beyond the formula
        want_mm = (tgt[net] - fixed[net]) / d_in3 - jog_e - residual      # electrical length the formula must give
        straight = shape["y"][1] - shape["y"][0]
        assert want_mm >= straight - 1e-9, "%s is already too long by %.3f mm" % (net, straight - want_mm)
        if plan["solve"] == "shift":
            v = T.solve(shape, want_mm, w_se, model, "shift", 0.02, 1.2)
            shape["shift"] = round(math.copysign(v, shape["shift"]), 6)
            dev = abs(shape["shift"] - plan["shape"]["shift"])
        else:
            v = T.solve(shape, want_mm, w_se, model, "amplitude", 2 * shape["radius"], tun["amplitude_max"])
            shape["amplitude"] = round(v, 6)
            dev = abs(shape["amplitude"] - plan["shape"]["amplitude"])
            assert shape["amplitude"] >= tun["amplitude_min"] - 1e-9 and shape["radius"] >= tun["radius_min"] - 1e-9, (net, shape)
        assert dev < 0.02, "%s: %s solved as %.3f, the plan has %.3f" % (net, plan["solve"], v, plan["shape"][plan["solve"]])
        verts = T.vertices(shape)
        # the legs stay inside the tuning zone (copper), the 45-degree runs inside shift_y
        leg_y = [q[1] for q in verts if q[2]]
        assert not leg_y or (min(leg_y) - w_se / 2 >= zone["y"][0] - 1e-9 and max(leg_y) + w_se / 2 <= zone["y"][1] + 1e-9), (net, leg_y)
        off_axis = [q[1] for q in verts if abs(q[0] - xc) > 1e-9]
        assert not off_axis or (min(off_axis) > tun["shift_y"][0] - 1e-9 and max(off_axis) < tun["shift_y"][1] + 1e-9), (net, off_axis)
        # a vertex at the U1 courtyard edge where the trace runs straight north there (rules "outside U1" test whole segments)
        k = next((i for i in range(len(verts) - 1) if abs(verts[i][0] - verts[i + 1][0]) < 1e-9
                  and verts[i][1] + (verts[i][2] or 0.0) < y_break - 0.05 and verts[i + 1][1] - (verts[i + 1][2] or 0.0) > y_break + 0.05), None)
        full = verts + [(xb, yb, None)]
        if k is None:
            C.add_path(board, stage, net, "In3.Cu", full, w_se)
        else:
            brk = (verts[k][0], y_break, None)
            C.add_path(board, stage, net, "In3.Cu", full[:k + 1] + [brk], w_se)
            C.add_path(board, stage, net, "In3.Cu", [brk] + full[k + 1:], w_se)
        geo, el = T.lengths(full, w_se, model)
        el += residual
        planned[net] = fixed[net] + el * d_in3
        lines[net] = LineString(T.centre_line(verts))
        rows.append((net, shape, plan, geo - straight - jog, el - straight - jog_e, geo - el))

    # ---- 4. gaps between the centre lines as drawn (copper to copper) ---------------------------------------------
    ck_line = {n: LineString([tuple(q) for q in cols[n]["in3_route"]]) for n in ("CK_N", "CK_P")}
    se = [n for n in order if n not in ck_line]
    gaps = {}
    for i, a in enumerate(se):
        for b in se[i + 1:]:
            d = lines[a].distance(lines[b]) - w_se
            if d < 1.0:
                gaps[(a, b)] = d
                assert d >= min_gap - 1e-9, "%s / %s: %.3f mm" % (a, b, d)
        d = min(lines[a].distance(l) for l in ck_line.values()) - w_se / 2 - w_ck / 2
        if d < 1.2:
            gaps[(a, "CK")] = d
            assert d >= ncl["CMD_CK"]["pair_to_other_track_min"] - 1e-9, "%s to the CK pair: %.3f mm" % (a, d)

    C.save_board(board)
    n_trk = stage.count.get("track", 0)
    C.log(STAGE, "ripped up %d items; %d track items (10 finger stubs, 10 divider columns, 8 CA / CS traces with their tuning features, the CK pair), no vias added"
          % (removed, n_trk))
    C.log(STAGE, "bend model k_mitre %.4f + %.3f w, k_arc0 %.3f, k_arc1 %.2f, k_leg %.2f, plus the HFSS residual of each feature; leg pitch %.2f mm (gap %.2f mm)"
          % (model["k_mitre0"], model["k_mitre1"], model["k_arc0"], model["k_arc1"], model["k_leg"], pitch, pitch - w_se))
    for net, shape, plan, geo_x, el_x, corr in rows:
        C.log(STAGE, "  %-4s shift %+.3f mm, %d bump(s) x %.3f mm r %s: centre line +%.3f mm, electrical +%.3f mm (%.2f ps; bend correction %.2f ps)"
              % (net, shape["shift"], plan["bumps"], shape["amplitude"] if plan["bumps"] else 0.0, shape["radius"], geo_x, el_x,
                 el_x * d_in3, corr * d_in3))
    C.log(STAGE, "  gaps: " + ", ".join("%s/%s %.3f" % (a, b, d) for (a, b), d in gaps.items()))
    hp = g["per_net_host_plus_pkg_ps"]
    C.log(STAGE, "  planned (the board is measured again by check_pcb.py):  net   target  drawn   error | total (host + package + card)")
    for net in order:
        C.log(STAGE, "    %-5s %7.2f %7.2f %+6.2f | %7.2f" % (net, tgt[net], planned[net], planned[net] - tgt[net], hp[net] + planned[net]))


if __name__ == "__main__":
    main()

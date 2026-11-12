#!/usr/bin/env python3
"""Independent consistency check of design/netlist.json.

Nothing here imports the generator: every expectation is re-derived from the reference data in
design/ref (host pin map, host + package delays, U1 ball reference exported from the upstream
schematic) and from the stock KiCad libraries.  Exit status 0 = all checks passed.

Usage: python3 scripts/check_netlist.py [path/to/netlist.json]
"""
import csv
import json
import math
import os
import re
import sys
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REF = os.path.join(ROOT, "design", "ref")
KICAD_FP = "/usr/share/kicad/footprints"
KICAD_SYM = "/usr/share/kicad/symbols"
PROJECT_LIB = "lpddr5_testbed"
R0201 = "Resistor_SMD:R_0201_0603Metric"

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))


def par(a, b):
    return a * b / (a + b)


def ohms(v):
    v = v.strip()
    m = re.fullmatch(r"(\d+)R", v)
    if m:
        return float(m.group(1))
    m = re.fullmatch(r"(\d+)k(\d*)", v)
    if m:
        return float(m.group(1) + "." + (m.group(2) or "0")) * 1e3
    raise ValueError(v)


from collections import defaultdict as collections_dd      # noqa: E402



def check_power(d):
    """The power stage (floorplan.power, scripts/power_side.py), re-derived from the netlist alone: placements, wires, the layer-hop rule,
    connectivity of the nets that are wholly tracks, the sizing of the 1.2 V strip, RESET_n, and the plan-view crossings with the high-speed
    wires of the spec."""
    from shapely.geometry import LineString, Point, box, Polygon
    from shapely.ops import unary_union
    fp = d["floorplan"]
    pw = fp["power"]
    B = pw["build"]
    W, V = B["wires"], B["vias"]
    parts = {p["ref"]: p for p in d["parts"]}
    po, pl = fp["placements_other"], fp["placements"]
    fingers = {f["finger"]: f for f in d["fingers"]}
    j1 = parts["J1"]["pins"]
    u1 = parts["U1"]["pins"]
    cx, cy = fp["U1"]["centre"]
    rows = ["A", "B", "C", "D", "E", "F", "G", "H", "J", "K", "L", "M", "N", "P", "R", "T", "U", "V", "W", "Y", "AA"]

    def bxy(b):
        r = b.rstrip("0123456789")
        return (round(cx - (int(b[len(r):]) - 8) * 0.8, 3), round(cy + (rows.index(r) - 10) * 0.7, 3))
    # ---- placements
    back = {"U3", "U4", "C506", "C507", "C508", "C510", "R504", "R506", "R507", "R508", "R509", "R402", "R403"}
    pwr_refs = [r for r in po if r[0] in "CRUT" and r not in ("U1",) and not (r.startswith("C4") or r.startswith("R40") and r in ("R401", "R404", "R405") or r.startswith("RN"))]
    wrong = [r for r in pwr_refs if (po[r]["side"] == "B") != (r in back)]
    check("power stage, placement: U2 (VDD2H) and its parts on F.Cu; U3 (VDD1), U4 (VDDQ) with C506 / C507 / C508 / C510 / R504 / R506 .. R509 and the "
          "RESET_n parts R402 / R403 on B.Cu; the test pads on F.Cu", not wrong and po["U2"]["side"] == "F" and all(po[t]["side"] == "F" for t in ("TP1", "TP2", "TP3", "TP5")),
          str(wrong))

    # ---- wires: layers, angles, widths
    def turn_ok(path):
        for a, b, c in zip(path, path[1:], path[2:]):
            u, v = (b[0] - a[0], b[1] - a[1]), (c[0] - b[0], c[1] - b[1])
            if (u[0] * v[0] + u[1] * v[1]) / (math.hypot(*u) * math.hypot(*v)) < -1e-6:
                return False
        return True
    bad, necks = [], []
    cyd = po["U1"]["courtyard"]
    u1c = box(*cyd)
    pclass = set(d["rules"]["netclasses"]["POWER"]["nets"])
    for w in W:
        p = w["path"]
        for a, b in zip(p, p[1:]):
            dx, dy = abs(a[0] - b[0]), abs(a[1] - b[1])
            if not (dx < 1e-6 or dy < 1e-6 or abs(dx - dy) < 1e-6) or dx + dy < 1e-6:
                bad.append((w["net"], w["role"], "angle"))
        if not turn_ok(p):
            bad.append((w["net"], w["role"], "acute"))
        if w["layer"] not in ("F.Cu", "B.Cu"):
            bad.append((w["net"], w["role"], w["layer"]))
        if w["net"] in pclass and w["width"] < 0.25 - 1e-9 and not LineString(p).intersects(u1c):
            bad.append((w["net"], w["role"], "width %.2f" % w["width"]))
        if w["width"] < 0.15 - 1e-9:
            necks.append(w)
            if not (w["net"] == "VDD2H" and w["width"] >= 0.12 - 1e-9 and LineString(p).within(u1c) and LineString(p).length <= 1.6):
                bad.append((w["net"], w["role"], "width %.2f" % w["width"]))
    nlay = Counter(w["layer"] for w in W)
    check("power stage: %d centre lines, on F.Cu (%d) and B.Cu (%d) only - none on In3 or on a plane layer; every segment at 0 / 45 / 90 / 135 degrees, no bend "
          "sharper than 90 degrees; supply and GND tracks at least 0.25 mm wide outside the land field of U1, slow nets 0.15 mm; nothing thinner than 0.15 mm "
          "except ONE neck: the VDD2H link F6 - K6 inside the land field, 0.12 mm over %.2f mm, between the vias of H5 and H6"
          % (len(W), nlay["F.Cu"], nlay["B.Cu"], sum(LineString(w["path"]).length for w in necks)), not bad and len(W) > 60 and len(necks) == 1, str(bad[:6]))

    # ---- pads, balls, fingers, vias of every net; copper graph
    vip = set(fp["U1"]["via_in_pad"]["balls"])
    EP = {"U2.11": (0.825, 1.2), "U5.9": (0.45, 0.8), "U4.7": (0.4, 0.7)}       # exposed pads: half size (x, y) as placed
    pads = defaultdict(list)            # net -> [(name, layer or None for a through connection, (x, y), half size)]
    for ref, p in parts.items():
        if ref in ("J1", "U1"):
            continue
        q = po.get(ref) or pl[ref]
        pp = q["pads"] if "pads" in q else {"1": q["pad1"], "2": q["pad2"]}
        for pin, net in p["pins"].items():
            if net and pin in pp:
                hw = 0.5 if ref.startswith("TP") else EP.get("%s.%s" % (ref, pin), 0.12)
                pads[net].append(("%s.%s" % (ref, pin), q["side"] + ".Cu", tuple(pp[pin]), hw))
    for b, net in u1.items():
        if net:
            pads[net].append(("U1." + b, "F.Cu", bxy(b), 0.15))
            if b in vip:                                # a via-in-pad is the ball itself on the back: decoupling and links start on it
                pads[net].append(("U1.%s via-in-pad" % b, "B.Cu", bxy(b), 0.15))
    for f, fr in fingers.items():
        if j1[str(f)]:
            pads[j1[str(f)]].append(("J1.%d" % f, fr["side"] + ".Cu", (fr["x_mm"], 1.4), None))
    vias = defaultdict(list)
    for v in V:
        vias[v["net"]].append((v["x"], v["y"]))
    for v in fp["vias"] + fp["finger_vias"]:
        vias[v["net"]].append((v["x"], v["y"]))
    for b in vip:
        vias[u1[b]].append(bxy(b))
    other = defaultdict(list)           # wires of the earlier stages on the same nets (finger ties, U1 links, data-side GND links)
    for v in fp["finger_vias"]:
        for t in v["ties"]:
            other[v["net"]].append((t["layer"], t["width"], t["path"]))
    for l in fp["U1"]["f_cu_links"] + fp["U1"]["b_cu_ties"]:
        other[l["net"]].append((l["layer"], l["width"], l["path"]))
    for w in fp["data"]["wires"]:
        other[w["net"]].append((w["layer"], w["width"], w["path"]))

    def graph(net):
        """Union-find over the copper of one net.  Nodes: wires, pads, vias.  Returns (find, items)."""
        items = []
        for w in W:
            if w["net"] == net:
                items.append(("wire", w["layer"], LineString(w["path"]).buffer(w["width"] / 2), w["role"]))
        for lay, wd, path in other[net]:
            items.append(("wire", lay, LineString(path).buffer(wd / 2), "earlier stage"))
        for name, lay, xy, hw in pads[net]:
            if hw is None:
                fr = fingers[int(name.split(".")[1])]
                items.append(("pad", lay, box(fr["x_mm"] - 0.175, 0.15, fr["x_mm"] + 0.175, 2.70), name))
            elif isinstance(hw, tuple):
                items.append(("pad", lay, box(xy[0] - hw[0], xy[1] - hw[1], xy[0] + hw[0], xy[1] + hw[1]), name))
            else:
                items.append(("pad", lay, Point(xy).buffer(hw), name))
        for x, y in vias[net]:
            items.append(("via", None, Point(x, y).buffer(0.2), "via (%.3f, %.3f)" % (x, y)))
        par = list(range(len(items)))

        def find(i):
            while par[i] != i:
                par[i] = par[par[i]]
                i = par[i]
            return i
        for i in range(len(items)):
            for k in range(i + 1, len(items)):
                a, b_ = items[i], items[k]
                if (a[1] is None or b_[1] is None or a[1] == b_[1]) and a[2].intersects(b_[2]):
                    par[find(i)] = find(k)
        return find, items
    track_nets = ["RESET_N", "RESET_N_F", "PG_VDD2H", "PWR_EN", "VDD2H_FB", "VDD2H_SS", "VDDQ_FB", "DAC_CAP", "SCL", "SDA", "V3P3", "VDD1"]
    split, hops, loose = [], [], []
    for net in sorted({w["net"] for w in W}):
        find, items = graph(net)
        if net in track_nets:
            roots = {find(i) for i, it in enumerate(items) if it[0] == "pad"}
            if len(roots) != 1:
                split.append((net, len(roots)))
        # layer hop: a connected set of wires on one layer that touches no pad and is joined to two or more vias
        widx = [i for i, it in enumerate(items) if it[0] == "wire"]
        seen = set()
        for i in widx:
            if i in seen:
                continue
            comp, stack = {i}, [i]
            while stack:
                a = stack.pop()
                for k in widx:
                    if k not in comp and items[k][1] == items[a][1] and items[k][2].intersects(items[a][2]):
                        comp.add(k)
                        stack.append(k)
            seen |= comp
            lay = items[i][1]
            shp = unary_union([items[k][2] for k in comp])
            has_pad = any(it[0] == "pad" and it[1] == lay and it[2].intersects(shp) for it in items)
            nv = sum(1 for it in items if it[0] == "via" and it[2].intersects(shp))
            if not has_pad and nv >= 2:
                hops.append((net, lay, round(shp.area, 2)))
        # every wire end on something
        for w in W:
            if w["net"] != net:
                continue
            own = LineString(w["path"]).buffer(w["width"] / 2)
            for e in (w["path"][0], w["path"][-1]):
                cap = Point(e).buffer(w["width"] / 2 - 1e-4)
                if not any(it[2].intersects(cap) for it in items if (it[1] is None or it[1] == w["layer"]) and not it[2].equals(own)):
                    loose.append((net, w["role"], e))
    check("power stage: every centre line starts and ends on a pad, a ball, a finger or a via of its net, or on the copper of another line of that net on "
          "the same layer", not loose, str(loose[:6]))
    check("power stage, connectivity of the nets that are routed with tracks alone (%s): the copper of each is ONE piece that reaches every pin, ball and "
          "finger of the net (V1P2, VDD2H, VDDQ and GND also use islands and pours: the board gate measures them)" % ", ".join(track_nets),
          not split, str(split))
    check("NO LAYER HOP in the plan: on no net of the power stage does a stretch of track that touches no pad lie between two vias; every via of the stage "
          "stands at an end of its wire (beside a pad of the net on one of the two layers) or joins a plane", not hops, str(hops))
    # every via of the plan: on one layer at least, the copper it joins reaches a pad within 3 mm, or it is a plane via (GND, VDD2H, V1P2 island)
    far = []
    for v in V:
        if v["net"] in ("GND", "VDD2H"):
            continue
        near = [math.hypot(v["x"] - xy[0], v["y"] - xy[1]) for name, lay, xy, hw in pads[v["net"]] if hw is not None]
        if v["net"] == "V1P2" and v["y"] < 6.0:
            continue                        # the four island vias: In2 island below, the strip above
        if not near or min(near) > 3.0:
            far.append((v["net"], v["x"], v["y"], round(min(near), 2) if near else None))
    check("power stage: every via of a slow or supply wire stands within 3 mm of a pad of its net (the layer change is at the end of the wire, not in "
          "the middle of a run); %d vias of the plan, of which %d GND" % (len(V), sum(1 for v in V if v["net"] == "GND")), not far, str(far))

    # ---- the 1.2 V strip, re-derived
    sz = pw["v1p2_feed"]["sizing"]
    stp = pw["v1p2_feed"]["strip"]
    rho, t = 1.72e-5, next(l["thickness_mm"] for l in d["stackup"]["layers"] if l.get("layer") == "F.Cu")
    rs = rho / t * 1e3
    i_tot = sz["currents_a"]["V1P2"]
    l_strip = stp["y"][1] - max(v["y"] for v in V if v["net"] == "V1P2" and v["y"] < 6.0) + 0.35
    r_strip = rs * l_strip / stp["width"]
    spec_strip = sz["parts"]["strip (corridor)"]
    need_w = rs * l_strip * i_tot / 5.0
    a_mil2 = (stp["width"] / 0.0254) * (t / 0.0254)
    rise = (i_tot / (0.048 * a_mil2 ** 0.725)) ** (1 / 0.44)
    check("1.2 V strip sized by calculation (re-derived: %.3f mohm per square at %.3f mm copper): %.1f mm long, %.2f mm wide, %.2f A: %.2f mohm (spec %.2f), "
          "%.2f mV (spec %.2f), %.1f mW, +%.2f K (IPC-2221), %.1f A/mm2; a 5 mV budget needs %.2f mm (spec %.2f): chosen %.2f mm, no wider than 1.5; "
          "strip foot to U2 %.2f mV, with the In2 island and its vias %.1f mV worst (limit %.0f; the island is solved on the board by gate 9); 19 fingers at %.0f mA (limit 500), four island vias at %.0f mA"
          % (rs, t, l_strip, stp["width"], i_tot, r_strip, spec_strip["mohm"], r_strip * i_tot, spec_strip["mv"], r_strip * i_tot ** 2, rise, i_tot / (stp["width"] * t), need_w,
             sz["width_for_the_budget_mm"], stp["width"], sz["drop_fingers_to_u2_mv"], sz["drop_fingers_to_u2_total_mv"]["worst"], sz["drop_fingers_to_u2_total_mv"]["limit_mv"],
             1e3 * i_tot / 19, 1e3 * i_tot / 4),
          abs(r_strip - spec_strip["mohm"]) < 0.05 * r_strip and abs(need_w - sz["width_for_the_budget_mm"]) < 0.05 and need_w <= stp["width"] <= 1.5 + 1e-9
          and rise < 1.0 and sz["drop_fingers_to_u2_mv"] < 10.0 and sz["drop_fingers_to_u2_total_mv"]["worst"] <= sz["drop_fingers_to_u2_total_mv"]["limit_mv"]
          and abs(sz["drop_fingers_to_u2_total_mv"]["worst"] - (sz["drop_fingers_to_u2_mv"] + sz["island"]["expected_worst_finger_mv"] + sz["island_vias"]["drop_mv"])) < 0.11
          and i_tot / 19 < 0.5 and abs(i_tot - 0.77) < 1e-9
          and sz["currents_a"]["VDD2H"] == 0.52 and sz["currents_a"]["VDDQ"] == 0.225)

    # ---- plan-view crossings with the high-speed wires of the spec
    hs = []                                 # (net, layer, shape, what)
    for w in fp["data"]["wires"]:
        if w["group"] in ("WR", "RD", "RDQS", "WCK"):
            hs.append((w["net"], w["layer"], LineString(w["path"]), w["group"]))
    for n, c in fp["command"]["columns"].items() if isinstance(fp.get("command"), dict) and "columns" in fp["command"] else []:
        hs.append((n, "In3.Cu", LineString(c["in3_route"]), "CMD"))
    for n, st_ in fp["finger_stubs"].items():
        if not n.startswith(("VREF", "RESET")):
            hs.append((n, st_["layer"], LineString(st_["path"]), "STUB"))
    xs = defaultdict(list)
    for w in W:
        if w["net"] == "GND":
            ln = LineString(w["path"])
        else:
            ln = LineString(w["path"])
        for net, lay, sh, grp in hs:
            if lay != w["layer"] and ln.buffer(w["width"] / 2).intersects(sh):
                xs[(w["net"], w["role"])].append((net, lay, grp))
    reset_x = [q for (n, r), l in xs.items() if n.startswith("RESET") for q in l]
    by_net = defaultdict(set)
    lay_of = {(w["net"], w["role"]): w["layer"] for w in W}
    for k, l in xs.items():
        by_net["%s %s" % (k[0], lay_of[k])] |= {q[0] for q in l}
    exp = {k: set(v["crosses"]) for k, v in B["crossings_expected"].items()}
    check("1.2 V strip, plan view: the V1P2 copper on F.Cu crosses the eight In3 read lines and the two In3 legs of the RDQS pair (10 crossings, In1 and In2 "
          "solid GND between) and no other high-speed wire", by_net.get("V1P2 F.Cu") == exp["V1P2 F.Cu"] and len(exp["V1P2 F.Cu"]) == 10, str(sorted(by_net.get("V1P2 F.Cu", []))))
    check("RESET_n: F.Cu from ball H1 to ONE via, then B.Cu to R403 / R402 on the back at finger 232 (no via at the finger end); it crosses NO high-speed wire "
          "in plan view; no copper of it on In3", not reset_x and sum(1 for v in V if v["net"] == "RESET_N") == 1
          and {w["layer"] for w in W if w["net"] == "RESET_N_F"} == {"B.Cu"} and po["R402"]["side"] == "B" and po["R403"]["side"] == "B", str(reset_x[:6]))
    rs_b = unary_union([LineString(w["path"]).buffer(w["width"] / 2) for w in W if w["net"].startswith("RESET") and w["layer"] == "B.Cu"])
    rdqs_f = unary_union([LineString(w["path"]).buffer(w["width"] / 2) for w in fp["data"]["wires"] if w["group"] == "RDQS" and w["layer"] == "F.Cu"])
    read_b = unary_union([LineString(w["path"]).buffer(w["width"] / 2) for w in fp["data"]["wires"] if w["group"] == "RD" and w["layer"] != "In3.Cu"])
    vref = unary_union([LineString(w["path"]).buffer(w["width"] / 2) for w in fp["data"]["wires"] if w["group"] == "REF" and w["layer"] == "B.Cu"]
                       + [Point(v["x"], v["y"]).buffer(0.2) for v in fp["vias"] if v["net"].startswith("VREF")])
    d_rdqs, d_read, d_vref = rs_b.distance(rdqs_f), rs_b.distance(read_b), rs_b.distance(vref)
    check("RESET_n on B.Cu keeps, in plan view, %.2f mm from the RDQS pair on F.Cu (>= 0.80), %.2f mm from the nearest read finger track (>= 0.80) and "
          "%.2f mm from every VREF track and via on its layer (>= 0.90); it passes under the F.Cu rail of VREF_RD once, static under DC, with every plane between" % (d_rdqs, d_read, d_vref), d_rdqs >= 0.80 and d_read >= 0.80 and d_vref >= 0.90)
    got = {k: v for k, v in by_net.items() if v}
    print("        plan-view crossings of a high-speed centre line of the spec by a wire of the power stage, by net and layer:")
    for k in sorted(got):
        print("          %-10s x %s" % (k, ", ".join(sorted(got[k]))))
    check("power stage, crossings with high-speed wires (plan view, centre lines of the spec): exactly the ones the spec lists with their reason - "
          + "; ".join("%s x %d" % (k, len(v)) for k, v in exp.items()) + " = %d in all; no slow net (RESET_n, PG, I2C, enable, feedback) and no GND link crosses "
          "a high-speed wire; the board is counted by gate 8" % sum(len(v) for v in exp.values()),
          got == exp and all(v.get("why") and v.get("between") for v in B["crossings_expected"].values()),
          "not listed: %s; listed but absent: %s" % ({k: sorted(got[k] - exp.get(k, set())) for k in got if got[k] - exp.get(k, set())},
                                                    {k: sorted(exp[k] - got.get(k, set())) for k in exp if exp[k] - got.get(k, set())}))
    # no power / slow wire through a resistor group of the data side
    rg = []
    for g in fp["data"]["resistor_groups"]:
        xs_, ys_ = [], []
        for r in g["parts"]:
            q = pl.get(r) or po[r]
            for k in ("pad1", "pad2"):
                xs_.append(q[k][0])
                ys_.append(q[k][1])
        rg.append((g["name"], box(min(xs_) - 0.2, min(ys_) - 0.23, max(xs_) + 0.2, max(ys_) + 0.23)))
    thr = [(w["net"], w["role"], n) for w in W for n, bx in rg if LineString(w["path"]).intersects(bx)]
    check("power stage: no supply or slow wire runs through a resistor group of the data side or of the command field", not thr, str(thr[:6]))
    # pour rules
    names = {r["name"] for r in d["rules"]["spacing_rules"]}
    og = pw["outer_layer_ground"]
    check("outer layers: NO ground flood, no thieving and no stitching grid (floorplan.power.gnd_pours is empty, outer_layer_ground.local_gnd_pours is empty, "
          "the spec has no stitching rule and no pour keep-out): as on the upstream board the outer layers carry tracks, pads and local power pours only, "
          "referenced to In1 / In4; no DRC rule of the spec is written for a GND pour",
          pw["gnd_pours"] == [] and og["local_gnd_pours"] == [] and "stitching" not in pw and "pour_keepouts" not in pw
          and not any(n.startswith("gnd_pour") for n in names) and og["policy"] and og["gnd_pads"])
    low = [z for z in B["pours"] if "lower" in z["name"] or "centre" in z["name"]]
    cmd_g = [bxy(b) for b, n in u1.items() if n and re.fullmatch(r"CA\d|CS0|CK_[PN]", n)]
    d_cmd = min(Polygon(z["outline"]).distance(Point(c)) - 0.2 for z in low for c in cmd_g)
    check("power pours and the command bus: the outlines of the lower pours end at y = %.2f under rows A .. F (tabs over G1 / G2 and G14 / G15 only); nearest pour OUTLINE to the "
          "B.Cu pad of a command via-in-pad %.3f mm (>= 0.30)" % (max(q[1] for z in low for q in z["outline"] if 16.4 < q[0] < 25.0), d_cmd),
          d_cmd >= 0.30 - 1e-9 and len(low) == 4)
    check("power pours: six outlines, all on B.Cu under U1 (VDDQ: lower east in two parts beside the WCK pair, upper east, lower west, upper west; VDD2H centre); DRC rules hold them %.2f mm from every high-speed track, %.2f mm from "
          "high-speed pads and vias and %.2f mm from every via of the command bus; minimum width %.2f mm"
          % (pw["rules"]["power_pour_to_high_speed_track_mm"], 0.15, pw["rules"]["power_pour_to_command_via_mm"], pw["rules"]["power_pour_min_width_mm"]),
          {"pour_to_high_speed_track_F_Cu", "pour_to_high_speed_track_B_Cu", "pour_to_high_speed_pad_F_Cu", "pour_to_high_speed_pad_B_Cu",
           "pour_to_command_via_B_Cu"} <= names and pw["rules"]["power_pour_to_high_speed_track_mm"] >= 0.30
          and pw["rules"]["power_pour_to_command_via_mm"] >= 0.30 and pw["rules"]["power_pour_min_width_mm"] >= 0.20
          and len(B["pours"]) == 6 and all(z["layer"] == "B.Cu" and z["net"] != "GND" for z in B["pours"]))
    # every GND pad whose copper is in the plan (this stage's wires, the finger ties, the U1 links, the data-side links) hangs on a GND via
    find, items = graph("GND")
    has_via = {find(i) for i, it in enumerate(items) if it[0] == "via"}
    cmd_parts = set(fp["placements"])                    # the 0201 field parts: their GND links are drawn and checked by s20_cmd / s30_data (gates 6 / 7)
    lonely = [it[3] for i, it in enumerate(items) if it[0] == "pad" and find(i) not in has_via and it[3].split(".")[0] not in cmd_parts]
    n_pad = sum(1 for it in items if it[0] == "pad" and it[3].split(".")[0] not in cmd_parts)
    check("GND pads: each of the %d GND pads, balls and fingers outside the 0201 fields hangs on a GND via through a via in the pad or a track of the plan "
          "(no pad relies on a pour); %d GND wires and %d GND vias in the plan of the power stage"
          % (n_pad, sum(1 for w in W if w["net"] == "GND"), sum(1 for v in V if v["net"] == "GND")), not lonely, str(lonely))

def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "design", "netlist.json")
    d = json.load(open(path))
    for k in ("meta", "stackup", "rules", "parts", "no_connect", "fingers", "groups", "floorplan", "bom_summary"):
        check("top-level key '%s' present" % k, k in d)
    parts = {p["ref"]: p for p in d["parts"]}
    check("part references unique", len(parts) == len(d["parts"]), "%d parts" % len(d["parts"]))

    # ------------------------------------------------------------------ nets
    net_pins = defaultdict(list)
    empty = set()
    for p in d["parts"]:
        for pin, net in p["pins"].items():
            if net == "":
                empty.add((p["ref"], pin))
            else:
                net_pins[net].append((p["ref"], pin))
    nc = [(n["ref"], n["pin"]) for n in d["no_connect"]]
    check("no_connect list has no duplicates", len(nc) == len(set(nc)), "%d entries" % len(nc))
    check("every pin without a net is listed in no_connect, and nothing else is", empty == set(nc),
          "unlisted %s / listed but connected %s" % (sorted(empty - set(nc))[:5], sorted(set(nc) - empty)[:5]))
    single = sorted(n for n, pins in net_pins.items() if len(pins) < 2)
    check("no net with a single pin", not single, "single-pin nets: %s" % single)
    names_ok = all(re.fullmatch(r"[A-Z0-9_]+", n) for n in net_pins)
    check("net names are plain upper case without hierarchy", names_ok,
          "%d nets" % len(net_pins))

    # KiCad pairs nets by a trailing _P / _N (digits and underscores after the letter are skipped)
    def dp_partner(n):
        s = n.rstrip("0123456789_")
        if s and s[-1] in "PN":
            return s[:-1] + ("N" if s[-1] == "P" else "P") + n[len(s):]
        return None
    pairs_found = sorted({tuple(sorted((n, dp_partner(n)))) for n in net_pins if dp_partner(n) in net_pins})
    want_pairs = [("CK_F_N", "CK_F_P"), ("CK_N", "CK_P"), ("RDQS_N", "RDQS_P"), ("WCK_F_N", "WCK_F_P"), ("WCK_N", "WCK_P")]
    check("KiCad recognises exactly the three pairs and the finger-side nets of CK and WCK as differential pairs (_P / _N at the end of the name; RDQS has no "
          "finger-side net since its series resistors were removed), and no other net pair",
          pairs_found == want_pairs, str(pairs_found))

    # ------------------------------------------------------------------ parts: generic
    bad = []
    for p in d["parts"]:
        for k in ("ref", "value", "mpn", "manufacturer", "description", "footprint", "symbol", "dnp", "sheet", "pins"):
            if k not in p or p[k] in ("", None) and k != "dnp":
                bad.append((p.get("ref"), k))
        if p["sheet"] not in ("connector", "lpddr5", "command", "write", "read", "power"):
            bad.append((p["ref"], "sheet"))
        if ":" not in p["footprint"] or ":" not in p["symbol"]:
            bad.append((p["ref"], "lib:name"))
        if p.get("in_bom", True) and (p["mpn"].startswith("N/A") or len(p["mpn"]) < 5):
            bad.append((p["ref"], "mpn"))
    check("every part has value, MPN, manufacturer, footprint lib:name, symbol lib:name and a valid sheet", not bad, str(bad[:8]))
    two = [p["ref"] for p in d["parts"] if re.fullmatch(r"[RC]\d+", p["ref"]) and sorted(p["pins"]) != ["1", "2"]]
    check("every resistor and capacitor has exactly pins 1 and 2", not two, str(two))
    arr = [p["ref"] for p in d["parts"] if p["ref"].startswith("RN") and sorted(p["pins"], key=int) != [str(i) for i in range(1, 9)]]
    check("every resistor array has pins 1..8", not arr, str(arr))
    shorted = [p["ref"] for p in d["parts"] if re.fullmatch(r"[RC]\d+", p["ref"]) and p["pins"]["1"] == p["pins"]["2"]]
    check("no two-pin part with both pins on one net", not shorted, str(shorted))

    # ------------------------------------------------------------------ parts: against the stock libraries
    fp_bad, fp_seen, proj_fp = [], 0, set()
    for p in d["parts"]:
        lib, name = p["footprint"].split(":")
        if lib == PROJECT_LIB:
            f = os.path.join(ROOT, "lib", PROJECT_LIB + ".pretty", name + ".kicad_mod")
            if not os.path.exists(f):
                proj_fp.add(name)
                continue
        else:
            f = os.path.join(KICAD_FP, lib + ".pretty", name + ".kicad_mod")
        if not os.path.exists(f):
            fp_bad.append((p["ref"], "missing " + p["footprint"]))
            continue
        pads = set(re.findall(r'\(pad "([^"]+)"', open(f).read()))
        fp_seen += 1
        if pads != set(p["pins"]):
            fp_bad.append((p["ref"], "pads %s vs pins %s" % (sorted(pads), sorted(p["pins"]))))
    check("footprints (stock and project library) exist and their pad names equal the part's pins", not fp_bad and not proj_fp,
          "%d parts checked; project footprints missing: %s; problems: %s" % (fp_seen, sorted(proj_fp), fp_bad[:5]))
    sym_bad, sym_seen, proj_sym, cache = [], 0, set(), {}
    for p in d["parts"]:
        lib, name = p["symbol"].split(":")
        if lib == PROJECT_LIB:
            proj_sym.add(name)
            continue
        f = os.path.join(KICAD_SYM, lib + ".kicad_sym")
        if not os.path.exists(f):
            sym_bad.append((p["ref"], "missing library " + lib))
            continue
        if f not in cache:
            cache[f] = open(f).read()
        t = cache[f]
        nm = name
        nums = None
        for _ in range(4):  # follow "extends"
            i = t.find('\n\t(symbol "%s"' % nm)
            if i < 0:
                break
            j = t.find('\n\t(symbol "', i + 10)
            blk = t[i:j if j > 0 else len(t)]
            ext = re.search(r'\(extends "([^"]+)"\)', blk)
            if ext:
                nm = ext.group(1)
                continue
            nums = set(re.findall(r'\(number "([^"]+)"', blk))
            break
        if nums is None:
            sym_bad.append((p["ref"], "missing symbol " + p["symbol"]))
            continue
        sym_seen += 1
        if nums != set(p["pins"]):
            sym_bad.append((p["ref"], "symbol pins %s vs part pins %s" % (sorted(nums), sorted(p["pins"]))))
    check("stock symbols exist and their pin numbers equal the part's pins", not sym_bad,
          "%d parts checked; project symbols still to be drawn: %s; problems: %s" % (sym_seen, sorted(proj_sym), sym_bad[:5]))

    # ------------------------------------------------------------------ J1
    j1 = parts["J1"]["pins"]
    check("J1 has pads 1..260 exactly once", sorted(j1, key=int) == [str(i) for i in range(1, 261)], "%d pads" % len(j1))
    pm = {int(r["finger"]): r for r in csv.DictReader(open(os.path.join(REF, "zcu104_sodimm_pinmap.csv")))}
    td = {int(r["finger"]): r for r in csv.DictReader(open(os.path.join(REF, "zcu104_finger_total_delay.csv")))}
    fingers = {r["finger"]: r for r in d["fingers"]}
    check("fingers table has rows 1..260 exactly once", sorted(fingers) == list(range(1, 261)), "%d rows" % len(d["fingers"]))
    mism = []
    for f in range(1, 261):
        r, h = fingers[f], pm[f]
        if r["net"] != j1[str(f)]:
            mism.append((f, "net differs from J1 pad"))
        if r["side"] != ("F" if f % 2 else "B") or h["side"] != ("front" if f % 2 else "back"):
            mism.append((f, "side"))
        if abs(r["x_mm"] - float(h["x_mm"])) > 1e-6:
            mism.append((f, "x"))
        if (r["fpga_ball"] or "") != h["fpga_ball"]:
            mism.append((f, "ball"))
        if (str(r["bank"]) if r["bank"] else "") != h["bank"]:
            mism.append((f, "bank"))
        if (r["bytegroup"] or "") != h["byte_nibble"]:
            mism.append((f, "byte group"))
        if (r["pn"] or "") != (h["pn"] if h["pn"] in ("P", "N") else ""):
            mism.append((f, "P/N"))
        hp = h["partner_finger"].split()[0] if h["partner_finger"][:1].isdigit() else ""
        if (str(r["partner_finger"]) if r["partner_finger"] else "") != hp:
            mism.append((f, "partner"))
        want = float(td[f]["total_mid_ps"]) if f in td else None
        if r["host_plus_pkg_delay_ps"] != want:
            mism.append((f, "delay"))
        if f in td and (td[f]["fpga_ball"] != h["fpga_ball"] or td[f]["bank"] != h["bank"]):
            mism.append((f, "delay table ball/bank"))
    check("every finger matches the ZCU104 pin map (side, x, FPGA ball, bank, byte group, P/N, partner, delay)", not mism, str(mism[:6]))
    vdd = [f for f in pm if pm[f]["host_net"] == "VCC1V2"]
    gnd = [f for f in pm if pm[f]["host_net"] == "GND"]
    check("all 19 host 1.2 V (VDD) fingers are on V1P2, none on GND", len(vdd) == 19 and all(j1[str(f)] == "V1P2" for f in vdd))
    check("all 94 host VSS fingers are on GND", len(gnd) == 94 and all(j1[str(f)] == "GND" for f in gnd))
    wrong = [f for f in pm if j1[str(f)] in ("GND", "V1P2") and pm[f]["host_net"] not in ("GND", "VCC1V2")]
    check("no host signal or other rail finger is tied to GND or V1P2", not wrong, str(wrong))
    check("finger 255 (host 3.3 V) is V3P3; 2.5 V, VTT and VREFCA fingers are not tied to any card rail",
          j1["255"] == "V3P3" and all(j1[str(f)] == "" for f in (257, 258, 259, 164)))
    used_sig = {f: j1[str(f)] for f in pm if pm[f]["fpga_ball"] and j1[str(f)]}
    check("unused FPGA fingers are left open", all(j1[str(f)] == "" or f in used_sig for f in pm if pm[f]["fpga_ball"]),
          "%d FPGA fingers used, %d left open" % (len(used_sig), sum(1 for f in pm if pm[f]["fpga_ball"]) - len(used_sig)))

    # ------------------------------------------------------------------ bank plan
    def fin(net):
        return [f for f in range(1, 261) if j1[str(f)] == net]

    def fside(n):          # finger-side net of a divider-driven net; a pair leg keeps _P / _N at the end of its name
        m = re.fullmatch(r"(W?CK)_([PN])", n)
        if m:
            return "%s_F_%s" % m.groups()
        return n + ("_WF" if n.startswith("DQ") else "_F")

    cmd = ["CA%d" % i for i in range(7)] + ["CS0", "CK_P", "CK_N"]
    cf = {n: fin(fside(n)) for n in cmd}
    check("each command net has exactly one finger", all(len(v) == 1 for v in cf.values()), str({k: v for k, v in cf.items() if len(v) != 1}))
    cf = {n: v[0] for n, v in cf.items()}
    check("all ten command fingers are in bank 65, byte group T0 (fingers 66-84)",
          all(pm[f]["bank"] == "65" and pm[f]["byte_nibble"].startswith("T0") and 66 <= f <= 84 for f in cf.values()),
          str(sorted(cf.values())))
    check("CK_P on finger 76 (P), CK_N on finger 74 (N), same I/O pair",
          cf["CK_P"] == 76 and cf["CK_N"] == 74 and pm[76]["pn"] == "P" and pm[74]["pn"] == "N" and pm[76]["partner_finger"].startswith("74"))
    wr = ["DQ%d" % i for i in range(8)]
    wf = {n: fin(fside(n)) for n in wr}
    wf.update({n: fin(fside(n)) for n in ("WCK_P", "WCK_N")})
    check("each write net has exactly one finger", all(len(v) == 1 for v in wf.values()))
    wf = {n: v[0] for n, v in wf.items()}
    # the pin map of the data side, as decided with the user (comb, 'T at the divider'): stated here, not read from the spec
    WANT_WR = {"DQ4": 143, "DQ5": 144, "DQ1": 145, "DQ0": 150, "DQ3": 151, "DQ2": 156, "DQ6": 157, "DQ7": 161}
    # read end without crossings: the In3 lines bottom -> top (DQ7, DQ6, DQ2, DQ3, DQ0, DQ1, DQ5, DQ4) on the read fingers west -> east
    WANT_RD = {"DQ7": (182, 183), "DQ6": (186, 187), "DQ2": (190, 191), "DQ3": (194, 195), "DQ0": (204, 203), "DQ1": (208, 207), "DQ5": (212, 211), "DQ4": (215, 216)}
    check("write DQ on the bank-64 fingers 143, 144, 145, 150, 151, 156, 157, 161 above the comb (DQ4, DQ5, DQ1, DQ0, DQ3, DQ2, DQ6, DQ7: the lowest F.Cu line takes "
          "the westmost column), all FPGA pins of bank 64",
          {n: wf[n] for n in wr} == WANT_WR and all(pm[f]["bank"] == "64" and pm[f]["fpga_ball"] for f in wf.values()),
          " ".join("%s:%d" % (n, wf[n]) for n in sorted(wr, key=lambda n: wf[n])))
    gw_ = {g["name"]: g for g in d["groups"]}["WR_WCK"]
    check("WCK on the host CK0 pair (fingers 137 = P pin, 139 = N pin), inverted: WCK_N (WCK_c, D5) on 137, WCK_P (WCK_t, E4) on 139 - the "
          "B.Cu pair enters its two divider columns with the E4 leg on the east; the inversion is stated for the gateware in the group and "
          "in the function of both fingers",
          wf["WCK_P"] == 139 and wf["WCK_N"] == 137 and pm[137]["pn"] == "P" and pm[139]["pn"] == "N" and pm[137]["partner_finger"].startswith("139")
          and "inverted" in gw_["polarity"] and all("inverse" in fingers[f]["function"] or "inverted" in fingers[f]["function"] for f in (137, 139)),
          "WCK_N %d, WCK_P %d" % (wf["WCK_N"], wf["WCK_P"]))
    rf = {n: fin(n) for n in wr}
    check("each read DQ has exactly one finger, on the net of the ball itself (no part between the ball and the read finger; the write finger is on DQx_WF "
          "behind the 240 ohm series resistor)", all(len(v) == 1 for v in rf.values()) and not any(n.endswith("_RF") for n in net_pins),
          str({k: v for k, v in rf.items() if len(v) != 1}))
    rf = {n: v[0] for n, v in rf.items()}
    ok = True
    det = []
    for n, f in rf.items():
        h = pm[f]
        nf = int(h["partner_finger"].split()[0]) if h["partner_finger"][:1].isdigit() else None
        good = (h["bank"] == "66" and h["byte_nibble"][:2] in ("T3", "T2", "T1") and h["pn"] == "P" and nf is not None
                and j1[str(nf)] == "VREF_N%s" % n[2:] and pm[nf]["pn"] == "N" and (f, nf) == WANT_RD[n])
        ok &= good
        det.append("%s P%d/N%s" % (n, f, nf))
    check("read DQ on P pins of complete pairs in bank 66 (T3 / T2 / T1), N partner on the matching VREF_Nx, the pairs as planned (182 .. 215, in the order of "
          "the In3 bundle: DQ7, DQ6, DQ2, DQ3, DQ0, DQ1, DQ5, DQ4 from west to east)", ok, ", ".join(det))
    gq_ = {g["name"]: g for g in d["groups"]}["RD_RDQS"]
    check("RDQS on the host DQS6 pair in bank 66 (a QBC strobe pair, routed as a pair on the host), EAST of every read DQ finger, inverted: RDQS_P (RDQS_t, B3) on "
          "finger 219 (the N pin), RDQS_N (RDQS_c, C4) on finger 221 (the P pin) - the pair comes down between the two fingers with the C4 leg on the east; "
          "the inversion is stated for the gateware",
          fin("RDQS_P") == [219] and fin("RDQS_N") == [221] and pm[221]["pn"] == "P" and pm[219]["pn"] == "N" and pm[221]["bank"] == "66"
          and pm[221]["partner_finger"].startswith("219") and "QBC" in pm[221]["fpga_pin_name"] and "QBC" in pm[219]["fpga_pin_name"]
          and abs(float(pm[221]["host_main_width_mil"]) - 6.5) < 1e-9 and abs(float(pm[219]["host_main_width_mil"]) - 6.5) < 1e-9
          and all(fingers[f]["x_mm"] < fingers[219]["x_mm"] for f in rf.values())
          and "inverts" in gq_["polarity"] and all("inverse" in fingers[f]["function"] for f in (219, 221)))
    check("RESET_n on finger 232 (back side, bank 66 T0L, AP11: a static LVCMOS12 output with VSS fingers 230 / 234 beside it on the back), "
          "no longer on finger 229 or 108",
          fin("RESET_N_F") == [232] and pm[232]["bank"] == "66" and j1["108"] == "" and j1["229"] == "" and pm[232]["fpga_ball"] == "AP11"
          and fingers[232]["side"] == "B" and j1["230"] == "GND" and j1["234"] == "GND")
    b66 = sorted(j1[str(f)] for f in pm if pm[f]["bank"] == "66" and j1[str(f)])
    check("bank 66 carries inputs only (read DQ, references, RDQS) and the one static output RESET_n",
          all(re.fullmatch(r"DQ[0-7]", n) or n in ("RDQS_P", "RDQS_N", "RESET_N_F") or n.startswith("VREF_N") for n in b66), "%d fingers used" % len(b66))
    b65 = [j1[str(f)] for f in pm if pm[f]["bank"] == "65" and j1[str(f)]]
    check("bank 65 carries the ten command outputs and nothing else", sorted(b65) == sorted(fside(n) for n in cmd))

    # ------------------------------------------------------------------ U1
    rows = ["A", "B", "C", "D", "E", "F", "G", "H", "J", "K", "L", "M", "N", "P", "R", "T", "U", "V", "W", "Y", "AA"]
    balls = ["%s%d" % (r, c) for r in rows for c in range(1, 16)]
    u1 = parts["U1"]["pins"]
    check("U1 has all 315 balls exactly once", sorted(u1) == sorted(balls), "%d balls" % len(u1))
    ref = json.load(open(os.path.join(REF, "u1_ball_reference.json")))["balls"]
    supply = {"VSS": "GND", "VDD2H": "VDD2H", "VDD2L": "VDD2H", "VDDQ": "VDDQ", "VDD1": "VDD1"}
    bad = [b for b in balls if ref[b]["supply"] and u1[b] != supply[ref[b]["supply"]]]
    cnt = Counter(ref[b]["supply"] for b in balls if ref[b]["supply"])
    check("every supply ball matches the upstream schematic ball by ball (VSS 102, VDD2H 61, VDD2L 16, VDDQ 40, VDD1 4)",
          not bad and cnt == Counter({"VSS": 102, "VDD2H": 61, "VDD2L": 16, "VDDQ": 40, "VDD1": 4}), "%s %s" % (dict(cnt), bad[:5]))
    expect = {}
    for b in balls:
        fn = re.sub(r"_%s$" % b, "", ref[b]["pin_function"])
        if ref[b]["supply"]:
            continue
        if fn in ("NC", "RFU"):
            e = ""
        elif fn == "RESET_n":
            e = "RESET_N"
        elif fn == "ZQ_A":
            e = "ZQ"
        elif fn.endswith("_A"):
            s = fn[:-2]
            e = {"CK_t": "CK_P", "CK_c": "CK_N", "CS0": "CS0", "CS1": "GND", "WCK0_t": "WCK_P", "WCK0_c": "WCK_N",
                 "RDQS0_t": "RDQS_P", "RDQS0_c": "RDQS_N", "DMI0": "GND", "DMI1": "GND", "WCK1_t": "WCK1_T",
                 "WCK1_c": "WCK1_C", "RDQS1_t": "", "RDQS1_c": ""}.get(s, s)       # CAx, DQ0-7 keep their names
            if re.fullmatch(r"DQ(8|9|1[0-5])", s):                                  # the unused byte is open (no pull-down)
                e = ""
        else:  # channel B
            s = fn[:-2]
            if s.startswith("CA") or s.startswith("CS") or s in ("CK_t", "WCK0_t", "WCK1_t"):
                e = "GND"
            elif s in ("CK_c", "WCK0_c", "WCK1_c"):
                e = "VDDQ"
            else:
                e = ""
        expect[b] = e
    bad = [(b, ref[b]["pin_function"], u1[b], expect[b]) for b in expect if u1[b] != expect[b]]
    check("every signal ball carries the net expected from its pin function (channel A byte 0 wired, the rest tied off or open)",
          not bad, str(bad[:5]))
    for net, ball in (("CK_P", "H9"), ("CK_N", "J9"), ("CS0", "H7"), ("RESET_N", "H1"), ("ZQ", "H15"), ("WCK_P", "E4"),
                      ("WCK_N", "D5"), ("RDQS_P", "B3"), ("RDQS_N", "C4"), ("DQ0", "D1"), ("DQ7", "F5"), ("CA0", "G4"), ("CA6", "G12")):
        if u1.get(ball) != net:
            check("U1 ball %s is %s" % (ball, net), False, u1.get(ball))
    # tie-offs that rely on an adjacent ball (no part, no via)
    def nb(b):
        r = b.rstrip("0123456789")
        c = int(b[len(r):])
        i = rows.index(r)
        out = []
        for di, dc in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            if 0 <= i + di < 21 and 1 <= c + dc <= 15:
                out.append("%s%d" % (rows[i + di], c + dc))
        return out
    direct = [b for b in expect if expect[b] in ("GND", "VDDQ") and b != "N7"]
    lonely = [b for b in direct if not any(ref[x]["supply"] and supply[ref[x]["supply"]] == expect[b] for x in nb(b))]
    check("every directly tied-off ball has an orthogonally adjacent ball of the same rail (F.Cu link, no via)", not lonely,
          "%d direct ties; without neighbour: %s (N7 = CK_c_B needs a via to the VDDQ pour)" % (len(direct), lonely))

    # ------------------------------------------------------------------ divider topology and levels
    def two_pin(a, b):
        out = []
        for p in d["parts"]:
            if p["ref"][0] == "R" and not p["ref"].startswith("RN") and not p["dnp"] and set(p["pins"].values()) == {a, b}:
                out.append(p)
        return out

    def divider(fnet, bnet):
        s, sh = two_pin(fnet, bnet), two_pin(bnet, "GND")
        if len(s) != 1 or len(sh) != 1:
            return None
        return ohms(s[0]["value"]), ohms(sh[0]["value"])

    def corner(r1, r2, rs=(26.0, 36.0, 44.0), v=(1.14, 1.20, 1.26), tol=0.01):
        lo = v[0] * r2 * (1 - tol) / (rs[2] + r1 * (1 + tol) + r2 * (1 - tol))
        nom = v[1] * r2 / (rs[1] + r1 + r2)
        hi = v[2] * r2 * (1 + tol) / (rs[0] + r1 * (1 - tol) + r2 * (1 + tol))
        imax = v[2] / (rs[0] + (r1 + r2) * (1 - tol)) * 1e3
        return lo, nom, hi, imax

    lev = []
    ok = True
    for n in ["CA%d" % i for i in range(7)] + ["CK_P", "CK_N", "WCK_P", "WCK_N"]:
        dv = divider(fside(n), n)
        if not dv:
            ok = False
            lev.append(n + ": divider missing")
            continue
        lo, nom, hi, imax = corner(*dv)
        ok &= hi <= 0.470 and lo >= 0.345 + 0.020 and imax <= 10.0
        if n in ("CA0", "CK_P", "WCK_P"):
            lev.append("%s %g/%g ohm: %.3f / %.3f / %.3f V, %.1f mA max" % (n, dv[0], dv[1], lo, nom, hi, imax))
    check("CA / CK / WCK dividers: high level <= 0.470 V (VDDQ min) in the fastest corner, >= 0.365 V in the slowest, pin current <= 10 mA",
          ok, "; ".join(lev))
    dv = divider("WCK_F_P", "WCK_P")
    if dv:
        r1, r2 = dv
        odt = 1.14 * par(r2 * 0.99, 40) / (44 + r1 * 1.01 + par(r2 * 0.99, 40))
        check("WCK differential swing with the 40 ohm WCK ODT forced during CBT >= 0.30 V p-p", 2 * odt >= 0.30, "%.3f V p-p worst case" % (2 * odt))
    dv = divider("CS0_F", "CS0")
    if dv:
        lo, nom, hi, imax = corner(*dv)
        check("CS0 divider: 0.55 V (ViHPD) + 50 mV <= high level <= VDD2H(min) + 0.2 V, pin current <= 10 mA",
              lo >= 0.60 and hi <= 1.032 + 0.2 and imax <= 10.0, "CS0 %g/%g ohm: %.3f / %.3f / %.3f V, %.1f mA max" % (dv[0], dv[1], lo, nom, hi, imax))
    else:
        check("CS0 divider present", False)
    ok, lev = True, []
    for i in range(8):
        n = "DQ%d" % i
        dv = divider(fside(n), n)
        # no series part in the read branch: the net of the ball carries the series resistor, the shunt, the ball and the read finger
        on_net = sorted(net_pins[n])
        want_net = sorted([("J1", str(rf[n])), ("U1", [b for b in u1 if u1[b] == n][0])] + [(q["ref"], pin) for q in d["parts"]
                          if q["ref"] in ("R%d" % (201 + i), "R%d" % (211 + i)) for pin, nn in q["pins"].items() if nn == n])
        if not dv or on_net != want_net:
            ok = False
            lev.append(n + ": write divider missing, or something else on the net: %s" % on_net)
            continue
        lo, nom, hi, imax = corner(*dv)
        ok &= hi <= 0.470 and lo >= 0.30 and imax <= 10.0 and 120.0 <= dv[1] <= 200.0
        if i == 0:
            vddq = (0.4876, 0.4996, 0.5116)
            f = (0.766, 0.772, 0.792)
            vh = [vddq[k] * f[k] for k in range(3)]
            lev.append("DQ %g/%g ohm: write %.3f / %.3f / %.3f V; read high into %g ohm %.3f / %.3f / %.3f V"
                       % (dv[0], dv[1], lo, nom, hi, dv[1], vh[0], vh[1], vh[2]))
    r22 = sorted(q["ref"] for q in d["parts"] if q["value"] == "22R" and q["sheet"] in ("read", "write"))
    check("DQ write dividers: level <= 0.470 V in the fastest corner; shunt 120..200 ohm (read load); the net DQx is exactly ball, series resistor, "
          "shunt and read finger - no 22 ohm tap (R301..R308, R311, R312 removed: no 22 ohm part on the read or write sheet, no R30x / R31[12])",
          ok and not r22 and not any(re.fullmatch(r"R30[1-8]|R31[12]", r_) for r_ in parts), "; ".join(lev) + "; 22 ohm parts left: %s" % r22)
    dv = divider("RESET_N_F", "RESET_N")
    if dv:
        lo, nom, hi, imax = corner(*dv, rs=(26.0, 60.0, 86.0))
        check("RESET_n: 0.8 x VDD2H(max) <= high level <= VDD2H(min) + 0.2 V; pull-down holds reset with a 100 uA pull-up",
              lo >= 0.8 * 1.064 and hi <= 1.032 + 0.2 and 100e-6 * (dv[0] + dv[1]) <= 0.2 * 1.032,
              "%g/%g ohm: %.3f / %.3f / %.3f V; %.3f V at 100 uA" % (dv[0], dv[1], lo, nom, hi, 100e-6 * (dv[0] + dv[1])))
    else:
        check("RESET_n network present", False)
    zq = two_pin("ZQ", "VDDQ")
    check("ZQ: 240 ohm to VDDQ (not to GND)", len(zq) == 1 and ohms(zq[0]["value"]) == 240.0 and not two_pin("ZQ", "GND"))

    # ------------------------------------------------------------------ read reference
    ok = True
    for i in range(8):
        r = two_pin("VREF_N%d" % i, "VREF_RD")
        ok &= len(r) == 1 and ohms(r[0]["value"]) == 39.0 and len(net_pins["VREF_N%d" % i]) == 2
    c100 = [p for p in d["parts"] if p["ref"][0] == "C" and set(p["pins"].values()) == {"VREF_RD", "GND"} and p["value"] == "100n"]
    check("each of the 8 N fingers: 39 ohm to VREF_RD and nothing else on the stub; 8 x 100 nF on VREF_RD", ok and len(c100) == 8,
          "%d x 100 nF" % len(c100))
    ra, rb, rc = two_pin("DAC_OUT", "VREF_RD"), two_pin("VDDQ", "VREF_RD"), two_pin("VREF_RD", "GND")
    if len(ra) == len(rb) == len(rc) == 1:
        ra, rb, rc = ohms(ra[0]["value"]), ohms(rb[0]["value"]), ohms(rc[0]["value"])
        g = 1 / ra + 1 / rb + 1 / rc
        v0 = 0.4996 * rc / (rb + rc)
        vmin, vmax, vbad = (0.4996 / rb) / g, (1.815 / ra + 0.4996 / rb) / g, (3.3 / ra + 0.4996 / rb) / g
        check("reference network: power-up default 0.17..0.21 V, DAC range covers 0.14..0.42 V, <= 1.40 V even if the DAC is set to 3.3 V, source <= 120 ohm",
              0.17 <= v0 <= 0.21 and vmin <= 0.14 and vmax >= 0.42 and vbad <= 1.40 and 1 / g <= 120 and par(rb, rc) <= 120,
              "default %.4f V; DAC 0..1.815 V -> %.3f..%.3f V; at 3.3 V %.3f V; source %.0f ohm (DAC on) / %.0f ohm (DAC high-Z)"
              % (v0, vmin, vmax, vbad, 1 / g, par(rb, rc)))
        vn = 0.195
        vh = (0.4876 * 0.766, 0.5116 * 0.792)
        vicm1 = [(v + vn) / 2 for v in vh]
        check("read levels inside MIPI_DPHY_DCI HS (VID >= 70 mV, VICM 70..330 mV, pin <= 460 mV) and SLVS_400_18 (VID >= 140 mV)",
              min(vh) - vn >= 0.140 and max(vicm1) <= 0.330 and vn / 2 >= 0.070 and max(vh) <= 0.460 and vn >= 0.140,
              "one: VID %.0f..%.0f mV, VICM %.0f..%.0f mV; zero: VID -%.0f mV, VICM %.1f mV" %
              (1e3 * (vh[0] - vn), 1e3 * (vh[1] - vn), 1e3 * vicm1[0], 1e3 * vicm1[1], 1e3 * vn, 1e3 * vn / 2))
    else:
        check("reference network Ra / Rb / Rc present", False)
    u5 = parts.get("U5", {}).get("pins", {})
    check("DAC53401: VDD = V3P3, SCL/SDA on fingers 253/254, A0 = GND (address 0x48), FB tied to OUT, CAP capacitor present",
          u5.get("6") == "V3P3" and u5.get("2") == "SCL" and u5.get("3") == "SDA" and j1["253"] == "SCL" and j1["254"] == "SDA"
          and u5.get("1") == "GND" and u5.get("7") == u5.get("8") == "DAC_OUT" and len(net_pins[u5.get("4")]) == 2)
    spd = set(range(0x50, 0x58)) | set(range(0x30, 0x38)) | set(range(0x18, 0x20))
    check("DAC address 0x48 and broadcast 0x47 do not collide with DDR4 SPD / page / temperature-sensor addresses", not ({0x48, 0x47} & spd))

    # ------------------------------------------------------------------ regulators
    def fb(out, fbnet, vref):
        a, b = two_pin(out, fbnet), two_pin(fbnet, "GND")
        if len(a) != 1 or len(b) != 1:
            return None
        return vref * (1 + ohms(a[0]["value"]) / ohms(b[0]["value"])), ohms(a[0]["value"]), ohms(b[0]["value"])
    u2, u3, u4 = parts["U2"]["pins"], parts["U3"]["pins"], parts["U4"]["pins"]
    v = fb("VDD2H", u2["8"], 0.8)
    check("U2 TPS74801: IN = V1P2, BIAS = V3P3, EN = VDD1, VDD2H 1.01..1.12 V with 1.5 % total error (and with the 2.5 % of the legacy die), R2 <= 4.99 k",
          v and u2["1"] == u2["2"] == "V1P2" and u2["4"] == "V3P3" and u2["5"] == "VDD1" and u2["9"] == u2["10"] == "VDD2H"
          and u2["6"] == u2["11"] == "GND" and 1.01 <= v[0] * 0.985 and v[0] * 1.015 <= 1.12 and 1.01 <= v[0] * 0.975 and v[0] * 1.025 <= 1.12 and v[2] <= 4990,
          "VDD2H = %.4f V nominal (%.3f..%.3f)" % (v[0], v[0] * 0.985, v[0] * 1.015) if v else "")
    v = fb("VDDQ", u4["5"], 0.4)
    check("U4 MIC47050: IN = V1P2, BIAS = V3P3, EN = power good of U2, VDDQ 0.47..0.57 V with 2.4 % total error, R1 <= 10 k",
          v and u4["3"] == "V1P2" and u4["1"] == "V3P3" and u4["6"] == u2["3"] and u4["4"] == "VDDQ" and u4["2"] == u4["7"] == "GND"
          and 0.47 <= v[0] * 0.976 and v[0] * 1.024 <= 0.57 and v[1] <= 10e3 and len(two_pin("V3P3", u2["3"])) == 1,
          "VDDQ = %.4f V nominal (%.3f..%.3f)" % (v[0], v[0] * 0.976, v[0] * 1.024) if v else "")
    check("U3 TLV70018: IN = V3P3, OUT = VDD1, EN pulled to V1P2 (VDD1 cannot rise before the host 1.2 V rail)",
          u3["1"] == "V3P3" and u3["5"] == "VDD1" and u3["2"] == "GND" and len(two_pin("V1P2", u3["3"])) == 1)
    check("power-up order by construction: VDD1 enabled by V1P2, VDD2H enabled by VDD1, VDDQ enabled by VDD2H power good",
          u2["5"] == "VDD1" and u4["6"] == u2["3"] and len(two_pin("V1P2", u3["3"])) == 1)
    caps = Counter()
    for p in d["parts"]:
        if p["ref"][0] == "C" and "GND" in p["pins"].values():
            other = [n for n in p["pins"].values() if n != "GND"][0]
            caps[other] += 1
    check("decoupling present on every rail (VDD2H >= 12, VDDQ >= 8, VDD1 >= 4, V1P2 >= 3, V3P3 >= 4)",
          caps["VDD2H"] >= 12 and caps["VDDQ"] >= 8 and caps["VDD1"] >= 4 and caps["V1P2"] >= 3 and caps["V3P3"] >= 4,
          str({k: caps[k] for k in ("VDD2H", "VDDQ", "VDD1", "V1P2", "V3P3", "VREF_RD")}))
    tps = {p["pins"]["1"] for p in d["parts"] if p["ref"].startswith("TP")}
    check("test points on VDD1, VDD2H, VDDQ, VREF_RD and GND only (none on high-speed nets)", tps == {"VDD1", "VDD2H", "VDDQ", "VREF_RD", "GND"}, str(sorted(tps)))

    # ------------------------------------------------------------------ planarity / ordering
    fp = d["floorplan"]
    cx, cy = fp["U1"]["centre"]

    def bxy(b):
        r = b.rstrip("0123456789")
        c = int(b[len(r):])
        return (cx - (c - 8) * 0.8, cy + (rows.index(r) - 10) * 0.7)   # rotation 180: row A toward the fingers
    check("U1 rotation 180 deg, body inside the card and above the front keep-out",
          fp["U1"]["rotation_deg"] == 180 and cy - 7.5 >= 5.10 and cy + 7.5 <= 30.0 - 1.0 and cx - 6.2 >= 3.0, "centre (%.3f, %.2f)" % (cx, cy))
    cols = fp["command"]["columns"]
    by_f = sorted(cmd, key=lambda n: fingers[cf[n]]["x_mm"])
    by_c = sorted(cmd, key=lambda n: cols[n]["x"])
    ballof = {n: [b for b in balls if u1[b] == n][0] for n in cmd}
    by_b = sorted(cmd, key=lambda n: (round(bxy(ballof[n])[0], 3), n != "CK_N"))
    check("command fan-out is planar: finger order = divider column order = ball column order (left to right)",
          by_f == by_c == by_b, " ".join(by_f))
    step = [round(cols[b]["x"] - cols[a]["x"], 3) for a, b in zip(by_c, by_c[1:])]
    dx = max(abs(cols[n]["x"] - fingers[cf[n]]["x_mm"]) for n in cmd)
    off = [round(abs(cols[n]["x"] - bxy(ballof[n])[0]), 3) for n in cmd]
    check("command divider columns sit on the 0.8 mm BGA column channels (ball 0.4 mm to the side), finger fan <= 1.5 mm",
          all(s == 0.8 for s in step) and all(o == 0.4 for o in off) and dx <= 1.5, "fan max %.2f mm" % dx)
    check("all command balls are in rows G / H / J of channel A", all(ballof[n][0] in "GHJ" for n in cmd))
    # ------------------------------------------------------------------ U1 via plan (audit: straps and omitted vias)
    vip = fp["U1"]["via_in_pad"]
    omitted = vip["omitted"]
    routed = {"CA%d" % i for i in range(7)} | {"CS0", "CK_P", "CK_N", "WCK_P", "WCK_N", "RDQS_P", "RDQS_N", "WCK1_T", "WCK1_C", "ZQ"}
    own_via = set(vip.get("strapped_with_own_via", []))            # strapped inputs with a via-in-pad of their own (A4)
    via_balls = ({b for b in balls if ref[b]["supply"] or u1[b] in routed or b == "N7"} | own_via) - set(omitted)
    check("A4 (DMI0_A, tied to VSS) has a via-in-pad of its own and C5 (VSS) has its via back: the F.Cu links A4 - B4 and C5 - D6 are gone "
          "(the DQ4 and DQ5 necks use those row channels)",
          own_via == {"A4"} and u1["A4"] == "GND" and "C5" not in omitted and "C5" in via_balls
          and not any({l_["from"], l_["to"]} in ({"A4", "B4"}, {"C5", "D6"}) for l_ in fp["U1"]["f_cu_links"]))
    brow = lambda b: rows.index(b.rstrip("0123456789"))
    bcol = lambda b: int(b[len(b.rstrip("0123456789")):])

    def adjacent(a, b, diag):
        dr, dc = abs(brow(a) - brow(b)), abs(bcol(a) - bcol(b))
        return (dr + dc == 1) or (diag and dr == 1 and dc == 1)

    bad = []
    for b, o in omitted.items():
        if not ref[b]["supply"] or u1[b] != o["net"]:
            bad.append((b, "not a supply ball / net"))
        seen, todo, fed = {b}, [b], False
        while todo:
            x = todo.pop()
            for y in (omitted[x]["f_cu_link_to"] if x in omitted else []):
                if u1.get(y) != u1[b] or not adjacent(x, y, True):
                    bad.append((b, "link %s-%s is not between touching balls of one net" % (x, y)))
                elif y in via_balls:
                    fed = True
                elif y not in seen:
                    seen.add(y)
                    todo.append(y)
        if not fed:
            bad.append((b, "no path to a ball with a via"))
    check("every U1 ball whose via-in-pad is omitted is linked on F.Cu (orthogonal or diagonal neighbour, same net) to a ball that keeps its via",
          not bad, "%d omitted: %s; problems: %s" % (len(omitted), " ".join(sorted(omitted)), bad))
    trim_ = vip.get("in3_pad_trim", {})
    ev_ = os.path.join(ROOT, "design", "ref", "hfss_ca0_data_corner.json")
    evd = json.load(open(ev_)) if os.path.exists(ev_) else {}
    gap_c4 = abs(bxy("C4")[0] - fp["command"]["columns"]["CA0"]["x"]) - trim_.get("inner_diameter", 0.40) / 2 - fp["command"]["columns"]["CA0"]["in3_width"] / 2
    check("command bus protection: the In3 pad of the via-in-pads of C4 / B3 (the RDQS pair leaves them on In3) is trimmed to 0.35 mm (annular ring 0.10 mm, "
          "the board minimum): the CA0 trace passes the C4 pad at %.3f mm (class clearance %.2f; 0.125 with the 0.40 mm pad). The HFSS result is on file "
          "(design/ref/hfss_ca0_data_corner.json) with a passing verdict for the geometry as built: CA0 delay %+.3f ps (limit %.1f), RDQS -> CA0 coupling "
          "%.2f %% of the RDQS swing at the fastest edge (limit %.0f %%); the command bus is not re-tuned"
          % (gap_c4, d["rules"]["netclasses"]["CMD_SE"]["clearance"], evd.get("verdict", {}).get("ca0_delay_change_ps", float("nan")),
             evd.get("verdict", {}).get("ca0_delay_limit_ps", float("nan")), evd.get("verdict", {}).get("coupling_percent", float("nan")),
             evd.get("verdict", {}).get("coupling_limit_percent", float("nan"))),
          trim_.get("balls") == ["C4", "B3"] and trim_.get("inner_diameter") == 0.35 and (0.35 - vip["size"]["drill"]) / 2 >= d["rules"]["board"]["min_annular_ring"] - 1e-9
          and abs(gap_c4 - 0.15) < 1e-6 and abs(trim_.get("ca0_trace_to_c4_pad_mm", 0) - gap_c4) < 1e-6 and evd.get("verdict", {}).get("pass") is True
          and evd.get("geometry_as_built", {}).get("c4_in3_pad_mm") == 0.35 and abs(evd["verdict"]["ca0_delay_change_ps"]) <= 0.3
          and evd["verdict"]["coupling_percent"] <= 1.0 and fp["data"]["command_zone_exception"]["net"] == "RDQS_N")
    lonely = [b for b in direct if b not in own_via and not any(ref[x]["supply"] and supply[ref[x]["supply"]] == expect[b] and x in via_balls for x in nb(b))]
    check("every strapped ball (CS1_A, DMI0_A, DMI1_A, channel-B inputs) touches a ball of its rail that keeps its via", not lonely,
          "%d straps; without a via-carrying neighbour: %s" % (len(direct), lonely))
    check("CK corridor: no via-in-pad on A9..G9, and the DQ / strapped / NC balls carry none",
          not ({"%s9" % r for r in "ABCDEFG"} & via_balls) and not ({b for b in balls if u1[b].startswith("DQ") or u1[b] == ""} & via_balls)
          and not ((set(direct) - own_via) & via_balls))

    # ------------------------------------------------------------------ geometry helpers
    def seg_pt(p, a, b):
        ax, ay, bx, by = a[0], a[1], b[0], b[1]
        dx, dy = bx - ax, by - ay
        l2 = dx * dx + dy * dy
        t = 0.0 if l2 == 0 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / l2))
        return math.hypot(p[0] - ax - t * dx, p[1] - ay - t * dy)

    def cross(a, b, c, e):     # proper or touching intersection of segments ab and ce
        def orient(p, q, r):
            v = (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
            return 0 if abs(v) < 1e-12 else (1 if v > 0 else -1)
        o1, o2, o3, o4 = orient(a, b, c), orient(a, b, e), orient(c, e, a), orient(c, e, b)
        if o1 != o2 and o3 != o4:
            return True
        return min(seg_pt(c, a, b), seg_pt(e, a, b), seg_pt(a, c, e), seg_pt(b, c, e)) < 1e-9

    def seg_seg(a, b, c, e):
        if cross(a, b, c, e):
            return 0.0
        return min(seg_pt(c, a, b), seg_pt(e, a, b), seg_pt(a, c, e), seg_pt(b, c, e))

    def plen_(pl):
        return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pl, pl[1:]))

    def segs(pl):
        return list(zip(pl, pl[1:]))

    def poly_poly(p, q):
        return min(seg_seg(a, b, c, e) for a, b in segs(p) for c, e in segs(q))

    def seg_rect(a, b, r):     # distance from segment ab to the axis-parallel rectangle r = (x0, y0, x1, y1)
        x0, y0, x1, y1 = r
        if x0 <= a[0] <= x1 and y0 <= a[1] <= y1:
            return 0.0
        c = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
        return min(seg_seg(a, b, c[i], c[(i + 1) % 4]) for i in range(4))

    def rect_rect_gap(r, s):
        dx = max(r[0] - s[2], s[0] - r[2], 0.0)
        dy = max(r[1] - s[3], s[1] - r[3], 0.0)
        return math.hypot(dx, dy) if (dx > 0 or dy > 0) else -min(r[2] - s[0], s[2] - r[0], r[3] - s[1], s[3] - r[1])

    def fp_geom(lib_name):     # pad offset, pad (along, across), courtyard half (along, across) from the stock footprint file
        lib, name = lib_name.split(":")
        t = open(os.path.join(KICAD_FP, lib + ".pretty", name + ".kicad_mod")).read()
        m = re.search(r'\(pad "2"[^\n]*\n\s*\(at ([-\d.]+) ([-\d.]+)[^\n]*\n\s*\(size ([\d.]+) ([\d.]+)', t)
        off, plen, pwid = abs(float(m.group(1))), float(m.group(3)), float(m.group(4))
        cx = [abs(float(v)) for blk in re.findall(r'\(fp_line\s*\(start ([-\d.]+) [-\d.]+\)\s*\(end ([-\d.]+) [-\d.]+\)(?:(?!\(fp_line).)*?F\.CrtYd', t, re.S) for v in blk]
        cy = [abs(float(v)) for blk in re.findall(r'\(fp_line\s*\(start [-\d.]+ ([-\d.]+)\)\s*\(end [-\d.]+ ([-\d.]+)\)(?:(?!\(fp_line).)*?F\.CrtYd', t, re.S) for v in blk]
        return off, plen, pwid, max(cx), max(cy)

    place = fp["placements"]
    fvias = fp["vias"]
    VR = 0.20                  # radius of the 0.40 mm via pads (field vias and via-in-pads)
    geom = {}
    pads = []                  # (ref, pin, net, side, rect)
    court = {}                 # ref -> (side, rect)
    bad = []
    for r_, pl in place.items():
        if r_ not in parts:
            bad.append((r_, "unknown part"))
            continue
        fpn = parts[r_]["footprint"]
        if fpn not in geom:
            geom[fpn] = fp_geom(fpn)
        off, plen, pwid, chl, chw = geom[fpn]
        if pl["orientation"] != "vertical" or abs(abs(pl["pad1"][1] - pl["y"]) - off) > 1e-6 or abs(abs(pl["pad2"][1] - pl["y"]) - off) > 1e-6 \
                or pl["pad1"][0] != pl["x"] or pl["pad2"][0] != pl["x"] or (pl["pad1"][1] - pl["y"]) * (pl["pad2"][1] - pl["y"]) >= 0:
            bad.append((r_, "pad positions do not match the footprint"))
        court[r_] = (pl["side"], (pl["x"] - chw, pl["y"] - chl, pl["x"] + chw, pl["y"] + chl))
        for pin in ("1", "2"):
            x, y = pl["pad" + pin]
            pads.append((r_, pin, parts[r_]["pins"][pin], pl["side"], (x - pwid / 2, y - plen / 2, x + pwid / 2, y + plen / 2)))
    want = {"R%d" % i for i in list(range(101, 121)) + list(range(201, 221)) + [309, 310] + list(range(321, 329))} | {"C%d" % i for i in range(321, 329)}
    check("all 58 divider, RDQS load and reference-network parts have a placement that matches their footprint",
          not bad and set(place) == want, "%d placed; problems: %s; missing: %s" % (len(place), bad[:4], sorted(want - set(place))[:6]))
    fnets = set(j1.values()) - {"", "GND", "V1P2", "V3P3", "SCL", "SDA"} - set(wr) - {"RDQS_P", "RDQS_N"}      # DQx / RDQS: ball-side nets that also reach a read finger
    turned = []
    for r_, pl in place.items():
        for pin in ("1", "2"):
            if parts[r_]["pins"][pin] in fnets and pl["pad" + pin][1] > pl["pad" + ("2" if pin == "1" else "1")][1]:
                turned.append(r_)
            if parts[r_]["pins"][pin] in fnets and fingers[[f for f in range(1, 261) if j1[str(f)] == parts[r_]["pins"][pin]][0]]["side"] != pl["side"]:
                turned.append(r_ + " (wrong face)")
    check("every placed part that connects to a finger has its finger-net pad on the finger side (south) and sits on the finger's face",
          not turned, str(turned))
    small = [r_ for r_ in place if re.fullmatch(r"R(1|2)\d\d|R309|R310", r_) and "R_0201" not in parts[r_]["footprint"]]
    check("every divider and RDQS load resistor is 0201", not small, str(small))
    small2 = [r_ for r_ in place if re.fullmatch(r"[RC]32\d", r_) and "_0201_" not in parts[r_]["footprint"]]
    check("the 16 parts of the reference networks (8 x 39 ohm, 8 x 100 nF) are 0201", not small2 and len([r_ for r_ in place if re.fullmatch(r"[RC]32\d", r_)]) == 16, str(small2))

    # keep-out rule areas of the edge footprint (card frame: y = -Y of the footprint)
    edge = open(os.path.join(ROOT, "lib", PROJECT_LIB + ".pretty", "DDR4_SODIMM_260_Edge.kicad_mod")).read()
    zones = []
    for ln in edge.splitlines():
        m = re.search(r'\(name "(SODIMM_no_components[^"]*)"\).*\(polygon \(pts (.*)\)\)\)', ln)
        if m:
            zones.append((m.group(1), [(float(a), -float(b)) for a, b in re.findall(r'\(xy ([-\d.]+) ([-\d.]+)\)', m.group(2))]))

    def in_poly(p, poly):
        ins = False
        for i in range(len(poly)):
            (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % len(poly)]
            if (y1 > p[1]) != (y2 > p[1]) and p[0] < x1 + (p[1] - y1) * (x2 - x1) / (y2 - y1):
                ins = not ins
        return ins

    def rect_hits_poly(r, poly):
        e = 1e-6
        x0, y0, x1, y1 = r[0] + e, r[1] + e, r[2] - e, r[3] - e
        c = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
        if any(in_poly(q, poly) for q in c) or any(x0 < q[0] < x1 and y0 < q[1] < y1 for q in poly):
            return True
        return any(cross(c[i], c[(i + 1) % 4], poly[j], poly[(j + 1) % len(poly)]) for i in range(4) for j in range(len(poly)))

    hits = [(r_, z) for r_, (sd, rc) in court.items() for z, poly in zones if rect_hits_poly(rc, poly)]
    lowest = min(rc[1] for sd, rc in court.values())
    zarea = sorted(round(abs(sum(q[i][0] * q[(i + 1) % len(q)][1] - q[(i + 1) % len(q)][0] * q[i][1] for i in range(len(q)))) / 2, 2) for z, q in zones)
    check("no courtyard of a placed part enters a 'footprints not allowed' rule area of the edge footprint (fingers, key, sides)",
          len(zones) == 3 and sorted(len(q) for z, q in zones) == [6, 6, 8] and zarea == [69.85, 69.85, 279.2] and not hits, "%d rule areas; lowest courtyard edge y = %.2f; inside: %s" % (len(zones), lowest, hits[:6]))
    tall = [r_ for r_, (sd, rc) in court.items() if rc[1] < 5.10 and not re.search(r"_0201_|_0402_", parts[r_]["footprint"])]
    check("only 0201 / 0402 parts (<= 0.8 mm tall) between y = 4.00 and 5.10", not tall, str(tall))
    names = sorted(court)
    ov = [(a, b) for i, a in enumerate(names) for b in names[i + 1:] if court[a][0] == court[b][0] and rect_rect_gap(court[a][1], court[b][1]) < -1e-6]
    check("courtyards of the placed parts do not overlap on either face", not ov, str(ov[:6]))
    pp = [(a[0], b[0], round(rect_rect_gap(a[4], b[4]), 3)) for i, a in enumerate(pads) for b in pads[i + 1:]
          if a[3] == b[3] and a[2] != b[2] and rect_rect_gap(a[4], b[4]) < 0.09 - 1e-6]
    check("pads of different nets on one face are at least 0.09 mm apart", not pp, str(pp[:6]))

    # field vias: against each other and against the pads of both faces (through vias)
    vv = []
    for i, a in enumerate(fvias):
        for b in fvias[i + 1:]:
            dd = math.hypot(a["x"] - b["x"], a["y"] - b["y"])
            if dd - a["drill"] / 2 - b["drill"] / 2 < 0.25 - 1e-6 or (a["net"] != b["net"] and dd - a["diameter"] / 2 - b["diameter"] / 2 < 0.10 - 1e-6):
                vv.append((a["net"], b["net"], round(dd, 3)))
    check("field vias: hole to hole >= 0.25 mm, pads of different nets >= 0.10 mm apart", not vv and all(v["diameter"] == 0.40 and v["drill"] == 0.20 for v in fvias),
          "%d vias; problems: %s" % (len(fvias), vv[:6]))

    def circ_rect(c, rad, r):
        dx = max(r[0] - c[0], c[0] - r[2], 0.0)
        dy = max(r[1] - c[1], c[1] - r[3], 0.0)
        return math.hypot(dx, dy) - rad
    vp = [(v["net"], pd[0], round(circ_rect((v["x"], v["y"]), VR, pd[4]), 3)) for v in fvias for pd in pads
          if v["net"] != pd[2] and circ_rect((v["x"], v["y"]), VR, pd[4]) < 0.09 - 1e-6]
    check("field vias (through) keep 0.09 mm from every pad of another net on both faces", not vp, str(vp[:6]))
    netvias = defaultdict(list)
    for v in fvias:
        netvias[v["net"]].append(v)
    far = []
    for r_, pin, net, sd, rc in pads:
        if net in fnets or net == "VREF_RD":
            continue                                    # finger-side pads and the reference bus are reached by tracks, not by a field via
        if net in ("RDQS_P", "RDQS_N") or (r_ in ("R209", "R210") and pin == "2"):
            continue                                    # RDQS loads hang on the B.Cu legs; WCK series to node via is a checked F.Cu trace
        if r_ in ("R309", "R310") and net == "GND":
            continue                                    # the RDQS loads go to the finger via of the VSS pair beside the drop (checked in the data section)
        if re.fullmatch(r"C32\d", r_) and net == "GND":
            continue                                    # the 100 nF of a reference network goes to the via of its VSS finger (checked in the data section)
        c = ((rc[0] + rc[2]) / 2, (rc[1] + rc[3]) / 2)
        dmin = min([math.hypot(c[0] - v["x"], c[1] - v["y"]) for v in netvias[net]] or [99])
        if dmin > 1.25:
            far.append((r_, pin, net, round(dmin, 2)))
    check("every node-side and GND pad of a placed part has a via of its net within 1.25 mm", not far, str(far[:6]))

    u1v = [(b, u1[b], bxy(b)) for b in sorted(via_balls)]
    yA = bxy("A1")[1]

    def samples(pl, step=0.05):
        out = []
        for p, q in segs(pl):
            n_ = max(1, int(math.hypot(q[0] - p[0], q[1] - p[1]) / step))
            out += [(p[0] + (q[0] - p[0]) * i / n_, p[1] + (q[1] - p[1]) * i / n_) for i in range(n_ + 1)]
        return out

    # ------------------------------------------------------------------ command field (audit: fence vias, tuning room, stubs)
    crow = fp["command"]["rows_y"]
    ncl0 = d["rules"]["netclasses"]
    ccl = ncl0["CMD_SE"]["clearance"]
    w = ncl0["CMD_SE"]["layers"]["In3.Cu"]["width"]
    worst = (9.9, None)
    for n in cmd:
        if n in ("CK_P", "CK_N"):
            continue
        xc = cols[n]["x"]
        a, b = (xc, crow["node_via"]), (xc, bxy(ballof[n])[1] - 0.4)
        rt_ = [tuple(q_) for q_ in cols[n]["in3_route"]]
        near_ = lambda p_, q_: abs(p_[0] - q_[0]) < 1e-6 and abs(p_[1] - q_[1]) < 1e-6      # noqa: E731
        if not near_(rt_[0], a) or not near_(rt_[-2], b) or not near_(rt_[-1], bxy(ballof[n])) or any(abs(q_[0] - xc) > 1e-9 for q_ in rt_[:-1]) \
                or not any(abs(q_[1] - fp["command"]["ck_split"]["courtyard_break_y"]) < 1e-9 for q_ in rt_):
            worst = (-1.0, "%s: in3_route is not the straight channel route with a vertex at the U1 courtyard edge" % n)
        for v in fvias:
            if v["net"] != n:
                worst = min(worst, (seg_pt((v["x"], v["y"]), a, b) - VR - w / 2, "%s to the %s via at (%.3f, %.2f)" % (n, v["net"], v["x"], v["y"])))
        for bn, net, c in u1v:
            if net != n:
                worst = min(worst, (seg_pt(c, a, b) - VR - w / 2, "%s to U1 via %s" % (n, bn)))
    check("In3 command traces run straight up their channels with at least the class clearance to every fence via and via-in-pad",
          worst[0] >= ccl - 1e-6 and ccl >= d["rules"]["board"]["min_clearance"], "smallest clearance %.3f mm (%s); class clearance %.2f" % (worst[0], worst[1], ccl))
    xck = bxy("H9")[0]
    ckw = ncl0["CMD_CK"]["layers"]["In3.Cu"]
    half = ckw["gap"] / 2 + ckw["width"]
    near = [round(abs(v["x"] - xck) - VR - half, 3) for v in fvias if v["y"] > crow["node_via"] + 0.1 and abs(v["x"] - xck) < 1.0 and v["y"] < bxy("A1")[1]]
    near += [round(abs(c[0] - xck) - VR - half, 3) for bn, net, c in u1v if brow(bn) < brow("H9") and abs(c[0] - xck) < 1.0]
    check("CK pair corridor on ball column 9: no fence via and no via-in-pad closer than the class limit to the pair",
          near and min(near) >= ncl0["CMD_CK"]["pair_to_via_pad_min"] - 1e-6 and abs(abs(cols["CK_P"]["x"] - xck) - abs(cols["CK_N"]["x"] - xck)) < 1e-6,
          "smallest pad-to-pair distance %.3f mm" % (min(near) if near else -1))
    from shapely.geometry import LineString, Point
    from shapely.ops import unary_union
    gcmd = {g["name"]: g for g in d["groups"]}["CMD"]
    fnet_of = dict(zip(gcmd["nets"], gcmd["finger_side_nets"])).get
    tz = fp["command"]["in3_tuning_zone"]
    tg = gcmd["tuning"]
    bm = d["rules"]["bend_model"]
    trims = {n: gcmd["per_net_target_card_delay_ps"][n] - gcmd["per_net_min_card_delay_estimate_ps"][n] for n in cmd}
    lay_ = d["stackup"]["layers"]
    h_in3 = min(lay_[i_ + k_]["thickness_mm"] for i_, l_ in enumerate(lay_) if l_.get("layer") == "In3.Cu" for k_ in (-1, 1))
    # mid-copper heights of the stackup (the board gate measures the barrels between them)
    zmid, zz_ = {}, 0.0
    for l_ in lay_:
        if l_["type"] == "soldermask":
            continue
        if l_["type"] == "copper":
            zmid[l_["layer"]] = zz_ + l_["thickness_mm"] / 2
        zz_ += l_["thickness_mm"]
    dly = d["rules"]["delay_ps_per_mm"]

    def turn_of(p0, p1, p2):
        u_, v_ = (p1[0] - p0[0], p1[1] - p0[1]), (p2[0] - p1[0], p2[1] - p1[1])
        return math.acos(max(-1.0, min(1.0, (u_[0] * v_[0] + u_[1] * v_[1]) / (math.hypot(*u_) * math.hypot(*v_)))))

    def electrical(pts, wd, radius_at=None):
        """Own implementation of rules.bend_model (not scripts/tuning.py): (centre line as drawn, electrical length, turn in rad,
        leg coupling mm, radii fit) of the polyline pts; radius_at = {vertex index: corner radius}."""
        radius_at = radius_at or {}
        geo = sum(math.hypot(b_[0] - a_[0], b_[1] - a_[1]) for a_, b_ in zip(pts, pts[1:]))
        turned, tan_len, short_ = 0.0, [0.0] * len(pts), 0.0
        for i_ in range(1, len(pts) - 1):
            t_ = turn_of(pts[i_ - 1], pts[i_], pts[i_ + 1])
            turned += t_
            if i_ in radius_at and t_ > 1e-9:
                r_ = radius_at[i_]
                tan_len[i_] = r_ * math.tan(t_ / 2)
                geo += r_ * t_ - 2 * tan_len[i_]
                short_ += (bm["k_arc0"] * wd + bm["k_arc1"] * (r_ - wd / math.log((r_ + wd / 2) / (r_ - wd / 2)))) * t_
            else:
                short_ += (bm["k_mitre0"] + bm["k_mitre1"] * wd) * t_
        fit = all(tan_len[i_] + tan_len[i_ + 1] <= math.hypot(pts[i_ + 1][0] - pts[i_][0], pts[i_ + 1][1] - pts[i_][1]) + 1e-9 for i_ in range(len(pts) - 1))
        legs_ = [(pts[i_][0], pts[i_ + 1][0], pts[i_][1]) for i_ in range(len(pts) - 1)
                 if i_ in radius_at and i_ + 1 in radius_at and abs(pts[i_][1] - pts[i_ + 1][1]) < 1e-9]
        leg_mm = 0.0
        for (a0, a1, ya), (b0, b1, yb) in zip(legs_, legs_[1:]):
            ov = min(max(a0, a1), max(b0, b1)) - max(min(a0, a1), min(b0, b1))
            if (a1 - a0) * (b1 - b0) < 0 and ov > 0:
                leg_mm += 2 * bm["k_leg"] * math.exp(-math.pi * (abs(yb - ya) - wd) / bm["leg_decay_mm"]) * ov
        return geo, geo - short_ - leg_mm, turned, leg_mm, fit

    def feature_pts(sh):
        """Vertices of a tuning feature (own construction from the shape dict) and the indices of its rounded corners."""
        xc, (ya, yb) = sh["x"], sh["y"]
        s_, a_ = sh["shift"], sh["amplitude"]
        pts, rnd = [(xc, ya)], {}
        xp = xc + s_
        if abs(s_) > 1e-9:
            pts += [(xc, sh["shift_y"][0]), (xp, sh["shift_y"][0] + abs(s_))]
        for k_, y_ in enumerate(sh["rows"]):
            for x_ in (xp + sh["pattern"][k_] * a_, xp + sh["pattern"][k_ + 1] * a_):
                rnd[len(pts)] = sh["radius"]
                pts.append((x_, y_))
        if abs(s_) > 1e-9:
            pts += [(xp, sh["shift_y"][1] - abs(s_)), (xc, sh["shift_y"][1])]
        pts.append((xc, yb))
        return pts, rnd

    def rounded_line(pts, rnd, step=0.01):
        out = [pts[0]]
        for i_ in range(1, len(pts) - 1):
            if i_ not in rnd:
                out.append(pts[i_])
                continue
            p0, p1, p2 = pts[i_ - 1], pts[i_], pts[i_ + 1]
            t_ = turn_of(p0, p1, p2)
            r_ = rnd[i_]
            lu, lv = math.hypot(p1[0] - p0[0], p1[1] - p0[1]), math.hypot(p2[0] - p1[0], p2[1] - p1[1])
            u_, v_ = ((p1[0] - p0[0]) / lu, (p1[1] - p0[1]) / lu), ((p2[0] - p1[0]) / lv, (p2[1] - p1[1]) / lv)
            tl = r_ * math.tan(t_ / 2)
            a_ = (p1[0] - u_[0] * tl, p1[1] - u_[1] * tl)
            left = u_[0] * v_[1] - u_[1] * v_[0] > 0
            n_ = (-u_[1], u_[0]) if left else (u_[1], -u_[0])
            c_ = (a_[0] + n_[0] * r_, a_[1] + n_[1] * r_)
            a0 = math.atan2(a_[1] - c_[1], a_[0] - c_[0])
            sw = t_ if left else -t_
            m_ = max(4, int(r_ * t_ / step))
            out += [(c_[0] + r_ * math.cos(a0 + sw * k_ / m_), c_[1] + r_ * math.sin(a0 + sw * k_ / m_)) for k_ in range(m_ + 1)]
        out.append(pts[-1])
        return LineString(out)

    # ---- card delay of every command net re-derived: stub + resistor + link, barrels between mid-copper, In3 electrical
    ebad, edet = [], []
    for n in cmd:
        c_ = cols[n]
        fs_ = fp["finger_stubs"][fnet_of(n)]
        stub_ = sum(math.hypot(b_[0] - a_[0], b_[1] - a_[1]) for a_, b_ in zip(fs_["path"], fs_["path"][1:])) - (fp["outline"]["finger_pad_y"][1] - fs_["path"][0][1])
        outer_ = stub_ + (crow["node_via"] - fs_["path"][-1][1])              # pad 1 -> pad 2 -> node via, same column
        z_node = abs(zmid["In3.Cu"] - zmid[fs_["layer"]])
        wd = c_["in3_width"]
        rt_ = [tuple(q_) for q_ in c_["in3_route"]]
        e_in3 = electrical(rt_, wd)[1]
        ps_ = outer_ * dly["F.Cu/B.Cu w0.20"] + (z_node + abs(zmid["In3.Cu"] - zmid["F.Cu"])) * dly["through via (per mm of barrel)"] + e_in3 * dly["In3.Cu"]
        if abs(ps_ - gcmd["per_net_min_card_delay_estimate_ps"][n]) > 0.011:
            ebad.append((n, round(ps_, 3), gcmd["per_net_min_card_delay_estimate_ps"][n]))
        edet.append("%s %.2f" % (n, ps_))
    tot_ = gcmd["total_delay_target_ps"]
    longest = max(cmd, key=lambda n: gcmd["per_net_host_plus_pkg_ps"][n] + gcmd["per_net_min_card_delay_estimate_ps"][n])
    check("command card delays re-derived (finger stub, resistor and link at the 0.20 mm figure, via barrels between the middles of the "
          "copper layers: F-In3 %.4f mm, B-In3 %.4f mm, In3 by its electrical length with rules.bend_model, mitre %.4f mm/rad at 0.15 mm): equal to the "
          "plan's estimates; the total %.2f ps is set by the CK pair (longest: %s; CK_P is brought to CK_N by its hook) and no net "
          "needs a common offset"
          % (abs(zmid["In3.Cu"] - zmid["F.Cu"]), abs(zmid["B.Cu"] - zmid["In3.Cu"]), bm["k_mitre0"] + bm["k_mitre1"] * w, tot_, longest),
          not ebad and longest in ("CK_N", "CK_P") and abs(trims["CK_N"]) < 0.02 and abs(trims["CK_P"]) < 0.15
          and abs(abs(zmid["In3.Cu"] - zmid["F.Cu"]) - 0.6425) < 1e-6 and abs(abs(zmid["B.Cu"] - zmid["In3.Cu"]) - 0.5145) < 1e-6
          and "common offset" not in gcmd["note"].replace("no common offset", ""),
          "; ".join(edet) + ("; problems: %s" % ebad if ebad else ""))
    arc_rad = lambda r_: bm["k_arc0"] * w + bm["k_arc1"] * (r_ - w / math.log((r_ + w / 2) / (r_ - w / 2)))      # noqa: E731
    mit = lambda w_: bm["k_mitre0"] + bm["k_mitre1"] * w_      # noqa: E731
    # the HFSS runs behind the model (design/ref/hfss_cmd_tuning.json), re-evaluated here with the checker's own formula
    href = json.load(open(os.path.join(ROOT, "design", "ref", "hfss_cmd_tuning.json")))
    cal_bad, cal_det = [], []
    for c_ in href["calibration"]:
        pts_ = [(v_[0], v_[1]) for v_ in c_["vertices"]]
        rnd_ = {i_: v_[2] for i_, v_ in enumerate(c_["vertices"]) if v_[2]}
        g_, e_, _t, _l, fit_ = electrical(pts_, c_["width"], rnd_)
        st_ = pts_[-1][1] - pts_[0][1]
        dev = (e_ - st_) - c_["hfss_electrical_added_mm"]
        # pitch 0.48 mm shapes with almost no straight between the arcs are outside what the board uses
        legs_y = sorted({pts_[i_][1] for i_ in rnd_})
        tight = len(legs_y) > 1 and min(b_ - a_ for a_, b_ in zip(legs_y, legs_y[1:])) < 0.6
        if abs(dev) > (0.03 if tight else 0.015):
            cal_bad.append((c_["run"], round(dev, 4)))
        cal_det.append("%s %+.4f" % (c_["run"], dev))
    check("bend model of the spec against its %d HFSS calibration runs (design/ref/hfss_cmd_tuning.json): formula - HFSS within 0.015 mm (0.1 ps) "
          "on every shape with legs 0.72 mm apart or more, within 0.03 mm on the 0.48 mm shapes; on the 0.15 mm trace a mitred vertex loses "
          "%.4f mm per radian (%.4f on 0.10 mm), an arc %.4f / %.4f / %.4f mm per radian at r = 0.20 / 0.15 / 0.10 mm, two antiparallel legs "
          "%.2f %% of their common length at a gap of 0.33 mm and %.2f %% at the gap used"
          % (len(href["calibration"]), mit(w), mit(0.10), arc_rad(0.20), arc_rad(0.15), arc_rad(0.10),
             100 * bm["k_leg"] * math.exp(-math.pi * 0.33 / bm["leg_decay_mm"]), 100 * bm["k_leg"] * math.exp(-math.pi * (tg["leg_pitch"] - w) / bm["leg_decay_mm"])),
          not cal_bad and len(href["calibration"]) >= 8 and abs(href["straight_line_ps_per_mm"] / dly["In3.Cu"] - 1) < 0.002,
          "formula - HFSS, mm: " + "; ".join(cal_det) + ("; outside: %s" % cal_bad if cal_bad else ""))

    # ---- the tuning feature of every CA / CS net, re-derived from its shape
    grid_ = [round(tg["first_leg_y"] + tg["leg_pitch"] * k_, 3) for k_ in range(tg["rows"])]
    xs_ = sorted((cols[n]["x"], n) for n in cmd)
    tbad, tdet, tline, tcorr, tres = [], [], {}, {}, {}
    for i, (x, n) in enumerate(xs_):
        if n in ("CK_P", "CK_N"):
            continue
        q = tz["per_net"][n]
        sh = q["shape"]
        rt_ = [tuple(q_) for q_ in cols[n]["in3_route"]]
        pts, rnd = feature_pts(sh)
        geo, el, turned, leg_mm, fit = electrical(pts, w, rnd)
        straight = sh["y"][1] - sh["y"][0]
        # HFSS of this net's own feature beyond the formula, re-derived from the reference file
        hf_ = href["features"].get(n)
        res_ = 0.0
        if hf_:
            pts_s, rnd_s = feature_pts(hf_["shape"])
            g_s, e_s = electrical(pts_s, w, rnd_s)[:2]
            res_ = hf_["hfss_electrical_added_mm"] - (e_s - (hf_["shape"]["y"][1] - hf_["shape"]["y"][0]))
        el += res_
        tres[n] = (res_, hf_)
        added_ps = (el - straight) * dly["In3.Cu"]
        tcorr[n] = (geo - el) * dly["In3.Cu"]
        tline[n] = rounded_line(pts, rnd)
        why = []
        if abs(sh["x"] - x) > 1e-9 or abs(sh["y"][0] - rt_[0][1]) > 1e-9 or abs(sh["y"][1] - rt_[-2][1]) > 1e-9:
            why.append("shape is not on the channel axis between the node via and the jog")
        if abs(added_ps - trims[n]) > 0.03 or abs(q["trim_ps"] - trims[n]) > 0.011:
            why.append("adds %.2f ps, trim %.2f ps" % (added_ps, trims[n]))
        if abs(geo - straight - q["added_centre_line_mm"]) > 1.5e-3 or abs(el - straight - q["added_electrical_mm"]) > 1.5e-3:
            why.append("lengths differ from the plan")
        if not fit:
            why.append("corner radii do not fit")
        if hf_ is None or abs(res_ - q["hfss_residual_mm"]) > 2e-4 or abs(res_) > 0.08 \
                or abs(hf_["shape"]["shift"] - sh["shift"]) > 0.03 or abs(hf_["shape"]["amplitude"] - sh["amplitude"]) > (0.03 if q["bumps"] else 9) \
                or (q["bumps"] and (hf_["shape"]["rows"] != sh["rows"] or hf_["shape"]["radius"] != sh["radius"])):
            why.append("no HFSS run of (nearly) this feature, or its residual is not the one in the plan")
        if (x < xck) != (sh["shift"] <= 0) and abs(sh["shift"]) > 1e-9:
            why.append("shift toward the CK corridor")
        if q["bumps"]:
            ok_rows = all(y_ in grid_ for y_ in sh["rows"]) and len(sh["rows"]) == 2 * q["bumps"] and sh["pattern"] == [0] + [1, 0] * q["bumps"]
            run_ok = abs(sh["shift"]) < 1e-9 or (sh["shift_y"][0] + abs(sh["shift"]) <= sh["rows"][0] - sh["radius"] + 1e-9
                                                and sh["shift_y"][1] - abs(sh["shift"]) >= sh["rows"][-1] + sh["radius"] - 1e-9)
            if not ok_rows or not run_ok or q["solve"] != "amplitude":
                why.append("legs are not whole bumps on the common rows of the shifted run")
            if sh["amplitude"] < max(tg["amplitude_min"], 2 * sh["radius"]) - 1e-9 or sh["amplitude"] > tg["amplitude_max"] + 1e-9 \
                    or sh["radius"] < tg["radius_min"] - 1e-9 or 2 * sh["radius"] > tg["leg_pitch"] + 1e-9:
                why.append("amplitude %.3f / radius %.3f outside the rules" % (sh["amplitude"], sh["radius"]))
            if min(sh["rows"]) - w / 2 < tz["y"][0] - 1e-9 or max(sh["rows"]) + w / 2 > tz["y"][1] + 1e-9:
                why.append("leg copper outside the zone")
        elif q["solve"] != "shift" or abs(sh["shift"]) < 1e-9:
            why.append("a net without bumps must take its trim as a shift")
        if abs(sh["shift"]) > 1e-9 and (sh["shift_y"] != tg["shift_y"] or 2 * abs(sh["shift"]) > sh["shift_y"][1] - sh["shift_y"][0] - 0.3):
            why.append("shift does not fit between y %s" % tg["shift_y"])
        if why:
            tbad.append((n, why))
        tdet.append("%s %+.3f%s: +%.3f mm drawn, %.0f deg turned, HFSS residual %+.4f mm, %.2f ps"
                    % (n, sh["shift"], " + %d x %.3f r %.2f" % (q["bumps"], sh["amplitude"], sh["radius"]) if q["bumps"] else "", geo - straight,
                       math.degrees(turned), res_, added_ps))
    check("command length tuning (drawn by s20_cmd), re-derived from the shape of every net: the feature adds exactly the trim in ELECTRICAL "
          "length (rules.bend_model plus the HFSS residual of that net's own feature, re-derived here from design/ref/hfss_cmd_tuning.json: "
          "at most 0.08 mm, simulated shape within 0.03 mm of the planned one); parallel shifts away from the CK corridor between y %.2f and %.2f, bumps pointing east as whole bumps on "
          "the common rows %s (leg pitch %.2f mm = %.2f mm of gap = %.1f h, h = %.2f mm In3 to In2), radius >= %.2f mm (>= w) and fitting "
          "without reduction, amplitude >= %.2f mm (>= 2 w), leg copper inside the zone y %.2f .. %.2f; the CK pair itself needs no meander"
          % (tg["shift_y"][0], tg["shift_y"][1], grid_, tg["leg_pitch"], tg["leg_pitch"] - w, (tg["leg_pitch"] - w) / h_in3, h_in3, tg["radius_min"],
             tg["amplitude_min"], tz["y"][0], tz["y"][1]),
          not tbad and tg["leg_pitch"] - w >= 3 * h_in3 - 1e-9 and tg["radius_min"] >= w - 1e-9 and tg["amplitude_min"] >= 2 * w - 1e-9
          and tz["y"][0] >= crow["gnd_via"] + VR + 0.2 - 1e-6 and tz["y"][1] <= bxy("A1")[1] - 0.4 + 1e-6
          and tg["shift_y"][0] >= crow["gnd_via"] + 0.3 - 1e-9 and tg["shift_y"][1] <= bxy("A1")[1] - 0.3 + 1e-9
          and tz["rows_y"] == grid_ and tg["leg_pitch"] == tz["leg_pitch"],
          "; ".join(tdet) + ("; problems: %s" % tbad if tbad else ""))
    # gaps between the features as they will be drawn (rounded centre lines), to the CK pair and to every via pad
    ckl = {nm: LineString([tuple(q_) for q_ in cols[nm]["in3_route"]]) for nm in ("CK_P", "CK_N")}
    se_ = [n for x, n in xs_ if n not in ckl]
    gmin, gck, gvia, gbad = (9.9, None), (9.9, None), (9.9, None), []
    for i, a_ in enumerate(se_):
        for b_ in se_[i + 1:]:
            g_ = tline[a_].distance(tline[b_]) - w
            gmin = min(gmin, (g_, "%s / %s" % (a_, b_)))
            rec = tz["per_net"][a_]["gap_mm"].get(b_)
            if rec is not None and abs(rec - g_) > 2e-3:
                gbad.append((a_, b_, round(g_, 3), rec))
        for nm, l_ in ckl.items():
            gck = min(gck, (tline[a_].distance(LineString(list(l_.coords)[2:5])) - w / 2 - 0.05, "%s / %s" % (a_, nm)))
        for v in fvias:
            if v["net"] != a_:
                gvia = min(gvia, (tline[a_].distance(Point(v["x"], v["y"])) - VR - w / 2, "%s to the %s via at (%.3f, %.2f)" % (a_, v["net"], v["x"], v["y"])))
        for bn, net, c in u1v:
            if net != a_:
                gvia = min(gvia, (tline[a_].distance(Point(*c)) - VR - w / 2, "%s to U1 via %s" % (a_, bn)))
        xs_a = [q_[0] for q_ in tline[a_].coords]
        if min(xs_a) - w / 2 < tz["x"][0] - 1e-9 or max(xs_a) + w / 2 > tz["x"][1] + 1e-9:
            gbad.append((a_, "outside the zone x %s" % tz["x"]))
    ck_corr = {nm: (lambda g_e: (g_e[0] - g_e[1]) * dly["In3.Cu"])(electrical([tuple(q_) for q_ in cols[nm]["in3_route"]], cols[nm]["in3_width"]))
               for nm in ("CK_P", "CK_N")}
    for n in se_:      # the jog into the ball belongs to the trace as well
        tcorr[n] += mit(w) * math.pi / 4 * dly["In3.Cu"]
    dcorr = max(tcorr.values()) - min(ck_corr.values())
    check("command tuning features as drawn (rounded centre lines): copper to copper >= %.2f mm (3 h) between neighbouring nets, >= %.2f mm to "
          "the coupled CK pair, >= the class clearance %.2f mm to every fence via and via-in-pad, all inside the In3 zone x %s; bend corrections "
          "%.2f .. %.2f ps per CA / CS net and %.2f / %.2f ps on CK_P / CK_N, so an error of 20 %% in the bend model moves no net against the "
          "clock by more than %.2f ps (limit 0.5 ps)"
          % (tg["min_gap_to_neighbour"], ncl0["CMD_CK"]["pair_to_other_track_min"], ccl, tz["x"], min(tcorr.values()), max(tcorr.values()),
             ck_corr["CK_P"], ck_corr["CK_N"], 0.2 * dcorr),
          gmin[0] >= tg["min_gap_to_neighbour"] - 1e-9 and tg["min_gap_to_neighbour"] >= 3 * h_in3 - 1e-9
          and gck[0] >= ncl0["CMD_CK"]["pair_to_other_track_min"] - 1e-9 and gvia[0] >= ccl - 1e-6 and not gbad and 0.2 * dcorr <= 0.5,
          "smallest gaps: %.3f mm (%s), CK %.3f mm (%s), via pad %.3f mm (%s)%s" % (gmin + gck + gvia + (("; problems: %s" % gbad) if gbad else "",)))
    # CK pair: explicit In3 centre lines
    hk = gcmd["tuning"]["ck_hook"]
    rP, rN = [tuple(q_) for q_ in cols["CK_P"]["in3_route"]], [tuple(q_) for q_ in cols["CK_N"]["in3_route"]]
    ckbad = []
    ckmin = (9.9, None)
    for nm, pl in (("CK_P", rP), ("CK_N", rN)):
        if any(not (abs(a_[0] - b_[0]) < 1e-6 or abs(a_[1] - b_[1]) < 1e-6 or abs(abs(a_[0] - b_[0]) - abs(a_[1] - b_[1])) < 1e-6) for a_, b_ in segs(pl)):
            ckbad.append(nm + ": segment not at 0 / 45 / 90 degrees")
        if math.hypot(pl[0][0] - cols[nm]["x"], pl[0][1] - crow["node_via"]) > 1e-6 \
                or math.hypot(pl[-1][0] - bxy(ballof[nm])[0], pl[-1][1] - bxy(ballof[nm])[1]) > 1e-6:
            ckbad.append(nm + ": does not run from its node via to its ball")
        for a_, b_ in segs(pl):
            for v in fvias:
                if v["net"] != nm:
                    ckmin = min(ckmin, (seg_pt((v["x"], v["y"]), a_, b_) - VR - 0.05, "%s to the %s via at (%.3f, %.2f)" % (nm, v["net"], v["x"], v["y"])))
            for bn, net, c in u1v:
                if net != nm:
                    ckmin = min(ckmin, (seg_pt(c, a_, b_) - VR - 0.05, "%s to U1 via %s" % (nm, bn)))
    ysplit = fp["command"]["ck_split"]["split_y"]
    coupled = [m_ for m_ in [(rP[2][0], yy / 100.0) for yy in range(int(rP[2][1] * 100) + 1, int(ysplit * 100))]]
    cgap = [min(seg_pt(m_, a_, b_) for a_, b_ in segs(rN)) for m_ in coupled]
    unc = fp["command"]["ck_split"]["uncoupled_mm"]
    plen = lambda pl: sum(math.hypot(b_[0] - a_[0], b_[1] - a_[1]) for a_, b_ in segs(pl))      # noqa: E731
    check("CK pair on In3: both legs run from their node vias to H9 / J9 in 45-degree segments, coupled at 0.10 / 0.10 mm from y %.2f to %.2f, "
          "then split around the H9 via; every foreign via pad >= the class clearance away; CK_P hooks back into H9 (corner at H9 + t, "
          "t = %.3f inside %s); uncoupled ends <= the class limit" % (rP[2][1], ysplit, hk["t"], hk["t_range"]),
          not ckbad and ckmin[0] >= ccl - 1e-6 and max(cgap) < 0.20 + 1e-6 and min(cgap) > 0.20 - 1e-6
          and hk["t_range"][0] <= hk["t"] <= hk["t_range"][1] and abs(rP[-2][0] - (xck + hk["t"])) < 1.5e-3
          and abs(rP[-2][1] - (bxy("H9")[1] + hk["t"])) < 1.5e-3
          and max(unc.values()) <= ncl0["CMD_CK"]["max_uncoupled_mm"] and abs(plen(rP[4:]) - unc["CK_P"]) < 0.01
          and abs(rP[3][1] - fp["command"]["ck_split"]["courtyard_break_y"]) < 1e-9 and abs(rN[3][1] - rP[3][1]) < 1e-9
          and abs(fp["command"]["ck_split"]["courtyard_break_y"] - (cy - 7.5 - 0.25 - 0.05)) < 1e-9,
          "smallest clearance %.3f mm (%s); lengths CK_P %.3f / CK_N %.3f mm; uncoupled at the ball end %s; %s"
          % (ckmin[0], ckmin[1], plen(rP), plen(rN), unc, ckbad))
    ckr = {r_["name"]: r_ for r_ in d["rules"]["spacing_rules"]}["CK_pair_to_other_tracks"]
    node_unc = plen(rP[:3])
    links = (crow["node_via"] - crow["series_resistor_centre"] - 0.32) + (crow["shunt_resistor_centre"] - 0.32 - crow["node_via"])
    drc_unc = max(unc.values()) + node_unc + links
    check("CK pair rules as the DRC applies them: pair-to-track spacing on In3 only (on the outer layers CK_P / CK_N are the links of their own "
          "divider columns); uncoupled length over all layers %.2f mm (In3 %.2f + %.2f, outer-layer links %.2f) within the limit %.1f mm, "
          "In3 alone <= 2.5 mm" % (drc_unc, node_unc, max(unc.values()), links, ncl0["CMD_CK"]["max_uncoupled_mm"]),
          ckr.get("layer") == "In3.Cu" and drc_unc <= ncl0["CMD_CK"]["max_uncoupled_mm"] <= drc_unc + 0.25 and node_unc + max(unc.values()) <= 2.5,
          "%.3f" % drc_unc)
    check("matching tolerances are not relaxed: CMD <= 2 ps (CK pair <= 1 ps), WR <= 10 ps, RD_P <= 10 ps",
          gcmd["tolerance_ps"] <= 2.0 and gcmd["intra_pair"]["tolerance_ps"] <= 1.0
          and all({g["name"]: g for g in d["groups"]}[k]["tolerance_ps"] <= 10.0 for k in ("WR", "RD_P")))

    # ------------------------------------------------------------------ finger stubs and finger vias (review B1, M1, M2)
    from shapely.geometry import LineString, Point, box
    ftop = fp["outline"]["finger_top_y"]
    stubs = fp["finger_stubs"]
    fgv = fp["finger_vias"]
    kx, kd = fp["outline"]["key_notch"]["x_centre"], fp["outline"]["key_notch"]["depth"]
    kr = fp["outline"]["key_notch"]["width"] / 2
    slot = box(kx - kr, -1.0, kx + kr, kd - kr).union(Point(kx, kd - kr).buffer(kr, 64))
    sig_f = {f: j1[str(f)] for f in range(1, 261) if j1[str(f)] and j1[str(f)] not in ("GND", "V1P2", "V3P3", "SCL", "SDA")}
    cls_of = {}
    for k_, c_ in ncl0.items():
        for n_ in c_["nets"]:
            cls_of[n_] = k_
    switching = set(ncl0["VREF"]["switching_classes"])

    def need_between(na, nb):
        """Required copper clearance between two nets on one layer (tracks)."""
        ca, cb = cls_of.get(na, "Default"), cls_of.get(nb, "Default")
        if (ca == "VREF" and cb in switching) or (cb == "VREF" and ca in switching):
            if {na, nb} in ({"VREF_N4", "DQ4"}, {"VREF_N4", "DQ5"}, {"VREF_N5", "DQ4"}, {"VREF_N5", "DQ5"}):
                return ncl0["VREF"]["keep_from_switching_nets_east_end"]        # rule area vref_east_end (fingers 211 .. 216: DQ5 / VREF_N5 on 212 / 211, DQ4 / VREF_N4 on 215 / 216)
            return ncl0["VREF"]["keep_from_switching_nets"]
        return max(ncl0[ca]["clearance"], ncl0[cb]["clearance"]) if "clearance_layer" not in ncl0[ca] and "clearance_layer" not in ncl0[cb] else 0.10

    def need_static(na, nb):
        """Required clearance of a track of na to a pad or via of nb (the 0.9 mm reference rule is track to track / via)."""
        ca, cb = cls_of.get(na, "Default"), cls_of.get(nb, "Default")
        if "VREF" in (ca, cb):      # 0201 lands 0.17; a POWER via / tie in the finger-via row 0.12; else the class value
            return ncl0["VREF"]["finger_via_row_clearance"] if "POWER" in (ca, cb) else ncl0["VREF"]["land_clearance_0201"]
        return 0.10

    def is45(pl):
        return all(abs(a_[0] - b_[0]) < 1e-6 or abs(a_[1] - b_[1]) < 1e-6 or abs(abs(a_[0] - b_[0]) - abs(a_[1] - b_[1])) < 1e-6 for a_, b_ in segs(pl))

    def no_acute(pl):
        for a_, b_, c_ in zip(pl, pl[1:], pl[2:]):
            if (b_[0] - a_[0]) * (c_[0] - b_[0]) + (b_[1] - a_[1]) * (c_[1] - b_[1]) < -1e-9:
                return False
        return True

    def fpad(f):
        x = fingers[f]["x_mm"]
        return box(x - 0.175, fp["outline"]["finger_pad_y"][0], x + 0.175, ftop)

    def end_point(to, net, last):
        if to == "read via":
            vs = [v for v in fvias if v["net"] == net and abs(v["x"] - last[0]) < 1e-6 and abs(v["y"] - last[1]) < 1e-6]
            return (vs[0]["x"], vs[0]["y"]) if len(vs) == 1 else None
        if to == "the F.Cu leg of the pair":      # RDQS: the leg runs straight into the finger; the stub entry is its last piece, up to the load link
            lk_ = [w_ for w_ in fp["data"]["wires"] if w_["net"] == net and w_["role"] == "load link"]
            return tuple(lk_[0]["path"][0]) if len(lk_) == 1 else None
        r_, pin = to.split(".")
        if parts[r_]["pins"][pin] != net:
            return None
        if r_ in place:
            return tuple(place[r_]["pad" + pin])
        return tuple(fp["placements_other"][r_]["pads"][pin])

    sbad = []
    shape, layer_of = {}, {}
    for net, s_ in stubs.items():
        pl = [tuple(q_) for q_ in s_["path"]]
        f = s_["finger"]
        if j1[str(f)] != net or s_["layer"] != fingers[f]["side"] + ".Cu":
            sbad.append((net, "finger / layer"))
        if not is45(pl) or not no_acute(pl):
            sbad.append((net, "angles"))
        w0_ = s_["neck"]["width"] if "neck" in s_ else s_["width"]          # width of the piece that leaves the finger
        # the RDQS legs stay coupled (0.10 / 0.10) into fingers 219 / 221, which are 0.50 mm apart: each leg enters 0.15 mm off its
        # finger axis, 0.025 mm of its width beyond the pad edge
        off_max = 0.15 if s_["kind"] == "RDQS leg end" else 0.175 - w0_ / 2
        if not (fp["outline"]["finger_pad_y"][0] < pl[0][1] < ftop and abs(pl[0][0] - fingers[f]["x_mm"]) <= off_max + 1e-9):
            sbad.append((net, "does not start inside its finger pad"))
        e_ = end_point(s_["to"], net, pl[-1])
        if e_ is None or math.hypot(pl[-1][0] - e_[0], pl[-1][1] - e_[1]) > 1e-6:
            sbad.append((net, "does not end on %s" % s_["to"]))
        ln_ = sum(math.hypot(b_[0] - a_[0], b_[1] - a_[1]) for a_, b_ in segs(pl)) - (ftop - pl[0][1])
        lim = ncl0["HOST_SE"]["max_length"] if cls_of.get(net) == "HOST_SE" else (ncl0["VREF"]["max_stub_length"] if cls_of.get(net) == "VREF" else 99)
        if ln_ > lim + 1e-6 or abs(ln_ - s_["length_from_finger_top_mm"]) > 2e-3:
            sbad.append((net, "length %.2f" % ln_))
        scls_ = "RD_DQ" if s_["kind"] == "read track" else cls_of[net]        # DQx: one net, the read finger track follows the read-branch rules
        if abs(s_["width"] - ncl0[scls_]["layers"][s_["layer"]]["width"]) > 1e-9:
            sbad.append((net, "width"))
        if ("neck" in s_) != (s_["kind"] in ("write stub", "RESET_n stub")):
            sbad.append((net, "the stubs of the data side that feed a series resistor have a neck, no other"))
        if "neck" in s_:
            # write / WCK / RESET_n stub: 0.15 mm from the finger through the row of the finger vias, the class width above it.
            # Re-derived here: the neck is the first part of the path and ends on the lane of the stub at least 0.15 mm above
            # the pads of the finger vias (y = 3.05 + 0.20), at the height the class states
            hs_ = ncl0["HOST_SE"]
            nk_, wd_ = [tuple(q_) for q_ in s_["neck"]["path"]], [tuple(q_) for q_ in s_["wide"]["path"]]
            nk_len = sum(math.hypot(b_[0] - a_[0], b_[1] - a_[1]) for a_, b_ in segs(nk_)) - (ftop - nk_[0][1])
            if nk_ + wd_[1:] != pl or abs(s_["neck"]["width"] - hs_["finger_neck_width"]) > 1e-9 or abs(s_["wide"]["width"] - hs_["layers"][s_["layer"]]["width"]) > 1e-9 \
                    or abs(nk_len - s_["neck"]["length_from_finger_top_mm"]) > 2e-3 or abs(nk_[-1][1] - hs_["finger_neck_to_y"]) > 1e-9 \
                    or nk_[-1][1] < 3.05 + 0.20 + 0.15 - 1e-9 or abs(nk_[-1][0] - nk_[-2][0]) > 1e-9 or hs_["layers"][s_["layer"]]["min_width"] != hs_["finger_neck_width"]:
                sbad.append((net, "neck / wide sections"))
            shape[net] = LineString(nk_).buffer(s_["neck"]["width"] / 2, 32).union(LineString(wd_).buffer(s_["wide"]["width"] / 2, 32))
        else:
            shape[net] = LineString(pl).buffer(s_["width"] / 2, 32)
        layer_of[net] = s_["layer"]
    check("finger stubs: one planned track for every signal finger (%d), on the finger's own layer, class width (the stubs of the data side that feed "
          "a series resistor: 0.15 mm neck from the finger to 0.20 mm above the finger-via pads, then the class width), starting inside the finger pad, "
          "ending on the named pad centre, read via or leg, 0 / 45 / 90 degree segments without acute bends, HOST_SE <= %.1f mm, reference <= %.1f mm"
          % (len(stubs), ncl0["HOST_SE"]["max_length"], ncl0["VREF"]["max_stub_length"]),
          not sbad and set(stubs) == set(sig_f.values()), "%s; missing %s" % (sbad[:6], sorted(set(sig_f.values()) - set(stubs))[:6]))
    edge = min((shape[n_].distance(slot), n_) for n_ in shape)
    check("finger stubs keep 0.35 mm of copper clearance to the key slot (rule 0.30 mm)", edge[0] >= 0.35 - 1e-6, "smallest %.3f mm (%s)" % edge)
    # finger vias and their ties
    vbad = []
    tied = Counter()
    via_shape, tie_shape = [], []
    for v in fgv:
        if v["diameter"] != 0.40 or v["drill"] != 0.20 or abs(v["y"] - 3.05) > 1e-9 or not v["ties"]:
            vbad.append((v["x"], "size / row"))
        via_shape.append((Point(v["x"], v["y"]).buffer(v["diameter"] / 2, 64), v["net"], v))
        for t_ in v["ties"]:
            f = t_["finger"]
            pl = [tuple(q_) for q_ in t_["path"]]
            tied[f] += 1
            if j1[str(f)] != v["net"] or v["net"] not in ("GND", "V1P2") or t_["layer"] != fingers[f]["side"] + ".Cu" or not is45(pl) or not no_acute(pl) \
                    or not fpad(f).contains(Point(pl[0])) or math.hypot(pl[-1][0] - v["x"], pl[-1][1] - v["y"]) > 1e-6 \
                    or t_["width"] < ncl0["POWER"]["layers"]["any"]["width"] - 1e-9:
                vbad.append((f, "tie"))
            tie_shape.append((LineString(pl).buffer(t_["width"] / 2, 32), v["net"], t_["layer"], f))
    pwr_f = [f for f in range(1, 261) if j1[str(f)] in ("GND", "V1P2")]
    check("finger vias: every VSS and VDD finger (%d) is tied on its own layer to exactly one 0.40 / 0.20 via of its net at y = 3.05 (%d vias)"
          % (len(pwr_f), len(fgv)), not vbad and all(tied[f] == 1 for f in pwr_f) and set(tied) == set(pwr_f), str(vbad[:6]))
    # clearances, with every stub, tie and via present
    worst = (9.9, None)

    tight = []

    def note(val, need, what):
        nonlocal worst
        if val - need < 0.06:
            tight.append((round(val - need, 3), what, round(val, 3)))
        if val - need < worst[0]:
            worst = (val - need, "%s: %.3f mm (required %.2f)" % (what, val, need))
    names_ = sorted(shape)
    for i, a_ in enumerate(names_):
        for b_ in names_[i + 1:]:
            if layer_of[a_] == layer_of[b_]:
                note(shape[a_].distance(shape[b_]), need_between(a_, b_), "%s to %s on %s" % (a_, b_, layer_of[a_]))
        lay = layer_of[a_]
        xs0, ys0, xs1, ys1 = shape[a_].bounds
        for f in range(1, 261):
            if j1[str(f)] != a_ and fingers[f]["side"] + ".Cu" == lay and xs0 - 1.0 < fingers[f]["x_mm"] < xs1 + 1.0:
                note(shape[a_].distance(fpad(f)), 0.10, "%s to finger %d" % (a_, f))
        for r_, pin, net, sd, rc in pads:
            if net != a_ and sd + ".Cu" == lay and rc[2] > xs0 - 1.0 and rc[0] < xs1 + 1.0:
                note(shape[a_].distance(box(*rc)), need_static(a_, net), "%s to pad %s.%s" % (a_, r_, pin))
        for v in fvias:
            if v["net"] != a_:
                note(shape[a_].distance(Point(v["x"], v["y"]).buffer(VR, 64)), need_static(a_, v["net"]), "%s to the %s via at (%.3f, %.2f)" % (a_, v["net"], v["x"], v["y"]))
        for vs, vnet, v in via_shape:
            note(shape[a_].distance(vs), need_static(a_, vnet), "%s to the finger via of %s" % (a_, v["fingers"]))
        for ts, tnet, tlay, f in tie_shape:
            if tlay == lay:
                note(shape[a_].distance(ts), need_static(a_, tnet), "%s to the tie of finger %d" % (a_, f))
    for vs, vnet, v in via_shape:
        for f in range(1, 261):
            if j1[str(f)] != vnet and abs(fingers[f]["x_mm"] - v["x"]) < 1.0:
                note(vs.distance(fpad(f)), 0.10, "finger via %s to finger %d" % (v["fingers"], f))
        for r_, pin, net, sd, rc in pads:
            if net != vnet and abs((rc[0] + rc[2]) / 2 - v["x"]) < 1.5:
                note(vs.distance(box(*rc)), 0.10, "finger via %s to pad %s.%s" % (v["fingers"], r_, pin))
        for u in fvias:
            dd = math.hypot(u["x"] - v["x"], u["y"] - v["y"])
            note(dd - 0.20, 0.25, "finger via %s hole to the hole of the %s via at (%.3f, %.2f)" % (v["fingers"], u["net"], u["x"], u["y"]))
        for vs2, vnet2, v2 in via_shape:
            if v2 is not v:
                note(math.hypot(v2["x"] - v["x"], v2["y"] - v["y"]) - 0.20, 0.25, "finger via %s hole to finger via %s" % (v["fingers"], v2["fingers"]))
                if vnet2 != vnet:
                    note(vs.distance(vs2), 0.10, "finger via %s to finger via %s" % (v["fingers"], v2["fingers"]))
    for ts, tnet, tlay, f in tie_shape:
        for g_ in range(max(1, f - 6), min(260, f + 6) + 1):
            if j1[str(g_)] != tnet and fingers[g_]["side"] + ".Cu" == tlay:
                note(ts.distance(fpad(g_)), 0.10, "tie of finger %d to finger %d" % (f, g_))
    check("finger field with every stub, tie and via present: stub to stub, stub to finger pad / part pad / via, via to pad, hole to hole "
          "all at or above the required clearance (0.10 mm; around a reference track 0.17 mm to an 0201 land and 0.12 mm to a finger via or tie "
          "in the finger-via row; 0.90 mm between a reference track and a switching track on one layer, 0.40 mm in the area vref_east_end; "
          "holes 0.25 mm)", worst[0] >= 0.0 - 1e-9,
          "smallest margin over the requirement: %+.3f mm (%s); %d item pairs with less than 0.06 mm of margin" % (worst[0], worst[1], len(tight)))
    if os.environ.get("CHECK_NETLIST_VERBOSE"):
        for t_ in sorted(tight):
            print("   tight:", t_)
    close = sorted((v["margin_over_required_clearance"], v["x"], v["fingers"]) for v in fgv)[:3]
    by_pos = Counter(v["position"].split(" of ")[0].split(" finger")[0] for v in fgv)
    check("every VSS / VDD finger pair has a legal via position (margin over the required clearance >= 0)", close[0][0] >= -1e-9,
          "positions: %s; tightest: %s" % (dict(by_pos), close))

    # ------------------------------------------------------------------ the data side (stage s30_data): 'T at the divider', comb
    # Everything below is re-derived from the stored centre lines (floorplan.data.wires), the placements and the vias:
    # connectivity, topology, the delay of every net (own walker, own delay model with the bends), levels, spacings,
    # crossings.  Nothing is taken from the generator or from scripts/data_side.py.
    D = fp["data"]
    DW = D["wires"]
    po_ = fp["placements_other"]
    bend = d["rules"]["bend_model"]
    zmid, zrun = {}, 0.0
    for l_ in d["stackup"]["layers"]:
        if l_["type"] == "soldermask":
            continue
        if l_["type"] == "copper":
            zmid[l_["layer"]] = zrun + l_["thickness_mm"] / 2
        zrun += l_["thickness_mm"]
    PAIRN = {"WCK_P", "WCK_N", "RDQS_P", "RDQS_N"}

    def psmm(layer, width, net):
        if layer == "In3.Cu":
            return dly["In3.Cu"]
        if net in PAIRN and layer in ("B.Cu", "F.Cu") and width <= 0.105:      # WCK on B.Cu, RDQS on F.Cu east of its via pair
            return dly["F.Cu/B.Cu pair 0.10/0.10 (odd mode)"]
        tab = [(0.09, dly["F.Cu/B.Cu w0.09"]), (0.15, dly["F.Cu/B.Cu w0.15"]), (0.20, dly["F.Cu/B.Cu w0.20"])]
        if width <= 0.09:
            return tab[0][1]
        if width >= 0.20:
            return tab[2][1]
        for (w0, v0), (w1, v1) in zip(tab, tab[1:]):
            if w0 <= width <= w1:
                return v0 + (v1 - v0) * (width - w0) / (w1 - w0)

    def K(p_, layer):
        return (round(p_[0], 4), round(p_[1], 4), layer)

    # ---- geometry of the wires
    gbad = [(w_["net"], w_["role"]) for w_ in DW if not is45([tuple(q_) for q_ in w_["path"]]) or not no_acute([tuple(q_) for q_ in w_["path"]])
            or w_["layer"] not in ("F.Cu", "In3.Cu", "B.Cu") or any(math.hypot(b_[0] - a_[0], b_[1] - a_[1]) < 1e-6 for a_, b_ in segs(w_["path"]))]
    wwid = Counter((w_["group"], w_["layer"], w_["width"]) for w_ in DW)
    ang_hist = Counter()
    for w_ in DW:
        pl_ = w_["path"]
        for a_, b_, c_ in zip(pl_, pl_[1:], pl_[2:]):
            u_, v_ = (b_[0] - a_[0], b_[1] - a_[1]), (c_[0] - b_[0], c_[1] - b_[1])
            ang_hist[int(round(180 - math.degrees(math.acos(max(-1.0, min(1.0, (u_[0] * v_[0] + u_[1] * v_[1]) / (math.hypot(*u_) * math.hypot(*v_))))))))] += 1
    check("data side: %d centre lines, all on F.Cu / In3.Cu / B.Cu (none on a plane layer), every segment at 0 / 45 / 90 / 135 degrees, no zero-length "
          "segment, no bend sharper than 90 degrees (interior angles at the vertices: %s)" % (len(DW), dict(sorted(ang_hist.items()))),
          not gbad and min(ang_hist) >= 90, str(gbad[:6]))

    # ---- pads and vias the wires may end on
    land = {}                       # (ref, pin) -> (net, side, rect, centre)
    for r_, pin, net, sd, rc in pads:
        land[(r_, pin)] = (net, sd, rc, tuple(place[r_]["pad" + pin]))
    for r_ in ("R402", "R403", "TP4", "C331", "R331", "R332", "R333", "U5"):
        for pin, xy in po_[r_]["pads"].items():
            hw = 0.35 if r_ != "TP4" else 0.5
            land[(r_, pin)] = (parts[r_]["pins"][pin], po_[r_]["side"], (xy[0] - hw, xy[1] - hw, xy[0] + hw, xy[1] + hw), tuple(xy))
    via_at = defaultdict(list)      # net -> [(x, y)] of every via the net has: field vias, finger vias, via-in-pads
    for v in fvias:
        via_at[v["net"]].append((v["x"], v["y"]))
    for v in fgv:
        via_at[v["net"]].append((v["x"], v["y"]))
    for b in via_balls:
        via_at[u1[b]].append(bxy(b))
    ball_at = {n_: bxy(b) for b in balls for n_ in [u1[b]] if n_ and n_ not in ("GND", "VDD2H", "VDDQ", "VDD1")}

    def lands_at(net, p_, layer):
        """What a point of a wire of 'net' on 'layer' sits on: 'via', a pad (ref.pin), a ball or a finger."""
        if any(math.hypot(p_[0] - x_, p_[1] - y_) < 1e-4 for x_, y_ in via_at[net]):
            return "via"
        for (r_, pin), (nn, sd, rc, c_) in land.items():
            if nn == net and sd + ".Cu" == layer and math.hypot(p_[0] - c_[0], p_[1] - c_[1]) < 1e-4:
                return "%s.%s" % (r_, pin)
        if layer == "F.Cu" and net in ball_at and math.hypot(p_[0] - ball_at[net][0], p_[1] - ball_at[net][1]) < 1e-4:
            return "ball"
        for f in range(1, 261):
            if j1[str(f)] == net and fingers[f]["side"] + ".Cu" == layer and abs(p_[0] - fingers[f]["x_mm"]) <= 0.176 and 0.15 <= p_[1] <= ftop:
                return "finger %d" % f
        return None
    vert = defaultdict(int)
    for w_ in DW:
        for q_ in w_["path"]:
            vert[(w_["net"],) + K(q_, w_["layer"])] += 1
    loose = []
    for w_ in DW:
        for e_ in (w_["path"][0], w_["path"][-1]):
            if not lands_at(w_["net"], e_, w_["layer"]) and vert[(w_["net"],) + K(e_, w_["layer"])] < 2:
                loose.append((w_["net"], w_["role"], e_))
    check("data side: every centre line starts and ends on a pad, ball, finger or via of its net, or on a vertex of another line of the same net "
          "on the same layer (junctions are vertices of both lines)", not loose, str(loose[:6]))

    # ---- one connected copper graph per net: every pin of the net is reached
    DNETS = wr + [n_ + "_WF" for n_ in wr] + ["WCK_P", "WCK_N", "WCK_F_P", "WCK_F_N", "RDQS_P", "RDQS_N", "VREF_RD", "DAC_OUT"] \
        + ["VREF_N%d" % i for i in range(8)]
    adjn = defaultdict(lambda: defaultdict(list))        # net -> node -> [(node, mm, layer, width)]
    for w_ in DW:
        for a_, b_ in segs(w_["path"]):
            ka, kb = K(a_, w_["layer"]), K(b_, w_["layer"])
            ln_ = math.hypot(b_[0] - a_[0], b_[1] - a_[1])
            adjn[w_["net"]][ka].append((kb, ln_, w_["layer"], w_["width"]))
            adjn[w_["net"]][kb].append((ka, ln_, w_["layer"], w_["width"]))
    for net in DNETS:
        for x_, y_ in via_at[net]:
            here = [l_ for l_ in ("F.Cu", "In3.Cu", "B.Cu") if K((x_, y_), l_) in adjn[net]]
            for i_, la in enumerate(here):
                for lb in here[i_ + 1:]:
                    adjn[net][K((x_, y_), la)].append((K((x_, y_), lb), abs(zmid[la] - zmid[lb]), "via", 0.0))
                    adjn[net][K((x_, y_), lb)].append((K((x_, y_), la), abs(zmid[la] - zmid[lb]), "via", 0.0))
    unreached = []
    for net in DNETS:
        nodes = list(adjn[net])
        if not nodes:
            unreached.append((net, "no copper"))
            continue
        seen, todo = {nodes[0]}, [nodes[0]]
        while todo:
            c_ = todo.pop()
            for nb_ in adjn[net][c_]:
                if nb_[0] not in seen:
                    seen.add(nb_[0])
                    todo.append(nb_[0])
        if len(seen) != len(nodes):
            unreached.append((net, "%d copper islands" % 2))
        on = {lands_at(net, (k_[0], k_[1]), k_[2]) for k_ in seen} - {None, "via"}
        want_ = set()
        for r_, pin in net_pins[net]:
            if r_ == "J1":
                want_.add("finger %s" % pin)
            elif r_ == "U1":
                want_.add("ball" if u1[pin] not in ("WCK_P", "WCK_N", "RDQS_P", "RDQS_N") else None)
            else:
                want_.add("%s.%s" % (r_, pin))
        want_ -= {None}
        if net in ("WCK_P", "WCK_N", "RDQS_P", "RDQS_N") and not any(math.hypot(k_[0] - ball_at[net][0], k_[1] - ball_at[net][1]) < 1e-4 for k_ in seen):
            unreached.append((net, "ball via-in-pad"))
        if want_ - on - ({"R332.2", "R332.1"} if net == "VREF_RD" else set()) - ({"U5.7", "U5.8"} & on):
            unreached.append((net, sorted(want_ - on)))
    check("data side: the copper of each of the %d nets (DQ0-7 with their finger-side nets, WCK, RDQS, VREF_N0-7, VREF_RD, DAC_OUT) is ONE "
          "connected piece that reaches every pin of the net" % len(DNETS), not unreached, str(unreached[:6]))

    # ---- the delay walker
    def walk(nets, start, end, bridges=()):
        """Fastest copper path from start to end (nodes (x, y, layer)) over the wires of 'nets': returns (ps centre line, ps bends, ps leg coupling,
        mm per layer, barrels mm, vertices).  bridges: [(node a, node b, mm)] = a series resistor, counted as 0.20 mm microstrip."""
        import heapq
        g_ = defaultdict(list)
        for net in nets:
            for ka, lst in adjn[net].items():
                for kb, ln_, lay, wd in lst:
                    ps_ = ln_ * (dly["through via (per mm of barrel)"] if lay == "via" else psmm(lay, wd, net))
                    g_[ka].append((kb, ln_, ps_, lay, wd, net))
        for a_, b_, mm_ in bridges:
            g_[a_].append((b_, mm_, mm_ * dly["F.Cu/B.Cu w0.20"], "part", 0.2, None))
            g_[b_].append((a_, mm_, mm_ * dly["F.Cu/B.Cu w0.20"], "part", 0.2, None))
        best = {start: (0.0, None, None)}
        heap = [(0.0, start)]
        while heap:
            dd, u_ = heapq.heappop(heap)
            if dd > best[u_][0] + 1e-12:
                continue
            if u_ == end:
                break
            for e_ in g_[u_]:
                if e_[0] not in best or dd + e_[2] < best[e_[0]][0] - 1e-12:
                    best[e_[0]] = (dd + e_[2], u_, e_)
                    heapq.heappush(heap, (dd + e_[2], e_[0]))
        if end not in best:
            return None
        path, u_ = [], end
        while best[u_][1] is not None:
            path.append((best[u_][1], u_, best[u_][2]))
            u_ = best[u_][1]
        path.reverse()
        centre = sum(e_[2] for _a, _b, e_ in path)
        mm_ = defaultdict(float)
        barrels = []
        for a_, b_, e_ in path:
            if e_[3] == "via":
                barrels.append(round(e_[1], 4))
            else:
                mm_[e_[3]] += e_[1]
        bends, nvert = 0.0, 0
        for (a0, b0, e0), (a1, b1, e1) in zip(path, path[1:]):
            if e0[3] in ("via", "part") or e1[3] != e0[3]:
                continue
            u0, u1_ = (b0[0] - a0[0], b0[1] - a0[1]), (b1[0] - a1[0], b1[1] - a1[1])
            t_ = math.acos(max(-1.0, min(1.0, (u0[0] * u1_[0] + u0[1] * u1_[1]) / (math.hypot(*u0) * math.hypot(*u1_)))))
            if t_ > math.radians(0.5):
                bends += (bend["k_mitre0"] + bend["k_mitre1"] * e1[4]) * t_ * psmm(e1[3], e1[4], e1[5])
                nvert += 1
        legs_ps = 0.0
        tr_ = [(a_, b_, e_) for a_, b_, e_ in path if e_[3] not in ("via", "part")]
        for i_ in range(len(tr_)):
            for j_ in range(i_ + 1, len(tr_)):
                (a0, b0, e0), (a1, b1, e1) = tr_[i_], tr_[j_]
                if e0[3] != e1[3]:
                    continue
                u0, u1_ = (b0[0] - a0[0], b0[1] - a0[1]), (b1[0] - a1[0], b1[1] - a1[1])
                l0, l1 = math.hypot(*u0), math.hypot(*u1_)
                if (u0[0] * u1_[0] + u0[1] * u1_[1]) / (l0 * l1) > -0.999:
                    continue
                ex, ey = u0[0] / l0, u0[1] / l0
                dist = abs((a1[0] - a0[0]) * (-ey) + (a1[1] - a0[1]) * ex)
                if dist > 1.0:
                    continue
                tb = sorted([(a1[0] - a0[0]) * ex + (a1[1] - a0[1]) * ey, (b1[0] - a0[0]) * ex + (b1[1] - a0[1]) * ey])
                ov_ = min(l0, tb[1]) - max(0.0, tb[0])
                gap_ = dist - (e0[4] + e1[4]) / 2
                if ov_ > 1e-9 and gap_ > 0:
                    legs_ps += 2 * bend["k_leg"] * math.exp(-math.pi * gap_ / bend["leg_decay_mm"]) * ov_ * psmm(e0[3], e0[4], e0[5])
        return centre, bends, legs_ps, dict(mm_), barrels, nvert

    def finger_node(net):
        f = fin(net)[0]
        lay = fingers[f]["side"] + ".Cu"
        nn = [k_ for k_ in adjn[net] if k_[2] == lay and abs(k_[0] - fingers[f]["x_mm"]) <= 0.176 and 0.15 <= k_[1] <= ftop]
        return (min(nn, key=lambda k_: k_[1]) if nn else None), f, lay          # the end inside the finger pad (y = 2.60)

    def finger_cut(net, lay):
        """The track ends 0.10 mm inside the finger pad: that piece is not card delay (finger top = y %.2f)."""
        f = fin(net)[0]
        k_ = finger_node(net)[0]
        wd = [e_[3] for e_ in adjn[net][k_]][0]
        return (ftop - k_[1]) * psmm(lay, wd, net)

    hostps = lambda f: float(td[f]["total_mid_ps"])     # noqa: E731
    cols = D["columns"]
    tol_plan = 0.05
    # ---- write paths
    wr_tot, wr_bad, wr_rows = {}, [], []
    for n in wr:
        c_ = cols[n]
        ser, sh = c_["series"], c_["shunt"]
        fk, f, lay = finger_node(n + "_WF")
        br = [(K(place[ser]["pad1"], lay), K(place[ser]["pad2"], lay), abs(place[ser]["pad2"][1] - place[ser]["pad1"][1]))]
        r_ = walk([n, n + "_WF"], fk, K(ball_at[n], "F.Cu"), br) if fk else None
        if r_ is None:
            wr_bad.append((n, "no path"))
            continue
        card = r_[0] - r_[1] - r_[2] - finger_cut(n + "_WF", lay)
        wr_tot[n] = card + hostps(f)
        st_ = D["delays"]["write"][n]
        if abs(card - st_["card_ps"]) > tol_plan or abs(wr_tot[n] - st_["total_ps"]) > tol_plan or st_["finger"] != f or abs(st_["host_ps"] - hostps(f)) > 1e-9 \
                or r_[4] != ([] if lay == "F.Cu" else [round(zmid["B.Cu"] - zmid["F.Cu"], 4)]):
            wr_bad.append((n, round(card, 3), st_["card_ps"], r_[4]))
        wr_rows.append("%s f%d %.2f + %.1f = %.2f" % (n, f, card, hostps(f), wr_tot[n]))
    gwr = {g["name"]: g for g in d["groups"]}["WR"]
    w_lo, w_hi = min(wr_tot.values()), max(wr_tot.values())
    w_cen, w_half = (w_lo + w_hi) / 2, (w_hi - w_lo) / 2
    check("write group, every delay re-derived from the centre lines (finger top -> stub -> 240 ohm -> node via -> tooth -> line -> neck -> ball; barrel of the "
          "node via only for a back finger; every mitred vertex by the bend model): equal to the plan within %.2f ps on all eight nets" % tol_plan,
          not wr_bad and len(wr_tot) == 8, "; ".join(wr_rows) + ("; problems %s" % wr_bad if wr_bad else ""))
    dk = gwr["deskew"]
    check("write group is NOT matched on the card and the spec says so: totals %.1f .. %.1f ps = centre %.1f ps +/-%.1f (group rule +/-%.0f); the per-bit "
          "output delay that closes it is the latest total minus the net's own (0 .. %.1f ps), status '%s'; each net is held to its own planned total "
          "within +/-%.1f ps" % (w_lo, w_hi, w_cen, w_half, gwr["group_rule_ps"], w_hi - w_lo, dk["status"], gwr["tolerance_ps"]),
          gwr["matched_on_the_card"] is False and gwr["total_delay_target_ps"] is None and abs(gwr["total_delay_centre_ps"] - w_cen) <= 0.02
          and abs(gwr["spread_half_ps"] - w_half) <= 0.02 and w_half > gwr["group_rule_ps"] and gwr["tolerance_ps"] <= 1.0
          and all(abs(dk["per_bit_output_delay_ps"][n] - (w_hi - wr_tot[n])) <= 0.06 for n in wr) and min(dk["per_bit_output_delay_ps"].values()) == 0.0
          and dk["status"].startswith("accepted by the user") and "ODELAY" in dk["method"]
          and all(abs(gwr["per_net_planned_total_ps"][n] - wr_tot[n]) <= 0.02 for n in wr) and gwr["meander_total_mm"] == 0.0,
          " ".join("%s %+.1f" % (n, wr_tot[n] - w_cen) for n in sorted(wr, key=lambda n: cols[n]["x"])))
    # ---- read paths
    grd_ = {g["name"]: g for g in d["groups"]}["RD_P"]
    rd_tot, rd_bad, rd_rows, rd_in3 = {}, [], [], {}
    for n in wr:
        fk, f, lay = finger_node(n)
        r_ = walk([n], K(ball_at[n], "F.Cu"), fk) if fk else None
        if r_ is None:
            rd_bad.append((n, "no path"))
            continue
        card = r_[0] - r_[1] - r_[2] - finger_cut(n, lay)
        rd_tot[n] = card + hostps(f)
        rd_in3[n] = r_[3].get("In3.Cu", 0.0)
        st_ = D["delays"]["read"][n]
        want_b = [round(zmid["In3.Cu"] - zmid["F.Cu"], 4), round(abs(zmid[lay] - zmid["In3.Cu"]), 4)]
        if abs(card - st_["card_ps"]) > tol_plan or abs(rd_tot[n] - st_["total_ps"]) > tol_plan or r_[4] != want_b or f != rf[n]:
            rd_bad.append((n, round(card, 3), st_["card_ps"], r_[4]))
        rd_rows.append("%s f%d %.2f + %.1f = %.2f" % (n, f, card, hostps(f), rd_tot[n]))
    r_lo, r_hi = min(rd_tot.values()), max(rd_tot.values())
    r_cen, r_half = (r_lo + r_hi) / 2, (r_hi - r_lo) / 2
    rtgt = r_cen                              # the reference of the strobe: the centre of the (unmatched) read group
    check("read group, every delay re-derived from the centre lines (ball -> neck -> line -> tooth -> node via F.Cu to In3 -> In3 branch -> read via -> "
          "finger track -> finger top; two barrels; every mitred vertex by the bend model): equal to the plan within %.2f ps on all eight nets" % tol_plan,
          not rd_bad and len(rd_tot) == 8, "; ".join(rd_rows) + ("; problems %s" % rd_bad if rd_bad else ""))
    dkr = grd_["deskew"]
    check("read group is NOT matched on the card and the spec says so (read end without crossings): totals %.1f .. %.1f ps = centre %.1f ps +/-%.1f (group rule "
          "+/-%.0f); the per-bit input delay that closes it is the latest total minus the net's own (0 .. %.1f ps, inside the IDELAY range %d .. %d ps), "
          "each net is held to its own planned total within +/-%.1f ps"
          % (r_lo, r_hi, r_cen, r_half, grd_["group_rule_ps"], r_hi - r_lo, dkr["idelay_range_ps"][0], dkr["idelay_range_ps"][1], grd_["tolerance_ps"]),
          grd_["matched_on_the_card"] is False and grd_["total_delay_target_ps"] is None and abs(grd_["total_delay_centre_ps"] - r_cen) <= 0.02
          and abs(grd_["spread_half_ps"] - r_half) <= 0.02 and r_half > grd_["group_rule_ps"] and grd_["tolerance_ps"] <= 1.0
          and all(abs(dkr["per_bit_input_delay_ps"][n] - (r_hi - rd_tot[n])) <= 0.06 for n in wr) and min(dkr["per_bit_input_delay_ps"].values()) == 0.0
          and "IDELAY" in dkr["method"] and dkr["idelay_range_ps"] == [0, 1100] and max(dkr["per_bit_input_delay_ps"].values()) <= 0.25 * dkr["idelay_range_ps"][1]
          and all(abs(grd_["per_net_planned_total_ps"][n] - rd_tot[n]) <= 0.02 for n in wr) and grd_["meander_total_mm"] == 0.0
          and d["rules"]["netclasses"]["RD_DQ"]["read_plan_tolerance_ps"] == grd_["tolerance_ps"],
          " ".join("%s %+.1f" % (n, rd_tot[n] - r_cen) for n in sorted(wr, key=lambda n: fingers[rf[n]]["x_mm"])))
    # ---- RDQS and WCK
    pr_tot, pr_bad = {}, []
    for n, lay_in in (("RDQS_P", "In3.Cu"), ("RDQS_N", "In3.Cu"), ("WCK_P", "B.Cu"), ("WCK_N", "B.Cu")):
        if n.startswith("RDQS"):
            fk, f, lay = finger_node(n)
            r_ = walk([n], K(ball_at[n], lay_in), fk)
            extra = (zmid[lay_in] - zmid["F.Cu"]) * dly["through via (per mm of barrel)"]            # via-in-pad: ball (F.Cu) to In3
            card = r_[0] - r_[1] - r_[2] - finger_cut(n, lay) + extra
            st_ = D["delays"]["rdqs"][n]
        else:
            fn_ = "WCK_F_" + n[-1]
            fk, f, lay = finger_node(fn_)
            ser = D["wck"]["series"][n]
            br = [(K(place[ser]["pad1"], "F.Cu"), K(place[ser]["pad2"], "F.Cu"), abs(place[ser]["pad2"][1] - place[ser]["pad1"][1]))]
            r_ = walk([n, fn_], fk, K(ball_at[n], lay_in), br)
            extra = (zmid[lay_in] - zmid["F.Cu"]) * dly["through via (per mm of barrel)"]            # via-in-pad: B.Cu to the ball
            card = r_[0] - r_[1] - r_[2] - finger_cut(fn_, lay) + extra
            st_ = D["delays"]["wck"][n]
        pr_tot[n] = card + hostps(f)
        if abs(card - st_["card_ps"]) > tol_plan or st_["finger"] != f:
            pr_bad.append((n, round(card, 3), st_["card_ps"]))
    gq_ = {g["name"]: g for g in d["groups"]}["RD_RDQS"]
    rq_off = (pr_tot["RDQS_P"] + pr_tot["RDQS_N"]) / 2 - r_cen
    rq_mm = {n: D["delays"]["rdqs"][n]["mm"] for n in ("RDQS_P", "RDQS_N")}
    check("RDQS pair, re-derived (ball -> via-in-pad to In3 -> In3 leg -> via just above the fingers -> F.Cu drop -> finger top; bends and the coupling of the "
          "two legs of the skew bump by the bend model): RDQS_P %.2f ps, RDQS_N %.2f ps total = %+.2f ps P to N (limit +/-%.0f); NOT matched to the read group "
          "(which is not matched on the card): the strobe arrives %+.2f ps from its centre (%.1f ps), %+.1f ps from the latest and %+.1f ps from the earliest bit "
          "(inside the span of the eight bits), as the spec states; equal to the plan within %.2f ps; In3 %.2f / %.2f mm, F.Cu %.2f / %.2f mm per leg"
          % (pr_tot["RDQS_P"], pr_tot["RDQS_N"], pr_tot["RDQS_P"] - pr_tot["RDQS_N"], gq_["tolerance_ps"],
             rq_off, r_cen, (pr_tot["RDQS_P"] + pr_tot["RDQS_N"]) / 2 - r_hi, (pr_tot["RDQS_P"] + pr_tot["RDQS_N"]) / 2 - r_lo, tol_plan,
             rq_mm["RDQS_P"]["In3.Cu"], rq_mm["RDQS_N"]["In3.Cu"], rq_mm["RDQS_P"]["F.Cu"], rq_mm["RDQS_N"]["F.Cu"]),
          not [b_ for b_ in pr_bad if b_[0].startswith("RDQS")] and abs(pr_tot["RDQS_P"] - pr_tot["RDQS_N"]) <= 0.2 and gq_["tolerance_ps"] == 1.0
          and gq_["tolerance_to_dq_ps"] is None and gq_["total_delay_target_ps"] is None and abs(gq_["strobe_minus_dq_group_ps"] - rq_off) <= 0.06
          # the strobe is not on the centre of the group (the pair stays on In3 down to its fingers: user instruction of the final fix pass); it must
          # arrive inside the span of the eight bits, so that the deskew table delays data only, never the strobe
          and r_lo <= (pr_tot["RDQS_P"] + pr_tot["RDQS_N"]) / 2 <= r_hi and d["rules"]["netclasses"]["RD_RDQS"]["match_to_dq_ps"] is None
          # the outer-layer part of each leg is the short drop from the via pair to the finger top, no longer than the shortest read finger track + 0.5 mm
          and all(rq_mm[n]["F.Cu"] <= min(D["read"][m_]["read_via"][1] for m_ in wr) - fp["outline"]["finger_top_y"] + 0.1 + 0.5 for n in rq_mm)
          and all(rq_mm[n]["In3.Cu"] >= 40.0 for n in rq_mm)
          and all(abs(gq_["per_net_planned_total_ps"][n] - pr_tot[n]) <= 0.02 for n in ("RDQS_P", "RDQS_N")),
          str(pr_bad))
    gk_ = {g["name"]: g for g in d["groups"]}["WR_WCK"]
    check("WCK pair, re-derived (finger top -> stub -> 91 ohm -> 0.20 mm link -> node via -> B.Cu leg -> via-in-pad -> ball): WCK_P %.2f ps, WCK_N %.2f ps "
          "total = %+.2f ps P to N (limit +/-%.0f), no skew bump; equal to the plan within %.2f ps"
          % (pr_tot["WCK_P"], pr_tot["WCK_N"], pr_tot["WCK_P"] - pr_tot["WCK_N"], gk_["tolerance_ps"], tol_plan),
          not [b_ for b_ in pr_bad if b_[0].startswith("WCK")] and abs(pr_tot["WCK_P"] - pr_tot["WCK_N"]) <= gk_["tolerance_ps"] - 0.2 and gk_["tolerance_ps"] == 1.0
          and gk_["skew_bump"] is None and abs(gk_["planned_skew_at_the_balls_ps"] - (pr_tot["WCK_P"] - pr_tot["WCK_N"])) <= 0.02, str(pr_bad))

    # ---- topology of every DQ column: the T at the divider
    ncw = d["rules"]["netclasses"]["WR_DQ"]
    tbad, trow, edge_far = [], [], 0.0
    colx = sorted(wr, key=lambda n: cols[n]["x"])
    for n in wr:
        c_ = cols[n]
        ser, sh = place[c_["series"]], place[c_["shunt"]]
        nv = [v for v in fvias if v["net"] == n and abs(v["x"] - c_["x"]) < 1e-6 and abs(v["y"] - D["rows_y"]["node_via"]) < 1e-6]
        f = wf[n]
        sd = fingers[f]["side"]
        okc = (len(nv) == 1 and ser["x"] == sh["x"] == c_["x"] and ser["y"] == sh["y"] and ser["side"] == sd and sh["side"] != sd
               and parts[c_["series"]]["pins"] == {"1": n + "_WF", "2": n} and parts[c_["shunt"]]["pins"] == {"1": n, "2": "GND"}
               and ser["pad2"][1] > ser["y"] and sh["pad1"][1] > sh["y"] and ohms(parts[c_["series"]]["value"]) == 240.0 and ohms(parts[c_["shunt"]]["value"]) == 150.0)
        # copper from the node via to the two lands: the link to the pad centre, plus the barrel when the land is on the far face from the tooth (F.Cu)
        d_ser = abs(D["rows_y"]["node_via"] - ser["pad2"][1]) + (0.0 if ser["side"] == "F" else zmid["B.Cu"] - zmid["F.Cu"])
        d_sh = abs(D["rows_y"]["node_via"] - sh["pad1"][1]) + (0.0 if sh["side"] == "F" else zmid["B.Cu"] - zmid["F.Cu"])
        gap_land = abs(D["rows_y"]["node_via"] - ser["pad2"][1]) - VR - geom[R0201][1] / 2      # via pad edge to land edge
        top_ = max(ser["pad2"][1], sh["pad1"][1]) + geom[R0201][1] / 2
        edge_far = max(edge_far, top_)
        # the three copper pieces meet on the node via: F.Cu tooth, In3 branch, and the two links
        at_node = {(w_["layer"], w_["role"]) for w_ in DW if w_["net"] == n and any(math.hypot(q_[0] - c_["x"], q_[1] - D["rows_y"]["node_via"]) < 1e-6
                                                                                     for q_ in (w_["path"][0], w_["path"][-1]))}
        okc &= {r for l, r in at_node} == {"line and tooth", "read branch from the node via", "series resistor - node via", "node via - shunt"}
        okc &= d_ser <= ncw["t_to_resistor_max_mm"] and d_sh <= ncw["t_to_resistor_max_mm"] and top_ <= ncw["divider_to_card_edge_max_mm"] and abs(gap_land - 0.25) < 1e-6
        if not okc:
            tbad.append(n)
        trow.append("%s x %.3f: series %s on %s %.2f mm, shunt %s on %s %.2f mm" % (n, c_["x"], c_["series"], ser["side"], d_ser, c_["shunt"], sh["side"], d_sh))
    check("T at the divider: for every DQ the node via stands on the column axis, the 240 ohm series resistor (finger's face) and the 150 ohm shunt (other face) "
          "sit back to back south of it with their lands 0.25 mm from the via pad, and the F.Cu tooth, the In3 read branch and both links meet on that via. "
          "Copper from the T to each land (through the barrel for the far face) <= %.1f mm; all sixteen lands within %.2f mm of the card edge (limit %.1f)"
          % (ncw["t_to_resistor_max_mm"], edge_far, ncw["divider_to_card_edge_max_mm"]), not tbad, "; ".join(trow))
    # comb: lowest line - westmost column; teeth straight down the axis; one parallel fan
    lvl = {n: cols[n]["level_y"] for n in wr}
    lines_ = {n: [tuple(q_) for w_ in DW if w_["net"] == n and w_["role"] == "line and tooth" for q_ in w_["path"]] for n in wr}
    comb_ok = sorted(wr, key=lambda n: lvl[n]) == colx
    fan = []
    for n in wr:
        pl_ = lines_[n]
        comb_ok &= abs(pl_[-1][0] - cols[n]["x"]) < 1e-6 and abs(pl_[-2][0] - cols[n]["x"]) < 1e-6 and pl_[-2][1] > pl_[-1][1] \
            and any(abs(a_[1] - lvl[n]) < 1e-6 and abs(b_[1] - lvl[n]) < 1e-6 and b_[0] > a_[0] for a_, b_ in segs(pl_))
        dg = [(a_, b_) for a_, b_ in segs(pl_) if abs(abs(b_[0] - a_[0]) - abs(b_[1] - a_[1])) < 1e-6 and a_[0] > 27.0]
        comb_ok &= len(dg) == 1 and dg[0][1][0] > dg[0][0][0] and dg[0][1][1] < dg[0][0][1]
        fan.append((n, round(dg[0][1][1], 3), round(math.hypot(dg[0][1][0] - dg[0][0][0], dg[0][1][1] - dg[0][0][1]), 3)))
    upper = [q_ for q_ in fan if q_[0] in colx[2:]]
    comb_ok &= len({q_[1] for q_ in upper}) == 1 and upper[0][1] == D["rows_y"]["fan_end"]
    pitch_lv = sorted(lvl.values())
    check("comb on F.Cu: the lowest line takes the westmost column (order %s), every line runs east on its level, turns once on a 45-degree run "
          "and comes straight down its column axis into the node via; the six upper lines turn on one parallel fan that ends on y = %.2f; levels %s "
          "(0.55 mm pitch among the six upper lines and between the two lower ones)" % (" ".join(colx), D["rows_y"]["fan_end"], pitch_lv),
          comb_ok and all(abs(b_ - a_ - 0.55) < 1e-9 for a_, b_ in zip(pitch_lv[2:], pitch_lv[3:])) and abs(pitch_lv[1] - pitch_lv[0] - 0.55) < 1e-9, str(fan))
    # read branches: lane, level, order, pitch, ramp, via on its level above its finger
    rdd_ = D["read"]
    PIT = d["rules"]["netclasses"]["RD_DQ"]["bundle_pitch"]
    rb_ok, rb_rows = True, []
    order_in3 = sorted(wr, key=lambda n: cols[n]["in3_level_at_the_field"])
    for k_, n in enumerate(order_in3):
        q_ = rdd_[n]
        pl_ = [tuple(p_) for p_ in q_["in3_route"]]
        rb_ok &= pl_ == [tuple(p_) for w_ in DW if w_["net"] == n and w_["layer"] == "In3.Cu" for p_ in w_["path"]]
        rb_ok &= abs(cols[n]["in3_level_at_the_field"] - (D["rows_y"]["node_via"] + PIT * k_)) < 1e-9 and colx.index(n) == 7 - k_
        rb_ok &= abs(pl_[0][0] - cols[n]["x"]) < 1e-9 and abs(pl_[0][1] - D["rows_y"]["node_via"]) < 1e-9
        rb_ok &= abs(pl_[-1][1] - q_["level_y"]) < 1e-9 and abs(pl_[-2][1] - q_["level_y"]) < 1e-9 and pl_[-1] == tuple(q_["read_via"])
        rb_ok &= any(v["net"] == n and abs(v["x"] - pl_[-1][0]) < 1e-6 and abs(v["y"] - pl_[-1][1]) < 1e-6 for v in fvias)
        rb_ok &= abs(pl_[-1][0] - fingers[q_["p_finger"]]["x_mm"]) <= 0.48 and q_["p_finger"] == rf[n]
        rb_ok &= all(b_[0] >= a_[0] - 1e-9 and b_[1] >= a_[1] - 1e-9 for a_, b_ in segs(pl_))       # never west, never south
        if k_ >= 2:        # riser in the lane 0.40 mm east of the column
            rb_ok &= abs(pl_[1][0] - cols[n]["x"] - 0.40) < 1e-9 and abs(pl_[2][0] - pl_[1][0]) < 1e-9
        rb_rows.append("%s level %.3f (raised %.3f)" % (n, q_["level_y"], q_["raised_mm"]))
    lv_end = [rdd_[n]["level_y"] for n in order_in3]
    rs = [rdd_[n]["raised_mm"] for n in order_in3]
    rb_ok &= all(b_ - a_ >= PIT - 1e-9 for a_, b_ in zip(lv_end, lv_end[1:])) and all(b_ >= a_ - 1e-9 for a_, b_ in zip(rs, rs[1:])) and min(rs) >= -1e-9 \
        and abs(lv_end[0] - (D["rows_y"]["node_via"] + D["knobs"]["ramp_mm"])) < 1e-9
    check("read branches on In3: each starts on its node via, takes the lane 0.40 mm east of its column (the two eastmost go straight), runs north to "
          "its level (6.00 + %.2f per column, west column on top), east in the bundle, up one 45-degree ramp and east to a via on its own level above "
          "its read finger; nothing runs west or south. Levels at the read end are %.2f mm apart or more, from y = %.2f; a raised line takes the lines "
          "above it with it (raises non-decreasing: %s)" % (PIT, PIT, lv_end[0], " ".join("%.3f" % v for v in rs)), rb_ok, "; ".join(rb_rows))
    # are these the best levels?  Re-derived: smallest spread over the raises (non-decreasing, >= 0) with the slopes measured on the plan
    k_lv = D["knobs"]["ps_per_mm_of_level"]
    base_tot = [rd_tot[n] - rdd_[n]["raised_mm"] * k_lv for n in order_in3]

    def spread_for(lo_edge):
        cur, tot_ = 0.0, []
        for t_ in base_tot:
            cur = max(cur, (lo_edge - t_) / k_lv, 0.0)
            tot_.append(t_ + cur * k_lv)
        return (max(tot_) - min(tot_)) / 2
    best_half = min(spread_for(min(base_tot) + (max(base_tot) - min(base_tot)) * j_ / 2000.0) for j_ in range(2001))
    check("read levels: no level set does better for this finger assignment: with raises that are non-decreasing in the order of the bundle the "
          "smallest half spread is %.2f ps; the plan has %.2f ps with NO line raised (the late lines are the top ones, and a raised line takes them with it): "
          "the read vias stand on the regular staircase, %.2f mm per line" % (best_half, (r_hi - r_lo) / 2, PIT),
          (r_hi - r_lo) / 2 <= best_half + 0.05 and abs(D["knobs"]["read_half_spread_ps"] - (r_hi - r_lo) / 2) <= 0.02 and max(rs) <= 1e-9
          and all(abs(b_ - a_ - PIT) < 1e-9 for a_, b_ in zip(lv_end, lv_end[1:])))
    # read end without crossings, re-derived from the centre lines: no read finger track crosses the In3 line of another bit in plan view,
    # the fingers are taken west -> east by the In3 lines bottom -> top, and the pairs used are the admissible ones of bank 66
    def _sx(a_, b_, c_, e_):
        d1 = (b_[0] - a_[0], b_[1] - a_[1])
        d2 = (e_[0] - c_[0], e_[1] - c_[1])
        den = d1[0] * d2[1] - d1[1] * d2[0]
        if abs(den) < 1e-12:
            return False
        t_ = ((c_[0] - a_[0]) * d2[1] - (c_[1] - a_[1]) * d2[0]) / den
        u_ = ((c_[0] - a_[0]) * d1[1] - (c_[1] - a_[1]) * d1[0]) / den
        return -1e-9 <= t_ <= 1 + 1e-9 and -1e-9 <= u_ <= 1 + 1e-9
    rtrk = {n: [tuple(q_) for w_ in DW if w_["net"] == n and w_["role"] == "read finger track" for q_ in w_["path"]] for n in wr}
    rin3 = {n: [tuple(q_) for w_ in DW if w_["net"] == n and w_["role"] == "read branch from the node via" for q_ in w_["path"]] for n in wr}
    rex = [(a, b) for a in wr for b in wr if a != b and any(_sx(p_, q_, c_, e_) for p_, q_ in segs(rtrk[a]) for c_, e_ in segs(rin3[b]))]
    fx_order = [fingers[rf[n]]["x_mm"] for n in order_in3]
    strobe_pairs = sorted(f for f in pm if pm[f]["bank"] == "66" and pm[f]["pn"] == "P" and abs(float(pm[f]["host_main_width_mil"] or 0) - 6.5) < 1e-9)
    adm = sorted(f for f in pm if pm[f]["bank"] == "66" and pm[f]["pn"] == "P" and pm[f]["partner_finger"][:1].isdigit() and f not in strobe_pairs
                 and not (D["reserved"]["v1p2_feed_corridor"]["x"][0] <= fingers[f]["x_mm"] <= D["reserved"]["v1p2_feed_corridor"]["x"][1])
                 and fingers[f]["x_mm"] < fingers[219]["x_mm"])
    check("READ END WITHOUT CROSSINGS: no read finger track crosses the In3 read line of another bit (counted here on the centre lines: %d); the In3 lines "
          "bottom -> top (%s) take the read fingers west -> east (%s); the pairs used are exactly the admissible ones of bank 66 west of the RDQS pair (P pin with its "
          "N partner on the socket, not a host-coupled strobe pair %s, not in the F.Cu corridor of the 1.2 V feed): %s, so the order is unique"
          % (len(rex), " ".join(order_in3), " ".join(str(rf[n]) for n in order_in3), strobe_pairs, adm),
          not rex and all(b_ > a_ for a_, b_ in zip(fx_order, fx_order[1:])) and sorted(rf.values()) == adm and len(adm) == 8
          and D["knobs"]["read_end_crossings"] == 0 and d["rules"]["netclasses"]["RD_DQ"]["read_end_crossings_max"] == 0
          and [D["read_end_choice"]["chosen"][n] for n in order_in3] == [rf[n] for n in order_in3], str(rex))

    # ---- same-layer clearances between the wires of the data side, and to pads and vias
    from shapely.geometry import LineString as LS_, Point as PT_, box as BX_
    wshape = [(w_, LS_(w_["path"]).buffer(w_["width"] / 2, 16)) for w_ in DW]
    u1box = BX_(fp["U1"]["body"]["x"][0] - 0.25, fp["U1"]["body"]["y"][0] - 0.25, fp["U1"]["body"]["x"][1] + 0.25, fp["U1"]["body"]["y"][1] + 0.25)
    ra_ = {a_["name"]: BX_(a_["x"][0], a_["y"][0], a_["x"][1], a_["y"][1]) for a_ in fp["rule_areas"]}
    same_x, gaps = [], defaultdict(lambda: (9.9, None))

    def cls_w(w_):
        n_ = w_["net"]
        if re.fullmatch(r"DQ\d", n_):
            return "RD" if w_["layer"] == "In3.Cu" or w_["role"] == "read finger track" else "WR"
        return {"WCK": "WCK", "RDQS": "RDQS", "REF": "VREF", "GND": "GND", "RESET": "SLOW", "WR": "WRF"}[w_["group"]]

    def upd(key, val, what):
        if val < gaps[key][0]:
            gaps[key] = (val, what)
    for i_, (wa, sa) in enumerate(wshape):
        for wb, sb in wshape[i_ + 1:]:
            if wa["layer"] != wb["layer"] or wa["net"] == wb["net"]:
                continue
            dd = sa.distance(sb)
            if dd < 1e-6:
                same_x.append((wa["net"], wb["net"], wa["layer"]))
                continue
            if dd > 1.5:
                continue
            ca, cb = sorted((cls_w(wa), cls_w(wb)))
            lay = wa["layer"]
            what = "%s / %s on %s" % (wa["net"], wb["net"], lay)
            if sa.intersects(u1box) or sb.intersects(u1box):        # the rules below hold outside the courtyard of U1: clip both there
                sa_, sb_ = sa.difference(u1box), sb.difference(u1box)
                if sa_.is_empty or sb_.is_empty:
                    continue
                dd = sa_.distance(sb_)
            if (ca, cb) == ("WR", "WR") and lay == "F.Cu":
                # the bundle: lines and teeth; the necks end 0.025 mm outside the courtyard line (x = 26.9), where they are 0.226 mm apart
                la_ = LS_(wa["path"]).intersection(BX_(27.2, 0, 70, 30))
                lb_ = LS_(wb["path"]).intersection(BX_(27.2, 0, 70, 30))
                if not la_.is_empty and not lb_.is_empty:
                    upd("F.Cu DQ bundle", la_.distance(lb_) - 0.15, what)
            elif (ca, cb) == ("RD", "RD") and lay == "In3.Cu":
                upd("In3 DQ - DQ", dd, what)
            elif "WCK" in (ca, cb) and lay == "B.Cu" and ca != cb:
                upd("B.Cu WCK to another track", dd, what)
            elif "WCK" in (ca, cb) and lay == "F.Cu" and ca != cb and {wa["group"], wb["group"]} == {"WCK", "WR"} and "WCK_F" not in wa["net"] + wb["net"] and "_WF" not in wa["net"] + wb["net"]:
                upd("F.Cu WCK column links to the DQ4 tooth", dd, what)
            elif "RDQS" in (ca, cb) and lay == "F.Cu" and ca != cb and "GND" not in (ca, cb) and "VREF" not in (ca, cb):
                rq_ = sa if cls_w(wa) == "RDQS" else sb
                if rq_.within(ra_["rdqs_termination"]):
                    upd("F.Cu RDQS load link to a read track (inside the area rdqs_termination)", dd, what)
                elif rq_.within(ra_["rdqs_via_pair"]):
                    upd("F.Cu RDQS step from its via to the pair, to a read track (inside the area rdqs_via_pair)", dd, what)
                else:
                    upd("F.Cu RDQS to another track", dd, what)
            elif "RDQS" in (ca, cb) and lay == "In3.Cu" and ca != cb and "GND" not in (ca, cb) and "VREF" not in (ca, cb):
                upd("In3 RDQS to another track", dd, what)
            elif "VREF" in (ca, cb) and ca != cb and "GND" not in (ca, cb) and "SLOW" not in (ca, cb):
                vr_ = sa if cls_w(wa) == "VREF" else sb
                upd("VREF to a switching track" + (" (inside the area vref_east_end)" if vr_.intersects(ra_["vref_east_end"]) and "DQ" in wa["net"] + wb["net"] else ""), dd, what)
    # In3 lines to the In3 pads of foreign vias (node vias and read vias carry one; GND vias have none: hole + 0.20)
    for wa, sa in wshape:
        if wa["layer"] != "In3.Cu" or not re.fullmatch(r"DQ\d", wa["net"]):
            continue
        for v in fvias:
            if v["net"] == wa["net"]:
                continue
            dd = sa.distance(PT_(v["x"], v["y"]))
            if dd > 1.0:
                continue
            if re.fullmatch(r"DQ\d", v["net"]):
                upd("In3 DQ line to the In3 pad of a foreign DQ via", dd - VR, "%s to the %s via at (%.3f, %.3f)" % (wa["net"], v["net"], v["x"], v["y"]))
            else:
                upd("In3 DQ line to the hole of a via without In3 pad", dd - v["drill"] / 2, "%s to the %s via at (%.3f, %.3f)" % (wa["net"], v["net"], v["x"], v["y"]))
    rdc = d["rules"]["netclasses"]["RD_DQ"]
    wk_ = d["rules"]["netclasses"]["WR_WCK"]
    vrf = d["rules"]["netclasses"]["VREF"]
    lim = {"F.Cu DQ bundle": ncw["gap_min_outside_u1"], "In3 DQ - DQ": rdc["in3_gap_min"], "B.Cu WCK to another track": wk_["pair_to_other_min"],
           "F.Cu WCK column links to the DQ4 tooth": wk_["f_cu_links_to_other_tracks_min"], "F.Cu RDQS to another track": d["rules"]["netclasses"]["RD_RDQS"]["pair_to_other_min"],
           "F.Cu RDQS load link to a read track (inside the area rdqs_termination)": 0.40,
           "F.Cu RDQS step from its via to the pair, to a read track (inside the area rdqs_via_pair)": 0.60,
           "In3 RDQS to another track": d["rules"]["netclasses"]["RD_RDQS"]["pair_to_other_min"],
           "VREF to a switching track": vrf["keep_from_switching_nets"],
           "VREF to a switching track (inside the area vref_east_end)": vrf["keep_from_switching_nets_east_end"],
           "In3 DQ line to the In3 pad of a foreign DQ via": rdc["in3_to_foreign_via_pad_min"], "In3 DQ line to the hole of a via without In3 pad": 0.20}
    low = [(k_, round(gaps[k_][0], 4), lim[k_]) for k_ in lim if gaps[k_][0] < lim[k_] - 1.5e-3]
    check("data side, spacing on one layer (centre lines at their widths): no line touches or crosses a line of another net; "
          + "; ".join("%s >= %.2f (%.3f)" % (k_, lim[k_], gaps[k_][0]) for k_ in lim if gaps[k_][1]),
          not same_x and not low and all(gaps[k_][1] for k_ in ("F.Cu DQ bundle", "In3 DQ - DQ", "B.Cu WCK to another track", "F.Cu RDQS to another track", "In3 RDQS to another track")),
          "crossing %s; below the limit %s; smallest: %s" % (same_x[:4], low, "; ".join("%s: %s" % (k_, gaps[k_][1]) for k_ in lim if gaps[k_][1])))
    # the corridor reserved for the 1.2 V feed
    cor = D["reserved"]["v1p2_feed_corridor"]
    cbx = BX_(cor["x"][0], cor["y"][0], cor["x"][1], cor["y"][1])
    in_cor = [(w_["net"], w_["role"]) for w_, s_ in wshape if w_["layer"] == cor["layer"] and s_.intersects(cbx)]
    via_cor = [(v["net"], v["x"], v["y"]) for v in fvias if cbx.buffer(-0.01).contains(PT_(v["x"], v["y"]))]
    pwf = fp["power"]["v1p2_feed"]
    stp = pwf["strip"]
    tab_ = fp["power"]["v1p2_island"]["extensions"]["feed_tab"]
    isl_vias = [v for v in fp["power"]["build"]["vias"] if v["net"] == "V1P2" and v["y"] < 6.0]
    bulk_e = all(po_[r_]["pads"]["1"][0] < po_[r_]["pads"]["2"][0] and po_[r_]["courtyard"][1] >= 4.0 and po_[r_]["courtyard"][2] <= 48.5
                 and po_[r_]["pads"]["1"][0] - 0.5 < stp["x"] + stp["width"] / 2 for r_ in ("C502", "C512"))
    check("corridor reserved for the 1.2 V feed (F.Cu, x %.1f .. %.1f, y %.1f .. %.1f): no wire and no via of the data side stands in it on F.Cu; the power "
          "stage builds a %.1f mm STRIP in it (x %.2f .. %.2f, not a pour), C502 / C512 stand east of its foot with the V1P2 pad on the strip and their GND on VSS finger vias; the "
          "island is on In2 with its halves joined by the bridge, and the four vias of the strip stand on its tab (x %.2f .. %.2f, up to y = %.2f)"
          % (cor["x"][0], cor["x"][1], cor["y"][0], cor["y"][1], stp["width"], stp["x"] - stp["width"] / 2, stp["x"] + stp["width"] / 2,
             tab_["x"][0], tab_["x"][1], tab_["y"][1]),
          not in_cor and not via_cor and bulk_e and pwf["status"].startswith("BUILT") and len(pwf["alternatives"]) >= 3
          and fp["power"]["v1p2_island"]["halves"].get("joined") and "plan" not in pwf
          and cor["x"][0] + 0.3 <= stp["x"] - stp["width"] / 2 and stp["x"] + stp["width"] / 2 <= cor["x"][1] - 0.3 and stp["width"] <= 1.5 + 1e-9
          and len(isl_vias) == 4
          and all(tab_["x"][0] + 0.2 <= v["x"] <= tab_["x"][1] - 0.2 and v["y"] <= tab_["y"][1] - 0.2 and abs(v["x"] - stp["x"]) < stp["width"] / 2 - 0.2 for v in isl_vias),
          "%s %s" % (in_cor, via_cor))

    # ---- return vias
    gvias = [(v["x"], v["y"]) for v in fvias if v["net"] == "GND"]
    rv_rows, rv_bad = [], []
    for n in wr:
        x_, y_ = rdd_[n]["read_via"]
        dd = min(math.hypot(x_ - gx, y_ - gy) for gx, gy in gvias)
        rv_rows.append("%s %.2f" % (n, dd))
        if not (rdc["return_via_min_mm"] - 1e-9 <= dd <= rdc["return_via_max_mm"] + 1e-9):
            rv_bad.append((n, round(dd, 3)))
    nd_rows, nd_far = [], []
    for n in colx:
        dd = min(math.hypot(cols[n]["x"] - gx, D["rows_y"]["node_via"] - gy) for gx, gy in gvias)
        nd_rows.append("%s %.2f" % (n, dd))
        if dd > rdc["return_via_max_mm"]:
            nd_far.append(n)
    rqv = D["rdqs"]["via_pair"]
    rq_d = [min(math.hypot(x_ - gx, y_ - gy) for gx, gy in gvias) for x_, y_ in rqv]
    check("return vias: a GND via within %.2f .. %.2f mm of every read via (%s) and within 1.0 mm of both vias of the RDQS pair (%.2f / %.2f mm, mirror "
          "images about the pair); the node vias have one where a through via fits: %s - the four inner columns (%s) have the shunt GND via only "
          "(0.8 mm pitch, tooth on the axis, riser in the lane): recorded, not fixed"
          % (rdc["return_via_min_mm"], rdc["return_via_max_mm"], ", ".join(rv_rows), rq_d[0], rq_d[1], ", ".join(nd_rows), " ".join(nd_far)),
          not rv_bad and max(rq_d) <= 1.0 and abs(rq_d[0] - rq_d[1]) < 1e-6 and sorted(nd_far) == sorted(["DQ0", "DQ3", "DQ2", "DQ6"]), str(rv_bad))

    # ---- reference networks
    rfn = D["reference"]
    rn_bad = []
    for n in wr:
        q_ = rfn[n]
        i_ = int(n[2:])
        rr, cc = place[q_["resistor"]], place[q_["capacitor"]]
        nf = q_["n_finger"]
        sd = fingers[nf]["side"]
        vss = q_["vss_finger_of_the_capacitor"]
        okr = (j1[str(nf)] == "VREF_N%d" % i_ and rr["side"] == cc["side"] == sd and abs(rr["x"] - fingers[nf]["x_mm"]) < 1e-9
               and j1[str(vss)] == "GND" and fingers[vss]["side"] == sd and abs(cc["x"] - fingers[vss]["x_mm"]) < 1e-9 and abs(abs(cc["x"] - rr["x"]) - 0.5) < 1e-9
               and (cc["x"] - rr["x"]) * (rr["x"] - fingers[rf[n]]["x_mm"]) > 0          # the capacitor on the side away from the P finger
               and parts[q_["resistor"]]["pins"] == {"1": "VREF_N%d" % i_, "2": "VREF_RD"} and parts[q_["capacitor"]]["pins"] == {"1": "VREF_RD", "2": "GND"}
               and rr["pad1"][1] < rr["y"] and cc["pad2"][1] < cc["y"] and rr["y"] == D["reference_rows_y"]["resistor"] and cc["y"] == D["reference_rows_y"]["capacitor"]
               and fingers[rf[n]]["side"] != sd)                                         # the read track runs on the other face
        gl = [w_ for w_ in DW if w_["role"] == "100 nF - VSS finger via" and abs(w_["path"][0][0] - cc["x"]) < 1e-9 and w_["layer"] == sd + ".Cu"]
        okr &= len(gl) == 1 and any(vss in v["fingers"] and abs(v["x"] - gl[0]["path"][-1][0]) < 1e-9 and abs(v["y"] - gl[0]["path"][-1][1]) < 1e-9 for v in fgv)
        if not okr:
            rn_bad.append(n)
    court_low = min(court[r_][1][1] for r_ in court if re.fullmatch(r"[RC]32\d", r_))
    check("reference networks: per N finger, on its own face, 39 ohm on the finger axis (row y = %.2f) and 100 nF on the axis of the neighbouring VSS finger "
          "on the side away from the P finger (row y = %.2f), the capacitor's GND land on the via of that VSS finger; the read track runs on the "
          "other face; lowest courtyard edge of the two rows y = %.3f (keep-out up to 4.00)" % (D["reference_rows_y"]["resistor"], D["reference_rows_y"]["capacitor"], court_low),
          not rn_bad and court_low >= 4.0 + 0.04 and D["reference_rows_y"]["resistor"] == 4.75 and D["reference_rows_y"]["capacitor"] == 6.15, str(rn_bad))
    vref_in3 = [w_["role"] for w_ in DW if w_["group"] == "REF" and w_["layer"] == "In3.Cu"]
    check("VREF_RD distribution: bus on F.Cu along the north ends of the front networks, one via, B.Cu to the network of N finger 216 and on to a via below TP4; "
          "no VREF copper on In3", not vref_in3 and sum(1 for v in fvias if v["net"] == "VREF_RD") == 2)
    gr_q = next(g_ for g_ in d["groups"] if g_["name"] == "RD_RDQS")
    # RDQS loads: beside the coupled pair, on the axes of the two VSS finger pairs next to the drop, GND on those finger vias (no field via)
    ld_bad = []
    for n_, r_ in (("RDQS_P", "R309"), ("RDQS_N", "R310")):
        pl_ = place[r_]
        lk_ = [w_ for w_ in DW if w_["net"] == n_ and w_["role"] == "load link"]
        gl_ = [w_ for w_ in DW if w_["net"] == "GND" and w_["role"] == "load - VSS finger via" and abs(w_["path"][0][0] - pl_["x"]) < 1e-9]
        leg_ = [w_ for w_ in DW if w_["net"] == n_ and w_["role"] == "leg" and w_["layer"] == "F.Cu"][0]["path"]
        ok_ = (pl_["side"] == "F" and parts[r_]["pins"] == {"1": n_, "2": "GND"} and pl_["pad1"][1] > pl_["y"] > pl_["pad2"][1]
               and len(lk_) == 1 and len(gl_) == 1 and lk_[0]["layer"] == gl_[0]["layer"] == "F.Cu"
               and tuple(lk_[0]["path"][-1]) == tuple(pl_["pad1"]) and any(tuple(q_) == tuple(lk_[0]["path"][0]) for q_ in leg_)
               and abs(plen_(lk_[0]["path"]) - 0.40) < 1e-6 and tuple(gl_[0]["path"][0]) == tuple(pl_["pad2"])
               and any(v["net"] == "GND" and abs(v["x"] - gl_[0]["path"][-1][0]) < 1e-9 and abs(v["y"] - gl_[0]["path"][-1][1]) < 1e-9 for v in fgv)
               and abs(gl_[0]["path"][0][0] - gl_[0]["path"][-1][0]) < 1e-9 and court[r_][1][1] >= 4.0 + 0.04)
        if not ok_:
            ld_bad.append(r_)
    oc_ = gr_q["outer_layer_cases"]
    rqmm = D["delays"]["rdqs"]["RDQS_N"]["mm"]
    oc_nom = gr_q["strobe_minus_dq_group_ps"]
    check("strobe and read group over the outer copper / mask the fab may deliver (%s): the offset of the strobe from the centre of the DQ group is %s ps "
          "(nominal %+.2f; nothing is matched to it, the figure moves by at most %.1f ps), the DQ group stays +/-%.1f .. %.1f ps and the pair P to N within %.2f ps "
          "(limit 1); the pair is stripline for %.0f %% of its length"
          % (", ".join("%s Cu %.3f mask %.3f" % (k_, v_["outer_copper_mm"], v_["mask_mm"]) for k_, v_ in oc_.items()),
             " / ".join("%+.1f" % v_["strobe_minus_dq_centre_ps"] for v_ in oc_.values()), oc_nom,
             max(abs(v_["strobe_minus_dq_centre_ps"] - oc_nom) for v_ in oc_.values()),
             min(v_["dq_half_spread_ps"] for v_ in oc_.values()), max(v_["dq_half_spread_ps"] for v_ in oc_.values()),
             max(abs(v_["p_minus_n_ps"]) for v_ in oc_.values()), 100 * rqmm["In3.Cu"] / (rqmm["In3.Cu"] + rqmm["F.Cu"])),
          len(oc_) >= 3 and max(abs(v_["strobe_minus_dq_centre_ps"] - oc_nom) for v_ in oc_.values()) <= 8.0
          and max(abs(v_["p_minus_n_ps"]) for v_ in oc_.values()) <= 1.0 and rqmm["In3.Cu"] >= rqmm["F.Cu"])
    lx_ = [place[r_]["x"] for r_ in ("R309", "R310")]
    check("RDQS loads R309 / R310: on F.Cu, the face of the pair, in the row of the 39 ohm resistors (y = %.2f), mirror images about the drop (x = %.3f +/- %.2f) on the axes of the "
          "VSS finger pairs 217 / 218 and 222 / 223; pad 1 (north) on a 0.40 mm side link from its leg, pad 2 (south) straight down that axis into the pair's finger via: "
          "no GND via of their own, nothing of them north of y = 5.3" % (place["R309"]["y"], sum(lx_) / 2, abs(lx_[1] - lx_[0]) / 2),
          not ld_bad and abs(sum(lx_) / 2 - D["knobs"]["rdqs_down_x"]) < 1e-9 and place["R309"]["y"] == place["R310"]["y"] == D["reference_rows_y"]["resistor"]
          and not any("load" in v["role"] and v["net"] == "GND" for v in fvias), str(ld_bad))

    # ---- crossings in plan view (different layers), counted here from the centre lines against the budget
    def proper_x(pa, pb):
        """Crossing points of two polylines (proper intersections; a touch at a vertex counts once)."""
        out = []
        for a_, b_ in segs(pa):
            for c_, e_ in segs(pb):
                d1 = (b_[0] - a_[0], b_[1] - a_[1])
                d2 = (e_[0] - c_[0], e_[1] - c_[1])
                den = d1[0] * d2[1] - d1[1] * d2[0]
                if abs(den) < 1e-12:
                    continue
                t_ = ((c_[0] - a_[0]) * d2[1] - (c_[1] - a_[1]) * d2[0]) / den
                s_ = ((c_[0] - a_[0]) * d1[1] - (c_[1] - a_[1]) * d1[0]) / den
                if -1e-9 <= t_ <= 1 + 1e-9 and -1e-9 <= s_ <= 1 + 1e-9:
                    p_ = (round(a_[0] + t_ * d1[0], 4), round(a_[1] + t_ * d1[1], 4))
                    if p_ not in out:
                        out.append(p_)
        return out
    cmdl_ = {n: [tuple(q_) for q_ in fp["command"]["columns"][n]["in3_route"]] for n in cmd}
    hs_w = [w_ for w_ in DW if w_["group"] in ("WR", "RD", "WCK", "RDQS") and w_["net"] != "GND"]
    cxd = []
    for n, pl_ in cmdl_.items():
        for w_ in hs_w:
            if w_["layer"] != "In3.Cu":
                for p_ in proper_x(pl_, [tuple(q_) for q_ in w_["path"]]):
                    cxd.append((n, w_["net"], w_["layer"], p_))
    want_c = sorted((c_["cmd"], c_["by"], c_["layer"], tuple(c_["at"])) for c_ in D["command_crossings"])
    inside_u1 = all(fp["U1"]["body"]["x"][0] <= p_[0] <= fp["U1"]["body"]["x"][1] and fp["U1"]["body"]["y"][0] <= p_[1] <= fp["U1"]["body"]["y"][1] for _n, _b, _l, p_ in cxd)
    bud = D["crossing_budget"]
    check("crossings of the command bundle by data wires, counted here from the centre lines: %d (budget %d), exactly the seven the spec lists, all inside "
          "the land field of U1: %s" % (len(cxd), bud["cmd_x_data"], "; ".join("%s x %s (%s) at (%.3f, %.2f)" % (a_, b_, l_, p_[0], p_[1]) for a_, b_, l_, p_ in sorted(cxd))),
          len(cxd) == bud["cmd_x_data"] == 7 and sorted((a_, b_, l_, (round(p_[0], 3), round(p_[1], 3))) for a_, b_, l_, p_ in cxd) == want_c and inside_u1)
    dxd = defaultdict(int)
    for i_, wa in enumerate(hs_w):
        for wb in hs_w[i_ + 1:]:
            if wa["layer"] == wb["layer"] or wa["net"].replace("_WF", "") == wb["net"].replace("_WF", ""):
                continue
            k_ = len(proper_x([tuple(q_) for q_ in wa["path"]], [tuple(q_) for q_ in wb["path"]]))
            if k_:
                dxd[tuple(sorted((cls_w(wa).replace("WRF", "WR"), cls_w(wb).replace("WRF", "WR"))))] += k_
    n_dd = sum(dxd.values())
    vref_w = [w_ for w_ in DW if w_["group"] == "REF"]
    n_hv = sum(len(proper_x([tuple(q_) for q_ in wa["path"]], [tuple(q_) for q_ in wb["path"]])) for wa in hs_w for wb in vref_w if wa["layer"] != wb["layer"])
    check("crossings among the data wires (two layers, plan view; pair legs one by one), counted here: %d (budget %d) = %s; high-speed x VREF: %d (budget %d). "
          "The count IS the lower bound of this comb and corner escape: 28 F.Cu tooth x In3 branch (each pair of bits once), 6 DQ x WCK, 14 DQ x RDQS (each leg "
          "over the six nets between its balls and bank 66, and the DQ5 neck inside U1), 4 WCK x RDQS; 0 read-end crossings (the read group is +/-%.1f ps for it, "
          "closed by IDELAY)" % (n_dd, bud["data_x_data"], dict(sorted(dxd.items())), n_hv, bud["hs_x_vref"], r_half),
          n_dd <= bud["data_x_data"] and n_hv <= bud["hs_x_vref"] and bud["data_x_data"] == 52 and bud["hs_x_vref"] == 9 and bud["same_layer"] == 0
          and bud["trans_through"] == 0 and D["knobs"]["read_end_crossings"] == 0
          and sum(v_ for k_, v_ in bud["data_x_data_breakdown"].items() if isinstance(v_, int)) == bud["data_x_data"])
    # resistor groups given to the crossing tool
    rgs = {g_["name"]: g_ for g_ in D["resistor_groups"]}
    grp_ok = set(rgs) == {"wr_div_wck", "wr_div_dq", "n_ref_west", "n_ref_east", "rdqs_term"} and sorted(rgs["wr_div_wck"]["parts"]) == ["R209", "R210", "R219", "R220"] \
        and sorted(rgs["wr_div_dq"]["parts"]) == sorted(["R%d" % (201 + i) for i in range(8)] + ["R%d" % (211 + i) for i in range(8)]) \
        and sorted(rgs["n_ref_west"]["parts"] + rgs["n_ref_east"]["parts"]) == sorted(["R%d" % (321 + i) for i in range(8)] + ["C%d" % (321 + i) for i in range(8)]) \
        and sorted(rgs["rdqs_term"]["parts"]) == ["R309", "R310"]
    dq_top = max(pd_[4][3] for pd_ in pads if pd_[0] in rgs["wr_div_dq"]["parts"])
    in3_low = min(q_[1] for w_ in DW if w_["layer"] == "In3.Cu" and re.fullmatch(r"DQ\d", w_["net"]) for q_ in w_["path"][1:])
    check("resistor groups for the crossing tool: the WCK columns and the DQ columns are two groups (and the reference networks west / east of the RDQS drop, "
          "the RDQS loads): the lands of the DQ group end at y = %.2f, the lowest In3 line that runs across the columns is at y = %.2f, north of the node vias"
          % (dq_top, D["rows_y"]["node_via"] + PIT), grp_ok and dq_top < D["rows_y"]["node_via"] - VR and in3_low >= D["rows_y"]["node_via"] - 1e-9)

    # ---- rules of the data side and the list of rule changes
    R_ = d["rules"]
    sr_ = {r_["name"]: r_ for r_ in R_["spacing_rules"]}
    chg = {c_["rule"]: c_ for c_ in R_["changes_of_the_data_replan"]}
    check("rule values of the data side: WCK pair to other tracks 0.80 mm, WCK skew limit 0.50 mm, WCK uncoupled 4.0 mm, RDQS uncoupled 4.5 mm (the rehearsed comb needed 4.75), HOST_SE "
          "minimum width 0.15 mm, In3 DQ gap 0.40 mm (DRC 0.39), bundle pitch 0.55 mm; the three VREF exceptions each have their own rule; every change "
          "against the board before the re-plan is listed with its reason (%d entries)" % len(chg),
          sr_["WCK_pair_to_other_tracks"]["min"] == 0.80 and wk_["max_skew_mm"] == 0.50 and wk_["max_uncoupled_mm"] == 4.0
          and R_["netclasses"]["RD_RDQS"]["max_uncoupled_mm"] == 4.5 and R_["netclasses"]["HOST_SE"]["layers"]["F.Cu"]["min_width"] == 0.15
          and rdc["in3_gap_min"] == 0.40 and sr_["RD_DQ_to_RD_DQ_In3"]["min"] == 0.39 and ncw["bundle_pitch"] == 0.55 == rdc["bundle_pitch"]
          and all(k_ in sr_ for k_ in ("VREF_to_switching_nets_east_end", "VREF_0201_lands", "VREF_N_stub_in_the_finger_via_row"))
          and sr_["VREF_to_switching_nets"].get("a_not_in_area") == "vref_east_end" and sr_["RDQS_pair_to_read_tracks"].get("a_not_enclosed_by_area") == ["rdqs_termination", "rdqs_via_pair"] and sr_["WR_DQ_to_WR_DQ_F_Cu"]["min"] == 0.39
          and sr_["RDQS_pair_to_other_tracks_In3"]["min"] == 0.80 and sr_["RDQS_pair_to_other_tracks_In3"]["layer"] == "In3.Cu" and "WR_DQ" in sr_["RDQS_pair_to_other_tracks_In3"]["b_classes"]
          and len(chg) >= 10 and all(c_.get("why") and c_.get("kind") for c_ in chg.values())
          and {a_["name"] for a_ in fp["rule_areas"]} == {"rdqs_termination", "rdqs_via_pair", "vref_east_end", "finger_via_row"})
    # RDQS skew bump: legs 3 h apart or more, pointing away from the other leg
    bp_ = D["rdqs"]["bump"]
    bw = [w_ for w_ in DW if w_["net"] == bp_["net"] and w_["layer"] == bp_["layer"] and w_["role"] == "leg"][0]["path"]
    vert_legs = [(a_, b_) for a_, b_ in segs(bw) if abs(a_[0] - b_[0]) < 1e-9 and bp_["x"][0] - 1e-6 <= a_[0] <= bp_["x"][1] + 1e-6]
    check("RDQS skew bump: one bump on %s only, on In3 within 2 mm east of the land field of U1 (where the skew of the ball rows arises), two legs %.2f mm apart (copper gap %.2f mm >= %.2f = 3 h), %.3f mm deep, pointing south "
          "(away from RDQS_N), eight 45-degree vertices; no other trim feature and no serpentine on any net of the data side"
          % (bp_["net"], bp_["leg_pitch"], bp_["leg_pitch"] - 0.10, R_["netclasses"]["RD_RDQS"]["bump_leg_gap_min"], bp_["depth_mm"]),
          len(vert_legs) == 2 and abs(abs(vert_legs[0][0][0] - vert_legs[1][0][0]) - bp_["leg_pitch"]) < 1e-6
          and bp_["leg_pitch"] - 0.10 >= R_["netclasses"]["RD_RDQS"]["bump_leg_gap_min"] and bp_["net"] == "RDQS_P" and bp_["side"] == "south"
          and bp_["layer"] == "In3.Cu" and 26.925 + 0.4 <= bp_["x"][0] and bp_["x"][1] <= 26.925 + 2.0
          and all(abs(abs(a_[1] - b_[1]) - bp_["depth_mm"]) < 1e-6 for a_, b_ in vert_legs) and grd_["meander_total_mm"] == 0.0)

    # ------------------------------------------------------------------ regions and planes (audit: overlapping regions, island extent)
    regs = fp["regions"]
    ov = []
    for i, a in enumerate(regs):
        for b in regs[i + 1:]:
            if set(a["layers"]) & set(b["layers"]) and min(a["x"][1], b["x"][1]) - max(a["x"][0], b["x"][0]) > 1e-6 \
                    and min(a["y"][1], b["y"][1]) - max(a["y"][0], b["y"][0]) > 1e-6:
                ov.append((a["name"], b["name"]))
    check("floorplan regions that share a copper layer do not overlap", not ov and len(regs) >= 20, "%d regions; overlaps: %s" % (len(regs), ov))
    comp = [r_ for r_ in regs if ("F.Cu" in r_["layers"] or "B.Cu" in r_["layers"])]
    outside = [r_["name"] for r_ in comp if r_["y"][0] < 4.0 - 1e-6 or r_["y"][1] > 29.5 or r_["x"][0] < 3.0 or r_["x"][1] > 66.6]
    hole = [r_["name"] for r_ in comp for h in fp["outline"]["tooling_holes"]
            if max(r_["x"][0] - h["x"], h["x"] - r_["x"][1], 0) < h["d"] / 2 + 0.3 and max(r_["y"][0] - h["y"], h["y"] - r_["y"][1], 0) < h["d"] / 2 + 0.3]
    check("every region with parts or outer-layer tracks lies inside the component area (y >= 4.0, x 3.0 .. 66.6) and clear of the tooling holes",
          not outside and not hole, "outside: %s; at a hole: %s" % (outside, hole))
    covered = {}
    for r_, (sd, rc) in court.items():
        lay = "F.Cu" if sd == "F" else "B.Cu"
        inside = [g_["name"] for g_ in regs if lay in g_["layers"] and g_["x"][0] - 1e-6 <= rc[0] and rc[2] <= g_["x"][1] + 1e-6
                  and g_["y"][0] - 1e-6 <= rc[1] and rc[3] <= g_["y"][1] + 1e-6]
        if not inside:
            covered[r_] = rc
    check("every placed part lies inside a declared region of its face", not covered, str(sorted(covered)[:8]))
    isl = [fp["power"]["vdd2h_island"]]                      # In4: the reference of B.Cu and the lower reference of In3
    routing = [r_ for r_ in regs if r_["name"] in ("wck_run_b_cu", "read_bundle_in3", "rdqs_high_run", "command_in3_tuning", "rdqs_termination_b_cu", "n_networks_b_cu")]
    under = [(r_["name"], k["layer"]) for r_ in routing for k in isl
             if min(r_["x"][1], k["x"][1]) - max(r_["x"][0], k["x"][0]) > 1e-6 and min(r_["y"][1], k["y"][1]) - max(r_["y"][0], k["y"][0]) > 1e-6]
    v1 = fp["power"]["v1p2_island"]
    vin = [v["net"] for v in fvias if v1["x"][0] <= v["x"] <= v1["x"][1] and v["y"] <= v1["y"][1] + VR + 0.2 and v["net"] != "V1P2"]
    land_bottom = min(pd_[4][1] for pd_ in pads if re.fullmatch(r"R2\d\d", pd_[0]))
    gnd_via_low = min(v["y"] for v in fvias if "shunt" in v["role"] and v["y"] < 5.0)
    vdd_f = [f for f in range(1, 261) if j1[str(f)] == "V1P2"]
    v2 = fp["power"]["vdd2h_island"]
    v2h_vias = [b for b in via_balls if u1[b] == "VDD2H"]
    n_v2h = len(v2h_vias)
    n_isl = sum(1 for b in v2h_vias if v2["x"][0] + VR <= bxy(b)[0] <= v2["x"][1] - VR and v2["y"][0] + VR <= bxy(b)[1] <= v2["y"][1] - VR)
    v2r = (v2["x"][0], v2["y"][0], v2["x"][1], v2["y"][1])
    isl_gap = min(seg_rect(a_, b_, v2r) - w_["width"] / 2 for w_ in DW if w_["group"] in ("WR", "RD", "WCK", "RDQS") and w_["layer"] in ("In3.Cu", "B.Cu")
                  for a_, b_ in segs(w_["path"]))
    # the V1P2 island is on In2 (only In3 refers to it): its shape = finger rectangle + bridge north of the key slot + feed tab
    ext_ = v1.get("extensions") or {}
    v1shape = BX_(v1["x"][0], v1["y"][0], v1["x"][1], v1["y"][1])
    for e_ in ext_.values():
        v1shape = v1shape.union(BX_(e_["x"][0], e_["y"][0], e_["x"][1], e_["y"][1]))
    in3_w = [(w_, s_) for w_, s_ in wshape if w_["layer"] == "In3.Cu"]
    in3_cmd = [LS_(c_["in3_route"]).buffer(c_["in3_width"] / 2) for c_ in fp["command"]["columns"].values()]
    in3_gap = min([s_.distance(v1shape) for w_, s_ in in3_w] + [s_.distance(v1shape) for s_ in in3_cmd]
                  + [PT_(v["x"], v["y"]).distance(v1shape) - VR for v in fvias if v["net"] not in ("GND", "V1P2") and v["y"] > 3.5])
    pierce = sorted((v["net"], v["x"], v["y"]) for v in list(fvias) + list(fgv) if v["net"] != "V1P2" and v1shape.buffer(VR + 0.1).contains(PT_(v["x"], v["y"])))
    key_ = fp["outline"]["key_notch"]
    kx0, kx1, ktop = key_["x_centre"] - key_["width"] / 2 - 0.3, key_["x_centre"] + key_["width"] / 2 + 0.3, key_["depth"] + 0.3
    br_ = ext_.get("bridge", {"x": [0, 0], "y": [0, 0]})
    web_ = min([br_["y"][1] - (v_[2] + VR + 0.1) for v_ in pierce if kx0 <= v_[1] <= kx1] or [br_["y"][1] - ktop])
    neck_w = min(kx0 - br_["x"][0], br_["x"][1] - kx1)
    check("islands: the V1P2 island is on In2 (upstream convention), takes in all 19 VDD fingers (x %.1f .. %.1f, y %.1f .. %.1f) and is ONE piece: the bridge "
          "north of the key slot (x %.1f .. %.1f, y up to %.2f) joins its two halves, %.2f mm of copper above the slot and %.2f mm above the antipad of the one "
          "GND via that stands in it, the two necks beside the slot %.2f mm wide; the tab x %.1f .. %.1f up to y = %.2f is the foot of the feed corridor. "
          "Only In3 refers to In2: the nearest In3 copper of any net (track or via pad) is %.2f mm from the island in plan (limit 0.30 = 2.7 h), so no In3 "
          "trace has V1P2 above it; %d GND vias pierce it (three finger vias of VSS pairs east of the last VDD finger, three shunt GND vias). The VDD2H island (In4) starts north of the via pad of "
          "the last command ball (J9), takes in the VDD2H via-in-pads from row K on (%d of %d), and the nearest In3 / B.Cu copper of the data side stays "
          "%.2f mm from it (limit 0.90)"
          % (v1["x"][0], v1["x"][1], v1["y"][0], v1["y"][1], br_["x"][0], br_["x"][1], br_["y"][1], br_["y"][1] - ktop, web_, neck_w,
             ext_.get("feed_tab", br_)["x"][0], ext_.get("feed_tab", br_)["x"][1], ext_.get("feed_tab", br_)["y"][1], in3_gap, len(pierce), n_isl, n_v2h, isl_gap),
          v1["layer"] == "In2.Cu" and fp["power"]["vdd2h_island"]["layer"] == "In4.Cu" and v1["y"][0] >= 1.0
          and all(v1["x"][0] + 0.2 <= fingers[f]["x_mm"] <= v1["x"][1] - 0.2 for f in vdd_f) and v1["halves"].get("joined")
          and br_["y"][1] - ktop >= 1.0 - 1e-9 and web_ >= 0.4 - 1e-9 and neck_w >= 1.0 and in3_gap >= 0.30 - 1e-9 and all(n_ == "GND" for n_, _x, _y in pierce)
          and not under
          and fp["power"]["vdd2h_island"]["y"][0] >= bxy("J9")[1] + VR + 0.2 - 1e-9
          and fp["power"]["vdd2h_island"]["y"][0] <= bxy("K1")[1] - VR - 0.1 + 1e-9 and n_isl >= 50 and isl_gap >= 0.9,
          "pierced by %s; V1P2 island x %.1f .. %.1f, y %.1f .. %.1f + %s; VDD2H island from y = %.1f"
          % (pierce, v1["x"][0], v1["x"][1], v1["y"][0], v1["y"][1], sorted(ext_), fp["power"]["vdd2h_island"]["y"][0]))
    pb = fp["plane_plan"]["pull_back"]
    check("every inner layer has a pull-back from the finger edge (In1 / In4 >= 1.0, In2 >= 0.5) and >= 0.3 mm from the other edges",
          all(k in pb for k in ("In1.Cu", "In2.Cu", "In3.Cu", "In4.Cu")) and pb["In1.Cu"]["finger_edge"] >= 1.0 and pb["In4.Cu"]["finger_edge"] >= 1.0
          and pb["In2.Cu"]["finger_edge"] >= 0.5 and all(pb[k]["other_edges"] >= 0.3 for k in ("In1.Cu", "In2.Cu", "In3.Cu", "In4.Cu")))


    # ------------------------------------------------------------------ groups
    gr = {g["name"]: g for g in d["groups"]}
    check("groups CMD, WR, RD_P and RD_N exist", all(k in gr for k in ("CMD", "WR", "RD_P", "RD_N")), str(sorted(gr)))
    allnets = set(net_pins)
    miss = [(g["name"], n) for g in d["groups"] for n in g["nets"] if n not in allnets]
    check("every net named in a group exists", not miss, str(miss))
    hpr_ = gr["RD_P"]["per_net_host_plus_pkg_ps"]
    check("RD_P: the host + package figure of every read net is the one of its read finger (design/ref/zcu104_finger_total_delay.csv)",
          all(hpr_[n] == float(td[rf[n]]["total_mid_ps"]) for n in wr), "host spread %.1f ps" % (max(hpr_.values()) - min(hpr_.values())))
    for name, key in (("CMD", cf),):          # WR and RD_P are not matched on the card: checked with the data side above
        g = gr[name]
        tgt, hpp = g["per_net_target_card_delay_ps"], g["per_net_host_plus_pkg_ps"]
        tot = g["total_delay_target_ps"]
        ok = all(abs(tgt[n] + hpp[n] - tot) <= 0.051 for n in tgt)
        ok &= all(hpp[n] == float(td[key[n]]["total_mid_ps"]) for n in tgt)
        if "per_net_planned_error_ps" in g:             # RD_P: matched by the levels; the plan states the error each net is left with
            ok &= all(abs(g["per_net_planned_card_delay_ps"][n] - tgt[n] - g["per_net_planned_error_ps"][n]) <= 0.06
                      and abs(g["per_net_planned_error_ps"][n]) <= g["tolerance_ps"] - 0.2 for n in tgt)
        elif "per_net_planned_card_delay_ps" in g:      # WR: matched by path choice, the plan must hit the target itself
            ok &= all(abs(tgt[n] - g["per_net_planned_card_delay_ps"][n]) <= 0.05 for n in tgt)
        else:
            est = g["per_net_min_card_delay_estimate_ps"]
            ok &= all(tgt[n] >= est[n] - 0.05 for n in tgt)
        sp = max(hpp.values()) - min(hpp.values())
        check("%s: host + package + target card delay is the same for every net and every target is reachable%s" % (
              name, " (RD_P: within the stated error of each net, all inside the group tolerance)" if name == "RD_P" else ""), ok,
              "total %.1f ps, host spread %.1f ps, card targets %.1f..%.1f ps, tolerance +/-%g ps"
              % (tot, sp, min(tgt.values()), max(tgt.values()), g["tolerance_ps"]))
    cmdl = gr["CMD"]["layer"]
    check("command group is on In3 between GND planes", cmdl.startswith("In3") and "GND" in cmdl)

    # ------------------------------------------------------------------ stackup
    st = d["stackup"]
    lay = {l["layer"]: l for l in st["layers"]}
    tot = round(sum(l["thickness_mm"] for l in st["layers"]), 3)
    bare = round(tot - lay["F.Mask"]["thickness_mm"] - lay["B.Mask"]["thickness_mm"], 3)
    check("stackup: 6 copper layers, 0.035 / 0.018 mm copper and three 0.110 mm prepregs as in the upstream stackup",
          [l["layer"] for l in st["layers"] if l["type"] == "copper"] == ["F.Cu", "In1.Cu", "In2.Cu", "In3.Cu", "In4.Cu", "B.Cu"]
          and lay["F.Cu"]["thickness_mm"] == lay["B.Cu"]["thickness_mm"] == 0.035
          and all(lay["In%d.Cu" % i]["thickness_mm"] == 0.018 for i in (1, 2, 3, 4))
          and all(lay["dielectric %d" % i]["thickness_mm"] == 0.110 and lay["dielectric %d" % i]["type"] == "prepreg" for i in (1, 3, 5))
          and all(lay["dielectric %d" % i]["type"] == "core" for i in (2, 4)))
    check("stackup totals are consistent and the card is 1.20 +/-0.10 mm at the fingers",
          tot == st["total_thickness_with_mask_mm"] and bare == st["thickness_at_fingers_no_mask_mm"] and 1.10 <= bare <= 1.30
          and abs(bare + st["finger_finish_allowance_mm"] - 1.20) <= 0.01,
          "%.3f mm with mask, %.3f mm bare at the fingers, %.3f mm with the finger finish; a generic +/-10 %% fab window would be %.3f..%.3f mm, so +/-0.10 mm must be specified"
          % (tot, bare, bare + st["finger_finish_allowance_mm"], bare * 0.9, bare * 1.1))
    check("card outline 69.6 x 30.0 mm, key at 38.3 mm", fp["outline"]["width"] == 69.6 and fp["outline"]["height"] == 30.0
          and fp["outline"]["key_notch"]["x_centre"] == 38.3)
    vias = d["rules"]["vias"]
    check("via sizes: through 0.40/0.20 and 0.45/0.20, via-in-pad 0.40/0.15",
          (vias["through_signal"]["diameter"], vias["through_signal"]["drill"]) == (0.40, 0.20)
          and (vias["through_power"]["diameter"], vias["through_power"]["drill"]) == (0.45, 0.20)
          and (vias["via_in_pad"]["diameter"], vias["via_in_pad"]["drill"]) == (0.40, 0.15))
    ncl = d["rules"]["netclasses"]
    covered = set()
    for k, c in ncl.items():
        covered |= set(c["nets"])
    hs = set(cmd) | {fside(n) for n in cmd} | set(wr) | {fside(n) for n in wr} | \
        {"WCK_P", "WCK_N", "WCK_F_P", "WCK_F_N", "RDQS_P", "RDQS_N"}
    check("every high-speed net is assigned to a net class with a width per layer (DQx: one net from the ball to the read finger, class WR_DQ; "
          "its read branch follows the entry RD_DQ, which names no nets of its own)", hs <= covered and ncl["RD_DQ"]["nets"] == []
          and ncl["RD_DQ"].get("same_nets_as") == "WR_DQ" and not any(n.endswith("_RF") or "_RF_" in n for n in covered), str(sorted(hs - covered)))

    # ------------------------------------------------------------------ audit regressions: power
    check("U2 is ordered with the M3 suffix (current die only: +/-1 % output), so the 1.5 % total error used above is guaranteed",
          parts["U2"]["mpn"].endswith("M3") and parts["U2"]["mpn"].startswith("TPS74801DRC"), parts["U2"]["mpn"])
    en = u3["3"]
    en_pins = sorted(net_pins[en])
    check("VDD1 enable: only U3.EN and the pull-up to V1P2 on the net, no finger and no optional link (a host-driven enable would "
          "switch VDD1 off under a live VDD2H)", en_pins == sorted([("U3", "3"), (two_pin("V1P2", en)[0]["ref"], "1" if two_pin("V1P2", en)[0]["pins"]["1"] == en else "2")])
          and j1["109"] == "" and not any(p["dnp"] for p in d["parts"]), "%s: %s" % (en, en_pins))
    c47 = lambda net: sum({"4u7": 4.7e-6, "22u": 22e-6, "1u": 1e-6, "100n": 0.1e-6, "2n2": 2.2e-9}[p["value"]] for p in d["parts"]
                          if p["ref"][0] == "C" and set(p["pins"].values()) == {net, "GND"})
    b1 = two_pin("VDD1", "GND")
    fbsum = sum(ohms(p["value"]) for p in two_pin("VDD2H", u2["8"]) + two_pin(u2["8"], "GND"))
    if len(b1) == 1:
        rb = ohms(b1[0]["value"])
        t_300 = rb * c47("VDD1") * math.log(1.836 / 0.3)                 # nominal capacitance (no DC-bias loss), highest VDD1
        tau1_min, tau2_max = rb * c47("VDD1") * 0.6, fbsum * c47("VDD2H")  # VDD1 with 40 % DC-bias loss against VDD2H nominal
        check("VDD1 has a bleeder: below 0.3 V within 2 s (JESD209-5C tUNCTL_POFF) and still slower than the VDD2H tail (VDD1 falls last)",
              t_300 <= 2.0 and tau1_min >= 1.2 * tau2_max, "%g ohm x %.1f uF: 0.3 V after %.2f s; tau >= %.2f s against VDD2H %.2f s"
              % (rb, c47("VDD1") * 1e6, t_300, tau1_min, tau2_max))
    else:
        check("VDD1 bleeder present", False)
    bq = two_pin("VDDQ", "GND")
    check("VDDQ bleeder: time constant <= 1 ms so that VDDQ follows VDD2H down, dissipation <= 25 mW in 0201",
          len(bq) == 1 and ohms(bq[0]["value"]) * c47("VDDQ") <= 1.0e-3 and 0.512 ** 2 / ohms(bq[0]["value"]) <= 0.025,
          "%s: tau %.2f ms, %.1f mW" % (bq[0]["value"], ohms(bq[0]["value"]) * c47("VDDQ") * 1e3, 0.512 ** 2 / ohms(bq[0]["value"]) * 1e3) if bq else "")
    u4fp = parts["U4"]["footprint"].split(":")
    t = open(os.path.join(ROOT, "lib", PROJECT_LIB + ".pretty", u4fp[1] + ".kicad_mod")).read() if u4fp[0] == PROJECT_LIB else ""
    m = re.search(r'\(pad "7" smd rect \(at 0 0\) \(size ([\d.]+) ([\d.]+)\)', t)
    check("U4 footprint: exposed pad 0.80 x 1.40 mm as in the MIC47050 package drawing (not the 1.0 x 1.6 of the stock DFN-6)",
          bool(m) and (float(m.group(1)), float(m.group(2))) == (0.8, 1.4), parts["U4"]["footprint"])

    # ------------------------------------------------------------------ audit regressions: levels
    an = d["analysis"]
    dv = divider("CA0_F", "CA0")
    ibis = 1.26 * dv[1] * 1.01 / (25.1 + dv[0] * 0.99 + dv[1] * 1.01)
    dq = divider("DQ0_WF", "DQ0")
    ibis_dq = 1.26 * dq[1] * 1.01 / (25.1 + dq[0] * 0.99 + dq[1] * 1.01)
    vddq_min = 0.4 * (1 + 2490.0 / 10000.0) * 0.976
    check("with the fastest IBIS driver (25.1 ohm) every VDDQ-domain level still stays below the card's own VDDQ minimum",
          ibis <= vddq_min - 0.010 and ibis_dq <= vddq_min - 0.010, "CA / CK / WCK %.4f V, DQ %.4f V, VDDQ min %.4f V" % (ibis, ibis_dq, vddq_min))
    zc = d["rules"]["netclasses"]["CMD_SE"]["layers"]["In3.Cu"]["z0_ohm"]
    zp = par(dv[1], zc)
    k = 4 * zp / (dv[0] + zp + 39.0)
    fi_fast, dc_fast = k * 1.26 * 39.0 / (26.0 + 39.0), 1.26 * dv[1] / (26.0 + dv[0] + dv[1])
    check("first-incident level at the open ball (fast corner) exceeds the card's VDDQ by less than the JEDEC overshoot allowance (0.3 V)",
          fi_fast - vddq_min <= 0.10 and abs(an["dividers"]["CA0-6"]["first_incident_at_open_ball_v"][2] - fi_fast) < 2e-3,
          "first incident %.3f V = %.2f x the DC level; %.0f mV above VDDQ min" % (fi_fast, fi_fast / dc_fast, 1e3 * (fi_fast - vddq_min)))
    wd = divider("WCK_F_P", "WCK_P")
    odt36 = 2 * 1.14 * par(wd[1] * 0.99, 36.0) / (44 + wd[0] * 1.01 + par(wd[1] * 0.99, 36.0))
    check("WCK with the CBT ODT at its low limit (36 ohm): still >= 0.30 V p-p differential, and the stored figure is this worst case",
          odt36 >= 0.30 and abs(an["dividers"]["WCK_P/WCK_N (per leg)"]["with_40ohm_wck_odt_differential_vpp"][0] - odt36) < 2e-3, "%.3f V p-p" % odt36)
    sh = dq[1]
    vj = (0.4876 * sh / (52.0 + sh), 0.5116 * sh / (28.0 + sh))      # JESD209-5C Table 472: 40 ohm x 1.3 / 0.7, un-terminated
    vn = an["read"]["reference_v"]
    check("read levels over the JEDEC pull-up range (28 .. 52 ohm into the shunt), reference 0.195 V: inside MIPI_DPHY_DCI and SLVS_400_18 windows",
          vn <= 0.200 and (vj[1] + vn) / 2 <= 0.330 and vj[1] <= 0.460 and vj[0] - vn >= 0.140 and vn / 2 >= 0.070
          and abs(an["read"]["read_high_jedec_bound_v"][0] - vj[0]) < 2e-3 and abs(an["read"]["read_high_jedec_bound_v"][2] - vj[1]) < 2e-3,
          "read high %.3f .. %.3f V; VICM <= %.0f mV (330), pin <= %.0f mV (460), SLVS VID >= %.0f mV (140)"
          % (vj[0], vj[1], 1e3 * (vj[1] + vn) / 2, 1e3 * vj[1], 1e3 * (vj[0] - vn)))
    wn = an["write_node"]
    rsrc = par(sh, dq[0] + 36.0)
    tau = rsrc * wn["capacitance_total_pf"] * 1e-3
    wrnote = {g["name"]: g for g in d["groups"]}["WR"]["note"]
    check("write node: time constant computed from the full node capacitance (>= 25 pF with the read branch and host line), unit interval "
          "at 100 Mb/s >= 3 tau, and no 200 Mb/s claim", wn["capacitance_total_pf"] >= 25.0 and abs(wn["tau_ns"] - tau) < 0.02 and 10.0 >= 3 * tau
          and "200 Mb/s" not in wrnote and wn["capacitance_pf"]["read_branch_in3_pf"] >= 0.1515 * max(rd_in3.values()) - 0.2,
          "%.0f ohm x %.1f pF = %.2f ns" % (rsrc, wn["capacitance_total_pf"], tau))
    check("idle RDQS (both legs at 0 V) is documented as invalid outside the read burst", "gated" in an["read"]["rdqs_true_differential"]["idle"])
    # ---- the open FPGA end without a series resistor: the line simulation is re-run here and compared with what the spec states
    import read_linesim as LS
    oe = an["read"]["open_end"]
    kw_ = dict(tau_trunk=oe["delays_ps"]["trunk_ps"] * 1e-12, tau_rd=oe["delays_ps"]["read_branch_ps"] * 1e-12,
               tau_host=oe["delays_ps"]["host_read_ps"] * 1e-12, tau_wr=oe["delays_ps"]["host_write_ps"] * 1e-12)
    mine = LS.table(0.0, **kw_)
    same_ = len(mine) == len(oe["without_series_resistor"]) and all(
        abs(a_[k_] - b_[k_]) <= 0.002 for a_, b_ in zip(mine, oe["without_series_resistor"]) for k_ in ("peak_v", "min_v"))
    pk_, mn_ = max(r_["peak_v"] for r_ in mine), min(r_["min_v"] for r_ in mine)
    out_ns = max(max(r_["above_window_ns"], r_["below_window_ns"]) for r_ in mine)
    late_ = max(max(abs(r_["high_minus_final_mV_at_5ns"]), abs(r_["low_minus_final_mV_at_5ns"])) for r_ in mine)
    late8_ = max(max(abs(r_["high_minus_final_mV_at_8ns"]), abs(r_["low_minus_final_mV_at_8ns"])) for r_ in mine)
    # delays of the simulation re-derived from the walker: line (ball to the node via, barrel to In3 included), read branch, host lines
    tk_ps, br_ps = {}, {}
    for n in wr:
        r_ = walk([n], K(ball_at[n], "F.Cu"), K((cols[n]["x"], D["rows_y"]["node_via"]), "F.Cu"))
        tk_ps[n] = r_[0] - r_[1] + (zmid["In3.Cu"] - zmid["F.Cu"]) * dly["through via (per mm of barrel)"]
        br_ps[n] = rd_tot[n] - hostps(rf[n]) - tk_ps[n]
    pn_ = oe["per_net"]
    pn_ok = set(pn_) == set(wr)
    worst_n = {"peak_v": -9.0, "min_v": 9.0, "at8": 0.0, "at5": 0.0}
    for n in wr:
        q_ = pn_[n]["delays_ps"]
        pn_ok &= abs(q_["trunk_ps"] - tk_ps[n]) <= 0.1 and abs(q_["read_branch_ps"] - br_ps[n]) <= 0.1 and q_["host_read_ps"] == hostps(rf[n]) and q_["host_write_ps"] == hostps(wf[n])
        m_ = LS.summary(28.0, 28.0, 0.0, tau_trunk=tk_ps[n] * 1e-12, tau_rd=br_ps[n] * 1e-12, tau_host=hostps(rf[n]) * 1e-12, tau_wr=hostps(wf[n]) * 1e-12)
        pn_ok &= abs(m_["peak_v"] - pn_[n]["strong"]["peak_v"]) <= 0.002 and abs(m_["min_v"] - pn_[n]["strong"]["min_v"]) <= 0.002
        worst_n["peak_v"] = max(worst_n["peak_v"], m_["peak_v"])
        worst_n["min_v"] = min(worst_n["min_v"], m_["min_v"])
        worst_n["at8"] = max(worst_n["at8"], abs(m_["high_minus_final_mV_at_8ns"]), abs(m_["low_minus_final_mV_at_8ns"]))
        worst_n["at5"] = max(worst_n["at5"], abs(m_["high_minus_final_mV_at_5ns"]), abs(m_["low_minus_final_mV_at_5ns"]))
    check("read branch without series resistor (analysis.read.open_end, line simulation re-run here with the lengths of the comb): with the mean delays "
          "the open FPGA pin sees %.3f V / %+.3f V at worst (driver 30 %% strong at the 40 ohm setting), inside the recommended range of the pin "
          "(%.2f .. %.2f V) with more than 70 mV to spare; outside the single-ended window of the MIPI HS receiver (%.2f .. %.2f V) for at most "
          "%.1f ns per edge - stated as such, not hidden; within %.0f mV of the final level 5 ns after the edge arrives and %.0f mV after 8 ns "
          "(10 ns unit interval); the spec carries the same numbers and names the gateware knobs (MR3 drive strength, MR17)"
          % (pk_, mn_, oe["fpga_pin_recommended_v"][0], oe["fpga_pin_recommended_v"][1], oe["hs_window_v"][0], oe["hs_window_v"][1], out_ns, late_, late8_),
          same_ and pk_ <= oe["fpga_pin_recommended_v"][1] - 0.07 and mn_ >= oe["fpga_pin_recommended_v"][0] + 0.07 and 0.3 <= out_ns <= 3.0
          and late_ <= 45.0 and late8_ <= 20.0 and an["read"]["series_ohm"] == 0 and "MR3" in oe["gateware_knobs"] and "MR17" in oe["gateware_knobs"]
          and "outside the single-ended window" in oe["answer"] and abs(oe["delays_ps"]["trunk_ps"] - sum(tk_ps.values()) / 8) <= 0.1
          and abs(oe["delays_ps"]["read_branch_ps"] - sum(br_ps.values()) / 8) <= 0.1
          and max(r_["peak_v"] for r_ in oe["with_22_ohm"]) <= oe["hs_window_v"][1] and min(r_["min_v"] for r_ in oe["with_22_ohm"]) >= oe["hs_window_v"][0],
          "nominal driver: %.3f V / %+.3f V; with the 22 ohm that were removed: %.3f V / %+.3f V at worst"
          % (mine[1]["peak_v"], mine[1]["min_v"], max(r_["peak_v"] for r_ in oe["with_22_ohm"]), min(r_["min_v"] for r_ in oe["with_22_ohm"])))
    check("read line simulation per net, re-run here for every DQ with its own lengths (line %.0f .. %.0f ps, read branch %.0f .. %.0f ps), strong corner: "
          "peak %.3f V, minimum %+.3f V over the eight nets, within %.0f mV of the final level 5 ns after the edge and %.0f mV after 8 ns; equal to the "
          "figures the spec stores per net" % (min(tk_ps.values()), max(tk_ps.values()), min(br_ps.values()), max(br_ps.values()), worst_n["peak_v"],
                                               worst_n["min_v"], worst_n["at5"], worst_n["at8"]),
          # 65 mV to spare per net (the mean-length case above keeps 70): the read end without crossings makes the longest read branch 228 ps
          # (DQ4, was 195 ps), whose open end undershoots to -0.131 V in the strong corner (was -0.127 V)
          pn_ok and worst_n["peak_v"] <= oe["fpga_pin_recommended_v"][1] - 0.065 and worst_n["min_v"] >= oe["fpga_pin_recommended_v"][0] + 0.065
          and worst_n["at8"] <= 20.0 and abs(oe["per_net_worst"]["peak_v"] - worst_n["peak_v"]) <= 0.002 and abs(oe["per_net_worst"]["min_v"] - worst_n["min_v"]) <= 0.002)

    # ------------------------------------------------------------------ audit regressions: stackup, impedance, text
    imp = json.load(open(os.path.join(ROOT, "design", "impedance_results.json")))
    oc = imp.get("outer_copper_and_mask_cases", {})
    tab = oc.get("stackup_table_nominal", {})
    rng = lambda key, w, f_: sorted(x[f_] for c in oc.values() for x in c[key] if abs(x["w"] - w) < 1e-9)
    z20, zp10 = rng("single", 0.20, "z0") if oc else [], rng("pair", 0.10, "zdiff") if oc else []
    host_z = d["rules"]["netclasses"]["HOST_SE"]["layers"]["F.Cu"]["z0_ohm"]
    pair_z = d["rules"]["netclasses"]["WR_WCK"]["layers"]["B.Cu"]["zdiff_ohm"]
    check("outer-layer impedances are solved for the copper / mask of the stackup table and for the cap-plated case, and the design values lie between them",
          len(oc) == 3 and abs(tab.get("t_outer", 0) - lay["F.Cu"]["thickness_mm"]) < 1e-9 and abs(tab.get("t_mask", 0) - lay["F.Mask"]["thickness_mm"]) < 1e-9
          and z20[0] <= host_z <= z20[-1] and zp10[0] <= pair_z <= zp10[-1] and "impedance_control" in st,
          "0.20 mm: %.1f .. %.1f ohm (design %.1f); pair 0.10 / 0.10: %.1f .. %.1f ohm (design %.1f)" % (z20[0], z20[-1], host_z, zp10[0], zp10[-1], pair_z) if oc else "")
    check("card with cap-plated outer copper is still inside 1.10 .. 1.30 mm at the fingers",
          1.10 <= st["thickness_at_fingers_with_cap_plating_mm"] <= 1.30 - 0.05, "%.3f mm" % st["thickness_at_fingers_with_cap_plating_mm"])
    blob = json.dumps(d)
    tops = set(re.findall(r"finger top \(y = ([\d.]+)\)", blob))
    check("every text that quotes the finger top uses the floorplan value", tops <= {"%.2f" % fp["outline"]["finger_top_y"]}, str(tops))
    gw = {g["name"]: g for g in d["groups"]}["WR"]
    m = re.search(r"centre ([\d.]+), \+/-([\d.]+)\)", gw["note"])
    check("the spread quoted in the WR note is the one re-derived here; no meander is planned on any write net",
          bool(m) and abs(float(m.group(1)) - w_cen) <= 0.06 and abs(float(m.group(2)) - w_half) <= 0.06 and gw["meander_total_mm"] == 0.0,
          "centre %.1f ps, +/-%.1f" % (w_cen, w_half))
    grd = {g["name"]: g for g in d["groups"]}["RD_P"]
    md = open(os.path.join(ROOT, "design", "DESIGN.md")).read()
    cx_, cy_ = fp["U1"]["centre"]
    need = ["(%g, %.1f)" % (cx_, cy_), parts["U2"]["mpn"], "%d items" % len(d["parts"]), "%.1f ps" % gcmd["total_delay_target_ps"],
            "%.1f ps" % grd["total_delay_centre_ps"], "+/-%.1f ps" % grd["spread_half_ps"], "+/-%.1f ps" % gw["spread_half_ps"], "MLF-6-1EP_2x2mm_P0.65mm_EP0.8x1.4mm",
            "fingers 219 / 221", "read end without crossings",
            "gateware_deskew.json", "accepted by the user", "finger 232", "scripts/power_side.py", "1.5 mm STRIP",
            # final fix pass: RDQS on In3 down to its fingers, the unused byte open, fiducials, the one-command build
            "%.2f / %.2f" % (gq_["per_net_planned_total_ps"]["RDQS_P"], gq_["per_net_planned_total_ps"]["RDQS_N"]),
            "%+.2f" % gq_["strobe_minus_dq_group_ps"], "y = %.2f" % D["knobs"]["rdqs_via_y"], "RDQS_pair_to_other_tracks_In3", "rdqs_via_pair",
            "intentionally open", "FID1 .. FID6", "%d nets" % len(net_pins), "%d BOM lines" % len(d["bom_summary"]), "design/gateware/", "TSOT-23-5"]
    # release: the restructured document (rules table, upstream comparison, crossing table, rebuild and fabrication package) and its crossing figures
    cb_, pb_ = fp["data"]["crossing_budget"], fp["power"]["crossing_budget"]
    need += ["## 10. The user's rules and how each is met", "## 11. Comparison with the Antmicro LPDDR5 testbed", "## 12. Crossings", "## Appendix A. History",
             "scripts/make_fab.py", "scripts/check_fab.py", "fab/README.md", "design/README_PROPOSED.md",
             "command bundle %d, data x data %d, high-speed x VREF %d, high-speed x supply %d"
             % (cb_["cmd_x_data"] + pb_["cmd_x_pwr"] + pb_["cmd_x_gnd"] + pb_["cmd_x_slow"], cb_["data_x_data"], cb_["hs_x_vref"], pb_["hs_x_pwr"]),
             "| **All crossings with a high-speed wire** | **%d** |" % (cb_["cmd_x_data"] + pb_["cmd_x_pwr"] + pb_["cmd_x_gnd"] + pb_["cmd_x_slow"] + cb_["data_x_data"]
                                                                      + cb_["hs_x_vref"] + pb_["hs_x_pwr"]),
             "%.3f mm at the bare fingers" % d["stackup"]["thickness_at_fingers_no_mask_mm"]]
    stale = ["R505", "PWR_EN_F", "(20.725, 15.0)", "y = 2.55", "TPS74801DRCR,", "100 ohm bleeds", "+/-20 ps", "DFN-6-1EP",
             "CK_T", "CK_C", "RDQS_T", "RDQS_C", "from y = 19.4", "1140.5 ps", "643.4 ps", "measure.py",
             "746.8 ps", "925.0 ps", "(36.70, 10.00)", "(37.15, 7.96)",        # first routing of the write group (section 15)
             "923.3 ps", "(46.10, 13.85)", "y = 14.50",                         # read plan before stage s40 (section 16)
             "22 ohm tap", "RDQS_RF", "strobe arrives 56 ps", "total 940.6 ps", "y = 16.20 and runs east",     # read group before revision 2
             "s30_write", "s40_read", "trunks.py", "staircase via", "906.8 ps", "794.0 ps", "section 18", "section 17",   # before the data re-plan (comb)
             "pending the user", "rehearsed, not built", "finger 229,", "RESET_n hop |",          # before the power stage
             "905.4 ps", "x = 38.566", "y = 16.85", "x = 35.5 across", "the links pad wide", "own vias at y = 8.85", "0.651 mm deep",
             "not joined on In4", "an In4 island under the finger row",       # the comb before the review of stage s30_data
             "894.7 ps", "-9.72 .. +9.63", "x = 50.894", "data x data 80",     # the comb with the matched read group (20 read-end crossings, RDQS on 198 / 200)
             "308.96 / 308.76", "via pair at x = 49.000", "39-53.3", "rdqs outer run", "unused byte pulldowns", "**905.36 / 905.36**",
             "Command bundle, all 29", "LPDDR5_RDQS=mid`)", "x 28.7-47.7", "127 items", "28 BOM lines",      # before the final fix pass
             "Sections 10 to 12 are change logs", "## 16. Final review", "(it was 59.50)", "section 16"]     # before the release restructuring
    check("DESIGN.md carries the current key numbers and none of the superseded ones",
          all(x in md for x in need) and not any(x in md for x in stale),
          "missing: %s; stale: %s" % ([x for x in need if x not in md], [x for x in stale if x in md]))

    check_power(d)

    # ------------------------------------------------------------------ PCB skeleton data (placements_other, U1 fan-out)
    po = fp["placements_other"]
    check("every part has a placement: %d field parts + %d others = all %d parts, none twice" % (len(place), len(po), len(d["parts"])),
          set(po) | set(place) == set(parts) and not (set(po) & set(place)), "missing %s" % sorted(set(parts) - set(po) - set(place))[:8])
    crt = dict(court)
    for r_, pl in po.items():
        if "courtyard" in pl:
            crt[r_] = (pl["side"], tuple(pl["courtyard"]))
    names2 = sorted(crt)
    ov2 = [(a, b) for i, a in enumerate(names2) for b in names2[i + 1:] if crt[a][0] == crt[b][0] and rect_rect_gap(crt[a][1], crt[b][1]) < -1e-6]
    hits2 = [(r_, z) for r_, (sd, rc) in crt.items() for z, poly in zones if rect_hits_poly(rc, poly)]
    tall2 = [r_ for r_, (sd, rc) in crt.items() if rc[1] < 5.10 and not re.search(r"_0201_|_0402_", parts[r_]["footprint"])]
    check("courtyards of all %d parts: no overlap on either face, none in a 'footprints not allowed' area, only 0201 / 0402 below y = 5.10" % len(crt),
          not ov2 and not hits2 and not tall2, "overlaps %s; keep-out %s; tall %s" % (ov2[:6], hits2[:6], tall2[:6]))
    noreg = []
    for r_, (sd, rc) in crt.items():
        lay = "F.Cu" if sd == "F" else "B.Cu"
        m_ = 0.25 if r_ == "U1" else 0.0                    # the U1 region is the package body, the courtyard 0.25 mm larger
        if not any(lay in g_["layers"] and g_["x"][0] - 1e-6 <= rc[0] + m_ and rc[2] - m_ <= g_["x"][1] + 1e-6
                   and g_["y"][0] - 1e-6 <= rc[1] + m_ and rc[3] - m_ <= g_["y"][1] + 1e-6 for g_ in regs):
            noreg.append(r_)
    check("every part lies inside a declared region of its face (all parts)", not noreg, str(sorted(noreg)[:10]))
    # pad positions written by the generator against an independent transform of the library footprints
    def lib_pads(lib_name):
        lib, name = lib_name.split(":")
        base = os.path.join(ROOT, "lib", PROJECT_LIB + ".pretty") if lib == PROJECT_LIB else os.path.join(KICAD_FP, lib + ".pretty")
        t = open(os.path.join(base, name + ".kicad_mod")).read()
        out = {}
        for m in re.finditer(r'\(pad\s+"([^"]+)"\s+\w+\s+\w+\s*\(at\s+([-\d.]+)\s+([-\d.]+)[^)]*\)\s*\(size\s+([\d.]+)\s+([\d.]+)\)\s*(?:\([^l][^)]*\)\s*)*\(layers\s+([^)]*)\)', t):
            if ".Cu" in m.group(6):
                q = (float(m.group(2)), float(m.group(3)))
                if m.group(1) not in out or abs(q[0]) + abs(q[1]) < abs(out[m.group(1)][0]) + abs(out[m.group(1)][1]):
                    out[m.group(1)] = q
        return out
    padbad = []
    for r_, pl in po.items():
        if not isinstance(pl["pads"], dict):
            continue
        lp = lib_pads(pl["footprint"])
        th = math.radians(pl["rot_deg"])
        for n_, (xl, yl) in lp.items():
            if pl["side"] == "B":
                yl = -yl
            x_ = pl["x"] + xl * math.cos(th) + yl * math.sin(th)
            y_ = pl["y"] + xl * math.sin(th) - yl * math.cos(th)
            if n_ not in pl["pads"] or abs(pl["pads"][n_][0] - x_) > 2e-3 or abs(pl["pads"][n_][1] - y_) > 2e-3:
                padbad.append((r_, n_))
        if set(lp) != set(parts[r_]["pins"]) or pl["footprint"] != parts[r_]["footprint"]:
            padbad.append((r_, "pins"))
    check("placements_other: footprint, pin set and every pad centre agree with the library footprint at the stated side and rotation",
          not padbad, str(padbad[:8]))
    # U1 fan-out
    def same(p_, q):
        return abs(p_[0] - q[0]) < 1e-6 and abs(p_[1] - q[1]) < 1e-6
    vp_ = fp["U1"]["via_in_pad"]
    vb = vp_["balls"]
    check("U1 via-in-pad list = every supply ball and routed signal ball less the omitted ones (%d balls), each on its net and position" % len(vb),
          set(vb) == set(via_balls) and all(vb[b]["net"] == u1[b] and same(vb[b]["xy"], bxy(b)) for b in vb),
          "%s" % sorted(set(vb) ^ set(via_balls))[:8])
    lk = fp["U1"]["f_cu_links"]
    adj = defaultdict(set)
    lbad = []
    for l_ in lk:
        a_, b_ = l_["from"], l_["to"]
        adj[a_].add(b_)
        adj[b_].add(a_)
        pa, pb_ = bxy(a_), bxy(b_)
        ln_ = sum(math.hypot(q[0] - p_[0], q[1] - p_[1]) for p_, q in zip(l_["path"], l_["path"][1:]))
        if u1[a_] != u1[b_] or u1[a_] != l_["net"] or not u1[a_] or math.hypot(pa[0] - pb_[0], pa[1] - pb_[1]) > 1.07 or ln_ > 1.12 \
                or not same(l_["path"][0], pa) or not same(l_["path"][-1], pb_) or l_["layer"] != "F.Cu" \
                or any(not (abs(q[0] - p_[0]) < 1e-6 or abs(q[1] - p_[1]) < 1e-6 or abs(abs(q[0] - p_[0]) - abs(q[1] - p_[1])) < 1e-6)
                       for p_, q in zip(l_["path"], l_["path"][1:])):
            lbad.append((a_, b_))

    def reaches_via(b0):
        seen, todo = {b0}, [b0]
        while todo:
            c_ = todo.pop()
            if c_ in vb:
                return True
            for n_ in adj[c_] - seen:
                seen.add(n_)
                todo.append(n_)
        return False
    need_link = [b for b in balls if u1[b] in ("GND", "VDD2H", "VDDQ", "VDD1") and b not in vb]
    orphan = [b for b in need_link if not reaches_via(b)]
    extra = [l_["from"] for l_ in lk if l_["from"] in vb]
    check("U1 F.Cu links: every supply-net ball without a via (%d: omitted supply balls and strapped inputs) is linked through touching balls of "
          "its net to a ball with a via; links are 0 / 45 / 90 degree, <= 1.12 mm" % len(need_link),
          not lbad and not orphan and not extra, "bad %s; orphan %s; from a via ball %s" % (lbad[:6], orphan[:6], extra[:6]))
    ties = fp["U1"]["b_cu_ties"]
    check("CK_c_B (N7) is tied on B.Cu from its via to a VDDQ via",
          len(ties) == 1 and ties[0]["from"] == "N7" and u1["N7"] == "VDDQ" and u1[ties[0]["to"]] == "VDDQ" and ties[0]["to"] in vb and "N7" in vb
          and ref[ties[0]["to"]]["supply"] == "VDDQ")
    # the unused byte of the used channel: open balls, stated as such, with nothing of the old pull-down arrays left
    ub = [b for b in balls if re.fullmatch(r"DQ(8|9|1[0-5])_A_%s" % b, ref[b]["pin_function"])]
    ncr = {(n["ref"], n["pin"]): n["reason"] for n in d["no_connect"]}
    ubad = [b for b in ub if u1[b] != "" or b in vb or "intentionally open" not in ncr.get(("U1", b), "") or "UG583" not in ncr.get(("U1", b), "")]
    left = [r_ for r_ in parts if r_.startswith("RN")] + [n_ for n_ in net_pins if re.fullmatch(r"DQ(8|9|1[0-5])", n_)] \
        + [g_["name"] for g_ in regs if "pulldown" in g_["name"]] + [k_ for k_ in fp["U1"] if "escape" in k_] \
        + [l_["from"] for l_ in lk if l_["from"] in ub or l_["to"] in ub]
    fub = fp["U1"]["unused_byte"]
    check("unused byte: the eight balls DQ8-15_A are intentionally open (no net, no via, no link, reason with its sources in no_connect); "
          "no pull-down array, no escape and no region of them is left in the spec",
          len(ub) == 8 and not ubad and not left and sorted(fub["balls"].values()) == sorted(ub)
          and all(ref[b_]["pin_function"] == "%s_A_%s" % (n_, b_) for n_, b_ in fub["balls"].items()),
          "balls %s; bad %s; left over %s" % (sorted(ub), ubad, left))
    # WCK1 of that byte keeps its static level: WCK1_t low and WCK1_c high through 10 k each (an input-only clock pair must not float)
    w1 = {n_: two_pin(n_, r_) for n_, r_ in (("WCK1_T", "GND"), ("WCK1_C", "VDDQ"))}
    check("WCK1_t_A / WCK1_c_A keep their tie-off: 10 k to GND / 10 k to VDDQ",
          all(len(v_) == 1 and ohms(v_[0]["value"]) == 10000.0 for v_ in w1.values()), str({k_: [p_["ref"] for p_ in v_] for k_, v_ in w1.items()}))
    dec = {"VDD2H": 0, "VDDQ": 0, "VDD1": 0}
    dbad = []
    wck_gap = 9.9
    for r_, pl in po.items():
        if not (re.fullmatch(r"C4\d\d", r_) or r_ in ("R401", "R404", "R405")):
            continue
        for pin, xy in pl["pads"].items():
            net = parts[r_]["pins"][pin]
            near = [b for b in vb if u1[b] == net and math.hypot(bxy(b)[0] - xy[0], bxy(b)[1] - xy[1]) <= 0.40]
            if not near or pl["side"] != "B":
                dbad.append((r_, pin, net))
        if r_[0] == "C":
            dec[parts[r_]["pins"]["1"]] += 1
        for pin, xy in pl["pads"].items():           # none in the way of the WCK pair on B.Cu (diagonal D3 - C2 - B1): pad 0.62 x 0.56 around its centre
            for w_ in DW:
                if w_["group"] == "WCK" and w_["layer"] == "B.Cu" and parts[r_]["pins"][pin] != w_["net"]:
                    gp_ = min(seg_rect(a_, b_, (xy[0] - 0.31, xy[1] - 0.31, xy[0] + 0.31, xy[1] + 0.31)) for a_, b_ in segs(w_["path"])) - 0.05
                    wck_gap = min(wck_gap, gp_)
                    if gp_ < 0.10 - 1e-9:
                        dbad.append((r_, "in the way of %s on B.Cu" % w_["net"]))
    check("U1 decoupling and tie resistors sit on B.Cu with every pad on a via-in-pad of its net (within 0.40 mm); 12 VDD2H + 8 VDDQ + 4 VDD1; "
          "none in the way of the WCK pair on B.Cu (nearest pad %.2f mm; C415 moved to the via-in-pads of A3 / A4)" % wck_gap,
          not dbad and dec == {"VDD2H": 12, "VDDQ": 8, "VDD1": 4}, "%s %s" % (dec, dbad[:8]))

    # ------------------------------------------------------------------ assembly fiducials
    fid = {r_: po[r_] for r_ in po if r_.startswith("FID")}
    fbad = []
    for sd in ("F", "B"):
        pts_ = [(q["x"], q["y"]) for q in fid.values() if q["side"] == sd]
        tri = 0.0
        if len(pts_) == 3:
            (x1, y1), (x2, y2), (x3, y3) = pts_
            tri = abs((x2 - x1) * (y3 - y1) - (x3 - x1) * (y2 - y1)) / 2
        if len(pts_) != 3 or tri < 100.0:
            fbad.append((sd, len(pts_), round(tri, 1)))
    W_, H_ = fp["outline"]["width"], fp["outline"]["height"]
    fbad += [r_ for r_, q in fid.items() if not (3.0 <= q["x"] <= W_ - 3.0 and 6.0 <= q["y"] <= H_ - 3.0) or parts[r_]["pins"] or parts[r_].get("in_bom", True)
             or any(math.hypot(q["x"] - h_["x"], q["y"] - h_["y"]) < h_["d"] / 2 + 2.0 for h_ in fp["outline"]["tooling_holes"])]
    check("assembly fiducials: three per face, spanning a triangle of 100 mm2 or more, 3 mm or more from the card edges and above the finger area, clear of "
          "the tooling holes; parts without pins, not in the BOM (%s)" % ", ".join("%s %s (%.1f, %.1f)" % (r_, q["side"], q["x"], q["y"]) for r_, q in sorted(fid.items())),
          len(fid) == 6 and not fbad, str(fbad))

    # ------------------------------------------------------------------ BOM
    n_bom = sum(b["qty"] for b in d["bom_summary"])
    refs = [r for b in d["bom_summary"] for r in b["refs"]]
    check("BOM summary covers every BOM part exactly once",
          n_bom == sum(1 for p in d["parts"] if p.get("in_bom", True)) == len(set(refs)), "%d placements, %d lines" % (n_bom, len(d["bom_summary"])))
    branding = [p["ref"] for p in d["parts"] if re.search(r"antmicro|open ?hardware|oshw", json.dumps(p), re.I)]
    check("no third-party branding in part data", not branding and not re.search(r"antmicro|oshw", json.dumps(d["meta"]), re.I), str(branding))

    # ------------------------------------------------------------------ report
    width = max(len(n) for n, _, _ in results)
    fails = 0
    for name, ok, detail in results:
        fails += not ok
        print("%s  %s%s" % ("PASS" if ok else "FAIL", name, ("\n        " + detail) if detail else ""))
    print()
    print("nets: %d   parts: %d (BOM %d, DNP %d)   J1 pads: %d   U1 balls: %d   no-connects: %d"
          % (len(net_pins), len(d["parts"]), n_bom, sum(1 for p in d["parts"] if p["dnp"]), len(j1), len(u1), len(nc)))
    print("RESULT: %d checks, %d failed" % (len(results), fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())

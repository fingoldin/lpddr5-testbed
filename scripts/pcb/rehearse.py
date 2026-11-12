#!/usr/bin/env python3
"""Rehearsal of the floorplan: every planned centre line of design/netlist.json is drawn on a COPY of the board
and the KiCad DRC is run on that copy.  The project board is never written.

  rehearse.py <workdir> [--negative] [--render <outdir>]

Drawn (stage tag 'rehearse'):
  floorplan.finger_stubs (command)          the ten command finger stubs (F.Cu / B.Cu)
  floorplan.command.columns[*].in3_route    the eight CA / CS traces and the two CK legs on In3 (no tuning)
  floorplan.data.wires                      every centre line of the data side: finger stubs, divider links, the F.Cu comb, the In3 read
                                            branches, read finger tracks, the WCK and RDQS legs, reference networks, VREF_RD bus
The power stage (s50_power: the 1.2 V strip, the regulators, slow nets, pours) is not rehearsed: it is built from floorplan.power.build
and measured by gate 9 of check_pcb.py.  (Before that stage existed the rehearsal drew a planned 1.2 V pour in the corridor; the stage
builds a 1.5 mm strip instead.)
Not drawn: the pad-to-via links inside the command field, slow nets, the other pours, and every planned centre line that a routing stage
(s20_cmd, s30_data) has already put on the board: there the real copper is checked.
"Already on the board" is decided per centre line, not per net: a planned line is skipped only if a routing stage has
copper of the same net ON THE SAME LAYER at both of its ends.  The lists of drawn and skipped lines are
printed, and the run fails if a line that belongs to a stage that has not run yet is not drawn.

Result: DRC violations of severity error by type (the gate is: none), and the planned length / delay of every drawn net.

--negative  adds, one at a time on a fresh copy, a track that breaks one spacing or pair rule of
            rules.spacing_rules / the pair rules, and checks that the DRC reports exactly that rule.
"""
import argparse
import collections
import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C      # noqa: E402

STAGE = "rehearse"


def copy_project(workdir, name="rehearsal"):
    os.makedirs(workdir, exist_ok=True)
    dst = os.path.join(workdir, name + ".kicad_pcb")
    shutil.copyfile(C.BOARD, dst)
    shutil.copyfile(C.PRO, os.path.join(workdir, name + ".kicad_pro"))
    shutil.copyfile(C.DRU, os.path.join(workdir, name + ".kicad_dru"))
    # the footprint table resolves ${KIPRJMOD}/lib: link the library next to the copy
    for f in ("fp-lib-table", "lib"):
        t = os.path.join(workdir, f)
        if not os.path.lexists(t):
            os.symlink(os.path.join(C.ROOT, f), t)
    return dst


def planned(spec):
    """(net, layer, width, centre line, what) of every planned track."""
    fpl = spec["floorplan"]
    out = []
    for net, s in fpl["finger_stubs"].items():
        if s["kind"] == "command stub":          # the stubs of the data side are among floorplan.data.wires
            out.append((net, s["layer"], s["width"], s["path"], s["kind"]))
    for net, c in fpl["command"]["columns"].items():
        out.append((net, "In3.Cu", c["in3_width"], c["in3_route"], "command In3"))
    for w in fpl["data"]["wires"]:
        out.append((w["net"], w["layer"], w["width"], w["path"], "data " + w["group"]))
    return out


ROUTING_STAGES = ("s20_cmd", "s30_data", "s50_power")


def owner_of(net, what):
    """The stage that draws each kind of planned centre line (planned(): 'what')."""
    return "s20_cmd" if what.startswith("command") else "s30_data"


def routed_index(board):
    """(net, layer) -> track items a routing stage has drawn; and the set of routing stages present on the board."""
    idx = collections.defaultdict(list)
    present = set()
    for t in board.GetTracks():
        st = C.stage_of(t)
        if st in ROUTING_STAGES:
            present.add(st)
            if t.Type() != C.pcbnew.PCB_VIA_T:
                idx[(t.GetNetname(), board.GetLayerName(t.GetLayer()))].append(t)
    return idx, present


def is_routed(idx, net, layer, path, tol=0.02):
    """True if routed copper of this net on this layer reaches both ends of the planned centre line."""
    items = idx.get((net, layer), [])
    if not items:
        return False

    def touched(pt):
        for t in items:
            for q in (t.GetStart(), t.GetEnd()):
                c = C.k2c(q)
                if abs(c[0] - pt[0]) <= tol and abs(c[1] - pt[1]) <= tol:
                    return True
        return False
    return touched(tuple(path[0])) and touched(tuple(path[-1]))


def draw(board_path, extra=(), power_feed=True):
    """Returns (drawn, skipped, missing, zones ok): drawn / skipped = [(net, layer, what)]; missing = planned lines that
    are neither routed nor could be drawn although their stage has not run (must be empty)."""
    spec = C.spec()
    board = C.load_board(board_path)
    C.rip_up(board, STAGE)
    stage = C.Stage(board, STAGE)
    idx, present = routed_index(board)
    drawn, skipped, missing = [], [], []
    for net, layer, width, path, what in list(planned(spec)) + list(extra):
        if path is None:
            continue
        if is_routed(idx, net, layer, path):
            skipped.append((net, layer, what))
            continue
        if owner_of(net, what) in present and what != "x":
            # the stage that owns this line has run but its copper does not end where the plan does: board and plan disagree
            missing.append((net, layer, what))
        C.add_track(board, stage, net, layer, [tuple(p) for p in path], width)
        drawn.append((net, layer, what))
    filler_ok = C.fill_zones(board)
    C.save_board(board, board_path)
    return drawn, skipped, missing, filler_ok


def drc(board_path):
    out = os.path.splitext(board_path)[0] + "_drc.json"
    r = subprocess.run(["kicad-cli", "pcb", "drc", "--severity-all", "--all-track-errors", "--format", "json", "--units", "mm", "-o", out, board_path],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if r.returncode != 0 or not os.path.exists(out):
        raise RuntimeError("kicad-cli pcb drc failed: %s" % r.stdout[-300:])
    return json.load(open(out))


def summarise(d):
    ox, oy = C.origin()
    by = collections.Counter((v["type"], v["severity"]) for v in d["violations"])
    rows = []
    for v in d["violations"]:
        if v["severity"] == "error":
            pos = [(round(i["pos"]["x"] - ox, 3), round(oy - i["pos"]["y"], 3)) for i in v["items"]][:2]
            rows.append((v["type"], v["description"], pos, [i["description"] for i in v["items"]][:2]))
    return by, rows


def replace_net(plan, net, new_path):
    """Planned list with the centre line of one net replaced (or removed with None)."""
    return [(n, l, w, (new_path if n == net and what in ("command In3", "B.Cu pair") else p), what) for n, l, w, p, what in plan]


def negative_tests(workdir):
    """Each case: (name, extra tracks, modified plan or None, expected violation type, text expected in the description)."""
    spec = C.spec()
    fpl = spec["floorplan"]
    cols = fpl["command"]["columns"]
    xk = cols["CK_P"]["in3_route"][2][0]                     # CK_P leg in the coupled pair
    xd = fpl["data"]["knobs"]["rdqs_down_x"]                 # axis of the RDQS pair on its drop (legs at x -/+ 0.10)
    lq = fpl["data"]["rdqs"]["outer_layer"]                  # the outer layer of the pair: F.Cu (fingers 219 / 221)
    yd = (6.2, 7.2)                                          # a stretch of the F.Cu drop between the via pair (y = 8.50) and the loads; the test tracks stand EAST of it, where that layer carries nothing else
    yd3 = (10.0, 11.0)                                       # a stretch of the In3 part of the drop, above the via pair: the In3 rule
    wy = 10.05                                               # axis of the WCK pair on its east run (B.Cu, legs at y 9.95 / 10.15)
    n0 = next(q for q in fpl["finger_stubs"].values() if q["finger"] == 183)     # a reference stub outside the rule area vref_east_end (finger 183)
    low = min(fpl["data"]["read"].values(), key=lambda q: q["level_y"])
    y_in3_low = low["level_y"]                               # the lowest east run (to finger 182): a foreign In3 track 0.25 mm south of it must be reported
    x_in3_low = (low["read_via"][0] - 1.1, low["read_via"][0] - 0.2)
    base = planned(spec)
    ckp = [tuple(p) for p in cols["CK_P"]["in3_route"]]
    # CK_P pulled 1.0 mm east of its place in the pair between y 8 and 11 (uncoupled) and lengthened
    far = ckp[:3] + [(ckp[2][0], 7.0), (ckp[2][0] + 1.0, 8.0), (ckp[2][0] + 1.0, 10.0), (ckp[2][0], 11.0)] + ckp[3:]
    cases = [
        ("CA2-class track 0.30 mm from CK_P on In3", [("CA2", "In3.Cu", 0.15, [(xk + 0.05 + 0.30 + 0.075, 8.6), (xk + 0.05 + 0.30 + 0.075, 10.6)], "x")],
         None, "clearance", "CK_pair_to_other_tracks"),
        ("WCK-class track 0.30 mm from the RDQS pair on %s" % lq, [("WCK_P", lq, 0.10, [(xd + 0.15 + 0.30 + 0.05, yd[0]), (xd + 0.15 + 0.30 + 0.05, yd[1])], "x")],
         None, "clearance", "RDQS_pair_to_other_tracks"),
        ("read-class track 0.30 mm from the RDQS pair on %s" % lq, [("DQ3", lq, 0.20, [(xd + 0.15 + 0.30 + 0.10, yd[0]), (xd + 0.15 + 0.30 + 0.10, yd[1])], "x")],
         None, "clearance", "RDQS_pair_to_read_tracks"),
        ("read-class track 0.30 mm from the RDQS pair on In3", [("DQ3", "In3.Cu", 0.15, [(xd + 0.15 + 0.30 + 0.075, yd3[0]), (xd + 0.15 + 0.30 + 0.075, yd3[1])], "x")],
         None, "clearance", "RDQS_pair_to_other_tracks_In3"),
        ("two write lines 0.25 mm apart on F.Cu", [("DQ0", "F.Cu", 0.15, [(29.0, fpl["data"]["columns"]["DQ1"]["level_y"] - 0.40), (31.0, fpl["data"]["columns"]["DQ1"]["level_y"] - 0.40)], "x")],
         None, "clearance", "WR_DQ_to_WR_DQ_F_Cu"),
        ("write stub 0.30 mm from the WCK pair on B.Cu", [("DQ5_WF", "B.Cu", 0.20, [(30.0, wy - 0.15 - 0.30 - 0.10), (32.0, wy - 0.15 - 0.30 - 0.10)], "x")],
         None, "clearance", "WCK_pair_to_other_tracks"),
        ("read track 0.30 mm from a reference stub", [("DQ0", n0["layer"], 0.15, [(n0["path"][0][0] - 0.45, 3.45), (n0["path"][0][0] - 0.45, 3.85)], "x")],
         None, "clearance", "VREF_to_switching_nets"),
        ("two read branches 0.25 mm apart on In3", [("DQ5", "In3.Cu", 0.15, [(x_in3_low[0], y_in3_low - 0.40), (x_in3_low[1], y_in3_low - 0.40)], "x")],
         None, "clearance", "RD_DQ_to_RD_DQ_In3"),
        ("CK_P routed 1 mm away from CK_N for 3 mm", [], replace_net(base, "CK_P", far), "diff_pair_uncoupled_length_too_long", "CMD_CK_pair_coupling"),
        ("CK_P 0.8 mm longer than CK_N", [], replace_net(base, "CK_P", far), "skew_out_of_range", "CMD_CK_pair_skew"),
        ("CK legs 0.15 mm apart", [("CK_N", "In3.Cu", 0.10, [(xk - 0.25, 9.0), (xk - 0.25, 10.0)], "x")],
         replace_net(base, "CK_N", None), "diff_pair_gap_out_of_range", ""),
    ]
    results = []
    for i, (name, extra, plan, vtype, text) in enumerate(cases):
        b = copy_project(workdir, "neg%d" % i)
        board = C.load_board(b)
        for st in ROUTING_STAGES:           # the rule tests run on the bare plan: the real routing is taken off the copy
            C.rip_up(board, st)
        stage = C.Stage(board, STAGE)
        for net, layer, width, path, what in (plan if plan is not None else base) + extra:
            if path is None:
                continue
            C.add_track(board, stage, net, layer, [tuple(p) for p in path], width, allow_any_angle=True)
        C.save_board(board, b)
        d = drc(b)
        sev = ("warning",) if vtype == "diff_pair_gap_out_of_range" else ("error",)
        hit = [v for v in d["violations"] if v["type"] == vtype and v["severity"] in sev and text in v["description"]
               and (vtype != "diff_pair_gap_out_of_range" or "0.15" in v["description"])]
        results.append((name, vtype, text, len(hit), hit[0]["description"][:140] if hit else ""))
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("workdir")
    ap.add_argument("--negative", action="store_true")
    ap.add_argument("--render", default="")
    ap.add_argument("--json", default="")
    a = ap.parse_args()
    spec = C.spec()
    b = copy_project(a.workdir)
    drawn, skipped, missing, ok = draw(b)
    C.log(STAGE, "%d planned centre lines drawn on %s (zones refilled: %s)" % (len(drawn), b, "ok" if ok else "FAILED"))

    def by_kind(rows):
        k = collections.defaultdict(list)
        for net, layer, what in rows:
            k["%s (%s)" % (what, layer)].append(net)
        return "; ".join("%s: %s" % (w, " ".join(sorted(v))) for w, v in sorted(k.items())) or "none"
    C.log(STAGE, "  drawn:   %s" % by_kind(drawn))
    C.log(STAGE, "  skipped (a routing stage has this line on the board, same net, same layer, both ends): %s" % by_kind(skipped))
    # completeness: every planned line is either routed or drawn
    board0 = C.load_board(C.BOARD)
    _idx, present = routed_index(board0)
    need = [(net, layer, what) for net, layer, width, path, what in planned(spec) if path is not None and owner_of(net, what) not in present]
    not_drawn = [q for q in need if q not in set(drawn)]
    plan_ok = not missing and not not_drawn and len(drawn) + len(skipped) == sum(1 for q in planned(spec) if q[3] is not None)
    C.log(STAGE, "  completeness: %d planned lines = %d drawn + %d routed; lines of stages that have not run and are not drawn: %s; routed "
          "stage whose copper does not end where the plan does: %s -> %s"
          % (sum(1 for q in planned(spec) if q[3] is not None), len(drawn), len(skipped), not_drawn or "none", missing or "none", "ok" if plan_ok else "FAILED"))
    d = drc(b)
    by, rows = summarise(d)
    errors = {t: c for (t, s), c in by.items() if s == "error"}
    warns = {t: c for (t, s), c in by.items() if s != "error"}
    feed_ok = True
    C.log(STAGE, "DRC errors %s" % (dict(sorted(errors.items())) or "none"))
    C.log(STAGE, "DRC warnings %s; unconnected items %d" % (dict(sorted(warns.items())), len(d.get("unconnected_items", []))))
    for t, desc, pos, items in rows[:40]:
        C.log(STAGE, "  %s: %s @ %s  %s" % (t, desc[:150], pos, items))
    # lengths and delays of the planned nets
    board = C.load_board(b)
    rep = {}
    for gname in ("CMD", "WR_WCK", "RD_P", "RD_RDQS"):
        g = next(g for g in spec["groups"] if g["name"] == gname)
        for net in list(g["nets"]) + list(g.get("finger_side_nets") or []):
            r = C.net_report(board, net)
            rep[net] = {"mm": r["mm"], "ps": r["ps"], "layers": {k: v["mm"] for k, v in r["layers"].items()}}
    for pair in (("CK_P", "CK_N"), ("WCK_P", "WCK_N"), ("RDQS_P", "RDQS_N")):
        C.log(STAGE, "  %s %.3f mm / %s %.3f mm on the drawn layer(s): difference %.3f mm"
              % (pair[0], rep[pair[0]]["mm"], pair[1], rep[pair[1]]["mm"], rep[pair[0]]["mm"] - rep[pair[1]]["mm"]))
    allowed = set()                          # the plan of the data side is the routed geometry: nothing is left untuned
    bad = {t: c for t, c in errors.items() if t not in allowed}
    neg = []
    if a.negative:
        neg = negative_tests(a.workdir)
        for name, vtype, text, hits, desc in neg:
            C.log(STAGE, "  negative test %-48s -> %s %s: %s  %s" % (name, vtype, text, "REPORTED (%d)" % hits if hits else "NOT REPORTED", desc))
    if a.json:
        json.dump({"errors": errors, "warnings": warns, "rows": rows, "lengths": rep, "negative": neg, "drawn": drawn, "skipped": skipped,
                   "plan_complete": plan_ok}, open(a.json, "w"), indent=1)
    if a.render:
        import render
        sys.argv = ["render.py", a.render, "--board", b, "--stage", "rehearsal: planned centre lines drawn", "--prefix", "plan_"]
        render.main()
    fail = bool(bad) or any(h == 0 for _, _, _, h, _ in neg) or not plan_ok or not feed_ok
    C.log(STAGE, "RESULT: %s" % ("FAIL" if fail else "pass (DRC errors: none)"))
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()

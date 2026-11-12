#!/usr/bin/env python3
"""Stage s10: the fan-out of U1 and the vias of the divider fields.  No bundles.

  - via-in-pad 0.40 / 0.15 (filled and capped) on the balls listed in floorplan.U1.via_in_pad.balls
    (every supply ball and every routed signal ball; not on the omitted ones, not on DQ / NC balls); the two of
    floorplan.U1.via_in_pad.in3_pad_trim (C4, B3: the RDQS pair leaves them on In3) with a 0.35 mm pad on the inner layers
  - F.Cu links of the balls without a via and of the strapped balls (floorplan.U1.f_cu_links)
  - B.Cu tie of CK_c_B (floorplan.U1.b_cu_ties)
  - nothing for the unused byte: DQ8-15 are open (floorplan.U1.unused_byte)
  - the node, fence, staircase, return and GND vias of the divider fields (floorplan.vias, 0.40 / 0.20)
  - the finger vias: one 0.40 / 0.20 via per VSS / VDD finger pair at y = 3.05 with the short ties to its fingers
    (floorplan.finger_vias).  They are placed here, before any routing, because their positions and the finger
    stubs of the three buses (floorplan.finger_stubs) were fixed together

Re-running the stage removes what it made before (UUID tag) and builds it again.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C      # noqa: E402

STAGE = "s10_escape"


def main():
    spec = C.spec()
    fpl = spec["floorplan"]
    u1 = fpl["U1"]
    board = C.load_board()
    removed = C.rip_up(board, STAGE)
    stage = C.Stage(board, STAGE)

    # 1. via-in-pads
    size = u1["via_in_pad"]["size"]
    pins = C.parts()["U1"]["pins"]
    n_vip = 0
    for b, v in u1["via_in_pad"]["balls"].items():
        assert pins[b] == v["net"], (b, pins[b], v["net"])
        x, y = C.ball(board, b)
        assert abs(x - v["xy"][0]) < 1e-6 and abs(y - v["xy"][1]) < 1e-6, "ball %s is not where the spec has it" % b
        trim = u1["via_in_pad"].get("in3_pad_trim") or {}
        C.add_via(board, stage, v["net"], x, y, size["diameter"], size["drill"], in_pad=True,
                  remove_unused_pads=u1["via_in_pad"].get("unused_inner_pads") == "removed",
                  inner_diameter=trim.get("inner_diameter") if b in trim.get("balls", []) else None)
        n_vip += 1

    # 2. F.Cu links and straps, 3. B.Cu ties
    n_link = 0
    for l in u1["f_cu_links"] + u1["b_cu_ties"]:
        a, b = C.ball(board, l["from"]), C.ball(board, l["to"])
        p = [tuple(q) for q in l["path"]]
        assert max(abs(p[0][0] - a[0]), abs(p[0][1] - a[1]), abs(p[-1][0] - b[0]), abs(p[-1][1] - b[1])) < 1e-6, l
        assert pins[l["from"]] == pins[l["to"]] == l["net"], l
        C.add_track(board, stage, l["net"], l["layer"], p, l["width"])
        n_link += 1

    # 4. the unused byte (DQ8-15) is open: nothing to draw (floorplan.U1.unused_byte)
    assert "unused_byte_escape" not in u1 and all(pins[b] == "" for b in u1["unused_byte"]["balls"].values())

    # 5. field vias
    for v in fpl["vias"]:
        # 'fill': filled and capped like a via-in-pad (a via whose pad is next to a solder land, e.g. the command node vias);
        # 'unused_inner_pads': removed where the via connects to nothing (the command fence vias: no pad on In3)
        C.add_via(board, stage, v["net"], v["x"], v["y"], v["diameter"], v["drill"], in_pad=v.get("fill") == "filled and capped",
                  remove_unused_pads=v.get("unused_inner_pads") == "removed")

    # 6. finger vias and their ties
    j1 = C.parts()["J1"]["pins"]
    n_tie = 0
    for v in fpl["finger_vias"]:
        # no pad on an inner layer where the via connects to nothing: a VSS finger via inside the V1P2 island of In2 leaves no land there
        C.add_via(board, stage, v["net"], v["x"], v["y"], v["diameter"], v["drill"], remove_unused_pads=True)
        for t in v["ties"]:
            assert j1[str(t["finger"])] == v["net"] and C.finger_layer(t["finger"]) == t["layer"], t
            fx, fy = C.finger(board, t["finger"])
            p0 = tuple(t["path"][0])
            assert abs(p0[0] - fx) < 1e-6 and p0[1] < C.finger_top(t["finger"])[1], "tie of finger %d does not start on its pad" % t["finger"]
            C.add_track(board, stage, v["net"], t["layer"], [tuple(q) for q in t["path"]], t["width"])
            n_tie += 1

    C.save_board(board)
    C.log(STAGE, "ripped up %d items; %d via-in-pads, %d links / ties, %d field vias, %d finger vias with %d ties (unused byte DQ8-15: open)"
          % (removed, n_vip, n_link, len(fpl["vias"]), len(fpl["finger_vias"]), n_tie))


if __name__ == "__main__":
    main()

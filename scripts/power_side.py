#!/usr/bin/env python3
"""Power, slow nets and pours of the card as ONE plan (stage s50_power): the 1.2 V feed, the three regulators, the rails to U1,
decoupling, the DAC with its I2C, RESET_n and the power pours under U1 (six outlines).  There is NO ground pour on an outer layer and no
stitching via: every GND pad has a via of its own (in the pad or at the end of a short track); see floorplan.power.outer_layer_ground.  scripts/build_netlist.py writes the
result to floorplan.power.build; scripts/pcb/s50_power.py draws it and decides nothing.

Rules of this plan (DESIGN.md section 15):

  no layer hop   a slow or power wire stays on ONE layer for its whole run and changes layer at most at its ends, where a pad of
                 the net stands on the other layer.  No pad-free stretch of track lies between two vias.  In3 carries the
                 command group, the read group and the RDQS pair only
  one layer per regulator  U2 (VDD2H, 0.52 A, its output goes straight into the In4 island) stands on F.Cu.  U3 (VDD1) and U4
                 (VDDQ) stand on B.Cu with all their parts: on one layer the nets V1P2 / PG / V3P3 cannot be brought from U2 to U4
                 without a crossing (the pin order of both packages is the same sense of rotation), and the 3.3 V trunk, which
                 comes from the east, cuts the 1.2 V arm off from the enable of U3.  With U3 / U4 on the back every one of these
                 nets changes layer once, at a via beside the pin it ends on, and the two outputs VDD1 / VDDQ start on the layer
                 on which they are distributed (B.Cu) without any via
  1.2 V feed     a 1.5 mm STRIP on F.Cu in the corridor between the comb and the read end (not a pour), sized below
  crossings      power and slow wires cross a high-speed wire only where the topology leaves no choice (listed in
                 floorplan.power.build.crossings_expected, counted on the board by gate 8)

Coordinates: card frame, mm.
"""
import math
from collections import OrderedDict

# ------------------------------------------------------------------------------------------------ copper and sizing
RHO_CU = 1.72e-5            # ohm mm at 20 C
T_OUTER = 0.035             # mm, stackup nominal of F.Cu / B.Cu (finished copper is 0.040 .. 0.055: the figures below are worst case)
T_INNER = 0.018
VIA_PLATING = 0.020         # mm, barrel wall
I_RAIL = OrderedDict(       # A, worst case (DESIGN.md section 4)
    V1P2=0.77, V1P2_to_U2=0.53, V1P2_to_U4=0.23, V3P3=0.030, VDD1=0.026, VDD2H=0.52, VDDQ=0.225)

X_STRIP, W_STRIP = 45.65, 1.50          # 1.2 V strip: centre line and width
Y_STRIP0, Y_STRIP1 = 4.40, 20.65        # its centre line runs from the island vias to the mitre into the arm
Y_ARM, W_ARM = 21.05, 0.90              # west arm to U2 (and the taps of U4 / R504); its north edge is 0.13 mm south of the land of R503 on the PG track
X_ARM_END = 35.75
Y_PG = 21.90                            # PG_VDD2H on F.Cu, 0.325 mm north of the arm
Y_3V3 = 22.92                           # 3.3 V trunk on F.Cu
X_3V3_N = 62.97                         # ... its north - south part (axis of C332 pad 1)
Y_VDD1_LANE = 25.005                    # B.Cu: VDD1 from U3 west to the east rail of U1
Y_VDDQ_LANE = 27.25                     # B.Cu: VDDQ from U4 west to the north rail of U1
W_VDDQ_LANE = 1.00                      # 2.4 mV of the 4.8 mV to the farthest ball; 1.5 mm was tried (4.0 mV) and not kept: the limit of 5 mV is self-set,
                                        # U4 at its lowest (0.488 V) minus 4.8 mV is still 13 mV above the JEDEC minimum, and the card is to stay minimal
Y_RESET = 17.50                         # B.Cu: RESET_n east, 0.5 mm south of the VDD2H island of In4

W_LINK_WIDE, W_LINK_NECK = 0.30, 0.12   # the VDD2H link F6 - K6 under U1 (B.Cu)
Y_POUR_TOP_LOW = 15.65                  # top of the three lower pours under U1: 0.35 mm south of the B.Cu pads of the command vias of row G
ISLAND_WORST_MV, ISLAND_MEAN_MV = 5.2, 3.8      # In2 island at 0.77 A, from the sheet solve of gate 9 (west-most finger via / mean over the 19 fingers)
VIA_PWR = OrderedDict(diameter=0.45, drill=0.20)
VIA_SIG = OrderedDict(diameter=0.40, drill=0.20)
POUR_TO_PAIR = 0.30                     # a power pour edge that runs parallel to the WCK pair: this far from its copper (the DRC rule)


def sheet_mohm(t=T_OUTER, temp_c=20.0):
    return RHO_CU / t * (1 + 0.0039 * (temp_c - 20.0)) * 1e3


def ipc2221_rise(i, w, t=T_OUTER, external=True):
    """Temperature rise (K) of a trace of width w (mm) and thickness t (mm) at i (A), IPC-2221: I = k dT^0.44 A^0.725 (A in mil^2)."""
    a = (w / 0.0254) * (t / 0.0254)
    k = 0.048 if external else 0.024
    return (i / (k * a ** 0.725)) ** (1 / 0.44)


def via_mohm(length, drill=0.20, wall=VIA_PLATING):
    return RHO_CU * length / (math.pi * (drill + wall) * wall) * 1e3


def strip_sizing():
    """The 1.2 V strip by calculation: resistance, drop, dissipation, temperature rise and current density of every part of the
    feed, and the width a 5 mV budget needs."""
    rs = sheet_mohm()
    parts = OrderedDict()

    def seg(name, length, width, amps):
        r = rs * length / width
        parts[name] = OrderedDict(length_mm=round(length, 2), width_mm=width, current_a=amps, squares=round(length / width, 2),
                                  mohm=round(r, 2), mv=round(r * amps, 2), mw=round(r * amps * amps, 2),
                                  rise_k=round(ipc2221_rise(amps, width), 2),
                                  a_per_mm2=round(amps / (width * T_OUTER), 1))
    seg("strip (corridor)", Y_STRIP1 - 4.70, W_STRIP, I_RAIL["V1P2"])
    seg("arm, corner to the U4 tap", 1.414 * (Y_ARM - Y_STRIP1) + (X_STRIP - (Y_ARM - Y_STRIP1) - X_U4_TAP), W_ARM, I_RAIL["V1P2"])
    seg("arm, U4 tap to U2", X_U4_TAP - X_ARM_END, W_ARM, I_RAIL["V1P2_to_U2"])
    tot_u2 = sum(p["mv"] for p in parts.values())
    budget_mv = 5.0
    l_strip = Y_STRIP1 - 4.70
    need = rs * l_strip * I_RAIL["V1P2"] / budget_mv
    return OrderedDict(
        copper="F.Cu, %.3f mm (stackup nominal; plated copper is thicker), %.3f mohm per square at 20 C" % (T_OUTER, rs),
        currents_a=I_RAIL,
        parts=parts,
        drop_fingers_to_u2_mv=round(tot_u2, 2),
        drop_fingers_to_u2_mv_is="the F.Cu copper only: foot of the strip to U2 IN",
        island=OrderedDict(
            layer="In2.Cu", copper_mm=T_INNER, mohm_per_square=round(sheet_mohm(T_INNER), 3),
            expected_worst_finger_mv=ISLAND_WORST_MV, expected_mean_mv=ISLAND_MEAN_MV,
            note="the drop inside the In2 island depends on its filled shape (bridge north of the key slot, antipads): gate 9 of scripts/pcb/check_pcb.py "
                 "solves the sheet on the copper as filled (every finger via injects its share, the four strip vias at 0 V). The two figures here are "
                 "that result, kept as the expectation: about 2.3 mV of the worst figure is the bridge"),
        drop_fingers_to_u2_total_mv=OrderedDict(
            worst=round(tot_u2 + ISLAND_WORST_MV + via_mohm(0.489) * I_RAIL["V1P2"] / 4, 1),
            mean=round(tot_u2 + ISLAND_MEAN_MV + via_mohm(0.489) * I_RAIL["V1P2"] / 4, 1),
            limit_mv=15.0,
            why_15="worst corner: host rail 1.14 V, 0.52 A, pass element of U2 68 mV: U2 still delivers 1.14 - 0.015 - 0.068 = 1.057 V, 47 mV above the JEDEC "
                   "minimum of VDD2H (1.01 V); at 1.20 V the regulator is far from dropout"),
        drop_fingers_to_u4_mv=round(parts["strip (corridor)"]["mv"] + parts["arm, corner to the U4 tap"]["mv"], 2),
        budget="%.0f mV at %.2f A in the strip alone (0.4 %% of 1.2 V); fingers to U2 IN with the island: see drop_fingers_to_u2_total_mv" % (budget_mv, I_RAIL["V1P2"]),
        width_for_the_budget_mm=round(need, 2),
        width_for_10_k_rise_mm=0.12,
        width_chosen_mm=W_STRIP,
        margin="%.0f %% over the width the 5 mV budget needs (etch and copper-thickness tolerance)" % (100 * (W_STRIP / need - 1)),
        island_vias=OrderedDict(count=4, each_a=round(I_RAIL["V1P2"] / 4, 3), barrel_mm=0.489,
                                each_mohm=round(via_mohm(0.489), 2), drop_mv=round(via_mohm(0.489) * I_RAIL["V1P2"] / 4, 2)),
        fingers=OrderedDict(count=19, each_a=round(I_RAIL["V1P2"] / 19, 3), limit_a=0.5),
        crossings="the strip passes over the eight In3 read lines (on their 45-degree ramp) and over the two legs of the RDQS pair "
                  "on In3 (90 degrees), with In1 and In2 solid GND between: 10 plan-view crossings, the minimum of any feed that leaves "
                  "the finger row between the comb and the read end")


# ------------------------------------------------------------------------------------------------ placements
X_U4_TAP = 44.40            # V1P2 vias of U4 on the arm
# ref -> (side, x, y, rot_deg, note).  rot: 90 = pad 1 south, 270 = pad 1 north, 0 = pad 1 west, 180 = pad 1 east (two-pad parts)
PLACE = OrderedDict([
    # ---- RESET_n at finger 232 (B.Cu): the whole run is on the back, no via at the finger end ------------------------------------
    ("R402", ("B", 61.175, 4.75, 90, "series, on the axis of finger 232, pad 1 toward the finger")),
    ("R403", ("B", 61.675, 6.40, 270, "pull-down on the axis of the VSS finger 234: GND pad south, straight down to that pair's finger via")),
    # ---- reference DAC (F.Cu) ---------------------------------------------------------------------------------------------------
    ("C511", ("F", 65.20, 13.38, 90, "3.3 V bulk on the 3.3 V run north of U5 (the tooling hole and the 3 mm side margin leave no place at the finger)")),
    # ---- 1.2 V input capacitors east of the strip (F.Cu), V1P2 pad on the strip, GND pad east --------------------------------------
    ("C512", ("F", 46.95, 4.50, 0, "100 nF; GND down to the finger via of the VSS pair 175 / 176")),
    ("C502", ("F", 47.00, 5.85, 0, "22 uF, 0.9 mm high (courtyard above y = 5.10); GND down to the finger via of the VSS pair 180 / 181: no via in the In3 zone of the read bundle")),
    ("C513", ("F", 43.00, 20.27, 270, "100 nF on the arm at the tap of U4; own GND via south of it")),
    # ---- VDD2H regulator (F.Cu): U2 with IN / PG / BIAS / EN east, OUT / FB / SS / GND west ----------------------------------------
    ("U2", ("F", 33.75, 21.90, 180, "pins 1, 2 (IN) at the south-east on the 1.2 V arm, 9, 10 (OUT) at the south-west")),
    ("C501", ("F", 36.75, 20.35, 270, "input: V1P2 pad north, on the arm")),
    ("C505", ("F", 30.20, 20.30, 270, "output: VDD2H pad north, on the output strap")),
    ("R501", ("F", 28.90, 21.20, 90, "FB top: VDD2H south, FB north; west of C505")),
    ("R502", ("F", 28.90, 23.10, 90, "FB bottom: FB south, GND north; in line above R501")),
    ("C504", ("F", 31.10, 22.88, 90, "soft start: SS south, GND north (to the GND pin of U2)")),
    ("C503", ("F", 36.60, 23.40, 90, "bias: V3P3 south, on the 3.3 V trunk")),
    ("R503", ("F", 38.00, 22.41, 270, "PG pull-up: V3P3 north (trunk), PG south (PG track)")),
    # ---- VDD1 regulator (B.Cu) --------------------------------------------------------------------------------------------------
    ("U3", ("B", 40.50, 24.055, 180, "IN north-east, EN south-east, OUT north-west (toward U1)")),
    ("C506", ("B", 43.02, 24.525, 270, "input: across the IN and GND pins")),
    ("C507", ("B", 37.95, 24.525, 270, "output: VDD1 north (on the lane), GND south")),
    ("R509", ("B", 36.50, 24.555, 270, "bleeder: VDD1 north, GND south")),
    ("R504", ("B", 41.6375, 21.40, 90, "enable: V1P2 south (via from the arm), PWR_EN north, straight into the EN pin")),
    # ---- VDDQ regulator (B.Cu) --------------------------------------------------------------------------------------------------
    ("U4", ("B", 46.60, 24.60, 0, "west: BIAS, GND, IN (south to north); east: EN, FB, OUT")),
    ("C508", ("B", 44.40, 25.73, 90, "input: V1P2 south, GND north")),
    ("C509", ("F", 46.20, 23.40, 90, "bias: on the 3.3 V trunk beside the tap via of U4, GND north on the via of the exposed pad")),
    ("C510", ("B", 49.80, 25.73, 90, "output: VDDQ south, GND north")),
    ("R506", ("B", 48.80, 24.74, 270, "FB top: VDDQ north, FB south")),
    ("R507", ("B", 48.80, 22.79, 270, "FB bottom: FB north, GND south")),
    ("R508", ("B", 50.58, 23.60, 180, "bleeder: VDDQ east, GND west")),
    # ---- test pads (F.Cu), each where its rail comes to the front ------------------------------------------------------------------
    ("TP1", ("F", 34.50, 25.30, 0, "VDD1")),
    ("TP2", ("F", 28.40, 19.10, 0, "VDD2H")),
    ("TP3", ("F", 50.90, 20.80, 0, "VDDQ")),
    ("TP5", ("F", 53.10, 19.30, 0, "GND")),
])


# ------------------------------------------------------------------------------------------------ RESET_n
RESET_FINGER = 232                                  # back side, bank 66 T0L (AP11), static LVCMOS12 output
RESET_VIA = (28.20, Y_RESET)
X_RESET_DROP = 61.50


def reset_stub(finger_x, y_pad1):
    """The finger stub of RESET_n (B.Cu): 0.15 mm through the row of the finger vias, 0.20 mm above it, straight up the finger axis."""
    x = finger_x(RESET_FINGER)
    return (OrderedDict(net="RESET_N_F", layer="B.Cu", width=0.15, path=[[x, 2.60], [x, 3.45]]),
            OrderedDict(net="RESET_N_F", layer="B.Cu", width=0.20, path=[[x, 3.45], [x, round(y_pad1, 4)]]))


# ------------------------------------------------------------------------------------------------ the plan
def build(P, ball, finger_x, finger_via, wck_n_b_cu):
    """P('U2.3') -> pad centre; ball('H1') -> ball centre; finger_x(n); finger_via(n) -> position of the finger via of a VSS finger;
    wck_n_b_cu: the centre line of WCK_N on B.Cu (data side), which bounds the VDDQ enclave pour."""
    W, V, Z = [], [], []

    def wire(net, layer, width, pts, role, group):
        pts = [(round(p[0] + 0.0, 4), round(p[1] + 0.0, 4)) for p in pts]
        for a, b in zip(pts, pts[1:]):
            dx, dy = abs(a[0] - b[0]), abs(a[1] - b[1])
            assert dx < 1e-6 or dy < 1e-6 or abs(dx - dy) < 1e-6, "%s %s: %s -> %s is not at a multiple of 45 degrees" % (net, role, a, b)
            assert dx + dy > 1e-6, "%s %s: zero-length segment at %s" % (net, role, a)
        W.append(OrderedDict(net=net, layer=layer, width=width, group=group, role=role, path=[list(p) for p in pts]))

    def via(net, x, y, kind, role, group, in_pad=False):
        d = VIA_PWR if kind == "power" else VIA_SIG
        V.append(OrderedDict(net=net, x=round(x + 0.0, 4), y=round(y + 0.0, 4), diameter=d["diameter"], drill=d["drill"], role=role, group=group,
                             in_pad=bool(in_pad)))

    def pour(net, layer, name, outline, **kw):
        Z.append(OrderedDict(net=net, layer=layer, name=name, outline=[[round(x, 3), round(y, 3)] for x, y in outline], **kw))

    def d45(a, x=None, y=None):
        """End of a 45-degree run from a to the given x or y."""
        if x is not None:
            return (x, a[1] + (abs(x - a[0]) if y is None or y > a[1] else -abs(x - a[0])))
        raise ValueError

    # ================================================================ 1.2 V: island vias, strip, arm, capacitors
    for vx, vy in ((45.15, 4.35), (45.85, 4.35), (45.15, 5.05), (45.85, 5.05)):
        via("V1P2", vx, vy, "power", "In2 island to the F.Cu strip (0.19 A each)", "V1P2")
    wire("V1P2", "F.Cu", W_STRIP, [(X_STRIP, Y_STRIP0), (X_STRIP, Y_STRIP1)], "1.2 V strip in the corridor between the comb and the read end", "V1P2")
    wire("V1P2", "F.Cu", W_ARM, [(X_STRIP, Y_STRIP1), (X_STRIP - (Y_ARM - Y_STRIP1), Y_ARM), (X_ARM_END, Y_ARM)], "1.2 V arm west to U2", "V1P2")
    for n in ("1", "2"):
        p = P("U2." + n)
        wire("V1P2", "F.Cu", 0.25, [p, (X_ARM_END, p[1])], "U2 IN pin %s to the arm" % n, "V1P2")
    c = P("C501.1")
    assert abs(c[1] - Y_ARM) < W_ARM / 2 - 0.3, c
    wire("V1P2", "F.Cu", 0.60, [c, (c[0], Y_ARM)], "C501 (22 uF) on the arm", "V1P2")
    for ref, w_ in (("C512", 0.40), ("C502", 0.60)):
        p = P(ref + ".1")
        wire("V1P2", "F.Cu", w_, [p, (X_STRIP, p[1])], "%s to the strip" % ref, "V1P2")
    g = P("C512.2")
    fv = finger_via(175)
    wire("GND", "F.Cu", 0.25, [g, (g[0], fv[1] + (g[0] - fv[0])), fv], "C512 to the finger via of the VSS pair 175 / 176", "GND")
    g = P("C502.2")
    fv = finger_via(180)
    assert fv[0] > g[0] and g[1] - (fv[0] - g[0]) > fv[1], (g, fv)
    wire("GND", "F.Cu", 0.30, [g, (fv[0], g[1] - (fv[0] - g[0])), fv], "C502 (pad centre) to the finger via of the VSS pair 180 / 181", "GND")
    c = P("C513.1")
    assert abs(c[1] - Y_ARM) < W_ARM / 2 - 0.1, c
    wire("V1P2", "F.Cu", 0.40, [c, (c[0], Y_ARM)], "C513 (100 nF) on the arm", "V1P2")
    g = P("C513.2")
    wire("GND", "F.Cu", 0.30, [g, (g[0], g[1] - 0.75)], "C513 to its GND via", "GND")
    via("GND", g[0], g[1] - 0.75, "power", "GND of C513", "GND")
    g = P("C501.2")
    wire("GND", "F.Cu", 0.40, [g, (g[0], g[1] - 0.875)], "C501 to its GND via", "GND")
    via("GND", g[0], g[1] - 0.875, "power", "GND of C501", "GND")

    # ================================================================ U2 (F.Cu): output, feedback, soft start, GND
    o9, o10 = P("U2.9"), P("U2.10")
    yo = (o9[1] + o10[1]) / 2
    co = P("C505.1")
    wire("VDD2H", "F.Cu", 0.25, [o10, o9], "U2 OUT pins", "VDD2H")
    wire("VDD2H", "F.Cu", 0.80, [(o9[0], yo), (co[0], yo)], "U2 output strap to C505 (22 uF)", "VDD2H")
    for vx in (31.10, 31.60):
        for vy in (yo - 0.25, yo + 0.25):
            via("VDD2H", vx, vy, "power", "U2 output into the VDD2H island of In4 (0.13 A each)", "VDD2H")
    r1, r12, r2, tp2 = P("R501.1"), P("R501.2"), P("R502.1"), P("TP2.1")
    assert abs(r1[0] - r2[0]) < 1e-9 and r1[1] < yo < r12[1] < r2[1]
    xk = co[0] - 0.58
    wire("VDD2H", "F.Cu", 0.25, [(co[0], yo), (xk, yo), (xk - (yo - r1[1]), r1[1]), r1], "output strap to R501 (sense)", "VDD2H")
    wire("VDD2H", "F.Cu", 0.25, [r1, (r1[0], tp2[1] + (r1[0] - tp2[0])), tp2], "R501 to the test pad TP2", "VDD2H")
    fb = P("U2.8")
    assert r12[1] < fb[1] < r2[1]
    wire("VDD2H_FB", "F.Cu", 0.15, [r12, (r1[0], fb[1]), r2], "FB: R501 - R502", "SLOW")
    wire("VDD2H_FB", "F.Cu", 0.15, [fb, (r1[0], fb[1])], "FB: U2 pin 8 west to the divider", "SLOW")
    ss, c4 = P("U2.7"), P("C504.1")
    wire("VDD2H_SS", "F.Cu", 0.15, [ss, c4], "soft start: U2 pin 7 to C504", "SLOW")
    g6, c42 = P("U2.6"), P("C504.2")
    wire("GND", "F.Cu", 0.25, [g6, (g6[0] + 0.75, g6[1])], "U2 GND pin into the exposed pad", "GND")
    wire("GND", "F.Cu", 0.25, [c42, (c42[0] + (c42[1] - g6[1]), g6[1]), g6], "C504 to the GND pin of U2", "GND")
    for vx in (33.35, 34.15):
        for vy in (21.30, 22.50):
            via("GND", vx, vy, "power", "U2 exposed pad", "GND", in_pad=True)
    g = P("R502.2")
    wire("GND", "F.Cu", 0.25, [g, (g[0], g[1] + 0.75)], "R502 to its GND via", "GND")
    via("GND", g[0], g[1] + 0.75, "power", "GND of R502", "GND")
    g = P("C505.2")
    wire("GND", "F.Cu", 0.40, [g, (g[0], g[1] - 0.825)], "C505 to its GND via", "GND")
    via("GND", g[0], g[1] - 0.825, "power", "GND of C505", "GND")

    # ================================================================ PG (F.Cu, one via at the EN pin of U4)
    pg, r32 = P("U2.3"), P("R503.2")
    en4 = P("U4.6")
    assert abs(pg[1] - Y_PG) < 1e-9 and abs(r32[1] - Y_PG) < 1e-9
    wire("PG_VDD2H", "F.Cu", 0.15, [pg, r32, (en4[0], Y_PG)], "PG: U2 pin 3 east along the arm, R503, to its via south of U4 EN", "SLOW")
    via("PG_VDD2H", en4[0], Y_PG, "signal", "PG to U4 EN on B.Cu", "SLOW")
    wire("PG_VDD2H", "B.Cu", 0.15, [(en4[0], Y_PG), en4], "PG via straight north into U4 EN", "SLOW")

    # ================================================================ 3.3 V: finger 255, DAC, trunk on F.Cu to U2, taps to U3 / U4
    xf = finger_x(255)
    xe = 67.30
    pin6, c32 = P("U5.6"), P("C332.1")
    assert abs(c32[0] - X_3V3_N) < 1e-9
    y_run = 12.90
    wire("V3P3", "F.Cu", 0.30, [(xf, 2.60), (xf, 4.20), (66.15, 4.20 + (xf - 66.15)), (66.15, 7.10), (xe, 7.10 + (xe - 66.15)), (xe, y_run),
                                (P("C511.1")[0], y_run), (X_3V3_N, y_run)],
         "3.3 V from finger 255: west of the tooling hole, up the east side of U5 (outside SCL / SDA), west along the north of the DAC", "V3P3")
    assert abs(P("C511.1")[1] - y_run) < 1e-9
    wire("V3P3", "F.Cu", 0.30, [(X_3V3_N, y_run), c32], "to C332 (100 nF at the DAC supply pin)", "V3P3")
    wire("V3P3", "F.Cu", 0.25, [c32, (c32[0], pin6[1] + 0.35), (c32[0] + 0.35, pin6[1]), pin6], "C332 to U5 VDD", "V3P3")
    r31, c31 = P("R503.1"), P("C503.1")
    b2 = P("U2.4")
    assert abs(r31[1] - Y_3V3) < 1e-9 and abs(c31[1] - Y_3V3) < 1e-9
    x_u3_tap, x_u4_tap, x_c509 = 43.75, P("U4.1")[0], P("C509.1")[0]
    assert abs(P("C509.1")[1] - Y_3V3) < 1e-9
    xd = c31[0] - 0.15
    wire("V3P3", "F.Cu", 0.30, [(X_3V3_N, y_run), (X_3V3_N, Y_3V3 - 0.5), (X_3V3_N - 0.5, Y_3V3), (x_c509, Y_3V3), (x_u4_tap, Y_3V3), (x_u3_tap, Y_3V3),
                                r31, c31, (xd, Y_3V3), (xd - (Y_3V3 - b2[1]), b2[1]), b2],
         "3.3 V trunk: north from the DAC, west along y = %.2f to R503 / C503 and into U2 BIAS; taps of U4 and U3 on it" % Y_3V3, "V3P3")
    g = P("C503.2")
    xg = 37.20
    c72, r92 = P("C507.2"), P("R509.2")
    assert abs(c72[1] - r92[1]) < 1e-9
    yg = c72[1]
    wire("GND", "F.Cu", 0.25, [g, (xg - (yg - g[1]), g[1]), (xg, yg)], "C503 to the GND via it shares with C507 / R509 (B.Cu)", "GND")
    via("GND", xg, yg, "power", "GND of C503 (F.Cu) and of C507 / R509 (B.Cu)", "GND")
    wire("GND", "B.Cu", 0.25, [r92, (xg, yg), c72], "R509 and C507 to their GND via", "GND")
    # taps
    in3 = P("U3.1")
    c61, c62 = P("C506.1"), P("C506.2")
    via("V3P3", x_u3_tap, Y_3V3, "signal", "3.3 V tap of U3 (IN)", "V3P3")
    wire("V3P3", "B.Cu", 0.30, [(x_u3_tap, Y_3V3), (x_u3_tap, c61[1]), c61, (in3[0], c61[1])], "tap via north, through C506, into U3 IN", "V3P3")
    gnd3 = P("U3.2")
    wire("GND", "B.Cu", 0.30, [(gnd3[0], c62[1]), c62, (c62[0], c62[1] - 0.595)], "U3 GND pin, C506, their GND via", "GND")
    via("GND", c62[0], c62[1] - 0.595, "power", "GND of U3 and C506", "GND")
    b4 = P("U4.1")
    via("V3P3", x_u4_tap, Y_3V3, "signal", "3.3 V tap of U4 (BIAS)", "V3P3")
    wire("V3P3", "B.Cu", 0.30, [(x_u4_tap, Y_3V3), b4], "tap via straight north into U4 BIAS", "V3P3")
    ep4 = P("U4.7")
    g = P("C509.2")
    wire("GND", "F.Cu", 0.25, [g, (g[0] + (ep4[1] - 0.35 - g[1]), ep4[1] - 0.35), (ep4[0], ep4[1] - 0.35)], "C509 to the via of the U4 exposed pad", "GND")
    for dy in (-0.35, 0.35):
        via("GND", ep4[0], ep4[1] + dy, "power", "U4 exposed pad", "GND", in_pad=True)
    wire("GND", "B.Cu", 0.30, [P("U4.2"), ep4], "U4 GND pin into the exposed pad", "GND")
    # DAC
    for n in ("1", "5"):
        p = P("U5." + n)
        wire("GND", "F.Cu", 0.25, [p, (P("U5.9")[0], p[1])], "U5 GND pin %s into the exposed pad" % n, "GND")
    e5 = P("U5.9")
    for dy in (-0.40, 0.40):
        via("GND", e5[0], e5[1] + dy, "power", "U5 exposed pad", "GND", in_pad=True)
    for ref in ("C332", "C333", "C511"):
        g = P(ref + ".2")
        wire("GND", "F.Cu", 0.25, [g, (g[0], g[1] + 0.72)], "%s to its GND via" % ref, "GND")
        via("GND", g[0], g[1] + 0.72, "power", "GND of %s" % ref, "GND")
    cp, c33 = P("U5.4"), P("C333.1")
    wire("DAC_CAP", "F.Cu", 0.15, [cp, (c33[0], cp[1] + (c33[0] - cp[0])), c33], "U5 CAP to C333", "SLOW")
    # I2C
    scl = P("U5.2")
    xs = finger_x(253)
    x1 = 65.60
    x2 = 66.10
    wire("SCL", "F.Cu", 0.15, [(xs, 2.60), (xs, 3.60), (x1, 3.60 + (xs - x1)), (x1, 7.30), (x2, 7.30 + (x2 - x1)), (x2, scl[1] - (x2 - 65.775)), (65.775, scl[1]), scl],
         "SCL from finger 253, west of the tooling hole, into U5 pin 2 from the east", "SLOW")
    sda = P("U5.3")
    xa = finger_x(254)
    xv = 66.60
    wire("SDA", "B.Cu", 0.15, [(xa, 2.60), (xa, 3.90), (66.15, 3.90 + (xa - 66.15)), (66.15, 7.50), (xv, 7.50 + (xv - 66.15)), (xv, sda[1])],
         "SDA from finger 254 (back) to its via beside U5 pin 3", "SLOW")
    via("SDA", xv, sda[1], "signal", "SDA to U5 pin 3 on F.Cu", "SLOW")
    wire("SDA", "F.Cu", 0.15, [(xv, sda[1]), sda], "SDA via into U5 pin 3", "SLOW")

    # ================================================================ B.Cu: V1P2 taps of U4 and R504
    in4 = P("U4.3")
    c81, c82 = P("C508.1"), P("C508.2")
    assert abs(c81[0] - X_U4_TAP) < 1e-9 and abs(c81[1] - in4[1]) < 1e-9
    for dy in (-0.225, 0.225):
        via("V1P2", X_U4_TAP, Y_ARM + dy, "power", "1.2 V tap of U4 on the arm (0.12 A each)", "V1P2")
    wire("V1P2", "B.Cu", 0.40, [(X_U4_TAP, Y_ARM - 0.225), c81, in4], "tap vias north to C508 and into U4 IN", "V1P2")
    wire("GND", "B.Cu", 0.30, [c82, (c82[0] - 0.75, c82[1])], "C508 to its GND via", "GND")
    via("GND", c82[0] - 0.75, c82[1], "power", "GND of C508", "GND")
    r41, r42, en3 = P("R504.1"), P("R504.2"), P("U3.3")
    assert abs(r42[0] - en3[0]) < 1e-9
    xv4 = r41[0] - 0.74
    via("V1P2", xv4, Y_ARM, "signal", "1.2 V tap of R504 (enable of U3)", "V1P2")
    wire("V1P2", "B.Cu", 0.25, [(xv4, Y_ARM), (xv4 + (Y_ARM - r41[1]), r41[1]), r41], "tap via to R504", "V1P2")
    wire("PWR_EN", "B.Cu", 0.15, [r42, en3], "R504 straight into U3 EN", "SLOW")

    # ================================================================ B.Cu: VDD1 from U3 to U1, F.Cu: U2 EN, TP1, the north link
    out3 = P("U3.5")
    c71, r91 = P("C507.1"), P("R509.1")
    c231, c211 = P("C423.1"), P("C421.1")
    x_rail_e = 27.50
    x_v1 = 35.60
    assert abs(out3[1] - Y_VDD1_LANE) < 1e-9 and abs(c71[1] - Y_VDD1_LANE) < 1e-9
    wire("VDD1", "B.Cu", 0.30, [out3, c71, (r91[0], Y_VDD1_LANE), (x_v1, Y_VDD1_LANE), (x_rail_e, Y_VDD1_LANE), (x_rail_e, c231[1]), c231],
         "VDD1 lane: U3 OUT west past C507 / R509 to the east rail and C423 (ball W1)", "VDD1")
    wire("VDD1", "B.Cu", 0.30, [(x_rail_e, c231[1]), (x_rail_e, c211[1] + (x_rail_e - c211[0])), c211],
         "VDD1 east rail: C423 (W1) south to C421 (C1), outside the VDDQ rail", "VDD1")
    via("VDD1", x_v1, Y_VDD1_LANE, "signal", "VDD1 to the front: U2 EN and the test pad TP1", "VDD1")
    en2, tp1 = P("U2.5"), P("TP1.1")
    xd1 = 35.98
    wire("VDD1", "F.Cu", 0.25, [(x_v1, Y_VDD1_LANE), (xd1, Y_VDD1_LANE - (xd1 - x_v1)), (xd1, en2[1] + 0.17), (xd1 - 0.17, en2[1]), en2],
         "VDD1 via south into U2 EN", "VDD1")
    wire("VDD1", "F.Cu", 0.25, [(x_v1, Y_VDD1_LANE), (x_v1 - (tp1[1] - Y_VDD1_LANE), tp1[1]), tp1], "to the test pad TP1", "VDD1")
    w1, w15 = ball("W1"), ball("W15")
    xe1, xw1, yn1 = 27.45, 14.05, 27.10
    wire("VDD1", "F.Cu", 0.25, [w1, (xe1 - 0.35, w1[1]), (xe1, w1[1] + 0.35), (xe1, yn1 - 0.25), (xe1 - 0.25, yn1), (xw1 + 0.25, yn1), (xw1, yn1 - 0.25),
                                (xw1, w15[1]), w15],
         "VDD1 north link on F.Cu: ball W1 around the north of U1 to ball W15", "VDD1")
    # West rail: on F.Cu as well, from the corner of the north link down the west side of U1 to ball C15.  The supply then changes layer ONCE, at
    # ball W1 (B.Cu lane and east rail -> F.Cu north link and west rail), and never comes back: the balls W15 / C15 are F.Cu pads, and their
    # capacitors C424 / C422 sit on the via-in-pads of those balls on the back.  (Until the final fix pass the west rail was a B.Cu track between
    # the two capacitors, so that the feed of C15 ran B.Cu - F.Cu - B.Cu; F.Cu west of U1 was taken by the escapes of the unused byte then.)
    c15 = ball("C15")
    assert abs(c15[0] - w15[0]) < 1e-9
    wire("VDD1", "F.Cu", 0.25, [(xw1, w15[1]), (xw1, c15[1] + 0.25), (xw1 + 0.25, c15[1]), c15],
         "VDD1 west rail on F.Cu: from the north link south to ball C15", "VDD1")

    # ================================================================ B.Cu: VDDQ from U4 to U1, test pad, R332
    out4, fb4 = P("U4.4"), P("U4.5")
    x_north = 16.725
    y_nrail = 26.65
    aa3, aa13 = ball("AA3"), ball("AA13")
    wire("VDDQ", "B.Cu", 0.35, [out4, (out4[0], out4[1] + 0.6)], "U4 OUT, neck", "VDDQ")
    wire("VDDQ", "B.Cu", W_VDDQ_LANE, [(out4[0], out4[1] + 0.6), (out4[0], Y_VDDQ_LANE - 0.5), (out4[0] - 0.5, Y_VDDQ_LANE), (27.90, Y_VDDQ_LANE),
                                       (27.90 - (Y_VDDQ_LANE - y_nrail), y_nrail)],
         "VDDQ lane: U4 OUT north and west to the north-east corner of U1", "VDDQ")
    wire("VDDQ", "B.Cu", 0.50, [(27.90 - (Y_VDDQ_LANE - y_nrail), y_nrail), (aa3[0], y_nrail), (x_north, y_nrail)],
         "VDDQ north rail above row AA", "VDDQ")
    wire("VDDQ", "B.Cu", 0.30, [(aa3[0], y_nrail), aa3], "north rail to AA3", "VDDQ")
    wire("VDDQ", "B.Cu", 0.30, [(x_north, y_nrail), aa13], "north rail to AA13", "VDDQ")
    r61, r62 = P("R506.1"), P("R506.2")
    c101, c102 = P("C510.1"), P("C510.2")
    r81, r82 = P("R508.1"), P("R508.2")
    r71, r72 = P("R507.1"), P("R507.2")
    x_col = r81[0]
    assert abs(r61[1] - out4[1]) < 1e-9 and abs(c101[1] - out4[1]) < 1e-9
    wire("VDDQ", "B.Cu", 0.30, [out4, r61, c101, (x_col, out4[1]), r81, (x_col, Y_PG)], "VDDQ run east: R506, C510, R508, to the via of the test pad", "VDDQ")
    via("VDDQ", x_col, Y_PG, "signal", "VDDQ to the front: test pad TP3 and R332", "VDDQ")
    tp3, r332 = P("TP3.1"), P("R332.1")
    assert abs(tp3[0] - x_col) < 1e-9
    wire("VDDQ", "F.Cu", 0.25, [(x_col, Y_PG), tp3], "VDDQ via to the test pad TP3", "VDDQ")
    wire("VDDQ", "F.Cu", 0.25, [tp3, (r332[0] - 0.5, tp3[1]), (r332[0], tp3[1] - 0.5), r332], "TP3 east and south to R332 (default divider of VREF_RD)", "VDDQ")
    wire("VDDQ_FB", "B.Cu", 0.15, [fb4, (r62[0] - (fb4[1] - r62[1]), fb4[1]), r62, r71], "FB: U4 pin 5, R506, R507", "SLOW")
    wire("GND", "B.Cu", 0.25, [r72, (r72[0] + 0.75, r72[1])], "R507 to its GND via", "GND")
    via("GND", r72[0] + 0.75, r72[1], "power", "GND of R507 and R508", "GND")
    xg7 = r72[0] + 0.75
    wire("GND", "B.Cu", 0.25, [r82, (r82[0], r72[1] + (r82[0] - xg7)), (xg7, r72[1])], "R508 to the GND via of R507", "GND")
    wire("GND", "B.Cu", 0.30, [c102, (c102[0] + 0.75, c102[1])], "C510 to its GND via", "GND")
    via("GND", c102[0] + 0.75, c102[1], "power", "GND of C510", "GND")
    tp5 = P("TP5.1")
    wire("GND", "F.Cu", 0.30, [tp5, (tp5[0], tp5[1] - 0.95)], "test pad TP5 to its GND via", "GND")
    via("GND", tp5[0], tp5[1] - 0.95, "power", "GND of the test pad TP5", "GND")

    # ================================================================ U1 on B.Cu: rails, links, pours
    x_er, x_wr = 26.90, 14.55
    g1, r1b, t1 = ball("G1"), ball("R1"), ball("T1")
    wire("VDDQ", "B.Cu", 0.40, [g1, (x_er, g1[1]), (x_er, r1b[1]), (x_er, t1[1]), t1], "VDDQ east rail outside column 1: rows F / G to rows R / T", "VDDQ")
    wire("VDDQ", "B.Cu", 0.30, [(x_er, r1b[1]), r1b], "east rail to R1", "VDDQ")
    g15, r15, t15 = ball("G15"), ball("R15"), ball("T15")
    wire("VDDQ", "B.Cu", 0.40, [g15, (x_wr, g15[1]), (x_wr, r15[1]), (x_wr, t15[1]), t15], "VDDQ west rail outside column 15", "VDDQ")
    wire("VDDQ", "B.Cu", 0.30, [(x_wr, r15[1]), r15], "west rail to R15", "VDDQ")
    for a, b_, c_ in (("H2", "J2", "K2"), ("H14", "J14", "K14")):
        wire("VDD2H", "B.Cu", 0.20, [ball(a), ball(b_), ball(c_)], "%s - %s - %s: rows H / J to the island row K" % (a, b_, c_), "VDD2H")
    for a, b_ in (("J5", "K5"), ("J11", "K11")):
        wire("VDD2H", "B.Cu", 0.20, [ball(a), ball(b_)], "%s - %s" % (a, b_), "VDD2H")
    f6, k6 = ball("F6"), ball("K6")
    # F6 - K6: the ONLY way from the centre pour (rows A .. F, 18 balls) to the island row K.  Every channel of row H between columns 4 and 14
    # has a command via on one side (H5 CA1, H7 CS0, H9 CK_P, H11 CA3, H13 CA5); the link takes the gap H5 / H6 and is necked to 0.12 mm
    # there (0.14 mm of gap to the stub end of CA1 and to the GND via of H6 over 0.4 mm); 0.30 mm wide where columns 5 / 7 leave room
    wire("VDD2H", "B.Cu", W_LINK_WIDE, [f6, (f6[0], f6[1] + 0.8)], "F6 - K6 link, south part (past the free position G6)", "VDD2H")
    wire("VDD2H", "B.Cu", W_LINK_NECK, [(f6[0], f6[1] + 0.8), (f6[0] + 0.4, f6[1] + 1.2), (f6[0] + 0.4, f6[1] + 1.6), (f6[0], f6[1] + 2.0)],
         "F6 - K6 link, neck past the GND via of H6 in the gap to H5 (CA1)", "VDD2H")
    wire("VDD2H", "B.Cu", W_LINK_WIDE, [(f6[0], f6[1] + 2.0), k6], "F6 - K6 link, north part (past the free position J6)", "VDD2H")
    d4, e5 = ball("D4"), ball("E5")
    wire("VDDQ", "F.Cu", 0.20, [d4, (d4[0] - 0.7, d4[1] + 0.7), e5], "D4 - E5 on F.Cu: the four VDDQ balls south-west of the WCK diagonal (A3, B2, C3, D4) to the rest of the rail", "VDDQ")
    # Pours (B.Cu, power only).  The lower three end at y = 15.65: no pour edge beside the B.Cu end of a command via-in-pad (row G: CA0, CA2,
    # CA4, CA6 at y = 16.20, pad edge 16.00); only the two tabs over the VDDQ balls G1 / G2 and G14 / G15 reach row G, 1.0 mm from CA0 / CA6.
    # The east flank is drawn as two outlines with straight edges on both sides of the WCK pair instead of one rectangle that the clearance
    # rule cuts into pieces: the enclave (A3, B2, C3, D4) has one edge parallel to the pair, 0.30 mm from WCK_N, and the main part stays
    # north of the pair's exit (nearest edge 0.39 mm from WCK_P, not parallel to it)
    xl = (14.83, 18.58)
    xr = (22.87, 26.62)
    yt = Y_POUR_TOP_LOW
    x_tab_w, x_tab_e = 16.50, 25.05      # the tabs end 0.10 mm from the GND lands of C414 / C416 and are chamfered toward row H (CA5 at H13: 0.40 mm)
    wn = [tuple(q) for q in wck_n_b_cu]                    # WCK_N on B.Cu: ball D5, 45-degree stub, y = const, the diagonal
    assert abs((wn[3][0] - wn[2][0]) + (wn[3][1] - wn[2][1])) < 1e-6, wn
    s_edge = wn[2][0] + wn[2][1] - (0.05 + POUR_TO_PAIR) * math.sqrt(2)      # x + y of the enclave's edge: 0.30 mm from the copper of WCK_N
    y_lobe = wn[1][1] - 0.05 - POUR_TO_PAIR - 0.05                             # top of the lobe at D4: 0.35 mm from WCK_N's run between rows D and E
    x_e5 = (wn[0][0] - wn[0][1]) - (0.05 + POUR_TO_PAIR) * math.sqrt(2) + 14.62   # the lobe at E5: its chamfer runs 0.30 mm from the 45-degree stub of WCK_N
    pour("VDDQ", "B.Cu", "VDDQ lower east, enclave (A3, B2, C3, D4)",
         [(24.33, 11.72), (25.85, 11.72), (25.85, s_edge - 25.85), (s_edge - y_lobe, y_lobe), (23.72, y_lobe), (23.72, 13.90), (23.88, 13.90),
          (24.33, 13.45)], priority=3)
    pour("VDDQ", "B.Cu", "VDDQ lower east (rows E .. G)",
         [(xr[0], 14.62), (x_e5, 14.62), (23.50, 14.62 + (23.50 - x_e5)), (23.50, 15.15), (25.05, 15.15), (25.05, 14.60), (26.02, 14.60), (26.02, 15.15), (xr[1], 15.15),
          (xr[1], 16.48), (x_tab_e + 0.20, 16.48), (x_tab_e + 0.20, 16.15), (x_tab_e, 15.95), (x_tab_e, yt), (xr[0], yt)], priority=3)
    pour("VDDQ", "B.Cu", "VDDQ upper east (rows R .. AA)", [(xr[0], 21.52), (xr[1], 21.52), (xr[1], y_nrail), (xr[0], y_nrail)], priority=3)
    pour("VDDQ", "B.Cu", "VDDQ lower west (rows A .. G)",
         [(xl[0], 11.72), (xl[1], 11.72), (xl[1], yt), (x_tab_w, yt), (x_tab_w, 15.95), (x_tab_w - 0.20, 16.15), (x_tab_w - 0.20, 16.48), (xl[0], 16.48)], priority=3)
    pour("VDDQ", "B.Cu", "VDDQ upper west (rows R .. AA)", [(xl[0], 21.52), (xl[1], 21.52), (xl[1], y_nrail), (xl[0], y_nrail)], priority=3)
    pour("VDD2H", "B.Cu", "VDD2H centre (rows A .. F, columns 6 .. 10)", [(18.88, 11.72), (22.57, 11.72), (22.57, yt), (18.88, yt)], priority=3)

    # ================================================================ RESET_n: F.Cu stub, one via, B.Cu to the parts at finger 232
    h1 = ball("H1")
    xfr = finger_x(RESET_FINGER)
    r21, r22 = P("R402.1"), P("R402.2")
    r31_, r32_ = P("R403.1"), P("R403.2")
    assert abs(r21[0] - xfr) < 1e-9
    wire("RESET_N", "F.Cu", 0.15, [h1, (h1[0] + (Y_RESET - h1[1]), Y_RESET), RESET_VIA], "RESET_n: ball H1 to its via east of the two rails of U1", "RESET")
    via("RESET_N", RESET_VIA[0], RESET_VIA[1], "signal", "RESET_n: the one layer change, beside U1", "RESET")
    wire("RESET_N", "B.Cu", 0.15, [RESET_VIA, (X_RESET_DROP - 0.5, Y_RESET), (X_RESET_DROP, Y_RESET - 0.5), (X_RESET_DROP, r31_[1] + (r31_[0] - X_RESET_DROP)), r31_],
         "RESET_n on B.Cu: east north of everything of the data side, down east of the DAC network to R403", "RESET")
    wire("RESET_N", "B.Cu", 0.15, [r22, (r22[0], r31_[1] - (r31_[0] - r22[0])), r31_], "R402 - R403", "RESET")
    wire("RESET_N_F", "B.Cu", 0.15, [(xfr, 2.60), (xfr, 3.45)], "RESET_n finger stub, through the finger-via row", "RESET")
    wire("RESET_N_F", "B.Cu", 0.20, [(xfr, 3.45), r21], "RESET_n finger stub", "RESET")
    fv = finger_via(234)
    assert abs(fv[0] - r32_[0]) < 1e-9
    wire("GND", "B.Cu", 0.25, [r32_, fv], "R403 straight down the axis of the VSS finger 234 to that pair's finger via", "GND")

    # ================================================================ plan-view crossings of a high-speed wire by a wire of this stage
    X = OrderedDict([
        ("V1P2 F.Cu", OrderedDict(
            crosses=["DQ0", "DQ1", "DQ2", "DQ3", "DQ4", "DQ5", "DQ6", "DQ7", "RDQS_N", "RDQS_P"], their_layer="In3.Cu", between="In1 and In2, solid GND",
            why="the 1.2 V strip: the VDD fingers lie south of the In3 read bundle and the regulators north of it; any feed between the comb and the read "
                "end crosses the eight read lines and the RDQS pair once (the strip is 1.5 mm wide: 2.1 mm of each read line on its 45-degree ramp, "
                "1.5 mm of each RDQS leg at 90 degrees)")),
        ("VDD1 B.Cu", OrderedDict(
            crosses=["DQ0", "DQ1", "DQ2", "DQ3", "DQ6", "DQ7"], their_layer="F.Cu", between="In1, In2 and In4, solid GND (east of the via field)",
            why="the VDD1 ball C1 lies in the pocket between the six F.Cu lines that leave U1 eastward (north of it) and the DQ4 / DQ5 lines, the WCK pair "
                "and the RDQS pair (south and east of it): the rail from C423 (W1) to C421 (C1) crosses the six lines once, 0.5 mm east of the land "
                "field, four at 90 and two at 45 degrees")),
        ("VDDQ B.Cu", OrderedDict(
            crosses=["DQ7"], their_layer="F.Cu", between="In1, In2 and In4 (perforated by the via field)",
            why="the VDDQ balls G1 / G2 lie between the necks of DQ6 and DQ7: the east rail that joins the lower flank to the upper one crosses the DQ7 "
                "neck once, at 90 degrees")),
        ("VDDQ F.Cu", OrderedDict(
            crosses=["CA0", "WCK_N"], their_layer="In3.Cu / B.Cu", between="In1 and In2 (CA0); In1, In2 and In4 (WCK_N), perforated by the via field",
            why="the strap D4 - E5: the WCK pair on B.Cu cuts the four VDDQ balls A3, B2, C3, D4 off from the rest of the rail on the back; the one F.Cu "
                "link that joins them crosses the WCK_N leg (which starts on D5 between the two balls) and CA0 (which encloses ball D5: the command "
                "bundle is crossed here by WCK_N already)")),
    ])
    # ================================================================ DC budget: the current each piece of supply copper carries (worst case)
    DC = [
        OrderedDict(net="V1P2", layer="F.Cu", width=W_STRIP, current_a=I_RAIL["V1P2"], what="strip: U2 + U4"),
        OrderedDict(net="V1P2", layer="F.Cu", width=W_ARM, current_a=I_RAIL["V1P2_to_U2"], what="arm to U2 (its first 1.4 mm also carries U4)"),
        OrderedDict(net="V1P2", layer="B.Cu", width=0.40, current_a=I_RAIL["V1P2_to_U4"], what="tap of U4"),
        OrderedDict(net="VDD2H", layer="F.Cu", width=0.80, current_a=I_RAIL["VDD2H"], what="U2 output strap to its four vias into the In4 island"),
        OrderedDict(net="VDD2H", layer="B.Cu", width=W_LINK_WIDE, current_a=round(I_RAIL["VDD2H"] * 18 / 77, 4), what="link F6 - K6, wide parts: the 18 balls of rows A .. F"),
        OrderedDict(net="VDD2H", layer="B.Cu", width=W_LINK_NECK, current_a=round(I_RAIL["VDD2H"] * 18 / 77, 4), what="link F6 - K6, neck past H6"),
        OrderedDict(net="VDDQ", layer="B.Cu", width=0.35, current_a=I_RAIL["VDDQ"], what="U4 OUT neck"),
        OrderedDict(net="VDDQ", layer="B.Cu", width=W_VDDQ_LANE, current_a=I_RAIL["VDDQ"], what="lane U4 to U1"),
        OrderedDict(net="VDDQ", layer="B.Cu", width=0.50, current_a=round(I_RAIL["VDDQ"] / 2, 4), what="north rail: the 20 balls of the west flanks"),
        OrderedDict(net="VDDQ", layer="B.Cu", width=0.40, current_a=round(I_RAIL["VDDQ"] / 4, 4), what="east / west rail: the 10 balls of one lower flank each (both rails are in the length)"),
        OrderedDict(net="V3P3", layer="F.Cu", width=0.30, current_a=I_RAIL["V3P3"], what="finger 255 to U2 BIAS, taps of U3 / U4"),
        OrderedDict(net="VDD1", layer="B.Cu", width=0.30, current_a=I_RAIL["VDD1"], what="lane and rails"),
        OrderedDict(net="VDD1", layer="F.Cu", width=0.25, current_a=round(I_RAIL["VDD1"] / 2, 4), what="north link and west rail to W15 / C15 (F.Cu), U2 EN, TP1"),
    ]
    return OrderedDict(wires=W, vias=V, pours=Z, crossings_expected=X, dc_budget=DC)

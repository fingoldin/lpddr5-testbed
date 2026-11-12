#!/usr/bin/env python3
"""The data side of the card as ONE plan: write group, read group, WCK pair, RDQS pair, reference networks.
Pure Python (no pcbnew).  scripts/build_netlist.py calls build() and writes the result into design/netlist.json
(floorplan.data, floorplan.placements, floorplan.vias, floorplan.finger_stubs, groups WR / WR_WCK / RD_P / RD_RDQS /
RD_N); scripts/pcb/s30_data.py draws it; scripts/check_netlist.py and gate 7 of scripts/pcb/check_pcb.py re-derive it.

Topology ("T at the divider", comb form; DESIGN.md section 14):

    ball - 0.09 neck - F.Cu line (0.15) on its level - one parallel 45-degree fan - tooth down the column axis
                                                                                        |
       read finger - finger track (own face) - read via - In3 line  ------------  NODE VIA = the T   (y = 6.00)
                                              (on its level)  (lane, level, ramp)       |  |
                                                                 240R in line on the finger's face   150R back to back
                                                                 (lands y 4.68 / 5.32)               on the other face
                                                                          |                               |
                                                                     write finger                 GND via south of the row

  DQ    the lowest F.Cu line takes the westmost column (a comb on one layer is planar for one order only).  The read
        branch leaves the node via on In3: one 45-degree run into the lane 0.40 mm east of the column, north to its
        level (6.00 + 0.55 per column, west column on top), east in an ordered bundle at 0.55 mm pitch, one 45-degree
        ramp of the whole bundle, east to a via on its own level above its read finger, finger track straight down.
        READ END WITHOUT CROSSINGS (variant "read0"): the read fingers are taken in the order of the bundle (the
        lowest In3 line ends first, on the westmost finger; every higher line passes north of the vias below it and
        ends further east), so no finger track crosses an In3 line.  The read vias stand on a regular staircase
        (0.55 mm per line).  Neither group is delay-matched on the card in this form: both are planned as they fall
        and closed per bit in the FPGA (ODELAY for the writes, IDELAY for the reads), design/gateware_deskew.json.
  WCK   B.Cu pair on the diagonal D3 - C2 - B1, the lowest lane, into two columns (0.20 mm links like the DQ columns,
        shunt north of the node, the two shunt GND vias mirror images about the pair).
  RDQS  In3 all the way, like a read DQ line: out of U1, east between the two lower and the six upper F.Cu lines, ONE
        45-degree run north-east across the six diagonals of the fan (90 degrees to them), east above every read via,
        ONE 45-degree run down to the axis of the host pair DQS6 and south to one via pair just above fingers 219 / 221
        (y = RDQS_VIA_Y, two GND return vias beside it), then 5.8 mm of F.Cu straight down into the fingers, EAST of
        every read finger: the drop crosses no read line.  One skew bump on RDQS_P on In3 beside U1; the two loads low
        beside the drop, GND on the VSS finger vias.  Not matched to the read group: its offset is in the deskew table.
  VREF  two rows of 0201 per N finger (39R on the N axis, 100 nF on the neighbouring VSS axis, GND on that finger),
        bus along the north ends.

Coordinates: card frame, mm (origin lower-left corner seen from the front, y up).
"""
import itertools
import math
import os
from collections import OrderedDict

SQ2 = math.sqrt(2.0)
T225 = math.tan(math.radians(22.5))
FINGER_TOP = 2.70
FINGER_START = 2.60          # every finger track starts 0.10 mm inside the finger pad
PAD = 0.32                   # 0201: pad centre to part centre
W_NECK, W_LINE, W_STUB_NECK, W_STUB, W_PAIR, W_VREF, W_GND, W_LINK_WCK, W_IN3 = 0.09, 0.15, 0.15, 0.20, 0.10, 0.15, 0.25, 0.20, 0.15

# ------------------------------------------------------------------------------------------------ pin map
LEVELS = ["DQ4", "DQ5", "DQ1", "DQ0", "DQ3", "DQ2", "DQ6", "DQ7"]      # F.Cu levels bottom -> top = columns west -> east
BALL = {"DQ4": "B5", "DQ5": "C6", "DQ1": "C2", "DQ0": "D1", "DQ3": "D3", "DQ2": "E2", "DQ6": "E6", "DQ7": "F5"}
WRITE_FINGER = OrderedDict([("DQ4", 143), ("DQ5", 144), ("DQ1", 145), ("DQ0", 150), ("DQ3", 151), ("DQ2", 156), ("DQ6", 157), ("DQ7", 161)])
# RDQS is on the host pair DQS6, fingers 219 / 221, east of every read finger (a comparison build with the pair on 198 / 200, between the
# read fingers, counted 8 crossings more; its code is gone)
# read: P finger (N finger).  The complete P / N pairs of bank 66 that a read DQ may take (design/ref/zcu104_sodimm_pinmap.csv):
# not the four host-coupled strobe pairs (177 / 179, 198 / 200, 219 / 221, 240 / 242), not 169 / 173 (their F.Cu track would
# stand in the corridor of the 1.2 V feed), and nothing east of the RDQS drop (225, 229, 233 ...: the In3 line would cross
# the pair).  Eight are left, and the order without a crossing is unique: the In3 lines bottom -> top take the fingers
# west -> east (choose_read_end() derives it and build() asserts that READ_FINGER is that order)
READ_CANDIDATES = OrderedDict([(182, 183), (186, 187), (190, 191), (194, 195), (204, 203), (208, 207), (212, 211), (215, 216)])
READ_FINGER = OrderedDict([("DQ4", (215, 216)), ("DQ5", (212, 211)), ("DQ1", (208, 207)), ("DQ0", (204, 203)),
                           ("DQ3", (194, 195)), ("DQ2", (190, 191)), ("DQ6", (186, 187)), ("DQ7", (182, 183))])
WCK_FINGER = OrderedDict([("WCK_N", 137), ("WCK_P", 139)])               # host pair CK0, inverted (WCK_c on the P pin AF18)
WCK_BALL = {"WCK_P": "E4", "WCK_N": "D5"}
RDQS_FINGER = OrderedDict([("RDQS_P", 219), ("RDQS_N", 221)])            # host pair DQS6 (T1U, QBC), inverted (RDQS_t on the N pin AL8)
RDQS_BALL = {"RDQS_P": "B3", "RDQS_N": "C4"}
RESET_FINGER = 232                                                        # bank 66 T0L (AP11), static LVCMOS12 output; back side. Routed by the power stage (scripts/power_side.py)
RESET_BALL = "H1"

# ------------------------------------------------------------------------------------------------ BGA corner (fixed)
# level of every F.Cu line east of U1; necks 0.09 mm from the ball to the edge of the land field (one per row channel,
# none over a pair, none on a command channel); DQ4 / DQ5 turn east once on a 45-degree run ("lead")
Y_LEVEL = OrderedDict([("DQ4", 10.75), ("DQ5", 11.30), ("DQ1", 13.60), ("DQ0", 14.15), ("DQ3", 14.70), ("DQ2", 15.25), ("DQ6", 15.80), ("DQ7", 16.35)])
NECK = {
    "DQ4": [(23.125, 12.7), (23.475, 12.35), (24.175, 12.35), (24.325, 12.2), (24.325, 11.6)],
    "DQ5": [(22.325, 13.4), (22.675, 13.75), (24.975, 13.75), (25.125, 13.6), (25.125, 11.695)],
    "DQ1": [(25.525, 13.4), (25.875, 13.75), (26.55, 13.75), (26.7, 13.6), (26.9, 13.6)],
    "DQ0": [(26.325, 14.1), (26.6, 14.1), (26.65, 14.15), (26.9, 14.15)],
    "DQ3": [(24.725, 14.1), (25.075, 14.45), (26.55, 14.45), (26.8, 14.7), (26.9, 14.7)],
    "DQ2": [(25.525, 14.8), (25.875, 15.15), (26.6, 15.15), (26.7, 15.25), (26.9, 15.25)],
    "DQ6": [(22.325, 14.8), (22.675, 15.15), (24.975, 15.15), (25.125, 15.3), (25.125, 15.7), (25.275, 15.85), (26.6, 15.85), (26.65, 15.8), (26.9, 15.8)],
    "DQ7": [(23.125, 15.5), (23.475, 15.85), (24.175, 15.85), (24.325, 16.0), (24.325, 16.4), (24.475, 16.55), (26.6, 16.55), (26.8, 16.35), (26.9, 16.35)],
}
LEAD = {"DQ4": [(24.325, 11.6), (25.175, 10.75)], "DQ5": [(25.125, 11.695), (25.52, 11.3)]}
# WCK: B.Cu legs from the via-in-pads of D5 / E4 to the node vias of the two columns (the asymmetric end replaces a skew bump)
WCK_COL = OrderedDict([("WCK_N", 34.925), ("WCK_P", 36.125)])
WCK_LEG = {
    "WCK_N": [(23.125, 14.1), (23.475, 14.45), (24.28, 14.45), (28.78, 9.95), (34.625, 9.95), (34.925, 9.65), (34.925, 6.0)],
    "WCK_P": [(23.925, 14.8), (24.2128, 14.8), (28.8628, 10.15), (34.7078, 10.15), (35.125, 9.7328), (35.125, 7.0), (36.125, 6.0)],
}
# GND vias of the two shunts: mirror images about the axis of the B.Cu pair (x = 35.025), 0.55 mm from it on either side
WCK_GND_VIA = {"WCK_N": (34.475, 7.90), "WCK_P": (35.575, 7.90)}
WCK_GND_LINK = {"WCK_N": [(34.925, 7.32), (34.925, 7.45), (34.475, 7.9)], "WCK_P": [(36.125, 7.32), (36.125, 7.35), (35.575, 7.9)]}
Y_WCK_SHUNT = 7.00
# RDQS: In3 legs inside the land field of U1 (row channel B|C, 0.205 mm from the hole walls of C3, B2, C1), up to x = 27.6
RDQS_HEAD = {
    "RDQS_P": [(24.725, 12.7), (24.895, 12.87), (25.0828, 12.87), (25.2428, 13.03), (25.8172, 13.03), (25.9772, 12.87), (26.6672, 12.87), (26.9372, 12.6), (27.6, 12.6)],
    "RDQS_N": [(23.925, 13.4), (24.255, 13.07), (25.0, 13.07), (25.16, 13.23), (25.9, 13.23), (26.06, 13.07), (26.75, 13.07), (27.02, 12.8), (27.6, 12.8)],
}
Y_RDQS_LOW = 12.70           # pair axis east of U1 (In3): between the two lower F.Cu lines (DQ4, DQ5 at y 10.75 / 11.30) and the six upper ones
                             # (from y = 13.60); 0.25 mm north of the middle, so that the skew bump (south side) ends 0.40 mm from the DQ5 line in plan
X_RDQS_NE = 36.50            # here the pair leaves that axis on ONE 45-degree run to the north-east: it crosses the six diagonals of
                             # the F.Cu fan at 90 degrees (the fan runs south-east) and nothing else
Y_RDQS_HIGH = 15.50          # pair axis east of the fan: north of every read via and its return via
X_RDQS_DOWN = 58.175         # the pair comes down between fingers 219 / 221 (both on the front), 1.075 mm east of the last read track
RDQS_OUTER = "F.Cu"          # outer layer of the pair below its via pair = the face of its fingers
# corner of the drop: ONE 45-degree run of 3.0 mm (4.24 mm of pair) from the high run down to the drop: it passes 0.73 mm
# north-east of the pad of the last read via
RDQS_CHAMFER = 3.00
# The pair stays on In3 from its balls to a via pair just above its fingers and drops the last 5.8 mm on F.Cu, like the eight read DQ
# lines (In3, one via above the finger, a short outer-layer track).  User: "do  RDQS could stay on In3 until just above its fingers and
# drop a short way on F.Cu".  The two vias stand side by side across the drop, RDQS_VIA_DX on either side of the pair axis (pads
# 0.20 mm apart), at the lowest y a through via can take there: its B.Cu pad has to keep 0.90 mm (rule VREF_to_switching_nets) from the
# VREF_RD bus that passes under the drop on B.Cu at y = Y_BUS_B, i.e. y >= 7.30 + 0.075 + 0.90 + 0.20 = 8.475
RDQS_VIA_Y = 8.50
RDQS_VIA_DX = 0.30
# GND return vias of the layer change: mirror images about the pair axis, north of the via pair (the In3 side: the F.Cu pair below the
# vias has no foreign copper beside it), 0.58 mm from the via of their side.  x: the F.Cu pad keeps 0.30 mm from the read track of
# finger 215 (rule WR_DQ_F_Cu_to_foreign_via 0.27) and the hole 0.225 mm from the In3 leg (hole to copper 0.20; no pad on In3)
RDQS_RETURN_DX, RDQS_RETURN_DY = 0.475, 0.55
# skew bump on In3, on the leg that is the shorter one, 0.5 mm east of the land field of U1: where the skew arises
# (ball C4 lies one row beyond B3).  Legs 0.45 apart: gap 0.35 mm = 3.2 h; it points south, away from the other leg
RDQS_BUMP = OrderedDict(net="RDQS_P", layer="In3.Cu", x0=27.90, leg_pitch=0.45, mitre=0.15)
# the two 150 ohm loads: on the face of the pair, on the axes of the two VSS finger pairs beside the drop (217 / 218 and
# 222 / 223), in the row of the 39 ohm resistors; RDQS pad north on a 0.40 mm side link,
# GND pad south, straight down the axis into that pair's finger via
RDQS_LOAD_DX = 0.50
Y_RDQS_LOAD = 4.75
# the legs run coupled (0.10 / 0.10) straight into the fingers, which are 0.50 mm apart: each lies 0.15 mm off its finger axis
# and 0.025 mm of its width beyond the pad edge.  A step outward above the finger top would bring the leg within 0.75 mm of
# the read tracks of fingers 194 / 204 where they step past the VSS finger vias (pair rule 0.80)

# ------------------------------------------------------------------------------------------------ the comb
# column x.  0.8 mm pitch; DQ4 / DQ5 stand west of the key at 37.25 / 38.05 (0.15 mm east of the rehearsed plan: the return via
# at (36.70, 6.30) then stands 0.275 mm from both the WCK_P link (0.20 mm wide) and the DQ4 tooth)
COL = OrderedDict([("DQ4", 37.250), ("DQ5", 38.050), ("DQ1", 39.400), ("DQ0", 40.200), ("DQ3", 41.000), ("DQ2", 41.800), ("DQ6", 42.600), ("DQ7", 43.400)])
Y_SER, Y_NODE = 5.00, 6.00
Y_FAN_END = 10.40            # the six upper lines leave their level on one 45-degree fan that ends on this line
X_BUNDLE = 27.40             # every F.Cu line has a vertex here, 0.18 mm east of the U1 courtyard (x = 27.22): the track item that touches the
                             # courtyard then ends here, and the DRC rules that hold "outside U1" (WR_DQ_to_WR_DQ_F_Cu) apply to the whole bundle
CHAMFER = 0.30
CHAMFER_LOW = 0.35           # corner of the two lower lines (DQ4, DQ5): the DQ5 corner then runs 0.566 mm beside the DQ1 fan diagonal (gap 0.416)
STUB_LANE = {143: 0.175, 144: 0.175}       # x offset of the stub lane from the finger axis (0: straight up); the F.Cu lane of 143 and the
                                           # B.Cu lane of 144 stay 0.25 mm apart in plan (no parallel overlap through the board)
Y_STUB_WIDE = 3.45           # 0.15 mm from the finger through the row of the finger vias, 0.20 mm from here
STUB_PAD_RUN = {143: 0.33}   # straight run into the pad after the 45-degree run (0: the run ends on the pad).  Finger 143: its 45-degree run
                             # would lie over the B.Cu GND link of the DQ4 shunt, which leaves the same land in the same direction
Y_FINGER_VIA = 3.05
# GND vias of the shunts, south of the row, in the lane no finger stub uses; one per pair of columns
# (each stands 0.20 mm or more from the finger stubs on both faces: DQ1 / DQ0 at x 40.0, DQ6 / DQ7 at x 42.925)
SHUNT_GND_VIA = OrderedDict([("DQ4", (36.650, 4.05)), ("DQ5", (38.650, 4.68)), ("DQ1", (40.000, 4.05)), ("DQ0", (40.000, 4.05)),
                             ("DQ3", (41.600, 4.05)), ("DQ2", (41.600, 4.05)), ("DQ6", (42.925, 4.05)), ("DQ7", (42.925, 4.05))])
# GND return vias beside the node row where a through via fits (between the WCK_P and DQ4 columns, in the gap over the
# key, east of the DQ7 column).  The four inner columns have none: 0.8 mm pitch, tooth on the axis, riser in the lane.
NODE_RETURN_VIAS = [(36.700, 6.30, "DQ4, WCK_P"), (38.850, 6.30, "DQ5, DQ1"), (44.000, 5.30, "DQ7")]      # 0.275 mm from the DQ4 / DQ1 teeth on F.Cu,
                                                                                                         # 0.30 mm from the 0.20 mm series link of DQ7

# ------------------------------------------------------------------------------------------------ In3 read bundle
PITCH = 0.55
LANE = 0.40                  # riser lane: 0.40 mm east of the column axis (midway between two teeth, never under its own)
RAMP = 2.20                  # 45-degree ramp of the whole bundle: lowest level 6.00 -> 8.20, the lowest a read via can stand: its pad 0.90 mm
                             # north of the VREF_RD bus on F.Cu (rule VREF_to_switching_nets)
X_RAMP = 45.30               # first corner of the ramp of the bottom line
RAMP_END_TO_VIA_MIN = 0.50   # a raised line must still run east for this much before its via
READ_TRACK = {               # P finger: read finger track from the finger (y = 2.6) up to the foot of the straight run to the read via
    182: [(48.675, 2.6)],
    186: [(49.675, 2.6), (49.675, 2.7), (49.85, 2.875), (49.85, 3.45), (49.675, 3.625)],
    190: [(50.675, 2.6), (50.675, 2.7), (50.85, 2.875), (50.85, 3.45), (50.675, 3.625)],
    194: [(51.675, 2.6), (51.675, 2.7), (51.85, 2.875), (51.85, 3.45), (51.675, 3.625)],
    204: [(54.175, 2.6), (54.175, 2.7), (54.0, 2.875), (54.0, 3.45), (54.175, 3.625)],
    208: [(55.175, 2.6)],
}
# the VREF_RD bus changes side between the read tracks of fingers 212 (B.Cu) and 215 (F.Cu), which are 0.75 mm apart, and the RDQS
# pair comes down 1.25 mm east of finger 215: the track of 212 runs 0.235 mm WEST of its finger axis above the finger-via row and
# the track of 215 keeps the 0.175 mm step east that takes it past the finger via of 213 / 214 (0.825 mm from the RDQS leg)
READ_TRACK[212] = [(56.175, 2.6), (56.175, 3.45), (55.94, 3.685)]
READ_TRACK[215] = [(56.925, 2.6), (56.925, 2.7), (57.1, 2.875)]
READ_VIA_X = {f: p[-1][0] for f, p in READ_TRACK.items()}
# GND return via of every read via.  In the order without crossings the next line passes 0.55 mm north of every read via, so the return
# via cannot stand north of it: it stands 0.64 mm to the south-east (0.45, -0.45), one bearing for all, and to the south-west where the
# south-east spot is taken (the next finger track nearer than 1 mm, or the RDQS drop); a return via that then stands within 0.75 mm
# of the read via below as well serves both.  place_return_vias() takes the first bearing of this list that keeps every distance below
RETURN_BEARINGS = [(0.45, -0.45), (-0.45, -0.45), (0.0, 0.65)]
RETURN_TO_IN3_LINE_MIN = 0.375   # centre of a return via (0.20 hole, no In3 pad) to the centre line of a foreign In3 line: 0.20 mm copper to hole
RETURN_SHARE_MAX = 0.75          # two read vias share one return via if it stands within this of both
RETURN_TO_VIA_MIN = 0.55         # centre to centre, to a read via or another return via (0.15 mm between the pads)
RETURN_TO_TRACK_MIN = 0.45       # centre to the centre line of a read finger track on either face (through via): 0.15 mm pad to track
RETURN_TO_PAIR_MIN = 0.55        # centre to the centre line of an RDQS leg: 0.30 mm pad to leg (rules.netclasses.RD_RDQS.pair_to_read_via_min)
READ_PLAN_TOL = 1.0          # each read net against its own planned total, ps (the group is not matched on the card)
LEVEL_GRID = 0.05            # the raise of a read level is a multiple of this

# ------------------------------------------------------------------------------------------------ reference networks
Y_REF_R, Y_REF_C, Y_BUS, Y_BUS_B = 4.75, 6.15, 7.00, 7.30          # 39R row, 100 nF row, bus on F.Cu, bus on B.Cu
# bus via F.Cu - B.Cu: between the read tracks of fingers 212 (B.Cu) and 215 (F.Cu); 0.405 mm from the B.Cu bus to the track of
# 212 and from the F.Cu bus to the track of 215 (rule 0.40 inside the area vref_east_end)
BUS_VIA = (56.52, 7.30)
X_FEED = 59.70               # B.Cu feed to the via below TP4: 0.95 mm from the pad of the eastern via of the RDQS pair on both faces (rule 0.90)
Y_FEED_VIA = 8.60
N_STUB_BULGE = {211: 0.175}  # the N stub of finger 211 steps east in the finger-via row (finger via of 209 / 210 at x 55.675)

# RESET_n is not part of this plan any more: it is a slow net of the power stage (scripts/power_side.py): F.Cu from ball H1 to ONE via
# beside U1, then B.Cu all the way to its two parts on the back at finger 232

# reserved for the power stage: nothing of this plan may stand in it on F.Cu (the 1.2 V strip runs in it; RESET_n passes under it on B.Cu)
V1P2_CORRIDOR = OrderedDict(layer="F.Cu", x=[44.4, 47.9], y=[3.3, 19.3])


# ================================================================================================ helpers
def r4(v):
    return round(v + 0.0, 4)


def P4(pts):
    out = []
    for x, y in pts:
        q = [r4(x), r4(y)]
        if not out or q != out[-1]:
            out.append(q)
    return out


def plen(pts):
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:]))


def offset_polyline(pts, d):
    """Parallel of a 0/45/90 polyline at distance d to the LEFT of the direction of travel (mitred joins)."""
    out = []
    n = len(pts)
    dirs = []
    for a, b in zip(pts, pts[1:]):
        L = math.hypot(b[0] - a[0], b[1] - a[1])
        dirs.append(((b[0] - a[0]) / L, (b[1] - a[1]) / L))
    for i in range(n):
        if i == 0:
            u = dirs[0]
            out.append((pts[0][0] - u[1] * d, pts[0][1] + u[0] * d))
        elif i == n - 1:
            u = dirs[-1]
            out.append((pts[-1][0] - u[1] * d, pts[-1][1] + u[0] * d))
        else:
            u, v = dirs[i - 1], dirs[i]
            nu, nv = (-u[1], u[0]), (-v[1], v[0])
            den = 1 + nu[0] * nv[0] + nu[1] * nv[1]
            out.append((pts[i][0] + (nu[0] + nv[0]) * d / den, pts[i][1] + (nu[1] + nv[1]) * d / den))
    return out


class Model:
    """Delay model of the project: ps / mm per layer and width, barrels between mid-copper heights, the bend model
    (mitred vertices on every layer; coupling of antiparallel legs), finger top = y 2.70."""

    def __init__(self, td, bend, z, pair_nets):
        self.td, self.bend, self.z, self.pair_nets = td, bend, z, set(pair_nets)

    def ps_mm(self, layer, width, net):
        if layer == "In3.Cu":
            return self.td["In3"]
        if net in self.pair_nets and layer in ("B.Cu", "F.Cu") and width <= 0.105:      # edge-coupled microstrip pair (same on both faces)
            return self.td["pair"]
        tab = [(0.09, self.td["ms09"]), (0.15, self.td["ms15"]), (0.20, self.td["ms20"])]
        if width <= tab[0][0]:
            return tab[0][1]
        if width >= tab[-1][0]:
            return tab[-1][1]
        for (w0, v0), (w1, v1) in zip(tab, tab[1:]):
            if w0 <= width <= w1:
                return v0 + (v1 - v0) * (width - w0) / (w1 - w0)

    def barrel(self, la, lb):
        return abs(self.z[la] - self.z[lb])

    def chain(self, net, items):
        """items in signal order: ("t", layer, width, pts) | ("v", layer a, layer b) | ("r", mm) | ("finger",).
        Returns OrderedDict(ps, centre_ps, bend_ps, leg_ps, mm per layer, vias mm, vertices, turned_deg)."""
        centre = bend = leg = 0.0
        mm = OrderedDict()
        vias = []
        nv, turned = 0, 0.0
        prev = None                      # (layer, last direction, last point)
        segs = []                        # (layer, width, a, b) of every straight segment, for the leg coupling
        for it in items:
            if it[0] == "t":
                _, layer, width, pts = it
                k = self.ps_mm(layer, width, net)
                pts = [tuple(p) for p in pts]
                for a, b in zip(pts, pts[1:]):
                    L = math.hypot(b[0] - a[0], b[1] - a[1])
                    if L < 1e-9:
                        continue
                    d = ((b[0] - a[0]) / L, (b[1] - a[1]) / L)
                    if prev is not None and prev[0] == layer and math.hypot(prev[2][0] - a[0], prev[2][1] - a[1]) < 1e-6:
                        t = math.acos(max(-1.0, min(1.0, prev[1][0] * d[0] + prev[1][1] * d[1])))
                        if t > math.radians(0.5):
                            bend += (self.bend["k_mitre0"] + self.bend["k_mitre1"] * width) * t * k
                            nv += 1
                            turned += t
                    centre += L * k
                    mm[layer] = mm.get(layer, 0.0) + L
                    segs.append((layer, width, a, b, k))
                    prev = (layer, d, b)
            elif it[0] == "v":
                s = self.barrel(it[1], it[2])
                centre += s * self.td["via"]
                vias.append(round(s, 4))
                prev = None
            elif it[0] == "r":
                centre += it[1] * self.td["ms20"]
                mm["part"] = mm.get("part", 0.0) + it[1]
                prev = None
            elif it[0] == "finger":      # the track before / after ends 0.10 mm inside the finger pad: not counted
                layer, width = it[1], it[2]
                centre -= (FINGER_TOP - FINGER_START) * self.ps_mm(layer, width, net)
        # antiparallel legs of one net on one layer (a bump): each pair loses 2 k_leg exp(-pi gap / decay) x overlap
        for i in range(len(segs)):
            for j in range(i + 1, len(segs)):
                la, wa, a0, a1, ka = segs[i]
                lb, wb, b0, b1, kb = segs[j]
                if la != lb:
                    continue
                ua = (a1[0] - a0[0], a1[1] - a0[1])
                ub = (b1[0] - b0[0], b1[1] - b0[1])
                La, Lb = math.hypot(*ua), math.hypot(*ub)
                if (ua[0] * ub[0] + ua[1] * ub[1]) / (La * Lb) > -0.999:
                    continue
                ux, uy = ua[0] / La, ua[1] / La
                dist = abs((b0[0] - a0[0]) * (-uy) + (b0[1] - a0[1]) * ux)
                if dist > 1.0:
                    continue
                ta = sorted([0.0, La])
                tb = sorted([(b0[0] - a0[0]) * ux + (b0[1] - a0[1]) * uy, (b1[0] - a0[0]) * ux + (b1[1] - a0[1]) * uy])
                ov = min(ta[1], tb[1]) - max(ta[0], tb[0])
                gap = dist - (wa + wb) / 2
                if ov > 1e-9 and gap > 0:
                    leg += 2 * self.bend["k_leg"] * math.exp(-math.pi * gap / self.bend["leg_decay_mm"]) * ov * ka
        return OrderedDict(ps=centre - bend - leg, centre_ps=centre, bend_ps=bend, leg_ps=leg,
                           mm=OrderedDict((k, round(v, 4)) for k, v in mm.items()), vias_mm=vias, vertices=nv,
                           turned_deg=round(math.degrees(turned), 1))


# ================================================================================================ geometry
def fl(f):
    return "F.Cu" if f % 2 else "B.Cu"


def other(layer):
    return "B.Cu" if layer == "F.Cu" else "F.Cu"


def kidx(net):
    return LEVELS.index(net)


def level0(net):
    """In3 level of the net at the field: the eastmost column leaves its node straight east."""
    return Y_NODE + PITCH * (7 - kidx(net))


IN3_ORDER = sorted(LEVELS, key=level0)          # In3 levels bottom -> top: DQ7, DQ6, DQ2, DQ3, DQ0, DQ1, DQ5, DQ4


def line_pts(net):
    """F.Cu line from the end of the neck to the node via."""
    x, y = COL[net], Y_LEVEL[net]
    pts = list(LEAD.get(net, [NECK[net][-1]])) + [(X_BUNDLE, y)]
    if kidx(net) < 2:
        pts += [(x - CHAMFER_LOW, y), (x, y - CHAMFER_LOW), (x, Y_NODE)]
    else:
        c = y - Y_FAN_END
        pts += [(x - c, y), (x, Y_FAN_END), (x, Y_NODE)]
    return pts


def in3_pts(net, finger, level):
    """In3 read branch from the node via to the read via above 'finger', the via on 'level'."""
    x = COL[net]
    m = 7 - kidx(net)
    yl = level0(net)
    ramp = level - yl
    if m == 0:
        pts = [(x, Y_NODE)]
    elif yl - Y_NODE <= LANE + CHAMFER:
        pts = [(x, Y_NODE), (x + (yl - Y_NODE), yl)]
    else:
        pts = [(x, Y_NODE), (x + LANE, Y_NODE + LANE), (x + LANE, yl - CHAMFER), (x + LANE + CHAMFER, yl)]
    xr = X_RAMP - PITCH * T225 * m
    pts += [(xr, yl), (xr + ramp, yl + ramp), (READ_VIA_X[finger], yl + ramp)]
    return pts


def read_track(finger, level):
    """Read finger track from the finger (y = 2.6) to the read via."""
    return READ_TRACK[finger] + [(READ_VIA_X[finger], level)]


def write_stub(finger, net, fx):
    """(neck, wide): 0.15 mm from the finger through the finger-via row, 0.20 mm up the lane and on one 45-degree run
    onto the south pad of the series resistor."""
    x0 = fx(finger)
    xl = x0 + STUB_LANE.get(finger, 0.0)
    neck = [(x0, FINGER_START)]
    if abs(xl - x0) > 1e-9:
        neck += [(x0, FINGER_TOP), (xl, FINGER_TOP + abs(xl - x0))]
    neck.append((xl, Y_STUB_WIDE))
    xc, yp = (COL.get(net) or WCK_COL[net]), Y_SER - PAD
    wide = [(xl, Y_STUB_WIDE)]
    run = STUB_PAD_RUN.get(finger, 0.0)
    if abs(xc - xl) > 1e-9:
        wide.append((xl, yp - run - abs(xc - xl)))
        if run:
            wide.append((xc, yp - run))
    wide.append((xc, yp))
    return neck, wide


def shunt_gnd_link(net, layer, fx):
    """GND land of the shunt (south pad) to the GND via south of the row."""
    x, y0 = COL[net], Y_SER - PAD
    gx, gy = SHUNT_GND_VIA[net]
    if abs(gy - y0) < 1e-9:
        return [(x, y0), (gx, gy)]
    first = next(n for n in LEVELS if SHUNT_GND_VIA[n] == SHUNT_GND_VIA[net] and other(fl(WRITE_FINGER[n])) == layer)
    if first != net:        # a second shunt on the same face shares the via: its link joins the corner of the first one (one piece of copper into the via)
        jy = y0 - abs(gx - COL[first])
        sgn = 1 if x > gx else -1
        return [(x, y0), (gx + sgn * (abs(gx - x) - (y0 - jy)), jy), (gx, jy)]
    return [(x, y0), (gx, y0 - abs(gx - x)), (gx, gy)]


def rdqs_bump(x0, y, depth):
    """Skew bump on the southern leg, pointing south: two legs RDQS_BUMP.leg_pitch apart, 'depth' = length of each
    vertical leg; eight 45-degree vertices."""
    m, p = RDQS_BUMP["mitre"], RDQS_BUMP["leg_pitch"]
    return [(x0, y), (x0 + m, y - m), (x0 + m, y - m - depth), (x0 + 2 * m, y - 2 * m - depth), (x0 + p, y - 2 * m - depth),
            (x0 + p + m, y - m - depth), (x0 + p + m, y - m), (x0 + p + 2 * m, y)]


def rdqs_paths(depth):
    """In3 legs from the end of the escape inside U1 east along y = Y_RDQS_LOW, on one 45-degree run across the F.Cu fan
    up to y = Y_RDQS_HIGH, east above the read vias, on one 45-degree run down to x = X_RDQS_DOWN and south to the via pair
    at y = RDQS_VIA_Y, just above the fingers; outer-layer legs (RDQS_OUTER) from the via pair straight down into the fingers.
    Returns net -> (In3 path, outer-layer path, via position)."""
    cc, yh, yl, yv = RDQS_CHAMFER, Y_RDQS_HIGH, Y_RDQS_LOW, RDQS_VIA_Y
    c_in3 = [(27.6, yl), (X_RDQS_NE, yl), (X_RDQS_NE + (yh - yl), yh), (X_RDQS_DOWN - cc, yh), (X_RDQS_DOWN, yh - cc), (X_RDQS_DOWN, yv + 0.2)]
    c_out = [(X_RDQS_DOWN, yv - 0.2), (X_RDQS_DOWN, FINGER_START)]
    out = OrderedDict()
    for net, sgn in (("RDQS_P", -1), ("RDQS_N", 1)):
        a = offset_polyline(c_in3, 0.1 * sgn)
        if net == RDQS_BUMP["net"] and depth > 0:
            assert sgn == -1 and RDQS_BUMP["layer"] == "In3.Cu"
            a = a[:1] + rdqs_bump(RDQS_BUMP["x0"], yl + 0.1 * sgn, depth) + a[1:]
        v = (X_RDQS_DOWN + RDQS_VIA_DX * sgn, yv)        # south of the corner the leg of sgn = +1 (RDQS_N) is the eastern one
        in3 = RDQS_HEAD[net][:-1] + a + [v]
        b = offset_polyline(c_out, 0.1 * sgn)
        b = b[:-1] + [(b[-1][0], Y_RDQS_LOAD + PAD), b[-1]]          # vertex where the link of the 150 ohm load joins the leg
        out[net] = (in3, [v] + b, v)
    return out


# ================================================================================================ delays
def trunk_items(net):
    return [("t", "F.Cu", W_NECK, NECK[net]), ("t", "F.Cu", W_LINE, line_pts(net))]


def write_chain(net, fx):
    f = WRITE_FINGER[net]
    lay = fl(f)
    neck, wide = write_stub(f, net, fx)
    x = COL[net]
    items = [("t", lay, W_STUB_NECK, neck), ("finger", lay, W_STUB_NECK), ("t", lay, W_STUB, wide), ("r", 2 * PAD),
             ("t", lay, W_STUB, [(x, Y_SER + PAD), (x, Y_NODE)])]
    if lay != "F.Cu":
        items.append(("v", "B.Cu", "F.Cu"))
    return items + [("t", "F.Cu", W_LINE, list(reversed(line_pts(net)))), ("t", "F.Cu", W_NECK, list(reversed(NECK[net])))]


def read_chain(net, finger, level):
    lay = fl(finger)
    return trunk_items(net) + [("v", "F.Cu", "In3.Cu"), ("t", "In3.Cu", W_IN3, in3_pts(net, finger, level)), ("v", "In3.Cu", lay),
                               ("t", lay, W_STUB, list(reversed(read_track(finger, level)))), ("finger", lay, W_STUB)]


def rdqs_chain(net, paths):
    in3, bcu = paths[net][:2]
    return [("v", "F.Cu", "In3.Cu"), ("t", "In3.Cu", W_PAIR, in3), ("v", "In3.Cu", RDQS_OUTER), ("t", RDQS_OUTER, W_PAIR, bcu), ("finger", RDQS_OUTER, W_PAIR)]


def wck_chain(net, fx):
    f = WCK_FINGER[net]
    neck, wide = write_stub(f, net, fx)
    x = WCK_COL[net]
    return [("t", "F.Cu", W_STUB_NECK, neck), ("finger", "F.Cu", W_STUB_NECK), ("t", "F.Cu", W_STUB, wide), ("r", 2 * PAD),
            ("t", "F.Cu", W_LINK_WCK, [(x, Y_SER + PAD), (x, Y_NODE)]), ("v", "F.Cu", "B.Cu"),
            ("t", "B.Cu", W_PAIR, list(reversed(WCK_LEG[net]))), ("v", "B.Cu", "F.Cu")]


def crossings_read_end(assign):
    """Read-end crossings of an assignment net -> P finger: the finger track of a line crosses every In3 line of a
    lower level that goes on east of it."""
    n = 0
    for a, b in itertools.combinations(LEVELS, 2):
        hi, lo = (a, b) if level0(a) > level0(b) else (b, a)
        if READ_VIA_X[assign[hi]] < READ_VIA_X[assign[lo]]:
            n += 1
    return n


def best_raise(t0, k, lim=None, steps=400):
    """Raises e[i] >= 0, non-decreasing in the order of the bundle (a raised line takes the lines above it with it),
    that give the smallest spread of t0[i] + e[i] * k[i].  For a lower edge Lo the cheapest raises are
    e[i] = max(e[i-1], (Lo - t0[i]) / k[i], 0); Lo is scanned (grid, then refined).  Returns (half spread, e, totals)."""
    def at(lo_edge):
        e, cur, tot = [], 0.0, []
        for i in range(len(t0)):
            cur = max(cur, (lo_edge - t0[i]) / k[i], 0.0)
            if lim is not None and cur > lim[i] + 1e-9:
                return None
            e.append(cur)
            tot.append(t0[i] + cur * k[i])
        return (max(tot) - min(tot)) / 2, e, tot
    a, b = min(t0), max(t0)
    best, best_lo = None, a
    for j in range(steps + 1):
        lo_edge = a + (b - a) * j / steps
        r = at(lo_edge)
        if r is not None and (best is None or r[0] < best[0] - 1e-12):
            best, best_lo = r, lo_edge
    h = (b - a) / steps
    for _ in range(30):                     # refine around the best grid point
        h /= 2
        for lo_edge in (best_lo - h, best_lo + h):
            r = at(lo_edge)
            if r is not None and r[0] < best[0] - 1e-12:
                best, best_lo = r, lo_edge
    # among the lower edges that reach this spread within 0.02 ps take the LOWEST: no line is raised for nothing
    # (two edges often tie: the latest and the earliest line of the group are then lines that move together)
    for j in range(8 * steps + 1):
        r = at(a + (b - a) * j / (8 * steps))
        if r is not None and r[0] <= best[0] + 0.02:
            return r
    return best


def solve_levels(model, hp, assign):
    """Level of every read via: 0.55 mm pitch from Y_NODE + RAMP up in the order of the bundle (a regular staircase); a line
    that is early may be raised and takes the lines above it with it (best_raise: the smallest spread, and of the level sets
    that reach it the lowest).  Returns (levels, totals, half spread, centre)."""
    base_y = [Y_NODE + RAMP + PITCH * i for i in range(8)]
    order = IN3_ORDER
    t0 = [model.chain(n, read_chain(n, assign[n], base_y[i]))["ps"] + hp(assign[n]) for i, n in enumerate(order)]
    eps = 0.01
    k = [(model.chain(n, read_chain(n, assign[n], base_y[i] + eps))["ps"] - model.chain(n, read_chain(n, assign[n], base_y[i]))["ps"]) / eps
         for i, n in enumerate(order)]
    lim = []
    for i, n in enumerate(order):
        xr = X_RAMP - PITCH * T225 * (7 - kidx(n))
        lim.append(READ_VIA_X[assign[n]] - RAMP_END_TO_VIA_MIN - xr - (base_y[i] - level0(n)))
    _half, e, _tot = best_raise(t0, k, lim)
    er, cur = [], 0.0
    for v in e:                             # raises on a 0.05 mm grid (0.45 ps), still non-decreasing: the levels stay round numbers
        cur = max(cur, round(round(v / LEVEL_GRID) * LEVEL_GRID, 4))
        er.append(cur)
    levels = OrderedDict((n, round(base_y[i] + er[i], 4)) for i, n in enumerate(order))
    totals = OrderedDict((n, model.chain(n, read_chain(n, assign[n], levels[n]))["ps"] + hp(assign[n])) for n in order)
    tv = list(totals.values())
    return levels, totals, (max(tv) - min(tv)) / 2, (max(tv) + min(tv)) / 2


def explore(model, hp):
    """All 40320 assignments of the eight read pairs to the eight lines: (read-end crossings, half spread with the levels
    solved, assignment in the order of the bundle) per assignment, and the front crossings -> smallest half spread."""
    base_y = [Y_NODE + RAMP + PITCH * i for i in range(8)]
    order = IN3_ORDER
    t0, k, lim = {}, {}, {}
    for i, n in enumerate(order):
        xr = X_RAMP - PITCH * T225 * (7 - kidx(n))
        for f in READ_CANDIDATES:
            a = model.chain(n, read_chain(n, f, base_y[i]))["ps"]
            b = model.chain(n, read_chain(n, f, base_y[i] + 0.01))["ps"]
            t0[(n, f)] = a + hp(f)
            k[(n, f)] = (b - a) / 0.01
            lim[(n, f)] = READ_VIA_X[f] - RAMP_END_TO_VIA_MIN - xr - (base_y[i] - level0(n))
    front, rows = {}, []
    for perm in itertools.permutations(READ_CANDIDATES):
        assign = dict(zip(order, perm))
        r = best_raise([t0[(n, assign[n])] for n in order], [k[(n, assign[n])] for n in order], [lim[(n, assign[n])] for n in order], steps=60)
        if r is None:
            continue
        c = crossings_read_end(assign)
        rows.append((c, r[0], perm, r[1]))
        if c not in front or r[0] < front[c][0] - 1e-9:
            front[c] = (r[0], perm, r[1])
    return front, rows


def choose_read_end(model, hp):
    """Every assignment of eight of the READ_CANDIDATES pairs to the eight In3 lines with ZERO read-end crossings, i.e. the
    lines bottom -> top on the fingers west -> east (for a set of eight fingers that is one order), each with its levels
    solved.  Sorted: smallest half spread, then the tidiest read end (vias on the regular staircase = no line raised, the
    shortest finger tracks, the lowest top level).  Returns the list of rows; row[0] is the plan."""
    cands = sorted(READ_CANDIDATES, key=lambda f: READ_VIA_X[f])
    rows = []
    for sub in itertools.combinations(cands, 8):
        assign = OrderedDict(zip(IN3_ORDER, sub))
        assert crossings_read_end(assign) == 0
        levels, totals, half, cen = solve_levels(model, hp, assign)
        raised = sum(levels[n] - (Y_NODE + RAMP + PITCH * i) for i, n in enumerate(IN3_ORDER))
        track = sum(plen(read_track(assign[n], levels[n])) for n in IN3_ORDER)
        rows.append(OrderedDict(assign=assign, levels=levels, totals=totals, half_spread_ps=half, centre_ps=cen,
                                raised_mm=round(raised, 4), finger_track_mm=round(track, 3), top_level_y=max(levels.values())))
    rows.sort(key=lambda r: (round(r["half_spread_ps"], 2), r["raised_mm"], r["finger_track_mm"], r["top_level_y"]))
    return rows


def _pt_seg(p, a, b):
    L2 = (b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * (b[0] - a[0]) + (p[1] - a[1]) * (b[1] - a[1])) / L2))
    return math.hypot(p[0] - a[0] - t * (b[0] - a[0]), p[1] - a[1] - t * (b[1] - a[1]))


def _pt_poly(p, pts):
    return min(_pt_seg(p, a, b) for a, b in zip(pts, pts[1:]))


def place_return_vias(read, assign, levels, pair_paths):
    """GND return via of every read via, from the top of the staircase down.  A read via first takes a return via that
    is already placed if it stands RETURN_TO_VIA_MIN .. RETURN_SHARE_MAX from it (two read vias then share the via
    between them); otherwise the first bearing of RETURN_BEARINGS that keeps RETURN_TO_IN3_LINE_MIN from every In3 read
    line, RETURN_TO_VIA_MIN from every read via and from the return vias placed before it, RETURN_TO_TRACK_MIN from
    every read finger track (a through via: both faces) and RETURN_TO_PAIR_MIN from the RDQS legs on every layer.
    Returns net -> ([x, y], bearing or "shared with <net>")."""
    eps = 1e-6
    out = OrderedDict()
    for n in reversed(IN3_ORDER):
        vx, vy = read[n]["read_via"]
        share = [(o, q) for o, (q, _b) in out.items() if RETURN_TO_VIA_MIN - eps <= math.hypot(q[0] - vx, q[1] - vy) <= RETURN_SHARE_MAX + eps]
        if share:
            out[n] = (list(share[0][1]), "shared with " + share[0][0])
            continue
        for dx, dy in RETURN_BEARINGS:
            q = (vx + dx, vy + dy)
            ok = True
            for o in LEVELS:
                ok &= _pt_poly(q, read[o]["in3_route"]) >= RETURN_TO_IN3_LINE_MIN - eps
                ok &= math.hypot(q[0] - read[o]["read_via"][0], q[1] - read[o]["read_via"][1]) >= RETURN_TO_VIA_MIN - eps
                ok &= _pt_poly(q, read_track(assign[o], levels[o])) >= RETURN_TO_TRACK_MIN - eps
            for (x, y), _b in out.values():
                ok &= math.hypot(q[0] - x, q[1] - y) >= RETURN_TO_VIA_MIN - eps
            for legs in pair_paths.values():
                for pts in legs:
                    ok &= _pt_poly(q, pts) >= RETURN_TO_PAIR_MIN - eps
            if ok:
                out[n] = ([r4(q[0]), r4(q[1])], [dx, dy])
                break
        else:
            raise AssertionError("no place for the return via of the read via of %s" % n)
    return out


# ================================================================================================ the plan
def build(hp, fx, ball_xy, td, bend, z):
    """hp(finger) = host + package delay, ps; fx(finger) = finger x; ball_xy(ball); td = ps / mm table; bend = bend
    model; z = mid-copper height per layer.  Returns the whole data side."""
    model = Model(td, bend, z, ["WCK_P", "WCK_N", "RDQS_P", "RDQS_N"])
    D = OrderedDict()
    wires, vias, parts = [], [], []

    def wire(net, layer, width, pts, role, group):
        wires.append(OrderedDict(net=net, layer=layer, width=width, group=group, role=role, path=P4(pts)))

    def via(net, x, y, role, group, **kw):
        for v in vias:
            if v["net"] == net and abs(v["x"] - x) < 1e-6 and abs(v["y"] - y) < 1e-6:
                v["role"] += "; " + role
                return
        vias.append(OrderedDict(net=net, x=r4(x), y=r4(y), role=role, group=group, **kw))

    def part(ref, kind, layer, x, y, south_net, north_net, pad1_south, group):
        parts.append(OrderedDict(ref=ref, kind=kind, side=layer[0], x=r4(x), y=r4(y), pad1_south=pad1_south, group=group,
                                 south=south_net, north=north_net))

    for n in LEVELS:                    # the plan starts where the board has the balls
        assert tuple(NECK[n][0]) == tuple(ball_xy(BALL[n])), (n, NECK[n][0], ball_xy(BALL[n]))
    for n in ("WCK_P", "WCK_N"):
        assert tuple(WCK_LEG[n][0]) == tuple(ball_xy(WCK_BALL[n])), n
    for n in ("RDQS_P", "RDQS_N"):
        assert tuple(RDQS_HEAD[n][0]) == tuple(ball_xy(RDQS_BALL[n])), n

    # ---- read end: the order without crossings and its levels (choose_read_end), then the RDQS via pair and bump ---------
    assign = OrderedDict((n, READ_FINGER[n][0]) for n in LEVELS)
    choice = choose_read_end(model, hp)
    assert dict(choice[0]["assign"]) == dict(assign), ("READ_FINGER is not the read end chosen by choose_read_end()", choice[0]["assign"])
    assert crossings_read_end(assign) == 0
    levels, rtot, rhalf, rcen = solve_levels(model, hp, assign)
    assert all(abs(levels[n] - (Y_NODE + RAMP + PITCH * i)) < 1e-9 for i, n in enumerate(IN3_ORDER)), ("the read vias are not on the regular staircase", levels)
    target = round(rcen, 1)             # centre of the (unmatched) read group: the strobe is NOT put on it (its offset goes to the deskew table)

    def rd(net, pp):
        return model.chain(net, rdqs_chain(net, pp))["ps"] + hp(RDQS_FINGER[net])
    lo, hi = 0.0, 2.5
    for _ in range(60):
        depth = (lo + hi) / 2
        pp = rdqs_paths(depth)
        if rd("RDQS_P", pp) < rd("RDQS_N", pp):
            lo = depth
        else:
            hi = depth
    depth = round(depth, 3)
    pp = rdqs_paths(depth)
    assert RDQS_VIA_Y - 0.20 - (Y_BUS_B + W_VREF / 2) >= 0.90 + 1e-9, "the B.Cu pad of the RDQS vias is nearer than 0.90 mm to the VREF_RD bus on B.Cu"
    assert X_FEED - W_VREF / 2 - (X_RDQS_DOWN + RDQS_VIA_DX + 0.20) >= 0.90 + 1e-9, "the VREF_RD feed is nearer than 0.90 mm to the pad of the eastern RDQS via"

    # ---- write side: columns ---------------------------------------------------------------------------------------------
    cols = OrderedDict()
    for n in LEVELS:
        i = int(n[2:])
        x, f = COL[n], WRITE_FINGER[n]
        lay, oth = fl(f), other(fl(f))
        ser, sh = "R%d" % (201 + i), "R%d" % (211 + i)
        part(ser, "R0201", lay, x, Y_SER, n + "_WF", n, True, "wr_dq")
        part(sh, "R0201", oth, x, Y_SER, "GND", n, False, "wr_dq")
        via(n, x, Y_NODE, "node via = the T: F.Cu tooth, In3 read branch, 240R on %s, 150R on %s" % (lay, oth), "WR",
            unused_inner_pads="removed")
        neck, wide = write_stub(f, n, fx)
        wire(n + "_WF", lay, W_STUB_NECK, neck, "finger stub, through the finger-via row", "WR")
        wire(n + "_WF", lay, W_STUB, wide, "finger stub", "WR")
        wire(n, lay, W_STUB, [(x, Y_SER + PAD), (x, Y_NODE)], "series resistor - node via", "WR")
        wire(n, oth, W_STUB, [(x, Y_NODE), (x, Y_SER + PAD)], "node via - shunt", "WR")
        wire("GND", oth, W_GND, shunt_gnd_link(n, oth, fx), "shunt - GND via", "GND")
        gx, gy = SHUNT_GND_VIA[n]
        via("GND", gx, gy, "GND via of the shunt of %s, south of the row" % n, "WR", unused_inner_pads="removed")
        wire(n, "F.Cu", W_NECK, NECK[n], "neck", "WR")
        wire(n, "F.Cu", W_LINE, line_pts(n), "line and tooth", "WR")
        cols[n] = OrderedDict(x=x, ball=BALL[n], level_y=Y_LEVEL[n], write_finger=f, series=ser, series_side=lay[0], shunt=sh,
                              shunt_side=oth[0], node_via=[x, Y_NODE], shunt_gnd_via=[gx, gy], in3_level_at_the_field=r4(level0(n)),
                              riser_lane_x=r4(x + LANE) if kidx(n) < 6 else None)
    for x, y, who in NODE_RETURN_VIAS:
        via("GND", x, y, "return via beside the node via(s) of %s" % who, "WR", unused_inner_pads="removed")

    # ---- WCK -----------------------------------------------------------------------------------------------------------
    for k, n in enumerate(["WCK_P", "WCK_N"]):
        x, f = WCK_COL[n], WCK_FINGER[n]
        ser, sh = "R%d" % (209 + k), "R%d" % (219 + k)
        part(ser, "R0201", "F.Cu", x, Y_SER, "WCK_F_" + n[-1], n, True, "wr_wck")
        part(sh, "R0201", "F.Cu", x, Y_WCK_SHUNT, n, "GND", True, "wr_wck")
        via(n, x, Y_NODE, "node via of the %s column: series resistor south, shunt north on F.Cu, B.Cu leg" % n, "WCK", unused_inner_pads="removed")
        gx, gy = WCK_GND_VIA[n]
        via("GND", gx, gy, "GND via of the %s shunt, 0.4 mm beside the column axis" % n, "WCK", unused_inner_pads="removed")
        neck, wide = write_stub(f, n, fx)
        wire("WCK_F_" + n[-1], "F.Cu", W_STUB_NECK, neck, "finger stub, through the finger-via row", "WCK")
        wire("WCK_F_" + n[-1], "F.Cu", W_STUB, wide, "finger stub", "WCK")
        wire(n, "F.Cu", W_LINK_WCK, [(x, Y_SER + PAD), (x, Y_NODE)], "series resistor - node via", "WCK")
        wire(n, "F.Cu", W_LINK_WCK, [(x, Y_NODE), (x, Y_WCK_SHUNT - PAD)], "node via - shunt", "WCK")
        wire("GND", "F.Cu", W_GND, WCK_GND_LINK[n], "shunt - GND via", "GND")
        wire(n, "B.Cu", W_PAIR, WCK_LEG[n], "leg", "WCK")

    # ---- read side -----------------------------------------------------------------------------------------------------
    read = OrderedDict()
    for n in LEVELS:
        f, nf = READ_FINGER[n]
        lay = fl(f)
        lv = levels[n]
        pts = in3_pts(n, f, lv)
        xvia = READ_VIA_X[f]
        wire(n, "In3.Cu", W_IN3, pts, "read branch from the node via", "RD")
        via(n, xvia, lv, "read via In3 - %s on the level of its line, above finger %d" % (lay, f), "RD", unused_inner_pads="removed")
        wire(n, lay, W_STUB, list(reversed(read_track(f, lv))), "read finger track", "RD")
        base = Y_NODE + RAMP + PITCH * IN3_ORDER.index(n)
        read[n] = OrderedDict(p_finger=f, n_finger=nf, finger_track_layer=lay, read_via=[r4(xvia), lv], level_y=lv,
                              level_at_pitch_y=r4(base), raised_mm=r4(lv - base), ramp_mm=r4(lv - level0(n)),
                              hairpin_mm=r4(level0(n) - Y_NODE), in3_route=P4(pts))
    placed = place_return_vias(read, assign, levels, OrderedDict((n, q[:2]) for n, q in pp.items()))
    ret, ret_bearing = OrderedDict(), OrderedDict()
    for n in LEVELS:
        (x, y), bearing = placed[n]
        via("GND", x, y, "return via of the read via of %s" % n, "RD", unused_inner_pads="removed")
        ret[n] = [x, y]
        ret_bearing[n] = bearing

    # ---- RDQS ----------------------------------------------------------------------------------------------------------
    for k, n in enumerate(["RDQS_P", "RDQS_N"]):
        in3, bcu, (vx, vy) = pp[n]
        sgn = -1 if n == "RDQS_P" else 1
        wire(n, "In3.Cu", W_PAIR, in3, "leg", "RDQS")
        # the 45-degree step from the centre of the via to the coupled pair is a track item of its own: it lies wholly inside the rule area
        # rdqs_via_pair (the DRC rule RDQS_pair_to_read_tracks holds for the pair below it)
        wire(n, RDQS_OUTER, W_PAIR, bcu[:2], "leg, via to the pair", "RDQS")
        wire(n, RDQS_OUTER, W_PAIR, bcu[1:], "leg", "RDQS")
        via(n, vx, vy, "RDQS via In3 - %s just above finger %d (lowest position: 0.90 mm from the VREF_RD bus on B.Cu)" % (RDQS_OUTER, RDQS_FINGER[n]), "RDQS",
            unused_inner_pads="removed")
        via("GND", X_RDQS_DOWN + RDQS_RETURN_DX * sgn, vy + RDQS_RETURN_DY, "return via of the RDQS via pair", "RDQS", unused_inner_pads="removed")
        ref = "R%d" % (309 + k)
        x = X_RDQS_DOWN + RDQS_LOAD_DX * sgn
        part(ref, "R0201", RDQS_OUTER, x, Y_RDQS_LOAD, "GND", n, False, "rdqs_term")
        xl = X_RDQS_DOWN + 0.1 * sgn
        wire(n, RDQS_OUTER, W_PAIR, [(xl, Y_RDQS_LOAD + PAD), (x, Y_RDQS_LOAD + PAD)], "load link", "RDQS")
        wire("GND", RDQS_OUTER, W_GND, [(x, Y_RDQS_LOAD - PAD), (x, Y_FINGER_VIA)], "load - VSS finger via", "GND")

    # ---- reference networks ----------------------------------------------------------------------------------------------
    refnet = OrderedDict()
    taps = {"F.Cu": [], "B.Cu": []}
    for n in LEVELS:
        i = int(n[2:])
        f, nf = READ_FINGER[n]
        lay = fl(nf)
        xn = fx(nf)
        sgn = 1 if xn > fx(f) else -1
        xc = r4(xn + 0.5 * sgn)                    # axis of the VSS finger on the side away from the P finger
        rr, cc = "R%d" % (321 + i), "C%d" % (321 + i)
        part(rr, "R0201", lay, xn, Y_REF_R, "VREF_N%d" % i, "VREF_RD", True, "n_ref")
        part(cc, "C0201", lay, xc, Y_REF_C, "GND", "VREF_RD", False, "n_ref")
        stub = [(xn, FINGER_START)]
        b = N_STUB_BULGE.get(nf)
        if b:
            stub += [(xn, FINGER_TOP), (xn + b, FINGER_TOP + b), (xn + b, Y_STUB_WIDE), (xn, Y_STUB_WIDE + b)]
        stub.append((xn, Y_REF_R - PAD))
        yb = Y_BUS if lay == "F.Cu" else Y_BUS_B
        wire("VREF_N%d" % i, lay, W_VREF, stub, "N finger stub", "REF")
        wire("VREF_RD", lay, W_VREF, [(xn, Y_REF_R + PAD), (xn, yb)], "39R - bus", "REF")
        wire("VREF_RD", lay, W_VREF, [(xc, Y_REF_C + PAD), (xc, yb)], "100 nF - bus", "REF")
        taps[lay] += [xn, xc]
        refnet[n] = OrderedDict(n_finger=nf, side=lay[0], resistor=rr, capacitor=cc, x_resistor=r4(xn), x_capacitor=xc,
                                vss_finger_of_the_capacitor=None, stub_mm=r4(plen(stub) - (FINGER_TOP - FINGER_START)))
    assert max(taps["F.Cu"]) < BUS_VIA[0] - 0.3 and min(taps["B.Cu"]) > BUS_VIA[0]
    # every tap is a vertex of the bus (tracks join at end points)
    wire("VREF_RD", "F.Cu", W_VREF, [(x, Y_BUS) for x in sorted(taps["F.Cu"])] + [(BUS_VIA[0] - (BUS_VIA[1] - Y_BUS), Y_BUS), BUS_VIA], "bus, front side", "REF")
    wire("VREF_RD", "B.Cu", W_VREF, [BUS_VIA] + [(x, Y_BUS_B) for x in sorted(taps["B.Cu"])] + [(X_FEED, Y_BUS_B), (X_FEED, Y_FEED_VIA)],
         "bus, back side, and feed to the DAC network", "REF")
    via("VREF_RD", BUS_VIA[0], BUS_VIA[1], "bus via F.Cu - B.Cu, between the read tracks of fingers 212 (B.Cu, %.3f mm west of its axis) and 215 (F.Cu, %.3f mm east)"
        % (fx(212) - READ_VIA_X[212], READ_VIA_X[215] - fx(215)), "REF", unused_inner_pads="removed")
    via("VREF_RD", X_FEED, Y_FEED_VIA, "feed via B.Cu - F.Cu below TP4", "REF", unused_inner_pads="removed")

    # ---- delay budgets -----------------------------------------------------------------------------------------------------
    wr = OrderedDict()
    for n in LEVELS:
        c = model.chain(n, write_chain(n, fx))
        wr[n] = OrderedDict(finger=WRITE_FINGER[n], card_ps=round(c["ps"], 3), centre_line_ps=round(c["centre_ps"], 3), bend_ps=round(c["bend_ps"], 3),
                            host_ps=hp(WRITE_FINGER[n]), total_ps=round(c["ps"] + hp(WRITE_FINGER[n]), 3), mm=c["mm"], barrels_mm=c["vias_mm"],
                            vertices=c["vertices"])
    wt = [v["total_ps"] for v in wr.values()]
    for n in LEVELS:
        wr[n]["to_centre_ps"] = round(wr[n]["total_ps"] - (max(wt) + min(wt)) / 2, 2)
        wr[n]["odelay_that_closes_it_ps"] = round(max(wt) - wr[n]["total_ps"], 1)
    rdd = OrderedDict()
    r_late = max(rtot.values())
    for n in LEVELS:
        f = READ_FINGER[n][0]
        c = model.chain(n, read_chain(n, f, levels[n]))
        tr = model.chain(n, trunk_items(n))
        rdd[n] = OrderedDict(finger=f, card_ps=round(c["ps"], 3), centre_line_ps=round(c["centre_ps"], 3), bend_ps=round(c["bend_ps"], 3),
                             trunk_ps=round(tr["ps"], 3), host_ps=hp(f), total_ps=round(c["ps"] + hp(f), 3), mm=c["mm"], barrels_mm=c["vias_mm"],
                             vertices=c["vertices"], error_ps=round(c["ps"] + hp(f) - target, 3),
                             to_centre_ps=round(c["ps"] + hp(f) - rcen, 2), idelay_that_closes_it_ps=round(r_late - (c["ps"] + hp(f)), 1))
    rq = OrderedDict()
    for n in ("RDQS_P", "RDQS_N"):
        c = model.chain(n, rdqs_chain(n, pp))
        rq[n] = OrderedDict(finger=RDQS_FINGER[n], card_ps=round(c["ps"], 3), centre_line_ps=round(c["centre_ps"], 3), bend_ps=round(c["bend_ps"], 3),
                            leg_coupling_ps=round(c["leg_ps"], 3), host_ps=hp(RDQS_FINGER[n]), total_ps=round(c["ps"] + hp(RDQS_FINGER[n]), 3), mm=c["mm"],
                            barrels_mm=c["vias_mm"], vertices=c["vertices"], error_ps=round(c["ps"] + hp(RDQS_FINGER[n]) - target, 3))
    wk = OrderedDict()
    for n in ("WCK_P", "WCK_N"):
        c = model.chain(n, wck_chain(n, fx))
        wk[n] = OrderedDict(finger=WCK_FINGER[n], card_ps=round(c["ps"], 3), centre_line_ps=round(c["centre_ps"], 3), bend_ps=round(c["bend_ps"], 3),
                            host_ps=hp(WCK_FINGER[n]), total_ps=round(c["ps"] + hp(WCK_FINGER[n]), 3), mm=c["mm"], barrels_mm=c["vias_mm"], vertices=c["vertices"])

    k_level = (model.chain("DQ0", read_chain("DQ0", 204, 9.5))["ps"] - model.chain("DQ0", read_chain("DQ0", 204, 9.4))["ps"]) / 0.1
    D.update(
        pin_map=OrderedDict(write=WRITE_FINGER, read=OrderedDict((n, OrderedDict(p=READ_FINGER[n][0], n=READ_FINGER[n][1])) for n in LEVELS),
                            wck=WCK_FINGER, rdqs=RDQS_FINGER, reset_n=RESET_FINGER),
        columns=cols, read=read, read_return_vias=ret, read_return_bearing=ret_bearing, reference=refnet,
        read_end_choice=OrderedDict(
            rule="zero read-end crossings: the In3 lines bottom -> top take the read fingers west -> east; of the assignments and level sets "
                 "that do, the smallest spread, then no line raised (regular staircase), the shortest finger tracks, the lowest bundle",
            candidates=list(READ_CANDIDATES), zero_crossing_assignments=len(choice),
            chosen=OrderedDict((n, choice[0]["assign"][n]) for n in IN3_ORDER),
            half_spread_ps=round(choice[0]["half_spread_ps"], 3), centre_ps=round(choice[0]["centre_ps"], 3),
            raised_mm=choice[0]["raised_mm"], finger_track_mm=choice[0]["finger_track_mm"], top_level_y=choice[0]["top_level_y"]),
        knobs=OrderedDict(read_target_ps=target, read_centre_ps=round(rcen, 3), read_half_spread_ps=round(rhalf, 3),
                          ps_per_mm_of_level=round(k_level, 3), rdqs_via_y=RDQS_VIA_Y, rdqs_via_dx=RDQS_VIA_DX, rdqs_bump_depth_mm=depth, rdqs_high_run_y=Y_RDQS_HIGH,
                          rdqs_ne_x=X_RDQS_NE, rdqs_low_run_y=Y_RDQS_LOW, rdqs_down_x=X_RDQS_DOWN, fan_end_y=Y_FAN_END, ramp_mm=RAMP, ramp_x=X_RAMP, pitch=PITCH,
                          lane=LANE, read_end_crossings=crossings_read_end(assign), rdqs_outer_layer=RDQS_OUTER,
                          rdqs_outer_drop_mm=r4(RDQS_VIA_Y - FINGER_TOP), read_latest_total_ps=round(r_late, 3)),
        rdqs=OrderedDict(via_pair=[[r4(X_RDQS_DOWN - RDQS_VIA_DX), RDQS_VIA_Y], [r4(X_RDQS_DOWN + RDQS_VIA_DX), RDQS_VIA_Y]],
                         return_vias=[[r4(X_RDQS_DOWN - RDQS_RETURN_DX), r4(RDQS_VIA_Y + RDQS_RETURN_DY)], [r4(X_RDQS_DOWN + RDQS_RETURN_DX), r4(RDQS_VIA_Y + RDQS_RETURN_DY)]],
                         bump=OrderedDict(net=RDQS_BUMP["net"], layer=RDQS_BUMP["layer"], x=[r4(RDQS_BUMP["x0"] + RDQS_BUMP["mitre"]),
                                                                              r4(RDQS_BUMP["x0"] + RDQS_BUMP["mitre"] + RDQS_BUMP["leg_pitch"])],
                                          leg_pitch=RDQS_BUMP["leg_pitch"], mitre=RDQS_BUMP["mitre"], depth_mm=depth,
                                          bottom_y=r4(Y_RDQS_LOW - 0.1 - 2 * RDQS_BUMP["mitre"] - depth), side="south"),
                         loads=OrderedDict((n, [r4(X_RDQS_DOWN + RDQS_LOAD_DX * sg), Y_RDQS_LOAD]) for n, sg in (("RDQS_P", -1), ("RDQS_N", 1))),
                         outer_layer=RDQS_OUTER, down_x=X_RDQS_DOWN,
                         load_gnd="the VSS finger vias of the pairs 217 / 218 and 222 / 223 (y = %.2f)" % Y_FINGER_VIA,
                         ne_run=OrderedDict(x=[X_RDQS_NE, r4(X_RDQS_NE + Y_RDQS_HIGH - Y_RDQS_LOW)], y=[Y_RDQS_LOW, Y_RDQS_HIGH]),
                         ),
        delays=OrderedDict(write=wr, read=rdd, rdqs=rq, wck=wk),
        wires=wires, vias=vias, parts=parts, model=model, levels=levels, assign=assign, rdqs_paths=pp)
    return D


def finish_reference_gnd(D, finger_vias, fx):
    """GND land of every 100 nF down the axis of its VSS finger to that finger's via (called once the finger vias are
    placed: their x follows from the finger stubs)."""
    by_finger = {}
    for v in finger_vias:
        for f in v["fingers"]:
            by_finger[f] = v
    fmap = {}
    for f in range(1, 261):
        fmap[(round(fx(f), 3), fl(f))] = f
    for n, q in D["reference"].items():
        lay = "F.Cu" if q["side"] == "F" else "B.Cu"
        xc = q["x_capacitor"]
        f = fmap[(round(xc, 3), lay)]
        v = by_finger[f]
        assert v["net"] == "GND", (n, f, v["net"])
        q["vss_finger_of_the_capacitor"] = f
        d = abs(v["x"] - xc)
        pts = [(xc, Y_REF_C - PAD)] + ([(xc, v["y"] + d)] if d > 1e-9 else []) + [(v["x"], v["y"])]
        D["wires"].append(OrderedDict(net="GND", layer=lay, width=W_GND, group="GND", role="100 nF - VSS finger via", path=P4(pts)))

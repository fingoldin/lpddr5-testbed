#!/usr/bin/env python3
"""Generate design/netlist.json, the single source of truth for schematic and PCB.

The design decisions live in this file as plain tables; the JSON it writes is what the
schematic and PCB generators read.  Re-run after any change, then run scripts/check_netlist.py.

Inputs (kept in the repository so the build is reproducible):
  design/ref/zcu104_sodimm_pinmap.csv        host side of every J1 finger (ball, bank, P/N, x position)
  design/ref/zcu104_finger_total_delay.csv   host copper + FPGA package delay per finger (Vivado + XTP485)

Coordinates: origin = lower-left corner of the card seen from the front (component side),
x along the finger edge from the pin-1 end, y up from the finger edge, millimetres.
"""
import csv
import json
import math
import os
import sys
from collections import OrderedDict, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tuning as TUNE      # noqa: E402  tuning geometry and the electrical length of bends (shared with the PCB stages)
import data_side as DATA   # noqa: E402  the data side as one plan: write, read, WCK, RDQS, reference (DESIGN.md section 14)
import power_side as POWER  # noqa: E402  power, slow nets (RESET_n, I2C) and pours as one plan (DESIGN.md section 15)
import read_linesim as LINESIM   # noqa: E402  line simulation of a read DQ without series resistor (analysis.read.open_end)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REF = os.path.join(ROOT, "design", "ref")
OUT = os.path.join(ROOT, "design", "netlist.json")

# ----------------------------------------------------------------------------------------------
# 1. Mechanics, stackup, rules
# ----------------------------------------------------------------------------------------------
CARD_W, CARD_H = 69.6, 30.0
FINGER_TOP = 2.70   # fingers are 0.35 x 2.55 mm pads from y = 0.15 to y = 2.70 (lib/README.md)

# Electrical length of bends on In3 (scripts/tuning.py; DESIGN.md section 13).  Fitted to HFSS runs of the tuning
# shapes used on this board (0.15 mm trace): a mitred vertex loses 0.020 mm per radian turned (k_mitre0 + k_mitre1 x w;
# 0.017 on the 0.10 mm CK legs), an arc
# 0.021 / 0.023 / 0.029 mm per radian at 0.20 / 0.15 / 0.10 mm centre radius (k_arc0 x w + k_arc1 x (r - r_lm));
# two antiparallel legs lose 1.9 % of their common length at a gap of 0.33 mm and 0.35 % at 0.57 mm.
BEND_MODEL = OrderedDict(k_mitre0=0.0120, k_mitre1=0.054, k_arc0=0.095, k_arc1=0.65, k_leg=0.20, leg_decay_mm=0.443)
# HFSS runs: the calibration shapes behind BEND_MODEL and one run of the tuning feature of every CA / CS net as planned.
# The plan adds, per net, what HFSS found for that net's own feature beyond the formula (bends closer than about
# 0.2 mm to each other interact, which a local formula cannot know).
HFSS_REF = os.path.join(REF, "hfss_cmd_tuning.json")

STACKUP = [
    # layer, type, thickness_mm, material, er, role
    ("F.Mask", "soldermask", 0.010, "LPI solder mask", 3.5, "removed over the edge fingers"),
    ("F.Cu", "copper", 0.035, "copper", None,
     "U1, the DQ0-7 comb, the last 5.8 mm of the RDQS pair into its fingers (microstrip over In1), front-side divider and reference parts; "
     "U2 (VDD2H), the DAC, the 1.2 V strip and its arm, the 3.3 V trunk, test pads; no ground flood (reference: In1)"),
    ("dielectric 1", "prepreg", 0.110, "PR2116", 4.29, "impedance-defining for F.Cu (unchanged)"),
    ("In1.Cu", "copper", 0.018, "copper", None, "solid GND plane, no tracks, reference for F.Cu"),
    ("dielectric 2", "core", 0.360, "FR4", 4.29, "thinned from 0.400 to 0.360 for the 1.20 mm card"),
    ("In2.Cu", "copper", 0.018, "copper", None,
     "GND plane, no tracks: upper reference of In3, solid under every In3 trace. One island: V1P2 under the VDD fingers (y 1.0..3.6) with "
     "a bridge north of the key slot and a tab under the foot of the 1.2 V strip, all south of y = 5.45, where In3 carries nothing "
     "(upstream convention: In2 = GND + power island)"),
    ("dielectric 3", "prepreg", 0.110, "PR2116", 4.29, "impedance-defining for In3 (unchanged)"),
    ("In3.Cu", "copper", 0.018, "copper", None,
     "high-speed stripline only: command bundle (CK, CA, CS) under U1; DQ read branches east of x = 37; RDQS pair as far as its "
     "via pair. Nothing else: no supply, no slow net"),
    ("dielectric 4", "core", 0.360, "FR4", 4.29, "thinned from 0.400 to 0.360 for the 1.20 mm card"),
    ("In4.Cu", "copper", 0.018, "copper", None,
     "GND plane; VDD2H island only under U1 rows K..AA and toward U2; solid GND under the whole finger row, under the command "
     "bundle, under the In3 read branches and under the B.Cu pairs and back-side finger stubs"),
    ("dielectric 5", "prepreg", 0.110, "PR2116", 4.29, "impedance-defining for B.Cu (unchanged)"),
    ("B.Cu", "copper", 0.035, "copper", None,
     "WCK pair (microstrip over In4), seven read finger tracks, decoupling, back-side divider parts; U3 (VDD1) and U4 (VDDQ) with their parts, the VDD1 / VDDQ "
     "lanes and rails, VDDQ / VDD2H pours under U1, RESET_n with its two parts at finger 232; no ground flood (reference: In4)"),
    ("B.Mask", "soldermask", 0.010, "LPI solder mask", 3.5, "removed over the edge fingers"),
]


def stackup_json():
    rows = [OrderedDict(layer=l, type=t, thickness_mm=th, material=m, er=er, role=role)
            for l, t, th, m, er, role in STACKUP]
    total = round(sum(r["thickness_mm"] for r in rows), 3)
    bare = round(total - 0.020, 3)
    return OrderedDict(
        layers=rows,
        total_thickness_with_mask_mm=total,
        thickness_at_fingers_no_mask_mm=bare,
        finger_finish_allowance_mm=0.008,
        thickness_at_fingers_with_hard_gold_mm=round(bare + 0.008, 3),
        ddr4_sodimm_limit_mm=[1.10, 1.30],
        generic_fab_window_pm10pct_mm=[round(bare * 0.9, 3), round(bare * 1.1, 3)],
        required_fab_tolerance="finished thickness measured over the gold fingers 1.20 +/-0.10 mm "
                               "(+/-8.3 %); a generic +/-10 % window (1.073..1.311 mm) is 0.027 / 0.011 mm "
                               "outside the standard at the two ends, so the fab note must state the absolute tolerance",
        upstream_stackup_for_comparison_mm=OrderedDict(core=0.400, total_with_mask=1.292, at_fingers=1.272),
        outer_copper_note="0.035 mm is the nominal of the upstream table. The via-in-pad fill-and-cap plating "
                          "(IPC-4761 type VII) can leave up to about 0.055 mm on F.Cu / B.Cu, fingers included: "
                          "the card is then up to 0.04 mm thicker than the table",
        thickness_at_fingers_with_cap_plating_mm=round(bare + 0.008 + 2 * 0.020, 3),
        impedance_control="controlled impedance, fab may adjust widths by +/-0.02 mm: In3 0.15 mm 46 ohm; In3 pair "
                          "0.10 / 0.10 mm 85 ohm differential; F.Cu / B.Cu 0.20 mm 46 ohm; F.Cu / B.Cu pair 0.10 / 0.10 mm "
                          "92 ohm differential (WCK on B.Cu, RDQS on F.Cu; all +/-10 %). The outer values depend on the finished copper and mask "
                          "(design/impedance_results.json, outer_copper_and_mask_cases): 0.20 mm = 46.7 / 45.6 / 44.8 ohm "
                          "and the pair = 97.6 / 91.8 / 86.7 ohm for 0.035 Cu + 0.010 mask / 0.040 + 0.020 / 0.055 + 0.020",
        copper_finish="hard gold over nickel on the fingers (30 uin min), ENIG elsewhere",
        via_in_pad="filled and capped (IPC-4761 type VII) under U1 and at the ten command divider node vias (0.40 / 0.20, "
                   "y = 5.85): their pads are 0.05 - 0.16 mm from 0201 solder lands. The ten node vias of the data side (DQ0-7, "
                   "WCK_P / WCK_N, y = 6.00) are plain vias: their pads are 0.25 mm from the lands",
        edge="0.25 mm x 45 deg chamfer only (no deep bevel); finger copper starts <= 0.30 mm from the edge",
    )


WR_PITCH_RULE = DATA.PITCH     # 0.55: bundle pitch of the DQ lines on F.Cu and of their read branches on In3 (the upstream figure)

RULES = OrderedDict(
    units="mm",
    board=OrderedDict(min_clearance=0.09, min_track=0.09, min_via_diameter=0.40, min_through_hole=0.15,
                      min_annular_ring=0.10, hole_to_hole=0.25, hole_clearance=0.20,
                      copper_to_edge=0.15, copper_to_edge_note="0.15 only so that the finger pads pass; everything "
                      "else keeps 0.30 mm from the outline (see floorplan.plane_plan.pull_back)",
                      blind_buried_vias=False, microvias=False,
                      angles="0/45/90/135 degrees only; rounded 90-degree arcs only inside tuning meanders"),
    vias=OrderedDict(
        through_signal=OrderedDict(diameter=0.40, drill=0.20,
                                   use="signal vias outside U1 (divider nodes, read vias, finger vias) and every GND via "
                                       "inside the divider fields (command fence, shunt and capacitor GND vias)"),
        through_power=OrderedDict(diameter=0.45, drill=0.20, use="supply vias and the GND vias of parts outside the divider fields"),
        via_in_pad=OrderedDict(diameter=0.40, drill=0.15, use="under U1 only, filled and capped"),
    ),
    impedance_solver="scripts/impedance.py (2-D finite differences, er 4.29, prepreg 0.11, core 0.36, outer copper "
                     "0.040 and mask 0.020 as design values); results in design/impedance_results.json including the "
                     "outer-layer corners 0.035 Cu + 0.010 mask and 0.055 Cu + 0.020 mask; accuracy about +/-5 %",
    delay_ps_per_mm=OrderedDict({"In3.Cu": 6.91, "F.Cu/B.Cu w0.15": 6.01, "F.Cu/B.Cu w0.20": 6.07,
                                 "F.Cu/B.Cu w0.09": 5.94, "F.Cu/B.Cu pair 0.10/0.10 (odd mode)": 5.69,
                                 "through via (per mm of barrel)": 7.0}),
    bend_model=OrderedDict(
        k_mitre0=BEND_MODEL["k_mitre0"], k_mitre1=BEND_MODEL["k_mitre1"], k_arc0=BEND_MODEL["k_arc0"], k_arc1=BEND_MODEL["k_arc1"],
        k_leg=BEND_MODEL["k_leg"], leg_decay_mm=BEND_MODEL["leg_decay_mm"],
        layers=["In3.Cu"],
        mitre="a mitred vertex is electrically shorter than its centre line by (k_mitre0 + k_mitre1 x w) x (angle turned, "
              "rad): 0.0201 mm per radian on the 0.15 mm trace (0.016 mm = 0.11 ps per 45-degree vertex), 0.0174 mm per "
              "radian on the 0.10 mm CK legs",
        arc="an arc of centre radius r by (k_arc0 x w + k_arc1 x (r - r_lm)) x (angle turned, rad), r_lm = w / ln((r + w/2) / "
            "(r - w/2)): 0.021 / 0.023 / 0.029 mm per radian at r = 0.20 / 0.15 / 0.10 mm (0.036 mm = 0.25 ps per 90 degrees "
            "at the 0.15 mm used)",
        legs="two adjacent antiparallel legs of one net lose 2 x k_leg x exp(-pi x gap / leg_decay_mm) x (their common "
             "length): gap = copper to copper; 1.9 % of the common length at 0.33 mm (leg pitch 0.48), 0.35 % at 0.57 mm "
             "(leg pitch 0.72, the one used)",
        calibration="fitted to HFSS (2026 R1, lossless, er 4.29, 0.018 mm copper as PEC between two ground planes 0.488 mm "
                    "apart, wave ports, adaptive at 8 GHz to delta S 0.002, phase of S21 at 0.5 .. 10 GHz against a straight "
                    "line; the delay is flat over that band) runs of the tuning shapes of this board; results and residuals "
                    "in DESIGN.md section 13 and design/ref/hfss_cmd_tuning.json. The model applies to every bend of an "
                    "In3 trace: the tuning features, the 0.4 mm jogs into the balls, the CK split and the CK_P hook",
        per_feature="the formula is local: bends that follow each other within about 0.2 mm interact. Each CA / CS net "
                    "therefore carries the difference between an HFSS run of its own tuning feature and the formula "
                    "(groups[CMD].tuning.per_net[net].hfss.residual_mm, at most 0.06 mm = 0.4 ps), added to the "
                    "electrical length of that feature by the plan, the router and the gate",
        used_by="scripts/tuning.py: build_netlist.py (targets, CK_P hook, tuning plan), scripts/pcb/s20_cmd.py (solver); "
                "scripts/data_side.py (every mitred vertex of the data side on any layer, the legs of the RDQS skew bump); "
                "scripts/pcb/check_pcb.py (gates 6 and 7 measure the board with it)"),
    netclasses=OrderedDict(
        CMD_SE=OrderedDict(
            nets=["CA0", "CA1", "CA2", "CA3", "CA4", "CA5", "CA6", "CS0"],
            layers={"In3.Cu": OrderedDict(width=0.15, z0_ohm=45.6)},
            clearance=0.12, clearance_layer="In3.Cu", clearance_other_layers=0.10, bundle_pitch=0.80,
            tuning=OrderedDict(amplitude_max=0.80, amplitude_min=0.30, radius_min=0.15, leg_pitch_min=0.48,
                               min_gap_to_neighbour=0.33,
                               note="length is added with few, large bends: a parallel shift of the trace (two 45-degree "
                                    "runs, no 90-degree bend) and, where that is not enough, rounded bumps on the shifted "
                                    "run: legs 0.48 mm apart (gap 0.33 mm = 3.0 h, h = 0.11 mm to In2), centre radius "
                                    ">= 0.15 mm (the trace width), amplitude >= 0.30 mm (2 w). At least 0.33 mm (3 h) "
                                    "to the neighbouring trace everywhere. Shape per net: groups[CMD].tuning.per_net; "
                                    "delays are computed with rules.bend_model"),
            note="one trace per 0.8 mm BGA column channel; gap 0.65 mm = 4.3 w, Kb 0.2 %; "
                 "matches the divider back impedance 68 || (91 + 39) = 44.6 ohm; 0.125 mm to the via pads of the "
                 "channel (0.40 mm pads on 0.8 mm), hence class clearance 0.12 on In3. On the outer layers the class "
                 "keeps 0.10 mm: the F.Cu escape of DQ7 passes row G (CA0 ball) in the "
                 "0.29 mm row channel, 0.10 mm from the ball lands"),
        CMD_CK=OrderedDict(
            nets=["CK_P", "CK_N"],
            layers={"In3.Cu": OrderedDict(width=0.10, gap=0.10, zdiff_ohm=84.6, zodd_ohm=42.3)},
            clearance=0.12, clearance_layer="In3.Cu", clearance_other_layers=0.10,
            pair_to_other_track_min=0.50, pair_to_via_pad_min=0.40, intra_pair_skew_ps=1.0,
            max_uncoupled_mm=3.5, max_skew_mm=0.30,
            uncoupled_note="the DRC adds up every layer: per leg 0.62 mm (node via to the pair) and 1.63 / 1.74 mm (split "
                           "at row H) on In3, plus the two links of the divider column on the outer layers, 0.53 mm "
                           "(series pad to node via) and 0.48 mm (node via to shunt pad): 3.26 / 3.37 mm. On In3 alone "
                           "2.25 / 2.37 mm, which scripts/pcb/check_pcb.py holds to 2.5 mm",
            note="tightly coupled from the node vias to row G, centred on ball column 9 (via-in-pads of A9..G9 "
                 "omitted): 0.45 mm to the via pads of columns 8 and 10. At row H the legs split around the H9 via "
                 "(0.15 mm to the pads of H8 / H9 / H10): CK_N goes on to J9, CK_P hooks back into H9 from the "
                 "north-east, which makes up the ball-row difference without a meander; "
                 "per-leg back impedance 68 || (91 + 33) = 43.9 ohm"),
        HOST_SE=OrderedDict(
            nets=["CA0_F", "CA1_F", "CA2_F", "CA3_F", "CA4_F", "CA5_F", "CA6_F", "CS0_F", "CK_F_P", "CK_F_N",
                  "WCK_F_P", "WCK_F_N", "RESET_N_F"] + ["DQ%d_WF" % i for i in range(8)],
            layers={"F.Cu": OrderedDict(width=0.20, z0_ohm=45.6, min_width=0.15), "B.Cu": OrderedDict(width=0.20, z0_ohm=45.6, min_width=0.15)},
            clearance=0.10, max_length=4.0, finger_neck_width=0.15, finger_neck_to_y=DATA.Y_STUB_WIDE,
            note="1.2 V side: finger to series resistor on the finger's own layer. Command stubs: 0.20 mm, straight "
                 "lead-out past the row of finger vias (y = 3.05), then one 45-degree run onto the resistor pad. Stubs "
                 "of the data side (write, WCK, RESET_n): 0.15 mm from the finger through the row of the finger vias "
                 "(to y = 3.45: at 0.20 mm the stub of finger 161 would stand 0.075 mm from two V1P2 finger vias), "
                 "0.20 mm from there (floorplan.finger_stubs)"),
        WR_DQ=OrderedDict(
            nets=["DQ%d" % i for i in range(8)],
            layers={"F.Cu": OrderedDict(width=0.15, z0_ohm=52.4, neck_width=0.09, neck_z0_ohm=64.2, neck_max_length=8.0,
                                        max_width=0.20)},
            clearance=0.10, bundle_pitch=WR_PITCH_RULE, gap_min_outside_u1=0.40, via_pad_clearance_outside_u1=0.20,
            t_to_resistor_max_mm=2.0, divider_to_card_edge_max_mm=6.0, write_plan_tolerance_ps=1.0,
            note="ball to divider node without a via at the ball: 0.09 mm neck inside the land field of U1, 0.15 mm line "
                 "on its level, one parallel 45-degree fan, tooth down the column axis into the node via = the T. Bundle "
                 "pitch 0.55 mm (gap 0.40 mm = 3.6 h, h = 0.11 mm to In1), the upstream figure, wherever two lines run "
                 "side by side outside the land field (0.566 mm between the fan diagonals, 0.80 mm between the teeth). "
                 "max_width: the links series resistor - node via - shunt and the F.Cu read finger track of finger 215 "
                 "are 0.20 mm; t_to_resistor_max_mm: copper from the node via to the land of the series resistor and "
                 "of the shunt (the one on the far face through the barrel); divider_to_card_edge_max_mm: every land "
                 "of the sixteen divider parts; write_plan_tolerance_ps: each net against its own planned total (the "
                 "group is not matched on the card: groups[WR])"),
        WR_WCK=OrderedDict(
            nets=["WCK_P", "WCK_N"],
            layers={"B.Cu": OrderedDict(width=0.10, gap=0.10, zdiff_ohm=91.8, zodd_ohm=45.9),
                    "F.Cu": OrderedDict(width=0.20, note="only the links of the two divider columns, as wide as those of the DQ columns")},
            clearance=0.10, pair_to_other_min=0.80, pair_to_other_min_in_u1_escape=0.10,
            intra_pair_skew_ps=1.0, max_uncoupled_mm=4.0, max_uncoupled_b_cu_mm=3.0, max_skew_mm=0.50,
            f_cu_links_to_other_tracks_min=0.80,
            skew_note="max_skew_mm is the DRC limit on the LENGTH difference of the nets WCK_P / WCK_N (vias included). "
                      "There is no skew bump: WCK_N goes straight into its node via and WCK_P makes one 45-degree run "
                      "of 1.0 mm to its own, which takes up the ball-row difference between E4 and D5. The electrical "
                      "match (intra_pair_skew_ps, host included) is measured by gate 7 of scripts/pcb/check_pcb.py",
            uncoupled_note="per leg about 0.35 mm at the balls and 1.4 / 1.0 mm at the split above the node vias, plus the "
                           "two F.Cu links of each column (1.36 mm), which the DRC adds: held to max_uncoupled_mm; "
                           "the board gate holds each B.Cu leg to max_uncoupled_b_cu_mm",
            note="B.Cu pair from the via-in-pads of E4 / D5 on the one diagonal of via-free balls (D3 - C2 - B1), east as the "
                 "lowest lane, south into its two divider columns; F.Cu only as the 0.20 mm links series resistor - "
                 "node via - shunt of the two columns. The GND vias of the two shunts are mirror images about the axis of "
                 "the B.Cu pair (0.55 mm on either side). f_cu_links_to_other_tracks_min: those links to the DQ4 tooth"),
        RD_DQ=OrderedDict(
            nets=[], same_nets_as="WR_DQ",
            layers={"In3.Cu": OrderedDict(width=0.15, z0_ohm=45.6),
                    "F.Cu": OrderedDict(width=0.20, z0_ohm=45.6),
                    "B.Cu": OrderedDict(width=0.20, z0_ohm=45.6)},
            clearance=0.10, bundle_pitch=DATA.PITCH, in3_gap_min=0.40, in3_gap_drc=0.39, in3_backward_crosstalk_per_neighbour=0.022,
            via_pad_clearance=0.20, in3_to_foreign_via_pad_min=0.27, return_via_max_mm=1.0, return_via_min_mm=0.55,
            read_plan_tolerance_ps=DATA.READ_PLAN_TOL, read_end_crossings_max=0,
            return_via_applies_to="every read via, both vias of the RDQS pair and the node vias of the outer columns (DQ4, DQ5, DQ1, DQ7)",
            node_return_via_exception=OrderedDict(
                nets=["DQ0", "DQ3", "DQ2", "DQ6"], max_mm=2.1,
                why="the node vias of the four inner columns stand at 0.8 mm pitch with the F.Cu tooth on the axis and the In3 riser in the "
                    "lane between: a 0.40 mm via between two teeth would leave 0.125 mm to each (rule 0.27). Their return path is the shunt "
                    "GND via south of the row (1.38 .. 2.04 mm) and the unbroken In1 / In2 / In4 planes (0.2 mm webs between the antipads). "
                    "Accepted at this data rate (100 Mb/s target); widening the comb for it would lengthen every line"),
            note="the read branch of the nets DQ0-7. There is no part between the ball and the read finger, so line, "
                 "divider node, read branch and read finger of a bit are ONE net, DQx, and KiCad has one class for it "
                 "(WR_DQ); this entry holds the rules of the read branch and is written to the .kicad_dru for the "
                 "layers the write class does not use (In3.Cu, B.Cu). In3 from the node via: one 45-degree run into "
                 "the lane 0.40 mm east of the column, north to its level, east at 0.55 mm pitch (gap 0.40 mm = "
                 "3.6 h), one 45-degree ramp of the whole bundle, east to a via on its own level above the read "
                 "finger; from that via straight down to the finger on the finger's own outer layer, 0.20 mm. "
                 "in3_gap_drc: the DRC value leaves 0.01 mm for the rounding of the ramp vertices; the board gate "
                 "holds in3_gap_min. in3_to_foreign_via_pad_min: the upstream figure is 0.275 (the DQ6 line past the "
                 "DQ7 node via). return_via_max_mm: a GND via within this distance of every via named in return_via_applies_to, "
                 "return_via_min_mm centre to centre (0.15 mm between the pads); node_return_via_exception: the four inner node vias. "
                 "read_plan_tolerance_ps: each net against its own planned total (the read group is not matched on the card: groups[RD_P]); "
                 "read_end_crossings_max: no read finger track crosses an In3 read line of another bit (the fingers are taken in the order "
                 "of the bundle)"),
        RD_RDQS=OrderedDict(
            nets=["RDQS_P", "RDQS_N"],
            layers={"In3.Cu": OrderedDict(width=0.10, gap=0.10, zdiff_ohm=84.6, zodd_ohm=42.3),
                    DATA.RDQS_OUTER: OrderedDict(width=0.10, gap=0.10, zdiff_ohm=91.8, zodd_ohm=45.9)},
            clearance=0.10, pair_to_other_min=0.80, pair_to_read_via_min=0.30,
            intra_pair_skew_ps=1.0, max_uncoupled_mm=4.5, max_skew_mm=0.50,
            match_to_dq_ps=None, plan_tolerance_ps=1.0, island_edge_min=1.0, bump_leg_gap_min=0.33,
            uncoupled_note="what the DRC adds up per leg: the ball end (C4 lies one row beyond B3), the skew bump of "
                           "RDQS_P (RDQS_N runs alone past it), the split to the two vias of the pair and the 0.40 mm link "
                           "to the 150 ohm load",
            note="RDQS_P / RDQS_N: In3 pair from the via-in-pads of B3 / C4 through the row channel B|C, east between the "
                 "two lower and the six upper F.Cu lines, on ONE 45-degree run to the north-east across the six diagonals "
                 "of the F.Cu fan (90 degrees to them), east above every read via, on ONE 45-degree run of 3.0 mm down to "
                 "the axis of the host pair DQS6 and south to one via pair just above fingers 219 / 221 (y = 8.50, the lowest a "
                 "through via can stand: 0.90 mm from the VREF_RD bus on B.Cu), like the eight read DQ lines; then 5.8 mm of F.Cu "
                 "straight down into the fingers, 1.075 mm EAST of the last read finger track (finger 215): the pair crosses no "
                 "read line. The two 150 ohm loads stand "
                 "beside the coupled pair on 0.40 mm links in the row of the 39 ohm resistors, on F.Cu like the pair, GND "
                 "on the finger vias of the VSS pairs 217 / 218 and 222 / 223. "
                 "One skew bump on RDQS_P, on In3 beside U1, where the skew arises. No series part, one net per leg. "
                 "match_to_dq_ps: none, the read group is not matched on the card and the strobe is not matched to it; "
                 "plan_tolerance_ps: each leg against its own planned total; the offset of the strobe from the centre of "
                 "the read group is in groups[RD_RDQS] and in design/gateware_deskew.json; pair_to_read_via_min: the pair "
                 "past the pad of a read via or of a return via; bump_leg_gap_min: 3 h"),
        VREF=OrderedDict(
            nets=["VREF_RD", "DAC_OUT"] + ["VREF_N%d" % i for i in range(8)],
            layers={"F.Cu": OrderedDict(width=0.15), "B.Cu": OrderedDict(width=0.15)},
            clearance=0.20, land_clearance_0201=0.17, finger_via_row_clearance=0.12, max_stub_length=5.0,
            keep_from_switching_nets=0.90, keep_from_switching_nets_east_end=0.40,
            switching_classes=["CMD_SE", "CMD_CK", "HOST_SE", "WR_DQ", "WR_WCK", "RD_RDQS"],
            note="VREF_Nx: finger to 39 ohm, at most 5 mm; never routed across the card. 0.20 mm to everything, 0.90 mm "
                 "from a VREF track to any track or via of a switching class on the same layer. Three places bend that, "
                 "each with its own rule: the two lands of an 0201 are 0.18 mm apart (land_clearance_0201); the N stub "
                 "of finger 211 passes the finger via of 209 / 210 and the tie of 213 at 0.14 mm "
                 "(finger_via_row_clearance, rule area finger_via_row); the bus changes side between the read tracks of "
                 "fingers 212 and 215 (keep_from_switching_nets_east_end, rule area vref_east_end: 0.405 mm as built, the "
                 "track of 212 displaced 0.235 mm west and that of 215 0.175 mm east of their finger axes for it)"),
        POWER=OrderedDict(
            nets=["V1P2", "V3P3", "VDD1", "VDD2H", "VDDQ", "GND"],
            layers={"any": OrderedDict(width=0.25)}, clearance=0.10,
            note="pours and planes; tracks at least 0.25 mm (the finger-to-via ties; 0.20 mm inside the BGA field), 0.30 mm and more elsewhere"),
        Default=OrderedDict(nets=["*"], layers={"any": OrderedDict(width=0.15)}, clearance=0.09),
    ),
    pair_naming="KiCad recognises a differential pair only by the suffixes _P / _N (or + / -) at the end of the net "
                "name. The three pairs are therefore named CK_P / CK_N, WCK_P / WCK_N and RDQS_P / RDQS_N "
                "(_P = JEDEC _t, _N = JEDEC _c) and their finger-side nets CK_F_P / CK_F_N and WCK_F_P / WCK_F_N "
                "(RDQS has no finger-side net: no series part), so that the pair router, the diff_pair_gap / diff_pair_uncoupled / skew "
                "constraints and the pair tuner work. WCK1_T / WCK1_C (unused byte, static) are not a routed pair "
                "and keep their names",
    spacing_rules=[
        # written to the .kicad_dru by scripts/pcb/s00_board.py; 'outside_u1' = neither item touches the U1 courtyard
        OrderedDict(name="CK_pair_to_other_tracks", a_class="CMD_CK", a_type="Track", b_type="Track", layer="In3.Cu",
                    b_not_classes=["CMD_CK", "POWER"], min=0.50, where="outside_u1",
                    note="In3 only, where the pair runs: on the outer layers CK_P / CK_N are the 0.5 mm links of their "
                         "own divider columns, 0.44 mm from their own finger stubs across the series resistor"),
        OrderedDict(name="CK_pair_to_via_pads", a_class="CMD_CK", a_type="Track", b_type="Via",
                    b_not_classes=["CMD_CK"], min=0.40, where="outside_u1", layer="In3.Cu"),
        OrderedDict(name="WCK_pair_to_other_tracks", a_class="WR_WCK", a_type="Track", b_type="Track", layer="B.Cu",
                    b_classes=["RD_RDQS", "HOST_SE", "WR_DQ", "CMD_SE", "CMD_CK"], min=0.80, where="outside_u1",
                    note="0.80 mm everywhere outside U1 (0.925 mm to the B.Cu link of the DQ4 node, the nearest track)"),
        OrderedDict(name="RDQS_pair_to_other_tracks_In3", a_class="RD_RDQS", a_type="Track", b_type="Track", layer="In3.Cu",
                    b_classes=["WR_WCK", "HOST_SE", "CMD_SE", "CMD_CK", "WR_DQ"], min=0.80, where="outside_u1",
                    note="on In3, where the pair runs from U1 to its via pair above fingers 219 / 221: 0.80 mm to every other In3 track outside U1 "
                         "(the read branches: 0.85 mm to the end of the DQ4 line at its read via, the nearest; the command bundle lies inside U1 and south of it)"),
        OrderedDict(name="RDQS_pair_to_other_tracks", a_class="RD_RDQS", a_type="Track", b_type="Track", layer=DATA.RDQS_OUTER,
                    b_classes=["WR_WCK", "HOST_SE", "CMD_SE", "CMD_CK"], min=0.80, where="outside_u1",
                    note="on the outer layer of the pair (F.Cu: its fingers 219 / 221 are on the front; only the drop below the via pair). The DQ tracks on that layer "
                         "(the read finger track of finger 215, the lines and teeth of the comb) have their own rule (RDQS_pair_to_read_tracks)"),
        OrderedDict(name="RDQS_pair_to_read_tracks", a_class="RD_RDQS", a_type="Track", b_type="Track", layer=DATA.RDQS_OUTER,
                    b_classes=["WR_DQ"], min=0.80, where="outside_u1", a_not_enclosed_by_area=["rdqs_termination", "rdqs_via_pair"],
                    note="the F.Cu drop of the pair against the F.Cu read finger track of finger 215 (0.825 mm), the only "
                         "DQ track beside it: the drop is the eastmost wire of the read end. Left out: an RDQS track that lies wholly inside the rule area "
                         "'rdqs_termination' (floorplan.rule_areas), i.e. the two 0.40 mm load links; the western one ends 0.425 mm from that "
                         "track. The legs themselves are held to 0.80 mm inside the area too: the earlier form of this rule left out "
                         "every track that touched the area, which was the whole drop of each leg (found by the rehearsal's negative test). Left out "
                         "likewise: an RDQS track wholly inside the area 'rdqs_via_pair', i.e. the 0.28 mm on which each F.Cu leg leaves the centre of its via: "
                         "the western one lies inside and beside the via pad, 0.625 mm from the track of finger 215 (the pad itself 0.475 mm; pads are under no "
                         "spacing rule, as at the read vias)"),
        OrderedDict(name="VREF_to_switching_nets", a_class="VREF", a_type="Track", b_type="any",
                    b_classes=["CMD_SE", "CMD_CK", "HOST_SE", "WR_DQ", "WR_WCK", "RD_RDQS"], min=0.90, a_not_in_area="vref_east_end"),
        OrderedDict(name="VREF_to_switching_nets_east_end", min=0.40,
                    condition="A.NetClass == 'VREF' && A.Type == 'Track' && (B.Type == 'Track' || B.Type == 'Via') && (B.NetClass == 'WR_DQ') "
                              "&& A.Net != B.Net && A.intersectsArea('vref_east_end')",
                    note="VREF_RD changes side between the read tracks of fingers 212 (B.Cu) and 215 (F.Cu): 0.40 mm track to "
                         "track there instead of 0.90 (0.405 mm as built on both faces; the alternative, a hop of the bus on In3, was not taken)"),
        OrderedDict(name="VREF_0201_lands", min=0.17,
                    condition="A.NetClass == 'VREF' && A.Type == 'Pad' && !A.memberOfFootprint('J1')",
                    note="the two lands of an 0201 are 0.18 mm apart: every 0201 on a VREF net would break the class value 0.20"),
        OrderedDict(name="VREF_N_stub_in_the_finger_via_row", min=0.12,
                    condition="A.NetClass == 'VREF' && A.Type == 'Track' && A.intersectsArea('finger_via_row') && (B.NetClass == 'POWER')",
                    note="N stub of finger 211 between the finger via of 209 / 210 and the tie of 213: 0.14 mm (geometric limit 0.1375)"),
        OrderedDict(name="RD_DQ_to_RD_DQ_In3", a_class="WR_DQ", a_type="Track", b_type="Track", layer="In3.Cu",
                    b_classes=["WR_DQ"], min=0.39,
                    note="the read branches of DQ0-7 (the only In3 copper of these nets): 0.40 mm = 3.6 h by plan "
                         "(rules.netclasses.RD_DQ.in3_gap_min, held by the board gate); 0.39 in the DRC for the rounding of the ramp vertices"),
        OrderedDict(name="WR_DQ_to_WR_DQ_F_Cu", a_class="WR_DQ", a_type="Track", b_type="Track", layer="F.Cu",
                    b_classes=["WR_DQ"], min=0.39, where="outside_u1",
                    note="the F.Cu bundle (lines, fan, teeth) and every other F.Cu track of DQ0-7 outside the U1 courtyard: 0.40 mm by plan "
                         "(rules.netclasses.WR_DQ.gap_min_outside_u1, held by the board gate); 0.39 in the DRC for the rounding of the fan "
                         "vertices. Before, only the board gate held this gap: a wrong hand edit in KiCad raised no DRC item"),
        OrderedDict(name="WR_DQ_F_Cu_to_foreign_via", min=0.27, layer="F.Cu",
                    condition="A.NetClass == 'WR_DQ' && A.Type == 'Track' && B.Type == 'Via' && A.Net != B.Net "
                              "&& !A.intersectsCourtyard('U1') && !A.intersectsArea('finger_via_row')",
                    note="an F.Cu line, tooth or link of DQ0-7 to the pad of a via of another net (return vias beside the teeth: 0.275 mm; the "
                         "VREF_RD bus via 0.28 mm from the F.Cu read track of finger 215); "
                         "not in the row of the finger vias, where the F.Cu read track of finger 215 passes the VSS finger vias at the class clearance"),
    ],
    changes_of_the_data_replan=[
        # every rule value that the re-plan of the data side changed against the board before it, with its reason
        OrderedDict(rule="WCK_pair_to_other_tracks", before=0.75, now=0.80, kind="restored",
                    why="no other B.Cu track within 0.9 mm of the pair outside U1 any more (upstream keeps 0.87 on 95 %)"),
        OrderedDict(rule="WR_WCK_pair_skew (max length difference)", before=0.60, now=0.50, kind="restored",
                    why="no skew bump: the legs differ by 0.26 mm"),
        OrderedDict(rule="WR_WCK_pair_coupling (max uncoupled)", before=4.5, now=4.0, kind="restored",
                    why="3.2 / 3.5 mm per leg as counted by the DRC"),
        OrderedDict(rule="RD_RDQS_pair_coupling (max uncoupled)", before=7.0, now=4.5, kind="restored in part",
                    why="the pair runs coupled into the fingers; what is left is the ball end, the via pair, the bump and the load "
                        "links: the DRC counts 4.39 mm (upstream 2.6 .. 5.0 per leg). The rehearsed comb needed 4.75: its legs ran 0.3 mm "
                        "straight into each via of the pair; as built each leg reaches its via on one 45-degree run"),
        OrderedDict(rule="HOST_SE_F_Cu_width / HOST_SE_B_Cu_width (min)", before=0.20, now=0.15, kind="relaxed",
                    why="0.15 mm neck of the write, WCK and RESET_n finger stubs through the row of the finger vias (upstream track: 0.15)"),
        OrderedDict(rule="VREF_0201_lands", before="class clearance 0.20", now=0.17, kind="new",
                    why="the 16 reference parts are 0201: their two lands are 0.18 mm apart"),
        OrderedDict(rule="VREF_N_stub_in_the_finger_via_row", before="class clearance 0.20", now=0.12, kind="new",
                    why="N stub of finger 211: 0.14 mm to the finger via of 209 / 210 and the tie of 213"),
        OrderedDict(rule="VREF_to_switching_nets_east_end", before=0.90, now=0.40, kind="relaxed in the area vref_east_end",
                    why="the bus via between the read tracks of fingers 212 / 215"),
        OrderedDict(rule="RDQS_pair_to_read_tracks: area rdqs_termination", before="x 51.95 .. 53.9, y 3.2 .. 8.15", now="x %.3f .. %.3f, y 4.0 .. 5.6, on %s"
                                                                                                                         % (DATA.X_RDQS_DOWN - 0.975, DATA.X_RDQS_DOWN + 0.975, DATA.RDQS_OUTER),
                    kind="kept, smaller", why="the loads stand in the row of the 39 ohm resistors on the axes of the VSS finger pairs beside the drop and take their GND from "
                                              "those finger vias; only their two 0.40 mm side links are inside"),
        OrderedDict(rule="RD_DQ_to_RD_DQ_In3", before=0.33, now=0.39, kind="tightened",
                    why="In3 bundle pitch 0.50 -> 0.55 (gap 0.40, the board gate holds 0.40)"),
        OrderedDict(rule="WR_DQ bundle pitch (gate)", before=0.45, now=0.55, kind="restored", why="upstream figure; gap 0.40 mm"),
        OrderedDict(rule="RDQS_pair_to_read_tracks: exemption", before="every RDQS track that touches the area rdqs_termination (= the whole drop)",
                    now="only an RDQS track wholly inside the area (the two load links)", kind="tightened",
                    why="the DRC did not hold the 0.80 mm of the drop against the read tracks; a negative test on the drop now proves it does"),
        OrderedDict(rule="WR_DQ_to_WR_DQ_F_Cu", before="board gate only", now=0.39, kind="new",
                    why="the F.Cu bundle gap was held only by gate 7; the DRC now reports it too (the rehearsal has a negative test for it)"),
        OrderedDict(rule="WR_DQ_F_Cu_to_foreign_via", before="board gate only", now=0.27, kind="new",
                    why="F.Cu DQ copper to the pad of a foreign via, as on In3"),
        OrderedDict(rule="RDQS_pair_to_other_tracks / RDQS_pair_to_read_tracks: layer", before="B.Cu", now=DATA.RDQS_OUTER, kind="moved with the pair (read end without crossings)",
                    why="RDQS takes the host pair DQS6 (fingers 219 / 221, both on the front), east of every read finger: its outer-layer part is on F.Cu"),
        OrderedDict(rule="vref_east_end (rule area)", before="x 55.7 .. 58.1", now="x 55.6 .. 57.4", kind="moved",
                    why="the bus via stands at x = %.2f, between the read track of finger 212 (B.Cu, displaced 0.235 mm west) and that of finger 215 "
                        "(F.Cu, 0.175 mm east); the value of the rule (0.40) is unchanged, 0.405 mm as built on both faces (before: 0.425)" % DATA.BUS_VIA[0]),
        OrderedDict(rule="RD_P group tolerance", before="+/-10 ps to one common total", now="+/-1 ps of each net to its own planned total", kind="changed",
                    why="read end without crossings: the read group is not matched on the card (+/-56.8 ps), the per-bit IDELAY of the FPGA closes it"),
        OrderedDict(rule="RD_RDQS match to the read DQ", before="+/-10 ps", now="none; each leg +/-1 ps to its own planned total, P to N +/-1 ps", kind="removed",
                    why="there is no matched group to match the strobe to; its offset from the centre of the group is in the deskew table"),
        OrderedDict(rule="RDQS_pair_to_other_tracks_In3", before="none (the F.Cu rules covered the 20 mm of the pair on F.Cu)", now=0.80, kind="new (final fix pass)",
                    why="the pair stays on In3 down to a via pair just above its fingers (user: stay on In3 until just above the fingers, short F.Cu drop): "
                        "the 0.80 mm of the pair to every other track now has to hold on In3"),
        OrderedDict(rule="crossing budget data x data / high-speed x VREF", before="80 / 10", now="52 / 9", kind="tightened (regression lock)",
                    why="read end without crossings: 20 read-end crossings and 8 crossings of the RDQS drop are gone; the F.Cu track of finger 215 crosses VREF once"),
        OrderedDict(rule="WR_WCK F.Cu link width", before=0.40, now=0.20, kind="changed",
                    why="the links of the WCK columns are as wide as those of the DQ columns (25 ohm stubs of 1.36 mm removed; the return via "
                        "between the WCK_P and DQ4 columns keeps 0.275 mm to both)"),
    ],
)

# ----------------------------------------------------------------------------------------------
# 2. U1 ball map (JEDEC JESD209-5C 16.3.2 / Micron Y42M Figure 11, 315-ball 2CHx32, top view, A1 top-left)
# ----------------------------------------------------------------------------------------------
ROWS = ["A", "B", "C", "D", "E", "F", "G", "H", "J", "K", "L", "M", "N", "P", "R", "T", "U", "V", "W", "Y", "AA"]
BALLMAP = """
A  NC      NC      VDDQ      DMI0_A    VSS       VDD2L   VDD2H   VDD2H   VDD2H   VDD2L   VSS       DMI1_A    VDDQ      NC      NC
B  NC      VDDQ    RDQS0_t_A VSS       DQ4_A     VDD2L   VDD2H   VSS     VDD2H   VDD2L   DQ12_A    VSS       RDQS1_t_A VDDQ    NC
C  VDD1    DQ1_A   VDDQ      RDQS0_c_A VSS       DQ5_A   VDD2H   VSS     VDD2H   DQ13_A  VSS       RDQS1_c_A VDDQ      DQ9_A   VDD1
D  DQ0_A   VSS     DQ3_A     VDDQ      WCK0_c_A  VSS     VSS     VDD2H   VSS     VSS     WCK1_c_A  VDDQ      DQ11_A    VSS     DQ8_A
E  VSS     DQ2_A   VSS       WCK0_t_A  VDDQ      DQ6_A   VDD2H   VSS     VDD2H   DQ14_A  VDDQ      WCK1_t_A  VSS       DQ10_A  VSS
F  VDDQ    VSS     VDDQ      VDDQ      DQ7_A     VDD2H   VDD2H   VSS     VDD2H   VDD2H   DQ15_A    VDDQ      VDDQ      VSS     VDDQ
G  VDDQ    VDDQ    VSS       CA0_A     VSS       CS1_A   VSS     CA2_A   VSS     CA4_A   VSS       CA6_A     VSS       VDDQ    VDDQ
H  RESET_n VDD2L   VSS       VSS       CA1_A     VSS     CS0_A   VSS     CK_t_A  VSS     CA3_A     VSS       CA5_A     VDD2L   ZQ_A
J  VSS     VDD2L   VSS       RFU       VDD2H     RFU     VSS     VSS     CK_c_A  VSS     VDD2H     VSS       VSS       VDD2L   VSS
K  VDD2H   VDD2H   VDD2H     VDD2H     VDD2H     VDD2H   VSS     VSS     VSS     VDD2H   VDD2H     VDD2H     VDD2H     VDD2H   VDD2H
L  VSS     VSS     VSS       VSS       VSS       VDD2H   VDD2H   VDD2H   VDD2H   VDD2H   VSS       VSS       VSS       VSS     VSS
M  VDD2H   VDD2H   VDD2H     VDD2H     VDD2H     VDD2H   VSS     VSS     VSS     VDD2H   VDD2H     VDD2H     VDD2H     VDD2H   VDD2H
N  VSS     VDD2L   VSS       VSS       VDD2H     VSS     CK_c_B  VSS     VSS     VSS     VDD2H     VSS       VSS       VDD2L   VSS
P  RFU     VDD2L   CA5_B     VSS       CA3_B     VSS     CK_t_B  VSS     CS0_B   VSS     CA1_B     VSS       VSS       VDD2L   RFU
R  VDDQ    VDDQ    VSS       CA6_B     VSS       CA4_B   VSS     CA2_B   VSS     CS1_B   VSS       CA0_B     VSS       VDDQ    VDDQ
T  VDDQ    VSS     VDDQ      VDDQ      DQ15_B    VDD2H   VDD2H   VSS     VDD2H   VDD2H   DQ7_B     VDDQ      VDDQ      VSS     VDDQ
U  VSS     DQ10_B  VSS       WCK1_t_B  VDDQ      DQ14_B  VDD2H   VSS     VDD2H   DQ6_B   VDDQ      WCK0_t_B  VSS       DQ2_B   VSS
V  DQ8_B   VSS     DQ11_B    VDDQ      WCK1_c_B  VSS     VSS     VDD2H   VSS     VSS     WCK0_c_B  VDDQ      DQ3_B     VSS     DQ0_B
W  VDD1    DQ9_B   VDDQ      RDQS1_c_B VSS       DQ13_B  VDD2H   VSS     VDD2H   DQ5_B   VSS       RDQS0_c_B VDDQ      DQ1_B   VDD1
Y  NC      VDDQ    RDQS1_t_B VSS       DQ12_B    VDD2L   VDD2H   VSS     VDD2H   VDD2L   DQ4_B     VSS       RDQS0_t_B VDDQ    NC
AA NC      NC      VDDQ      DMI1_B    VSS       VDD2L   VDD2H   VDD2H   VDD2H   VDD2L   VSS       DMI0_B    VDDQ      NC      NC
"""

U1_CENTRE = (20.725, 19.0)
U1_ROT = 180  # row A toward the fingers, ball A1 at the lower right seen from the front


def ball_fn():
    d = OrderedDict()
    for line in BALLMAP.strip().splitlines():
        t = line.split()
        assert len(t) == 16, line
        for c, fn in enumerate(t[1:], start=1):
            d["%s%d" % (t[0], c)] = fn
    assert len(d) == 315
    return d


def ball_xy(ball):
    row = ball.rstrip("0123456789")
    col = int(ball[len(row):])
    r = ROWS.index(row)
    return (round(U1_CENTRE[0] - (col - 8) * 0.8, 3), round(U1_CENTRE[1] + (r - 10) * 0.7, 3))


# The eight data balls of the second byte of channel A (DQ8-15 of the x16 die) are left open.  Two 4 x 10 k pull-down arrays (RN401 / RN402)
# stood on them until the final review: they needed eight F.Cu escapes west out of the ball field (37 mm of driven stubs: the die drives
# these balls on every read) that crossed the In3 command bundle 15 times in plan view.  What the guidance says: JESD209-5C has no rule for
# the unused byte of a x16 die and treats an undriven DQ bus as normal ("DQ channel can be floated"; DQ ODT is off by default); AMD UG583
# leaves DQ[15:8], DMI1 and DQS1 of an LPDDR4 x16 device with only DQ[7:0] in use unconnected (ECC device, Tables 55 / 56) and says for
# DDR4 "DQ[15:8] can be left floating"; Micron TN-40-40 (DDR4 point to point): unused DQ "can be floated".  The upper-byte receivers are
# clocked by WCK1, which is held static (R404 / R405), and channel B has always had all 16 DQ open
UNUSED_BYTE_OPEN = ("unused byte 1 (DQ8-15 of the x16 die): intentionally open, no pull-down. JESD209-5C has no rule for an unused byte and "
                    "lets an undriven DQ bus float; AMD UG583 leaves DQ[15:8] of an LPDDR4 x16 device open (ECC device, only DQ[7:0] used); "
                    "Micron TN-40-40: unused DQ can be floated")


# ball function -> (net, treatment)
def u1_net(fn):
    supply = {"VSS": "GND", "VDD2H": "VDD2H", "VDD2L": "VDD2H", "VDDQ": "VDDQ", "VDD1": "VDD1"}
    if fn in supply:
        return supply[fn], "supply" + (" (VDD2L tied to VDD2H, single-rail mode, MR13 OP[7] = 1)" if fn == "VDD2L" else "")
    if fn in ("NC", "RFU"):
        return "", "not internally connected"
    a = {
        "RESET_n": ("RESET_N", "RESET_n, VDD2H domain"),
        "ZQ_A": ("ZQ", "240 ohm 1 % to VDDQ"),
        "CK_t_A": ("CK_P", "command clock"), "CK_c_A": ("CK_N", "command clock"),
        "CS0_A": ("CS0", "chip select rank 0"),
        "CS1_A": ("GND", "tied low (NC on a single-rank package; keeps rank 1 deselected on a two-rank part)"),
        "WCK0_t_A": ("WCK_P", "write clock byte 0"), "WCK0_c_A": ("WCK_N", "write clock byte 0"),
        "RDQS0_t_A": ("RDQS_P", "read strobe byte 0"), "RDQS0_c_A": ("RDQS_N", "read strobe byte 0 (MR20 OP[1:0] = 10b)"),
        "DMI0_A": ("GND", "tied to VSS through its own via-in-pad (fixed low; data mask, DBI, link ECC and READ FIFO "
                          "unused). No F.Cu strap: the DQ4 neck uses the row channel between A4 and B4"),
        "DMI1_A": ("GND", "tied on F.Cu to the adjacent VSS ball A11 (unused byte)"),
        "WCK1_t_A": ("WCK1_T", "unused byte 1: static low through 10 k"),
        "WCK1_c_A": ("WCK1_C", "unused byte 1: static high through 10 k to VDDQ"),
        "RDQS1_t_A": ("", "unused byte 1 output, open"), "RDQS1_c_A": ("", "unused byte 1 output, open"),
        # channel B: die never selected
        "CK_t_B": ("GND", "unused channel: clock-stop level, CK_t low"),
        "CK_c_B": ("VDDQ", "unused channel: clock-stop level, CK_c high"),
        "CS0_B": ("GND", "unused channel: permanent DESELECT"),
        "CS1_B": ("GND", "unused channel: permanent DESELECT"),
        "WCK0_t_B": ("GND", "unused channel: static WCK_t low"), "WCK1_t_B": ("GND", "unused channel: static WCK_t low"),
        "WCK0_c_B": ("VDDQ", "unused channel: static WCK_c high"), "WCK1_c_B": ("VDDQ", "unused channel: static WCK_c high"),
    }
    if fn in a:
        return a[fn]
    if fn.startswith("CA") and fn.endswith("_A"):
        return fn[:-2], "command/address"
    if fn.startswith("CA") and fn.endswith("_B"):
        return "GND", "unused channel: tied to the adjacent VSS ball"
    if fn.startswith("DQ") and fn.endswith("_A"):
        n = int(fn[2:-2])
        if n >= 8:      # no pull-down: see UNUSED_BYTE_OPEN
            return "", UNUSED_BYTE_OPEN
        return "DQ%d" % n, "data byte 0"
    if fn.endswith("_B"):
        return "", "unused channel I/O, open (Hi-Z, die never selected)"
    raise ValueError(fn)


# ----------------------------------------------------------------------------------------------
# 3. Finger assignment
# ----------------------------------------------------------------------------------------------
# command: bank 65 byte group T0, order-preserving left to right (finger x == ball-column order)
CMD_FINGERS = OrderedDict([(66, "CA5"), (67, "CA6"), (70, "CA3"), (71, "CA4"), (74, "CK_N"), (76, "CK_P"),
                           (79, "CA2"), (80, "CS0"), (83, "CA1"), (84, "CA0")])
CMD_BALL = {"CA0": "G4", "CA1": "H5", "CA2": "G8", "CA3": "H11", "CA4": "G10", "CA5": "H13", "CA6": "G12",
            "CS0": "H7", "CK_P": "H9", "CK_N": "J9"}
# divider columns = the 0.8 mm In3 channels between ball columns, left to right
CMD_COLUMNS = OrderedDict((net, round(16.325 + 0.8 * k, 3)) for k, net in enumerate(CMD_FINGERS.values()))

# write, read, WCK, RDQS, RESET_n: the data side is one plan (scripts/data_side.py, DESIGN.md section 14).
# write: bank 64, fingers 143 .. 161 above the comb (DQ4 / DQ5 west of the key, the six others east of it); the lowest
# F.Cu line takes the westmost column, so the finger of a bit follows from the exit order of the BGA corner.
# WCK: fingers 137 / 139 = the P / N pins of the host pair CK0, the card polarity inverted (gateware drives it inverted).
# read: bank 66; DQ on the P pin of a complete pair, the N pin on its reference network; the pairs are taken in the order
# of the In3 bundle (lowest line, westmost finger), so that no read finger track crosses an In3 read line
# (data_side.choose_read_end); the group is not matched on the card.
# RDQS: fingers 219 / 221 = the host pair DQS6, east of every read finger, inverted (RDQS_t on the N pin).
# RESET_n: finger 232 (back side, bank 66, static).  Routed by the power stage (scripts/power_side.py).
WR_FINGERS = OrderedDict(sorted([(f, n) for n, f in DATA.WRITE_FINGER.items()] + [(f, n) for n, f in DATA.WCK_FINGER.items()]))
DQ_BALL = dict(DATA.BALL)
WCK_BALL = dict(DATA.WCK_BALL)
RDQS_BALL = dict(DATA.RDQS_BALL)
RD_PAIRS = OrderedDict(sorted((pf, (nf, int(net[2:]))) for net, (pf, nf) in DATA.READ_FINGER.items()))     # P finger: (N finger, bit)
RDQS_FINGERS = OrderedDict((f, n) for n, f in DATA.RDQS_FINGER.items())      # no series part: the finger is on the net of the ball
RESET_FINGER = POWER.RESET_FINGER
assert DATA.RESET_FINGER == RESET_FINGER

VDD_FINGERS = [111, 112, 117, 118, 123, 124, 129, 130, 135, 136, 141, 142, 147, 148, 153, 154, 159, 160, 163]

PAIR_FINGER_SIDE = {"CK_P": "CK_F_P", "CK_N": "CK_F_N", "WCK_P": "WCK_F_P", "WCK_N": "WCK_F_N"}


def fnet(n):
    """Finger-side net of a divider-driven net (pair legs keep _P / _N at the end of the name)."""
    if n in PAIR_FINGER_SIDE:
        return PAIR_FINGER_SIDE[n]
    return n + ("_WF" if n.startswith("DQ") else "_F")


def rfnet(n):
    """Read-finger net of a ball-side net: the same net (no part between the ball and the read finger)."""
    return n


FINGER_NET = {}
FINGER_FUNC = {}
for f, n in CMD_FINGERS.items():
    FINGER_NET[f] = fnet(n)
    FINGER_FUNC[f] = "COMMAND %s (FPGA output, 1.2 V, to the %s divider)" % (n, n)
for f, n in WR_FINGERS.items():
    if n.startswith("DQ"):
        FINGER_NET[f] = fnet(n)
        FINGER_FUNC[f] = "WRITE %s (FPGA output, 1.2 V, Hi-Z during reads)" % n
    else:
        FINGER_NET[f] = fnet(n)
        FINGER_FUNC[f] = "WRITE clock %s (FPGA DIFF_SSTL12 output; card polarity is the inverse of the host pair: drive inverted)" % n
for p, (n, bit) in RD_PAIRS.items():
    FINGER_NET[p] = "DQ%d" % bit
    FINGER_FUNC[p] = "READ DQ%d (FPGA differential input, P leg)" % bit
    FINGER_NET[n] = "VREF_N%d" % bit
    FINGER_FUNC[n] = "READ reference for DQ%d (FPGA differential input, N leg; DC, 39 ohm + 100 nF to VREF_RD)" % bit
for f, n in RDQS_FINGERS.items():
    FINGER_NET[f] = n
    FINGER_FUNC[f] = "READ strobe %s (true differential pair on the host-coupled %s pair; card polarity is the inverse of the host pair)" % (n, "DQS6")
FINGER_NET[RESET_FINGER] = "RESET_N_F"
FINGER_FUNC[RESET_FINGER] = "RESET_n (FPGA LVCMOS12 output; 100 ohm series, 1 k pull-down on the card)"
FINGER_NET[253] = "SCL"
FINGER_FUNC[253] = "I2C clock to the reference DAC (host 4.99 k pull-up to 3.3 V, mux 0x74 channel 7)"
FINGER_NET[254] = "SDA"
FINGER_FUNC[254] = "I2C data to the reference DAC (host 4.99 k pull-up to 3.3 V)"
FINGER_NET[255] = "V3P3"
FINGER_FUNC[255] = "VDDSPD: 3.3 V on the ZCU104 (VDD1 regulator input, LDO bias, DAC)"

# ----------------------------------------------------------------------------------------------
# 4. Parts
# ----------------------------------------------------------------------------------------------
PARTS = []
NO_CONNECT = []
# assembly fiducials (found missing by the mechanical review): three per face, not in line, 3 mm or more from every card edge, nothing of
# the design (copper, courtyard, silk) within 1.6 mm of the centre.  Card frame; the back ones are given in the same x (seen through the card)
FIDUCIALS = OrderedDict([("FID1", ("F", 6.0, 26.5)), ("FID2", ("F", 8.0, 9.0)), ("FID3", ("F", 54.0, 25.5)),
                         ("FID4", ("B", 6.0, 26.5)), ("FID5", ("B", 8.0, 9.0)), ("FID6", ("B", 64.0, 26.5))])


def part(ref, value, mpn, mfr, desc, fp, sym, sheet, pins, dnp=False, in_bom=True):
    PARTS.append(OrderedDict(ref=ref, value=value, mpn=mpn, manufacturer=mfr, description=desc, footprint=fp,
                             symbol=sym, dnp=dnp, in_bom=in_bom, sheet=sheet,
                             pins=OrderedDict((str(k), v) for k, v in pins.items())))


R0201 = "Resistor_SMD:R_0201_0603Metric"
R0402 = "Resistor_SMD:R_0402_1005Metric"
C0201 = "Capacitor_SMD:C_0201_0603Metric"
C0402 = "Capacitor_SMD:C_0402_1005Metric"
C0603 = "Capacitor_SMD:C_0603_1608Metric"
# Every symbol is in the project library (lib/lpddr5_testbed.kicad_sym, written by scripts/gen_schematic.py): R, C,
# TestPoint, TPS74801DRC and TLV70018_SOT23-5 are copies of the KiCad library symbols of the same name, so
# the project opens without any stock symbol library (their file layout differs between KiCad installations).
RSYM, CSYM = "lpddr5_testbed:R", "lpddr5_testbed:C"


def yageo(size, val):
    """Yageo RC series thick film, 1 %."""
    code = {"0R": "0R", "22R": "22R", "33R": "33R", "39R": "39R", "68R": "68R", "91R": "91R", "100R": "100R",
            "150R": "150R", "180R": "180R", "240R": "240R", "300R": "300R", "1k": "1K", "1k37": "1K37",
            "2k49": "2K49", "4k42": "4K42", "10k": "10K", "47k": "47K"}[val]
    if val == "0R":
        return "RC%sJR-070RL" % size
    return "RC%sFR-07%sL" % (size, code)


def res(ref, val, a, b, sheet, desc, size="0402", dnp=False):
    tol = "jumper" if val == "0R" else "1 %"
    part(ref, val, yageo(size, val), "Yageo", "%s, resistor %s %s %s" % (desc, val, tol, size),
         R0201 if size == "0201" else R0402, RSYM, sheet, {1: a, 2: b}, dnp=dnp)


CAPS = {
    "2n2": ("CL05B222KB5NNNC", "2.2 nF 50 V X7R 10 %", C0402),
    "100n": ("CL05B104KO5NNNC", "100 nF 16 V X7R 10 %", C0402),
    "100n_0201": ("CL03A104KQ3NNNC", "100 nF 6.3 V X5R 10 %", C0201),
    "1u": ("CL05A105KP5NNNC", "1 uF 10 V X5R 10 %", C0402),
    "4u7": ("CL05A475MP5NRNC", "4.7 uF 10 V X5R 20 %", C0402),
    "22u": ("CL10A226MQ8NRNC", "22 uF 6.3 V X5R 20 %", C0603),
}


def cap(ref, val, a, b, sheet, desc, size=None):
    mpn, d, fp = CAPS[val if size is None else "%s_%s" % (val, size)]
    part(ref, val, mpn, "Samsung Electro-Mechanics", "%s, capacitor %s %s" % (desc, d, fp.split("_")[1]), fp, CSYM,
         sheet, {1: a, 2: b})


def build_parts(pm):
    # ---- J1 edge connector ----------------------------------------------------------------
    pins = OrderedDict()
    for f in range(1, 261):
        r = pm[f]
        host = r["host_net"]
        if f in FINGER_NET:
            net = FINGER_NET[f]
        elif host == "GND":
            net = "GND"
        elif f in VDD_FINGERS:
            net = "V1P2"
        else:
            net = ""
            NO_CONNECT.append(OrderedDict(ref="J1", pin=str(f), reason=finger_nc_reason(f, r)))
        pins[f] = net
    part("J1", "DDR4_SODIMM_260", "N/A (card edge, hard-gold fingers)", "PCB feature",
         "260-pin DDR4 SO-DIMM card edge (JEDEC MO-310), 0.50 mm pitch per side, odd pins front, even pins back",
         "lpddr5_testbed:DDR4_SODIMM_260_Edge", "lpddr5_testbed:DDR4_SODIMM_260_Edge", "connector", pins,
         in_bom=False)

    # ---- U1 LPDDR5 ------------------------------------------------------------------------
    pins = OrderedDict()
    for ball, fn in ball_fn().items():
        net, why = u1_net(fn)
        pins[ball] = net
        if net == "":
            NO_CONNECT.append(OrderedDict(ref="U1", pin=ball, reason="%s: %s" % (fn, why)))
    part("U1", "H58G56BK7BX068N", "H58G56BK7BX068N", "SK hynix",
         "LPDDR5X SDRAM, 315-ball FBGA 12.4 x 15.0 mm, 0.8 x 0.7 mm pitch, JEDEC 2CHx32 ball map "
         "(pin-compatible: Micron MT62F1G32D2DS-023 WT:B)",
         "lpddr5_testbed:BGA-315_12.4x15mm_P0.8x0.7mm", "lpddr5_testbed:LPDDR5_315B_2CH", "lpddr5", pins)

    # ---- command dividers (0201, one column per 0.8 mm BGA channel) -------------------------
    order = ["CA0", "CA1", "CA2", "CA3", "CA4", "CA5", "CA6", "CS0", "CK_P", "CK_N"]
    for k, n in enumerate(order):
        rs, rp = ("33R", "100R") if n == "CS0" else ("91R", "68R")
        res("R%d" % (101 + k), rs, fnet(n), n, "command", "%s series (finger side of the divider)" % n, size="0201")
        res("R%d" % (111 + k), rp, n, "GND", "command", "%s shunt to GND at the divider node" % n, size="0201")

    # ---- write dividers (0201); the read branch starts on the divider node without a part ----
    for i in range(8):
        res("R%d" % (201 + i), "240R", "DQ%d_WF" % i, "DQ%d" % i, "write", "DQ%d write series" % i, size="0201")
        res("R%d" % (211 + i), "150R", "DQ%d" % i, "GND", "write", "DQ%d shunt (write divider and read load)" % i, size="0201")
    for k, n in enumerate(["WCK_P", "WCK_N"]):
        res("R%d" % (209 + k), "91R", fnet(n), n, "write", "%s series" % n, size="0201")
        res("R%d" % (219 + k), "68R", n, "GND", "write", "%s shunt to GND at the node via" % n, size="0201")

    # ---- read strobe (true differential, no reference) --------------------------------------
    for k, n in enumerate(["RDQS_P", "RDQS_N"]):
        res("R%d" % (309 + k), "150R", n, "GND", "read", "%s load to GND at the read finger (sets the read high level like the DQ shunt)" % n,
            size="0201")

    # ---- read reference ---------------------------------------------------------------------
    for i in range(8):
        res("R%d" % (321 + i), "39R", "VREF_N%d" % i, "VREF_RD", "read",
            "N finger of DQ%d: matched termination of the host line into the reference" % i, size="0201")
        cap("C%d" % (321 + i), "100n", "VREF_RD", "GND", "read", "AC ground of the reference at the N finger of DQ%d" % i, size="0201")
    part("U5", "DAC53401", "DAC53401DSGR", "Texas Instruments",
         "10-bit voltage-output DAC with NVM, I2C address 0x48 (A0 = AGND), WSON-8 2 x 2 mm",
         "Package_SON:WSON-8-1EP_2x2mm_P0.5mm_EP0.9x1.6mm", "lpddr5_testbed:DAC53401DSG", "read",
         OrderedDict([(1, "GND"), (2, "SCL"), (3, "SDA"), (4, "DAC_CAP"), (5, "GND"), (6, "V3P3"), (7, "DAC_OUT"),
                      (8, "DAC_OUT"), (9, "GND")]))
    res("R331", "300R", "DAC_OUT", "VREF_RD", "read", "DAC output to the reference bus (Ra)")
    res("R332", "300R", "VDDQ", "VREF_RD", "read", "VDDQ-derived power-up default, upper leg (Rb)")
    res("R333", "180R", "VREF_RD", "GND", "read", "reference bus to GND (Rc)")
    cap("C331", "1u", "VREF_RD", "GND", "read", "reference bus bulk")
    cap("C332", "100n", "V3P3", "GND", "read", "DAC VDD decoupling")
    cap("C333", "1u", "DAC_CAP", "GND", "read", "DAC internal LDO capacitor (0.5..15 uF)")

    # ---- U1 support ---------------------------------------------------------------------------
    res("R401", "240R", "ZQ", "VDDQ", "lpddr5", "ZQ calibration reference to VDDQ", size="0201")
    res("R402", "100R", "RESET_N_F", "RESET_N", "lpddr5", "RESET_n series", size="0201")
    res("R403", "1k", "RESET_N", "GND", "lpddr5", "RESET_n pull-down (holds reset while the FPGA is unconfigured)")
    res("R404", "10k", "WCK1_T", "GND", "lpddr5", "unused WCK1_t static low")
    res("R405", "10k", "WCK1_C", "VDDQ", "lpddr5", "unused WCK1_c static high")
    # DQ8-15 of the unused byte carry no part: the balls are open (UNUSED_BYTE_OPEN)
    n = 401
    for rail, count in (("VDD2H", 12), ("VDDQ", 8), ("VDD1", 4)):
        for _ in range(count):
            cap("C%d" % n, "4u7", rail, "GND", "lpddr5", "U1 %s decoupling (B.Cu under the BGA)" % rail)
            n += 1

    # ---- power ----------------------------------------------------------------------------------
    part("U2", "TPS74801", "TPS74801DRCRM3", "Texas Instruments",
         "1.5 A LDO with bias rail, soft start and power good; VDD2H 1.05 V from the 1.2 V fingers, VSON-10 3 x 3 mm; "
         "the M3 suffix guarantees the current die (+/-1 % output, PG low 0.125 V), not the legacy one (+/-2 %)",
         "Package_SON:VSON-10-1EP_3x3mm_P0.5mm_EP1.65x2.4mm", "lpddr5_testbed:TPS74801DRC", "power",
         OrderedDict([(1, "V1P2"), (2, "V1P2"), (3, "PG_VDD2H"), (4, "V3P3"), (5, "VDD1"), (6, "GND"), (7, "VDD2H_SS"),
                      (8, "VDD2H_FB"), (9, "VDD2H"), (10, "VDD2H"), (11, "GND")]))
    part("U3", "TLV70018", "TLV70018DDCR", "Texas Instruments",
         "200 mA LDO 1.8 V fixed, no output discharge, enable; VDD1 from 3.3 V, thin SOT-23-5 (DDC, 1.1 mm high)",
         "Package_TO_SOT_SMD:TSOT-23-5", "lpddr5_testbed:TLV70018_SOT23-5", "power",
         OrderedDict([(1, "V3P3"), (2, "GND"), (3, "PWR_EN"), (4, ""), (5, "VDD1")]))
    NO_CONNECT.append(OrderedDict(ref="U3", pin="4", reason="NC pin (no internal connection)"))
    part("U4", "MIC47050", "MIC47050YML-TR", "Microchip",
         "500 mA NMOS LDO with bias rail, adjustable from 0.4 V; VDDQ 0.5 V from the 1.2 V fingers, MLF-6 2 x 2 mm",
         "lpddr5_testbed:MLF-6-1EP_2x2mm_P0.65mm_EP0.8x1.4mm", "lpddr5_testbed:MIC47050YML", "power",
         OrderedDict([(1, "V3P3"), (2, "GND"), (3, "V1P2"), (4, "VDDQ"), (5, "VDDQ_FB"), (6, "PG_VDD2H"), (7, "GND")]))
    cap("C501", "22u", "V1P2", "GND", "power", "1.2 V input bulk at U2")
    cap("C502", "22u", "V1P2", "GND", "power", "1.2 V input bulk at the VDD fingers")
    cap("C503", "1u", "V3P3", "GND", "power", "U2 BIAS decoupling")
    cap("C504", "2n2", "VDD2H_SS", "GND", "power", "U2 soft start (3.3 ms)")
    res("R501", "1k37", "VDD2H", "VDD2H_FB", "power", "U2 feedback upper (1.048 V)")
    res("R502", "4k42", "VDD2H_FB", "GND", "power", "U2 feedback lower")
    cap("C505", "22u", "VDD2H", "GND", "power", "U2 output bulk")
    res("R503", "10k", "V3P3", "PG_VDD2H", "power", "U2 power-good pull-up = U4 enable")
    cap("C506", "1u", "V3P3", "GND", "power", "U3 input")
    cap("C507", "1u", "VDD1", "GND", "power", "U3 output (1 uF: with the four 4.7 uF at U1 the 47 k bleeder still empties VDD1 within 2 s)")
    res("R504", "47k", "V1P2", "PWR_EN", "power", "U3 enable from the host 1.2 V rail")
    cap("C508", "4u7", "V1P2", "GND", "power", "U4 input")
    cap("C509", "1u", "V3P3", "GND", "power", "U4 BIAS decoupling")
    res("R506", "2k49", "VDDQ", "VDDQ_FB", "power", "U4 feedback upper (0.4996 V)")
    res("R507", "10k", "VDDQ_FB", "GND", "power", "U4 feedback lower")
    cap("C510", "4u7", "VDDQ", "GND", "power", "U4 output")
    res("R508", "22R", "VDDQ", "GND", "power", "VDDQ bleeder (23 mA, 11 mW): VDDQ follows VDD2H down within about 1 ms",
        size="0201")
    res("R509", "47k", "VDD1", "GND", "power", "VDD1 bleeder (38 uA): VDD1 below 0.3 V within 2 s of power-off, still the last rail to fall")
    cap("C511", "4u7", "V3P3", "GND", "power", "3.3 V input bulk at finger 255")
    cap("C512", "100n", "V1P2", "GND", "power", "V1P2 to GND at the foot of the 1.2 V feed corridor (return path of the write fingers)")
    cap("C513", "100n", "V1P2", "GND", "power", "V1P2 to GND at the foot of the 1.2 V feed corridor")
    for k, net in enumerate(["VDD1", "VDD2H", "VDDQ", "VREF_RD", "GND"]):
        part("TP%d" % (k + 1), net, "N/A (PCB pad)", "PCB feature", "test pad 1.0 mm, %s" % net,
             "TestPoint:TestPoint_Pad_D1.0mm", "lpddr5_testbed:TestPoint", "read" if net == "VREF_RD" else "power", {1: net},
             in_bom=False)
    # assembly fiducials: three per face (0201 parts and a 0.8 x 0.7 mm BGA on the front, 0201 / MLF on the back); no pin, no net
    for ref, (side, x, y) in FIDUCIALS.items():
        part(ref, "Fiducial", "N/A (PCB feature)", "PCB feature",
             "assembly fiducial, %s side: 1.0 mm copper dot in a 2.0 mm solder-mask opening" % ("front" if side == "F" else "back"),
             "lpddr5_testbed:Fiducial_1mm_Mask2mm", "lpddr5_testbed:Fiducial", "power", {}, in_bom=False)


def finger_nc_reason(f, r):
    name = r["ddr4_name"]
    host = r["host_net"]
    if r["fpga_ball"]:
        return "%s: unused FPGA pin %s (bank %s), left open on the card" % (name, r["fpga_ball"], r["bank"])
    if host == "UTIL_2V5":
        return "%s: host 2.5 V VPP, not needed (3.3 V supplies bias and VDD1)" % name
    if name == "VTT":
        return "VTT: host 0.6 V termination rail, not used"
    if name == "VREFCA":
        return "VREFCA: host 0.6 V reference, not used"
    if name.startswith("SA"):
        return "%s: SPD address strap, no SPD on the card" % name
    return "%s: no function on this card" % name


# ----------------------------------------------------------------------------------------------
# 5. Fingers table, groups, floorplan
# ----------------------------------------------------------------------------------------------
def finger_x(n):
    if n % 2:
        return 1.425 + 0.25 * (n - 1) if n <= 143 else 39.425 + 0.25 * (n - 145)
    return 1.675 + 0.25 * (n - 2) if n <= 144 else 39.675 + 0.25 * (n - 146)


def build_fingers(pm, td, j1pins):
    rows = []
    for f in range(1, 261):
        r = pm[f]
        partner = None
        if r["pn"] in ("P", "N") and r["partner_finger"] and r["partner_finger"][0].isdigit():
            partner = int(r["partner_finger"].split()[0])
        net = j1pins[str(f)]
        if f in FINGER_FUNC:
            func = FINGER_FUNC[f]
        elif net == "GND":
            func = "VSS"
        elif net == "V1P2":
            func = "VDD: host 1.2 V (VCC1V2), input of the VDD2H and VDDQ regulators"
        else:
            func = "no connect: " + finger_nc_reason(f, r)
        assert abs(finger_x(f) - float(r["x_mm"])) < 1e-6
        rows.append(OrderedDict(
            finger=f, side="F" if f % 2 else "B", x_mm=round(finger_x(f), 3), ddr4_name=r["ddr4_name"],
            host_net=r["host_net"], fpga_ball=r["fpga_ball"] or None, fpga_pin_name=r["fpga_pin_name"] or None,
            bank=int(r["bank"]) if r["bank"] else None, bytegroup=r["byte_nibble"] or None,
            pn=r["pn"] if r["pn"] in ("P", "N") else None, partner_finger=partner,
            host_plus_pkg_delay_ps=float(td[f]["total_mid_ps"]) if f in td else None,
            net=net, function=func))
    return rows


TD = {"In3": 6.91, "ms15": 6.01, "ms20": 6.07, "ms09": 5.94, "pair": 5.69, "via": 7.0}


def _mid_copper_z():
    z, out = 0.0, {}
    for lay, typ, th, _m, _er, _role in STACKUP:
        if typ == "soldermask":
            continue
        if typ == "copper":
            out[lay] = z + th / 2
        z += th
    return out


# barrel lengths between the middles of the copper layers (the board is measured the same way by check_pcb.py)
Z_F_IN3 = round(_mid_copper_z()["In3.Cu"] - _mid_copper_z()["F.Cu"], 4)   # F.Cu to In3 through the board: 0.6425
Z_B_IN3 = round(_mid_copper_z()["B.Cu"] - _mid_copper_z()["In3.Cu"], 4)   # B.Cu to In3: 0.5145


def r45(dx, dy):
    dx, dy = abs(dx), abs(dy)
    return max(dx, dy) + 0.414 * min(dx, dy)


def r3(v):
    return round(v + 0.0, 3)


def P(x, y):
    return [r3(x), r3(y)]


# ---- geometry of the divider fields (card frame, mm) ------------------------------------------------
# Stock footprints, part placed "vertical" (long axis along y): pad centres +/-d from the part centre,
# pad size (across x, along y), courtyard half size (x, y).
GEOM = {
    "R0201": OrderedDict(pad_offset=0.32, pad=(0.40, 0.46), courtyard_half=(0.35, 0.70), height=0.26),
    "C0201": OrderedDict(pad_offset=0.32, pad=(0.40, 0.46), courtyard_half=(0.35, 0.70), height=0.33),
    "R0402": OrderedDict(pad_offset=0.51, pad=(0.64, 0.54), courtyard_half=(0.47, 0.93), height=0.40),
    "C0402": OrderedDict(pad_offset=0.48, pad=(0.62, 0.56), courtyard_half=(0.46, 0.91), height=0.55),
}
Y_FINGER_VIA = 3.05      # row of the VSS / VDD finger vias, just above the mask-free finger strip (2.85)
FINGER_START = round(FINGER_TOP - 0.10, 3)   # every finger track starts 0.10 mm inside the finger pad (same for all nets)
FINGER_VIA = OrderedDict(diameter=0.40, drill=0.20, tie_width=0.25)
FINGER_PAD_HALF_W = 0.175
# Lead-out of a finger stub: x offset from the finger axis.  A signal finger whose neighbour (0.25 mm away, on the
# other face) belongs to a VSS / VDD finger pair leaves flush with its pad edge, away from that pair, so that the
# through via of the pair (0.40 mm) keeps 0.15 mm from the stub.  Fingers not listed leave on their axis.
LEAD_OFFSET = {"CA6": -0.075, "CA3": 0.075, "CA4": -0.075, "CK_N": 0.075, "CK_P": -0.075, "CA2": 0.075, "CS0": -0.075,
               "CA1": 0.075}       # command fingers only; the stubs of the data side are drawn by scripts/data_side.py
CMD_ROWS = OrderedDict(series_resistor_centre=5.00, node_via=5.85, shunt_resistor_centre=6.65, gnd_via=7.50)
Y_DAC_REGION0 = 8.00     # south edge of the DAC region (the GND via of C331 stands at y = 8.20)
# GND returns of the reference network: own vias beside the GND pads of R333 (divider leg) and C331 (1 uF), reached by a
# short 0.25 mm link straight away from the VREF_RD rail (floorplan.read.links)
DAC_GND_VIAS = OrderedDict(R333=(62.00, 12.75), C331=(61.00, 8.20))
Y_VREF_RAIL = 10.45      # VREF_RD rail of the DAC network: between C331 / R331 (south of it) and R332 / R333 (north of it)
X_VREF_TP = 59.50        # test pad TP4 on the rail line, west of the network.  The feed comes up at x = DATA.X_FEED (59.70: 0.95 mm from the
                         # pad of the eastern RDQS via) and makes one 45-degree step of 0.2 mm onto the axis of the pad, below it
CK_SPLIT = OrderedDict(below_row_h=0.75, t_min=0.22, t_max=0.34)
# Every In3 command trace has a vertex here, 0.05 mm south of the U1 courtyard (y = 11.25): the spacing rules that
# hold "outside U1" test whole track segments, so the segment in the tuning zone must end before the courtyard.
Y_U1_COURTYARD_BREAK = round(U1_CENTRE[1] - 7.5 - 0.25 - 0.05, 3)
# Length tuning of CA / CS on In3, between the fence vias and row A of U1.  Few, large bends (rules.bend_model):
#   shift   the trace runs parallel to its channel axis, shifted away from the CK corridor, between two 45-degree runs
#           that leave the axis at shift_y[0] and rejoin it at shift_y[1].  A shift s adds 2 s (sqrt(2) - 1) without any
#           90-degree bend.  The two half-bundles fan out: the west half to the west, the east half to the east.
#   bumps   rounded bumps on the shifted run, all pointing east, legs on the common rows first_leg_y + k * leg_pitch
#           (leg pitch 0.72 mm: 0.57 mm of gap between two antiparallel legs = 5.2 h, h = 0.11 mm, In3 to In2; at
#           0.48 mm the legs of one net couple and the bump loses about 10 % more of its delay), corner radius
#           radius (centre line; >= radius_min = the trace width).
# y: the copper of the legs stays inside; min_gap: copper to copper to the neighbouring trace (3 h); amplitude_min 2 w.
CMD_TUNE = OrderedDict(y=[7.90, 11.60], shift_y=[7.80, 11.70], leg_pitch=0.72, first_leg_y=8.67, rows=4,
                       min_gap=0.33, amplitude_min=0.30, radius_min=0.15, amplitude_max=0.80, shift_max=1.00)
# per net: shift (mm, + = east; "west" / "east": the whole trim is taken as a shift in that direction, solved),
# bumps, row0 (index of the row of the first leg), radius.  Nets with bumps have their amplitude solved.
CMD_TUNE_X = [15.5, 24.8]     # In3 zone of the tuning features: the shifted runs of CA5 / CA0 lie outside the ball columns
CMD_TUNE_NET = OrderedDict([
    ("CA5", dict(shift="west")),
    ("CA6", dict(shift=-0.50, bumps=2, row0=0, radius=0.15)),
    ("CA3", dict(shift=-0.28, bumps=2, row0=0, radius=0.15)),
    ("CA4", dict(shift=-0.14, bumps=2, row0=0, radius=0.15)),
    ("CA2", dict(shift=0.0, bumps=2, row0=0, radius=0.15)),
    ("CS0", dict(shift=0.55, bumps=2, row0=0, radius=0.15)),
    ("CA1", dict(shift="east")),
    ("CA0", dict(shift="east")),
])

PLACE = OrderedDict()   # ref -> placement
VIAS = []               # field vias (through, 0.40 / 0.20)


def side_of(f):
    return "F" if f % 2 else "B"


def other(s):
    return "B" if s == "F" else "F"


def place(ref, kind, side, x, y, pad1_south=True):
    g = GEOM[kind]
    d = g["pad_offset"] if pad1_south else -g["pad_offset"]
    PLACE[ref] = OrderedDict(side=side, x=r3(x), y=r3(y), orientation="vertical", footprint_geometry=kind,
                             pad1=P(x, y - d), pad2=P(x, y + d))


def via(net, x, y, role, fill=None, unused_inner_pads=None):
    for v in VIAS:                      # one via can serve two parts (shared GND via)
        if v["net"] == net and abs(v["x"] - x) < 1e-6 and abs(v["y"] - y) < 1e-6:
            v["role"] += "; " + role
            return
    v = OrderedDict(net=net, x=round(x + 0.0, 4), y=round(y + 0.0, 4), diameter=0.40, drill=0.20, role=role)
    if fill:
        v["fill"] = fill                # "filled and capped": the via pad is 0.05 - 0.10 mm from a 0201 solder land
    if unused_inner_pads:
        v["unused_inner_pads"] = unused_inner_pads      # "removed": no pad on an inner layer where the via connects to nothing
    VIAS.append(v)


def cmd_gnd_via_x(k, xc):
    """Fence via of column k: on the ball-column line away from the CK corridor (x = 19.925)."""
    return r3(xc - 0.4 if k <= 4 else xc + 0.4)


_DP = {}


def data_plan(td=None):
    """The data side (scripts/data_side.py), built once: geometry, vias, parts, delay budgets."""
    if not _DP:
        _DP.update(DATA.build(lambda f: float(td[f]["total_mid_ps"]), finger_x, ball_xy, TD, BEND_MODEL, _mid_copper_z()))
    return _DP


def build_placement():
    order = ["CA0", "CA1", "CA2", "CA3", "CA4", "CA5", "CA6", "CS0", "CK_P", "CK_N"]
    for k, (f, n) in enumerate(CMD_FINGERS.items()):
        xc, s, i = CMD_COLUMNS[n], side_of(f), order.index(n)
        place("R%d" % (101 + i), "R0201", s, xc, CMD_ROWS["series_resistor_centre"])
        place("R%d" % (111 + i), "R0201", other(s), xc, CMD_ROWS["shunt_resistor_centre"])
        via(n, xc, CMD_ROWS["node_via"], "command divider node: series (finger side), shunt (other side), In3 trace",
            fill="filled and capped")
        via("GND", cmd_gnd_via_x(k, xc), CMD_ROWS["gnd_via"], "GND of the %s shunt; fence via on a ball-column line" % n,
            unused_inner_pads="removed")    # no In3 pad 0.125 mm beside the command trace that passes it
    dp = data_plan()
    for q in dp["parts"]:               # write field, WCK columns, RDQS loads, reference networks (all 0201, upright)
        place(q["ref"], q["kind"], q["side"], q["x"], q["y"], pad1_south=q["pad1_south"])
        PLACE[q["ref"]]["group"] = q["group"]
    for v in dp["vias"]:
        via(v["net"], v["x"], v["y"], v["role"], unused_inner_pads=v.get("unused_inner_pads"))
    for ref, (x, y) in DAC_GND_VIAS.items():
        via("GND", x, y, "GND of %s: DC return of the VREF_RD network, straight into In1 / In2 / In4" % ref,
            unused_inner_pads="removed")    # no land on In3: that layer carries the high-speed groups only


# ---- explicit centre lines -------------------------------------------------------------------------
def poly_len(pts):
    return sum(((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5 for a, b in zip(pts, pts[1:]))


def divider_stub(f, net, xc, y_pad):
    """Finger stub of a divider-driven net: straight up the lead-out line, then one 45-degree run that ends on the
    centre of pad 1 of the series resistor."""
    xl = finger_x(f) + LEAD_OFFSET.get(net, 0.0)
    dx = abs(xc - xl)
    pts = [(xl, FINGER_START)]
    if dx > 1e-9:
        pts.append((xl, y_pad - dx))
    pts.append((xc, y_pad))
    return [P(*q) for q in pts]


def stub_len(path):
    """Length of a finger track counted from the finger top."""
    return poly_len(path) - (FINGER_TOP - FINGER_START)


def cmd_route(net, hook_t=None):
    """In3 centre line of a command net from its node via to its ball via.
    CA / CS: straight up the column channel, 0.4 mm jog into the ball.
    CK: the legs converge at once into the 0.10 / 0.10 pair on ball column 9 and split again 0.75 mm below row H
    into the two column channels beside the H9 via.  CK_N goes on to J9; CK_P passes H9 on the east and hooks
    back into it from the north-east (corner at H9 + (t, t)), which adds 2 t to its length."""
    xc, yn = CMD_COLUMNS[net], CMD_ROWS["node_via"]
    xb, yb = ball_xy(CMD_BALL[net])
    ycy = Y_U1_COURTYARD_BREAK
    if net not in ("CK_P", "CK_N"):
        return [P(xc, yn), P(xc, ycy), P(xc, yb - abs(xb - xc)), P(xb, yb)]
    xk, yh = ball_xy("H9")
    s = -1 if net == "CK_N" else 1                     # CK_N is the western leg
    xin, xout = xk + 0.10 * s, xk + 0.40 * s
    ys = yh - CK_SPLIT["below_row_h"]
    pts = [(xc, yn), (xc, yn + 0.20), (xin, yn + 0.20 + abs(xc - xin)), (xin, ycy), (xin, ys), (xout, ys + abs(xout - xin))]
    if net == "CK_N":
        pts += [(xout, yb - abs(xb - xout)), (xb, yb)]
    else:
        t = CK_SPLIT["t_min"] if hook_t is None else hook_t
        pts += [(xout, yh - 0.40 + 2 * t), (xk + t, yh + t), (xk, yh)]
    return [P(*q) for q in pts]


# ---- finger stubs and finger vias ------------------------------------------------------------------
def build_finger_stubs(support):
    """Every planned track that leaves a signal finger: net -> finger, layer, width, end, centre line."""
    S = OrderedDict()

    def add(net, f, width, to, path, kind, neck=None):
        S[net] = OrderedDict(finger=f, layer=side_of(f) + ".Cu", width=width, kind=kind, to=to, path=path,
                             length_from_finger_top_mm=round(stub_len(path), 3))
        if neck:        # (width, centre line): the first part of the path, drawn narrower; the rest ('wide') has 'width'
            S[net]["neck"] = OrderedDict(width=neck[0], path=neck[1], length_from_finger_top_mm=round(stub_len(neck[1]), 3))
            S[net]["wide"] = OrderedDict(width=width, path=path[len(neck[1]) - 1:], length_mm=round(poly_len(path[len(neck[1]) - 1:]), 3))
    order = ["CA0", "CA1", "CA2", "CA3", "CA4", "CA5", "CA6", "CS0", "CK_P", "CK_N"]
    g = GEOM["R0201"]
    for f, n in CMD_FINGERS.items():
        add(fnet(n), f, 0.20, "R%d.1" % (101 + order.index(n)),
            divider_stub(f, n, CMD_COLUMNS[n], CMD_ROWS["series_resistor_centre"] - g["pad_offset"]), "command stub")
    # ---- the data side: taken from the wires of the plan (scripts/data_side.py)
    dp = data_plan()
    by = defaultdict(list)
    for w in dp["wires"]:
        by[w["net"]].append(w)

    def two(net):
        neck = next(w for w in by[net] if w["role"].endswith("through the finger-via row"))
        wide = next(w for w in by[net] if w["role"].endswith("finger stub"))
        assert neck["path"][-1] == wide["path"][0], net
        return neck, wide
    for f, n in WR_FINGERS.items():
        ref = "R%d" % (201 + int(n[2:])) if n.startswith("DQ") else {"WCK_P": "R209", "WCK_N": "R210"}[n]
        neck, wide = two(fnet(n))
        add(fnet(n), f, wide["width"], ref + ".1", neck["path"] + wide["path"][1:], "write stub", neck=(neck["width"], neck["path"]))
    neck, wide = POWER.reset_stub(finger_x, support["R402"]["pads"]["1"][1])       # RESET_n: a slow net of the power stage, on the back
    add("RESET_N_F", RESET_FINGER, wide["width"], "R402.1", [P(*q) for q in neck["path"]] + [P(*q) for q in wide["path"][1:]], "RESET_n stub",
        neck=(neck["width"], [P(*q) for q in neck["path"]]))
    for pf, (nf, bit) in RD_PAIRS.items():
        w = next(q for q in by["VREF_N%d" % bit] if q["role"] == "N finger stub")
        add("VREF_N%d" % bit, nf, w["width"], "R%d.1" % (321 + bit), w["path"], "reference stub")
        w = next(q for q in by["DQ%d" % bit] if q["role"] == "read finger track")
        add("DQ%d" % bit, pf, w["width"], "read via", list(reversed(w["path"])), "read track")
    for f, n in RDQS_FINGERS.items():       # the outer-layer leg runs straight into the finger: its last segment
        w = next(q for q in by[n] if q["role"] == "leg" and q["layer"] == DATA.RDQS_OUTER)
        add(n, f, w["width"], "the %s leg of the pair" % DATA.RDQS_OUTER, [w["path"][-1], w["path"][-2]], "RDQS leg end")
    return S


def dac_links(support):
    """The DAC network east of the reference bus as explicit centre lines: the rail (TP4, C331 / R332, R331 / R333),
    DAC_OUT, and the GND returns of R333 / C331 to their own vias.  Unchanged by the data re-plan; drawn by s30_data."""
    L = []

    def add(net, layer, width, path, what):
        L.append(OrderedDict(net=net, layer=layer, width=width, group="REF" if net != "GND" else "GND", role=what, path=[P(*q) for q in path]))
    wv = RULES["netclasses"]["VREF"]["layers"]["F.Cu"]["width"]
    wg = 0.25
    pd = lambda ref, pin: tuple(support[ref]["pads"][pin])      # noqa: E731
    assert pd("TP4", "1") == (X_VREF_TP, Y_VREF_RAIL)
    xa, xb = pd("C331", "1")[0], pd("R331", "2")[0]
    assert abs(xa - pd("R332", "2")[0]) < 1e-9 and abs(xb - pd("R333", "1")[0]) < 1e-9 and X_VREF_TP < xa < xb
    assert pd("C331", "1")[1] < Y_VREF_RAIL < pd("R332", "2")[1] and pd("R331", "2")[1] < Y_VREF_RAIL < pd("R333", "1")[1]
    # the feed comes up at x = X_FEED and steps 0.2 mm west onto the axis of TP4 on one 45-degree run below the pad (two 135-degree vertices;
    # a step that ended in the pad centre would meet the rail at 45 degrees)
    dxf = DATA.X_FEED - X_VREF_TP
    yj = Y_VREF_RAIL - 0.75                          # the step ends 0.25 mm below the edge of the 1.0 mm pad
    assert 0 <= dxf <= 0.25 and yj - dxf > DATA.Y_FEED_VIA + 0.4
    add("VREF_RD", "F.Cu", wv, [(DATA.X_FEED, DATA.Y_FEED_VIA)] + ([(DATA.X_FEED, yj - dxf), (X_VREF_TP, yj)] if dxf > 1e-9 else []) + [(X_VREF_TP, Y_VREF_RAIL)], "feed via - TP4")
    add("VREF_RD", "F.Cu", wv, [(X_VREF_TP, Y_VREF_RAIL), (xa, Y_VREF_RAIL), (xb, Y_VREF_RAIL)], "rail of the DAC network: TP4, C331 / R332, R331 / R333")
    add("VREF_RD", "F.Cu", wv, [pd("C331", "1"), (xa, Y_VREF_RAIL)], "C331 (1 uF) to the rail")
    add("VREF_RD", "F.Cu", wv, [(xa, Y_VREF_RAIL), pd("R332", "2")], "rail to R332 (300 ohm to VDDQ)")
    add("VREF_RD", "F.Cu", wv, [pd("R331", "2"), (xb, Y_VREF_RAIL)], "R331 (300 ohm from the DAC) to the rail")
    add("VREF_RD", "F.Cu", wv, [(xb, Y_VREF_RAIL), pd("R333", "1")], "rail to R333 (180 ohm to GND)")
    for ref, pin in (("R333", "2"), ("C331", "2")):
        vx, vy = DAC_GND_VIAS[ref]
        px, py = pd(ref, pin)
        assert abs(px - vx) < 1e-9 and 0.6 < abs(py - vy) < 0.9 and (vy - py) * (py - Y_VREF_RAIL) > 0, (ref, (px, py))
        add("GND", "F.Cu", wg, [(px, py), (vx, vy)], "%s to its GND via (DC return of the reference network)" % ref)
    o7, o8, r1 = pd("U5", "7"), pd("U5", "8"), pd("R331", "1")
    assert abs(o7[0] - o8[0]) < 1e-9 and r1[0] < o8[0] and 0 < o8[1] - r1[1] < 0.3
    add("DAC_OUT", "F.Cu", wv, [o7, o8], "the two output pins of U5")
    add("DAC_OUT", "F.Cu", wv, [o8, (r1[0] + (o8[1] - r1[1]), o8[1]), r1], "U5 output to R331")
    return L


def _seg_pt(p, a, b):
    ax, ay, bx, by = a[0], a[1], b[0], b[1]
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy
    t = 0.0 if l2 == 0 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / l2))
    return ((p[0] - ax - t * dx) ** 2 + (p[1] - ay - t * dy) ** 2) ** 0.5


def _poly_pt(p, pts):
    return min(_seg_pt(p, a, b) for a, b in zip(pts, pts[1:]))


def build_finger_vias(j1pins, stubs):
    """One through via (0.40 / 0.20) at y = Y_FINGER_VIA for every run of adjacent VSS or VDD fingers (a front / back
    pair, 0.25 mm apart), tied to each finger of the run on that finger's own layer.

    Position: the axis of the back finger, else the axis of the front finger, else the middle of the pair - the
    first of these that keeps 0.05 mm more than the required clearance (0.10 mm; 0.20 mm to a reference stub) from
    every planned finger track (through via: both faces count), from every finger pad of another net and 0.50 mm
    from the neighbouring finger vias; if none does, the one with the largest margin."""
    net_of = {f: j1pins[str(f)] for f in range(1, 261)}
    r = FINGER_VIA["diameter"] / 2
    obstacles = []                                       # (centre line, width, required clearance)
    planned = set()
    for net, s in stubs.items():
        for sec in ([s["neck"], s["wide"]] if "neck" in s else [s]):
            obstacles.append((sec["path"], sec["width"], RULES["netclasses"]["VREF"]["finger_via_row_clearance"] if net.startswith("VREF") else 0.10))
        planned.add(s["finger"])
    for f in range(1, 261):                              # SCL, SDA, V3P3: lead-out not planned yet, keep the axis free
        if net_of[f] and net_of[f] not in ("GND", "V1P2") and f not in planned:
            obstacles.append(([P(finger_x(f), FINGER_START), P(finger_x(f), Y_FINGER_VIA + 0.55)], 0.20, 0.10))
    runs, cur = [], []
    for f in range(1, 261):
        n = net_of[f]
        if n in ("GND", "V1P2") and cur and net_of[cur[-1]] == n and abs(finger_x(f) - finger_x(cur[-1])) < 0.26:
            cur.append(f)
        else:
            if cur:
                runs.append(cur)
            cur = [f] if n in ("GND", "V1P2") else []
    if cur:
        runs.append(cur)
    out = []
    for run in runs:
        assert len(run) <= 2, run
        net = net_of[run[0]]
        back = [f for f in run if f % 2 == 0]
        front = [f for f in run if f % 2 == 1]
        cands = [(finger_x(f), "axis of finger %d" % f) for f in back + front]
        if len(run) == 2:
            cands.append((r3((finger_x(run[0]) + finger_x(run[1])) / 2), "middle of the pair"))

        def margin(x):
            c = (x, Y_FINGER_VIA)
            m = min([_poly_pt(c, path) - r - w / 2 - req for path, w, req in obstacles] or [9.9])
            for f in range(max(1, run[0] - 4), min(260, run[-1] + 4) + 1):
                if f in run or net_of[f] == net:
                    continue
                dx = max(abs(x - finger_x(f)) - FINGER_PAD_HALF_W, 0.0)
                m = min(m, (dx * dx + (Y_FINGER_VIA - FINGER_TOP) ** 2) ** 0.5 - r - 0.10)
            for v in out:
                m = min(m, abs(v["x"] - x) - 0.50)
            return m
        ms = [(margin(x), x, why) for x, why in cands]
        good = [q for q in ms if q[0] >= 0.05 - 1e-9]
        m, x, why = good[0] if good else max(ms, key=lambda q: q[0])
        assert m >= -1e-9, "no legal finger via for fingers %s (best margin %.3f)" % (run, m)
        ties = []
        for f in run:
            xf, d = finger_x(f), abs(finger_x(f) - x)
            path = [P(xf, FINGER_START)] + ([P(xf, Y_FINGER_VIA - d)] if d > 1e-9 else []) + [P(x, Y_FINGER_VIA)]
            ties.append(OrderedDict(finger=f, layer=side_of(f) + ".Cu", width=FINGER_VIA["tie_width"], path=path))
        out.append(OrderedDict(net=net, x=r3(x), y=Y_FINGER_VIA, diameter=FINGER_VIA["diameter"], drill=FINGER_VIA["drill"],
                               fingers=run, position=why, margin_over_required_clearance=round(m, 3), ties=ties))
    return out


# ---- delay estimates ---------------------------------------------------------------------------------
def cmd_card_delay(f, net, hook_t=None):
    """Card path of one command net as planned (no tuning), ps: finger top to ball."""
    g = GEOM["R0201"]
    y_pad1 = CMD_ROWS["series_resistor_centre"] - g["pad_offset"]
    y_node = CMD_ROWS["node_via"]
    stub = stub_len(divider_stub(f, net, CMD_COLUMNS[net], y_pad1))    # finger top to the series resistor pad
    series = 2 * g["pad_offset"] + (y_node - CMD_ROWS["series_resistor_centre"] - g["pad_offset"])
    zvia = Z_F_IN3 if f % 2 else Z_B_IN3
    w = 0.10 if net in ("CK_P", "CK_N") else 0.15
    in3 = TUNE.polyline_lengths(cmd_route(net, hook_t), w, BEND_MODEL)[1]        # electrical length (rules.bend_model)
    return (stub + series) * TD["ms20"] + zvia * TD["via"] + in3 * TD["In3"] + Z_F_IN3 * TD["via"]


Z_F_B = round(_mid_copper_z()["B.Cu"] - _mid_copper_z()["F.Cu"], 4)        # whole barrel, F.Cu to B.Cu: 1.157

def cmd_tune_shape(net, shift, amplitude):
    """Tuning feature of one command net (the dict scripts/tuning.py draws and measures)."""
    c = CMD_TUNE_NET[net]
    route = cmd_route(net)
    pat, rows = [0], []
    for i in range(c.get("bumps", 0)):
        k = c["row0"] + 2 * i
        pat += [1, 0]
        rows += [r3(CMD_TUNE["first_leg_y"] + CMD_TUNE["leg_pitch"] * k), r3(CMD_TUNE["first_leg_y"] + CMD_TUNE["leg_pitch"] * (k + 1))]
    return OrderedDict(x=CMD_COLUMNS[net], y=[route[0][1], route[-2][1]], shift=round(shift, 4), shift_y=list(CMD_TUNE["shift_y"]),
                       pattern=pat, rows=rows, amplitude=round(amplitude, 4), radius=c.get("radius"))


def cmd_tuning_plan(trim):
    """Per-net plan for the In3 tuning zone.  trim: ps to add per net.  The free parameter of every net (the shift
    of a shift-only net, the bump amplitude of the others) is solved so that the ELECTRICAL length of the feature
    adds exactly the trim; s20_cmd.py solves it again from the copper it has drawn."""
    from shapely.geometry import LineString, Point
    w = RULES["netclasses"]["CMD_SE"]["layers"]["In3.Cu"]["width"]
    out, lines = OrderedDict(), {}
    ref = json.load(open(HFSS_REF))["features"] if os.path.exists(HFSS_REF) else {}
    for net, c in CMD_TUNE_NET.items():
        route = cmd_route(net)
        straight = route[-2][1] - route[0][1]
        # HFSS of this net's own feature against the formula, for the shape that was simulated
        hf = None
        if net in ref:
            sim = dict(ref[net]["shape"])
            g_s, e_s = TUNE.lengths(TUNE.vertices(sim), w, BEND_MODEL)
            st_s = sim["y"][1] - sim["y"][0]
            hf = OrderedDict(run=ref[net]["run"], simulated_shift=sim["shift"], simulated_amplitude=sim["amplitude"],
                             simulated_radius=sim.get("radius"), simulated_rows=sim.get("rows") or [],
                             hfss_electrical_added_mm=ref[net]["hfss_electrical_added_mm"],
                             formula_electrical_added_mm=round(e_s - st_s, 4),
                             residual_mm=round(ref[net]["hfss_electrical_added_mm"] - (e_s - st_s), 4))
        residual = hf["residual_mm"] if hf else 0.0
        want = straight + trim[net] / TD["In3"] - residual
        if isinstance(c["shift"], str):
            sign = -1.0 if c["shift"] == "west" else 1.0
            sh = cmd_tune_shape(net, sign * 0.3, 0.0)
            sh["shift"] = round(sign * TUNE.solve(sh, want, w, BEND_MODEL, "shift", 0.02, CMD_TUNE["shift_max"]), 4)
            solve = "shift"
        else:
            sh = cmd_tune_shape(net, c["shift"], 0.5)
            sh["amplitude"] = round(TUNE.solve(sh, want, w, BEND_MODEL, "amplitude", 2 * c["radius"], CMD_TUNE["amplitude_max"]), 4)
            solve = "amplitude"
            assert sh["amplitude"] >= CMD_TUNE["amplitude_min"] and c["radius"] >= CMD_TUNE["radius_min"], (net, sh)
        verts = TUNE.vertices(sh)
        geo, el = TUNE.lengths(verts, w, BEND_MODEL)
        lines[net] = LineString(TUNE.centre_line(verts))
        out[net] = OrderedDict(
            solve=solve, shape=sh, trim_ps=trim[net], bumps=c.get("bumps", 0), arcs=sum(1 for v in verts if v[2]),
            mitred_vertices=sum(1 for v in verts[1:-1] if not v[2]),
            added_centre_line_mm=round(geo - straight, 3), added_electrical_mm=round(el - straight + residual, 3),
            bend_correction_ps=round((geo - el - residual) * TD["In3"], 2),
            hfss=hf, hfss_residual_mm=residual)
    # gaps, copper to copper, between the planned centre lines (CK: the coupled pair on ball column 9)
    xk = ball_xy("H9")[0]
    nets = list(CMD_TUNE_NET)
    ck = {n: LineString([tuple(q) for q in cmd_route(n)[2:5]]) for n in ("CK_N", "CK_P")}
    vias = [(v, CMD_ROWS["gnd_via"]) for v in (cmd_gnd_via_x(k, x) for k, x in enumerate(CMD_COLUMNS.values()))]
    for i, net in enumerate(nets):
        g = OrderedDict()
        for other in nets[max(0, i - 1):i] + nets[i + 1:i + 2]:
            if abs(CMD_COLUMNS[other] - CMD_COLUMNS[net]) < 1.0:
                g[other] = round(lines[net].distance(lines[other]) - w, 3)
        for n, l in ck.items():
            if abs(CMD_COLUMNS[n] - CMD_COLUMNS[net]) < 1.0:
                g["CK pair"] = round(lines[net].distance(l) - w / 2 - 0.05, 3)
        out[net]["gap_mm"] = g
        out[net]["fence_via_pad_clearance_mm"] = round(min(lines[net].distance(Point(v)) for v in vias) - 0.20 - w / 2, 3)
        xs = [q[0] for q in lines[net].coords]
        out[net]["x_extent"] = [r3(min(xs)), r3(max(xs))]
    del xk
    return out


def build_groups(td):
    hp = lambda f: float(td[f]["total_mid_ps"])
    groups = []
    # ---- CMD -----------------------------------------------------------------------------
    import math as _m
    est = OrderedDict()
    for f, n in CMD_FINGERS.items():
        est[n] = cmd_card_delay(f, n)
    # the longest path sets the total: it gets no tuning, every other net is brought up to it
    total = _m.ceil(max(hp(f) + est[n] for f, n in CMD_FINGERS.items()) * 100 - 1e-6) / 100
    order = ["CK_P", "CK_N", "CS0", "CA0", "CA1", "CA2", "CA3", "CA4", "CA5", "CA6"]
    fing = {n: f for f, n in CMD_FINGERS.items()}
    tgt = OrderedDict((n, round(total - hp(fing[n]), 2)) for n in order)
    # CK_P: the hook into H9 takes up the difference to CK_N (2 t of In3 per t)
    t_hook = CK_SPLIT["t_min"] + max(0.0, tgt["CK_P"] - est["CK_P"]) / TD["In3"] / 2
    t_hook = round(min(t_hook, CK_SPLIT["t_max"]), 3)
    est["CK_P"] = cmd_card_delay(fing["CK_P"], "CK_P", t_hook)
    trim = OrderedDict((n, round(tgt[n] - est[n], 2)) for n in order)
    plan = cmd_tuning_plan(trim)
    xk, yh = ball_xy("H9")
    groups.append(OrderedDict(
        name="CMD", nets=order, finger_side_nets=[fnet(n) for n in order],
        layer="In3.Cu (between In2 GND and In4 GND); finger-side stubs on the finger's own outer layer",
        match="delay from the FPGA die pad to the U1 ball (host copper + FPGA package + card)",
        tolerance_ps=2.0, intra_pair=OrderedDict(nets=["CK_P", "CK_N"], finger_side_nets=[fnet("CK_P"), fnet("CK_N")],
                                                tolerance_ps=1.0),
        total_delay_target_ps=total,
        per_net_host_plus_pkg_ps=OrderedDict((n, hp(fing[n])) for n in order),
        per_net_target_card_delay_ps=tgt,
        per_net_min_card_delay_estimate_ps=OrderedDict((n, round(est[n], 2)) for n in order),
        per_net_trim_ps=trim,
        per_net_trim_mm_on_in3=OrderedDict((n, round(trim[n] / TD["In3"], 3)) for n in order),
        trim_total_mm=round(sum(trim.values()) / TD["In3"], 1),
        tuning=OrderedDict(
            where="In3, in the straight run between the fence vias (y = %.2f) and row A of U1 (y = %.2f): the 45-degree "
                  "runs of the shifts leave the channel axis at y = %.2f and rejoin it at y = %.2f; the copper of the "
                  "bump legs stays inside y %.2f .. %.2f"
                  % (CMD_ROWS["gnd_via"], ball_xy("A1")[1], CMD_TUNE["shift_y"][0], CMD_TUNE["shift_y"][1], CMD_TUNE["y"][0], CMD_TUNE["y"][1]),
            meander="few, large bends (rules.bend_model): every net runs parallel to its channel axis, shifted away from "
                    "the CK corridor between two 45-degree runs (the west half fans out to the west, the east half to the "
                    "east); nets that need more add rounded bumps on the shifted run, all pointing east, legs %.2f mm "
                    "apart (gap 0.33 mm = 3.0 h) on the common rows y = %.2f + k x %.2f, corner radius >= %.2f mm, "
                    "amplitude >= %.2f mm; >= %.2f mm (3 h) to the neighbouring trace. No bump faces the CK pair closer "
                    "than the pair rule"
                    % (CMD_TUNE["leg_pitch"], CMD_TUNE["first_leg_y"], CMD_TUNE["leg_pitch"], CMD_TUNE["radius_min"],
                       CMD_TUNE["amplitude_min"], CMD_TUNE["min_gap"]),
            leg_pitch=CMD_TUNE["leg_pitch"], first_leg_y=CMD_TUNE["first_leg_y"], rows=CMD_TUNE["rows"],
            shift_y=CMD_TUNE["shift_y"], min_gap_to_neighbour=CMD_TUNE["min_gap"],
            amplitude_min=CMD_TUNE["amplitude_min"], amplitude_max=CMD_TUNE["amplitude_max"],
            radius_min=CMD_TUNE["radius_min"],
            per_net=plan, largest_trim_ps=max(trim.values()),
            delay_model="trims are electrical: centre line less the bend corrections of rules.bend_model",
            ck="the CK pair is not meandered. It is the longest path of the group and sets the total. The row "
               "difference between H9 and J9 is made up by the hook of CK_P (floorplan.command.ck_split)",
            ck_hook=OrderedDict(t=t_hook, corner=[r3(xk + t_hook), r3(yh + t_hook)], t_range=[CK_SPLIT["t_min"], CK_SPLIT["t_max"]],
                                in3_per_t_mm=2.0, residual_ps=trim["CK_P"])),
        note="card delay = copper from the finger top (y = %.2f) to the ball, vias by height between the middles of "
             "the copper layers, both sides of the series resistor counted, In3 bends by their electrical length "
             "(rules.bend_model); no common offset is used: the board is tuned to these targets; the host + "
             "package figures are good to about +/-5 ps, so the residual is removed per bit with ODELAY; "
             "no tuning inside the BGA field" % FINGER_TOP))
    # ---- the data side: one plan (scripts/data_side.py) ---------------------------------------------------------------
    dp = data_plan(td)
    dl, kn = dp["delays"], dp["knobs"]
    dq = ["DQ%d" % i for i in range(8)]
    # ---- WR --------------------------------------------------------------------------------
    w = dl["write"]
    wt = [w[n]["total_ps"] for n in dq]
    w_centre, w_half, w_late = (max(wt) + min(wt)) / 2, (max(wt) - min(wt)) / 2, max(wt)
    groups.append(OrderedDict(
        name="WR", nets=dq, finger_side_nets=[fnet(n) for n in dq],
        layer="F.Cu (microstrip over In1 GND), no via at the ball; the series resistor on the finger's face",
        match="NOT matched on the card. Delay from the FPGA die pad to the U1 ball (write finger, series resistor, node "
              "via, tooth, line, neck) as it falls in the comb; the per-bit output delay of the FPGA closes it (deskew)",
        matched_on_the_card=False,
        tolerance_ps=RULES["netclasses"]["WR_DQ"]["write_plan_tolerance_ps"],
        tolerance_of="each net against its own planned total (per_net_planned_total_ps), not against a common target",
        total_delay_target_ps=None,
        total_delay_centre_ps=round(w_centre, 2), spread_half_ps=round(w_half, 2), group_rule_ps=10.0,
        per_net_host_plus_pkg_ps=OrderedDict((n, w[n]["host_ps"]) for n in dq),
        per_net_target_card_delay_ps=OrderedDict((n, round(w[n]["card_ps"], 2)) for n in dq),
        per_net_planned_card_delay_ps=OrderedDict((n, round(w[n]["card_ps"], 2)) for n in dq),
        per_net_planned_total_ps=OrderedDict((n, round(w[n]["total_ps"], 2)) for n in dq),
        per_net_to_centre_ps=OrderedDict((n, round(w[n]["total_ps"] - w_centre, 2)) for n in dq),
        per_net_bend_correction_ps=OrderedDict((n, round(w[n]["bend_ps"], 2)) for n in dq),
        per_net_length_mm=OrderedDict((n, w[n]["mm"]) for n in dq),
        per_net_node_via_barrel_mm=OrderedDict((n, (w[n]["barrels_mm"] or [0.0])[0]) for n in dq),
        meander_total_mm=0.0,
        deskew=OrderedDict(
            status='accepted by the user ("The write group\'s spread is acceptable")',
            method="per-bit output delay in the FPGA (ODELAYE3 of the HP bank, DELAY_FORMAT = TIME): each bit is delayed by "
                   "the latest total of the group minus its own total, so that all eight arrive with the latest one",
            latest_total_ps=round(w_late, 2),
            per_bit_output_delay_ps=OrderedDict((n, round(w_late - w[n]["total_ps"], 1)) for n in dq),
            file="design/gateware_deskew.json (written by gate 7 of scripts/pcb/check_pcb.py from the delays measured on the board)",
            why_not_on_the_card="a comb on one layer is planar for one order only (the lowest line takes the westmost "
                                "column), and the corner escape puts the two balls with the longest necks (E6, F5) on the two "
                                "top lines, which also get the two farthest columns. Finger choice moves about 8 ps (bank 64 "
                                "hosts span 602 .. 615 ps). Closing it to +/-10 ps on the card needs about 20 mm of serpentine "
                                "on six nets, three of which are interior lines at 0.40 mm gap from ball to node with no room "
                                "for it"),
        tuning=OrderedDict(method="none: the group is planned as it falls (no serpentine, no trim feature); see deskew",
                           bend_model="rules.bend_model applied to every mitred vertex of the path on F.Cu / B.Cu as an estimate "
                                      "(%.2f .. %.2f ps per net)" % (min(v["bend_ps"] for v in w.values()), max(v["bend_ps"] for v in w.values()))),
        note="card delay = finger top (y = %.2f) -> stub -> series resistor (0.64 mm) -> node via (barrel between the middles "
             "of the copper layers: none for a front finger, the whole board for a back finger) -> tooth -> line -> neck -> "
             "ball. The totals span %.1f .. %.1f ps (centre %.1f, +/-%.1f): +/-%.1f ps is %.2f %% of the 10 ns unit interval "
             "at 100 Mb/s, and the write timing is trained per bit in the gateware anyway (tWCK2DQI 300 .. 900 ps)"
             % (FINGER_TOP, min(wt), max(wt), w_centre, w_half, w_half, w_half / 100.0)))
    # ---- WR_WCK ----------------------------------------------------------------------------
    k = dl["wck"]
    wck_skew = k["WCK_P"]["total_ps"] - k["WCK_N"]["total_ps"]
    groups.append(OrderedDict(
        name="WR_WCK", nets=["WCK_P", "WCK_N"], finger_side_nets=["WCK_F_P", "WCK_F_N"],
        layer="B.Cu (microstrip pair over In4 GND) between the via-in-pads of E4 / D5 and the node vias; "
              "F.Cu only inside the two divider columns (series resistor, node via, shunt in line)",
        match="P to N only; WCK is not matched to DQ (tWCK2DQI is 300..900 ps: write timing is trained in gateware)",
        tolerance_ps=1.0,
        polarity="WCK_P (= WCK_t, ball E4) is on finger 139, the N pin of the host pair (AG18); WCK_N (= WCK_c, ball D5) on "
                 "finger 137, the P pin (AF18): the gateware drives the pair inverted. Reason: the B.Cu pair turns south "
                 "into its two divider columns with the E4 leg on the east",
        per_net_host_plus_pkg_ps=OrderedDict((n, k[n]["host_ps"]) for n in ("WCK_P", "WCK_N")),
        per_net_target_card_delay_ps=OrderedDict((n, round(k[n]["card_ps"], 2)) for n in ("WCK_P", "WCK_N")),
        per_net_planned=OrderedDict((n, OrderedDict(card_ps=round(k[n]["card_ps"], 2), bend_ps=round(k[n]["bend_ps"], 2), mm=k[n]["mm"],
                                                    barrels_mm=k[n]["barrels_mm"])) for n in ("WCK_P", "WCK_N")),
        per_net_planned_total_ps=OrderedDict((n, round(k[n]["total_ps"], 2)) for n in ("WCK_P", "WCK_N")),
        planned_skew_at_the_balls_ps=round(wck_skew, 2),
        skew_bump=None,
        wck_minus_dq_centre_ps=round((k["WCK_P"]["total_ps"] + k["WCK_N"]["total_ps"]) / 2 - w_centre, 1),
        note="no skew bump: the two legs end differently (WCK_N straight into its node via, WCK_P on one 45-degree run of "
             "1.0 mm), which takes up the ball-row difference between E4 and D5 to %+.2f ps with the host. Both legs pass "
             "the same two barrels and the same 0.20 mm links" % wck_skew))
    # ---- RD_P --------------------------------------------------------------------------------
    r = dl["read"]
    rfing = OrderedDict((n, DATA.READ_FINGER[n][0]) for n in dq)
    rt = [r[n]["total_ps"] for n in dq]
    r_centre, r_half, r_late = (max(rt) + min(rt)) / 2, (max(rt) - min(rt)) / 2, max(rt)
    total = kn["read_target_ps"]            # the centre of the group, rounded to 0.1 ps: the reference the strobe is planned against
    assert abs(total - r_centre) <= 0.06 and kn["read_end_crossings"] == 0
    rd_tol = DATA.READ_PLAN_TOL
    rdp = dp["read"]
    groups.append(OrderedDict(
        name="RD_P", nets=dq, ball_side_nets=dq,
        layer="F.Cu line and tooth (shared with WR) + node via + In3 branch from the node via to the read via (In3 between "
              "In2 GND and In4 GND) + the read finger's own outer layer from that via down to the finger; one net per bit",
        match="NOT matched on the card. Delay from the U1 ball to the FPGA die pad (line + branch + host copper + FPGA package, bends by "
              "rules.bend_model) as it falls with the read fingers taken in the order of the In3 bundle (no read-end crossing); the per-bit "
              "input delay of the FPGA closes it (deskew)",
        matched_on_the_card=False,
        tolerance_ps=rd_tol,
        tolerance_of="each net against its own planned total (per_net_planned_total_ps), not against a common target",
        total_delay_target_ps=None,
        total_delay_centre_ps=round(r_centre, 2), spread_half_ps=round(r_half, 2), group_rule_ps=10.0,
        per_net_host_plus_pkg_ps=OrderedDict((n, r[n]["host_ps"]) for n in dq),
        per_net_target_card_delay_ps=OrderedDict((n, round(r[n]["card_ps"], 2)) for n in dq),
        per_net_planned_card_delay_ps=OrderedDict((n, round(r[n]["card_ps"], 2)) for n in dq),
        per_net_planned_total_ps=OrderedDict((n, round(r[n]["total_ps"], 2)) for n in dq),
        per_net_to_centre_ps=OrderedDict((n, round(r[n]["total_ps"] - r_centre, 2)) for n in dq),
        per_net_bend_correction_ps=OrderedDict((n, round(r[n]["bend_ps"], 2)) for n in dq),
        per_net_length_mm=OrderedDict((n, r[n]["mm"]) for n in dq),
        level_y=OrderedDict((n, rdp[n]["level_y"]) for n in dq),
        raised_mm=OrderedDict((n, rdp[n]["raised_mm"]) for n in dq),
        meander_total_mm=0.0,
        deskew=OrderedDict(
            status='accepted by the user ("Same with read group"): built as the read end without crossings',
            method="per-bit input delay in the FPGA (IDELAYE3 of the HP bank, DELAY_FORMAT = TIME, DELAY_VALUE in ps; range 0 .. 1100 ps, "
                   "512 taps of 2.1 .. 12 ps): each bit is delayed by the latest total of the group minus its own total, so that all eight "
                   "arrive at the capture flops with the latest one",
            latest_total_ps=round(r_late, 2),
            per_bit_input_delay_ps=OrderedDict((n, round(r_late - r[n]["total_ps"], 1)) for n in dq),
            idelay_range_ps=[0, 1100], idelay_taps=512, idelay_tap_ps=[2.1, 12.0],
            file="design/gateware_deskew.json (written by gate 7 of scripts/pcb/check_pcb.py from the delays measured on the board)",
            why_not_on_the_card="the user asked for fewer crossings between layers and allowed the DQ order to change in the gateware. With the "
                                "In3 lines in the order of the comb (fixed) the read fingers can be taken so that no finger track crosses an "
                                "In3 line of another bit in exactly one order: the lowest line (the east column, DQ7) ends first, on the "
                                "westmost finger. That order makes the short lines shorter and the long ones longer, and no level set "
                                "helps (a raised line takes the lines above it with it, and the late lines are the top ones): +/-%.1f ps. "
                                "Matching it on the card takes 20 read-end crossings (the comb as built before: +/-9.7 ps)" % r_half),
        tuning=OrderedDict(
            method="none: no serpentine, no raised level. The read vias stand on a regular staircase, 0.55 mm per line from y = %.2f up "
                   "in the order of the bundle (1 mm of level would be %.2f ps); see deskew" % (DATA.Y_NODE + DATA.RAMP, kn["ps_per_mm_of_level"]),
            ps_per_mm_of_level=kn["ps_per_mm_of_level"], read_end_crossings=kn["read_end_crossings"],
            half_spread_ps=kn["read_half_spread_ps"], centre_ps=kn["read_centre_ps"],
            assignment="the eight admissible P / N pairs of bank 66 (182 .. 215) taken west -> east by the In3 lines bottom -> top: the only "
                       "assignment without a read-end crossing (floorplan.data.read_end_choice)"),
        note="card delay = ball -> neck -> line -> tooth -> node via (F.Cu to In3) -> In3 branch -> read via (In3 to the "
             "finger's face) -> finger track -> read finger top (y = %.2f); barrels between the middles of the copper "
             "layers; every mitred vertex by rules.bend_model. The totals span %.1f .. %.1f ps (centre %.1f, +/-%.1f): the per-bit "
             "input delays are 0 .. %.1f ps, a tenth of the IDELAY range; the host figures in them are good to about +/-5 ps, so "
             "read training refines them" % (FINGER_TOP, min(rt), max(rt), r_centre, r_half, r_late - min(rt))))
    # ---- RD_RDQS -----------------------------------------------------------------------------
    q = dl["rdqs"]
    rq_skew = q["RDQS_P"]["total_ps"] - q["RDQS_N"]["total_ps"]
    # the strobe-to-data match over the outer copper / mask the fab may deliver (design/impedance_results.json): the DQ are single
    # microstrips and stripline, RDQS is stripline and an edge-coupled microstrip pair, whose velocity depends more on mask and plating
    imp_ = json.load(open(os.path.join(ROOT, "design", "impedance_results.json")))
    outer_cases = OrderedDict()
    for cname, cv in imp_["outer_copper_and_mask_cases"].items():
        s_ = {round(x["w"], 2): x["td_ps_mm"] for x in cv["single"]}
        tdv = dict(TD, ms09=s_[0.09], ms15=s_[0.15], ms20=s_[0.2], pair=cv["pair"][0]["td_odd_ps_mm"])
        mv = DATA.Model(tdv, BEND_MODEL, _mid_copper_z(), ["WCK_P", "WCK_N", "RDQS_P", "RDQS_N"])
        dq_t = [mv.chain(n, DATA.read_chain(n, dp["assign"][n], dp["levels"][n]))["ps"] + hp(dp["assign"][n]) for n in DATA.LEVELS]
        rq_t = [mv.chain(n, DATA.rdqs_chain(n, dp["rdqs_paths"]))["ps"] + hp(DATA.RDQS_FINGER[n]) for n in ("RDQS_P", "RDQS_N")]
        st_ = sum(rq_t) / 2
        outer_cases[cname] = OrderedDict(outer_copper_mm=cv["t_outer"], mask_mm=cv["t_mask"],
                                         ps_per_mm=OrderedDict(w009=round(s_[0.09], 3), w015=round(s_[0.15], 3), w020=round(s_[0.2], 3), pair_odd=round(tdv["pair"], 3)),
                                         dq_half_spread_ps=round((max(dq_t) - min(dq_t)) / 2, 2),
                                         strobe_minus_latest_dq_ps=round(st_ - max(dq_t), 2), strobe_minus_earliest_dq_ps=round(st_ - min(dq_t), 2),
                                         strobe_minus_dq_centre_ps=round(st_ - (max(dq_t) + min(dq_t)) / 2, 2),
                                         p_minus_n_ps=round(rq_t[0] - rq_t[1], 2))
    assert abs(rq_skew) <= 0.05, (q["RDQS_P"]["total_ps"], q["RDQS_N"]["total_ps"], total)
    rq_total = (q["RDQS_P"]["total_ps"] + q["RDQS_N"]["total_ps"]) / 2
    rdqs_f = [DATA.RDQS_FINGER["RDQS_P"], DATA.RDQS_FINGER["RDQS_N"]]
    groups.append(OrderedDict(
        name="RD_RDQS", nets=["RDQS_P", "RDQS_N"],
        layer="In3 (pair between In2 GND and In4 GND) from the via-in-pads of B3 / C4 to the via pair just above the fingers, %s (microstrip "
              "pair over %s GND) for the last %.2f mm into fingers %d / %d; no series part"
              % (DATA.RDQS_OUTER, "In1" if DATA.RDQS_OUTER == "F.Cu" else "In4", kn["rdqs_outer_drop_mm"], rdqs_f[0], rdqs_f[1]),
        match="P to N within 1 ps. NOT matched to the read DQ group (which is not matched on the card): each leg is held to its own "
              "planned total; the pair stays on In3 to a via pair just above its fingers (like the read DQ lines) "
              "and arrives %+.1f ps from the centre of the read group, a figure of the deskew table" % (rq_total - r_centre),
        tolerance_ps=1.0, tolerance_to_dq_ps=None,
        total_delay_target_ps=None, read_group_centre_ps=round(r_centre, 2),
        polarity="RDQS_P (= RDQS_t, ball B3) is on finger 219, the N pin of the host pair DQS6 (AL8); RDQS_N (= RDQS_c, ball "
                 "C4) on finger 221, the P pin (AK8): the gateware inverts the strobe. Reason: the pair comes down between "
                 "the two fingers with the C4 leg on the east",
        per_net_host_plus_pkg_ps=OrderedDict((n, q[n]["host_ps"]) for n in ("RDQS_P", "RDQS_N")),
        per_net_target_card_delay_ps=OrderedDict((n, round(q[n]["card_ps"], 2)) for n in ("RDQS_P", "RDQS_N")),
        per_net_planned=OrderedDict((n, OrderedDict(card_ps=round(q[n]["card_ps"], 2), bend_ps=round(q[n]["bend_ps"], 2),
                                                    leg_coupling_ps=round(q[n]["leg_coupling_ps"], 2), mm=q[n]["mm"],
                                                    barrels_mm=q[n]["barrels_mm"])) for n in ("RDQS_P", "RDQS_N")),
        per_net_planned_total_ps=OrderedDict((n, round(q[n]["total_ps"], 2)) for n in ("RDQS_P", "RDQS_N")),
        planned_skew_at_the_fpga_ps=round(rq_skew, 2),
        strobe_minus_dq_group_ps=round(rq_total - r_centre, 2),
        strobe_minus_latest_dq_ps=round(rq_total - r_late, 2), strobe_minus_earliest_dq_ps=round(rq_total - min(rt), 2),
        via_pair=OrderedDict(x=[v_[0] for v_ in dp["rdqs"]["via_pair"]], y=kn["rdqs_via_y"], return_vias=dp["rdqs"]["return_vias"],
                             outer_drop_mm=kn["rdqs_outer_drop_mm"],
                             note="the pair changes layer once, just above its fingers 219 / 221: In3 north of the via pair, %.2f mm of %s south of it "
                                  "(finger top to via centre), like the read DQ lines (5.5 .. 9.4 mm). y = %.2f is the lowest position of a through via "
                                  "there: its B.Cu pad keeps 0.90 mm from the VREF_RD bus on B.Cu (y = %.2f). The position is no delay knob any more: the "
                                  "strobe is not matched to the read group (%.1f ps), its offset is in the deskew table. Two GND return vias, mirror "
                                  "images about the pair axis, %.2f mm from the via of their side"
                                  % (kn["rdqs_outer_drop_mm"], DATA.RDQS_OUTER, kn["rdqs_via_y"], DATA.Y_BUS_B, r_centre,
                                     math.hypot(DATA.RDQS_RETURN_DX - DATA.RDQS_VIA_DX, DATA.RDQS_RETURN_DY))),
        skew_bump=dp["rdqs"]["bump"],
        outer_layer_cases=outer_cases,
        outer_layer_note="the same copper evaluated with the ps / mm of each outer copper / mask case of design/impedance_results.json: the "
                         "offset of the strobe from the centre of the DQ group moves by a few picoseconds with the outer copper / mask the fab "
                         "delivers (%.1f of the %.1f mm of the pair are on the outer layer); the read group is not matched on the card and the "
                         "per-bit input delays are trained, so this is a start value, not a tolerance"
                         % (q["RDQS_N"]["mm"].get(DATA.RDQS_OUTER, 0.0), sum(q["RDQS_N"]["mm"].values())),
        note="without the bump RDQS_P would arrive before RDQS_N: C4 lies one ball row beyond B3 and RDQS_P is the "
             "inner leg of the turn into the drop (the two corners of the north-east run on In3 cancel). One bump on "
             "RDQS_P, on In3 0.5 mm east of the land field of U1 (where the skew arises, so that the pair is in step "
             "over its whole length), pointing south (away from RDQS_N): two legs %.2f mm apart (gap %.2f mm = %.1f h), "
             "%.3f mm deep, eight 45-degree vertices; bends by rules.bend_model at 0.10 mm, the coupling of the two legs included"
             % (DATA.RDQS_BUMP["leg_pitch"], DATA.RDQS_BUMP["leg_pitch"] - 0.10, (DATA.RDQS_BUMP["leg_pitch"] - 0.10) / 0.11,
                kn["rdqs_bump_depth_mm"])))
    # ---- RD_N --------------------------------------------------------------------------------
    groups.append(OrderedDict(
        name="RD_N", nets=["VREF_N%d" % i for i in range(8)], layer="outer layer of the N finger (F.Cu or B.Cu)",
        match="none (DC reference); every N stub has the same structure: finger, at most 5 mm of 0.15 mm track, "
              "39 ohm, VREF_RD bus, 100 nF to the neighbouring VSS finger",
        tolerance_ps=None,
        per_net_host_plus_pkg_ps=OrderedDict(("VREF_N%d" % bit, hp(nf)) for pf, (nf, bit) in RD_PAIRS.items()),
        per_net_target_card_delay_ps=OrderedDict(("VREF_N%d" % bit, None) for pf, (nf, bit) in RD_PAIRS.items())))
    return groups


# U1 via-in-pads that are left out, and the F.Cu link that feeds each of those balls instead
VIA_OMITTED = OrderedDict([
    ("A9", OrderedDict(reason="CK corridor on In3", f_cu_link_to=["A8"])),
    ("B9", OrderedDict(reason="CK corridor on In3", f_cu_link_to=["A9"])),
    ("C9", OrderedDict(reason="CK corridor on In3", f_cu_link_to=["B9"])),
    ("D9", OrderedDict(reason="CK corridor on In3", f_cu_link_to=["D10"])),
    ("E9", OrderedDict(reason="CK corridor on In3", f_cu_link_to=["F9"])),
    ("F9", OrderedDict(reason="CK corridor on In3", f_cu_link_to=["F10"])),
    ("G9", OrderedDict(reason="CK corridor on In3", f_cu_link_to=["H8", "H10"])),
])


# ----------------------------------------------------------------------------------------------
# 5b. PCB skeleton: board origin, placement of every remaining part, explicit U1 fan-out
#     (written back from the layout phase so that the PCB never holds a position the spec does not)
# ----------------------------------------------------------------------------------------------
KICAD_FP = "/usr/share/kicad/footprints"
BOARD_ORIGIN = (100.0, 130.0)     # KiCad page position of the card origin: kicad = (100 + x, 130 - y)
VIP = OrderedDict(diameter=0.40, drill=0.15)
IN3_PAD_TRIM = 0.35      # In3 pad of the via-in-pads of C4 / B3 (the RDQS pair leaves them on In3, beside the CA0 trace): annular ring 0.10 mm


def _sexpr(text):
    """Minimal s-expression reader: nested lists of strings."""
    import re as _re
    tok = _re.findall(r'"(?:[^"\\]|\\.)*"|[()]|[^\s()]+', text)
    stack, cur = [], []
    for t in tok:
        if t == "(":
            stack.append(cur)
            cur = []
        elif t == ")":
            done, cur = cur, stack.pop()
            cur.append(done)
        else:
            cur.append(t[1:-1] if t.startswith('"') else t)
    return cur[0]


_FP_CACHE = {}


def fp_geom(lib_name):
    """Pads {number: (x, y)} and courtyard bounding box (x0, y0, x1, y1) of a footprint, library frame (y down)."""
    if lib_name in _FP_CACHE:
        return _FP_CACHE[lib_name]
    lib, name = lib_name.split(":")
    base = os.path.join(ROOT, "lib", lib + ".pretty") if lib == "lpddr5_testbed" else os.path.join(KICAD_FP, lib + ".pretty")
    tree = _sexpr(open(os.path.join(base, name + ".kicad_mod")).read())
    pads, xs, ys = OrderedDict(), [], []

    def sub(node, key):
        return next((c for c in node if isinstance(c, list) and c and c[0] == key), None)
    for n in tree:
        if not isinstance(n, list) or not n:
            continue
        if n[0] == "pad" and n[1] != "":
            lay = sub(n, "layers")
            if lay and any(l.endswith(".Cu") for l in lay[1:]):
                at = sub(n, "at")
                q = (float(at[1]), float(at[2]))
                # a number used by several pads (exposed pad plus its tabs): keep the one at the centre
                if n[1] not in pads or abs(q[0]) + abs(q[1]) < abs(pads[n[1]][0]) + abs(pads[n[1]][1]):
                    pads[n[1]] = q
        elif n[0] in ("fp_line", "fp_rect", "fp_poly", "fp_circle") and (sub(n, "layer") or [None, ""])[1] == "F.CrtYd":
            if n[0] == "fp_circle":
                c, e = sub(n, "center"), sub(n, "end")
                r = ((float(e[1]) - float(c[1])) ** 2 + (float(e[2]) - float(c[2])) ** 2) ** 0.5
                xs += [float(c[1]) - r, float(c[1]) + r]
                ys += [float(c[2]) - r, float(c[2]) + r]
            elif n[0] == "fp_poly":
                for q in sub(n, "pts")[1:]:
                    xs.append(float(q[1]))
                    ys.append(float(q[2]))
            else:
                for k in ("start", "end"):
                    q = sub(n, k)
                    xs.append(float(q[1]))
                    ys.append(float(q[2]))
    out = (pads, (min(xs), min(ys), max(xs), max(ys)) if xs else None)
    _FP_CACHE[lib_name] = out
    return out


def fp_to_card(xl, yl, side, rot_deg, x, y):
    """Footprint library point -> card frame.  rot_deg is the KiCad footprint orientation (counter-clockwise seen
    from the front); a part on the back has its library drawing mirrored top-bottom (y -> -y) first, as KiCad does."""
    import math as _m
    if side == "B":
        yl = -yl
    c, s_ = _m.cos(_m.radians(rot_deg)), _m.sin(_m.radians(rot_deg))
    return (r3(x + xl * c + yl * s_), r3(y + xl * s_ - yl * c))


# ref -> (side, x, y, rot_deg, note).  Positions were chosen in the layout phase inside the regions below.
SUPPORT = OrderedDict([
    # ---- under U1 on B.Cu, directly on the via-in-pads --------------------------------------------------
    ("R401", ("B", 15.125, 16.55, 270, "ZQ: pad 1 on the H15 via, pad 2 on the G15 (VDDQ) via")),
    ("R404", ("B", 16.925, 14.80, 180, "WCK1_t: pad 1 on the E12 via, pad 2 on the E13 (VSS) via")),
    ("R405", ("B", 18.325, 14.50, 90, "WCK1_c: pad 1 on the D11 via, pad 2 on the E11 (VDDQ) via; 0.12 mm to the C11 via")),
    ("C401", ("B", 21.125, 12.70, 180, "VDD2H B7 / VSS B8")),
    ("C402", ("B", 21.125, 14.10, 0, "VDD2H D8 / VSS D7")),
    ("C403", ("B", 21.125, 15.50, 180, "VDD2H F7 / VSS F8")),
    ("C404", ("B", 18.725, 12.00, 180, "VDD2L A10 / VSS A11")),
    ("C405", ("B", 16.405, 17.60, 0, "VDD2L J14 / VSS J13")),
    ("C406", ("B", 19.525, 18.30, 0, "VDD2H K10 / VSS K9")),
    ("C407", ("B", 21.925, 18.30, 180, "VDD2H K6 / VSS K7")),
    ("C408", ("B", 19.525, 19.70, 0, "VDD2H M10 / VSS M9")),
    ("C409", ("B", 21.925, 19.70, 180, "VDD2H M6 / VSS M7")),
    ("C410", ("B", 25.125, 17.60, 180, "VDD2L J2 / VSS J3")),
    ("C411", ("B", 16.325, 20.40, 0, "VDD2L N14 / VSS N13")),
    ("C412", ("B", 25.125, 20.40, 180, "VDD2L N2 / VSS N3")),
    ("C413", ("B", 15.990, 13.40, 90, "VDDQ B14 / VSS D14 (bridges the via-less DQ9 ball C14)")),
    ("C414", ("B", 16.405, 16.20, 0, "VDDQ G14 / VSS G13")),
    ("C415", ("B", 24.325, 12.00, 180, "VDDQ A3 / VSS A4 (the new via-in-pad of A4); moved off the diagonal D3 - C2 - B1 of the WCK pair")),
    ("C416", ("B", 25.125, 16.20, 180, "VDDQ G2 / VSS G3")),
    ("C417", ("B", 16.325, 21.80, 0, "VDDQ R14 / VSS R13")),
    ("C418", ("B", 25.125, 21.80, 180, "VDDQ R2 / VSS R3")),
    ("C419", ("B", 15.990, 24.60, 270, "VDDQ Y14 / VSS V14")),
    ("C420", ("B", 25.460, 24.60, 270, "VDDQ Y2 / VSS V2")),
    ("C421", ("B", 26.390, 14.10, 90, "VDD1 C1 / VSS E1")),
    ("C422", ("B", 15.060, 14.10, 90, "VDD1 C15 / VSS E15")),
    ("C423", ("B", 26.390, 23.90, 270, "VDD1 W1 / VSS U1")),
    ("C424", ("B", 15.060, 23.90, 270, "VDD1 W15 / VSS U15")),
    # ---- reference DAC (F.Cu) ------------------------------------------------------------------------------
    ("U5", ("F", 64.65, 9.80, 180, "SCL / SDA pins toward fingers 253 / 254 (east), outputs west")),
    ("C333", ("F", 65.35, 11.55, 180, "DAC_CAP at pin 4")),
    ("C332", ("F", 63.45, 11.55, 0, "VDD decoupling at pins 5 / 6")),
    ("R331", ("F", 62.00, 9.45, 90, "DAC_OUT (south) to the VREF_RD rail at y = %.2f" % Y_VREF_RAIL)),
    ("C331", ("F", 61.00, 9.45, 270, "VREF_RD (north pad) to GND")),
    ("R332", ("F", 61.00, 11.45, 270, "VDDQ (north pad) to the VREF_RD rail")),
    ("R333", ("F", 62.00, 11.45, 90, "VREF_RD rail to GND (north pad)")),
    ("TP4", ("F", X_VREF_TP, Y_VREF_RAIL, 0, "VREF_RD: where the feed from the reference bus (via below it) turns into the rail of the DAC network")),
    ("C511", ("F", 65.80, 6.20, 90, "3.3 V input bulk at finger 255")),
    # ---- 1.2 V input bulk at the foot of the corridor reserved for the 1.2 V feed (F.Cu); not routed by the data stage ----
    ("C512", ("F", 44.95, 4.95, 270, "GND pad south, on the axis of the VSS finger pair 167 / 168; V1P2 pad north")),
    ("C513", ("F", 46.05, 4.95, 270, "GND pad south, on the axis of the VSS finger pair 171 / 172; V1P2 pad north")),
    ("C502", ("F", 46.60, 7.45, 270, "22 uF, 0.9 mm high: its courtyard starts above y = 5.10; GND pad south, V1P2 pad north")),
    # ---- VDD2H regulator (F.Cu): U2 with IN / PG / BIAS / EN east, OUT / FB / SS / GND west ---------------------
    ("U2", ("F", 33.75, 21.90, 180, "pins 1, 2 (IN) at the south-east, 9, 10 (OUT) at the south-west")),
    ("C505", ("F", 30.85, 20.30, 270, "output: VDD2H pad north, beside the OUT pins")),
    ("R501", ("F", 29.60, 20.90, 90, "FB top: VDD2H south, FB north")),
    ("R502", ("F", 29.60, 22.80, 90, "FB bottom: FB south, GND north")),
    ("C504", ("F", 31.10, 23.20, 90, "soft start: SS south")),
    ("C501", ("F", 36.75, 20.35, 270, "input: V1P2 pad north, beside the IN pins")),
    ("C503", ("F", 36.75, 23.00, 90, "bias: V3P3 south")),
    ("R503", ("F", 38.00, 22.50, 270, "PG pull-up: V3P3 north, PG south (on the way to U4 EN)")),
    # ---- VDD1 regulator (F.Cu) -------------------------------------------------------------------------------
    ("U3", ("F", 32.30, 27.30, 180, "OUT at the south-west, IN at the south-east, EN at the north-east")),
    ("C507", ("F", 29.60, 26.70, 90, "output: VDD1 south")),
    ("R509", ("F", 30.90, 24.90, 180, "bleeder: VDD1 east")),
    ("C506", ("F", 33.30, 24.90, 180, "input: V3P3 east")),
    ("R504", ("F", 35.00, 27.70, 90, "enable: PWR_EN north, V1P2 south")),
    # ---- VDDQ regulator (F.Cu): south row BIAS, GND, IN; north row EN, ADJ, OUT ------------------------------
    ("U4", ("F", 41.50, 23.00, 90, "IN at the south-east on the V1P2 pour, OUT at the north-east")),
    ("C508", ("F", 43.30, 22.00, 90, "input: V1P2 south")),
    ("C509", ("F", 39.70, 22.60, 90, "bias: V3P3 south, level with U4 pin 1; clear of the V1P2 arm (y <= 21.6)")),
    ("C510", ("F", 43.30, 24.00, 270, "output: VDDQ north")),
    ("R506", ("F", 42.80, 25.45, 180, "FB top: VDDQ east")),
    ("R507", ("F", 40.90, 25.45, 180, "FB bottom: FB east")),
    ("R508", ("F", 44.15, 24.00, 270, "bleeder: VDDQ north")),
    # ---- test pads (F.Cu) ----------------------------------------------------------------------------------------
    ("TP3", ("F", 46.60, 24.00, 0, "VDDQ")),
    ("TP5", ("F", 49.20, 24.00, 0, "GND")),
    ("TP2", ("F", 46.60, 26.50, 0, "VDD2H")),
    ("TP1", ("F", 49.20, 26.50, 0, "VDD1")),
])


# power stage: every part it re-placed or placed on the back (scripts/power_side.py) replaces the entry above
for _ref, _v in POWER.PLACE.items():
    SUPPORT[_ref] = _v
for _ref, (_side, _x, _y) in FIDUCIALS.items():
    SUPPORT[_ref] = (_side, _x, _y, 0, "assembly fiducial")


def placement_record(ref, fp_name, side, x, y, rot, note=""):
    pads, cy = fp_geom(fp_name)
    rec = OrderedDict(side=side, x=r3(x), y=r3(y), rot_deg=rot, footprint=fp_name)
    rec["pads"] = OrderedDict((n, list(fp_to_card(px, py, side, rot, x, y))) for n, (px, py) in pads.items()) \
        if len(pads) <= 16 else "see floorplan.U1.ball_xy / fingers[].x_mm"
    if cy:
        c = [fp_to_card(cx, cy_, side, rot, x, y) for cx in (cy[0], cy[2]) for cy_ in (cy[1], cy[3])]
        rec["courtyard"] = [min(q[0] for q in c), min(q[1] for q in c), max(q[0] for q in c), max(q[1] for q in c)]
    if note:
        rec["note"] = note
    return rec


def build_support_placements():
    fpn = {p["ref"]: p["footprint"] for p in PARTS}
    out = OrderedDict()
    out["J1"] = placement_record("J1", fpn["J1"], "F", 0.0, 0.0, 0, "footprint origin = card origin; carries the outline")
    out["U1"] = placement_record("U1", fpn["U1"], "F", U1_CENTRE[0], U1_CENTRE[1], U1_ROT, "ball A1 at the lower right")
    for ref, (side, x, y, rot, note) in SUPPORT.items():
        out[ref] = placement_record(ref, fpn[ref], side, x, y, rot, note)
    return out


# ---- U1 fan-out, explicit ---------------------------------------------------------------------------------
VIP_SIGNALS = ("CA0_A", "CA1_A", "CA2_A", "CA3_A", "CA4_A", "CA5_A", "CA6_A", "CS0_A", "CK_t_A", "CK_c_A", "WCK0_t_A", "WCK0_c_A",
               "RDQS0_t_A", "RDQS0_c_A", "WCK1_t_A", "WCK1_c_A", "ZQ_A", "CK_c_B")
# strapped balls (no via): ball -> the touching ball of the same net that keeps its via
STRAPS = OrderedDict([
    ("A12", "A11"), ("G6", "G5"),
    ("P3", "P4"), ("P5", "P6"), ("P7", "P8"), ("P9", "P10"), ("P11", "P12"),
    ("R4", "R5"), ("R6", "R7"), ("R8", "R9"), ("R10", "R11"), ("R12", "R13"),
    ("U4", "U3"), ("U12", "U13"), ("V5", "V4"), ("V11", "V12"),
])
# strapped inputs that have a via-in-pad of their own instead of an F.Cu strap: A4 (DMI0_A, tied to VSS).  The DQ4 neck
# uses the A|B row channel between A4 and B4, so the strap is gone; the via also carries the GND pad of C415
VIP_STRAPPED = ("A4",)
LINK_W = 0.20            # F.Cu links between touching balls (POWER class inside the BGA field)
# unused byte 1 (DQ8-15): no escape, the balls are open (UNUSED_BYTE_OPEN)
UNUSED_BYTE_BALLS = OrderedDict([("DQ8", "D15"), ("DQ9", "C14"), ("DQ10", "E14"), ("DQ11", "D13"), ("DQ12", "B11"), ("DQ13", "C10"),
                                 ("DQ14", "E10"), ("DQ15", "F11")])
N7_TIE_TO = "T4"         # CK_c_B: B.Cu track from the N7 via to the nearest VDDQ via


def link_path(a, b):
    """F.Cu centre line between two touching balls: one 45-degree run plus a straight remainder."""
    (xa, ya), (xb, yb) = ball_xy(a), ball_xy(b)
    dx, dy = xb - xa, yb - ya
    if abs(dx) < 1e-9 or abs(dy) < 1e-9:
        return [P(xa, ya), P(xb, yb)]
    d = min(abs(dx), abs(dy))
    mid = (xa + d * (1 if dx > 0 else -1), ya + d * (1 if dy > 0 else -1))
    return [P(xa, ya), P(*mid), P(xb, yb)]


def build_u1_fanout(support):
    fn = ball_fn()
    supply = ("VSS", "VDD2H", "VDD2L", "VDDQ", "VDD1")
    vias = OrderedDict()
    for b, f in fn.items():
        if (f in supply or f in VIP_SIGNALS or b in VIP_STRAPPED) and b not in VIA_OMITTED:
            vias[b] = OrderedDict(net=u1_net(f)[0], xy=list(ball_xy(b)))
    links = []
    for b, v in VIA_OMITTED.items():
        for t in v["f_cu_link_to"]:
            links.append(OrderedDict(kind="supply ball without via", net=u1_net(fn[b])[0], layer="F.Cu", width=LINK_W,
                                     path=link_path(b, t), **{"from": b, "to": t}))
    for b, t in STRAPS.items():
        links.append(OrderedDict(kind="strap of an unused input", net=u1_net(fn[b])[0], layer="F.Cu", width=LINK_W,
                                 path=link_path(b, t), **{"from": b, "to": t}))
    # N7 (CK_c_B) to VDDQ on B.Cu: up the column 7|8 channel, east along the R|T row channel, into T4
    x7, y7 = ball_xy("N7")
    xt, yt = ball_xy(N7_TIE_TO)
    xch = r3((ball_xy("N7")[0] + ball_xy("N8")[0]) / 2)
    ych = r3((ball_xy("R1")[1] + ball_xy("T1")[1]) / 2)
    tie = [P(x7, y7), P(xch, y7 + (x7 - xch)), P(xch, ych - 0.2), P(xch + 0.2, ych), P(xt - (yt - ych), ych), P(xt, yt)]
    ties = [OrderedDict(net="VDDQ", layer="B.Cu", width=0.10, path=tie, **{"from": "N7", "to": N7_TIE_TO},
                        note="CK_c_B has no VDDQ neighbour: its via-in-pad is tied on B.Cu to the VDDQ via of %s" % N7_TIE_TO)]
    for net, ball in UNUSED_BYTE_BALLS.items():        # nothing is drawn for the unused byte
        assert fn[ball] == net + "_A" and u1_net(fn[ball])[0] == "" and ball not in vias, (net, ball)
    return vias, links, ties


X_DAC_REGION0 = 58.4           # west edge of the DAC region (the courtyard of TP4 starts at x = 58.5)
X_READ_BUNDLE_EAST = 57.45     # east edge of the In3 read bundle: 0.15 mm east of the In3 pad of the last read via (DQ4, x = 57.10)


def regions():
    """Named rectangles of the floorplan: every part lies in one of its face; no two on one layer overlap."""
    yA = ball_xy("A1")[1]
    R = []

    def add(name, layers, x, y, contents):
        R.append(OrderedDict(name=name, layers=layers, x=[r3(x[0]), r3(x[1])], y=[r3(y[0]), r3(y[1])], contents=contents))
    dp = data_plan()
    c = DATA.V1P2_CORRIDOR
    ytop = max(q["level_y"] for q in dp["read"].values())
    add("command_field", ["F.Cu", "In3.Cu", "B.Cu"], (15.9, 24.0), (4.0, 7.75), "R101..R120, node vias, fence vias")
    add("command_in3_tuning", ["In3.Cu"], tuple(CMD_TUNE_X), (7.75, 11.5),
        "length tuning of CA / CS: parallel shifts and bumps (continues 0.2 mm under the U1 outline)")
    add("U1", ["F.Cu", "In3.Cu", "B.Cu"], (14.525, 26.925), (11.5, 26.5),
        "U1, via-in-pads, In3 command bundle; byte-0 corner: the eight F.Cu necks, the WCK pair on B.Cu (diagonal D3 - C2 - B1), "
        "the RDQS pair on In3 (row channel B|C); decoupling on B.Cu")
    add("write_field_wck", ["F.Cu", "B.Cu"], (34.2, 36.85), (4.0, 8.2),
        "WCK columns: R209, R210 (series), node vias, R219, R220 (shunts), their GND vias; B.Cu: the last 2 mm of the pair")
    add("write_field_dq", ["F.Cu", "B.Cu"], (36.85, 44.3), (4.0, 6.6),
        "DQ columns: R201..R208 (240R, on the finger's face) and R211..R218 (150R, back to back on the other face), the eight "
        "node vias (the T) at y = %.2f, the shunt GND vias south of the row, three return vias" % DATA.Y_NODE)
    add("dq_teeth_f_cu", ["F.Cu"], (36.85, 44.3), (6.6, DATA.Y_FAN_END), "the eight teeth of the comb, straight down the column axes")
    add("dq_lines_f_cu", ["F.Cu"], (26.925, 44.3), (DATA.Y_FAN_END, 16.6), "the eight DQ lines at 0.55 mm pitch and their one parallel 45-degree fan")
    add("wck_run_b_cu", ["B.Cu"], (26.925, 36.85), (8.2, 10.5), "WCK pair running east as the lowest lane, turning south into its columns")
    add("read_bundle_in3", ["In3.Cu"], (36.85, X_READ_BUNDLE_EAST), (5.7, r3(ytop + 0.4)),
        "the eight read branches: risers in the lanes, the bundle at 0.55 mm pitch across the teeth, the 45-degree ramp, the runs to the read vias")
    yv_ = dp["knobs"]["rdqs_via_y"]
    xd_ = DATA.X_RDQS_DOWN
    add("rdqs_high_run", ["In3.Cu"], (39.0, r3(xd_ + 0.2)), (r3(ytop + 0.4), 16.6),
        "RDQS pair on In3 north of the read vias, east and on one 45-degree run down to the axis of its fingers")
    add("rdqs_drop_in3", ["In3.Cu"], (X_READ_BUNDLE_EAST, r3(xd_ + 0.95)), (r3(yv_ - 0.3), r3(ytop + 0.4)),
        "RDQS pair on In3 south to its via pair at y = %.2f, east of the last read via; the two vias of the pair and their two GND return vias" % yv_)
    add("rdqs_drop_f_cu", [DATA.RDQS_OUTER], (X_READ_BUNDLE_EAST, X_DAC_REGION0), (5.6, r3(yv_ + 0.85)),
        "RDQS pair on %s from its via pair straight down to fingers 219 / 221 (the last %.2f mm), east of every read track; the pads of the via pair and of its "
        "return vias (the eastern two reach 0.45 mm into the empty south-west corner of the DAC region, 0.94 mm from the pad of TP4)"
        % (DATA.RDQS_OUTER, yv_ - FINGER_TOP))
    add("v1p2_feed_corridor", ["F.Cu"], (c["x"][0], 48.5), (4.0, c["y"][1]),
        "the 1.2 V strip of the power stage (1.5 mm, F.Cu) with C512, C502 east of its foot; nothing of the data side on F.Cu")
    add("n_networks_f_cu", ["F.Cu"], (48.5, 56.4), (4.0, 7.2), "39 ohm (y = %.2f) + 100 nF (y = %.2f) of the seven front N fingers, bus at y = %.2f" % (DATA.Y_REF_R, DATA.Y_REF_C, DATA.Y_BUS))
    add("n_networks_b_cu", ["B.Cu"], (56.7, 58.1), (4.0, 7.2), "39 ohm + 100 nF of the back N finger 216")
    add("rdqs_termination_" + DATA.RDQS_OUTER[0].lower() + "_cu", [DATA.RDQS_OUTER], (r3(DATA.X_RDQS_DOWN - 0.975), r3(DATA.X_RDQS_DOWN + 0.975)), (4.0, 5.6),
        "R309, R310 beside the coupled pair in the row of the 39 ohm resistors, GND down the axes of the VSS finger pairs beside the drop (%s)" % dp["rdqs"]["load_gnd"])
    add("reset_parts", ["B.Cu"], (60.8, 62.2), (4.0, 7.4), "R402 (on the axis of finger 232), R403 (on the axis of the VSS finger 234)")
    add("dac", ["F.Cu"], (X_DAC_REGION0, 66.3), (Y_DAC_REGION0, 15.0), "TP4 (on the feed), U5, R331..R333, C331..C333, C511, the GND vias of C331 and R333")
    add("vdd2h_regulator", ["F.Cu"], (27.2, 44.3), (18.0, 26.4), "U2, C501, C503..C505, C513, R501..R503, the test pads TP1 (VDD1) and TP2 (VDD2H)")
    add("u4_bias_cap", ["F.Cu"], (45.6, 46.8), (22.4, 24.4), "C509: on the 3.3 V trunk beside the tap via of U4")
    add("test_points", ["F.Cu"], (49.8, 54.2), (18.2, 21.9), "TP3 (VDDQ), TP5 (GND)")
    for ref, (side, x, y) in FIDUCIALS.items():
        add("fiducial_%s" % ref.lower(), ["F.Cu" if side == "F" else "B.Cu"], (x - 1.6, x + 1.6), (y - 1.6, y + 1.6),
            "%s: fiducial, nothing else within 1.6 mm of its centre on this face" % ref)
    add("regulators_b_cu", ["B.Cu"], (35.9, 51.4), (20.4, 27.0), "on the back: U3 (VDD1) with C506, C507, R509, R504; U4 (VDDQ) with C508, C510, R506..R508")
    return R


V1P2_ISLAND_X = (28.7, 46.55)
V1P2_ISLAND_Y = (1.0, 3.6)
# the island is on In2 (upstream: In2 = GND + power island).  F.Cu refers to In1 and B.Cu to In4, both solid GND there, and In3
# carries nothing south of the node row (y = 6.00): no signal of the card sees the island or its two extensions
V1P2_BRIDGE = OrderedDict(x=[36.0, 40.6], y=[3.6, 5.4])       # joins the two halves north of the key slot; 0.2 mm of GND web is left below
                                                              # the In2 antipads of the node vias above it (y 5.7 .. 6.3)
V1P2_FEED_TAB = OrderedDict(x=[44.75, 46.55], y=[3.6, 5.45])   # foot of the F.Cu strip: the four vias of the power stage stand on it
VDD2H_ISLAND_Y0 = 18.0
# outer-layer pours of the power stage: POWER pours only (six outlines, on B.Cu under U1).  There is NO ground flood on F.Cu / B.Cu and no stitching
# grid (layer organisation of the upstream board: the outer layers carry tracks, pads and a few local power pours; their reference is the solid
# GND plane 0.11 mm below, In1 under F.Cu and In4 under B.Cu)
HS_CLASSES = ("CMD_SE", "CMD_CK", "HOST_SE", "WR_DQ", "WR_WCK", "RD_RDQS")
CMD_CLASSES = ("CMD_SE", "CMD_CK")
POUR_TO_HS_TRACK = 0.30     # a power pour: at least this far from a high-speed outer-layer track
POUR_TO_HS_PAD = 0.15       # ... and from the pads and vias of those nets
POUR_TO_CMD_VIA = 0.30      # ... and from the B.Cu end of a via-in-pad of the command bus (equal surroundings for the ten stub ends)
POUR_TO_VREF = 0.20
POWER_POUR_MIN_WIDTH = 0.20
for _lay in ("F.Cu", "B.Cu"):
    _cls = "(" + " || ".join("B.NetClass == '%s'" % k for k in HS_CLASSES) + ")"
    _cmd = "(" + " || ".join("B.NetClass == '%s'" % k for k in CMD_CLASSES) + ")"
    RULES["spacing_rules"] += [
        OrderedDict(name="pour_to_high_speed_track_%s" % _lay.replace(".", "_"), min=POUR_TO_HS_TRACK, layer=_lay,
                    condition="A.Type == 'Zone' && B.Type == 'Track' && " + _cls,
                    note="outer-layer pours (the VDDQ / VDD2H pours under U1; there is no GND pour) keep 0.30 mm from every high-speed track on their layer: "
                         "a coplanar edge at 2.7 h moves the impedance of the track by about 1 % (scripts/coplanar.py, DESIGN.md section 15)"),
        OrderedDict(name="pour_to_high_speed_pad_%s" % _lay.replace(".", "_"), min=POUR_TO_HS_PAD, layer=_lay,
                    condition="A.Type == 'Zone' && (B.Type == 'Pad' || B.Type == 'Via') && " + _cls,
                    note="... and 0.15 mm from the pads and vias of those nets"),
        OrderedDict(name="pour_to_command_via_%s" % _lay.replace(".", "_"), min=POUR_TO_CMD_VIA, layer=_lay,
                    condition="A.Type == 'Zone' && B.Type == 'Via' && " + _cmd,
                    note="... and 0.30 mm from the vias of the command bus: no pour edge hugs the B.Cu end of a command via-in-pad"),
        OrderedDict(name="pour_to_reference_%s" % _lay.replace(".", "_"), min=POUR_TO_VREF, layer=_lay,
                    condition="A.Type == 'Zone' && B.NetClass == 'VREF'",
                    note="no pour stands beside the reference nets today (their reference is the plane below); the rule holds any later one 0.20 mm off"),
    ]
OUTER_GROUND = OrderedDict(
    policy="NO ground flood and no copper thieving on F.Cu / B.Cu, and no stitching grid: as on the upstream board (GND zones there: 4 % of F.Cu, 3 % of "
           "B.Cu, two local pours at its regulators), the outer layers carry tracks, pads and a few local POWER pours. Their reference is the solid GND "
           "plane 0.11 mm below (In1 under F.Cu, In4 under B.Cu). There is no coplanar ground beside any outer-layer track",
    gnd_pads="every GND pad of every part reaches In1 / In2 / In4 through a via of its own net: in the pad (U1 balls, exposed pads of U2 / U4 / U5), "
             "or through a track of at most 1.5 mm on its own layer (VSS fingers: the tie to the finger via of their pair; two-pad parts: a stub to a via "
             "beside the pad, which two neighbours may share). Counted pad by pad on the board by gate 9",
    gnd_vias="a GND via stands only where it serves a GND pad or the return current of a layer change (fence vias of the command field, return vias of "
             "the read vias and of the RDQS via pair): none exists to tie an outer pour to the planes",
    local_gnd_pours=[],
    local_gnd_pours_note="none: the three regulators are small linear regulators whose exposed pads carry their own vias; the upstream board uses local "
                         "GND pours only around its switching regulators")
# crossing budget of the data side (plan view, different layers; scripts/pcb/crossings.py counts the board against it)
# cmd_x_data, data_x_data and hs_x_vref EQUAL the counts of the plan: they are regression locks (a change of the board that adds a crossing fails
# gate 8), not quality criteria; same_layer and trans_through are requirements (0)
CROSSING_BUDGET = OrderedDict(cmd_x_data=7, data_x_data=52, hs_x_vref=9,
                              same_layer=0, trans_through=0, overlap_cmd_x_data_mm=0.2,
                              data_x_data_breakdown=OrderedDict(
                                  f_cu_line_or_tooth_x_in3_read_branch=28, read_finger_track_x_in3_line=0, dq_x_wck=6,
                                  dq_x_rdqs=14, wck_x_rdqs=4,
                                  lower_bound="52 with this comb and this corner escape: 28 = each pair of bits once (the In3 line of the west column under the tooth "
                                              "of the east column); 14 = the RDQS pair leaves U1 between the DQ5 and DQ1 lines, so each of its two legs crosses the "
                                              "six nets DQ1, DQ0, DQ3, DQ2, DQ6, DQ7 once on its way to any finger of bank 66 (12) and the DQ5 neck inside U1 (2); "
                                              "6 and 4 lie inside or beside the land field of U1 (WCK diagonal), untouched"),
                              kind="regression lock: the three counts are those of the plan as built, not targets that were met with margin")
CMD_CROSSINGS = [   # the seven crossings of the command bundle by data wires: all inside the land field of U1
    OrderedDict(cmd="CA0", by="DQ4", layer="F.Cu", at=[23.525, 12.35], why="ball B5 is enclosed by CA0"),
    OrderedDict(cmd="CA1", by="DQ5", layer="F.Cu", at=[22.725, 13.75], why="ball C6 is enclosed by CA1 and CA0"),
    OrderedDict(cmd="CA0", by="DQ5", layer="F.Cu", at=[23.525, 13.75], why="same"),
    OrderedDict(cmd="CA0", by="WCK_N", layer="B.Cu", at=[23.525, 14.45], why="ball D5 is enclosed by CA0"),
    OrderedDict(cmd="CA1", by="DQ6", layer="F.Cu", at=[22.725, 15.15], why="ball E6"),
    OrderedDict(cmd="CA0", by="DQ6", layer="F.Cu", at=[23.525, 15.15], why="same"),
    OrderedDict(cmd="CA0", by="DQ7", layer="F.Cu", at=[23.575, 15.85], why="ball F5, on the last 0.57 mm of CA0 into G4 (45 degrees)"),
]


def build_floorplan(groups, j1pins):
    build_placement()
    support = build_support_placements()
    vip, links, ties = build_u1_fanout(support)
    dp = data_plan()
    stubs = build_finger_stubs(support)
    fvias = build_finger_vias(j1pins, stubs)
    DATA.finish_reference_gnd(dp, fvias, finger_x)
    data_wires = dp["wires"] + dac_links(support)

    def _pad(refpin):
        ref, pin = refpin.split(".")
        return tuple(support[ref]["pads"][pin])

    def _finger_via(f):
        return next((v["x"], v["y"]) for v in fvias if f in v["fingers"])
    wck_n = next(w["path"] for w in data_wires if w["net"] == "WCK_N" and w["layer"] == "B.Cu" and w["group"] == "WCK" and len(w["path"]) >= 4)
    pw = POWER.build(_pad, lambda b_: tuple(ball_xy(b_)), finger_x, _finger_via, wck_n)
    gr = {g["name"]: g for g in groups}
    t_hook = gr["CMD"]["tuning"]["ck_hook"]["t"]
    yA = ball_xy("A1")[1]
    cmd_cols = OrderedDict()
    for k, ((f, n), x) in enumerate(zip(CMD_FINGERS.items(), CMD_COLUMNS.values())):
        route = cmd_route(n, t_hook if n == "CK_P" else None)
        cmd_cols[n] = OrderedDict(x=x, finger=f, finger_x=round(finger_x(f), 3), stub_lead_x=r3(finger_x(f) + LEAD_OFFSET.get(n, 0.0)),
                                  ball=CMD_BALL[n], ball_xy=ball_xy(CMD_BALL[n]), series_resistor_side=side_of(f),
                                  shunt_resistor_side=other(side_of(f)), gnd_via_x=cmd_gnd_via_x(k, x),
                                  in3_width=0.10 if n in ("CK_P", "CK_N") else 0.15, in3_route=route,
                                  in3_length_mm=round(poly_len(route), 3))
    dl, kn = dp["delays"], dp["knobs"]
    return OrderedDict(
        coordinate_system="origin = lower-left card corner seen from the front; x from the pin-1 end along the "
                          "finger edge; y up from the finger edge; mm; the back side uses the same x (seen through the card)",
        outline=OrderedDict(width=CARD_W, height=CARD_H, corner_chamfer="0.6 x 45 deg (4x)",
                            key_notch=OrderedDict(x_centre=38.3, width=1.0, depth=4.0),
                            latch_notches=OrderedDict(y_from=18.0, y_to=22.0, depth=1.75, radius=0.65, sides="both"),
                            tooling_holes=[OrderedDict(x=2.0, y=6.0, d=1.8), OrderedDict(x=67.6, y=6.0, d=1.8)],
                            finger_pad_y=[0.15, FINGER_TOP], finger_top_y=FINGER_TOP, finger_width=0.35, finger_pitch_per_side=0.50),
        keepouts=OrderedDict(components_from_y=4.00, max_height_between_y4_and_y5p1=0.80, max_height_above_y5p1=1.20,
                             side_margin_below_y21p35=3.00, side_margin_above_y22p35=2.00,
                             key_zone=OrderedDict(x=[36.3, 40.3], free_of_components_up_to_y=4.20),
                             max_module_thickness=3.7,
                             source="lib/README.md (edge footprint, both faces identical); rule areas are in the edge "
                                    "footprint and apply to courtyards, not only to pads"),
        U1=OrderedDict(ref="U1", side="F", centre=list(U1_CENTRE), rotation_deg=U1_ROT,
                       body=OrderedDict(x=[r3(U1_CENTRE[0] - 6.2), r3(U1_CENTRE[0] + 6.2)], y=[U1_CENTRE[1] - 7.5, U1_CENTRE[1] + 7.5]),
                       ball_xy="x = %.3f - (column - 8) * 0.8 ; y = %.1f + (row_index - 10) * 0.7 with row A = 0 ... AA = 20"
                               % U1_CENTRE,
                       orientation="row A toward the fingers (channel A = lower half), column 1 at the right, "
                                   "ball A1 at the lower-right corner; byte 0 = lower-right corner of the package",
                       command_ball_rows=OrderedDict((r, ball_xy(r + "1")[1]) for r in ("G", "H", "J")),
                       via_in_pad=OrderedDict(
                           rule="every supply ball and every routed signal ball (CA, CS0, CK, WCK0, RDQS0, WCK1, ZQ, N7) has a "
                                "0.40 / 0.15 via-in-pad, except the balls listed under 'omitted'. DQ0-15, RESET_n (H1), "
                                "the balls strapped on F.Cu (CS1_A, DMI1_A, channel-B inputs) and NC / RFU balls have none; DMI0_A (A4) is tied to VSS by a via-in-pad of its own",
                           omitted=OrderedDict((b, OrderedDict(net=u1_net(ball_fn()[b])[0], **v)) for b, v in VIA_OMITTED.items()),
                           strap_links="A12 (DMI1_A) - A11; G6 (CS1_A) - G5; channel-B inputs to the "
                                       "adjacent VSS / VDDQ ball: all on F.Cu, and the ball they are tied to keeps its via. "
                                       "A4 (DMI0_A, tied to VSS) has a via-in-pad of its own instead (the DQ4 neck uses the "
                                       "row channel between A4 and B4)",
                           strapped_with_own_via=list(VIP_STRAPPED),
                           in3_pad_trim=OrderedDict(
                               balls=["C4", "B3"], inner_diameter=IN3_PAD_TRIM,
                               why="the via-in-pads of C4 / B3 (RDQS_N / RDQS_P) connect on In3 and keep a pad there; the pad of C4 stands beside the "
                                   "CA0 trace. With the 0.40 mm pad of every other via-in-pad it would be 0.125 mm from the trace and couple RDQS_N into "
                                   "CA0 at 1.15 % of the RDQS swing for the fastest edge (HFSS, design/ref/hfss_ca0_data_corner.json); at 0.35 mm (annular "
                                   "ring 0.10 mm, the board minimum) it is 0.15 mm away and couples 0.95 %. B3 is trimmed with it (one pad size for the pair)",
                               ca0_trace_to_c4_pad_mm=round(abs(ball_xy("C4")[0] - CMD_COLUMNS["CA0"]) - IN3_PAD_TRIM / 2 - 0.075, 3)),
                           size=VIP, finish="filled and capped (IPC-4761 type VII), flat for the ball on F.Cu and for the "
                                            "0402 / 0201 pads that sit on them on B.Cu",
                           unused_inner_pads="removed",
                           unused_inner_pads_note="no pad on an inner layer on which the via connects to nothing (F.Cu and "
                                                  "B.Cu keep theirs). On In3 the command traces then pass the barrels of "
                                                  "the neighbouring ball columns at 0.25 mm (hole clearance 0.20) instead "
                                                  "of their pads at 0.125 mm, and the antipads in the GND planes shrink "
                                                  "from 0.60 to 0.55 mm (0.25 mm of plane between two ball columns)",
                           balls=vip),
                       f_cu_links=links,
                       b_cu_ties=ties,
                       unused_byte=OrderedDict(balls=UNUSED_BYTE_BALLS, treatment="open: no via, no track, no part", why=UNUSED_BYTE_OPEN,
                                               removed="RN401 / RN402 (2 x 4 x 10 k) with their eight F.Cu escapes west out of the ball field and two GND "
                                                       "vias: the escapes crossed the In3 command bundle 15 times in plan view")),
        finger_stubs=stubs,
        finger_stub_rule="every track that leaves a signal finger starts %.2f mm inside the finger pad (y = %.2f) and is "
                         "drawn exactly as listed in finger_stubs. Command stubs: straight up the lead-out line, then one "
                         "45-degree run that ends on the centre of pad 1 of the series resistor. Stubs of the data side "
                         "(floorplan.data.wires): write, WCK and RESET_n 0.15 mm ('neck') up to y = 3.45, above the pads of "
                         "the finger vias, 0.20 mm ('wide') from there, one 45-degree run onto the pad; read tracks 0.20 mm "
                         "straight north to the read via (a 0.175 mm step in the finger-via row where a finger via stands "
                         "beside them); the RDQS legs run straight into fingers %d / %d; reference stubs straight up the "
                         "finger axis" % ((FINGER_TOP - FINGER_START, FINGER_START) + tuple(DATA.RDQS_FINGER.values())),
        finger_vias=fvias,
        finger_via_rule="one 0.40 / 0.20 through via at y = %.2f for every VSS / VDD finger pair (and for the single VDD "
                        "finger 163), tied to each finger of the pair with a %.2f mm track on that finger's own layer. "
                        "Position: axis of the back finger, else axis of the front finger, else the middle of the pair "
                        "(the first that keeps 0.15 mm from every planned finger track; 0.20 mm to a reference stub is "
                        "accepted where nothing better exists). Placed by s10_escape together with the field vias"
                        % (Y_FINGER_VIA, FINGER_VIA["tie_width"]),
        command=OrderedDict(
            region=OrderedDict(x=[15.9, 24.0], y=[4.0, 7.75], sides="F and B"),
            columns=cmd_cols,
            rows_y=CMD_ROWS,
in3_tuning_zone=OrderedDict(x=CMD_TUNE_X, y=CMD_TUNE["y"], shift_y=CMD_TUNE["shift_y"],
                                        leg_pitch=CMD_TUNE["leg_pitch"], first_leg_y=CMD_TUNE["first_leg_y"],
                                        rows_y=[r3(CMD_TUNE["first_leg_y"] + CMD_TUNE["leg_pitch"] * k) for k in range(CMD_TUNE["rows"])],
                                        min_gap_to_neighbour=CMD_TUNE["min_gap"],
                                        x_extent_of_the_copper=[r3(min(q["x_extent"][0] for q in gr["CMD"]["tuning"]["per_net"].values()) - 0.075),
                                                                r3(max(q["x_extent"][1] for q in gr["CMD"]["tuning"]["per_net"].values()) + 0.075)],
                                        per_net=gr["CMD"]["tuning"]["per_net"],
                                        largest_trim_ps=gr["CMD"]["tuning"]["largest_trim_ps"],
                                        keep_out="no via and no other In3 copper inside x / y of this zone: the shifted "
                                                 "runs of CA5 and CA0 / CA1 use the In3 space west and east of the ball columns"),
            finger_stub_length_mm=OrderedDict((n, stubs[fnet(n)]["length_from_finger_top_mm"]) for f, n in CMD_FINGERS.items()),
            description="ten 0201 divider columns on the 0.8 mm pitch of the BGA column channels; series resistor "
                        "on the finger's own side below the node via, shunt on the opposite side above it. The GND "
                        "via of every shunt sits 0.4 mm to the side, on a ball-column line, so that the In3 trace runs "
                        "straight up its channel between two fence vias (0.125 mm clear) and jogs 0.4 mm into the ball via",
            ck="CK_N / CK_P node vias at x 19.525 / 20.325 converge at once into a 0.10 / 0.10 pair centred on "
               "x = 19.925 (no fence via on that line); the via-in-pads of A9..G9 are omitted (U1.via_in_pad) so the "
               "pair has a 1.2 mm via-free corridor. 0.75 mm below row H the legs split into the two column channels "
               "beside the H9 via: CK_N goes on to J9, CK_P passes H9 on the east and hooks back into it",
            ck_split=OrderedDict(split_y=r3(ball_xy("H9")[1] - CK_SPLIT["below_row_h"]), hook_t=t_hook,
                                 hook_corner=gr["CMD"]["tuning"]["ck_hook"]["corner"],
                                 clearance_to_h8_h9_h10_via_pads=0.15,
                                 uncoupled_mm=OrderedDict(CK_P=round(poly_len(cmd_cols["CK_P"]["in3_route"][4:]), 2),
                                                          CK_N=round(poly_len(cmd_cols["CK_N"]["in3_route"][4:]), 2)),
                                 courtyard_break_y=Y_U1_COURTYARD_BREAK,
                                 note="the hook corner is H9 + (t, t): raising t by 0.01 mm adds 0.02 mm (0.14 ps) to CK_P; "
                                      "this is the only intra-pair adjustment, there is no meander on either leg")),
        data=OrderedDict(
            description="the data side as one plan (scripts/data_side.py, DESIGN.md section 14): 'T at the divider', comb form. "
                        "Per DQ: ball - 0.09 mm neck - F.Cu line on its level - one parallel 45-degree fan - tooth down the column "
                        "axis into the node via (y = %.2f) = the T. At the T: 240 ohm in line on the finger's face and 150 ohm "
                        "back to back on the other face (lands %.2f mm from the via pad), the shunt GND via south of the row. "
                        "The read branch leaves the node via on In3: one 45-degree run into the lane %.2f mm east of the "
                        "column, north to its level, east in an ordered bundle at %.2f mm pitch, one 45-degree ramp, a via on "
                        "its own level above its read finger, finger track straight down; the read fingers are taken in the order of the "
                        "bundle (lowest line, westmost finger), so no finger track crosses an In3 line and the read vias form a regular "
                        "staircase. WCK on B.Cu (lowest lane). RDQS on In3, then F.Cu down into the host pair DQS6 east of every read "
                        "finger. Neither group is matched on the card (design/gateware_deskew.json). Reference networks in two rows with the bus at the north end"
                        % (DATA.Y_NODE, DATA.Y_NODE - DATA.Y_SER - DATA.PAD - 0.23 - 0.20, DATA.LANE, DATA.PITCH),
            stage="s30_data",
            pin_map=dp["pin_map"],
            rows_y=OrderedDict(finger_via=DATA.Y_FINGER_VIA, shunt_gnd_via=4.05, resistors=DATA.Y_SER, node_via=DATA.Y_NODE,
                               wck_shunt=DATA.Y_WCK_SHUNT, fan_end=DATA.Y_FAN_END),
            columns=dp["columns"],
            wck=OrderedDict(columns_x=DATA.WCK_COL, fingers=DATA.WCK_FINGER, balls=DATA.WCK_BALL, node_via_y=DATA.Y_NODE,
                            series=OrderedDict(WCK_P="R209", WCK_N="R210"), shunt=OrderedDict(WCK_P="R219", WCK_N="R220"),
                            gnd_vias=OrderedDict((n, list(v)) for n, v in DATA.WCK_GND_VIA.items()),
                            f_cu_link_to_dq4_tooth_mm=round(DATA.COL["DQ4"] - 0.075 - DATA.WCK_COL["WCK_P"] - DATA.W_LINK_WCK / 2, 3),
                            note="the WCK_P column is %.3f mm from the DQ4 column: its F.Cu links (0.20 mm, like those of the DQ columns) "
                                 "are 0.925 mm from the DQ4 tooth; the return via between the two columns stands 0.275 mm from each"
                                 % (DATA.COL["DQ4"] - DATA.WCK_COL["WCK_P"])),
            read=dp["read"],
            read_return_vias=dp["read_return_vias"],
            read_return_bearing=dp["read_return_bearing"],
            read_end_choice=dp["read_end_choice"],
            node_return_vias=[OrderedDict(x=x, y=y, serves=who) for x, y, who in DATA.NODE_RETURN_VIAS],
            rdqs=dp["rdqs"],
            reference=dp["reference"],
            reference_rows_y=OrderedDict(resistor=DATA.Y_REF_R, capacitor=DATA.Y_REF_C, bus_f_cu=DATA.Y_BUS, bus_b_cu=DATA.Y_BUS_B),
            reference_bus="VREF_RD on F.Cu at y = %.2f along the north ends of the seven front networks; one via at (%.2f, %.2f) between "
                          "the read tracks of fingers 212 (B.Cu, displaced 0.235 mm west) and 215 (F.Cu, 0.175 mm east); B.Cu at y = %.2f east past the "
                          "network of N finger 216 and under the RDQS drop (F.Cu) to x = %.2f, north to a via at y = %.2f below TP4, F.Cu up to TP4 and the rail of "
                          "the DAC network. No VREF copper on In3" % (DATA.Y_BUS, DATA.BUS_VIA[0], DATA.BUS_VIA[1], DATA.Y_BUS_B, DATA.X_FEED, DATA.Y_FEED_VIA),
            reset_n=OrderedDict(ball=DATA.RESET_BALL, finger=RESET_FINGER,
                                route="not a wire of the data side any more: floorplan.power.build (stage s50_power). F.Cu from H1 north-east to ONE via at "
                                      "(%.2f, %.2f), then B.Cu east at y = %.2f, north of everything of the data side and under the F.Cu 1.2 V strip, down "
                                      "x = %.2f east of the DAC network to R403 / R402 on the back at finger %d; crosses no high-speed wire"
                                      % (POWER.RESET_VIA[0], POWER.RESET_VIA[1], POWER.Y_RESET, POWER.X_RESET_DROP, RESET_FINGER)),
            knobs=kn,
            delays=OrderedDict((k_, OrderedDict((n, v) for n, v in d_.items())) for k_, d_ in dl.items()),
            resistor_groups=[
                OrderedDict(name="wr_div_wck", parts=["R209", "R210", "R219", "R220"], column_axis="y"),
                OrderedDict(name="wr_div_dq", parts=["R%d" % (201 + i) for i in range(8)] + ["R%d" % (211 + i) for i in range(8)], column_axis="y"),
                OrderedDict(name="n_ref_west", parts=sorted(q[k_] for q in dp["reference"].values() for k_ in ("resistor", "capacitor") if q["x_resistor"] < 52.925),
                            column_axis="y"),
                OrderedDict(name="n_ref_east", parts=sorted(q[k_] for q in dp["reference"].values() for k_ in ("resistor", "capacitor") if q["x_resistor"] > 52.925),
                            column_axis="y"),
                OrderedDict(name="rdqs_term", parts=["R309", "R310"], column_axis="y"),
            ],
            resistor_groups_note="the groups scripts/pcb/crossings.py tests for wires that run through a resistor group: the box of "
                                 "a group is the lands of its parts. The WCK columns (two rows: series at y 5.0, shunt at 7.0) and "
                                 "the DQ columns (one row, back to back) are two groups: one box around all parts of the write "
                                 "sheet would reach y = 7.55 and take in the In3 bundle that runs north of the DQ node row, "
                                 "where no part stands. The reference networks are two groups, west and east of the 2.5 mm gap of fingers "
                                 "196 .. 202 (free since RDQS moved to 219 / 221); the two RDQS loads are a group of their own on F.Cu",
            command_zone_exception=OrderedDict(
                net="RDQS_N", max_mm_inside=0.80, evidence="design/ref/hfss_ca0_data_corner.json",
                why="the RDQS pair leaves U1 on In3 (both pairs on B.Cu would fence in the VDD1 ball C1): the RDQS_N leg starts on the via-in-pad of ball C4, "
                    "0.40 mm east of the CA0 trace, and runs away from it at 45 degrees. Gate 6 of scripts/pcb/check_pcb.py accepts this one net inside the "
                    "In3 command zone, for this length, while the HFSS result is on file with a passing verdict (CA0 delay change <= 0.3 ps, coupling <= 1 %)"),
            crossing_budget=CROSSING_BUDGET,
            command_crossings=CMD_CROSSINGS,
            reserved=OrderedDict(v1p2_feed_corridor=DATA.V1P2_CORRIDOR,
                                 note="nothing of the data side stands on F.Cu inside it (the 1.2 V strip of the power stage runs in it; RESET_n passes under it on B.Cu). "
                                      "Reserved by the data stage, built by the power stage: DESIGN.md sections 4 and 15"),
            wires=data_wires),
        power=OrderedDict(
            stage="s50_power",
            description="power and slow nets as one plan (scripts/power_side.py, DESIGN.md section 15): U2 (VDD2H) on F.Cu, U3 (VDD1) and U4 (VDDQ) "
                        "with their parts on B.Cu; the 1.2 V feed is a %.1f mm strip on F.Cu; RESET_n runs on B.Cu from ONE via beside U1 to its two parts "
                        "on the back at finger %d; no wire of the stage changes layer and comes back" % (POWER.W_STRIP, RESET_FINGER),
            rules=OrderedDict(
                no_layer_hop="a slow or power wire stays on one layer for its whole run and changes layer at most at its ends, at a via beside a pad of the "
                             "net. Gate 9 of scripts/pcb/check_pcb.py: on no net of the board does a stretch of track that touches no pad lie between "
                             "two vias, except the documented high-speed topology (command: stub - node via - In3 - via-in-pad; read: tooth - node via - "
                             "In3 branch - read via - finger track; RDQS: via-in-pad - In3 - via pair - F.Cu)",
                in3="In3 carries the command group, the eight read branches and the RDQS pair only (gate 9)",
                pours="NO GND pour on an outer layer (outer_layer_ground). The power pours under U1 on B.Cu (six outlines) keep %.2f mm from every high-speed "
                      "track on their layer, %.2f mm from high-speed pads and vias and %.2f mm from every via of the command bus (DRC rules "
                      "pour_to_high_speed_track / pour_to_high_speed_pad / pour_to_command_via), minimum width %.2f mm; none over the fingers (pull-back table)"
                      % (POUR_TO_HS_TRACK, POUR_TO_HS_PAD, POUR_TO_CMD_VIA, POWER_POUR_MIN_WIDTH),
                power_pour_to_high_speed_track_mm=POUR_TO_HS_TRACK, power_pour_to_command_via_mm=POUR_TO_CMD_VIA, power_pour_min_width_mm=POWER_POUR_MIN_WIDTH,
                high_speed_classes=list(HS_CLASSES)),
            regions=OrderedDict(
                vdd2h_regulator=OrderedDict(x=[27.2, 44.3], y=[18.0, 26.4], side="F", contents="U2, C501, C503..C505, C513, R501..R503, TP1, TP2"),
                regulators_b_cu=OrderedDict(x=[35.9, 51.4], y=[20.4, 27.0], side="B", contents="U3, C506, C507, R509, R504; U4, C508, C510, R506..R508"),
                test_points=OrderedDict(x=[49.8, 54.2], y=[18.2, 21.9], side="F", contents="TP3, TP5")),
            v1p2_island=OrderedDict(layer="In2.Cu", x=list(V1P2_ISLAND_X), y=list(V1P2_ISLAND_Y),
                                    extensions=OrderedDict(bridge=V1P2_BRIDGE, feed_tab=V1P2_FEED_TAB),
                                    description="on In2 (upstream convention: In2 = GND + power island). Picks up all 19 VDD fingers "
                                                "through their finger vias (y = 3.05). The key slot would split it in two halves "
                                                "(12 fingers west, 7 east): a bridge north of the slot (x %.1f .. %.1f, up to y = %.2f) "
                                                "JOINS them, and a tab (x %.2f .. %.2f, up to y = %.2f) carries the four vias of the F.Cu "
                                                "strip. No signal sees any of it: F.Cu refers to In1 and B.Cu to In4 (solid GND "
                                                "under the whole finger row and the divider rows), and In3, the only layer that "
                                                "refers to In2, carries nothing south of the node vias (y = %.2f); the island ends "
                                                "%.2f mm south of the lowest In3 copper"
                                                % (V1P2_BRIDGE["x"][0], V1P2_BRIDGE["x"][1], V1P2_BRIDGE["y"][1], V1P2_FEED_TAB["x"][0],
                                                   V1P2_FEED_TAB["x"][1], V1P2_FEED_TAB["y"][1], DATA.Y_NODE, DATA.Y_NODE - 0.20 - V1P2_BRIDGE["y"][1]),
                                    halves=OrderedDict(west="x 28.7 .. 37.5 (fingers 111 .. 142)", east="x 39.1 .. %.2f (fingers 147 .. 163)" % V1P2_ISLAND_X[1],
                                                       joined="by the bridge on In2")),
            vdd2h_island=OrderedDict(layer="In4.Cu", x=[14.5, 37.5], y=[VDD2H_ISLAND_Y0, U1_CENTRE[1] + 7.5],
                                     description="under U1 rows K..AA and east to U2: all VDD2H / VDD2L balls from row K "
                                                 "on (53 of 77) reach it through their via-in-pads. It starts 0.2 mm "
                                                 "north of the J9 via pad (the last command ball); In3 is referenced to "
                                                 "In2 at 0.11 mm, and no In3 routing region or B.Cu pair lies over it. The "
                                                 "RDQS high run (In3 at y = %.2f, referenced to In2) passes %.2f mm south of "
                                                 "its edge; RESET_n (B.Cu, y = %.2f) runs %.2f mm south of it, over GND"
                                                 % (DATA.Y_RDQS_HIGH, VDD2H_ISLAND_Y0 - DATA.Y_RDQS_HIGH - 0.15, POWER.Y_RESET, VDD2H_ISLAND_Y0 - POWER.Y_RESET)),
            v1p2_feed=OrderedDict(
                status="BUILT by the power stage (s50_power): a strip, not a pour",
                decision="the island on In2 (two halves joined by the bridge north of the key slot: all 19 VDD fingers feed); four vias on its tab up to a "
                         "%.1f mm wide F.Cu strip that runs north in the corridor between the comb and the read end (x %.2f .. %.2f) to y = %.2f and turns "
                         "west as a %.1f mm arm (y = %.2f) to U2; U4 and the enable resistor of U3 tap the arm through vias (they stand on B.Cu)"
                         % (POWER.W_STRIP, POWER.X_STRIP - POWER.W_STRIP / 2, POWER.X_STRIP + POWER.W_STRIP / 2, POWER.Y_STRIP1, POWER.W_ARM, POWER.Y_ARM),
                corridor=DATA.V1P2_CORRIDOR,
                strip=OrderedDict(layer="F.Cu", x=POWER.X_STRIP, width=POWER.W_STRIP, y=[POWER.Y_STRIP0, POWER.Y_STRIP1], arm_y=POWER.Y_ARM, arm_width=POWER.W_ARM,
                                  arm_end_x=POWER.X_ARM_END),
                sizing=POWER.strip_sizing(),
                alternatives=["a pour that fills the corridor (51 mm2, rehearsed by the data stage): ten times the copper the current needs; not built",
                              "a strip on In4 or In3 in the corridor: In4 is the far reference of the eight In3 read lines and In3 is their own layer; "
                              "on F.Cu two solid GND planes (In1, In2) lie between the strip and the read lines",
                              "around the east end of the read fingers: 40 mm more copper (13 mohm at 1.5 mm) through the RDQS drop and the DAC network",
                              "the island on In4 with a bridge there: three back-side series resistors, two GND vias and the back-side finger stubs "
                              "would refer to a V1P2 plane",
                              "the east half of the island alone (7 fingers, 110 mA per contact): the twelve west fingers would be left without a path"]),
            build=pw,
            crossing_budget=OrderedDict(
                hs_x_pwr=18, cmd_x_pwr=3, hs_x_slow=0, hs_x_gnd=0, cmd_x_slow=0, cmd_x_gnd=5, without_solid_gnd=4,
                overlap_cmd_mm=10.6, overlap_hs_other_mm=10.4,
                kind="regression lock on the counts as built (scripts/pcb/crossings.py, plan view, tracks only; gate 8): hs_x_pwr 18 = the 1.2 V strip over "
                     "the eight In3 read lines and the two In3 legs of RDQS (10), the VDD1 east rail under six F.Cu DQ lines (6), the VDDQ east rail under the "
                     "DQ7 neck (1), the VDDQ strap D4 - E5 over WCK_N (1); cmd_x_pwr 3 = the two VDD2H straps of the U1 escape and the strap D4 - E5 over CA0; "
                     "cmd_x_gnd 5 are GND links of the U1 escape, unchanged; cmd_x_slow 0 (it was 15: the escapes of the unused byte DQ8-15 to their pull-down "
                     "arrays, removed with the arrays); no slow net of this stage (RESET_n, "
                     "PG, I2C, enable, feedback) and no GND link of it crosses a high-speed wire; the one new crossing without solid GND on every plane "
                     "between lies on the land of C421 (the VDD1 rail ends under the DQ1 neck, at the edge of the via field); overlap: 0.28 mm of the VDD2H "
                     "link F6 - K6 (its 0.12 mm neck) beside the last jog of CA1 (In4 solid between), 0.18 mm of the VDDQ east rail under the DQ7 neck, 0.07 mm of the VDD1 rail "
                     "under the DQ0 neck. The pours are planes and are not counted as wires"),
            gnd_pours=[],
            outer_layer_ground=OUTER_GROUND),
        lpddr5_support=OrderedDict(
            decoupling="24 x 4.7 uF 0402 on B.Cu under U1 on the via-in-pad power balls (12 VDD2H, 8 VDDQ, 4 VDD1 at "
                       "the corners); none in the pair escape (x 22.9 .. 24.9, rows A .. E)",
            zq=OrderedDict(ball="H15", ball_xy=ball_xy("H15"), part="R401", side="B"),
            reset=OrderedDict(ball="H1", ball_xy=ball_xy("H1"), finger=RESET_FINGER, route="floorplan.data.reset_n"),
            unused_byte="DQ8-15 of channel A are open (floorplan.U1.unused_byte); WCK1 keeps its static level",
            wck1_ties=OrderedDict(side="B", contents="R404, R405 at the via-in-pads of E12 / D11")),
        board_origin_kicad_mm=list(BOARD_ORIGIN),
        placements=PLACE,
        placements_other=support,
        placement_convention="placements: the two-pad 0201 field parts (command, write, WCK, RDQS loads, reference networks) with their pad centres. placements_other: every "
                             "other part; rot_deg is the KiCad footprint orientation (counter-clockwise seen from the front; "
                             "a back-side part has its library drawing mirrored top-bottom first), pads = resulting pad "
                             "centres, courtyard = [x0, y0, x1, y1], all in the card frame. KiCad position = "
                             "(board_origin.x + x, board_origin.y - y)",
        vias=VIAS,
        rule_areas=[
            OrderedDict(name="rdqs_termination", layers=[DATA.RDQS_OUTER], x=[r3(DATA.X_RDQS_DOWN - 0.975), r3(DATA.X_RDQS_DOWN + 0.975)], y=[4.0, 5.6],
                        restricts="nothing (a named area for the DRC rule RDQS_pair_to_read_tracks, which holds outside it)",
                        contents="the two 0.40 mm load links of the RDQS pair: the western one ends 0.425 mm from the read track of finger 215"),
            OrderedDict(name="rdqs_via_pair", layers=[DATA.RDQS_OUTER], x=[r3(DATA.X_RDQS_DOWN - 0.60), r3(DATA.X_RDQS_DOWN + 0.60)],
                        y=[r3(DATA.RDQS_VIA_Y - 0.30), r3(DATA.RDQS_VIA_Y + 0.30)],
                        restricts="nothing (a named area for the DRC rule RDQS_pair_to_read_tracks, which holds outside it)",
                        contents="the two vias of the RDQS pair and the 0.28 mm of each F.Cu leg from the centre of its via to the coupled pair (a 45-degree "
                                 "step of 0.2 mm, half of it under the via pad): the western one is 0.625 mm from the read track of finger 215, the pair "
                                 "itself 0.825 mm"),
            OrderedDict(name="vref_east_end", layers=["F.Cu", "B.Cu"], x=[55.6, 57.4], y=[2.4, 9.6],
                        restricts="nothing (named for the DRC rules VREF_to_switching_nets / VREF_to_switching_nets_east_end)",
                        contents="VREF_RD changes side between the read tracks of fingers 212 (B.Cu) and 215 (F.Cu): 0.40 mm track to track instead of 0.90"),
            OrderedDict(name="finger_via_row", layers=["F.Cu", "B.Cu"], x=[-0.5, 70.1], y=[2.3, 3.5],
                        restricts="nothing (named for the DRC rule VREF_N_stub_in_the_finger_via_row)",
                        contents="N stub of finger 211 between the finger via of 209 / 210 and the tie of 213: 0.14 mm (VREF class 0.20)")],
        regions=regions(),
        plane_plan=OrderedDict(
            pull_back=OrderedDict({"In1.Cu": OrderedDict(finger_edge=1.0, other_edges=0.3),
                                   "In2.Cu": OrderedDict(finger_edge=0.5, other_edges=0.3),
                                   "In3.Cu": OrderedDict(finger_edge=3.0, other_edges=0.3),
                                   "In4.Cu": OrderedDict(finger_edge=1.0, other_edges=0.3),
                                   "F.Cu / B.Cu pours": OrderedDict(finger_edge=2.85, other_edges=0.3),
                                   "note": "the board rule copper-to-edge is 0.15 mm only for the finger pads; without "
                                           "these keep-outs the planes would fill to the chamfered finger edge and "
                                           "to the key slot. In1 / In4 reach 1.7 mm under the finger pads (about 0.2 pF "
                                           "per finger at 0.11 mm); whether to pull them back further under the command "
                                           "fingers is decided with the HFSS socket model (DESIGN.md section 8)"}),
            In1="solid GND everywhere",
            In2="GND, no tracks, solid under every In3 trace; one island: V1P2 under the fingers of the VDD pairs (x %.1f .. %.1f, "
                "y %.1f .. %.1f) with the bridge north of the key slot (x %.1f .. %.1f, to y = %.2f) and the tab at the foot of the "
                "feed corridor (x %.1f .. %.1f, to y = %.2f)"
                % (V1P2_ISLAND_X[0], V1P2_ISLAND_X[1], V1P2_ISLAND_Y[0], V1P2_ISLAND_Y[1], V1P2_BRIDGE["x"][0], V1P2_BRIDGE["x"][1],
                   V1P2_BRIDGE["y"][1], V1P2_FEED_TAB["x"][0], V1P2_FEED_TAB["x"][1], V1P2_FEED_TAB["y"][1]),
            In4="GND, with one island: VDD2H under U1 rows K..AA (y %.1f .. %.1f) extended east to U2 (x <= 37.5). "
                "Solid GND under the whole finger row, under the command bundle (x 15.5 .. 24.5, y < %.1f), under the B.Cu WCK / "
                "RDQS pairs, the back-side finger stubs and under every In3 trace of the data side"
                % (VDD2H_ISLAND_Y0, U1_CENTRE[1] + 7.5, VDD2H_ISLAND_Y0),
            outer="VDDQ pours on B.Cu on both flanks of U1 rows A..J and R..AA (the east flank is cut once by the WCK "
                  "diagonal); VDD2H pour on B.Cu under columns 6..10 (the only plane of the 24 VDD2H / VDD2L balls of rows "
                  "A..J); the 1.2 V feed: a 1.5 mm F.Cu strip in the corridor (power.v1p2_feed), not a pour; NO ground flood on either outer layer and "
                  "no stitching grid (power.outer_layer_ground): every GND pad has its own via to the planes"),
    )


# ----------------------------------------------------------------------------------------------
# 6. Electrical analysis stored with the netlist
# ----------------------------------------------------------------------------------------------
def div(vcco, rs, r1, r2):
    return vcco * r2 / (rs + r1 + r2)


def corners(r1, r2, rs_set=(26.0, 36.0, 44.0), v_set=(1.14, 1.20, 1.26), tol=0.01):
    lo = div(v_set[0], rs_set[2], r1 * (1 + tol), r2 * (1 - tol))
    nom = div(v_set[1], rs_set[1], r1, r2)
    hi = div(v_set[2], rs_set[0], r1 * (1 - tol), r2 * (1 + tol))
    imax = v_set[2] / (rs_set[0] + (r1 + r2) * (1 - tol)) * 1e3
    inom = v_set[1] / (rs_set[1] + r1 + r2) * 1e3
    return OrderedDict(v_min=round(lo, 3), v_nom=round(nom, 3), v_max=round(hi, 3),
                       i_nom_mA=round(inom, 2), i_max_mA=round(imax, 2))


def par(a, b):
    return a * b / (a + b)


def first_incident(r1, r2, z_card, z_host=39.0, rs_set=(26.0, 36.0, 44.0), v_set=(1.14, 1.20, 1.26)):
    """Level of the first wave at the open ball: launched wave VCCO x Zhost / (Rs + Zhost), transmitted through
    the divider into the card line, doubled at the open end."""
    zp = par(r2, z_card)
    k = 2 * 2 * zp / (r1 + zp + z_host)
    return [round(k * v * z_host / (rs + z_host), 3) for v, rs in zip(v_set, reversed(rs_set))]


VDDQ_SET = (0.4996 * (1 - 0.024), 0.4996, 0.4996 * (1 + 0.024))


def build_analysis():
    a = OrderedDict()
    a["assumptions"] = ("FPGA driver 26 / 36 / 44 ohm, VCCO 1.14 / 1.20 / 1.26 V, resistors 1 %. The IBIS model "
                        "HP_SSTL12_F_OUT40 gives 25.1 / 29.3 / 34.9 ohm and DS925 36..44 ohm with DCI; 26..44 ohm covers "
                        "both to within 1 ohm. The design target for every VDDQ-domain level is 0.47 V (JEDEC VDDQ "
                        "minimum); the hard limit is the card's own VDDQ minimum, 0.488 V")
    d = OrderedDict()
    fi = first_incident(91, 68, 45.6)
    d["CA0-6"] = OrderedDict(series=91, shunt=68, **corners(91, 68), back_impedance_ohm=round(par(68, 91 + 39), 1),
                             ibis_fastest_corner_25p1_ohm_v=round(div(1.26, 25.1, 91 * 0.99, 68 * 1.01), 4),
                             first_incident_at_open_ball_v=fi,
                             first_incident_over_dc=[round(fi[0] / corners(91, 68)["v_min"], 2), round(fi[2] / corners(91, 68)["v_max"], 2)],
                             overshoot="fast corner: 0.525 V plateau for one host round trip (about 1.1 ns), 0.56 V peak "
                                       "at the die = 43..76 mV above the card's VDDQ, 0.02..0.04 V-ns: inside JESD209-5C "
                                       "Table 416 (0.3 V, 0.1 V-ns)",
                             limit="DC level <= 0.47 V target, <= 0.488 V (card VDDQ min) hard; >= 0.351 V at boot "
                                   "(default VREF(CA) 0.256 V max + 0.095 V)")
    d["CK_P/CK_N (per leg)"] = OrderedDict(series=91, shunt=68, **corners(91, 68),
                                           back_impedance_ohm=round(par(68, 91 + 33), 1),
                                           limit="differential >= 0.35 V p-p; each leg within VSS..VDDQ")
    d["CS0"] = OrderedDict(series=33, shunt=100, **corners(33, 100), back_impedance_ohm=round(par(100, 33 + 39), 1),
                           limit="0.55 V (ViHPD) .. VDD2H + 0.2 V; Vref_CS = VDD2H / 3 = 0.35 V")
    odt = [round(div(1.14, 44, 91.91, par(67.32, 36.0)), 3), round(div(1.20, 36, 91, par(68, 40)), 3)]
    d["WCK_P/WCK_N (per leg)"] = OrderedDict(series=91, shunt=68, **corners(91, 68),
                                             back_impedance_ohm=round(par(68, 91 + 33), 1),
                                             with_40ohm_wck_odt_v=odt,
                                             with_40ohm_wck_odt_differential_vpp=[round(2 * odt[0], 3), round(2 * odt[1], 3)],
                                             with_40ohm_wck_odt_note="worst case with the ODT at 36 ohm (-10 %); the "
                                                                     "differential slew is then about 2 V/ns, at the JEDEC minimum: "
                                                                     "command-bus training with forced WCK ODT is marginal in the "
                                                                     "slowest driver corner and is not needed for this card",
                                             limit="differential >= 0.30 V p-p, also with the 40 ohm WCK ODT forced during CBT")
    d["DQ0-7 write"] = OrderedDict(series=240, shunt=150, **corners(240, 150),
                                   back_impedance_ohm=round(par(150, 240 + 39), 1),
                                   ibis_fastest_corner_25p1_ohm_v=round(div(1.26, 25.1, 240 * 0.99, 150 * 1.01), 4),
                                   limit="<= 0.47 V (VDDQ min); VREF(DQ) programmed to half the level (<= 0.225 V)")
    d["RESET_n"] = OrderedDict(series=100, shunt=1000, **corners(100, 1000, rs_set=(26.0, 60.0, 86.0)),
                               limit="VIH 0.8 x VDD2H = 0.85 V .. VDD2H + 0.2 V = 1.23 V; low with 100 uA pull-up 0.11 V")
    a["dividers"] = d
    # write node: source resistance and the capacitance it drives
    dp = data_plan()
    dl = dp["delays"]
    z_ = _mid_copper_z()
    in3_max = max(v["mm"]["In3.Cu"] for v in dl["read"].values())            # longest In3 branch
    trk = OrderedDict((n, DATA.plen(DATA.read_track(q["p_finger"], q["level_y"])) - (FINGER_TOP - DATA.FINGER_START)) for n, q in dp["read"].items())
    trk_max = max(trk.values())
    cnode = OrderedDict(read_branch_in3_pf=round(in3_max * 0.1515, 1), read_finger_track_pf=round(trk_max * 0.1147, 1),
                        host_line_pf=11.3, fpga_package_pf=3.2, fpga_pad_pf=3.1, trunk_ball_die_pf=4.5)
    ctot = sum(cnode.values())
    rsrc = par(150.0, 240.0 + 36.0)
    a["write_node"] = OrderedDict(
        source_resistance_ohm=round(rsrc, 1), capacitance_pf=cnode, capacitance_total_pf=round(ctot, 1),
        tau_ns=round(rsrc * ctot * 1e-3, 2), rise_10_90_ns=round(2.2 * rsrc * ctot * 1e-3, 1),
        supported_rate="100 Mb/s (the design target): about 4 %% ISI, eye at the die 0.35 .. 0.43 V high and 5.6 .. 7.6 ns "
                       "wide at +/-70 mV around VREF 0.21 V in the audit simulation with a 40 mm In3 branch (6.1 pF); the "
                       "branch of this floorplan (about %.0f mm of In3 and %.0f mm of finger track) is %.1f pF. "
                       "200 Mb/s is not supported by this node" % (in3_max, trk_max, cnode["read_branch_in3_pf"] + cnode["read_finger_track_pf"]),
        note="the read branch (In3 about %.0f mm plus %.0f mm on the finger's outer layer) and the host read line "
             "(73 mm, open at the FPGA) hang on the node " % (in3_max, trk_max) +
             "directly (the read branch has no series resistor); the shunt is fixed by the read level, "
             "so the node cannot be made faster without giving up read level")
    f = (0.766, 0.772, 0.792)   # read high level / VDDQ into 150 ohm, LPDDR4X IBIS proxy corners
    vh = [round(VDDQ_SET[i] * f[i], 3) for i in range(3)]
    rpu = (52.0, 40.0, 28.0)    # JESD209-5C Table 472: un-terminated pull-up 40 ohm x 1.3 / 1.0 / 0.7
    vj = [round(VDDQ_SET[i] * 150.0 / (rpu[i] + 150.0), 3) for i in range(3)]
    nb = len(dl["read"])
    barrel = (z_["In3.Cu"] - z_["F.Cu"]) * TD["via"]            # the node via, F.Cu to In3: counted with the line
    per_net_tau = OrderedDict()
    for n in DATA.LEVELS:        # ball - line - node (150R, 240R - host write line) - read branch - host read line - FPGA pin
        r_, w_ = dl["read"][n], dl["write"][n]
        per_net_tau[n] = OrderedDict(trunk_ps=round(r_["trunk_ps"] + barrel, 1), read_branch_ps=round(r_["card_ps"] - r_["trunk_ps"] - barrel, 1),
                                     host_read_ps=r_["host_ps"], host_write_ps=w_["host_ps"])
    tau = OrderedDict((k_, round(sum(q[k_] for q in per_net_tau.values()) / nb, 1)) for k_ in ("trunk_ps", "read_branch_ps", "host_read_ps", "host_write_ps"))
    kw = dict(tau_trunk=tau["trunk_ps"] * 1e-12, tau_rd=tau["read_branch_ps"] * 1e-12, tau_host=tau["host_read_ps"] * 1e-12,
              tau_wr=tau["host_write_ps"] * 1e-12)
    ring = LINESIM.table(0.0, **kw)
    ring22 = LINESIM.table(22.0, **kw)
    per_net_ring = OrderedDict()
    for n, q in per_net_tau.items():     # every net with its own lengths, strong corner (28 / 28 ohm) and nominal (40 / 40)
        kn_ = dict(tau_trunk=q["trunk_ps"] * 1e-12, tau_rd=q["read_branch_ps"] * 1e-12, tau_host=q["host_read_ps"] * 1e-12, tau_wr=q["host_write_ps"] * 1e-12)
        st, no = LINESIM.summary(28.0, 28.0, 0.0, **kn_), LINESIM.summary(40.0, 40.0, 0.0, **kn_)
        per_net_ring[n] = OrderedDict(delays_ps=q, strong=OrderedDict((k_, st[k_]) for k_ in ("peak_v", "min_v", "above_window_ns", "below_window_ns",
                                                                                                "high_minus_final_mV_at_5ns", "high_minus_final_mV_at_8ns",
                                                                                                "low_minus_final_mV_at_5ns", "low_minus_final_mV_at_8ns")),
                                      nominal=OrderedDict((k_, no[k_]) for k_ in ("peak_v", "min_v", "above_window_ns", "below_window_ns")))
    worst, nom = ring[0], ring[1]
    rd = OrderedDict(load_ohm=150, series_ohm=0, vddq_v=[round(v, 4) for v in VDDQ_SET], read_high_v=vh,
                     read_high_basis="LPDDR4X IBIS proxies (0.766 .. 0.792 x VDDQ into 150 ohm)",
                     read_high_jedec_bound_v=vj,
                     read_high_jedec_basis="JESD209-5C Table 472, un-terminated pull-up 28 / 40 / 52 ohm into 150 ohm",
                     reference_default_v=round(0.4996 * 180 / 480, 4))
    vn = 0.195
    rd["reference_v"] = vn
    rd["static_one"] = OrderedDict(vid_v=[round(v - vn, 3) for v in vh], vicm_v=[round((v + vn) / 2, 3) for v in vh],
                                   jedec_bound_vid_v=[round(vj[0] - vn, 3), round(vj[2] - vn, 3)],
                                   jedec_bound_vicm_v=[round((vj[0] + vn) / 2, 3), round((vj[2] + vn) / 2, 3)])
    rd["static_zero"] = OrderedDict(vid_v=-vn, vicm_v=round(vn / 2, 4))
    rd["margins_at_jedec_bound_mV"] = OrderedDict(
        vicm_to_330=round((0.330 - (vj[2] + vn) / 2) * 1e3, 1), pin_to_460=round((0.460 - vj[2]) * 1e3, 1),
        slvs_vid_to_140=round((vj[0] - vn - 0.140) * 1e3, 1), vicm_zero_to_70=round((vn / 2 - 0.070) * 1e3, 1))
    rd["reference_rule"] = ("keep VREF_RD at or below 0.20 V if the measured read high level is above 0.42 V "
                            "(VICM <= 330 mV); the DAC covers 0.136 .. 0.631 V")
    rd["mipi_dphy_dci_hs_window"] = OrderedDict(vid_min_v=0.070, vicm_v=[0.070, 0.330], pin_v=[-0.040, 0.460])
    rd["read_high_at_the_pin"] = ("the same as at the node: the read branch carries no DC current (the FPGA pin is open), so "
                                  "removing the 22 ohm series resistors changes no static level")
    rd["open_end"] = OrderedDict(
        question="what the open FPGA end does without a series resistor in the read branch (the 22 ohm taps R301..R308 "
                 "and R311 / R312 were removed on the user's remark: 'is it that bad if it overshoots a little?')",
        model="scripts/read_linesim.py: lossless lines, linear driver, 80 ps edge, 100 Mb/s; trunk 52.4 ohm, read "
              "branch 45.6 ohm, host lines 39 ohm, 150 ohm shunt, 240 ohm to the Hi-Z write pin; 3.1 pF at each FPGA pin, "
              "1 pF at the ball. Pessimistic for ringing: no loss, no driver non-linearity",
        delays_ps=tau,
        delays_note="means of the eight nets; per_net holds every net with its own lengths",
        per_net=per_net_ring,
        per_net_worst=OrderedDict(peak_v=max(q["strong"]["peak_v"] for q in per_net_ring.values()), min_v=min(q["strong"]["min_v"] for q in per_net_ring.values()),
                                  from_final_at_8ns_mV=max(max(abs(q["strong"]["high_minus_final_mV_at_8ns"]), abs(q["strong"]["low_minus_final_mV_at_8ns"]))
                                                           for q in per_net_ring.values())),
        without_series_resistor=ring,
        with_22_ohm=ring22,
        fpga_pin_recommended_v=[-0.20, 1.40], fpga_pin_absolute_v=[-0.55, 1.75],
        fpga_pin_note="DS925: VIN of an HP bank -0.20 .. VCCO + 0.20 V recommended, -0.55 .. VCCO + 0.55 V absolute, VCCO 1.2 V",
        hs_window_v=list(LINESIM.HS_WINDOW),
        answer="not bad. Worst corner (driver 30 %% stronger than nominal at the 40 ohm setting): %.3f V / %+.3f V at the pin, "
               "%.1f ns per edge outside the single-ended window of the MIPI HS receiver (%.2f .. %.2f V), far inside the "
               "recommended range of the pin (-0.20 .. 1.40 V). Nominal driver: %.3f V / %+.3f V, %.2f ns below the window "
               "after a falling edge, never above it. In every corner the pin is within 30 mV of its final level 5 ns after "
               "the edge arrives and within 20 mV after 8 ns: the ringing is over long before a mid- or late-UI sample at "
               "100 Mb/s (10 ns). The 22 ohm resistors kept the pin inside the window in all corners (%.3f V / %+.3f V at "
               "worst); what is given up is that margin for 1 .. 2 ns after each edge"
               % (worst["peak_v"], worst["min_v"], max(worst["above_window_ns"], worst["below_window_ns"]),
                  LINESIM.HS_WINDOW[0], LINESIM.HS_WINDOW[1], nom["peak_v"], nom["min_v"], nom["below_window_ns"],
                  max(r["peak_v"] for r in ring22), min(r["min_v"] for r in ring22)),
        gateware_knobs="the same damping is available without parts: MR3 OP[2:0] (pull-down drive strength 48 or 60 ohm "
                       "instead of 40) and the SoC-ODT / pull-up calibration code of MR17. 60 ohm keeps a strong driver "
                       "at %.3f V / %+.3f V; a nominal 48 ohm driver stays inside the window (%.3f V / %+.3f V). The price "
                       "is read level (150 / (150 + R))"
                       % (ring[5]["peak_v"], ring[5]["min_v"], ring[4]["peak_v"], ring[4]["min_v"]))
    rd["slvs_400_18_window"] = OrderedDict(vid_v=[0.140, 0.450], vicm_v=[0.070, 0.330])
    rd["rdqs_true_differential"] = OrderedDict(
        vid_v=vh[1], vicm_v=round(vh[1] / 2, 3), series_ohm=0,
        idle="both legs at 0 V whenever the DRAM does not drive (VID 0, VICM 0, outside the receiver window): the "
             "receiver output is valid only inside the read burst and must be gated with the read-enable window")
    a["read"] = rd
    g = 1 / 300 + 1 / 300 + 1 / 180
    a["reference"] = OrderedDict(
        network="DAC_OUT -300R- VREF_RD ; VDDQ -300R- VREF_RD ; VREF_RD -180R- GND ; 1 uF + 8 x 100 nF on the bus",
        dac_high_z_v=round(0.4996 * 180 / 480, 4), dac_high_z_source_ohm=round(par(300, 180), 1),
        dac_active="V = %.4f x Vdac + %.4f V" % ((1 / 300) / g, (0.4996 / 300) / g),
        dac_active_source_ohm=round(1 / g, 1),
        range_internal_ref_x1p5_v=[round((0.4996 / 300) / g, 3), round((1.815 / 300 + 0.4996 / 300) / g, 3)],
        lsb_mV=round(1.815 / 1023 * (1 / 300) / g * 1e3, 3),
        worst_case_if_misprogrammed_to_3p3_v=round((3.3 / 300 + 0.4996 / 300) / g, 3),
        leakage_shift_mV_at_8x15uA=round(8 * 15e-6 / g * 1e3, 1),
        dac_code_for_0p195_v=int(round(((0.195 * g) - 0.4996 / 300) * 300 / 1.815 * 1023)),
        i2c_address="0x48 (A0 to AGND); DDR4 SPD uses 0x50-0x57, 0x30-0x37, 0x18-0x1F: no conflict")
    c_vdd1 = (12e-6, 19.8e-6)     # 4 x 4.7 uF + 1 uF: effective at 1.8 V bias .. nominal
    c_vddq = 9 * 4.7e-6
    c_vdd2h = (65e-6, 78e-6)
    a["rails"] = OrderedDict(
        VDD1=OrderedDict(regulator="U3 TLV70018DDCR", v=[1.764, 1.80, 1.836], jedec=[1.70, 1.95], source="V3P3",
                         load_max_mA=26, enable="PWR_EN = V1P2 through 47 k (VEN(high) 0.9 V); no connection to any finger",
                         bleeder="R509 47 k: tau %.2f .. %.2f s, below 0.3 V after %.1f .. %.1f s (JEDEC tUNCTL_POFF 2 s)"
                                 % (47e3 * c_vdd1[0], 47e3 * c_vdd1[1], 47e3 * c_vdd1[0] * 1.79, 47e3 * c_vdd1[1] * 1.79),
                         bleeder_tau_s=[round(47e3 * c_vdd1[0], 3), round(47e3 * c_vdd1[1], 3)]),
        VDD2H=OrderedDict(regulator="U2 TPS74801DRCRM3", v=[1.032, 1.048, 1.064], jedec=[1.01, 1.12], source="V1P2",
                          accuracy="+/-1 % (new die, guaranteed by the M3 suffix) + 0.5 % for the 1 % feedback resistors; "
                                   "the legacy die (+/-2 %) would give 1.022 .. 1.074 V",
                          bias="V3P3", load_max_mA=520, enable="VDD1 (VEN(hi) 1.1 V)", soft_start_ms=3.3,
                          own_tail_tau_s=[round(5790 * c_vdd2h[0], 2), round(5790 * c_vdd2h[1], 2)],
                          dropout_budget_mV=OrderedDict(at_0p6A_worst=78, headroom_at_v1p2_1p14=92, headroom_at_v1p2_1p20=152)),
        VDDQ=OrderedDict(regulator="U4 MIC47050YML-TR", v=[0.488, 0.4996, 0.512], jedec=[0.47, 0.57], source="V1P2",
                         bias="V3P3", load_max_mA=225, enable="PG of U2 pulled up to V3P3 (EN high 1.0 V)",
                         bleeder="R508 22 ohm (23 mA, 11 mW): tau %.2f ms with 9 x 4.7 uF" % (22 * c_vddq * 1e3),
                         bleeder_tau_ms=round(22 * c_vddq * 1e3, 2)),
        power_off="1.2 V first (ramp >= 1 ms): VDD2H follows the rail, U2 power-good disables U4 and R508 pulls VDDQ "
                  "down with tau 0.9 ms, so VDD2L stays above VDDQ - 0.2 V to within a few tens of mV; VDD1 falls last. "
                  "A faster 1.2 V collapse, or 3.3 V falling first, is an uncontrolled power-off in JEDEC terms "
                  "(allowed 400 times); R509 then brings VDD1 below 0.3 V inside the 2 s limit",
        socket=OrderedDict(V1P2_A=0.77, V1P2_contacts=19, V1P2_mA_per_contact=41, V3P3_mA=30, V3P3_contacts=1,
                           contact_limit_A=0.5))
    return a


# ----------------------------------------------------------------------------------------------
def main():
    pm = {int(r["finger"]): r for r in csv.DictReader(open(os.path.join(REF, "zcu104_sodimm_pinmap.csv")))}
    td = {int(r["finger"]): r for r in csv.DictReader(open(os.path.join(REF, "zcu104_finger_total_delay.csv")))}
    build_parts(pm)
    j1 = next(p for p in PARTS if p["ref"] == "J1")
    bom = defaultdict(list)
    for p in PARTS:
        if p["in_bom"]:
            bom[(p["value"], p["mpn"], p["manufacturer"], p["footprint"], p["dnp"])].append(p["ref"])
    bom_summary = [OrderedDict(qty=len(refs), value=k[0], mpn=k[1], manufacturer=k[2], footprint=k[3], dnp=k[4], refs=refs)
                   for k, refs in sorted(bom.items(), key=lambda kv: (kv[0][3], kv[0][0]))]
    doc = OrderedDict()
    doc["meta"] = OrderedDict(
        title="LPDDR5 command-bus testbed, DDR4 SO-DIMM form factor, for the ZCU104 PL socket J1",
        revision="0.2", date="2026-11-10", generator="scripts/build_netlist.py", checker="scripts/check_netlist.py",
        eda="KiCad 10", target_dram="SK hynix H58G56BK7BX068N (JEDEC 315-ball 2CHx32 ball map)",
        host="ZCU104 rev 1.0, XCZU7EV-2FFVC1156, socket J1 (HP banks 64 / 65 / 66 at 1.2 V)",
        scope="LPDDR5 channel A, byte 0, rank 0",
        bank_plan=OrderedDict(command="bank 65 byte group T0, fingers 66-84, CK_t/CK_c on 76/74",
                              write="bank 64 byte groups T1 / T2 / T3, fingers 143-161 (the columns of the comb), WCK_c/WCK_t on 137/139 (CK0 pair, inverted)",
                              read="bank 66 byte groups T3 / T2 / T1, fingers 182-216 in the order of the In3 bundle, RDQS on 219/221 (DQS6 pair, inverted); "
                                   "one static output in the bank: RESET_n on finger 232 (back side, T0L)"),
        net_naming="X_F / X_WF = finger side of a divider, X = ball side, X_RF = read finger, VREF_Nx = N finger node; "
                   "pairs end in _P / _N (= JEDEC _t / _c) so that KiCad recognises them: CK_P / CK_N, WCK_P / WCK_N, "
                   "RDQS_P / RDQS_N, finger side CK_F_P / CK_F_N, WCK_F_P / WCK_F_N, RDQS_RF_P / RDQS_RF_N",
        part_counts=OrderedDict(total=len(PARTS), in_bom=sum(1 for p in PARTS if p["in_bom"]),
                                dnp=sum(1 for p in PARTS if p["dnp"])))
    doc["stackup"] = stackup_json()
    doc["rules"] = RULES
    doc["parts"] = PARTS
    doc["no_connect"] = NO_CONNECT
    doc["fingers"] = build_fingers(pm, td, j1["pins"])
    groups = build_groups(td)
    doc["groups"] = groups
    doc["floorplan"] = build_floorplan(groups, j1["pins"])
    doc["analysis"] = build_analysis()
    doc["floorplan"]["data"]["delays_note"] = ("card delays of the plan, ps (model of the project: ps / mm per layer and width, barrels between "
                                               "mid-copper heights, every mitred vertex by rules.bend_model, finger top = y 2.70); gate 7 of "
                                               "scripts/pcb/check_pcb.py measures the board against them")
    doc["bom_summary"] = bom_summary
    with open(OUT, "w") as fh:
        json.dump(doc, fh, indent=1)
        fh.write("\n")
    print("wrote %s: %d parts, %d no-connects, %d BOM lines" % (os.path.relpath(OUT, ROOT), len(PARTS), len(NO_CONNECT), len(bom_summary)))
    for g in groups:
        if g["name"] == "WR":
            print("  %-5s NOT matched on the card: centre %.1f ps +/-%.1f; per-bit output delay (ps): %s" % (
                g["name"], g["total_delay_centre_ps"], g["spread_half_ps"], " ".join("%s %.1f" % kv for kv in g["deskew"]["per_bit_output_delay_ps"].items())))
        if g["name"] == "RD_P":
            print("  %-5s NOT matched on the card: centre %.1f ps +/-%.1f, levels %s" % (g["name"], g["total_delay_centre_ps"], g["spread_half_ps"],
                                                                                         " ".join("%s %.3f" % kv for kv in g["level_y"].items())))
            print("        per-bit input delay (ps): " + " ".join("%s %.1f" % kv for kv in g["deskew"]["per_bit_input_delay_ps"].items()))
        if g["name"] == "RD_RDQS":
            print("  RDQS  via pair x = %.3f / %.3f on y = %.2f (F.Cu drop %.2f mm), bump %.3f mm deep, %.2f / %.2f ps total, skew %+.2f ps, %+.2f ps from the centre of the read group" % (
                g["via_pair"]["x"][0], g["via_pair"]["x"][1], g["via_pair"]["y"], g["via_pair"]["outer_drop_mm"], g["skew_bump"]["depth_mm"], g["per_net_planned_total_ps"]["RDQS_P"],
                g["per_net_planned_total_ps"]["RDQS_N"], g["planned_skew_at_the_fpga_ps"], g["strobe_minus_dq_group_ps"]))
        if "per_net_trim_ps" in g:
            print("  %-5s total %.1f ps, trims %s" % (g["name"], g["total_delay_target_ps"],
                                                    " ".join("%s %.1f" % kv for kv in g["per_net_trim_ps"].items())))
            if "per_net" in g["tuning"] and g["name"] == "CMD":
                for n, q in g["tuning"]["per_net"].items():
                    sh = q["shape"]
                    print("        %-4s %5.2f ps: shift %+.3f, %d bump(s) x %.3f r %s, +%.3f mm centre line / +%.3f mm electrical (HFSS residual %s), gaps %s"
                          % (n, q["trim_ps"], sh["shift"], q["bumps"], sh["amplitude"] if q["bumps"] else 0.0, sh["radius"],
                             q["added_centre_line_mm"], q["added_electrical_mm"],
                             "%+.4f mm" % q["hfss_residual_mm"] if q["hfss"] else "none: no run", dict(q["gap_mm"])))



if __name__ == "__main__":
    main()

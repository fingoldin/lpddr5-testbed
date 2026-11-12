# LPDDR5 command-bus test card for the ZCU104: design description (as built, R0.2)

This file describes the board as it is built. It is meant to be read from top to bottom: sections 0 to 9 say what the card is, how commands,
writes, reads and power work, every value with its reason, how the gateware has to use it, what is still open and how to rebuild it;
sections 10 to 12 answer the three questions a reviewer asks first (were the user's rules met, how does it differ from the Antmicro board,
where do high-speed wires cross); sections 13 to 15 are the routing as measured on the copper, stage by stage. The history of the design
is a short appendix.

**The card.** DDR4 SO-DIMM outline (69.6 x 30.0 mm, 260 fingers) for the PL memory socket J1 of the Xilinx ZCU104. One SK hynix
H58G56BK7BX068N (LPDDR5, JEDEC 315-ball map); LPDDR5 channel A, byte 0, rank 0 are used. It is a test card for a custom memory controller that does
processing-in-memory with a fast command stream: **the command bus is the priority bus** (CK up to 800 MHz, 1.6 Gb/s on CA); reads and writes
only have to work at a moderate rate (100 Mb/s by design).
131 items: 119 BOM parts (none DNP), 5 test pads, 6 assembly fiducials and the card edge; 5 ICs; 71 nets; 27 BOM lines; 393 vias.

**Single source.** `design/netlist.json` is the design: parts, every pad, fingers, groups with delay targets, stackup, rules, floorplan with every
placement, via and wire of the data and power side as a centre line. It is written by `scripts/build_netlist.py` (the data side by
`scripts/data_side.py`, power and slow nets by `scripts/power_side.py`); the schematic and the board are generated from it by scripts and are never
edited by hand. `scripts/pcb/build.sh` builds everything and runs every gate (section 9); `scripts/make_fab.py` writes the fabrication package
`fab/` (section 9).

**Sources** cited as: J = `references/jedec/JESD209-5C_LPDDR5.txt`, DS925 / UG571 / UG583 = `references/amd/`, parts = `references/parts/`.
"Review", "audit" and "Vivado dry run" refer to this project's research notes, which are not in the repository; the Vivado 2026.1 pin check is
(`design/gateware/vivado_pincheck/`).

**Coordinates:** origin at the lower-left card corner seen from the front, x from the pin-1 end, y up from the finger edge, mm. The back side
uses the same x. This is also the origin of every fabrication file.

## 0. The design at a glance

### 0.1 How it works

```
                           finger            divider at the fingers                 on the card                          U1 ball
 commands  bank 65 (T0) -- 66 .. 84 ----- 91R --+----------- In3 stripline, 11-13 mm, all ten matched ----------------- CA0-6, CS0, CK_t/c
           SSTL12 1.2 V                         68R to GND     (CS: 33R / 100R)                                          0.42 V swing
 writes    bank 64 ------- 143 .. 161 --- 240R -+- node via "T" ---- F.Cu line (comb), 20-28 mm ----------------------- DQ0-7
                                                150R to GND    |                                                         0.42 V swing
 reads     bank 66 P pin - 182 .. 215 ----- outer-layer track - read via - In3 branch 6-24 mm (same net, no part) -----+
           bank 66 N pin - 183 .. 216 ----- 39R --+-- VREF_RD = 0.187 V default / 0.195 V set (DAC53401, I2C 0x48)
                                                 100 nF
 strobes   bank 64 ------- 137 / 139 ---- 91R / 68R ---- B.Cu pair ---------------------------------------------------- WCK_t/c
           bank 66 ------- 219 / 221 ---- 150R to GND per leg ---- F.Cu 5.8 mm - via pair - In3 pair 41 mm ------------- RDQS_t/c
 reset     bank 66 ------- 232 ---------- 100R, 1 k to GND ---- B.Cu ---- one via ---- F.Cu 2 mm ---------------------- RESET_n
 power     1.2 V (19 fingers), 3.3 V (finger 255) -> three LDOs: VDD1 1.80 V, VDD2H = VDD2L 1.05 V, VDDQ 0.50 V; no sequencer
```

- **Commands** (section 1). The FPGA drives 1.2 V SSTL into a resistive divider at the fingers; the divider's back impedance (44.6 ohm)
  source-terminates a 45.6 ohm stripline on In3, between two ground planes, to the open ball. All ten nets are in one FPGA byte group and are
  delay-matched on the card from the FPGA die pad to the ball (641.8 ps, 0.0 ps as routed).
- **Writes** (section 2). Same principle with 240 / 150 ohm. The 150 ohm shunt is also the read load, so one node per DQ (a via, "the T")
  joins the line from the ball, the write divider and the read branch.
- **Reads** (section 3). The DRAM drives the same net; from the T an In3 branch runs to the P pin of a differential pair of bank 66. The N pin
  of that pair sits on a common reference VREF_RD at about half the read swing (39 ohm + 100 nF per N finger, a DAC behind a default divider).
  RDQS is a true differential pair.
- **Power** (section 4). Three small linear regulators from the socket's 1.2 V and 3.3 V; the order of the rails follows from what enables what.
- **Not matched on the card:** the eight write DQ (+/-26.7 ps) and the eight read DQ (+/-56.8 ps). Both spreads are the price of user decisions
  (section 10) and are closed per bit in the FPGA with the delays of `design/gateware_deskew.json` (section 7).

### 0.2 Decisions

| Item | Choice |
|---|---|
| Command | bank 65 byte group T0, fingers 66-84; CK_t/CK_c (nets CK_P / CK_N) on 76 (P) / 74 (N); In3 stripline, 0.15 mm, one trace per BGA column channel |
| Write | bank 64, fingers 143 ... 161 (the finger of a bit follows from the comb, section 14); WCK on the host CK0 pair 137 / 139, **inverted**: WCK_c (WCK_N) on 137 (P pin), WCK_t (WCK_P) on 139 (N pin), section 2. Each DQ is one F.Cu line from its ball to its divider column, no via between the ball and the node, no meander; the WCK pair on B.Cu as the lowest lane, no skew bump |
| Read | bank 66 byte groups T3 / T2 / T1; DQ on the P pin of eight complete pairs (fingers 182 ... 215), the N pin of each pair on the reference. The pairs are taken in the order of the In3 bundle (DQ7, DQ6, DQ2, DQ3, DQ0, DQ1, DQ5, DQ4 from west to east): the **read end without crossings**. The read branch leaves the node via on In3 and ends on a via above its read finger; the read vias form a regular staircase. RDQS (RDQS_P / RDQS_N) is a true differential pair on the host DQS6 pair, fingers 219 / 221, east of every read finger, **inverted** (RDQS_t on 219, the N pin); it has the same form: In3 from its balls to a via pair at (57.875 / 58.475, y = 8.50) just above its fingers, 5.8 mm of F.Cu into them. The only output in bank 66 is the static RESET_n (finger 232) |
| Dividers | at the card fingers, all 0201: CA / CK / WCK 91 ohm + 68 ohm, CS 33 + 100 ohm, DQ 240 + 150 ohm; RESET_n 100 ohm + 1 k |
| Read path | 150 ohm DQ shunt is the read load (0.39 V high); no series part: the In3 branch starts on the node via of the divider column (6.2-24.2 mm, 45.6 ohm) and ends on a via above the read finger; the finger's own outer layer goes down to the finger at 0.20 mm (45.6 ohm) |
| Net names | pairs end in _P / _N (= JEDEC _t / _c) so that KiCad treats them as differential pairs: CK_P / CK_N, WCK_P / WCK_N, RDQS_P / RDQS_N; finger side CK_F_P / CK_F_N, WCK_F_P / WCK_F_N. DQx and RDQS_x are one net each from the ball to the read finger (no series part); DQx_WF is the write finger behind the 240 ohm |
| Reference | DAC53401 (0x48) -300R- VREF_RD, VDDQ -300R- VREF_RD -180R- GND; 39 ohm + 100 nF per N finger |
| Rails | VDD1 1.80 V TLV70018 (from 3.3 V), VDD2H = VDD2L 1.048 V TPS74801DRCRM3 (from 1.2 V, bias 3.3 V), VDDQ 0.4996 V MIC47050 (from 1.2 V, bias 3.3 V). U2 (VDD2H) stands on F.Cu, U3 (VDD1) and U4 (VDDQ) with their parts on B.Cu (section 15) |
| Stackup | upstream 6-layer structure, cores 0.40 -> 0.36 mm: 1.192 mm at the fingers (1.20 with finish) |
| U1 | centre (20.725, 19.0), rotated 180 deg: row A toward the fingers (y = 12.0), byte 0 at the lower right |
| Matching | command +/-2 ps (CK pair +/-1 ps), die pad to ball, 0.0 ps as routed. WCK and RDQS pairs +/-1 ps P to N (-0.54 / 0.00 ps as routed). **Write DQ: not matched on the card**, 748.0 .. 801.5 ps = +/-26.7 ps (rule +/-10 ps); closed by the per-bit output delays of `design/gateware_deskew.json` (section 7). **Read DQ: not matched on the card** (read end without crossings), ball to FPGA die pad 836.0 .. 949.7 ps = +/-56.8 ps about 892.8 ps; closed by the per-bit input delays of the same file; RDQS +30.3 ps from the centre of the group (In3 down to its fingers) |

### 0.3 State of the build

| Check | Result |
|---|---|
| `scripts/pcb/build.sh` on a clean tree | passes; rebuilding an unchanged tree reproduces every generated file byte for byte |
| `check_netlist.py` (the spec) | 184 checks, 0 failed |
| `check_schematic.py` | 69 checks, 0 failed; ERC 0 errors, 0 warnings, 2 documented exclusions; netlist parity 0 differences |
| `check_pcb.py` (gates 1-9 on the board) | 111 checks, 0 failed |
| KiCad 10.0.6 DRC, all severities, with schematic parity | 0 errors, 0 unconnected, 0 parity issues; 9 warnings, all `diff_pair_gap_out_of_range` where the legs of a pair must part (CK 5, WCK 3, RDQS 1) |
| `check_fab.py` (the fabrication files read back, section 9) | 56 checks, 0 failed |
| Command bus | 641.77 ps on all ten nets, CK_P to CK_N 0.00 ps |
| Pairs | WCK P - N -0.54 ps, RDQS P - N 0.00 ps (rule +/-1 ps) |
| Write group / read group | +/-26.7 ps / +/-56.8 ps on the card, closed per bit in the FPGA |
| Plan-view crossings with a high-speed wire | 94 on the whole board: command bundle 15, data x data 52, high-speed x VREF 9, high-speed x supply 18; same layer 0 (section 12) |
| Vias | 393, all through; 253 filled and capped; 199 on GND |
| DC drops | V1P2 fingers to U2 12.4 mV (limit 15), VDD1 2.0 mV, VDD2H 1.7 mV, VDDQ 4.8 mV |

## 1. Commands (CK, CA0-6, CS0): the priority bus

**Why bank 65.** The brief asks for the bank assignment that minimises the routing delay of the command bus. Host + package delay to the
fingers (median): 527.7 ps for bank 65 against 610.4 ps (bank 64) and 601.6 ps (bank 66). Inside bank 65 byte group T2 is 9 ps faster than T0 but
lies at fingers 3 .. 21, 20 mm west of where U1 can stand; T0 (fingers 66 .. 84) is directly under U1, so the In3 run is a straight 11 mm.

**Fingers.** All ten nets are in one byte group (bank 65 T0, both nibbles), on G-S-G fingers, host layer L5.
The mapping is order-preserving, so nothing crosses on any layer (checked by script):

| Finger | Side | FPGA ball | Net | U1 ball | Host + pkg ps | Card target ps | Trim ps | Column x | Fence via x | Stub leaves the finger at x |
|---|---|---|---|---|---|---|---|---|---|---|
| 66 | B | AP22 | CA5 | H13 | 532.9 | 109.0 | 1.3 | 16.325 | 15.925 | 17.675 |
| 67 | F | AP21 | CA6 | G12 | 527.4 | 114.5 | 12.3 | 17.125 | 16.725 | 17.850 |
| 70 | B | AN23 | CA3 | H11 | 527.2 | 114.7 | 8.3 | 17.925 | 17.525 | 18.750 |
| 71 | F | AM23 | CA4 | G10 | 533.6 | 108.3 | 7.6 | 18.725 | 18.325 | 18.850 |
| 74 | B | AN21 (N) | CK_N | J9 | 530.4 | 111.5 | 0.0 | 19.525 | 19.125 | 19.750 |
| 76 | B | AM21 (P) | CK_P | H9 | 531.2 | 110.7 | 0.0 | 20.325 | 20.725 | 20.100 |
| 79 | F | AN19 | CA2 | G8 | 526.2 | 115.7 | 15.0 | 21.125 | 21.525 | 21.000 |
| 80 | B | AM19 | CS0 | H7 | 527.0 | 114.9 | 8.5 | 21.925 | 22.325 | 21.100 |
| 83 | F | AP23 | CA1 | H5 | 530.3 | 111.6 | 4.5 | 22.725 | 23.125 | 22.000 |
| 84 | B | AN22 | CA0 | G4 | 535.1 | 106.8 | 3.9 | 23.525 | 23.925 | 22.175 |

Finger 75 (between the CK fingers, bitslice 0) stays open on the card; gateware drives it low.

**Topology and rows.** Finger -> 0.20 mm stub on the finger's own outer layer -> 0201 series resistor -> node via
-> In3 -> via-in-pad at the ball. The shunt sits on the opposite face above the node via.

| Row | y | What |
|---|---|---|
| finger vias | 3.05 | one through via per VSS finger pair, placed with the skeleton (`floorplan.finger_vias`) |
| series resistor | 5.00 | centre; courtyard 4.30-5.70, clear of the 4.00 mm component line |
| node via | 5.85 | 0.40 / 0.20, full barrel used (no stub) |
| shunt resistor | 6.65 | centre, opposite face |
| fence via | 7.50 | GND via of the shunt, 0.4 mm to the side of the column, on a ball-column line |
| In3 tuning zone | 7.80-11.70 | between the fence and row A of U1 (y = 12.0): parallel shifts 7.80-11.70, bump legs 7.90-11.60 |

**Finger stubs and the VSS finger vias** (`floorplan.finger_stubs`, checked with every stub, tie and via present).
Fingers alternate faces at 0.25 mm and every via is a through via, so a via on a VSS finger sits 0.25 mm from the
axis of the signal finger next to it. Each stub therefore leaves its finger 0.075 mm off axis (flush with the pad
edge), away from the neighbouring VSS pair, runs straight up and makes one 45-degree run that ends on the centre of
the resistor pad (the run starts as late as the pad allows: y = 3.33 for CA5 / CA0, 3.86-4.56 for the others). The
via of each of the four inner VSS pairs is then in the middle of its pair with 0.15 mm to both stubs; the two outer
pairs (64 / 65, 85 / 86) have theirs on the outer finger, 0.25 mm from the long diagonals of CA5 / CA0. The stub
width stays 0.20 mm.

The ten columns are on the 0.8 mm pitch of the In3 channels between the BGA ball columns (x = 16.325 ... 23.525),
so every In3 trace is a straight line up its own channel with a 0.4 mm jog into the ball via: about 11 mm,
pitch 0.8 mm, gap 0.65 mm (Kb 0.2 %). Because the fence vias sit on the ball-column lines, each trace passes
between two of them exactly as it later passes between the via-in-pads: 0.125 mm from where their pads would be
(class clearance 0.12). The fence vias and the U1 via-in-pads carry no pad on an inner layer on which they connect
to nothing, so on In3 the trace has 0.225 / 0.25 mm to their barrels and no foreign copper beside it; the only In3
pads it still passes at 0.125 mm are the ball vias of the other command nets (CA3 past G12, for instance). The same
removal shrinks the antipads in the GND planes from 0.60 to 0.55 mm.
The west five columns have their fence via on the west side, the east five on the east side, so there is none
on the CK line x = 19.925. Fence vias are 0.40 / 0.20 (the 0.45 mm power via would leave 0.10 mm). The node vias
are filled and capped like the via-in-pads: their pads are 0.05-0.10 mm from the 0201 lands, and the links
land - via - land are drawn 0.40 mm wide (the land width), so no slot is left between land and via pad.

**CK pair.** 0.10 / 0.10 mm on In3 from the node vias to row G, centred on ball column 9. The via-in-pads of
A9..G9 are omitted, which gives the pair a 1.2 mm via-free corridor (0.45 mm to the pads of columns 8 and 10).
0.75 mm below row H the legs split into the two column channels beside the H9 via (0.15 mm from where the pads of H8
and H10 would be: those GND via-in-pads carry no In3 pad; 0.15 mm to the pad of H9). CK_N goes on to J9. CK_P passes H9 on the east and hooks back into it from the north-east: the corner
of the hook is H9 + (t, t) and every 0.01 mm of t adds 0.02 mm to CK_P. With t = 0.306 mm the two legs are equal to
0.01 ps including the 0.8 ps the host wants and the bends of both legs (the hook turns 90 degrees more than the
jog of CK_N: 0.19 ps in `rules.bend_model`), so the row difference between H9 and J9 (0.94 mm) needs no meander.
The legs are uncoupled for 0.6 mm at the node vias and 1.6 / 1.7 mm at the balls. The omitted balls are fed on F.Cu:
A9-A8, B9-A9, C9-B9 (VDD2H), D9-D10 (VSS), E9-F9, F9-F10 (VDD2H), G9-H8 and G9-H10 (VSS, diagonal: G9 has no
orthogonal VSS neighbour). The column 9/10 channel stays free on F.Cu at rows A and B for the DQ13 escape.

**Matching.** Target: equal delay from the FPGA die pad to the U1 ball, 641.8 ps, +/-2 ps (CK_t to CK_c +/-1 ps).
Host + package delays are Vivado package delays plus copper extracted from the ZCU104 artwork
(`design/ref/zcu104_finger_total_delay.csv`); they are good to about +/-5 ps, so what the copper cannot know is
removed per bit with ODELAY. The CK pair is the longest path and sets the total; it is not meandered. The eight
single-ended nets need 0.9-14.6 ps more than their straight channel gives (0.12-2.11 mm of In3, 8.4 mm in total):
the 8.9 ps host spread, one ball row (0.7 mm) between rows G and H, and the different finger fans.

*A bend is electrically shorter than its centre line.* HFSS of this In3 trace (section 13) gives 0.020 mm per radian
turned at a mitred 45-degree vertex and 0.023 mm per radian in an arc of 0.15 mm radius (0.036 mm = 0.25 ps per
90 degrees; more at smaller radii), and two antiparallel legs 0.33 mm apart lose a further 2 % of their length. The
first routing of this bus (accordions of three or four 0.3 mm bumps, 0.10 mm radius, legs 0.48 mm apart, tuned by
centre-line length) therefore arrived up to 4 ps early on the nets with the most bumps. Three consequences:

- every In3 delay in the plan, in the router and in the board gate is an **electrical** length (`rules.bend_model`,
  `scripts/tuning.py`): centre line less the turn terms and the leg coupling. It covers all bends, also the jogs
  into the balls, the CK split and the CK_P hook;
- the length is added with as little turning as possible. Every net runs **parallel to its channel axis, shifted
  away from the CK corridor** between two 45-degree runs (y 7.80 to 11.70): the west half of the bundle fans out to
  the west, the east half to the east. A shift s adds 0.83 s for 180 degrees of turning and no 90-degree bend.
  CA5, CA1 and CA0 take their whole trim this way. The five nets that need more add **two rounded bumps** on the
  shifted run, all pointing east, with their legs on four common rows (y 8.67 / 9.39 / 10.11 / 10.83): leg pitch
  0.72 mm (0.57 mm of gap = 5.2 h, where the leg coupling is 0.35 %), corner radius 0.15 mm (the trace width),
  amplitude 0.38-0.74 mm. The shifts are nested so that every bump has a lane of its own: at least 0.37 mm (3.4 h)
  between any two nets, 0.67 mm from the nearest bump (CA4) to the CK pair, and the pair's east side is untouched
  (CA2 bumps away from it);
- the formula is local, but bends that follow each other within about 0.2 mm interact (a bump with short legs
  loses up to 0.06 mm less than the formula says). **Each net's own feature was therefore run in HFSS**, and the
  difference to the formula (-0.02 to +0.06 mm, `design/ref/hfss_cmd_tuning.json`) is part of that net's electrical
  length. The copper is within 0.003 mm of the simulated shapes on the bumped nets.

As routed by `scripts/pcb/s20_cmd.py` (shift or amplitude solved from the drawn copper):

| Net | Shift | Bumps x amplitude | Centre line added | Turned | Electrical added = trim | Smallest gap |
|---|---|---|---|---|---|---|
| CA5 | 0.222 mm west | - | 0.184 mm | 180 deg | 0.124 mm = 0.86 ps | 0.37 mm to CA6 |
| CA6 | 0.500 mm west | 2 x 0.549 mm | 2.094 mm | 900 deg | 1.719 mm = 11.88 ps | 0.37 mm to CA5 |
| CA3 | 0.280 mm west | 2 x 0.441 mm | 1.479 mm | 900 deg | 1.140 mm = 7.88 ps | 0.38 mm to CA6 |
| CA4 | 0.140 mm west | 2 x 0.442 mm | 1.368 mm | 900 deg | 1.041 mm = 7.19 ps | 0.40 mm to CA3, 0.67 mm to the CK pair |
| CA2 | - | 2 x 0.738 mm | 2.439 mm | 720 deg | 2.112 mm = 14.59 ps | 0.51 mm to CS0, 0.97 mm to the CK pair |
| CS0 | 0.550 mm east | 2 x 0.382 mm | 1.470 mm | 900 deg | 1.169 mm = 8.08 ps | 0.42 mm to CA1 |
| CA1 | 0.801 mm east | - | 0.664 mm | 180 deg | 0.599 mm = 4.14 ps | 0.42 mm to CS0 / CA0 |
| CA0 | 0.690 mm east | - | 0.571 mm | 180 deg | 0.507 mm = 3.50 ps | 0.42 mm to CA1 |

Measured on the board (section 13): all ten nets arrive within 0.01 ps of each other in this model, CK_P to CK_N
0.00 ps. The bend corrections inside those figures are 0.5-0.6 ps on the shifted nets, 2.2-2.7 ps on the bumped
nets and 0.47 / 0.66 ps on CK_N / CK_P: 2.2 ps of the match rest on HFSS, which repeats to 0.002 mm.

The shifted runs of CA5 and CA0 lie outside the ball columns (copper from x 16.03 to 24.29): the In3 zone
x 15.5-24.8, y 7.75-11.7 is reserved for this bus (no via, no other In3 copper).

No tuning inside the BGA field or on the finger stubs.

U1 stands with row A at y = 12.0, 4 mm further from the fingers than the fan-out alone would need. That room is what the tuning
zone, the two pairs below row A (WCK on B.Cu, RDQS on In3) and the keep-out at the finger line need. It costs 28 ps of flight time on a
source-terminated line and nothing in signal quality.

**Reference planes.** In3 lies between In2 (GND, 0.11 mm) and In4 (GND there, 0.36 mm). In2 is solid GND under every In3
trace; its one island (1.2 V under the VDD fingers, x >= 28.7, y <= 5.45: section 4) is 4 mm east of the command bundle. In4 has
no power island under x 15.5-24.5, y < 18.0 (the VDD2H island starts at y = 18.0, 0.2 mm north of
the via pad of J9, the last command ball).

**Impedance.** In3 0.15 mm = 45.6 ohm (0.09: 56.5, 0.18: 41.7, 0.20: 39.5). The line that matters for matching
is the divider's back impedance, because the ball end is open: 68 || (91 + 39 ohm host) = 44.6 ohm. 0.15 mm is
within 2 % of it and is the upstream convention, so it is kept. CK: Zodd 42.3 ohm against 68 || (91 + 33) = 43.9 ohm.

**Divider values** (driver 26 / 36 / 44 ohm, VCCO 1.14 / 1.20 / 1.26 V, 1 % resistors; min / nominal / max):

| Net | Series / shunt | DC level at the ball | Pin current nom / max | Limits |
|---|---|---|---|---|
| CA0-6, CK legs | 91 / 68 ohm | 0.378 / 0.418 / 0.468 V | 6.2 / 6.9 mA | target <= 0.47 V (JEDEC VDDQ min), hard limit the card's VDDQ min 0.485 V (section 4); boot needs >= 0.351 V (default VREF 0.256 V max + 95 mV) |
| CS0 | 33 / 100 ohm | 0.640 / 0.710 / 0.797 V | 7.1 / 8.0 mA | Vref_CS = VDD2H/3 = 0.35 V; ViHPD >= 0.55 V; <= VDD2H + 0.2 V |

- The review's starting point 82 / 68 ohm reaches 0.492 V in the fastest corner; 91 / 68 keeps the DC level of
  every corner at or below 0.468 V. With the fastest IBIS driver (25.1 ohm instead of 26) it is 0.4706 V, still
  14 mV under the card's own VDDQ minimum.
- First-incident level at the open ball is 0.98-1.12 x the DC level over the driver range (it equals the DC level
  only for a 39 ohm driver). Fast corner: 0.525 V for one host round trip (1.1 ns), 0.56 V peak at the die, that
  is 43-76 mV and 0.02-0.04 V-ns above the card's VDDQ, inside J Table 416 (0.3 V, 0.1 V-ns).
- Eye at the die in the audit simulation (1.6 Gb/s, linear driver, socket and package guesses): 2.3-2.7 x the
  155 mV mask height, 4.6-4.9 x tCIVW. Crosstalk and the real socket are not in it (section 8).
- CK differential swing 0.67-0.86 V p-p at 800 MHz (>= 0.35 V), slew 3.2-5.9 V/ns, crossing at half the level.
- After boot train VREF(CA) (MR12, about 0.21 V); the boot default 0.25 V is only good at low rate.
- FPGA: SSTL12 / DIFF_SSTL12, RDRV_40_40, FAST. All pins <= 8 mA (absolute maximum 20 mA, DS925).


**RESET_n** (finger 232, AP11, bank 66 byte group T0L, LVCMOS12; a back-side finger with the VSS fingers 230 / 234 beside it on the back; the DDR4
RESET_N finger 108 lies west of the write field where every layer is taken): 100 ohm series, 1 k to GND at the ball side: 0.96 / 1.03 / 1.12 V
(VIH 0.85 V, max VDD2H + 0.2 V = 1.23 V). The 1 k holds reset while the FPGA is unconfigured; with a 100 uA
configuration pull-up the pin sits at 0.11 V (limit 0.21 V). Route: section 15.3 (2.1 mm of F.Cu from ball H1, ONE via, B.Cu to the finger; it
crosses no high-speed wire and never touches In3).

## 2. Writes (WCK, DQ0-7)

**Fingers** (bank 64). The write DQ sit on the eight fingers above the comb (section 14): a comb on one layer is planar for one order only, the lowest
F.Cu line takes the westmost column, so the finger of a bit follows from the order in which the balls leave the BGA corner. The series resistor is
on the finger's face; the 150 ohm shunt sits back to back with it on the other face.

| Finger | Side | FPGA ball | Byte group | Net | U1 ball | F.Cu level y | Column x | Series / shunt | Host + pkg ps | Card ps | Total ps | To centre ps |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 143 | F | AD16 | T3U | DQ4 | B5 | 10.75 | 37.250 | R205 (F) / R215 (B) | 609.2 | 138.82 | 748.02 | -26.7 |
| 144 | B | AH16 | T2U | DQ5 | C6 | 11.30 | 38.050 | R206 (B) / R216 (F) | 610.6 | 167.84 | 778.44 | +3.7 |
| 145 | F | AL16 | T1L | DQ1 | C2 | 13.60 | 39.400 | R202 (F) / R212 (B) | 612.1 | 138.45 | 750.55 | -24.2 |
| 150 | B | AL15 | T1L | DQ0 | D1 | 14.15 | 40.200 | R201 (B) / R211 (F) | 612.7 | 148.07 | 760.77 | -14.0 |
| 151 | F | AA16 | T3U | DQ3 | D3 | 14.70 | 41.000 | R204 (F) / R214 (B) | 604.6 | 155.93 | 760.52 | -14.2 |
| 156 | B | AA14 | T3U | DQ2 | E2 | 15.25 | 41.800 | R203 (B) / R213 (F) | 608.6 | 165.84 | 774.43 | -0.3 |
| 157 | F | AL17 | T1U | DQ6 | E6 | 15.80 | 42.600 | R207 (F) / R217 (B) | 613.0 | 185.09 | 798.10 | +23.3 |
| 161 | F | AM16 | T1L | DQ7 | F5 | 16.35 | 43.400 | R208 (F) / R218 (B) | 615.0 | 186.51 | 801.51 | +26.7 |
| 137 | F | AF18 (P pin) | T2L | WCK_N | D5 | B.Cu pair | 34.925 | R210 / R220 (F) | 647.8 | 136.99 | 784.79 | pair |
| 139 | F | AG18 (N pin) | T2L | WCK_P | E4 | B.Cu pair | 36.125 | R209 / R219 (F) | 648.8 | 135.46 | 784.26 | pair |

Total = FPGA die pad to U1 ball. **The write group is not delay-matched on the card**: the totals span 53.5 ps (centre 774.8 ps, +/-26.7 ps; the group rule
is +/-10 ps). A comb has no knob (section 14.3); the FPGA closes it with a per-bit output delay (table in section 7,
`design/gateware_deskew.json`; status: accepted by the user: "The write group's spread is acceptable").

**DQ column** (0201, eight columns at 0.8 mm pitch, DQ4 / DQ5 west of the key at x 37.25 / 38.05, the six others at x 39.4 ... 43.4):

| y | What |
|---|---|
| 2.70-4.68 | finger stub on the finger's face: 0.15 mm from the finger through the row of the finger vias (to y = 3.45), 0.20 mm from there, one 45-degree run onto the pad; 2.1-2.4 mm |
| 4.05 | GND via of the shunt, south of the row, in a lane no finger stub uses, 0.20 mm or more from the stubs on both faces: DQ4 has its own (36.65), DQ1 / DQ0, DQ3 / DQ2 and DQ6 / DQ7 share one per pair (x 40.0, 41.6, 42.925); the one of DQ5 stands at (38.65, 4.68), above the end of the key slot |
| 5.00 | 240 ohm series resistor on the finger's face and 150 ohm shunt on the other face, back to back (lands y 4.45-5.55: all sixteen within 5.55 mm of the card edge) |
| 6.00 | node via = **the T**: plain 0.40 / 0.20 via on the column axis, its pad 0.25 mm from the lands. The F.Cu tooth comes straight down the column axis into it, the In3 read branch leaves it, and the two 0.20 mm links (0.68 mm) go south to the two resistors |

Nothing stands north of the node row in the DQ columns, and no wire runs between two columns on F.Cu or B.Cu. The read branch is described in
section 14.

| Net | Series / shunt | Level at the ball | Pin current | Limits |
|---|---|---|---|---|
| DQ0-7 write | 240 / 150 ohm | 0.389 / 0.423 / 0.460 V | 2.8 / 3.1 mA | <= VDDQ(min) 0.47 V; VREF(DQ) = 0.21 V (MR14, max 0.225 V) |
| WCK legs | 91 / 68 ohm | 0.378 / 0.418 / 0.468 V | 6.2 / 6.9 mA | >= 0.30 V p-p differential |

- The shunt is fixed by the read level (section 3); 240 ohm is then the smallest E24 series value that keeps the
  fastest corner below 0.47 V (200 ohm gives 0.508 V).
- **Write speed.** Node source resistance 150 || (240 + 36) = 97 ohm into about 27 pF (read branch 4.8 pF: up to 24.2 mm of
  In3 and 9.4 mm of finger track, on the node directly; host read line 11.3 pF, FPGA package
  and pad 6.3 pF, line, ball and die 4.5 pF): tau 2.6 ns, 10-90 % about 5.8 ns (worst net, DQ4).
  At 100 Mb/s, the design target, the audit simulation gives 0.35-0.43 V high and 5.6-7.6 ns of eye at the die
  with about 4 % ISI (JEDEC has no minimum DQ input slew). 200 Mb/s is not supported by this node.
- With the 40 ohm WCK ODT that command-bus training forces, WCK is 0.34 V p-p and about 2 V/ns in the slowest
  driver corner: at the JEDEC minimum. CBT is not needed for this card; do not rely on it.
- The write pin is Hi-Z during reads (driving it low would cost 36 mV of read level).

**WCK.** Fingers 137 / 139 are on the front; each leg has a column on F.Cu: 91 ohm series resistor
(y = 5.00), node via (6.00), 68 ohm shunt (7.00), the links 0.20 mm wide like those of the DQ columns. The GND vias of the two shunts
(y = 7.90) are mirror images about the axis of the B.Cu pair, 0.55 mm on either side of it (0.20 mm from each leg). The B.Cu pair
leaves the via-in-pads of D5 / E4 on the one diagonal of via-free balls (D3 - C2 - B1, south of the VDD1 ball C1 and its capacitor),
runs east as the lowest lane (y 9.95 / 10.15) and turns south into its two columns: WCK_N goes straight into its node via, WCK_P
makes one 45-degree run of 1.0 mm to its own. That asymmetry takes up the ball-row difference between E4 and D5: P to N -0.54 ps
with the host, **no skew bump**. The leg of E4 (WCK_t) is the eastern one, so **WCK_t takes finger 139 and WCK_c finger 137: the
inverse of the host pair** (137 = AF18 = P pin). The gateware drives the pair inverted (section 7). WCK is not matched to DQ
(tWCK2DQI is 300-900 ps: write timing is trained); it arrives 9.8 ps after the centre of the DQ totals.

## 3. Reads (DQ0-7, RDQS)

**Fingers** (bank 66; DQ on the P pin of a complete pair, the N pin on its reference network; the pairs are taken west to east
by the In3 lines bottom to top, section 14: the read end without crossings):

| P finger (ball) | N finger (ball) | Byte group | Net on P | Net on N | Host + pkg ps (P) | Level of the read via y | Read via x | In3 mm | Finger track mm | Card ps | Total ps | To the centre ps | Input delay ps |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 182 B (AD14) | 183 F (AE14) | T3L | DQ7 | VREF_N7 | 585.4 | 8.20 | 48.675 | 6.19 | 5.50 | 250.58 | 835.98 | -56.84 | 113.7 |
| 186 B (AE12) | 187 F (AF12) | T3U | DQ6 | VREF_N6 | 577.9 | 8.75 | 49.675 | 8.21 | 6.20 | 266.48 | 844.38 | -48.45 | 105.3 |
| 190 B (AG13) | 191 F (AH13) | T2L | DQ2 | VREF_N2 | 604.0 | 9.30 | 50.675 | 10.48 | 6.75 | 257.37 | 861.37 | -31.46 | 88.3 |
| 194 B (AF8) | 195 F (AG8) | T2U | DQ3 | VREF_N3 | 604.7 | 9.85 | 51.675 | 12.83 | 7.30 | 275.89 | 880.59 | -12.24 | 69.1 |
| 204 B (AH11) | 203 F (AJ11) | T2L | DQ0 | VREF_N0 | 608.0 | 10.40 | 54.175 | 16.68 | 7.85 | 288.87 | 896.87 | +4.04 | 52.8 |
| 208 B (AG11) | 207 F (AG10) | T2U | DQ1 | VREF_N1 | 604.7 | 10.95 | 55.175 | 19.03 | 8.25 | 307.61 | 912.31 | +19.48 | 37.4 |
| 212 B (AK12) | 211 F (AL12) | T1L | DQ5 | VREF_N5 | 597.1 | 11.50 | 55.940 | 21.69 | 8.90 | 349.08 | 946.18 | +53.35 | 3.5 |
| 215 F (AJ9) | 216 B (AK9) | T1U | DQ4 | VREF_N4 | 598.6 | 12.05 | 57.100 | 24.20 | 9.42 | 351.07 | 949.67 | +56.84 | 0.0 |
| 219 F (AL8, N pin) | 221 F (AK8, P pin) | T1U, QBC | RDQS_P | RDQS_N | 596.4 / 596.6 | via pair y 8.50 | 57.875 / 58.475 | 41.44 / 41.28 | F.Cu 5.98 / 5.98 | 326.72 / 326.52 | 923.12 / 923.12 | +30.29 | 0 |

Total = ball to FPGA die pad. **The read group is not delay-matched on the card**: the totals span 113.7 ps (centre 892.8 ps,
+/-56.8 ps; the group rule is +/-10 ps). The order that has no crossing at the read end is the order of the In3 bundle, and
it makes the short lines shorter and the long ones longer; no level set helps (section 14). The FPGA closes it with a per-bit
input delay (latest total minus own total; table in section 7, `design/gateware_deskew.json`; status: accepted by the user: "Same with
read group"). The read
finger is on the net of the ball: DQx and RDQS_x have no finger-side net. **RDQS is inverted on the host pair DQS6**: RDQS_t (RDQS_P) is on
finger 219, the N pin; the pair comes down between the two fingers with the C4 leg on the east (section 7). RDQS is not matched
to the data either: it arrives 30.3 ps after the centre of the group, 26.6 ps before the latest bit.

DQ avoids the host-coupled strobe pairs (a data edge would couple into its own reference); RDQS takes one of them (DQS6: 219 / 221,
T1U, a QBC pair, routed as a 6.5 mil pair on the ZCU104).
Vivado accepts `IBUFDS_DPHY` / MIPI_DPHY_DCI and `IBUFDS` / SLVS_400_18 (no differential termination) on
these bank-66 pins, through bitstream, with the final pin set of fingers 182 ... 232 (Vivado 2026.1 pin check: section 8, item 31).

**Levels.** Read high = VDDQ x Rload / (Rpu + Rload) with the 150 ohm shunt as the only DC load. Reference 0.195 V.

| Basis | Read high | VID (one) | VICM (one) | Margins: VICM <= 330 / pin <= 460 / SLVS VID >= 140 mV |
|---|---|---|---|---|
| LPDDR4X IBIS proxies, 0.766-0.792 x VDDQ | 0.374 / 0.386 / 0.405 V | +179 to +210 mV | 284-300 mV | +30 / +55 / +39 mV |
| J Table 472, un-terminated pull-up 28-52 ohm (the specification bound) | 0.362 / 0.394 / 0.431 V | +167 to +236 mV | 278-313 mV | +17 / +29 / +27 mV |

Zero state: P 0 V, N 0.195 V, VID -195 mV, VICM 97.5 mV (>= 70 mV: +27 mV; SLVS VID: +55 mV).
If the measured high level is above 0.42 V, keep VREF_RD at or below 0.20 V (half the level would leave only
7 mV of VICM margin). Requires MR17 OP[2:0] = 110b (pull-up strength defined), MR3 PDDS 40 ohm, ZQ calibrated.
The pin sees the same static levels as the node: the read branch carries no DC current and no series resistor (the ten
22 ohm resistors R301-R308, R311, R312 were removed on the user's remark, section 10). What that leaves is an open FPGA end
for 1-2 ns after each edge (line simulation `scripts/read_linesim.py`, lossless, re-run by `check_netlist.py`; mean
lengths of the eight nets in the first rows, every net with its own lengths in the last):

| DRAM driver (pull-up / pull-down) | Peak at the pin | Minimum at the pin | Outside the MIPI HS window -40..460 mV, per edge | From the final level after 5 ns / 8 ns |
|---|---|---|---|---|
| 40 ohm setting, 30 % strong (28 ohm, the JEDEC bound) | 0.539 V | -0.122 V | 1.6 ns above, 1.6 ns below | 5 / 9 mV |
| 40 ohm setting, nominal | 0.445 V | -0.056 V | never above, 0.3 ns below | 17 / 10 mV |
| 40 ohm setting, 30 % weak (52 ohm) | 0.379 V | -0.015 V | none | 23 / 13 mV |
| 48 ohm setting, strong / nominal | 0.491 / 0.399 V | -0.087 / -0.027 V | 0.5 + 1.3 ns / none | 12 / 9 mV, 22 / 12 mV |
| 60 ohm setting, strong / nominal | 0.433 / 0.345 V | -0.048 / +0.004 V | 0.2 ns below / none | 19 / 11 mV, 26 / 15 mV |
| for comparison, with the 22 ohm series resistors that were removed (worst corner) | 0.429 V | -0.012 V | none | |
| every net with its own lengths, strong corner (line 123-171 ps, read branch 80-228 ps) | 0.522-0.549 V | -0.105 to -0.131 V | up to 1.7 ns | 6 / 11 mV |

The recommended range of the pin is -0.20 .. 1.40 V (DS925, HP bank at 1.2 V; absolute -0.55 .. 1.75 V): the worst
excursion (-0.131 V, on DQ4, whose read branch is the longest: 228 ps) is 69 mV inside it. The single-ended window of the MIPI HS receiver (-40 .. 460 mV) is exceeded for up to
1.6 ns per edge with a strong driver and for 0.3 ns (undershoot only) with a nominal one; with the 22 ohm resistors it never was.
That margin is what the removal gives up. At 100 Mb/s the sample falls 5 ns or more after the edge, where the pin is
within 26 mV of its final level in every corner (the slow tail is the write-side host line charging through 240 ohm).
If the bench shows the receiver to mind the excursion, the damping is a gateware change:
MR3 drive strength 48 or 60 ohm, or the MR17 pull-up code, at the price of read level (60 ohm nominal: 0.357 V high).

**Reference network.** VREF_RD has three branches: DAC_OUT 300 ohm, VDDQ 300 ohm, GND 180 ohm, plus 1 uF.
- DAC output Hi-Z (factory default, DAC53401 data sheet 8.3.1): 0.4996 x 180 / 480 = **0.187 V**, 112 ohm source. Reads work before any I2C traffic.
- DAC active: V = 0.2727 x Vdac + 0.136 V, 82 ohm source. Internal reference x1.5 (1.815 V full scale): 0.136-0.631 V
  in 0.48 mV steps; code 121 gives 0.195 V. It cannot go below 0.136 V (the 0.14 V floor of the receiver) and, even
  if set to the VDD reference at full scale (3.3 V), stays at 1.04 V, inside the FPGA's input range.
- Worst-case pin leakage (8 x 15 uA) moves it 10 mV.
- All three branches are DC paths and each needs its copper: the DAC branch (U5 - DAC_OUT - R331) and the GND
  branch are routed with the data side, R333 and the 1 uF C331 each on a 0.8 mm link to its own GND via
  ((62.00, 12.75) and (61.00, 8.20), solid in In1 / In2 / In4, no thermal spoke of a pour); the VDDQ branch (R332
  pad 1) is fed on F.Cu from the VDDQ test pad TP3 (0.25 mm track, section 15); gate 7 checks that it is connected.
- Each N finger: stub -> 39 ohm -> VREF_RD with 100 nF to GND beside the resistor. The 39 ohm matches the host line, so
  anything coupled onto the open-ended N line is absorbed after one transit instead of resonating near 370 MHz.
  N lines are never routed across the card.
- DAC: VDD = 3.3 V (finger 255), SCL / SDA on fingers 253 / 254 (host 4.99 k pull-ups to 3.3 V, mux 0x74 channel 7),
  A0 to GND = address 0x48, broadcast 0x47; DDR4 SPD devices use 0x50-0x57, 0x30-0x37, 0x18-0x1F. No SPD EEPROM:
  the PL design has a fixed PHY and nothing reads SPD.

**N networks** (0201, two rows, on the N finger's own face: seven on F.Cu, the one of finger 216 on B.Cu). A 0.15 mm stub
runs up the finger axis (1.73 mm; finger 211 jogs 0.175 mm past the finger via of 209 / 210: 1.88 mm) into the 39 ohm, centred at
y = 4.75 (courtyard from y = 4.045). The 100 nF stands one finger pitch beside it, centred at y = 6.15, above the VSS finger
next to the N finger: its south pad goes straight down that finger's axis on a 0.25 mm link to the pair's finger via
(y = 3.05), so **no capacitor has a GND via of its own** and nothing of a network stands north of y = 6.7. The north pads of
both parts join the VREF_RD bus: F.Cu at y = 7.00 along the seven front networks (x 48.9-55.9), one via at (56.52, 7.30)
between the read tracks of fingers 212 (B.Cu) and 215 (F.Cu), B.Cu at y = 7.30 east past the network of finger 216, under the
RDQS drop (which is on F.Cu) to x = 59.70, north to a via at (59.70, 8.60), F.Cu up, with one 45-degree step of 0.2 mm onto the axis of the test pad TP4
(59.5, 10.45), and along the rail of the DAC network (C331 / R332 at x = 61.0, R331 / R333 at x = 62.0, the rail at y = 10.45
between them). No VREF copper on In3. The feed stands at x = 59.70 because a VREF track keeps 0.90 mm from the vias of a switching net as well: 0.95 mm
to the pad of the eastern RDQS via on both faces. The bus is on both faces because the reference network of finger 216 is on the back; that the feed
then returns to F.Cu for the DAC network is the one place where a static net goes F.Cu - B.Cu - F.Cu (its B.Cu stretch carries the pads of C325 / R325;
the alternative, the four parts of the DAC network and TP4 on the back, was not taken: section 15.6). The rows stand where the courtyards of the lower row
just clear the finger keep-out below y = 4.00.

The fingers 212 (back) and 215 (front) are 0.75 mm apart and the RDQS pair comes down 1.25 mm east of finger 215, so the room for
the bus via is made on both sides: the read track of finger 212 runs 0.235 mm WEST of its finger axis above the finger-via row
(x = 55.94, its read via too) and the track of finger 215 keeps the 0.175 mm step east that takes it past the finger via of
213 / 214 (x = 57.10, 0.825 mm from the RDQS leg). Both then lie 0.015 / 0.075 mm, in plan, from the reference stub and the
39 ohm link of their own N finger on the other face (3.6 + 4.0 mm of parallel run with In1, In2 and In4 between: section 12.1).

Clearances that had to be written as rules (`rules.spacing_rules`, each with its reason in `rules.changes_of_the_data_replan`):
0.17 mm between the two lands of neighbouring 0201 of the network (they are 0.18 mm apart; class value 0.20), 0.12 mm for the
stub of finger 211 inside the area `finger_via_row` (0.14 mm to the finger via of 209 / 210 and to the tie of 213), and 0.40 mm
instead of 0.90 mm between VREF_RD and a switching track inside the area `vref_east_end` (x 55.6-57.4: the bus via between the
read tracks of fingers 212 / 215, 0.405 mm track to track as built on both faces; the pad of the bus via itself, which is under no rule, stands
0.280 mm from both read tracks). Everywhere else VREF keeps more than 1.0 mm from every switching track
on its layer (1.10 mm: the feed to the RDQS leg where it leaves its via; 0.95 mm to the pad of that via). The bus is crossed nine times by high-speed wires on the other
face (seven read finger tracks: the westmost one, of finger 182, lies west of the first network; the two RDQS legs),
all at 90 degrees with In1, In2 and In4 between.

**Read fingers and VSS finger vias.** No signal via stands in the finger row: one VSS via per finger pair at y = 3.05
(`floorplan.finger_vias`, placed with the skeleton), and the read track comes straight down the finger's own layer from its read
via, 0.20 mm wide (45.6 ohm) all the way into the finger. The tracks of fingers 182 and 208 run on the finger axis; those of
186, 190, 194 and 204 step 0.175 mm off the axis between y = 2.875 and 3.45 to pass the finger via of the neighbouring VSS pair
(0.15 mm); that of 212 leaves its axis above the row and that of 215 keeps its step (above). Seven of the eight read fingers are
on B.Cu; their tracks pass the front-side N networks on the other face. No read finger track crosses an In3 read line (section 14).

**RDQS.** True differential, no reference: each leg 150 ohm to GND, no series part (VID +/-0.386 V,
VICM 0.193 V while the DRAM drives). Needs MR20 OP[1:0] = 10b. It tells on the bench whether a
read problem is the receiver or the reference. When the DRAM does not drive, both legs sit at 0 V, outside the
receiver's window: the receiver output is valid only inside the read burst and is gated with the read-enable window.
The pair has the form of a read DQ line: **In3 from its balls to a via pair just above its fingers, then a short F.Cu drop**
(section 10, the user's remark on the pair that changed layer half-way). It leaves the via-in-pads of B3 / C4 on
In3 (both pairs on B.Cu would fence in the VDD1 ball C1 and its capacitor), runs east at y = 12.60 / 12.80 in the gap between the two
lower F.Cu lines (DQ4, DQ5) and the six upper ones, and leaves that axis at x = 36.5 on ONE 45-degree run to the north-east. The F.Cu
fan runs south-east there, so the pair crosses the six diagonals of the fan at 90 degrees within 2.8 mm and crosses nothing else. From
(39.3, 15.5) it runs east on In3 at y = 15.50, north of every read via, to x = 55.175, comes down on ONE 45-degree run of 3.0 mm
(0.73 mm past the pad of the last read via) and then south at x = 58.175, coupled, to a **via pair at (57.875 / 58.475, 8.50)**, the
two vias side by side across the pair (pads 0.20 mm apart), with two GND return vias 0.58 mm from them, mirror images, at
(57.70 / 58.65, 9.05). From there 5.8 mm of **F.Cu** straight down into the **fingers 219 / 221**, 1.075 mm east of the read track of
finger 215: the pair is the eastmost wire of the read end and crosses no read line. y = 8.50 is the lowest position of a through via
there: its B.Cu pad keeps 0.90 mm from the VREF_RD bus that passes under the drop on B.Cu at y = 7.30. The pair is 47.4 mm long:
41.4 / 41.3 mm of stripline between In2 and In4 (GND on both along the whole run) and 6.0 mm of F.Cu over In1 (GND).

The two loads R309 / R310 stand on F.Cu beside the drop in the row of the 39 ohm resistors (y = 4.75), on the axes of the two
VSS finger pairs next to fingers 219 / 221 (x = 57.675 / 58.675, mirror images): pad 1 north on a 0.40 mm side link at
y = 5.07, pad 2 south, straight down the axis into that pair's finger via (y = 3.05), which the 100 nF of the reference network
of finger 216 uses from the back. The loads have no GND via of their own, the coupled pair is not opened for them, and
nothing of them stands in the field of the read vias. Two named rule areas that restrict nothing mark, for the DRC, the RDQS tracks that
are left out of the rule that holds the pair 0.80 mm from every read track: `rdqs_termination` (F.Cu, x 57.2-59.15, y 4.0-5.6: the two
side links; the western one ends 0.425 mm from the read track of finger 215, and the land of R309 on it stands 0.275 mm from that track
over 0.46 mm) and `rdqs_via_pair` (F.Cu, x 57.575-58.775, y 8.2-8.8: the 0.28 mm on which each F.Cu leg leaves the centre of its via, half
of it under the via pad; the western one is 0.625 mm from that track, the via pad itself 0.475 mm - pads are under no spacing rule, as at
the read vias). The legs themselves keep 0.825 mm along the whole drop, and 0.85 mm on In3 to the nearest other In3 track (the end of the
DQ4 line at its read via; DRC rule `RDQS_pair_to_other_tracks_In3`, 0.80). **Polarity:** the C4 leg (RDQS_c) is the eastern
one and lands on finger 221, the P pin of the host pair: the gateware inverts the strobe (section 7).

Delay: RDQS is not matched to the read group (there is no common total) and its via pair is no delay knob any more: it stands where the
F.Cu drop is shortest. RDQS_P / RDQS_N 923.12 / 923.12 ps, **+30.3 ps from the centre of the group** (892.8 ps), 87.1 ps after the
earliest bit (DQ7) and 26.6 ps before the latest (DQ4): inside the span of the eight bits, so the deskew table delays data only. Over the outer copper / mask cases of `design/impedance_results.json` the offset is 30.3 .. 32.2 ps (`groups[RD_RDQS].outer_layer_cases`):
with only 6 mm on the outer layer the strobe now depends less on plating and mask than the DQ lines do. RDQS_P is the shorter leg
(C4 lies one ball row beyond B3, and RDQS_P is the inner leg of the turn into the drop; the two corners of the north-east run cancel):
it carries the one skew bump of the board, on In3 1.1-1.6 mm east of the land field of U1 (x 28.05-28.50), where
the skew arises, so that the pair is in step from there on (about 0.33 mm of the bump compensates the two corners of the drop, 30 mm
downstream): two legs 0.553 mm deep and 0.45 mm apart (copper gap
0.35 mm = 3 h), pointing south, away from RDQS_N, 0.37 mm from the DQ5 line in plan (another layer, two planes between), 3.8 mm
from any other In3 copper.
**Read branch and matching:** section 14.

**Why the N fingers carry 39 ohm + 100 nF at the fingers instead of going straight to the DAC.** An N line of the host is about 73 mm of 39 ohm
line that ends open at the FPGA pin. Tied directly to a far reference it would be an unterminated stub that rings near 370 MHz with everything
coupled onto it; the 39 ohm at the finger absorbs that after one transit, and the 100 nF beside it makes the reference local to each pair, so
the eight N pins do not share return current through a long reference track. The DAC then only sets the DC level.

**Why only the command bundle has serpentines.** It is the only group that is delay-matched on the card (section 1). The data groups are
closed per bit in the FPGA, by the user's decisions (section 10), so their wires are as short and as straight as the form allows; the one
other tuning feature of the board is the skew bump of RDQS_P beside U1.

## 4. Power

| Rail | Regulator | From | Setting | Output (worst case) | JEDEC | Max load |
|---|---|---|---|---|---|---|
| VDD1 | U3 TLV70018DDCR (DDC = thin SOT-23-5; footprint TSOT-23-5, same lands) | 3.3 V | fixed | 1.764-1.836 V | 1.70-1.95 | 26 mA |
| VDD2H + VDD2L | U2 TPS74801DRCRM3, VSON-10 | 1.2 V, bias 3.3 V | 1.37 k / 4.42 k (data sheet Table 7-1) | 1.032-1.064 V | 1.01-1.12 | 0.52 A |
| VDDQ | U4 MIC47050YML, MLF-6 | 1.2 V, bias 3.3 V | 2.49 k / 10 k, Vref 0.4 V | 0.488-0.512 V unloaded; 0.485 V at full load (0.5 % load regulation) | 0.47-0.57 | 0.225 A |

- **VDD2H.** The M3 suffix is ordered on purpose: without it TI may ship the legacy die (+/-2 % instead of +/-1 %,
  power-good low 0.3 V instead of 0.125 V), which would give 1.022-1.074 V. That is still inside JEDEC, and the
  checker tests both. Bias headroom 3.3 - 1.05 = 2.25 V (>= 1.62 V needed). Pass element about 0.13 ohm worst case
  at this headroom (TPS748 Fig. 5-11 scaled by max/typ): 68 mV at 0.52 A. Headroom 152 mV at 1.20 V, 92 mV at
  1.14 V. These dropout figures are scaled from a typical curve; the data-sheet values (165 mV legacy die, 100 mV new die at 1.5 A) hold for
  VBIAS - VOUT >= 3.25 V and the card has 2.25 V (2.07 V at 3.3 V - 5 %). At the worst corner (host rail 1.14 V at the fingers, full load, VOUT at
  +1.5 %) the regulator is in dropout: it then delivers about 1.057 V, 47 mV above the JEDEC minimum, without supply rejection. Not in the 15 mV
  budget below: the socket contacts (about 1.5 .. 2 mV at 41 mA per contact). EN = VDD1 (threshold 1.1 V max, driven with 1.8 V). CSS 2.2 nF: 3.3 ms ramp. PG is open drain, 10 k to
  3.3 V. VDD2L balls are tied to VDD2H (single-rail mode, J 4.1.2; MR13 OP[7] = 1).
- **VDD1.** EN through 47 k to the host 1.2 V rail (VEN(high) 0.9 V, 240 mV margin), so VDD1 cannot rise before the
  1.2 V rail; the 47 k also limits the current into EN when 1.2 V is present without 3.3 V. Nothing else is on the
  enable net: an enable driven from a finger would switch VDD1 off under a live VDD2H, which J Table 19 forbids.
  The regulator has no output discharge, so VDD1 falls last; R509 (47 k to GND, 38 uA) empties the 19.8 uF on the
  rail to 0.3 V within 1.0-1.7 s (J tUNCTL_POFF 2 s) while staying slower than the VDD2H tail (0.38-0.45 s).
- **VDDQ.** 0.5 V needs a reference below 0.5 V. TI's fixed 0.5 V parts (TPS7A1105P) exist only as a 0.35 mm
  5-ball WCSP with a 22 uF output-capacitor ceiling; TPS7A84A needs 47 uF and its enable sits 40-100 mV under a
  1.2 V rail. MIC47050 is adjustable from 0.4 V in a 2 x 2 mm MLF, stable from 1 uF. Its own input limits
  (VIN >= 1.0 V, UVLO up to 1.0 V) rule out feeding it from VDD2H, so it is fed from 1.2 V and **enabled by U2's
  power good** (EN high 1.0 V max, driven to 3.3 V = VBIAS; PG low < 0.13 V against a 0.2 V threshold). It has
  no output discharge either: R508 (22 ohm, 23 mA, 11 mW) gives VDDQ a 0.9 ms time constant.
  Footprint: project `MLF-6-1EP_2x2mm_P0.65mm_EP0.8x1.4mm` (the package's exposed pad is 0.80 x 1.40 mm; the
  stock 2 x 2 mm DFN-6 has 1.0 x 1.6).
- **Power-up against J Table 19 / 20.** VDD1 starts when 3.3 V and 1.2 V are both present (100 us), VDD2H ramps
  for 3.3 ms behind it (VDD1 > VDD2H holds from the first 300 mV), VDDQ starts when VDD2H is above 90 %
  (VDD2L > VDDQ - 0.2 V holds). All rails in range about 5 ms after the first one moves (tINIT0 <= 20 ms).
  Any host rail order works: nothing starts until both host rails are up. On the ZCU104 one signal
  (UTIL_5V0_PGOOD) releases 3.3 V, 2.5 V and 1.2 V together and their order is set by PMIC firmware, which is
  why the card does not depend on it.
- **Power-off.** If the host 1.2 V rail falls first and takes at least about 1 ms, VDD2H follows it, U2's power
  good disables U4, VDDQ follows within a few tens of mV of the 200 mV allowance, and VDD1 goes last. A faster
  collapse of 1.2 V, or 3.3 V falling first, is an uncontrolled power-off in JEDEC terms (allowed 400 times);
  the two bleeders then bring every rail below 0.3 V inside 2 s.
- **Socket currents.** 1.2 V: <= 0.77 A over 19 contacts = 41 mA each (limit 0.5 A). 3.3 V: <= 30 mA on finger 255.
  The 2.5 V VPP fingers, VTT, VREFCA and finger 109 are not connected.
- **1.2 V distribution.** All 19 VDD fingers go to V1P2 (never to GND) through an island on **In2** under the finger row,
  x 28.7-46.55, y 1.0-3.6, one 0.40 mm via per front/back finger pair at y = 3.05 (placed with the
  skeleton, `floorplan.finger_vias`). In2 is the upstream place for a power island (In2 = GND + power island), and no
  signal of the card sees this one: F.Cu refers to In1 and B.Cu to In4, both solid GND under the whole finger row and the
  divider rows, and In3, the only layer that refers to In2, carries nothing south of the node vias (y = 6.00; the island
  ends 0.40 mm in plan from the nearest In3 copper, the pad of a node via). The back-finger write stubs have GND (In4) under them all the way. The key slot would split the island into two
  halves, 12 fingers west (x 28.7-37.5) and 7 east (x 39.1-46.55): a **bridge** on In2 north of the slot (x 36.0-40.6, up to
  y = 5.40: 1.1 mm of copper above the slot, 0.42 mm above the antipad of the one shunt GND via that stands in it, the two
  necks beside the slot 1.5 mm wide) joins them, so the island is one piece and all 19 fingers feed (41 mA per contact).
  Three shunt GND vias and the two finger vias of VSS pairs east of the last VDD finger pierce it (the finger vias carry no pad on an inner layer
  on which they connect to nothing). The island with its bridge is the simplest arrangement that lets all 19 fingers feed, and no
  In3 trace lies over it.
- **1.2 V feed: a 1.5 mm STRIP (section 15).** A tab of the island (x 44.75-46.55, up to y = 5.45) carries four 0.45 mm vias
  (192 mA each) up to a **1.5 mm wide F.Cu strip** at x 44.90-46.40, which runs north in the corridor between the comb and the read end
  (`floorplan.data.reserved`: no track and no via of the data side in it) to y = 20.65 and turns west as a 0.9 mm arm (y = 21.05) to U2. U4 and the
  enable resistor of U3, which stand on B.Cu, tap the arm through vias. Sized by calculation (0.491 mohm per square at 0.035 mm copper):

  | Piece | Length | Width | Current | Resistance | Drop | Loss | Rise (IPC-2221) | Density |
  |---|---|---|---|---|---|---|---|---|
  | strip | 16.25 mm | 1.50 mm | 0.77 A | 5.3 mohm | 4.1 mV | 3.2 mW | +0.4 K | 14.7 A/mm2 |
  | arm to U2 | 10.1 mm | 0.90 mm | 0.53 A (0.77 A on its first 1.4 mm) | 5.5 mohm | 2.9 mV | 1.5 mW | +0.4 K (+0.9 K) | 16.8 A/mm2 |
  | tap of U4 (B.Cu) | 5.6 mm | 0.40 mm | 0.23 A | 6.8 mohm | 1.6 mV | 0.4 mW | +0.2 K | 16.4 A/mm2 |

  A 5 mV budget for the strip (0.4 % of 1.2 V) needs 1.21 mm; 10 K of rise would need 0.12 mm. 1.5 mm is that width plus 24 % for etch and copper
  tolerance. 19 fingers at 41 mA (limit 0.5 A).
  **Fingers to U2 IN, with the In2 island: 12.4 mV for the west-most finger, 11.0 mV on average** = island 5.2 / 3.8 mV (18 um copper, 0.956 mohm per square; solved on
  the filled copper by gate 9, every finger via injecting its share; about 2.3 mV of it is the bridge north of the key slot) + 0.1 mV in the four vias +
  7.1 mV in the strip and the arm. To U4 IN 1.6 mV more. Gate 9 holds the whole path, island included, to 15 mV: at the worst corner (host rail 1.14 V, 0.52 A, pass element 68 mV) U2 still delivers
  1.14 - 0.015 - 0.068 = 1.057 V, 47 mV above the JEDEC minimum of VDD2H, and at a nominal 1.20 V its input is 140 mV above the output, twice the 68 mV the pass element needs.
  The strip passes over the eight In3 read lines (on their 45-degree ramp) and the two In3 legs of RDQS (90 degrees), with In1 and In2 solid GND
  between: 10 plan-view crossings. Decoupling: C512 (100 nF) and C502 (22 uF) east of the foot, V1P2 pad on the strip, GND pad straight to the finger
  vias of the VSS pairs 175 / 176 and 180 / 181 (no via in the In3 zone of the read bundle); C513 (100 nF) and C501 (22 uF) on the arm; C508
  (4.7 uF) at U4. Alternatives that were not taken:
  - **a pour that fills the corridor** (51 mm2; the user: "What is the giant pour you added"): ten times the copper the current needs;
  - **a strip on In4 or In3:** In4 is the far reference of the eight In3 read lines and In3 is their own layer;
  - **around the east end of the read fingers:** 40 mm more copper (13 mohm at 1.5 mm) through the RDQS drop and the DAC network;
  - **the island on In4 with a bridge there:** three back-side resistors, two GND vias and the back-finger stubs would refer to a V1P2 plane;
  - **the east half of the island alone:** 7 fingers at 110 mA per contact would do electrically, but the twelve west fingers would be left without a path.
- **Dissipation.** U2 <= 0.13 W, U4 <= 0.16 W (90 K/W: +14 K), U3 0.04 W.
- **Capacitors** (X5R; DC-bias loss is a typical figure, the vendor curve is not in `references/parts`):

| Where | Fitted | Effective | Need |
|---|---|---|---|
| U1 VDD2H / VDD2L | 12 x 4.7 uF 0402 + 22 uF | about 65 uF | U2 >= 2.2 uF; 40 mohm at 2-10 MHz (J Table 408): about 5 mohm |
| U1 VDDQ | 8 x 4.7 uF + 4.7 uF | about 40 uF | U4 >= 1 uF; 40 mohm |
| U1 VDD1 | 4 x 4.7 uF + 1 uF | about 12 uF at 1.8 V | U3 >= 0.1 uF effective; 100 mohm |
| 1.2 V input | 2 x 22 uF + 4.7 uF + 2 x 100 nF | about 40 uF | U2 >= 1 uF, U4 10 uF recommended |
| 3.3 V | 4.7 uF + 3 x 1 uF + 100 nF | about 4 uF | bias pins >= 0.1 uF |
| DAC CAP | 1 uF | 0.75 uF | 0.5-15 uF |

The decoupling is all one value (4.7 uF 0402, as upstream) on B.Cu under U1 on the via-in-pad power balls: 24
instead of the upstream 38, because one rail and one channel less are in use.

- **Test pads:** VDD1, VDD2H, VDDQ, VREF_RD, GND. None on signal nets.

**Where the 1.05 V is.** VDD2H has no pour on an outer layer: it is an island of In4 under rows K .. AA of U1, reaching east to the four output
vias of U2 (section 15.2). The VDD2L balls are tied to VDD2H: JEDEC single-rail mode (J 4.1.2), allowed from power-up; MR13 OP[7] = 1 is
written during initialisation (section 7).

## 5. U1 tie-offs and via-in-pads

| Balls | Treatment | Parts |
|---|---|---|
| ZQ (H15) | 240 ohm 1 % to VDDQ (J 4.2.1.5; upstream had it to GND) | R401 |
| CS1_A (G6) | to the adjacent VSS ball G5 on F.Cu | none |
| DMI0_A (A4), DMI1_A (A12) | tied to VSS. A12 to the adjacent VSS ball A11 on F.Cu (A11 keeps its via). A4 by a via-in-pad of its own into the GND planes: the DQ4 neck uses the row channel between A4 and B4, so there is no F.Cu strap; the via is also the GND pad of the VDDQ capacitor C415 (A3 / A4, B.Cu) | none |
| DQ8-15_A | **open** (no via, no track, no part), intentionally open: see below | none |
| WCK1_t_A / WCK1_c_A | 10 k to GND / 10 k to VDDQ on B.Cu (static clock-stop level; the resistors are the only rework access). Kept: see below | R404, R405 |
| RDQS1_t/c_A | open (outputs) | none |
| Channel B: CA0-6, CS0, CS1, CK_t, WCK0_t, WCK1_t | to the adjacent VSS ball | none |
| Channel B: WCK0_c, WCK1_c | to the adjacent VDDQ ball | none |
| Channel B: CK_c (N7) | to VDDQ through its via-in-pad (no VDDQ neighbour): 0.10 mm B.Cu track up the column 7/8 channel and along the R/T row channel to the T4 via | none |
| Channel B: DQ, DMI, RDQS; all NC and RFU balls | open | none |

**The unused byte: DQ8-15 open, WCK1 tied off** (user: "also why are there pulldown elements in the bottom left of U1"). The eight data balls
of the second byte of channel A are left unconnected. Two 4 x 10 k pull-down arrays (RN401 / RN402, west of U1) once held them at ground; they were a
precaution without a source, and they cost eight F.Cu escapes west out of the ball field: 37 mm of stubs that the x16 die drives on every read, crossing
the In3 command bundle 15 times in plan view. What the guidance says:
- **JESD209-5C** has no rule for the unused byte of a x16 die ("byte mode" is a different, x8 die, not a setting). It treats an undriven DQ bus as
  normal ("DQ channel can be floated"), and DQ, CA, CS, CK, WCK and NT ODT are all off by default (MR11, MR17, MR18). The DQ receivers are used
  only during write bursts, and those of the upper byte are clocked by WCK1, which is held static.
- **AMD UG583** (PCB design guide, LPDDR4 with an ECC device: a x16 die with only DQ[7:0] in use, Tables 55 / 56) leaves DQ[15:8], DMI1 and DQS1
  of that device without a connection; for DDR4 it says "DQ[15:8] can be left floating" and ties only the unused strobe pair to its inactive levels.
- **Micron TN-40-40** (DDR4 point to point, Table 11): unused DQ "can be floated". Only TN-46-14 (DDR1, 2008) asks for pull-downs.
- The card already relied on it: all 16 DQ, both DMI and both RDQS of channel B, and RDQS1 of channel A, are open.
- A mode register is not needed: DQ ODT (MR11) would hold the open balls at VSS during writes but would also load DQ0-7 (write high 0.42 V
  falls to about 0.30 V): not used.

So there are no arrays, no escapes and no GND vias for them; the eight balls are listed in `no_connect` with the reason, and the command bundle is
crossed 15 times instead of 30. **R404 / R405 stay** (WCK1_t low, WCK1_c high through 10 k each): WCK1 is an input-only differential clock whose
buffer is switched on together with WCK0 at every CAS WS_WR / WS_RD / WS_FS (J 7.2.1), so a floating pair could chatter during every burst; the
JEDEC valid static level is WCK_t low / WCK_c high, which matches the strobe guidance of UG583. They have no track and cross nothing. A strap
to the neighbouring VSS / VDDQ balls (as channel B has) would save the two parts and two via barrels beside the CA6 / CA3 / CA4 channels, but
gives up the rework access and would draw 12 mA if WCK ODT were ever enabled; not done.

**DMI is strapped, not pulled down.** The strap is safe as long as the die never drives DMI: with read DBI, link
ECC and read data copy off the DMI driver is off for READ, MRR and READ DQ CALIBRATION (J Table 233 / 234); only
READ FIFO drives it ("follows FIFO definition"), which would put 12 mA per ball into the strap. That is not
destructive, but READ FIFO, read DBI, link ECC and data copy are listed in section 7 as not to be used. WRITE FIFO
is harmless (DMI is an input then), and CBT mode 2 is unavailable (it needs DMI0 as an input).
**Via-in-pads.** Every supply ball and every routed signal ball has one, except A9, B9, C9, D9, E9, F9, G9 (CK
corridor, section 1). Each of these is linked on F.Cu to a touching ball of its net that keeps
its via (`floorplan.U1.via_in_pad`). DQ0-15, H1, the balls strapped on F.Cu and NC / RFU balls have none. In the byte-0
corner (section 14): A4 has a via (above), C5 (VSS) has its via (the WCK
pair does not pass it), and the via-in-pads of B3 / C4 (RDQS) connect on In3 and keep a pad there, trimmed to
0.35 mm (every other via-in-pad pad is 0.40 mm) because the pad of C4 stands 0.15 mm from the CA0 trace.

All supply balls were checked ball by ball against the upstream schematic export and the Micron ball maps
(VSS 102, VDD2H 61, VDD2L 16, VDDQ 40, VDD1 4).

## 6. Mechanics, stackup, layer plan, impedance

- Outline 69.6 x 30.0 mm, key at 38.3 mm (1.0 x 4.0 mm), latch notches 18-22 mm, 260 fingers 0.35 x 2.55 mm
  (0.15 to 2.70 mm above the edge), pin 1 at 1.425 mm, tooling holes 1.8 mm at (2.0, 6.0) and (67.6, 6.0), all as in the
  project edge footprint (`lib/README.md`). The mask-free finger strip ends at y = 2.75 (the top of the pads' own openings), so the lands of the
  finger vias (row y = 3.05) keep a 0.10 mm mask dam. Three assembly fiducials per face (FID1 .. FID6: 1.0 mm dot in a 2.0 mm mask opening, at
  (6.0, 26.5), (8.0, 9.0) on both faces, (54.0, 25.5) front, (64.0, 26.5) back; nothing within 2.4 mm of a centre). The drill / place origin of the
  board file is the card origin. No part courtyard below y = 4.00 on either face (the edge footprint's
  rule areas apply to courtyards: the lowest one is at 4.02), parts <= 0.8 mm tall between 4.00 and 5.10 mm (only
  0201 / 0402 there), <= 1.2 mm above (U3 is the thin DDC package for this reason). U1 ends at 26.5 mm.
- **Stackup and layer plan** (the upstream structure, copper weights and the three 0.11 mm prepregs unchanged; only the two cores thinned). The plan
  follows the upstream convention: each high-speed group has a layer with little else on it and a solid ground plane next to it; In1, In2 and In4
  carry no track at all, In3 carries the 20 high-speed nets and nothing else:

| Layer | mm | Role |
|---|---|---|
| F.Mask | 0.010 | |
| F.Cu | 0.035 | U1, the eight DQ lines (comb), the last 5.8 mm of the RDQS pair into its fingers; U2 (VDD2H), the DAC, the 1.2 V strip and arm, the 3.3 V trunk, the VDD1 link around the north and west of U1, test pads, three fiducials; no zone at all (no ground flood: the reference is In1) |
| prepreg PR2116 | 0.110 | |
| In1 | 0.018 | solid GND |
| core FR4 | **0.360** (was 0.400) | |
| In2 | 0.018 | GND, no tracks, solid under every In3 trace; one island: V1P2 under the VDD fingers (x 28.7-46.55, y 1.0-3.6) with its bridge north of the key slot and the tab under the foot of the 1.2 V strip (section 4) |
| prepreg PR2116 | 0.110 | |
| In3 | 0.018 | command bundle; DQ read branches; RDQS pair from U1 to its via pair above fingers 219 / 221; nothing else |
| core FR4 | **0.360** (was 0.400) | |
| In4 | 0.018 | GND + VDD2H island (U1 rows K-AA, from y = 18.0, east to U2: 53 of the 72 VDD2H via-in-pads); solid GND under the whole finger row |
| prepreg PR2116 | 0.110 | |
| B.Cu | 0.035 | WCK pair, seven read finger tracks, decoupling; U3 (VDD1) and U4 (VDDQ) with their parts, VDD1 / VDDQ lanes and rails, VDDQ / VDD2H pours under U1, RESET_n, three fiducials; no ground flood (the reference is In4) |
| B.Mask | 0.010 | |

  Total 1.212 mm with mask, **1.192 mm at the bare fingers, 1.200 mm with hard gold**. The upstream stack is
  1.272 mm at the fingers: inside 1.20 +/-0.10 but with 28 um to the upper limit. 0.36 mm (14 mil) is the standard
  core that centres the nominal. No nominal can absorb a generic +/-10 % fab window (it is 0.24 mm wide against a
  0.20 mm standard window; here 1.073-1.311 mm), so the fab note must call out **1.20 +/-0.10 mm finished over the
  fingers**. Thinning changes In3 from 46.0 to 45.6 ohm and nothing else. The via-in-pad fill-and-cap plating can
  leave up to 0.055 mm of outer copper instead of 0.035, fingers included: the card is then 1.24 mm, still inside.
- **Plane pull-back.** The board rule copper-to-edge is 0.15 mm only so that the finger pads pass. In1 and In4 stop
  1.0 mm from the finger edge, In2 0.5 mm, In3 3.0 mm, any outer-layer pour 2.85 mm (rule area; the lowest pour copper is at y = 11.7); every layer keeps
  0.3 mm from all other edges including the key slot.
- **No ground flood on the outer layers** (as upstream): F.Cu and B.Cu carry tracks, pads and, on B.Cu under U1, the VDDQ / VDD2H pours. Copper per face
  16.6 % (F.Cu) and 16.0 % (B.Cu) of the board area, a third of it the finger row; the fab note forbids thieving (section 15.4).
- Widths (field solver, er 4.29, +/-5 %; delays: In3 6.91 ps/mm, outer 0.15 mm 6.01 ps/mm, outer pair 5.69 ps/mm):

| Class | Layer | Geometry | Impedance |
|---|---|---|---|
| CA, CS | In3 | 0.15 mm, pitch 0.80 | 45.6 ohm |
| CK | In3 | 0.10 / 0.10 mm | 84.6 ohm differential |
| finger-side stubs | F.Cu / B.Cu | 0.20 mm | 45.6 ohm (46.7 / 44.8) |
| DQ lines and teeth | F.Cu | 0.15 mm (0.09 neck in the BGA, 0.6-5.3 mm), bundle pitch 0.55 (gap 0.40 = 3.6 h) wherever two lines run side by side outside the land field, 0.8 mm between teeth | 52.4 ohm (53.9 / 51.2); neck 64.2 |
| WCK; RDQS drop | B.Cu; F.Cu | 0.10 / 0.10 mm | 91.8 ohm differential (97.6 / 86.7) |
| RDQS | In3 | 0.10 / 0.10 mm (the CK geometry) | 84.6 ohm differential |
| DQ read branches | In3 | 0.15 mm, pitch 0.55 (gap 0.40) | 45.6 ohm |

  The outer-layer design values assume 0.040 mm copper and 0.020 mm mask. In brackets: the stackup-table nominal
  (0.035 mm copper, 0.010 mm mask) and the cap-plated case (0.055 mm copper). The fab note asks for controlled
  impedance: In3 0.15 mm 46 ohm, In3 pair 85 ohm, outer 0.20 mm 46 ohm, F.Cu / B.Cu pair 92 ohm (WCK on B.Cu, RDQS on F.Cu), all +/-10 %, widths
  adjustable by +/-0.02 mm.
- Vias: through 0.40 / 0.20 (signals and every GND via inside the divider fields) and 0.45 / 0.20 (power);
  via-in-pad 0.40 / 0.15 under U1, filled and capped. **Filled and capped as well** (253 vias in all, fab note 6 lists them): the ten command
  node vias (0.40 / 0.20; their pads stand 0.05-0.10 mm from 0201 lands) and the eight 0.45 / 0.20 vias in the exposed pads of U2, U4, U5 (one of them, in the pad of U4, also touches the GND land of C509 on the other face and would take its
  solder if it were open; gate 9 fails on any plain via whose hole stands within 0.15 mm of a land with paste). The node vias of the data side (eight DQ,
  two WCK) are plain: the DQ lands stand 0.25 mm from the via pad.
  The U1 via-in-pads, the fence, shunt, read, return, reference, finger and data-side node vias and every via of the power stage have no pad on
  inner layers where they connect to nothing (outer pads kept); **the ten command node vias keep their pads on every layer** (stage s20 as
  simulated). The In3 pads of the RDQS via-in-pads B3 / C4 are 0.35 mm. Clearance 0.09 mm as a rule, 0.10 mm the smallest gap drawn; track
  0.09 mm minimum (inside U1 only), hole to copper 0.20 mm. 0 / 45 / 90 / 135 degree segments only.
- Fingers: hard gold, 0.25 mm x 45 degree chamfer only, copper starting <= 0.30 mm from the edge.

**Floorplan** (`floorplan.regions`, checked for overlap per copper layer; `floorplan.placements` and
`floorplan.vias` give every part and via of the fields, `floorplan.data.wires` every wire of the data side):

| Region | x | y | Layers | Contents |
|---|---|---|---|---|
| command field | 15.9-24 | 4-7.75 | F, In3, B | R101..R120, node vias, fence vias |
| command in3 tuning | 15.5-24.8 | 7.75-11.5 | In3 | length tuning of CA / CS: parallel shifts and bumps (continues 0.2 mm under the U1 outline) |
| U1 | 14.525-26.925 | 11.5-26.5 | F, In3, B | U1, via-in-pads, In3 command bundle; byte-0 corner: the eight F.Cu necks, the WCK pair on B.Cu (diagonal D3 - C2 - B1), the RDQS pair on In3 (row channel B / C); decoupling on B.Cu |
| write field wck | 34.2-36.85 | 4-8.2 | F, B | WCK columns: R209, R210 (series), node vias, R219, R220 (shunts), their GND vias; B.Cu: the last 2 mm of the pair |
| write field dq | 36.85-44.3 | 4-6.6 | F, B | DQ columns: R201..R208 (240R, on the finger's face) and R211..R218 (150R, back to back on the other face), the eight node vias (the T) at y = 6.00, the shunt GND vias south of the row, three return vias |
| dq teeth f cu | 36.85-44.3 | 6.6-10.4 | F | the eight teeth of the comb, straight down the column axes |
| dq lines f cu | 26.925-44.3 | 10.4-16.6 | F | the eight DQ lines at 0.55 mm pitch and their one parallel 45-degree fan |
| wck run b cu | 26.925-36.85 | 8.2-10.5 | B | WCK pair running east as the lowest lane, turning south into its columns |
| read bundle in3 | 36.85-57.45 | 5.7-12.45 | In3 | the eight read branches: risers in the lanes, the bundle at 0.55 mm pitch across the teeth, the 45-degree ramp, the runs to the read vias |
| rdqs high run | 39-58.375 | 12.45-16.6 | In3 | RDQS pair on In3 north of the read vias, east and on one 45-degree run down to the axis of its fingers |
| rdqs drop in3 | 57.45-59.125 | 8.2-12.45 | In3 | RDQS pair on In3 south to its via pair at y = 8.50, east of the last read via; the two vias of the pair and their two GND return vias |
| rdqs drop f cu | 57.45-58.4 | 5.6-9.35 | F | RDQS pair on F.Cu from its via pair straight down to fingers 219 / 221 (the last 5.80 mm), east of every read track; the pads of the via pair and of its return vias (the eastern two reach 0.45 mm into the empty south-west corner of the DAC region, 0.94 mm from the pad of TP4) |
| v1p2 feed corridor | 44.4-48.5 | 4-19.3 | F | the 1.2 V strip of the power stage (1.5 mm, F.Cu) with C512, C502 east of its foot; nothing of the data side on F.Cu |
| n networks f cu | 48.5-56.4 | 4-7.2 | F | 39 ohm (y = 4.75) + 100 nF (y = 6.15) of the seven front N fingers, bus at y = 7.00 |
| n networks b cu | 56.7-58.1 | 4-7.2 | B | 39 ohm + 100 nF of the back N finger 216 |
| rdqs termination f cu | 57.2-59.15 | 4-5.6 | F | R309, R310 beside the coupled pair in the row of the 39 ohm resistors, GND down the axes of the VSS finger pairs beside the drop (the VSS finger vias of the pairs 217 / 218 and 222 / 223 (y = 3.05)) |
| reset parts | 60.8-62.2 | 4-7.4 | B | R402 (on the axis of finger 232), R403 (on the axis of the VSS finger 234) |
| dac | 58.4-66.3 | 8-15 | F | TP4 (on the feed), U5, R331..R333, C331..C333, C511, the GND vias of C331 and R333 |
| vdd2h regulator | 27.2-44.3 | 18-26.4 | F | U2, C501, C503..C505, C513, R501..R503, the test pads TP1 (VDD1) and TP2 (VDD2H) |
| u4 bias cap | 45.6-46.8 | 22.4-24.4 | F | C509: on the 3.3 V trunk beside the tap via of U4 |
| test points | 49.8-54.2 | 18.2-21.9 | F | TP3 (VDDQ), TP5 (GND) |
| fiducial fid1 | 4.4-7.6 | 24.9-28.1 | F | FID1: fiducial, nothing else within 1.6 mm of its centre on this face |
| fiducial fid2 | 6.4-9.6 | 7.4-10.6 | F | FID2: fiducial, nothing else within 1.6 mm of its centre on this face |
| fiducial fid3 | 52.4-55.6 | 23.9-27.1 | F | FID3: fiducial, nothing else within 1.6 mm of its centre on this face |
| fiducial fid4 | 4.4-7.6 | 24.9-28.1 | B | FID4: fiducial, nothing else within 1.6 mm of its centre on this face |
| fiducial fid5 | 6.4-9.6 | 7.4-10.6 | B | FID5: fiducial, nothing else within 1.6 mm of its centre on this face |
| fiducial fid6 | 62.4-65.6 | 24.9-28.1 | B | FID6: fiducial, nothing else within 1.6 mm of its centre on this face |
| regulators b cu | 35.9-51.4 | 20.4-27 | B | on the back: U3 (VDD1) with C506, C507, R509, R504; U4 (VDDQ) with C508, C510, R506..R508 |
## 7. Gateware interface: what the hardware assumes

**The one place for the gateware is `design/gateware/`** (written by `scripts/gen_gateware.py` at every build from `design/netlist.json` and
`design/gateware_deskew.json`): `PINMAP.md` / `pinmap.csv` (every FPGA pin with finger, net, ball, bank, nibble, standard), `lpddr5_testbed_zcu104.xdc`
(+ `_rx_slvs.xdc` for the fall-back receiver), `lpddr5_testbed_pins.vh` (polarities and per-bit delays), `BRINGUP.md` (pin states, reset timing, the
mode-register writes byte by byte, the DAC, the receiver, what to measure first) and `vivado_pincheck/` (a top level on the real pins and its Vivado
2026.1 results). This section states what the hardware assumes; the files carry the detail.

- **Clock plan.** WCK is tied to CK at 2:1 (MR18 OP[7] = 1, the reset default) or 4:1. The data target is 100 Mb/s (set by the 2.6 ns write node,
  section 2), i.e. WCK = 50 MHz: **every READ and WRITE runs at CK = 25 MHz (2:1) or 12.5 MHz (4:1)**; tCK(avg) may be as long as 200 ns. **CK = 800 MHz
  (1.6 Gb/s on CA) is for command-only traffic**, the purpose of the card. The controller changes the clock frequency between the command stream
  and any read or write (clock change, J 7.6.7, or frequency set points, J 7.6.3), each set point with its own latencies (MR1 / MR2), VREF (MR12 /
  MR14) and termination codes.
- **The 1.6 Gb/s command bus needs native mode in the FPGA** (Vivado 2026.1 pin check): `TX_BITSLICE` with `BITSLICE_CONTROL` and a PLL in bank 65;
  component-mode `OSERDESE3` stops at 1.25 Gb/s (DS925 tables 70 / 71). 1.6 Gb/s is the data-sheet maximum of the part. All ten nets lie in byte
  group T0 of bank 65 with CK on bitslice 0 of the upper nibble, which is what native mode wants; the slow boot clock is made by repeating bits.
- Mode registers (whole bytes in `BRINGUP.md`): MR13 = A0h: OP[7] = 1 (single VDD2 rail), OP[5] = 1 (data mask off), OP[4] = 0 (DMI behaviour mode 1:
  mode 2 with data mask on would drive DMI into the strap during READ DQ CALIBRATION, J Table 234); MR17 = 3Eh: OP[2:0] = 110b (pull-up strength
  defined) with OP[5:3] = 111b (CK / CS / CA termination off); MR3 PDDS = 40 ohm; MR11 = 00h and MR18 OP[2:0] = 000b (all DRAM ODT off: defaults,
  written explicitly); MR20 OP[1:0] = 10b; MR12 / MR14 VREF trained (about 42 %, 0.21 V).
- Never used, because DMI is strapped to VSS: READ FIFO, read DBI, link ECC, read data copy, MASKED WRITE, CBT
  mode 2. READ DQ CALIBRATION is fine (DMI driver off in mode 1).
- Read pins input-only in the XDC; `IBUFDS_DPHY` with `IOSTANDARD = "MIPI_DPHY_DCI"` on the instance,
  `DIFF_TERM = "FALSE"`, `HSRX_DISABLE = 0`, `LPRX_DISABLE = 1`. VREF_RD at or below 0.20 V if the read high level is above 0.42 V.
- The RDQS receiver output is valid only inside the read burst: gate it with the read-enable window.
- All divider-driven pins low until the rails are up; write DQ pins Hi-Z during reads. No power-good of the card reaches the FPGA (it would cost a
  finger, a level shift from 3.3 V and a route across the read end), so "until the rails are up" is a fixed wait: the card's rails are in range about
  5 ms after the host rails, long before configuration ends; then RESET_n low for at least 200 us, CS low 10 ns before and 2 ms after RESET_n rises
  (J Table 20).
- Option: RDRV_48_48 on the command pins (IBIS 30 / 35 / 42 ohm) matches the 39 ohm host line better than
  RDRV_40_40 and still gives 0.386 / 0.420 / 0.453 V.
- Static skew left by the +/-5 ps knowledge of the host is removed per bit with ODELAY / IDELAY.
- **Pin map of the data byte** (`design/gateware_deskew.json` carries it with the delays; the command pins are unchanged):

  | Bit | Write finger | FPGA ball (bank 64) | Byte group | Output delay ps | Read finger P / N | FPGA ball P (bank 66) | Byte group | Read total ps | To the strobe ps | Input delay ps |
  |---|---|---|---|---|---|---|---|---|---|---|
  | DQ0 | 150 | AL15 | T1L | 40.7 | 204 / 203 | AH11 | T2L | 896.87 | -26.24 | 52.8 |
  | DQ1 | 145 | AL16 | T1L | 51.0 | 208 / 207 | AG11 | T2U | 912.31 | -10.81 | 37.4 |
  | DQ2 | 156 | AA14 | T3U | 27.1 | 190 / 191 | AG13 | T2L | 861.37 | -61.75 | 88.3 |
  | DQ3 | 151 | AA16 | T3U | 41.0 | 194 / 195 | AF8 | T2U | 880.59 | -42.53 | 69.1 |
  | DQ4 | 143 | AD16 | T3U | 53.5 | 215 / 216 | AJ9 | T1U | 949.67 | +26.56 | 0.0 |
  | DQ5 | 144 | AH16 | T2U | 23.1 | 212 / 211 | AK12 | T1L | 946.18 | +23.06 | 3.5 |
  | DQ6 | 157 | AL17 | T1U | 3.4 | 186 / 187 | AE12 | T3U | 844.38 | -78.74 | 105.3 |
  | DQ7 | 161 | AM16 | T1L | 0.0 | 182 / 183 | AD14 | T3L | 835.98 | -87.13 | 113.7 |
  | RDQS | | | | | 219 / 221 (RDQS_t / RDQS_c) | AL8 / AK8 | T1U (QBC) | 923.12 | 0 | 0 |
  WCK: finger 137 (AF18) = WCK_c, 139 (AG18) = WCK_t. RDQS: finger 219 (AL8) = RDQS_t, 221 (AK8) = RDQS_c. RESET_n: finger 232
  (AP11, bank 66, byte group T0L, LVCMOS12, static).
- **Write deskew** (status: accepted by the user: "The write group's spread is acceptable"). The write group is not delay-matched on the card: the eight totals
  (FPGA die pad to ball) span 748.0 .. 801.5 ps, +/-26.7 ps (section 14). Delay each DQ output by the value of the table (latest
  total minus own total; ODELAYE3, `DELAY_FORMAT = TIME`, `DELAY_VALUE` in ps) and all eight arrive together. The write pins lie
  in three byte groups of bank 64 (T1, T2, T3) and WCK in T2L: use the delay primitives per pin, not one native byte interface.
  The values come from the copper as measured by gate 7 of `check_pcb.py`, which rewrites the JSON at every build; the host
  figures in them are good to +/-5 ps, so write training has to refine them like any static skew.
- **Read deskew** (status: accepted by the user: "Same with read group"). The read
  group is not delay-matched on the card: the eight totals (ball to FPGA die pad, card and host as modelled) span
  836.0 .. 949.7 ps, +/-56.8 ps about 892.8 ps (section 14). Delay each DQ input by the value of the table (latest total minus
  own total; IDELAYE3, `DELAY_FORMAT = TIME`, `DELAY_VALUE` in ps: range 0 .. 1100 ps on UltraScale+, 512 taps of 2.1 .. 12 ps,
  IDELAYCTRL with a 300-800 MHz reference and `EN_VTC` handling; table values below one tap are left to training) and all eight reach the
  capture flops together. The largest
  value, 113.7 ps, is a tenth of the range and 1.1 % of the 10 ns bit time at 100 Mb/s. **The strobe is not delayed by the table and is not on
  the centre of the group**: RDQS stays on In3 down to its fingers and arrives at 923.12 ps, 30.3 ps after the centre, 87.1 ps after the earliest
  bit and 26.6 ps before the latest (column "to the strobe" of the table: total of the bit minus the strobe); after the deskew every DQ arrives
  26.6 ps after the strobe edge that left the DRAM with it, and the capture delay of the strobe (a quarter of the bit time and tDQSQ, as in any DDR
  read) is added to that by the gateware. An IDELAYE3 in TIME mode has an insertion delay even at value 0: if the strobe is sampled like data
  (the 100 Mb/s scheme of the Vivado check: 8x oversampling with the system clock), pass it through one at value 0 as well, so that the 26.6 ps
  hold. The strobe cannot be used as a clock that way: an IDELAYE3 output may not drive a BUFG (REQP-1741), and its pin AK8 / AL8 is a QBC pin, not
  a global-clock pin, so receiver -> BUFGCE needs `CLOCK_DEDICATED_ROUTE FALSE`; a strobe-clocked native RX would want the read data in the
  strobe's nibble, which the read end without crossings does not give (six nibbles). The read pins lie in six byte groups of bank 66 (T3L, T3U,
  T2L, T2U, T1L, T1U) and the strobe in T1U: use the delay primitives per pin, as for the writes. The values come from the copper as measured by
  gate 7 of `check_pcb.py`, which rewrites the JSON at every build; the host figures in them are good to +/-5 ps, so read training
  has to refine them like any static skew.
- **RDQS is inverted on the host pair DQS6:** finger 219 (AL8, the N pin) carries RDQS_t, finger 221 (AK8, the P pin) RDQS_c.
  Invert the strobe after the differential receiver (or swap the capture edges); nothing else changes.
- No series resistor in the read branches (section 3): if the receiver minds the 1-2 ns of ringing after an edge,
  lower the DRAM drive strength (MR3 OP[2:0]: 48 or 60 ohm) or the pull-up calibration code (MR17) and re-centre
  VREF_RD; sample in the second half of the unit interval.
- **WCK is inverted on the host pair:** finger 137 (AF18, the P
  pin of the pair) carries WCK_c, finger 139 (AG18, the N pin) carries WCK_t. Drive the differential output with
  inverted data (or swap the phase of the WCK generator by 180 degrees); nothing else changes.
- RESET_n is the only output in bank 66 (LVCMOS12 DRIVE 8, static, AP11, byte group T0L): Vivado accepts it beside the eight MIPI_DPHY_DCI read pairs and
  the strobe of that bank (Vivado 2026.1 pin check, section 8, item 31). The default 12 mA drive is rejected in an HP bank.
- CK_t is on AM21, whose second function is SMBALERT: an ordinary I/O after configuration; before it, the pin could at most move the CK_t divider
  node between 0 V and 0.42 V while the DRAM is in reset.
- Tool versions: `IDELAYCTRL` needs `SIM_DEVICE = "ULTRASCALE_PLUS"` in Vivado 2026.1 (2023.1 wants "ULTRASCALE"); keep the `IOSTANDARD` attribute
  on every `IBUFDS_DPHY` instance (2023.1 refuses the bitstream without it).
- The reference DAC is on the PS I2C1 bus behind the switch at 0x74, port 7 (also reachable from PL pins N12 / P12); reads work without it at the
  0.187 V default.

## 8. Open risks, and what to measure first

### 8.1 What to measure first

The order below is the order of `design/gateware/BRINGUP.md` section 7, which gives the expected value of every step.

1. **Rails** on TP1 .. TP5 and the host 1.2 V at the card under load (items 10, 11 below), before U1 is trusted with anything.
2. **A static high at a divider node** (command, CS, write DQ): gives the real driver resistance (item 2).
3. **Termination really off** on the read pins: under 1 mV across a 39 ohm N resistor (item 7).
4. **RESET_n levels**, then the first mode-register read at the boot clock.
5. **Read high level** into 150 ohm and its dependence on MR17 / MR3 (item 5); then the **VREF_RD sweep** against bit errors at 100 Mb/s with the
   MIPI receiver and with the SLVS fall-back (item 6): this is the test of the pseudo-differential read scheme.
6. **A read finger with an active probe** for the three drive strengths (item 29): the open FPGA end rings for 1-2 ns per edge.
7. **The unused half of the die and the idle channel B** (items 8, 9): supply currents and read stability.
8. **The command eye at 1.6 Gb/s**, the purpose of the card (items 3, 4, 18): per-bit ODELAY and MR12 sweep against command errors; TDR of CA2
   against CA0 at the fingers of a bare card checks the on-card match.

Before ordering boards: obtain the SK hynix data sheet or package outline (item 1), agree the stackup, the finished thickness over the fingers and
the via-in-pad process with the fab (items 14, 19, 27, 35), and decide the points of section 15.6 that were left to the user.

### 8.2 Assumptions that need bench or simulation confirmation

1. **Part.** No data sheet of H58G56BK7BX068N is in the repository and none was found in public: body size, ball
   map, rank count, CS ODT and IDD are taken from JEDEC and Micron. CS1 is tied low, which covers a two-rank
   part. Obtain the SK hynix data sheet or package outline before ordering boards.
2. **Driver resistance** 26-44 ohm (IBIS 25-35 ohm for "40 ohm", DS925 36-44 ohm). Measure a static high
   at a divider node; all limits hold over the whole range.
3. **Command eye at 1.6 Gb/s** with the real IBIS driver, the socket (HFSS: G-S-G contacts, finger, first via), the
   divider column (stub, 0201 parts, node via and its fence via: ten signal vias in a row at 0.8 mm pitch with the nearest
   GND via 1.70 mm away, probably the largest coupling term on the card) and the In3 bundle in the BGA via field. The
   linear model gives 2.3 x mask height; crosstalk and the socket are not in it.
4. **Unterminated CA / CK at 800 MHz.** If the eye needs it, CA ODT (MR11 / MR17) is a gateware change; it lowers
   the level (240 ohm: 0.35 V).
5. **Read high level** of the real LPDDR5 into 150 ohm (proxies 0.386 V, JEDEC bound 0.362-0.431 V) and its
   dependence on MR17.
6. **MIPI / SLVS receiver with one static leg**: 200 mV common-mode step per edge, input offset, FPGA package
   P-to-N coupling. Sweep VREF_RD against bit errors at 100 Mb/s.
7. **Termination really off** with `DIFF_TERM = FALSE`: under 1 mV across a 39 ohm N resistor with P at 0 V.
8. **WCK1 static** while WCK0 toggles, and DQ8-15 left open, on a x16 die: JEDEC is silent; open DQ of an unused byte is what UG583 draws for
   LPDDR4 (section 5). Watch the VDDQ / VDD2H current and read stability; the pads of the eight balls are not reachable after assembly.
9. **Idle channel-B die** with VDD2L = 1.05 V and MR13 OP[7] still 0, and its ZQ calibration on the shared ZQ ball.
10. **Host 1.2 V at the fingers under load** (>= 1.12 V for 0.52 A) and the host ramp times (PMIC firmware
    values are not in the repository). Fallback: trim VCC1V2 over PMBus.
11. **TPS74801 dropout at 2.25 V bias headroom** is read from a typical curve, not a guaranteed figure; at the worst corner (host rail 1.14 V at
    the fingers, full load) the regulator is in dropout and delivers about 1.057 V without supply rejection (section 4).
12. **VDD2H current** during back-to-back ACT / PRE at 800 MHz is not covered by any IDD pattern (sized by IDD4R).
13. **Power-off order** when 3.3 V falls before 1.2 V, or when 1.2 V collapses faster than 1 ms.
14. **Finished thickness** with the fab's actual plating, and fit in the LOTES socket under the ZCU104 shield cage
    (no public drawing of either).
15. **Layout feasibility is checked by script only as far as the model goes**: part courtyards and pads, field
    vias, finger vias, the In3 command traces and the CK pair, and every wire of the data side, each as an explicit
    centre line. `scripts/pcb/rehearse.py` draws all of them on a copy of the board and runs the KiCad DRC; it decides
    per centre line (net, layer, both ends) what is already routed and prints what it drew and skipped. The command bus
    is real copper since stage s20 (section 13), the data side since stage s30 (section 14).
    The power nets, the slow nets and the power pours under U1 are real copper since stage s50 (section 15), measured by gate 9.
16. **Capacitor DC-bias figures** are typical values; the file `references/parts/Samsung_CL05A475MP5NRNC_dcbias.json`
    is an error page, not data.
17. **Write settling** with the read branch (up to 24.2 mm of In3 and 9.4 mm of finger track, directly on the node) and the 73 mm host read line on the node (tau 2.6 ns): decides
    the maximum write rate, not function at 100 Mb/s.
18. **Bend model of the command bus** (section 13): HFSS of one ideal trace between two solid planes, per feature.
    Not in it: the perforated planes under U1 (CA / CS only, the CK corridor is solid), the neighbouring traces of
    the fan (0.37 mm or more away), the 0.4 mm jog next to a via barrel, the two close vertices of the CK_P hook
    (taken as two separate vertices of a 0.10 mm trace: 0.19 ps more than CK_N), loss, etch tolerance of the 0.15 mm
    radius. Each is estimated at 0.1-0.3 ps. The match of the ten nets is therefore good to a few tenths of a
    picosecond on the card, not to the 0.01 ps of the table, and the +/-5 ps of the host figures dominates in any
    case. A bench check is possible: TDR of CA2 against CA0 at the fingers of a bare card.
19. **Unused inner pads removed** on the U1 via-in-pads (B3 / C4 keep a 0.35 mm pad on In3), the command fence vias, the
    node vias of the data side (the eight DQ ones keep their In3 pad, where the read branch starts; the ten command node vias keep every pad),
    the shunt GND vias, the read, return, bus, finger and power-stage vias: standard practice, but confirm with the fab that the via-in-pad process does not require them.
    The 0.35 mm pad on a 0.15 mm hole is the board's minimum annular ring (0.10 mm).
20. **Write deskew rests on the delay model** (section 14). The write group is closed in the FPGA with delays computed
    from: the F.Cu lines at 6.01 ps/mm, the 0.09 mm necks at 5.94 ps/mm between BGA lands (0.6-5.3 mm per net, the lands
    load them: perhaps 1 ps between the shortest and the longest), the node-via barrel at 7.0 ps/mm on the three
    back-finger nets (8.1 ps; a via through four plane antipads may be 1-2 ps off), the bends of the F.Cu lines by the
    stripline model of section 13 (0.46-1.01 ps per net, not simulated for microstrip) and the host figures (+/-5 ps).
    Together a few ps on a table whose entries reach 53.5 ps: it is a starting point for write training, not a calibration.
    The user has accepted the deskew (section 7).
21. **Pair skews are costed at the odd-mode speed over the whole outer-layer leg** (sections 2, 3). The stretches that the board gate
    counts as uncoupled run at the single-line speed (about 0.3 ps/mm slower). WCK: 1.98 / 2.26 mm per leg, so the -0.54 ps
    are -0.6 ps in truth. RDQS: its F.Cu legs part only at the via pair and at the load links (equal on both legs), the bump is on In3, where a
    single line and the pair have the same speed: no correction. The 1.0 ps of host skew in the WCK figure is known to
    +/-5 ps like every host figure. +/-1 ps is the claim for both pairs.
22. **The V1P2 island on In2** (section 4). No issue for the signals: the back-finger write stubs of fingers 144 / 150 / 156
    have GND under them all the way (gate 7); the island is on In2, south of every In3 trace.
    What is left to confirm is the power side: the bridge north of the key slot carries the current of the twelve west
    fingers (up to 0.49 A if the contacts share equally) through 0.42 mm of 18 um copper beside one via antipad for 0.6 mm
    of length (about 1.4 mohm, 0.3 mW). The 1.2 V rail is decoupled at the foot of the strip (C512, C502), on the arm (C513, C501) and at U4 (C508).
23. **Delay model of the read group** (section 14). The eight DQ are not matched on the card: they end -56.8 .. +56.8 ps
    from the centre of the group by plan (read end without crossings) and are closed per bit with IDELAY (section 7); gate 7
    holds every net within +/-1 ps of its own planned total. What the model takes as given: two barrels
    per path at 7.0 ps/mm (node via F.Cu to In3 0.64 mm; read via 0.51 mm to B.Cu on seven nets, 0.64 mm to F.Cu on DQ4),
    the open via stubs (0.51-0.64 mm), the bends (0.96-1.50 ps per net: the stripline formula of section 13 applied to
    every mitred vertex on every layer, an estimate on the outer layers), and the +/-5 ps of the host figures. With these
    a bit may sit 15 ps from its table value on the real card; that residue is what read training removes.
    The outer-layer figures (6.01 / 6.07 ps/mm for 0.15 / 0.20 mm, 5.69 for the pair) are for the design values of
    section 6 (finished outer copper 0.040 mm under 0.020 mm of mask); with the stackup-table nominals (0.035 /
    0.010 mm) they are 1.5 % lower on single tracks and 3.5 % lower on the pair. RDQS has 6.0 mm of F.Cu pair and 41 mm of
    stripline, each DQ 28-34 mm of outer-layer track: over the three copper / mask cases the strobe moves by 1.9 ps
    against the centre of the data (30.3 .. 32.2 ps after it) and the group stays +/-56.8 .. 57.1 ps. Nothing is matched to
    these figures; they are start values of the deskew table.
    The fab drawing has to name the finished outer copper (0.040 mm) and the mask over copper (0.020 mm) all the same.
24. **RDQS skew bump and via pair** (section 3). The bump (two legs 0.553 mm deep) is on In3, the layer the bend model
    was calibrated on; the 21 / 13 mitred vertices of the two legs are corrected with that model also where they lie on
    F.Cu microstrip (1.97 / 1.21 ps in all). The In3 - F.Cu change is modelled as 0.64 mm of barrel at 7.0 ps/mm with a GND
    return via 0.58 mm from each leg; the two In3 legs pass between the barrels of those return vias at 0.225 mm (hole to copper 0.20). +/-1 ps P
    to N is the claim, not the 0.00 ps of the table.
25. **RDQS loads on side links.** Each 150 ohm load hangs on a 0.40 mm link off its leg of the coupled pair, 2.4 mm before
    the finger top: a stub of about 2.5 ps, and the western link ends 0.425 mm from the read track of finger 215 (rule area
    `rdqs_termination`; the pair itself keeps 0.825 mm; nothing stands east of the drop). The GND of each load is 1.4 mm of 0.25 mm track down the axis of a
    VSS finger pair into that pair's finger via, 0.225 mm beside its leg and a mirror image of the other: about 1 nH in
    series with 150 ohm. Strobe-to-data coupling over that length is far below the read margin at 100 Mb/s.
26. **Crosstalk inside the In3 read bundle.** 0.55 mm pitch, 0.40 mm of gap = 3.6 h to In2, but the other plane
    (In4) is 0.36 mm away, so the coupling is that of a trace over one near plane: the field calculation of the
    verification gave a backward coefficient of 2.8 % per neighbour at 0.50 mm pitch and 1-2 % at 0.55-0.65 mm, against
    0.4 % in the command bundle. It is backward only (forward crosstalk is nil in stripline): it travels to the DRAM end,
    meets the node there (150 ohm in parallel with the line and the driver, about 30 ohm against 45.6 ohm: a fifth comes
    back, inverted), so the FPGA end sees a few mV, and at 100 Mb/s all of it has settled long before the sample.
    Not simulated in the time domain.
27. **In3 lines beside foreign vias.** An In3 read line passes the In3 pad of the read via of another bit at 0.275 mm
    (DQ0 beside the DQ3 via; the upstream figure) and the hole of a return via at 0.225 mm; the antipads of those vias in
    In2 / In4 dent the reference of the line for about 0.6 mm of its length. Likewise on F.Cu: the DQ4 tooth and the 0.20 mm
    link of the WCK_P column each pass the pad of the return via at (36.70, 6.30) at 0.275 mm. Inside U1 the RDQS In3 legs run
    along the antipads of three via-in-pads with 0.205 mm from hole wall to copper (rule 0.20): the fab has to be told that
    the hole-to-copper figure is used at its limit there.
28. **The hairpin of the comb.** The In3 read branch runs back north 0.40 mm beside its own F.Cu tooth, In1 and In2 between
    the two: 3.15 mm on DQ4 falling to nothing on DQ6 / DQ7, 10.6 mm in all. No coupling through two solid planes is
    expected, and it would be a net onto itself; it is listed because the user asked about wires that go up and come
    back, and this is where the comb does it (section 14).
29. **Open FPGA end without series resistors** (section 3): the line model is lossless and linear; the real edge is
    slower and the ringing smaller. Bench: look at a read finger with an active probe at 100 Mb/s for the three
    drive-strength settings before deciding that nothing needs to be changed in MR3 / MR17.
30. **RDQS beside CA0 inside U1** (section 14, `design/ref/hfss_ca0_data_corner.json`). The HFSS model is 4.4 mm of the CA0
    channel with the neighbouring barrels as plain cylinders; signal barrels end 0.05 mm short of the planes instead of
    carrying real antipads, and the model is lossless. The coupled pulse (0.95 % of the RDQS swing, 3.6 mV) is for a 42 ps
    edge, i.e. an assumed 9 V/ns at the ball; the JEDEC output slew of the part is not in the repository. The margin to
    the 1 % limit is 5 % of the limit, and the limit is self-set: if the bench shows a faster edge, the remedy is the MR3
    drive strength, not copper. The priority bus depends on one fabrication detail here: the 0.35 mm In3 pad of the C4 / B3
    via-in-pads, an annular ring of 0.10 mm on a 0.15 mm drill.
31. **Vivado pin check with the final pin set: done in Vivado 2026.1, legality only** (`design/gateware/vivado_pincheck/`, summaries with every
    message in `results/`). v2026.1, xczu7ev-ffvc1156-2-e, synthesis through `write_bitstream` on the real pins: command bank 65 T0 as native
    `TX_BITSLICE` x10 with a PLL (1.6 Gb/s, CK 800 MHz), write bank 64 `OSERDESE3` -> `ODELAYE3` -> `OBUFT` per DQ and WCK inverted, read bank 66
    eight `IBUFDS_DPHY` (MIPI_DPHY_DCI) and RDQS -> `IDELAYE3` -> `ISERDESE3`, RESET_n LVCMOS12 on AP11. With `IBUFDS_DPHY` and with the SLVS_400_18
    fall-back: 0 errors, 0 critical warnings, bitstream written, setup slack 0.297 ns; no `INTERNAL_VREF`, no `DCI_CASCADE`, no card pin on a VRP
    pin. What it shows: the command bus at 1.6 Gb/s needs native mode (component mode
    violates the `OSERDESE3` minimum period at 800 MHz); RDQS cannot clock through an IDELAYE3 or a BUFG without `CLOCK_DEDICATED_ROUTE FALSE` (QBC
    pin); two tool differences (IDELAYCTRL `SIM_DEVICE`, the `IBUFDS_DPHY` instance attribute), section 7. It says nothing about levels or timing
    on the real card.
32. **U3 and U4 on the back** (section 15). The two small regulators and their parts cannot be probed or reworked while the card sits in the
    socket; the three rails have test pads on the front (TP1 .. TP3). Height: 1.1 mm (U3) and 0.9 mm (U4), inside the 1.2 mm of the back side.
33. **VDDQ runs 21.7 mm on B.Cu** from U4 to U1 (1.0 mm lane, 10.7 mohm): 2.4 mV at 0.225 A, 4.8 mV to the farthest balls with the rails, against a
    limit of 5 mV that is self-set; U4 at its lowest (0.488 V) minus 4.8 mV is 13 mV above the JEDEC minimum. A 1.5 mm lane (4.0 mV) buys nothing the part needs and would be the widest object on the back. The regulator senses at its own output; 40 uF at the
    balls carry the transients.
34. **No coplanar ground on the outer layers** (section 15.4): the outer-layer lines are plain microstrips over In1 / In4, which is what the field solver
    and the delay model compute. One pour edge runs along a high-speed track: the VDDQ enclave under U1, 0.30 mm from WCK_N on one side over 2.4 mm
    (-0.11 % impedance, -0.008 ps on the pair, `design/ref/coplanar_pour.json`). Return current of an outer-layer line that changes layer has the GND
    vias of the earlier stages beside its via (fence vias, return vias); nothing else stitches the planes outside U1 and the finger row, where 101
    GND via-in-pads and 47 GND finger vias tie In1 / In2 / In4 together. Not simulated in 3-D.
35. **Vias in the exposed pads** of U2, U4 and U5 (eight, 0.45 / 0.20 mm) are filled and capped like the via-in-pads of U1 (fab note 6): they take no solder, and the one that also touches the GND land of C509 on the other face is safe. They share the 0.20 mm drill with plain vias: the fab identifies them by the list in note 6 and by the via attributes of the board file.
36. **Plan-view meetings of supply copper with the command bus inside U1** (section 15): the F.Cu straps A9 - B9 - C9 and E9 - F9 (VDD2H, 4.2 mm) and
    D9 - D10 (GND) along the CK pair in its corridor with In1 / In2 solid between (stage s20, the balls of column 9 without via); the F.Cu strap D4 - E5 (VDDQ) over CA0 with In1 / In2 between, and
    0.28 mm of the B.Cu link F6 - K6 (VDD2H) beside the last jog of CA1 with In4 between; the neck of that link passes the B.Cu end of the CA1 via-in-pad at
    0.14 mm (section 15.4: the one stub end of the ten with a supply track beside it). The VDDQ / VDD2H pours under U1 lie under the command
    traces with In4 between; In3 refers to In2 (0.11 mm) and In4 (0.36 mm), both unbroken there except for the antipads of the via field.
37. **Socket model.** The HFSS model of the socket launch of the command bus is built from the drawings of the socket; a STEP file of the Foxconn
    DDR4 socket that the user mentioned was not found on this machine. The result of that run (command-bus launch and eye) is reported separately
    and is not part of this file.

## 9. Files, and how to rebuild

**Rebuild.** One command builds everything from the spec, also on a clean tree (every generated file deleted):

```
scripts/pcb/build.sh                  # spec -> schematic -> board stages -> gates -> schematic PDF -> gateware files   (about 30 s)
scripts/pcb/build.sh --render <dir>   # the same, plus per-layer PNG renders and zoom crops in <dir>
scripts/pcb/build.sh --rehearse <dir> # the same, plus the rehearsal of the floorplan on a copy with eleven negative tests of the rules
scripts/pcb/build.sh --fab            # the same, plus the fabrication package fab/ (python3 scripts/make_fab.py) and the layer images doc/img/
```

The order inside is fixed: the board stage s00 reads the schematic's netlist export, and the schematic check reads the project file that s00
writes. A rebuild of an unchanged tree reproduces every generated file byte for byte (the PDF is replaced only when its text changes: KiCad's PDF
is not byte-reproducible; the fabrication files carry a creation time and are therefore not part of the default build). To change the design,
change `scripts/build_netlist.py`, `scripts/data_side.py` or `scripts/power_side.py` and rebuild; a single board stage can be re-run by hand
(it removes its own earlier output first). Needs KiCad 10 (kicad-cli, pcbnew for python3), numpy, scipy, shapely, pycairo and python3-gi with
librsvg; the stock KiCad footprint library is read from `/usr/share/kicad/footprints`.

**Fabrication package.** `scripts/make_fab.py` exports `fab/`: Gerber X2 for the six copper layers, masks, pastes, legends, profile and drawing
layers, Excellon drill files (plated and non-plated) with a drill map, the filled-and-capped vias as their own drill file and list, the IPC-D-356
netlist, pick-and-place per face, the BOM, a fabrication drawing (outline with dimensions, drill map and table, stackup, impedance, via treatment,
the notes), assembly drawings, the schematic and a STEP model. `scripts/check_fab.py` reads the files back with its own Gerber / Excellon / IPC-D-356
parsers and compares them with the board as the pcbnew API reports it (copper layers rasterised from both and compared to one pixel; drill hits,
mask openings, paste, placement rows and BOM against the board items). `fab/README.md` lists every file, the order parameters, the coordinate
conventions, the verification result and the SHA-256 of the board file the package was made from.

| File | Content |
|---|---|
| `design/netlist.json` | the design: parts and pads, 260 fingers, groups with per-net delay targets, stackup, rules (with the reason of every rule change), floorplan (regions, placements, vias, every wire of the data side as a centre line, crossing budget), level analysis, BOM |
| `design/impedance_results.json` | field-solver tables, including the outer-layer copper / mask corners |
| `design/ref/` | host pin map, host + package delays, U1 ball reference (inputs of the checker); HFSS evidence: the tuning features of the command bus and `hfss_ca0_data_corner.json` (CA0 beside the RDQS escape) |
| `scripts/build_netlist.py` | writes the netlist |
| `scripts/check_netlist.py` | verifies it (connectivity, bank plan, levels, power, geometry of the fields and pairs, regions, this file's key numbers) |
| `scripts/impedance.py` | 2-D field solver |
| `scripts/tuning.py` | tuning geometry (parallel shift, bumps) and the electrical length of bends (`rules.bend_model`); used by the generator, by `s20_cmd.py` and by gate 6 |
| `scripts/read_linesim.py` | line simulation of one read DQ (open FPGA end with and without a series resistor); used by the generator (`analysis.read.open_end`) and re-run by the checker |
| `scripts/data_side.py` | the data side as one plan: pin map, comb geometry, read levels (search over the finger assignment), WCK and RDQS pairs, reference networks, return vias, delays with the bend model; used by the generator, the result is `floorplan.data` |
| `scripts/power_side.py` | power and slow nets as one plan: placements of the regulators, the 1.2 V strip and its sizing, every supply and slow wire as a centre line, vias, the pours under U1, RESET_n, the expected crossings and the DC budget; the result is `floorplan.power.build` |
| `scripts/coplanar.py`, `design/ref/coplanar_pour.json` | copper beside an outer-layer track with the field solver of `scripts/impedance.py`: the one pour edge that is left (VDDQ enclave beside WCK_N), and, for the record, what the removed ground floods did |
| `design/gateware_deskew.json` | per-bit output delays that close the write group, per-bit input delays that close the read group, the offset of the strobe, pin map and pair polarities; written by gate 7 of `check_pcb.py` from the delays measured on the board |
| `scripts/pcb/build.sh` | the one command that builds everything (above): spec (`build_netlist.py`, `check_netlist.py`) -> schematic (`gen_schematic.py`) -> board stages -> gates (`check_netlist.py`, `check_schematic.py --pdf doc/lpddr5-testbed.pdf`, `check_pcb.py`) -> gateware hand-off (`gen_gateware.py`, then `--check`).; with `--fab` also `make_fab.py` |
| `scripts/pcb/` | the board stages and gates: `s00_board.py` (board, rules, footprints incl. test pads and fiducials, fabrication notes, drill / place origin), `s10_escape.py` (U1 fan-out, field vias, finger vias), `s20_cmd.py` (command bus: finger stubs, divider links, In3 bundle, CK pair, tuning features), `s30_data.py` (the data side: every centre line of `floorplan.data.wires`), `s50_power.py` (power and slow nets, the power pours under U1: `floorplan.power`; no ground pour, no stitching), `s90_zones.py` (planes), then `check_pcb.py` (gates 1-5 board, 6 command bus, 7 data side, 8 crossing budget, 9 power: connectivity, DC budget with the In2 island, layer-hop rule, power pours, command stub ends, GND pad audit, no open via at a solder land, outer-layer copper coverage; 6 and 7 measure every net on the board; gate 2 is KiCad's DRC with all severities and its own schematic parity, and fails if the schematic is missing); `crossings.py` (plan-view crossings, parallel overlap and wires through resistor groups, on the board or on a plan), `rehearse.py` (draws every planned centre line of the floorplan that is not routed yet on a copy of the board, runs the DRC and eleven negative tests of the spacing and pair rules), `render.py`, `common.py` |
| `scripts/gen_gateware.py`, `design/gateware/` | the gateware hand-off (section 7): XDC, Verilog header, pin map, bring-up notes, the Vivado pin check and its results; written from `design/netlist.json` and `design/gateware_deskew.json` at every build, `--check` fails if a file on disk is stale |
| `scripts/gen_footprints.py`, `scripts/check_footprints.py` | project footprints and their checks |
| `lib/lpddr5_testbed.pretty/` | `DDR4_SODIMM_260_Edge`, `BGA-315_12.4x15mm_P0.8x0.7mm`, `MLF-6-1EP_2x2mm_P0.65mm_EP0.8x1.4mm`, `Fiducial_1mm_Mask2mm`. Every other footprint (0201 / 0402 / 0603, WSON-8, VSON-10, TSOT-23-5, test pad) is the stock KiCad footprint of that name, read from the installed library (`/usr/share/kicad/footprints`) |
| `scripts/gen_schematic.py` (with `scripts/kisch.py`) | writes the schematic from the netlist: the root sheet and six sub-sheets, `lib/lpddr5_testbed.kicad_sym`, `sym-lib-table`, `design/erc_settings.json` |
| `scripts/check_schematic.py` | schematic gate: ERC with the settings of `design/erc_settings.json`, pin-by-pin parity of KiCad's netlist export with the netlist, file checks; with `--pdf` (as `build.sh` calls it) it refreshes `doc/lpddr5-testbed.pdf` when the text of a fresh export differs |
| `lib/lpddr5_testbed.kicad_sym` | every symbol of the design, so the project needs no stock symbol library: J1, U1, U4, U5 and the rail power symbols are drawn by the generator; R, C, TestPoint, Fiducial, TPS74801DRC, TLV70018_SOT23-5, GND and PWR_FLAG are copies of the KiCad library symbols |
| `design/erc_settings.json` | the `erc` section for `lpddr5-testbed.kicad_pro`: rule severities and the two exclusions for the DMI balls strapped to VSS (section 5) |
| `references/parts/TI_TLV700.*`, `Microchip_MIC47050.*`, `TI_TPS7A11.*` | data sheets added for this design (TPS7A11: the rejected VDDQ option) |
| `scripts/make_fab.py`, `scripts/check_fab.py`, `fab/` | the fabrication and assembly package and its verification (above); `fab/README.md` is the index |
| `doc/lpddr5-testbed.pdf`, `doc/img/` | the schematic as PDF (kept current by `build.sh`) and PNG images of the board as built: the six copper layers, all copper layers overlaid, top and bottom assembly view (`scripts/pcb/render.py doc/img --only layer_,asm_,all_copper`, run by `build.sh --fab`) |
| `design/README_PROPOSED.md` | proposed text for the repository's `README.md`, which still describes an earlier plan |

## 10. The user's rules and how each is met

The first table takes the brief (`PROMPT.md`) line by line, the second the remarks and decisions of the user during the build, in the order in
which they came. Quotes are verbatim. "Met" is said only where a gate measures it; where a rule is met in part, the part that is not is named.

### 10.1 The brief

| Rule (verbatim) | How it is met | Where |
|---|---|---|
| "We want to achieve the highest level of signal integrity for the command bus, NOT reads and writes." | The command bus has the fastest bank, its own layer between two ground planes with a reserved zone, one byte group, a straight 11 mm run, source termination within 2 % of the line, and is matched from the FPGA die pad to the ball with a bend model calibrated in HFSS: 641.77 ps on all ten nets. Every conflict between the buses was decided for it (the VDD2H neck, the unused-byte escapes, the In3 pad of ball C4). It is still crossed 15 times in plan view, all inside or at U1, by wires on other layers | 1, 12, 13 |
| "Writes and commands are sent with resisitive divider level shifters, while reads are directly wired to the zcu104 with a pseudo-differential scheme where N pins are wired to a DAC maintaining ~half the swing voltage." | 40 divider resistors (0201) at the fingers; DQx is one net from the ball to the read finger with no part in it; the N pin of each read pair sits on VREF_RD through 39 ohm + 100 nF; VREF_RD is 0.187 V without any I2C traffic and 0.136 .. 0.631 V with the DAC53401 (read high about 0.39 V) | 1, 2, 3 |
| "Choose the assignment of banks to read, write, and command duty by minimizing the routing delay for the command bus." | Commands on bank 65 (host + package median 527.7 ps against 610.4 and 601.6 ps for banks 64 and 66), byte group T0 directly under U1; writes on bank 64, reads on bank 66 (the user's bank plan) | 1 |
| "Keep the same layer organization as the antmicro board. Make it as a minimal as possible overall. Keep the same routing conventions, the same stackup." | Same six layers with the same roles and materials; the two cores are 0.36 instead of 0.40 mm for the DDR4 finger thickness (allowed by the user: "only change thickness if necessary to be compatible with DDR4 sodimm standard"). In1 / In2 / In4 carry no track; no ground flood on the outer layers. What follows the upstream board and what deviates: section 11 | 6, 11 |
| "Do not overlap wires in high speed groups." | Read as a same-layer rule, as on the upstream board: no two wires of a group cross or overlap on one layer (gates 6, 7: 0). Through the board they do cross, at 45 or 90 degrees, 52 times among the data wires, which is the lower bound of this topology; parallel overlap of two different data wires on two layers is 1.06 mm in all. **This reading is ours and needs the user's confirmation** | 12 |
| "Keep clean routing of high speed wires like in the antmicro design, with parallel clean fanout bundles and 90-135 degree bends (no sharp corners)." | Every segment of the board is at 0 / 45 / 90 / 135 degrees (932 segments, 0 acute vertices); the 40 arcs are the rounded corners of the ten command tuning bumps; the DQ comb is one parallel fan at 0.55 mm pitch; one 90-degree vertex exists (the tip of the CK_P hook into ball H9) | 13, 14 |
| "Focus on only a single channel, a single byte, and a single rank. Choose which ones to maximize signal integrity and clean routing." | Channel A, byte 0, rank 0: byte 0 of channel A lies beside the channel-A command balls on the finger side of U1, so both buses leave U1 toward the fingers; CS1 is tied low; channel B and the second byte are tied off or open | 5 |
| "Be as minimal and simple as possible. For example, for the power supply design, make sure everything has enough current but there's no need to implement some really complicated power-on chain or string together many ICs when a simpler, more dumb design will almost certainly work." | Three linear regulators, no sequencer, no supervisor: VDD1 is enabled by the 1.2 V rail, VDD2H by VDD1, VDDQ by the power-good of VDD2H, which gives the JEDEC order by wiring alone. Five ICs in all (U1, three LDOs, the DAC) | 4 |
| "Remove any Antmicro or OpenHardware branding in the board files." | None in the board, project, rules, schematic sheets, symbol library, footprints and PDF; the legend reads "LPDDR5-CMD-TESTBED ZCU104 R0.2". `.ci.yml`, `.gitmodules` and `README.md` are the user's files and were not edited (Appendix B) | 9 |
| "Use kicad 10." | Board, schematic and libraries are KiCad 10 files (kicad-cli 10.0.6 reports nothing to upgrade); the project footprints are written in the KiCad 8 syntax, which KiCad 10 reads unchanged | 9 |
| "Always assume that any gateware change on the host FPGA (zcu104) is trivial and can be done. It's always easier to do that than change the physical PCB design." | Used four times: both pairs WCK and RDQS are inverted on their host pairs, the DQ bits are permuted on the write and on the read pins, both data groups are deskewed per bit, and the receiver settings replace the removed read taps | 7 |
| "Vivado is available as version 2026." | The pin map was run through Vivado 2026.1 from synthesis to bitstream on the real pins: 0 errors, 0 critical warnings (a legality check, not a timing or level check) | 8 item 31 |
| "In the end, produce a design that's almost ready for fabrication. No need to target a specific fab vendor for now." | Every net routed, DRC clean, fabrication notes on the board, `fab/` with Gerber, drill, netlist, placement, BOM and drawings, read back and compared with the board. What is still open before an order: section 8.1 | 8, 9 |
| "You are only allowed to use git commands that do not modify the state - no adding, commiting, etc." | No state-changing git command was run; the work tree holds all changes uncommitted | |
| "When routing read/write byte groups, and the command bundles, delay match the wires within one group." | Command bundle: met on the card (0.0 ps as routed, rule +/-2 ps). Pairs: met P to N. **Write and read DQ groups: not met on the card** (+/-26.7 and +/-56.8 ps), by the later decisions of the user (10.2, rows 3, 8, 9); closed per bit in the FPGA | 1, 14.2 |
| "When routing high speed wires, follow Antmicro's convention: groups of high speed wires in a layer without much else, sandwiched by ground layers. Always route differential pairs tightly together." | Commands, read branches and RDQS on In3 between In2 and In4 (GND on both under 100 % of the copper outside U1); the DQ comb alone on F.Cu over In1; WCK alone on B.Cu over In4. Pairs are 0.10 / 0.10 mm, coupled except where the legs must part (CK 81 % of its In3 length; uncoupled per leg WCK 2.0 / 2.3 mm, RDQS 3.35 / 2.84 mm) | 6, 13, 14 |
| "Keep the resistive dividers near the card fingers." | Every land of the 40 divider resistors lies within 7.6 mm of the card edge (command lands within 7.2 mm, the sixteen DQ lands within 5.55 mm), directly above the 4.00 mm component line | 1, 2 |

### 10.2 Remarks and decisions during the build

| # | The user (verbatim) | What was done | Where |
|---|---|---|---|
| 1 | Bank plan: commands on bank 65, writes on bank 64, reads on bank 66; dividers near the connector; DAC reference; 100 Mb/s read / write target; "only change thickness if necessary to be compatible with DDR4 sodimm standard" | Built so. The two FR4 cores were thinned from 0.40 to 0.36 mm: 1.192 mm at the bare fingers, 1.20 mm with hard gold (the upstream stack is 1.272 mm, 28 um from the upper limit of 1.20 +/-0.10) | 1-3, 6 |
| 2 | "Show me images here of each layer as you make routing progress." | `scripts/pcb/render.py` renders every layer and 29 zoom crops at every stage; the final set is in `doc/img/` | 9 |
| 3 | "Match rdqs." - later, when on-card matching of the DQ groups turned out to cost crossings: "The write group's spread is acceptable" and "Same with read group" | RDQS_P to RDQS_N 0.00 ps (rule +/-1 ps). With the read group unmatched (accepted by the user) there is no common total to match the strobe to: it arrives 923.12 ps after the ball, +30.29 ps from the centre of the group, inside the span of the eight bits. DQ skew is closed by per-bit FPGA delay (`design/gateware_deskew.json`); the command bus stays matched on the card; each differential pair stays matched P to N | 3, 7, 14.2 |
| 4 | "Why do the read wires curve up and then back down?" | No stem and no serpentine: no wire runs past its end and comes back over itself (gate 7: 0 mm of self-overlap). **Not fully met:** with the T at the divider (row 8) every read branch comes down its tooth to y = 6.00, turns north again beside the tooth (0 .. 3.15 mm, then 2.20 mm up the ramp) and its finger track comes down once more. The ramp is as low as a read via can stand and no line is raised above its place | 14.3 |
| 5 | "Are you sure you need the read taps? Is it that bad if it overshoots a little?" | The ten 22 ohm read resistors are not fitted and not on the board. The open FPGA end overshoots for 1-2 ns per edge (0.549 V / -0.131 V in the strong corner), inside the recommended pin range; at 100 Mb/s the sample falls 5 ns or more after the edge | 3 |
| 6 | "Minimize crossings between high speed wires and other wires in other planes too. Also, don't route wires through perpendicular to the resistor groups" | Crossings are counted by `scripts/pcb/crossings.py` and locked by gate 8: 94 on the whole board, each class at the lower bound of the topology or explained one by one. No wire runs through a resistor group (0); every wire enters its divider along the column axis | 12 |
| 7 | "With the removed read taps, can you roll back any of the compromises you made before from the original antmicro design such as putting wires closer together" | Yes: bundle pitch 0.55 mm (gap 0.40) on F.Cu and In3, WCK and RDQS 0.80 mm from other tracks, WCK skew limit 0.50 mm, uncoupled limits 4.0 / 4.5 mm, one via per column, plain node vias 0.25 mm from the lands | 11 |
| 8 | "The read/write T and divider resistors should all be near each other for each DQ line" and, offered two ways: "Bring the T down to the divider" | For every DQ the line from the ball, the read branch and both resistors meet on one via (the node via at y = 6.00); the two resistors sit back to back on the two faces, 0.68 mm of copper from the T (1.84 mm through the barrel for the one on the far face); gate 7 holds 2.0 mm. Price: the write group can not be matched on the card and the read wires go down, up and down (row 4) | 2, 14.1, 14.3 |
| 9 | "Can you reorder the blue wires so there are less crossing signs across layers? It's ok to reorder dq wires in the gateware for example" | The read fingers are taken in the order of the In3 bundle (the read end without crossings): no read finger track crosses an In3 line (20 -> 0) and RDQS comes down east of every read finger (8 -> 0). Data x data 52 instead of 80. Price: the read group is +/-56.8 ps on the card, closed by per-bit input delays of 0 .. 113.7 ps | 3, 12, 14.2 |
| 10 | "Why does one of the wires in FCu go to the blue layer for a bit and back - that's dumb" | That wire was RESET_n. Rule for every slow and supply net (gate 9): no wire changes layer and comes back, a via stands only at the end of a wire, In3 carries high-speed groups only. RESET_n is 2.1 mm of F.Cu, ONE via, B.Cu to finger 232. Stated exceptions: the eight read branches (node via - In3 - read via, the topology of row 8), the VDDQ strap D4 - E5 and the VREF_RD feed | 15.1, 15.3, 15.6 |
| 11 | "What is the giant pour you added" | The 1.2 V feed is a 1.5 mm strip on F.Cu, sized by calculation (5.3 mohm, 4.1 mV, +0.4 K at 0.77 A; a 5 mV budget needs 1.21 mm) | 4 |
| 12 | "Why do F and B seem to have copper across the whole board in their images" | No ground flood on the outer layers, as on the Antmicro board: F.Cu carries no zone at all, B.Cu only the VDDQ / VDD2H pours under U1 (2.8 % of the board area); every GND pad has a via of its own; the fab note forbids thieving | 15.4 |
| 13 | Why the N fingers carry 39 ohm + 100 nF near the fingers instead of going straight to the DAC; where the 1.05 V pour is; why only the command bundle has serpentines | Answered, design unchanged: damping of the open-ended N lines and a local reference (section 3); an island on In4, VDD2L tied to VDD2H in JEDEC single-rail mode (section 4); the command bundle is the only group matched on the card (section 3) | 3, 4 |
| 13b | "Why is there a diff pair that starts in the blue layer and transitions to the red layer? also why is there a lone diff pair in the teal layer?" | The first is RDQS. It **stays on In3 from its balls to a via pair at (57.875 / 58.475, 8.50), just above fingers 219 / 221, and drops 5.8 mm on F.Cu**, like the eight read DQ lines (5.5 .. 9.4 mm); same plan route, so no crossing was added. It must change layer once: its balls can only be left on In3 (F.Cu is taken by the DQ escapes, B.Cu by WCK) and its fingers are on an outer layer. The second is WCK, on B.Cu as on the Antmicro board. **WCK cannot share a layer with RDQS:** the RDQS balls B3 / C4 lie on one side of the WCK diagonal (D5 / E4 toward B1) and the RDQS fingers (x = 58) on the other side of the WCK columns (x = 35), so on one layer the two pairs would cross; they cross once, in plan view, at (25.8, 13.0) inside U1, on two layers. F.Cu is taken by the DQ necks that both pairs have to cross. So one pair has In3, the other B.Cu, and B.Cu has nothing else to carry there | 3, 14.4 |
| 13c | "also why are there pulldown elements in the bottom left of U1" | They were two 4 x 10 k arrays on the eight data balls of the unused byte. Removed with their eight escapes: JEDEC has no rule for the unused byte of a x16 die, AMD UG583 leaves DQ[15:8] of such a device unconnected, Micron TN-40-40 says unused DQ can be floated. The command bundle is crossed 15 times instead of 30. R404 / R405 (WCK1_t low / WCK1_c high) stay: a floating input-only clock pair is not the same as a floating data bus | 5 |
| - | "there's also a single line that goes from red quickly to teal in the right side of the chip. why?" (recorded in the final fix pass) | RESET_n again: one via is the minimum. All on F.Cu is closed by the 1.2 V strip, its arm and the DQ comb; all on B.Cu from a via in ball H1 would have to cross the VDDQ and VDD1 rails on their own layer | 15.3 |
| - | Where is the 1.8 V pour (recorded in the final fix pass) | There is none: VDD1 (26 mA) is plain track, as on the upstream board; it changes layer once, at ball W1 | 15.2 |
| 14 | "Somewhere on this machine is step file of a ddr4 socket from Foxconn. Find it, copy it to references/, and use it in the HFSS model of the socket" | **Open:** the file was not found in any readable location; the user has been asked for the path. The socket model of the command-bus simulation is built from the drawings | 8 item 37 |

## 11. Comparison with the Antmicro LPDDR5 testbed

"Antmicro" or "upstream" is the Antmicro LPDDR5 testbed this repository started from (DDR5 SO-DIMM form, two channels, for their Kintex-7
tester). Its figures were measured on the pristine board file; it has no spacing, coupling or skew rules of its own.

### 11.1 What follows it

| Convention | Antmicro | This card |
|---|---|---|
| Stackup | 6 layers, 0.11 mm PR2116 prepregs, FR4 cores, er 4.29, copper 0.035 / 0.018 mm, through vias only | the same; only the two cores are 0.36 instead of 0.40 mm |
| Layer roles | F.Cu DQ over solid In1 GND; In3 CA / CS / CK; B.Cu pairs and decoupling; In2 GND + a power island; In4 GND + a power island | the same roles (section 6); In2 and In4 carry no track here |
| Outer layers | no ground flood; local power pours on B.Cu under the BGA | no ground flood; VDDQ / VDD2H pours on B.Cu under U1 only |
| Geometry | single-ended 0.15 mm, pairs 0.10 / 0.10 mm, 0.09 mm necks in the BGA, DQ bundle pitch 0.55 mm, pairs >= 0.87 mm from other signals | the same widths and bundle pitch; pairs >= 0.80 mm by rule (0.825 .. 0.975 mm as built) |
| Angles | 0 / 45 / 90 / 135 degrees only; arcs only as rounded corners of tuning meanders | the same |
| BGA fan-out | via-in-pad 0.40 / 0.15 for inner signals and power, outer DQ rows leave on F.Cu without a via, no dog-bones | the same (via-in-pad on every supply ball and every routed inner signal ball) |
| Dividers | at the connector end, series part and shunt part back to back on the two faces | the same, in 0201 |
| Reads | direct, no part in the path, on a second finger per DQ; reference from an I2C DAC | the same; the DAC is on the card |
| Decoupling | 4.7 uF 0402 on B.Cu under the BGA on the via-in-pad power vias | the same value and place (24 instead of 38: one rail and one channel less) |
| WCK | B.Cu pair from via-in-pads | the same |

### 11.2 What deviates, and why

| Item | Antmicro | This card | Why |
|---|---|---|---|
| Form factor and host | DDR5 SO-DIMM (262 fingers, 69.6 x 40 mm), tester with 1.1 V banks | DDR4 SO-DIMM (260 fingers, 69.6 x 30.0 mm), ZCU104 socket at 1.2 V | the brief |
| Scope | two channels, four bytes | channel A, byte 0, rank 0 | the brief |
| Core thickness | 0.40 mm (1.272 mm at the fingers) | 0.36 mm (1.192 mm, 1.20 with gold) | DDR4 finger thickness 1.20 +/-0.10 mm; allowed by the user |
| Divider values | 100 / 100 ohm on every driven signal | 91 / 68 (CA, CK, WCK), 33 / 100 (CS), 240 / 150 (DQ) | 1.2 V banks instead of 1.1 V and a level limit of 0.47 V at the ball; back impedance equal to the line; the DQ shunt is the read load |
| Divider parts | 4 x 0402 arrays at 0.50 mm pitch, discrete 0402 | discrete 0201 at 0.8 mm pitch | arrays do not fit eight columns between the key and finger 161; one column per ball-field channel on the command side |
| Reads | plain single-ended inputs against VREF | pseudo-differential: DQ on the P pin of a pair, N pin on VREF_RD with 39 ohm + 100 nF | the brief |
| Command bus | F.Cu and In3 mixed, 1-3 vias per net, 107 mm of it at 0.09 mm, matched to 0.8-2.5 mm of length | In3 only at 0.15 mm, exactly two vias per net, one trace per ball-column channel, matched by electrical length (HFSS bend model) to 0.0 ps | the priority bus of the brief |
| RDQS | B.Cu pair with WCK | In3 from the balls to a via pair above its fingers, 5.8 mm of F.Cu | both pairs on B.Cu would fence in the VDD1 ball C1 and would have to cross each other (section 10.2, row 13b) |
| DQ match in the byte | 0.3-0.5 mm (2-3 ps) with serpentines (729 tuning arcs on F.Cu) | write +/-26.7 ps, read +/-56.8 ps on the card, closed per bit in the FPGA; no serpentine | user decisions (T at the divider; fewer crossings; "It's ok to reorder dq wires in the gateware") |
| Read tap | branches off at the resistor node about 9 mm from it, 6 mm hop on In3 | branches off at the divider node via itself (the T), In3 all the way to a via above the read finger | user: "The read/write T and divider resistors should all be near each other" |
| In2 | GND with a 1.1 V island and 1.8 V / 5 V / I2C tracks that cut the reference of In3 | GND with one island under the VDD fingers, south of every In3 trace; no track | In3 is the command layer |
| Planes at the fingers | run under the gold fingers | pulled back 1.0 mm (In1, In4), 0.5 mm (In2), 3.0 mm (In3) from the finger edge | finger impedance and edge chamfer |
| Power | one quad buck regulator from 5 V with inductors, one LDO with a trim pot, 0 ohm jumpers | three fixed / resistor-set LDOs from 1.2 V and 3.3 V | the socket has no 5 V; "simpler, more dumb" |
| ZQ | 240 ohm to GND | 240 ohm to VDDQ | JEDEC (J 4.2.1.5) |
| Via-in-pad process | not recorded in the files | 253 vias carry the filled / capped attribute; fab note 6 | fabrication |
| Unused inner pads | kept | removed on all vias but the ten command node vias | 0.225 mm instead of 0.125 mm from an In3 command trace to foreign copper |
| Fiducials, origin, fab notes | none | six fiducials, drill / place origin at the card corner, 13 fabrication notes | fabrication |

### 11.3 Spacings rolled back to the Antmicro values

After the read taps were removed (section 10.2, rows 5 and 7) every spacing that had been tightened was taken back as far as the form allows.
Every rule change is in `rules.changes_of_the_data_replan` with its reason.

| Item | Antmicro | As built | At the Antmicro value? |
|---|---|---|---|
| F.Cu DQ bundle, gap in long runs | 0.40 (0.55 pitch) | 0.40 everywhere outside the land field (0.416 on the fan of the two lower lines) | **yes** |
| Smallest DQ - DQ gap at the land field | 0.10 | 0.226 between two 0.09 mm necks inside the courtyard, 0.40 from the courtyard line on | **yes** |
| DQ line to a foreign via pad | 0.275 in the via row | 0.275 (one place: the DQ4 tooth beside a return via); no wire beside a column | **yes** |
| How a DQ wire meets its divider | in-line via, along the columns | tooth on the column axis into the node via | **yes** |
| Divider parts and pitch | 4 x 0402 arrays, 0.50 | 0201, 0.8 (the command field's pitch), back to back | no: arrays do not fit eight columns between the key and finger 161 |
| Vias in line per column | 1 | 1 (the shunt GND via south of the row, shared by two columns) | **yes** |
| Signal via pad to solder land | 0.05-0.10 | 0.25, plain via | **yes** in the data field; command field unchanged |
| WCK pair to another signal track | >= 0.87 on 95 % | 0.975 on B.Cu, 0.925 for the F.Cu column links (0.20 mm wide); rule 0.80 | **yes** |
| WCK length P - N | 0.01-0.13 | 0.26, limit 0.50, no bump | **yes** |
| WCK uncoupled per leg | 3.3-5.8 | 2.0 / 2.3 on B.Cu (3.2 / 3.5 as the DRC counts, with the column links), limit 4.0 | **yes** |
| Pair skew shape | serpentine of the coupled pair | WCK none; RDQS one bump 0.55 mm deep, legs 3 h apart, on In3 beside U1 where the skew arises | WCK yes; RDQS no: ball C4 lies one row beyond B3 |
| Neck length at the BGA | 0.01-5.6 mm | 0.6-5.3 mm | no: C6, E6, F5 leave through row channels so that nothing lies along a command channel |
| RDQS uncoupled per leg | 2.6-5.0 | 3.35 / 2.84 (inside the DRC limit), **limit 4.5** | **yes**, in part: what is left is the ball end, the bump and the via pair |
| Read track to the RDQS load lands | - | 0.425 (the western side link to the track of finger 215; the pair itself 0.825) | **yes** |
| Read track to a capacitor GND via | 0.100 | no such via: the capacitor's ground is the VSS finger | **yes** |
| In3 DQ gap | 0.25 | 0.40 (pitch 0.55); rule 0.39 | **yes** |
| RDQS pair to another signal | >= 0.75 on 97 % | >= 0.825 on F.Cu outside the two side links and the 0.28 mm steps at the via pair; >= 0.85 on In3 (new DRC rule) | **yes** |
| DQ match in the byte | 2-3 ps (with serpentines: 729 tuning arcs on F.Cu) | write +/-26.7 (gateware), read +/-56.8 (gateware; +/-9.7 with 20 read-end crossings) | **no: worse** (section 14.3) |
| Reference parts | 0402 | 0201 in two rows: lands 0.18 mm apart (rule 0.17), one stub 0.14 mm from a finger via (rule 0.12) | no: two new local clearances |
| Command stage | 0.09-0.80 | 0.37 mm or more between nets outside U1 | looser than Antmicro |

### 11.4 Outer-layer copper

| Outer-layer copper, % of the board area | F.Cu | Antmicro F.Cu | B.Cu | Antmicro B.Cu |
|---|---|---|---|---|
| all copper (tracks, pads, vias, zones) | **16.6** | 28.1 | **16.0** | 24.9 |
| GND zones | **0** | 4.0 | **0** | 2.8 |
| GND, all copper (fingers, pads, links, vias) | 4.7 | 7.1 | 4.1 | 7.0 |
| power zones | 0 | 7.4 | 2.8 (VDDQ 2.27, VDD2H 0.57) | 8.1 |

Measured by gate 9 on the board and, with the same code (`check_pcb.py --coverage <board>`), on the upstream board. A third of the copper of each
face is the finger row. Details and the reasons: section 15.4.

## 12. Crossings

The user asked to minimise crossings between high-speed wires and wires on other layers and not to route through resistor groups (section 10.2,
row 6). `scripts/pcb/crossings.py` counts them on the board in plan view (two different layers, the two legs of a pair one by one), gate 8 locks
the counts. No two wires cross on one layer anywhere.

### 12.1 The table

| Class | As built | Lower bound of this topology | Why it cannot be lower |
|---|---|---|---|
| Command x data | **7** | 7 | the balls B5, C6, D5, E6, F5 are enclosed by the In3 traces of CA0 / CA1 (list in 12.2) |
| Command x supply strap | **3** | | two VDD2H straps of the CK corridor (balls of column 9 without via), the VDDQ strap D4 - E5 over CA0; In1 / In2 between |
| Command x GND link | **5** | | F.Cu links between GND balls of the U1 escape (among them D9 - D10 and the two diagonals from G9 in the CK corridor) |
| **Command bundle, all** | **15** | | 7 + 3 + 5. With the eight escapes of the unused byte it was 30 (section 5) |
| F.Cu line or tooth x In3 read branch of another bit | **28** | 28 | each pair of bits once: the In3 line of the west column passes under the tooth of the east column, at 90 degrees |
| Read finger track x In3 line | **0** | 0 | the read fingers are taken in the order of the bundle (the read end without crossings); the read group is +/-56.8 ps for it |
| DQ x WCK | **6** | 6 | the DQ4 and DQ5 lines over the WCK diagonal just east of U1 (4), the necks of DQ1 and DQ3 over WCK_P inside U1 (2) |
| DQ x RDQS | **14** | 14 | 12: the 45-degree run of the RDQS In3 pair under the six diagonals of the F.Cu fan, at 90 degrees; 2: the DQ5 neck inside U1. The pair leaves U1 between the DQ5 and the DQ1 line and bank 66 lies east of the comb: each of its two legs has to cross the six nets DQ1, DQ0, DQ3, DQ2, DQ6, DQ7 once, wherever it goes. The drop crosses nothing: it is the eastmost wire of the read end |
| WCK x RDQS | **4** | 4 | the two pairs leave the corner on different layers, inside U1: one place, at 45 degrees, WCK on B.Cu under RDQS on In3 with only the perforated In4 between. WCK toggles during reads, so this is the one crossing of two active pairs without a solid plane between them (0.2 mm of overlap per leg: negligible, but named) |
| **Data x data**, legs counted singly (pairs as one wire: 40) | **52** | 52 | 28 + 0 + 6 + 14 + 4: the count as built IS the lower bound of this comb and this corner escape |
| High-speed x VREF | **9** | 9 | seven read finger tracks (every one but the westmost, finger 182, which has no network west of it) and the two RDQS legs across the reference bus, each once: the bus has to reach the DAC network east of them |
| High-speed x supply | **18** | | 12.3 |
| **All crossings with a high-speed wire** | **94** | | 15 + 52 + 9 + 18 |
| Without solid GND on every plane between | **4** | | three inside U1 over the perforated planes; the fourth is the VDD1 east rail under the DQ1 neck on the land of C421 |
| Same-layer crossings | **0** | 0 | requirement |
| Wires through a resistor group | **0** | 0 | requirement: every wire enters along the column axis |
| Jogs of a group's own nets off the column axis | 3 | | the two CK lead-outs; the 45-degree run of WCK_P into its node via |
| Parallel overlap on two layers, mm: command x data / data x data / data x other | 0.19 / 1.06 / 10.09 | | data x other: the read tracks of fingers 212 and 215 lie 0.015 / 0.075 mm, in plan, from the stub and the 39 ohm link of their own N finger on the other face (In1, In2, In4 solid between) |
| Serpentine in the data groups, mm | 0 | | |

Gate 8 fails above: command x data 7, data x data 52, high-speed x VREF 9, high-speed x supply 18, same layer 0, wires through a resistor group
0, overlap command x data 0.2 mm. The limits that EQUAL the counts as built are regression locks (a later change that adds a crossing fails the
gate), not quality criteria that were met with margin; the two zeros are requirements. The resistor groups are in the spec
(`floorplan.data.resistor_groups`); the WCK columns (two rows) and the DQ columns (one row, back to back) are two groups, because one box around
both would take in the In3 bundle north of the DQ node row, where no part stands.

How the count came down, for the record: the first routing of the data side had 166 crossings with a high-speed wire (command x data 25, data x
data 109, five wires through resistor groups); the comb with the read group matched on the card had 80 among the data wires (20 at the read end).
Of the 40320 assignments of the eight read pairs, the front "read-end crossings against read spread" is: 0 crossings +/-56.8 ps (this board);
8: +/-42.3; 12: +/-25.9; 14: +/-17.0; 18: +/-11.7; 20: +/-9.7 ps (`data_side.explore()`).

### 12.2 The seven crossings of the command bundle by data wires

All inside the land field of U1; In3 below, the data wire on F.Cu (WCK_N on B.Cu).


| Command (In3) | Crossed by | at | Why it cannot be avoided |
|---|---|---|---|
| CA0 | DQ4 (F.Cu) | (23.525, 12.35) | ball B5 is enclosed by CA0 |
| CA1 | DQ5 (F.Cu) | (22.725, 13.75) | ball C6 is enclosed by CA1 and CA0 |
| CA0 | DQ5 (F.Cu) | (23.525, 13.75) | same |
| CA0 | WCK_N (B.Cu) | (23.525, 14.45) | ball D5 is enclosed by CA0 |
| CA1 | DQ6 (F.Cu) | (22.725, 15.15) | ball E6 |
| CA0 | DQ6 (F.Cu) | (23.525, 15.15) | same |
| CA0 | DQ7 (F.Cu) | (23.575, 15.85) | ball F5, on the last 0.57 mm of CA0 into G4 (45 degrees) |

**Command bus protection.** The RDQS escape on In3 puts copper beside CA0: the In3
pad of the C4 via-in-pad, 0.71 mm of the RDQS_N leg (`floorplan.data.command_zone_exception`), and the GND barrels of A4 / C5 0.25 mm from CA0 / CA1.
HFSS of the CA0 channel with its neighbouring barrels (`design/ref/hfss_ca0_data_corner.json`): the two barrels move CA0 by +0.009 ps;
with a 0.40 mm pad on C4 the delay would move +0.034 ps and RDQS would couple 1.15 % of its swing into CA0 at the
fastest edge considered (42 ps), above the 1 % limit. With the pad trimmed to
0.35 mm, as built, CA0 moves **+0.026 ps** (limit 0.3), |S11| stays at or below 0.0025 and the coupling is **0.95 %** (0.50 % at an
80 ps edge; 3.6 mV on CA0 for the length of the edge). About two thirds of that coupling is the C4 barrel. Not in the model: section 8, item 30.

### 12.3 Crossings of a high-speed wire by a supply wire

| Wire | Crosses | Planes between | Why it cannot be avoided |
|---|---|---|---|
| 1.2 V strip (F.Cu) | the eight In3 read lines (45 degrees, on their ramp), RDQS_P / RDQS_N on In3 (90 degrees): 10 | In1, In2 solid | the VDD fingers lie south of the read bundle, the regulators north of it |
| VDD1 east rail (B.Cu) | DQ0, DQ1, DQ2, DQ3, DQ6, DQ7 on F.Cu: 6 | In1, In2, In4 | ball C1 lies in the pocket between the six lines that leave U1 eastward and the DQ4 / DQ5 lines, WCK and RDQS; the crossing of DQ1 lies on the land of C421 |
| VDDQ east rail (B.Cu) | DQ7 neck on F.Cu: 1 | In1, In2, In4 | balls G1 / G2 lie between the necks of DQ6 and DQ7 |
| VDDQ strap D4 - E5 (F.Cu) | CA0 on In3, WCK_N on B.Cu: 2 | In1, In2 (CA0); In1, In2, In4 (WCK_N) | the WCK pair cuts four VDDQ balls off from the rail on the back |

18 on high-speed wires of the data side plus the strap over CA0, which the table of 12.1 counts with the command bundle. RESET_n, PG, SCL, SDA, the
enable and feedback nets and every GND link of the power stage cross none. Parallel overlap: 0.28 mm of the VDD2H
link F6 - K6 beside the last jog of CA1 (In4 between), 0.18 mm under the DQ7 neck, 0.07 mm under the DQ0 neck. The pours are planes and are not
counted as wires: the VDDQ / VDD2H pours lie under the necks and the command traces of the U1 corner like the In4 plane above them. Also in plan
view, and given by the socket: the command finger stubs on opposite faces overlap for 3.97 mm in all (front and back fingers are 0.25 mm apart),
with In1, In2 and In4 between.

## 13. The command bus as routed (stage s20)

`scripts/pcb/s20_cmd.py` draws the ten finger stubs, the links of the ten divider columns (series land - node via -
shunt land 0.40 mm wide, shunt land to fence via 0.25 mm), the CK pair and the eight CA / CS traces with their tuning
features: 203 track items, no additional via. Gate 6 of `scripts/pcb/check_pcb.py` measures the result on the board,
bends included, and `scripts/check_netlist.py` re-derives the plan with its own geometry code. The principles are in section 1; this
section is the evidence.

**Bend calibration (HFSS 2026 R1).** One In3 trace, 0.15 x 0.018 mm (0.10 mm for the CK leg), between two ground
planes 0.488 mm apart (0.110 mm to In2), er 4.29, lossless, wave ports, adaptive at 8 GHz to delta S 0.002, phase of
S21 at 0.5-10 GHz against a straight line of the same box. The straight line gives 6.914 ps/mm (plan: 6.91). The
added delay of every shape is flat to 0.002 mm from 0.5 to 8 GHz, and a run repeated with delta S 0.0005 differs by
0.001 mm. Data: `design/ref/hfss_cmd_tuning.json`. "Formula" is `rules.bend_model`:

| Shape (w 0.15 mm unless noted) | Centre line added | Turned | HFSS: electrical added | Shortfall | Formula - HFSS |
|---|---|---|---|---|---|
| parallel shift 0.68 mm (four mitred 45-degree vertices), w 0.15 | 0.563 mm | 180 deg | 0.500 mm | 0.063 mm = 0.44 ps | +0.000 mm |
| parallel shift 0.68 mm (four mitred 45-degree vertices), w 0.10 | 0.563 mm | 180 deg | 0.509 mm | 0.055 mm = 0.38 ps | -0.000 mm |
| one bump 0.73 mm, r 0.20, legs 1.44 mm apart | 1.116 mm | 360 deg | 0.988 mm | 0.128 mm = 0.88 ps | -0.001 mm |
| one bump 0.73 mm, r 0.20, legs 0.72 mm apart | 1.116 mm | 360 deg | 0.982 mm | 0.134 mm = 0.92 ps | -0.000 mm |
| two bumps 0.73 mm, r 0.20, legs 0.72 mm apart | 2.232 mm | 720 deg | 1.960 mm | 0.272 mm = 1.88 ps | -0.001 mm |
| two bumps 0.73 mm, r 0.20, legs 0.48 mm apart | 2.232 mm | 720 deg | 1.884 mm | 0.348 mm = 2.41 ps | +0.006 mm |
| shift 0.50 mm + two bumps 0.45 mm, r 0.20, legs 0.72 mm apart | 1.526 mm | 900 deg | 1.204 mm | 0.323 mm = 2.23 ps | -0.007 mm |
| two bumps 0.55 mm, r 0.15, legs 0.72 mm apart | 1.683 mm | 720 deg | 1.382 mm | 0.301 mm = 2.08 ps | +0.002 mm |
| two bumps 0.55 mm, r 0.15, legs 0.48 mm apart | 1.683 mm | 720 deg | 1.336 mm | 0.348 mm = 2.40 ps | -0.003 mm |
| two bumps 0.55 mm, r 0.10, legs 0.48 mm apart | 1.854 mm | 720 deg | 1.433 mm | 0.421 mm = 2.91 ps | -0.006 mm |

What the runs say: (1) the loss goes with the angle turned: 0.0201 mm per radian at a mitred 45-degree vertex
(0.0174 on a 0.10 mm trace), 0.0203 / 0.0229 / 0.0292 mm per radian in arcs of 0.20 / 0.15 / 0.10 mm radius;
(2) legs 0.48 mm apart cost a further 0.05-0.08 mm per two bumps, legs 0.72 mm apart 0.01 mm; (3) a bump of
amplitude a therefore adds only about 2 a - 0.40 mm of delay at 0.15 mm radius, so shifts and few large bumps beat
many small ones (an accordion of 22 small bumps loses 3-4 ps against its centre-line length). The verifier's FDTD
(zero-thickness trace) gives about three quarters of the HFSS shortfall on the same kind of shape; HFSS is the
reference used.

**Every feature in HFSS.** The formula treats each bend by itself. The runs of the features as planned show that
bends close to each other interact: with short bump legs (CS0: 0.08 mm of straight between two arcs) the loss is
0.06 mm smaller than the formula, with long ones (CA2) 0.02 mm larger. The plan therefore carries, per net, HFSS
minus formula for that net's own feature, and the router and the gate add it. Two rounds were run; the table is the
second, on the shapes the first round led to (the residual moved by 0.012 mm or less between the rounds):

| Net | Simulated feature | Centre line added | HFSS: electrical added | Formula | HFSS - formula (residual in the plan) | Board against the simulated shape |
|---|---|---|---|---|---|---|
| CA5 | shift 0.224 mm | 0.186 mm | 0.1264 mm | 0.1224 mm | +0.0040 mm = +0.03 ps | shift -0.002 mm |
| CA6 | shift 0.500 mm + 2 x 0.552 mm | 2.104 mm | 1.7314 mm | 1.7418 mm | -0.0104 mm = -0.07 ps | shift +0.000 mm, amplitude -0.003 mm |
| CA3 | shift 0.280 mm + 2 x 0.441 mm | 1.478 mm | 1.1404 mm | 1.1178 mm | +0.0226 mm = +0.16 ps | shift +0.000 mm, amplitude +0.000 mm |
| CA4 | shift 0.140 mm + 2 x 0.442 mm | 1.369 mm | 1.0430 mm | 1.0086 mm | +0.0344 mm = +0.24 ps | shift +0.000 mm, amplitude -0.001 mm |
| CA2 | no shift + 2 x 0.738 mm | 2.436 mm | 2.1104 mm | 2.1328 mm | -0.0224 mm = -0.15 ps | shift +0.000 mm, amplitude +0.000 mm |
| CS0 | shift 0.550 mm + 2 x 0.384 mm | 1.473 mm | 1.1739 mm | 1.1143 mm | +0.0596 mm = +0.41 ps | shift +0.000 mm, amplitude -0.001 mm |
| CA1 | shift 0.802 mm | 0.665 mm | 0.6001 mm | 0.6017 mm | -0.0016 mm = -0.01 ps | shift -0.001 mm |
| CA0 | shift 0.690 mm | 0.571 mm | 0.5065 mm | 0.5081 mm | -0.0016 mm = -0.01 ps | shift +0.000 mm |


A shift alone needs almost no correction (0.002-0.004 mm at 0.22, 0.25, 0.68, 0.69, 0.71, 0.80 and 0.82 mm).

Measured on the board (finger top to ball; tracks and arcs by layer and width, series resistor pad to pad, barrels
between mid-copper heights, In3 bends by `rules.bend_model` with arcs, vertices and legs read from the board, plus
the HFSS residual of the feature):

| Net | Outer mm | In3 mm | Vias mm | Bends: arcs / mitres, turned | HFSS residual mm | Bend correction ps | Card ps | Target ps | Error ps | Total ps |
|---|---|---|---|---|---|---|---|---|---|---|
| CA5 | 3.709 | 11.400 | 1.157 | 0 / 5, 225 deg | +0.0040 | -0.52 | 108.87 | 108.87 | -0.00 | 641.77 |
| CA6 | 3.450 | 12.609 | 1.285 | 8 / 5, 945 deg | -0.0104 | -2.69 | 114.37 | 114.37 | +0.00 | 641.77 |
| CA3 | 3.492 | 12.696 | 1.157 | 8 / 5, 945 deg | +0.0226 | -2.45 | 114.57 | 114.57 | +0.00 | 641.77 |
| CA4 | 3.202 | 11.883 | 1.285 | 8 / 5, 945 deg | +0.0344 | -2.37 | 108.17 | 108.17 | -0.00 | 641.77 |
| CK_N | 3.243 | 12.164 | 1.157 | 0 / 5, 225 deg | - | -0.47 | 111.37 | 111.37 | -0.00 | 641.77 |
| CK_P | 3.243 | 12.076 | 1.157 | 0 / 6, 315 deg | - | -0.66 | 110.57 | 110.57 | +0.00 | 641.77 |
| CA2 | 3.202 | 12.954 | 1.285 | 8 / 1, 765 deg | -0.0224 | -2.37 | 115.57 | 115.57 | -0.00 | 641.77 |
| CS0 | 3.492 | 12.686 | 1.157 | 8 / 5, 945 deg | +0.0596 | -2.19 | 114.77 | 114.77 | -0.00 | 641.77 |
| CA1 | 3.450 | 11.880 | 1.285 | 0 / 5, 225 deg | -0.0016 | -0.56 | 111.47 | 111.47 | -0.00 | 641.77 |
| CA0 | 3.709 | 11.087 | 1.157 | 0 / 5, 225 deg | -0.0016 | -0.56 | 106.67 | 106.67 | -0.00 | 641.77 |

Without the bend corrections the same copper would read 642.24 ps (CK_N) to 644.46 ps (CA6): 2.2 ps of the match
rest on HFSS. What the model does not contain is listed in section 8 (item 18).

- **Pair.** Coupled at 0.10 mm over 9.80 mm (y 6.35-16.15, 81 % of the In3 length), mirror-symmetric about ball column 9
  from the finger stubs to the split; In3 length difference 0.088 mm, which with the hook's bends and the host (0.8 ps)
  leaves 0.00 ps at the balls.
- **Vias.** Every net passes exactly two: the 0.40 / 0.20 node via (0.64 mm of barrel from F.Cu on four nets, 0.51 mm from
  B.Cu on six, CK_P and CK_N both from B.Cu; the rest of the barrel leads to the shunt, so there is no open stub) and the
  0.40 / 0.15 via-in-pad (0.64 mm, open stub to B.Cu 0.51 mm).
- **Reference planes.** In2 and In4 are GND under every In3 trace over its whole length: 100 % of the trace copper lies
  over plane copper on both layers. Under U1 the planes are perforated by the antipads of the non-GND via-in-pads
  (0.55 mm on the 0.80 mm column pitch: a 0.25 mm web under the 0.15 mm trace). Widened by h = 0.11 mm on both sides the
  CA / CS traces see 93.6-96.1 % plane inside the via field (97.3-98.2 % over their whole length); the CK pair, in its
  via-free corridor, 99.6-100 %.
- **Return vias.** A GND via 1.70 mm from every node via (its fence via) and 0.70-0.80 mm from every ball via (the
  neighbouring VSS ball).
- **Spacing on In3.** South of the via field: CA / CS to CA / CS at least 0.375 mm (3.4 h; CA6 / CA5), CA / CS bumps to the
  CK pair at least 0.673 mm (CA4), trunks to the CK lead-out at the node vias 0.675 mm; inside the via field 0.250 mm
  where a trace jogs into its ball. No foreign via pad on In3 beside any trace; barrels at 0.225 mm or more.
- **Angles.** 153 vertices: 86 straight joints (arc tangents and the vertex at the U1 courtyard edge), 66 of 135 degrees,
  one of 90 degrees (the tip of the CK_P hook); 40 arcs of 90 degrees, 0.15 mm radius, all in the bumps.
- **DRC.** No error. Five `diff_pair_gap_out_of_range` warnings on CK, all where the legs must part (the links of the two
  divider columns on F.Cu / B.Cu, the lead-out of the node vias, the split around H9). The DRC limit for the uncoupled
  length of CK stays at 3.5 mm because KiCad adds the outer-layer links; the board gate holds In3 alone to 2.5 mm.
- **Rules of the tuning features** (generator and both checkers): amplitude >= 0.30 mm (2 w), radius >= 0.15 mm; a radius that does not fit is
  an error and is never reduced. Barrel lengths are taken between the middles of the copper layers (F-In3 0.6425 mm, B-In3 0.5145 mm).
- **Kept as they are:** the 90-degree vertex of the CK_P hook (it is where the H9 / J9 row difference arises, and both its vertices are in the
  bend model); the 0.10 mm width of the CK legs where they are uncoupled; no return via nearer than 1.70 mm to a node via (no room at 0.8 mm pitch
  between 0201 lands; part of the socket launch model, section 8).

## 14. The data side as built (stage s30_data: write group, read group, WCK, RDQS, reference networks)

One stage draws the whole data side. `scripts/data_side.py` holds the plan, `scripts/build_netlist.py` writes it to
`floorplan.data` (pin map, columns, 138 wires as centre lines, vias, return vias, rule areas, crossing budget), stages
s00 / s10 place the parts and the 62 field vias, and `scripts/pcb/s30_data.py` draws the wires and adds no via. Gate 7 of
`check_pcb.py` measures every net on the board, gate 8 counts the crossings with `scripts/pcb/crossings.py`. The fingers,
levels and resistors are tabulated in sections 2 and 3; this section gives the form, the numbers as built and what the
form costs.

### 14.1 Topology: the T at the divider

Each DQ is a tooth of a comb. From the ball to the fingers:

| Piece | Layer | Where |
|---|---|---|
| neck | F.Cu, 0.09 mm | from the ball out of the land field, one per row or column channel, 0.6-5.3 mm; no via at the ball |
| line | F.Cu, 0.15 mm | east on its own level: DQ4 and DQ5 leave the land field southward and run at y = 10.75 / 11.30; the six others leave eastward at y = 13.60 ... 16.35, pitch 0.55 mm (gap 0.40 mm) |
| fan | F.Cu | one 45-degree run per line, all parallel, down to y = 10.40 (10.95 for DQ5) on the axis of its column; the lowest line takes the westmost column, so nothing crosses |
| tooth | F.Cu | straight down the column axis into the node via at y = 6.00; teeth 0.8 mm apart (1.35 mm across the key) |
| **the T** | via 0.40 / 0.20 | the node via. Here the line from the ball, the In3 read branch and both divider resistors meet. 0.68 mm south of it, back to back on the two faces: the 240 ohm on the write finger's face (then the finger stub, 2.1-2.4 mm) and the 150 ohm on the other face (then its GND via) |
| read branch | In3, 0.15 mm | leaves the node via on one 45-degree run into the lane 0.40 mm east of the column axis, runs north in the lane to its level (y = 6.00 for the east column, +0.55 per column westward, 9.85 for DQ4), then east under the teeth of the columns east of it, all crossings at 90 degrees |
| bundle and ramp | In3 | eight lines at 0.55 mm pitch (gap 0.40 mm); east of the comb (x 43.7 ... 47.5) the whole bundle climbs one 45-degree ramp (2.20 mm of rise, the same for every line: none is raised above its place) and each line runs east on the level of its read via. The lowest level is y = 8.20: a read via cannot stand lower, its pad has to keep 0.90 mm from the VREF_RD bus on F.Cu |
| read via | via 0.40 / 0.20 | on the level of its line, above its read finger. **The read fingers are taken in the order of the bundle**: the lowest line (DQ7) ends first, on the westmost finger (182); every higher line passes 0.55 mm north of the vias below it and ends further east, the top line (DQ4) on finger 215. The eight read vias form a regular staircase, 0.55 mm per step, from (48.675, 8.20) to (57.100, 12.05). GND return vias: 0.64 mm to the south-east of the read via (the next line passes north of it), to the south-west where the next finger track or the RDQS drop takes that spot; six return vias serve the eight read vias (those of DQ0 / DQ1 and of DQ5 / DQ4 stand between the two read vias, 0.56-0.72 mm from each) |
| finger track | finger's face, 0.20 mm | from the read via straight south into the finger, 5.5-9.4 mm; it crosses **no** In3 line: every line of a lower level has ended west of it |

The west column carries the lowest F.Cu line and the highest In3 line, so the F.Cu comb and the In3 bundle are each planar and
nest into each other. There is no part and no via north of the node row in the DQ columns, no wire between two columns on
F.Cu or B.Cu, and no wire that enters a resistor from the side.

| Net | Ball | F.Cu level y | Column x | T (node via) | T to the 240 ohm land | T to the 150 ohm land | In3 beside its own tooth mm | In3 level in the field / at the read via | Read via | Read finger | GND via nearest the T / the read via mm |
|---|---|---|---|---|---|---|---|---|---|---|---|
| DQ4 | B5 | 10.75 | 37.250 | (37.250, 6.00) | 0.68 mm (R205, F) | 1.84 mm (R215, B) | 3.15 | 9.85 / 12.05 | (57.100, 12.05) | 215 (F) | 0.63 / 0.64 |
| DQ5 | C6 | 11.30 | 38.050 | (38.050, 6.00) | 1.84 mm (R206, B) | 0.68 mm (R216, F) | 2.60 | 9.30 / 11.50 | (55.940, 11.50) | 212 | 0.85 / 0.72 |
| DQ1 | C2 | 13.60 | 39.400 | (39.400, 6.00) | 0.68 mm (R202, F) | 1.84 mm (R212, B) | 2.05 | 8.75 / 10.95 | (55.175, 10.95) | 208 | 0.63 / 0.64 |
| DQ0 | D1 | 14.15 | 40.200 | (40.200, 6.00) | 1.84 mm (R201, B) | 0.68 mm (R211, F) | 1.50 | 8.20 / 10.40 | (54.175, 10.40) | 204 | 1.38 / 0.56 |
| DQ3 | D3 | 14.70 | 41.000 | (41.000, 6.00) | 0.68 mm (R204, F) | 1.84 mm (R214, B) | 0.95 | 7.65 / 9.85 | (51.675, 9.85) | 194 | 2.04 / 0.64 |
| DQ2 | E2 | 15.25 | 41.800 | (41.800, 6.00) | 1.84 mm (R203, B) | 0.68 mm (R213, F) | 0.40 | 7.10 / 9.30 | (50.675, 9.30) | 190 | 1.96 / 0.64 |
| DQ6 | E6 | 15.80 | 42.600 | (42.600, 6.00) | 0.68 mm (R207, F) | 1.84 mm (R217, B) | 0.00 | 6.55 / 8.75 | (49.675, 8.75) | 186 | 1.57 / 0.64 |
| DQ7 | F5 | 16.35 | 43.400 | (43.400, 6.00) | 0.68 mm (R208, F) | 1.84 mm (R218, B) | 0.00 | 6.00 / 8.20 | (48.675, 8.20) | 182 | 0.92 / 0.64 |

T distances are copper lengths from the centre of the node via to the land; the resistor on B.Cu is reached through the barrel
(1.157 mm). Limits of gate 7: 2.0 mm, and every land of the sixteen divider parts within 6.0 mm of the card edge (5.55 mm as
built). Three GND return vias stand in the node row where one fits: (36.70, 6.30) for DQ4 and WCK_P, (38.85, 6.30) above the
key for DQ5 / DQ1, (44.00, 5.30) for DQ7 (0.30 mm from the DQ7 series link). **Stated exception of the spec**
(`rules.netclasses.RD_DQ.node_return_via_exception`): the rule "a GND via within 1.0 mm" holds for the read vias, the RDQS
via pair and the node vias of the outer columns; the node vias of DQ0, DQ3, DQ2, DQ6 have only the shunt GND vias south
of the row (1.38-2.04 mm, limit of the exception 2.1 mm), because a through via does not fit between teeth at 0.8 mm with
an In3 riser in every lane. The planes are unbroken there (0.2 mm webs between the antipads on In1, In2 and In4).

**WCK** (section 2): B.Cu pair from the via-in-pads of D5 / E4 along the diagonal D3 - C2 - B1, east as the lowest lane, south
into two columns with 0.20 mm links, the two shunt GND vias mirror images about the pair; no skew bump. **RDQS** (section 3, 14.4):
In3 from the via-in-pads of B3 / C4 east between the two lower and the six upper F.Cu lines, one 45-degree run across the
six diagonals of the fan (90 degrees), east above the read vias, one 45-degree run down to the axis of fingers 219 / 221 and south
to a via pair at y = 8.50, 5.8 mm of F.Cu down into the fingers, east of every read finger; one skew bump on In3 beside U1; the loads low
beside the drop on the VSS finger axes.
**Reference networks** (section 3): 0201 in two rows, the capacitor grounded on the neighbouring VSS finger.
**RESET_n** is not a wire of the data side: the power stage routes it on B.Cu (sections 1, 15).

**Inside U1** (stages s00 / s10): the eight necks; A4 with a via-in-pad of its own (GND; no strap to B4, the DQ4 neck uses that
channel); C5 with its via; C415 at A3 / A4, off the WCK diagonal; In3 pads of 0.35 mm on the via-in-pads of B3 / C4.
The command stage s20 is untouched and gate 6 runs unchanged, with one stated exception in its layer-purity check: 0.71 mm of
the RDQS_N In3 leg lie inside the command zone where the leg leaves C4 (`floorplan.data.command_zone_exception`), accepted
only while the HFSS result of section 12.2 is on file with a passing verdict.

### 14.2 Delays as built (gate 7, measured on the copper)

Model: ps / mm per layer and width (section 6), via barrels at 7.0 ps/mm between mid-copper heights, the series resistor as
0.64 mm of line, every mitred vertex on every layer by `rules.bend_model`, counted from the finger top (y = 2.70); host and
package from `design/ref/`. The board agrees with the plan to 0.01 ps on every net.

| Net | Write: card ps | host ps | **total ps** | to the centre ps | output delay ps | Read: card ps | host ps | **total ps** | to the centre ps | input delay ps |
|---|---|---|---|---|---|---|---|---|---|---|
| DQ4 | 138.82 | 609.2 | **748.02** | -26.7 | 53.5 | 351.07 | 598.6 | **949.67** | +56.84 | 0.0 |
| DQ5 | 167.84 | 610.6 | **778.44** | +3.7 | 23.1 | 349.08 | 597.1 | **946.18** | +53.35 | 3.5 |
| DQ1 | 138.45 | 612.1 | **750.55** | -24.2 | 51.0 | 307.61 | 604.7 | **912.31** | +19.48 | 37.4 |
| DQ0 | 148.07 | 612.7 | **760.77** | -14.0 | 40.7 | 288.87 | 608.0 | **896.87** | +4.04 | 52.8 |
| DQ3 | 155.93 | 604.6 | **760.52** | -14.2 | 41.0 | 275.89 | 604.7 | **880.59** | -12.24 | 69.1 |
| DQ2 | 165.84 | 608.6 | **774.43** | -0.3 | 27.1 | 257.37 | 604.0 | **861.37** | -31.46 | 88.3 |
| DQ6 | 185.09 | 613.0 | **798.10** | +23.3 | 3.4 | 266.48 | 577.9 | **844.38** | -48.45 | 105.3 |
| DQ7 | 186.51 | 615.0 | **801.51** | +26.7 | 0.0 | 250.58 | 585.4 | **835.98** | -56.84 | 113.7 |
| RDQS_P / RDQS_N | | | | | | 326.72 / 326.52 | 596.4 / 596.6 | **923.12 / 923.12** | +30.29 | 0 |
| WCK_P / WCK_N | 135.46 / 136.99 | 648.8 / 647.8 | **784.26 / 784.79** | P - N -0.54 | | | | | | |

- **Read group: +/-10 ps, NOT met: +/-56.8 ps** (836.0 .. 949.7 ps about 892.8 ps), by the user's request for fewer crossings.
  With the In3 lines in the order of the comb, the read fingers can be taken without a crossing in one order only: the lowest
  line on the westmost finger. `data_side.choose_read_end()` derives it from the admissible pairs of bank 66 (a P pin with its
  N partner on the socket; not a host-coupled strobe pair; not 169 / 173, whose F.Cu track would stand in the corridor of the
  1.2 V feed; nothing east of the RDQS pair): exactly the eight pairs 182 ... 215, so the assignment is unique. That order makes
  the short lines shorter (DQ7: the shortest In3 line, the nearest finger, and the fastest host pin but one) and the long ones
  longer (DQ4). The level of a read via is no knob here: a raised line takes the lines above it with it, and the late lines are
  the top ones, so the lowest level set, the regular staircase with no line raised, is also the one with the smallest spread
  (re-derived by `check_netlist.py`). The trade of crossings against spread over all assignments is in section 12.1. Gate 7 holds every net within +/-1 ps of its own planned total and
  reports the spread; the per-bit input delays of the table (latest total minus own total, 0 .. 113.7 ps of the 1100 ps
  IDELAY range) are written to `design/gateware_deskew.json` at every build. Status: accepted by the user ("Same with read group").
- **RDQS: P to N +/-1 ps, met (0.00 ps); not matched to the read group.** The pair arrives 923.12 ps after the ball, 30.3 ps
  after the centre of the group, 87.1 ps after the earliest bit (DQ7) and 26.6 ps before the latest (DQ4): inside the span of the eight
  bits; with the input delays of the table every bit arrives 26.6 ps after the strobe. The pair stays on In3 down to a via pair just above
  its fingers (14.4). Over the outer
  copper / mask cases of section 6 the offset is 30.3 .. 32.2 ps and the group +/-56.8 .. 57.1 ps.
- **WCK pair: +/-1 ps, met** (-0.54 ps) without a bump. WCK arrives 9.8 ps after the centre of the DQ totals; it is not matched to DQ.
- **Write group: +/-10 ps, NOT met: +/-26.7 ps** (748.0 .. 801.5 ps). Gate 7 holds every net within +/-1 ps of its own planned total
  and reports the spread. The per-bit output delays of the table (latest total minus own total) are written to
  `design/gateware_deskew.json` at every build; status: accepted by the user ("The write group's spread is acceptable").

### 14.3 What this form costs

The comb with the T at the divider and the read end without crossings were chosen by the user (section 10.2, rows 8 and 9). What they cost:

1. **Write match: +/-26.7 ps on the card** (rule +/-10 ps; brief: "delay match the wires within one group"). With the T at
   the divider the line from the ball ends on its column, the columns stand in one row and a comb on one layer has one possible
   order, so the length of a line is given: the west columns are 5.5 mm nearer than the east ones, the necks differ by 4.7 mm
   and three nets pass a barrel. The fingers do not help: the ordinary pins of bank 64 differ by 13 ps, and the four that are
   35 ps slower (the CK0 / CK1 pairs) lie west of the comb, where the order of the columns allows only DQ4 to use one. There
   is no knob left that is not a wire doubling back. The match is in the FPGA: per-bit output delays of 0 .. 53.5 ps
   (section 7). Status: accepted by the user ("The write group's spread is acceptable").
   *What the copper alternative would cost* (computed with the plan's own model, `scripts/data_side.py`): the write-only copper
   is the 2 mm finger stub, so the length has to go into the F.Cu line from the ball, which the read path shares. Closing
   the write group to +/-10 ps takes 19.4 mm of accordion on six lines (DQ4 5.6, DQ1 5.2, DQ0 3.5, DQ3 3.5, DQ2 1.2, DQ5
   0.5 mm), to 0 ps 39.9 mm on seven; the six straight lines at 0.55 mm pitch would have to open to about 1.15 mm for it,
   and the read branches would then need accordions as well. Both groups matched in copper means serpentines in both bundles.
2. **Read match: +/-56.8 ps on the card** (rule +/-10 ps), for a read end without crossings: the read fingers are taken in the order of
   the In3 bundle, no finger track crosses an In3 line, and the per-bit IDELAY closes the group (0 .. 113.7 ps, section 7).
   Status: accepted by the user ("Same with read group"). With 20 read-end crossings the group would be +/-9.7 ps.
3. **Read wires go down, up and down.** Every read branch comes down its tooth to
   y = 6.00, turns and runs north again (0-3.15 mm beside its own tooth, then 2.20 mm up the ramp), and its finger track comes
   down 5.5-9.4 mm. No wire lies over itself (gate 7: 0 mm), there is no stem and no serpentine, but the up and down is real:
   10.6 mm of In3 beside its own tooth (In1 and In2 between the two). The ramp cannot be lower: the read vias are through vias and the reference
   networks with their bus fill both faces up to y = 7.1, so the lowest read via stands at y = 8.20. The eight read vias are not in one row: a line
   has to pass north of the vias below it, so they form a regular staircase, which is also the lowest and the shortest form.
4. **The longest read branch is 24.2 mm of In3** (DQ4; the shortest 6.2 mm, DQ7): the open-end ringing of
   the longest net is 0.549 V / -0.131 V in the strong corner (69 mV inside the recommended pin range) and the write node of DQ4 has 26.9 pF,
   tau 2.61 ns.
5. **Two read tracks leave their finger axes.** The read tracks of fingers 212 and 215 leave their finger axes by 0.235 mm west / 0.175 mm east
   to make room for the VREF_RD bus via, and lie over the stub and the 39 ohm link of their own N finger on the other face for 3.6 / 4.0 mm (three
   solid planes between). The western RDQS load link ends 0.425 mm from the read track of finger 215 (the legs keep 0.825 mm).
6. **T to the far resistor 1.84 mm** of copper (through the barrel), 0.68 mm to the near one; "near each other" is met in
   plan view (both lands 0.25 mm from the via pad), not as a stub length below 1 mm.
7. **Return vias:** four node vias (DQ0, DQ3, DQ2, DQ6) without a GND via within 1.4 mm (14.1): a stated exception of the spec.
8. **Bank plan:** the write pins lie in three byte groups of bank 64 and the read pins in six nibbles of bank 66, WCK and RDQS are inverted on
   their host pairs, and RESET_n is an output in the read bank (section 7).
9. **RDQS is the longest wire of the card**, 47.4 mm, and RDQS on In3 costs the three items of the command-bus protection (section 12.2).

What the form gives: one via per DQ column and no filled via in the data field, the upstream spacings (section 11.3), every divider entered
along its axis, no read taps, and the crossing counts of section 12.

### 14.4 RDQS: In3 down to its fingers

The pair has the form of a read DQ line (section 10.2, row 13b).

| Item | As built |
|---|---|
| Route | In3 from the via-in-pads of B3 / C4, east at y = 12.60 / 12.80, one 45-degree run across the six diagonals of the F.Cu fan, east at y = 15.50 north of every read via, one 45-degree run of 3.0 mm down, south at x = 58.175 |
| Layer change | via pair at (57.875 / 58.475, 8.50), above fingers 219 / 221; the two vias side by side across the pair, pads 0.20 mm apart |
| On F.Cu | 6.0 mm: the drop only (finger top to via 5.80 mm; the read DQ finger tracks are 5.5 .. 9.4 mm) |
| On In3 | 41.4 / 41.3 mm |
| GND planes | In2 / In4 under the In3 part, In1 under the F.Cu drop: 100 % of the copper over plane (gate 7) |
| Return vias | two, 0.58 mm from the pair vias, mirror images, at (57.70 / 58.65, 9.05) |
| P to N | 0.00 ps; one skew bump on RDQS_P, 0.553 mm deep, on In3 beside the balls where the skew arises |
| Coupled / uncoupled | coupled over 38.7 mm of In3 and 5.7 mm of F.Cu; 3.35 / 2.84 mm uncoupled per leg (limit 4.5) |
| To other tracks | 0.825 mm on F.Cu (drop), 0.85 mm on In3 (to the end of the DQ4 line); DRC rules with negative tests, among them `RDQS_pair_to_other_tracks_In3` (0.80) |
| Total / to the read centre | 923.12 ps / +30.3 ps (26.6 ps before the latest bit) |
| Loads | R309 / R310 (150 ohm to GND per leg) beside the drop on the axes of the VSS finger pairs, GND on those pairs' finger vias |

Plan-view crossings of the pair (legs counted singly): the six diagonals of the F.Cu fan 12, the DQ5 neck inside U1 2, WCK inside U1 4, the 1.2 V
strip on F.Cu 2 (In3 under it, In1 / In2 between), the VREF_RD bus on B.Cu under the F.Cu drop 2. None could be removed: the strip and the fan lie
between the balls and the fingers, and the bus has to reach the DAC network east of the drop.

Why the via pair stands at y = 8.50 and not lower: it is a through via, and its B.Cu pad has to keep 0.90 mm from the VREF_RD bus that runs
under the drop on B.Cu at y = 7.30. **One exception to the 0.80 mm rule:** the two F.Cu steps on which each leg leaves the centre of its via are
under a rule area of their own (`rdqs_via_pair`): the western via stands 0.475 mm (pad) from the read track of finger 215, and the 0.28 mm of track
from its centre to the coupled pair 0.625 mm; the pair itself keeps 0.825 mm. The alternative that keeps every track at 0.80 mm puts the western
via on the axis of its leg and the eastern one 0.6 mm aside: an asymmetric pair of vias, not taken. Why WCK cannot share a layer with this pair:
section 10.2, row 13b.

## 15. Power and slow nets as built (stage s50_power)
`scripts/power_side.py` holds the plan, `scripts/build_netlist.py` writes it to `floorplan.power` (placements, 86 centre lines, 39 vias, six pour
outlines under U1, the DC budget, the expected crossings, the policy for outer-layer ground), `scripts/pcb/s50_power.py` draws it and decides
nothing, gate 9 of `check_pcb.py` measures the board. The board has **no unconnected item**. There is **no ground pour on F.Cu / B.Cu
and no stitching via** (15.4).

### 15.1 The three remarks of the user and the rules drawn from them

- "Why does one of the wires in FCu go to the blue layer for a bit and back - that's dumb" (RESET_n). **Rule for every slow and supply net: no wire
  changes layer and comes back.** A wire stays on one layer for its whole run and changes layer at most at its ends, at a via beside a pad of the
  net; In3 carries the command group, the read group and the RDQS pair only. As a gate (9): on no net of the board does a stretch of track that
  touches no pad lie between two vias. A via-in-pad of U1 is the ball itself. The only stretches of that kind on the board are the eight In3 read
  branches (node via - In3 - read via), the documented topology of section 14.
- "What is the giant pour you added" (a 1.2 V pour that filled the corridor). **The feed is a 1.5 mm strip**, sized by calculation (section 4).
- "Why do F and B seem to have copper across the whole board in their images" (two board-wide GND floods). **No ground
  flood on an outer layer**: the brief says to keep the layer organisation and the routing conventions of the upstream board and to stay minimal, and
  the upstream board has none (15.4). Copper is put where a current or a signal needs it.

### 15.2 Where the regulators stand, and why two of them are on the back

U2 (VDD2H, 0.52 A) stands on F.Cu beside U1: its output goes through four vias (130 mA each) straight into the VDD2H island of In4. U3 (VDD1) and U4
(VDDQ) stand on **B.Cu** with all their parts. On one layer the layout has no solution without a hop:

- U2 and U4 share three nets: V1P2 (IN), V3P3 (BIAS) and PG (PG of U2 = EN of U4). Seen from above, both packages have these three pins in the same
  sense of rotation (V1P2 - PG - V3P3), so the three connections cannot be drawn on one layer without a crossing, whatever the placement;
- the 3.3 V trunk comes from finger 255 at the east end of the card and the 1.2 V strip from the fingers in the south: the trunk cuts the 1.2 V arm
  off from the enable resistor of U3 (R504, 1.2 V to EN), and the VDD1 output of U3 has to reach the EN pin of U2 on the other side of it.

With U3 and U4 on the back, every one of these nets changes layer once, at a via that stands beside the pin it ends on (V1P2: two vias at U4 IN, one
at R504; V3P3: one tap via each for U3 IN and U4 BIAS; PG: one via under U4 EN; VDD1: one via to U2 EN and the test pad), PWR_EN is 1.2 mm of B.Cu,
and the two outputs VDD1 / VDDQ start on the layer on which they are distributed to U1, without any via. On F.Cu the regulator area is three parallel
lines from the east into U2: the 1.2 V arm (y = 21.05), PG (y = 21.90) and the 3.3 V trunk (y = 22.92).

| Rail | Path |
|---|---|
| V1P2 | 19 fingers - finger vias - In2 island - 4 vias - F.Cu strip (corridor) - arm - U2 IN, C501, C513; 2 vias - B.Cu - C508 - U4 IN; 1 via - B.Cu - R504 |
| V3P3 | finger 255 - F.Cu west of the tooling hole, up the east side of U5 (outside SCL / SDA), west along the north of the DAC (C511, C332, U5 VDD), north at x = 62.97 and west along y = 22.92 - C509, R503, C503 - U2 BIAS; tap vias to U4 BIAS and U3 IN (C506) on B.Cu. 54.5 mm at 0.30 mm: 89 mohm, 2.7 mV at 30 mA |
| VDD2H | U2 OUT - 0.8 mm strap - C505 - 4 vias - In4 island - via-in-pads of rows K .. AA; rows H / J: B.Cu links H2 - J2 - K2, H14 - J14 - K14, J5 - K5, J11 - K11; rows A .. F (18 balls, 122 mA): B.Cu pour under columns 6 .. 10 with C401 .. C404, joined to K6 by ONE link past the via of H6, 0.30 mm wide with a 0.12 mm neck of 1.5 mm between the vias of H5 and H6 (8.9 mohm, 1.1 mV; 15.4); feedback R501 / R502, soft start C504, test pad TP2 on F.Cu |
| VDDQ | U4 OUT (B.Cu) - C510, R506 / R507, R508 - 1.0 mm lane north of the two regulators west to the north-east corner of U1 (21.7 mm, 10.7 mohm, 2.4 mV) - 0.5 mm north rail above row AA (AA3, AA13) - pours on the four flanks (rows A .. F with tabs over G1 / G2 and G14 / G15, rows R .. AA; the lower east flank in two outlines beside the WCK pair) - 0.4 mm rails outside columns 1 and 15 from rows R / T to row G; the four balls A3, B2, C3, D4 south-west of the WCK diagonal by one F.Cu strap D4 - E5; one via to the test pad TP3 and on F.Cu to R332 (default divider of VREF_RD). Worst-case drop to the farthest ball 4.8 mV |
| VDD1 | U3 OUT (B.Cu) - C507, R509 - 0.3 mm lane west to the east rail (C423 / W1 south to C421 / C1, outside the VDDQ rail), all on B.Cu; **from ball W1 on F.Cu** around the north of U1 to ball W15 and down the west side of U1 to ball C15 (0.25 mm, 32 mm); the capacitors C424 / C422 sit on the via-in-pads of W15 / C15 on the back. ONE layer change, at the ball W1; one via to U2 EN and the test pad TP1. 2.0 mV at 26 mA. There is no 1.8 V pour: 26 mA is plain track, as upstream |
| GND | no pour: every GND pad on a via of its own (15.4). Every capacitor and resistor on a short link and via (C512 / C502 on the finger vias of the VSS pairs 175 / 176 and 180 / 181, R403 on that of 234 / 235); vias in the exposed pads of U2 (4), U4 (2), U5 (2), filled and capped |

### 15.3 RESET_n

Ball H1 - 2.12 mm of F.Cu - ONE via at (28.20, 17.50) - 45.7 mm of B.Cu - R403 / R402 on the back - finger 232 (AP11, bank 66, T0L). The via stands
east of the VDDQ and VDD1 rails of U1, which is why the first 2 mm are on F.Cu and not a via in the ball. No via at the finger end, no In3, no
crossing with a high-speed wire; 2.38 mm from the RDQS pair in plan, 4.8 mm from the read finger tracks, 1.52 mm from VREF copper on its layer; over GND on
In4 all the way, 0.5 mm south of the VDD2H island. Finger 228 (the partner of the former pin, 0.4 mm from the VREF_RD feed via) and the DDR4
RESET_N finger 108 (west of the comb: the run would cross twelve high-speed wires) were not taken.

**One via is the minimum** (user: "there's also a single line that goes from red quickly to teal in the right
side of the chip. why?"). *All on F.Cu:* from H1 the wire has to get east of the 1.2 V strip, which is on F.Cu from the fingers (y = 4.4) to its arm
(y = 21.05) and on into U2, and it starts inside the pocket formed by the eight F.Cu DQ lines, the strip, its arm, PG and the 3.3 V trunk; nothing east of the strip is in the way. *All on B.Cu from a via in the ball H1:* the VDDQ east rail (x = 26.9)
and the VDD1 east rail (x = 27.5) run north-south on B.Cu past row H; RESET_n would have to cross both on their own layer, i.e. each rail would
need a layer change of its own to let a static net save one via. Left as built.
### 15.4 Outer-layer copper: no ground flood, power pours under U1 only

The outer layers carry what a current or a signal needs and nothing else: tracks, pads and, on B.Cu under U1, the VDDQ / VDD2H pours. The upstream
board does the same (its GND zones on the outer layers are two local pours at its switching regulators; its high-speed outer-layer lines refer to
the solid plane 0.11 mm below). A board-wide GND flood with a stitching grid was built once and removed on the user's remark (15.1); the figures
are in section 11.4.

- **Every GND pad has a via of its own net; none relies on a pour** (gate 9 walks all 297 GND pads): 128 with a via in the pad (101 balls of U1, the 24
  decoupling capacitors under U1, the exposed pads of U2 / U4 / U5), 2 with a via whose land sits on the pad (C509, R404), 167 on a track on their own
  layer to a via: the 94 VSS fingers on the tie to the finger via of their pair, 16 balls of U1 on a 0.8 .. 1.1 mm link to a neighbour's via-in-pad, 57 pads of
  parts on stubs of 0.6 .. 2.8 mm (the longest: R403, C502 and the eight reference capacitors, which run down a VSS finger axis to that pair's finger
  via; C504 goes to the GND pin of U2, where its reference is).
- **Every GND via has a job** (gate 9): it serves a GND pad or it is the return via of a signal via within 2 mm (fence vias of the command field,
  return vias of the read vias and of the RDQS via pair). 199 GND vias: 101 in balls, 47 at the fingers, 30 of the command and data fields, 21 of this stage. 393 vias on the board, 253 of them filled and capped.
- **No local GND pour either.** The upstream board has two, around its switching regulators with their inductors and bulk capacitors. The three
  regulators here are small linear ones: the exposed pads of U2 (0.13 W) and U4 (0.16 W) carry their own vias into three planes, and each capacitor
  at a regulator has its GND via within 0.9 mm of its pad.
- **Power pours on B.Cu under U1** (upstream: 0.5 V pours on B.Cu under its BGA), six outlines, 2.8 % of the board area, minimum width 0.20 mm, each one
  filled piece: VDDQ on the four flanks, VDD2H under columns 6 .. 10 of rows A .. F.
  - *Command stub ends.* The three lower pours end at y = 15.65, 0.35 mm south of the B.Cu pads of the command via-in-pads of row G (CA0, CA2, CA4,
    CA6); only two tabs over the VDDQ balls G1 / G2 and G14 / G15 reach row G, 0.8 mm or more from a command via. A DRC rule (`pour_to_command_via`,
    0.30 mm) holds any pour off the vias of the command bus. Gate 9 lists what stands within 0.30 mm of each of the ten stub ends: no pour; the lands of decoupling capacitors
    at 0.16 .. 0.24 mm (CA0, CA2, CA5, CA6, CK_N); and ONE track of this stage, below.
  - *The link F6 - K6* is the only way from the centre pour (rows A .. F) to the island row K: every channel of row H between columns 4 and 14 has a
    command via on one side (H5 CA1, H7 CS0, H9 CK_P, H11 CA3, H13 CA5), In4 under the command bundle must stay GND, and any other route crosses the
    VDDQ flanks. It takes the gap between H5 (CA1) and H6 (GND) and is necked there to 0.12 mm: 0.14 mm of gap to the pad of CA1 over about 0.7 mm. CA1 is therefore the one stub end with a supply track beside it: a few fF to a DC rail,
    estimated at 0.1 ps or less, not simulated. A second link was looked for and does not exist without passing another command via.
  - *WCK exit.* The lower east flank is two outlines with straight edges instead of one rectangle cut by the clearance rule: the enclave (A3, B2, C3,
    D4, joined to the rest by the F.Cu strap D4 - E5) has one edge parallel to the pair, 0.30 mm from WCK_N over 2.4 mm - the balls B2 and D4
    themselves stand 0.11 mm from that leg, so the edge cannot be pulled back to 0.45 mm - and the main part stays north of the pair's exit (nearest
    corner 0.39 mm from WCK_P, not parallel to it). The pour is on one side of the pair only: -0.11 % impedance, -0.008 ps (`scripts/coplanar.py`).
- **Coplanar effect.** None on the outer-layer lines of the command, write and read groups: no copper of another net runs beside them. The delay model
  (`scripts/pcb/common.py`, `scripts/impedance.py`) never contained a coplanar term, and gates 6 and 7 give the same delays with the floods and
  without (641.77 ps on the ten command nets, write / read / RDQS / WCK tables and `design/gateware_deskew.json` identical byte for byte). With
  floods the real lines would be up to 0.2 % faster than the model where a flood flanks them (0.45 ps on the longest 34 mm outer path).
- **Fabrication.** Copper on the outer layers is 16.6 % / 16.0 % of the board area. Fab note 9 says so and forbids thieving and dummy fill inside the
  outline (robber bars on the panel border instead): added copper beside the impedance-controlled lines is exactly what the design avoids.
- **Inner planes:** every plane and island is one filled outline. No via of a net other than the 20 In3 nets has a land on In3.

### 15.5 Checks

- `check_netlist.py`: placements, wires on F.Cu / B.Cu only, every end on a pad or via, one connected piece per track-routed net, the layer-hop rule,
  the strip sizing re-derived with the island expectation, RESET_n, the crossings against the list above; no GND pour, no stitching rule and no pour
  keep-out in the spec; the outlines of the lower pours 0.30 mm or more from every command via; every GND pad of the plan on a GND via.
- `check_pcb.py` gate 9: the plan on the board, zero unconnected items, the DC table measured from the copper, angles, layer purity, the layer-hop
  rule over all nets, the strip, RESET_n, the vias against the constraints of the earlier stages, inner planes, reference planes under the In3 lines.
  The DC table includes the In2 island (sheet solve on the filled copper) and the link F6 - K6. Outer layers: no GND zone and no zone on F.Cu; each
  power pour one filled piece, nowhere narrower than 0.20 mm, 0.30 mm from high-speed tracks and from command vias; the surroundings of the ten
  command stub ends; the GND pad audit (every pad to its via, listed); no GND via without a job, no track piece that touches nothing; copper
  coverage per layer and net against the upstream figures; no via land of a foreign net on In3. Gate 2: no DRC error, the nine pair-gap warnings (where the legs of a
  pair must part) the only warnings, KiCad's own schematic <-> board comparison clean (fields and the nets of unconnected pins are set from the
  schematic's netlist export).

### 15.6 What is not ideal, and what was left to the user

- U3 and U4 on the back (section 8, item 32); the VDDQ lane is 21.7 mm long because U4 stands where its three inputs come to the back.
- The VDD1 link on F.Cu around the north and west of U1 (32 mm of perimeter track) feeds two balls.
- The enclave A3 / B2 / C3 / D4 of VDDQ hangs on one 0.20 mm F.Cu strap (0.9 mm) besides its own capacitor C415.
- The 3.3 V trunk and the VDDQ track to R332 are long thin L-shaped tracks across the open north-east quarter (54 mm and 19 mm).
- **Layer changes of supply and static nets that remain** (the gate counts a via-in-pad of U1 as the ball itself, so it does not flag them):
  - *VDDQ enclave:* B.Cu pour - ball D4 - 0.9 mm F.Cu strap - ball E5 - B.Cu pour. The WCK pair on B.Cu cuts the four balls A3, B2, C3, D4 off from the
    rest of the rail; the only B.Cu path around the ball end of the pair runs up the column 5|6 channel, under CA1 for 0.7 mm and across CA0. The strap
    crosses CA0 once with In1 / In2 between. Kept.
  - *VREF_RD:* F.Cu bus - via - B.Cu past the network of finger 216 - via - F.Cu to TP4 and the DAC network (section 3). Its B.Cu stretch carries
    pads, so it is not a pad-less hop, but the net does go to the back and return. A version without the return needs R331 .. R333, C331 and TP4 on
    the back; not done (static net, 4.3 mm).
  - *VDD1:* B.Cu lane and east rail, then from ball W1 on F.Cu around the north and west of U1: one layer change, at the ball W1 (15.2). A route on B.Cu alone exists only as a 75 mm loop around both back-side regulators and
    along the top edge (VDD1 and VDDQ both come from the east, and one has to get around the other); not taken.
- V1P2 from the fingers to U2 IN drops 12.4 mV for the west-most finger (section 4): the In2 island with its bridge costs 5.2 mV of it; the socket
  contacts add about 2 mV that the 15 mV limit does not contain, and at the 1.14 V corner U2 is in dropout (section 4).
- The VDDQ budget has little margin against its own limit (4.8 of 5 mV; 10 mV against JEDEC at full load).
- The link F6 - K6 is a single 0.12 mm neck for 18 balls (122 mA, 29 A/mm2 over 1.5 mm, +0.4 K) and stands 0.14 mm from the stub end of CA1 (15.4).
  A review proposed to drop the via of ball H6 (VSS) and run the link straight, 0.40 mm wide. Not done: H6 is the ground via between the
  ball vias of CA1 (H5) and CS0 (H7) - row H alternates command and VSS vias - and the 0.12 mm neck is a DC link with margin (1.1 mV, +0.4 K; the
  package also ties its VDD2H balls together). The command bus has priority over a wider supply link. **Left to the user.**
- With no flood and no stitching grid, the planes In1 / In2 / In4 are tied together only where GND vias exist for another reason (U1: 101, finger
  row: 47, the command and data fields: 30, the parts of this stage: 21). East of x = 47 and north of y = 13 there are six, west of U1 none. The planes are solid
  and 0.36 / 0.11 mm apart, the same arrangement as upstream; plane resonances were not simulated (an unstitched 69.6 mm cavity has its first mode
  near 1.0 GHz). A dozen plane-to-plane vias in the empty west end and north-east quarter would be free, but each would be a via without a job
  (15.4, the user's remark on copper without a purpose): **left for the user to decide.**
- The return via of a command node via is its fence via, 1.70 mm away (four of the ten nets also change reference from In1 to In2 there): about
  0.2 nH, part of the HFSS launch model of the command bus (section 8, item 3).
- The Vivado pin check passes in the 2026.1 release, but it is a legality check (section 8, item 31).
- Silkscreen text is 0.5 mm high with a 0.08 mm stroke (below the usual 0.8 / 0.12): larger text does not fit between the 0201 fields; every
  reference is complete on the Fab layers and in the assembly drawings. The finger-number labels are 2.2 .. 2.6 mm inboard of the fingers they name.
- The SK hynix data sheet is missing (section 8, item 1): at a 1.3 mm body the front face would exceed the 1.20 mm per-face figure by 0.1 mm.

## Appendix A. History

Kept short on purpose; the sections above describe the board, not how it got there.

| Step | What it settled | What it replaced |
|---|---|---|
| Research and plan | pseudo-differential reads on MIPI D-PHY / SLVS receivers; dividers; bank plan 65 / 64 / 66 (user); 0.36 mm cores (user) | |
| Design audit (revision 0.2) | U1 at y = 19.0 for the tuning zone; all field resistors 0201; fence vias on the ball-column lines; TPS74801DRCRM3; bleeders R508 / R509; project MLF-6 footprint; both outer-layer copper corners solved | U1 at y = 15.0; an enable link; a stock DFN-6 footprint |
| PCB skeleton (s00 / s10 / s90) | every part placed; decoupling across two columns; one VSS via per finger pair at y = 3.05, no signal via in the finger row; pair nets named _P / _N so that KiCad sees pairs; spacing rules in the .kicad_dru | read vias in the finger row; a U-turn read route with accordions |
| Command bus (s20) | electrical length with the HFSS bend model; parallel shifts and ten bumps of 0.38-0.74 mm; node vias filled and capped; unused inner pads removed beside the traces | 22 small bumps tuned by centre-line length, which arrived up to 4 ps early |
| Data side, first routings | write and read groups matched in copper with a T on the line | 166 crossings, wires through resistor groups, three vias per column |
| User: no read taps; roll back spacings | ten 22 ohm resistors removed; upstream spacings restored (section 11.3) | |
| User: T at the divider (comb) | the node via is the T; write group +/-26.7 ps, in gateware | write group matched in copper |
| User: fewer crossings between layers | read end without crossings; read group +/-56.8 ps, in gateware; RDQS on fingers 219 / 221 | read group +/-9.7 ps with 20 read-end crossings, RDQS on fingers 198 / 200 |
| Power stage (s50) | 1.2 V strip instead of a pour; U3 / U4 on the back; RESET_n on B.Cu to finger 232 with one via; no outer ground flood, no stitching grid; the layer-hop rule | a corridor pour; RESET_n with a hop on In3 to finger 229; two board-wide GND floods with 53 stitching vias |
| Final review and fix pass | RDQS on In3 down to a via pair above its fingers (before: a via pair at x = 49 and 20 mm on F.Cu, 17.8 ps earlier); pull-down arrays RN401 / RN402 of the unused byte removed (command bundle 30 -> 15 crossings); VDD1 west rail on F.Cu (one layer change); the eight exposed-pad vias filled and capped; six fiducials; mask strip to y = 2.75; drill / place origin; fab notes 6-13; one-command build on a clean tree; gateware hand-off with the Vivado 2026.1 pin check | |
| Release | `scripts/make_fab.py`, `scripts/check_fab.py`, `fab/`; this file restructured; `doc/img/` | |

## Appendix B. Review findings that were checked and not changed

| Finding | Decision |
|---|---|
| VDD2H link F6 - K6: drop the via of H6 and widen the link (layout review, major) | not done: H6 is the ground via between the CA1 and CS0 ball vias; the neck is a DC link with margin (15.6) |
| Strap WCK1 to its neighbour balls instead of R404 / R405 | not done: either is correct; the resistors keep rework access and stage s20's surroundings stay as simulated (section 5) |
| Plane-split channels 0.10 -> 0.20 mm (mechanical review) | not done: it moves the In4 GND edge under the J9 command via or makes the island tangent to the via pads of row K; 0.10 mm is the gap of every pair and antipad; the bare-board netlist test (fab note 10) catches a short |
| VREF_RD goes to the back and returns | documented (15.6); a version without it needs the DAC network on the back |
| VDDQ strap D4 - E5 | no same-layer route without running under the command traces (15.6) |
| RESET_n on one layer | none exists (15.3) |
| VDD1 on one layer only | only as a 75 mm perimeter loop (15.6); the rail now changes layer once |
| Plane stitching vias in the empty quarters | left to the user: such a via has no other job (15.6) |
| Unused inner pads of the ten command node vias | kept (stage s20 as simulated); section 6 and fab note 7 say so |
| Silkscreen text 0.5 mm high / 0.08 mm stroke (below the usual 0.8 / 0.12) | left: larger text does not fit between the 0201 fields; references are complete on the Fab layers. The finger-number labels are 2.2 .. 2.6 mm inboard of the fingers they name |
| Stock footprints copied into the project library; project footprints saved in KiCad 10 syntax | not done: KiCad 10 reads both unchanged, and the installed library has the same names; noted in section 9 |
| Separate drill tool for the ten filled 0.20 mm node vias | not done (it would change stage s20): fab note 6 lists every filled via by position, the board file carries the attribute |
| README.md describes an earlier plan (bank 64 for commands, 16 DQ, PWR_EN / PGOOD fingers) and `.ci.yml` names the upstream CI template | the user's files: not edited, reported; a replacement text for the README is in `design/README_PROPOSED.md` |
| No data sheet of H58G56BK7BX068N (ball map, body height 1.1 or 1.3 mm) | open since the start (section 8, item 1): at 1.3 mm the front face is 0.1 mm over the 1.20 mm per-face figure |
| Bank 65 for the command bus: why (now in section 1) | host + package median 527.7 ps for bank 65 against 610.4 (bank 64) and 601.6 (bank 66): the brief asks for the bank with the least routing delay. Byte group T2 of bank 65 is 9 ps faster than T0 but lies at fingers 3 .. 21, 20 mm west of U1; T0 (fingers 66 .. 84) is under U1 |
| Plan-view overlap of command finger stubs on opposite faces (CA3_F / CA4_F and CA2_F / CS0_F 1.25 mm each, CA5_F / CA6_F and CA1_F / CA0_F 0.73 mm each: 3.97 mm) | given by the socket: front and back fingers are 0.25 mm apart; In1, In2 and In4 lie between. Recorded here; the tool counts it in "overlap command" (gate 8) |
| DRC severities set to "ignore" | six checks; enabling them adds two harmless items: J1 has no courtyard (it is the card edge) and the end of the 1.2 V strip is not centred on one of its four vias |

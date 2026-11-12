# Project footprint library

`lpddr5_testbed.pretty/` holds the four footprints that are not taken from the stock KiCad libraries. All are
written by `scripts/gen_footprints.py` (plain Python, deterministic output, fixed UUIDs) and verified by
`scripts/check_footprints.py` (pcbnew API, exits non-zero on any failed check, writes `lib/render/*.png`).
Do not edit the `.kicad_mod` files by hand; change the constants in the generator and re-run both scripts.

| Footprint | What it is |
|---|---|
| `DDR4_SODIMM_260_Edge` | 260-finger DDR4 SO-DIMM module edge **and the complete card outline** (Edge.Cuts), tooling holes, mask-free finger strip, component keep-outs |
| `BGA-315_12.4x15mm_P0.8x0.7mm` | LPDDR5 / LPDDR5X 315-ball FBGA land pattern |
| `MLF-6-1EP_2x2mm_P0.65mm_EP0.8x1.4mm` | 6-lead 2 x 2 mm MLF with the 0.80 x 1.40 mm exposed pad of the MIC47050YML |
| `Fiducial_1mm_Mask2mm` | assembly fiducial: 1.0 mm copper dot in a 2.0 mm solder-mask opening, no paste, no silk (FID1 .. FID6, three per face) |

Every other part uses stock footprints from `/usr/share/kicad/footprints`.
Library nickname to use in `fp-lib-table`: `lpddr5_testbed` -> `${KIPRJMOD}/lib/lpddr5_testbed.pretty`.

## 1. Coordinates

**Card frame** (used in all scripts and in this document): millimetres, module seen from the **front**
(the face with the odd fingers; **pin 1 is at the left**, pin 259 at the right).

- origin = lower-left corner of the 69.6 x 30.0 card rectangle (pin-1 end of the finger edge)
- +x along the finger edge from pin 1 towards pin 259
- **+y away from the fingers** (finger edge y = 0, top edge y = 30)
- back-side items use the same x, i.e. they are described as seen *through* the card from the front.
  Pin 2 is 0.25 mm to the right of pin 1; nothing is mirrored.

**KiCad** has y pointing down. The edge footprint origin is the card origin and it is placed on F.Cu at
0 degrees, so with the footprint at board position `(ox, oy)`:

    kicad_x = ox + card_x
    kicad_y = oy - card_y

Inside the `.kicad_mod` file every y is therefore the negative of the card y (the card occupies local
x 0..69.6, y -30..0). Helpers in `scripts/gen_footprints.py` (import it from other scripts):

| Function / constant | Meaning |
|---|---|
| `finger_xy(n)` | centre of finger pad n, card frame: `(x, 1.425)` |
| `finger_top_xy(n)` | top end of the finger (trace exit), `(x, 2.70)` |
| `finger_x(n)`, `finger_side(n)` (`'F'`/`'B'`), `finger_layer(n)` | |
| `card_to_kicad(x, y, origin)`, `kicad_to_card(x, y, origin)` | the transform above |
| `component_area()` | polygon (card frame) for component bodies, same on both faces |
| `max_component_height(y)` | 0 below 4.00, 0.80 for 4.00..5.10, 1.20 above |
| `card_outline()` | outline segments, card frame |
| `ball_xy('H9')`, `ball_names()` | BGA ball centre in the footprint frame (KiCad, y down, top view) |

Finger centre x: odd n <= 143: `1.425 + 0.25 (n-1)`; even n <= 144: `1.675 + 0.25 (n-2)`;
odd n >= 145: `39.425 + 0.25 (n-145)`; even n >= 146: `39.675 + 0.25 (n-146)`.

## 2. DDR4 SO-DIMM edge: dimensions and sources

Sources (under `references/`):

- **[FOX]** `socket/Foxconn_DDR4_SODIMM_socket.pdf` sheet 2, "DDRIV SO DIMM MODULE BOARD LAYOUT" (drawing
  317-0000-2253 rev D). It reproduces the JEDEC MO-310 module outline with tolerances and is the governing
  source here. Read from the rendered page (the `.txt` loses the associations).
- **[MIC]** `sodimm_module/Micron_atf8c1gx64hz_DDR4_SODIMM_RevJ.pdf` Fig. 3 (p. 24), JEDEC-derived module drawing.
- **[PRE]** `sodimm_module/antmicro_lpddr4-testbed/lpddr4-testbed.kicad_pcb`: an existing, working LPDDR4
  test card in DDR4 SO-DIMM form; its edge geometry was extracted from the file and compared.
- **[KIC]** stock KiCad `Connector_PCBEdge:SODIMM-260_DDR4_H4.0-5.2_OrientationStd_Socket` (TE 2309407 socket).
- JEDEC MO-310 itself and the drawing of the ZCU104 socket (LOTES ADDR0067-P001A) are not available
  (`sodimm_module/MODULE_SIDE_REVIEW.md` section 1.2).

| Item | Value in the footprint | Source |
|---|---|---|
| Card outline | 69.60 x 30.00 mm | [FOX] 69.60 +/-0.15, 30.00 +/-0.15; [MIC] 69.47-69.73, 29.87-30.13 |
| Thickness at the fingers | **1.20 +/-0.10 mm including fab tolerance** (stackup phase must meet this) | [FOX] 1.20 +/-0.10, flatness 0.15; [MIC] 1.1-1.3 |
| Fingers | 130 front (odd, F.Cu) + 130 back (even, B.Cu) | [FOX] pin labels on front/back views; [MIC]; [PRE] |
| Pin 1 | front, **left** end, centre 1.425 from the left edge | [FOX] "PIN #1 (1.425)"; [PRE] 1.425 |
| Pin 2 | back, same end, centre 1.675 from the same edge (front view) | [FOX] back view (drawn flipped top-to-bottom, so left stays left) "(1.675) PIN #2"; [MIC] back view (flipped left-right) shows pin 2 at the right; [PRE] 1.675 |
| Pin 259 / 260 | 67.925 / 68.175 = 1.675 / 1.425 from the right edge | [FOX] "(1.675)" front, "(1.425)" back |
| Pitch | 0.50 per face, front/back offset 0.25 | [FOX] detail B 0.50; [MIC] 0.5 TYP; [KIC] |
| Spans | 1..143 and 2..144 = 35.50; 145..259 and 146..260 = 28.50; 143->145 and 144->146 = 2.50 | [FOX] basic dimensions on both views; [MIC] 35.5 / 28.5 / 2.5 TYP |
| Finger width | 0.35 | [FOX] 0.35 +/-0.03; [MIC] 0.35 TYP |
| Finger length | **2.55** (pad 0.15 .. 2.70 above the card edge) | [FOX] detail B: 2.55 is the pad length and the pad starts "0.30 MAX" above the edge; [MIC] 2.55 TYP |
| Key slot | x 37.80 .. 38.80 (centre **38.30**), 4.00 deep, full radius R0.50 | [FOX] detail A: 1.00 +/-0.05 wide, 4.00 +/-0.10, "FULL R", centre = pin 143 + 1.375 = pin 144 + 1.125; [MIC] 38.3 TYP, 1.0 TYP; [PRE] 37.8..38.8, 4.0 |
| Nearest copper to the key | finger 144 edge at 37.35 (0.45 from the slot), finger 145 edge at 39.25 (0.45) | derived |
| Latch notches (both sides) | y 18.00 .. 22.00, 1.75 deep, inner corners R0.65 | [FOX] 18.00 +/-0.10, 4.00 +/-0.10, 1.75, R0.65; [MIC] 18.0, 4.0, 1.75, R0.65 (4X); [PRE] same |
| Tooling holes | NPTH 1.80 at (2.00, 6.00) and (67.60, 6.00) | [FOX] dia 1.80 (2X), 6.00, spacing 65.60; [MIC] 1.8 TYP, 6.0, 2.0; [PRE] same |
| Corners | 0.60 x 45 deg chamfer, all four | [MIC] "0.6 x 45deg (4X)" |
| Solder mask | open strip y -0.3 .. 2.75 over the full width on F.Mask and B.Mask (pads also carry their own mask layer, which ends at 2.75 too; the lands of the finger vias at y = 3.05 keep a 0.10 mm mask dam) | fab guides in `sodimm_module/fab_guides/` (no mask between contacts) |
| Finger-edge chamfer | **not drawn**; fab note on Cmts.User: 0.25 mm x 45 deg max per face or none, no gold-finger bevel, copper to within 0.30 mm of the edge | [MIC] 0.25 x 45deg (2X); [FOX] "C 0.25X0.05 MAX 2X OPTIONAL"; `MODULE_SIDE_REVIEW.md` M2-M4 |
| Component area (identical on both faces) | y >= 4.00; side margin 3.00 up to y 21.35 then 45 deg to 2.00 from y 22.35 to the top edge; 4.00-wide zone around the key free up to 4.20 | [FOX] plan views "3.00 MIN (4X)", "2.00 MIN (4X)", side view "4.00 MIN (2X)", detail A "4.20 MIN / 4.0 MIN", 4.00 |
| Component height | **<= 0.80 mm for bodies between y 4.00 and 5.10; full height from y 5.10** | [FOX] side view "0.80 MAX (2X)", "5.10 MIN (2X)" |
| Full component height | module total 3.70 MAX; **1.20 mm per face** used here = (3.70 - 1.30) / 2 | [FOX] 3.70 MAX; [MIC] 3.7 MAX. The per-face split is not dimensioned; the side view is symmetric (derived value) |

How the keep-outs are stored:

- `Dwgs.User`: the component-area boundary and the y = 5.10 line with the two height notes.
- Three rule areas on F.Cu + B.Cu named `SODIMM_no_components_*` with **footprints not allowed**
  (tracks, vias, pads and pours are allowed). KiCad DRC reports `items_not_allowed` for a front or back
  footprint whose courtyard enters them (tested with 0402 parts on both faces).
- Heights cannot be checked by KiCad; later scripts use `max_component_height()`.
- `Cmts.User`: six fabrication notes below the card.

Board rules this footprint needs (set in the project, not in the footprint): copper-to-edge clearance
<= 0.15 mm for the fingers (the reference design uses 0.01), copper clearance <= 0.15 mm (finger gap).
The footprint has no courtyard on purpose. It carries the attribute `allow_soldermask_bridges`: the mask-free strip
over the finger row joins the openings of all fingers by design (without it KiCad DRC reports one solder-mask-bridge
error per finger pair).

### Discrepancies between sources, and what was chosen

1. **Finger length and start.** [FOX] detail B dimensions the pad itself as 2.55 long, starting no more
   than 0.30 above the card edge. [PRE] uses 0.35 x 2.50 pads from 0.05 to 2.55; an earlier revision of this
   project used 2.35 (0.20 .. 2.55). Chosen: the full 2.55 length starting at 0.15 (0.15 .. 2.70), which
   meets the 0.30 MAX and keeps copper off the routed edge.
2. **Finger x in the precedent card** differs by up to 2 um (36.923 / 67.924 instead of 36.925 / 67.925):
   rounding in that file. The drawing values are used; they match the stock KiCad socket footprint exactly.
3. **Corners.** [MIC] 0.6 x 45 deg (4X), [FOX] square, [PRE] R0.75. Chosen: [MIC]. Either fits the socket.
4. **Key-mouth break** "C OR R 0.25 MAX, 2X OPTIONAL" ([FOX] detail A) is optional and not drawn.
5. **Front/back component area.** The digest in `MODULE_SIDE_REVIEW.md` (M8, M9) reads "front from 5.10,
   back from 4.00; side 3.00 front, 2.00 back". On the rendered drawing both plan views start at 4.00
   and have the 3.00 margin below the latch notch and the 2.00 margin above it, and every dimension is
   marked 2X or 4X (both faces). The footprint follows the drawing: both faces identical.
6. **[FOX] basic dimension 3.50** next to the key in the front view could not be assigned to a card
   feature (it is not the key or a finger dimension) and is not used.
7. **Precedent card top cut-out** (a 6 mm wide recess at the top centre of [PRE]) is specific to that
   board, not part of the module outline, and is not reproduced.
8. **Edge chamfer.** [MIC] 0.25 x 45 deg, [FOX] "0.25 x 0.05 MAX" (probably 0.25 long); both are far less
   than a fab's standard gold-finger bevel. See the fab note.

## 3. BGA-315

Sources: **[Y42M]** `dram/Micron_Y42M_LPDDR5X_315b_441b_563b_RevF.pdf` Fig. 11 (ball map) and Fig. 14
(package drawing), JESD209-5C 16.3.2 (315-ball 2CHx32, MO-338A), **[CSN]** `micron/CSN-33` Rev G.
The target part SK hynix H58G56BK7BX068N is assumed to use this JEDEC outline and map (PROMPT.md).

| Item | Value | Source |
|---|---|---|
| Body | 12.4 (x) x 15.0 (y) mm | [Y42M] 12.4 +/-0.1, 15 +/-0.1 |
| Grid | 15 columns x 21 rows, fully populated = 315 | [Y42M] Fig. 11/14 |
| Pitch | **0.8 mm between columns (x, short side), 0.7 mm between rows (y, long side)**; matrix 11.2 x 14.0 | [Y42M] 0.8 TYP / 11.2 CTR, 0.7 TYP / 14.0 CTR; JEDEC "0.8 mm pitch (X-axis) 15 columns, 0.7 mm pitch (Y-axis) 21 rows" |
| Rows | A B C D E F G H J K L M N P R T U V W Y AA (no I, O, Q, S, X, Z; the last row is **AA**, there is no AB) | [Y42M] Fig. 14 |
| A1 | top-left in top view: A1 at (-5.6, -7.0), AA15 at (+5.6, +7.0), L8 at the centre | [Y42M] "Ball A1 ID" |
| Land | 0.41 mm round copper, non-solder-mask-defined, mask opening 0.51 (margin 0.05), paste 1:1 | kept from the existing board; see below |
| Courtyard | body + 0.25 | as on the existing board |

Land size against Micron guidance: the package has 0.48 mm solder-mask-defined ball pads with 0.475 mm
balls ([Y42M] Fig. 14). [CSN] (PCB design guidelines table) recommends for a package SMD pad and a PCB
NSMD pad a ratio of 1 : 0.8, i.e. about **0.38 mm**, and says the PCB land must never be larger than the
package pad; where the ball is smaller than the package opening a 1 : 1 ratio may be better. The 0.41 mm
land is 0.85 of the package pad: inside the allowed range, 0.03 mm above the 80 % figure. It was kept as
requested (it is proven on the existing board). Changing `BGA_LAND` to 0.38 would add 0.03 mm to the
0.29 mm row-to-row copper gap; [CSN] also recommends teardrops on NSMD pad exits and a 1:1 stencil aperture.

Differences from the footprint embedded in the original board: same land pattern and ball names; renamed,
author / licence / MPN / manufacturer fields and the 3D-model reference removed, mask margin made
explicit, BGA pad property set, silkscreen redrawn (A1 triangle outside the body, beside the column-1 edge at row B / C: the byte-0 trunks pass the A1 corner itself).

## 4. MLF-6 2 x 2 mm (U4, MIC47050YML)

Source: MIC47050 data sheet page 16, "6-Pin 2mm x 2mm MLF (ML)" (`references/parts/Microchip_MIC47050.pdf`):
body 2.000 BSC square, 0.850 high, pitch 0.650 BSC, leads 0.250 x 0.350, exposed pad 0.800 x 1.400 (all +/-0.050).

The stock KiCad `Package_DFN_QFN:DFN-6-1EP_2x2mm_P0.65mm_EP1x1.6mm` has the right leads and numbering but a
1.0 x 1.6 mm exposed pad, 0.2 mm larger than the package pad in both directions, which leaves 0.225 mm of copper
between the pad and the lead lands. This footprint keeps the stock lead lands (0.65 x 0.35 at x = +/-1.05, pin 1
top-left, counter-clockwise) and makes pad 7 exactly 0.80 x 1.40 (copper gap 0.325 mm), with two 0.64 x 0.52 paste
apertures (59 % coverage).

## 5. Checks

    python3 scripts/gen_footprints.py
    python3 scripts/check_footprints.py        # prints the table, asserts, renders lib/render/*.png

The check script asserts the literal drawing numbers (not the generator's constants), builds the board
outline polygon from Edge.Cuts with pcbnew and probes the key slot, notches and corners point by point,
compares the finger pattern with the stock KiCad DDR4 SO-DIMM socket footprint (identical) and with the
precedent card (within 2 um), checks the MLF-6 against the package drawing numbers and the stock lead positions,
and scans the files for leftover vendor branding.

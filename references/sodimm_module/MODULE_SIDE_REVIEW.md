# LPDDR5 testbed rev 2.1.0: module-side (edge-card) review against DDR4 SO-DIMM sources

Date: 2026-10-02. Scope: the card as a JEDEC MO-310 260-pin DDR4 SO-DIMM module (edge, fingers, keep-outs, plating,
holes, power fingers, SPD). Design measured read-only with the pcbnew API from `kicad/lpddr5-testbed.kicad_pcb`
(finger edge y = 100.000, sides x = 99.975 / 169.575). The mechanical items already settled in
`doc/DRC_MECHANICAL_REVIEW_rev2.1.0.md` (outline, key, latch notches, finger size, thickness tolerance) are only
re-confirmed here, not repeated. Machine-readable list: `index.json`.


> **rev 1.0.0 status (2026-10-03):** M11 is fixed. The J1 NPTH holes are now 1.80 mm at 2.00 mm from each side and
> 6.00 mm from the finger edge (`validation/eco_20261003_changes.json`). M8 no longer applies: R152 moved off the
> finger edge, 13 mm up the RDQS1_P line. M2/M3: the order notes ask for a chamfer of at most 0.25 mm x 45 deg with no
> copper pull-back beyond 0.30 mm, or no bevel (`fab/README_ORDER.md`).

## 1. Documents

### 1.1 Saved here (9 PDFs, 4.1 MB; 6 fab-guide web pages as .html + .txt; one KiCad reference design; about 11 MB in total)

| File | Title / version | Source (retrieved 2026-10-02) | Used for |
|---|---|---|---|
| `Micron_atf8c1gx64hz_DDR4_SODIMM_RevJ.pdf` | Micron 8GB 260-pin DDR4 SODIMM, Rev J 3/21 | https://cdn.eicom.ru/media/PDF/8078788.pdf | Module drawing (Fig. 3): 1.1-1.3 thick, 3.7 MAX, **edge chamfer 0.25 x 45 deg (2X)**, holes 1.8 (2X) at 6.0 / 2.0 |
| `TE_114-115015_AppSpec_DDR4_SODIMM.pdf` | TE application spec 114-115015 Rev A, 2017-08-04 | https://www.farnell.com/datasheets/2371056.pdf | Module must meet MO-310 dimensions; cam-in insertion; latch handling |
| `TE_2309408-2_datasheet.pdf`, `TE_2309413-4_datasheet.pdf`, `TE_2309407-1_octopart.pdf`, `TE_DDR4_SODIMM_260p_catalog_drawing.pdf` | TE DDR4 SODIMM socket product data (4.0 / 9.2 mm) | docs.rs-online.com A700000015405084 / A700000015405108; datasheet.octopart.com; media.onlinecomponents.com | 0.5 A per contact; mating plating gold flash / 5 / 15 / 30 uin |
| `Amphenol_DDR4_SODIMM_lcsc.pdf` | Amphenol CS DDR4 SO-DIMM flyer, 2022-11 | atta.szlcsc.com (892DDC6B...) | 0.5 A per contact; spec GS-12-1397 |
| `Microchip_34AA04_DDR4_SPD_EEPROM.pdf`, `FMSH_FM34C04D_SPD_EEPROM.pdf` | EE1004-v SPD EEPROMs (2014, 2017) | atta.szlcsc.com (9546B2EE...); static.chipdip.ru DOC013496631 | SPD addressing 0x50-0x57, SPA/WP 0x30-0x37 |
| `fab_guides/` Eurocircuits (x2), PCBWay, JLCPCB blog, Advanced Circuits, Lion Circuits | Edge-connector / gold-finger guides | URLs in `index.json` | Bevel tables, inner-layer clearance, hole/pad keep-out, hard gold vs ENIG |
| `antmicro_lpddr4-testbed/` | Antmicro lpddr4-testbed (LPDDR4 in DDR4 SO-DIMM form), main @ 93e67cf 2025-11-05, Apache-2.0 | https://github.com/antmicro/lpddr4-testbed | Practical comparison card |
| `foxconn_317-0000-2253_contact_point_estimate.png` | 1200 dpi crop of the Foxconn socket section, annotated | derived from `../socket/Foxconn_DDR4_SODIMM_socket.pdf` | Finding M4 |

Also used (already in `references/`): `socket/Foxconn_DDR4_SODIMM_socket.pdf` (317-0000-2253 rev D; sheet 2 is the
MO-310 "DDRIV SO DIMM module board layout"), `socket/DDR4_SODIMM_socket_comet28777.pdf` (Attend 124B-150A02),
`zcu104/RDF0437_ZCU104_BOM_rev1.0.txt` (host J1 = **LOTES ADDR0067-P001A**, shielded), `zcu104/UG1267...txt` (SODIMM
I2C = TCA9548A 0x74 channel 7, SPD at 0x51), `parts/` regulator data sheets, `doc/POWER_REVIEW_rev2.1.0.md` (loads).

### 1.2 Not obtainable

| Document | URL / note |
|---|---|
| JEDEC MO-310C (2014), MODULE4.20.25 Rel. 29 (2019) + raw-card annexes A/C/D/G/K, MODULE4.20.26, SO-018D socket outline, SPD4.1.2.L-5, EE1004-v, TSE2004av | jedec.org (e.g. https://www.jedec.org/node/8562); free with registration, HTTP 403 to scripts. MO-310 dimensions are reproduced by Foxconn sheet 2 and Micron Fig. 3 |
| TE product spec 108-115122, TE customer drawings 2309407/2309408 | te.com DDEController (JS stub). Search abstract of 108-115122: card 1.20 +/-0.10, 25 cycles |
| Amphenol GS-12-1397 + drawings | cdn.amphenol-cs.com behind Cloudflare |
| LOTES ADDR0067-P001A drawing (the actual ZCU104 socket) | no public copy |
| Molex DDR4 SODIMM PS; Sierra Circuits (404); NCAB; SK hynix / Samsung / Kingston module design notes | not found / not public |
| Socket contact-point (wipe) position | not dimensioned in any public drawing; estimated in M4 |

## 2. Findings

Verdicts: **OK**, **ISSUE** (rule not met, or met only by luck, and not raised before), **CHECK-ON-HARDWARE**, **N-A**.
"NEW" = not in the DRC/mechanical, vendor-guideline or power reviews, or corrects them.

| # | Module-side rule (source, section) | Design value (measured) | Verdict | New? |
|---|---|---|---|---|
| M1 | Outline 69.60 x 30.00 +/-0.15, key 38.3 from left, notches 18.00/4.00/1.75 R0.65, fingers 0.35 +/-0.03 at 0.50, top 2.55, pin1 1.425 / pin2 1.675 (Foxconn sh.2; Micron Fig. 3) | As in the DRC review; pin 1 F.Cu at 1.425, pin 2 B.Cu at 1.675, 130+130 pads 0.35 x 2.35 | OK | no |
| M2 | **Finger-edge chamfer: "C 0.25 X 0.05 MAX, 2X OPTIONAL"** (Foxconn sh.2 side view); "0.25 x 45 deg (2X)" (Micron Fig. 3). A real module has only a 0.25 mm 45-deg edge break, about 0.7 mm of edge thickness left | Ordered: JLC "gold fingers, 45 deg bevel". Fab bevels remove far more: PCBWay table (1.6 mm card, 0.5 mm left) bevel length 0.50 mm at 45 deg, 0.95 mm at 30 deg (+/-5 deg, +/-0.13 mm); JLC copper setback 0.74 mm (45 deg) / 1.13 mm (30 deg, JLC's recommended angle) for 1.6 mm. Scaled to 1.2 mm with 0.5 mm left: 0.35 mm (45 deg) / 0.61 mm (30 deg) of bevel, up to 0.55 / 0.88 mm with tolerances | **ISSUE** | **NEW** |
| M3 | Pad starts no more than 0.30 mm from the card edge ("0.30 MAX", Foxconn sh.2 detail B) | Design 0.20 mm: compliant as drawn. After a JLC cut-back of 0.55-1.13 mm the finished card no longer meets MO-310 | **ISSUE** (with M2) | **NEW** |
| M4 | The socket contact must land on gold at full seating (no public contact-point dimension) | Estimated from the Foxconn 317-0000-2253 section at 1200 dpi (scale from the 1.2 mm card, 80 px/mm; free-state profile, +/-0.2 mm): one contact row touches **about 0.9 mm** from the seated card edge, the other about 2.1 mm (`foxconn_..._contact_point_estimate.png`). Copper left: 30 deg / 1.13 mm cut-back puts the 0.9 mm row on the bare bevel, so that whole row **opens**. 45 deg / 0.74 mm leaves about 0.16 mm of margin; 0.60 mm (the order remark) leaves about 0.30 mm. JEDEC's 0.25 mm chamfer leaves about 0.65 mm. Gold left (to 2.55 mm): 1.42 mm at 30 deg, 1.81 mm at 45 deg, 2.30 mm with the JEDEC chamfer | **ISSUE + CHECK-ON-HARDWARE** | **NEW** (the DRC review assumed "the socket still mates" at 1.95 mm) |
| M5 | Finger plating: socket mating areas carry gold flash to 30 uin (TE 2309407/8/13, Foxconn note); edge contacts need hard gold 0.5-1 um over Ni (Advanced Circuits: ENIG "will degrade quickly" in edge connectors; PCBWay: immersion gold is 1-3 uin) | ENIG (JLC gold fingers are ENIG). Socket durability 25-60 cycles (Attend 25, FCI 25, TE 25, Foxconn 60) | CHECK-ON-HARDWARE | partly (the DRC review flagged it; sources now attached) |
| M6 | Hard-gold electroplating needs a plating bus/tie bars or a bus-less process (fab guides) | No bus; not needed for ENIG | N-A (next revision only) | NEW |
| M7 | Card 1.20 +/-0.10 (Foxconn sh.2, Micron 1.1-1.3, TE 108-115122 abstract); **flatness "- 0.15 (M)"** on the card at the fingers (Foxconn sh.2) | 1.194 mm nominal (covered). Copper fill by layer: F 26 %, In1 89, In2 6, In3 **5**, In4 89, In5 **12**, In6 **89**, In7 7, In8 88, B 33 %. The In3/In6 and In4/In5 mirror pairs are unbalanced (the halves are balanced: 215 vs 229 %). 0.5 oz inner copper limits the bow. JLC standard bow/twist is 0.75 % (0.52 mm over 69.6 mm) | CHECK-ON-HARDWARE | **NEW** (flatness) |
| M8 | Component area: front from 5.10 mm, back from 4.00 mm (plan views); side view: **0.80 mm MAX height per side between 4.00 and 5.10 mm** from the edge (Foxconn sh.2) | Back R152 0201 (0.26 mm) body at 4.88 mm, the only back part below 5.10. Front R151 0201 body at 5.23 mm (courtyard 5.055). Back parts are all at most 0.8 mm tall (0402, 0201, EXB28V, WSON-6) | OK | **NEW** (height band not checked before; `keepout_check.py` used pads only) |
| M9 | Side keep-out: front 3.00 MIN, back 2.00 MIN (Foxconn sh.2), latch region | Front: R153 body 3.18, C112 body 3.35 (courtyard 2.96). Back: **R163 and R165 at exactly 2.000** (pad and body), R160 2.02; R174/R168 at 2.25 (0.50 from the notch) at 18.9/21.4 mm, which is the latch height | OK (zero margin) | NEW |
| M10 | Module thickness 3.7 MAX (Foxconn sh.2, Micron); socket bottom clearance 1.4 MIN for the 5.2 mm socket (Foxconn sh.1) | Front tallest parts: U1 about 1.1, SC-70 1.1, VSON 1.0, 22 uF 0603 about 0.95. Back at most 0.8. Total at most 1.1 + 1.3 + 0.8 = 3.2 | OK | partly |
| M11 | Tooling holes **1.80 (2X) at 6.00 mm from the edge, about 2.0 mm from the sides** (Foxconn sh.2; Micron "1.8 TYP (2X)", 6.0, 2.0); Antmicro: 1.8 at (2.0, 6.0) | NPTH **1.5 and 1.6 mm at 1.10 / 1.115 mm from the sides**, 6.0 from the edge (`gen_edge_conn.py`: "mounting NPTH 1.5/1.6"; these look like the socket's PCB peg sizes) | CHECK-ON-HARDWARE | **NEW** |
| M12 | No vias, holes, test points or silk in the finger area; fab guides keep PTH/pads at least 1.0 mm (JLC, PCBWay) or 2.0 mm (Eurocircuits) from the finger inner edge | None inside 2.55 mm. Silk at 2.84 mm or more (J1 outline). **74 vias within 1.0 mm of the finger tops** (43 GND, 14 +1V2, DMI1, PWR_EN, RESET, PWRGD); the closest annulus is 0.10 mm above a finger top. 161 within 2.0 mm. Antmicro's card has 61 within 1.0 mm. Vias tented + POFV | OK (deviation from the generic fab rule; this is normal module practice) | NEW |
| M13 | Inner-layer pull-back from the bevelled edge: 1.00 mm at 45 deg, 2.00 mm at 30 deg (Eurocircuits); "> 3 mm" (PCBWay) | Planes 1.00 mm. **Inner signal minimum is 1.38 mm** (In5 `LPDDR5_IN_A.CA0`, x 135.3-137.4), not 1.955 as the DRC review states. Geometry check: In1/In8 lie about 0.16 mm under the surface, so even a 30 deg bevel exposes them only within about 0.33-0.57 mm of the edge | OK (corrects the DRC review) | **NEW** |
| M14 | Mask fully open over the fingers, continuous, no mask/silk on contacts (PCBWay, JLC) | Open from the finger tops (97.45) past the edge on both sides, split at the key | OK | no |
| M15 | Power and ground fingers at the JEDEC positions; at most 0.5 A per contact (Foxconn note 1.1, Attend 1.1, TE, Amphenol) | All 19 VDD to +1V2, VPP 257/259 to +2V5, VDDSPD 255 to +3V3, all 94 VSS to GND; VTT 258 and VREFCA 164 open. +1V2 worst case 1.76 A (U21 1.32 + 0.035, U22 0.40 + 0.006; datasheet-maximum case) = **93 mA per finger** (BO case about 58 mA); +2V5 0.094 A / 2 = 47 mA; +3V3 (TPS74801 bias 2 x 2 mA, TPS7A84 3.5 mA, DAC 0.8 mA, TMP117, AXC1T45) is about 10 mA on one finger; GND about 20 mA per finger | OK (at most 19 % of rating) | **NEW** |
| M16 | First-mate/last-break | DDR4 SODIMM fingers are all the same length (MO-310 detail B); modules are not hot-pluggable. Insert only with the ZCU104 powered off (TE 114-115015 3.11) | N-A | no |
| M17 | SPD: EE1004-v at 0x50-0x57 (SA2..0), set-page/write-protect at 0x30-0x37 (SPA0 0x36, SPA1 0x37), TS at 0x18-0x1F; VDDSPD 2.5 V nominal (Micron; 34AA04 runs at 1.7-3.6 V) | No SPD. The host does not need one: the PL design has a fixed (custom LPDDR5) PHY, and the ZCU104 MIG does not read SPD. UG1267 expects SPD at 0x51 on mux channel 7; tools (`decode-dimms`, ee1004/jc42 drivers) will just see NACKs. Card I2C devices DAC53401 0x48 and TMP117 0x49 do not collide with 0x18-0x1F, 0x30-0x37 or 0x50-0x57. VDDSPD is 3.3 V on this host (already in the power review) | N-A | **NEW** |
| M18 | ESD | JEDEC modules carry no ESD protection; usual handling | N-A | no |
| M19 | Label area | No JEDEC requirement; silk/logo away from the fingers | N-A | no |
| M20 | Thermal / airflow | No requirement in the sources. Card dissipation is well below 1 W at the board's operating point (power review); the DRAM case-temperature measurement is already planned (vendor review A24) | OK | no |
| M21 | Insertion: 20-30 deg cam-in to a hard stop, then press down; for low-rigidity boards press near the latch notches (TE 114-115015 3.11); insertion 59.8 N max (Attend) | 1.2 mm 10-layer card, no parts near the notches beyond M9 | OK | no |

## 3. Recommended actions

### Before ordering

1. **(M2-M4) Change the bevel request.** Ask for the JEDEC edge break, not a gold-finger bevel. Suggested remark: *"SO-DIMM
   module per JEDEC MO-310: finger edge gets only a 45 deg x 0.25 mm max chamfer on each face (edge thickness at least
   0.7 mm), or no chamfer. Do NOT apply the standard 30/45 deg gold-finger bevel. Finger copper must remain to within
   0.30 mm of the edge: no CAM cut-back beyond 0.30 mm."* If JLC can only offer "gold fingers with bevel", order gold
   fingers **without bevel/chamfer** and break the edge by hand (fine file, about 0.2 mm). If JLC insists on its
   bevel, accept **45 deg only, never 30 deg**, with a cut-back of at most 0.50 mm, and treat the order as at risk
   until item 2 is done. The existing order note ("cut-back at most 0.60 mm") leaves only about 0.3 mm on the estimated
   0.9 mm contact row. `fab/README_ORDER.md` remark 2 and CAM question 2 need this edit (outside this folder; not made here).
2. **(M4) Measure the real contact position** before committing: on the Hynix HMAA2GS6 module that has been in the
   ZCU104 socket, measure the contact witness/wipe marks on both faces from the card edge (microscope or loupe with a
   scale). If either row's mark sits below about 1.0 mm, item 1 is mandatory. It also fixes the cut-back limit.
3. **(M11) Look at the ZCU104 LOTES socket** (latch arms and ends, shielded version) for any peg or boss that would
   enter a module hole at 6.0 mm / 2.0 mm. If there is one, the card's 1.5/1.6 mm holes at 1.1 mm from the sides
   will block seating, and they must move to 1.8 mm at (2.0, 6.0) before ordering. If there is none, the holes are
   cosmetic.
4. (M12, CAM note, optional) Add: *"Vias 0.10 mm above the finger ends are intentional; tented and POFV; do not
   move or pull back."* so CAM does not apply a generic 1 mm hole keep-out.

### On hardware (first boards)

- (M5) ENIG fingers: keep insertions to about 10, insert only with power off, and inspect for wear-through (nickel
  showing) on the contact rows. Log the resistance of a GND finger to the socket if links become flaky.
- (M7) Measure the bow along the finger edge with feeler gauges on a surface plate; target 0.15 mm or less.
- (M4) After the first insertion, check that both witness rows sit on gold.

### Next revision

- Fingers: hard gold (at least 0.76 um / 30 uin over 3-5 um Ni) from a vendor that offers it, with a plating bus
  or a bus-less process (M5, M6); keep the JEDEC 0.25 mm chamfer.
- Tooling holes 1.80 mm at (2.0, 6.0) per MO-310 (M11).
- Move R163/R165 (back) at least 0.1 mm inboard of the 2.00 mm side line (M9).
- Copper balance: thieving on In3 and In5 to match In6/In4 (M7).
- Pull In5 `IN_A.CA0` back to 2.0 mm or more if any 30 deg bevel is ever used (M13).
- Optional ID EEPROM (EE1004-compatible addressing at 0x50-0x57; Antmicro fits an AT24CS01) for board ID/rev and
  stored VREF_RD training codes (M17).

## 4. Corrections to the existing reviews

- DRC/mechanical review section 4, "Finger-to-edge copper setback": "the socket still mates" at about 1.95 mm of finger is not
  supported. JEDEC chamfers only 0.25 mm, and one contact row appears to land about 0.9 mm from the edge (M2-M4).
- The same table, "Bevel keep-out": the inner signal minimum is 1.38 mm (In5 IN_A.CA0), not 1.955 mm (M13).
- `scripts/keepout_check.py` checks pad extents only: it does not check bodies/courtyards, the 0.80 mm height band or
  the tooling-hole size (M8, M9, M11).

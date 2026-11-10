# LPDDR5 testbed rev 2.1.0: review against general high-speed / SI / PI layout guides

Date: 2026-10-02. Board: `kicad/lpddr5-testbed.kicad_pcb` (rev 2.1.0, read only). Scope: general high-speed,
signal-integrity, power-integrity and layout guides from vendors other than the ones already reviewed in
`references/VENDOR_GUIDELINES_REVIEW.md` (UG583, DS925, PG188 and the Micron BGA notes are not repeated here). Findings
already made by the release, power, DRC/mechanical or vendor reviews are referenced, not re-reported (for example A4
"GND via within 50 mil", A6 impedance, A7 reference planes, A12 PDN Z(f)).

Every number below was measured from the KiCad file with the pcbnew API (9.0) and shapely. The scripts are in `tools/`:
run `extract.py` first, then the analysis script. Their raw outputs are the `measurements_*.json` files in this folder.
Page numbers are PDF pages, which here equal the printed folios except in SZZA009. The `.txt` file next to each PDF is
the `pdftotext -layout` text.

Rates used to judge relevance:
- CK up to 800 MHz and CA 1600 Mb/s, used for command-only traffic.
- Writes at 320 Mb/s (WCK 160 MHz); reads at 100 Mb/s.
- FPGA edges about 50-150 ps. A 1 mm stripline is about 7 ps.
- The highest frequency that matters is about 2.4 GHz (3x the CA Nyquist frequency).
- Via-stub quarter-wave resonances on this 1.17 mm stack are above 40 GHz.


> **rev 1.0.0 status (2026-10-03, `doc/RELEASE_REPORT_rev1.0.0.md`):** F5 VREF_RD decoupling: C113-C118 (100 nF) added on
> the comb; the 200 Mb/s read eye opens from 0.6-1.6 ns to 2.7-4.5 ns. F6 stubs: DQ7 8.72 -> 3.56 mm, DQ11 7.62 -> 2.59 mm,
> DMI0 5.86 -> 4.40 mm. F3 spacing: IN_A.DQ13 below 3w 35/32 -> 21/21 mm; the rest of DQ13 and IN_A.DMI1 have no 3w path.
> Adjacent layers: all In2/In3 broadside write lanes pass at 320/400 Mb/s with every aggressor switching.

## 1. Documents (14 PDFs, 18.3 MB)

| File | Document | Version / date | Source URL | Used for |
|---|---|---|---|---|
| `TI_SPRACN9_Jacinto7_LPDDR4_Board_Design.pdf` | TI Jacinto 7 LPDDR4 Board Design and Layout Guidelines | SPRACN9F, Aug 2024 | https://www.ti.com/lit/pdf/spracn9 | Rules most directly applicable here: broadside (p5), stack-up PS1-PS13 (p5), bypass caps Table 1-3 (p7), spacing/vias/stubs/skew Tables 2-6 and 2-7 (p16, p18), no stubs (p15) |
| `TI_SPRAD66_AM62A_LPDDR4_Board_Design.pdf` | TI AM62Ax/AM62Px LPDDR4 Board Design and Layout Guidelines | SPRAD66B, Dec 2024 | https://www.ti.com/lit/pdf/sprad66 | Same rule set with 5w spacing (Tables 2-6/2-7, p15/p17) and bypass Table 1-3 (p6) |
| `TI_SPRABI1_HW_Design_Guide_KeyStone.pdf` | TI DDR3 Design Requirements for KeyStone Devices | SPRABI1D, Jul 2022 | https://www.ti.com/lit/pdf/sprabi1 | VREF routing and decoupling (p20 §6.3.1.8), 5W spacing above 1066 MT/s (p16), no splits (p16) |
| `TI_SPRAAR7_HighSpeed_Interface_Layout.pdf` | TI High-Speed Interface Layout Guidelines | SPRAAR7J, Feb 2023 | https://www.ti.com/lit/pdf/spraar7 | Reference planes and stitching caps (p6-8), stitching vias within 200 mil (p8), plane edge 90 mil, voids 1.5W (p9), stubs, anti-pads, NFPs (p11-12), 90° adjacent layers (p14 Table 3-1) |
| `TI_SCAA082_HighSpeed_Layout_Guidelines.pdf` | TI High-Speed Layout Guidelines | SCAA082A, Aug 2017 | https://www.ti.com/lit/pdf/scaa082 | 2-3W spacing, orthogonal adjacent layers, return vias, decoupling vias (p8, p13-17) |
| `TI_SZZA009_PCB_Design_Guidelines_Reduced_EMI.pdf` | TI PCB Design Guidelines for Reduced EMI | SZZA009, Nov 1999 | https://www.ti.com/lit/pdf/szza009 | Connector return ratio (p19), board-edge routing (p17), plane slots (p10) |
| `TI_SLVA118_LDO_basics.pdf` | TI Linear Regulator Design Guide for LDOs | SLVA118A, Jun 2008 | https://www.ti.com/lit/pdf/slva118 | LDO thermal only (p4-20). Its rules are already covered by the power review |
| `TI_SWPA222_Power_Delivery_Network_Analysis.pdf` | TI Power Delivery Network Analysis | SWPA222A, Nov 2012 | https://www.ti.com/lit/pdf/swpa222 | Z-target method, loop inductance, via-in-pad caps (p4-12) |
| `ADI_MT-101_Decoupling_Techniques.pdf` | Analog Devices MT-101 Decoupling Techniques | Rev 0, 03/2009 | https://escies.org/download/webDocumentFile?id=62177 (mirror; the analog.com URL timed out) | Short cap-to-plane connection (p2, p6-7) |
| `Wurth_ANP098a_Layout_Vias_Filter_Capacitors.pdf` | Würth ANP098a, effect of layout and vias on filter capacitors | 2022-05-16 | https://www.we-online.com/components/media/o695199v410%20ANP098a%20EN.pdf | Trace ESL (1-2 mm ≈ 0.5 nH, p3), several GND vias per pad (p5) |
| `AMD_XAPP623_PDS_Bypass_Decoupling_v2.1.pdf` | Xilinx XAPP623 PDS Design: Bypass/Decoupling Capacitors | v2.1, 2005-02-28 (obsolete, Virtex-II) | https://docs.amd.com/api/khub/documents/rqk~alpjrAD0EGuVgnooOw/content | Mounting inductance (p5-6 Fig 6), no via sharing (p6), VREF cap mix (p15), cap placement radius (p9-12) |
| `Altera_AN958_Board_Design_Guidelines_2023-06-26.pdf` | Altera AN 958 Board Design Guidelines | 2023.06.26 | https://docs.altera.com/api/khub/maps/hkpBDF0x7nXLkKhrX_~pPw/attachments/YSX2aMF9uRiOvdiZZr06JQ-hkpBDF0x7nXLkKhrX_~pPw/content | Mounting inductance Table 1 (p10), splits (p14), dual stripline (p16, p24), return vias (p51-52), stubs (p28), clocks (p27). Its memory section (§6 p75) only links old app notes |
| `Altera_AN224_HighSpeed_Board_Layout_Guidelines.pdf` | Altera AN 224 High-Speed Board Layout Guidelines | v1.2, Aug 2009 | https://docs.altera.com/api/khub/documents/SWozSioq1TGTvzL1_kYmRg/content | 3H / 4W spacing (p12), dual stripline (p11), stub < tr/3 (p14), termination placement (p19-23) |
| `Altera_WP_Basic_Principles_of_Signal_Integrity.pdf` | Altera WP Basic Principles of Signal Integrity | v1.3, Dec 2007 | https://docs.altera.com/api/khub/documents/Jifb6ZTngK0IeftvvNlgug/content | Qualitative only (p1-4) |

How the documents were read: all 14 were read in full (every page of the text versions), and every applicable rule was
extracted with its page. The citations behind the verdicts below were re-checked against the text. Examples:
- SPRACN9F p5 "not recommended", and the Table 1-3 footnotes;
- SPRAAR7J p8 (200 mil) and p9 (90 mil);
- SPRABI1D p20 (30 mil / 15 mil VREF);
- AN958 p16/p24 (dual stripline);
- XAPP623 p6 (via sharing);
- AN224 p12/p14 (3H and stub).

### 1.1 Not obtained

| Document | Why |
|---|---|
| NXP i.MX 93 / i.MX 8M Plus Hardware Developer's Guides (IMX93HDG, IMX8MPHDG), LPDDR4/LPDDR5 layout chapters | nxp.com returns "Page not available" to scripted downloads; IMX93HDG is behind nxp.com/webapp/Download (login). URLs: https://www.nxp.com/docs/en/user-guide/IMX8MPHDG.pdf, https://www.nxp.com/webapp/Download?colCode=IMX93HDG |
| AMD XTP427 UltraScale+ Schematic Review Checklist | The docs.amd.com entry is a placeholder zip; the file needs the license form at https://account.amd.com/en/forms/downloads/license-form-tip.html?cid=XTP427-en-us&filename=xtp427-ultrascale-plus-schematic-review-checklist.zip |
| Real Digital PCB / SI guides | realdigital.org now hosts only course and board pages. The only PDFs found were the AUP-ZU3 reference manual and schematic, which are not layout guides and were not saved |
| Intel copy of AN 224 (intel.com) | Access denied (Akamai). The same document was taken from docs.altera.com |
| Altera AN 315 (2004) | Superseded by AN 224; skipped |
| Altera Agilex 5 EMIF IP user guide (424 pp) | Downloaded and then removed: it has pin-planning rules but no board-layout rules |
| H. Ott articles, Murata decoupling notes, Bogatin/Hartley material | No downloadable vendor PDF found (hottconsultants.com returns HTML) |
| Micron TN-53-06/09 (LPDDR4 layout/PI) | Not public (as in the vendor review) |

None of the downloads was over 40 MB.

## 2. Findings

Verdict key:
- **ISSUE** = a concrete violation of a guide rule, with numbers, that matters at this board's rates; the severity is given.
- **CHECK-ON-HARDWARE** = an effect that can only be settled by measurement.
- **OK** = the rule is met, or the deviation is justified with numbers.
- **N-A** = the rule was written for a regime that does not apply here, with the reason.

### 2.1 Issues and checks

#### F1. Broadside coupling between In2 and In3. ISSUE (medium, next revision) + CHECK-ON-HARDWARE

**Rules**
- SPRACN9F p5 §1.4 and SPRAD66B p4 §1.3: routing on 2 adjacent signal layers "is not recommended as crosstalk occurs on any trace routed parallel to another trace on an adjacent layer, even for a very short distance".
- SPRAAR7J p14 Table 3-1 note 1: route directly adjacent signal layers at a 90° offset.
- AN958 p16/p24 and AN224 p11: orthogonal, or ≥4W apart; increase the signal-signal separation; minimise the distance to the plane.
- SCAA082A p14/p17: orthogonal.

**Measured**
- The In2-In3 dielectric is 0.1164 mm, wider than the 0.100 mm from each layer to its GND plane (In1, In4).
- About 158 mm of In2/In3 routing runs parallel to a different net at < 0.2 mm lateral offset.

| Victim / aggressor | Parallel length, offset < 0.2 mm | Of which offset < 0.1 mm |
|---|---|---|
| IN_A.DQ1 / DQ7 | 47.8 mm | 32.5 mm |
| IN_A.DQ6 / DMI1 | 23.1 mm | 13.0 mm |
| IN_A.DQ15 / DQ6 | 19.4 mm | 14.1 mm |
| IN_A.DQ8 / DQ11 | 15.9 mm | |
| IN_A.DQ10 / DQ15 | 13.3 mm | 6.2 mm |
| RD_RDQS0_P / VREF_RD_N70 | 11.0 mm | 2.1 mm (see F2) |
| IN_A.DQ15 / VREF_RD_N62 | 6.2 mm | |
| VREF_RD_N74 / IN_A.DQ11 | 6.0 mm | |

The backward coupling coefficient Kb (Codex `sim/si/extract/linemodels.json`) is 0.107 at 0.1 mm offset and 0.056 at 0.2 mm.

**Cross-check with SI**
- The only crosstalk cases simulated are DQ1↔DQ7 (`sim/si/results/host_coupling.json`, 48 cases).
- That pair is the release report's 400 Mb/s write limit: DQ7 fails by 15 ps in one corner.
- The other pairs are half that length or less and were not simulated.
- Writes at 320 Mb/s keep +0.57 ns.

**Action**
- Next revision: make In2/In3 orthogonal, or move byte lanes so that no two write lanes share a broadside track.
- Hardware: compare the write-training windows of DQ6, DQ15, DMI1, DQ8, DQ11 and DQ10 with the rest of the byte.

#### F2. RD_RDQS0_P broadside onto VREF_RD_N70 (the N input of RD_DMI0). CHECK-ON-HARDWARE (low)

**Rules:** same as F1, plus SPRACN9F p4 §1.3: "minimize reference voltage noise through proper isolation".

**Measured**
- 11.0 mm of parallel In2/In3 overlap at < 0.2 mm offset, 2.1 mm of it at < 0.1 mm.
- The aggressor is the read strobe, so it toggles during every read burst, at the same time as the lane it disturbs. That differs from F1's write-on-write coupling, where victim and aggressor carry the same burst.
- The simulated N-node swing at 200 Mb/s is 66-133 mV p-p on DMI0, from `pl_read.json`. That model does not include this coupling.

**Action**
- Hardware: compare the DMI0 read window and VREF code against DQ0-7.
- Next revision: separate the two traces.

#### F3. Same-layer spacing inside byte 1 on In7, and in the outer-layer OUT_A groups. CHECK-ON-HARDWARE + next revision

**Rules (centre-to-centre spacing)**

| Guide | Rule |
|---|---|
| SPRACN9F Table 2-7 p18 | 3w within a byte, 4w byte-to-byte and DQS; 2w only for ≤ 500 mil (12.7 mm) near the endpoints (fn 7) |
| SPRAD66B Table 2-7 p17 | 3w within a byte, 5w between classes |
| SPRABI1D p16 | ≥ 5W above 1066 MT/s |
| AN224 p12 and AN958 p24 | ≥ 4W centre-to-centre, or ≥ 3H |

**Measured**
- The 0.09 mm traces are at the 0.09 mm clearance, which is 2w centre-to-centre.
- Length within a byte at centre-to-centre < 3w:

  | Net | Length | Layer |
  |---|---|---|
  | IN_A.DQ13 | 67.0 mm | In7/In5 |
  | IN_A.DMI1 | 42.5 mm | |
  | IN_A.DQ12 | 38.1 mm | |
  | IN_A.DQ14 | 35.7 mm | |
  | OUT_A.DQ12 | 34.3 mm | F.Cu, 0.15 mm wide |
  | IN_A.DQ2 | 22.9 mm | |
  | IN_A.DQ8 | 19.3 mm | |

- Between groups at < 4w:

  | Net | Length | Aggressor |
  |---|---|---|
  | IN_A.DQ12 | 23.6 mm | WCK1_N |
  | IN_A.DMI0 | 22.6 mm | DQ14 |
  | IN_A.DQ10 | 22.6 mm | |

- Kb at 2w on In5/In7 is 0.066 per neighbour, and DQ13 has neighbours on both sides.
- 42 % of the 3.47 m of signal copper is microstrip (F.Cu 939 mm, B.Cu 543 mm). SPRACN9F p4 says stripline except breakout.
- None of these combinations is in the simulated crosstalk set.
- The command bus is clean: only 2.7 mm of CA/CK is below 0.30 mm centre-to-centre (IN_A.CA1/CA5 on F.Cu). IN_A.CA0 runs 9.0 mm next to the static RESET_n.

**Judgement**
- In stripline the coupling returns as NEXT toward the FPGA driver, so its effect is bounded by the same mechanism as DQ1/DQ7.
- It is acceptable at 320 Mb/s, but it is a candidate second limiter at 400 Mb/s.

**Action**
- Hardware: the byte-1 write windows (DQ12-14, DMI1).
- Next revision: ≥ 3w within bytes and ≥ 4w between bytes and WCK.
- Optional now (no board change): run the existing `sim/si/tools/xtalk_sim.py` for DQ13 with DQ12/DQ14 same-layer aggressors.

#### F4. In7 traces whose nearer plane is the In8 +1V05 pour. ISSUE (low, next revision) + CHECK-ON-HARDWARE

**Rules**
- SPRACN9F Table 1-1 PS5 p5: 0 reference-plane cuts in the DDR routing region.
- SPRAAR7J p6-7 §2.4: no power-plane references. If a split must be crossed, place ≤ 1 µF stitching caps "as close as possible to the plane crossing".
- SPRABI1D p16: no splits under DDR routes.
- AN958 p14: do not cross plane splits.

**Measured**
- In7 sits 0.100 mm from In8 and 0.1164 mm from In6 (GND), so In8 is the nearer plane.
- 27.5 mm of In7 routing lies over the In8 +1V05 pour:

  | Net | Length |
  |---|---|
  | IN_A.DMI0 | 5.7 mm |
  | IN_A.DQ12 | 4.6 mm |
  | IN_A.DQ13 | 5.8 mm |
  | IN_A.DQ14 | 6.1 mm |
  | IN_A.WCK1_P | 2.3 mm |
  | IN_A.WCK1_N | 3.1 mm |

- These traces cross the In8 GND/+1V05 boundary 9 times (x 141.8-147.8, y 87.6-92.0).
- The nearest +1V05 capacitor to a crossing is 5.3-9.7 mm away.
- Vendor review A7 counted this as minor because In6 GND is solid. The added point here is that In8 is the closer plane, and there is no stitching cap at the crossings.

**Action**
- Next revision: pull the In8 +1V05 pour back from under the In7 DDR routes, or route those nets on In5; otherwise add 100 nF GND-to-+1V05 caps within about 1 mm of the crossings.
- Hardware: WCK1 and byte-1 write windows (shared with F3).

#### F5. VREF_RD is decoupled only at the DAC end. ISSUE (low-medium, next revision) + CHECK-ON-HARDWARE

**Rules**
- SPRABI1D p20 §6.3.1.8: decouple VREF at the receiver pins (0.01 µF + 0.1 µF each), plus 0.1 µF at the source. The trace should be ≥ 30 mil (0.762 mm) wide, with ≥ 15 mil (0.381 mm) spacing to all other nets.
- XAPP623 p15: split VREF caps 50/50 between 0.1-0.47 µF and 0.01-0.047 µF.
- SPRACN9F p4 §1.3: decoupling on the reference input pins.

**Measured**
- VREF_RD is 90.7 mm long: 78.4 mm at 0.15 mm, 7.9 mm on In5 at 0.09 mm, 4.4 mm at 0.20 mm.
- Isolation is good: only 3.0 mm of the trunk is within 0.381 mm of another net (minimum 0.30 mm to +3V3, 0.33 mm to A.WCK1_P).
- There is one 100 nF capacitor (C110), at the DAC divider. Each of the 18 39 Ω N-leg resistors (R160-R177) is 17-32 mm of trace from it (17 of 18 resolved), and there is no capacitor on the comb.

**Why it matters:** the release report names the shared VREF_RD bounce as the read limit above 100 Mb/s. The simulated N swing is 46-162 mV p-p at 200 Mb/s (`pl_read.json`), against a 158 mV reference and a 70 mV VID minimum.

**Action**
- Next revision: add 100 nF + 10 nF (0402) at 2-3 points along the comb trunk next to R160-R177. Evaluate it first in `sim/si/tools/dstage.py` (C110 and comb model), because part of the N swing is P/N coupling inside the FPGA package, which no board cap removes.
- Hardware: read-training VREF window per lane at 100/200 Mb/s.
- The width rule (30 mil) is not needed for a 0.15 mm trace that carries only N-pin currents through 39 Ω.

#### F6. T-branch stubs where the read path taps the write path. ISSUE (low, next revision)

**Rules**
- SPRACN9F p15 §2.11-2.12 / SPRAD66B p14: no stubs on CK/CA/data nets; test points in-line.
- AN224 p14 / AN958 p28: TDstub < t(10-90)/3. For 100 ps edges that is about 33 ps, or about 4.7 mm.

**Measured** (`measurements_topology.json`, branch length from the T-junction):

| Branch | Net | Stub length |
|---|---|---|
| To the 22 Ω read resistor | OUT_A.DQ7 | 7.6 mm |
| | DQ11 | 6.5 mm |
| | DMI0 | 5.2 mm |
| | DQ12 | 4.5 mm |
| | DQ6 | 3.8 mm |
| | DQ0 | 0 (in-line) |
| To the 100 Ω/68 Ω pad | DQ12 | 5.3 mm |
| | DQ10 | 4.9 mm |
| | DQ3 | 4.9 mm |
| | DMI1 | 4.4 mm |

- The 68/82 Ω shunts sit on the 100 Ω pads, 1.1-1.3 mm away, through the board.
- The SI flow simulates each lane through its routed module topology, so this is inside the qualified 320/100 Mb/s numbers.
- DQ7 combines the longest stub with the longest broadside (F1).

**Action (next revision):** put the 22 Ω tap at the 100 Ω pad (as DQ0 already is), so the stub is ≤ 1 mm.

#### F7. LDO feedback and grounding. CHECK-ON-HARDWARE (low)

**Rules**
- TPS74801 data sheet §7.4.1 (`references/parts/TI_TPS74801.pdf`): connect the top of R1 "as close as possible to the load".
- TPS7A84A §10.1: same-side caps, a wide component-side ground, and no vias to the I/O caps. (None of the 14 guides covers LDO layout; SLVA118 is thermal only.)

**Measured**
- U21's divider (R114/R115) senses +1V05 at the regulator, about 6 mm of pour and vias from the nearest VDD2H ball.
- Output caps are same-side, on short traces:

  | Cap | Trace length |
  |---|---|
  | C95 (U22) | 0.57 mm |
  | C88 (U21) | 1.2 mm |
  | C84 (U20) | 0.92 mm |

- C96, C89 and C83 connect through vias or pour.
- Grounds return through in-pad vias or 0.6 mm traces to In1, 0.116 mm below; there is no F.Cu GND copper.
- Exposed-pad vias: U20 4, U21 5, U22 4.
- IR drop is covered by Codex's PDN numbers (power review §1).

**Action:** bring-up already measures rails. Measure VDD2H at a BGA-side point under traffic as well as at the regulator.

### 2.2 Rules met or not applicable (measured)

#### Return path and reference

**R1. Stitching vias at layer changes. OK**
- Rule: within 200 mil (5.08 mm) centre-to-centre (SPRAAR7J p8); more vias around the signal via is better (SCAA082A p15-17).
- Measured: across all 255 signal vias, the farthest nearest-GND-via distance is 3.48 mm (OUT_A.DQ15), so 0 exceed 5.08 mm.
- The stricter UG583 50-mil test is vendor review A4 (it fails for 94 of 255) and is not repeated here.
- Pair vias with the farthest GND via: IN_A.WCK1 at R13/R14 (2.8-3.05 mm) and RD_RDQS at the 22 Ω resistors (1.55-2.69 mm).

**R2. Voids and antipads. OK (N-A at these edges)**
- Rule: ≥ 1.5W from reference voids (SPRAAR7J p9); beware antipad voids (SPRACN9F PS5).
- Measured: 6 continuous runs > 1 mm over a void. The longest are 1.87 mm (IN_A.DQ15 In3, finger band), 1.45 mm (IN_A.DQ0 In7), 1.16 mm (OUT_A.DQ7 F.Cu), 1.15 mm (A.CA3 In5 under the BGA) and 1.12 mm (IN_A.DQ7).
- 1.9 mm is about 13 ps, which is below tr/3 for 50-150 ps edges.

**R3. Plane edge. OK**
- Rule: ≥ 90 mil from the reference-plane edge (SPRAAR7J p9, written for SerDes pairs); never route noisy traces on the outside edge (SZZA009 p17).
- Measured: the GND planes are pulled back 0.20 mm from the outline.
- Only about 25 mm of signal is within 0.3 mm (3H) of a plane outer edge, all where a trace must reach a finger: at the key notch (x ≈ 137.4, y ≥ 95.5), near x ≈ 101, y ≈ 95, and VREF_RD_N70/N41 for about 0.5 mm beside the left latch notch.
- A 90 mil rule is not achievable at an SO-DIMM edge, and none of these segments is longer than 3.4 mm.

**R4. Edge-finger returns. OK**
- Rule: ≥ 1 GND per 9 signals, moving toward 1:5 (SZZA009 p19); enter connector pads on their own layer (SPRAAR7J p10).
- Measured: 94 GND fingers for 78 signal fingers.
- Every GND finger has a GND via 0.45-1.55 mm from its pad edge (median 0.83 mm).
- Signal fingers are entered on their own layer, with no via in the finger.

#### Vias and stubs

**R5. Via stubs. N-A / OK**
- Rule: CA/CK ≤ 20 mil (0.508 mm), DQ ≤ 40 mil (1.016 mm), backdrill above 3200 Mb/s (SPRACN9F Tables 2-6/2-7); < 15 mil else backdrill (SPRAAR7J p11); TDstub < tr/3 (AN224 p14).
- Measured on through vias:
  - CA/CK: maximum stub 0.635 mm. 8 of 40 vias exceed 0.508 mm (IN_A.CA0/CA2/CS0/CS1, B.Cu→In5).
  - DQ and other nets: maximum 0.882 mm, so all are ≤ 1.016 mm.
- 0.635 mm is about 4.4 ps of stub, and its quarter-wave frequency is about 60 GHz, so the stub rules are irrelevant at 1.6 Gb/s.

**R6. Non-functional pads. N-A**
- Rule: not permitted, or remove them (SPRAAR7J p12, AN958 p14/p51, SPRACN9F p26).
- Measured: all 727 vias keep pads on all 10 layers (`remove_unused_layers` is not set). That is about 0.05-0.1 pF per via, already inside the SI flow's 0.25 pF lumped via.
- Optional for the next revision.

#### Pairs, corners and clocks

**R7. Differential pairs. OK / N-A**
- Rules:
  - CK/DQS intra-pair skew ≤ 0.75 ps (SPRACN9F), ≤ 1.5 ps (SPRAD66B);
  - equal via counts (SPRAAR7J p12, AN958 p49);
  - constant S and D > 2S (AN224 p19).
- Measured intra-pair length difference:

  | Pair | ΔL | Skew |
  |---|---|---|
  | A.CK, IN_A.CK, A.WCK0/1, IN_A.WCK0, RD_RDQS0 | 0-0.1 mm | ≤ 0.7 ps |
  | IN_A.WCK1 | 1.87 mm | about 13 ps |
  | OUT_A.RDQS0 | 1.96 mm | about 14 ps |
  | OUT_A.RDQS1 | 1.35 mm | |
  | RD_RDQS1 | 0.55 mm | |

- The DRC review's delay-based figure is 0.2-10.4 ps.
- Gap: the median is 0.10 mm (outer layers and In5) or 0.30 mm (In7 and In2), and 77-100 % of each pair is within ±10 % of its median.
- RD_RDQS0 and RD_RDQS1 have 3 vias on P and 2 on N.
- 14 ps is 0.4 % of the 3.1 ns write UI and 0.1 % of the read UI. The 0.75 ps rule is for 4266 MT/s.

**R8. Corners. N-A**
- Rule: bends > 135° and no 90° corners (SPRAAR7J p13, AN958 p31, SCAA082A p14).
- Measured: of 5974 signal-trace vertices, 77 are 90° and 8 are acute (mostly sub-0.1 mm fold-backs).
- Corner capacitance is in the fF range, which does not matter at ≤ 2.4 GHz.

**R9. Clocks: one layer, no vias (AN224 p14, AN958 p27). OK (deviation simulated)**
- A.CK: 2 vias (F→In5).
- IN_A.CK: 0 vias.
- IN_A.WCK0: 4 vias, routed as 55 mm of outer-layer microstrip.
- IN_A.WCK1: 2 vias, on In7.
- The clocks pass 28/28 in the uncoupled-bracket SI (DRC review) and in `ck_final.json`.

#### Termination

**R10. Termination placement. OK (by design)**
- Rule: parallel termination at the load, series at the source (AN224 p19-23, AN958 p33-41).
- On this board the 100 Ω series plus 68/82 Ω shunt at the fingers is a source-side attenuator from the 1.2 V FPGA into 0.5 V LVSTL, not a load termination; the DRAM ODT is the load termination.
- Measured distances: fingers to the first series resistor are 3.7-14 mm for CA/CK and 49-77 mm for write DQ; 100 Ω to the U1 ball is 12-25 mm for CA/CK and 23-38 mm for DQ.
- The SI flow simulates this topology. Remaining stubs: F6.

#### Decoupling and power

**R11. DRAM bypass capacitors. OK**
- Rules:
  - SPRACN9F Table 1-3 p7 and SPRAD66B Table 1-3 p6: ≥ 12 caps and ≥ 0.85 µF per DRAM; cap centre ≤ 150 mil from the ball; 1 via per ball, ball-to-via ≤ 60 mil; 2 vias per cap, cap-to-via trace ≤ 100 mil; no via sharing on the same side.
  - XAPP623 p6: no via sharing; vias butted to the land.
- VDDQ: 12 × 4.7 µF 0402 on the bottom. Cap centres are 0.03-0.33 mm from the nearest VDDQ ball, and every pad sits on the ball's via-in-pad (opposite side).
- VDD2H: 22 caps.
  - 14 are on the bottom, via-in-pad.
  - 8 are on the top, on the +1V05 pour, 2.1-2.7 mm (83-106 mil) from the nearest ball.
- VDD1: 4 caps, 0.73-0.81 mm from the nearest ball. Pad-to-via traces are 0.68-1.15 mm (27-45 mil).
- The longest pad-to-via trace is 1.58 mm (62 mil, C58 GND).
- One extra GND via (136.88, 76.0) is shared by C45 and C64 on the same side, but each also has its own in-pad via.
- Estimated mounting inductance (AN958 p9 equation) is about 0.6 nH for the via pair plus ESL. With 12 in parallel that is about 0.08 nH, consistent with vendor review A12.

**R12. Capacitor value mix. OK (deviation justified)**
- Rule: 0.01-0.1 µF in parallel, one value per decade (AN958 p59, XAPP623 Table 6 p15, SCAA082A p13).
- Measured: only 4.7 µF 0402 is fitted at the BGA.
- A 4.7 µF 0402 has the same ESL as a 0.1 µF 0402, and identical values create no anti-resonance between them. Above about 100 MHz the LPDDR5 package and die capacitance take over.
- Z(f) is checked on hardware under vendor review A12.

**R13. Power planes next to GND at < 4-5 mil (XAPP623 p7/p12, AN958 p56). OK**
- The +1V05/+1V2 pours on In5 sit between GND In4 (4.6 mil) and GND In6 (3.9 mil).
- The B.Cu +0V5 pour is 4.6 mil from GND In8.
- The In8 +1V05 pour is coplanar with In8 GND (see F4 for its effect on signals).

**R14. Thermal reliefs. OK**
- The GND planes are set to thermal relief, but that applies only to PTH pads, and there are none on GND; vias connect solid.
- All power pours are FULL.

#### Other

**R15. SMD parts on differential pairs: ≤ 0603, 0402 preferred, symmetric (SPRAAR7J p12). OK**
- All are 0402/0201: R11/R12, R13/R14, R15/R16, R154/R155, R146-R149, R150-R153.
- P/N placement is mirrored.

**R16. Test points. OK**
- Rule: none on high-speed differential pairs (SPRAAR7J p9); test points in-line (SPRACN9F p15).
- Measured: test points only on rails (TP10-TP16) and on VREF_RD (TP30); none on DDR signals.

**R17. ESD (SPRAAR7J p14-15). N-A**
- There are no ESD parts. JEDEC memory modules carry none, and adding capacitance to the LVSTL lines would cost margin.

**R18. Rules already covered elsewhere, not repeated**

| Topic | Rule | Covered by |
|---|---|---|
| Impedance | 40 Ω SE / 80 Ω diff ±10 % (SPRACN9F Table 1-1) | Vendor review A6: 48-71 Ω, simulated |
| Skew and length budgets | SPRACN9F Tables 2-6/2-7 | PL per-pin training (release report §2) |
| Placement X/Y limits | SPRACN9F Table 2-3 | Do not apply: the controller is on the ZCU104 |
| LDO thermal | SLVA118 | Power review (≤ 99 °C junction) |
| Fiber weave | SPRAAR7J p3-5 | Below 1.6 Gb/s with 2116 glass |

## 3. Actions

### Before ordering

None. The only rule violations with a measured link to this board's performance are F1, F3 and F4 (coupling and
reference), F5 (the VREF_RD decoupling location) and F6 (stubs). Each of them affects the 400 Mb/s write and > 100 Mb/s
read headroom. The qualified operating points keep +0.57 ns (writes, 320 Mb/s), +1.45 ns (reads, 100 Mb/s) and +32 ps
(CA, 1600 Mb/s); the CA bus has no significant same-layer or broadside aggressor (F3: 2.7 mm below 0.30 mm
centre-to-centre). None of them justifies re-routing a released board.

Optional and cheap, with no board change:
- Run `sim/si/tools/xtalk_sim.py` for the unsimulated combinations: DQ13 with DQ12/DQ14 same-layer, DQ6/DMI1 and DQ15/DQ6 broadside, and RDQS0_P→VREF_RD_N70.
- Run `dstage.py` with comb decoupling caps, so the hardware plan knows which lanes to watch.

### Bring-up (check on hardware)

1. Per-lane write-training windows. Flag lanes that are narrower than the rest of their byte:
   - DQ6, DQ15, DMI1, DQ8, DQ11, DQ10 (F1 broadside);
   - DQ12, DQ13, DQ14, DMI1 (F3 same-layer);
   - WCK1 and byte 1 (F4 power-pour reference).

   Narrow windows confirm coupling, not the DRAM.
2. Read window and VREF code of DMI0 against DQ0-7 (F2).
3. Per-lane VREF_RD sweep at 100 and 200 Mb/s (F5). This is the same sweep as vendor review A8.
4. VDD2H at a BGA-side point under traffic, as well as at U21 (F7).

### Next revision

1. In2/In3: route orthogonally, or keep write lanes off shared broadside tracks; the worst case is DQ1/DQ7 at 48 mm (F1). Move RD_RDQS0 away from VREF_RD_N70 (F2).
2. Spacing ≥ 3w within a byte and ≥ 4w between bytes and WCK, with 2w only within 12.7 mm of an endpoint. Prefer stripline for the long OUT_A F.Cu runs (F3).
3. Remove the In8 +1V05 pour from under the In7 DDR routes, or add stitching caps at the crossings (F4).
4. Decouple VREF_RD on the comb next to R160-R177 with 100 nF + 10 nF at 2-3 points, after a `dstage.py` check (F5).
5. Put the 22 Ω read taps at the 100 Ω pads, so T-stubs are ≤ 1 mm (F6).
6. Optional:
   - remove NFPs on signal vias (R6);
   - equalise the RD_RDQS via counts (R7);
   - add GND vias next to the IN_A.WCK1 and RD_RDQS transitions at R13/R14 and R146-R149 (R1; also vendor review A4).

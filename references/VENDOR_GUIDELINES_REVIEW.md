# LPDDR5 testbed rev 2.1.0: AMD and Micron guideline review

> Review of internal pre-release rev 2.1.0; findings still apply to the current rev 1.1.0 except where a "now:" note says otherwise.

Date: 2026-10-02. Scope: AMD (Xilinx) and Micron documents that bear on the rev 2.1.0 card (`kicad/lpddr5-testbed.kicad_pcb`,
read only) and on the FPGA side of its interface (`fpga/`, `doc/pinmap.csv`). Each rule was compared with the design.
Values were measured from the KiCad board with the pcbnew API and from the ZCU104 schematic (XTP484 rev 1.0). Items that the
release, power and DRC/mechanical reviews already cover are listed separately in section 3.

Page numbers are PDF page numbers. Text versions (`pdftotext -layout`) sit next to every PDF. The machine-readable list
is `vendor_index.json`.

## 1. Documents

### 1.1 Downloaded (18 PDFs, 89.6 MB)

| File | Title | Version / date | Source | Why it matters |
|---|---|---|---|---|
| `amd/UG583_UltraScale_Architecture_PCB_Design_v1.29.pdf` | UltraScale Architecture PCB Design User Guide | v1.29, 2025-12-23 | docs.amd.com (map 5t7dhkhtFM3JC0rDT2nwdQ) | General memory routing rules (items 1-19, p.26-35); LPDDR5 terminations, impedance, length, vias and skew (Tables 116-119, p.155-160) |
| `amd/UG571_UltraScale_SelectIO_Resources_v1.16.pdf` | UltraScale Architecture SelectIO Resources | v1.16, 2025-01-14 | docs.amd.com/api/khub/documents/kFbaUC5HGcXyGNauhgU6Gw | IBUFDS_DPHY (p.46), MIPI_DPHY_DCI attributes and VRP (Table 1-75, p.131), DCI cascading rules (p.23), native-mode bitslices |
| `amd/DS925_ZynqUSplus_DC_AC_Switching_Characteristics_v1.30.pdf` | Zynq UltraScale+ MPSoC DC and AC Switching Characteristics | v1.30, 2026-07-09 | docs.amd.com (map sTfrXTPA6iXT4rkhrRi7qg) | HP VIN limits (Tables 1, 2, 7), leakage and pull-ups (Table 4), MIPI_DPHY_DCI_HS input levels (Table 17, p.21), async receiver limit (p.52), IDELAY resolution |
| `amd/PG188_High_Speed_SelectIO_Wizard_v3.6_2020-06-10.pdf` | High Speed SelectIO Wizard v3.6 | PG188, 2020-06-10 | docs.amd.com/api/khub/documents/f9e9UOnpRuAaaYviw5USoA | The wizard used on banks 64/65/66: async (serial-mode) RX rules (p.21, 24, 30), VAR_LOAD |
| `amd/PG150_UltraScale_Memory_IP_v1.4_2022-04-20.pdf` | UltraScale Memory IP v1.4 | 2022-04-20 | xilinx.com legacy URL | Byte-group and strobe (QBC/DBC) pin rules for comparison with the custom map. The 2025-12-03 edition (39.4 MB) was not downloaded; its URL is in the index |
| `amd/PG202_MIPI_DPHY_v4.3.pdf` | MIPI D-PHY v4.3 | 2024-12-04 | docs.amd.com (map 33PCiXatxYYhWV5yiwKHkQ) | AMD's own IBUFDS_DPHY use (P bitslice only, HS termination) |
| `amd/UG1075_ZynqUSplus_Packaging_and_Pinouts_v1.13.pdf` | Zynq UltraScale+ Packaging and Pinouts | v1.13, 2024-10-11 | docs.amd.com/api/khub/documents/k38T6kZ59QxjypJdQzD_KQ | FFVC1156 pin functions (VRP, VREF, GC/QBC/DBC), package delays |
| `amd/UG1267_ZCU104_Evaluation_Board_User_Guide_v1.1.pdf` | ZCU104 Evaluation Board User Guide | v1.1, 2018-10-09 | docs.amd.com/api/khub/documents/g76en4BfB6qIp2gHDRkQxg | Host board: VCCO of banks 64-66, SODIMM socket. Its pin tables are wrong (finding A1). An identical copy is in `zcu104/` |
| `amd/UG572_UltraScale_Clocking_Resources_v1.11.pdf` | UltraScale Architecture Clocking Resources | v1.11, 2025-05-29 | docs.amd.com (map N1UbyXlQHGKYgPvaK7rhzQ) | MMCM -> BUFG -> bank-PLL clocking of the PHY |
| `amd/XAPP1274_Native_High-Speed_IO_Interfaces_v1.2.pdf` | Native High-Speed I/O Interfaces | v1.2, 2019-09-06 | docs.amd.com/api/khub/documents/om2Kf~mU6ToqvVkj0OzUNg | Async-receive reference design that PG188 cites |
| `amd/XAPP1330_Async_Data_Capture_HSSIO_Wizard_v1.1.pdf` | Asynchronous Data Capture Using the HSSIO Wizard | v1.1, 2018-08-03 | docs.amd.com/api/khub/documents/FTMytUCz52Bn7_luenbU8A | Async capture: clocking recommendation, VAR_LOAD/COUNT |
| `micron/CSN-33_Micron_BGA_Manufacturers_User_Guide_RevG_2020-04.pdf` | Micron BGA Manufacturer's User Guide | Rev. G, 2020-04 | assets.micron.com | Land-pad type and ratio (Table 3, p.5), teardrops for NSMD lands (p.4), stencil (Table 4), moisture sensitivity |
| `micron/CSN-34_Micron_PoP_User_Guide_RevC_2016-05.pdf` | Micron PoP User Guide | Rev. C, 2016-05 | assets.micron.com | Fine-pitch LPDDR assembly, warpage (secondary) |
| `micron/MT62F512M32D2_MT62F1G32D4_Automotive_LPDDR5_315b_RevD_2021-04.pdf` | 315b x32 Automotive LPDDR5 (short form) | Rev. D, 2021-04 | farnell.com (distributor copy) | 315-ball drawing: 0.475 mm balls on 0.48 mm SMD package pads |
| `micron/TN-00-08_Thermal_Applications_RevK_2017-01.pdf` | TN-00-08 Thermal Applications | Rev. K, 2017-01 | mirror (aliyuncs) | Case-temperature measurement method |
| `micron/TN-00-15_Recommended_Soldering_Parameters_RevF_2012-12.pdf` | TN-00-15 Recommended Soldering Parameters | Rev. F, 2012-12 | mirror (dzsc) | Pb-free reflow limits, floor life |
| `micron/TN-40-40_DDR4_Point-to-Point_Design_Guide_RevB_2016-10.pdf` | TN-40-40 DDR4 Point-to-Point Design Guide | Rev. B, 2016-10 | mirror (oldfriend.url.tw) | Context for the host DDR4 SODIMM channel |
| `micron/TN-46-14_Hardware_Tips_Point-to-Point_System_Design_RevB_2008-06.pdf` | TN-46-14 Hardware Tips for Point-to-Point System Design | Rev. B, 2008-06 | mirror (pcbartists.com) | Reference (VREF) routing and decoupling, return paths |

`other/` was empty (and no longer exists): no third-party document was needed beyond those already in `references/socket/`.

### 1.2 Used but not downloaded again (already in `references/`)

`zcu104/XTP484_ZCU104_schematic_rev1.0.pdf` (the docs.amd.com copy is login-gated and returns an empty zip),
`zcu104/XTP485_ZCU104_layout_rev1.0.pdf`, `dram/Micron_MT62F_315b_Y31M.pdf`, `dram/Micron_Y42M_LPDDR5X_315b_441b_563b_RevF.pdf`,
`dram/Micron_315b_LPDDR5X_ballout.pdf`, `jedec/JESD209-5C_LPDDR5.txt`.

### 1.3 Not obtained

| Document | Reason |
|---|---|
| Micron TN-53-06 (LPDDR4/4X point-to-point layout) and TN-53-09 (LPDDR4/4X power integrity) | No public PDF found |
| Micron TN-40-44 DDR4 PDN impedance targets (https://www.mouser.com/pdfDocs/tn4044_ddr4_pdn_impedance_targets.pdf) | The host refused the scripted download |
| Micron MT62F1G32D4 full AC/DC data sheet and IBIS model | my.micron.com login required |
| AMD AR 67565 (MIPI_DPHY_DCI on HP banks, DCI_CASCADE for banks without VRP), https://adaptivesupport.amd.com/s/article/67565 | Support portal is JavaScript-only. Its content is taken from the search abstract: an HP bank needs 240 ohm on VRP, or a DCI_CASCADE from a bank that has one |
| JEDEC MO-310 | JEDEC login required. The socket drawings in `references/socket/` carry the module dimensions |

## 2. New findings

Verdict key: **ISSUE** = a vendor rule the design does not meet and that the existing reviews do not mention.
**CHECK-ON-HARDWARE** = a rule whose impact can only be settled on the board. **OK** = met, or a deviation that is
justified for this board.

| # | Rule (source) | Design value (measured) | Verdict | Recommended action |
|---|---|---|---|---|
| A1 | Finger -> FPGA pin map must follow the host board (XTP484 rev 1.0 p.5, 6, 22; UG1267 v1.1 Table 3-4 and the bank table on p.~31) | Every one of the 76 rows in `doc/pinmap.csv` matched XTP484. I parsed FPGA ball -> net from p.5/6 and net -> socket pin from p.22 by text coordinates: 76/76 match, 0 mismatches. UG1267 v1.1 disagrees with the schematic: it lists bank 64 as DQ[0:31], bank 66 as ADDR/CTRL, and A0 = AH8, which XTP484 shows as NC. | OK (documentation trap) | Keep XTP484 and the Vivado board file as the source of truth. Do not use UG1267's pin tables for future pin-map edits. |
| A2 | Trace-to-pad joints on NSMD BGA lands should use teardrops (Micron CSN-33 Rev G p.4, "Micron recommends use of a teardrop design ... to minimize risk of cracking at the trace-pad interface") | U1 has 0.41 mm NSMD lands. 113 F.Cu trace-to-U1-pad joints (62 GND, 24 power, 27 signal), 25 of them only 0.09 mm wide. The board has no teardrop zones. | ISSUE (minor, reliability) | Acceptable for a lab card with few thermal cycles. For the next revision, enable KiCad teardrops on the U1 pads. Avoid hot-air rework of U1. (now: still no teardrops in rev 1.1.0) |
| A3 | Moisture-sensitive BGAs must be within floor life or baked before reflow (CSN-33 p.3 "Moisture Sensitivity"; TN-00-15 Rev F p.5, J-STD-020/033) | U1 is consigned. Neither `fab/README_ORDER.md` nor the CPL notes ask for a dry-pack or bake check. The DRC review asks only for X-ray. | ISSUE (process) | Ship U1 in its sealed MBB with desiccant and HIC. Add an order remark: "U1 consigned, MSL per label; bake per J-STD-033 if floor life is exceeded; Pb-free profile, peak 260 C max." (now: done in `fab/README_ORDER.md`) |
| A4 | Each signal via that changes layers needs a ground via within 50 mil (1.27 mm) (UG583 v1.29 General Memory Routing Guidelines item 11, p.29) | Of the 220 signal vias on the card's interface nets, 83 have no GND via within 1.27 mm. The worst is 3.5 mm: OUT_A.DQ15 (108.0, 92.7), OUT_A.DQ14 3.1 mm, RD_DMI1 3.1 mm, IN_A.WCK1_P 3.1 mm, IN_A.CA0 3.0 mm, OUT_A.DMI1 2.9 mm. The SI flow models a via as a lumped 0.25 pF, with no return path, so the reviews did not catch this. | ISSUE (SI margin) -> CHECK-ON-HARDWARE | No change for rev 2.1.0 at 320/100 Mb/s. On hardware, compare the per-lane write and read training windows. Lanes that are narrow and cluster near these vias point to return-path crosstalk. Next revision: add GND stitching vias next to these transitions. |
| A5 | Data/WCK/RDQS at most 2 vias per signal; a byte on one internal layer; route data on upper layers (UG583 Table 118, p.158; item 9, p.28) | Signal-net via counts: 1 via 19 nets, 2 vias 44, 3 vias 13, 4 vias 12, 5 vias 4, 6 vias 1. IN_A.DMI0/DMI1 have 5 vias on 5 layers. IN_A.DQ8/9/10/11/14 have 4 vias on 4-5 layers. IN_A.WCK0_P/N have 4. | OK (deviation; rules are for 4266+ Mb/s) | None for this revision. The routed SI at 320/100 Mb/s includes via capacitance. If a future revision targets more than 800 Mb/s, re-route to the UG583 limits. |
| A6 | Single-ended 40 ohm ±10 %, differential 75 ohm (up to 55/100 ohm under devices); maximum 2000 mil from FPGA to device (UG583 Table 118, p.158) | JLC10121H-2116 line models (`validation/jlc_10layer_line_models.json`): In5/In7 0.09 mm = 48 ohm, In2/In3 0.09 mm = 54 ohm, F.Cu 0.15 mm = 57 ohm, F.Cu 0.09 mm = 71 ohm. The host ZCU104 trace plus card trace exceeds 2000 mil (CS1 alone is 73 mm on the card). | OK (deviation, covered by the routed SI that uses these models) | None. |
| A7 | Route over a solid reference plane, not over splits or voids (UG583 items 15-18, p.30-32) | 96.5 % of signal length is over GND on the adjacent plane(s). About 120 mm is over antipad voids in breakout, resistor-array and finger areas (largest runs 2-3 mm). 27.7 mm of In7 routing has In8 +1V05 as its second reference (In6 GND above is solid). RESET_n has 0.7 mm on B.Cu over +1V05. `fab/README_ORDER.md` says "In1/In4/In6/In8 carry GND reference planes", but In8 also carries +1V05 pours and In5 carries +1V05/+1V2 pours. | OK (minor) | None for this revision. Correct the stackup sentence in the order notes when that file is next edited. (now: done) |
| A8 | MIPI_DPHY_DCI_HS input: VICM 70-330 mV, VID >= 70 mV (DS925 v1.30 Table 17, p.21) | Read lines swing about 0-0.27 V at the FPGA. The common-mode floor is about VREF_RD/2, which is 79 mV at the nominal 158 mV, so only 9 mV of margin. The SI flow checks VICM (`sim/si/tools/analyze.py`). However, the VREF_RD DAC is trained per board and has no lower bound. | CHECK-ON-HARDWARE (firmware) | In read training, clamp the VREF_RD sweep to at least 140 mV, so the DC common mode stays >= 70 mV, and at most about 200 mV, so VID >= 70 mV on the high level. Log the chosen code per board. |
| A9 | "Asynchronous (ASYNC) mode requires AC coupling" (PG188 v3.6 p.30). XAPP1330 v1.1 p.2 recommends clocking straight from a GC_QBC pin into the bank PLL and notes that a cascaded MMCM "might limit the performance". | Bank-65 reads are DC-coupled, unencoded LPDDR5 data. A read burst can be all 0s or all 1s. The PHY clock path is CLK_300 -> MMCM_X1Y2 -> BUFG -> bank PLL. The trained mode uses a fixed sample class, and module mode runs the 3x CDR. | CHECK-ON-HARDWARE | On the bench, check that reads with long constant runs (all-0, all-1, MR reads) are captured correctly in the trained fixed-phase mode, and that the CDR is not relied on during real bursts. The jitter cost of the MMCM is negligible at 100 Mb/s. |
| A10 | VREF should track VDDQ and be decoupled at each receiver (TN-46-14 Rev B p.3) | VREF_RD comes from the DAC53401, which is referenced to 3.3 V and does not track VDDQ. It passes through 100 R / 49.9 R to one node with a single 100 nF (C110), then through 18 x 39 R to the host N pins. The release report already records the shared-node bounce. VDDQ tolerance (0.491-0.506 V, about ±1.5 %) moves the read high level by about ±4 mV. | OK (known; tracking error is small) | None. |
| A11 | HP pins must stay within -0.2 V to VCCO+0.2 V (recommended) and -0.55 V to VCCO+0.55 V (absolute) (DS925 Tables 1/2, p.1/3) | VREF_RD maximum with the DAC at full scale: 3.3 V x 49.9/149.9 = 1.10 V. That is within VCCO+0.2 V, but above VIHHS = 0.46 V. The DAC53401 powers up at zero scale. | OK | None. Note that a mis-programmed DAC cannot damage bank 65. |
| A12 | JEDEC Z(f) at the DRAM balls: VDD2H/VDDQ 40 mOhm (DC-2 MHz) and 80 mOhm (to 20 MHz); VDD1 100/170 mOhm (JESD209-5C Table 408) | The reviews cover DC IR and LDO transients only. A first-order estimate for VDDQ: 12 x 4.7 uF + 2 x 22 uF (57.7 uF effective worst case) gives about 2 mOhm at 2 MHz. With about 0.1-0.15 nH of mounted ESL plus via inductance, it is about 13-19 mOhm at 20 MHz. VDD2H has 22 x 4.7 uF + 2 x 22 uF. VDD1 has only 4 x 4.7 uF at the BGA plus 2 x 22 uF at U20. (now: rev 1.1.0 adds C119, a third 22 uF at the U22 output) | CHECK-ON-HARDWARE (low risk) | Optional: a PDN AC extraction, or a VNA two-port shunt measurement at a VDDQ/VDD2H ball-side test point on the first board. |
| A13 | RESET_n: 4.99 kOhm to GND (UG583 Table 116, p.156). RESET_n <= 0.2 x VDD2H while power ramps (JESD209-5C 4.1.1) | Card: 10 k pull-down (R127) after 1 k series. Host: no pull resistor on DDR4_SODIMM_RESET_B (XTP484 p.22). Worst-case leakage (FPGA 15 uA, DS925 Table 4, plus DRAM 8 uA, JESD Table 409) x 10 k = 0.23 V, which is about 0.2 x VDD2H. This only applies while the FPGA is unconfigured and the rails are up, which cannot happen: PWR_EN needs a configured FPGA, and the host CKE0 has a 4.7 k pull-down (R506). | OK | Optional: 4.99 k in the next revision. |
| A14 | U1 land: never larger than the package pad. SMD package pad -> NSMD PCB land at 1:0.8; 80 % of the ball on the land (CSN-33 Table 3, p.5; p.9) | Package: 0.475 mm balls on 0.48 mm SMD pads (Micron 315b drawing). Card: 0.41 mm NSMD land (0.85 of the package pad), 0.51 mm mask. 0.41 mm covers 86 % of the 0.475 mm ball. | OK | This closes the "attention: not the vendor pattern" item in the DRC/mechanical review: the land matches Micron's NSMD rule (0.38-0.48 mm). |
| A15 | Stencil aperture 1:1 to the land; aperture/thickness >= 3:1; area ratio >= 0.66 (CSN-33 Table 4, p.8; IPC-7525) | 0.41 mm aperture: 3.4:1 at a 0.12 mm stencil. Area ratio 0.41/(4 x 0.12) = 0.85. | OK | Keep the stencil at 0.12 mm or thinner (JLC default 0.10-0.12 mm). |
| A16 | ZQ: 240 ohm ±1 % from ZQ to VDDQ (JESD209-5C 4.2.1.5; UG583 Table 116) | R69 is 240 R 0402 (LCSC C25094, UNI-ROYAL 0402WGF, 1 %; now YAGEO RC0402FR-07240RL C114755, rev 1.1.0) between H15 ZQ_A and +0V5. The package has one ZQ_A shared by its dies (Micron 315b ballout). | OK | None. |
| A17 | CS: 240 ohm to GND (UG583 Table 116) | Each of CS0 and CS1 has its own 220 R shunt. | OK | None. |
| A18 | VRP 240 ohm for MIPI_DPHY_DCI; DCI cascade master and slaves in one HP column with the same VCCO and compatible VREF (UG571 v1.16 p.131 and p.23; AR 67565) | ZCU104: VRP_64 = R622 240 R 1 %, VRP_65 = R621 240 R 1 % (XTP484 p.5), and VRP_66 is fitted too (p.6). `top.xdc`: DCI_CASCADE {65 66} on bank 64, all at VCC1V2 (now: set in the `rx_std.xdc` that `fpga/build.tcl` generates, not in `fpga/top.xdc`). Bank 64 uses INTERNAL_VREF 0.6 V for PGOOD (SSTL12); banks 65/66 have no VREF inputs. Vivado DRC: 0 violations. | OK | None. |
| A19 | When INTERNAL_VREF is used, tie the VREF pin to GND through 500 R-1 k (UG583 item 13, p.30) | ZCU104: VREF_64 = R683 1 k and VREF_65 = R681 1 k to GND (XTP484 p.5). | OK | None. |
| A20 | Configuration-time pull-ups (DS925 Table 4: IRPU up to 100 uA at 1.2 V) must not enable module power | ZCU104 PUDC_B = 1 k to VCC1V8 (R397, XTP484 p.3), so pull-ups are off during configuration. PWR_EN sees the 10 k module pull-down plus the host 4.7 k (R506). | OK | None. |
| A21 | Async RX: bitslice 0 of each nibble is inferred, TX and RX cannot share a serial-mode nibble, rate at most 1300 Mb/s (PG188 p.21/24; DS925 p.52 note 5) | Bank 65 is RX only. The pin-0 fingers (DM0-3_n, 12/33/54/75) are unconnected on the card. The rate is 1200 Mb/s. | OK | None. |
| A22 | IBUFDS_DPHY: DIFF_TERM allowed FALSE; HP banks only; VCCO 1.2 V (UG571 Table 1-75, p.131; DS925 Table 17 note 12) | DIFF_TERM FALSE, bank 65 HP at 1.2 V. | OK | None. |
| A23 | IDELAY/ODELAY tap 2.1-12 ps (DS925 IODELAY table) vs the 3.95-4.02 ps/tap simulation model | With the 2.1 ps worst case: 511 TX taps cover 1.07 ns, more than one 625 ps launch slot. The RX cascade covers about 2.1 ns, more than one 833 ps sample class. The coarse step always fits inside the fine range. | OK | None. Report tap values, not ps, in training logs. |
| A24 | Thermal: case temperature by thermocouple at the top centre; TJ = TC + PC x thetaJC (TN-00-08 Rev K p.4) | TMP117 (U10) is on the bottom side and reads board temperature. The README already asks for a DRAM case-temperature measurement. | OK (already planned) | Use a thermocouple on U1's top centre for the bring-up measurement, not U10. |
| A25 | Signal:GND via ratio 2:1 or better on the FPGA side (UG583 Table 118) | Card: 282 signal vias, 307 GND vias. | OK | None. |

## 3. Already handled by the existing reviews (confirmed against vendor documents, nothing new)

| Topic | Vendor rule | Where it is handled |
|---|---|---|
| FPGA VCCO 1.2 V vs the 1.0 V that UG583 Table 117 assumes for LPDDR5; overshoot into 0.5 V LVSTL inputs | UG583 Table 117; JESD209-5C Table 416 (0.3 V / 0.1 V-ns) | Series/shunt dividers; CS1 at 60 ohm for the Table 416 area (`fpga/top.xdc`, routed SI) |
| Skew limits ±50/±20/2 ps | UG583 Table 119 | Per-pin launch-slot and VAR_LOAD training (release report section 2); intra-pair skew 0.2-10.4 ps (DRC review) |
| VICM/VID of the pseudo-differential reads | DS925 Table 17 | `sim/si/tools/analyze.py` strict and DC readings (see A8 for the firmware bound) |
| Broadside coupling In2/In3 (DQ7-DQ1) | UG583 spacing rules, Table 118 | Release report: the 400 Mb/s write limit |
| Via-in-pad POFV, 188 vias in U1 pads, M5 off-centre via | CSN-33 land planarity | DRC/mechanical review and order notes |
| Card thickness, bevel, finger setback, key and latch notches | Socket drawings (MO-310 dimensions) | DRC/mechanical review |
| Rail levels, LDO dropout, BIAS, CBIAS, regulator Tj | JESD209-5C Table 408 DC windows | Power review (R110/R114/C90) |
| Power-up/down order, RESET_n timing, CS low during power-off | JESD209-5C 4.1.1-4.1.5 | 456/456 ngspice sequencing cases; TB_MODE 1 |
| Equivalent DRAM models unavailable | - | Release blocker, ACCEPTED |

## 4. Summary of actions

- **ISSUE A3 (process)**: add an MSL/bake and reflow-profile remark for the consigned U1 before the PCBA order.
- **ISSUE A2 (reliability, next revision)**: add teardrops to the 113 trace-to-pad joints at U1.
- **ISSUE A4 -> CHECK-ON-HARDWARE**: 83 of 220 layer-change signal vias have no GND via within 1.27 mm (UG583 item 11).
  Correlate this with the per-lane training windows, and add stitching vias in the next revision.
- **CHECK-ON-HARDWARE A8**: bound the VREF_RD training sweep to about 140-200 mV.
- **CHECK-ON-HARDWARE A9**: confirm that reads with long constant runs work in the trained fixed-phase mode.
- **CHECK-ON-HARDWARE A12 (optional)**: measure or simulate the rail Z(f) against JESD209-5C Table 408.
- Documentation: UG1267 v1.1's SODIMM pin tables contradict XTP484. The design follows XTP484 (verified 76/76).

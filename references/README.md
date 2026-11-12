# References

Documents and models used to design and verify the LPDDR5 testbed (developed as internal rev 2.x, released as rev 1.0.0 / 1.1.0), copied here for future work.
Vendor PCB-design guidelines collected afterwards are indexed in `vendor_index.json` and reviewed in `VENDOR_GUIDELINES_REVIEW.md`
(folders `amd/`, `micron/`; the empty `other/` was removed). Every PDF has a `.txt` extract where one was made, for grep. Zip archives are git-ignored.

| File | Document | Used for | Source |
|---|---|---|---|
| `jedec/JESD209-5C_LPDDR5.txt` | JEDEC JESD209-5C LPDDR5 standard (text extract of a purchased copy; licence: single user, do not redistribute outside the team) | Power-up/down order (Tables 19/22), overshoot (Table 416), Rx masks (Tables 466/467), tCK range, tWCK2DQI/O drift (Table 462); U1 ball map (16.3.2, 315-ball 2CHx32, MO-338A), single-rail VDD2 / MR13 OP[7] (4.1.2), ZQ 240 ohm (4.2.1.5), RESET_n levels (Table 414) | purchased |
| `fpga/UG571_UltraScale_SelectIO.pdf` | AMD UG571 UltraScale SelectIO Resources | Native-mode bitslices, VAR_LOAD delays, EN_VTC, DCI | https://docs.amd.com/r/en-US/ug571-ultrascale-selectio |
| `zcu104/UG1267_ZCU104_user_guide_v1.1.pdf` | AMD UG1267 ZCU104 Evaluation Board User Guide v1.1 | SO-DIMM socket, clocks, bank voltages | https://docs.amd.com/v/u/en-US/ug1267-zcu104-eval-bd |
| `zcu104/XTP484_ZCU104_schematic_rev1.0.pdf` | ZCU104 rev 1.0 schematic (XTP484) | Host connections of every SO-DIMM finger, PUDC_B, 1.2 V rail (IRPS5401) | https://www.xilinx.com/member/forms/download/design-license.html?filename=zcu104-schematic-xtp484.zip |
| `zcu104/XTP485_ZCU104_layout_rev1.0.pdf` | ZCU104 rev 1.0 Allegro layout plots (XTP485) | Host trace geometry | https://docs.amd.com/v/u/en-US/ZCU104-Allegro-Board-XTP485 |
| `zcu104/RDF0439_ZCU104_allegro_board_source.zip` | ZCU104 Allegro board source (RDF0439, binary .brd, git-ignored zip) | Not machine-read (needs Allegro); kept for reference | AMD ZCU104 design files page |
| `zcu104/odb/8955_1280961_hw-z1-zcu104_revision-1_0_odb.tgz` | ZCU104 rev 1.0 layout as ODB++ (from the RDF0440 Gerber package, `odb++/` folder; git-ignored) | Host trace sections for the SI model: `scripts/zcu104_board_extract.py` -> `sim/si/results/zcu104_host_paths.json` (method: `doc/ZCU104_BOARD_DATA.md`) | AMD ZCU104 design files page (RDF0440 Gerber files) |
| `zcu104/RDF0437_ZCU104_BOM_rev1.0.pdf` | ZCU104 rev 1.0 BOM | Host regulators and socket part | AMD ZCU104 design files page |
| `zcu104/ZCU104_stackup_Whizz8955.txt` | ZCU104 stackup (text extract from the Gerber package) | Host trace impedance and delay | AMD ZCU104 Gerber package |
| `zcu104/IRPS5401_PMIC_datasheet.pdf` | Infineon IRPS5401 PMIC data sheet | Host 1.2 V rail tolerance for the U21 dropout check | https://www.infineon.com/dgdl/irps5401.pdf |
| `dram/Micron_Y42M_LPDDR5X_315b_441b_563b_RevF.pdf` | Micron Y42M LPDDR5X addendum Rev. F (Digi-Key mirror) | CS/DQ Rx masks, IDD tables, 315-ball maps | https://mm.digikey.com/Volume0/opasdata/d220001/medias/docus/6592/315b441b563by42msdpddpqdp8dplpddr5xitwt.pdf |
| `dram/Micron_Y31M_LPDDR5_multi_package.pdf` | Micron Y31M LPDDR5 multi-package addendum | MT62F1G32D4DR pin-compatibility (315 balls), IDD | https://mm.digikey.com/Volume0/opasdata/d220001/medias/docus/8885/441b-315b-y31m-lpddr5-multi-pkg.pdf |
| `dram/Micron_MT62F_315b_Y31M.pdf` | Micron MT62F 315-ball Y31M addendum | Ball map, package | Digi-Key / Micron |
| `dram/Micron_315b_LPDDR5X_ballout.pdf` | Micron Y52Q LPDDR5X data sheet (MT62F512M32D1 / MT62F512M64D2; 315-, 441-, 496- and 561-ball maps) | Ball-map cross-check (Figure 7, 315-ball dual-channel) | Micron |
| `dram/Micron_315b_ballmap.png` | Micron 315-ball map render | Visual pin-1/ball check | derived |
| `dram/SKhynix_LPDDR5_candidate_datasheet.pdf` | SK hynix 12 GB LPDDR5 496-ball PoP specification (Oct 2020; not the U1 part), with the JEDEC LPDDR5 text reproduced | Supplies; its copy of the JEDEC 315-ball 2CHx32 ballout (16.3.2, p. 573: matches JEDEC, but the row letters skip U and add AB). The exact H9JCNNNCP3MLYR-N6E sheet is not public | web |
| `dram/Rayson_RS1G64LP5_LPDDR5X_496b_PoP.pdf` | Rayson RS1G64LP5 LPDDR5X data sheet (496-ball quad-channel PoP; the file was earlier misnamed 315b) | Third-party ball names only; not a U1 (315-ball) reference | LCSC |
| `dram/Acer_service_manual_memory_AVL_extract.txt` | Acer service manual (text): lists Hynix H9JCNNNCP3MLYR-N6E and Micron MT62F1G32D4DR-031 WT:B as alternatives | Evidence for the Micron alternative | https://documents.cdn.ifixit.com/aqAP5mMubp5dfyFf.pdf |
| `parts/TI_TPS74801.pdf` | TI TPS74801 LDO | U21 VDD2H (and U20 VDD1 until 2026-10-17): accuracy, dropout, sequencing | https://www.ti.com/lit/ds/symlink/tps748.pdf |
| `parts/TI_TPS74701.pdf` (+ .txt) | TI TPS74701 LDO, SBVS099H | U20 VDD1 since 2026-10-17 (`validation/eco_20261017_u20_tps74701.json`) | https://www.ti.com/lit/ds/symlink/tps74701.pdf |
| `parts/TI_TPS7A84A.pdf` | TI TPS7A84A LDO | U22 VDDQ 0.5 V | https://www.ti.com/lit/ds/symlink/tps7a84a.pdf |
| `parts/TI_SN74AXC1T45.pdf` | TI SN74AXC1T45 level shifter | U23 PWR_EN 1.2 V -> 3.3 V | https://www.ti.com/lit/ds/symlink/sn74axc1t45.pdf |
| `parts/TI_DAC53401.pdf` | TI DAC53401 DAC | U11 VREF_RD | https://www.ti.com/lit/ds/symlink/dac53401.pdf |
| `parts/TI_TMP117.pdf` | TI TMP117 temperature sensor | U10 (thermal pad left unsoldered, 10.1) | https://www.ti.com/lit/ds/symlink/tmp117.pdf |
| `parts/Samsung_CL05A_X5R_series.pdf` | Samsung CL05A MLCC series | DC-bias derating of the 0402 X5R parts | Samsung EM |
| `parts/Samsung_CL05A475MP5NRNC_dcbias.json` | Samsung CL05A475MP5NRNC DC-bias curve data | C90 / 4.7 uF derating | Samsung EM part page |
| `parts/Samsung_CL05A225KP5NSNC.pdf` | Samsung CL05A225KP5NSNC | C99 | Samsung EM |
| `parts/UniRoyal_0402WGF.pdf` | UNI-ROYAL 0402 thick-film resistors | Resistor tolerance/TCR | LCSC |
| `parts/UniRoyal_4D02_array.pdf` | UNI-ROYAL 4D02 resistor arrays | Shunt arrays | LCSC |
| `parts/Bourns_CR0402.pdf` | Bourns CR0402 resistors | R125-R127 etc. | Bourns |
| `socket/Foxconn_DDR4_SODIMM_socket.pdf` | Foxconn DDR4 SO-DIMM socket drawing | Card thickness 1.10-1.30 mm, keep-outs, key | https://wmsc.lcsc.com/wmsc/upload/file/pdf/v2/lcsc/FOXCONN-AS0A821-H4SB-7H_C225720.pdf |
| `socket/DDR4_SODIMM_socket_comet28777.pdf` | DDR4 SO-DIMM socket drawing (second vendor) | Mechanical cross-check | web |
| `models/tps7a84/` | TI TPS7A84 PSpice transient model | Power-sequencing sims of U22 | https://www.ti.com/product/TPS7A84A#design-tools-simulation |
| `models/dram/` | LPDDR4X IBIS proxies (Alliance, Winbond) and the Versal LVSTL extract, with URLs in its README | DRAM driver/package proxy model (`doc/DRAM_MODEL.md`) | see `models/dram/README.md` |
| `models/zynquplus_ibis_extract/` | Zynq UltraScale+ IBIS models used by the SI flow (extracted from Vivado 2023.1 data/parts/xilinx/zynquplus/public/ibis/zynquplus.ibs, 124 MB) | FPGA drivers/receivers: HP_SSTL12/POD12 40/48/60, MIPI_DPHY_DCI, LVCMOS12 | Vivado install |

Not public (needs a my.micron.com account or an NDA): Micron "General LPDDR5/LPDDR5X Specifications 2: AC/DC and
Interface Specifications" and the MT62F1G32D4DR / Y31M HSPICE/IBIS models; the exact SK hynix H9JCNNNCP3MLYR-N6E data
sheet and models. Data sheets of the 2026-10-17 U1 parts (LPDDR5X H58G56BK7BX068N, K3KL8L80CM-MGCT) are not in
`references/` either. The SI flow uses an inferred DRAM model instead (sim/si/tools/cases.py DRAM_DRV, chan.py parasitics;
since 2026-10-13 the proxy model in `sim/si/dram_model/`, `doc/DRAM_MODEL.md`).

## Reviews against external guidance

| Folder | Contents | Review |
|---|---|---|
| `amd/`, `micron/` | AMD UG583, UG571, DS925, PG188, PG150, UG1075, XAPP1274/1330, ...; Micron BGA/soldering/thermal notes, MT62F short sheet | `VENDOR_GUIDELINES_REVIEW.md`, `vendor_index.json` |
| `jlc/` | JLCPCB capabilities, gold fingers, PCBA, stackups, via covering / POFV, panelisation, thickness, parts sourcing (rendered PDF + text) | `jlc/README.md` |
| `sodimm_module/` | DDR4 SO-DIMM module drawing (Micron), TE application spec and socket sheets, SPD EEPROMs, PCB-fab edge-connector guides, Antmicro lpddr4-testbed sources | `sodimm_module/MODULE_SIDE_REVIEW.md`, `index.json` |
| `hs_design/` | TI (SPRACN9, SPRAD66, SPRABI1, SPRAAR7, SCAA082, SZZA009, SLVA118, SWPA222), ADI MT-101, Würth ANP098, AMD XAPP623, Altera AN958 / AN224 / SI white paper | `hs_design/HS_DESIGN_REVIEW.md`, `index.json` |

**Caution, `zcu104/UG1267_ZCU104_user_guide_v1.1`:** the FPGA-ball column of its DDR4 SO-DIMM tables is shifted by one bank (for example it gives
DQ28 = AN18 and CLK_300 = AH12 / AJ12; the schematic XTP484 and the Vivado board file say AP22 and AH18 / AH17). The pin map of this card
(`design/ref/zcu104_sodimm_pinmap.csv`) follows the schematic `zcu104/XTP484`; do not take FPGA balls from UG1267.

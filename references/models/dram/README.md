# DRAM-side I/O models (proxies for the LPDDR5 DRAM)

Used by `sim/si/dram_model/` (see `doc/DRAM_MODEL.md`). No LPDDR5 DRAM IBIS model is publicly downloadable;
everything below is LPDDR4X DRAM IBIS (same LVSTL output stage, 0.6 V VDDQ) or SoC-side LVSTL IBIS.
Downloaded 2026-10-13 unless noted. `*.zip` archives are git-ignored by `lpddr5-testbed/board/.gitignore`
(`references/**/*.zip`); the extracted `.ibs` files that the scripts read are kept.

| Folder / file | What | Source URL | Licence / terms |
|---|---|---|---|
| `alliance_lpddr4x/as4c1g32md4v-046bin.ibs` | Alliance Memory AS4C1G32MD4V-046BIN, 32 Gb LPDDR4X x32 2ch, **dual-rank** (CS0/CS1, 2 dies on every DQ), IBIS 5.0, rev 1.0 2024-04-03. Primary V-I / Ku-Kd shape proxy and typ package. | https://www.alliancememory.com/wp-content/uploads/as4c1g32md4v-046bin.ibs | Free download, no login. File [Disclaimer]: "provided as is", customers may modify to fit their application; (c) 2024 Alliance Memory |
| `alliance_lpddr4x/as4c512m32md4v-046bin.ibs` | AS4C512M32MD4V-046BIN 16 Gb, single-rank, same die models | https://www.alliancememory.com/wp-content/uploads/as4c512m32md4v-046bin.ibs | as above |
| `alliance_lpddr4x/AS4C512M32MD4VA-046BIN.ibs` | AS4C512M32MD4VA-046BIN 16 Gb (2025-07-29), package high bound | https://www.alliancememory.com/wp-content/uploads/AS4C512M32MD4VA-046BIN.ibs | as above |
| `winbond_lpddr4x/w66dq2nquafi_a001.ibs` | Winbond W66DQ2NQUAFI, 8 Gb LPDDR4X DDP x32 (2ch x16), -40..95 C, IBIS 5.0 rev 001 2019-10-29. 42 output models (PDDS 40..240 x SoC ODT 40..240/off, VOH = 0.5 / 0.6 VDDQ), ODT 40..240 for DQ/CA/CS/CK, **coupled per-ball package matrices** ([Define Package Model], L/C/R sparse Maxwell matrices). Second-vendor V-I / package / C_comp / threshold reference and cross-validation target. | archive `W66xQxNxUAxI_ibis.zip` from https://www.winbond.com/resource-files/W66xQxNxUAxI_ibis.zip (listed at https://www.winbond.com/hq/support/documentation/levelOne.jsp?__locale=en&DocNo=DA03-GAA106) | Public "level one" document, no login. File [Disclaimer]: "AS IS", no warranty; (c) 2019 Winbond Electronics Corp., all rights reserved (reference use; do not redistribute outside the project) |
| `winbond_lpddr4x/w66cq2nquaf{a,k,s}_a001.ibs` | Winbond W66CQ2NQUAFA / FK / FS, 4 Gb LPDDR4X DDP automotive, IBIS rev 001 2021-12-23. Identical except the hot corner of the `min` column: **95 / 105 / 125 C** at VDDQ 0.57 V. Used to isolate temperature sensitivity (RONPD +0.128 %/C, RTT +0.133 %/C, pull-up +0.068 %/C, edges flat). [Pin] R/L/C per ball. | archive `W66xQxNxUAxx_ibis_Automotive.zip` from https://www.winbond.com/resource-files/W66xQxNxUAxx_ibis_Automotive.zip (DocNo DA03-GAA107.9) | as above, (c) 2021 Winbond |
| `winbond_lpddr4x/*.zip` | the two original archives (4 + 18 `.ibs` files) | as above | as above |
| `winbond_lpddr4x/SHA256SUMS` | hashes of the files as downloaded | | |
| `versal_lvstl_extract/X5_LVSTL0{5,6}_*.txt` | AMD Versal XPIO LVSTL05/06 output (40/48/60 ohm) and ODT-input models, extracted [Model] blocks. **Controller (SoC) side**, not DRAM: used only as a second opinion on LVSTL pull-up/pull-down law shapes at 0.5 V. | Vivado 2026.1 install: `/tools/Xilinx/2026.1/data/parts/xilinx/versal/public/ibis/versal.ibs` (model at line 1292506) | AMD/Xilinx public IBIS shipped with Vivado |

SHA-256 of the Alliance files:
```
5fc0341b58e01e996ea855073879d913eeb0741b2ebaf13fe64e5f507511b9b4  as4c1g32md4v-046bin.ibs
2d75e53c185913421cce03a245da71c599a37374b8f5eac307e2ae270a88cc77  as4c512m32md4v-046bin.ibs
7c635763f677becaad1718457cae99e75aa513a4cd88fbf3f426c5d6182f5c21  AS4C512M32MD4VA-046BIN.ibs
```

Other Winbond LPDDR4/4X IBIS archives found on the same site (not downloaded into the repo; the 2025 `W66xQxNxxAxJ_ibis.zip`
holds nested zips of the same W66 x16 die family): DocNo DA03-GAA101.6 (`W66xMxNxUAxx_ibis.zip`), DA03-GAA107.19
(`W66xQxNxxAxJ_ibis.zip`), DA03-JAA127.1 (`W66xPxRxxAxJ_ibis.7z`). Search: https://www.winbond.com/hq/search/?q=IBIS%20LPDDR4

## Searched, not obtainable without an account / NDA (2026-10-13)

| What | Where | What the user would need to do |
|---|---|---|
| Micron MT62F1G32D4DR-031 WT:B (Y31M die) IBIS / HSPICE models, and "General LPDDR5/LPDDR5X Specifications 2: AC/DC and Interface" | micron.com part page (https://www.micron.com/products/memory/dram-components/lpddr5/part-catalog/part-detail/mt62f1g32d4dr-031-wt-b) -> Documents / Simulation models; requires a my.micron.com account and a per-request approval (DigiKey forum: "Micron is personally reviewing every request", https://forum.digikey.com/t/ibis-model-for-lpddr4-from-micron/26268) | Register at micron.com with a company e-mail, request the IBIS model for MT62F1G32D4DR-031 WT:B (or ask a Micron FAE / distributor FAE). Drop the `.ibs` into this folder; `sim/si/dram_model/ibis_read.py` reads it directly and `proxy_survey.py` can survey it after adding a namer. |
| SK hynix H9JCNNNCP3MLYR-N6E IBIS + full data sheet | no public download; SK hynix customer portal / sales (https://product.skhynix.com) | Request through SK hynix sales / distributor under NDA |
| Samsung LPDDR5 IBIS | semiconductor.samsung.com -> support, account + NDA | as above |
| MathWorks Signal Integrity Toolbox "LPDDR5 architectural kit" (generic SDRAM buffer models, `LPDDR5.pdf`) | https://www.mathworks.com/help/signal-integrity/ug/lpddr5.html | Signal Integrity Toolbox licence; `openSignalIntegrityKit("LPDDR5")` |
| Keysight ADS Memory Designer LPDDR5 example models | ADS install | ADS licence |
| NVIDIA Jetson Orin (LPDDR5 on module) SI kit DA-11143 | Jetson download center (developer login); models are attached inside the PDF (forum 301569) | NVIDIA developer account; contents are SoC-side, DRAM models not confirmed |

Searched with no LPDDR5 DRAM model found: IBIS-ORG model list (https://ibis.org/models/), GitHub (repositories `lpddr5`,
`ibis`; `antmicro/lpddr5-testbed` has no SI models), NXP i.MX 93/95, TI Jacinto/AM6x e2e, Rockchip RK3588 / Radxa,
Intel/Altera Agilex 5 EMIF (FPGA-side IBIS-AMI only), MediaTek Genio / Qualcomm (portal), ISSI, Nanya, CXMT, Rayson,
Yemassemi (315b data sheet, no models), Farnell/DigiKey Micron automotive LPDDR5/5X addenda (no electrical tables).

# JLCPCB documents (retrieved 2026-10-02)

Each page is saved twice: `<name>.pdf` is a rendered capture (headless Firefox, full page, split into pages),
`<name>.txt` is the text of the server-rendered HTML including the collapsed table sections (with the source URL on line 1).
JLC's site renders with JavaScript, so a plain HTML download is not readable; these two forms are.

| File | Page | Used for |
|---|---|---|
| `jlc_pcb_capabilities` | https://jlcpcb.com/capabilities/pcb-capabilities | thickness tolerance, drill/via, trace/space, outline tolerance, via-in-pad process |
| `jlc_gold_fingers_article` | https://jlcpcb.com/help/article/jlcpcb-gold-fingers | finger plating, bevel angle and copper setback, mask opening, minimum board size |
| `jlc_assembly_capabilities` | https://jlcpcb.com/capabilities/pcb-assembly-capabilities | Standard PCBA board size (70 x 70 mm minimum), gold fingers in PCBA |
| `jlc_impedance_stackups` | https://jlcpcb.com/impedance | JLC10121H-2116 stackup, dielectric constants |
| `jlc_impedance_calculator` | https://jlcpcb.com/capabilities/impedance-calculator | impedance tolerance, panel limits |
| `jlc_panelization` | https://jlcpcb.com/help/article/pcb-panelization | panel / V-cut rules (the card uses tabs, no V-cut on the finger edge) |
| `jlc_via_covering` | https://jlcpcb.com/help/article/pcb-via-covering | via covering options, epoxy filled and capped |
| `jlc_pofv_via_in_pad_blog` | https://jlcpcb.com/blog/Free-Via-in-Pad-on-6-20-Layer-PCBs-with-POFV | POFV design requirements (hole/diameter table, annular ring) |
| `jlc_pcba_parts_sourcing` | https://jlcpcb.com/help/article/pcba-parts-sourcing-instruction | consigned parts (U1) |
| `jlc_pcb_thickness` | https://jlcpcb.com/resources/pcb-thickness | thickness options and tolerance |

Not retrievable: the gold-finger FAQ (https://jlcpcb.com/quote/pcbOrderFaq/Gold%20Fingers) returns an empty page.

## Rules that bear on this card

| Rule (source) | Card | Verdict |
|---|---|---|
| Thickness tolerance +/-10 % for >= 1.0 mm (capabilities, thickness page): 1.08-1.32 mm for 1.2 mm | socket needs 1.10-1.30 mm | order remark + CAM question 1 (already in `fab/README_ORDER.md`) |
| Gold fingers only with ENIG (gold-finger article 1) | ENIG ordered | OK |
| No bevel on boards smaller than 50 x 50 mm, and no bevel on V-cut finger edges (article 2, 3, last line) | panel 83.7 x 70.3 mm, finger edge is the outer panel edge, tabs not V-cut | OK |
| Bevel copper setback (article table, 1.6 mm board): 30 +/- 5 deg -> about 1.13 mm; 45 +/- 5 deg -> about 0.74 mm. JLC pulls finger copper back to this setback if the design is closer, and recommends 30 deg | fingers start 0.20 mm from the edge | **Not compatible with a SO-DIMM**: the socket's lower contact row touches about 0.9 mm from the edge (`../sodimm_module/MODULE_SIDE_REVIEW.md`), and JEDEC modules have only a 0.25 mm x 45 deg chamfer. The order notes now ask for a chamfer of at most 0.25 mm with no copper pull-back beyond 0.30 mm, or no bevel (remark + CAM question 2) |
| Finger area must have the solder mask fully open (article note) | fully open, one window per side | OK |
| Standard PCBA board size 70 x 70 mm minimum (assembly capabilities) | 83.7 x 70.3 mm panel | OK |
| Via-in-pad process: epoxy filled and capped, default for >= 6 layers; "compatible with via diameters from 0.15 to 0.55 mm" (capabilities) vs POFV design table hole 0.2-0.5 mm, annular ring >= 0.05 mm (POFV blog) | U1 has 187 in-pad vias: 173 x 0.15/0.40, 10 x 0.20/0.40, 4 x 0.15/0.35 mm | **Ambiguous for the 177 vias with 0.15 mm drills.** Ask CAM (question 4). If JLC requires 0.20 mm: enlarging those drills (same pad sizes) passes DRC except 4 annular-ring warnings against the project's own rule on the 0.35 mm pads (0.075 mm ring, which JLC's POFV table allows) |
| Min. via hole/diameter 0.15/0.25 mm multilayer; 0.15 mm holes cost more (capabilities) | 0.15/0.35 and 0.15/0.40 used | OK (priced option) |
| Outline tolerance +/-0.1 mm precision routing (capabilities) | key 1.00 +/-0.05 requested | remark already in the order notes; CAM to confirm |

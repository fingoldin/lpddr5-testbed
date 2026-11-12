<!-- Proposed replacement for the repository's README.md, which still describes an earlier plan (commands on bank 64, 16 DQ,
     PWR_EN / PGOOD on fingers, 320 Mb/s writes). README.md itself was not edited. Every figure below is taken from design/DESIGN.md
     and design/netlist.json as built (R0.2). -->

# LPDDR5 command-bus test card for the ZCU104

One LPDDR5 chip (SK hynix H58G56BK7BX068N, JEDEC 315-ball map) on a card in the DDR4 SO-DIMM form factor (69.6 x 30.0 mm, 260 fingers)
that plugs into the PL memory socket J1 of the ZCU104 and is driven by the FPGA through a dedicated pin map. It is a custom LPDDR5 card,
not a DDR4 memory module: do not plug it into anything but a ZCU104 with matching gateware.

One channel (A), one byte (DQ0-7), one rank are wired. The Samsung K3KL8L80CM-MGCT named in the earlier plan has not been checked against
this card.

### Goals

Implement a custom PHY and controller on the ZCU104 FPGA and talk to the LPDDR5 at the highest command rate possible: CK up to 800 MHz,
1.6 Gb/s on the command bus. The command bus is the priority bus of the board. Reads and writes may be slow; the card is designed for
100 Mb/s (the original floor was 20 Mb/s).

### High-level signal routing

FPGA <-> LPDDR5, channel A, byte 0, rank 0 (all three banks at 1.2 V):

- **Bank 65** (byte group T0, fingers 66-84): command bus CK, CA0-6, CS0 through 91 / 68 ohm dividers (CS: 33 / 100 ohm) at the fingers,
  then an In3 stripline between two ground planes. The ten nets are delay-matched on the card, FPGA die pad to DRAM ball (641.8 ps).
- **Bank 64** (fingers 143-161): write data DQ0-7 through 240 / 150 ohm dividers; WCK on fingers 137 / 139 through 91 / 68 ohm,
  **polarity inverted** on the host pair.
- **Bank 66** (fingers 182-216): read data DQ0-7 direct, without any part, on the P pin of eight differential pairs; the N pins sit on a
  common reference VREF_RD (39 ohm + 100 nF per N finger). VREF_RD is 0.187 V without any I2C traffic and adjustable 0.136 .. 0.631 V with
  the DAC53401 on the card (I2C address 0x48 on the socket's SPD bus, fingers 253 / 254). RDQS is a true differential pair on fingers
  219 / 221, **polarity inverted**. RESET_n is on finger 232 (LVCMOS12, static).
- The DQ bits are permuted on the write pins and on the read pins, and **the two data groups are not delay-matched on the card**
  (write +/-26.7 ps, read +/-56.8 ps): the per-bit delays that close them are in `design/gateware_deskew.json`.
- No DMI, no second byte (its data balls are open), no second rank, no power-good or enable signal to the FPGA.

Pin map, constraints and bring-up sequence: `design/gateware/` (`PINMAP.md`, `lpddr5_testbed_zcu104.xdc`, `BRINGUP.md`).

### Board components

- U1: the LPDDR5, with 24 x 4.7 uF decoupling under it.
- 40 divider resistors (0201) directly above the fingers: commands, writes, WCK.
- Read reference: DAC53401 (U5) behind a resistor network that gives 0.187 V by default; eight 39 ohm + 100 nF networks at the N fingers;
  150 ohm to ground on each RDQS leg.
- Power, from the socket's 1.2 V (19 fingers) and 3.3 V (finger 255), three linear regulators and no sequencer: VDD1 1.80 V (TLV70018),
  VDD2H = VDD2L 1.05 V (TPS74801), VDDQ 0.50 V (MIC47050). The enable chain gives the JEDEC order by wiring alone.
- Test pads for VDD1, VDD2H, VDDQ, VREF_RD and GND; none on signal nets.

131 items, 119 placed parts in 27 BOM lines, 5 ICs.

### Operating notes

- LPDDR5 ties the data rate to CK (WCK:CK 2:1 or 4:1). Reads and writes at 100 Mb/s run at CK = 25 MHz (2:1) or 12.5 MHz (4:1); CK = 800 MHz
  is for command-only traffic. The controller changes the clock frequency between the two.
- 200 Mb/s writes are not supported: the write node has a time constant of 2.6 ns.
- The 1.6 Gb/s command bus needs the native-mode I/O primitives of the FPGA (`TX_BITSLICE`) in bank 65.
- The read receivers are `IBUFDS_DPHY` (MIPI_DPHY_DCI) without differential termination; an SLVS fall-back is prepared.
- Mode-register settings the hardware relies on (single VDD2 rail, data mask off, all DRAM ODT off, pull-up strength), and what must never
  be used because DMI is strapped to ground: `design/DESIGN.md` section 7.

### Status

Every net is routed; KiCad DRC is clean (0 errors, 0 unconnected, schematic parity 0) and all script gates pass. The fabrication package is
in `fab/`. Not yet built or measured. Open before ordering: the SK hynix data sheet (ball map and body height are assumed from JEDEC / Micron),
the points listed in `design/DESIGN.md` sections 8.1 and 15.6.

### File layout

| Folder / file | Contents |
|---|---|
| `design/DESIGN.md` | the design description as built: every value with its reason, the rules it was built to, open risks |
| `design/netlist.json` | the design itself (parts, pads, fingers, stackup, rules, floorplan); written by `scripts/build_netlist.py` |
| `design/gateware/`, `design/gateware_deskew.json` | pin map, XDC, bring-up notes, per-bit delays, the Vivado 2026.1 pin check |
| `*.kicad_sch`, `lpddr5-testbed.kicad_pcb` | schematic and board (KiCad 10), **generated**: do not edit by hand |
| `scripts/` | the generators, the board stages and the gates |
| `fab/` | fabrication and assembly package (Gerber X2, drill, IPC-D-356, placement, BOM, drawings, STEP); `fab/README.md` is the index |
| `doc/` | schematic PDF and images of every layer |
| `lib/` | project symbol and footprint libraries |
| `references/` | data sheets, user guides, the JEDEC text, models and vendor PCB guidelines used for the design (`references/README.md`) |

### Build

```
scripts/pcb/build.sh          # spec -> schematic -> board -> all gates -> schematic PDF -> gateware files
scripts/pcb/build.sh --fab    # the same, plus fab/ and doc/img/
```

Needs KiCad 10 with its Python module and the stock footprint library, python3 with numpy, scipy, shapely, pycairo and python3-gi (librsvg).

Derived from the Antmicro LPDDR5 testbed.

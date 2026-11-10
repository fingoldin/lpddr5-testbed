# LPDDR5 testbed for the ZCU104

A single-channel LPDDR5X module (currently targeting SK hynix H58G56BK7BX068N or Samsung K3KL8L80CM-MGCT, both 2 x 16 Gb
with one rank) on a card that plugs into the ZCU104's DDR4
SO-DIMM socket and is driven by the FPGA through a dedicated pin map. It is a custom LPDDR5 card, not a DDR4 memory
module.

### Goals

Implement custon PHY and controller on ZCU104 FPGA, communicate with one rank on one channel of the LPDDR5 package at
the highest command rate possible (up to 800 MHz, DDR). Reads and writes can be slow, but must be at least 20 Mb/s 
(WCK = 10 MHz). 

### High-level signal routing

FPGA <-> LPDDR5 routes, for one channel and one rank with no DM wires:
- Bank 64 on FPGA: command bus and write data -- CK, CA0-6, CS0, RESET_n, WCK0/1, DQ0-15, plus PWR_EN and PGOOD
  (33 of 37 pins, socket fingers 108-162). LPDDR5 balls are connected via a resistive divider to level-shift the FPGA
  signals down to ~0.5 V from 1.2 V.
- Bank 66 on FPGA: read data and strobes -- 16 DQ with RDQS0/1 as true pairs (18 of 20 pairs, socket fingers 169-250).
  LPDDR5 balls are connected directly to FPGA P pins, while the corresponding N pins carry VREF_RD from a static DAC
  defining the read reference value around 170 mV.
- Bank 65 on FPGA: spare (0 of 44 pins, socket fingers 3-84), free to take DMI or a second channel later.

### Board components

Resistive dividers for 

### Operating notes

LPDDR5 allows CK down to 5 MHz (tCK max 200 ns), but the data rate is tied to CK (4x or 8x), so reads and writes run in
their own frequency set point: CK 25 MHz for 100 Mb/s reads, 80 MHz for 320 Mb/s writes. 800 MHz CK is for
command-only traffic.


### File layout

| Folder | Contents |
|---|---|
| `kicad/` | the design (schematic, PCB, rules), KiCad 10 format; 3D models in `kicad/3dmodels/` |
| `fab/` | single-board outputs and `fab/jlc/` (outputs for uploading to JLCPCB) |
| `fpga/` | PHY gateware generators (`gen_ips.py`, `gen_top.py`, `build.tcl`), testbench (`sim/`), build and xsim results (`results/rev1.1.0/`, `results/rev2.1.0/`) |
| `references/` | data sheets, user guides, the JEDEC text, models and vendor PCB guidelines used for the design (`references/README.md`) |


Derived from the Antmicro LPDDR5 testbed.

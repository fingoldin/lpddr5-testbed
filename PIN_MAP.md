# Pin map

Where each ZCU104 SO-DIMM finger that carries an LPDDR5 signal goes on the card: to a U1 ball directly, or to a
resistor. It follows the bank assignment in `README.md`: bank 64 for command and write, bank 66 for read. Channel A,
one rank (CS0), no DMI.

### Sources and conventions

- **Finger:** JEDEC DDR4 260-pin SO-DIMM numbering; odd fingers on the front, pin 1 at the left in front view.
- **ZCU104 net, FPGA ball, I/O:** `DDR4_SODIMM_*` nets from the ZCU104 schematic (`references/zcu104/XTP484`, pages 5, 6
  and 22). Every finger was cross-checked against the JEDEC names in the Micron module data sheet. The I/O column gives
  the pin pair (`L14P`, ...), the byte group `T0`-`T3`, and the clock-capable tags (GC, QBC, DBC).
- **U1 ball:** JESD209-5C 16.3.2 (315-ball 2CHx32), channel A. It matches the U1 footprint in this repo.
- **Resistor names** are placeholders until the schematic is drawn:
  - `RS_x`: series resistor of the divider for signal x. The path is finger -> `RS_x` -> U1 ball, with a shunt `RP_x`
    from the ball side to GND (not listed per row). The divider brings 1.2 V down to ~0.5 V.
  - `RS_RESET`: a lighter divider to ~1.0 V. RESET_n needs 0.84 V minimum and 1.25 V maximum (JESD209-5C Table 414),
    and a 1.2 V +5 % VCCO can exceed 1.25 V, so it can't be wired direct.
  - `RV_x`: one per bank-66 N pin, from the finger to the VREF_RD net (the static DAC output, ~170 mV).

### Bank 64: command and write (31 fingers)

| Finger | ZCU104 net | FPGA ball | I/O, byte group | LPDDR5 signal | Finger connects to | U1 ball |
|---|---|---|---|---|---|---|
| 108 | RESET_B | AB14 | L23N T3 | RESET_n | RS_RESET (divider to ~1.0 V) | H1 |
| 113 | BG1 | AB16 | L21P T3 | DQ8 | RS_DQ8 (divider to ~0.5 V) | D15 |
| 114 | ACT_B | AC17 | L20P T3 | WCK1_t | RS_WCK1_t (divider to ~0.5 V) | E12 |
| 115 | BG0 | AC16 | L20N T3 | WCK1_c | RS_WCK1_c (divider to ~0.5 V) | D11 |
| 119 | A12 | AL18 | L9N T1 | DQ0 | RS_DQ0 (divider to ~0.5 V) | D1 |
| 120 | A11 | AK15 | L10P T1 QBC | DQ1 | RS_DQ1 (divider to ~0.5 V) | C2 |
| 121 | A9 | AJ17 | L11P T1 GC | DQ2 | RS_DQ2 (divider to ~0.5 V) | E2 |
| 122 | A7 | AF17 | L15N T2 | CS0 | RS_CS0 (divider to ~0.5 V) | H7 |
| 125 | A8 | AK17 | L11N T1 GC | DQ3 | RS_DQ3 (divider to ~0.5 V) | D3 |
| 126 | A5 | AJ14 | L16N T2 QBC | CA5 | RS_CA5 (divider to ~0.5 V) | H13 |
| 127 | A6 | AH14 | L16P T2 QBC | CA6 | RS_CA6 (divider to ~0.5 V) | G12 |
| 128 | A4 | AF16 | L17P T2 | CA4 | RS_CA4 (divider to ~0.5 V) | G10 |
| 131 | A3 | AF15 | L17N T2 | CA3 | RS_CA3 (divider to ~0.5 V) | H11 |
| 132 | A2 | AG15 | L18P T2 | CA2 | RS_CA2 (divider to ~0.5 V) | G8 |
| 133 | A1 | AG14 | L18N T2 | CA1 | RS_CA1 (divider to ~0.5 V) | H5 |
| 137 | CK0_T | AF18 | L14P T2 GC | CK_t | RS_CK_t (divider to ~0.5 V) | H9 |
| 138 | CK1_T | AJ16 | L12P T1 GC | WCK0_t | RS_WCK0_t (divider to ~0.5 V) | E4 |
| 139 | CK0_C | AG18 | L14N T2 GC | CK_c | RS_CK_c (divider to ~0.5 V) | J9 |
| 140 | CK1_C | AJ15 | L12N T1 GC | WCK0_c | RS_WCK0_c (divider to ~0.5 V) | D5 |
| 143 | PARITY | AD16 | L24N T3 | DQ9 | RS_DQ9 (divider to ~0.5 V) | C14 |
| 144 | A0 | AH16 | N12 T2 | CA0 | RS_CA0 (divider to ~0.5 V) | G4 |
| 145 | BA1 | AL16 | L8P T1 | DQ4 | RS_DQ4 (divider to ~0.5 V) | B5 |
| 146 | A10 | AK14 | L10N T1 QBC | DQ5 | RS_DQ5 (divider to ~0.5 V) | C6 |
| 149 | CS0_B | AA15 | L22N T3 DBC | DQ10 | RS_DQ10 (divider to ~0.5 V) | E14 |
| 150 | BA0 | AL15 | L8N T1 | DQ6 | RS_DQ6 (divider to ~0.5 V) | E6 |
| 151 | WE_B | AA16 | L22P T3 DBC | DQ11 | RS_DQ11 (divider to ~0.5 V) | D13 |
| 152 | RAS_B | AD15 | L19P T3 DBC | DQ12 | RS_DQ12 (divider to ~0.5 V) | B11 |
| 155 | ODT0 | AE15 | L19N T3 DBC | DQ13 | RS_DQ13 (divider to ~0.5 V) | C10 |
| 156 | CAS_B | AA14 | L23P T3 | DQ14 | RS_DQ14 (divider to ~0.5 V) | E10 |
| 158 | A13 | AK18 | L9P T1 | DQ7 | RS_DQ7 (divider to ~0.5 V) | F5 |
| 162 | CS2_B | AN17 | L6P T0 | DQ15 | RS_DQ15 (divider to ~0.5 V) | F11 |

- **Byte groups:**
  - **T2:** CK, CA0-6 and CS0. CK uses the ZCU104's CK0 pair, and its address lines are routed as one group with it.
  - **T1:** write byte 0, with WCK0 on the CK1 pair, the only other pair the ZCU104 routes as differential.
  - **T3 plus one T0 pin:** write byte 1, with WCK1 on the L20 pair (adjacent fingers 114/115), and RESET_n.
- **Free to reorder at layout:** CA and CS order within T2, and DQ order within a write byte's fingers, since the PHY
  drives each pin on its own. Keep each signal in its byte group and keep the CK and WCK pairs as they are; update this
  file when anything moves.

### Bank 66: read (36 fingers)

| Finger | ZCU104 net | FPGA ball | I/O, byte group | LPDDR5 signal | Finger connects to | U1 ball |
|---|---|---|---|---|---|---|
| 169 | DQ37 | AE13 | L21P T3 | DQ14 | U1 ball (direct) | E10 |
| 170 | DQ36 | AF13 | L21N T3 | VREF_RD (DQ14 pair) | RV_DQ14 to VREF_RD |  |
| 173 | DQ33 | AB13 | L24P T3 | DQ12 | U1 ball (direct) | B11 |
| 174 | DQ32 | AC13 | L24N T3 | VREF_RD (DQ12 pair) | RV_DQ12 to VREF_RD |  |
| 182 | DQ39 | AD14 | L20P T3 | DQ15 | U1 ball (direct) | F11 |
| 183 | DQ38 | AE14 | L20N T3 | VREF_RD (DQ15 pair) | RV_DQ15 to VREF_RD |  |
| 186 | DQ35 | AE12 | L23P T3 | DQ13 | U1 ball (direct) | C10 |
| 187 | DQ34 | AF12 | L23N T3 | VREF_RD (DQ13 pair) | RV_DQ13 to VREF_RD |  |
| 190 | DQ45 | AG13 | L15P T2 | DQ10 | U1 ball (direct) | E14 |
| 191 | DQ44 | AH13 | L15N T2 | VREF_RD (DQ10 pair) | RV_DQ10 to VREF_RD |  |
| 194 | DQ41 | AF8 | L18P T2 | DQ8 | U1 ball (direct) | D15 |
| 195 | DQ40 | AG8 | L18N T2 | VREF_RD (DQ8 pair) | RV_DQ8 to VREF_RD |  |
| 198 | DQS5_C | AH9 | L16N T2 QBC | RDQS1_c | U1 ball (direct) | C12 |
| 200 | DQS5_T | AG9 | L16P T2 QBC | RDQS1_t | U1 ball (direct) | B13 |
| 203 | DQ46 | AJ11 | L14N T2 GC | VREF_RD (DQ11 pair) | RV_DQ11 to VREF_RD |  |
| 204 | DQ47 | AH11 | L14P T2 GC | DQ11 | U1 ball (direct) | D13 |
| 207 | DQ42 | AG10 | L17N T2 | VREF_RD (DQ9 pair) | RV_DQ9 to VREF_RD |  |
| 208 | DQ43 | AG11 | L17P T2 | DQ9 | U1 ball (direct) | C14 |
| 211 | DQ52 | AL12 | L9N T1 | VREF_RD (DQ2 pair) | RV_DQ2 to VREF_RD |  |
| 212 | DQ53 | AK12 | L9P T1 | DQ2 | U1 ball (direct) | E2 |
| 215 | DQ49 | AJ9 | L12P T1 GC | DQ0 | U1 ball (direct) | D1 |
| 216 | DQ48 | AK9 | L12N T1 GC | VREF_RD (DQ0 pair) | RV_DQ0 to VREF_RD |  |
| 219 | DQS6_C | AL8 | L10N T1 QBC | RDQS0_c | U1 ball (direct) | C4 |
| 221 | DQS6_T | AK8 | L10P T1 QBC | RDQS0_t | U1 ball (direct) | B3 |
| 224 | DQ54 | AL10 | L8N T1 | VREF_RD (DQ3 pair) | RV_DQ3 to VREF_RD |  |
| 225 | DQ55 | AL11 | L8P T1 | DQ3 | U1 ball (direct) | D3 |
| 228 | DQ50 | AK10 | L11N T1 GC | VREF_RD (DQ1 pair) | RV_DQ1 to VREF_RD |  |
| 229 | DQ51 | AJ10 | L11P T1 GC | DQ1 | U1 ball (direct) | C2 |
| 232 | DQ60 | AP11 | L3N T0 | VREF_RD (DQ6 pair) | RV_DQ6 to VREF_RD |  |
| 233 | DQ61 | AN11 | L3P T0 | DQ6 | U1 ball (direct) | E6 |
| 236 | DQ57 | AM9 | L6P T0 | DQ4 | U1 ball (direct) | B5 |
| 237 | DQ56 | AM8 | L6N T0 | VREF_RD (DQ4 pair) | RV_DQ4 to VREF_RD |  |
| 245 | DQ62 | AP9 | L2N T0 | VREF_RD (DQ7 pair) | RV_DQ7 to VREF_RD |  |
| 246 | DQ63 | AP10 | L2P T0 | DQ7 | U1 ball (direct) | F5 |
| 249 | DQ58 | AM10 | L5N T0 | VREF_RD (DQ5 pair) | RV_DQ5 to VREF_RD |  |
| 250 | DQ59 | AM11 | L5P T0 | DQ5 | U1 ball (direct) | C6 |

- **Receivers:** every read pair is a `MIPI_DPHY_DCI` receiver, which accepts a 70-330 mV common mode at VCCO 1.2 V
  (DS925 Table 17). The P pin carries DQ and the N pin carries VREF_RD.
- **Strobes:** RDQS0 sits on the T1 strobe pair (QBC), which can clock T0 and T1, i.e. DQ0-7. RDQS1 sits on the T2
  strobe pair (QBC), which can clock T2 and T3, i.e. DQ8-15. This keeps strobe-clocked capture possible, even though
  100 Mb/s reads will be oversampled.

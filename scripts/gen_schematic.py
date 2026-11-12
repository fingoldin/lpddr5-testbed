#!/usr/bin/env python3
"""Generate the complete KiCad 10 schematic from design/netlist.json.

    python3 scripts/gen_schematic.py            # writes the files listed below
    python3 scripts/check_schematic.py          # ERC + netlist parity against design/netlist.json

Outputs (repository root unless noted):
    lpddr5-testbed.kicad_sch                    root sheet (block diagram, six hierarchical sheets)
    connector.kicad_sch  lpddr5.kicad_sch  command.kicad_sch  write.kicad_sch  read.kicad_sch  power.kicad_sch
    lib/lpddr5_testbed.kicad_sym                every symbol of the design: J1, U1, U4, U5 and the rail power
                                                symbols are drawn here; R, C, TestPoint, TPS74801DRC,
                                                TLV70018_SOT23-5, GND and PWR_FLAG are copies of the KiCad library
                                                symbols of the same name
    sym-lib-table                               the project library only: the schematic needs no stock symbol
                                                library (their file layout depends on the KiCad installation)
    fp-lib-table                                only if it is missing
    design/erc_settings.json                    the "erc" section for the .kicad_pro: explicit rule severities and
                                                the two exclusions for the strapped DMI balls (check_schematic.py
                                                runs ERC with exactly these settings)

Connectivity is never typed in here: every pin gets the net that design/netlist.json gives it (a wire to its
neighbour where the drawing shows one, a global label, a power symbol or a no-connect flag), and
check_schematic.py compares KiCad's own netlist export with the JSON pin by pin.  Net names are global labels
and power symbols only, so they are identical to the JSON names (no sheet-path prefixes).  A net that lives on
one sheet therefore has a global label that appears once (KiCad's single_global_label check, ignored by default);
a local label would rename the net to /Sheet/NAME in the PCB.

The stock symbols are read from the installed KiCad libraries (KICAD10_SYMBOL_DIR, KICAD_SYMBOL_DIR or
/usr/share/kicad/symbols; one file per library or a .kicad_symdir directory) only when this script runs.
Numbers and names in the notes on the sheets come from the netlist (fingers, parts, groups, analysis), not from
this file.

The files are first written in the KiCad 8 syntax of the stock libraries and then re-saved by
`kicad-cli sch upgrade` / `kicad-cli sym upgrade` (KiCad 10) in a scratch copy of the project, so the
repository holds KiCad 10's own serialisation.  kicad-cli never runs inside the repository (it would rewrite
the .kicad_prl), and the .kicad_pro / .kicad_pcb are only read.

All UUIDs are uuid5(kisch.NS, key).  Symbol key: "sym/<ref>/<unit>"; sheet key: "sheet/<name>"; the PCB can
therefore compute a footprint's schematic link as /<sheet uuid>/<uuid of unit 1> without reading these files.
"""
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kisch import (Q, Sheet, SymDef, dump, find, find_all, fx, on_grid, parse, snap, stock_symbol,  # noqa: E402
                   uid)

ROOT = Path(__file__).resolve().parents[1]
NET = json.loads((ROOT / "design" / "netlist.json").read_text())
PARTS = {p["ref"]: p for p in NET["parts"]}
FINGERS = {f["finger"]: f for f in NET["fingers"]}
BALLS = json.loads((ROOT / "design" / "ref" / "u1_ball_reference.json").read_text())["balls"]
ANALYSIS = NET["analysis"]

PROJECT = "lpddr5-testbed"
ROOT_UUID = "239015f0-3f9a-4270-9961-5849571f87d5"      # unchanged: the .kicad_pro refers to it
TITLE = "LPDDR5 testbed for ZCU104"
LIB = "lpddr5_testbed"
RAILS = ["V1P2", "V3P3", "VDD1", "VDD2H", "VDDQ"]
POWER_NETS = ["GND"] + RAILS
SHEETS = [                                              # netlist sheet key, sheet name, file, page
    ("connector", "Connector", "connector.kicad_sch", 2),
    ("lpddr5", "LPDDR5", "lpddr5.kicad_sch", 3),
    ("command", "Command", "command.kicad_sch", 4),
    ("write", "Write", "write.kicad_sch", 5),
    ("read", "Read", "read.kicad_sch", 6),
    ("power", "Power", "power.kicad_sch", 7),
]
STOCK_COPIES = {                                        # key -> KiCad library, symbol (copied into the project
    "R": ("Device", "R"), "C": ("Device", "C"),         # library under the same name)
    "TP": ("Connector", "TestPoint"), "FID": ("Mechanical", "Fiducial"),
    "U2": ("Regulator_Linear", "TPS74801DRC"), "U3": ("Regulator_Linear", "TLV70018_SOT23-5"),
    "GND": ("power", "GND"), "FLAG": ("power", "PWR_FLAG"),
}
CMD_CLASS = "CMD_SE"                                    # netclass of the single-ended command lines
P = 2.54                                                # pin pitch
CH = 1.0                                                # estimated width of one 1.27 mm character


def sheet_uuid(key):
    return uid("sheet/" + key)


# =============================================================================================
# project symbol library
# =============================================================================================
def _font():
    return ["effects", ["font", ["size", 1.27, 1.27]]]


def _prop(name, value, x, y, hide=False, justify=None):
    e = ["effects", ["font", ["size", 1.27, 1.27]]]
    if justify:
        e.append(["justify", justify])
    if hide:
        e.append(["hide", "yes"])
    return ["property", Q(name), Q(value), ["at", x, y, 0], e]


def _pin(etype, x, y, angle, length, name, number):
    return ["pin", etype, "line", ["at", x, y, angle], ["length", length],
            ["name", Q(name), _font()], ["number", Q(number), _font()]]


def ic_symbol(name, prefix, units, description, keywords, fp_filter, footprint="", datasheet="~",
              pinlen=5.08, min_width=0.0):
    """Rectangular symbol.  units: list of dict(title, left, right, top, bottom); every pin is
    (number, name, electrical type), None in left/right leaves a row free.

    Unit geometry (returned in SymDef.geom[unit]): body W x H, origin at (cx, cy) from the body's top-left
    corner, both multiples of 2.54, so a unit placed with its top-left corner on the grid has every pin
    on the grid.  Left/right pins start on the second row (third if the unit has a title), 2.54 apart."""
    node = ["symbol", Q(name), ["pin_names", ["offset", 1.016]], ["exclude_from_sim", "no"],
            ["in_bom", "yes"], ["on_board", "yes"],
            _prop("Reference", prefix, 0, 0), _prop("Value", name, 0, 0),
            _prop("Footprint", footprint, 0, 0, hide=True), _prop("Datasheet", datasheet, 0, 0, hide=True),
            _prop("Description", description, 0, 0, hide=True),
            _prop("ki_keywords", keywords, 0, 0, hide=True), _prop("ki_fp_filters", fp_filter, 0, 0, hide=True)]
    geom = {}
    for u, unit in enumerate(units, 1):
        left, right = unit.get("left", []), unit.get("right", [])
        top, bottom = unit.get("top", []), unit.get("bottom", [])
        title = unit.get("title")
        rows = max(len(left), len(right), 1)
        r0 = 1 + (1 if title else 0) + (2 if top else 0)
        nrows = r0 + rows + (2 if bottom else 0)
        H = nrows * P

        def longest(pins):
            return max([len(p[1]) for p in pins if p] + [0])

        need = (longest(left) + longest(right)) * CH * 1.15 + 7.0
        if title:
            need = max(need, len(title) * CH * 1.1 + 4.0)
        need = max(need, min_width, (max(len(top), len(bottom)) + 1) * 5.08)
        W = math.ceil(need / 5.08) * 5.08
        cx, cy = W / 2, round(H / 2 / P) * P
        x0, x1, ytop = -cx, W - cx, cy
        body = ["symbol", Q("%s_%d_1" % (name, u)),
                ["rectangle", ["start", x0, ytop], ["end", x1, ytop - H],
                 ["stroke", ["width", 0.254], ["type", "default"]], ["fill", ["type", "background"]]]]
        if title:
            body.append(["text", Q(title), ["at", (x0 + x1) / 2, ytop - P, 0],
                         ["effects", ["font", ["size", 1.27, 1.27], ["bold", "yes"]]]])
        for i, p in enumerate(left):
            if p:
                body.append(_pin(p[2], x0 - pinlen, ytop - (r0 + i) * P, 0, pinlen, p[1], p[0]))
        for i, p in enumerate(right):
            if p:
                body.append(_pin(p[2], x1 + pinlen, ytop - (r0 + i) * P, 180, pinlen, p[1], p[0]))

        def xs(n):
            return [round(((k - (n - 1) / 2) * 5.08 + (x0 + x1) / 2) / P) * P for k in range(n)]

        for xk, p in zip(xs(len(top)), top):
            body.append(_pin(p[2], xk, ytop + pinlen, 270, pinlen, p[1], p[0]))
        for xk, p in zip(xs(len(bottom)), bottom):
            body.append(_pin(p[2], xk, ytop - H - pinlen, 90, pinlen, p[1], p[0]))
        node.append(body)
        geom[u] = dict(W=W, H=H, cx=cx, cy=cy)
    # library field positions (unit 1): reference top-left, value below the body
    g = geom[1]
    find_all(node, "property")[0][3] = ["at", -g["cx"], g["cy"] + 1.27, 0]
    find_all(node, "property")[1][3] = ["at", -g["cx"], g["cy"] - g["H"] - 1.27, 0]
    for k in (0, 1):
        find_all(node, "property")[k][4].append(["justify", "left"])
    sym = SymDef(LIB, name, node)
    sym.geom = geom
    return sym


def rail_symbol(net):
    """Power symbol (arrow) whose value, and therefore net name, is `net`."""
    node = ["symbol", Q(net), ["power"], ["pin_numbers", "hide"], ["pin_names", ["offset", 0], "hide"],
            ["exclude_from_sim", "no"], ["in_bom", "yes"], ["on_board", "yes"],
            _prop("Reference", "#PWR", 0, -3.81, hide=True), _prop("Value", net, 0, 3.556),
            _prop("Footprint", "", 0, 0, hide=True), _prop("Datasheet", "", 0, 0, hide=True),
            _prop("Description", 'Power symbol creates a global label with name "%s"' % net, 0, 0, hide=True),
            _prop("ki_keywords", "global power", 0, 0, hide=True),
            ["symbol", Q(net + "_0_1")] + [
                ["polyline", ["pts"] + [["xy", a, b] for a, b in pts],
                 ["stroke", ["width", 0], ["type", "default"]], ["fill", ["type", "none"]]]
                for pts in (((-0.762, 1.27), (0, 2.54)), ((0, 0), (0, 2.54)), ((0, 2.54), (0.762, 1.27)))],
            ["symbol", Q(net + "_1_1"), _pin("power_in", 0, 0, 90, 0, "~", "1")]]
    return SymDef(LIB, net, node)


def ball_key(b):
    m = re.match(r"([A-Z]+)(\d+)$", b)
    rows = "A B C D E F G H J K L M N P R T U V W Y AA".split()
    return (rows.index(m.group(1)), int(m.group(2)))


def ball_name(b):
    """JEDEC pin function without the ball suffix: 'CA0_A_G4' -> 'CA0_A'."""
    f = BALLS[b]["pin_function"]
    assert f.endswith("_" + b), (b, f)
    return f[:-len(b) - 1]


def bank_group(fingers):
    """('65', 'T0'): bank(s) and byte group(s), without the nibble letter, of a list of finger numbers."""
    fs = [FINGERS[n] for n in fingers]
    assert all(f["bank"] and f["bytegroup"] for f in fs), fingers
    return (" / ".join(str(b) for b in sorted({f["bank"] for f in fs})),
            " / ".join(sorted({f["bytegroup"][:2] for f in fs})))


def build_j1():
    """260-finger card edge, one unit per function group.  Pin name = DDR4 SO-DIMM name (FPGA ball)."""
    pins = PARTS["J1"]["pins"]

    def entry(n):
        f = FINGERS[n]
        name = f["ddr4_name"] + ("(%s)" % f["fpga_ball"] if f["fpga_ball"] else "")   # no spaces in pin names
        return (str(n), name, "passive")

    cls = {n: f["function"].split()[0].rstrip(":") for n, f in FINGERS.items()}
    of = lambda *kinds: [n for n in sorted(FINGERS) if cls[n] in kinds]     # noqa: E731
    cmd, wr, rst, rd = of("COMMAND"), of("WRITE"), of("RESET_n"), of("READ")
    vdd, spd, i2c, gnd, nc = of("VDD"), of("VDDSPD"), of("I2C"), of("VSS"), of("no")
    assert len(cmd) == 10 and len(wr) == 10 and len(rst) == 1 and len(rd) == 18 and len(i2c) == 2
    assert all(pins[str(n)] == "GND" for n in gnd) and all(pins[str(n)] == "" for n in nc)
    assert all(pins[str(n)] == "V1P2" for n in vdd) and pins[str(spd[0])] == "V3P3"
    assert len(cmd + wr + rst + rd + vdd + spd + i2c + gnd + nc) == 260
    half = (len(nc) + 1) // 2
    ghalf = len(gnd) // 2
    units = [
        dict(title="COMMAND   bank %s, %s" % bank_group(cmd), right=[entry(n) for n in cmd]),
        dict(title="WRITE, RESET_n   bank %s" % bank_group(wr + rst)[0],
             right=[entry(n) for n in rst] + [None] + [entry(n) for n in wr]),
        dict(title="READ   bank %s, %s" % bank_group(rd), right=[entry(n) for n in rd]),
        dict(title="POWER, I2C", right=[entry(n) for n in vdd] + [None] + [entry(n) for n in spd] + [None] +
             [entry(n) for n in i2c]),
        dict(title="GND", left=[entry(n) for n in gnd[:ghalf]], right=[entry(n) for n in gnd[ghalf:]]),
        dict(title="NOT CONNECTED ON THE CARD", left=[entry(n) for n in nc[:half]],
             right=[entry(n) for n in nc[half:]]),
    ]
    sym = ic_symbol("DDR4_SODIMM_260_Edge", "J", units,
                    "260-finger DDR4 SO-DIMM card edge (JEDEC MO-310), odd fingers front, even fingers back; "
                    "pin names: DDR4 SO-DIMM signal (ZCU104 FPGA ball)",
                    "DDR4 SODIMM edge connector fingers", "DDR4*SODIMM*", min_width=30.48)
    sym.groups = dict(cmd=cmd, wr=wr, rst=rst, rd=rd, vdd=vdd, spd=spd, i2c=i2c, gnd=gnd, nc=nc,
                      gnd_left=gnd[:ghalf], gnd_right=gnd[ghalf:], nc_left=nc[:half], nc_right=nc[half:])
    return sym


def build_u1():
    """315-ball LPDDR5: unit A used channel-A signals, B unused channel-A signals, C channel B + NC/RFU,
    D supplies, E VSS."""
    pins = PARTS["U1"]["pins"]
    names = {b: ball_name(b) for b in BALLS}
    assert set(names) == set(pins)

    def etype(n):
        if n in ("VSS", "VDD1", "VDD2H", "VDD2L", "VDDQ"):
            return "power_in"
        if n == "NC":
            return "no_connect"
        if n == "RFU" or n.startswith("ZQ"):
            return "passive"
        if n.startswith("RDQS"):
            return "output"
        if n.startswith(("DQ", "DMI")):
            return "bidirectional"
        return "input"

    def e(b):
        return (b, names[b], etype(names[b]))

    by_name = {}
    for b in sorted(names, key=ball_key):
        by_name.setdefault(names[b], []).append(b)

    def one(n):
        assert len(by_name[n]) == 1, n
        return e(by_name[n][0])

    def allof(n):
        return [e(b) for b in by_name[n]]

    ua_left = [one("CK_t_A"), one("CK_c_A"), None, one("CS0_A"), None] + \
              [one("CA%d_A" % i) for i in range(7)] + [None, one("RESET_n"), one("ZQ_A")]
    ua_right = [one("DQ%d_A" % i) for i in range(8)] + [None, one("WCK0_t_A"), one("WCK0_c_A"), None,
                                                         one("RDQS0_t_A"), one("RDQS0_c_A")]
    ub_right = [one("DQ%d_A" % i) for i in range(8, 16)] + [None, one("WCK1_t_A"), one("WCK1_c_A"), None,
                                                             one("CS1_A"), one("DMI0_A"), one("DMI1_A"), None,
                                                             one("RDQS1_t_A"), one("RDQS1_c_A")]
    chb = [b for b in sorted(names, key=ball_key) if names[b].endswith("_B")]
    chb_vddq = [e(b) for b in chb if pins[b] == "VDDQ"]
    chb_gnd = [e(b) for b in chb if pins[b] == "GND"]
    def natural(b):
        m = re.search(r"\d+", names[b])
        return (re.sub(r"\d+", "", names[b]), int(m.group(0)) if m else 0)

    chb_open = [e(b) for b in sorted(chb, key=natural) if pins[b] == ""]
    assert len(chb_vddq) + len(chb_gnd) + len(chb_open) == len(chb) == 37
    uc_left = chb_vddq + [None] + chb_gnd + [None] + allof("RFU") + [None] + allof("NC")
    used = {p[0] for p in ua_left + ua_right + ub_right + uc_left + chb_open if p}
    supply = {b for b in names if etype(names[b]) == "power_in"}
    assert used | supply == set(names) and not used & supply
    ud_left = allof("VDD2H")
    ud_right = allof("VDD2L") + [None, None] + allof("VDDQ") + [None, None] + allof("VDD1")
    vss = allof("VSS")
    units = [
        dict(title="CHANNEL A: CA, BYTE 0", left=ua_left, right=ua_right),
        dict(title="CHANNEL A: UNUSED", right=ub_right),
        dict(title="CHANNEL B, NC, RFU", left=uc_left, right=chb_open),
        dict(title="SUPPLIES", left=ud_left, right=ud_right),
        dict(title="VSS", left=vss[:len(vss) // 2], right=vss[len(vss) // 2:]),
    ]
    sym = ic_symbol("LPDDR5_315B_2CH", "U", units,
                    "LPDDR5 / LPDDR5X SDRAM, 315-ball FBGA 12.4 x 15.0 mm, JEDEC 2-channel x32 ball map "
                    "(JESD209-5 16.3.2)", "LPDDR5 LPDDR5X SDRAM 315 ball", "BGA*315*", pinlen=6.35, min_width=30.48)
    sym.groups = dict(chb_vddq=[p[0] for p in chb_vddq], chb_gnd=[p[0] for p in chb_gnd],
                      chb_open=[p[0] for p in chb_open], rfu=by_name["RFU"], nc=by_name["NC"],
                      vdd2h=by_name["VDD2H"], vdd2l=by_name["VDD2L"], vddq=by_name["VDDQ"],
                      vdd1=by_name["VDD1"], vss_left=[p[0] for p in vss[:len(vss) // 2]],
                      vss_right=[p[0] for p in vss[len(vss) // 2:]], by_name=by_name)
    return sym


def build_library():
    syms = {
        "J1": build_j1(),
        "U1": build_u1(),
        "U4": ic_symbol("MIC47050YML", "U", [dict(
            left=[("3", "IN", "power_in"), ("6", "EN", "input")],
            right=[("4", "OUT", "power_out"), ("5", "ADJ", "input")],
            top=[("1", "BIAS", "power_in")],
            bottom=[("2", "GND", "power_in"), ("7", "EP", "passive")])],
            "500 mA NMOS LDO with bias rail, adjustable output from 0.4 V, MLF-6 2 x 2 mm",
            "LDO regulator low voltage NMOS bias adjustable", "MLF*6*2x2mm*P0.65mm*",
            footprint=PARTS["U4"]["footprint"], datasheet="references/parts/Microchip_MIC47050.pdf",
            pinlen=P, min_width=25.4),
        "U5": ic_symbol("DAC53401DSG", "U", [dict(
            left=[("2", "SCL", "input"), ("3", "SDA", "bidirectional"), ("1", "A0", "input")],
            right=[("8", "OUT", "output"), ("7", "FB", "input"), None, ("4", "CAP", "passive")],
            top=[("6", "VDD", "power_in")],
            bottom=[("5", "AGND", "power_in"), ("9", "EP", "passive")])],
            "10-bit voltage-output DAC with NVM and I2C, WSON-8 2 x 2 mm",
            "DAC 10 bit I2C NVM voltage output", "WSON*8*2x2mm*P0.5mm*",
            footprint=PARTS["U5"]["footprint"], datasheet="references/parts/TI_DAC53401.pdf",
            pinlen=P, min_width=25.4),
    }
    for net in RAILS:
        syms[net] = rail_symbol(net)
    return syms


def project_copy(lib, name, footprint=None):
    """Copy of the KiCad library symbol lib:name for the project library (same name, derived symbols
    flattened); `footprint` replaces the library default of a symbol that is used for one part."""
    node = stock_symbol(lib, name).node
    if footprint is not None:
        next(c for c in find_all(node, "property") if c[1] == "Footprint")[2] = Q(footprint)
        # the footprint filter of the copy must admit the footprint the part uses (U3: the DDC package is the thin TSOT-23-5, whose lands are
        # those of SOT-23-5; the library filter "SOT?23*" does not match the name)
        import fnmatch as _fn
        flt = next((c for c in find_all(node, "property") if c[1] == "ki_fp_filters"), None)
        fpname = footprint.split(":")[1]
        if flt is not None and not any(_fn.fnmatch(fpname.lower(), pat.lower()) for pat in flt[2].strip('"').split()):
            flt[2] = Q(flt[2].strip('"') + " " + fpname.split("_")[0] + "*")
    return SymDef(LIB, name, node)


SYM = build_library()
for _key, (_lib, _name) in STOCK_COPIES.items():
    assert _key not in SYM
    SYM[_key] = project_copy(_lib, _name, PARTS[_key]["footprint"] if _key in PARTS else None)
assert len({s.name for s in SYM.values()}) == len(SYM), "two project symbols with one name"
BY_LIB_ID = {s.lib_id: s for s in SYM.values()}
for _p in NET["parts"]:                                # every part: its symbol is in the project library
    assert _p["symbol"] in BY_LIB_ID, "%s: symbol %s is not in the project library" % (_p["ref"], _p["symbol"])
    assert set(BY_LIB_ID[_p["symbol"]].pins) == set(_p["pins"]), "pin set of %s differs from its symbol" % _p["ref"]
for _ref in ("J1", "U1", "U2", "U3", "U4", "U5"):
    assert PARTS[_ref]["symbol"] == SYM[_ref].lib_id, (_ref, PARTS[_ref]["symbol"], SYM[_ref].lib_id)


def library_text():
    nodes = sorted((s.node for s in SYM.values()), key=lambda n: str(n[1]))
    return dump(["kicad_symbol_lib", ["version", 20231120], ["generator", Q("kicad_symbol_editor")],
                 ["generator_version", Q("8.0")]] + nodes) + "\n"


# =============================================================================================
# drawing helpers
# =============================================================================================
def new_sheet(key):
    k, name, fn, page = next(s for s in SHEETS if s[0] == key)
    su = sheet_uuid(k)
    sh = Sheet(PROJECT, k, fn, su, "/%s/%s" % (ROOT_UUID, su), page, TITLE,
               date=NET["meta"]["date"], rev=NET["meta"]["revision"],
               comments=("Sheet: %s" % name, "Generated from design/netlist.json by scripts/gen_schematic.py"))
    return sh


def netof(ref, pin):
    return PARTS[ref]["pins"][str(pin)]


def fld(rot, x, y, just="left"):
    """Field spec for horizontal text at an absolute sheet position.  KiCad stores the field angle
    relative to the symbol and mirrors the horizontal justification for rotations 90 and 180."""
    angle = 0 if rot in (0, 180) else 90
    if just in ("left", "right") and rot in (90, 180):
        just = "right" if just == "left" else "left"
    return (x, y, angle, None if just == "center" else just)


def part_kw(ref):
    p = PARTS[ref]
    return dict(value=p["value"], footprint=p["footprint"], in_bom=p["in_bom"], dnp=p["dnp"],
                description=p["description"], extra={"MPN": p["mpn"], "Manufacturer": p["manufacturer"]})


def pt(inst, pin):
    """Position of a pin; marks it as wired by hand."""
    inst.sheet.connected.add((inst.ref, str(pin)))
    return inst.pin(pin)


def add(p, d, n=1.0):
    return (round(p[0] + d[0] * n, 4), round(p[1] + d[1] * n, 4))


def place_unit(sh, key, ref, unit, X, Y):
    """Place a unit of a project IC symbol with its body's top-left corner at (X, Y)."""
    sym = SYM[key]
    g = sym.geom[unit]
    fields = {"Reference": (X, Y - 1.4, 0, "left"), "Value": (X + g["W"], Y - 1.4, 0, "right")}
    inst = sh.place(sym, ref, X + g["cx"], Y + g["cy"], 0, unit, fields=fields, **part_kw(ref))
    inst.box = (X, Y, X + g["W"], Y + g["H"])
    return inst


def place_r(sh, ref, x, y, orient="h", labels=True):
    """Resistor.  h: horizontal, pin 1 left; h2: pin 1 right; v: vertical, pin 1 up; v2: pin 1 down."""
    rot = {"h": 90, "h2": 270, "v": 0, "v2": 180}[orient]
    if orient in ("h", "h2"):
        fields = {"Reference": fld(rot, x, y - 2.4, "center"), "Value": fld(rot, x, y + 2.4, "center")}
    else:
        fields = {"Reference": fld(rot, x + 1.9, y - 1.2), "Value": fld(rot, x + 1.9, y + 1.2)}
    return sh.place(SYM["R"], ref, x, y, rot, fields=fields, **part_kw(ref))


def place_c(sh, ref, x, y):
    """Capacitor, vertical, pin 1 up."""
    fields = {"Reference": fld(0, x + 2.7, y - 1.4), "Value": fld(0, x + 2.7, y + 1.4)}
    return sh.place(SYM["C"], ref, x, y, 0, fields=fields, **part_kw(ref))


def place_tp(sh, ref, x, y):
    """Test pad, pin at (x, y), body above."""
    fields = {"Reference": fld(0, x, y - 7.2, "center"), "Value": fld(0, x, y - 5.3, "center")}
    return sh.place(SYM["TP"], ref, x, y, 0, fields=fields, **part_kw(ref))


def power(sh, net, x, y, d=None):
    """Power symbol for `net` at (x, y), pointing in direction d (default: GND down, rails up)."""
    if net == "GND":
        d = tuple(d or (0, 1))
        rot = {(0, 1): 0, (1, 0): 90, (0, -1): 180, (-1, 0): 270}[d]
        sym, off = SYM["GND"], 3.9
    else:
        d = tuple(d or (0, -1))
        rot = {(0, -1): 0, (-1, 0): 90, (0, 1): 180, (1, 0): 270}[d]
        sym, off = SYM[net], 3.7
    if d[0]:
        val = fld(rot, x + d[0] * 3.2, y, "left" if d[0] > 0 else "right")
    else:
        val = fld(rot, x, y + d[1] * off, "center")
    sh.place(sym, sh.next_pwr(), x, y, rot, fields={"Value": val}, value=net, in_bom=True,
             description=sym.props.get("Description", ""))


def flag(sh, x, y):
    return sh.place(SYM["FLAG"], sh.next_pwr("#FLG"), x, y, 0,
                    fields={"Value": fld(0, x, y - 3.9, "center")}, value="PWR_FLAG")


def tie(sh, inst, pin, length=P, net=None, upright=True):
    """Connect one pin by itself: no-connect flag, power symbol, or a stub with a global label.
    upright=False keeps the power symbol of a horizontal pin in line with the pin (rotated), for a pin whose
    neighbours leave no room for the jog."""
    net = netof(inst.ref, pin) if net is None else net
    assert net == netof(inst.ref, pin)
    p, o = pt(inst, pin), inst.out(pin)
    if net == "":
        sh.nc(*p)
        return p
    q = add(p, o, length)
    sh.wire(p, q)
    if net in POWER_NETS and o[0] and upright:         # horizontal pin: short jog, the symbol stands upright
        r = (q[0], q[1] + (P if net == "GND" else -P))
        sh.wire(q, r)
        power(sh, net, *r)
    elif net in POWER_NETS:
        power(sh, net, q[0], q[1], o if o[0] else None)
    else:
        sh.glabel(net, q[0], q[1], o)
    return q


def bar(sh, points):
    """Wire through collinear points (pin ends), a junction on every inner one."""
    pts = sorted(set(points))
    assert len({p[0] for p in pts}) == 1 or len({p[1] for p in pts}) == 1, "bar points not collinear"
    for a, b in zip(pts, pts[1:]):
        sh.wire(a, b)
    for p in pts[1:-1]:
        sh.junction(*p)
    return pts


def tied_group(sh, inst, pins, net, stub=5.08):
    """Pins of one unit side strapped to a rail: stubs to a common bar, power symbol at the bar's end
    (GND below the last row, a rail above the first row)."""
    assert all(netof(inst.ref, b) == net for b in pins), net
    o = inst.out(pins[0])
    ends = sorted(add(pt(inst, b), o, stub) for b in pins)
    for b in pins:
        sh.wire(inst.pin(b), add(inst.pin(b), o, stub))
    if net == "GND":
        tip = (ends[-1][0], ends[-1][1] + P)
        chain = ends + [tip]
        joints = ends[1:] if len(ends) > 1 else []
    else:
        tip = (ends[0][0], ends[0][1] - P)
        chain = [tip] + ends
        joints = ends[:-1] if len(ends) > 1 else []
    for a, b in zip(chain, chain[1:]):
        sh.wire(a, b)
    for p in joints:
        sh.junction(*p)
    power(sh, net, *tip)
    return tip


def heading(sh, text, sub=None):
    sh.text(text, 15.24, 15.5, size=3.0, bold=True, justify="left top")
    if sub:
        sh.text(sub, 15.24, 21.5, size=1.5, justify="left top")


def wrap(text, width):
    """Wrap every line of `text` to `width` characters; continuation lines keep the indent plus four."""
    out = []
    for line in text.split("\n"):
        ind = len(line) - len(line.lstrip(" "))
        out += textwrap.wrap(line, width, subsequent_indent=" " * (ind + 4), initial_indent="",
                             break_long_words=False, drop_whitespace=True,
                             replace_whitespace=False) or [""]
    return "\n".join(out)


def note(sh, text, x, y, size=1.27, bold=False, width=None):
    if not text.strip():
        return
    sh.text(wrap(text, width) if width else text, x, y, size=size, bold=bold, justify="left top")


def finger_of(net):
    fs = [f for f in NET["fingers"] if f["net"] == net]
    assert len(fs) == 1, net
    return fs[0]


def finger_info(net):
    """'finger 84 (AN22)' for the finger that carries `net`."""
    f = finger_of(net)
    return "finger %d%s" % (f["finger"], " (%s)" % f["fpga_ball"] if f["fpga_ball"] else "")


def fingers_with(kind):
    """Finger numbers whose function text starts with the word `kind` (VSS, VDD, VDDSPD, I2C, no, ...)."""
    return [f["finger"] for f in NET["fingers"] if f["function"].split()[0].rstrip(":") == kind]


def group(name):
    return next(g for g in NET["groups"] if g["name"] == name)


def bank_of(nets):
    """('65', 'T0') for the fingers that carry `nets`."""
    return bank_group([finger_of(n)["finger"] for n in nets])


def pair_text(t_net, c_net, inverted=False):
    """'CK_P on finger 76 (P pin), CK_N on finger 74 (N pin)' from the finger table (finger-side nets).
    inverted: the true leg is on the N pin of the host pair and the complement on the P pin (the gateware drives the
    pair inverted); the text says so."""
    out = []
    for n in (t_net, c_net):
        f = finger_of(n)
        assert netof("J1", f["finger"]) == n
        out.append("%s on finger %d (%s pin)" % (netof(series_of(n), 2), f["finger"], f["pn"]))
    want = ("N", "P") if inverted else ("P", "N")
    assert (finger_of(t_net)["pn"], finger_of(c_net)["pn"]) == want, (t_net, c_net)
    assert finger_of(t_net)["partner_finger"] == finger_of(c_net)["finger"], (t_net, c_net)
    return ", ".join(out) + (":\n    the host pair is driven INVERTED by the gateware" if inverted else "")


def open_between(net_a, net_b):
    """Fingers numbered between those of two nets that are left open on the card."""
    a, b = sorted((finger_of(net_a)["finger"], finger_of(net_b)["finger"]))
    return [n for n in range(a + 1, b) if FINGERS[n]["net"] == ""]


def series_of(finger_net):
    """The series resistor whose pin 1 is on a finger-side net."""
    rs = [r for r, p in PARTS.items() if r[0] == "R" and p["pins"].get("1") == finger_net]
    assert len(rs) == 1, finger_net
    return rs[0]


def ohms(ref):
    """Resistance of a part from its value: '22R' -> 22, '1k37' -> 1370, '4x10k' -> 10000."""
    v = PARTS[ref]["value"].split("x")[-1]
    m = re.fullmatch(r"(\d+)([RkM])(\d*)", v)
    assert m, (ref, v)
    return float("%s.%s" % (m.group(1), m.group(3) or "0")) * {"R": 1, "k": 1e3, "M": 1e6}[m.group(2)]


def rtxt(ref):
    """'22 R', '1.37 k', '10 k' for the notes."""
    v = ohms(ref)
    return "%g k" % (v / 1e3) if v >= 1e3 else "%g R" % v


def same_value(refs):
    refs = list(refs)
    assert len({PARTS[r]["value"] for r in refs}) == 1, refs
    return refs[0]


def ctxt(ref):
    """'100 nF', '1 uF' from a capacitor value like 100n, 4u7."""
    m = re.fullmatch(r"(\d+)([pnu])(\d*)", PARTS[ref]["value"])
    assert m, ref
    return "%g %sF" % (float("%s.%s" % (m.group(1), m.group(3) or "0")), m.group(2))


def stacked_pin_note(key, ref):
    """What the symbol of `ref` does not draw: hidden pins stacked on a visible pin, hidden open pins."""
    sym, part = SYM[key], PARTS[ref]
    at = {}
    for num, pin in sym.pins.items():
        at.setdefault((pin["x"], pin["y"]), []).append(num)
    pad = max(sym.pins, key=int) if "-1EP" in part["footprint"] else None
    stacked, hidden = [], []
    for num in sorted(sym.pins, key=int):
        pin = sym.pins[num]
        if not pin["hidden"]:
            continue
        vis = [n for n in at[(pin["x"], pin["y"])] if not sym.pins[n]["hidden"]]
        if vis:
            assert len(vis) == 1 and netof(ref, num) == netof(ref, vis[0]), (ref, num)
            stacked.append("%s%s on %s (%s)" % (num, " = exposed pad" if num == pad else "", vis[0],
                                                sym.pins[vis[0]]["name"]))
        else:
            assert netof(ref, num) == "" and pin["etype"] == "no_connect", (ref, num)
            hidden.append("pin %s (%s) is not drawn and stays open" % (num, pin["name"]))
    out = []
    if stacked:
        out.append("%s pins stacked in the symbol: %s." % (ref, ", ".join(stacked)))
    out += ["%s %s." % (ref, h) for h in hidden]
    assert out, ref
    return "\n".join(out)


def ball_of(net):
    balls = [b for b, n in PARTS["U1"]["pins"].items() if n == net]
    assert len(balls) == 1, net
    return "U1.%s" % balls[0]


def cap_row(sh, refs, x, y, pitch=11.43):
    """Decoupling row: capacitors between one rail (top) and GND (bottom), power symbol at the left end."""
    rail = netof(refs[0], 1)
    assert all(netof(r, 1) == rail and netof(r, 2) == "GND" for r in refs)
    tops, bots = [], []
    for i, r in enumerate(refs):
        c = place_c(sh, r, x + i * pitch, y + 3.81)
        tops.append(pt(c, 1))
        bots.append(pt(c, 2))
    left = (x - 7.62, y)
    bar(sh, [left] + tops)
    power(sh, rail, *left)
    bar(sh, bots)
    g = (bots[0][0] - 7.62, bots[0][1])
    sh.wire(g, bots[0])
    power(sh, "GND", *g)
    if len(refs) > 1:
        sh.junction(*bots[0])
    return tops


# =============================================================================================
# sheets
# =============================================================================================
def divider_cell(sh, ser, shunt, x, y, info):
    """finger-side label - series R - node - ball-side label, shunt R from the node to GND."""
    n_in, n_out = netof(ser, 1), netof(ser, 2)
    assert netof(shunt, 1) == n_out and netof(shunt, 2) == "GND", (ser, shunt)
    rs = place_r(sh, ser, x + 10.16, y, "h")
    rp = place_r(sh, shunt, x + 20.32, y + 7.62, "v")
    sh.wire((x, y), pt(rs, 1))
    sh.glabel(n_in, x, y, (-1, 0))
    node = (x + 20.32, y)
    sh.wire(pt(rs, 2), node, (x + 27.94, y))
    sh.junction(*node)
    sh.glabel(n_out, x + 27.94, y, (1, 0))
    sh.wire(node, pt(rp, 1))
    power(sh, "GND", *pt(rp, 2))
    note(sh, info, x - 10.16, y - 7.4)


def sheet_command():
    sh = new_sheet("command")
    dv = ANALYSIS["dividers"]
    grp = group("CMD")
    heading(sh, "Command bus: resistive dividers at the card fingers",
            "ZCU104 bank %s byte group %s (1.2 V SSTL12 outputs) -> LPDDR5 channel A CA / CK / CS at VDDQ level. "
            "Series resistor at the finger, shunt at the In3 node via." % bank_of(grp["finger_side_nets"]))
    order = ["CK_P", "CK_N", "CS0", "CA0", "CA1", "CA2", "CA3", "CA4", "CA5", "CA6"]
    assert sorted(order) == sorted(grp["nets"])
    ckt, ckc = grp["intra_pair"]["finger_side_nets"]
    fside = dict(zip(grp["nets"], grp["finger_side_nets"]))
    between = open_between(ckt, ckc)
    z0 = NET["rules"]["netclasses"][CMD_CLASS]["layers"]["In3.Cu"]["z0_ohm"]
    ser = {netof(r, 2): r for r, p in PARTS.items() if p["sheet"] == "command" and netof(r, 1) in set(grp["finger_side_nets"])}
    shu = {netof(r, 1): r for r, p in PARTS.items() if p["sheet"] == "command" and netof(r, 2) == "GND"}
    for i, n in enumerate(order):
        x = 50.8 + (i // 5) * 114.3
        y = 50.8 + (i % 5) * 30.48
        divider_cell(sh, ser[n], shu[n], x, y, "%s:  %s  ->  %s" % (n, finger_info(fside[n]), ball_of(n)))
    ca, cs = dv["CA0-6"], dv["CS0"]
    note(sh, "Levels at the ball (min / nominal / max)", 259.08, 43.18, size=1.5, bold=True)
    note(sh,
         "Corners: %s.\n\n"
         "CA0-6, CK_P, CK_N:  %d R / %d R   %.3f / %.3f / %.3f V   pin current %.1f mA max\n"
         "    back impedance %.1f ohm into the %.1f ohm In3 line; limit: %s\n\n"
         "CS0:  %d R / %d R   %.3f / %.3f / %.3f V   pin current %.1f mA max\n"
         "    limit: %s\n\n"
         "FPGA: SSTL12 / DIFF_SSTL12, RDRV_40_40, FAST.\n"
         "%s%s.\n"
         "All %d nets are delay matched FPGA die pad to ball within +/-%g ps (CK_P to CK_N +/-%g ps)\n"
         "on In3 between two ground planes; see design/DESIGN.md section 1."
         % (ANALYSIS["assumptions"].split(". ")[0],
            ca["series"], ca["shunt"], ca["v_min"], ca["v_nom"], ca["v_max"], ca["i_max_mA"],
            ca["back_impedance_ohm"], z0, ca["limit"],
            cs["series"], cs["shunt"], cs["v_min"], cs["v_nom"], cs["v_max"], cs["i_max_mA"], cs["limit"],
            pair_text(ckt, ckc),
            "; finger %s between them stays open" % " / ".join(map(str, between)) if between else "",
            len(grp["nets"]), grp["tolerance_ps"], grp["intra_pair"]["tolerance_ps"]),
         259.08, 49.53, width=104)
    return sh


def sheet_write():
    sh = new_sheet("write")
    dv = ANALYSIS["dividers"]
    gdq, gck = group("WR"), group("WR_WCK")
    heading(sh, "Write path: resistive dividers at the card fingers",
            "ZCU104 bank %s byte group %s (1.2 V outputs) -> LPDDR5 channel A byte 0 DQ and WCK0. "
            "The DQ shunt is also the read load; the read branch leaves the same node without a part (sheet Read)."
            % bank_of(gdq["finger_side_nets"] + gck["finger_side_nets"]))
    order = ["DQ%d" % i for i in range(8)] + ["WCK_P", "WCK_N"]
    ser = {netof(r, 2): r for r, p in PARTS.items() if p["sheet"] == "write" and netof(r, 2) in order
           and netof(r, 1) != netof(r, 2) and netof(r, 1) in set(gdq["finger_side_nets"] + gck["finger_side_nets"])}
    shu = {netof(r, 1): r for r, p in PARTS.items() if p["sheet"] == "write" and netof(r, 2) == "GND"}
    assert sorted(ser) == sorted(order) == sorted(shu)
    for i, n in enumerate(order):
        x = 50.8 + (i // 5) * 114.3
        y = 50.8 + (i % 5) * 30.48
        divider_cell(sh, ser[n], shu[n], x, y,
                     "%s:  %s  ->  %s" % (n, finger_info(netof(ser[n], 1)), ball_of(n)))
    dq, wck, node = dv["DQ0-7 write"], dv["WCK_P/WCK_N (per leg)"], ANALYSIS["write_node"]
    assert gck["nets"] == ["WCK_P", "WCK_N"] and "F.Cu" in gdq["layer"] and "B.Cu" in gck["layer"]
    note(sh, "Levels at the ball (min / nominal / max)", 259.08, 43.18, size=1.5, bold=True)
    note(sh,
         "DQ0-7:  %d R / %d R   %.3f / %.3f / %.3f V   pin current %.1f mA max\n"
         "    limit: %s\n"
         "    node source resistance %.0f ohm, tau %.1f ns: design target %s writes\n\n"
         "WCK_P, WCK_N:  %d R / %d R   %.3f / %.3f / %.3f V per leg\n"
         "    limit: %s\n\n"
         "%s.\n"
         "The write pins are Hi-Z during reads.\n"
         "DQ0-7 are NOT delay matched on the card: die pad to ball +/-%.1f ps (F.Cu comb, the T at\n"
         "the divider); the FPGA closes it with a per-bit output delay (design/gateware_deskew.json).\n"
         "WCK_P to WCK_N within +/-%g ps (B.Cu pair); see design/DESIGN.md section 14."
         % (dq["series"], dq["shunt"], dq["v_min"], dq["v_nom"], dq["v_max"], dq["i_max_mA"], dq["limit"],
            node["source_resistance_ohm"], node["tau_ns"], node["supported_rate"].split(" (")[0],
            wck["series"], wck["shunt"], wck["v_min"], wck["v_nom"], wck["v_max"], wck["limit"],
            pair_text(*gck["finger_side_nets"], inverted="inverted" in gck.get("polarity", "")),
            gdq["spread_half_ps"], gck["tolerance_ps"]),
         259.08, 49.53, width=96)
    return sh


def sheet_read():
    sh = new_sheet("read")
    rd, ref = ANALYSIS["read"], ANALYSIS["reference"]
    here = {r: p for r, p in PARTS.items() if p["sheet"] == "read" and r[0] == "R"}
    rn = {netof(r, 1): r for r in here if netof(r, 1).startswith("VREF_N")}
    cn = sorted(r for r, p in PARTS.items() if p["sheet"] == "read" and r.startswith("C32"))
    assert len(rn) == 8 and len(cn) == 8
    grp = group("RD_P")
    r_n, c_n = rtxt(same_value(rn.values())), ctxt(same_value(cn))
    # no series part in the read branch: the read finger is on the net of the ball
    assert rd["series_ohm"] == 0 and all(finger_of(n)["pn"] == "P" for n in grp["nets"])
    assert not any(netof(r, 2).endswith("_RF") for r in here)
    assert all(finger_of(n)["pn"] == "N" for n in rn)
    heading(sh, "Read path: DQ nodes straight to the P fingers, reference on the N fingers",
            "LPDDR5 DQ0-7 -> divider node (sheet Write) -> ZCU104 bank %s P pins, no series part; every N pin sits on "
            "VREF_RD (pseudo-differential). RDQS is a true differential pair." % bank_of(grp["nets"])[0])

    # --- DQ read fingers (no part: the nets are drawn on sheets Write, LPDDR5 and Connector) -----------------
    note(sh, "DQ read fingers (no series part: same net as the divider node)", 25.4, 33.02, size=1.5, bold=True)
    for i in range(8):
        n = "DQ%d" % i
        x, y = 40.64, 45.72 + i * 12.7
        note(sh, "%s  ->  %s, P   (%s)" % (n, finger_info(n), ball_of(n)), x - 10.16, y - 1.0)
    oe = rd["open_end"]["without_series_resistor"]
    note(sh, "Without a series resistor the open FPGA end rings for 1 .. 2 ns after an edge:\n"
             "%.2f V / %+.2f V at worst (strong driver), %.2f V / %+.2f V nominal; the pin allows\n"
             "%.2f .. %.2f V. Damping, if wanted: MR3 drive strength 48 / 60 ohm (gateware)."
         % (oe[0]["peak_v"], oe[0]["min_v"], oe[1]["peak_v"], oe[1]["min_v"],
            rd["open_end"]["fpga_pin_recommended_v"][0], rd["open_end"]["fpga_pin_recommended_v"][1]),
         25.4, 45.72 + 8 * 12.7 - 4.0, width=88)

    # --- RDQS --------------------------------------------------------------------------------
    rdqs = [(n, next(r for r in here if netof(r, 1) == n and netof(r, 2) == "GND")) for n in ("RDQS_P", "RDQS_N")]
    note(sh, "RDQS: %s load per leg, no series part; host pair DQS6 (fingers 219 / 221) inverted (RDQS_P on its N pin)" % rtxt(same_value(r[1] for r in rdqs)),
         25.4, 151.13, size=1.5, bold=True)
    for i, (n, load) in enumerate(rdqs):
        x, y = 40.64, 165.1 + i * 30.48
        node = (x + 7.62, y)
        rp = place_r(sh, load, node[0], y + 7.62, "v")
        sh.wire((x, y), node, pt(rp, 1))
        sh.glabel(n, x, y, (-1, 0))
        power(sh, "GND", *pt(rp, 2))
        note(sh, "%s  <-  %s" % (finger_info(n), ball_of(n)), x - 10.16, y - 7.4)

    # --- N networks and the reference bus ------------------------------------------------------
    note(sh, "N fingers: %s into VREF_RD, %s to GND at each resistor" % (r_n, c_n), 124.46, 33.02, size=1.5,
         bold=True)
    bx = 172.72
    bus = []
    rows = []
    for i in range(8):
        n = "VREF_N%d" % i
        x, y = 139.7, 45.72 + i * 25.4
        r = place_r(sh, rn[n], x + 10.16, y, "h")
        c = place_c(sh, cn[i], x + 20.32, y + 7.62)
        assert netof(cn[i], 1) == "VREF_RD" and ("N finger of DQ%d," % i) in PARTS[cn[i]]["description"], cn[i]
        node = (x + 20.32, y)
        sh.wire((x, y), pt(r, 1))
        sh.glabel(n, x, y, (-1, 0))
        sh.wire(pt(r, 2), node, (bx, y))
        sh.junction(*node)
        sh.wire(node, pt(c, 1))
        power(sh, "GND", *pt(c, 2))
        note(sh, "%s, N of DQ%d" % (finger_info(n), i), x - 10.16, y - 7.4)
        bus.append((bx, y))
        rows.append(y)
    yv = rows[3] + 12.7                               # between two rows: no four-way junction on the bus
    for a, b in zip(bus, bus[1:]):
        sh.wire(a, b)
    for p in bus[1:-1]:
        sh.junction(*p)

    # --- DAC and the reference divider ------------------------------------------------------
    i2c = ref["i2c_address"].split()[0]
    assert re.fullmatch(r"0x[0-9A-Fa-f]{2}", i2c), i2c
    note(sh, "Reference: %s (I2C %s) and the %s-derived power-up default"
         % (PARTS["U5"]["value"], i2c, netof("R332", 1)), 220.98, 33.02, size=1.5, bold=True)
    x331, x332, x333, xc, xtp, xend = 302.26, 317.5, 332.74, 347.98, 363.22, 373.38
    r331 = place_r(sh, "R331", x331, yv - 7.62, "v")
    r332 = place_r(sh, "R332", x332, yv - 7.62, "v")
    r333 = place_r(sh, "R333", x333, yv + 7.62, "v")
    c331 = place_c(sh, "C331", xc, yv + 7.62)
    tp4 = place_tp(sh, "TP4", xtp, yv)
    assert netof("R331", 2) == netof("R332", 2) == netof("R333", 1) == netof("C331", 1) == "VREF_RD"
    line = [(bx, yv), (x331, yv), (x332, yv), (x333, yv), (xc, yv), pt(tp4, 1), (xend, yv)]
    for a, b in zip(line, line[1:]):
        sh.wire(a, b)
    for p in line[:-1]:
        sh.junction(*p)
    sh.glabel("VREF_RD", xend, yv, (1, 0))
    sh.wire(pt(r331, 2), (x331, yv))
    sh.wire(pt(r332, 2), (x332, yv))
    power(sh, netof("R332", 1), *pt(r332, 1))
    sh.wire((x333, yv), pt(r333, 1))
    power(sh, "GND", *pt(r333, 2))
    sh.wire((xc, yv), pt(c331, 1))
    power(sh, "GND", *pt(c331, 2))

    yo = round(pt(r331, 1)[1] - 22.86, 4)            # row of the DAC output pin
    u5 = place_unit(sh, "U5", "U5", 1, 233.68, round(yo - 7.62, 4))
    assert abs(u5.pin(8)[1] - yo) < 1e-6, (u5.pin(8), yo)
    out, fb = pt(u5, 8), pt(u5, 7)
    assert netof("U5", 8) == netof("U5", 7) == netof("R331", 1) == "DAC_OUT"
    xj = out[0] + 5.08
    sh.wire(out, (xj, yo), (x331, yo), pt(r331, 1))
    sh.wire(fb, (xj, fb[1]), (xj, yo))
    sh.junction(xj, yo)
    sh.junction(xj, fb[1])
    sh.wire((xj, fb[1]), (xj + P, fb[1]))
    sh.glabel("DAC_OUT", xj + P, fb[1], (1, 0))
    cap = pt(u5, 4)                                   # CAP -> C333 -> GND
    c333 = place_c(sh, "C333", cap[0] + 7.62, cap[1] + 6.35)
    sh.wire(cap, (cap[0] + 7.62, cap[1]), pt(c333, 1))
    sh.wire((cap[0] + 7.62, cap[1]), (cap[0] + 12.7, cap[1]))
    sh.junction(cap[0] + 7.62, cap[1])
    sh.glabel("DAC_CAP", cap[0] + 12.7, cap[1], (1, 0))
    power(sh, "GND", *pt(c333, 2))
    vdd = pt(u5, 6)                                   # VDD, decoupled by C332
    vtop = (vdd[0], vdd[1] - 5.08)
    c332 = place_c(sh, "C332", vdd[0] + 20.32, vtop[1] + 3.81)
    assert netof("C332", 1) == netof("U5", 6)
    sh.wire(vdd, vtop, pt(c332, 1))
    sh.wire(vtop, (vtop[0], vtop[1] - P))
    sh.junction(*vtop)
    power(sh, netof("U5", 6), vtop[0], vtop[1] - P)
    power(sh, "GND", *pt(c332, 2))
    for pin in (2, 3):                                # SCL, SDA
        tie(sh, u5, pin, 5.08)
    a0 = pt(u5, 1)                                    # A0 = AGND: address 0x48
    agnd, ep = pt(u5, 5), pt(u5, 9)
    assert netof("U5", 1) == netof("U5", 5) == netof("U5", 9) == "GND"
    yg = agnd[1] + P
    sh.wire(a0, (a0[0] - P, a0[1]), (a0[0] - P, yg), (agnd[0], yg), (ep[0], yg), (ep[0], yg + P))
    sh.wire(agnd, (agnd[0], yg))
    sh.wire(ep, (ep[0], yg))
    sh.junction(agnd[0], yg)
    sh.junction(ep[0], yg)
    power(sh, "GND", ep[0], yg + P)

    note(sh,
         "VREF_RD network:  %s\n"
         "  DAC output Hi-Z (factory default):  %.3f V, %.0f ohm source: reads work before any I2C traffic\n"
         "  DAC active:  %s  (%.3f .. %.3f V, %.2f mV / LSB, code %d = %.3f V)\n"
         "  %s\n\n"
         "Read levels (%d R shunt on sheet Write is the only DC load; no series resistor):\n"
         "  read high %.3f / %.3f / %.3f V  (JEDEC bound %.3f .. %.3f V),  reference %.3f V\n"
         "  receiver: IBUFDS_DPHY, IOSTANDARD MIPI_DPHY_DCI, DIFF_TERM = FALSE; read pins are inputs only\n"
         "  each N line ends in %s = the host line impedance, AC-grounded by %s\n\n"
         "RDQS_P / RDQS_N:  VID %.3f V, VICM %.3f V while the DRAM drives; both legs 0 V otherwise:\n"
         "  gate the receiver output with the read-enable window.\n"
         "DQ0-7 read paths are delay matched ball to FPGA die pad within +/-%g ps (In3 bundle)."
         % (ref["network"].replace(" ;", ";"),
            ref["dac_high_z_v"], ref["dac_high_z_source_ohm"], ref["dac_active"],
            ref["range_internal_ref_x1p5_v"][0], ref["range_internal_ref_x1p5_v"][1], ref["lsb_mV"],
            ref["dac_code_for_0p195_v"], rd["reference_v"], rd["reference_rule"].split("; the DAC covers")[0],
            rd["load_ohm"],
            rd["read_high_v"][0], rd["read_high_v"][1], rd["read_high_v"][2],
            rd["read_high_jedec_bound_v"][0], rd["read_high_jedec_bound_v"][2], rd["reference_v"],
            r_n, c_n,
            rd["rdqs_true_differential"]["vid_v"], rd["rdqs_true_differential"]["vicm_v"],
            grp["tolerance_ps"]),
         200.66, 162.56, width=120)
    return sh


def sheet_power():
    sh = new_sheet("power")
    rails = ANALYSIS["rails"]
    heading(sh, "Power: three LDOs from the host 1.2 V and 3.3 V fingers",
            "Sequence: VDD1 (enabled by V1P2) -> VDD2H = VDD2L (enabled by VDD1, soft start) -> "
            "VDDQ (enabled by the VDD2H power good).")

    def in_caps(pin_pt, refs, xs, rail_x):
        """Input line going left from a pin: capacitors hanging to GND, rail symbol at the end."""
        end = (rail_x, pin_pt[1])
        pts = [pin_pt, end]
        for r, x in zip(refs, xs):
            c = place_c(sh, r, x, pin_pt[1] + 3.81)
            pts.append(pt(c, 1))
            power(sh, "GND", *pt(c, 2))
        bar(sh, pts)
        power(sh, netof(refs[0], 1), *end)

    def out_line(pin_pt, items, x_end, net):
        """Output line going right from a pin; items: (kind, ref, x) hanging from / standing on it."""
        pts = [pin_pt, (x_end, pin_pt[1])]
        for kind, ref, x in items:
            if kind == "c":
                c = place_c(sh, ref, x, pin_pt[1] + 3.81)
                assert netof(ref, 1) == net
                pts.append(pt(c, 1))
                power(sh, "GND", *pt(c, 2))
            elif kind == "r":
                r = place_r(sh, ref, x, pin_pt[1] + 3.81, "v")
                assert netof(ref, 1) == net and netof(ref, 2) == "GND"
                pts.append(pt(r, 1))
                power(sh, "GND", *pt(r, 2))
            elif kind == "tp":
                t = place_tp(sh, ref, x, pin_pt[1])
                assert netof(ref, 1) == net
                pts.append(pt(t, 1))
            else:
                pts.append((x, pin_pt[1]))
        bar(sh, pts)
        power(sh, net, x_end, pin_pt[1])

    def fb_divider(out_y, fb_pin, upper, lower, x, net):
        """Feedback divider hanging from the output line at x; returns the node."""
        assert netof(upper, 2) == netof(lower, 1) == net and netof(lower, 2) == "GND"
        ru = place_r(sh, upper, x, out_y + 3.81, "v")
        node = (x, out_y + 10.16)                      # the feedback wire joins here,
        tap = (x, node[1] + P)                         # the label one step lower: two three-way junctions
        rl = place_r(sh, lower, x, tap[1] + P + 3.81, "v")
        sh.wire(pt(ru, 2), node, tap, pt(rl, 1))
        power(sh, "GND", *pt(rl, 2))
        xa = fb_pin[0] + 7.62
        sh.wire(fb_pin, (xa, fb_pin[1]), (xa, node[1]), node)
        sh.wire(tap, (x + P, tap[1]))
        sh.junction(*node)
        sh.junction(*tap)
        sh.glabel(net, x + P, tap[1], (1, 0))
        return pt(ru, 1)

    def divider_v(vref, upper, lower, rail):
        """Output voltage of a feedback divider; must be the nominal value of the rail in the analysis."""
        v = vref * (1 + ohms(upper) / ohms(lower))
        assert abs(v - rails[rail]["v"][1]) < 0.002, (rail, v, rails[rail]["v"])
        return v

    def bias_cap(pin_pt, ref, rail):
        """Bias pin on top: up to the rail symbol, decoupling capacitor to the right."""
        j = (pin_pt[0], pin_pt[1] - 15.24)
        c = place_c(sh, ref, pin_pt[0] + 10.16, j[1] + 3.81)
        assert netof(ref, 1) == rail
        sh.wire(pin_pt, j, pt(c, 1))
        sh.wire(j, (j[0], j[1] - P))
        sh.junction(*j)
        power(sh, rail, j[0], j[1] - P)
        power(sh, "GND", *pt(c, 2))

    X = 101.6
    # --- U3: VDD1 ------------------------------------------------------------------------------
    y3 = 60.96
    note(sh, "VDD1  %.2f V  (%s)" % (rails["VDD1"]["v"][1], rails["VDD1"]["regulator"]), 25.4, y3 - 22.86,
         size=1.5, bold=True)
    u3 = sh.place(SYM["U3"], "U3", X, y3, 0,
                  fields={"Reference": fld(0, X - 5.08, y3 - 6.2), "Value": fld(0, X - 1.5, y3 - 6.2)},
                  **part_kw("U3"))
    sh.connected.add(("U3", "4"))                     # hidden pin of type "not connected"
    assert netof("U3", 4) == "" and SYM["U3"].pins["4"]["etype"] == "no_connect"
    vin = pt(u3, 1)
    in_caps(vin, ["C506"], [vin[0] - 15.24], vin[0] - 25.4)
    en = pt(u3, 3)
    assert netof("R504", 2) == netof("U3", 3) == "PWR_EN"
    ye = en[1] + 17.78
    r504 = place_r(sh, "R504", en[0] - 15.24, ye, "h")
    corner = (en[0] - 5.08, ye)
    sh.wire(en, (corner[0], en[1]), corner, pt(r504, 2))
    sh.wire(corner, (corner[0] + P, ye))
    sh.junction(*corner)
    sh.glabel("PWR_EN", corner[0] + P, ye, (1, 0))
    v1 = (en[0] - 25.4, ye)
    sh.wire(pt(r504, 1), v1)
    power(sh, netof("R504", 1), *v1)
    power(sh, "GND", *pt(u3, 2))
    out = pt(u3, 5)
    out_line(out, [("c", "C507", out[0] + 12.7), ("r", "R509", out[0] + 25.4), ("tp", "TP1", out[0] + 38.1)],
             out[0] + 48.26, "VDD1")
    note(sh, "EN = %s through %s: VDD1 cannot rise before the host 1.2 V rail.\n"
             "R509 (%s) empties VDD1 after power-off (no output discharge in U3).\n%s"
         % (netof("R504", 1), rtxt("R504"), rtxt("R509"), stacked_pin_note("U3", "U3")),
         out[0] + 60.96, y3 - 6.35)

    # --- U2: VDD2H -------------------------------------------------------------------------------
    y2 = 132.08
    note(sh, "VDD2H = VDD2L  %.3f V  (%s)" % (rails["VDD2H"]["v"][1], rails["VDD2H"]["regulator"]), 25.4,
         y2 - 35.56, size=1.5, bold=True)
    u2 = sh.place(SYM["U2"], "U2", X, y2, 0,
                  fields={"Reference": fld(0, X + 2.0, y2 - 6.4), "Value": fld(0, X + 5.6, y2 - 6.4)},
                  **part_kw("U2"))
    vin = pt(u2, 1)
    assert u2.pin(2) == vin and netof("U2", 2) == netof("U2", 1)
    pt(u2, 2)
    in_caps(vin, ["C501"], [vin[0] - 27.94], vin[0] - 38.1)
    tie(sh, u2, 5, 5.08, upright=False)                # EN = VDD1 (IN above and SS below: no room for a jog)
    ss = pt(u2, 7)
    js = (ss[0] - P, ss[1] + 5.08)
    c504 = place_c(sh, "C504", js[0], js[1] + 3.81)
    assert netof("C504", 1) == netof("U2", 7)
    sh.wire(ss, (js[0], ss[1]), js)
    sh.wire(js, pt(c504, 1))
    sh.wire(js, (js[0] - P, js[1]))
    sh.junction(*js)
    sh.glabel(netof("U2", 7), js[0] - P, js[1], (-1, 0))
    power(sh, "GND", *pt(c504, 2))
    g = pt(u2, 6)
    assert u2.pin(11) == g and netof("U2", 11) == "GND"
    pt(u2, 11)
    power(sh, "GND", *g)
    bias_cap(pt(u2, 4), "C503", netof("U2", 4))
    out = pt(u2, 9)
    assert u2.pin(10) == out and netof("U2", 10) == netof("U2", 9)
    pt(u2, 10)
    xd = out[0] + 17.78
    fb_divider(out[1], pt(u2, 8), "R501", "R502", xd, netof("U2", 8))
    out_line(out, [("j", None, xd), ("c", "C505", out[0] + 43.18), ("tp", "TP2", out[0] + 55.88)],
             out[0] + 66.04, "VDD2H")
    pg = pt(u2, 3)
    yp = pg[1] + 27.94
    xr = out[0] + 35.56
    r503 = place_r(sh, "R503", xr, yp - 3.81, "v")
    assert netof("R503", 2) == netof("U2", 3) == "PG_VDD2H"
    sh.wire(pg, (pg[0] + P, pg[1]), (pg[0] + P, yp), pt(r503, 2), (xr + 5.08, yp))
    sh.junction(xr, yp)
    sh.glabel("PG_VDD2H", xr + 5.08, yp, (1, 0))
    power(sh, netof("R503", 1), *pt(r503, 1))
    note(sh, "VOUT = 0.8 V x (1 + R501 / R502) = %.3f V, %s.\n"
             "EN = %s.  C504 (%s): %.1f ms soft start.  PG (open drain, %s to %s) enables U4.\n%s"
         % (divider_v(0.8, "R501", "R502", "VDD2H"), rails["VDD2H"]["accuracy"].split(" + ")[0],
            netof("U2", 5), ctxt("C504"), rails["VDD2H"]["soft_start_ms"], rtxt("R503"), netof("R503", 1),
            stacked_pin_note("U2", "U2")),
         out[0] + 78.74, y2 - 6.35, width=62)

    # --- U4: VDDQ ---------------------------------------------------------------------------------
    y4 = 210.82
    note(sh, "VDDQ  %.3f V  (%s)" % (rails["VDDQ"]["v"][1], rails["VDDQ"]["regulator"]), 25.4, y4 - 38.1,
         size=1.5, bold=True)
    g4 = SYM["U4"].geom[1]
    u4 = place_unit(sh, "U4", "U4", 1, X - g4["cx"], y4 - g4["cy"])
    vin = pt(u4, 3)
    in_caps(vin, ["C508"], [vin[0] - 30.48], vin[0] - 40.64)
    tie(sh, u4, 6, 5.08)                               # EN = PG_VDD2H
    gp, ep = pt(u4, 2), pt(u4, 7)
    assert netof("U4", 2) == netof("U4", 7) == "GND"
    yg = gp[1] + P
    mid = (snap((gp[0] + ep[0]) / 2), yg)
    assert on_grid((gp[0] + ep[0]) / 2)
    sh.wire(gp, (gp[0], yg), mid, (ep[0], yg), ep)
    sh.wire(mid, (mid[0], yg + P))
    sh.junction(*mid)
    power(sh, "GND", mid[0], yg + P)
    bias_cap(pt(u4, 1), "C509", netof("U4", 1))
    out = pt(u4, 4)
    xd = out[0] + 17.78
    fb_divider(out[1], pt(u4, 5), "R506", "R507", xd, netof("U4", 5))
    out_line(out, [("j", None, xd), ("c", "C510", out[0] + 43.18), ("r", "R508", out[0] + 55.88),
                   ("tp", "TP3", out[0] + 68.58)], out[0] + 78.74, "VDDQ")
    assert netof("U4", 6) == netof("U2", 3)
    note(sh, "VOUT = 0.4 V x (1 + R506 / R507) = %.4f V.\n"
             "EN = %s: VDDQ starts when U2 reports power good.\n"
             "R508 (%s) discharges VDDQ after U4 is disabled, time constant %.2g ms."
         % (divider_v(0.4, "R506", "R507", "VDDQ"), netof("U4", 6), rtxt("R508"),
            rails["VDDQ"]["bleeder_tau_ms"]),
         out[0] + 91.44, y4 - 6.35)

    # --- host rails ---------------------------------------------------------------------------------
    hx = 281.94
    note(sh, "Host rails at the fingers", hx - 12.7, 33.02, size=1.5, bold=True)
    y = 55.88
    v1caps = ["C502", "C512", "C513"]
    assert all(netof(r, 1) == "V1P2" and netof(r, 2) == "GND" for r in v1caps)
    pts = [(hx, y), (hx + 12.7, y)]
    for i, r in enumerate(v1caps):
        c = place_c(sh, r, hx + 27.94 + i * 12.7, y + 3.81)
        pts.append(pt(c, 1))
        power(sh, "GND", *pt(c, 2))
    bar(sh, pts)
    power(sh, "V1P2", hx, y)
    flag(sh, hx + 12.7, y)
    n_vdd, n_vss = len(fingers_with("VDD")), len(fingers_with("VSS"))
    assert n_vdd == rails["socket"]["V1P2_contacts"] and all(netof("J1", n) == "V1P2" for n in fingers_with("VDD"))
    note(sh, "1.2 V: %d VDD fingers, %.2f A max (%d mA per contact)"
         % (n_vdd, rails["socket"]["V1P2_A"], rails["socket"]["V1P2_mA_per_contact"]), hx - 12.7, y - 14.0)
    y = 91.44
    assert netof("C511", 1) == "V3P3"
    c = place_c(sh, "C511", hx + 27.94, y + 3.81)
    bar(sh, [(hx, y), (hx + 12.7, y), pt(c, 1)])
    power(sh, "GND", *pt(c, 2))
    power(sh, "V3P3", hx, y)
    flag(sh, hx + 12.7, y)
    f33 = finger_of("V3P3")
    note(sh, "3.3 V: finger %d (%s), %d mA max" % (f33["finger"], f33["ddr4_name"], rails["socket"]["V3P3_mA"]),
         hx - 12.7, y - 14.0)
    # assembly fiducials: marks on the board, no pin and no net
    fids = sorted(r for r in PARTS if r.startswith("FID"))
    note(sh, "Assembly fiducials (three per face)", hx - 12.7, 143.0)
    for i, ref in enumerate(fids):
        fx_, fy_ = hx - 7.62 + 10.16 * i, 152.4
        sh.place(SYM["FID"], ref, fx_, fy_, 0, fields={"Reference": fld(0, fx_, fy_ - 3.6, "center"), "Value": fld(0, fx_, fy_ + 3.6, "center")},
                 **part_kw(ref))
    y = 127.0
    tp5 = place_tp(sh, "TP5", hx + 27.94, y)
    bar(sh, [(hx, y), (hx + 12.7, y), pt(tp5, 1)])
    power(sh, "GND", hx, y)
    sh.gnd_flag = flag(sh, hx + 12.7, y)
    assert all(netof("J1", n) == "GND" for n in fingers_with("VSS"))
    note(sh, "GND: %d VSS fingers" % n_vss, hx - 12.7, y - 14.0)
    unused = sorted({FINGERS[n]["ddr4_name"].rstrip("12") for n in fingers_with("no")
                     if FINGERS[n]["ddr4_name"].startswith(("VPP", "VTT", "VREFCA"))})
    assert unused == ["VPP", "VREFCA", "VTT"], unused
    assert not [f for f in NET["fingers"] if f["ddr4_name"].startswith(("VPP", "VTT", "VREFCA")) and f["net"]]
    note(sh,
         "Power-up (JESD209-5 Table 19 / 20): nothing starts until both host rails are up;\n"
         "VDD1 first, VDD2H ramps %.1f ms behind it, VDDQ follows the VDD2H power good.\n"
         "Power-off: %s.\n\n"
         "Not connected: VPP (2.5 V), VTT, VREFCA.  No SPD EEPROM."
         % (rails["VDD2H"]["soft_start_ms"],
            "1.2 V first: VDD2H follows, PG disables U4, R508 pulls VDDQ down, VDD1 falls last"),
         hx - 12.7, 144.78)
    return sh


def sheet_connector():
    sh = new_sheet("connector")
    heading(sh, "J1: DDR4 SO-DIMM card edge, 260 fingers",
            "Plugs into the ZCU104 PL DDR4 socket. Pin names: DDR4 SO-DIMM signal name (FPGA ball of the "
            "XCZU7EV). Odd fingers on the front, even fingers on the back.")
    g = SYM["J1"].groups
    # signal units: stub and global label on every pin
    ua = place_unit(sh, "J1", "J1", 1, 25.4, 38.1)
    ub = place_unit(sh, "J1", "J1", 2, 25.4, 88.9)
    uc = place_unit(sh, "J1", "J1", 3, 25.4, 142.24)
    for inst, pins in ((ua, g["cmd"]), (ub, g["rst"] + g["wr"]), (uc, g["rd"])):
        for n in pins:
            tie(sh, inst, n, 5.08)
    note(sh, "to sheet Command", 25.4, 38.1 + SYM["J1"].geom[1]["H"] + 1.5)
    note(sh, "to sheets Write and LPDDR5 (RESET_N_F)", 25.4, 88.9 + SYM["J1"].geom[2]["H"] + 1.5)
    note(sh, "read fingers: DQx (the net of the divider node, sheet Write) = P pin,\nVREF_Nx = N pin of the same "
             "differential input (sheet Read); RDQS_P / RDQS_N = true pair", 25.4, 142.24 + SYM["J1"].geom[3]["H"] + 1.5)
    # power and I2C
    ud = place_unit(sh, "J1", "J1", 4, 111.76, 38.1)
    vdd = bar(sh, [pt(ud, n) for n in g["vdd"]])
    top = vdd[0]
    sh.wire(top, (top[0] + 7.62, top[1]))
    sh.junction(*top)
    power(sh, "V1P2", top[0] + 7.62, top[1])
    for n in g["spd"] + g["i2c"]:
        tie(sh, ud, n, 5.08)
    note(sh, "%d VDD fingers: host 1.2 V\n%s: host 3.3 V\nSCL / SDA: host I2C (4.99 k pull-ups to 3.3 V\n"
             "on the ZCU104), reference DAC at %s"
         % (len(g["vdd"]), FINGERS[g["spd"][0]]["ddr4_name"], ANALYSIS["reference"]["i2c_address"].split()[0]),
         111.76, 38.1 + SYM["J1"].geom[4]["H"] + 1.5)
    # ground
    ue = place_unit(sh, "J1", "J1", 5, 195.58, 38.1)
    for side in ("gnd_left", "gnd_right"):
        pts = bar(sh, [pt(ue, n) for n in g[side]])
        end = (pts[-1][0], pts[-1][1] + 5.08)
        sh.wire(pts[-1], end)
        power(sh, "GND", *end)
    # unused fingers
    uf = place_unit(sh, "J1", "J1", 6, 270.51 + 1.27 * 3, 38.1)
    for n in g["nc"]:
        tie(sh, uf, n)
    ck = list(group("CMD")["intra_pair"]["finger_side_nets"])
    low = open_between(*ck)
    fam = {}                                           # open fingers without an FPGA pin, by signal family
    for n in g["nc"]:
        if not FINGERS[n]["bank"]:
            name = re.sub(r"_[TCN]$", "", FINGERS[n]["ddr4_name"].split("/")[0])
            fam.setdefault(re.match(r"[A-Z]+", name).group(0), set()).add(name)
    others = ", ".join(sorted(k + "x" if len(v) > 1 else min(v) for k, v in fam.items()))
    note(sh, "%d fingers are left open on the card: unused FPGA pins of banks %s, and the fingers\n"
             "without an FPGA pin (%s).%s"
         % (len(g["nc"]), " / ".join(str(b) for b in sorted({FINGERS[n]["bank"] for n in g["nc"]
                                                             if FINGERS[n]["bank"]})), others,
            "\nGateware drives finger %s (between the CK fingers) low." % " / ".join(map(str, low)) if low else ""),
         270.51 + 1.27 * 3, 38.1 + SYM["J1"].geom[6]["H"] + 1.5)
    return sh


def sheet_lpddr5():
    sh = new_sheet("lpddr5")
    dv = ANALYSIS["dividers"]["RESET_n"]
    heading(sh, "U1: LPDDR5 SDRAM, channel A / byte 0 / rank 0 in use",
            "%s. Channel B and the upper byte of channel A are tied off or left open as listed below."
            % NET["meta"]["target_dram"])
    assert PARTS["U1"]["value"] in NET["meta"]["target_dram"]
    g = SYM["U1"].groups
    geo = SYM["U1"].geom
    # --- unit A: used signals -----------------------------------------------------------------
    ua = place_unit(sh, "U1", "U1", 1, 40.64, 35.56)
    for b, pin in SYM["U1"].unit_pins(1).items():
        tie(sh, ua, b, 5.08)
    # --- unit B: unused channel-A signals -----------------------------------------------------------
    yb = 116.84
    ub = place_unit(sh, "U1", "U1", 2, 40.64, yb)
    tied = [b for b in SYM["U1"].unit_pins(2) if netof("U1", b) == "GND"]
    for b in SYM["U1"].unit_pins(2):
        if b not in tied:
            tie(sh, ub, b, 5.08)
    tip = tied_group(sh, ub, tied, "GND")
    note(sh, "CS1_A, DMI0_A, DMI1_A strapped to the adjacent VSS ball on F.Cu.\n"
             "DMI: READ FIFO, read DBI, link ECC and read data copy must stay off.\n"
             "DQ8-15_A and RDQS1_t/c_A open (unused byte: no pull-downs, DESIGN.md section 5).", 40.64, yb + geo[2]["H"] + 1.5)
    # --- support parts ---------------------------------------------------------------------------
    cx = 127.0
    note(sh, "ZQ: %s %s to %s" % (rtxt("R401"), re.search(r"\d+ %", PARTS["R401"]["description"]).group(0),
                                  netof("R401", 2)), cx - 10.16, 35.56)
    r401 = place_r(sh, "R401", cx + 10.16, 45.72, "h")
    sh.wire((cx, 45.72), pt(r401, 1))
    sh.glabel(netof("R401", 1), cx, 45.72, (-1, 0))
    sh.wire(pt(r401, 2), (cx + 20.32, 45.72))
    power(sh, netof("R401", 2), cx + 20.32, 45.72)
    assert (ohms("R402"), ohms("R403")) == (dv["series"], dv["shunt"])
    note(sh, "RESET_n: %s, %s + %s: %.2f / %.2f / %.2f V" % (finger_info("RESET_N_F"), rtxt("R402"), rtxt("R403"),
                                                           dv["v_min"], dv["v_nom"], dv["v_max"]),
         cx - 10.16, 58.42)
    divider_cell(sh, "R402", "R403", cx, 68.58, "")
    note(sh, "Unused WCK1: static low / high (clock stop level)", cx - 10.16, 93.98)
    for i, (ref, y) in enumerate((("R404", 104.14), ("R405", 119.38))):
        r = place_r(sh, ref, cx + 10.16, y, "h")
        sh.wire((cx, y), pt(r, 1))
        sh.glabel(netof(ref, 1), cx, y, (-1, 0))
        sh.wire(pt(r, 2), (cx + 20.32, y))
        if netof(ref, 2) == "GND":
            sh.wire((cx + 20.32, y), (cx + 20.32, y + P))
            power(sh, "GND", cx + 20.32, y + P)
        else:
            power(sh, netof(ref, 2), cx + 20.32, y)
    # --- unit C: channel B, NC, RFU ----------------------------------------------------------------
    xc = 205.74
    uc = place_unit(sh, "U1", "U1", 3, xc, 35.56)
    tied_group(sh, uc, g["chb_vddq"], "VDDQ")
    tied_group(sh, uc, g["chb_gnd"], "GND")
    for b in g["rfu"] + g["nc"] + g["chb_open"]:
        tie(sh, uc, b)
    note(sh, "Channel B: inputs strapped to the adjacent VSS / VDDQ ball\n(CK_c_B through its via-in-pad), "
             "DQ / DMI / RDQS open.", xc - 17.78, 35.56 + geo[3]["H"] + 1.5)
    # --- unit D: supplies ---------------------------------------------------------------------------
    xd = 261.62
    ud = place_unit(sh, "U1", "U1", 4, xd, 35.56)
    for grp, net, side in ((g["vdd2h"], "VDD2H", -1), (g["vdd2l"], "VDD2H", 1), (g["vddq"], "VDDQ", 1),
                           (g["vdd1"], "VDD1", 1)):
        assert all(netof("U1", b) == net for b in grp), net
        pts = bar(sh, [pt(ud, b) for b in grp])
        end = (pts[0][0] + side * 7.62, pts[0][1])
        sh.wire(pts[0], end)
        power(sh, net, *end)
    l0 = ud.pin(g["vdd2l"][0])
    note(sh, "VDD2L = VDD2H\n(single rail,\nMR13 OP[7] = 1)", l0[0] + 12.7, l0[1] - 1.0)
    for grp in (g["vdd2h"], g["vdd2l"], g["vddq"], g["vdd1"]):
        sh.junction(*ud.pin(grp[0]))
    # --- unit E: VSS ---------------------------------------------------------------------------------
    xe = 350.52
    ue = place_unit(sh, "U1", "U1", 5, xe, 35.56)
    for side in ("vss_left", "vss_right"):
        pts = bar(sh, [pt(ue, b) for b in g[side]])
        end = (pts[-1][0], pts[-1][1] + 5.08)
        sh.wire(pts[-1], end)
        power(sh, "GND", *end)
    # --- decoupling -----------------------------------------------------------------------------------
    caps = sorted(r for r, p in PARTS.items() if p["sheet"] == "lpddr5" and r.startswith("C"))
    by_rail = {}
    for r in caps:
        by_rail.setdefault(netof(r, 1), []).append(r)
    assert set(by_rail) == {"VDD2H", "VDDQ", "VDD1"}
    note(sh, "Decoupling on B.Cu under U1, on the via-in-pad supply balls", 25.4, 205.74, size=1.5, bold=True)
    cap_row(sh, by_rail["VDD2H"], 40.64, 220.98)
    cap_row(sh, by_rail["VDDQ"], 40.64, 251.46)
    cap_row(sh, by_rail["VDD1"], 40.64 + 11.43 * 10, 251.46)
    return sh


def sheet_root(subs):
    sh = Sheet(PROJECT, "root", PROJECT + ".kicad_sch", ROOT_UUID, "/" + ROOT_UUID, 1, TITLE,
               date=NET["meta"]["date"], rev=NET["meta"]["revision"],
               comments=("Sheet: overview", "Generated from design/netlist.json by scripts/gen_schematic.py"))
    sh.text(TITLE, 15.24, 15.5, size=4.0, bold=True, justify="left top")
    note(sh, "One LPDDR5 SDRAM on a DDR4 SO-DIMM card for the ZCU104 PL memory socket: %s.\n"
             "Commands and writes go through resistive dividers at the card fingers; reads go "
             "straight to the FPGA, pseudo-differential against a DAC reference."
         % NET["meta"]["scope"].replace("LPDDR5 ", ""), 15.24, 23.5, size=1.5)
    wr = group("WR")["finger_side_nets"] + group("WR_WCK")["finger_side_nets"]
    boxes = {
        "connector": (40.64, 66.04, 45.72, 127.0, "J1: DDR4 SO-DIMM card edge\n%d fingers" % len(FINGERS)),
        "command": (132.08, 66.04, 50.8, 25.4, "dividers CA0-6, CK_P/C, CS0\nbank %s %s"
                    % bank_of(group("CMD")["finger_side_nets"])),
        "write": (132.08, 111.76, 50.8, 25.4, "dividers DQ0-7, WCK_P/C\nbank %s %s" % bank_of(wr)),
        "read": (132.08, 157.48, 50.8, 35.56, "DQ read fingers, RDQS loads,\nN-pin reference, %s;  bank %s"
                 % (PARTS["U5"]["value"], bank_of(group("RD_P")["nets"])[0])),
        "lpddr5": (228.6, 66.04, 45.72, 127.0, "U1 LPDDR5, tie-offs,\nRESET_n, ZQ, decoupling"),
        "power": (132.08, 218.44, 50.8, 25.4, "VDD1, VDD2H / VDD2L, VDDQ"),
    }
    for key, name, fn, page in SHEETS:
        x, y, w, h, descr = boxes[key]
        sh.items.append(
            ["sheet", ["at", x, y], ["size", w, h], ["fields_autoplaced", "yes"],
             ["stroke", ["width", 0.1524], ["type", "solid"]], ["fill", ["color", 0, 0, 0, 0.0]],
             ["uuid", Q(sheet_uuid(key))],
             ["property", Q("Sheetname"), Q(name), ["at", x, y - 0.7, 0], fx(justify="left bottom")],
             ["property", Q("Sheetfile"), Q(fn), ["at", x, y + h + 0.6, 0], fx(justify="left top")],
             ["instances", ["project", Q(PROJECT), ["path", Q("/" + ROOT_UUID), ["page", Q(str(page))]]]]])
        sh.text(descr, x + w / 2, y + h / 2, size=1.27, justify="center")

    def arrow(pts, text=None, tx=None, ty=None):
        sh.line(pts, style="solid", width=0.3)
        (x0, y0), (x1, y1) = pts[-2], pts[-1]
        dx, dy = (x1 - x0, y1 - y0)
        n = math.hypot(dx, dy)
        dx, dy = dx / n, dy / n
        a, b = 2.0, 0.8
        sh.line([(x1 - a * dx - b * dy, y1 - a * dy + b * dx), (x1, y1),
                 (x1 - a * dx + b * dy, y1 - a * dy - b * dx)], style="solid", width=0.3)
        if text:
            sh.text(text, tx, ty, justify="center")

    cx1, cx2 = 86.36, 132.08          # connector right edge, middle column left edge
    mx1, mx2 = 182.88, 228.6          # middle column right edge, LPDDR5 left edge
    arrow([(cx1, 78.74), (cx2, 78.74)], "CAx_F, CK_x_F, CS0_F", 109.22, 76.2)
    arrow([(mx1, 78.74), (mx2, 78.74)], "CA0-6, CK_P/C, CS0", 205.74, 76.2)
    arrow([(cx1, 124.46), (cx2, 124.46)], "DQx_WF, WCK_F_x", 109.22, 121.92)
    arrow([(mx1, 121.92), (mx2, 121.92)], "DQ0-7 (write), WCK_P/C", 205.74, 119.38)
    arrow([(mx2, 165.1), (mx1, 165.1)], "DQ0-7 (read), RDQS_P/C", 205.74, 162.56)
    arrow([(cx2, 165.1), (cx1, 165.1)], "DQ0-7, RDQS_P/N", 109.22, 162.56)
    arrow([(cx2, 175.26), (cx1, 175.26)], "VREF_N0-7", 109.22, 172.72)
    arrow([(cx1, 185.42), (cx2, 185.42)], "SCL, SDA", 109.22, 182.88)
    arrow([(81.28, 193.04), (81.28, 203.2), (256.54, 203.2), (256.54, 193.04)], "RESET_N_F", 157.48, 200.66)
    arrow([(71.12, 193.04), (71.12, 231.14), (cx2, 231.14)], "V1P2, V3P3", 101.6, 228.6)
    arrow([(mx1, 231.14), (266.7, 231.14), (266.7, 193.04)], "VDD1, VDD2H, VDDQ", 224.79, 228.6)
    note(sh, "Net names", 297.18, 63.5, size=1.5, bold=True)
    note(sh,
         "X_F, X_WF:  finger side of a divider (FPGA output)\n"
         "X:  ball side (U1); DQx and RDQS_x also reach their\n"
         "    read finger (FPGA input, P pin): no series part\n"
         "VREF_Nx:  N finger of read bit x, %s into VREF_RD\n"
         "V1P2, V3P3:  host rails;   VDD1, VDD2H, VDDQ:  card rails\n\n"
         "All nets are global labels or power symbols; the sheets\n"
         "have no hierarchical pins."
         % rtxt(same_value(r for r, p in PARTS.items() if r[0] == "R" and p["pins"]["1"].startswith("VREF_N"))),
         297.18, 69.85)
    note(sh, "Design data", 297.18, 111.76, size=1.5, bold=True)
    note(sh,
         "design/netlist.json:  parts, pins, fingers, delay targets,\n"
         "    stackup, rules, floorplan (single source of truth)\n"
         "design/DESIGN.md:  rationale\n"
         "scripts/gen_schematic.py:  writes these sheets from the netlist\n"
         "scripts/check_schematic.py:  ERC and pin-by-pin netlist parity\n\n"
         "%d parts, %d in the BOM, %s DNP.\n"
         "Host: %s"
         % (NET["meta"]["part_counts"]["total"], NET["meta"]["part_counts"]["in_bom"],
            NET["meta"]["part_counts"]["dnp"] or "none",
            NET["meta"]["host"].replace(", socket", ",\n      socket")), 297.18, 118.11)
    return sh


# =============================================================================================
# output
# =============================================================================================
SYM_TABLE_HEAD = "(sym_lib_table\n  (version 7)\n"
FP_STOCK = ["Capacitor_SMD", "Package_SON", "Package_TO_SOT_SMD", "Resistor_SMD", "TestPoint"]


def sym_lib_table():
    """The project library only: no row may point at a stock library, whose path and file layout depend on
    the KiCad installation and whose nickname would shadow the user's global table."""
    rows = ['  (lib (name "%s")(type "KiCad")(uri "${KIPRJMOD}/lib/%s.kicad_sym")(options "")'
            '(descr "Every symbol of this design"))' % (LIB, LIB)]
    return SYM_TABLE_HEAD + "\n".join(rows) + "\n)\n"


# ERC rule severities: KiCad's defaults written out, with the two checks that KiCad ignores by default but
# this schematic passes (four_way_junction, footprint_filter) switched on.  single_global_label stays ignored:
# every net is named by a global label, so a net that lives on one sheet has a label that appears once
# (check_schematic.py verifies that these are exactly the single-sheet nets).
ERC_SEVERITIES = {
    "bus_definition_conflict": "error", "bus_entry_needed": "error", "bus_to_bus_conflict": "error",
    "bus_to_net_conflict": "error", "different_unit_footprint": "error", "different_unit_net": "error",
    "duplicate_reference": "error", "duplicate_sheet_names": "error", "endpoint_off_grid": "warning",
    "extra_units": "error", "field_name_whitespace": "warning", "footprint_filter": "warning",
    "footprint_link_issues": "warning", "four_way_junction": "warning", "global_label_dangling": "warning",
    "ground_pin_not_ground": "warning", "hier_label_mismatch": "error", "isolated_pin_label": "warning",
    "label_dangling": "error", "label_multiple_wires": "warning", "lib_symbol_issues": "warning",
    "lib_symbol_mismatch": "warning", "missing_bidi_pin": "warning", "missing_input_pin": "warning",
    "missing_power_pin": "error", "missing_unit": "warning", "multiple_net_names": "warning",
    "net_not_bus_member": "warning", "no_connect_connected": "warning", "no_connect_dangling": "warning",
    "pin_not_connected": "error", "pin_not_driven": "error", "pin_to_pin": "warning",
    "power_pin_not_driven": "error", "same_local_global_label": "warning", "similar_label_and_power": "warning",
    "similar_labels": "warning", "similar_power": "warning", "simulation_model_issue": "ignore",
    "single_global_label": "ignore", "stacked_pin_name": "warning", "unannotated": "error",
    "unconnected_wire_endpoint": "warning", "undefined_netclass": "error", "unit_value_mismatch": "error",
    "unresolved_variable": "error", "wire_dangling": "error",
}


def erc_settings(subs):
    """The "erc" section for the .kicad_pro.  Exclusions: the DMI balls of the unused data-mask function are
    bidirectional pins strapped straight to VSS (design/DESIGN.md section 5), which KiCad reports against the
    power-output pin of the GND PWR_FLAG.  One exclusion per ball, in KiCad's marker syntax
    type|x|y|pin uuid|flag pin uuid|sheet path|sheet path of the pin|sheet path of the flag  (x, y in 100 nm)."""
    by_name = {sh.name: sh for sh in subs}
    flag_inst = by_name["power"].gnd_flag
    flag_pin = uid("pin/%s/%s/%d/%s" % ("power", flag_inst.ref, flag_inst.unit, "1"))
    excl = []
    for inst in by_name["lpddr5"].insts:
        if inst.ref != "U1":
            continue
        for ball, pin in sorted(inst.sym.unit_pins(inst.unit).items(), key=lambda kv: ball_key(kv[0])):
            if netof("U1", ball) != "GND" or pin["etype"] != "bidirectional":
                continue
            assert pin["name"].startswith("DMI"), "unexpected bidirectional ball on GND: %s" % ball
            x, y = inst.pin(ball)
            excl.append(["pin_to_pin|%d|%d|%s|%s|%s|%s|%s" % (
                round(x * 1e4), round(y * 1e4), uid("pin/%s/%s/%d/%s" % ("lpddr5", "U1", inst.unit, ball)),
                flag_pin, by_name["lpddr5"].path, by_name["lpddr5"].path, by_name["power"].path),
                "%s (U1.%s) is strapped to VSS on purpose: design/DESIGN.md section 5" % (pin["name"], ball)])
    assert len(excl) == 2, excl
    return {"erc_exclusions": excl, "meta": {"version": 0}, "rule_severities": dict(sorted(ERC_SEVERITIES.items()))}


def fp_lib_table():
    rows = ['  (lib (name "%s")(type "KiCad")(uri "${KIPRJMOD}/lib/%s.pretty")(options "")'
            '(descr "Project footprints: card edge, BGA-315, MLF-6"))' % (LIB, LIB)]
    for n in FP_STOCK:
        rows.append('  (lib (name "%s")(type "KiCad")(uri "${KICAD10_FOOTPRINT_DIR}/%s.pretty")(options "")'
                    '(descr "KiCad stock library"))' % (n, n))
    return "(fp_lib_table\n  (version 7)\n" + "\n".join(rows) + "\n)\n"


def kicad(*args, cwd):
    r = subprocess.run(["kicad-cli"] + list(args), cwd=cwd, capture_output=True, text=True)
    if r.returncode:
        sys.exit("kicad-cli %s failed:\n%s\n%s" % (" ".join(args), r.stdout, r.stderr))
    return r.stdout


def check_coverage(sheets):
    done = set()
    for sh in sheets:
        done |= sh.connected
        for inst in sh.insts:
            if inst.ref.startswith("#"):
                continue
            assert PARTS[inst.ref]["sheet"] == sh.name, "%s is on sheet %s" % (inst.ref, sh.name)
            assert PARTS[inst.ref]["symbol"] == inst.sym.lib_id, "%s drawn with %s" % (inst.ref, inst.sym.lib_id)
    want = {(p["ref"], pin) for p in NET["parts"] for pin in p["pins"]}
    missing = sorted(want - done)
    assert not missing, "pins without a connection or no-connect flag: %s" % missing[:20]
    placed = {}
    for sh in sheets:
        for inst in sh.insts:
            if not inst.ref.startswith("#"):
                placed.setdefault(inst.ref, set()).add(inst.unit)
    assert set(placed) == set(PARTS), set(PARTS) ^ set(placed)
    for ref, units in placed.items():
        n = BY_LIB_ID[PARTS[ref]["symbol"]].nunits
        assert units == set(range(1, n + 1)), "%s: units %s placed" % (ref, sorted(units))
    for p in NET["parts"]:
        lib, name = p["footprint"].split(":")
        d = ROOT / "lib" / (lib + ".pretty") if lib == LIB else Path("/usr/share/kicad/footprints") / (lib + ".pretty")
        assert (d / (name + ".kicad_mod")).exists(), "footprint %s does not exist" % p["footprint"]


def main():
    subs = [sheet_connector(), sheet_lpddr5(), sheet_command(), sheet_write(), sheet_read(), sheet_power()]
    root = sheet_root(subs)
    check_coverage(subs)

    build = Path(tempfile.mkdtemp(prefix="gen_schematic_"))
    try:
        (build / "lib").mkdir()
        os.symlink(ROOT / "lib" / (LIB + ".pretty"), build / "lib" / (LIB + ".pretty"))
        pro = ROOT / (PROJECT + ".kicad_pro")
        if pro.exists():
            shutil.copy(pro, build / (PROJECT + ".kicad_pro"))
        else:           # clean tree: the project file is written by scripts/pcb/s00_board.py, which runs after this script (build.sh);
            (build / (PROJECT + ".kicad_pro")).write_text('{"meta": {"filename": "%s.kicad_pro", "version": 3}}\n' % PROJECT)   # the name is all kicad-cli needs here
        (build / "sym-lib-table").write_text(sym_lib_table())
        (build / "fp-lib-table").write_text(fp_lib_table())
        libfile = build / "lib" / (LIB + ".kicad_sym")
        libfile.write_text(library_text())
        kicad("sym", "upgrade", "--force", str(libfile), cwd=build)
        root.write(build / root.filename, extra_tail=[["sheet_instances", ["path", Q("/"), ["page", Q("1")]]]])
        for sh in subs:
            sh.write(build / sh.filename)
        for sh in [root] + subs:
            kicad("sch", "upgrade", "--force", sh.filename, cwd=build)
            text = (build / sh.filename).read_text()
            assert "(generator_version \"10.0\")" in text, sh.filename
            assert '(project ""' not in text, "%s lost its project name" % sh.filename
            n_inst = len([i for i in sh.insts])
            assert text.count('(project "%s"' % PROJECT) == n_inst + (len(subs) if sh is root else 0), sh.filename
        (build / "erc_settings.json").write_text(json.dumps(
            {"about": "ERC settings of the schematic, written by scripts/gen_schematic.py: the value of \"erc\" "
                      "is the \"erc\" section of lpddr5-testbed.kicad_pro; scripts/check_schematic.py runs ERC "
                      "with exactly these settings", "erc": erc_settings(subs)}, indent=2) + "\n")
        outputs = [(libfile, ROOT / "lib" / (LIB + ".kicad_sym")),
                   (build / "sym-lib-table", ROOT / "sym-lib-table"),
                   (build / "erc_settings.json", ROOT / "design" / "erc_settings.json")]
        outputs += [(build / sh.filename, ROOT / sh.filename) for sh in [root] + subs]
        if not (ROOT / "fp-lib-table").exists():
            outputs.append((build / "fp-lib-table", ROOT / "fp-lib-table"))
        for src, dst in outputs:
            shutil.copyfile(src, dst)
            print("wrote %s" % dst.relative_to(ROOT))
    finally:
        shutil.rmtree(build, ignore_errors=True)
    n = sum(1 for sh in subs for i in sh.insts if not i.ref.startswith("#"))
    print("%d symbol units of %d parts on %d sheets" % (n, len(PARTS), len(subs) + 1))


if __name__ == "__main__":
    main()

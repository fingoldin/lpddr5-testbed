#!/usr/bin/env python3
"""Gateware hand-off of the LPDDR5 test card (ZCU104 PL socket J1).

Reads   design/netlist.json          (the design: fingers, parts, groups, analysis)
        design/gateware_deskew.json  (per-bit delays measured on the copper by gate 7 of scripts/pcb/check_pcb.py)
Writes  design/gateware/lpddr5_testbed_zcu104.xdc          pin constraints of every FPGA pin the card uses
        design/gateware/lpddr5_testbed_zcu104_rx_slvs.xdc  override for the fall-back receiver (read after the first)
        design/gateware/lpddr5_testbed_pins.vh             Verilog localparams: pair polarities, per-bit delays
        design/gateware/pinmap.csv, PINMAP.md              the pin map as a table
        design/gateware/BRINGUP.md                         what the gateware has to do, in order
        design/gateware/vivado_pincheck/                   a minimal top level + flow that proves the pin plan in Vivado

Nothing else is written, and nothing is read but the two JSON files (plus, for BRINGUP.md, the summaries that
vivado_pincheck/run_pincheck.sh leaves in vivado_pincheck/results/).  Re-run after every board build:
    python3 scripts/gen_gateware.py            # write
    python3 scripts/gen_gateware.py --check    # exit 1 if the files on disk differ from what would be written

The I/O standards are not in netlist.json; they are the ones design/DESIGN.md names (sections 1, 7 and 8 item 31)
and are kept in IOSTD below.  Where a finger's "function" string in the netlist names a standard, it is compared.
"""
import argparse
import csv
import io
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NETLIST = os.path.join(ROOT, "design", "netlist.json")
DESKEW = os.path.join(ROOT, "design", "gateware_deskew.json")
OUT = os.path.join(ROOT, "design", "gateware")
PART = "xczu7ev-ffvc1156-2-e"

# I/O standards and attributes: design/DESIGN.md section 1 ("FPGA: SSTL12 / DIFF_SSTL12, RDRV_40_40, FAST"),
# section 7 (read pins: IBUFDS_DPHY, MIPI_DPHY_DCI, DIFF_TERM FALSE), section 8 item 31 (RESET_n LVCMOS12 DRIVE 8).
IOSTD = {
    "cmd_se":   ("SSTL12",        {"OUTPUT_IMPEDANCE": "RDRV_40_40", "SLEW": "FAST"}),
    "cmd_diff": ("DIFF_SSTL12",   {"OUTPUT_IMPEDANCE": "RDRV_40_40", "SLEW": "FAST"}),
    "wr_se":    ("SSTL12",        {"OUTPUT_IMPEDANCE": "RDRV_40_40", "SLEW": "FAST"}),
    "wr_diff":  ("DIFF_SSTL12",   {"OUTPUT_IMPEDANCE": "RDRV_40_40", "SLEW": "FAST"}),
    "rd":       ("MIPI_DPHY_DCI", {"DIFF_TERM_ADV": "TERM_NONE"}),
    "reset":    ("LVCMOS12",      {"DRIVE": "8"}),
    "guard":    ("SSTL12",        {"OUTPUT_IMPEDANCE": "RDRV_40_40", "SLEW": "SLOW"}),
}
RX_FALLBACK = "SLVS_400_18"


class Bad(Exception):
    pass


def need(cond, msg):
    if not cond:
        raise Bad(msg)


def f3(v):
    return " / ".join("%.3f" % x for x in v)


# --------------------------------------------------------------------------------------------------------------
# model
# --------------------------------------------------------------------------------------------------------------
def pin_fields(name):
    """IO_L3N_T0L_N5_AD15N_65 -> pair 'L3', byte group 'T0', nibble 'L', N index 5, bitslice position, clock roles."""
    m = re.match(r"IO_(?:(L\d+)([PN])_)?T(\d)([LU])_N(\d+)_(.*?)_?(\d+)$", name)
    need(m, "cannot parse the FPGA pin name %r" % name)
    pair, _, bg, nib, nidx, rest, bank = m.groups()
    nidx = int(nidx)
    roles = [r for r in ("GC", "QBC", "DBC", "VRP") if r in rest.split("_")]
    return {"io_pair": pair or "-", "bg": "T" + bg, "nibble": nib, "nidx": nidx,
            "bitslice": nidx if nib == "L" else nidx - 6, "roles": roles}


def build(net, dsk):
    fingers = {f["finger"]: f for f in net["fingers"]}
    by_net = {}
    for f in net["fingers"]:
        if f["net"]:
            by_net.setdefault(f["net"], []).append(f)
    groups = {g["name"]: g for g in net["groups"]}
    parts = {p["ref"]: p for p in net["parts"]}
    u1 = {}
    for ball, n in parts["U1"]["pins"].items():
        if n:
            u1.setdefault(n, []).append(ball)
    two = {}
    for p in net["parts"]:
        pins = p["pins"]
        if len(pins) == 2 and p["ref"][0] in "RC" and not p["ref"].startswith("RN"):
            a, b = pins["1"], pins["2"]
            two.setdefault(a, []).append((p["ref"], p["value"], b))
            two.setdefault(b, []).append((p["ref"], p["value"], a))
    an = net["analysis"]

    def div(key_start):
        for k, v in an["dividers"].items():
            if k.startswith(key_start):
                return v
        raise Bad("analysis.dividers has no entry starting with %r" % key_start)

    def one_finger(netname):
        fs = [f for f in by_net.get(netname, []) if f["fpga_ball"]]
        need(len(fs) == 1, "net %s is on %d FPGA fingers, expected 1" % (netname, len(fs)))
        return fs[0]

    def divider_network(fnet, bnet):
        ser = [t for t in two.get(fnet, []) if t[2] == bnet]
        need(len(ser) == 1, "no single series part between %s and %s" % (fnet, bnet))
        sh = [t for t in two.get(bnet, []) if t[2] != fnet and t[0][0] == "R"]
        s = "finger - %s %s series - node %s - U1.%s" % (ser[0][0], ser[0][1], bnet, "/".join(u1.get(bnet, ["?"])))
        for r in sh:
            s += "; %s %s node to %s" % r
        return s, ser[0], sh

    rows = []

    def row(port, f, cls, direction, iostd_key, card_net, ball_net, network, level, deskew, note="", inverted=None):
        pf = pin_fields(f["fpga_pin_name"])
        std, attrs = IOSTD[iostd_key]
        rows.append({
            "port": port, "finger": f["finger"], "side": f["side"], "fpga_ball": f["fpga_ball"],
            "fpga_pin_name": f["fpga_pin_name"], "bank": f["bank"], "byte_group": f["bytegroup"],
            "nidx": pf["nidx"], "bitslice": pf["bitslice"], "io_pair": pf["io_pair"], "roles": pf["roles"],
            "pn": f["pn"] or "-", "partner_finger": f["partner_finger"], "class": cls, "direction": direction,
            "iostandard": std, "attrs": dict(attrs), "finger_net": f["net"], "card_net": card_net,
            "dram_net": ball_net, "dram_ball": "/".join(u1.get(ball_net, [])) if ball_net else "",
            "network": network, "level": level, "deskew_ps": deskew, "host_plus_pkg_ps": f["host_plus_pkg_delay_ps"],
            "ddr4_name": f["ddr4_name"], "note": note, "inverted": inverted})

    def pair_rows(prefix, fnet_p, fnet_n, bnet_p, bnet_n, cls, direction, key, network_of, level, note_of):
        """A differential pair.  The port <prefix>_p is the FPGA P pin whatever card leg it carries."""
        fp, fn = one_finger(fnet_p), one_finger(fnet_n)
        need(fp["partner_finger"] == fn["finger"] and fn["partner_finger"] == fp["finger"],
             "%s / %s are not the two pins of one FPGA pair" % (fnet_p, fnet_n))
        need({fp["pn"], fn["pn"]} == {"P", "N"}, "%s / %s: not a P and an N pin" % (fnet_p, fnet_n))
        inverted = fp["pn"] == "N"          # the true (_t) leg sits on the FPGA N pin
        for f, bnet, leg in ((fp, bnet_p, "t"), (fn, bnet_n, "c")):
            port = "%s_%s" % (prefix, "p" if f["pn"] == "P" else "n")
            row(port, f, cls, direction, key, bnet, bnet, network_of(f["net"], bnet), level,
                0.0 if direction == "in" else "", note_of(leg, inverted), inverted)
        return inverted

    # ---- command ----
    g = groups["CMD"]
    cmd = dict(zip(g["nets"], g["finger_side_nets"]))
    need(len(cmd) == 10, "CMD group: expected 10 nets")
    for bnet in sorted(n for n in cmd if n.startswith("CA")):
        f = one_finger(cmd[bnet])
        d = div("CA0")
        row("ca[%d]" % int(bnet[2:]), f, "command", "out", "cmd_se", bnet, bnet, divider_network(cmd[bnet], bnet)[0],
            "%s V high, 0 V low" % f3([d["v_min"], d["v_nom"], d["v_max"]]), "",
            "matched on the card (%s ps die pad to ball, +/-%s ps)" % (g["total_delay_target_ps"], g["tolerance_ps"]))
    f = one_finger(cmd["CS0"])
    d = div("CS0")
    row("cs0", f, "command", "out", "cmd_se", "CS0", "CS0", divider_network(cmd["CS0"], "CS0")[0],
        "%s V high, 0 V low" % f3([d["v_min"], d["v_nom"], d["v_max"]]), "", "matched with the CA bus")
    d = div("CK_P")
    ck_inv = pair_rows("ck", cmd["CK_P"], cmd["CK_N"], "CK_P", "CK_N", "command", "out", "cmd_diff",
                       lambda fn_, bn: divider_network(fn_, bn)[0],
                       "per leg %s V high, 0 V low" % f3([d["v_min"], d["v_nom"], d["v_max"]]),
                       lambda leg, inv: "CK_%s%s" % (leg, " (PAIR INVERTED on the host)" if inv else ""))

    # ---- write ----
    g = groups["WR"]
    wr = dict(zip(g["nets"], g["finger_side_nets"]))
    need(len(wr) == 8, "WR group: expected 8 nets")
    d = div("DQ0-7")
    for bnet in sorted(wr):
        f = one_finger(wr[bnet])
        j = dsk["write"]["per_bit"][bnet]
        need(j["finger"] == f["finger"] and j["fpga_ball"] == f["fpga_ball"],
             "gateware_deskew.json write %s: finger %s / %s differs from the netlist (%s / %s)"
             % (bnet, j["finger"], j["fpga_ball"], f["finger"], f["fpga_ball"]))
        row("wdq[%d]" % int(bnet[2:]), f, "write", "out (Hi-Z during reads)", "wr_se", bnet, bnet,
            divider_network(wr[bnet], bnet)[0], "%s V high, 0 V low" % f3([d["v_min"], d["v_nom"], d["v_max"]]),
            j["output_delay_ps"], "not matched on the card: total %s ps" % j["total_ps"])
    g = groups["WR_WCK"]
    wck = dict(zip(g["nets"], g["finger_side_nets"]))
    d = div("WCK_P")
    wck_inv = pair_rows("wck", wck["WCK_P"], wck["WCK_N"], "WCK_P", "WCK_N", "write", "out", "wr_diff",
                        lambda fn_, bn: divider_network(fn_, bn)[0],
                        "per leg %s V high, 0 V low" % f3([d["v_min"], d["v_nom"], d["v_max"]]),
                        lambda leg, inv: "WCK_%s%s" % (leg, " (PAIR INVERTED on the host: drive inverted)" if inv else ""))
    for r in rows:
        if r["port"].startswith("wck_"):
            need("DIFF_SSTL12" in fingers[r["finger"]]["function"], "WCK finger function no longer names DIFF_SSTL12")

    # ---- read ----
    rd = an["read"]
    ref = an["reference"]
    g = groups["RD_P"]
    need(len(g["nets"]) == 8, "RD_P group: expected 8 nets")
    for bnet in sorted(g["nets"]):
        i = int(bnet[2:])
        fs = [f for f in by_net[bnet] if f["fpga_ball"]]
        need(len(fs) == 1, "read net %s is on %d FPGA fingers" % (bnet, len(fs)))
        f = fs[0]
        need(f["pn"] == "P", "read %s is not on the P pin of its pair" % bnet)
        fn = fingers[f["partner_finger"]]
        need(fn["net"] == "VREF_N%d" % i, "the N pin of read %s carries %r, not VREF_N%d" % (bnet, fn["net"], i))
        j = dsk["read"]["per_bit"][bnet]
        need(j["p_finger"] == f["finger"] and j["p_fpga_ball"] == f["fpga_ball"] and j["n_finger"] == fn["finger"]
             and j["n_fpga_ball"] == fn["fpga_ball"], "gateware_deskew.json read %s differs from the netlist" % bnet)
        sh = [t for t in two.get(bnet, []) if t[2] == "GND"]
        ser = [t for t in two.get(bnet, []) if t[2] == wr[bnet]]
        nw = "U1.%s - node %s - finger (no series part)" % ("/".join(u1[bnet]), bnet)
        for t in sh:
            nw += "; %s %s node to GND = read load" % t[:2]
        for t in ser:
            nw += "; %s %s to the write finger %d" % (t[0], t[1], one_finger(wr[bnet])["finger"])
        row("rdq_p[%d]" % i, f, "read", "in", "rd", bnet, bnet, nw,
            "read high %s V into %s ohm (%s), low 0 V" % (f3(rd["read_high_v"]), rd["load_ohm"], rd["read_high_basis"]),
            j["input_delay_ps"], "not matched on the card: total %s ps, %+.2f ps to the strobe" % (j["total_ps"], j["to_strobe_ps"]))
        nn = two.get(fn["net"], [])
        need(len(nn) == 1 and nn[0][2] == "VREF_RD", "N finger net %s: expected one part to VREF_RD" % fn["net"])
        row("rdq_n[%d]" % i, fn, "read reference", "in (DC)", "rd", fn["net"], "",
            "finger - %s %s - VREF_RD (%s)" % (nn[0][0], nn[0][1], ref["network"]),
            "VREF_RD: %.3f V with the DAC Hi-Z (power-up), %.3f V at DAC code %d; range %s V"
            % (ref["dac_high_z_v"], rd["reference_v"], ref["dac_code_for_0p195_v"],
               " .. ".join("%.3f" % x for x in ref["range_internal_ref_x1p5_v"])),
            "", "static reference of rdq_p[%d]; never driven by the FPGA" % i)
    g = groups["RD_RDQS"]
    st = dsk["read"]["strobe"]
    rq = rd["rdqs_true_differential"]

    def rdqs_network(fnet, bnet):
        sh = [t for t in two.get(bnet, []) if t[2] == "GND"]
        return "U1.%s - finger (no series part)%s" % ("/".join(u1[bnet]), "".join("; %s %s to GND" % t[:2] for t in sh))
    rdqs_inv = pair_rows("rdqs", "RDQS_P", "RDQS_N", "RDQS_P", "RDQS_N", "read", "in", "rd", rdqs_network,
                         "true differential: VID +/-%.3f V, VICM %.3f V while the DRAM drives; both legs 0 V when idle"
                         % (rq["vid_v"], rq["vicm_v"]),
                         lambda leg, inv: "RDQS_%s%s" % (leg, " (PAIR INVERTED on the host: invert after the receiver)" if inv else ""))
    for k in ("RDQS_P", "RDQS_N"):
        f = one_finger(k)
        need(st[k]["finger"] == f["finger"] and st[k]["fpga_ball"] == f["fpga_ball"],
             "gateware_deskew.json strobe %s differs from the netlist" % k)

    # ---- reset ----
    f = one_finger("RESET_N_F")
    d = div("RESET_n")
    need("LVCMOS12" in f["function"], "RESET_n finger function no longer names LVCMOS12")
    need(dsk["reset_n"]["finger"] == f["finger"] and dsk["reset_n"]["fpga_ball"] == f["fpga_ball"],
         "gateware_deskew.json reset_n differs from the netlist")
    row("reset_n", f, "reset", "out (static)", "reset", "RESET_N", "RESET_N", divider_network("RESET_N_F", "RESET_N")[0],
        "%s V high, 0 V low" % f3([d["v_min"], d["v_nom"], d["v_max"]]), "", "the only output in its bank")

    used = [f for f in net["fingers"] if f["fpga_ball"] and f["net"]]
    need(len(used) == len(rows), "%d fingers with an FPGA ball carry a net, %d were classified" % (len(used), len(rows)))

    # ---- the open finger between the CK fingers (DESIGN.md section 1: "gateware drives it low") ----
    guard = None
    cmd_rows = [r for r in rows if r["class"] == "command"]
    lo, hi = min(r["finger"] for r in cmd_rows), max(r["finger"] for r in cmd_rows)
    cmd_bank = {r["bank"] for r in cmd_rows}
    cmd_bg = {r["byte_group"][:2] for r in cmd_rows}
    for f in net["fingers"]:
        if lo < f["finger"] < hi and f["fpga_ball"] and not f["net"] and f["bank"] in cmd_bank \
                and f["bytegroup"][:2] in cmd_bg:
            need(guard is None, "more than one open FPGA finger inside the command field")
            guard = f
    if guard:
        pf = pin_fields(guard["fpga_pin_name"])
        std, attrs = IOSTD["guard"]
        rows.append({
            "port": "cmd_open_low", "finger": guard["finger"], "side": guard["side"], "fpga_ball": guard["fpga_ball"],
            "fpga_pin_name": guard["fpga_pin_name"], "bank": guard["bank"], "byte_group": guard["bytegroup"],
            "nidx": pf["nidx"], "bitslice": pf["bitslice"], "io_pair": pf["io_pair"], "roles": pf["roles"],
            "pn": guard["pn"] or "-", "partner_finger": guard["partner_finger"], "class": "command (open finger)",
            "direction": "out (static low)", "iostandard": std, "attrs": dict(attrs), "finger_net": "", "card_net": "",
            "dram_net": "", "dram_ball": "", "network": "finger open on the card (no copper)", "level": "-",
            "deskew_ps": "", "host_plus_pkg_ps": guard["host_plus_pkg_delay_ps"], "ddr4_name": guard["ddr4_name"],
            "note": "open on the card, lies inside the command field: drive low (DESIGN.md section 1)", "inverted": None})

    open_fpga = [f for f in net["fingers"] if f["fpga_ball"] and not f["net"]]
    info = {"ck_inv": ck_inv, "wck_inv": wck_inv, "rdqs_inv": rdqs_inv, "open_fpga": open_fpga,
            "other": [f for f in net["fingers"] if not f["fpga_ball"] and f["net"] and f["net"] not in ("GND", "V1P2")],
            "n_v1p2": sum(1 for f in net["fingers"] if f["net"] == "V1P2"),
            "n_gnd": sum(1 for f in net["fingers"] if f["net"] == "GND")}
    return rows, info


def order_key(r):
    cls = ["command", "command (open finger)", "write", "read", "read reference", "reset"].index(r["class"])
    m = re.match(r"([a-z_0-9]+?)(?:\[(\d+)\])?$", r["port"])
    base = m.group(1)
    leg = 0
    if base.endswith("_p") or base.endswith("_n"):
        leg = 0 if base.endswith("_p") else 1
        base = base[:-2]
    grp = 1 if base in ("wck", "rdqs", "cs") else 0          # the data bits first, then the strobe of the group
    return (cls if cls != 4 else 3, grp, base, int(m.group(2) or 0), leg)


# --------------------------------------------------------------------------------------------------------------
# findings that follow from the pin names alone
# --------------------------------------------------------------------------------------------------------------
def pin_notes(rows, info):
    out = []
    by = {}
    for r in rows:
        by.setdefault(r["class"].split(" (")[0].replace(" reference", ""), []).append(r)
    for cls in ("command", "write", "read", "reset"):
        rs = by.get(cls, [])
        banks = sorted({r["bank"] for r in rs})
        nibs = sorted({r["byte_group"] for r in rs})
        out.append("%s: bank %s, %d pins in %d nibble(s): %s" % (cls, "/".join(map(str, banks)), len(rs), len(nibs), ", ".join(nibs)))
    for p in ("ck_p", "wck_p", "rdqs_p"):
        r = next(x for x in rows if x["port"] == p)
        n = next(x for x in rows if x["port"] == p[:-1] + "n")
        out.append("%s / %s on %s / %s (%s, bitslice %d of %s): %s%s" % (
            p, n["port"], r["fpga_ball"], n["fpga_ball"], r["fpga_pin_name"], r["bitslice"], r["byte_group"],
            ("clock-capable: " + " + ".join(r["roles"])) if r["roles"] else "not a clock-capable pin (no GC / QBC / DBC)",
            "; pair inverted on the host" if r["inverted"] else ""))
    for r in rows:
        if "VRP" in r["roles"]:
            out.append("%s uses the VRP pin %s of bank %d" % (r["port"], r["fpga_ball"], r["bank"]))
    for r in rows:
        if "SMBALERT" in r["fpga_pin_name"]:
            out.append("%s (%s, %s) has the multi-function name SMBALERT: UG1075 table 1-4 warns that such a pin can be driven before "
                       "configuration (it names Kintex / Virtex UltraScale+; whether that holds for this Zynq part is not stated). On this "
                       "card it could only move one divider node between 0 V and its high level while the DRAM is held in reset"
                       % (r["port"], r["fpga_ball"], r["fpga_pin_name"]))
    rd_banks = {r["bank"] for r in rows if r["class"].startswith("read")}
    outs = [r for r in rows if r["direction"].startswith("out") and r["bank"] in rd_banks]
    out.append("outputs in the read bank: %s" % (", ".join("%s (%s, %s)" % (r["port"], r["fpga_ball"], r["iostandard"]) for r in outs) or "none"))
    gc = [r for r in rows if r["class"] == "read" and "GC" in r["roles"]]
    out.append("read data on global-clock (GC) pins: %s" % (", ".join("%s (%s)" % (r["port"], r["fpga_ball"]) for r in gc) or "none"))
    return out


# --------------------------------------------------------------------------------------------------------------
# writers
# --------------------------------------------------------------------------------------------------------------
def head(net, cs="##"):
    m = net["meta"]
    return ("%s GENERATED by scripts/gen_gateware.py from design/netlist.json (revision %s, %s) and\n"
            "%s design/gateware_deskew.json.  Do not edit: change the spec and re-run the generator.\n"
            % (cs, m["revision"], m["date"], cs))


def ports(rows, pred):
    return " ".join(sorted({re.sub(r"\[\d+\]", "[*]", r["port"]) for r in rows if pred(r)}))


def gen_xdc(net, rows, info):
    o = io.StringIO()
    w = lambda s="": o.write(s + "\n")
    w(head(net))
    w("## %s" % net["meta"]["title"])
    w("## Host: %s.  Part %s." % (net["meta"]["host"], PART))
    w("##")
    w("## Port names: a differential port <x>_p is the FPGA P pin of its I/O pair, whatever leg of the card it carries.")
    w("## Pairs whose true leg (_t) sits on the FPGA N pin are marked INVERTED; the polarity is also in")
    w("## lpddr5_testbed_pins.vh (CK_INVERTED / WCK_INVERTED / RDQS_INVERTED).")
    w("## Comment fields: finger (card side) | card net -> DRAM ball | bank, nibble, N index (bitslice in the nibble) | pin name")
    w("##")
    w("## Banks 64 / 65 / 66 are HP banks at VCCO = 1.2 V on the ZCU104 (fixed by the board).  No INTERNAL_VREF and no")
    w("## DCI_CASCADE constraint is needed (no VREF-referenced input and no DCI output in this file).")

    def pin(r):
        tag = ""
        net_s = r["finger_net"] or "(open)"
        if r["dram_ball"]:
            net_s += " -> %s (U1.%s)" % (r["dram_net"], r["dram_ball"]) if r["dram_net"] != r["finger_net"] else " (U1.%s)" % r["dram_ball"]
        w("set_property PACKAGE_PIN %-4s [get_ports {%s}]%s ;# finger %d (%s) %s | %s | bank %d %s N%d (bitslice %d)%s | %s%s%s"
          % (r["fpga_ball"], r["port"], " " * max(0, 12 - len(r["port"])), r["finger"], r["side"], r["ddr4_name"], net_s,
             r["bank"], r["byte_group"], r["nidx"], r["bitslice"], (" " + "+".join(r["roles"])) if r["roles"] else "",
             r["fpga_pin_name"], (" | " + r["note"]) if r["note"] else "", tag))

    def std(pred, title=None):
        sel = [r for r in rows if pred(r)]
        if not sel:
            return
        ps = ports(rows, pred)
        w("set_property IOSTANDARD %s [get_ports {%s}]" % (sel[0]["iostandard"], ps))
        for k, v in sel[0]["attrs"].items():
            w("set_property %s %s [get_ports {%s}]" % (k, v, ps))

    def section(title, cls, lines):
        w()
        w("## " + "-" * 108)
        w("## " + title)
        for ln in lines:
            w("## " + ln)
        w("## " + "-" * 108)
        for r in sorted([r for r in rows if r["class"] == cls], key=order_key):
            pin(r)

    cmd = [r for r in rows if r["class"] == "command"]
    section("COMMAND bus: CA0-6, CS0, CK  (bank %d, byte group %s)" % (cmd[0]["bank"], cmd[0]["byte_group"][:2]), "command",
            ["FPGA outputs at 1.2 V into the card's dividers (CA / CK 91 + 68 ohm, CS 33 + 100 ohm).",
             "Delay-matched on the card, die pad to ball; CK is %s." % ("INVERTED" if info["ck_inv"] else "not inverted (CK_t on the P pin)"),
             "Source of the standards: design/DESIGN.md section 1 (SSTL12 / DIFF_SSTL12, RDRV_40_40, FAST)."])
    std(lambda r: r["class"] == "command" and r["iostandard"] == "SSTL12")
    std(lambda r: r["class"] == "command" and r["iostandard"] == "DIFF_SSTL12")
    g = [r for r in rows if r["class"] == "command (open finger)"]
    if g:
        w()
        w("## Finger %d lies between the command fingers and is open on the card: the gateware drives it low" % g[0]["finger"])
        w("## (design/DESIGN.md section 1).  It is bitslice %d of nibble %s." % (g[0]["bitslice"], g[0]["byte_group"]))
        pin(g[0])
        std(lambda r: r["class"] == "command (open finger)")

    wr = [r for r in rows if r["class"] == "write"]
    section("WRITE: DQ0-7 outputs, WCK  (bank %d)" % wr[0]["bank"], "write",
            ["FPGA outputs at 1.2 V into the dividers (DQ 240 + 150 ohm, WCK 91 + 68 ohm).  wdq[*] must be Hi-Z during reads",
             "(OBUFT): a driven-low write pin costs 36 mV of read level (design/DESIGN.md section 2).",
             "NOT delay-matched on the card: one ODELAYE3 per pin, values in lpddr5_testbed_pins.vh / PINMAP.md.",
             "WCK is %s." % ("INVERTED: wck_p (the FPGA P pin) carries WCK_c, wck_n carries WCK_t: drive the pair with inverted data"
                             if info["wck_inv"] else "not inverted")])
    std(lambda r: r["class"] == "write" and r["iostandard"] == "SSTL12")
    std(lambda r: r["class"] == "write" and r["iostandard"] == "DIFF_SSTL12")

    rdr = [r for r in rows if r["class"].startswith("read")]
    w()
    w("## " + "-" * 108)
    w("## READ: DQ0-7 pseudo-differential, RDQS true differential  (bank %d)" % rdr[0]["bank"])
    w("## rdq_p[i] = DQi straight from the DRAM ball (150 ohm to GND on the card), rdq_n[i] = the static reference VREF_RD")
    w("## through 39 ohm (DAC53401, see the I2C note at the end).  Input-only: never drive these pins.")
    w("## Receiver: IBUFDS_DPHY with IOSTANDARD = \"MIPI_DPHY_DCI\" ALSO AS AN ATTRIBUTE ON THE INSTANCE (without it Vivado")
    w("## 2023.1 fails write_bitstream with DRC AVAL-252; Vivado 2026.1 takes the standard from the port),")
    w("## DIFF_TERM = \"FALSE\", HSRX_DISABLE = 0, LPRX_DISABLE = 1, LP outputs open.")
    w("## DIFF_TERM_ADV TERM_NONE below is the UltraScale+ spelling of DIFF_TERM FALSE: no differential termination,")
    w("## because one leg of every data pair is a DC reference.")
    w("## NOT delay-matched on the card: one IDELAYE3 per pair, values in lpddr5_testbed_pins.vh / PINMAP.md.")
    w("## RDQS is %s." % ("INVERTED: rdqs_p (the FPGA P pin) carries RDQS_c, rdqs_n carries RDQS_t: invert after the receiver"
                         if info["rdqs_inv"] else "not inverted"))
    w("## Fall-back receiver: read lpddr5_testbed_zcu104_rx_slvs.xdc after this file (%s on plain IBUFDS)." % RX_FALLBACK)
    w("## " + "-" * 108)
    for r in sorted(rdr, key=order_key):
        pin(r)
    std(lambda r: r["class"].startswith("read"))

    section("RESET_n  (bank %d: the only output in the read bank)" % next(r["bank"] for r in rows if r["class"] == "reset"), "reset",
            ["Static LVCMOS12 output, 100 ohm series + 1 k to GND on the card: 0.96 / 1.03 / 1.12 V at the ball.",
             "The 1 k holds the DRAM in reset while the FPGA is unconfigured.  DRIVE 8: the default 12 mA is not available",
             "for LVCMOS12 in an HP bank (DRC BIVB-1)."])
    std(lambda r: r["class"] == "reset")

    w()
    w("## " + "-" * 108)
    w("## Other fingers the card uses; they are NOT FPGA socket pins.  Reference DAC: DAC53401, I2C address 0x48.")
    for f in info["other"]:
        w("##   finger %d (%s) %-6s -> card net %-5s : %s" % (f["finger"], f["side"], f["ddr4_name"], f["net"], f["function"]))
    w("## On the ZCU104 the SODIMM SCL / SDA go to port 7 of the I2C1 switch TCA9548A U34 (address 0x74); that bus is")
    w("## PS I2C1 (MIO16 SCL / MIO17 SDA) and is shared with the PL pins below through a level shifter (UG1267 figure 3-12,")
    w("## table 3-16; schematic XTP484 sheet 45, nets PL_I2C1_SCL_LS / PL_I2C1_SDA_LS).  Select port 7 on the switch first,")
    w("## then talk to 0x48.")
    w("## Uncomment to reach the DAC from the PL instead of the PS (bank 87, 3.3 V, open drain, pull-ups on the board):")
    w("#set_property PACKAGE_PIN N12 [get_ports pl_i2c1_scl] ;# IO_L1N_AD11N_87, PL_I2C1_SCL_LS")
    w("#set_property PACKAGE_PIN P12 [get_ports pl_i2c1_sda] ;# IO_L1P_AD11P_87, PL_I2C1_SDA_LS")
    w("#set_property IOSTANDARD LVCMOS33 [get_ports {pl_i2c1_scl pl_i2c1_sda}]")
    w()
    w("## %d further FPGA pins of the socket are left open on the card (list: PINMAP.md).  Leave them out of the design" % (len(info["open_fpga"]) - (1 if g else 0)))
    w("## or drive them low; none is tied to a rail on the card.")
    return o.getvalue()


def gen_xdc_slvs(net, rows):
    ps = ports(rows, lambda r: r["class"].startswith("read"))
    return (head(net) +
            "## FALL-BACK receiver for the read pins: %s on plain IBUFDS (no IBUFDS_DPHY), no differential termination.\n"
            "## Read this file AFTER lpddr5_testbed_zcu104.xdc; it only overrides the standard of the read ports.\n"
            "## With TERM_NONE the standard puts no 1.8 V requirement on the bank; with termination on, Vivado demands\n"
            "## VCCO = 1.8 V, which the ZCU104 does not have on these banks.\n"
            "## Window (design/netlist.json, analysis.read.slvs_400_18_window): VID 0.14 .. 0.45 V, VICM 0.07 .. 0.33 V.\n"
            "set_property IOSTANDARD %s [get_ports {%s}]\n"
            "set_property DIFF_TERM_ADV TERM_NONE [get_ports {%s}]\n" % (RX_FALLBACK, RX_FALLBACK, ps, ps))


def gen_vh(net, rows, info, dsk):
    wr = {int(re.search(r"\d+", r["port"]).group()): r for r in rows if r["port"].startswith("wdq[")}
    rd = {int(re.search(r"\d+", r["port"]).group()): r for r in rows if r["port"].startswith("rdq_p[")}

    def vec(d):
        return "{" + ", ".join("16'd%d" % int(round(float(d[i]["deskew_ps"]))) for i in sorted(d, reverse=True)) + "}"
    o = io.StringIO()
    o.write(head(net, "//"))
    o.write("// `include inside a module.  Delays in ps (rounded), for ODELAYE3 / IDELAYE3 with DELAY_FORMAT = \"TIME\".\n")
    o.write("// They are start values (the host figures in them are good to +/-5 ps): training refines them.\n")
    o.write("localparam integer CK_INVERTED   = %d;  // 1: the FPGA P pin of the pair carries the _c leg\n" % info["ck_inv"])
    o.write("localparam integer WCK_INVERTED  = %d;\n" % info["wck_inv"])
    o.write("localparam integer RDQS_INVERTED = %d;\n" % info["rdqs_inv"])
    o.write("// write: output delay of wdq[i] = latest total of the group minus its own total (index i at bits 16*i +: 16)\n")
    o.write("localparam [8*16-1:0] WDQ_ODELAY_PS = %s;\n" % vec(wr))
    o.write("// read: input delay of rdq_p[i] / rdq_n[i] (same rule); the strobe gets 0 through its own IDELAYE3\n")
    o.write("localparam [8*16-1:0] RDQ_IDELAY_PS = %s;\n" % vec(rd))
    o.write("localparam integer RDQS_IDELAY_PS = %d;\n" % int(round(dsk["read"]["strobe"]["input_delay_ps"])))
    o.write("// after the table every DQ reaches the capture flop this long after the strobe edge that left the DRAM with it\n")
    o.write("localparam integer RDQ_AFTER_RDQS_PS = %d;  // %.2f ps\n" % (int(round(dsk["read"]["strobe"]["dq_after_strobe_with_the_table_ps"])),
                                                                       dsk["read"]["strobe"]["dq_after_strobe_with_the_table_ps"]))
    o.write("// WCK arrives this long after the latest write DQ (negative: before), FPGA die pad to ball\n")
    o.write("localparam integer WCK_AFTER_LATEST_WDQ_PS = %d;  // %.1f ps\n" % (int(round(dsk["wck"]["later_than_the_latest_dq_ps"])),
                                                                            dsk["wck"]["later_than_the_latest_dq_ps"]))
    o.write("// reference DAC (DAC53401 at I2C 0x48 behind the ZCU104 I2C1 switch 0x74 port 7)\n")
    o.write("localparam [6:0] VREF_DAC_I2C_ADDR = 7'h48;\n")
    o.write("localparam integer VREF_DAC_CODE_DEFAULT = %d;  // %.3f V on VREF_RD\n"
            % (net["analysis"]["reference"]["dac_code_for_0p195_v"], net["analysis"]["read"]["reference_v"]))
    return o.getvalue()


CSV_COLS = ["port", "finger", "finger_side", "ddr4_finger_name", "fpga_ball", "fpga_pin_name", "bank", "byte_group", "n_index",
            "bitslice_in_nibble", "clock_roles", "pn_of_fpga_pair", "pair_inverted", "class", "direction", "iostandard",
            "attributes", "finger_net", "dram_net", "dram_ball", "on_card_network", "level", "deskew_ps",
            "host_plus_pkg_ps", "note"]


def csv_row(r):
    return [r["port"], r["finger"], r["side"], r["ddr4_name"], r["fpga_ball"], r["fpga_pin_name"], r["bank"], r["byte_group"],
            r["nidx"], r["bitslice"], "+".join(r["roles"]), r["pn"], "" if r["inverted"] is None else int(r["inverted"]),
            r["class"], r["direction"], r["iostandard"], " ".join("%s=%s" % kv for kv in r["attrs"].items()),
            r["finger_net"], r["dram_net"], r["dram_ball"], r["network"], r["level"], r["deskew_ps"],
            r["host_plus_pkg_ps"], r["note"]]


def gen_csv(rows):
    o = io.StringIO()
    wtr = csv.writer(o, lineterminator="\n")
    wtr.writerow(CSV_COLS)
    for r in sorted(rows, key=order_key):
        wtr.writerow(csv_row(r))
    return o.getvalue()


def gen_pinmap_md(net, rows, info, dsk):
    o = io.StringIO()
    w = lambda s="": o.write(s + "\n")
    w("# Pin map of the LPDDR5 test card on the ZCU104 (socket J1)")
    w()
    w(head(net, ">").replace("\n>", "\n>").rstrip())
    w()
    w("Constraints: `lpddr5_testbed_zcu104.xdc`. The same table as CSV: `pinmap.csv` (with the full FPGA pin names). Part `%s`." % PART)
    w()
    w("- **P / N** is the polarity of the FPGA pin inside its I/O pair. A port `<x>_p` is always the FPGA P pin.")
    w("- **Bitslice** is the position inside the nibble (lower nibble N0-N5 = 0-5, upper nibble N6-N12 = 0-6); position 0 is")
    w("  the strobe / clock position of a native-mode nibble.")
    w("- **Level** is min / nominal / max over FPGA driver 26 / 36 / 44 ohm, VCCO 1.14 / 1.20 / 1.26 V and 1 % resistors")
    w("  (`analysis.dividers`), at the DRAM ball for outputs and at the FPGA pin for inputs.")
    w("- **Deskew** is the static delay the FPGA adds on that pin (`design/gateware_deskew.json`): ODELAYE3 for outputs,")
    w("  IDELAYE3 for inputs, `DELAY_FORMAT = \"TIME\"`, ps. Empty = none (matched on the card or static).")
    w()
    for title, pred in (("Command (matched on the card)", lambda r: r["class"].startswith("command")),
                        ("Write", lambda r: r["class"] == "write"),
                        ("Read", lambda r: r["class"].startswith("read")),
                        ("Reset", lambda r: r["class"] == "reset")):
        w("## " + title)
        w()
        w("| Port | Finger | FPGA ball | Bank | Nibble | Bitslice | P / N | Net at the finger | DRAM ball | Direction | I/O standard | On-card network | Level | Deskew ps |")
        w("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for r in sorted([r for r in rows if pred(r)], key=order_key):
            pn = r["pn"] + (" (" + "+".join(r["roles"]) + ")" if r["roles"] else "")
            if r["inverted"] is not None:
                pn += ", **inverted pair**" if r["inverted"] else ""
            w("| `%s` | %d %s | %s | %d | %s | %d | %s | %s | %s | %s | %s%s | %s | %s | %s |" % (
                r["port"], r["finger"], r["side"], r["fpga_ball"], r["bank"], r["byte_group"], r["bitslice"], pn,
                r["finger_net"] or "(open)", r["dram_ball"] or "-", r["direction"], r["iostandard"],
                "".join(", %s %s" % kv for kv in r["attrs"].items()), r["network"], r["level"],
                ("%.1f" % r["deskew_ps"]) if r["deskew_ps"] != "" else ""))
        w()
    w("## Pair polarities")
    w()
    w("| Pair | FPGA P pin carries | FPGA N pin carries | Gateware |")
    w("|---|---|---|---|")
    for p, name, inv, act in (("ck", "CK", info["ck_inv"], "drive the pair with inverted data"),
                              ("wck", "WCK", info["wck_inv"], "drive the pair with inverted data (or shift the WCK generator by 180 degrees)"),
                              ("rdqs", "RDQS", info["rdqs_inv"], "invert the strobe after the differential receiver (or swap the capture edges)")):
        rp = next(r for r in rows if r["port"] == p + "_p")
        rn = next(r for r in rows if r["port"] == p + "_n")
        w("| %s | %s (finger %d, %s) | %s (finger %d, %s) | %s |" % (
            name, rp["note"].split(" (")[0], rp["finger"], rp["fpga_ball"], rn["note"].split(" (")[0], rn["finger"], rn["fpga_ball"],
            ("**inverted**: " + act) if inv else "not inverted"))
    w()
    w("## Deskew table (start values; training refines them)")
    w()
    wj, rj = dsk["write"], dsk["read"]
    w("| Bit | Write total ps (die pad to ball) | Output delay ps | Read total ps (ball to die pad) | To the strobe ps | Input delay ps |")
    w("|---|---|---|---|---|---|")
    for b in sorted(wj["per_bit"]):
        a, c = wj["per_bit"][b], rj["per_bit"][b]
        w("| %s | %.2f | %.1f | %.2f | %+.2f | %.1f |" % (b, a["total_ps"], a["output_delay_ps"], c["total_ps"], c["to_strobe_ps"], c["input_delay_ps"]))
    s = rj["strobe"]
    w("| RDQS | | | %.2f / %.2f | 0 | %.1f |" % (s["RDQS_P"]["total_ps"], s["RDQS_N"]["total_ps"], s["input_delay_ps"]))
    k = dsk["wck"]["per_leg"]
    w("| WCK | %.2f / %.2f | 0 | | | |" % (k["WCK_P"]["total_ps"], k["WCK_N"]["total_ps"]))
    w()
    w("- Write: spread +/-%.1f ps, latest total %.2f ps; WCK arrives %.1f ps after the latest DQ before the table is applied"
      % (wj["spread_half_ps"], wj["latest_total_ps"], dsk["wck"]["later_than_the_latest_dq_ps"]))
    w("  (negative = before) and is not matched to DQ (tWCK2DQI is trained).")
    w("- Read: spread +/-%.1f ps about %.2f ps; with the table every DQ reaches the capture flop %.2f ps after the strobe edge that"
      % (rj["spread_half_ps"], rj["centre_ps"], s["dq_after_strobe_with_the_table_ps"]))
    w("  left the DRAM with it. Pass the strobe through an IDELAYE3 at value 0 too, so that the insertion delay of the primitive cancels.")
    w("- RDQS: %s. It arrives %+.2f ps from the centre of the read group (%+.2f ps from the latest bit, %+.2f ps from the"
      % (s["route"].split(". ")[0], s["to_centre_of_the_dq_group_ps"], s["to_latest_dq_ps"], s["to_earliest_dq_ps"]))
    w("  earliest); strobe minus each bit before the table: %s ps." % ", ".join("%s %+.1f" % kv for kv in s["minus_each_dq_before_the_table_ps"].items()))
    w("- %s: range %s ps, %d taps of %s ps." % (rj["idelay"]["primitive"], " .. ".join(map(str, rj["idelay"]["range_ps"])), rj["idelay"]["taps"],
                                             " .. ".join(map(str, rj["idelay"]["tap_ps"]))))
    w("- The command bus needs no table: its ten nets are matched on the card; what the +/-5 ps knowledge of the host leaves is")
    w("  removed per bit with the output delay of the command pins.")
    w()
    w("## What follows from the pin names")
    w()
    for n in pin_notes(rows, info):
        w("- " + n)
    w()
    w("## Other fingers the card uses (not FPGA socket pins)")
    w()
    w("| Finger | DDR4 name | Card net | Function |")
    w("|---|---|---|---|")
    for f in info["other"]:
        w("| %d %s | %s | %s | %s |" % (f["finger"], f["side"], f["ddr4_name"], f["net"], f["function"]))
    w("| %d fingers | VDD | V1P2 | host 1.2 V rail |" % info["n_v1p2"])
    w("| %d fingers | VSS | GND | |" % info["n_gnd"])
    w()
    w("## FPGA pins of the socket that the card leaves open")
    w()
    w("%d pins; none is tied to a rail on the card. Leave them out of the design or drive them low." % len(info["open_fpga"]))
    w()
    w("| Finger | DDR4 name | FPGA ball | Bank | Nibble |")
    w("|---|---|---|---|---|")
    for f in info["open_fpga"]:
        w("| %d %s | %s | %s | %s | %s |" % (f["finger"], f["side"], f["ddr4_name"], f["fpga_ball"], f["bank"], f["bytegroup"]))
    return o.getvalue()


# --------------------------------------------------------------------------------------------------------------
# BRINGUP.md
# --------------------------------------------------------------------------------------------------------------
CASES = {
    "M1_dphy_native16": "first-choice receiver (IBUFDS_DPHY, MIPI_DPHY_DCI), native TX_BITSLICE command bus at 1.6 Gb/s",
    "M2_slvs_native16": "fall-back receiver (IBUFDS, SLVS_400_18), the rest as M1",
    "M3_dphy_native16_guard_obuf": "as M1, the open finger 75 as a plain OBUF inside the native nibble",
    "M4_dphy_comp_1200": "as M1, command bus on OSERDESE3 with a 600 MHz bit clock (1.2 Gb/s)",
    "M5_dphy_comp_1600": "as M1, command bus on OSERDESE3 with an 800 MHz bit clock (1.6 Gb/s)",
    "N1_rdqs_clock_behind_idelay": "negative test: RDQS -> IDELAYE3 -> BUFGCE as the capture clock",
    "N2_rdqs_clock_from_receiver": "negative test: RDQS receiver -> BUFGCE as the capture clock",
    "N2b_rdqs_clock_cdr_false": "as N2 with CLOCK_DEDICATED_ROUTE FALSE on the strobe net",
    "N3_dphy_attr_default": "IBUFDS_DPHY without IOSTANDARD = MIPI_DPHY_DCI on the instance (the standard only in the XDC)",
    "N3_dphy_attr_default_vivado2023": "N3 in Vivado 2023.1",
    "N4_dphy_primitive_with_slvs": "negative test: IBUFDS_DPHY on ports constrained to SLVS_400_18",
    "M1_dphy_native16_vivado2023": "M1 in Vivado 2023.1 (IDELAYCTRL SIM_DEVICE = ULTRASCALE)",
    "M2_slvs_native16_vivado2023": "M2 in Vivado 2023.1 (IDELAYCTRL SIM_DEVICE = ULTRASCALE)",
}


def read_results():
    """Summaries left by vivado_pincheck/run_pincheck.sh: tool version, stage results, error / critical-warning counts."""
    d = os.path.join(OUT, "vivado_pincheck", "results")
    res = []
    if not os.path.isdir(d):
        return res
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".summary.txt"):
            continue
        t = open(os.path.join(d, fn)).read()
        name = fn[:-len(".summary.txt")]
        tool = re.search(r"^@@VERSION (.*)$", t, re.M)
        stages = re.findall(r"^@@RESULT (\S+) (PASS|FAIL|SKIPPED)", t, re.M)
        cnt = re.search(r"^@@COUNT errors=(\d+) critical_warnings=(\d+)", t, re.M)
        ts = re.search(r"^@@TIMING_SUMMARY[^:]*:\s*(.*)$", t, re.M)
        nums = ts.group(1).split() if ts else []
        main = [(a, b) for a, b in stages if a in ("synth", "opt", "place", "route", "bitstream")]
        bad = [a for a, b in main if b == "FAIL"]
        routed = ("route", "PASS") in main
        ids = sorted(set(re.findall(r"^\s*\d+ ERROR: \[(?:DRC )?([A-Za-z_]+ ?[\d-]+)\]", t, re.M)) - {"Common 17-39", "Common 17-69", "Vivado 12-1345", "Vivado_Tcl 4-23", "Place 30-99"})
        res.append({"name": name, "what": CASES.get(name, ""), "tool": tool.group(1) if tool else "?",
                    "result": ("passes through write_bitstream" if not bad and len(main) == 5 else "FAILS at %s (%s)" % (bad[0] if bad else "?", ", ".join(ids) or "see summary")),
                    "errors": cnt.group(1) if cnt else "?", "cw": cnt.group(2) if cnt else "?",
                    "wns": nums[0] if routed and len(nums) > 8 else "-", "wpws": nums[8] if routed and len(nums) > 8 else "-"})
    return res


def gen_bringup(net, rows, info, dsk):
    an = net["analysis"]
    dv = an["dividers"]
    rd = an["read"]
    ref = an["reference"]
    rails = an["rails"]

    def lv(key):
        for k, v in dv.items():
            if k.startswith(key):
                return f3([v["v_min"], v["v_nom"], v["v_max"]])
        return "?"
    R = {r["port"]: r for r in rows}
    rst = R["reset_n"]
    guard = next((r for r in rows if r["class"] == "command (open finger)"), None)
    code0 = ref["dac_code_for_0p195_v"]
    m = re.match(r"V = ([\d.]+) x Vdac \+ ([\d.]+) V", ref["dac_active"])
    need(m, "analysis.reference.dac_active is no longer 'V = a x Vdac + b V'")
    ka, kb = float(m.group(1)), float(m.group(2))
    fs = 1.21 * 1.5          # TI DAC53401 data sheet: internal reference 1.21 V, DAC_SPAN 00 = x1.5

    def vref(code):
        return ka * code / 1024.0 * fs + kb
    wr_nibs = sorted({r["byte_group"] for r in rows if r["port"].startswith("wdq")})
    rd_nibs = sorted({r["byte_group"] for r in rows if r["port"].startswith("rdq_p")})
    o = io.StringIO()
    w = lambda s="": o.write(s + "\n")
    w("# Bring-up: what the gateware has to do, in order")
    w()
    w(head(net, ">").rstrip())
    w()
    w("Card: %s. DRAM: %s. Scope: %s." % (net["meta"]["title"], net["meta"]["target_dram"], net["meta"]["scope"]))
    w("Pins: `PINMAP.md`, `lpddr5_testbed_zcu104.xdc`. Delays and polarities as Verilog parameters: `lpddr5_testbed_pins.vh`.")
    w()
    w("Every statement carries its source: **D n** = `design/DESIGN.md` section n; **N** = `design/netlist.json` (key given);")
    w("**K** = `design/gateware_deskew.json`; **J** = `references/jedec/JESD209-5C_LPDDR5.txt` (table or clause);")
    w("**TI** = `references/parts/TI_DAC53401.txt`; **UG1267** / **XTP484** = `references/zcu104/`; **V** = the Vivado check in")
    w("`vivado_pincheck/`. **[no source]** marks what none of them states; such lines are suggestions, not design facts.")
    w()
    w("## 0. What the card does without any gateware")
    w()
    w("- Rails are made on the card from the host 1.2 V and 3.3 V: VDD1 %s V, VDD2H = VDD2L %s V, VDDQ %s V. Nothing starts until"
      % (f3(rails["VDD1"]["v"]), f3(rails["VDD2H"]["v"]), f3(rails["VDDQ"]["v"])))
    w("  both host rails are up; all rails are in range about 5 ms after the first one moves; any host rail order works (D 4).")
    w("- No power-good or enable signal of the card reaches the FPGA, and no FPGA pin can switch a rail (D 4: \"Nothing else is on")
    w("  the enable net\"; N `fingers`: no finger carries PG or an enable).")
    w("- While the FPGA is unconfigured every divider node is held at 0 V by its shunt (CA / CK / WCK 68 ohm, CS 100 ohm, DQ 150 ohm)")
    w("  and RESET_n is held low by 1 k: with a 100 uA configuration pull-up the ball sits at 0.11 V (limit 0.21 V) (D 1).")
    w("- VREF_RD is %.3f V with the DAC output Hi-Z, its factory default: reads work before any I2C traffic (D 3; N `analysis.reference`)."
      % ref["dac_high_z_v"])
    w()
    w("## 1. Pin states from configuration until the rails are known to be up")
    w()
    w("1. Drive every divider-driven pin low: `ca[6:0]`, `cs0`, both CK legs, both WCK legs, `wdq[7:0]`, and keep `reset_n` low")
    w("   (D 7: \"All divider-driven pins low until the rails are up\"). Reason: a high FPGA output puts %s V on a CA ball and" % lv("CA0"))
    w("   %s V on CS; JEDEC wants DQ, WCK, CK and CA between VSS and VDDQ during the ramp and CS at or below ViLPD (J 4.1.1 step 2)." % lv("CS0"))
    if guard:
        w("2. Drive `cmd_open_low` (finger %d, %s) low for good: the finger is open on the card and lies between the CK fingers (D 1)."
          % (guard["finger"], guard["fpga_ball"]))
    w("3. The read pins are inputs only; never enable an output buffer on `rdq_p / rdq_n / rdqs_p / rdqs_n` (D 7).")
    w("4. How long to wait: the card gives no signal. The rails follow the host rails within about 5 ms (D 4) and the host rails are")
    w("   up before the PL is configured. A fixed wait of at least tINIT0 (20 ms, J table 20) after configuration covers it **[no source")
    w("   for the figure: suggestion]**. Check the rails on TP1 (VDD1), TP2 (VDD2H), TP3 (VDDQ) the first time (section 7).")
    w()
    w("## 2. Reset and initialisation (J 4.1.1, table 20)")
    w()
    w("| Step | What | Limit | Source |")
    w("|---|---|---|---|")
    w("| 1 | `reset_n` low after the rails are in range | tINIT1 >= 200 us | J table 20 |")
    w("| 2 | `cs0` low before `reset_n` goes high | tINIT2 >= 10 ns | J table 20 |")
    w("| 3 | CK toggling, or static at a valid complementary level, about when `reset_n` is released (until here both CK legs were low, section 1) | - | J 4.1.1 step 4 |")
    w("| 4 | `reset_n` high: FPGA %s on %s gives %s V at the ball (VIH 0.85 V, maximum VDD2H + 0.2 V = 1.23 V) | - | D 1, N `analysis.dividers.RESET_n` |"
      % (rst["iostandard"], rst["fpga_ball"], lv("RESET_n")))
    w("| 5 | `cs0` low after `reset_n` high | tINIT3 >= 2 ms | J table 20 |")
    w("| 6 | CK stable before the first `cs0` high | tINIT4 >= 5 tCK | J table 20 |")
    w("| 7 | idle before the first MRW / MRR | tINIT5 >= 2 us | J table 20 |")
    w("| 8 | boot clock: tCKb >= 18 ns is what the DRAM guarantees (55 MHz); faster is system dependent | - | J table 20 notes |")
    w("| 9 | the initial ZQ calibration runs by itself after `reset_n` goes high (ZQ = 240 ohm to VDDQ, R401); issue ZQCal Latch, then wait tZQLAT = max(30 ns, 4 nCK) | - | J 4.1.1 step 7, D 5 |")
    w()
    w("- Levels during boot: with the default VREF(CA) of 50 %% of VDDQ a CA high needs >= 0.351 V; the divider gives %s V (D 1)." % lv("CA0"))
    w("- CS is sampled against VDD2H / 3 = 0.35 V; the CS divider gives %s V (D 1)." % lv("CS0"))
    w("- The 1 k on RESET_n and the 100 ohm series part are on the card; the FPGA pin needs no pull.")
    w()
    w("## 3. Mode-register writes")
    w()
    w("Write them at the boot clock, in this order, to frequency set point 0 (the default set point; D does not name another).")
    w("\"Byte\" is the whole operand with every other field at its JEDEC default; where a default is not stated in J the field is")
    w("given alone and the register is to be read back first (MRR) and modified.")
    w()
    w("| # | Register | Field = value | Byte | Why | Source |")
    w("|---|---|---|---|---|---|")
    w("| 1 | MR13 | OP[7] = 1 (single 1.05 V VDD2 rail); OP[5] = 1 (data mask off); OP[6] = 0 (CBT mode 1); OP[4] = 0 (DMI output behaviour mode 1: with mode 2 and data mask on the DRAM would drive DMI during READ DQ CALIBRATION into the strap, J table 234) | 0xA0 | The VDD2L balls are tied to VDD2H: write it first, right after MRW becomes possible and before any CBT. DMI0 is strapped to VSS, so the DRAM must not expect a mask and MASKED WRITE is never issued. CBT mode 2 needs DMI0 as an input. | D 4, D 5, D 7; J table 18 note 1, J figure 13 note 3, J table 89 |")
    w("| 2 | MR17 | OP[2:0] = 110b (SoC ODT code RZQ/6); OP[3] = OP[4] = OP[5] = 1 (CK, CS, CA termination disabled) | 0x3E | With DQ termination off this field sets the pull-up strength of the DRAM driver: it defines the read high level (%s V into the 150 ohm load). CA / CK / CS are unterminated on purpose: the dividers source-match the lines. | D 3 (\"Requires MR17 OP[2:0] = 110b\"), D 7; J table 99 note 6 |" % f3(rd["read_high_v"]))
    w("| 3 | MR3 | OP[2:0] = 110b (PDDS 40 ohm, the default); OP[7:6] = 00 (write and read DBI off) | read-modify-write | Pull-down strength for the read low; DBI stays off because DMI is strapped. If the receiver minds the 1-2 ns of ringing after an edge, use 48 or 60 ohm (101b / 100b) and re-centre VREF_RD (60 ohm nominal: 0.357 V high). | D 3, D 7; J table 68 |")
    w("| 4 | MR11 | OP[2:0] = 000 (DQ ODT off), OP[3] = 0, OP[6:4] = 000 (CA ODT off) | 0x00 (default) | All DRAM ODT off: the 150 ohm shunt is the only DC load of a DQ; a DQ ODT would pull the write level down. | D 7 (\"all DRAM ODT off\"); J table 84 |")
    w("| 5 | MR18 | OP[2:0] = 000 (WCK ODT off, default); OP[7] = 1 (CKR 2:1, default) | default | WCK reaches %s V per leg only without termination; with 40 ohm it is 0.34 V p-p, the JEDEC minimum. | D 2; J table 101 |" % lv("WCK_P"))
    w("| 6 | MR20 | OP[1:0] = 10b (RDQS_t and RDQS_c both enabled); OP[3:2] = 00 (differential WCK) | 0x02 | The card receives RDQS as a true differential pair, 150 ohm to GND on each leg. With the default 01b RDQS_c stays Hi-Z and sits at 0 V. | D 3 (\"Needs MR20 OP[1:0] = 10b\"), D 7; J table 105 |")
    w("| 7 | MR12 | OP[6:0] = 1000000b (VREF(CA) 42.0 %% of VDDQ = 0.21 V); OP[7] = 0 | 0x40, then train | Half of the CA high level (%s V). The boot default (50 %% = 0.25 V) is only good at low rate. | D 1, D 7; J table 87 |" % lv("CA0"))
    w("| 8 | MR14 | OP[6:0] = 1000000b (VREF(DQ[7:0]) 42.0 %% = 0.21 V); OP[7] = 0 | 0x40, then train | Half of the write level (%s V); must stay at or below 0.225 V. | D 2, D 7; J table 91 / 92 |" % lv("DQ0-7"))
    w()
    w("- **Never use**, because DMI is strapped to VSS: READ FIFO, read DBI, link ECC, read data copy, MASKED WRITE, CBT mode 2.")
    w("  READ DQ CALIBRATION is fine (D 5, D 7). JEDEC's DQ training flow names RD FIFO / WT FIFO (J 4.1.1 step 9): use READ DQ CALIBRATION")
    w("  and plain WRITE / READ instead.")
    w("- **Command-bus training is not needed and must not be relied on**: it forces a 40 ohm WCK ODT, which leaves WCK at the JEDEC")
    w("  minimum in the slowest driver corner (D 2). The command bus is matched on the card; VREF(CA) is set by MR12 directly.")
    w("- **CA ODT stays off.** If the 800 MHz eye needs it, it is a gateware change (MR11 OP[6:4] with MR17 OP[5] / OP[3] = 0); it lowers")
    w("  the CA level (240 ohm: 0.35 V) (D 8 item 4).")
    w("- **Clock plan (D 7).** WCK is tied to CK at 2:1 (MR18 OP[7] = 1, the reset default) or 4:1 (OP[7] = 0). The data target of this card is")
    w("  100 Mb/s (set by the 2.6 ns write node, D 2): WCK = 50 MHz, so **CK = 25 MHz at 2:1 or 12.5 MHz at 4:1 for every READ and WRITE**")
    w("  (tCK(avg) may be as long as 200 ns, J table 15.1). **CK = 800 MHz (1.6 Gb/s on CA) is for command-only traffic**, the purpose of the card.")
    w("  The controller therefore changes the clock frequency between the command stream and any read or write: stop or slow CK with the")
    w("  clock-change procedure (J 7.6.7) or switch frequency set points (FSP, J 7.6.3), each set point with its own MR1 / MR2 latencies,")
    w("  MR12 / MR14 VREF and MR11 / MR17 / MR18 termination codes. JEDEC defaults after reset are WL = 4, RL = 6, nWR = 5, CKR 2:1, set point 0")
    w("  (J table 18); the latencies per set point are for the controller to choose **[no value prescribed by D]**.")
    w("- Option on the FPGA side: `OUTPUT_IMPEDANCE RDRV_48_48` on the command pins matches the 39 ohm host line better than RDRV_40_40")
    w("  and still gives 0.386 / 0.420 / 0.453 V (D 7).")
    w()
    w("## 4. The reference DAC (VREF_RD, the N leg of every read pair)")
    w()
    w("- Network: %s (N `analysis.reference.network`)." % ref["network"])
    w("- DAC53401 U5, I2C address %s; broadcast address 0x47 (D 3; TI table 11 / 12)." % ref["i2c_address"].split(";")[0])
    w("- Bus: card fingers 253 (SCL) / 254 (SDA) -> ZCU104 I2C1 switch TCA9548A U34, address 0x74, port 7 (UG1267 table 3-16). The bus is")
    w("  PS I2C1 (MIO16 / MIO17) and is also wired to PL bank 87 (N12 = PL_I2C1_SCL_LS, P12 = PL_I2C1_SDA_LS) through a level shifter")
    w("  (UG1267 figure 3-12; XTP484). Select port 7 on the switch, then address 0x48. The control byte of the switch is not in the")
    w("  repository **[no source]** (TCA9548A: one bit per port, port 7 = 0x80).")
    w("- **Default, no I2C traffic:** DAC output Hi-Z -> VREF_RD = %.4f V from VDDQ through 300 / 180 ohm, source %.1f ohm (N)."
      % (ref["dac_high_z_v"], ref["dac_high_z_source_ohm"]))
    w("- **Active:** %s, source %.1f ohm. With the internal reference at gain 1.5 (1.21 V x 1.5 = 1.815 V full scale, 10 bits):"
      % (ref["dac_active"], ref["dac_active_source_ohm"]))
    w("  VREF_RD = %.4f V + code x %.3f mV, range %.3f .. %.3f V (N; TI 8.3.1.1.2)." % (kb, ref["lsb_mV"], ref["range_internal_ref_x1p5_v"][0], ref["range_internal_ref_x1p5_v"][1]))
    w("- **Target: code %d = %.3f V** (N `analysis.reference.dac_code_for_0p195_v`, `analysis.read.reference_v`)." % (code0, rd["reference_v"]))
    w("- Registers (16-bit, TI 8.6): `DAC_DATA` (0x21) = code << 2 (bits 11:2, left aligned): code %d -> 0x%04X. `GENERAL_CONFIG` (0xD1):"
      % (code0, code0 << 2))
    w("  DAC_PDN[4:3] = 00 (power up; reset value 10 = Hi-Z), REF_EN[2] = 1 (internal reference), DAC_SPAN[1:0] = 00 (gain 1.5),")
    w("  SLEW_RATE[8:5] = 1111 (no slew, the reset value) -> 0x01E4 (reset value 0x01F0). Write DAC_DATA first, then GENERAL_CONFIG, so that")
    w("  the output starts at the target **[order: suggestion, no source]**. Nothing is stored in the DAC's NVM by these writes.")
    w("- **Sweep:** step the code and count bit errors at 100 Mb/s (D 8 item 6).")
    w()
    w("  | Code | VREF_RD |")
    w("  |---|---|")
    for c in (0, 60, 90, code0, 150, 180, 210, 240, 300, 400, 1023):
        w("  | %d%s | %.3f V |" % (c, " (target)" if c == code0 else "", vref(c)))
    w()
    w("- Limits of the sweep: the receiver needs VICM = (pin + VREF_RD) / 2 <= 0.33 V and >= 0.07 V. With the pin at 0 V the common")
    w("  mode is VREF_RD / 2: %.4f V at the target. **Keep VREF_RD at or below 0.20 V if the measured read high level is above" % (rd["static_zero"]["vicm_v"]))
    w("  0.42 V** (D 3, N `analysis.read.reference_rule`). The network cannot go below %.3f V and, misprogrammed to the VDD reference"
      % ref["range_internal_ref_x1p5_v"][0])
    w("  at full scale, stays at %.2f V, inside the FPGA's input range (D 3)." % ref["worst_case_if_misprogrammed_to_3p3_v"])
    w()
    w("## 5. Per-bit delays")
    w()
    w("Neither data group is matched on the card (accepted by the user; D 0, D 7, D 14). Load these as start values and let training")
    w("refine them: the host figures inside them are good to about +/-5 ps and a bit may sit 15 ps from its value on the real card (D 8")
    w("items 20, 23). Source: K (written by gate 7 of `scripts/pcb/check_pcb.py` at every build).")
    w()
    w("| Bit | Write port | FPGA ball | Nibble | ODELAYE3 ps | Read port | FPGA ball P / N | Nibble | IDELAYE3 ps |")
    w("|---|---|---|---|---|---|---|---|---|")
    for i in range(8):
        a, c, n_ = R["wdq[%d]" % i], R["rdq_p[%d]" % i], R["rdq_n[%d]" % i]
        w("| DQ%d | `%s` | %s | %s | %.1f | `%s` | %s / %s | %s | %.1f |" % (i, a["port"], a["fpga_ball"], a["byte_group"], a["deskew_ps"],
                                                                         c["port"], c["fpga_ball"], n_["fpga_ball"], c["byte_group"], c["deskew_ps"]))
    st = dsk["read"]["strobe"]
    w("| RDQS | | | | | `rdqs_p` / `rdqs_n` | %s / %s | %s | %.1f |" % (R["rdqs_p"]["fpga_ball"], R["rdqs_n"]["fpga_ball"], R["rdqs_p"]["byte_group"], st["input_delay_ps"]))
    w()
    w("- Primitives: ODELAYE3 / IDELAYE3, `DELAY_FORMAT = \"TIME\"`, `DELAY_VALUE` in ps, one per pin (the write pins lie in %d nibbles: %s; the"
      % (len(wr_nibs), ", ".join(wr_nibs)))
    w("  read pins in %d: %s): not one native byte interface (D 7). IDELAYE3: range %s ps (K)."
      % (len(rd_nibs), ", ".join(rd_nibs), " .. ".join(map(str, dsk["read"]["idelay"]["range_ps"]))))
    w("  TIME mode needs an `IDELAYCTRL` (reference clock 300 .. 800 MHz) and `EN_VTC` handling per UG571; one tap is 2.1 .. 12 ps, so table")
    w("  values below one tap (the smallest non-zero entries) are within the resolution of the primitive and are left to training.")
    w("- After the table every read DQ arrives %.1f ps after the strobe edge that left the DRAM with it; the capture delay of the"
      % st["dq_after_strobe_with_the_table_ps"])
    w("  strobe (a quarter of the bit time and tDQSQ) is added by the gateware as in any DDR read. The strobe is not on the centre of the read")
    w("  group and need not be: it stays on In3 down to its fingers, like the read DQ lines, and arrives %+.1f ps from the centre, inside the span"
      % st["to_centre_of_the_dq_group_ps"])
    w("  of the eight bits, so the table delays data only. If the strobe is sampled like data (section 6: the check does that at 100 Mb/s) give it")
    w("  its own IDELAYE3 at 0, so that it sees the same insertion delay; an IDELAYE3 output cannot drive a BUFG (V, case N1) (D 7).")
    w("- WCK is not matched to DQ (tWCK2DQI is 300 .. 900 ps inside the DRAM: write timing is trained). Before the table WCK arrives %.1f ps"
      % dsk["wck"]["later_than_the_latest_dq_ps"])
    w("  relative to the latest write DQ (negative = earlier) (D 2, K).")
    w("- Polarity: %s" % "; ".join("%s is %s" % (n, "INVERTED on the host pair" if v else "not inverted")
                                 for n, v in (("CK", info["ck_inv"]), ("WCK", info["wck_inv"]), ("RDQS", info["rdqs_inv"]))) + " (D 7, N `groups`).")
    w("- The RDQS receiver output is valid only inside a read burst (both legs sit at 0 V otherwise): gate it with the read-enable")
    w("  window (D 3, D 7).")
    w("- `wdq[*]` Hi-Z during reads (D 2, D 7). Write rate: 100 Mb/s is the design target; the write node has tau 2.6 ns and does not")
    w("  support 200 Mb/s (D 2). At 100 Mb/s sample reads in the second half of the unit interval (D 7).")
    w()
    w("## 6. The receiver")
    w()
    hs, sl = rd["mipi_dphy_dci_hs_window"], rd["slvs_400_18_window"]
    mg = rd["margins_at_jedec_bound_mV"]
    w("| | First choice | Fall-back |")
    w("|---|---|---|")
    w("| Primitive | `IBUFDS_DPHY`, HS path (`HSRX_O`), `HSRX_DISABLE = 0`, `LPRX_DISABLE = 1`, LP outputs open | plain `IBUFDS` |")
    w("| Standard | `MIPI_DPHY_DCI`, in the XDC **and** as the `IOSTANDARD` attribute of the instance | `SLVS_400_18` (`lpddr5_testbed_zcu104_rx_slvs.xdc`) |")
    w("| Termination | `DIFF_TERM = \"FALSE\"` (`DIFF_TERM_ADV TERM_NONE`) | `DIFF_TERM_ADV TERM_NONE` |")
    w("| Window | VID >= %.2f V, VICM %.2f .. %.2f V, pin %.2f .. %.2f V | VID %.2f .. %.2f V, VICM %.2f .. %.2f V |"
      % (hs["vid_min_v"], hs["vicm_v"][0], hs["vicm_v"][1], hs["pin_v"][0], hs["pin_v"][1], sl["vid_v"][0], sl["vid_v"][1], sl["vicm_v"][0], sl["vicm_v"][1]))
    w("| Static one (min / nom / max) | VID %s V, VICM %s V | same signal |" % (f3(rd["static_one"]["vid_v"]), f3(rd["static_one"]["vicm_v"])))
    w("| Static zero | VID %.3f V, VICM %.4f V | same signal |" % (rd["static_zero"]["vid_v"], rd["static_zero"]["vicm_v"]))
    w("| Margin at the JEDEC bound of the read level | VICM to 0.33 V: %.0f mV; pin to 0.46 V: %.0f mV; zero-state VICM to 0.07 V: %.1f mV | VID to 0.14 V: %.0f mV |"
      % (mg["vicm_to_330"], mg["pin_to_460"], mg["vicm_zero_to_70"], mg["slvs_vid_to_140"]))
    w()
    w("Source: N `analysis.read`, D 3. No termination in either case: one leg of every data pair is a DC reference, and a 100 ohm")
    w("differential termination would load the read level. Whether a D-PHY HS receiver works with one static leg (200 mV common-mode")
    w("step per edge) is an open bench question (D 8 item 6); that is why the fall-back exists and why RDQS is a true pair: it tells")
    w("on the bench whether a read problem is the receiver or the reference (D 3).")
    w()
    res = read_results()
    w("**Vivado legality of this pin plan (V).** Legality only: Vivado knows nothing about levels.")
    w()
    if res:
        w("| Case | Tool | What | Result | Errors | Critical warnings | Setup slack ns | Pulse-width slack ns |")
        w("|---|---|---|---|---|---|---|---|")
        for r in res:
            w("| %s | %s | %s | %s | %s | %s | %s | %s |" % (r["name"], r["tool"], r["what"], r["result"], r["errors"], r["cw"], r["wns"], r["wpws"]))
        w()
        w("Summaries with every error and critical warning verbatim, the port properties and the bank properties:")
        w("`vivado_pincheck/results/`. Sources of the check: `vivado_pincheck/lpddr5_pincheck_top.v`, `run_pincheck.tcl`, `run_pincheck.sh`.")
        w()
        w("What the check establishes for the gateware (V):")
        w()
        w("- **Command bus at 1.6 Gb/s needs native mode.** `TX_BITSLICE` (DATA_WIDTH 8) with one `BITSLICE_CONTROL` per nibble and a")
        w("  `PLLE4_ADV` in the command bank (VCO 800 MHz, `CLKOUTPHY_MODE = VCO_2X` = 1600 MHz, fabric clock 200 MHz) meets timing and")
        w("  pulse width. CK is the `TX_BITSLICE` at bitslice 0 of the upper nibble with an `OBUFDS`. Each `TX_BITSLICE` has its own output")
        w("  delay for the per-bit command deskew. Component-mode `OSERDESE3` is limited to a 625 MHz bit clock (1.25 Gb/s) on this")
        w("  speed grade: at 800 MHz Vivado reports the minimum period of `OSERDESE3/CLK` (1.600 ns) violated (case M5).")
        w("  `references/amd/DS925` agrees: component mode 1250 Mb/s (table 70), native `TX_BITSLICE` DDR with DATA_WIDTH 8 375 .. 1600 Mb/s")
        w("  (table 71). 1.6 Gb/s is therefore the data-sheet maximum, and the PLL cannot be slowed below 375 Mb/s for the boot clock:")
        w("  keep the bit rate at 1.6 Gb/s and make slow CK / CA / CS by repeating bits in the 8-bit words.")
        w("- The open finger of the command nibble may be a `TX_BITSLICE` at constant 0 or a plain `OBUF`; both are accepted.")
        w("- Write side and read side in component mode, one delay primitive per pin, as section 5 says; `wdq[*]` on `OBUFT`.")
        w("- Reads at 100 Mb/s: receiver -> `IDELAYE3` -> `ISERDESE3` clocked by the system clock (8x oversampling), the strobe sampled")
        w("  like data and inverted in logic. Using RDQS as a clock is harder: an `IDELAYE3` output may not drive a BUFG (DRC REQP-1741),")
        w("  and the strobe pin is a QBC pin, not a global-clock pin, so receiver -> `BUFGCE` needs `CLOCK_DEDICATED_ROUTE FALSE`")
        w("  (Place 30-675 without it).")
        w("- `IDELAYCTRL`: `SIM_DEVICE = \"ULTRASCALE_PLUS\"` for Vivado 2026.1, `\"ULTRASCALE\"` for Vivado 2023.1; each release refuses the")
        w("  bitstream for the other value (DRC ADEF-911).")
        w("- `IBUFDS_DPHY`: keep `IOSTANDARD = \"MIPI_DPHY_DCI\"` on the instance. Vivado 2023.1 refuses the bitstream without it (DRC")
        w("  AVAL-252, case N3 in 2023.1); Vivado 2026.1 accepts the standard from the XDC alone. The primitive cannot be used with")
        w("  SLVS_400_18: the fall-back needs a plain `IBUFDS`.")
        w("- No `INTERNAL_VREF`, no `DCI_CASCADE`; the VRP pins of the three banks stay free. The ZCU104 system clock CLK_300 (AH18 / AH17)")
        w("  lies in the write bank, in the nibble of WCK; that is accepted.")
    else:
        w("Not run yet: `vivado_pincheck/run_pincheck.sh` (the summaries appear in `vivado_pincheck/results/`).")
    w()
    w("## 7. What to measure first (D 8)")
    w()
    w("1. **Rails** on TP1 (VDD1 %s V), TP2 (VDD2H %s V), TP3 (VDDQ %s V), TP4 (VREF_RD %.3f V with the DAC untouched), TP5 (GND); the"
      % (f3(rails["VDD1"]["v"]), f3(rails["VDD2H"]["v"]), f3(rails["VDDQ"]["v"]), ref["dac_high_z_v"]))
    w("   host 1.2 V at the card under load (>= 1.12 V for 0.52 A) (D 4, D 8 item 10).")
    w("2. **A static high at a divider node** with the FPGA driving one command pin high: expect %s V on CA, %s V on CS, %s V"
      % (lv("CA0"), lv("CS0"), lv("DQ0-7")))
    w("   on a write DQ. It gives the real driver resistance (D 8 item 2). No test pads on signal nets: probe at the shunt resistor.")
    w("3. **Termination really off:** under 1 mV across a 39 ohm N resistor with the P pin at 0 V (D 8 item 7).")
    w("4. **RESET_n** low %s V / high %s V at R403 before releasing reset for real (D 1)." % ("0", lv("RESET_n")))
    w("5. **First MRR** at the boot clock (MR8 or any register with a known value) through the RDQS-independent path: sample the eight read")
    w("   pairs with the system clock and look for the value **[choice of register: no source]**.")
    w("6. **Read high level** of this DRAM into 150 ohm (proxies %.3f V nominal, JEDEC bound %.3f .. %.3f V) and how it moves with MR17 / MR3"
      % (rd["read_high_v"][1], rd["read_high_jedec_bound_v"][0], rd["read_high_jedec_bound_v"][2]))
    w("   (D 8 item 5). If it is above 0.42 V keep VREF_RD at or below 0.20 V.")
    w("7. **VREF_RD sweep against bit errors at 100 Mb/s**, with `IBUFDS_DPHY` first, then with the SLVS fall-back (D 8 item 6).")
    w("8. **A read finger with an active probe at 100 Mb/s** for the three drive strengths (40 / 48 / 60 ohm): the open FPGA end overshoots")
    w("   for 1-2 ns per edge (0.549 V / -0.131 V in the strong corner) (D 3, D 8 item 29).")
    w("9. **The unused half of the x16 die** (DQ8-15 and RDQS1 of channel A are open balls, WCK1 is held at its static level by two 10 k) and the")
    w("   idle channel-B die with a single VDD2 rail: JEDEC is silent; watch VDDQ / VDD2H current and read stability (D 5, D 8 items 8, 9).")
    w("10. **Command eye at 1.6 Gb/s** (800 MHz CK): the goal of the card. Sweep the per-bit output delay of each CA pin and the MR12 code")
    w("    against command errors; TDR of CA2 against CA0 at the fingers of a bare card checks the on-card match (D 8 items 3, 4, 18).")
    return o.getvalue()


# --------------------------------------------------------------------------------------------------------------
# Vivado pin-plan check: generated top level, board constraints, flow
# --------------------------------------------------------------------------------------------------------------
PINCHECK_V = r'''// GENERATED by scripts/gen_gateware.py.  Pin-plan check of the LPDDR5 test card: NOT a memory controller.
// It instantiates the real I/O structure on the real pins of ../lpddr5_testbed_zcu104.xdc so that Vivado can
// say whether the plan is legal (synthesis, place, route, DRC, bitstream):
//   command  bank @CMD_BANK@   CMD_NATIVE = 1: native TX_BITSLICE, PLL VCO 800 MHz x2 = 1.6 Gb/s (CK = 800 MHz);
//                              CMD_NATIVE = 0: component-mode OSERDESE3 at CMD_COMP_HS MHz x2
//   write    bank @WR_BANK@   OSERDESE3 -> ODELAYE3 (TIME, per-bit value) -> OBUFT; WCK OSERDESE3 -> ODELAYE3 -> OBUFDS
//   read     bank @RD_BANK@   RX_SLVS = 0: IBUFDS_DPHY (MIPI_DPHY_DCI on the instance, DIFF_TERM FALSE, LP off)
//                              RX_SLVS = 1: IBUFDS (SLVS_400_18 from the override XDC)
//                              RX_SLVS = 2: IBUFDS_DPHY with IOSTANDARD "DEFAULT" on the instance (the standard from the XDC)
//                              RX_SLVS = 3: as 2, with the SLVS_400_18 override XDC (negative test)
//                              -> IDELAYE3 (TIME, per-bit value) -> ISERDESE3; RDQS the same way (RDQS_AS_CLOCK = 0),
//                              or RDQS as the capture clock through a BUFGCE: taken behind its IDELAYE3
//                              (RDQS_AS_CLOCK = 1, negative test) or straight from the receiver (RDQS_AS_CLOCK = 2)
//   reset_n  LVCMOS12, static, the only output in the read bank
// Clock: the ZCU104 300 MHz system clock (CLK_300, AH18 / AH17, bank 64), see pincheck_board.xdc.
`timescale 1ps/1ps

module pincheck_cmd_nibble #(parameter [6:0] TXMASK = 7'd0) (
  input  wire        pll_clk, clk, rst, en_vtc,
  input  wire [55:0] txd,
  output wire [6:0]  txo,
  output wire        rdy);
  wire [279:0] rx_c2s, rx_s2c, tx_c2s, tx_s2c;
  wire dly_rdy, vtc_rdy;
  assign rdy = dly_rdy & vtc_rdy;
  BITSLICE_CONTROL #(.CTRL_CLK("EXTERNAL"), .DIV_MODE("DIV4"), .EN_CLK_TO_EXT_NORTH("DISABLE"), .EN_CLK_TO_EXT_SOUTH("DISABLE"),
     .EN_DYN_ODLY_MODE("FALSE"), .EN_OTHER_NCLK("FALSE"), .EN_OTHER_PCLK("FALSE"), .IDLY_VT_TRACK("TRUE"), .INV_RXCLK("FALSE"),
     .ODLY_VT_TRACK("TRUE"), .QDLY_VT_TRACK("TRUE"), .READ_IDLE_COUNT(6'h00), .REFCLK_SRC("PLLCLK"), .ROUNDING_FACTOR(16),
     .RXGATE_EXTEND("FALSE"), .RX_CLK_PHASE_N("SHIFT_0"), .RX_CLK_PHASE_P("SHIFT_0"), .RX_GATING("DISABLE"),
     .SELF_CALIBRATE("ENABLE"), .SERIAL_MODE("FALSE"), .SIM_DEVICE("ULTRASCALE_PLUS"), .TX_GATING("DISABLE")) u_bsc (
     .PLL_CLK(pll_clk), .REFCLK(1'b0), .RST(rst), .EN_VTC(en_vtc), .DLY_RDY(dly_rdy), .VTC_RDY(vtc_rdy),
     .RIU_CLK(clk), .RIU_ADDR(6'd0), .RIU_NIBBLE_SEL(1'b0), .RIU_WR_DATA(16'd0), .RIU_WR_EN(1'b0), .RIU_RD_DATA(), .RIU_VALID(),
     .CLK_FROM_EXT(1'b1), .CLK_TO_EXT_NORTH(), .CLK_TO_EXT_SOUTH(), .DYN_DCI(),
     .NCLK_NIBBLE_IN(1'b1), .PCLK_NIBBLE_IN(1'b1), .NCLK_NIBBLE_OUT(), .PCLK_NIBBLE_OUT(),
     .PHY_RDCS0(4'd0), .PHY_RDCS1(4'd0), .PHY_RDEN(4'hF), .PHY_WRCS0(4'd0), .PHY_WRCS1(4'd0), .TBYTE_IN(4'hF),
     .RX_BIT_CTRL_IN0(rx_s2c[0+:40]), .RX_BIT_CTRL_IN1(rx_s2c[40+:40]), .RX_BIT_CTRL_IN2(rx_s2c[80+:40]), .RX_BIT_CTRL_IN3(rx_s2c[120+:40]),
     .RX_BIT_CTRL_IN4(rx_s2c[160+:40]), .RX_BIT_CTRL_IN5(rx_s2c[200+:40]), .RX_BIT_CTRL_IN6(rx_s2c[240+:40]),
     .RX_BIT_CTRL_OUT0(rx_c2s[0+:40]), .RX_BIT_CTRL_OUT1(rx_c2s[40+:40]), .RX_BIT_CTRL_OUT2(rx_c2s[80+:40]), .RX_BIT_CTRL_OUT3(rx_c2s[120+:40]),
     .RX_BIT_CTRL_OUT4(rx_c2s[160+:40]), .RX_BIT_CTRL_OUT5(rx_c2s[200+:40]), .RX_BIT_CTRL_OUT6(rx_c2s[240+:40]),
     .TX_BIT_CTRL_IN0(tx_s2c[0+:40]), .TX_BIT_CTRL_IN1(tx_s2c[40+:40]), .TX_BIT_CTRL_IN2(tx_s2c[80+:40]), .TX_BIT_CTRL_IN3(tx_s2c[120+:40]),
     .TX_BIT_CTRL_IN4(tx_s2c[160+:40]), .TX_BIT_CTRL_IN5(tx_s2c[200+:40]), .TX_BIT_CTRL_IN6(tx_s2c[240+:40]),
     .TX_BIT_CTRL_OUT0(tx_c2s[0+:40]), .TX_BIT_CTRL_OUT1(tx_c2s[40+:40]), .TX_BIT_CTRL_OUT2(tx_c2s[80+:40]), .TX_BIT_CTRL_OUT3(tx_c2s[120+:40]),
     .TX_BIT_CTRL_OUT4(tx_c2s[160+:40]), .TX_BIT_CTRL_OUT5(tx_c2s[200+:40]), .TX_BIT_CTRL_OUT6(tx_c2s[240+:40]),
     .TX_BIT_CTRL_IN_TRI(40'd0), .TX_BIT_CTRL_OUT_TRI());
  genvar b;
  generate for (b = 0; b < 7; b = b + 1) begin : g_b
    if (TXMASK[b]) begin : g_tx
      TX_BITSLICE #(.DATA_WIDTH(8), .DELAY_FORMAT("TIME"), .DELAY_TYPE("FIXED"), .DELAY_VALUE(0), .ENABLE_PRE_EMPHASIS("FALSE"),
         .INIT(1'b0), .NATIVE_ODELAY_BYPASS("FALSE"), .OUTPUT_PHASE_90("FALSE"), .REFCLK_FREQUENCY(1600.0),
         .SIM_DEVICE("ULTRASCALE_PLUS"), .TBYTE_CTL("T"), .UPDATE_MODE("ASYNC")) u_tx (
         .CE(1'b0), .CLK(clk), .CNTVALUEIN(9'd0), .CNTVALUEOUT(), .D(txd[8*b+:8]), .EN_VTC(en_vtc), .INC(1'b0), .LOAD(1'b0),
         .O(txo[b]), .RST(rst), .RST_DLY(rst), .T(1'b0), .T_OUT(), .TBYTE_IN(1'b0),
         .RX_BIT_CTRL_IN(rx_c2s[40*b+:40]), .RX_BIT_CTRL_OUT(rx_s2c[40*b+:40]),
         .TX_BIT_CTRL_IN(tx_c2s[40*b+:40]), .TX_BIT_CTRL_OUT(tx_s2c[40*b+:40]));
    end else begin : g_none
      assign rx_s2c[40*b+:40] = 40'd0;
      assign tx_s2c[40*b+:40] = 40'd0;
      assign txo[b] = 1'b0;
    end
  end endgenerate
endmodule

module lpddr5_pincheck_top #(
  parameter integer RX_SLVS        = 0,   // 0: IBUFDS_DPHY / MIPI_DPHY_DCI, 1: IBUFDS / SLVS_400_18, 2 / 3: IBUFDS_DPHY, attribute DEFAULT
  parameter integer CMD_NATIVE     = 1,   // 1: native TX_BITSLICE at 1.6 Gb/s, 0: OSERDESE3
  parameter integer RDQS_AS_CLOCK  = 0,   // 0: RDQS sampled like data; 1 / 2: RDQS through a BUFGCE clocks the capture flops
  parameter integer GUARD_BITSLICE = 1,   // native mode: 1 = the open finger is a TX_BITSLICE at constant 0, 0 = a plain OBUF
  parameter integer CMD_COMP_800   = 0,   // component mode: 0 = 600 MHz bit clock (1.2 Gb/s), 1 = 800 MHz (1.6 Gb/s)
  // Vivado 2026.1 wants "ULTRASCALE_PLUS" here and refuses the bitstream otherwise (DRC ADEF-911); Vivado 2023.1 knows
  // only "7SERIES" / "ULTRASCALE" and refuses the bitstream for "ULTRASCALE_PLUS" (the same DRC)
  parameter         IDELAYCTRL_SIM_DEVICE = "ULTRASCALE_PLUS"
)(
  input  wire       clk300_p, clk300_n,
  output wire [6:0] ca,
  output wire       cs0,
  output wire       ck_p, ck_n,
@GUARD_PORT@
  output wire [7:0] wdq,
  output wire       wck_p, wck_n,
  input  wire [7:0] rdq_p, rdq_n,
  input  wire       rdqs_p, rdqs_n,
  output wire       reset_n,
  output wire       led
);
`include "lpddr5_testbed_pins.vh"

  // ------------------------------------------------------------------ clocks
  wire clk300_i, mm_fb, mm_hs, mm_200, mm_cmp, mm_locked;
  wire clk_hs, clk_div, clk200;
  IBUFDS u_ibclk (.I(clk300_p), .IB(clk300_n), .O(clk300_i));
  // VCO 1200 MHz: 400 MHz bit clock of the component-mode serdes (800 Mb/s), 200 MHz for the command PLL,
  // 600 MHz for the component-mode command variant
  MMCME4_BASE #(.CLKIN1_PERIOD(3.333), .CLKFBOUT_MULT_F(4.0), .DIVCLK_DIVIDE(1), .CLKOUT0_DIVIDE_F(3.0), .CLKOUT1_DIVIDE(6),
                .CLKOUT2_DIVIDE(2)) u_mmcm
    (.CLKIN1(clk300_i), .CLKFBIN(mm_fb), .CLKFBOUT(mm_fb), .CLKOUT0(mm_hs), .CLKOUT1(mm_200), .CLKOUT2(mm_cmp),
     .LOCKED(mm_locked), .PWRDWN(1'b0), .RST(1'b0));
  BUFGCE     u_bhs  (.I(mm_hs), .CE(1'b1), .O(clk_hs));                                        // 400 MHz
  BUFGCE_DIV #(.BUFGCE_DIVIDE(4)) u_bdiv (.I(mm_hs), .CE(1'b1), .CLR(1'b0), .O(clk_div));     // 100 MHz
  BUFGCE     u_b200 (.I(mm_200), .CE(1'b1), .O(clk200));                                       // 200 MHz

  reg [7:0] rst_sr = 8'hFF;
  always @(posedge clk_div) rst_sr <= {rst_sr[6:0], ~mm_locked};
  wire rst = rst_sr[7];
  reg [31:0] lfsr = 32'hACE1_2345;
  always @(posedge clk_div) lfsr <= {lfsr[30:0], lfsr[31] ^ lfsr[21] ^ lfsr[1] ^ lfsr[0]};

  wire idc_rdy;
  // EN_VTC of the delay lines is logic-driven: it has to go low while a new value is loaded (training)
  reg dly_en_vtc = 1'b1, dly_load = 1'b0;
  always @(posedge clk_div) begin dly_en_vtc <= ~lfsr[30]; dly_load <= lfsr[30] & lfsr[29] & ~dly_en_vtc; end
  (* IODELAY_GROUP = "pincheck" *)
  IDELAYCTRL #(.SIM_DEVICE(IDELAYCTRL_SIM_DEVICE)) u_idc (.REFCLK(clk_hs), .RST(rst), .RDY(idc_rdy));

  // ------------------------------------------------------------------ command bus (bank @CMD_BANK@)
  wire cmd_par;
  generate
    if (CMD_NATIVE != 0) begin : g_cmd_native
      // PLL in the command bank: 200 MHz x4 = 800 MHz VCO, CLKOUTPHY = VCO x2 = 1600 MHz -> 1.6 Gb/s, CK = 800 MHz
      wire pll_fb, pll_phy, pll_c0, pll_locked, clk_cmd;
      PLLE4_ADV #(.CLKIN_PERIOD(5.0), .CLKFBOUT_MULT(4), .DIVCLK_DIVIDE(1), .CLKOUT0_DIVIDE(4), .CLKOUTPHY_MODE("VCO_2X")) u_pll (
         .CLKIN(clk200), .CLKFBIN(pll_fb), .CLKFBOUT(pll_fb), .CLKOUT0(pll_c0), .CLKOUT0B(), .CLKOUT1(), .CLKOUT1B(),
         .CLKOUTPHY(pll_phy), .CLKOUTPHYEN(1'b1), .LOCKED(pll_locked), .PWRDWN(1'b0), .RST(1'b0),
         .DADDR(7'd0), .DCLK(1'b0), .DEN(1'b0), .DI(16'd0), .DWE(1'b0), .DO(), .DRDY());
      BUFG u_bcmd (.I(pll_c0), .O(clk_cmd));                                                   // 200 MHz = 1.6 Gb/s / 8
      reg [7:0] crst = 8'hFF;
      always @(posedge clk_cmd) crst <= {crst[6:0], ~pll_locked};
      wire [1:0] rdy;
      reg en_vtc = 1'b0;
      always @(posedge clk_cmd) en_vtc <= &rdy;
      reg [31:0] cl = 32'h1357_9BDF;
      always @(posedge clk_cmd) cl <= {cl[30:0], cl[31] ^ cl[21] ^ cl[1] ^ cl[0]};
      reg [55:0] d_l, d_u;
      wire [6:0] o_l, o_u;
      always @(posedge clk_cmd) begin
@NATIVE_DATA@
      end
      pincheck_cmd_nibble #(.TXMASK(GUARD_BITSLICE != 0 ? @MASK_L_GUARD@ : @MASK_L@)) u_nib_l
        (.pll_clk(pll_phy), .clk(clk_cmd), .rst(crst[7]), .en_vtc(en_vtc), .txd(d_l), .txo(o_l), .rdy(rdy[0]));
      pincheck_cmd_nibble #(.TXMASK(@MASK_U@)) u_nib_u
        (.pll_clk(pll_phy), .clk(clk_cmd), .rst(crst[7]), .en_vtc(en_vtc), .txd(d_u), .txo(o_u), .rdy(rdy[1]));
@NATIVE_BUFS@
      reg par = 1'b0;
      always @(posedge clk_cmd) par <= par ^ cl[0] ^ (&rdy);
      assign cmd_par = par;
    end else begin : g_cmd_comp
      wire cclk_hs, cclk_div;
      if (CMD_COMP_800 != 0) begin : g_800
        wire pll_fb, pll_c0, pll_locked;
        PLLE4_ADV #(.CLKIN_PERIOD(5.0), .CLKFBOUT_MULT(4), .DIVCLK_DIVIDE(1), .CLKOUT0_DIVIDE(1), .CLKOUTPHY_MODE("VCO")) u_pll (
           .CLKIN(clk200), .CLKFBIN(pll_fb), .CLKFBOUT(pll_fb), .CLKOUT0(pll_c0), .CLKOUT0B(), .CLKOUT1(), .CLKOUT1B(),
           .CLKOUTPHY(), .CLKOUTPHYEN(1'b0), .LOCKED(pll_locked), .PWRDWN(1'b0), .RST(1'b0),
           .DADDR(7'd0), .DCLK(1'b0), .DEN(1'b0), .DI(16'd0), .DWE(1'b0), .DO(), .DRDY());
        BUFGCE     u_bc  (.I(pll_c0), .CE(1'b1), .O(cclk_hs));                                  // 800 MHz
        BUFGCE_DIV #(.BUFGCE_DIVIDE(4)) u_bcd (.I(pll_c0), .CE(1'b1), .CLR(1'b0), .O(cclk_div));
      end else begin : g_600
        BUFGCE     u_bc  (.I(mm_cmp), .CE(1'b1), .O(cclk_hs));                                  // 600 MHz
        BUFGCE_DIV #(.BUFGCE_DIVIDE(4)) u_bcd (.I(mm_cmp), .CE(1'b1), .CLR(1'b0), .O(cclk_div));
      end
      reg [7:0] crst = 8'hFF;
      always @(posedge cclk_div) crst <= {crst[6:0], ~mm_locked};
      reg [31:0] cl = 32'h1357_9BDF;
      always @(posedge cclk_div) cl <= {cl[30:0], cl[31] ^ cl[21] ^ cl[1] ^ cl[0]};
      wire [8:0] oq;                       // 0-6 CA, 7 CS, 8 CK
      genvar k;
      for (k = 0; k < 9; k = k + 1) begin : g_os
        reg [7:0] d;
        always @(posedge cclk_div) d <= (k == 8) ? (CK_INVERTED != 0 ? 8'b10101010 : 8'b01010101) : cl[k +: 8];
        OSERDESE3 #(.DATA_WIDTH(8), .SIM_DEVICE("ULTRASCALE_PLUS")) u_os
          (.CLK(cclk_hs), .CLKDIV(cclk_div), .D(d), .RST(crst[7]), .OQ(oq[k]), .T(1'b0), .T_OUT());
      end
      for (k = 0; k < 7; k = k + 1) begin : g_ob
        OBUF u_ob (.I(oq[k]), .O(ca[k]));
      end
      OBUF   u_ob_cs (.I(oq[7]), .O(cs0));
      OBUFDS u_ob_ck (.I(oq[8]), .O(ck_p), .OB(ck_n));
@COMP_GUARD@
      reg par = 1'b0;
      always @(posedge cclk_div) par <= par ^ cl[0];
      assign cmd_par = par;
    end
  endgenerate

  // ------------------------------------------------------------------ write side (bank @WR_BANK@)
  reg wr_en = 1'b0;                        // write DQ pins are Hi-Z during reads
  always @(posedge clk_div) wr_en <= lfsr[31];
  genvar i;
  generate
    for (i = 0; i < 8; i = i + 1) begin : g_wdq
      localparam integer DV = WDQ_ODELAY_PS[16*i +: 16];
      reg [7:0] d;
      always @(posedge clk_div) d <= lfsr[i +: 8];
      wire oq, tq, od;
      OSERDESE3 #(.DATA_WIDTH(8), .SIM_DEVICE("ULTRASCALE_PLUS")) u_os
        (.CLK(clk_hs), .CLKDIV(clk_div), .D(d), .RST(rst), .OQ(oq), .T(~wr_en), .T_OUT(tq));
      (* IODELAY_GROUP = "pincheck" *)
      ODELAYE3 #(.CASCADE("NONE"), .DELAY_FORMAT("TIME"), .DELAY_TYPE("VAR_LOAD"), .DELAY_VALUE(DV), .REFCLK_FREQUENCY(400.0),
                 .SIM_DEVICE("ULTRASCALE_PLUS"), .UPDATE_MODE("ASYNC")) u_od
        (.CASC_IN(1'b0), .CASC_RETURN(1'b0), .CASC_OUT(), .CE(1'b0), .CLK(clk_div), .CNTVALUEIN(lfsr[8:0]), .CNTVALUEOUT(),
         .ODATAIN(oq), .DATAOUT(od), .EN_VTC(dly_en_vtc), .INC(1'b0), .LOAD(dly_load), .RST(rst));
      OBUFT u_ob (.I(od), .T(tq), .O(wdq[i]));
    end
  endgenerate
  // WCK: the pattern is inverted when the true leg of the card sits on the FPGA N pin
  reg [7:0] wck_d;
  always @(posedge clk_div) wck_d <= (WCK_INVERTED != 0) ? 8'b10101010 : 8'b01010101;
  wire wck_oq, wck_od;
  OSERDESE3 #(.DATA_WIDTH(8), .SIM_DEVICE("ULTRASCALE_PLUS")) u_os_wck
    (.CLK(clk_hs), .CLKDIV(clk_div), .D(wck_d), .RST(rst), .OQ(wck_oq), .T(1'b0), .T_OUT());
  (* IODELAY_GROUP = "pincheck" *)
  ODELAYE3 #(.CASCADE("NONE"), .DELAY_FORMAT("TIME"), .DELAY_TYPE("VAR_LOAD"), .DELAY_VALUE(0), .REFCLK_FREQUENCY(400.0),
             .SIM_DEVICE("ULTRASCALE_PLUS"), .UPDATE_MODE("ASYNC")) u_od_wck
    (.CASC_IN(1'b0), .CASC_RETURN(1'b0), .CASC_OUT(), .CE(1'b0), .CLK(clk_div), .CNTVALUEIN(lfsr[8:0]), .CNTVALUEOUT(),
     .ODATAIN(wck_oq), .DATAOUT(wck_od), .EN_VTC(dly_en_vtc), .INC(1'b0), .LOAD(dly_load), .RST(rst));
  OBUFDS u_ob_wck (.I(wck_od), .O(wck_p), .OB(wck_n));

  // ------------------------------------------------------------------ read side (bank @RD_BANK@)
  wire [8:0] rx;                            // 0-7 DQ, 8 RDQS
  wire [8:0] rx_pp = {rdqs_p, rdq_p};
  wire [8:0] rx_nn = {rdqs_n, rdq_n};
  generate
    for (i = 0; i < 9; i = i + 1) begin : g_rx
      if (RX_SLVS == 0) begin : g_dphy
        IBUFDS_DPHY #(.DIFF_TERM("FALSE"), .IOSTANDARD("MIPI_DPHY_DCI"), .SIM_DEVICE("ULTRASCALE_PLUS")) u_ib
          (.I(rx_pp[i]), .IB(rx_nn[i]), .HSRX_DISABLE(1'b0), .LPRX_DISABLE(1'b1),
           .HSRX_O(rx[i]), .LPRX_O_N(), .LPRX_O_P());
      end else if (RX_SLVS == 2 || RX_SLVS == 3) begin : g_dphy_default
        IBUFDS_DPHY #(.DIFF_TERM("FALSE"), .IOSTANDARD("DEFAULT"), .SIM_DEVICE("ULTRASCALE_PLUS")) u_ib
          (.I(rx_pp[i]), .IB(rx_nn[i]), .HSRX_DISABLE(1'b0), .LPRX_DISABLE(1'b1),
           .HSRX_O(rx[i]), .LPRX_O_N(), .LPRX_O_P());
      end else begin : g_slvs
        IBUFDS u_ib (.I(rx_pp[i]), .IB(rx_nn[i]), .O(rx[i]));
      end
    end
  endgenerate
  wire [8:0] rx_dly;
  generate
    for (i = 0; i < 9; i = i + 1) begin : g_idly
      localparam integer DV = (i == 8) ? RDQS_IDELAY_PS : RDQ_IDELAY_PS[16*(i % 8) +: 16];
      (* IODELAY_GROUP = "pincheck" *)
      IDELAYE3 #(.CASCADE("NONE"), .DELAY_FORMAT("TIME"), .DELAY_SRC("IDATAIN"), .DELAY_TYPE("VAR_LOAD"), .DELAY_VALUE(DV),
                 .REFCLK_FREQUENCY(400.0), .SIM_DEVICE("ULTRASCALE_PLUS"), .UPDATE_MODE("ASYNC")) u_id
        (.CASC_IN(1'b0), .CASC_RETURN(1'b0), .CASC_OUT(), .CE(1'b0), .CLK(clk_div), .CNTVALUEIN(lfsr[8:0]), .CNTVALUEOUT(),
         .DATAIN(1'b0), .IDATAIN(rx[i]), .DATAOUT(rx_dly[i]), .EN_VTC(dly_en_vtc), .INC(1'b0), .LOAD(dly_load), .RST(rst));
    end
  endgenerate
  reg [8:0] rd_par = 9'd0;
  generate
    if (RDQS_AS_CLOCK == 0) begin : g_rd_serdes
      // 8x oversampling of a 100 Mb/s read with the system clock; the strobe is sampled like data and inverted here
      for (i = 0; i < 9; i = i + 1) begin : g_b
        wire [7:0] q;
        ISERDESE3 #(.DATA_WIDTH(8), .FIFO_ENABLE("FALSE"), .FIFO_SYNC_MODE("FALSE"), .IS_CLK_B_INVERTED(1'b1),
                    .SIM_DEVICE("ULTRASCALE_PLUS")) u_is
          (.CLK(clk_hs), .CLK_B(clk_hs), .CLKDIV(clk_div), .D(rx_dly[i]), .Q(q), .RST(rst),
           .FIFO_RD_CLK(1'b0), .FIFO_RD_EN(1'b0), .FIFO_EMPTY(), .INTERNAL_DIVCLK());
        reg [7:0] qr;
        always @(posedge clk_div) begin
          qr <= (i == 8 && RDQS_INVERTED != 0) ? ~q : q;
          rd_par[i] <= ^qr;
        end
      end
    end else begin : g_rd_strobe
      // the strobe as a clock: RDQS receiver (-> IDELAYE3 if RDQS_AS_CLOCK = 1) -> BUFGCE -> capture flops (both edges)
      // the inverted pair is handled by swapping the two capture edges: nothing stands between receiver and buffer
      wire rdqs_src = (RDQS_AS_CLOCK == 1) ? rx_dly[8] : rx[8];
      wire rdqs_clk;
      BUFGCE u_brdqs (.I(rdqs_src), .CE(1'b1), .O(rdqs_clk));
      reg [7:0] cr, cf;
      always @(posedge rdqs_clk) cr <= rx_dly[7:0];
      always @(negedge rdqs_clk) cf <= rx_dly[7:0];
      (* ASYNC_REG = "TRUE" *) reg [15:0] s0, s1;
      always @(posedge clk_div) begin
        s0 <= (RDQS_INVERTED != 0) ? {cr, cf} : {cf, cr};
        s1 <= s0;
        rd_par <= {1'b0, s1[15:8] ^ s1[7:0]};
      end
    end
  endgenerate

  // ------------------------------------------------------------------ RESET_n and the observation pin
  reg rst_n_q = 1'b0;
  always @(posedge clk_div) rst_n_q <= ~rst & idc_rdy;
  OBUF u_ob_rst (.I(rst_n_q), .O(reset_n));
  (* ASYNC_REG = "TRUE" *) reg xdom_sync0 = 1'b0, xdom_sync1 = 1'b0;
  reg led_q = 1'b0;
  always @(posedge clk_div) begin xdom_sync0 <= cmd_par; xdom_sync1 <= xdom_sync0; led_q <= ^rd_par ^ xdom_sync1; end
  OBUF u_ob_led (.I(led_q), .O(led));
endmodule
'''

PINCHECK_BOARD_XDC = '''## GENERATED by scripts/gen_gateware.py.  Board pins of the pin-plan check that are NOT on the card.
## CLK_300: the 300 MHz system clock of the ZCU104 PL DDR4 interface.  Schematic XTP484 sheet 5: CLK_300_P / CLK_300_N on
## AH18 / AH17 = IO_L13P / L13N_T2L_N0 / N1_GC_QBC_64, i.e. in the WRITE bank of the card, in the nibble of WCK, with a
## 100 ohm resistor across the pair on the board.  (UG1267 table 3-13 names AH12 / AJ12: that table is off by one bank;
## AH12 is the SODIMM finger 199.)  DIFF_SSTL12 is the standard the board's own constraint file uses for this clock; that
## file is not in this repository.
create_clock -name clk300 -period 3.333 [get_ports clk300_p]
set_property PACKAGE_PIN AH18 [get_ports clk300_p]
set_property PACKAGE_PIN AH17 [get_ports clk300_n]
set_property IOSTANDARD DIFF_SSTL12 [get_ports {clk300_p clk300_n}]
## GPIO_LED_0 (UG1267: D5, LVCMOS33)
set_property PACKAGE_PIN D5 [get_ports led]
set_property IOSTANDARD LVCMOS33 [get_ports led]
## the one bit that crosses from the command clock to the system clock
set_false_path -to [get_cells -hier -filter {NAME =~ *xdom_sync0_reg}]
'''

PINCHECK_TCL = r'''# GENERATED by scripts/gen_gateware.py.  Non-project flow of the pin-plan check.
#   vivado -mode batch -source run_pincheck.tcl -tclargs <name> <rx_slvs> <cmd_native> <rdqs_as_clock> <guard_bitslice> <cmd_comp_800> [extra.xdc]
# Reports go to the current directory.  The bit file is written and deleted (legality only).
lassign $argv name rx_slvs cmd_native rdqs_clk guard_bs comp800 extra
set here [file dirname [file normalize [info script]]]
set gw   [file dirname $here]
set part @PART@
# IDELAYCTRL SIM_DEVICE: "ULTRASCALE_PLUS" for Vivado 2026.1, "ULTRASCALE" for 2023.1 (each refuses the other's value)
set simdev ULTRASCALE_PLUS
if {[info exists ::env(IDELAYCTRL_SIM_DEVICE)]} { set simdev $::env(IDELAYCTRL_SIM_DEVICE) }
puts "@@VERSION [version -short]"
puts "@@CASE $name rx_slvs=$rx_slvs cmd_native=$cmd_native rdqs_as_clock=$rdqs_clk guard_bitslice=$guard_bs cmd_comp_800=$comp800 idelayctrl_sim_device=$simdev extra=[file tail $extra]"
read_verilog $here/lpddr5_pincheck_top.v
read_xdc $gw/lpddr5_testbed_zcu104.xdc
if {$rx_slvs == 1 || $rx_slvs == 3} { read_xdc $gw/lpddr5_testbed_zcu104_rx_slvs.xdc }
read_xdc $here/pincheck_board.xdc
if {$extra ne ""} { read_xdc $extra }
set ok 1
foreach {st cmd} [list \
   synth  "synth_design -top lpddr5_pincheck_top -part $part -include_dirs $gw -generic RX_SLVS=$rx_slvs -generic CMD_NATIVE=$cmd_native -generic RDQS_AS_CLOCK=$rdqs_clk -generic GUARD_BITSLICE=$guard_bs -generic CMD_COMP_800=$comp800 -generic IDELAYCTRL_SIM_DEVICE=$simdev" \
   opt    "opt_design" \
   drc_preplace "report_drc -file drc_preplace.rpt" \
   place  "place_design" \
   route  "route_design" \
   drc_routed "report_drc -file drc_routed.rpt" \
   drc_bitstream "report_drc -ruledecks bitstream_checks -file drc_bitstream.rpt" \
   methodology "report_methodology -file methodology.rpt" \
   reports "report_io -file io.rpt; report_route_status -file route_status.rpt; report_timing_summary -file timing.rpt; report_pulse_width -file pulse_width.rpt; report_ssn -file ssn.rpt; report_clock_utilization -file clock_util.rpt" \
   bitstream "write_bitstream -force $name.bit" ] {
  if {!$ok && $st ni {drc_preplace drc_routed drc_bitstream reports}} { puts "@@RESULT $st SKIPPED"; continue }
  puts "@@STAGE $st"
  if {[catch {uplevel #0 $cmd} msg]} {
    puts "@@RESULT $st FAIL :: $msg"
    if {$st in {synth opt place route bitstream}} { set ok 0 }
  } else { puts "@@RESULT $st PASS" }
  if {$st eq "place" || ($st eq "opt" && !$ok)} {
    catch {
      foreach c [get_cells -hier -filter {REF_NAME == PLLE4_ADV || REF_NAME == MMCME4_ADV || REF_NAME == BITSLICE_CONTROL || REF_NAME == IDELAYCTRL || REF_NAME =~ BUFG*}] {
        puts "@@LOC $c [get_property REF_NAME $c] [get_property LOC $c]"
      }
    }
  }
  if {$st eq "route"} {
    catch {
      foreach b [get_iobanks -filter "NAME==64 || NAME==65 || NAME==66"] {
        puts "@@BANK [get_property NAME $b] BANK_TYPE=[get_property BANK_TYPE $b] DCI_CASCADE=[get_property DCI_CASCADE $b] INTERNAL_VREF=[get_property INTERNAL_VREF $b]"
      }
      foreach p [lsort -dictionary [get_ports]] {
        puts "@@PORT $p pin=[get_property PACKAGE_PIN $p] bank=[get_property IOBANK $p] std=[get_property IOSTANDARD $p] dir=[get_property DIRECTION $p] drive=[get_property DRIVE $p] slew=[get_property SLEW $p] outz=[get_property OUTPUT_IMPEDANCE $p] diff_term_adv=[get_property DIFF_TERM_ADV $p] odt=[get_property ODT $p]"
      }
      foreach p [get_package_pins -filter {PIN_FUNC =~ *VRP* && (BANK == 64 || BANK == 65 || BANK == 66)}] {
        puts "@@VRP [get_property NAME $p] [get_property PIN_FUNC $p] used_by=[get_ports -quiet -of_objects $p]"
      }
      puts "@@TIMING WNS=[get_property SLACK [get_timing_paths -max_paths 1 -nworst 1 -setup]] WHS=[get_property SLACK [get_timing_paths -max_paths 1 -nworst 1 -hold]]"
    }
  }
}
catch { file delete -force $name.bit }
puts "@@DONE"
'''

PINCHECK_SH = r'''#!/bin/bash
# GENERATED by scripts/gen_gateware.py.  Runs one case of the pin-plan check and leaves a summary in results/.
#   run_pincheck.sh <work dir> <name> <rx_slvs> <cmd_native> <rdqs_as_clock> <guard_bitslice> <cmd_comp_800> [extra.xdc]
# The cases whose summaries are in results/ (Vivado 2026.1 unless the name says otherwise):
#   M1_dphy_native16             0 1 0 1 0   first-choice receiver, native 1.6 Gb/s command bus: THE pin-plan check
#   M2_slvs_native16             1 1 0 1 0   fall-back receiver
#   M3_dphy_native16_guard_obuf  0 1 0 0 0   the open finger as a plain OBUF inside the native nibble
#   M4_dphy_comp_1200            0 0 0 0 0   command bus on OSERDESE3 at 600 MHz (1.2 Gb/s)
#   M5_dphy_comp_1600            0 0 0 0 1   command bus on OSERDESE3 at 800 MHz (1.6 Gb/s): pulse-width violation
#   N1_rdqs_clock_behind_idelay  0 1 1 1 0   negative: RDQS -> IDELAYE3 -> BUFGCE
#   N2_rdqs_clock_from_receiver  0 1 2 1 0   negative: RDQS receiver -> BUFGCE (the pin is not a GC pin)
#   N2b_rdqs_clock_cdr_false     0 1 2 1 0 <here>/extra_rdqs_clock_dedicated_route_false.xdc
#   N3_dphy_attr_default         2 1 0 1 0   IBUFDS_DPHY without the IOSTANDARD attribute on the instance (also in 2023.1)
#   N4_dphy_primitive_with_slvs  3 1 0 1 0   negative: IBUFDS_DPHY on ports constrained to SLVS_400_18
#   <case>_vivado2023                        the same sources in Vivado 2023.1: VIVADO=<2023.1 binary> IDELAYCTRL_SIM_DEVICE=ULTRASCALE
# Vivado: source /tools/Xilinx/2026.1/Vivado/settings64.sh first (or set VIVADO to the binary).
set -u
here=$(dirname "$(readlink -f "$0")")
work=$1; shift
name=$1
mkdir -p "$work/$name" "$here/results"
cd "$work/$name" || exit 2
${VIVADO:-vivado} -mode batch -nojournal -log vivado.log -source "$here/run_pincheck.tcl" -tclargs "$@" > stdout.txt 2>&1
rc=$?
{
  grep -a '^@@' stdout.txt
  echo "@@EXIT $rc"
  [ -f timing.rpt ] && awk '/WNS\(ns\).*WPWS\(ns\)/ && !d {getline; getline; print "@@TIMING_SUMMARY WNS TNS fail total | WHS THS fail total | WPWS TPWS fail total:" $0; d=1}' timing.rpt
  echo "@@COUNT errors=$(grep -a -c '^ERROR' vivado.log) critical_warnings=$(grep -a -c '^CRITICAL WARNING' vivado.log)"
  echo "@@MESSAGES every ERROR and CRITICAL WARNING line of vivado.log, verbatim (count, text)"
  grep -a -E '^(ERROR|CRITICAL WARNING)' vivado.log | sort | uniq -c
  echo "@@DRC_ROUTED violations of drc_routed.rpt (rule, severity, count)"
  [ -f drc_routed.rpt ] && awk '/^\| Rule/,/^$/' drc_routed.rpt | grep -a '^|'
  echo "@@DRC_BITSTREAM"
  [ -f drc_bitstream.rpt ] && awk '/^\| Rule/,/^$/' drc_bitstream.rpt | grep -a '^|'
  echo "@@METHODOLOGY"
  [ -f methodology.rpt ] && awk '/^\| Rule/,/^$/' methodology.rpt | grep -a '^|'
} > "$here/results/$name.summary.txt"
exit $rc
'''


def gen_pincheck_v(rows, info):
    R = {r["port"]: r for r in rows}
    cmd = [r for r in rows if r["class"] == "command" and r["port"] != "ck_n"]
    guard = next((r for r in rows if r["class"] == "command (open finger)"), None)
    bgs = {r["byte_group"][:2] for r in cmd}
    need(len(bgs) == 1 and len({r["bank"] for r in cmd}) == 1,
         "the native-mode check expects the command bus in one byte group of one bank, found %s" % sorted(bgs))
    need(R["ck_p"]["bitslice"] % 2 == 0, "ck_p is not on the P position of its bitslice pair")
    mask = {"L": 0, "U": 0}
    data, bufs = [], []
    for r in sorted(cmd, key=order_key):
        nib = r["byte_group"][2]
        b = r["bitslice"]
        mask[nib] |= 1 << b
        v = "d_%s[%d +: 8]" % (nib.lower(), 8 * b)
        if r["port"] == "ck_p":
            data.append("        %s <= (CK_INVERTED != 0) ? 8'b10101010 : 8'b01010101;   // CK: 800 MHz" % v)
            bufs.append("      OBUFDS u_ob_ck (.I(o_%s[%d]), .O(ck_p), .OB(ck_n));   // %s / %s, %s bitslice %d"
                        % (nib.lower(), b, r["fpga_ball"], R["ck_n"]["fpga_ball"], r["byte_group"], b))
        elif r["port"] == "cs0":
            data.append("        %s <= {{2{cl[3]}}, {2{cl[2]}}, {2{cl[1]}}, {2{cl[0]}}};   // CS: SDR" % v)
            bufs.append("      OBUF   u_ob_cs (.I(o_%s[%d]), .O(cs0));   // %s, %s bitslice %d" % (nib.lower(), b, r["fpga_ball"], r["byte_group"], b))
        else:
            k = int(re.search(r"\d+", r["port"]).group())
            data.append("        %s <= cl[%d +: 8];   // %s: DDR, 1.6 Gb/s" % (v, k + 4, r["port"]))
            bufs.append("      OBUF   u_ob_ca%d (.I(o_%s[%d]), .O(ca[%d]));   // %s, %s bitslice %d" % (k, nib.lower(), b, k, r["fpga_ball"], r["byte_group"], b))
    mask_l_guard = mask["L"]
    if guard:
        gn = guard["byte_group"][2]
        need(gn == "L", "the open command finger is expected in the lower nibble")
        mask_l_guard = mask["L"] | (1 << guard["bitslice"])
        data.append("        d_l[%d +: 8] <= 8'h00;   // the open finger %d: constant low" % (8 * guard["bitslice"], guard["finger"]))
        bufs.append("      if (GUARD_BITSLICE != 0) begin : g_guard_bs\n"
                    "        OBUF u_ob_guard (.I(o_l[%d]), .O(cmd_open_low));   // %s, %s bitslice %d\n"
                    "      end else begin : g_guard_obuf\n"
                    "        OBUF u_ob_guard (.I(1'b0), .O(cmd_open_low));\n"
                    "      end" % (guard["bitslice"], guard["fpga_ball"], guard["byte_group"], guard["bitslice"]))
    # unused bits of the data registers
    used_l = {8 * b for b in range(7) if mask_l_guard >> b & 1}
    used_u = {8 * b for b in range(7) if mask["U"] >> b & 1}
    for b in range(7):
        if 8 * b not in used_l:
            data.append("        d_l[%d +: 8] <= 8'h00;" % (8 * b))
        if 8 * b not in used_u:
            data.append("        d_u[%d +: 8] <= 8'h00;" % (8 * b))
    v = PINCHECK_V
    rep = {
        "@CMD_BANK@": str(cmd[0]["bank"]), "@WR_BANK@": str(R["wdq[0]"]["bank"]), "@RD_BANK@": str(R["rdq_p[0]"]["bank"]),
        "@GUARD_PORT@": "  output wire       cmd_open_low," if guard else "",
        "@NATIVE_DATA@": "\n".join(data), "@NATIVE_BUFS@": "\n".join(bufs),
        "@MASK_L@": "7'b{:07b}".format(mask["L"]), "@MASK_L_GUARD@": "7'b{:07b}".format(mask_l_guard),
        "@MASK_U@": "7'b{:07b}".format(mask["U"]),
        "@COMP_GUARD@": "      OBUF u_ob_guard (.I(1'b0), .O(cmd_open_low));" if guard else "",
    }
    for k, val in rep.items():
        v = v.replace(k, val)
    need("@" not in re.sub(r"always @|@\(", "", v), "unreplaced placeholder in the pin-check top level")
    return v


# --------------------------------------------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="write nothing; exit 1 if a file on disk differs")
    a = ap.parse_args()
    net = json.load(open(NETLIST))
    dsk = json.load(open(DESKEW))
    try:
        rows, info = build(net, dsk)
        files = {
            "lpddr5_testbed_zcu104.xdc": gen_xdc(net, rows, info),
            "lpddr5_testbed_zcu104_rx_slvs.xdc": gen_xdc_slvs(net, rows),
            "lpddr5_testbed_pins.vh": gen_vh(net, rows, info, dsk),
            "pinmap.csv": gen_csv(rows),
            "PINMAP.md": gen_pinmap_md(net, rows, info, dsk),
            "BRINGUP.md": gen_bringup(net, rows, info, dsk),
            "vivado_pincheck/lpddr5_pincheck_top.v": gen_pincheck_v(rows, info),
            "vivado_pincheck/pincheck_board.xdc": PINCHECK_BOARD_XDC,
            "vivado_pincheck/run_pincheck.tcl": PINCHECK_TCL.replace("@PART@", PART),
            "vivado_pincheck/run_pincheck.sh": PINCHECK_SH,
            "vivado_pincheck/extra_rdqs_clock_dedicated_route_false.xdc":
                "## GENERATED by scripts/gen_gateware.py.  Only for the case N2b of run_pincheck.sh: lets the strobe, which is not\n"
                "## on a global-clock pin, reach a BUFGCE over general routing.\n"
                "create_clock -name rdqs -period 10.000 [get_ports rdqs_p]\n"
                "set_property CLOCK_DEDICATED_ROUTE FALSE [get_nets -of [get_pins g_rd_strobe.u_brdqs/I]]\n",
        }
    except Bad as e:
        print("gen_gateware: FAIL: %s" % e)
        return 1
    bad = 0
    for rel, txt in files.items():
        p = os.path.join(OUT, rel)
        if a.check:
            if not os.path.exists(p) or open(p).read() != txt:
                print("gen_gateware --check: %s differs" % os.path.relpath(p, ROOT))
                bad = 1
            continue
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as fh:
            fh.write(txt)
        if rel.endswith(".sh"):
            os.chmod(p, 0o755)
        print("wrote %s (%d lines)" % (os.path.relpath(p, ROOT), txt.count("\n")))
    if not a.check:
        print("%d FPGA pins constrained; pairs inverted on the host: %s"
              % (len(rows), ", ".join(n for n, v in (("CK", info["ck_inv"]), ("WCK", info["wck_inv"]), ("RDQS", info["rdqs_inv"])) if v) or "none"))
        for n in pin_notes(rows, info):
            print("  - " + n)
    return bad


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Gate for the generated schematic: ERC, netlist parity with design/netlist.json, file hygiene.

    python3 scripts/check_schematic.py                         # ERC + parity + hygiene, exit 1 on failure
    python3 scripts/check_schematic.py --pdf doc/lpddr5-testbed.pdf --png DIR [--dpi 200]

kicad-cli works on a scratch copy of the schematic files (it rewrites the .kicad_prl next to whatever it
opens).  The copy of the .kicad_pro gets the "erc" section of design/erc_settings.json (written by
gen_schematic.py: explicit severities for every rule and the two exclusions for the strapped DMI balls), so
the gate does not depend on what the repository's .kicad_pro says about ERC; the result with the
repository's own .kicad_pro is printed for information.  The gate wants 0 errors, 0 warnings and exactly
the two documented exclusions.

The scratch copy has the project's sym-lib-table (one row, the project library) and runs with an empty
KiCad configuration directory and an empty KICAD10_SYMBOL_DIR, so nothing can be resolved from a stock or
user symbol library.

Parity: KiCad's own netlist export (kicad-cli sch export netlist) against design/netlist.json:
  - identical set of reference designators,
  - identical value, footprint, symbol lib:name, description, MPN, manufacturer, DNP and exclude-from-BOM
    flag per part,
  - for every ref.pin the same net name, and the set of unconnected pins equal to the JSON no_connect list.
"""
import argparse
import collections
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kisch import SymDef, find, find_all, parse  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PROJECT = "lpddr5-testbed"
SHEET_FILES = [PROJECT + ".kicad_sch", "connector.kicad_sch", "lpddr5.kicad_sch", "command.kicad_sch",
               "write.kicad_sch", "read.kicad_sch", "power.kicad_sch"]
OBSOLETE = ["power_supply.kicad_sch", "sodim.kicad_sch", "translators.kicad_sch", "translators_a.kicad_sch"]
TITLE = "LPDDR5 testbed for ZCU104"
BRANDING = re.compile(r"antmicro|open ?hardware|oshw|\"License\"|\"Author\"", re.I)
LIB = "lpddr5_testbed"
ERC_SETTINGS = ROOT / "design" / "erc_settings.json"

fails = []
npass = 0


def check(ok, what, detail=""):
    global npass
    if ok:
        npass += 1
    else:
        fails.append(what)
    print("%s  %s%s" % ("PASS" if ok else "FAIL", what, ("  [%s]" % detail) if detail and not ok else ""))
    return ok


def kicad(args, cwd, ok_codes=(0,)):
    # empty configuration (no global library tables) and an empty stock symbol directory: every symbol has to
    # come from the project library
    env = dict(os.environ, KICAD_CONFIG_HOME=str(Path(cwd) / "config"), KICAD10_SYMBOL_DIR=str(Path(cwd) / "config"))
    r = subprocess.run(["kicad-cli"] + args, cwd=cwd, capture_output=True, text=True, env=env)
    if r.returncode not in ok_codes:
        sys.exit("kicad-cli %s failed (%d):\n%s\n%s" % (" ".join(args), r.returncode, r.stdout, r.stderr))
    return r.stdout


def mirror(own_erc=True):
    """Scratch copy of the project; own_erc: the .kicad_pro gets the "erc" section of design/erc_settings.json."""
    d = Path(tempfile.mkdtemp(prefix="check_schematic_"))
    for f in SHEET_FILES + ["sym-lib-table", "fp-lib-table"]:
        shutil.copyfile(ROOT / f, d / f)
    pro = json.loads((ROOT / (PROJECT + ".kicad_pro")).read_text())
    if own_erc:
        pro["erc"] = json.loads(ERC_SETTINGS.read_text())["erc"]
    (d / (PROJECT + ".kicad_pro")).write_text(json.dumps(pro, indent=2))
    (d / "config").mkdir()
    os.symlink(ROOT / "lib", d / "lib")
    return d


def hygiene(net):
    print("--- files")
    for f in SHEET_FILES:
        text = (ROOT / f).read_text()
        m = re.search(r"\(version (\d+)\)\s*\(generator \"eeschema\"\)\s*\(generator_version \"([\d.]+)\"\)", text)
        check(bool(m) and m.group(2) == "10.0", "%s is a KiCad 10 file" % f, m.group(0) if m else "no header")
        check(not BRANDING.search(text), "%s has no vendor branding, License or Author fields" % f,
              str(BRANDING.findall(text)[:3]))
        check('(title "%s")' % TITLE in text, "%s title block: %s" % (f, TITLE))
        check(not re.search(r"\(company \"[^\"]+\"\)", text), "%s title block has no company" % f)
        libs = set(re.findall(r'\(lib_id "([^":]+):', text))
        check(libs <= {LIB}, "%s uses only the project symbol library" % f, str(sorted(libs)))
        check('(text ""' not in text, "%s has no empty text item" % f)
        # every symbol origin, wire end, label and junction on the 1.27 mm grid
        off = []
        for kind in ("symbol", "global_label", "junction", "no_connect"):
            for m in re.finditer(r"\n\t\(%s\b[^\n]*(?:\n\t\t\(lib_id [^\n]*)?(?:\n\t\t\(shape [^\n]*)?\n\t\t\(at ([-\d.]+) ([-\d.]+)" % kind,
                                 text):
                for v in m.groups():
                    if abs(float(v) / 1.27 - round(float(v) / 1.27)) > 1e-4:
                        off.append((kind, m.groups()))
        for m in re.finditer(r"\n\t\(wire\s+\(pts\s+\(xy ([-\d.]+) ([-\d.]+)\) \(xy ([-\d.]+) ([-\d.]+)\)", text):
            for v in m.groups():
                if abs(float(v) / 1.27 - round(float(v) / 1.27)) > 1e-4:
                    off.append(("wire", m.groups()))
        check(not off, "%s: symbols, wires, labels, junctions on the 1.27 mm grid" % f, str(off[:3]))
    for f in ("lib/lpddr5_testbed.kicad_sym", "sym-lib-table", "fp-lib-table"):
        text = (ROOT / f).read_text()
        check(not BRANDING.search(text), "%s has no vendor branding" % f)
    libtext = (ROOT / "lib/lpddr5_testbed.kicad_sym").read_text()
    check("(generator_version \"10.0\")" in libtext, "lib/lpddr5_testbed.kicad_sym is a KiCad 10 library")
    rows = re.findall(r'\(lib \(name "([^"]+)"\)\(type "[^"]+"\)\(uri "([^"]+)"\)',
                      (ROOT / "sym-lib-table").read_text())
    check(rows == [(LIB, "${KIPRJMOD}/lib/%s.kicad_sym" % LIB)],
          "sym-lib-table has one row, the project library, relative to the project", str(rows))
    # every part's symbol is in the project library with exactly the part's pin numbers
    syms = {str(s[1]): SymDef(LIB, str(s[1]), s) for s in find_all(parse(libtext), "symbol")}
    bad = []
    for p in net["parts"]:
        lib, name = p["symbol"].split(":")
        if lib != LIB or name not in syms:
            bad.append("%s: %s is not in the project library" % (p["ref"], p["symbol"]))
        elif set(syms[name].pins) != set(p["pins"]):
            bad.append("%s: pins of %s differ from the part's pins" % (p["ref"], p["symbol"]))
    check(not bad, "every part's symbol is in the project library with the part's pin numbers (%d symbols used)"
          % len({p["symbol"] for p in net["parts"]}), "; ".join(bad[:5]))
    # a global label that appears once names a net whose pins are all on one sheet
    labels = collections.Counter()
    for f in SHEET_FILES:
        labels.update(re.findall(r'\(global_label "([^"]+)"', (ROOT / f).read_text()))
    sheets_of = collections.defaultdict(set)
    for p in net["parts"]:
        for n in p["pins"].values():
            if n:
                sheets_of[n].add(p["sheet"])
    once = sorted(n for n, k in labels.items() if k == 1)
    wrong = [n for n in once if len(sheets_of.get(n, ())) != 1]
    check(not wrong, "the %d global labels that appear once (%s) name single-sheet nets" % (len(once), ", ".join(once)),
          str(wrong))
    left = [f for f in OBSOLETE if (ROOT / f).exists()]
    check(not left, "obsolete upstream sheets are gone", str(left))
    for p in net["parts"]:
        lib, name = p["footprint"].split(":")
        d = ROOT / "lib" / (lib + ".pretty") if lib == "lpddr5_testbed" else \
            Path("/usr/share/kicad/footprints") / (lib + ".pretty")
        if not (d / (name + ".kicad_mod")).exists():
            check(False, "footprint %s of %s exists" % (p["footprint"], p["ref"]))
    check(True, "every footprint lib:name of the %d parts exists" % len(net["parts"]))


def run_erc(d):
    """-> (errors, warnings, exclusions, ignored rule keys); each violation as (sheet, type, text)."""
    kicad(["sch", "erc", "--severity-all", "--format", "json", "-o", "erc.json", SHEET_FILES[0]], d)
    kicad(["sch", "erc", "--severity-all", "-o", "erc.rpt", SHEET_FILES[0]], d)
    rep = json.loads((d / "erc.json").read_text())
    out = {"error": [], "warning": [], "exclusion": []}
    for s in rep.get("sheets", []):
        for v in s.get("violations", []):
            items = "; ".join(i.get("description", "") for i in v.get("items", []))
            kind = "exclusion" if v.get("excluded") else v["severity"]
            out.setdefault(kind, []).append((s.get("path", "?"), v["type"], "%s | %s" % (v.get("description", ""), items)))
    return out, {c["key"] for c in rep.get("ignored_checks", [])}


def erc(d):
    settings = json.loads(ERC_SETTINGS.read_text())["erc"]
    print("--- ERC (kicad-cli sch erc --severity-all, settings of design/erc_settings.json)")
    out, ignored = run_erc(d)
    print("ERC: %d errors, %d warnings, %d exclusions" % (len(out["error"]), len(out["warning"]), len(out["exclusion"])))
    for kind in ("error", "warning", "exclusion"):
        for sheet, typ, text in out[kind]:
            print("   %s [%s] %s: %s" % (sheet, kind, typ, text))
    want_ignored = {k for k, v in settings["rule_severities"].items() if v == "ignore"}
    check(ignored == want_ignored, "ERC ran with the severities of design/erc_settings.json (ignored: %s)"
          % ", ".join(sorted(want_ignored)), "KiCad ignored %s" % sorted(ignored))
    check(not out["error"], "ERC: 0 errors", "%d errors" % len(out["error"]))
    check(not out["warning"], "ERC: 0 warnings", "%d warnings" % len(out["warning"]))
    # exclusions: exactly the bidirectional DMI balls strapped to VSS (design/DESIGN.md section 5).  An
    # exclusion that matches no violation leaves that violation as a warning, which fails the check above
    ok = [e for e in out["exclusion"] if e[1] == "pin_to_pin" and re.search(r"Symbol U1 Pin \w+ \[DMI[01]_A,", e[2])
          and "Power output" in e[2]]
    check(len(ok) == len(out["exclusion"]) == len(settings["erc_exclusions"]) == 2,
          "ERC: the 2 exclusions are the two strapped DMI balls, and both match a violation",
          str(out["exclusion"]))
    rpt = (d / "erc.rpt").read_text()
    # for information: the same run with the repository's own .kicad_pro (not owned by the schematic scripts)
    pro = json.loads((ROOT / (PROJECT + ".kicad_pro")).read_text())
    d2 = mirror(own_erc=False)
    try:
        o2, _ = run_erc(d2)
    finally:
        shutil.rmtree(d2, ignore_errors=True)
    same = pro.get("erc") == settings
    print("NOTE  %s.kicad_pro as it is: %d errors, %d warnings, %d exclusions; its \"erc\" section %s "
          "design/erc_settings.json" % (PROJECT, len(o2["error"]), len(o2["warning"]), len(o2["exclusion"]),
                                        "equals" if same else "is missing or differs from"))
    return rpt


def parity(d, net):
    print("--- netlist parity (kicad-cli sch export netlist vs design/netlist.json)")
    kicad(["sch", "export", "netlist", "--format", "kicadsexpr", "-o", "export.net", SHEET_FILES[0]], d)
    tree = parse((d / "export.net").read_text())
    comps = {}
    for c in find_all(find(tree, "components"), "comp"):
        ref = str(find(c, "ref")[1])
        fields = {}
        fnode = find(c, "fields")
        for f in find_all(fnode, "field") if fnode else []:
            fields[str(find(f, "name")[1])] = str(f[2]) if len(f) > 2 else ""
        props = {str(find(p, "name")[1]) for p in find_all(c, "property")}
        sheet = str(find(find(c, "sheetpath"), "names")[1])
        fp = find(c, "footprint")
        src = find(c, "libsource")
        descr = find(c, "description")
        comps[ref] = dict(value=str(find(c, "value")[1]), footprint=str(fp[1]) if fp and len(fp) > 1 else "",
                          fields=fields, props=props, sheet=sheet,
                          symbol="%s:%s" % (find(src, "lib")[1], find(src, "part")[1]),
                          description=str(descr[1]) if descr and len(descr) > 1 else "")
    kpin = {}
    knets = collections.defaultdict(list)
    for n in find_all(find(tree, "nets"), "net"):
        name = str(find(n, "name")[1])
        for node in find_all(n, "node"):
            key = (str(find(node, "ref")[1]), str(find(node, "pin")[1]))
            assert key not in kpin, "pin %s.%s in two nets" % key
            kpin[key] = name
            knets[name].append(key)

    parts = {p["ref"]: p for p in net["parts"]}
    diffs = []
    check(set(comps) == set(parts), "identical set of %d reference designators" % len(parts),
          "only in schematic %s, only in netlist.json %s" % (sorted(set(comps) - set(parts))[:10],
                                                             sorted(set(parts) - set(comps))[:10]))
    nattr = 0
    for ref in sorted(set(comps) & set(parts)):
        c, p = comps[ref], parts[ref]
        for what, a, b in (("value", c["value"], p["value"]), ("footprint", c["footprint"], p["footprint"]),
                           ("symbol", c["symbol"], p["symbol"]), ("description", c["description"], p["description"]),
                           ("MPN", c["fields"].get("MPN"), p["mpn"]),
                           ("Manufacturer", c["fields"].get("Manufacturer"), p["manufacturer"]),
                           ("dnp", "dnp" in c["props"], p["dnp"]),
                           ("exclude_from_bom", "exclude_from_bom" in c["props"], not p["in_bom"]),
                           ("sheet", c["sheet"].strip("/").lower(), p["sheet"])):
            nattr += 1
            if a != b:
                diffs.append("%s %s: schematic %r, netlist.json %r" % (ref, what, a, b))
    check(not diffs, "identical value / footprint / symbol / description / MPN / manufacturer / DNP / BOM flag / "
          "sheet (%d comparisons)"
          % nattr, "; ".join(diffs[:8]))

    jpin = {(p["ref"], pin): n for p in net["parts"] for pin, n in p["pins"].items()}
    check(set(jpin) == set(kpin), "identical set of %d ref.pin" % len(jpin),
          "only in schematic %s, only in netlist.json %s" % (sorted(set(kpin) - set(jpin))[:10],
                                                             sorted(set(jpin) - set(kpin))[:10]))
    ndiff = []
    for key in sorted(set(jpin) & set(kpin)):
        want, got = jpin[key], kpin[key]
        if want == "":
            if not got.startswith("unconnected-"):
                ndiff.append("%s.%s: open in netlist.json, net %s in the schematic" % (key + (got,)))
        elif want != got:
            ndiff.append("%s.%s: netlist.json %s, schematic %s" % (key + (want, got)))
    check(not ndiff, "every ref.pin on the same net (%d pins, %d named nets)"
          % (len(jpin), len({n for n in jpin.values() if n})), "; ".join(ndiff[:8]))
    k_open = {k for k, n in kpin.items() if n.startswith("unconnected-")}
    j_open = {(x["ref"], str(x["pin"])) for x in net["no_connect"]}
    check(k_open == j_open == {k for k, n in jpin.items() if n == ""},
          "identical set of %d unconnected pins (no_connect list)" % len(j_open),
          "schematic only %s, netlist.json only %s" % (sorted(k_open - j_open)[:8], sorted(j_open - k_open)[:8]))
    named_k = {n for n in knets if not n.startswith("unconnected-")}
    named_j = {n for n in jpin.values() if n}
    check(named_k == named_j, "identical set of %d net names" % len(named_j),
          "schematic only %s, netlist.json only %s" % (sorted(named_k - named_j)[:8], sorted(named_j - named_k)[:8]))
    # no-connect flags: every open pin except hidden pins of type "not connected" carries a flag
    flags = set()
    pinpos = {}
    for f in SHEET_FILES[1:]:
        text = (ROOT / f).read_text()
        flags |= {(f, m.group(1), m.group(2)) for m in re.finditer(r"\(no_connect\s+\(at ([-\d.]+) ([-\d.]+)\)", text)}
        pinpos[f] = text
    n_flags = len(flags)
    hidden_nc = [k for k in j_open if k[0] == "U3"]
    check(n_flags == len(j_open) - len(hidden_nc),
          "%d no-connect flags = %d open pins - %d hidden 'not connected' pin (U3.4)"
          % (n_flags, len(j_open), len(hidden_nc)))
    total = len(diffs) + len(ndiff)
    print("netlist parity: %d differences" % total)
    return total


def export(d, pdf, png, dpi):
    kicad(["sch", "export", "pdf", "-o", "out.pdf", SHEET_FILES[0]], d)
    if pdf:
        # KiCad's PDF is not byte-reproducible (creation date, object ids).  The file in the repository is replaced only when its TEXT differs from
        # the fresh export, so that a rebuild of an unchanged design leaves it alone and a stale PDF is always replaced
        def text_of(f):
            return subprocess.run(["pdftotext", "-layout", str(f), "-"], check=True, capture_output=True, text=True).stdout
        Path(pdf).parent.mkdir(parents=True, exist_ok=True)
        if Path(pdf).exists() and text_of(pdf) == text_of(d / "out.pdf"):
            print("%s is current (same text as a fresh export)" % pdf)
        else:
            shutil.copyfile(d / "out.pdf", pdf)
            print("wrote %s" % pdf)
    if png:
        Path(png).mkdir(parents=True, exist_ok=True)
        subprocess.run(["pdftoppm", "-r", str(dpi), "-png", str(d / "out.pdf"), str(d / "page")], check=True)
        names = ["1_root", "2_connector", "3_lpddr5", "4_command", "5_write", "6_read", "7_power"]
        pages = sorted(d.glob("page-*.png"))
        assert len(pages) == len(names), pages
        for src, n in zip(pages, names):
            shutil.copyfile(src, Path(png) / (n + ".png"))
            print("wrote %s" % (Path(png) / (n + ".png")))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf")
    ap.add_argument("--png")
    ap.add_argument("--dpi", type=int, default=200)
    ap.add_argument("--erc-report", help="copy the plain-text ERC report here")
    a = ap.parse_args()
    net = json.loads((ROOT / "design" / "netlist.json").read_text())
    hygiene(net)
    d = mirror()
    try:
        rpt = erc(d)
        if a.erc_report:
            Path(a.erc_report).write_text(rpt)
        parity(d, net)
        if a.pdf or a.png:
            export(d, a.pdf, a.png, a.dpi)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    print("\n%d checks passed, %d failed" % (npass, len(fails)))
    for f in fails:
        print("   FAILED: " + f)
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()

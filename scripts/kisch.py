#!/usr/bin/env python3
"""Small KiCad schematic writer used by gen_schematic.py.

Plain Python, no KiCad import.  It writes S-expressions in the KiCad 8 file syntax; gen_schematic.py then
lets `kicad-cli sch upgrade` (KiCad 10) re-save every file, so what lands in the repository is KiCad 10's
own serialisation.

Coordinates: schematic sheets are in mm, y down, everything on the 1.27 mm grid.
Symbol libraries are y up.  A symbol instance at (x, y) with rotation r (0/90/180/270, counter-clockwise
on screen) puts a library point (px, py) at
    r=0: (x+px, y-py)   r=90: (x-py, y-px)   r=180: (x-px, y+py)   r=270: (x+py, y+px)
"""
import os
import re
import uuid

GRID = 1.27
NS = uuid.UUID("5c1d5ad0-3e55-4f0b-9d53-6c70647264e1")      # namespace of all generated UUIDs


def uid(key):
    """Deterministic UUID: the same key gives the same UUID on every run."""
    return str(uuid.uuid5(NS, key))


class Q(str):
    """A quoted S-expression string (plain str = bare token)."""


def parse(text):
    """S-expression text -> nested lists; quoted strings become Q, everything else stays a bare str."""
    pos = 0
    n = len(text)
    stack = [[]]
    while pos < n:
        c = text[pos]
        if c in " \t\r\n":
            pos += 1
        elif c == "(":
            stack.append([])
            pos += 1
        elif c == ")":
            done = stack.pop()
            stack[-1].append(done)
            pos += 1
        elif c == '"':
            pos += 1
            buf = []
            while text[pos] != '"':
                if text[pos] == "\\":
                    pos += 1
                    buf.append({"n": "\n", "t": "\t"}.get(text[pos], text[pos]))
                else:
                    buf.append(text[pos])
                pos += 1
            pos += 1
            stack[-1].append(Q("".join(buf)))
        else:
            m = re.compile(r'[^\s()"]+').match(text, pos)
            stack[-1].append(m.group(0))
            pos = m.end()
    assert len(stack) == 1, "unbalanced S-expression"
    return stack[0][0]


def num(v):
    """Format a coordinate: at most 4 decimals, no trailing zeros, never '-0'."""
    s = ("%.4f" % v).rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def atom(a):
    if isinstance(a, Q):
        return '"' + a.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'
    if isinstance(a, bool):
        return "yes" if a else "no"
    if isinstance(a, (int, float)):
        return num(a)
    return str(a)


def dump(node, ind=0):
    """Nested lists -> text.  Lists without sub-lists stay on one line."""
    if not isinstance(node, list):
        return atom(node)
    if not any(isinstance(c, list) for c in node):
        return "(" + " ".join(atom(c) for c in node) + ")"
    head = []
    rest = list(node)
    while rest and not isinstance(rest[0], list):
        head.append(atom(rest.pop(0)))
    out = "(" + " ".join(head)
    for c in rest:
        out += "\n" + "\t" * (ind + 1) + dump(c, ind + 1)
    return out + "\n" + "\t" * ind + ")"


def find(node, key):
    for c in node:
        if isinstance(c, list) and c and c[0] == key:
            return c
    return None


def find_all(node, key):
    return [c for c in node if isinstance(c, list) and c and c[0] == key]


def snap(v):
    return round(round(v / GRID) * GRID, 4)


def on_grid(v):
    return abs(v / GRID - round(v / GRID)) < 1e-6


def xform(rot, px, py):
    """Library offset (y up) -> sheet offset (y down) for a symbol rotated by rot."""
    return {0: (px, -py), 90: (-py, -px), 180: (-px, py), 270: (py, px)}[rot]


# ---------------------------------------------------------------------------------------------
# symbol definitions
# ---------------------------------------------------------------------------------------------
def _block(text, name):
    """Text of the top-level (symbol "name" ...) block of a .kicad_sym file."""
    i = text.find('\n\t(symbol "%s"' % name)
    if i < 0:
        raise KeyError(name)
    i += 2
    depth = 0
    j = i
    while True:
        c = text[j]
        if c == '"':
            j += 1
            while text[j] != '"':
                if text[j] == "\\":
                    j += 1
                j += 1
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                break
        j += 1
    return text[i:j + 1]


class SymDef:
    """One library symbol, flattened (no `extends`), with its pins indexed by number."""

    def __init__(self, lib, name, node):
        self.lib, self.name = lib, name
        self.lib_id = "%s:%s" % (lib, name)
        self.node = node                              # (symbol "name" ...), name without the library
        self.power = find(node, "power") is not None
        self.pins = {}                                # number -> dict
        self.nunits = 1
        for sub in find_all(node, "symbol"):
            m = re.match(r".*_(\d+)_(\d+)$", sub[1])
            unit = int(m.group(1))
            self.nunits = max(self.nunits, unit)
            for p in find_all(sub, "pin"):
                at = find(p, "at")
                number = str(find(p, "number")[1])
                assert number not in self.pins, "duplicate pin %s in %s" % (number, name)
                self.pins[number] = dict(
                    x=float(at[1]), y=float(at[2]), angle=int(float(at[3])),
                    length=float(find(p, "length")[1]), name=str(find(p, "name")[1]),
                    etype=p[1], hidden="hide" in p or find(p, "hide") is not None, unit=unit)
        self.props = {str(p[1]): str(p[2]) for p in find_all(node, "property")}

    def embedded(self):
        """Copy for a schematic's lib_symbols section: top-level name is the full lib_id."""
        n = list(self.node)
        n[1] = Q(self.lib_id)
        return n

    def unit_pins(self, unit):
        return {k: v for k, v in self.pins.items() if v["unit"] in (0, unit)}


_lib_text = {}


def stock_library_dir():
    """Directory of the installed KiCad symbol libraries (only read when the schematic is generated)."""
    for var in ("KICAD10_SYMBOL_DIR", "KICAD_SYMBOL_DIR"):
        if os.environ.get(var):
            return os.environ[var]
    return "/usr/share/kicad/symbols"


def _stock_text(lib, name, libdir):
    """Text that holds (symbol "name" ...) of a stock library.  The library is either one file
    <lib>.kicad_sym or a directory <lib>.kicad_symdir with one file per symbol."""
    one = os.path.join(libdir, lib + ".kicad_sym")
    key = (libdir, lib, None if os.path.isfile(one) else name)
    if key not in _lib_text:
        path = one if os.path.isfile(one) else os.path.join(libdir, lib + ".kicad_symdir", name + ".kicad_sym")
        with open(path) as f:
            _lib_text[key] = f.read()
    return _lib_text[key]


def stock_symbol(lib, name, libdir=None):
    """Load lib:name from an installed KiCad library and flatten a derived symbol into its parent."""
    libdir = libdir or stock_library_dir()
    node = parse(_block(_stock_text(lib, name, libdir), name))
    ext = find(node, "extends")
    if ext is not None:
        parent = parse(_block(_stock_text(lib, str(ext[1]), libdir), str(ext[1])))
        pname = str(parent[1])
        assert find(parent, "extends") is None, "two-level inheritance is not handled"
        child_props = {str(p[1]): p for p in find_all(node, "property")}
        flat = []
        for c in parent:
            if isinstance(c, list) and c[0] == "property":
                flat.append(child_props.pop(str(c[1]), c))
            elif isinstance(c, list) and c[0] == "symbol":
                sub = list(c)
                sub[1] = Q(name + str(sub[1])[len(pname):])
                flat.append(sub)
            else:
                flat.append(c)
        first_sub = next(i for i, c in enumerate(flat) if isinstance(c, list) and c[0] == "symbol")
        for p in child_props.values():                # properties only the child has
            flat.insert(first_sub, p)
            first_sub += 1
        flat[1] = Q(name)
        node = flat
    return SymDef(lib, name, node)


def fx(size=1.27, justify=None, hide=False, bold=False):
    font = ["font", ["size", size, size]]
    if bold:
        font.append(["bold", "yes"])
    e = ["effects", font]
    if justify:
        e.append(["justify"] + justify.split())
    if hide:
        e.append(["hide", "yes"])
    return e


def lib_property(name, value, x=0, y=0, angle=0, hide=False, justify=None):
    return ["property", Q(name), Q(value), ["at", x, y, angle], fx(hide=hide, justify=justify)]


# ---------------------------------------------------------------------------------------------
# sheets
# ---------------------------------------------------------------------------------------------
class Inst:
    """A placed symbol unit."""

    def __init__(self, sheet, sym, ref, x, y, rot, unit):
        self.sheet, self.sym, self.ref, self.x, self.y, self.rot, self.unit = sheet, sym, ref, x, y, rot, unit

    def pin(self, number):
        p = self.sym.pins[str(number)]
        dx, dy = xform(self.rot, p["x"], p["y"])
        return (round(self.x + dx, 4), round(self.y + dy, 4))

    def out(self, number):
        """Unit vector on the sheet pointing away from the body at this pin."""
        p = self.sym.pins[str(number)]
        v = {0: (-1, 0), 180: (1, 0), 90: (0, -1), 270: (0, 1)}[p["angle"]]
        dx, dy = xform(self.rot, *v)
        return (int(round(dx)), int(round(dy)))


class Sheet:
    def __init__(self, project, name, filename, uuid_, path, page, title, paper="A3", date="", rev="",
                 comments=()):
        self.project, self.name, self.filename = project, name, filename
        self.uuid, self.path, self.page = uuid_, path, page
        self.title, self.paper, self.date, self.rev, self.comments = title, paper, date, rev, comments
        self.items = []
        self.symdefs = {}
        self.insts = []
        self.connected = set()        # (ref, pin) that got a wire, label, power symbol or NC flag
        self.wire_ends = []
        self._n = 0
        self._pwr = 0

    def _uid(self, kind):
        self._n += 1
        return Q(uid("%s/%s/%d" % (self.name, kind, self._n)))

    # --- graphics and connectivity -------------------------------------------------------------
    def wire(self, *pts):
        for a, b in zip(pts, pts[1:]):
            a = (round(a[0], 4), round(a[1], 4))
            b = (round(b[0], 4), round(b[1], 4))
            if a == b:
                continue
            assert a[0] == b[0] or a[1] == b[1], "diagonal wire %s %s on %s" % (a, b, self.name)
            for v in a + b:
                assert on_grid(v), "wire off grid %s %s on %s" % (a, b, self.name)
            self.items.append(["wire", ["pts", ["xy", a[0], a[1]], ["xy", b[0], b[1]]],
                               ["stroke", ["width", 0], ["type", "default"]], ["uuid", self._uid("wire")]])

    def junction(self, x, y):
        self.items.append(["junction", ["at", x, y], ["diameter", 0], ["color", 0, 0, 0, 0],
                           ["uuid", self._uid("junction")]])

    def nc(self, x, y):
        self.items.append(["no_connect", ["at", x, y], ["uuid", self._uid("nc")]])

    def glabel(self, net, x, y, direction, shape="passive"):
        """Global label whose text grows in `direction` ((1,0) right, (-1,0) left, (0,-1) up, (0,1) down)."""
        angle, just = {(1, 0): (0, "left"), (-1, 0): (180, "right"), (0, -1): (90, "left"),
                       (0, 1): (270, "right")}[tuple(direction)]
        assert on_grid(x) and on_grid(y), "label off grid"
        self.items.append(["global_label", Q(net), ["shape", shape], ["at", x, y, angle],
                           fx(justify=just), ["uuid", self._uid("glabel")],
                           ["property", Q("Intersheetrefs"), Q("${INTERSHEET_REFS}"), ["at", x, y, 0],
                            fx(justify=just, hide=True)]])

    def text(self, s, x, y, size=1.27, bold=False, justify="left bottom"):
        assert s.strip(), "empty text on %s" % self.name
        self.items.append(["text", Q(s), ["exclude_from_sim", "no"], ["at", x, y, 0],
                           fx(size=size, bold=bold, justify=None if justify == "center" else justify),
                           ["uuid", self._uid("text")]])

    def line(self, pts, style="dash", width=0):
        self.items.append(["polyline", ["pts"] + [["xy", p[0], p[1]] for p in pts],
                           ["stroke", ["width", width], ["type", style]], ["uuid", self._uid("line")]])

    def rect(self, x1, y1, x2, y2, style="dash", width=0):
        self.items.append(["rectangle", ["start", x1, y1], ["end", x2, y2],
                           ["stroke", ["width", width], ["type", style]], ["fill", ["type", "none"]],
                           ["uuid", self._uid("rect")]])

    # --- symbols -------------------------------------------------------------------------------
    def place(self, sym, ref, x, y, rot=0, unit=1, fields=None, value=None, footprint="", extra=None,
              in_bom=True, dnp=False, on_board=True, datasheet=None, description=None):
        """Place one unit.  fields: {"Reference": (x, y, angle, justify) or None (hidden), "Value": ...}
        with absolute sheet positions."""
        assert on_grid(x) and on_grid(y), "%s off grid" % ref
        self.symdefs[sym.lib_id] = sym
        fields = fields or {}
        value = sym.props.get("Value", sym.name) if value is None else value

        def prop(name, val, default_hidden):
            spec = fields.get(name)
            if spec is None:
                return ["property", Q(name), Q(val), ["at", x, y, 0], fx(hide=True)] if default_hidden else \
                       ["property", Q(name), Q(val), ["at", x, y - 2.54, 0], fx()]
            px, py, pa, just = spec
            return ["property", Q(name), Q(val), ["at", px, py, pa], fx(justify=just)]

        node = ["symbol", ["lib_id", Q(sym.lib_id)], ["at", x, y, rot], ["unit", unit],
                ["exclude_from_sim", "no"], ["in_bom", in_bom], ["on_board", on_board], ["dnp", dnp],
                ["uuid", Q(uid("sym/%s/%d" % (ref, unit)) if not ref.startswith("#") else
                           uid("%s/sym/%s" % (self.name, ref)))],
                prop("Reference", ref, "Reference" not in fields),
                prop("Value", value, "Value" not in fields),
                ["property", Q("Footprint"), Q(footprint), ["at", x, y, 0], fx(hide=True)],
                ["property", Q("Datasheet"), Q(datasheet if datasheet is not None else
                                               sym.props.get("Datasheet", "")), ["at", x, y, 0], fx(hide=True)],
                ["property", Q("Description"), Q(description if description is not None else
                                                 sym.props.get("Description", "")), ["at", x, y, 0],
                 fx(hide=True)]]
        for k, v in (extra or {}).items():
            node.append(["property", Q(k), Q(v), ["at", x, y, 0], fx(hide=True)])
        for number in sym.pins:                       # KiCad lists the pins of all units in every unit
            node.append(["pin", Q(number), ["uuid", Q(uid("pin/%s/%s/%d/%s" % (self.name, ref, unit, number)))]])
        node.append(["instances", ["project", Q(self.project),
                                   ["path", Q(self.path), ["reference", Q(ref)], ["unit", unit]]]])
        self.items.append(node)
        inst = Inst(self, sym, ref, x, y, rot, unit)
        self.insts.append(inst)
        return inst

    def next_pwr(self, prefix="#PWR"):
        self._pwr += 1
        return "%s%s%03d" % (prefix, self.page, self._pwr)

    # --- output --------------------------------------------------------------------------------
    def tree(self, extra_tail=()):
        tb = ["title_block", ["title", Q(self.title)]]
        if self.date:
            tb.append(["date", Q(self.date)])
        if self.rev:
            tb.append(["rev", Q(self.rev)])
        for i, c in enumerate(self.comments, 1):
            tb.append(["comment", i, Q(c)])
        libsyms = ["lib_symbols"] + [self.symdefs[k].embedded() for k in sorted(self.symdefs)]
        root = ["kicad_sch", ["version", 20231120], ["generator", Q("eeschema")],
                ["generator_version", Q("8.0")], ["uuid", Q(self.uuid)], ["paper", Q(self.paper)], tb, libsyms]
        order = {"junction": 0, "no_connect": 1, "wire": 2, "polyline": 3, "rectangle": 3, "text": 4,
                 "global_label": 5, "symbol": 6, "sheet": 7}
        root += sorted(self.items, key=lambda it: order[it[0]])
        root += list(extra_tail)
        return root

    def write(self, path, extra_tail=()):
        with open(path, "w") as f:
            f.write(dump(self.tree(extra_tail)) + "\n")

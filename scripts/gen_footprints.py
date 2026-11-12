#!/usr/bin/env python3
"""Generate the four project footprints (deterministic, no dependencies).

    python3 scripts/gen_footprints.py      # (re)writes lib/lpddr5_testbed.pretty/*.kicad_mod

Footprints
  DDR4_SODIMM_260_Edge             260-pin DDR4 SO-DIMM module edge + complete card outline
  BGA-315_12.4x15mm_P0.8x0.7mm     LPDDR5/LPDDR5X 315-ball FBGA (JEDEC MO-338A)
  MLF-6-1EP_2x2mm_P0.65mm_EP0.8x1.4mm   MIC47050YML package
  Fiducial_1mm_Mask2mm             assembly fiducial: 1.0 mm copper dot in a 2.0 mm mask opening

The module is also imported by later scripts for the card geometry
(finger_xy, finger_side, card_to_kicad, component_area, ball_xy, ...).

COORDINATES  (read this before using any number from this file)
---------------------------------------------------------------
CARD frame: millimetres, seen from the FRONT of the module (the face with
the odd fingers, pin 1 at the LEFT).  Origin = lower-left corner of the
69.6 x 30.0 mm card rectangle (pin-1 end of the finger edge).
  +x : along the finger edge, from pin 1 towards pin 259
  +y : away from the fingers, into the card (finger edge is y = 0, top edge y = 30)
Back-side items (even fingers, back components) use the SAME x: they are
described as seen THROUGH the card from the front (pin 2 is 0.25 mm to the
right of pin 1, not mirrored).

KiCad frame: x right, y DOWN.  The edge footprint has its origin on the
card origin and is placed on F.Cu at 0 degrees, so
  kicad_x = origin_x + card_x
  kicad_y = origin_y - card_y            (card_to_kicad / kicad_to_card)
i.e. inside the footprint file every y is the NEGATIVE of the card y, and
the card occupies footprint-local x 0..69.6, y -30..0.

Sources (files under references/; details and discrepancies in lib/README.md)
  [FOX]  socket/Foxconn_DDR4_SODIMM_socket.pdf sheet 2 "DDRIV SO DIMM MODULE BOARD LAYOUT"
         (reproduces JEDEC MO-310; the dimensioned source for everything below)
  [MIC]  sodimm_module/Micron_atf8c1gx64hz_DDR4_SODIMM_RevJ.pdf Fig. 3
  [PRE]  sodimm_module/antmicro_lpddr4-testbed/lpddr4-testbed.kicad_pcb (working precedent)
  [KIC]  KiCad Connector_PCBEdge:SODIMM-260_DDR4_H4.0-5.2_OrientationStd_Socket (TE 2309407)
  [Y42M] dram/Micron_Y42M_LPDDR5X_315b_441b_563b_RevF.pdf Fig. 11 / Fig. 14
  [JESD] jedec/JESD209-5C 16.3.2 (315-ball 2CHx32, MO-338A)
  [CSN]  micron/CSN-33 BGA Manufacturer's User Guide Rev G
"""
import math
import os
import uuid

# ----------------------------------------------------------------------------
# DDR4 SO-DIMM card geometry (card frame, mm)
# ----------------------------------------------------------------------------
CARD_W = 69.60            # [FOX] 69.60 +/-0.15 ; [MIC] 69.47..69.73
CARD_H = 30.00            # [FOX] 30.00 +/-0.15 ; [MIC] 29.87..30.13
CARD_T = 1.20             # [FOX] 1.20 +/-0.10 at the fingers ; [MIC] 1.1..1.3
CARD_T_TOL = 0.10
CORNER_CHAMFER = 0.60     # [MIC] "0.6 x 45deg (4X)"  ([FOX] draws square corners, [PRE] R0.75)

N_FINGERS = 260
FINGER_PITCH = 0.50       # [FOX] detail B 0.50 basic ; [MIC] 0.5 TYP (per face)
FINGER_W = 0.35           # [FOX] 0.35 +/-0.03 ; [MIC] 0.35 TYP
FINGER_L = 2.55           # [FOX] detail B 2.55 = pad length ; [MIC] 2.55 TYP
FINGER_Y0 = 0.15          # pad start above the card edge. [FOX] "0.30 MAX"; chosen 0.15
FINGER_Y1 = FINGER_Y0 + FINGER_L          # 2.70  top of the fingers
FINGER_YC = (FINGER_Y0 + FINGER_Y1) / 2   # 1.425 pad centre
X_PIN1 = 1.425            # [FOX] front: pin 1 centre from the left edge
X_PIN2 = 1.675            # [FOX] back : pin 2 centre from the SAME (left, front view) edge
X_PIN145 = X_PIN1 + 35.50 + 2.50   # 39.425 [FOX] 35.50 (1..143) + 2.50 (143..145)
X_PIN146 = X_PIN2 + 35.50 + 2.50   # 39.675 [FOX] back 35.50 (2..144) + 2.50 (144..146)

KEY_X = 38.30             # [MIC] 38.3 TYP ; [FOX] pin 143 + 1.375 = pin 144 + 1.125
KEY_W = 1.00              # [FOX] detail A 1.00 +/-0.05
KEY_DEPTH = 4.00          # [FOX] detail A 4.00 +/-0.10 to the top of the full radius
KEY_KEEPOUT_W = 4.00      # [FOX] detail A: 4.00 wide zone around the key ...
KEY_KEEPOUT_Y = 4.20      # ... free of components up to 4.20 MIN

NOTCH_Y0 = 18.00          # [FOX] 18.00 +/-0.10 (2X) ; [MIC] 18.0 TYP
NOTCH_H = 4.00            # [FOX] 4.00 +/-0.10 (2X) ; [MIC] 4.0 TYP
NOTCH_D = 1.75            # [FOX] 1.75 (2X) ; [MIC] 1.75 (2X)
NOTCH_R = 0.65            # [FOX] R0.65 ; [MIC] R0.65 (4X)  (inner corners)

HOLE_D = 1.80             # [FOX] dia 1.80 (2X) ; [MIC] 1.8 TYP (2X) ; NPTH
HOLE_X = 2.00             # [MIC] 2.0 TYP from the side ; [PRE] 2.0
HOLE_Y = 6.00             # [FOX] 6.00 basic ; [MIC] 6.0 TYP

# Component area, identical on both faces [FOX sheet 2 plan + side views]
COMP_Y_MIN = 4.00         # no component below 4.00 MIN (2X) from the finger edge
COMP_Y_FULL = 5.10        # full height allowed from 5.10 MIN (2X)
COMP_H_LOW = 0.80         # 0.80 MAX (2X) component height between 4.00 and 5.10
COMP_SIDE_LOW = 3.00      # 3.00 MIN (4X) from the card sides, from the bottom up to the latch notch
COMP_SIDE_HIGH = 2.00     # 2.00 MIN (4X) from the card sides above the latch notch
COMP_SIDE_Y_A = 21.35     # 3.00 margin up to here, 45 deg transition ...
COMP_SIDE_Y_B = 22.35     # ... to the 2.00 margin from here (scaled from [FOX], conservative)
MODULE_T_MAX = 3.70       # [FOX] 3.70 MAX ; [MIC] 3.7 MAX   (components + card + components)
# Per-face limit is not dimensioned; the side view is symmetric, so with the
# thickest card (1.30):  (3.70 - 1.30) / 2 = 1.20 mm per face.
COMP_H_MAX = (MODULE_T_MAX - (CARD_T + CARD_T_TOL)) / 2

EDGE_CHAMFER = 0.25       # [MIC] 0.25 x 45deg (2X) ; [FOX] "C 0.25X0.05 MAX 2X OPTIONAL"
MASK_TOP = FINGER_Y1 + 0.05   # mask-free strip from below the edge up to 2.75 = the top of the pads' own mask openings (finger top 2.70 + 0.05).
                              # It was 2.85: the lands of the finger vias (row y = 3.05, 0.40 mm) then ended exactly on the strip edge, with the
                              # hole wall 0.10 mm from open mask; now a 0.10 mm mask dam covers the land edge and the hole is 0.20 mm from the strip
PIN_TEXT_Y = FINGER_Y1 + 0.70 # centre of the pin-number texts (unchanged position)


def finger_side(n):
    """'F' (front, odd fingers) or 'B' (back, even fingers)."""
    _chk(n)
    return 'F' if n % 2 else 'B'


def finger_x(n):
    """Centre x of finger n in the card frame (front view, also for back fingers)."""
    _chk(n)
    if n % 2:
        return X_PIN1 + 0.25 * (n - 1) if n <= 143 else X_PIN145 + 0.25 * (n - 145)
    return X_PIN2 + 0.25 * (n - 2) if n <= 144 else X_PIN146 + 0.25 * (n - 146)


def finger_xy(n):
    """Centre (x, y) of finger pad n in the card frame."""
    return (round(finger_x(n), 4), FINGER_YC)


def finger_top_xy(n):
    """Point on the top end of finger n (where a trace leaves the pad), card frame."""
    return (round(finger_x(n), 4), FINGER_Y1)


def finger_layer(n):
    return 'F.Cu' if finger_side(n) == 'F' else 'B.Cu'


def card_to_kicad(x, y, origin=(0.0, 0.0)):
    """Card frame -> KiCad board mm, for the edge footprint placed at `origin`, 0 deg, front."""
    return (round(origin[0] + x, 6), round(origin[1] - y, 6))


def kicad_to_card(x, y, origin=(0.0, 0.0)):
    return (round(x - origin[0], 6), round(origin[1] - y, 6))


def component_area():
    """Polygon (card frame) inside which component BODIES may sit; same for both faces."""
    w, kl, kr = CARD_W, KEY_X - KEY_KEEPOUT_W / 2, KEY_X + KEY_KEEPOUT_W / 2
    return [(COMP_SIDE_LOW, COMP_Y_MIN), (kl, COMP_Y_MIN), (kl, KEY_KEEPOUT_Y), (kr, KEY_KEEPOUT_Y),
            (kr, COMP_Y_MIN), (w - COMP_SIDE_LOW, COMP_Y_MIN), (w - COMP_SIDE_LOW, COMP_SIDE_Y_A),
            (w - COMP_SIDE_HIGH, COMP_SIDE_Y_B), (w - COMP_SIDE_HIGH, CARD_H),
            (COMP_SIDE_HIGH, CARD_H), (COMP_SIDE_HIGH, COMP_SIDE_Y_B), (COMP_SIDE_LOW, COMP_SIDE_Y_A)]


def max_component_height(y):
    """Maximum component height (mm) for a body whose lowest edge is at card y."""
    if y < COMP_Y_MIN:
        return 0.0
    return COMP_H_LOW if y < COMP_Y_FULL else COMP_H_MAX


def _chk(n):
    if not (isinstance(n, int) and 1 <= n <= N_FINGERS):
        raise ValueError('finger number %r out of range 1..260' % (n,))


# ----------------------------------------------------------------------------
# 315-ball FBGA geometry  [Y42M Fig. 14], [JESD 16.3.2]
# ----------------------------------------------------------------------------
BGA_ROWS = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'J', 'K', 'L', 'M', 'N', 'P', 'R', 'T',
            'U', 'V', 'W', 'Y', 'AA']        # 21 rows, JEDEC letters (no I, O, Q, S, X, Z)
BGA_COLS = 15
BGA_PX = 0.80             # column pitch (x, the 12.4 mm side): 14 x 0.8 = 11.2 CTR
BGA_PY = 0.70             # row pitch (y, the 15.0 mm side):    20 x 0.7 = 14.0 CTR
BGA_BODY_X = 12.40        # +/-0.1
BGA_BODY_Y = 15.00        # +/-0.1
BGA_LAND = 0.41           # NSMD copper land (kept from the existing design; see README)
BGA_MASK_MARGIN = 0.05    # mask opening 0.51 mm
BGA_COURTYARD = 0.25


def ball_xy(name):
    """Ball centre in the FOOTPRINT frame (KiCad, y down, top view, origin = package centre).

    A1 is top-left at (-5.6, -7.0); columns 1..15 run +x, rows A..AA run +y."""
    i = 0
    while name[i].isalpha():
        i += 1
    r = BGA_ROWS.index(name[:i])
    c = int(name[i:])
    if not 1 <= c <= BGA_COLS:
        raise ValueError(name)
    return (round((c - (BGA_COLS + 1) / 2) * BGA_PX, 4), round((r - (len(BGA_ROWS) - 1) / 2) * BGA_PY, 4))


def ball_names():
    return ['%s%d' % (r, c) for r in BGA_ROWS for c in range(1, BGA_COLS + 1)]


# ----------------------------------------------------------------------------
# S-expression writer
# ----------------------------------------------------------------------------
def f(v):
    s = ('%.6f' % v).rstrip('0').rstrip('.')
    return '0' if s in ('-0', '') else s


class FP:
    def __init__(self, name, descr, tags, attr):
        self.name, self.n, self.items = name, 0, []
        self.head = ['(footprint "%s"' % name, '\t(version 20240108)', '\t(generator "gen_footprints")',
                     '\t(layer "F.Cu")', '\t(descr "%s")' % descr, '\t(tags "%s")' % tags]
        self.attr = attr

    def uu(self):
        self.n += 1
        return '(uuid "%s")' % uuid.uuid5(uuid.NAMESPACE_URL, 'lpddr5_testbed/%s/%d' % (self.name, self.n))

    def add(self, s):
        self.items.append('\t' + s)

    def prop(self, key, val, x, y, layer, hide=False, size=1.0):
        self.add('(property "%s" "%s" (at %s %s 0) (layer "%s")%s %s (effects (font (size %s %s) (thickness %s))))'
                 % (key, val, f(x), f(y), layer, ' (hide yes)' if hide else '', self.uu(), f(size), f(size), f(size * 0.15)))

    def line(self, a, b, layer, w):
        self.add('(fp_line (start %s %s) (end %s %s) (stroke (width %s) (type solid)) (layer "%s") %s)'
                 % (f(a[0]), f(a[1]), f(b[0]), f(b[1]), f(w), layer, self.uu()))

    def arc(self, a, m, b, layer, w):
        self.add('(fp_arc (start %s %s) (mid %s %s) (end %s %s) (stroke (width %s) (type solid)) (layer "%s") %s)'
                 % (f(a[0]), f(a[1]), f(m[0]), f(m[1]), f(b[0]), f(b[1]), f(w), layer, self.uu()))

    def circle(self, c, r, layer, w, fill=False):
        self.add('(fp_circle (center %s %s) (end %s %s) (stroke (width %s) (type solid)) (fill %s) (layer "%s") %s)'
                 % (f(c[0]), f(c[1]), f(c[0] + r), f(c[1]), f(w), 'solid' if fill else 'none', layer, self.uu()))

    def poly(self, pts, layer, w, fill=False):
        self.add('(fp_poly (pts %s) (stroke (width %s) (type solid)) (fill %s) (layer "%s") %s)'
                 % (' '.join('(xy %s %s)' % (f(x), f(y)) for x, y in pts), f(w), 'solid' if fill else 'none', layer, self.uu()))

    def text(self, s, x, y, layer, size=0.5, just=None, mirror=False):
        j = ''
        if just or mirror:
            j = ' (justify%s%s)' % (' ' + just if just else '', ' mirror' if mirror else '')
        self.add('(fp_text user "%s" (at %s %s 0) (layer "%s") %s (effects (font (size %s %s) (thickness %s))%s))'
                 % (s, f(x), f(y), layer, self.uu(), f(size), f(size), f(size * 0.15), j))

    def keepout(self, name, pts, layers):
        self.add('(zone (net 0) (net_name "") (layers %s) %s (name "%s") (hatch edge 0.5) (connect_pads (clearance 0)) '
                 '(min_thickness 0.25) (filled_areas_thickness no) '
                 '(keepout (tracks allowed) (vias allowed) (pads allowed) (copperpour allowed) (footprints not_allowed)) '
                 '(fill (thermal_gap 0.5) (thermal_bridge_width 0.5)) (polygon (pts %s)))'
                 % (' '.join('"%s"' % l for l in layers), self.uu(), name,
                    ' '.join('(xy %s %s)' % (f(x), f(y)) for x, y in pts)))

    def write(self, directory):
        path = os.path.join(directory, self.name + '.kicad_mod')
        with open(path, 'w') as fh:
            fh.write('\n'.join(self.head + self.items[:self.nprop] + ['\t(attr %s)' % self.attr]
                               + self.items[self.nprop:]) + '\n)\n')
        return path


# ----------------------------------------------------------------------------
# DDR4 SO-DIMM edge
# ----------------------------------------------------------------------------
def K(p):
    """card frame -> footprint-local KiCad coordinates."""
    return (p[0], -p[1])


def card_outline():
    """Closed outline as a list of ('L', p0, p1) / ('A', p0, mid, p1) in the card frame,
    counter-clockwise from the bottom-left chamfer."""
    w, h, c = CARD_W, CARD_H, CORNER_CHAMFER
    kl, kr, kr_ = KEY_X - KEY_W / 2, KEY_X + KEY_W / 2, KEY_W / 2
    n0, n1, nd, r = NOTCH_Y0, NOTCH_Y0 + NOTCH_H, NOTCH_D, NOTCH_R
    q = r * (1 - math.sqrt(0.5))
    o = [('L', (c, 0), (kl, 0)),
         ('L', (kl, 0), (kl, KEY_DEPTH - kr_)),
         ('A', (kl, KEY_DEPTH - kr_), (KEY_X, KEY_DEPTH), (kr, KEY_DEPTH - kr_)),
         ('L', (kr, KEY_DEPTH - kr_), (kr, 0)),
         ('L', (kr, 0), (w - c, 0)),
         ('L', (w - c, 0), (w, c)),
         ('L', (w, c), (w, n0)),
         ('L', (w, n0), (w - nd + r, n0)),
         ('A', (w - nd + r, n0), (w - nd + q, n0 + q), (w - nd, n0 + r)),
         ('L', (w - nd, n0 + r), (w - nd, n1 - r)),
         ('A', (w - nd, n1 - r), (w - nd + q, n1 - q), (w - nd + r, n1)),
         ('L', (w - nd + r, n1), (w, n1)),
         ('L', (w, n1), (w, h - c)),
         ('L', (w, h - c), (w - c, h)),
         ('L', (w - c, h), (c, h)),
         ('L', (c, h), (0, h - c)),
         ('L', (0, h - c), (0, n1)),
         ('L', (0, n1), (nd - r, n1)),
         ('A', (nd - r, n1), (nd - q, n1 - q), (nd, n1 - r)),
         ('L', (nd, n1 - r), (nd, n0 + r)),
         ('A', (nd, n0 + r), (nd - q, n0 + q), (nd - r, n0)),
         ('L', (nd - r, n0), (0, n0)),
         ('L', (0, n0), (0, c)),
         ('L', (0, c), (c, 0))]
    return o


def gen_edge():
    fp = FP('DDR4_SODIMM_260_Edge',
            'DDR4 SO-DIMM module card edge, 260 fingers (130 front odd + 130 back even), 0.5 mm pitch, '
            'complete 69.6 x 30.0 mm card outline with key slot and latch notches, JEDEC MO-310 '
            '(dimensions from the module board layout reproduced on socket drawing 317-0000-2253 sheet 2). '
            'Origin = lower-left card corner seen from the front (pin 1 end); card +y is KiCad -y.',
            'DDR4 SODIMM SO-DIMM 260 edge module MO-310',
            'exclude_from_pos_files exclude_from_bom allow_soldermask_bridges')
    fp.prop('Reference', 'REF**', CARD_W / 2, -3.4, 'F.SilkS', hide=True, size=0.8)
    fp.prop('Value', 'DDR4_SODIMM_260_Edge', CARD_W / 2, 1.2, 'F.Fab', size=0.7)
    fp.prop('Footprint', '', 0, 0, 'F.Fab', hide=True)
    fp.prop('Datasheet', '', 0, 0, 'F.Fab', hide=True)
    fp.prop('Description', 'DDR4 SO-DIMM 260-pin module edge', 0, 0, 'F.Fab', hide=True)
    fp.nprop = len(fp.items)

    # board outline
    for seg in card_outline():
        if seg[0] == 'L':
            fp.line(K(seg[1]), K(seg[2]), 'Edge.Cuts', 0.05)
        else:
            fp.arc(K(seg[1]), K(seg[2]), K(seg[3]), 'Edge.Cuts', 0.05)

    # solder-mask-free strip over the fingers, both faces (extends past the card edge)
    for layer in ('F.Mask', 'B.Mask'):
        fp.poly([K(p) for p in ((-0.3, -0.3), (CARD_W + 0.3, -0.3), (CARD_W + 0.3, MASK_TOP), (-0.3, MASK_TOP))],
                layer, 0, fill=True)

    # component area / keep-out drawing (same on both faces) + notes
    area = component_area()
    fp.poly([K(p) for p in area], 'Dwgs.User', 0.1)
    fp.line(K((COMP_SIDE_LOW, COMP_Y_FULL)), K((CARD_W - COMP_SIDE_LOW, COMP_Y_FULL)), 'Dwgs.User', 0.05)
    fp.text('COMPONENT AREA (both faces) inside this line; h <= %.2f mm' % COMP_H_MAX, 5.0, -(COMP_Y_FULL + 0.9),
            'Dwgs.User', 0.5, 'left')
    fp.text('y 4.00..5.10: h <= 0.80 mm', 5.0, -(COMP_Y_MIN + 0.55), 'Dwgs.User', 0.4, 'left')
    notes = [
        'DDR4 SO-DIMM 260 (JEDEC MO-310) 69.60 x 30.00 +/-0.15 mm. Front view, pin 1 left. Odd fingers F.Cu, even B.Cu.',
        'THICKNESS AT FINGERS 1.20 +/-0.10 mm INCLUDING FAB TOLERANCE. Module total 3.70 mm max.',
        'FINGER EDGE: chamfer 0.25 mm x 45 deg MAX on each face, or none. NO 20/30/45 deg gold-finger bevel.',
        'Finger copper must stay within 0.30 mm of the card edge. Fingers: hard gold over nickel, no solder mask.',
        'Key slot 1.00 +/-0.05 wide, 4.00 +/-0.10 deep, full radius, centre 38.30 from the pin-1 side.',
        'Latch notches 1.75 deep, 4.00 +/-0.10 high, 18.00 +/-0.10 above the finger edge, inner R0.65. NPTH 1.80 x2.',
    ]
    for i, s in enumerate(notes):
        fp.text(s, 0, 2.6 + 0.9 * i, 'Cmts.User', 0.5, 'left')

    # enforceable keep-out (footprints not allowed) outside the component area
    w, h = CARD_W, CARD_H
    kl, kr = KEY_X - KEY_KEEPOUT_W / 2, KEY_X + KEY_KEEPOUT_W / 2
    fp.keepout('SODIMM_no_components_fingers',
               [K(p) for p in ((0, 0), (w, 0), (w, COMP_Y_MIN), (kr, COMP_Y_MIN), (kr, KEY_KEEPOUT_Y),
                               (kl, KEY_KEEPOUT_Y), (kl, COMP_Y_MIN), (0, COMP_Y_MIN))], ('F.Cu', 'B.Cu'))
    fp.keepout('SODIMM_no_components_left',
               [K(p) for p in ((0, COMP_Y_MIN), (COMP_SIDE_LOW, COMP_Y_MIN), (COMP_SIDE_LOW, COMP_SIDE_Y_A),
                               (COMP_SIDE_HIGH, COMP_SIDE_Y_B), (COMP_SIDE_HIGH, h), (0, h))], ('F.Cu', 'B.Cu'))
    fp.keepout('SODIMM_no_components_right',
               [K(p) for p in ((w, COMP_Y_MIN), (w - COMP_SIDE_LOW, COMP_Y_MIN), (w - COMP_SIDE_LOW, COMP_SIDE_Y_A),
                               (w - COMP_SIDE_HIGH, COMP_SIDE_Y_B), (w - COMP_SIDE_HIGH, h), (w, h))], ('F.Cu', 'B.Cu'))

    # pin markers (silk, above the mask-free strip)
    for n, layer, mirror in ((1, 'F.SilkS', False), (259, 'F.SilkS', False), (2, 'B.SilkS', True), (260, 'B.SilkS', True)):
        x = finger_x(n) + (2.2 if n < 100 else -2.6)
        fp.text(str(n), x, -PIN_TEXT_Y, layer, 0.8, None, mirror)
    for n in (1, 143, 145, 259, 2, 144, 146, 260):
        fp.text(str(n), finger_x(n), 0.5 if n % 2 else 1.0, 'F.Fab' if n % 2 else 'B.Fab', 0.3, None, n % 2 == 0)

    # tooling holes
    for x in (HOLE_X, CARD_W - HOLE_X):
        fp.add('(pad "" np_thru_hole circle (at %s %s) (size %s %s) (drill %s) (layers "*.Cu" "*.Mask") %s)'
               % (f(x), f(-HOLE_Y), f(HOLE_D), f(HOLE_D), f(HOLE_D), fp.uu()))
    # fingers
    for n in range(1, N_FINGERS + 1):
        x, y = finger_xy(n)
        side = finger_side(n)
        fp.add('(pad "%d" smd rect (at %s %s) (size %s %s) (layers "%s.Cu" "%s.Mask") (solder_mask_margin 0) %s)'
               % (n, f(x), f(-y), f(FINGER_W), f(FINGER_L), side, side, fp.uu()))
    return fp


# ----------------------------------------------------------------------------
# BGA-315
# ----------------------------------------------------------------------------
def gen_bga():
    fp = FP('BGA-315_12.4x15mm_P0.8x0.7mm',
            'LPDDR5/LPDDR5X 315-ball FBGA, 12.4 x 15.0 mm, 15 columns x 21 rows, 0.8 mm (x) x 0.7 mm (y) pitch, '
            'JEDEC MO-338A, JESD209-5 315-ball 2CHx32 discrete package; %.2f mm NSMD lands' % BGA_LAND,
            'BGA 315 0.8 0.7 LPDDR5 FBGA MO-338', 'smd')
    hx, hy = BGA_BODY_X / 2, BGA_BODY_Y / 2
    fp.prop('Reference', 'REF**', 0, -hy - 1.1, 'F.SilkS', size=0.8)
    fp.prop('Value', 'BGA-315_12.4x15mm_P0.8x0.7mm', 0, hy + 1.1, 'F.Fab', size=0.7)
    fp.prop('Footprint', '', 0, 0, 'F.Fab', hide=True)
    fp.prop('Datasheet', '', 0, 0, 'F.Fab', hide=True)
    fp.prop('Description', 'LPDDR5 315-ball FBGA', 0, 0, 'F.Fab', hide=True)
    fp.nprop = len(fp.items)
    # fab outline with A1 chamfer
    ch = 0.6
    pts = [(-hx + ch, -hy), (hx, -hy), (hx, hy), (-hx, hy), (-hx, -hy + ch)]
    fp.poly(pts, 'F.Fab', 0.1)
    fp.text('${REFERENCE}', 0, 0, 'F.Fab', 1.0)
    # silk: corner marks + A1 triangle outside the body
    s, l = 0.12, 1.5
    sx, sy = hx + s, hy + s
    for cx, cy in ((1, -1), (1, 1), (-1, 1)):
        fp.line((cx * sx, cy * sy), (cx * (sx - l), cy * sy), 'F.SilkS', 0.12)
        fp.line((cx * sx, cy * sy), (cx * sx, cy * (sy - l)), 'F.SilkS', 0.12)
    # A1 corner: on this card the byte-0 trunks leave the package round this corner on F.Cu (DQ2 0.25 mm outside the row-A
    # edge, DQ1 0.075 mm outside the column-1 edge), so the corner carries only the mark along the row-A edge, and the A1
    # triangle sits beside the column-1 edge at row B / C, pointing at the corner, where there is no copper under it
    fp.line((-sx + 0.6, -sy), (-sx + l, -sy), 'F.SilkS', 0.12)
    tx, ty = -sx - 0.30, -sy + 1.07
    fp.poly([(tx, ty + 0.45), (tx - 0.45, ty + 0.45), (tx, ty)], 'F.SilkS', 0.12, fill=True)
    # courtyard
    c = BGA_COURTYARD
    fp.poly([(-hx - c, -hy - c), (hx + c, -hy - c), (hx + c, hy + c), (-hx - c, hy + c)], 'F.CrtYd', 0.05)
    for name in ball_names():
        x, y = ball_xy(name)
        fp.add('(pad "%s" smd circle (at %s %s) (size %s %s) (property pad_prop_bga) (layers "F.Cu" "F.Paste" "F.Mask") '
               '(solder_mask_margin %s) %s)' % (name, f(x), f(y), f(BGA_LAND), f(BGA_LAND), f(BGA_MASK_MARGIN), fp.uu()))
    return fp


# ----------------------------------------------------------------------------
# MLF-6 2 x 2 mm with the 0.80 x 1.40 mm exposed pad of the MIC47050YML
# ----------------------------------------------------------------------------
MLF6_BODY = 2.00          # Micrel MIC47050 data sheet p.16 "6-Pin 2mm x 2mm MLF (ML)": 2.000 BSC square
MLF6_PITCH = 0.65         # 0.650 BSC
MLF6_LEAD_L = 0.35        # lead 0.350 +/-0.050 long ...
MLF6_LEAD_W = 0.25        # ... 0.250 +/-0.050 wide
MLF6_EP = (0.80, 1.40)    # exposed pad 0.800 +/-0.050 x 1.400 +/-0.050 (the stock KiCad DFN-6 has 1.0 x 1.6)
MLF6_PAD = (0.65, 0.35)   # land per lead (same as the stock KiCad 2 x 2 mm DFN-6): 0.075 heel, 0.375 toe
MLF6_PAD_X = 1.05
MLF6_PASTE = (0.64, 0.52, 0.37)   # two paste apertures on the exposed pad: size x, size y, +/-y offset (59 %)


def gen_mlf6():
    name = 'MLF-6-1EP_2x2mm_P0.65mm_EP0.8x1.4mm'
    fp = FP(name,
            '6-lead MLF / DFN, 2.0 x 2.0 x 0.85 mm, 0.65 mm pitch, exposed pad 0.80 x 1.40 mm '
            '(MIC47050YML package drawing); pin 1 top-left, counter-clockwise, pad 7 = exposed pad',
            'MLF DFN 6 2x2 0.65 EP0.8x1.4', 'smd')
    h = MLF6_BODY / 2
    fp.prop('Reference', 'REF**', 0, -2.0, 'F.SilkS', size=0.8)
    fp.prop('Value', name, 0, 2.0, 'F.Fab', size=0.5)
    fp.prop('Footprint', '', 0, 0, 'F.Fab', hide=True)
    fp.prop('Datasheet', '', 0, 0, 'F.Fab', hide=True)
    fp.prop('Description', '6-lead MLF 2 x 2 mm, EP 0.8 x 1.4 mm', 0, 0, 'F.Fab', hide=True)
    fp.nprop = len(fp.items)
    fp.poly([(-h + 0.5, -h), (h, -h), (h, h), (-h, h), (-h, -h + 0.5)], 'F.Fab', 0.1)
    fp.text('${REFERENCE}', 0, 0, 'F.Fab', 0.5)
    fp.line((-h, -h - 0.11), (h, -h - 0.11), 'F.SilkS', 0.12)
    fp.line((-h, h + 0.11), (h, h + 0.11), 'F.SilkS', 0.12)
    fp.poly([(-1.56, -1.01), (-1.84, -1.01), (-1.56, -1.29)], 'F.SilkS', 0.12, fill=True)
    fp.poly([(-1.65, -1.25), (1.65, -1.25), (1.65, 1.25), (-1.65, 1.25)], 'F.CrtYd', 0.05)
    pos = {1: (-MLF6_PAD_X, -MLF6_PITCH), 2: (-MLF6_PAD_X, 0), 3: (-MLF6_PAD_X, MLF6_PITCH),
           4: (MLF6_PAD_X, MLF6_PITCH), 5: (MLF6_PAD_X, 0), 6: (MLF6_PAD_X, -MLF6_PITCH)}
    for n in sorted(pos):
        x, y = pos[n]
        fp.add('(pad "%d" smd rect (at %s %s) (size %s %s) (layers "F.Cu" "F.Paste" "F.Mask") %s)'
               % (n, f(x), f(y), f(MLF6_PAD[0]), f(MLF6_PAD[1]), fp.uu()))
    fp.add('(pad "7" smd rect (at 0 0) (size %s %s) (layers "F.Cu" "F.Mask") %s)' % (f(MLF6_EP[0]), f(MLF6_EP[1]), fp.uu()))
    for sgn in (-1, 1):
        fp.add('(pad "" smd rect (at 0 %s) (size %s %s) (layers "F.Paste") %s)'
               % (f(sgn * MLF6_PASTE[2]), f(MLF6_PASTE[0]), f(MLF6_PASTE[1]), fp.uu()))
    return fp


FID_DOT, FID_MASK, FID_COURT = 1.0, 2.0, 2.5      # copper dot, mask opening, courtyard (diameters)


def gen_fiducial():
    name = 'Fiducial_1mm_Mask2mm'
    fp = FP(name, 'Assembly fiducial: round copper dot 1.0 mm in a solder-mask opening of 2.0 mm, no paste, no silk; keep copper '
            'and silk out of the 2.0 mm opening', 'fiducial', 'smd exclude_from_pos_files exclude_from_bom')
    fp.prop('Reference', 'REF**', 0, -1.7, 'F.Fab', size=0.5)
    fp.prop('Value', name, 0, 1.7, 'F.Fab', size=0.5)
    fp.prop('Footprint', '', 0, 0, 'F.Fab', hide=True)
    fp.prop('Datasheet', '', 0, 0, 'F.Fab', hide=True)
    fp.prop('Description', 'Assembly fiducial 1.0 mm, mask opening 2.0 mm', 0, 0, 'F.Fab', hide=True)
    fp.nprop = len(fp.items)
    fp.circle((0, 0), FID_MASK / 2, 'F.Fab', 0.1)
    fp.circle((0, 0), FID_COURT / 2, 'F.CrtYd', 0.05)
    fp.text('${REFERENCE}', 0, 0, 'F.Fab', 0.5)
    fp.add('(pad "" smd circle (at 0 0) (size %s %s) (layers "F.Cu" "F.Mask") (solder_mask_margin %s) (clearance %s) %s)'
           % (f(FID_DOT), f(FID_DOT), f((FID_MASK - FID_DOT) / 2), f((FID_MASK - FID_DOT) / 2), fp.uu()))
    return fp


LIB_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'lib', 'lpddr5_testbed.pretty'))


def main():
    os.makedirs(LIB_DIR, exist_ok=True)
    for fp in (gen_edge(), gen_bga(), gen_mlf6(), gen_fiducial()):
        print('wrote', fp.write(LIB_DIR))


if __name__ == '__main__':
    main()

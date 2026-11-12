#!/usr/bin/env python3
"""Check the generated footprints with pcbnew and render them.

    python3 scripts/check_footprints.py [--no-render]

Loads lib/lpddr5_testbed.pretty/{DDR4_SODIMM_260_Edge,BGA-315_12.4x15mm_P0.8x0.7mm,MLF-6-1EP_2x2mm_P0.65mm_EP0.8x1.4mm} with the
pcbnew API, prints pad counts / extents / key coordinates and asserts the DDR4 SO-DIMM
(JEDEC MO-310) numbers independently of gen_footprints.py (literal values from the drawings,
see lib/README.md), cross-checks the finger pattern against the stock KiCad DDR4 SO-DIMM
socket footprint, and writes PNG renders to lib/render/.
All coordinates printed are in the CARD frame (x from the pin-1 side, y up from the finger
edge) for the edge footprint and in the footprint frame for the BGA.
"""
import os
import subprocess
import sys
import tempfile

import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gen_footprints as g  # noqa: E402

LIB = g.LIB_DIR
OUT = os.path.normpath(os.path.join(LIB, '..', 'render'))
STOCK = '/usr/share/kicad/footprints/Connector_PCBEdge.pretty'
mm = pcbnew.ToMM
EPS = 1e-6
nfail = 0


def check(cond, msg):
    global nfail
    print('  %s %s' % ('ok  ' if cond else 'FAIL', msg))
    if not cond:
        nfail += 1


def near(a, b, tol=EPS):
    return abs(a - b) <= tol


def pads_of(fp):
    d = {}
    for p in fp.Pads():
        d.setdefault(p.GetNumber(), []).append(p)
    return d


def edge():
    print('== DDR4_SODIMM_260_Edge ==')
    fp = pcbnew.FootprintLoad(LIB, 'DDR4_SODIMM_260_Edge')
    pads = pads_of(fp)
    fingers = {int(k): v[0] for k, v in pads.items() if k}
    holes = pads.get('', [])
    print('  pads: %d fingers + %d NPTH' % (len(fingers), len(holes)))
    check(sorted(fingers) == list(range(1, 261)) and all(len(v) == 1 for k, v in pads.items() if k),
          '260 fingers numbered 1..260, each once')

    def cx(n):
        return mm(fingers[n].GetPosition().x)

    def cy(n):
        return -mm(fingers[n].GetPosition().y)

    print('  finger   x_card   y_card  layer   (card frame: x from pin-1 side, y up from finger edge)')
    for n in (1, 2, 143, 144, 145, 146, 259, 260):
        print('  %6d  %7.3f  %7.3f  %s' % (n, cx(n), cy(n), 'B.Cu' if fingers[n].IsOnLayer(pcbnew.B_Cu) else 'F.Cu'))
    # literal JEDEC MO-310 numbers (Foxconn 317-0000-2253 sheet 2 / Micron Fig. 3)
    exp = {1: 1.425, 2: 1.675, 143: 36.925, 144: 37.175, 145: 39.425, 146: 39.675, 259: 67.925, 260: 68.175}
    for n, x in exp.items():
        check(near(cx(n), x), 'finger %d x = %.3f' % (n, x))
    check(near(69.6 - cx(259), 1.675) and near(69.6 - cx(260), 1.425), 'pin 259 / 260 are 1.675 / 1.425 from the right edge')
    check(near(cx(143) - cx(1), 35.5) and near(cx(259) - cx(145), 28.5) and near(cx(145) - cx(143), 2.5),
          'front spans 35.50 / 2.50 / 28.50')
    check(near(cx(144) - cx(2), 35.5) and near(cx(260) - cx(146), 28.5) and near(cx(146) - cx(144), 2.5),
          'back spans 35.50 / 2.50 / 28.50')
    check(near((cx(143) + 1.375), 38.3) and near(cx(144) + 1.125, 38.3), 'key centre 38.30 = pin143 + 1.375 = pin144 + 1.125')
    ok_pitch = all(near(cx(n + 2) - cx(n), 0.5) for n in range(1, 259) if n not in (143, 144))
    check(ok_pitch, '0.50 pitch on each face (except across the key)')
    check(all(near(cx(n + 1) - cx(n), 0.25) for n in range(1, 260, 2)), 'even finger n+1 is 0.25 right of odd finger n (front view)')
    f_ok = all(fingers[n].IsOnLayer(pcbnew.F_Cu) and fingers[n].IsOnLayer(pcbnew.F_Mask)
               and not fingers[n].IsOnLayer(pcbnew.B_Cu) and not fingers[n].IsOnLayer(pcbnew.F_Paste) for n in range(1, 261, 2))
    b_ok = all(fingers[n].IsOnLayer(pcbnew.B_Cu) and fingers[n].IsOnLayer(pcbnew.B_Mask)
               and not fingers[n].IsOnLayer(pcbnew.F_Cu) and not fingers[n].IsOnLayer(pcbnew.B_Paste) for n in range(2, 261, 2))
    check(f_ok, 'odd fingers on F.Cu + F.Mask, no paste')
    check(b_ok, 'even fingers on B.Cu + B.Mask, no paste')
    sz = {(mm(p.GetSize().x), mm(p.GetSize().y)) for p in fingers.values()}
    print('  finger pad size', sz)
    check(sz == {(0.35, 2.55)}, 'finger pads 0.35 x 2.55')
    y0 = {round(cy(n) - 2.55 / 2, 6) for n in fingers}
    y1 = {round(cy(n) + 2.55 / 2, 6) for n in fingers}
    print('  finger span above the card edge: %s .. %s' % (y0, y1))
    check(len(y0) == 1 and 0.0 <= min(y0) <= 0.30, 'finger starts 0..0.30 mm from the card edge (0.30 MAX)')
    check(max(y1) < 4.0, 'finger tops below the 4.00 mm component line')
    # key slot clearance
    check(cx(143) + 0.175 < 37.8 - 0.3 and cx(144) + 0.175 < 37.8 - 0.3 and cx(145) - 0.175 > 38.8 + 0.3
          and cx(146) - 0.175 > 38.8 + 0.3, 'fingers 143..146 clear the key slot by > 0.3 mm')
    # holes
    hs = sorted((mm(p.GetPosition().x), -mm(p.GetPosition().y), mm(p.GetDrillSize().x)) for p in holes)
    print('  NPTH (x, y, drill):', hs)
    check(hs == [(2.0, 6.0, 1.8), (67.6, 6.0, 1.8)] and all(p.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH for p in holes),
          'two 1.80 mm NPTH at (2.0, 6.0) and (67.6, 6.0)')
    check(near(hs[1][0] - hs[0][0], 65.6), 'hole spacing 65.60 (basic dimension on the module drawing)')

    # outline: put the footprint on a board and let KiCad build the outline polygon
    b = pcbnew.BOARD()
    fp2 = pcbnew.FootprintLoad(LIB, 'DDR4_SODIMM_260_Edge')
    b.Add(fp2)
    fp2.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(100), pcbnew.FromMM(100)))
    poly = pcbnew.SHAPE_POLY_SET()
    closed = b.GetBoardPolygonOutlines(poly, False)
    bb = poly.BBox()
    w, h = mm(bb.GetWidth()), mm(bb.GetHeight())
    print('  outline: closed=%s outlines=%d bbox %.3f x %.3f mm, area %.2f mm2' % (closed, poly.OutlineCount(), w, h, poly.Area() / 1e12))
    check(closed and poly.OutlineCount() == 1, 'Edge.Cuts is one closed outline')
    check(abs(w - 69.6) < 0.01 and abs(h - 30.0) < 0.01, 'card outline 69.60 x 30.00')
    check(near(mm(bb.GetLeft()), 100, 0.01) and near(mm(bb.GetBottom()), 100, 0.01), 'footprint origin = lower-left card corner')

    def inside(x, y):   # card frame
        return poly.Contains(pcbnew.VECTOR2I(pcbnew.FromMM(100 + x), pcbnew.FromMM(100 - y)))
    key = [(38.3, 0.5, False), (38.3, 3.95, False), (38.3, 4.05, True), (37.75, 2.0, True), (37.85, 2.0, False),
           (38.75, 2.0, False), (38.85, 2.0, True)]
    check(all(inside(x, y) == v for x, y, v in key), 'key slot x 37.80..38.80 (1.00 wide), 4.00 deep, centre 38.30')
    notch = [(1.0, 20.0, False), (1.70, 20.0, False), (1.80, 20.0, True), (1.0, 17.95, True), (1.0, 18.05, False),
             (1.0, 21.95, False), (1.0, 22.05, True), (68.6, 20.0, False), (67.9, 20.0, False), (67.8, 20.0, True),
             (68.6, 17.95, True), (68.6, 22.05, True), (1.70, 18.1, True), (67.9, 21.9, True)]
    check(all(inside(x, y) == v for x, y, v in notch), 'latch notches y 18.00..22.00, 1.75 deep, rounded inner corners, both sides')
    check(not inside(0.2, 0.2) and inside(0.4, 0.4) and not inside(69.4, 29.8) and inside(0.05, 1.0),
          'corner chamfers 0.6 x 45 deg')
    # mask strip and keep-outs
    masks = [s for s in fp.GraphicalItems() if s.GetLayer() in (pcbnew.F_Mask, pcbnew.B_Mask)]
    mb = [(pcbnew.LayerName(s.GetLayer()), mm(s.GetBoundingBox().GetLeft()), -mm(s.GetBoundingBox().GetBottom()),
           mm(s.GetBoundingBox().GetRight()), -mm(s.GetBoundingBox().GetTop())) for s in masks]
    print('  mask openings (layer, x0, y0, x1, y1 card):', [(m[0],) + tuple(round(v, 3) for v in m[1:]) for m in mb])
    check(len(mb) == 2 and all(m[1] <= 0 and m[2] <= 0 and m[3] >= 69.6 and 2.70 <= m[4] < 4.0 for m in mb),
          'mask-free strip on F.Mask and B.Mask covers all fingers down to the edge')
    zones = list(fp.Zones())
    print('  keep-out rule areas:', [(z.GetZoneName(), z.GetDoNotAllowFootprints(), z.GetLayerSet().Contains(pcbnew.F_Cu),
                                      z.GetLayerSet().Contains(pcbnew.B_Cu)) for z in zones])
    check(len(zones) == 3 and all(z.GetIsRuleArea() and z.GetDoNotAllowFootprints() and not z.GetDoNotAllowTracks()
                                  and not z.GetDoNotAllowVias() for z in zones), '3 footprint keep-out areas (tracks/vias allowed), F+B')
    area = g.component_area()
    xs, ys = [p[0] for p in area], [p[1] for p in area]
    print('  component area bbox (card): x %.2f..%.2f, y %.2f..%.2f; h<=%.2f mm for y 4.00..5.10, else h<=%.2f mm'
          % (min(xs), max(xs), min(ys), max(ys), g.COMP_H_LOW, g.COMP_H_MAX))
    check(min(ys) == 4.0 and min(xs) == 2.0 and max(xs) == 67.6 and near(g.COMP_H_MAX, 1.2), 'component area numbers')

    # cross-check 1: helper functions agree with the file
    check(all(near(g.finger_xy(n)[0], cx(n)) and near(g.finger_xy(n)[1], cy(n)) for n in fingers), 'gen_footprints.finger_xy() matches the file')
    check(g.card_to_kicad(1.425, 1.425, (100, 100)) == (101.425, 98.575) and g.kicad_to_card(101.425, 98.575, (100, 100)) == (1.425, 1.425),
          'card_to_kicad / kicad_to_card')
    # cross-check 2: stock KiCad socket footprint (TE 2309407): same finger sequence along the socket
    st = pcbnew.FootprintLoad(STOCK, 'SODIMM-260_DDR4_H4.0-5.2_OrientationStd_Socket')
    sp = {int(p.GetNumber()): mm(p.GetPosition().y) for p in st.Pads() if p.GetNumber().isdigit()}
    d = {round((sp[n] - sp[1]) - (cx(n) - cx(1)), 6) for n in range(1, 261)}
    print('  stock socket footprint: along-socket offset of pin n relative to pin 1 minus ours:', d)
    check(d == {0.0}, 'finger x pattern identical to the stock KiCad DDR4 SO-DIMM socket footprint')
    # cross-check 3: working precedent (LPDDR4 testbed in DDR4 SO-DIMM form), values read from its board file
    prec = {1: 1.425, 2: 1.675, 143: 36.923, 144: 37.173, 145: 39.423, 146: 39.673, 259: 67.924, 260: 68.174}
    dm = max(abs(cx(n) - x) for n, x in prec.items())
    print('  precedent card (references/sodimm_module/.../lpddr4-testbed.kicad_pcb): max |dx| = %.3f mm' % dm)
    check(dm <= 0.0021, 'within 2 um of the precedent finger positions')
    return fp


def bga():
    print('== BGA-315_12.4x15mm_P0.8x0.7mm ==')
    fp = pcbnew.FootprintLoad(LIB, 'BGA-315_12.4x15mm_P0.8x0.7mm')
    pads = {p.GetNumber(): p for p in fp.Pads()}
    print('  pads:', len(fp.Pads()))
    check(len(fp.Pads()) == 315 and len(pads) == 315, '315 unique balls')
    rows = sorted({''.join(c for c in k if c.isalpha()) for k in pads}, key=lambda r: (len(r), r))
    cols = sorted({int(''.join(c for c in k if c.isdigit())) for k in pads})
    print('  rows:', ' '.join(rows))
    print('  cols:', cols[0], '..', cols[-1])
    check(rows == 'A B C D E F G H J K L M N P R T U V W Y AA'.split() and cols == list(range(1, 16)),
          '21 rows A..AA without I O Q S X Z, 15 columns')

    def xy(n):
        return (mm(pads[n].GetPosition().x), mm(pads[n].GetPosition().y))
    for n in ('A1', 'A15', 'B1', 'H9', 'J9', 'L8', 'AA1', 'AA15'):
        print('  ball %-5s x %6.2f  y %6.2f' % ((n,) + xy(n)))
    check(xy('A1') == (-5.6, -7.0) and xy('AA15') == (5.6, 7.0) and xy('L8') == (0.0, 0.0), 'A1 (-5.6,-7.0) top-left, AA15 (5.6,7.0), L8 centre')
    check(near(xy('A2')[0] - xy('A1')[0], 0.8) and near(xy('B1')[1] - xy('A1')[1], 0.7), 'pitch 0.8 (x, columns) x 0.7 (y, rows)')
    check(near(xy('A15')[0] - xy('A1')[0], 11.2) and near(xy('AA1')[1] - xy('A1')[1], 14.0), 'ball matrix 11.2 x 14.0 centre to centre')
    sz = {(mm(p.GetSize().x), mm(p.GetSize().y), p.GetShape(pcbnew.F_Cu) == pcbnew.PAD_SHAPE_CIRCLE) for p in pads.values()}
    print('  land (x, y, round):', sz)
    check(sz == {(0.41, 0.41, True)}, '0.41 mm round lands')
    check(all(p.IsOnLayer(pcbnew.F_Cu) and p.IsOnLayer(pcbnew.F_Mask) and p.IsOnLayer(pcbnew.F_Paste) for p in pads.values()), 'F.Cu + F.Mask + F.Paste')
    fab = [s for s in fp.GraphicalItems() if s.GetLayer() == pcbnew.F_Fab and s.GetClass() == 'PCB_SHAPE']
    bb = fab[0].GetBoundingBox()
    print('  body outline (F.Fab) %.2f x %.2f' % (mm(bb.GetWidth()) - 0.1, mm(bb.GetHeight()) - 0.1))
    check(near(mm(bb.GetWidth()) - 0.1, 12.4, 0.01) and near(mm(bb.GetHeight()) - 0.1, 15.0, 0.01), 'package outline 12.4 x 15.0')
    for name in g.ball_names():
        if g.ball_xy(name) != xy(name):
            check(False, 'ball_xy(%s)' % name)
            break
    else:
        check(True, 'gen_footprints.ball_xy() matches the file')
    return fp


def mlf6():
    name = 'MLF-6-1EP_2x2mm_P0.65mm_EP0.8x1.4mm'
    print('== %s ==' % name)
    fp = pcbnew.FootprintLoad(LIB, name)
    pads = pads_of(fp)
    num = {k: v[0] for k, v in pads.items() if k}
    check(sorted(num) == [str(i) for i in range(1, 8)] and all(len(v) == 1 for k, v in pads.items() if k), 'pads 1..7, each once')

    def xy(n):
        return (mm(num[n].GetPosition().x), mm(num[n].GetPosition().y))

    def size(n):
        return (mm(num[n].GetSize().x), mm(num[n].GetSize().y))
    for n in sorted(num):
        print('  pad %s  at (%5.2f, %5.2f)  size %.2f x %.2f' % ((n,) + xy(n) + size(n)))
    # literal numbers of the MIC47050 package drawing (data sheet p.16): body 2.0 square, pitch 0.65,
    # lead 0.25 x 0.35, exposed pad 0.80 x 1.40; pin 1 top-left, numbering counter-clockwise seen from the top
    check(xy('1') == (-1.05, -0.65) and xy('2') == (-1.05, 0.0) and xy('3') == (-1.05, 0.65), 'pins 1-3 on the left, top to bottom, 0.65 pitch')
    check(xy('4') == (1.05, 0.65) and xy('5') == (1.05, 0.0) and xy('6') == (1.05, -0.65), 'pins 4-6 on the right, bottom to top')
    check(all(size(str(n)) == (0.65, 0.35) for n in range(1, 7)), 'lead lands 0.65 x 0.35 (lead 0.35 x 0.25)')
    heel = 1.05 - 0.65 / 2
    check(heel <= 1.0 - 0.35 + 0.08 and 1.05 + 0.65 / 2 >= 1.0 + 0.25, 'land covers the lead: heel at %.3f (lead inner end 0.65), toe 0.375 beyond the body' % heel)
    check(xy('7') == (0.0, 0.0) and size('7') == (0.8, 1.4), 'exposed pad 0.80 x 1.40 = the package pad (not larger)')
    gap = heel - size('7')[0] / 2
    check(gap >= 0.30, 'copper gap exposed pad to lead lands %.3f mm (>= 0.30)' % gap)
    paste = [p for p in pads.get('', [])]
    area = sum(mm(p.GetSize().x) * mm(p.GetSize().y) for p in paste)
    check(len(paste) == 2 and all(abs(mm(p.GetPosition().x)) + mm(p.GetSize().x) / 2 <= 0.4 + EPS
                                  and abs(mm(p.GetPosition().y)) + mm(p.GetSize().y) / 2 <= 0.7 + EPS for p in paste)
          and 0.5 <= area / (0.8 * 1.4) <= 0.8, 'two paste apertures inside the exposed pad, coverage %.0f %%' % (100 * area / 1.12))
    check(not num['7'].IsOnLayer(pcbnew.F_Paste), 'exposed pad itself has no paste (apertures only)')
    stock = pcbnew.FootprintLoad('/usr/share/kicad/footprints/Package_DFN_QFN.pretty', 'DFN-6-1EP_2x2mm_P0.65mm_EP1x1.6mm')
    sp = {p.GetNumber(): p for p in stock.Pads() if p.GetNumber()}
    same = all((mm(sp[str(n)].GetPosition().x), mm(sp[str(n)].GetPosition().y)) == xy(str(n)) for n in range(1, 7))
    check(same, 'lead positions and numbering identical to the stock KiCad 2 x 2 mm DFN-6')
    return fp


def no_branding():
    print('== text scan ==')
    bad = ('antmicro', 'open hardware', 'openhardware', 'oshw', 'apache', 'license', 'author', 'ant3dmdl')
    for fn in sorted(os.listdir(LIB)):
        t = open(os.path.join(LIB, fn)).read().lower()
        hits = [w for w in bad if w in t]
        check(not hits, '%s: no vendor branding / licence fields %s' % (fn, hits or ''))


def render():
    print('== renders ==')
    import gi
    gi.require_version('Rsvg', '2.0')
    from gi.repository import Rsvg
    import cairo
    os.makedirs(OUT, exist_ok=True)
    jobs = [('DDR4_SODIMM_260_Edge', 'F.Cu,B.Cu,F.Mask,Edge.Cuts,Dwgs.User,F.SilkS,F.Fab', 'DDR4_SODIMM_260_Edge', 2400),
            ('DDR4_SODIMM_260_Edge', 'F.Cu,Edge.Cuts,F.Fab', 'DDR4_SODIMM_260_Edge_front_cu', 2400),
            ('DDR4_SODIMM_260_Edge', 'B.Cu,Edge.Cuts,B.Fab', 'DDR4_SODIMM_260_Edge_back_cu', 2400),
            ('BGA-315_12.4x15mm_P0.8x0.7mm', 'F.Cu,F.SilkS,F.Fab,F.CrtYd', 'BGA-315_12.4x15mm_P0.8x0.7mm', 1400),
            ('MLF-6-1EP_2x2mm_P0.65mm_EP0.8x1.4mm', 'F.Cu,F.Paste,F.SilkS,F.Fab,F.CrtYd', 'MLF-6-1EP_2x2mm_P0.65mm_EP0.8x1.4mm', 700)]
    for name, layers, out, width in jobs:
        with tempfile.TemporaryDirectory() as td:
            subprocess.run(['kicad-cli', 'fp', 'export', 'svg', '-o', td, '-l', layers, '--fp', name, LIB],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            h = Rsvg.Handle.new_from_file(os.path.join(td, name + '.svg'))
            ok, vw, vh = h.get_intrinsic_size_in_pixels()
            if not ok:
                d = h.get_dimensions()
                vw, vh = d.width, d.height
            s = width / vw
            surf = cairo.ImageSurface(cairo.FORMAT_RGB24, int(width), int(vh * s) + 1)
            cr = cairo.Context(surf)
            cr.set_source_rgb(1, 1, 1)
            cr.paint()
            r = Rsvg.Rectangle()
            r.x, r.y, r.width, r.height = 0, 0, width, vh * s
            h.render_document(cr, r)
            p = os.path.join(OUT, out + '.png')
            surf.write_to_png(p)
            print('  wrote', p)


if __name__ == '__main__':
    edge()
    bga()
    mlf6()
    no_branding()
    if '--no-render' not in sys.argv:
        render()
    print('RESULT: %s' % ('ALL CHECKS PASSED' if nfail == 0 else '%d CHECK(S) FAILED' % nfail))
    sys.exit(1 if nfail else 0)

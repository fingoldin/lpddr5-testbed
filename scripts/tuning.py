#!/usr/bin/env python3
"""Length-tuning geometry and the electrical length of bends.  Pure Python (no pcbnew): used by
scripts/build_netlist.py (the plan), scripts/pcb/s20_cmd.py (the copper) and scripts/pcb/check_pcb.py (the gate
uses only bend_shortfall and leg_coupling, on geometry it reads back from the board).

Why a bend model.  A wave does not follow the centre line of a bend: it cuts the corner.  HFSS runs of the In3
trace of this board (0.15 mm between In2 and In4; DESIGN.md section 13) show that the electrical length of a bent
trace is shorter than its centre line by an amount proportional to the angle turned: 0.020 mm per radian at a
mitred 45-degree vertex (0.017 on the 0.10 mm CK legs), 0.021 / 0.023 / 0.029 mm per radian in an arc of 0.20 /
0.15 / 0.10 mm centre radius (0.031 .. 0.046 mm = 0.22 .. 0.32 ps per 90 degrees).  Two antiparallel legs of one
net that lie close together lose a little more (they couple).  An accordion of small bumps therefore delivers far
less delay than its centre line suggests (found by the verification of stage s20: -4.2 ps on 15 ps).  The formula
is local; bends that follow each other within about 0.2 mm interact (a bump with short legs loses up to 0.06 mm
less than the formula says), so the plan adds, per net, the difference between an HFSS run of that net's own
feature and the formula (groups[CMD].tuning.per_net[net].hfss).  Consequences, both in this file:

  1. the delay of every In3 trace is computed from its ELECTRICAL length (rules.bend_model in the spec):
         centre line - sum over mitred vertices of  (k_mitre0 + k_mitre1 * w) * turn
                     - sum over arcs of             (k_arc0 * w + k_arc1 * (r - r_lm)) * turn
                     - sum over pairs of adjacent antiparallel legs of  2 * k_leg * exp(-pi * gap / leg_decay) * overlap
     turn in rad; r = centre radius, r_lm = w / ln((r + w/2) / (r - w/2)) (log-mean radius); gap = copper to copper
     between the two legs, overlap = their common length;
  2. tuning features turn as little as possible for the length they add: a parallel shift between two 45-degree
     runs (180 degrees in all) and a few large bumps (360 degrees each), legs 0.72 mm apart.

A tuning feature of one net (dict, stored per net in groups[CMD].tuning.per_net[net]["shape"]):
    x          column axis of the trace (the trunk)
    y          [y_from, y_to]: the straight run the feature replaces
    shift      lateral shift of the trunk on the plateau, signed (+ = east); 0 = none
    shift_y    [y0, y1]: the 45-degree run leaves the trunk at y0 and the trunk is rejoined at y1
    pattern    lateral position of the trace after every horizontal leg, in units of the amplitude, relative to the
               plateau: [0, 1, 0, 1, 0] = two bumps to the east
    rows       y of every horizontal leg (len(pattern) - 1 of them)
    amplitude  bump amplitude, mm
    radius     centre-line radius of the leg corners, mm (the 45-degree corners of the shift stay mitred)
"""
import math


def bend_shortfall(model, w, turn, radius=None):
    """mm by which the electrical length of a bend is shorter than its centre line: proportional to the angle
    turned; radius None = mitred vertex between two straight segments, else an arc of that centre radius."""
    if turn < 1e-9:
        return 0.0
    if not radius:
        return (model["k_mitre0"] + model["k_mitre1"] * w) * turn
    if radius <= w / 2 + 1e-9:
        raise ValueError("arc radius %.4f does not exceed half the width %.3f" % (radius, w))
    r_lm = w / math.log((radius + w / 2) / (radius - w / 2))
    return (model["k_arc0"] * w + model["k_arc1"] * (radius - r_lm)) * turn


def leg_coupling(model, w, legs):
    """mm lost to the coupling between adjacent antiparallel legs of one net.
    legs: [(x_from, x_to, y)] in signal order (horizontal legs, x_from -> x_to = direction of travel)."""
    out = 0.0
    for (a0, a1, ya), (b0, b1, yb) in zip(legs, legs[1:]):
        if (a1 - a0) * (b1 - b0) >= 0:
            continue                    # same direction: not an accordion pair
        overlap = min(max(a0, a1), max(b0, b1)) - max(min(a0, a1), min(b0, b1))
        gap = abs(yb - ya) - w
        if overlap > 0 and gap > 0:
            out += 2 * model["k_leg"] * math.exp(-math.pi * gap / model["leg_decay_mm"]) * overlap
    return out


def vertices(shape, amplitude=None, shift=None):
    """Centre line of the feature as [(x, y, corner radius or None)], from (x, y_from) to (x, y_to)."""
    a = shape["amplitude"] if amplitude is None else amplitude
    s = shape["shift"] if shift is None else shift
    xc = shape["x"]
    y_from, y_to = shape["y"]
    r = shape.get("radius")
    pts = [(xc, y_from, None)]
    xp = xc + s
    if abs(s) > 1e-9:
        y0, y1 = shape["shift_y"]
        pts += [(xc, y0, None), (xp, y0 + abs(s), None)]
    pat, rows = shape.get("pattern") or [0], shape.get("rows") or []
    assert len(rows) == len(pat) - 1 and pat[0] == 0 and pat[-1] == 0, shape
    for k, y in enumerate(rows):
        pts += [(xp + pat[k] * a, y, r), (xp + pat[k + 1] * a, y, r)]
    if abs(s) > 1e-9:
        pts += [(xp, y1 - abs(s), None), (xc, y1, None)]
    pts.append((xc, y_to, None))
    out = [pts[0]]
    for p in pts[1:]:                       # drop vertices that repeat the previous point
        if math.hypot(p[0] - out[-1][0], p[1] - out[-1][1]) > 1e-9:
            out.append(p)
    return out


def _turn(p0, p1, p2):
    u = (p1[0] - p0[0], p1[1] - p0[1])
    v = (p2[0] - p1[0], p2[1] - p1[1])
    lu, lv = math.hypot(*u), math.hypot(*v)
    return math.acos(max(-1.0, min(1.0, (u[0] * v[0] + u[1] * v[1]) / (lu * lv)))), lu, lv


def lengths(verts, w, model):
    """(centre-line length as drawn, electrical length) of a vertex list [(x, y, radius or None)].
    A corner with a radius is drawn as a tangent arc; the radius must fit (no silent reduction)."""
    geo = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(verts, verts[1:]))
    short = 0.0
    used = [0.0] * len(verts)               # tangent length taken from each side of a vertex
    for i in range(1, len(verts) - 1):
        turn, lu, lv = _turn(verts[i - 1], verts[i], verts[i + 1])
        if turn < 1e-9:
            continue
        r = verts[i][2]
        if r:
            t = r * math.tan(turn / 2)
            used[i] = t
            geo += r * turn - 2 * t
        short += bend_shortfall(model, w, turn, r)
    for i in range(len(verts) - 1):
        seg = math.hypot(verts[i + 1][0] - verts[i][0], verts[i + 1][1] - verts[i][1])
        if used[i] + used[i + 1] > seg + 1e-9:
            raise ValueError("corner radii do not fit the segment %s -> %s (%.4f + %.4f > %.4f)"
                             % (verts[i][:2], verts[i + 1][:2], used[i], used[i + 1], seg))
    legs = [(a[0], b[0], a[1]) for a, b in zip(verts, verts[1:]) if a[2] and b[2] and abs(a[1] - b[1]) < 1e-9]
    short += leg_coupling(model, w, legs)
    return geo, geo - short


def polyline_lengths(pts, w, model):
    """The same for a plain mitred polyline [(x, y), ...]."""
    return lengths([(p[0], p[1], None) for p in pts], w, model)


def solve(shape, want_electrical, w, model, param, lo, hi):
    """Value of 'amplitude' or 'shift' (magnitude; the sign of shape['shift'] is kept) for which the electrical
    length of the feature is want_electrical.  Bisection on [lo, hi]."""
    sign = -1.0 if shape.get("shift", 0.0) < 0 else 1.0

    def f(v):
        kw = {"amplitude": v} if param == "amplitude" else {"shift": sign * v}
        return lengths(vertices(shape, **kw), w, model)[1]
    if not (f(lo) - 1e-9 <= want_electrical <= f(hi) + 1e-9):
        raise ValueError("no solution: %s in [%.3f, %.3f] gives %.4f .. %.4f mm, wanted %.4f" % (param, lo, hi, f(lo), f(hi), want_electrical))
    for _ in range(60):
        mid = (lo + hi) / 2
        if f(mid) < want_electrical:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def centre_line(verts, step=0.01):
    """The centre line as drawn, as points: corners with a radius replaced by sampled tangent arcs."""
    out = [verts[0][:2]]
    for i in range(1, len(verts) - 1):
        p0, p1, p2 = verts[i - 1], verts[i], verts[i + 1]
        turn, lu, lv = _turn(p0, p1, p2)
        r = p1[2]
        if turn < 1e-9:
            continue
        if not r:
            out.append(p1[:2])
            continue
        u = ((p1[0] - p0[0]) / lu, (p1[1] - p0[1]) / lu)
        v = ((p2[0] - p1[0]) / lv, (p2[1] - p1[1]) / lv)
        t = r * math.tan(turn / 2)
        a = (p1[0] - u[0] * t, p1[1] - u[1] * t)
        left = u[0] * v[1] - u[1] * v[0] > 0
        nx, ny = (-u[1], u[0]) if left else (u[1], -u[0])
        c = (a[0] + nx * r, a[1] + ny * r)
        a0 = math.atan2(a[1] - c[1], a[0] - c[0])
        sw = turn if left else -turn
        n = max(4, int(r * turn / step))
        out += [(c[0] + r * math.cos(a0 + sw * k / n), c[1] + r * math.sin(a0 + sw * k / n)) for k in range(n + 1)]
    out.append(verts[-1][:2])
    return out

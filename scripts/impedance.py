#!/usr/bin/env python3
"""2-D quasi-static field solver (finite differences) for the card stackup.

Solves Laplace's equation on a rectangular grid for one or two conductors,
once with the real dielectrics and once in vacuum, and derives L and C
matrices: Z0, even/odd-mode impedance, delay per mm and backward coupling.

Geometries (all dimensions in mm):
  microstrip  : F.Cu / B.Cu over In1 / In4, prepreg h = 0.11, copper t, optional mask
  stripline   : In3 between In2 (0.11 mm prepreg above) and In4 (core below)

Run:  python3 scripts/impedance.py            (prints the tables used in design/DESIGN.md)
      python3 scripts/impedance.py --json     (machine-readable)
Cross-checked against closed-form values (Pozar 3.195-3.199 microstrip, 3.179 stripline).
"""
import sys, json
from math import sqrt, log, pi
import numpy as np
import scipy.sparse as sp
import scipy.sparse.linalg as spl

E0 = 8.854e-12
MU0 = 4e-7 * pi
C0 = 2.998e8
ER = 4.29          # PR2116 prepreg and FR4 core (value of the upstream stackup)
ER_MASK = 3.5
H_PP = 0.11        # prepreg thickness
H_CORE = 0.36      # thinned core
T_OUT = 0.040      # finished outer copper used for the design values: between the stackup-table nominal (0.035)
                   # and the copper left after the via-in-pad fill-and-cap plating (about 0.050..0.055)
T_MASK = 0.020     # solder mask over copper and laminate used for the design values (stackup table: 0.010 nominal)
T_IN = 0.018
# the two corners the fab may deliver on the outer layers; both are solved and stored (see tables())
OUTER_CASES = {"stackup_table_nominal": (0.035, 0.010), "design_value": (T_OUT, T_MASK), "via_in_pad_cap_plated": (0.055, 0.020)}


def _solve(ercell, cond):
    ny, nx = ercell.shape
    N = (nx + 1) * (ny + 1)
    idx = np.arange(N).reshape(ny + 1, nx + 1)
    erp = np.pad(ercell, 1, mode="edge")
    wx = 0.5 * (erp[0:ny + 1, 1:nx + 1] + erp[1:ny + 2, 1:nx + 1])
    wy = 0.5 * (erp[1:ny + 1, 0:nx + 1] + erp[1:ny + 1, 1:nx + 2])
    r = np.concatenate([idx[:, :-1].ravel(), idx[:-1, :].ravel()])
    s = np.concatenate([idx[:, 1:].ravel(), idx[1:, :].ravel()])
    w = np.concatenate([wx.ravel(), wy.ravel()])
    A = sp.coo_matrix((np.concatenate([w, w, -w, -w]),
                       (np.concatenate([r, s, r, s]), np.concatenate([r, s, s, r]))), shape=(N, N)).tocsr()
    cf = cond.ravel()
    free = np.where(cf == 0)[0]
    nc = cf.max()
    lu = spl.splu(A[free][:, free].tocsc())
    phis = []
    for k in range(1, nc + 1):
        v = np.zeros(N)
        v[cf == k] = 1.0
        x = v.copy()
        x[free] = lu.solve(-(A[free] @ v))
        phis.append(x)
    Cm = np.zeros((nc, nc))
    for a in range(nc):
        for b in range(nc):
            Cm[a, b] = E0 * phis[a] @ (A @ phis[b])
    return Cm


def line(kind, w, s=None, dx=0.005, mask=True, h_core=H_CORE, t=None, mask_t=T_MASK):
    """kind 'ms' (outer microstrip) or 'sl' (In3 stripline). s=None: single line, else coupled pair gap."""
    m = lambda v: int(round(v / dx))
    n = 1 if s is None else 2
    marg = m(1.0)
    nx = 2 * marg + n * m(w) + (n - 1) * m(s or 0)
    if kind == "ms":
        t = T_OUT if t is None else t
        ny = m(H_PP) + m(1.2)
        er = np.ones((ny, nx))
        er[:m(H_PP), :] = ER
        y0 = m(H_PP)
    else:
        t = T_IN if t is None else t
        ny = m(h_core) + m(t) + m(H_PP)
        er = np.full((ny, nx), ER)
        y0 = m(h_core)          # trace sits on the core, prepreg (0.11) above it
    cond = np.zeros((ny + 1, nx + 1), int)
    cond[0, :] = -1
    cond[-1, :] = -1
    cond[:, 0] = -1
    cond[:, -1] = -1
    for k in range(n):
        x0 = marg + k * (m(w) + m(s or 0))
        cond[y0:y0 + m(t) + 1, x0:x0 + m(w) + 1] = k + 1
        if kind == "ms" and mask:   # conformal mask: mask_t over copper and on the laminate
            er[y0:y0 + m(t) + m(mask_t), max(0, x0 - m(mask_t)):x0 + m(w) + m(mask_t)] = ER_MASK
    if kind == "ms" and mask:
        er[y0:y0 + m(mask_t), :] = np.where(er[y0:y0 + m(mask_t), :] == 1.0, ER_MASK, er[y0:y0 + m(mask_t), :])
    C = _solve(er, cond)
    Cv = _solve(np.ones_like(er), cond)
    L = MU0 * E0 * np.linalg.inv(Cv)
    out = {"kind": kind, "w": w, "s": s}
    if n == 1:
        out["z0"] = sqrt(L[0, 0] / C[0, 0])
        out["td_ps_mm"] = sqrt(L[0, 0] * C[0, 0]) * 1e9
        out["c_pf_mm"] = C[0, 0] * 1e9
    else:
        Cs, Cm_, Ls, Lm = C[0, 0], -C[0, 1], L[0, 0], L[0, 1]
        out["z0"] = sqrt(Ls / Cs)
        out["zodd"] = sqrt((Ls - Lm) / (Cs + Cm_))
        out["zeven"] = sqrt((Ls + Lm) / (Cs - Cm_))
        out["zdiff"] = 2 * out["zodd"]
        out["td_odd_ps_mm"] = sqrt((Ls - Lm) * (Cs + Cm_)) * 1e9
        out["td_ps_mm"] = sqrt(Ls * Cs) * 1e9
        out["kb"] = 0.25 * (Cm_ / Cs + Lm / Ls)
        out["kf_ps_mm"] = 0.5 * (Cm_ / Cs - Lm / Ls) * sqrt(Ls * Cs) * 1e9
    return out


def closed_form_ms(w, h, er):
    ee = (er + 1) / 2 + (er - 1) / 2 / sqrt(1 + 12 * h / w)
    u = w / h
    z = 60 / sqrt(ee) * log(8 / u + u / 4) if u <= 1 else 120 * pi / (sqrt(ee) * (u + 1.393 + 0.667 * log(u + 1.444)))
    return z, ee


def closed_form_sl_sym(w, b, er):
    we = w - (0 if w / b > 0.35 else (0.35 - w / b) ** 2 * b)
    return 30 * pi / sqrt(er) * b / (we + 0.441 * b)


def tables():
    res = {"params": {"er": ER, "h_prepreg": H_PP, "h_core": H_CORE, "t_outer": T_OUT, "t_mask": T_MASK, "t_inner": T_IN}}
    res["microstrip_single"] = [line("ms", w) for w in (0.09, 0.10, 0.125, 0.15, 0.18, 0.20, 0.22, 0.25)]
    res["microstrip_single_nomask"] = [line("ms", w, mask=False) for w in (0.15, 0.20)]
    res["stripline_single"] = [line("sl", w) for w in (0.09, 0.10, 0.125, 0.15, 0.165, 0.18, 0.20, 0.22)]
    res["stripline_single_core040"] = [line("sl", w, h_core=0.40) for w in (0.15,)]
    res["microstrip_pair"] = [line("ms", w, s) for w, s in ((0.10, 0.10), (0.11, 0.10), (0.12, 0.10), (0.125, 0.10), (0.15, 0.10), (0.15, 0.40))]
    res["stripline_pair"] = [line("sl", w, s) for w, s in ((0.09, 0.09), (0.10, 0.10), (0.11, 0.10), (0.12, 0.10), (0.10, 0.125), (0.15, 0.20), (0.15, 0.35), (0.15, 0.65))]
    res["outer_copper_and_mask_cases"] = {}
    for name, (t, mt) in OUTER_CASES.items():
        res["outer_copper_and_mask_cases"][name] = {
            "t_outer": t, "t_mask": mt,
            "single": [line("ms", w, t=t, mask_t=mt) for w in (0.09, 0.15, 0.20)],
            "pair": [line("ms", w, sgap, t=t, mask_t=mt) for w, sgap in ((0.10, 0.10), (0.11, 0.10), (0.12, 0.10))]}
    res["check_closed_form"] = {
        "microstrip_w0.15_h0.11": closed_form_ms(0.15, H_PP, ER)[0],
        "microstrip_w0.20_h0.11": closed_form_ms(0.20, H_PP, ER)[0],
        "sym_stripline_w0.15_b0.225": closed_form_sl_sym(0.15, 2 * H_PP + 0.005, ER),
        "fd_sym_stripline_w0.15_b0.225": line("sl", 0.15, h_core=H_PP, t=0.005)["z0"],
        "fd_microstrip_nomask_thin_w0.15": line("ms", 0.15, mask=False, t=0.005)["z0"],
        "fd_microstrip_nomask_thin_w0.20": line("ms", 0.20, mask=False, t=0.005)["z0"],
    }
    return res


def main():
    r = tables()
    if "--json" in sys.argv:
        json.dump(r, sys.stdout, indent=1)
        return
    print("stackup: er %.2f, prepreg %.3f mm, core %.3f mm, outer Cu %.3f, inner Cu %.3f" % (ER, H_PP, H_CORE, T_OUT, T_IN))
    print("\nOuter microstrip (F.Cu over In1, B.Cu over In4), with solder mask:")
    for o in r["microstrip_single"]:
        print("  w %.3f  Z0 %5.1f ohm  %.2f ps/mm  %.3f pF/mm" % (o["w"], o["z0"], o["td_ps_mm"], o["c_pf_mm"]))
    for o in r["microstrip_single_nomask"]:
        print("  w %.3f  Z0 %5.1f ohm  %.2f ps/mm  (no mask)" % (o["w"], o["z0"], o["td_ps_mm"]))
    print("\nIn3 stripline (0.11 mm to In2, %.2f mm to In4):" % H_CORE)
    for o in r["stripline_single"]:
        print("  w %.3f  Z0 %5.1f ohm  %.2f ps/mm  %.3f pF/mm" % (o["w"], o["z0"], o["td_ps_mm"], o["c_pf_mm"]))
    for o in r["stripline_single_core040"]:
        print("  w %.3f  Z0 %5.1f ohm  (upstream 0.40 mm core, for comparison)" % (o["w"], o["z0"]))
    print("\nOuter coupled pairs (edge-coupled microstrip, with mask):")
    for o in r["microstrip_pair"]:
        print("  w %.3f gap %.3f  Zdiff %5.1f  Zodd %5.1f  Zeven %5.1f  Kb %.4f  %.2f ps/mm (odd)" % (o["w"], o["s"], o["zdiff"], o["zodd"], o["zeven"], o["kb"], o["td_odd_ps_mm"]))
    print("\nIn3 coupled pairs / neighbours:")
    for o in r["stripline_pair"]:
        print("  w %.3f gap %.3f  Zdiff %5.1f  Zodd %5.1f  Zeven %5.1f  Kb %.4f  %.2f ps/mm" % (o["w"], o["s"], o["zdiff"], o["zodd"], o["zeven"], o["kb"], o["td_odd_ps_mm"]))
    print("\nOuter layers for the copper / mask the fab may deliver (single 0.09 / 0.15 / 0.20 mm; pair 0.10 / 0.10):")
    for name, o in r["outer_copper_and_mask_cases"].items():
        print("  %-24s Cu %.3f mask %.3f:  %s ohm;  pairs %s ohm diff" % (
            name, o["t_outer"], o["t_mask"], " / ".join("%.1f" % x["z0"] for x in o["single"]),
            " / ".join("%.1f (w %.2f)" % (x["zdiff"], x["w"]) for x in o["pair"])))
    c = r["check_closed_form"]
    print("\nCross-check, thin conductor (t = 0.005), no mask, microstrip: closed form w 0.15 %.1f / w 0.20 %.1f ohm; this solver %.1f / %.1f ohm"
          % (c["microstrip_w0.15_h0.11"], c["microstrip_w0.20_h0.11"], c["fd_microstrip_nomask_thin_w0.15"], c["fd_microstrip_nomask_thin_w0.20"]))
    print("Cross-check, thin conductor, symmetric stripline b 0.225, w 0.15: closed form %.1f ohm, this solver %.1f ohm"
          % (c["sym_stripline_w0.15_b0.225"], c["fd_sym_stripline_w0.15_b0.225"]))


if __name__ == "__main__":
    main()

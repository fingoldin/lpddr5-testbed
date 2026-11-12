#!/usr/bin/env python3
"""Coplanar copper beside the high-speed outer-layer tracks: what is left of it, and what the removed ground floods would have done.

The outer layers carry NO ground pour (floorplan.power.outer_layer_ground): every outer-layer track is a plain microstrip over In1 / In4, which
is what scripts/impedance.py and the delay model of scripts/pcb/common.py compute.  No coplanar correction exists in the delay model and none is
needed.  The only pour edge that runs along a high-speed track is the VDDQ enclave under U1 (B.Cu): 0.30 mm from WCK_N, on ONE side, over 2.4 mm
(DRC rule pour_to_high_speed_track_B_Cu).  This script puts copper of the same thickness beside the track into the 2-D field solver of
scripts/impedance.py (on one side or on both) and compares impedance and delay with the free track.

    python3 scripts/coplanar.py [--json design/ref/coplanar_pour.json]
"""
import json
import sys
from math import sqrt

import numpy as np

import impedance as Z


def ms(w, s=None, gap=None, dx=0.005, t=Z.T_OUT, mask_t=Z.T_MASK, sides=2):
    """Outer microstrip (single, or pair with gap s) with optional grounded pours at 'gap' on both sides (sides=2) or on the left only (sides=1).
    A pair is driven differentially; with a pour on one side it is not symmetric and the full 2 x 2 matrices are used."""
    m = lambda v: int(round(v / dx))        # noqa: E731
    n = 1 if s is None else 2
    marg = m(1.5)
    nx = 2 * marg + n * m(w) + (n - 1) * m(s or 0)
    ny = m(Z.H_PP) + m(1.2)
    er = np.ones((ny, nx))
    er[:m(Z.H_PP), :] = Z.ER
    y0 = m(Z.H_PP)
    cond = np.zeros((ny + 1, nx + 1), int)
    cond[0, :] = -1
    cond[-1, :] = -1
    cond[:, 0] = -1
    cond[:, -1] = -1
    xs = []
    for k in range(n):
        x0 = marg + k * (m(w) + m(s or 0))
        cond[y0:y0 + m(t) + 1, x0:x0 + m(w) + 1] = k + 1
        xs.append((x0, x0 + m(w)))
        er[y0:y0 + m(t) + m(mask_t), max(0, x0 - m(mask_t)):x0 + m(w) + m(mask_t)] = Z.ER_MASK
    if gap is not None:
        xl, xr = xs[0][0] - m(gap), xs[-1][1] + m(gap)
        cond[y0:y0 + m(t) + 1, 1:xl + 1] = -1
        er[y0:y0 + m(t) + m(mask_t), :xl + m(mask_t)] = Z.ER_MASK
        if sides == 2:
            cond[y0:y0 + m(t) + 1, xr:nx] = -1
            er[y0:y0 + m(t) + m(mask_t), xr - m(mask_t):] = Z.ER_MASK
    er[y0:y0 + m(mask_t), :] = np.where(er[y0:y0 + m(mask_t), :] == 1.0, Z.ER_MASK, er[y0:y0 + m(mask_t), :])
    C = Z._solve(er, cond)
    Cv = Z._solve(np.ones_like(er), cond)
    L = Z.MU0 * Z.E0 * np.linalg.inv(Cv)
    if n == 1:
        return {"z": sqrt(L[0, 0] / C[0, 0]), "td": sqrt(L[0, 0] * C[0, 0]) * 1e9}
    # differential drive (+q, -q) of a pair that need not be symmetric (pour on one side): series connection of the two legs
    P = np.linalg.inv(C)
    Cd = 1.0 / (P[0, 0] + P[1, 1] - 2 * P[0, 1])
    Ld = L[0, 0] + L[1, 1] - 2 * L[0, 1]
    return {"z": sqrt(Ld / Cd), "td": sqrt(Ld * Cd) * 1e9}


def table(gap=0.30, sides=2):
    rows = []
    for name, w, s in (("0.15 mm DQ line (F.Cu)", 0.15, None), ("0.20 mm finger track / stub", 0.20, None), ("0.09 mm neck", 0.09, None),
                       ("0.10 / 0.10 mm pair (WCK on B.Cu, RDQS on F.Cu), differential", 0.10, 0.10)):
        a, b = ms(w, s), ms(w, s, gap, sides=sides)
        rows.append({"track": name, "gap_mm": gap, "pour_on_sides": sides, "z_free_ohm": round(a["z"], 2), "z_with_pour_ohm": round(b["z"], 2),
                     "dz_percent": round(100 * (b["z"] / a["z"] - 1), 2), "td_free_ps_mm": round(a["td"], 4), "td_with_pour_ps_mm": round(b["td"], 4),
                     "dtd_percent": round(100 * (b["td"] / a["td"] - 1), 3)})
    return rows


def main():
    wck = [r for r in table(0.30, sides=1) if "pair" in r["track"]]
    l_edge = 2.4                                # mm of the enclave edge beside WCK_N
    d_ps = wck[0]["td_free_ps_mm"] * l_edge * wck[0]["dtd_percent"] / 100
    out = {"status": "NO ground pour on F.Cu / B.Cu (removed: the upstream board has none, its outer layers refer to In1 / In4 only). The delay model of "
                     "scripts/pcb/common.py never contained a coplanar term; gates 6 / 7 give the same delays with and without the floods",
           "model": "2-D quasi-static field solver of scripts/impedance.py; microstrip over the 0.11 mm prepreg, finished copper %.3f mm, mask %.3f mm; grounded "
                    "copper of the same thickness beside the track at the gap, on one side or on both" % (Z.T_OUT, Z.T_MASK),
           "as_built_power_pour_beside_wck_one_side_0p30": wck,
           "as_built_note": "the VDDQ enclave pour under U1 runs 0.30 mm from WCK_N on one side over %.1f mm (differential mode of the pair, pour at the left): "
                            "%.3f ps on the pair, far below the 1 ps pair tolerance; not put into the delay model" % (l_edge, d_ps),
           "for_the_record_removed_gnd_flood_both_sides_0p45": table(0.45),
           "for_the_record_note": "what the removed floods did where they flanked a track on both sides: about -0.3 %% impedance, -0.22 %% delay on a single line "
                                  "(0.45 ps on the longest outer-layer path of 34 mm, 0.2 .. 0.3 ps where the flood stood on one side), -0.03 %% on a pair. "
                                  "Without the floods these shifts do not exist",
           "comparison_both_sides_0p30": table(0.30), "comparison_both_sides_0p20": table(0.20)}
    for k in ("as_built_power_pour_beside_wck_one_side_0p30", "for_the_record_removed_gnd_flood_both_sides_0p45", "comparison_both_sides_0p30", "comparison_both_sides_0p20"):
        print(k)
        for r in out[k]:
            print("  %-64s Z %6.2f -> %6.2f ohm (%+.2f %%)   delay %.4f -> %.4f ps/mm (%+.3f %%)"
                  % (r["track"], r["z_free_ohm"], r["z_with_pour_ohm"], r["dz_percent"], r["td_free_ps_mm"], r["td_with_pour_ps_mm"], r["dtd_percent"]))
    print(out["as_built_note"])
    if "--json" in sys.argv:
        with open(sys.argv[sys.argv.index("--json") + 1], "w") as fh:
            json.dump(out, fh, indent=1)
            fh.write("\n")


if __name__ == "__main__":
    main()

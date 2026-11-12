#!/usr/bin/env python3
"""Quick line simulation of one read DQ, for the question "what does the FPGA pin see without a series resistor
in the read branch?" (DESIGN.md section 3 and section 8 item 29).  Pure numpy, lossless lines, linear driver: pessimistic for
ringing (no skin / dielectric loss, no driver non-linearity, no package inductance to slow the edge).

    ball --trunk 52.4 ohm-- node --[Rtap]-- In3 + finger track 45.6 ohm -- host line 39 ohm -- FPGA read pin (open, C)
                             |- 150 ohm to GND
                             |- 240 ohm -- host write line 39 ohm -- FPGA write pin (Hi-Z, C)

Method of characteristics (Bergeron): every line end is a Thevenin source (Z0 in series with the wave that left the
other end one delay earlier); the nodes are solved with the resistors and with backward-Euler capacitors.

Used by scripts/build_netlist.py (analysis.read.open_end) and re-run by scripts/check_netlist.py.
Run by hand:  python3 scripts/read_linesim.py
"""
import numpy as np

HS_WINDOW = (-0.040, 0.460)      # MIPI D-PHY HS receiver, single-ended input range (V)


def sim(rpu=40.0, rpd=40.0, rtap=0.0, vddq=0.5, cpin=3.1e-12, cball=1.0e-12, tr=80e-12, tau_trunk=100e-12, tau_rd=190e-12,
        tau_host=600e-12, tau_wr=610e-12, z_host=39.0, bit=10e-9, pattern=(0, 1, 0), cw=3.1e-12, dt=2e-12):
    """Voltage at the FPGA read pin, sampled every dt, for the bit pattern (one bit = 'bit' seconds; the first bit is
    the settled starting state).  rpu / rpd: pull-up / pull-down resistance of the DRAM driver."""
    n = int(round(len(pattern) * bit / dt))
    # nodes: 0 ball, 1 divider node, 2 write side of the 240 ohm, 3 write pin, 4 start of the read branch, 5 finger, 6 read pin
    nn = 7
    lines = [(0, 1, 52.4, tau_trunk), (2, 3, z_host, tau_wr), (4, 5, 45.6, tau_rd), (5, 6, z_host, tau_host)]
    res = [(1, -1, 150.0), (1, 2, 240.0), (1, 4, rtap if rtap > 0 else 1e-3)]
    caps = [(0, cball), (3, cw), (6, cpin)]

    def inverse(rdrv):
        g = np.zeros((nn, nn))
        for a, b, z, _tau in lines:
            g[a, a] += 1 / z
            g[b, b] += 1 / z
        for a, b, r in res:
            g[a, a] += 1 / r
            if b >= 0:
                g[b, b] += 1 / r
                g[a, b] -= 1 / r
                g[b, a] -= 1 / r
        for a, c in caps:
            g[a, a] += c / dt
        g[0, 0] += 1 / rdrv
        return np.linalg.inv(g)
    gi = {True: inverse(rpu), False: inverse(rpd)}
    delay = [int(round(l[3] / dt)) for l in lines]
    hist = [(np.zeros(n + d + 2), np.zeros(n + d + 2)) for d in delay]      # wave arriving at end a / at end b
    # start from the DC state of the first bit
    v0 = vddq * 150.0 / (150.0 + rpu) if pattern[0] else 0.0
    v = np.full(nn, v0)
    for ea, eb in hist:
        ea[:] = v0
        eb[:] = v0
    out = np.zeros(n)
    for k in range(n):
        t = k * dt
        ib = min(int(t // bit), len(pattern) - 1)
        lvl, prev = pattern[ib], pattern[max(ib - 1, 0)]
        frac = min(1.0, (t - ib * bit) / tr) if ib > 0 else 1.0
        vs = vddq * (prev + (lvl - prev) * frac)
        up = bool(lvl if frac > 0.5 else prev)
        rdrv = rpu if up else rpd
        cur = np.zeros(nn)
        cur[0] += vs / rdrv
        for (a, b, z, _tau), (ea, eb) in zip(lines, hist):
            cur[a] += ea[k] / z
            cur[b] += eb[k] / z
        for a, c in caps:
            cur[a] += c / dt * v[a]
        v = gi[up] @ cur
        for (a, b, _z, _tau), (ea, eb), d in zip(lines, hist, delay):
            eb[k + d] = 2 * v[a] - ea[k]
            ea[k + d] = 2 * v[b] - eb[k]
        out[k] = v[6]
    return out


def summary(rpu, rpd, rtap=0.0, dt=2e-12, bit=10e-9, **kw):
    """One rising and one falling edge (pattern 0 1 0 at 100 Mb/s): peak and minimum at the read pin, how long the pin is
    outside the HS window, and how far it is from its final level 5 ns and 8 ns after the edge has arrived."""
    p = sim(rpu=rpu, rpd=rpd, rtap=rtap, dt=dt, bit=bit, **kw)
    k1, k2 = int(round(bit / dt)), int(round(2 * bit / dt))
    hi, lo = p[k1:k2], p[k2:]
    vh = kw.get("vddq", 0.5) * 150.0 / (150.0 + rpu)
    t_arr = kw.get("tau_trunk", 100e-12) + kw.get("tau_rd", 190e-12) + kw.get("tau_host", 600e-12)
    ka = int(round(t_arr / dt))

    def at(seg, final, after):
        return float(seg[ka + int(round(after / dt))] - final)
    return dict(pull_up_ohm=rpu, pull_down_ohm=rpd, series_ohm=rtap, dc_high_v=round(vh, 3),
                peak_v=round(float(hi.max()), 3), min_v=round(float(lo.min()), 3),
                above_window_ns=round(float(np.sum(hi > HS_WINDOW[1]) * dt * 1e9), 2),
                below_window_ns=round(float(np.sum(lo < HS_WINDOW[0]) * dt * 1e9), 2),
                high_minus_final_mV_at_5ns=round(at(hi, vh, 5e-9) * 1e3, 1), high_minus_final_mV_at_8ns=round(at(hi, vh, 8e-9) * 1e3, 1),
                low_minus_final_mV_at_5ns=round(at(lo, 0.0, 5e-9) * 1e3, 1), low_minus_final_mV_at_8ns=round(at(lo, 0.0, 8e-9) * 1e3, 1))


CORNERS = (("strong: pull-up and pull-down 28 ohm (40 ohm - 30 %, JESD209-5C bound)", 28.0, 28.0),
           ("nominal: 40 / 40 ohm (MR3 PDDS 40 ohm)", 40.0, 40.0),
           ("weak: 52 / 52 ohm (40 ohm + 30 %)", 52.0, 52.0),
           ("48 ohm setting, strong: 33.6 / 33.6 ohm", 33.6, 33.6),
           ("48 ohm setting, nominal: 48 / 48 ohm", 48.0, 48.0),
           ("60 ohm setting, strong: 42 / 42 ohm", 42.0, 42.0),
           ("60 ohm setting, nominal: 60 / 60 ohm", 60.0, 60.0))


def table(rtap=0.0, **kw):
    return [dict(corner=name, **summary(rpu, rpd, rtap, **kw)) for name, rpu, rpd in CORNERS]


if __name__ == "__main__":
    for rtap in (22.0, 0.0):
        print("series resistor %g ohm" % rtap)
        for r in table(rtap):
            print("  %-68s DC high %.3f  peak %.3f V  min %+.3f V  outside the HS window %.2f / %.2f ns  from final at 5 ns %+.0f / %+.0f mV, at 8 ns %+.0f / %+.0f mV"
                  % (r["corner"], r["dc_high_v"], r["peak_v"], r["min_v"], r["above_window_ns"], r["below_window_ns"],
                     r["high_minus_final_mV_at_5ns"], r["low_minus_final_mV_at_5ns"], r["high_minus_final_mV_at_8ns"], r["low_minus_final_mV_at_8ns"]))

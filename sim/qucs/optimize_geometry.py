"""Optimise the trace geometry of the J1 -> AD8317 input with Qucs (qucsator).

The circuit is the as-built board of case C1 (coplanar lines on the real stackup,
ground plane B.Cu 1.44 mm below, In1/In2 void, see run_s11_study.chain_C). The
starting point is the existing geometry; the optimiser changes only what a
layout change can change, inside manufacturing limits:

  default variables   slot of the SMA pad,  common slot g of the line sections,
                      widths of the five line sections (L1..L5)
  --free-lengths      also the lengths of L1..L5 (moves R1, R2, C1 on the board)
  --shunt             also the value of the two shunt resistors R1 = R2

Goal: the smallest worst-case |S11| over fmin..fmax (default 1 MHz..10 GHz). The
AD8317 input data are uncertain above 4 GHz (datasheet table and Figure 15 chart
disagree), so the default is a ROBUST design: the worst case over both chip data
sets ("table" and "chart"). A small term of the mean |S11| in dB breaks ties.

Method: differential evolution (numpy only; every evaluation is one or two
qucsator runs of about 0.1 s, run in parallel), started from a population that
holds the as-built design, then a pattern search that snaps the result to a 0.01 mm
grid. The sensitivity of the optimum to +-10 % on every variable is reported.

    python optimize_geometry.py                     # default: both chip data sets
    python optimize_geometry.py --chips table       # only the datasheet table
    python optimize_geometry.py --free-lengths --shunt --gens 60
    python optimize_geometry.py --selftest          # the as-built design = case C1

Outputs (in this folder, <tag> = geom, geom_shunt, geom_len, geom_len_shunt): optimized_<tag>.json,
case_OPT_<tag>_cpw_bcu[_chart]_ad8317.net / .sch, s11_optimized_<tag>_1MHz_10GHz.png.
Open a case_OPT_*.sch in Qucs and press F2.
"""
import argparse
import itertools
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_s11_study as st  # noqa: E402

st.TIMEOUT = 20      # a run that takes longer is a geometry outside the model range
CHIP_FILES = {"table": st.S2P, "chart": st.S2P_CHART}
PENALTY = 60.0        # dB, for a geometry that qucsator rejects
_counter = itertools.count()
_lock = threading.Lock()


# --------------------------------------------------------------------------- variables
class Space:
    """The optimisation variables: name, lower, upper bound, start value; geometry mapping.

    Bounds follow what the board can carry: a trace is at least `min_feature` wide
    (default 0.127 mm = 5 mil) and the slot at least that; the trace sections that carry
    the 0402 pads of R1/R2/C1 are at most `max_width` wide (default 0.6 mm, a pad width);
    the slot of the line sections is at most `max_slot` (0.30 mm); the slot around the
    SMA pad is 0.15 .. 0.80 mm (its copper width is the footprint, 0.8 mm).
    """

    def __init__(self, min_feature, free_lengths, shunt, max_width=0.6, max_slot=0.30):
        d = st.DEFAULT_GEOM
        mf = float(min_feature)
        self.v = [("pad_slot", max(mf, 0.15), 0.80, d["pad"][1]), ("g", mf, float(max_slot), d["g"])]
        for k in ("L1", "L2", "L3", "L4", "L5"):
            hi = min(float(max_width), 0.40) if k in ("L4", "L5") else float(max_width)
            self.v.append(("w_" + k, mf, hi, d["w"][k]))
        if free_lengths:
            for k in ("L1", "L2", "L3", "L4", "L5"):
                self.v.append(("l_" + k, 0.6 * d["l"][k], 1.4 * d["l"][k], d["l"][k]))
        if shunt:
            self.v.append(("r_shunt", 50.0, 300.0, d["r_shunt"]))
        self.names = [n for n, *_ in self.v]
        self.lo = np.array([lo for _, lo, _, _ in self.v])
        self.hi = np.array([hi for _, _, hi, _ in self.v])
        self.x0 = np.array([x0 for *_, x0 in self.v])

    def geom(self, x):
        p = dict(zip(self.names, x))
        g = {"pad": (st.DEFAULT_GEOM["pad"][0], p["pad_slot"], st.DEFAULT_GEOM["pad"][2]),
             "g": p["g"], "w": {k: p["w_" + k] for k in ("L1", "L2", "L3", "L4", "L5")}}
        if "l_L1" in p:
            g["l"] = {k: p["l_" + k] for k in ("L1", "L2", "L3", "L4", "L5")}
        if "r_shunt" in p:
            g["r_shunt"] = p["r_shunt"]
        return g


# --------------------------------------------------------------------------- evaluation
def s11_curves(geom, chips, npts):
    """S11 (complex) for each chip data set; None for a geometry that qucsator rejects."""
    out = {}
    for chip in chips:
        name = "opt_tmp_%d" % next(_counter)
        try:
            f, s = st.run(name, st.netlist_C("cpw", "bcu", CHIP_FILES[chip], geom, npts))
        except SystemExit:
            out[chip] = (None, None)
        else:
            out[chip] = (f, s)
        finally:
            for ext in (".net", ".dat"):
                (HERE / (name + ext)).unlink(missing_ok=True)
    return out


def cost_of(curves, fmin, fmax):
    """Worst case over chip sets of (worst |S11| in band + 0.05 * mean |S11| in dB)."""
    worst = -1e9
    for f, s in curves.values():
        if f is None:
            return PENALTY
        m = (f >= fmin) & (f <= fmax)
        d = st.db(s[m])
        worst = max(worst, float(d.max()) + 0.05 * float(d.mean()))
    return worst


class Evaluator:
    def __init__(self, space, chips, fmin, fmax, npts):
        self.space, self.chips, self.fmin, self.fmax, self.npts = space, chips, fmin, fmax, npts
        self.cache, self.n = {}, 0

    def __call__(self, x):
        key = tuple(np.round(x, 5))
        if key not in self.cache:
            self.cache[key] = cost_of(s11_curves(self.space.geom(x), self.chips, self.npts), self.fmin, self.fmax)
            self.n += 1
        return self.cache[key]


# --------------------------------------------------------------------------- optimiser
def differential_evolution(ev, space, pop, gens, seed, workers, log):
    rng = np.random.default_rng(seed)
    n = len(space.names)
    P = np.empty((pop, n))
    P[0] = space.x0
    k = max(2, pop // 3)                                   # a third around the as-built design
    P[1:k] = np.clip(space.x0 * rng.uniform(0.8, 1.25, (k - 1, n)), space.lo, space.hi)
    P[k:] = space.lo + rng.random((pop - k, n)) * (space.hi - space.lo)
    with ThreadPoolExecutor(workers) as ex:
        fit = np.array(list(ex.map(ev, P)))
        for gen in range(gens):
            best = P[fit.argmin()]
            trial = np.empty_like(P)
            for i in range(pop):
                a, b, c = rng.choice(pop, 3, replace=False)
                F = rng.uniform(0.4, 0.9)
                mutant = (best if rng.random() < 0.5 else P[a]) + F * (P[b] - P[c])
                cross = rng.random(n) < 0.8
                cross[rng.integers(n)] = True
                t = np.where(cross, mutant, P[i])
                lo_bad, hi_bad = t < space.lo, t > space.hi
                t[lo_bad] = space.lo[lo_bad] + rng.random(lo_bad.sum()) * (P[i][lo_bad] - space.lo[lo_bad])
                t[hi_bad] = space.hi[hi_bad] - rng.random(hi_bad.sum()) * (space.hi[hi_bad] - P[i][hi_bad])
                trial[i] = t
            tf = np.array(list(ex.map(ev, trial)))
            better = tf <= fit
            P[better], fit[better] = trial[better], tf[better]
            log("gen %3d  best %.2f dB   mean %.2f dB   evaluations %d" % (gen + 1, fit.min(), fit.mean(), ev.n))
    return P[fit.argmin()].copy(), float(fit.min())


def pattern_search(ev, space, x, c, grid=0.01, log=print):
    """Coordinate search with a shrinking step, ending on the manufacturing grid."""
    x = np.clip(x, space.lo, space.hi)
    step = np.where(np.array([n.startswith(("w_", "g", "pad", "l_")) for n in space.names]), 0.02, 2.0)
    while True:
        improved = False
        for i in range(len(x)):
            for sgn in (+1, -1):
                t = x.copy()
                t[i] = np.clip(t[i] + sgn * step[i], space.lo[i], space.hi[i])
                ct = ev(t)
                if ct < c - 1e-9:
                    x, c, improved = t, ct, True
        if not improved:
            if step.max() <= grid + 1e-12:
                break
            step = np.maximum(step / 2, grid)
            log("  pattern search: step %.3f  cost %.3f dB" % (step.max(), c))
    snapped = x.copy()
    for i, nme in enumerate(space.names):
        snapped[i] = np.clip(round(x[i] / grid) * grid if nme != "r_shunt" else round(x[i]), space.lo[i], space.hi[i])
    cs = ev(snapped)
    return (snapped, cs) if cs <= c + 0.05 else (x, c)


# --------------------------------------------------------------------------- reporting
BANDS = [(1e6, 1e8), (1e8, 1e9), (1e9, 3e9), (3e9, 6e9), (6e9, 1e10)]


def band_table(curves):
    rows = {}
    for chip, (f, s) in curves.items():
        d = st.db(s)
        rows[chip] = [round(float(d[(f >= lo * (1 - 1e-9)) & (f <= hi * (1 + 1e-9))].max()), 2) for lo, hi in BANDS]
        rows[chip + "_pct_below_-10dB"] = round(100 * float((d < -10).mean()), 1)
    return rows


def sensitivity(ev, space, x, c0, rel=0.10):
    out = {}
    for i, nme in enumerate(space.names):
        d = []
        for sgn in (+1, -1):
            t = x.copy()
            t[i] = np.clip(t[i] * (1 + sgn * rel), space.lo[i], space.hi[i])
            d.append(round(ev(t) - c0, 2))
        out[nme] = d
    return out


def write_case_files(tag, g):
    """Netlists and Qucs schematics of a geometry (the .sch files hold absolute paths of this machine)."""
    import make_qucs_schematics as mq
    for sfx, chip in (("", None), ("_chart", st.S2P_CHART)):
        name = "case_OPT_%s_cpw_bcu%s_ad8317" % (tag, sfx)
        (HERE / (name + ".net")).write_text(st.netlist_C("cpw", "bcu", chip, g), encoding="ascii", newline="\n")
        sch = mq.C_case(name, "OPT %s: optimised coplanar geometry (as built: C1), chip data %s" % (tag, "chart" if sfx else "table"),
                        "cpw", "bcu", chip, g)
        (HERE / (name + ".sch")).write_text(sch.text(), encoding="ascii", newline="\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--chips", default="table,chart", help="chip data sets: table, chart (comma separated)")
    ap.add_argument("--fmin", type=float, default=1e6)
    ap.add_argument("--fmax", type=float, default=1e10)
    ap.add_argument("--min-feature", type=float, default=0.127, help="smallest trace width / slot in mm (default 0.127 = 5 mil)")
    ap.add_argument("--max-width", type=float, default=0.6, help="widest trace section in mm (default 0.6, an 0402 pad)")
    ap.add_argument("--max-slot", type=float, default=0.30, help="widest slot of the line sections in mm (default 0.30)")
    ap.add_argument("--free-lengths", action="store_true")
    ap.add_argument("--shunt", action="store_true")
    ap.add_argument("--pop", type=int, default=0, help="population (default 8 x variables)")
    ap.add_argument("--gens", type=int, default=40)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--workers", type=int, default=max(2, (os.cpu_count() or 4) - 1))
    ap.add_argument("--npts", type=int, default=81, help="frequency points while optimising (final: 401)")
    ap.add_argument("--tag", default="", help="name of this run in the output files (default: geom, geom_shunt, geom_len, geom_len_shunt)")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--regen", metavar="TAG", help="rewrite the .net / .sch files of a finished run from optimized_<TAG>.json (paths of this machine)")
    a = ap.parse_args()

    if a.regen:
        res = json.loads((HERE / ("optimized_%s.json" % a.regen)).read_text())
        sp = Space(res["min_feature_mm"], res["free_lengths"], res["shunt"])
        write_case_files(a.regen, sp.geom(np.array([res["variables"][n]["optimized"] for n in sp.names])))
        print("regenerated case_OPT_%s_*.net/.sch" % a.regen)
        return 0
    chips = [c.strip() for c in a.chips.split(",")]
    tag = a.tag or "geom" + ("_len" if a.free_lengths else "") + ("_shunt" if a.shunt else "")
    space = Space(a.min_feature, a.free_lengths, a.shunt, a.max_width, a.max_slot)
    ev = Evaluator(space, chips, a.fmin, a.fmax, a.npts)
    log = lambda m: print(m, flush=True)

    if a.selftest:
        full = s11_curves(space.geom(space.x0), ["table"], st.NPTS)
        t = band_table(full)["table"]
        ref = [-26.45, -24.72, -11.5, -9.18, -9.36]      # case C1, run_s11_study.py
        ok = all(abs(x - y) < 0.05 for x, y in zip(t, ref))
        log("as-built worst |S11| per band %s  expected %s -> %s" % (t, ref, "OK" if ok else "MISMATCH"))
        return 0 if ok else 1

    t0 = time.time()
    c0 = ev(space.x0)
    log("variables: %s" % ", ".join("%s [%.2f..%.2f]" % (n, lo, hi) for n, lo, hi, _ in space.v))
    log("as-built cost %.2f dB (worst over %s, %g..%g Hz)" % (c0, "+".join(chips), a.fmin, a.fmax))
    pop = a.pop or 8 * len(space.names)
    x, c = differential_evolution(ev, space, pop, a.gens, a.seed, a.workers, log)
    log("differential evolution: %.2f dB after %d evaluations (%.0f s)" % (c, ev.n, time.time() - t0))
    x, c = pattern_search(ev, space, x, c, log=log)
    log("after pattern search and 0.01 mm snap: %.2f dB" % c)

    # final numbers at 401 points
    init_curves = s11_curves(space.geom(space.x0), chips, st.NPTS)
    best_curves = s11_curves(space.geom(x), chips, st.NPTS)
    sens = sensitivity(ev, space, x, c)
    result = {
        "objective": "worst |S11| (dB) over %g..%g Hz + 0.05*mean, worst case over chip sets %s" % (a.fmin, a.fmax, chips),
        "cost_as_built_dB": round(c0, 2), "cost_optimized_dB": round(c, 2),
        "variables": {n: {"as_built": round(float(x0), 4), "optimized": round(float(v), 4)}
                      for n, x0, v in zip(space.names, space.x0, x)},
        "worst_S11_per_band_dB": {"bands_Hz": BANDS, "as_built": band_table(init_curves), "optimized": band_table(best_curves)},
        "sensitivity_dB_for_plus_minus_10pct": sens,
        "evaluations": ev.n, "seconds": round(time.time() - t0, 1),
        "min_feature_mm": a.min_feature, "free_lengths": a.free_lengths, "shunt": a.shunt,
    }
    (HERE / ("optimized_%s.json" % tag)).write_text(json.dumps(result, indent=1))

    write_case_files(tag, space.geom(x))

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(11, 6))
    colors = {"table": "tab:blue", "chart": "tab:red"}
    for chip in chips:
        f, s = init_curves[chip]
        ax.semilogx(f / 1e6, st.db(s), color=colors[chip], ls="--", label="as built, chip data: " + chip)
        f, s = best_curves[chip]
        ax.semilogx(f / 1e6, st.db(s), color=colors[chip], label="optimised, chip data: " + chip)
    ax.axhline(-10, color="k", lw=.6, ls=":")
    ax.set_xlabel("MHz")
    ax.set_ylabel("|S11| [dB]")
    ax.set_ylim(-45, 2)
    ax.grid(True, which="both", alpha=.3)
    ax.legend(fontsize=8, loc="lower left")
    ax.set_title("J1 -> AD8317: as-built vs optimised geometry (coplanar, B.Cu reference)")
    fig.tight_layout()
    fig.savefig(HERE / ("s11_optimized_%s_1MHz_10GHz.png" % tag), dpi=130)

    # console summary
    log("\nGEOMETRY (mm unless noted)           as built   optimised   Z0 [ohm] (grounded CPW, Simons)")
    for n, x0, v in zip(space.names, space.x0, x):
        z = ""
        if n.startswith("w_"):
            z = "%.0f -> %.0f" % (st.cpwg(x0 * 1e-3, space.x0[1] * 1e-3, st.H, st.ER)[0],
                                   st.cpwg(v * 1e-3, x[1] * 1e-3, st.H, st.ER)[0])
        log("  %-12s %12.3f %11.3f   %s" % (n, x0, v, z))
    log("\nworst |S11| per band [dB]  1-100 MHz / 0.1-1 / 1-3 / 3-6 / 6-10 GHz")
    for chip in chips:
        log("  chip data %-6s as built  %s   below -10 dB: %5.1f %%" % (
            chip, band_table(init_curves)[chip], band_table(init_curves)[chip + "_pct_below_-10dB"]))
        log("  chip data %-6s optimised %s   below -10 dB: %5.1f %%" % (
            chip, band_table(best_curves)[chip], band_table(best_curves)[chip + "_pct_below_-10dB"]))
    log("\nsensitivity (cost change in dB for -10%% / +10%% of each variable): %s" % sens)
    log("wrote optimized_%s.json, case_OPT_%s_cpw_bcu_ad8317.net/.sch (+ _chart), s11_optimized_%s_1MHz_10GHz.png" % (tag, tag, tag))
    return 0


if __name__ == "__main__":
    sys.exit(main())

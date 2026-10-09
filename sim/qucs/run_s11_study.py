"""S11 study of the J1 -> AD8317 RF input, 1 MHz - 10 GHz.

Cases (all solved with qucsator, 401 log points):
  A0  rfsim 3-port, ports 2/3 terminated 50 ohm each        (what rfsim assumed)
  A1  rfsim 3-port + AD8317 Zin between INHI (P2) and INLO (P3)   <- requested
  B   circuit model of the same board (CPWG stackup lines + R1/R2/C1/C2 with
      parasitics) + AD8317 Zin: cross-check of the EM result
  B0  same circuit model with 50 ohm on both pads
  B_chart  as B, with the Figure-15 chart points of the AD8317 at >= 5.8 GHz
           (the datasheet table and the chart disagree there)
Outputs: *.net / *.dat, s11_results.csv, s11_1MHz_10GHz.png, summary on stdout.

Limits of model B (a circuit model, not a field solution): no connector
launch, no pad-to-trace step capacitance, no via or ground-return
inductance for R1/R2/C2 beyond 0.25 nH, no radiation, no coupling between the
INHI and INLO lines; the pad is a 1.1 mm piece of grounded CPW. It is a
cross-check and a design aid, not a replacement for a VNA measurement.

qucsator is found with the QUCSATOR environment variable, then on PATH, then
at the default Windows install path below. Usage:
    python run_s11_study.py [--rfsim path\to\results.s3p]
(--rfsim rebuilds the 3-port file for Qucs from an rfsim result, flat from
10 MHz down to 1 MHz.)
"""
import os, re, shutil, subprocess, sys
from pathlib import Path
import numpy as np
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parent / "analysis"))
from ad8317_input_model import zin, gamma, file_names
from s3p_io import read_touchstone, write_touchstone

_DEFAULT_QUCSATOR = r"C:\Users\<user>\<cloud-folder>\tools\RF_Tools\qucs-0.0.19\bin\qucsator.exe"


def find_qucsator():
    for cand in (os.environ.get("QUCSATOR"), shutil.which("qucsator"), _DEFAULT_QUCSATOR):
        if cand and os.path.isfile(cand):
            return cand
    raise SystemExit("qucsator not found: set the QUCSATOR environment variable "
                     "to the full path of qucsator(.exe)")


QUCSATOR = find_qucsator()
S3P = "rfsim_J1_U1_3port_1MHz_10GHz.s3p"
S2P = file_names("table")[1]
S2P_CHART = file_names("chart")[1]
NPTS = 401
SWEEP = '.SP:SP1 Type="log" Start="1e6" Stop="1e10" Points="%d"\n' % NPTS
PORT = 'Pac:P1 n1 gnd Num="1" Z="50 Ohm" P="0 dBm" f="1e9"\n'

# Stackup. STACKUP=pcb (default): saved KiCad stackup. In1.Cu and In2.Cu are VOID under the whole RF input
# (J1 -> U1, see doc), so the only plane below the line is B.Cu, 1.44 mm away (FR-4 er ~4.5, tand 0.02).
# STACKUP=dialog: generic equal-spacing stackup that the rfsim dialog used (er 4.5, GND 0.533 mm below) - WRONG here.
import os
if os.environ.get("STACKUP", "pcb") == "dialog":
    ER, TAND, H = 4.5, 0.02, 0.533333e-3
else:
    ER, TAND, H = 4.5, 0.02, 1.44e-3

def _K(k):  # complete elliptic integral of the first kind via AGM
    a, b = 1.0, np.sqrt(1 - k * k)
    for _ in range(30):
        a, b = (a + b) / 2, np.sqrt(a * b)
    return np.pi / (2 * a)


def cpwg(w, g, h=H, er=ER):
    """Z0 [ohm], eps_eff of grounded CPW (Simons), thickness neglected. w,g,h in m."""
    a, b = w / 2, w / 2 + g
    k = a / b
    k1 = np.tanh(np.pi * a / (2 * h)) / np.tanh(np.pi * b / (2 * h))
    kp, k1p = np.sqrt(1 - k * k), np.sqrt(1 - k1 * k1)
    r = (_K(kp) / _K(k)) * (_K(k1) / _K(k1p))
    ee = (1 + er * r) / (1 + r)
    z0 = 60 * np.pi / np.sqrt(ee) / (_K(k) / _K(kp) + _K(k1) / _K(k1p))
    return z0, ee


def tl_line(name, n1, n2, w_mm, g_mm, l_mm, f_loss=5e9):
    z0, ee = cpwg(w_mm * 1e-3, g_mm * 1e-3)
    lam0 = 3e8 / f_loss
    alpha = np.pi / lam0 * (ER / np.sqrt(ee)) * (ee - 1) / (ER - 1) * TAND  # Np/m, dielectric
    txt = ('TLIN:%s %s %s Z="%.2f Ohm" L="%.6e" Alpha="%.4f" Temp="26.85"\n'
           % (name, n1, n2, z0, l_mm * 1e-3 * np.sqrt(ee), alpha))
    return txt, z0, ee


def netlist_A(chip=True):
    s = "# A: rfsim 3-port " + ("+ AD8317 Zin" if chip else "+ 50 ohm on ports 2,3") + "\n" + PORT
    s += 'SPfile:EM n1 n2 n3 gnd File="%s" Data="rectangular" Interpolator="linear" duringDC="open"\n' % S3P
    if chip:
        s += 'SPfile:CHIP n2 n3 gnd File="%s" Data="rectangular" Interpolator="linear" duringDC="open"\n' % S2P
    else:
        s += 'R:RP2 n2 gnd R="50 Ohm"\nR:RP3 n3 gnd R="50 Ohm"\n'
    return s + SWEEP


def width_for_z0(z_target, gap_mm):
    """Trace width [mm] giving z_target on the grounded CPW (bisection)."""
    lo, hi = 0.1, 3.0
    for _ in range(50):
        mid = (lo + hi) / 2
        if cpwg(mid * 1e-3, gap_mm * 1e-3)[0] > z_target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def netlist_B(load="chip", z50=False, chip_file=None):
    """Circuit model. Segment geometry read from the rfsim layout picture (mm).
    z50=True: every trace section after the pad re-drawn as 50 ohm (same 0.2 mm gap)."""
    segs = [  # name, from, to, w, gap, length
        ("PAD", "n1", "a", 0.80, 0.49, 1.10),    # SMA pad, port at its centre
        ("L1", "a", "r1", 0.2032, 0.20, 0.65),
        ("L2", "r1", "r2", 0.30, 0.20, 1.00),
        ("L3", "r2", "c1a", 0.2032, 0.20, 1.10),
        ("L4", "c1b", "inhi", 0.1524, 0.20, 1.68),
        ("L5", "inlo", "c2b", 0.25, 0.20, 1.70),
    ]
    if z50:
        w50 = width_for_z0(50.0, 0.20)
        segs = [sg if sg[0] == "PAD" else (sg[0], sg[1], sg[2], round(w50, 3), sg[4], sg[5]) for sg in segs]
    s = "# B: circuit model of J1->U1 + " + ("AD8317 Zin" if load == "chip" else "50 ohm pads") + "\n" + PORT
    info = []
    for nm, a, b, w, g, l in segs:
        t, z0, ee = tl_line(nm, a, b, w, g, l)
        s += t
        info.append((nm, w, g, l, z0, ee))
    for r, node in (("R1", "r1"), ("R2", "r2")):   # 100 ohm 0402 shunt to GND, ESL 0.25 nH
        s += 'R:%s %s %sx R="100 Ohm" Temp="26.85"\nL:%sL %sx gnd L="0.25 nH"\n' % (r, node, r, r, r)
    s += 'C:C1 c1a c1m C="47 nF"\nL:C1L c1m c1n L="0.25 nH"\nR:C1R c1n c1b R="0.035 Ohm"\n'
    s += 'C:C2 c2b c2m C="47 nF"\nL:C2L c2m c2n L="0.25 nH"\nR:C2R c2n gnd R="0.035 Ohm"\n'
    if load == "chip":
        s += 'SPfile:CHIP inhi inlo gnd File="%s" Data="rectangular" Interpolator="linear" duringDC="open"\n' % (chip_file or S2P)
    else:
        s += 'R:RP2 inhi gnd R="50 Ohm"\nR:RP3 inlo gnd R="50 Ohm"\n'
    return s + SWEEP, info


def run(name, text):
    net = HERE / (name + ".net")
    dat = HERE / (name + ".dat")
    net.write_text(text, encoding="ascii", newline="\n")
    if dat.exists():
        dat.unlink()  # a stale result must not pass for a new one
    r = subprocess.run([QUCSATOR, "-i", net.name, "-o", dat.name], cwd=HERE, capture_output=True, text=True)
    out = r.stdout + r.stderr
    bad = [ln for ln in out.splitlines() if re.search(r"\b(error|syntax|undefined|not found)\b", ln, re.I)
           and not re.search(r"\b0 errors?\b", ln, re.I)]
    if r.returncode != 0 or bad or not dat.exists():
        raise SystemExit("%s: qucsator failed (rc=%s)\n%s" % (name, r.returncode, "\n".join(bad or out.splitlines()[-8:])))
    t = dat.read_text()
    if "<dep S[1,1] frequency>" not in t:
        raise SystemExit(name + ": no S[1,1] in the qucsator result")
    f = np.array([float(x) for x in re.search(r"<indep frequency \d+>\n(.*?)</indep>", t, re.S).group(1).split()])
    body = re.search(r"<dep S\[1,1\] frequency>\n(.*?)</dep>", t, re.S).group(1).split()
    pat = re.compile(r"([+-][\d.]+e[+-]\d+)([+-])j([\d.]+e[+-]\d+)")
    s11 = []
    for tok in body:
        a, sg, b = pat.fullmatch(tok).groups()
        s11.append(complex(float(a), float(b) * (1 if sg == "+" else -1)))
    return f, np.array(s11)


def numpy_check(f):
    """Independent check of case A1: reduce the 3-port + load with Y-matrices."""
    fe, S, z0 = read_touchstone(HERE / S3P)
    out = []
    for fk in f:
        Sk = np.array([[np.interp(fk, fe, S[:, i, j].real) + 1j * np.interp(fk, fe, S[:, i, j].imag)
                        for j in range(3)] for i in range(3)])
        Y = (np.eye(3) - Sk) @ np.linalg.inv(np.eye(3) + Sk) / z0
        yl = 1.0 / zin(np.array([fk]))[0]
        YL = yl * np.array([[1, -1], [-1, 1]])
        yin = Y[0, 0] - (Y[0:1, 1:] @ np.linalg.inv(Y[1:, 1:] + YL) @ Y[1:, 0:1])[0, 0]
        zi = 1 / yin
        out.append((zi - z0) / (zi + z0))
    return np.array(out)


def db(x):
    return 20 * np.log10(np.maximum(np.abs(x), 1e-12))


def build_s3p(src):
    """Write the Qucs-readable 3-port from an rfsim result, flat below its first point."""
    f, S, z0 = read_touchstone(src)
    if f[0] > 1e6:
        f = np.r_[1e6, f]
        S = np.concatenate([S[:1], S])
    write_touchstone(HERE / S3P, f, S,
                     ["rfsim result %s: P1 = J1 pad, P2 = U1:1 (INHI), P3 = U1:8 (INLO)" % Path(src).name,
                      "points below the first rfsim frequency are copies of it (flat extension)"], z0)


def main():
    if "--rfsim" in sys.argv:
        build_s3p(sys.argv[sys.argv.index("--rfsim") + 1])
    if not (HERE / S3P).exists():
        raise SystemExit("%s is missing: run with --rfsim <results.s3p>" % S3P)
    res = {}
    f, res["A0 rfsim 3-port, P2/P3 = 50 ohm"] = run("case_A0_rfsim_50ohm", netlist_A(False))
    _, res["A1 rfsim 3-port + AD8317"] = run("case_A1_rfsim_ad8317", netlist_A(True))
    tb, info = netlist_B("chip")
    _, res["B  circuit model + AD8317"] = run("case_B_circuit_ad8317", tb)
    _, res["B_chart circuit model, AD8317 chart points >= 5.8 GHz"] = run(
        "case_B_chart_circuit_ad8317", netlist_B("chip", chip_file=S2P_CHART)[0])
    _, res["B0 circuit model, P2/P3 = 50 ohm"] = run("case_B0_circuit_50ohm", netlist_B("50")[0])
    tb2, info2 = netlist_B("chip", z50=True)
    _, res["B2 circuit model, 50 ohm traces + AD8317"] = run("case_B2_circuit_z50_ad8317", tb2)
    chk = numpy_check(f[::20])
    err = np.max(np.abs(chk - res["A1 rfsim 3-port + AD8317"][::20]))
    print("numpy vs qucsator, case A1: max |dS11| = %.2e" % err)
    print("B line segments (name, w mm, gap mm, L mm, Z0 ohm, eps_eff):")
    for r in info:
        print("  %-4s w=%.4f g=%.2f L=%.2f  Z0=%.1f  eeff=%.2f" % r)
    print("B2 (50 ohm re-draw): trace width %.3f mm (gap 0.20), Z0=%.1f" % (info2[1][1], info2[1][4]))

    with open(HERE / "s11_results.csv", "w", newline="\n") as fh:
        fh.write("freq_Hz," + ",".join(k.split()[0] + "_dB" for k in res) + "\n")
        for i, fk in enumerate(f):
            fh.write("%.6e," % fk + ",".join("%.3f" % db(v[i]) for v in res.values()) + "\n")

    fig, ax = plt.subplots(2, 1, figsize=(11, 8.5), sharex=True)
    sty = {"A0": ("tab:gray", "--"), "A1": ("tab:red", "-"), "B ": ("tab:blue", "-"), "B0": ("tab:cyan", "--"),
           "B2": ("tab:green", "-"), "B_": ("tab:purple", ":")}
    for k, v in res.items():
        c, ls = sty[k[:2]]
        ax[0].semilogx(f / 1e6, db(v), color=c, ls=ls, label=k)
    ax[0].axhline(-10, color="k", lw=.6, ls=":")
    ax[0].set_ylabel("|S11| [dB]")
    ax[0].set_ylim(-45, 2)
    ax[0].grid(True, which="both", alpha=.3)
    ax[0].legend(fontsize=8, loc="lower left")
    ax[0].set_title("J1 (SMA pad) S11 into AD8317 input, 1 MHz - 10 GHz")
    ax[1].semilogx(f / 1e6, db(gamma(zin(f))), color="k", label="AD8317 INHI-INLO alone vs 50 ohm")
    ax[1].set_ylabel("|S11| of chip alone [dB]")
    ax[1].set_xlabel("MHz")
    ax[1].grid(True, which="both", alpha=.3)
    ax[1].legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(HERE / "s11_1MHz_10GHz.png", dpi=130)

    bands = [(1e6, 1e8), (1e8, 1e9), (1e9, 3e9), (3e9, 6e9), (6e9, 1e10)]
    print("\nworst/best |S11| [dB] per band")
    for k, v in res.items():
        print(k)
        for lo, hi in bands:
            m = (f >= lo) & (f <= hi)
            d = db(v[m])
            print("   %6.0f-%6.0f MHz  worst %6.2f dB @ %7.0f MHz   best %6.2f dB @ %7.0f MHz"
                  % (lo / 1e6, hi / 1e6, d.max(), f[m][d.argmax()] / 1e6, d.min(), f[m][d.argmin()] / 1e6))
        print("   fraction of log-sweep with S11 < -10 dB: %.0f %%" % (100 * (db(v) < -10).mean()))


if __name__ == "__main__":
    main()

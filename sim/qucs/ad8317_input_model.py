"""AD8317 INHI-INLO input impedance model, 1 MHz - 10 GHz (numpy only).

Source: AD8317 Rev. D datasheet
  - p.10: low frequency input impedance ~500 ohm || 0.7 pF
  - pp.3-4 Table 1: INHI input impedance as parallel R||C at 0.9/1.9/2.2/3.6/5.8/8.0 GHz
  - p.9 Figure 15: Smith chart of the input with no termination (the only
    information at and above 10 GHz; read off the chart, so approximate)
INLO is the RF common (ac-coupled to ground), so the part is a two-terminal
load Zin between INHI and INLO. Between anchors G = 1/R and C are
interpolated linearly vs log10(f).

Variants (the table and the chart disagree at 5.8 GHz: 110 ohm || 0.05 pF in
the table, about 210 ohm || 0.5 pF read from the chart):
  "table" (default) table up to 8 GHz, chart point at 10 GHz
  "hold"            table up to 8 GHz, 8 GHz value held to 10 GHz (old model)
  "chart"           table up to 3.6 GHz, chart points at 5.8, 8 and 10 GHz
"""
from pathlib import Path
import numpy as np

# Table 1 anchors: f [Hz], R [ohm], C [F]
_TABLE = [(0.9e9, 1500.0, 0.33e-12), (1.9e9, 950.0, 0.38e-12), (2.2e9, 810.0, 0.39e-12),
          (3.6e9, 300.0, 0.33e-12), (5.8e9, 110.0, 0.05e-12), (8.0e9, 28.0, 0.79e-12)]
_LF = (1e6, 500.0, 0.7e-12)
# Figure 15 readings of Gamma (vs 50 ohm) at the labelled frequencies
_CHART = {5.8e9: 0.02 - 0.78j, 8.0e9: -0.58 - 0.38j, 10.0e9: -0.18 - 0.86j}
VARIANTS = ("table", "hold", "chart")


def _gc_from_gamma(f, gam, z0=50.0):
    """Parallel G [S] and C [F] equivalent of a reflection coefficient."""
    y = 1.0 / (z0 * (1 + gam) / (1 - gam))
    return max(y.real, 1e-6), y.imag / (2 * np.pi * f)


def _anchors(variant, lf_r=None, lf_c=None):
    if variant not in VARIANTS:
        raise ValueError("variant must be one of %s" % (VARIANTS,))
    lf = (_LF[0], lf_r or _LF[1], lf_c or _LF[2])
    pts = [(f, 1.0 / r, c) for f, r, c in [lf] + _TABLE]
    if variant == "table":
        pts.append((10e9,) + _gc_from_gamma(10e9, _CHART[10e9]))
    elif variant == "hold":
        pts.append((10e9, 1.0 / _TABLE[-1][1], _TABLE[-1][2]))
    else:  # chart
        pts = [p for p in pts if p[0] <= 3.6e9]
        pts += [(f,) + _gc_from_gamma(f, g) for f, g in sorted(_CHART.items())]
    a = np.array(pts)
    return a[:, 0], a[:, 1], a[:, 2]


def zin(f, variant="table", lf_r=None, lf_c=None):
    """Complex input impedance (ohm) at frequencies f [Hz]."""
    f = np.asarray(f, float)
    fa, ga, ca = _anchors(variant, lf_r, lf_c)
    x, xa = np.log10(f), np.log10(fa)
    g = np.interp(x, xa, ga)
    c = np.interp(x, xa, ca)
    return 1.0 / (g + 1j * 2 * np.pi * f * c)


def gamma(z, z0=50.0):
    return (z - z0) / (z + z0)


def series_2port(z, z0=50.0):
    """S of a series impedance between port 1 and port 2 -> [nf,2,2]."""
    d = z + 2 * z0
    s = np.empty((len(z), 2, 2), complex)
    s[:, 0, 0] = s[:, 1, 1] = z / d
    s[:, 0, 1] = s[:, 1, 0] = 2 * z0 / d
    return s


def write_touchstone(path, f, S, comment):
    """1- or 2-port Touchstone v1 (Hz, RI). Qucs and most tools read it."""
    n = S.shape[1]
    with Path(path).open("w", encoding="ascii", newline="\n") as fh:
        for c in comment:
            fh.write("! " + c + "\n")
        fh.write("# HZ S RI R 50\n")
        for k, fk in enumerate(f):
            m = [S[k, 0, 0]] if n == 1 else [S[k, 0, 0], S[k, 1, 0], S[k, 0, 1], S[k, 1, 1]]
            fh.write("%.6e " % fk + " ".join("%.9e %.9e" % (v.real, v.imag) for v in m) + "\n")


def file_names(variant):
    sfx = "" if variant == "table" else "_" + variant
    return ("AD8317_INHI_1MHz_10GHz%s.s1p" % sfx,
            "AD8317_INHI_INLO_series_1MHz_10GHz%s.s2p" % sfx)


def main():
    out = Path(__file__).resolve().parent
    f = np.logspace(6, 10, 401)
    for variant in VARIANTS:
        z = zin(f, variant)
        s1, s2 = file_names(variant)
        note = ["AD8317 input impedance model '%s', 1 MHz - 10 GHz (see ad8317_input_model.py)" % variant,
                "Reference impedance 50 ohm. INLO is ac-coupled RF common."]
        write_touchstone(out / s1, f, gamma(z)[:, None, None],
                         note + ["1-port: INHI to ground (INLO ac-grounded)."])
        write_touchstone(out / s2, f, series_2port(z),
                         note + ["2-port series element Zin between port1 (INHI) and port2 (INLO)."
                                 " Connect to rfsim ports 2 and 3."])
    print("written", out)


if __name__ == "__main__":
    main()

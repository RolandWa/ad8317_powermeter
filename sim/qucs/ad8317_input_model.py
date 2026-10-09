"""AD8317 INHI-INLO input impedance model, 1 MHz - 10 GHz (numpy only).

Source: AD8317 Rev. D datasheet
  - p.10: low frequency input impedance ~500 ohm || 0.7 pF
  - pp.3-4 Table 1: INHI input impedance as parallel R||C at 0.9/1.9/2.2/3.6/5.8/8.0 GHz
INLO is the RF common (ac-coupled to ground), so the part is a two-terminal
load Zin between INHI and INLO. Between anchors G=1/R and C are interpolated
linearly vs log10(f); above 8 GHz the 8 GHz values are held (datasheet gives
nothing beyond; Fig.15 only shows the trajectory up to 10 GHz).
"""
from pathlib import Path
import numpy as np

ANCHOR_F = np.array([1e6, 0.9e9, 1.9e9, 2.2e9, 3.6e9, 5.8e9, 8.0e9, 10e9])
ANCHOR_R = np.array([500.0, 1500.0, 950.0, 810.0, 300.0, 110.0, 28.0, 28.0])
ANCHOR_C = np.array([0.7, 0.33, 0.38, 0.39, 0.33, 0.05, 0.79, 0.79]) * 1e-12


def zin(f, lf_r=None, lf_c=None):
    """Complex input impedance (ohm) at frequencies f [Hz]."""
    r, c = ANCHOR_R.copy(), ANCHOR_C.copy()
    if lf_r is not None: r[0] = lf_r
    if lf_c is not None: c[0] = lf_c
    x = np.log10(f); xa = np.log10(ANCHOR_F)
    g = np.interp(x, xa, 1.0 / r)
    cc = np.interp(x, xa, c)
    return 1.0 / (g + 1j * 2 * np.pi * f * cc)


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
    n = S.shape[1]
    with Path(path).open("w", encoding="ascii", newline="\n") as fh:
        for c in comment: fh.write("! " + c + "\n")
        fh.write("# HZ S RI R 50\n")
        for k, fk in enumerate(f):
            vals = []
            if n == 2:  # Touchstone 2-port order: S11 S21 S12 S22
                m = [S[k, 0, 0], S[k, 1, 0], S[k, 0, 1], S[k, 1, 1]]
            else:
                m = [S[k, 0, 0]]
            fh.write("%.6e " % fk + " ".join("%.9e %.9e" % (v.real, v.imag) for v in m) + "\n")


def main():
    out = Path(__file__).resolve().parent
    f = np.logspace(6, 10, 401)
    z = zin(f)
    note = ["AD8317 input impedance model, 1 MHz - 10 GHz (datasheet Rev.D R||C table, see ad8317_input_model.py)",
            "Reference impedance 50 ohm. INLO is ac-coupled RF common."]
    write_touchstone(out / "AD8317_INHI_1MHz_10GHz.s1p", f, gamma(z)[:, None, None],
                     note + ["1-port: INHI to ground (INLO ac-grounded)."])
    write_touchstone(out / "AD8317_INHI_INLO_series_1MHz_10GHz.s2p", f, series_2port(z),
                     note + ["2-port series element Zin between port1 (INHI) and port2 (INLO)."
                             " Connect to rfsim ports 2 and 3."])
    print("written", out)


if __name__ == "__main__":
    main()

"""ADL5507 RFIN input reflection model, 1 MHz - 10 GHz (numpy only).

Source: ADL5507 Rev. 0 datasheet (doc/adl5507.pdf), Applications Information:
  - Table 5 "Raw Input Impedance": S11 (re, im) of the bare RFIN pin vs 50 ohm, 10 MHz - 15 GHz
  - Table 4 "Input Impedance with 51 ohm Shunt": the same with the recommended 51 ohm to ground
    at RFIN, 10 MHz - 15 GHz
  - RFIN is internally ac-coupled with a 25 pF series capacitor (corner about 4 MHz); the other
    side of the capacitor is the signal input of the log amp, COMM is the RF ground.
Unlike the AD8317 (INHI/INLO, differential) the ADL5507 has ONE RF input pin to ground, so its
model is a 1-port (.s1p), not a series 2-port.

Between the table points S11 is interpolated linearly (real and imaginary part) vs log10(f).
Below 10 MHz (not in the datasheet) the bare input is extended as a series capacitor behind a
constant resistance: Re(Z) = Re(Z at 10 MHz), Im(Z) scales with 1/f. The 51 ohm variant below
10 MHz is that extension in parallel with 51 ohm. Both are labelled as extrapolated in the files.

Variants:
  "raw"     bare RFIN pin (Table 5)
  "shunt51" with 51 ohm to ground at the pin (Table 4); this is the ADI reference match
"""
from pathlib import Path
import numpy as np

# (f [GHz], Re S11, Im S11) vs 50 ohm, from the datasheet
_RAW = [
    (0.01, +0.925, -0.036), (0.1, +0.908, -0.028), (0.5, +0.901, -0.114), (1, +0.876, -0.222),
    (2, +0.785, -0.415), (3, +0.666, -0.566), (4, +0.545, -0.672), (5, +0.421, -0.750),
    (6, +0.252, -0.788), (7, +0.070, -0.810), (8, -0.165, -0.761), (9, -0.336, -0.671),
    (10, -0.564, -0.524), (11, -0.548, -0.356), (12, -0.752, -0.262), (13, -0.697, -0.375),
    (14, -0.498, -0.365), (15, -0.673, -0.344)]
_SHUNT51 = [
    (0.01, -0.006, -0.009), (0.1, -0.011, -0.006), (0.5, -0.010, -0.026), (1, -0.010, -0.054),
    (2, -0.008, -0.113), (3, -0.003, -0.179), (4, +0.006, -0.250), (5, +0.012, -0.323),
    (6, -0.006, -0.392), (7, -0.063, -0.464), (8, -0.204, -0.504), (9, -0.354, -0.545),
    (10, -0.593, -0.459), (11, -0.616, -0.326), (12, -0.760, -0.274), (13, -0.731, -0.353),
    (14, -0.545, -0.439), (15, -0.609, -0.452)]
VARIANTS = ("raw", "shunt51")
R_SHUNT = 51.0
F_TABLE_MIN = 10e6


def _table(rows):
    a = np.array(rows, float)
    return a[:, 0] * 1e9, a[:, 1] + 1j * a[:, 2]


def gamma_to_z(g, z0=50.0):
    return z0 * (1 + g) / (1 - g)


def z_to_gamma(z, z0=50.0):
    return (z - z0) / (z + z0)


def _raw_below_table(f):
    """Bare input below 10 MHz: constant resistance, capacitive reactance that follows 1/f."""
    fa, ga = _table(_RAW)
    z10 = gamma_to_z(ga[0])
    return z_to_gamma(complex(z10.real, 0) + 1j * z10.imag * (fa[0] / f))


def gamma(f, variant="raw"):
    """Complex S11 vs 50 ohm of RFIN at frequencies f [Hz]."""
    if variant not in VARIANTS:
        raise ValueError("variant must be one of %s" % (VARIANTS,))
    f = np.atleast_1d(np.asarray(f, float))
    fa, ga = _table(_RAW if variant == "raw" else _SHUNT51)
    x, xa = np.log10(f), np.log10(fa)
    g = np.interp(x, xa, ga.real) + 1j * np.interp(x, xa, ga.imag)
    low = f < F_TABLE_MIN
    if low.any():
        glow = np.array([_raw_below_table(fk) for fk in f[low]])
        if variant == "shunt51":
            # raw extension in parallel with 51 ohm, shifted so that it meets Table 4 at 10 MHz
            def par(gr):
                return z_to_gamma(1.0 / (1.0 / gamma_to_z(gr) + 1.0 / R_SHUNT))
            glow = par(glow) + (ga[0] - par(_raw_below_table(fa[0])))
        g[low] = glow
    return g


def zin(f, variant="raw"):
    """Complex input impedance (ohm) of RFIN at frequencies f [Hz]."""
    return gamma_to_z(gamma(f, variant))


def write_touchstone(path, f, g, comment):
    """1-port Touchstone v1 (Hz, RI, 50 ohm)."""
    with Path(path).open("w", encoding="ascii", newline="\n") as fh:
        for c in comment:
            fh.write("! " + c + "\n")
        fh.write("# HZ S RI R 50\n")
        for fk, gk in zip(f, g):
            fh.write("%.6e %.9e %.9e\n" % (fk, gk.real, gk.imag))


def file_names(variant):
    return "ADL5507_RFIN_1MHz_10GHz_%s.s1p" % variant


def main():
    out = Path(__file__).resolve().parent
    f = np.logspace(6, 10, 401)
    for variant in VARIANTS:
        note = ["ADL5507 RFIN input reflection, variant '%s', 1 MHz - 10 GHz (see adl5507_input_model.py)" % variant,
                "Data: ADL5507 Rev. 0 datasheet, Table %d, 10 MHz - 15 GHz; below 10 MHz extrapolated "
                "(series 25 pF coupling capacitor)." % (5 if variant == "raw" else 4),
                "1-port: RFIN to ground (COMM). Reference impedance 50 ohm."]
        write_touchstone(out / file_names(variant), f, gamma(f, variant), note)
    print("written", out)


if __name__ == "__main__":
    main()

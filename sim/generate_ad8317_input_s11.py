"""Generate AD8317 input S11 Touchstone files from datasheet anchors.

Model notes:
- Pages 3-4 provide INHI input impedance points as parallel R||C at key frequencies.
- Page 9 (Figure 15) provides de-embedded Smith chart trajectory with no INHI termination.
- INLO is RF common and AC-coupled to ground, so this is a single-ended S11 model at INHI.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np


def z_from_parallel_rc(freq_ghz: float, r_ohm: float, c_f: float) -> complex:
    omega = 2.0 * np.pi * freq_ghz * 1e9
    admittance = (1.0 / r_ohm) + 1j * omega * c_f
    return 1.0 / admittance


def build_anchor_data() -> tuple[np.ndarray, np.ndarray]:
    # Frequency anchors in GHz.
    freqs = np.array([0.05, 0.10, 0.90, 1.90, 2.20, 3.60, 5.80, 8.00], dtype=float)

    # Figure 15 approximations at low/high ends (50 MHz, 100 MHz, 10 GHz).
    approx_z = {
        0.05: 2000.0 - 900.0j,
        0.10: 1100.0 - 700.0j,
    }

    # Datasheet pages 3-4: INHI input impedance in parallel R||C.
    datasheet_parallel_rc = {
        0.90: (1500.0, 0.33e-12),
        1.90: (950.0, 0.38e-12),
        2.20: (810.0, 0.39e-12),
        3.60: (300.0, 0.33e-12),
        5.80: (110.0, 0.05e-12),
        8.00: (28.0, 0.79e-12),
    }

    z_list: list[complex] = []
    for freq in freqs:
        if float(freq) in approx_z:
            z_list.append(approx_z[float(freq)])
        else:
            r_ohm, c_f = datasheet_parallel_rc[float(freq)]
            z_list.append(z_from_parallel_rc(float(freq), r_ohm, c_f))

    return freqs, np.array(z_list, dtype=complex)


def interpolate_impedance(freqs_ghz: np.ndarray, anchor_freqs_ghz: np.ndarray, anchor_z: np.ndarray) -> np.ndarray:
    x_anchor = np.log10(anchor_freqs_ghz)
    x_eval = np.log10(freqs_ghz)
    z_real = np.interp(x_eval, x_anchor, anchor_z.real)
    z_imag = np.interp(x_eval, x_anchor, anchor_z.imag)
    return z_real + 1j * z_imag


def z_to_s11(z: np.ndarray, z0: float = 50.0) -> np.ndarray:
    return (z - z0) / (z + z0)


def combined_gamma_fit(freqs_ghz: np.ndarray, use_rc_at_5p8: bool) -> np.ndarray:
    """Fit one S11 trajectory from numeric impedance and Smith-chart data."""
    rc_freqs = np.array([0.90, 1.90, 2.20, 3.60, 5.80, 8.00], dtype=float)
    rc_r = np.array([1500.0, 950.0, 810.0, 300.0, 110.0, 28.0])
    rc_c = np.array([0.33, 0.38, 0.39, 0.33, 0.05, 0.79]) * 1e-12
    rc_gamma = z_to_s11(1.0 / (1.0 / rc_r + 1j * 2.0 * np.pi * rc_freqs * 1e9 * rc_c))

    chart_freqs = np.array([0.05, 0.10, 0.90, 1.90, 2.20, 3.60, 5.80, 8.00, 10.00])
    chart_gamma = np.array([
        0.992 + 0.000j,
        0.985 - 0.020j,
        0.955 - 0.150j,
        0.900 - 0.300j,
        0.845 - 0.370j,
        0.690 - 0.500j,
        0.020 - 0.780j,
        -0.580 - 0.380j,
        -0.180 - 0.860j,
    ], dtype=complex)

    rc_by_freq = dict(zip(rc_freqs, rc_gamma))
    fit_gamma = []
    for freq, chart_value in zip(chart_freqs, chart_gamma):
        if freq in (0.05, 0.10, 10.00):
            value = chart_value
        elif freq == 5.80:
            value = rc_by_freq[freq] if use_rc_at_5p8 else chart_value
        else:
            # Numeric datasheet values are weighted more heavily; the chart
            # supplies the broader trajectory between measured anchors.
            value = 0.75 * rc_by_freq[freq] + 0.25 * chart_value
        fit_gamma.append(value)

    x_anchor = np.log10(chart_freqs)
    x_eval = np.log10(freqs_ghz)
    gamma = np.interp(x_eval, x_anchor, np.real(fit_gamma))
    gamma = gamma + 1j * np.interp(x_eval, x_anchor, np.imag(fit_gamma))
    magnitude = np.abs(gamma)
    over = magnitude >= 0.999
    gamma[over] = gamma[over] / magnitude[over] * 0.999
    return gamma


def write_s1p(path: Path, freqs_ghz: np.ndarray, s11: np.ndarray) -> None:
    with path.open("w", encoding="ascii", newline="\n") as f:
        f.write("! AD8317 INHI/INLO input model, 0.05-10 GHz\n")
        f.write("! Sources: AD8317 Rev.D pp.3-4 impedance table + p.9 Figure 15 approximation\n")
        f.write("! INLO is RF common (AC-coupled to ground), modeled implicitly in single-ended S11 at INHI.\n")
        f.write("# GHZ S RI R 50\n")
        for freq, gamma in zip(freqs_ghz, s11):
            f.write(f"{freq:0.6f} {gamma.real: .8f} {gamma.imag: .8f}\n")

def interpolate_gamma_strict_smith_fit(freqs_ghz: np.ndarray) -> np.ndarray:
    """Stricter fit to the page-9 Smith chart trajectory.

    These anchors follow the labeled frequency points on Figure 15
    (100, 900, 1900, 2200, 3600, 5800, 8000, 10000 MHz), plus 50 MHz start.
    The fit is performed directly in the complex reflection coefficient plane.
    """

    # Frequency anchors in GHz.
    f_anchor = np.array([0.05, 0.10, 0.90, 1.90, 2.20, 3.60, 5.80, 8.00, 10.00], dtype=float)

    # Approximate de-embedded Gamma points read from Figure 15 trajectory.
    g_anchor = np.array(
        [
            0.992 + 0.000j,  # 50 MHz start, near open
            0.985 - 0.020j,  # 100 MHz
            0.955 - 0.150j,  # 900 MHz
            0.900 - 0.300j,  # 1900 MHz
            0.845 - 0.370j,  # 2200 MHz
            0.690 - 0.500j,  # 3600 MHz
            0.020 - 0.780j,  # 5800 MHz
            -0.580 - 0.380j, # 8000 MHz
            -0.180 - 0.860j, # 10000 MHz
        ],
        dtype=complex,
    )

    # Log-frequency interpolation in complex plane to preserve trajectory shape.
    x_anchor = np.log10(f_anchor)
    x_eval = np.log10(freqs_ghz)
    g_real = np.interp(x_eval, x_anchor, g_anchor.real)
    g_imag = np.interp(x_eval, x_anchor, g_anchor.imag)
    gamma = g_real + 1j * g_imag

    # Keep points strictly inside the unit circle for passive stability.
    mag = np.abs(gamma)
    over = mag >= 0.999
    if np.any(over):
        gamma[over] = gamma[over] / mag[over] * 0.999

    return gamma


def main() -> None:
    anchor_freqs_ghz, anchor_z = build_anchor_data()

    f20 = np.logspace(np.log10(0.05), np.log10(10.0), 20)
    z20 = interpolate_impedance(f20, anchor_freqs_ghz, anchor_z)
    s20 = z_to_s11(z20)

    f50 = np.logspace(np.log10(0.05), np.log10(10.0), 50)
    z50 = interpolate_impedance(f50, anchor_freqs_ghz, anchor_z)
    s50 = z_to_s11(z50)

    out_dir = Path(__file__).resolve().parent
    write_s1p(out_dir / "AD8317_INHI_S11_20pt_0p05_to_10GHz.s1p", f20, s20)
    write_s1p(out_dir / "AD8317_INHI_S11_50pt_0p05_to_10GHz.s1p", f50, s50)

    # Stricter curve-fit set from page-9 Smith chart trajectory.
    s20_strict = interpolate_gamma_strict_smith_fit(f20)
    s50_strict = interpolate_gamma_strict_smith_fit(f50)
    write_s1p(out_dir / "AD8317_INHI_S11_20pt_0p05_to_10GHz_strict_smithfit.s1p", f20, s20_strict)
    write_s1p(out_dir / "AD8317_INHI_S11_50pt_0p05_to_10GHz_strict_smithfit.s1p", f50, s50_strict)

    # Combined fit: generate both interpretations of the conflicting 5.8 GHz point.
    combined_rc58 = combined_gamma_fit(f50, use_rc_at_5p8=True)
    combined_gamma58 = combined_gamma_fit(f50, use_rc_at_5p8=False)
    write_s1p(out_dir / "AD8317_INHI_S11_50pt_combined_RC_at_5p8.s1p", f50, combined_rc58)
    write_s1p(out_dir / "AD8317_INHI_S11_50pt_combined_Gamma_at_5p8.s1p", f50, combined_gamma58)

    print("Generated:")
    print("- AD8317_INHI_S11_20pt_0p05_to_10GHz.s1p")
    print("- AD8317_INHI_S11_50pt_0p05_to_10GHz.s1p")
    print("- AD8317_INHI_S11_20pt_0p05_to_10GHz_strict_smithfit.s1p")
    print("- AD8317_INHI_S11_50pt_0p05_to_10GHz_strict_smithfit.s1p")
    print("- AD8317_INHI_S11_50pt_combined_RC_at_5p8.s1p")
    print("- AD8317_INHI_S11_50pt_combined_Gamma_at_5p8.s1p")


if __name__ == "__main__":
    main()
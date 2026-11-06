"""Circuit post-processing of the 7-port HFSS network with the AD8317 detector.

The EM network ports are P1_SMA (wave port), P2_INHI, P3_INLO and the four
component-gap ports P4_R1, P5_R2, P6_C1, P7_C2. R1/R2/C1/C2 are applied as
one-port impedances; the AD8317 input is a series two-port between INHI and
INLO read from a Touchstone file (datasheet-derived model supplied by the user).
"""

import argparse
import csv
import json
from pathlib import Path

import numpy as np

Z0 = 50.0
ROOT = Path(__file__).resolve().parent
DETECTORS = {
    "table": ROOT / "inputs" / "detector" / "AD8317_INHI_INLO_series_1MHz_10GHz.s2p",
    "chart": ROOT / "inputs" / "detector" / "AD8317_INHI_INLO_series_1MHz_10GHz_chart.s2p",
    "hold": ROOT / "inputs" / "detector" / "AD8317_INHI_INLO_series_1MHz_10GHz_hold.s2p",
}
EXPECTED_PORTS = ["P1_SMA", "P2_INHI", "P3_INLO", "P4_R1", "P5_R2", "P6_C1", "P7_C2"]


def read_touchstone(path):
    """Return frequency [Hz], S[n, N, N] and the ordered port labels (if present)."""
    unit = 1e9
    fmt = "RI"
    labels = {}
    numbers = []
    starts = []
    for raw in Path(path).read_text().splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("!"):
            if "Port[" in line:
                head, name = line.lstrip("! ").split("=", 1)
                labels[int(head.strip()[5:-1])] = name.strip()
            continue
        if line.startswith("#"):
            tokens = line[1:].upper().split()
            unit = {"HZ": 1.0, "KHZ": 1e3, "MHZ": 1e6, "GHZ": 1e9}[tokens[0]]
            fmt = tokens[2]
            continue
        values = [float(v) for v in line.split("!")[0].split()]
        if not raw[0].isspace():
            starts.append(len(numbers))
        numbers.extend(values)
    ports = int(round(((len(numbers) / len(starts) - 1) / 2) ** 0.5))
    per_point = 1 + 2 * ports * ports
    data = np.array(numbers).reshape(len(starts), per_point)
    freq = data[:, 0] * unit
    a, b = data[:, 1::2], data[:, 2::2]
    if fmt == "RI":
        values = a + 1j * b
    elif fmt == "MA":
        values = a * np.exp(1j * np.deg2rad(b))
    else:
        values = 10 ** (a / 20) * np.exp(1j * np.deg2rad(b))
    s = values.reshape(len(freq), ports, ports)
    if ports == 2:
        s = s  # two-port Touchstone stores S11 S21 S12 S22
        s = s.reshape(len(freq), 2, 2).transpose(0, 2, 1)
    order = [labels.get(i + 1, f"port{i + 1}") for i in range(ports)]
    return freq, s, order


def resample(freq_src, s_src, freq_dst):
    out = np.empty((len(freq_dst),) + s_src.shape[1:], dtype=complex)
    for i in range(s_src.shape[1]):
        for j in range(s_src.shape[2]):
            out[:, i, j] = np.interp(freq_dst, freq_src, s_src[:, i, j].real) + 1j * np.interp(
                freq_dst, freq_src, s_src[:, i, j].imag
            )
    return out


def gamma(z):
    return (z - Z0) / (z + Z0)


def cap_impedance(freq, capacitance, model):
    """Generic MLCC series model: ESR(f) + jwESL + 1/(jwC).

    ESR = tan_delta/(wC) (dielectric loss) + esr_hf*sqrt(f/1GHz) (terminations/skin).
    This is a documented generic assumption, not a manufacturer part model, and it
    cannot reproduce the internal parallel resonances of a real MLCC above its SRF.
    """
    w = 2 * np.pi * freq
    model = model or {}
    esl = model.get("esl_h", 0.0)
    tan_d = model.get("tan_delta", 0.0)
    esr_hf = model.get("esr_hf_ohm", 0.0)
    esr = model.get("esr_ohm", 0.0) + tan_d / (w * capacitance) + esr_hf * np.sqrt(freq / 1e9)
    return esr + 1j * w * esl + 1.0 / (1j * w * capacitance)


# User-specified capacitor model (SPICE-style netlist): node1 - L_esl 0.7 nH - internal_1 -
# R_esr 0.04 ohm (constant) - internal_2 - C - node2. No dielectric-loss or skin terms are added.
CAP_USER = {"esl_h": 0.7e-9, "esr_ohm": 0.04}

# Coilcraft Document 158-1/158-2 lumped model (0402DC family): R2 in series with the parallel
# combination of [R_body + RVAR + jwL] and [R1 + 1/(jwC)], RVAR = k*sqrt(f).
# The document describes CHIP INDUCTORS. Applying it to a resistor is an assumption: the resistor's 100 ohm
# is placed in series with the L branch, and L, C, R1, R2, k come from the chosen 0402DC part.
COILCRAFT_0402DC = {
    "0402DC-0N8": {"R1": 10.150, "R2": 0.025, "C": 0.0270e-12, "L": 0.8e-9, "k": 1.890e-6},
    "0402DC-1N0": {"R1": 10.650, "R2": 0.045, "C": 0.0177e-12, "L": 1.0e-9, "k": 2.520e-6},
    "0402DC-2N0": {"R1": 16.200, "R2": 0.035, "C": 0.0263e-12, "L": 2.0e-9, "k": 4.270e-6},
    "0402DC-3N0": {"R1": 17.200, "R2": 0.037, "C": 0.0316e-12, "L": 3.0e-9, "k": 5.460e-6},
}


def coilcraft_resistor(freq, r_body, part):
    p = COILCRAFT_0402DC[part]
    w = 2 * np.pi * freq
    z_main = r_body + p["k"] * np.sqrt(freq) + 1j * w * p["L"]
    z_par = p["R1"] + 1.0 / (1j * w * p["C"])
    return p["R2"] + z_main * z_par / (z_main + z_par)


REALISTIC_0402 = {"esl_h": 0.45e-9, "tan_delta": 0.025, "esr_hf_ohm": 0.06}

# Real BOM parts: R1/R2 = UNI-ROYAL 0402WGF1000TCE (LCSC C25076, 100 ohm 1 %),
# C1/C2 = CCTC TCC0402X7R473K500AT (LCSC C880605, 47 nF X7R 50 V 0402).
# Neither vendor publishes RF S-parameters; the parasitics below are generic typical values
# for 0402 parts (assumptions) and are varied in the corner study.
REAL_PARTS = {
    "esl_h": 0.45e-9, "tan_delta": 0.025, "esr_hf_ohm": 0.06,
    "r_esl_h": 0.40e-9, "r_cp_f": 0.06e-12,
}
REAL_CORNERS = {
    "low_parasitics": {"esl_h": 0.30e-9, "tan_delta": 0.025, "esr_hf_ohm": 0.04, "r_esl_h": 0.25e-9, "r_cp_f": 0.03e-12},
    "nominal": REAL_PARTS,
    "high_parasitics": {"esl_h": 0.60e-9, "tan_delta": 0.025, "esr_hf_ohm": 0.10, "r_esl_h": 0.60e-9, "r_cp_f": 0.12e-12},
}
# User-specified parts: capacitor from the netlist above, resistor per Coilcraft topology.
USER_PARTS = {"cap_user": True, "esl_h": 0.7e-9, "esr_ohm": 0.04, "r_coilcraft": "0402DC-0N8"}


def component_impedances(freq, parasitics, c1=47e-9, c2=47e-9):
    w = 2 * np.pi * freq
    if parasitics.get("r_coilcraft"):
        z_r = coilcraft_resistor(freq, parasitics.get("r_ohm", 100.0), parasitics["r_coilcraft"])
    else:
        r_esl = parasitics.get("r_esl_h", 0.0)
        r_cp = parasitics.get("r_cp_f", 0.0)
        z_r = parasitics.get("r_ohm", 100.0) + 1j * w * r_esl
        if r_cp > 0:
            z_r = 1.0 / (1.0 / z_r + 1j * w * r_cp)
    if "c_esl_h" in parasitics or "esl_h" in parasitics or "tan_delta" in parasitics:
        model = {
            "esl_h": parasitics.get("esl_h", parasitics.get("c_esl_h", 0.0)),
            "tan_delta": parasitics.get("tan_delta", 0.0),
            "esr_hf_ohm": parasitics.get("esr_hf_ohm", parasitics.get("c_esr_ohm", 0.0)),
            "esr_ohm": parasitics.get("esr_ohm", 0.0),
        }
    else:
        model = {}
    z_c1 = cap_impedance(freq, c1, model)
    z_c2 = cap_impedance(freq, c2, model)
    return z_r, z_c1, z_c2


def loaded_network(freq, s7, detector_s2p=None, parasitics=None, detector_terminations=None,
                   c1=47e-9, c2=47e-9):
    """Return loaded S11, waves and node voltages for a 1 W-wave excitation at P1."""
    parasitics = parasitics or {}
    n = len(freq)
    z_r, z_c1, z_c2 = component_impedances(freq, parasitics, c1, c2)
    sload = np.zeros((n, 6, 6), dtype=complex)
    sload[:, 2, 2] = gamma(z_r)
    sload[:, 3, 3] = gamma(z_r)
    sload[:, 4, 4] = gamma(z_c1)
    sload[:, 5, 5] = gamma(z_c2)
    if detector_s2p is not None:
        sload[:, 0:2, 0:2] = detector_s2p
    elif detector_terminations is not None:
        sload[:, 0, 0] = detector_terminations
        sload[:, 1, 1] = detector_terminations
    s_ll = s7[:, 1:, 1:]
    s_l1 = s7[:, 1:, 0]
    s_1l = s7[:, 0, 1:]
    identity = np.eye(6)
    b_load = np.empty((n, 6), dtype=complex)
    a_load = np.empty((n, 6), dtype=complex)
    s11 = np.empty(n, dtype=complex)
    for k in range(n):
        b_load[k] = np.linalg.solve(identity - s_ll[k] @ sload[k], s_l1[k])
        a_load[k] = sload[k] @ b_load[k]
        s11[k] = s7[k, 0, 0] + s_1l[k] @ a_load[k]
    v = np.sqrt(Z0) * (a_load + b_load)
    return s11, v, a_load, b_load


def metrics(freq, s11, v):
    accepted = 1 - np.abs(s11) ** 2
    vdiff = v[:, 0] - v[:, 1]
    transfer_db = 20 * np.log10(np.abs(vdiff) / np.sqrt(Z0 * np.maximum(accepted, 1e-12) * 1.0) + 1e-30)
    # Vdiff per sqrt(incident power), relative to a 50-ohm ideal load voltage.
    h_inc_db = 20 * np.log10(np.abs(vdiff) / np.sqrt(Z0) + 1e-30)
    return accepted, vdiff, transfer_db, h_inc_db


def band_stats(freq, values_db, edges):
    rows = []
    for lo, hi in edges:
        mask = (freq >= lo) & (freq <= hi)
        rows.append((lo, hi, float(values_db[mask].max()), float(values_db[mask].mean())))
    return rows


def evaluate(s7_path, detector="table", parasitics=None, c1=47e-9, c2=47e-9, _cache={}):
    key = str(s7_path)
    if key not in _cache:
        _cache[key] = read_touchstone(s7_path)
    freq, s7, order = _cache[key]
    if order != EXPECTED_PORTS:
        raise RuntimeError(f"Unexpected port order: {order}")
    dkey = ("det", detector, len(freq))
    if dkey not in _cache:
        dfreq, ds2p, _ = read_touchstone(DETECTORS[detector])
        _cache[dkey] = resample(dfreq, ds2p, freq)
    s11, v, a_load, b_load = loaded_network(freq, s7, detector_s2p=_cache[dkey], parasitics=parasitics, c1=c1, c2=c2)
    accepted, vdiff, _, h_inc_db = metrics(freq, s11, v)
    absorbed = np.abs(b_load) ** 2 - np.abs(a_load) ** 2
    return {
        "freq_hz": freq, "s11": s11, "s11_db": 20 * np.log10(np.abs(s11) + 1e-30),
        "accepted": accepted, "vdiff": vdiff, "h_inc_db": h_inc_db,
        "p_chip": absorbed[:, 0:2].sum(axis=1),
        "p_resistors": absorbed[:, 2:4].sum(axis=1),
        "p_caps": absorbed[:, 4:6].sum(axis=1),
    }


def objective(result, band=(1e6, 10e9)):
    mask = (result["freq_hz"] >= band[0]) & (result["freq_hz"] <= band[1])
    s11 = result["s11_db"][mask]
    h = result["h_inc_db"][mask]
    return {
        "worst_s11_db": float(s11.max()),
        "mean_s11_db": float(s11.mean()),
        "h_flatness_db": float(h.max() - h.min()),
        "fraction_below_minus10": float((s11 < -10).mean()),
    }


def cross_check_with_3port(s7_path, s3_path):
    """Compare the circuit-reduced 7-port with the directly solved 3-port design."""
    freq, s7, _ = read_touchstone(s7_path)
    f3, s3, _ = read_touchstone(s3_path)
    s3 = resample(f3, s3, freq)
    termination = np.zeros(len(freq), dtype=complex)
    s11, v, a, b = loaded_network(freq, s7, detector_terminations=termination)
    # With 50-ohm P2/P3 terminations the load-port waves b are the delivered waves.
    s21 = b[:, 0]
    s31 = b[:, 1]
    diff = {
        "max_abs_dS11": float(np.max(np.abs(s11 - s3[:, 0, 0]))),
        "max_abs_dS21": float(np.max(np.abs(s21 - s3[:, 1, 0]))),
        "max_abs_dS31": float(np.max(np.abs(s31 - s3[:, 2, 0]))),
    }
    return diff


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("s7p")
    parser.add_argument("--s3p")
    parser.add_argument("--out", default="loaded_metrics")
    args = parser.parse_args()
    report = {}
    if args.s3p:
        report["cross_check_7port_vs_3port"] = cross_check_with_3port(args.s7p, args.s3p)
    for detector in ("table", "chart"):
        result = evaluate(args.s7p, detector)
        report[detector] = objective(result)
        with open(f"{args.out}_{detector}.csv", "w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["freq_GHz", "S11_dB", "accepted_power_fraction", "Vdiff_per_sqrtPinc_dB_re_sqrt50"])
            for f, s, a, h in zip(
                result["freq_hz"], result["s11_db"], result["accepted"], result["h_inc_db"]
            ):
                writer.writerow([f / 1e9, s, a, h])
    Path(f"{args.out}.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

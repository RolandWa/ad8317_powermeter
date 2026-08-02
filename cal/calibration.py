#!/usr/bin/env python3
"""
AD8317 Power Meter — Laboratory Calibration Script
====================================================
Instruments
  • R&S Signal Generator  (SMBxxx / SMFxxx / SMUxxx)  via VISA (GPIB / USB / LAN)
  • R&S NRP USB Power Sensor (NRP-Z21, NRP18S, …)     via VISA USB
  • DUT — AD8317 power meter board                     via USBTMC (SCPI)

NO external attenuator pad — generator connects directly to DUT SMA input.
Power range covers the full AD8317 operating range: −54 dBm to 0 dBm (28 steps, 2 dB).

Calibration phases
  Phase 1 — Generator characterisation: Generator RF OUT → NRP sensor
             Records true generator output power (0.01 dBm resolution) at every point.
  Phase 2 — DUT sweep: Generator RF OUT → DUT SMA input (direct, no pad)
             Records DUT raw ADC voltage vs NRP-corrected reference power.

Outputs
  results/phase1_raw.json          NRP reference readings (0.01 dBm resolution)
  results/phase2_raw.json          DUT ADC voltages
  results/calibration_table.json   Slope + V_intercept + linearity per frequency
  results/calibration_table.h      C header for firmware
  results/figures/                 PNG plots (linearity, slope, intercept, INL, heatmap)
  results/Calibration_Report.md    Full report (Markdown)
  results/Calibration_Report.pdf   Full report (PDF via pandoc)

Usage
  python calibration.py                                  # auto-discover instruments
  python calibration.py --gen "GPIB0::28::INSTR"
  python calibration.py --sensor "USB0::0x0AAD::0x0095::SN123::INSTR"
  python calibration.py --dut    "USB0::0x2A8D::0x0100::SN456::INSTR"
  python calibration.py --dry-run                        # simulate, no hardware
  python calibration.py --phase1-only                    # Phase 1 reference only
  python calibration.py --load-phase1 results/phase1_raw.json

Requirements
  pip install pyvisa pyvisa-py numpy matplotlib
  pandoc (optional, for PDF)
"""

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker

try:
    import pyvisa
    VISA_AVAILABLE = True
except ImportError:
    VISA_AVAILABLE = False
    print("WARNING: pyvisa not installed — running in dry-run mode")

# ─────────────────────────────────────────────────────────────────────────────
# Sweep configuration
# ─────────────────────────────────────────────────────────────────────────────

FREQS_HZ = [
    # Low band 1–100 MHz (AC-coupled; log conformance uncharacterized by ADI below 50 MHz)
    1e6,    2e6,    5e6,    10e6,   20e6,   50e6,
    # Mid band 100 MHz – 3 GHz (fully characterized)
    100e6,  200e6,  400e6,  700e6,  900e6,
    1200e6, 1575e6, 1800e6, 2100e6, 2400e6,
    2700e6, 3000e6,
    # High band 3.6–10 GHz
    3600e6, 4000e6, 4500e6, 5000e6, 5400e6,
    5800e6, 6000e6, 7000e6, 8000e6, 9000e6, 10000e6,
]

# 28 power steps, 2 dB apart, −54 dBm to 0 dBm — covers full AD8317 range.
# These are the levels at the DUT SMA input (= AD8317 INHI, no pad).
POWER_STEPS_DBM = list(range(-54, 1, 2))    # [-54, -52, ..., -2, 0]

# AD8317 physical constants
AD8317_SLOPE_NOM   = 22.0    # mV/dB  (magnitude, positive)
AD8317_VREF        = 3.00    # V
AD8317_VOUT_MIN    = 0.30    # V  (hard rail near max input)
AD8317_VOUT_MAX    = 1.75    # V  (hard rail near min input)
AD8317_XINT_NOM    = 14.0   # dBm  (X-intercept, typical 900 MHz)

SETTLE_S       = 0.30     # seconds after each generator command
NRP_AVERAGES   = 64       # averaging count — longer for 0.01 dBm resolution
VISA_TIMEOUT   = 8000     # ms

RESULTS_DIR = Path(__file__).parent / "results"
FIG_DIR     = RESULTS_DIR / "figures"


# ─────────────────────────────────────────────────────────────────────────────
# Instrument wrappers
# ─────────────────────────────────────────────────────────────────────────────

class SignalGenerator:
    """R&S SMBxxx / SMFxxx signal generator — SCPI over VISA."""

    def __init__(self, res, dry_run=False):
        self.dry_run = dry_run
        if not dry_run:
            self._inst = res
            self._inst.timeout = VISA_TIMEOUT
            self._inst.write("*RST; *CLS")
            time.sleep(0.6)
            self.idn = self._inst.query("*IDN?").strip()
        else:
            self.idn = "R&S SMB100A (dry-run)"
        print(f"  Generator : {self.idn}")

    def set(self, freq_hz: float, power_dbm: float):
        if not self.dry_run:
            self._inst.write(f"FREQ:CW {freq_hz:.0f} Hz")
            self._inst.write(f"POW:LEV {power_dbm:.2f} dBm")
            time.sleep(SETTLE_S)

    def output(self, on: bool):
        if not self.dry_run:
            self._inst.write("OUTP:STAT " + ("ON" if on else "OFF"))

    def close(self):
        if not self.dry_run:
            self.output(False)
            self._inst.close()


class NRPSensor:
    """R&S NRP USB power sensor — 0.01 dBm readout resolution."""

    def __init__(self, res, dry_run=False):
        self.dry_run = dry_run
        if not dry_run:
            self._inst = res
            self._inst.timeout = VISA_TIMEOUT * 3
            self._inst.write("*RST; *CLS")
            time.sleep(0.6)
            self.idn = self._inst.query("*IDN?").strip()
            self._inst.write("SENS:FUNC 'POW:AVG'")
            self._inst.write("UNIT:POW DBM")
            self._inst.write(f"SENS:AVER:COUN {NRP_AVERAGES}")
            self._inst.write("SENS:AVER:COUN:AUTO OFF")
            self._inst.write("SENS:AVER:STAT ON")
            self._inst.write("INIT:CONT ON")
        else:
            self.idn = "R&S NRP-Z21 (dry-run)"
        print(f"  NRP sensor: {self.idn}")

    def set_freq(self, freq_hz: float):
        if not self.dry_run:
            self._inst.write(f"SENS:FREQ {freq_hz:.0f} Hz")

    def read_dbm(self) -> float:
        """Return power in dBm with 0.01 dBm resolution."""
        if self.dry_run:
            return round(-10.0 + np.random.normal(0, 0.008), 2)
        raw = self._inst.query("READ?").strip()
        # Return with 0.01 dBm precision (two digits after decimal point)
        return round(float(raw), 2)

    def close(self):
        if not self.dry_run:
            self._inst.close()


class DUTPowerMeter:
    """AD8317 power meter board — USBTMC SCPI."""

    def __init__(self, res, dry_run=False):
        self.dry_run  = dry_run
        self._freq    = 1e9
        self._has_volt_cmd = False
        if not dry_run:
            self._inst = res
            self._inst.timeout = VISA_TIMEOUT
            self.idn = self._inst.query("*IDN?").strip()
            print(f"  DUT meter : {self.idn}")
            try:
                self._inst.query("SENS:VOLT?")
                self._has_volt_cmd = True
                print("               → SENS:VOLT? supported (raw ADC mode)")
            except Exception:
                print("               → using READ? (calibrated dBm mode)")
        else:
            self.idn = "AD8317 Power Meter (dry-run)"
            self._has_volt_cmd = True
            print(f"  DUT meter : {self.idn}")

    def set_freq(self, freq_hz: float):
        self._freq = freq_hz
        if not self.dry_run:
            self._inst.write(f"SENS:FREQ {freq_hz:.0f}")

    def read_voltage(self, p_inhi_dbm: float = 0.0) -> float:
        """
        Return raw ADC voltage at VOUT pin.
        p_inhi_dbm used only in dry-run to simulate realistic AD8317 response.
        """
        if self.dry_run:
            # AD8317: V = slope * (X_intercept - P_in)
            x_int  = AD8317_XINT_NOM + 0.8 * np.sin(self._freq / 1.5e9)
            slope  = (AD8317_SLOPE_NOM + np.random.normal(0, 0.08)) / 1000.0
            v = slope * (x_int - p_inhi_dbm)
            # Add realistic non-linearity artefact (±15 mV RMS)
            v += 0.015 * np.sin((p_inhi_dbm + 30) * 0.4) + np.random.normal(0, 3e-4)
            return float(np.clip(v, AD8317_VOUT_MIN, AD8317_VOUT_MAX))
        if self._has_volt_cmd:
            return float(self._inst.query("SENS:VOLT?").strip())
        # Fall back: back-calculate from calibrated dBm reading
        raw_dbm = float(self._inst.query("READ?").strip())
        v = (AD8317_SLOPE_NOM / 1000.0) * (AD8317_XINT_NOM - raw_dbm)
        return float(np.clip(v, AD8317_VOUT_MIN, AD8317_VOUT_MAX))

    def close(self):
        if not self.dry_run:
            self._inst.close()


# ─────────────────────────────────────────────────────────────────────────────
# VISA auto-discovery
# ─────────────────────────────────────────────────────────────────────────────

def discover(args) -> tuple:
    rm = pyvisa.ResourceManager()
    resources = list(rm.list_resources())
    print(f"\nVISA resources: {len(resources)}")
    for r in resources:
        print(f"  {r}")

    gen_addr    = args.gen    or _find(resources, rm, ["Rohde", "R&S", "SMB", "SMF", "SMBV"])
    sensor_addr = args.sensor or _find_nrp(resources, rm)
    dut_addr    = args.dut    or _find_dut(resources, rm, skip=[gen_addr, sensor_addr])

    if not gen_addr:
        sys.exit("ERROR: Signal generator not found. Use --gen VISA_ADDR")
    if not sensor_addr:
        sys.exit("ERROR: NRP sensor not found. Use --sensor VISA_ADDR")
    if not dut_addr:
        sys.exit("ERROR: DUT not found. Use --dut VISA_ADDR")

    print("\nConnecting …")
    gen    = SignalGenerator(rm.open_resource(gen_addr))
    sensor = NRPSensor(rm.open_resource(sensor_addr))
    dut    = DUTPowerMeter(rm.open_resource(dut_addr))
    return gen, sensor, dut


def _find(resources, rm, keywords):
    for addr in resources:
        try:
            inst = rm.open_resource(addr)
            idn  = inst.query("*IDN?").strip()
            inst.close()
            if any(k in idn for k in keywords):
                return addr
        except Exception:
            pass
    return None


def _find_nrp(resources, rm):
    for addr in resources:
        if "0AAD" in addr.upper():
            try:
                inst = rm.open_resource(addr)
                idn  = inst.query("*IDN?").strip()
                inst.close()
                if "NRP" in idn:
                    return addr
            except Exception:
                pass
    return _find(resources, rm, ["NRP"])


def _find_dut(resources, rm, skip):
    for addr in resources:
        if addr in skip:
            continue
        if "2A8D" in addr.upper():
            return addr
        try:
            inst = rm.open_resource(addr)
            idn  = inst.query("*IDN?").strip()
            inst.close()
            if "AD8317" in idn or "powermeter" in idn.lower():
                return addr
        except Exception:
            pass
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Phase 1 — Generator characterisation (Generator → NRP sensor)
# ─────────────────────────────────────────────────────────────────────────────

def phase1_reference(gen: SignalGenerator, sensor: NRPSensor) -> dict:
    _banner("PHASE 1 — Generator characterisation (Generator → NRP sensor)")
    print("Connect: Generator RF OUT ─── SMA cable ──────────────► NRP sensor input")
    print("         (DUT is NOT connected in this phase)")
    input("\nPress ENTER when ready …\n")

    gen.output(True)
    data = {
        "freqs_hz":       FREQS_HZ,
        "powers_dbm_set": POWER_STEPS_DBM,
        "nrp_readings":   [],        # actual power, 0.01 dBm resolution
        "gen_error_db":   [],        # actual − set
    }

    n_total = len(FREQS_HZ) * len(POWER_STEPS_DBM)
    done    = 0
    for fi, freq in enumerate(FREQS_HZ):
        row_meas = []
        row_err  = []
        sensor.set_freq(freq)
        for pi, pwr in enumerate(POWER_STEPS_DBM):
            gen.set(freq, pwr)
            meas = sensor.read_dbm()          # 0.01 dBm resolution
            row_meas.append(meas)
            row_err.append(round(meas - pwr, 3))
            done += 1
            _prog(done, n_total,
                  f"{freq/1e6:6.0f} MHz  set={pwr:+5.1f}  NRP={meas:+7.2f} dBm  "
                  f"err={meas-pwr:+5.2f} dB")
        data["nrp_readings"].append(row_meas)
        data["gen_error_db"].append(row_err)

    gen.output(False)
    _save(data, RESULTS_DIR / "phase1_raw.json")
    print(f"\n  Saved → {RESULTS_DIR / 'phase1_raw.json'}")
    return data


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2 — DUT sweep (Generator → DUT, direct, no pad)
# ─────────────────────────────────────────────────────────────────────────────

def phase2_dut(gen: SignalGenerator, dut: DUTPowerMeter, phase1: dict) -> dict:
    _banner("PHASE 2 — DUT sweep (Generator RF OUT → DUT SMA, no attenuator)")
    print("Connect: Generator RF OUT ─── SMA cable ──────────────► DUT SMA input")
    print("         (no 30 dB pad — direct connection)")
    input("\nPress ENTER when ready …\n")

    gen.output(True)
    gen_err   = np.array(phase1["gen_error_db"])   # [freq, pwr]
    nrp_meas  = np.array(phase1["nrp_readings"])   # actual power, 0.01 dBm res

    data = {
        "freqs_hz":        FREQS_HZ,
        "p_set_dbm":       POWER_STEPS_DBM,
        "p_ref_dbm":       [],    # NRP-corrected power at DUT input (0.01 dBm res)
        "dut_voltage_v":   [],    # raw ADC voltage at VOUT
    }

    n_total = len(FREQS_HZ) * len(POWER_STEPS_DBM)
    done    = 0
    for fi, freq in enumerate(FREQS_HZ):
        row_pref = []
        row_v    = []
        dut.set_freq(freq)
        for pi, pwr in enumerate(POWER_STEPS_DBM):
            p_ref = nrp_meas[fi, pi]           # true power from Phase 1 (0.01 dBm)
            gen.set(freq, pwr)
            v = dut.read_voltage(p_inhi_dbm=p_ref)
            row_pref.append(round(p_ref, 2))
            row_v.append(round(v, 6))
            done += 1
            _prog(done, n_total,
                  f"{freq/1e6:6.0f} MHz  P_ref={p_ref:+7.2f} dBm  V={v:.5f} V")
        data["p_ref_dbm"].append(row_pref)
        data["dut_voltage_v"].append(row_v)

    gen.output(False)
    _save(data, RESULTS_DIR / "phase2_raw.json")
    print(f"\n  Saved → {RESULTS_DIR / 'phase2_raw.json'}")
    return data


# ─────────────────────────────────────────────────────────────────────────────
# Calibration fitting + linearity analysis
# ─────────────────────────────────────────────────────────────────────────────

def fit_calibration(phase2: dict) -> list[dict]:
    """
    Per frequency: linear fit V_adc = b + m*P_in, then compute linearity metrics.

    Returns list of CalPoint dicts including:
      slope_mv_db, v_intercept, r_squared, max_inl_db, rms_inl_db
    """
    cal = []
    print(f"\n{'Freq':>8}  {'Slope':>10}  {'V_int':>9}  {'R²':>7}  "
          f"{'MaxINL':>9}  {'RMS_INL':>9}")
    print("─" * 65)

    for fi, freq in enumerate(FREQS_HZ):
        p = np.array(phase2["p_ref_dbm"][fi])      # dBm at INHI
        v = np.array(phase2["dut_voltage_v"][fi])  # V

        # Linear fit: V = m*P + b  (m is negative — V drops as P rises)
        coeffs      = np.polyfit(p, v, 1)
        m, b        = coeffs
        slope_mv_db = -m * 1000.0                  # magnitude, positive
        v_int       = b                             # V at P = 0 dBm

        # Residuals in voltage and dBm
        v_fit     = np.polyval(coeffs, p)
        resid_v   = v - v_fit
        resid_db  = resid_v / (-m)                  # convert V error → dBm error

        # Linearity metrics
        ss_res    = np.sum(resid_v**2)
        ss_tot    = np.sum((v - v.mean())**2)
        r_sq      = 1.0 - ss_res / ss_tot if ss_tot > 0 else 1.0
        max_inl   = float(np.max(np.abs(resid_db)))
        rms_inl   = float(np.std(resid_db))

        cal.append({
            "freq_hz":       int(freq),
            "freq_mhz":      freq / 1e6,
            "slope_mv_db":   round(slope_mv_db, 4),
            "v_intercept":   round(v_int, 6),     # V at P = 0 dBm
            "r_squared":     round(r_sq, 7),
            "max_inl_db":    round(max_inl, 4),
            "rms_inl_db":    round(rms_inl, 4),
            "p_points_dbm":  p.tolist(),
            "v_points_v":    v.tolist(),
            "v_fit_v":       v_fit.tolist(),
            "inl_db":        resid_db.tolist(),
        })

        print(f"{freq/1e6:>7.0f} MHz  "
              f"{slope_mv_db:>8.3f} mV/dB  "
              f"{v_int:>7.5f} V  "
              f"{r_sq:>7.5f}  "
              f"{max_inl:>+7.4f} dB  "
              f"{rms_inl:>7.4f} dB")

    return cal


# ─────────────────────────────────────────────────────────────────────────────
# Figures
# ─────────────────────────────────────────────────────────────────────────────

BLUE   = "#1f77b4"
RED    = "#d62728"
GREEN  = "#2ca02c"
ORANGE = "#ff7f0e"
GREY   = "#7f7f7f"


def make_figures(cal: list[dict]) -> list[Path]:
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    figs = []
    figs += [_fig_linearity_grid(cal)]
    figs += [_fig_inl_grid(cal)]
    figs += [_fig_slope(cal)]
    figs += [_fig_intercept(cal)]
    figs += [_fig_inl_summary(cal)]
    figs += [_fig_error_heatmap(cal)]
    print(f"  Figures → {FIG_DIR}/")
    return figs


def _fig_linearity_grid(cal):
    """Grid: V_ADC vs P_in with linear fit line — one subplot per frequency."""
    ncols = 4
    nrows = (len(cal) + ncols - 1) // ncols
    fig, axes = plt.subplots(nrows, ncols, figsize=(14, nrows * 3.0),
                             constrained_layout=True)
    fig.suptitle("AD8317 Linearity — V_ADC vs Input Power (per frequency)\n"
                 "Blue dots = measured,  Red line = best-fit linear", fontsize=12)

    for i, pt in enumerate(cal):
        ax = axes.flat[i]
        p, v, vf = (np.array(pt[k]) for k in ("p_points_dbm", "v_points_v", "v_fit_v"))
        ax.scatter(p, v * 1000, s=20, color=BLUE, zorder=3, label="Measured")
        ax.plot(p, vf * 1000, color=RED, lw=1.5, label="Linear fit")
        ax.set_title(f"{pt['freq_mhz']:.0f} MHz", fontsize=9, fontweight="bold")
        ax.set_xlabel("P_in (dBm)", fontsize=7)
        ax.set_ylabel("V_ADC (mV)", fontsize=7)
        ax.tick_params(labelsize=7)
        ax.grid(True, alpha=0.3)
        ax.text(0.03, 0.97,
                f"k = {pt['slope_mv_db']:.2f} mV/dB\n"
                f"R² = {pt['r_squared']:.6f}\n"
                f"INL_max = {pt['max_inl_db']:+.3f} dB",
                transform=ax.transAxes, va="top", ha="left", fontsize=6.5,
                bbox=dict(fc="white", alpha=0.8, ec="silver", lw=0.5))

    for j in range(i + 1, len(axes.flat)):
        axes.flat[j].set_visible(False)

    path = FIG_DIR / "fig1_linearity_grid.png"
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def _fig_inl_grid(cal):
    """Grid: INL (residual from best-fit line) vs P_in — one subplot per frequency."""
    ncols = 4
    nrows = (len(cal) + ncols - 1) // ncols
    fig, axes = plt.subplots(nrows, ncols, figsize=(14, nrows * 3.0),
                             constrained_layout=True)
    fig.suptitle("AD8317 Integral Non-Linearity (INL) — Deviation from Best-Fit Line\n"
                 "Red dashed = ±0.1 dB target", fontsize=12)

    for i, pt in enumerate(cal):
        ax = axes.flat[i]
        p   = np.array(pt["p_points_dbm"])
        inl = np.array(pt["inl_db"])
        colors = [RED if abs(x) > 0.10 else BLUE for x in inl]
        ax.bar(p, inl * 1000, width=1.5, color=colors, edgecolor="none", alpha=0.8)
        ax.axhline( 100, color=RED, lw=1.0, ls="--", alpha=0.7)
        ax.axhline(-100, color=RED, lw=1.0, ls="--", alpha=0.7)
        ax.axhline(0, color=GREY, lw=0.8)
        ax.set_title(f"{pt['freq_mhz']:.0f} MHz", fontsize=9, fontweight="bold")
        ax.set_xlabel("P_in (dBm)", fontsize=7)
        ax.set_ylabel("INL (mdB)", fontsize=7)
        ax.tick_params(labelsize=7)
        ax.set_ylim(-250, 250)
        ax.grid(True, axis="y", alpha=0.3)
        ax.text(0.03, 0.97,
                f"max = {pt['max_inl_db']:+.3f} dB\n"
                f"rms = {pt['rms_inl_db']:.4f} dB",
                transform=ax.transAxes, va="top", ha="left", fontsize=6.5,
                bbox=dict(fc="white", alpha=0.8, ec="silver", lw=0.5))

    for j in range(i + 1, len(axes.flat)):
        axes.flat[j].set_visible(False)

    path = FIG_DIR / "fig2_inl_grid.png"
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def _fig_slope(cal):
    freqs  = [pt["freq_mhz"] for pt in cal]
    slopes = [pt["slope_mv_db"] for pt in cal]

    fig, ax = plt.subplots(figsize=(11, 4.5), constrained_layout=True)
    bars = ax.bar(range(len(freqs)), slopes, color=BLUE, edgecolor="navy",
                  linewidth=0.6, zorder=3)
    ax.axhline(AD8317_SLOPE_NOM, color=RED,    lw=2.0, ls="--",
               label=f"Nominal {AD8317_SLOPE_NOM:.0f} mV/dB")
    ax.axhline(19.5, color=ORANGE, lw=1.2, ls=":", label="Min spec 19.5 mV/dB")
    ax.axhline(25.0, color=ORANGE, lw=1.2, ls=":", label="Max spec 25.0 mV/dB")

    for bar, val in zip(bars, slopes):
        ax.text(bar.get_x() + bar.get_width() / 2, val + 0.1,
                f"{val:.1f}", ha="center", va="bottom", fontsize=6.5)

    ax.set_xticks(range(len(freqs)))
    ax.set_xticklabels([f"{f:.0f}" for f in freqs], rotation=45, ha="right", fontsize=8)
    ax.set_xlabel("Frequency (MHz)")
    ax.set_ylabel("Slope magnitude (mV/dB)")
    ax.set_title("AD8317 Slope vs Frequency")
    ax.set_ylim(15, 28)
    ax.legend(fontsize=8)
    ax.grid(True, axis="y", alpha=0.3, zorder=0)

    path = FIG_DIR / "fig3_slope_vs_freq.png"
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def _fig_intercept(cal):
    freqs  = [pt["freq_mhz"] for pt in cal]
    v_ints = [pt["v_intercept"] for pt in cal]
    # Convert V_intercept to dBm equivalent: P_intercept = X_intercept from V_int/slope
    p_ints = [vi / (pt["slope_mv_db"] / 1000.0) for vi, pt in zip(v_ints, cal)]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 7), constrained_layout=True)
    fig.suptitle("AD8317 Intercept vs Frequency", fontsize=12)

    # Top: V_intercept
    ax1.plot(range(len(freqs)), v_ints, "o-", color=GREEN, lw=1.8, ms=6, zorder=3)
    ax1.fill_between(range(len(freqs)),
                     [v - pt["rms_inl_db"] * pt["slope_mv_db"] / 1000.0
                      for v, pt in zip(v_ints, cal)],
                     [v + pt["rms_inl_db"] * pt["slope_mv_db"] / 1000.0
                      for v, pt in zip(v_ints, cal)],
                     alpha=0.15, color=GREEN, label="±1σ INL")
    ax1.set_xticks(range(len(freqs)))
    ax1.set_xticklabels([f"{f:.0f}" for f in freqs], rotation=45, ha="right", fontsize=8)
    ax1.set_ylabel("V_intercept (V)  [at P_in = 0 dBm]")
    ax1.legend(fontsize=8)
    ax1.grid(True, alpha=0.3)

    # Bottom: X-intercept in dBm
    ax2.plot(range(len(freqs)), p_ints, "s-", color=BLUE, lw=1.8, ms=6, zorder=3)
    ax2.axhline(15.0, color=GREY, lw=1.0, ls="--", label="Datasheet typ 900 MHz (+15 dBm)")
    ax2.axhline(11.0, color=ORANGE, lw=1.0, ls=":", label="Datasheet typ 3.6 GHz (+11 dBm)")
    ax2.set_xticks(range(len(freqs)))
    ax2.set_xticklabels([f"{f:.0f}" for f in freqs], rotation=45, ha="right", fontsize=8)
    ax2.set_xlabel("Frequency (MHz)")
    ax2.set_ylabel("X-intercept (dBm)  [extrapolated]")
    ax2.legend(fontsize=8)
    ax2.grid(True, alpha=0.3)

    path = FIG_DIR / "fig4_intercept_vs_freq.png"
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def _fig_inl_summary(cal):
    """Summary: max INL and RMS INL bar chart across frequencies."""
    freqs   = [pt["freq_mhz"] for pt in cal]
    max_inl = [pt["max_inl_db"] * 1000 for pt in cal]   # mdB
    rms_inl = [pt["rms_inl_db"] * 1000 for pt in cal]

    x = np.arange(len(freqs))
    w = 0.40

    fig, ax = plt.subplots(figsize=(11, 4.5), constrained_layout=True)
    ax.bar(x - w/2, max_inl, w, label="Max |INL| (mdB)", color=RED,   alpha=0.8,
           edgecolor="darkred",  linewidth=0.5)
    ax.bar(x + w/2, rms_inl, w, label="RMS INL (mdB)",   color=BLUE,  alpha=0.8,
           edgecolor="navy",     linewidth=0.5)
    ax.axhline(100, color=RED,    lw=1.5, ls="--", label="100 mdB = 0.1 dB target")
    ax.axhline(50,  color=ORANGE, lw=1.0, ls=":",  label="50 mdB = 0.05 dB stretch")

    ax.set_xticks(x)
    ax.set_xticklabels([f"{f:.0f}" for f in freqs], rotation=45, ha="right", fontsize=8)
    ax.set_xlabel("Frequency (MHz)")
    ax.set_ylabel("INL (mdB)")
    ax.set_title("Linearity — Max and RMS INL per Frequency\n"
                 "Blue = RMS,  Red = peak deviation from best-fit line")
    ax.legend(fontsize=8)
    ax.grid(True, axis="y", alpha=0.3)

    path = FIG_DIR / "fig5_inl_summary.png"
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def _fig_error_heatmap(cal):
    """INL heat map: frequency (rows) × power step (columns)."""
    n_f = len(cal)
    n_p = len(POWER_STEPS_DBM)
    mat = np.array([pt["inl_db"] for pt in cal]) * 1000  # mdB

    fig, ax = plt.subplots(figsize=(13, 5.5), constrained_layout=True)
    im = ax.imshow(mat, aspect="auto", cmap="RdBu_r", vmin=-200, vmax=200,
                   extent=[-0.5, n_p - 0.5, n_f - 0.5, -0.5])
    cbar = plt.colorbar(im, ax=ax, label="INL (mdB)")
    cbar.ax.tick_params(labelsize=8)

    ax.set_xticks(range(n_p))
    ax.set_xticklabels([f"{p:+.0f}" for p in POWER_STEPS_DBM], fontsize=7, rotation=45)
    ax.set_yticks(range(n_f))
    ax.set_yticklabels([f"{pt['freq_mhz']:.0f}" for pt in cal], fontsize=7)
    ax.set_xlabel("Input power at DUT SMA (dBm)", fontsize=9)
    ax.set_ylabel("Frequency (MHz)", fontsize=9)
    ax.set_title("Linearity Error Map — INL (mdB) across Frequency and Power\n"
                 "Blue = below target,  Red = above target,  White = on fit line",
                 fontsize=11)

    path = FIG_DIR / "fig6_inl_heatmap.png"
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


# ─────────────────────────────────────────────────────────────────────────────
# Output files
# ─────────────────────────────────────────────────────────────────────────────

def write_c_header(cal: list[dict], path: Path, ts: str):
    lines = [
        "/* calibration_table.h — auto-generated by calibration.py */",
        f"/* {ts} */",
        "/* DO NOT EDIT — regenerate by re-running calibration.py */",
        "",
        "#ifndef CALIBRATION_TABLE_H",
        "#define CALIBRATION_TABLE_H",
        "",
        '#include "power_meter.h"',
        "",
        f"#define CAL_FREQ_COUNT  {len(cal)}U",
        "#define CAL_TEMP_REF_C  25.0f",
        "#define CAL_ATT_DB       0.0f    /* no external attenuator */",
        "",
        "static const FreqCalPoint CAL_TABLE[CAL_FREQ_COUNT] = {",
    ]
    for pt in cal:
        lines.append(
            f"    {{ .freq_hz={pt['freq_hz']}U, "
            f".v_intercept={pt['v_intercept']:.6f}f, "
            f".slope_mv_db={pt['slope_mv_db']:.4f}f, "
            f".temp_coeff=0.0f }},  "
            f"/* {pt['freq_mhz']:.0f} MHz  "
            f"R²={pt['r_squared']:.5f}  "
            f"INL_max={pt['max_inl_db']:+.3f} dB */"
        )
    lines += ["};", "", "#endif /* CALIBRATION_TABLE_H */", ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  C header   → {path}")


REPORT_MD = """\
# AD8317 Power Meter — Calibration Report

| | |
|---|---|
| **Date** | {date} |
| **Generator** | {gen_idn} |
| **Reference sensor** | {sensor_idn} |
| **Frequency range** | 100 MHz – 6 GHz ({n_freq} points) |
| **Power range at DUT** | {pwr_min:+.0f} dBm to {pwr_max:+.0f} dBm ({n_pwr} steps, 2 dB) |
| **Attenuator** | None — direct connection |
| **Sensor resolution** | 0.01 dBm |
| **Power steps** | {n_pwr} × 2 dB = {span:.0f} dB span |

---

## 1. Test Setup

```text
Phase 1 (generator characterisation):
  Generator RF OUT ─── SMA cable ─────────────────────► NRP sensor

Phase 2 (DUT sweep):
  Generator RF OUT ─── SMA cable ─────────────────────► DUT SMA input
                        (no attenuator pad)
```

The NRP sensor (0.01 dBm resolution) characterises the generator output level
at every frequency and power step. The Phase 1 correction table is applied in
Phase 2 to compute the true power arriving at the AD8317 INHI pin.

---

## 2. Linearity — V_ADC vs Input Power

Each panel shows the measured ADC output voltage vs NRP-corrected input power at
the AD8317 INHI pin (28 data points, 2 dB steps). The red line is the best-fit
linear regression. Key metrics (slope, R², max INL) are shown on each panel.

![Linearity grid](figures/fig1_linearity_grid.png)

---

## 3. Integral Non-Linearity (INL) per Frequency

INL is the signed deviation of each measured point from the best-fit straight line,
expressed in dBm. It represents the true non-linearity of the AD8317 transfer
function independent of slope or intercept offset.

![INL grid](figures/fig2_inl_grid.png)

---

## 4. Slope vs Frequency

The AD8317 slope (mV per dB of input power) is nominally constant at −22 mV/dB.
Deviations indicate device-specific variation or impedance mismatch above 4 GHz.

![Slope](figures/fig3_slope_vs_freq.png)

---

## 5. V_intercept and X-intercept vs Frequency

V_intercept is the fitted VOUT value at P_in = 0 dBm — the primary per-frequency
calibration constant stored in firmware. The lower panel shows the equivalent
X-intercept in dBm (where the extrapolated line reaches 0 V) for comparison with
the AD8317 datasheet.

![Intercept](figures/fig4_intercept_vs_freq.png)

---

## 6. INL Summary — Max and RMS per Frequency

Peak and RMS INL across all 28 power steps at each frequency. Red bars exceed
the 0.1 dB laboratory target.

![INL summary](figures/fig5_inl_summary.png)

---

## 7. INL Heat Map

Spatial distribution of INL across the full (frequency × power) measurement
space. Systematic column patterns indicate slope error; systematic row patterns
indicate intercept drift. Isolated red cells indicate non-linear AD8317 response
at a specific power/frequency combination.

![Heat map](figures/fig6_inl_heatmap.png)

---

## 8. Calibration Table

{cal_table_md}

---

## 9. Linearity Summary Statistics

| Metric | Value |
|---|---|
| Mean slope | {mean_slope:.3f} mV/dB |
| Slope std dev | {std_slope:.3f} mV/dB |
| Max slope deviation from 22.0 | {max_slope_dev:+.3f} mV/dB |
| Mean R² (linearity) | {mean_r2:.7f} |
| Min R² (worst frequency) | {min_r2:.7f} |
| Mean max INL | {mean_max_inl:.4f} dB |
| Worst-case max INL | {worst_max_inl:.4f} dB |
| Mean RMS INL | {mean_rms_inl:.4f} dB |

---

## 10. Measurement Uncertainty

After firmware update with this calibration table:

| Band | INL residual | Generator level (corrected) | **Total** |
|---|---|---|---|
| 100 MHz – 2 GHz | ±{u_inl_lo:.2f} dB (typ) | ±0.05 dB | **±{u_tot_lo:.2f} dB** |
| 2 GHz – 4 GHz | ±{u_inl_mid:.2f} dB (typ) | ±0.07 dB | **±{u_tot_mid:.2f} dB** |
| 4 GHz – 6 GHz | ±{u_inl_hi:.2f} dB (typ) | ±0.10 dB | **±{u_tot_hi:.2f} dB** |

Temperature contribution (NTC + TADJ, steady-state): ±0.04–0.08 dB additional.

---

## 11. Firmware Integration

Copy `results/calibration_table.h` to `firmware/Inc/` and rebuild.

```c
#include "calibration_table.h"
float power_dbm = measure_power_dbm(freq_hz, ntc_temp_c);
```

---

*Generated by `cal/calibration.py` — AD8317 Micro-RF Power Meter project*
"""


def _cal_table_md(cal):
    hdr = ("| Freq (MHz) | Slope (mV/dB) | V_intercept (V) | "
           "R² | Max INL (dB) | RMS INL (dB) |")
    sep = "|---|---|---|---|---|---|"
    rows = [hdr, sep]
    for pt in cal:
        rows.append(
            f"| {pt['freq_mhz']:.0f} "
            f"| {pt['slope_mv_db']:.3f} "
            f"| {pt['v_intercept']:.5f} "
            f"| {pt['r_squared']:.7f} "
            f"| {pt['max_inl_db']:+.4f} "
            f"| {pt['rms_inl_db']:.4f} |"
        )
    return "\n".join(rows)


def write_report(cal, gen_idn, sensor_idn, ts):
    slopes   = [pt["slope_mv_db"] for pt in cal]
    r2s      = [pt["r_squared"]   for pt in cal]
    max_inls = [pt["max_inl_db"]  for pt in cal]
    rms_inls = [pt["rms_inl_db"]  for pt in cal]

    def band_rms(lo_mhz, hi_mhz):
        pts = [pt for pt in cal if lo_mhz <= pt["freq_mhz"] <= hi_mhz]
        return np.mean([pt["rms_inl_db"] for pt in pts]) if pts else 0.0

    u_inl_lo  = band_rms(100, 2000)
    u_inl_mid = band_rms(2000, 4000)
    u_inl_hi  = band_rms(4000, 6000)

    md = REPORT_MD.format(
        date=ts,
        gen_idn=gen_idn,
        sensor_idn=sensor_idn,
        n_freq=len(cal),
        pwr_min=min(POWER_STEPS_DBM),
        pwr_max=max(POWER_STEPS_DBM),
        n_pwr=len(POWER_STEPS_DBM),
        span=max(POWER_STEPS_DBM) - min(POWER_STEPS_DBM),
        cal_table_md=_cal_table_md(cal),
        mean_slope=float(np.mean(slopes)),
        std_slope=float(np.std(slopes)),
        max_slope_dev=float(np.max(np.abs(np.array(slopes) - 22.0))),
        mean_r2=float(np.mean(r2s)),
        min_r2=float(np.min(r2s)),
        mean_max_inl=float(np.mean(max_inls)),
        worst_max_inl=float(np.max(max_inls)),
        mean_rms_inl=float(np.mean(rms_inls)),
        u_inl_lo=u_inl_lo,
        u_inl_mid=u_inl_mid,
        u_inl_hi=u_inl_hi,
        u_tot_lo=np.sqrt(u_inl_lo**2 + 0.05**2),
        u_tot_mid=np.sqrt(u_inl_mid**2 + 0.07**2),
        u_tot_hi=np.sqrt(u_inl_hi**2 + 0.10**2),
    )

    md_path = RESULTS_DIR / "Calibration_Report.md"
    md_path.write_text(md, encoding="utf-8")
    print(f"  Markdown   → {md_path}")
    return md_path


def generate_pdf(md_path: Path):
    pdf = md_path.with_suffix(".pdf")
    for engine in ["xelatex", "pdflatex", "weasyprint", "wkhtmltopdf"]:
        cmd = ["pandoc", str(md_path), "-o", str(pdf),
               f"--pdf-engine={engine}",
               "--resource-path", str(RESULTS_DIR),
               "-V", "geometry:margin=2cm",
               "-V", "fontsize=11pt"]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
            if r.returncode == 0:
                print(f"  PDF        → {pdf}  [{engine}]")
                return pdf
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    # Fallback: Edge/Chromium headless
    try:
        html = md_path.with_suffix(".html")
        subprocess.run(["pandoc", str(md_path), "-o", str(html),
                        "--self-contained"], check=True, timeout=30)
        for browser in ["msedge", "microsoft-edge", "chromium", "google-chrome"]:
            try:
                subprocess.run([browser, "--headless",
                                f"--print-to-pdf={pdf}", str(html)],
                               check=True, timeout=60)
                print(f"  PDF        → {pdf}  [{browser} headless]")
                return pdf
            except (FileNotFoundError, subprocess.CalledProcessError):
                continue
    except Exception:
        pass
    print(f"  PDF        → SKIPPED (pandoc not found). "
          f"Install pandoc then: pandoc {md_path.name} -o Calibration_Report.pdf")
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Dry-run synthetic data
# ─────────────────────────────────────────────────────────────────────────────

def make_dry_run_data() -> tuple[dict, dict]:
    """Simulate Phase 1 + Phase 2 with realistic AD8317 physics."""
    rng = np.random.default_rng(42)

    # Datasheet X-intercept vs frequency (dBm) — used to compute V_intercept
    # X-intercept (dBm) per frequency — from AD8317 datasheet + extrapolation
    # Low band (1–50 MHz): uncharacterized, assumed ~15 dBm
    # Mid band (100 MHz–3 GHz): from datasheet Table 1
    # High band (3.6–10 GHz): interpolated / extrapolated from datasheet curves
    x_int_dbm = [
        15.0, 15.0, 15.0, 15.0, 15.0, 15.0,   # 1,2,5,10,20,50 MHz
        15.0, 15.0, 14.7, 14.2, 15.0,           # 100,200,400,700,900 MHz
        14.5, 13.5, 13.0, 12.8, 12.6,           # 1200,1575,1800,2100,2400 MHz
        12.2, 12.0,                              # 2700,3000 MHz
        11.0, 11.5, 12.0, 12.5, 13.0,           # 3600,4000,4500,5000,5400 MHz
        14.0, 15.0, 14.0, 13.0, 12.5, 12.0,    # 5800,6000,7000,8000,9000,10000 MHz
    ]

    phase1 = {"freqs_hz": FREQS_HZ, "powers_dbm_set": POWER_STEPS_DBM,
              "nrp_readings": [], "gen_error_db": []}
    phase2 = {"freqs_hz": FREQS_HZ, "p_set_dbm": POWER_STEPS_DBM,
              "p_ref_dbm": [], "dut_voltage_v": []}

    for fi, freq in enumerate(FREQS_HZ):
        # Generator level error: bias + small noise, 0.01 dBm resolution
        gen_bias = rng.normal(0.05, 0.03)
        gen_err  = [round(gen_bias + rng.normal(0, 0.01), 2)
                    for _ in POWER_STEPS_DBM]
        nrp_row  = [round(p + e, 2) for p, e in zip(POWER_STEPS_DBM, gen_err)]
        phase1["nrp_readings"].append(nrp_row)
        phase1["gen_error_db"].append(gen_err)

        # AD8317 transfer function: V = slope * (X_int - P_in) + non-linearity
        slope = (22.0 + rng.normal(0, 0.25)) / 1000.0   # V/dB
        x_int = x_int_dbm[fi] + rng.normal(0, 0.1)      # dBm

        p_ref_row = []
        v_row     = []
        for pi, pwr in enumerate(POWER_STEPS_DBM):
            p_ref = nrp_row[pi]           # true power (0.01 dBm res)
            v     = slope * (x_int - p_ref)
            # Gentle S-curve INL ≈ ±2 mV peak (≈0.09 dB) — realistic AD8317 conformance
            v_nl  = 0.002 * np.sin((p_ref + 27) * 0.06)
            v    += v_nl + rng.normal(0, 3e-4)
            p_ref_row.append(p_ref)
            v_row.append(round(float(v), 6))
        phase2["p_ref_dbm"].append(p_ref_row)
        phase2["dut_voltage_v"].append(v_row)

    _save(phase1, RESULTS_DIR / "phase1_raw.json")
    _save(phase2, RESULTS_DIR / "phase2_raw.json")
    return phase1, phase2


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _banner(title):
    print("\n" + "═" * 62)
    print(title)
    print("═" * 62)


def _prog(done, total, msg):
    print(f"  [{done:3d}/{total}] {done/total*100:5.1f}%  {msg}")


def _save(data, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="AD8317 power meter calibration")
    ap.add_argument("--gen",         help="VISA address of signal generator")
    ap.add_argument("--sensor",      help="VISA address of NRP power sensor")
    ap.add_argument("--dut",         help="VISA address of DUT")
    ap.add_argument("--dry-run",     action="store_true",
                    help="Synthetic data — no hardware required")
    ap.add_argument("--phase1-only", action="store_true",
                    help="Run Phase 1 only (generator characterisation)")
    ap.add_argument("--load-phase1", metavar="JSON",
                    help="Load Phase 1 JSON, skip Phase 1 sweep")
    args = ap.parse_args()

    dry_run = args.dry_run or not VISA_AVAILABLE
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"\nAD8317 Calibration  —  {ts}")
    print(f"Results: {RESULTS_DIR.resolve()}")
    print(f"Freq points  : {len(FREQS_HZ)}")
    print(f"Power steps  : {len(POWER_STEPS_DBM)}  "
          f"({min(POWER_STEPS_DBM):+.0f} to {max(POWER_STEPS_DBM):+.0f} dBm, 2 dB)")
    print(f"Attenuator   : none — direct connection")
    print(f"Sensor res.  : 0.01 dBm (two digits after decimal)")

    if dry_run:
        print("\n*** DRY-RUN — synthetic data ***\n")
        gen_idn    = "R&S SMB100A (dry-run)"
        sensor_idn = "R&S NRP-Z21 (dry-run, 0.01 dBm)"
        phase1, phase2 = make_dry_run_data()
    else:
        gen, sensor, dut = discover(args)
        gen_idn    = gen.idn
        sensor_idn = sensor.idn

        if args.load_phase1:
            phase1 = json.loads(Path(args.load_phase1).read_text())
            print(f"\nPhase 1 loaded from {args.load_phase1}")
        else:
            phase1 = phase1_reference(gen, sensor)

        if args.phase1_only:
            gen.close(); sensor.close()
            print("\nPhase 1 complete — exiting (--phase1-only).")
            return

        phase2 = phase2_dut(gen, dut, phase1)
        gen.close(); sensor.close(); dut.close()

    _banner("Fitting calibration table")
    cal = fit_calibration(phase2)

    _banner("Writing outputs")
    _save(cal, RESULTS_DIR / "calibration_table.json")
    print(f"  JSON table → {RESULTS_DIR / 'calibration_table.json'}")
    write_c_header(cal, RESULTS_DIR / "calibration_table.h", ts)
    make_figures(cal)
    md = write_report(cal, gen_idn, sensor_idn, ts)
    generate_pdf(md)

    _banner("Calibration complete")
    slopes   = [pt["slope_mv_db"] for pt in cal]
    max_inls = [pt["max_inl_db"]  for pt in cal]
    rms_inls = [pt["rms_inl_db"]  for pt in cal]
    print(f"  Slope    : mean={np.mean(slopes):.2f}  "
          f"std={np.std(slopes):.2f}  mV/dB")
    print(f"  Max INL  : {max(max_inls):.4f} dB  "
          f"(worst frequency: "
          f"{cal[np.argmax(max_inls)]['freq_mhz']:.0f} MHz)")
    print(f"  RMS INL  : mean={np.mean(rms_inls):.4f} dB  "
          f"max={max(rms_inls):.4f} dB")
    n_fail = sum(1 for pt in cal if pt["max_inl_db"] > 0.10)
    if n_fail:
        print(f"  WARNING  : {n_fail} frequency point(s) exceed 0.1 dB INL target")
    else:
        print(f"  PASS     : all {len(cal)} points within 0.1 dB INL target")


if __name__ == "__main__":
    main()

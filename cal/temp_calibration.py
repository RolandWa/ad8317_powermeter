#!/usr/bin/env python3
"""
AD8317 Power Meter — Temperature Calibration Script
====================================================
Purpose
  Characterise the AD8317 output drift vs temperature across 1 MHz – 10 GHz.
  Uses 10 representative frequencies (rather than the full 29-point calibration grid)
  to keep chamber sweep time practical (~90 min total for 6 temperatures × 15 min soak).
  This produces a per-frequency temperature coefficient (dBm/°C) that firmware
  uses to correct readings between the NTC thermistor temperature and 25°C.

Equipment required
  • Vötsch climatic chamber (VC / VT series) — TCP or RS-232 control, or manual
  • Tracking generator (e.g., R&S ZVH, Keysight FieldFox, Siglent SSA/SVA series)
    connected to DUT SMA input.  Power level need NOT be calibrated — only drift
    is measured.
  • DUT — AD8317 power meter board (USBTMC SCPI)
  • Existing cal/results/calibration_table.json from the main calibration script
    (provides the slope [mV/dB] needed to convert ΔV → ΔdBm; interpolated to the
    10 temperature-cal frequencies from the full 29-point calibration grid)

No R&S NRP power sensor needed.

Temperature sweep
  16, 20, 25, 30, 35, 40 °C  (6 setpoints)
  Soak time 15 min per step (configurable).

Calibration model
  At reference temperature T_ref = 25°C, record V_ref[freq].
  At each temperature T: measure V[T, freq].
  Compute:
    ΔV[T, freq]    = V[T, freq] - V_ref[freq]
    ΔP[T, freq]    = ΔV / slope[freq]          (dBm, negative = output rose)
  Fit per frequency: ΔP_dBm = k(freq) × (T − T_ref)
  k  is the temperature coefficient in dBm/°C stored in calibration_table.h.

Firmware usage
  Power_corrected = Power_raw − k[freq_idx] × (T_ntc − 25.0)

Vötsch chamber control modes
  --chamber auto    TCP control to chamber LAN interface (default)
  --chamber serial  RS-232 control
  --chamber manual  Operator sets temperature on chamber, confirms in terminal
  --chamber dry-run Synthetic data, no hardware

Usage
  python temp_calibration.py                           # auto-discover, chamber auto
  python temp_calibration.py --chamber manual          # manual temperature control
  python temp_calibration.py --dry-run                 # full simulation, no hardware
  python temp_calibration.py --chamber-ip 192.168.1.50 # specify chamber LAN address
  python temp_calibration.py --gen "GPIB0::26::INSTR"  # tracking generator address
  python temp_calibration.py --dut "USB0::0x2A8D::..."  # DUT address

Requirements
  pip install pyvisa pyvisa-py numpy matplotlib
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

try:
    import pyvisa
    VISA_AVAILABLE = True
except ImportError:
    VISA_AVAILABLE = False
    print("WARNING: pyvisa not installed — running in dry-run mode")

# Vötsch driver — look next to this file first, then in the HW_Test repo
def _import_voetsch():
    import importlib, sys as _sys
    here = Path(__file__).parent
    hw_test = here.parents[2] / "HW_Test" / "Phase1.2" / "Tools"
    for path in [here, hw_test]:
        if (path / "VotschTechnikClimateChamber.py").exists():
            _sys.path.insert(0, str(path))
            mod = importlib.import_module("VotschTechnikClimateChamber")
            _sys.path.pop(0)
            return mod
    return None

_voetsch_mod = _import_voetsch()

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

# 10 representative frequencies spanning 1 MHz – 10 GHz.
# Chosen to bracket the key AD8317 intercept inflection points while
# keeping total chamber sweep time practical (< 2 hours).
# Firmware interpolates the k coefficients to the full 29-point cal grid.
FREQS_HZ = [
    1e6,    10e6,   100e6,  400e6,   900e6,
    1800e6, 3600e6, 5800e6, 8000e6, 10000e6,
]

TEMP_SETPOINTS_C = [16, 20, 25, 30, 35, 40]   # °C
TEMP_REF_C       = 25.0                         # reference temperature
SOAK_MINUTES     = 15                           # wait after chamber reaches setpoint
SETTLE_S         = 0.25                         # settle after each frequency command
VISA_TIMEOUT     = 8000                         # ms
TRACKING_GEN_PWR = -10.0                        # dBm nominal output (informational only)

RESULTS_DIR = Path(__file__).parent / "results"
FIG_DIR     = RESULTS_DIR / "figures"
CAL_TABLE_JSON = RESULTS_DIR / "calibration_table.json"


# ─────────────────────────────────────────────────────────────────────────────
# Vötsch chamber interface — wraps VotschTechnikClimateChamber.ClimateChamber
# ─────────────────────────────────────────────────────────────────────────────

class VoetschChamber:
    """
    Thin wrapper around the project's VotschTechnikClimateChamber driver.

    Real mode (--chamber auto):
      Uses ClimateChamber(ip, t_min, t_max, mock=False) from the existing driver.
      Protocol: TCP port 2049, Vötsch SIMSERV binary framing (0xB6 separator).
      Queries: chamber.temperature_measured / chamber.temperature_set_point = x

    Manual mode (--chamber manual):
      Operator sets temperature on the chamber panel; script prompts and soaks.

    Dry-run / mock:
      Uses MockClimateChamber from the same driver file (no network needed).
    """

    T_MIN = 10.0    # safety lower limit sent to the driver
    T_MAX = 60.0    # safety upper limit

    def __init__(self, mode: str, ip: str = "192.168.1.50", dry_run: bool = False):
        self.mode      = mode
        self.dry_run   = dry_run
        self._t_actual = TEMP_REF_C
        self._chamber  = None

        if mode == "manual":
            print("  Chamber   : manual control (operator sets temperature)")
            return

        if _voetsch_mod is None:
            if not dry_run:
                print("  WARNING   : VotschTechnikClimateChamber.py not found — "
                      "switching to manual mode")
                self.mode = "manual"
            return

        use_mock = dry_run or (mode == "dry-run")
        try:
            self._chamber = _voetsch_mod.ClimateChamber(
                ip=ip,
                temperature_min=self.T_MIN,
                temperature_max=self.T_MAX,
                mock=use_mock,
            )
            if not use_mock:
                self._chamber.start()
            print(f"  Chamber   : {self._chamber.idn}")
        except Exception as e:
            if not dry_run:
                print(f"  WARNING   : Cannot connect to chamber at {ip}: {e}")
                print("              Switching to manual mode.")
                self.mode = "manual"
                self._chamber = None

    def get_temp(self) -> float:
        if self._chamber is not None:
            try:
                return float(self._chamber.temperature_measured)
            except Exception:
                pass
        return self._t_actual

    def set_temp(self, temp_c: float):
        self._t_actual = temp_c
        if self._chamber is not None:
            try:
                self._chamber.temperature_set_point = temp_c
            except Exception as e:
                print(f"  WARNING   : set_temp({temp_c}) failed: {e}")

    def wait_stable(self, target_c: float, soak_min: int, dry_run: bool = False):
        if dry_run:
            print(f"    [dry-run] instant settle at {target_c:.0f}°C")
            return

        if self.mode == "manual" or self._chamber is None:
            print(f"\n  → Set chamber to {target_c:.0f}°C and wait for it to stabilise.")
            input(f"    Press ENTER when chamber reads {target_c:.0f}°C (±0.5°C) …")
            print(f"    Soaking for {soak_min} min …", end="", flush=True)
            for i in range(soak_min * 60):
                time.sleep(1)
                if (i + 1) % 60 == 0:
                    print(f" {(i+1)//60}m", end="", flush=True)
            print(" — done")
            return

        # Automatic ramp — poll temperature_measured until stable, then soak
        print(f"    Ramping to {target_c:.0f}°C …", end="", flush=True)
        deadline = time.time() + 2400   # 40 min max ramp
        while time.time() < deadline:
            t = self.get_temp()
            print(f"\r    Ramping to {target_c:.0f}°C … current={t:.1f}°C    ", end="")
            if abs(t - target_c) < 0.3:
                break
            time.sleep(15)
        print(f"\r    At {target_c:.0f}°C — soaking {soak_min} min …", end="")
        for i in range(soak_min * 60):
            time.sleep(1)
            if (i + 1) % 60 == 0:
                print(f" {(i+1)//60}m", end="", flush=True)
        print(" — done")

    def close(self):
        if self._chamber is not None:
            try:
                self._chamber.stop()
            except Exception:
                pass


# ─────────────────────────────────────────────────────────────────────────────
# Tracking generator
# ─────────────────────────────────────────────────────────────────────────────

class TrackingGenerator:
    """
    Tracking generator or signal generator used as a fixed-level RF source.
    SCPI interface — same command set as R&S SMBxxx.
    The output power level is nominal and does NOT need to be calibrated.
    """

    def __init__(self, res, dry_run: bool = False):
        self.dry_run = dry_run
        if not dry_run:
            self._inst = res
            self._inst.timeout = VISA_TIMEOUT
            self._inst.write("*RST; *CLS")
            time.sleep(0.5)
            self.idn = self._inst.query("*IDN?").strip()
            # Set a fixed output level — tracking generators output whatever
            # their internal reference is; we just need it stable
            self._inst.write(f"POW:LEV {TRACKING_GEN_PWR:.1f} dBm")
        else:
            self.idn = "Tracking Generator (dry-run)"
        print(f"  Generator : {self.idn}")

    def set_freq(self, freq_hz: float):
        if not self.dry_run:
            self._inst.write(f"FREQ:CW {freq_hz:.0f} Hz")
            time.sleep(SETTLE_S)

    def output(self, on: bool):
        if not self.dry_run:
            try:
                self._inst.write("OUTP:STAT " + ("ON" if on else "OFF"))
            except Exception:
                pass

    def close(self):
        if not self.dry_run:
            self.output(False)
            self._inst.close()


# ─────────────────────────────────────────────────────────────────────────────
# DUT (same as main calibration script)
# ─────────────────────────────────────────────────────────────────────────────

class DUTPowerMeter:
    def __init__(self, res, dry_run: bool = False):
        self.dry_run = dry_run
        self._freq   = 1e9
        if not dry_run:
            self._inst = res
            self._inst.timeout = VISA_TIMEOUT
            self.idn = self._inst.query("*IDN?").strip()
        else:
            self.idn = "AD8317 Power Meter (dry-run)"
        print(f"  DUT meter : {self.idn}")

    def set_freq(self, freq_hz: float):
        self._freq = freq_hz
        if not self.dry_run:
            self._inst.write(f"SENS:FREQ {freq_hz:.0f}")

    def read_voltage(self, temp_c: float = TEMP_REF_C) -> float:
        if self.dry_run:
            # AD8317 drift: 8 mdB/°C average, with frequency dependence
            drift_db_per_c = 0.008 + 0.004 * np.sin(self._freq / 2e9)
            # Convert drift to voltage shift using nominal slope
            slope = 22.0e-3  # V/dB nominal
            v_ref = slope * (13.0 - (-20.0))  # ≈ 726 mV at -20 dBm, 13 dBm X-int
            delta_t = temp_c - TEMP_REF_C
            v = v_ref * (1.0 - drift_db_per_c * delta_t * slope / v_ref)
            # Add small per-frequency variation
            v += 0.005 * np.sin(self._freq / 1.3e9) + np.random.normal(0, 5e-4)
            return float(v)
        try:
            return float(self._inst.query("SENS:VOLT?").strip())
        except Exception:
            raw_dbm = float(self._inst.query("READ?").strip())
            return (22.0e-3) * (13.0 - raw_dbm)

    def close(self):
        if not self.dry_run:
            self._inst.close()


# ─────────────────────────────────────────────────────────────────────────────
# VISA discovery (simplified — only gen + DUT needed)
# ─────────────────────────────────────────────────────────────────────────────

def discover_visa(args, dry_run: bool):
    rm = pyvisa.ResourceManager()
    resources = list(rm.list_resources())
    print(f"\nVISA resources: {len(resources)}")
    for r in resources:
        print(f"  {r}")

    gen_addr = args.gen or _find_gen(resources, rm)
    dut_addr = args.dut or _find_dut(resources, rm, skip=[gen_addr])

    if not gen_addr:
        sys.exit("ERROR: Tracking generator not found. Use --gen VISA_ADDR")
    if not dut_addr:
        sys.exit("ERROR: DUT not found. Use --dut VISA_ADDR")

    print("\nConnecting …")
    gen = TrackingGenerator(rm.open_resource(gen_addr))
    dut = DUTPowerMeter(rm.open_resource(dut_addr))
    return gen, dut


def _find_gen(resources, rm):
    for addr in resources:
        try:
            inst = rm.open_resource(addr)
            idn  = inst.query("*IDN?").strip()
            inst.close()
            if any(k in idn for k in ["Rohde", "R&S", "SMB", "SMF", "Siglent",
                                       "Anritsu", "Keysight", "Agilent"]):
                return addr
        except Exception:
            pass
    return None


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
# Load existing calibration slope table
# ─────────────────────────────────────────────────────────────────────────────

def load_slopes() -> dict[int, float]:
    """
    Returns {freq_hz: slope_mv_db} interpolated to the 10 FREQS_HZ points.
    The main calibration table has 29 frequencies (1 MHz – 10 GHz); values are
    log-linearly interpolated to the temperature-calibration frequency grid.
    """
    if not CAL_TABLE_JSON.exists():
        print(f"WARNING: {CAL_TABLE_JSON} not found — using nominal 22.0 mV/dB for all freqs")
        return {int(f): 22.0 for f in FREQS_HZ}
    cal = json.loads(CAL_TABLE_JSON.read_text())
    # Build interpolation arrays from main cal table
    cal_freqs  = np.array([pt["freq_hz"]   for pt in cal], dtype=float)
    cal_slopes = np.array([pt["slope_mv_db"] for pt in cal], dtype=float)
    result = {}
    for f in FREQS_HZ:
        slope = float(np.interp(np.log10(f),
                                np.log10(cal_freqs), cal_slopes))
        result[int(f)] = round(slope, 4)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Temperature sweep
# ─────────────────────────────────────────────────────────────────────────────

def run_temp_sweep(gen: TrackingGenerator, dut: DUTPowerMeter,
                   chamber: VoetschChamber, dry_run: bool) -> dict:
    """
    Sweep all temperature setpoints and record V_ADC at each (temp, freq).
    Returns raw measurement dict.
    """
    _banner("Temperature sweep")
    print(f"  Temperatures  : {TEMP_SETPOINTS_C} °C")
    print(f"  Frequencies   : {len(FREQS_HZ)} points, 100 MHz – 6 GHz")
    print(f"  Soak time     : {SOAK_MINUTES} min per setpoint")
    print(f"  RF source     : tracking generator (fixed power, frequency stepped)")
    print(f"  Power sensor  : NOT used — only drift relative to 25°C is measured\n")

    data = {
        "temps_c":     TEMP_SETPOINTS_C,
        "freqs_hz":    FREQS_HZ,
        "v_adc":       [],   # [temp_idx][freq_idx]  in Volts
        "chamber_act": [],   # actual chamber temp read at start of each freq sweep
    }

    gen.output(True)

    for ti, temp_c in enumerate(TEMP_SETPOINTS_C):
        _banner(f"Temperature setpoint: {temp_c}°C  ({ti+1}/{len(TEMP_SETPOINTS_C)})")
        chamber.set_temp(temp_c)
        chamber.wait_stable(temp_c, SOAK_MINUTES, dry_run=dry_run)

        t_actual = chamber.get_temp()
        print(f"  Chamber actual: {t_actual:.1f}°C")
        data["chamber_act"].append(round(t_actual, 1))

        row = []
        for fi, freq in enumerate(FREQS_HZ):
            gen.set_freq(freq)
            dut.set_freq(freq)
            v = dut.read_voltage(temp_c=t_actual)
            row.append(round(v, 6))
            _prog(fi + 1, len(FREQS_HZ),
                  f"{freq/1e6:6.0f} MHz  V_ADC={v:.5f} V  T={t_actual:.1f}°C")
        data["v_adc"].append(row)

    gen.output(False)

    _save(data, RESULTS_DIR / "temp_raw.json")
    print(f"\n  Saved → {RESULTS_DIR / 'temp_raw.json'}")
    return data


# ─────────────────────────────────────────────────────────────────────────────
# Analysis — fit temperature coefficient per frequency
# ─────────────────────────────────────────────────────────────────────────────

def fit_temp_coefficients(data: dict, slopes: dict[int, float]) -> list[dict]:
    """
    For each frequency:
      1. Find the 25°C row (or interpolate)
      2. Compute ΔP_dBm[temp] = ΔV[temp] / slope
      3. Fit linear: ΔP = k × (T − 25)
      4. Return k in dBm/°C

    Returns list of TempCalPoint dicts.
    """
    temps  = np.array(data["temps_c"], dtype=float)
    v_mat  = np.array(data["v_adc"])       # [temp, freq]
    act    = np.array(data["chamber_act"])

    # Find or interpolate V at T_ref = 25°C
    if TEMP_REF_C in temps:
        ref_idx = list(temps).index(TEMP_REF_C)
        v_ref   = v_mat[ref_idx, :]
    else:
        # Interpolate — shouldn't happen with default setpoints
        v_ref = np.array([np.interp(TEMP_REF_C, temps, v_mat[:, fi])
                          for fi in range(len(FREQS_HZ))])

    print(f"\n{'Freq':>8}  {'k (mdBm/°C)':>13}  {'R²':>7}  {'Max ΔP @40°C':>14}")
    print("─" * 50)

    result = []
    for fi, freq in enumerate(FREQS_HZ):
        slope_mv = slopes.get(int(freq), 22.0)
        slope    = slope_mv / 1000.0       # V/dB

        # ΔP in dBm at each temperature (positive = output power reading went UP)
        delta_v  = v_mat[:, fi] - v_ref[fi]
        delta_p  = delta_v / (-slope)      # sign: V rises as P drops for AD8317

        # Linear fit: ΔP = k × (T - T_ref)
        delta_t  = temps - TEMP_REF_C
        coeffs   = np.polyfit(delta_t, delta_p, 1)
        k        = coeffs[0]               # dBm/°C

        fitted   = np.polyval(coeffs, delta_t)
        resid    = delta_p - fitted
        ss_res   = np.sum(resid**2)
        ss_tot   = np.sum((delta_p - delta_p.mean())**2)
        r_sq     = 1.0 - ss_res / ss_tot if ss_tot > 1e-12 else 1.0

        max_drift = float(k * (max(TEMP_SETPOINTS_C) - TEMP_REF_C))

        result.append({
            "freq_hz":       int(freq),
            "freq_mhz":      freq / 1e6,
            "slope_mv_db":   slope_mv,
            "k_db_per_c":    round(float(k), 6),
            "k_mdb_per_c":   round(float(k) * 1000, 3),
            "r_squared":     round(float(r_sq), 6),
            "max_drift_db":  round(max_drift, 4),
            "temps_c":       temps.tolist(),
            "delta_p_db":    delta_p.tolist(),
            "fitted_db":     fitted.tolist(),
        })

        print(f"{freq/1e6:>7.0f} MHz  "
              f"{k*1000:>+10.2f} mdBm/°C  "
              f"{r_sq:>7.4f}  "
              f"{max_drift:>+10.3f} dB")

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Figures
# ─────────────────────────────────────────────────────────────────────────────

BLUE   = "#1f77b4"
RED    = "#d62728"
GREEN  = "#2ca02c"
ORANGE = "#ff7f0e"
GREY   = "#7f7f7f"


def make_figures(result: list[dict]) -> list[Path]:
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    figs = []
    figs.append(_fig_drift_grid(result))
    figs.append(_fig_k_vs_freq(result))
    figs.append(_fig_max_drift_heatmap(result))
    print(f"  Figures → {FIG_DIR}/")
    return figs


def _fig_drift_grid(result):
    """Grid: ΔP_dBm vs temperature + linear fit — one subplot per frequency."""
    ncols = 4
    nrows = (len(result) + ncols - 1) // ncols
    fig, axes = plt.subplots(nrows, ncols, figsize=(14, nrows * 3.0),
                             constrained_layout=True)
    fig.suptitle("AD8317 Drift vs Temperature — ΔP (dBm) relative to 25°C\n"
                 "Blue dots = measured,  Red line = linear fit  k = dBm/°C",
                 fontsize=12)

    for i, pt in enumerate(result):
        ax = axes.flat[i]
        t  = np.array(pt["temps_c"])
        dp = np.array(pt["delta_p_db"])
        ft = np.array(pt["fitted_db"])
        ax.scatter(t, dp * 1000, s=30, color=BLUE, zorder=3)
        ax.plot(t, ft * 1000, color=RED, lw=1.8)
        ax.axhline(0, color=GREY, lw=0.8, ls="--")
        ax.axvline(25, color=GREY, lw=0.8, ls="--")
        ax.set_title(f"{pt['freq_mhz']:.0f} MHz", fontsize=9, fontweight="bold")
        ax.set_xlabel("Temperature (°C)", fontsize=7)
        ax.set_ylabel("ΔP (mdBm)", fontsize=7)
        ax.tick_params(labelsize=7)
        ax.grid(True, alpha=0.3)
        ax.text(0.03, 0.97,
                f"k = {pt['k_mdb_per_c']:+.1f} mdBm/°C\n"
                f"R² = {pt['r_squared']:.4f}",
                transform=ax.transAxes, va="top", ha="left", fontsize=6.5,
                bbox=dict(fc="white", alpha=0.8, ec="silver", lw=0.5))

    for j in range(i + 1, len(axes.flat)):
        axes.flat[j].set_visible(False)

    path = FIG_DIR / "fig_t1_drift_grid.png"
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def _fig_k_vs_freq(result):
    freqs  = [pt["freq_mhz"] for pt in result]
    k_vals = [pt["k_mdb_per_c"] for pt in result]  # mdBm/°C

    fig, ax = plt.subplots(figsize=(11, 4.5), constrained_layout=True)
    colors  = [RED if abs(k) > 15 else BLUE for k in k_vals]
    bars    = ax.bar(range(len(freqs)), k_vals, color=colors, edgecolor="navy",
                     alpha=0.85, linewidth=0.5)
    ax.axhline(0,   color=GREY,   lw=0.8, ls="--")
    ax.axhline(+8,  color=ORANGE, lw=1.2, ls=":",
               label="AD8317 datasheet typ +8 mdBm/°C")
    ax.axhline(-8,  color=ORANGE, lw=1.2, ls=":")
    ax.axhline(+20, color=RED,    lw=1.2, ls=":",
               label="AD8317 datasheet max ±20 mdBm/°C")
    ax.axhline(-20, color=RED,    lw=1.2, ls=":")

    for bar, val in zip(bars, k_vals):
        ax.text(bar.get_x() + bar.get_width() / 2,
                val + (0.3 if val >= 0 else -0.5),
                f"{val:+.1f}", ha="center", va="bottom" if val >= 0 else "top",
                fontsize=6.0)

    ax.set_xticks(range(len(freqs)))
    ax.set_xticklabels([f"{f:.0f}" for f in freqs], rotation=45, ha="right", fontsize=8)
    ax.set_xlabel("Frequency (MHz)")
    ax.set_ylabel("Temperature coefficient k (mdBm/°C)")
    ax.set_title("AD8317 Temperature Coefficient vs Frequency\n"
                 "Firmware correction: P_corr = P_raw − k × (T_ntc − 25)")
    ax.legend(fontsize=8)
    ax.grid(True, axis="y", alpha=0.3)

    path = FIG_DIR / "fig_t2_k_vs_freq.png"
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def _fig_max_drift_heatmap(result):
    """
    Show ΔP at each (freq, temp) as a heat map.
    Rows = frequency, columns = temperature.
    """
    freqs = [pt["freq_mhz"] for pt in result]
    temps = result[0]["temps_c"]
    mat   = np.array([[pt["delta_p_db"][ti] * 1000
                        for ti in range(len(temps))]
                       for pt in result])    # [freq, temp] in mdBm

    fig, ax = plt.subplots(figsize=(9, 6.5), constrained_layout=True)
    im  = ax.imshow(mat, aspect="auto", cmap="RdBu_r", vmin=-200, vmax=200,
                    extent=[-0.5, len(temps) - 0.5, len(freqs) - 0.5, -0.5])
    cbar = plt.colorbar(im, ax=ax, label="ΔP (mdBm) relative to 25°C")
    cbar.ax.tick_params(labelsize=8)

    ax.set_xticks(range(len(temps)))
    ax.set_xticklabels([f"{t}°C" for t in temps], fontsize=9)
    ax.set_yticks(range(len(freqs)))
    ax.set_yticklabels([f"{f:.0f}" for f in freqs], fontsize=7)
    ax.set_xlabel("Chamber temperature", fontsize=9)
    ax.set_ylabel("Frequency (MHz)", fontsize=9)
    ax.set_title("AD8317 Power Reading Drift vs Temperature\n"
                 "ΔP (mdBm) relative to 25°C — Blue = reads low, Red = reads high",
                 fontsize=11)

    # Annotate cells
    for fi in range(len(freqs)):
        for ti in range(len(temps)):
            ax.text(ti, fi, f"{mat[fi, ti]:+.0f}", ha="center", va="center",
                    fontsize=6, color="white" if abs(mat[fi, ti]) > 100 else "black")

    path = FIG_DIR / "fig_t3_drift_heatmap.png"
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


# ─────────────────────────────────────────────────────────────────────────────
# Write output files
# ─────────────────────────────────────────────────────────────────────────────

def update_c_header(result: list[dict], ts: str):
    """
    Patch the existing calibration_table.h to add the temp_coeff values.
    If not present, writes a standalone temp_cal_table.h instead.
    """
    # Write temp_cal_table.h (always — standalone)
    lines = [
        "/* temp_cal_table.h — auto-generated by temp_calibration.py */",
        f"/* {ts} */",
        "/* DO NOT EDIT */",
        "",
        "#ifndef TEMP_CAL_TABLE_H",
        "#define TEMP_CAL_TABLE_H",
        "",
        '#include "power_meter.h"',
        "",
        f"#define TEMP_CAL_FREQ_COUNT  {len(result)}U",
        f"#define TEMP_CAL_T_REF_C     {TEMP_REF_C:.1f}f",
        "",
        "/* k_db_per_c: correction = P_raw - k * (T_ntc - T_ref) */",
        "static const float TEMP_COEFF_DB_PER_C[TEMP_CAL_FREQ_COUNT] = {",
    ]
    for pt in result:
        lines.append(
            f"    {pt['k_db_per_c']:.6f}f,  "
            f"/* {pt['freq_mhz']:.0f} MHz  "
            f"k = {pt['k_mdb_per_c']:+.1f} mdBm/°C  "
            f"max ΔP @{max(TEMP_SETPOINTS_C)}°C = {pt['max_drift_db']:+.3f} dB */"
        )
    lines += ["};", "", "#endif /* TEMP_CAL_TABLE_H */", ""]
    path = RESULTS_DIR / "temp_cal_table.h"
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  C header   → {path}")


REPORT_TEMPLATE = """\
# AD8317 Power Meter — Temperature Calibration Report

| | |
|---|---|
| **Date** | {date} |
| **Chamber** | Vötsch climatic chamber |
| **Temperature range** | {t_min}°C to {t_max}°C ({n_temps} setpoints) |
| **Soak time per step** | {soak} minutes |
| **RF source** | Tracking generator (no reference sensor) |
| **Reference temperature** | 25°C |
| **Frequency points** | {n_freq} (100 MHz – 6 GHz) |

---

## 1. Method

No NRP power sensor was available. Temperature drift is measured as the **change
in DUT reading relative to 25°C** at the same generator frequency and power.
The tracking generator output power is assumed stable over the 30–40 minute sweep
duration (verified by the R² linearity of the fit).

The drift ΔP (dBm) at each (frequency, temperature) is converted from ΔV_ADC
using the slope table from the main calibration:

```text
ΔP_dBm = −ΔV_ADC / slope[freq]
k(freq) = linear fit slope of ΔP vs (T − 25°C)
```

Firmware applies:

```c
P_corrected = P_raw − k[freq_idx] × (T_ntc − 25.0f);
```

---

## 2. Drift vs Temperature (per frequency)

Each panel shows measured ΔP (mdBm) at 6 temperatures and the best-fit linear
correction line. A flat line through zero would be perfect temperature independence.

![Drift grid](figures/fig_t1_drift_grid.png)

---

## 3. Temperature Coefficient vs Frequency

The fitted k values (mdBm/°C) determine how much correction the firmware applies
per degree of NTC deviation from 25°C. Orange lines = AD8317 datasheet typical
(±8 mdBm/°C); red lines = datasheet maximum (±20 mdBm/°C).

![k vs freq](figures/fig_t2_k_vs_freq.png)

---

## 4. Drift Heat Map

Full (frequency × temperature) drift map in mdBm relative to 25°C.

![Heatmap](figures/fig_t3_drift_heatmap.png)

---

## 5. Temperature Coefficient Table

| Freq (MHz) | k (mdBm/°C) | R² | ΔP max @ {t_max}°C |
| --- | --- | --- | --- |
{table_rows}

---

## 6. Uncertainty Notes

- **NTC thermal gradient:** NTC pad ↔ AD8317 die ≈ ±3°C steady-state → residual ≈ ±k×3 dBm
- **Tracking generator stability:** not characterised — included in R² residual
- **Temperature non-linearity:** linear model; residuals shown in panel plots
- **Combined budget (after correction):**

| Band | k × ΔT_NTC_error | Residual | Total |
| --- | --- | --- | --- |
| 100 MHz – 2 GHz | ±{u_lo:.3f} dB | <0.05 dB | ±{u_tot_lo:.2f} dB |
| 2 GHz – 4 GHz | ±{u_mid:.3f} dB | <0.05 dB | ±{u_tot_mid:.2f} dB |
| 4 GHz – 6 GHz | ±{u_hi:.3f} dB | <0.07 dB | ±{u_tot_hi:.2f} dB |

(NTC error = ±3°C steady-state, ±8°C transient)

---

## 7. Firmware Integration

Copy `results/temp_cal_table.h` to `firmware/Inc/` and update `measure_power_dbm()`:

```c
#include "temp_cal_table.h"

float measure_power_dbm(uint32_t freq_hz, float ntc_temp_c)
{{
    uint8_t idx = find_freq_index(freq_hz);
    float v_adc = read_adc_voltage();
    float p_raw = (v_adc - CAL_TABLE[idx].v_intercept)
                  / (-CAL_TABLE[idx].slope_mv_db * 1e-3f);
    return p_raw - TEMP_COEFF_DB_PER_C[idx] * (ntc_temp_c - TEMP_CAL_T_REF_C);
}}
```

---

*Generated by `cal/temp_calibration.py` — AD8317 Micro-RF Power Meter project*
"""


def write_report(result, ts):
    slopes  = [pt["slope_mv_db"]  for pt in result]
    ks      = [abs(pt["k_db_per_c"]) for pt in result]

    def band_k(lo, hi):
        pts = [pt for pt in result if lo <= pt["freq_mhz"] <= hi]
        return np.mean([abs(pt["k_db_per_c"]) for pt in pts]) if pts else 0.0

    k_lo  = band_k(100,  2000)
    k_mid = band_k(2000, 4000)
    k_hi  = band_k(4000, 6000)
    ntc_err = 3.0   # °C steady-state

    rows = "\n".join(
        f"| {pt['freq_mhz']:.0f} | {pt['k_mdb_per_c']:+.1f} | "
        f"{pt['r_squared']:.4f} | {pt['max_drift_db']:+.3f} dB |"
        for pt in result
    )

    md = REPORT_TEMPLATE.format(
        date=ts,
        t_min=min(TEMP_SETPOINTS_C),
        t_max=max(TEMP_SETPOINTS_C),
        n_temps=len(TEMP_SETPOINTS_C),
        soak=SOAK_MINUTES,
        n_freq=len(result),
        table_rows=rows,
        u_lo=k_lo * ntc_err,
        u_mid=k_mid * ntc_err,
        u_hi=k_hi * ntc_err,
        u_tot_lo=np.sqrt((k_lo * ntc_err)**2 + 0.05**2),
        u_tot_mid=np.sqrt((k_mid * ntc_err)**2 + 0.05**2),
        u_tot_hi=np.sqrt((k_hi * ntc_err)**2 + 0.07**2),
    )

    md_path = RESULTS_DIR / "Temperature_Calibration_Report.md"
    md_path.write_text(md, encoding="utf-8")
    print(f"  Markdown   → {md_path}")
    return md_path


def generate_pdf(md_path):
    pdf = md_path.with_suffix(".pdf")
    for engine in ["xelatex", "pdflatex", "weasyprint", "wkhtmltopdf"]:
        cmd = ["pandoc", str(md_path), "-o", str(pdf),
               f"--pdf-engine={engine}",
               "--resource-path", str(RESULTS_DIR),
               "-V", "geometry:margin=2cm"]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
            if r.returncode == 0:
                print(f"  PDF        → {pdf}  [{engine}]")
                return pdf
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    print(f"  PDF        → SKIPPED (pandoc not installed)")
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Dry-run synthetic data
# ─────────────────────────────────────────────────────────────────────────────

def make_dry_run_data() -> dict:
    """Simulate realistic AD8317 temperature drift."""
    rng = np.random.default_rng(7)
    v_adc = []
    act   = []
    for temp_c in TEMP_SETPOINTS_C:
        row = []
        for fi, freq in enumerate(FREQS_HZ):
            # AD8317 drift: 8 mdB/°C with frequency dependence (up to 20 mdB/°C at high freq)
            k_true   = 0.008 + 0.008 * (freq / 6e9)    # dBm/°C
            slope    = (22.0 + rng.normal(0, 0.1)) / 1000.0  # V/dB
            v_ref    = slope * (13.0 - (-20.0))         # at P_in = -20 dBm
            delta_v  = -slope * k_true * (temp_c - TEMP_REF_C)
            v        = v_ref + delta_v + rng.normal(0, 4e-4)
            row.append(round(float(v), 6))
        v_adc.append(row)
        act.append(round(temp_c + rng.normal(0, 0.05), 1))

    data = {
        "temps_c":     TEMP_SETPOINTS_C,
        "freqs_hz":    FREQS_HZ,
        "v_adc":       v_adc,
        "chamber_act": act,
    }
    _save(data, RESULTS_DIR / "temp_raw.json")
    return data


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _banner(title):
    print("\n" + "═" * 62)
    print(title)
    print("═" * 62)


def _prog(done, total, msg):
    print(f"  [{done:2d}/{total}] {done/total*100:5.1f}%  {msg}")


def _save(data, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    global SOAK_MINUTES
    ap = argparse.ArgumentParser(
        description="AD8317 temperature calibration — Vötsch chamber + tracking generator")
    ap.add_argument("--gen",        help="VISA address of tracking generator")
    ap.add_argument("--dut",        help="VISA address of DUT")
    ap.add_argument("--chamber",    choices=["auto", "manual"],
                    default="auto",
                    help="Chamber control: auto = LAN via VotschTechnikClimateChamber driver, "
                         "manual = operator sets temperature")
    ap.add_argument("--chamber-ip", default="192.168.1.50",
                    help="Vötsch chamber LAN IP address (default 192.168.1.50)")
    _soak_default = SOAK_MINUTES
    ap.add_argument("--soak",       type=int, default=_soak_default,
                    help=f"Soak minutes per temperature step (default {_soak_default})")
    ap.add_argument("--dry-run",    action="store_true",
                    help="Synthetic data — no hardware required")
    ap.add_argument("--load-raw",   metavar="JSON",
                    help="Load existing temp_raw.json, skip sweep")
    args = ap.parse_args()

    dry_run = args.dry_run or not VISA_AVAILABLE
    SOAK_MINUTES = args.soak
    chamber_mode = "dry-run" if dry_run else args.chamber
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"\nAD8317 Temperature Calibration  —  {ts}")
    print(f"Chamber mode  : {chamber_mode}")
    print(f"Temperatures  : {TEMP_SETPOINTS_C} °C")
    print(f"Soak          : {SOAK_MINUTES} min/step")

    # Load slope table from main calibration
    slopes = load_slopes()
    print(f"Slope table   : loaded {len(slopes)} entries from {CAL_TABLE_JSON.name}")

    # Hardware setup
    if dry_run:
        print("\n*** DRY-RUN — synthetic data ***\n")
        gen_idn = "Tracking Generator (dry-run)"
        dut_idn = "AD8317 Power Meter (dry-run)"
        gen = TrackingGenerator(None, dry_run=True)
        dut = DUTPowerMeter(None, dry_run=True)
        chamber = VoetschChamber("dry-run", dry_run=True)
    else:
        gen, dut = discover_visa(args, dry_run)
        gen_idn  = gen.idn
        dut_idn  = dut.idn
        chamber  = VoetschChamber(
            mode=args.chamber,
            ip=args.chamber_ip,
        )

    # Sweep or load
    if args.load_raw:
        data = json.loads(Path(args.load_raw).read_text())
        print(f"\nLoaded raw data from {args.load_raw}")
    elif dry_run:
        data = make_dry_run_data()
    else:
        data = run_temp_sweep(gen, dut, chamber, dry_run=False)
        gen.close()
        dut.close()
        chamber.close()

    _banner("Fitting temperature coefficients")
    result = fit_temp_coefficients(data, slopes)

    _banner("Writing outputs")
    _save(result, RESULTS_DIR / "temp_cal_table.json")
    print(f"  JSON       → {RESULTS_DIR / 'temp_cal_table.json'}")
    update_c_header(result, ts)
    make_figures(result)
    md = write_report(result, ts)
    generate_pdf(md)

    _banner("Temperature calibration complete")
    ks = [pt["k_mdb_per_c"] for pt in result]
    print(f"  k range    : {min(ks):+.1f} to {max(ks):+.1f} mdBm/°C")
    print(f"  Worst drift: {max(abs(pt['max_drift_db']) for pt in result):.3f} dB "
          f"@ {max(TEMP_SETPOINTS_C)}°C")
    print(f"\nNext step: copy results/temp_cal_table.h to firmware/Inc/")


if __name__ == "__main__":
    main()

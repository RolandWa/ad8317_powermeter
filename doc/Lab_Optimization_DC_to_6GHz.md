# Laboratory Optimization: AD8317 Power Meter DC to 6 GHz

**Project:** Handheld Micro-RF Power Meter (ad8317_powermeter)
**Date:** 2026-07-24
**Author:** Author
**Scope:** Optimization of signal path, hardware, firmware, and calibration for laboratory use
over the full 100 MHz – 6 GHz measurement range.

---

## 1. Frequency Coverage — Realistic Limits

The AD8317 is specified from 1 MHz to 10 GHz, but the useful lower bound is constrained by
the AC coupling network:

| Limit | Frequency | Cause |
| --- | --- | --- |
| AC coupling −3 dB (47 nF + 50 Ω) | ~68 kHz | External coupling capacitors |
| Internal gain-stage pole | ~850 kHz | Internal 10 pF ∥ 18.7 kΩ differential input |
| Lowest characterized by ADI | **50 MHz** | Lowest TADJ table entry (Table 4, datasheet) |
| Lowest with reliable log conformance | **~100 MHz** | No conformance curves below 900 MHz in datasheet |

**Practical operating range for this instrument: 100 MHz – 6 GHz.**

Below 100 MHz the slope remains nominally −22 mV/dB but the log conformance error is
uncharacterized — individual unit calibration is required for each frequency point.

---

## 2. AD8317 Frequency-Dependent Parameters

### 2.1 Slope vs Frequency

The slope is **constant at −22 mV/dB (typical)** from 900 MHz through 8 GHz.
No frequency dependence of slope is specified by ADI for any tabulated frequency.

```text
Slope (all frequencies, typ):  −22.0 mV/dB
Slope (min/max at 900 MHz and 1.9 GHz): −25.0 / −19.5 mV/dB
```

For the calibration model, a **single slope value per frequency** is measured and stored
during calibration. Re-measuring slope at every frequency is not necessary if slope
deviation from 22 mV/dB is small.

### 2.2 Intercept vs Frequency (Dominant Calibration Variable)

The intercept shifts by up to ±5 dBm across the operating range. This is the primary
frequency-dependent correction stored in firmware as `v_intercept` (V at P_in = 0 dBm).

| Frequency | VOUT at −10 dBm (typ) | X-intercept (typ, dBm) | ΔIntercept from 900 MHz |
| --- | --- | --- | --- |
| 100 MHz | (uncharacterized) | (cal required) | — |
| 900 MHz | 0.58 V | **+15 dBm** | 0 dB (reference) |
| 1.9 GHz | — | **+14 dBm** | −1 dB |
| 2.2 GHz | — | **+14 dBm** | −1 dB |
| 3.6 GHz | — | **+11 dBm** | −4 dB |
| 5.8 GHz | — | **+16 dBm** | +1 dB |
| 6.0 GHz | (interpolate) | ~+17 dBm est. | ~+2 dB |

The worst-case intercept swing across the full 100 MHz – 6 GHz range is **~6–8 dBm**,
directly translating to a ±3–4 dB measurement error without frequency-indexed calibration.

### 2.3 Input Impedance vs Frequency

The AD8317 die input impedance varies strongly with frequency. The external 52.3 Ω shunt
resistor provides the 50 Ω match.

| Frequency | Internal Z (bare die) | With 52.3 Ω shunt | Return loss (est.) |
| --- | --- | --- | --- |
| 900 MHz | 1500 Ω ∥ 0.33 pF | ~51 Ω | >20 dB |
| 1.9 GHz | 950 Ω ∥ 0.38 pF | ~50 Ω | >20 dB |
| 3.6 GHz | 300 Ω ∥ 0.33 pF | ~44 Ω | ~16 dB |
| 5.8 GHz | 110 Ω ∥ 0.05 pF | ~36 Ω | ~10 dB |

Above 4 GHz the 50 Ω match begins to degrade. The input mismatch is absorbed into the
per-frequency calibration — it does not need separate correction provided the calibration
cable path matches the measurement path.

---

## 3. Input Signal Path

### 3.1 Input Connection — Direct (No Attenuator Pad)

The calibration workflow uses a **direct connection** from the RF generator to the DUT SMA
input. No external attenuator is fitted in the calibrated configuration.

| Range at detector INHI | Range at SMA connector |
| --- | --- |
| −54 dBm to 0 dBm (calibrated sweep range) | **−54 dBm to 0 dBm** |
| 0 dBm upper limit (±1 dB log conformance) | **0 dBm at SMA** |
| Absolute maximum (+12 dBm at INHI) | **+12 dBm at SMA** — do not exceed |

If the DUT is used to measure signals above 0 dBm, an external attenuator can be added and
its insertion loss entered as a system offset via the `CALC:GAIN` SCPI command or as a
firmware constant.

### 3.2 PCB Layout Requirements at 6 GHz

At 6 GHz, λ/4 in FR-4 (εr = 4.4) ≈ 6 mm. Any transmission line discontinuity becomes
significant above ~1 GHz.

**50 Ω microstrip on 1.6 mm FR-4:**

```text
Width = 3.0 mm  (εr = 4.4, h = 1.6 mm, t = 35 µm copper)
Effective εr ≈ 3.1,  velocity factor ≈ 0.57
λ at 6 GHz ≈ 28.5 mm  →  λ/4 ≈ 7.1 mm
```

**Layout rules for the RF input trace (SMA to INHI):**

| Rule | Value |
| --- | --- |
| Trace width | 3.0 mm (50 Ω microstrip) |
| Maximum trace length | < 8 mm (< λ/8 at 6 GHz) |
| Ground plane clearance | Solid ground on B.Cu under trace, no cuts |
| Via stitching | Ground vias every 3 mm on both sides of trace |
| Coupling capacitor C1 | 47 nF 0402 C0G, placed within 1 mm of INHI pin |
| 52.3 Ω shunt R1 | 0402 placed within 1 mm of INHI, before C1 |
| INLO bypass C2 | 47 nF 0402 C0G to GND, at INLO pin |

**Do not route any digital signals parallel to the RF trace within 5 mm.**

---

## 4. Temperature Compensation — TADJ Strategy for DC to 6 GHz

### 4.1 The Problem: No Single RTADJ Value for DC to 6 GHz

The AD8317 TADJ pin requires different resistors at different frequencies (Table 4, datasheet).
There is no compromise value that is correct across the full operating range:

| Band | Optimal RTADJ |
| --- | --- |
| 50 MHz – 900 MHz | 18 kΩ |
| 1.8 GHz – 3.6 GHz | 8 kΩ |
| 5.3 GHz – 5.8 GHz | 500 Ω |

### 4.2 Recommended: Fixed RTADJ = 8 kΩ + NTC Software Correction

For minimum BOM cost and PCB area (Hammond 1590A enclosure):

```text
RTADJ = 8 kΩ fixed (optimized for 1.8–3.6 GHz)
NTC thermistor 0402 (Murata NCP15WF104) placed 1 mm from AD8317 → software correction
```

Residual temperature error after NTC + Phase 3 calibration correction:
**±0.03–0.10 dB steady-state** (see `ADC_Signal_Path_Analysis.md`, Section 9).

A 3-channel analog mux (e.g. TS5A3159) to switch RTADJ per band is also possible if
tighter temperature compensation is required above 3.6 GHz.

---

## 5. Calibration Framework for 100 MHz – 6 GHz

### 5.1 Overview — Two-Script, Three-Phase Workflow

Calibration produces two C header files that firmware loads from flash.

| Script | Phase | Equipment | Output header |
| --- | --- | --- | --- |
| `cal/calibration.py` | 1: generator reference | R&S generator + NRP sensor (0.01 dBm) | `calibration_table.h` |
| `cal/calibration.py` | 2: DUT linearity sweep | R&S generator → DUT direct (no pad) | `calibration_table.h` |
| `cal/temp_calibration.py` | 3: temperature drift | Vötsch chamber + tracking generator | `temp_cal_table.h` |

**The NRP power sensor is only required for Phases 1 and 2.**
Phase 3 uses a tracking generator as a stable RF source and measures only relative V_ADC
drift versus temperature — absolute power calibration is not needed.

### 5.2 Calibration Frequency Grid

Log-spaced calibration points cover the full operating range.

| Index | Frequency | Band |
| --- | --- | --- |
| 0 | 100 MHz | VHF |
| 1 | 200 MHz | VHF |
| 2 | 400 MHz | UHF |
| 3 | 700 MHz | UHF |
| 4 | 900 MHz | L-band |
| 5 | 1200 MHz | L-band |
| 6 | 1575 MHz | GPS L1 |
| 7 | 1800 MHz | S-band |
| 8 | 2100 MHz | S-band |
| 9 | 2400 MHz | ISM 2.4 GHz |
| 10 | 2700 MHz | S-band |
| 11 | 3000 MHz | S-band |
| 12 | 3600 MHz | C-band |
| 13 | 4000 MHz | C-band |
| 14 | 4500 MHz | C-band |
| 15 | 5000 MHz | C-band |
| 16 | 5400 MHz | ISM 5 GHz |
| 17 | 5800 MHz | ISM 5.8 GHz |
| 18 | 6000 MHz | C-band upper |

**Total: 19 calibration points.** Power sweep per frequency: 28 steps, −54 to 0 dBm, 2 dB.

### 5.3 Firmware Calibration Data Structures

```c
/* calibration_table.h — generated by cal/calibration.py */
#define CAL_FREQ_COUNT  19
#define CAL_ATT_DB       0.0f    /* no external attenuator */

typedef struct {
    uint32_t freq_hz;       /* calibration frequency in Hz */
    float    v_intercept;   /* VOUT at P_in = 0 dBm, at 25°C (volts) */
    float    slope_mv_db;   /* slope magnitude (mV/dB), typ 22.0 */
    float    temp_coeff;    /* reserved — use TEMP_COEFF_DB_PER_C[] instead */
} FreqCalPoint;

static const FreqCalPoint CAL_TABLE[CAL_FREQ_COUNT] = { ... };

/* temp_cal_table.h — generated by cal/temp_calibration.py */
#define TEMP_CAL_T_REF_C  25.0f

/* k[i] = dBm error per °C at freq i.  Apply: P_corr = P_raw - k*(T-25) */
static const float TEMP_COEFF_DB_PER_C[CAL_FREQ_COUNT] = { ... };
```

### 5.4 Runtime Power Calculation

```c
float measure_power_dbm(uint32_t freq_hz, float ntc_temp_c)
{
    /* 1. Average 64 DMA samples → voltage */
    float v_adc = adc_average_64() * (3.00f / 65535.0f);

    /* 2. Interpolate cal table in log(freq) */
    uint8_t idx;
    float frac = find_cal_bracket(freq_hz, &idx);
    float v_int = lerp(CAL_TABLE[idx].v_intercept,
                       CAL_TABLE[idx+1].v_intercept, frac);
    float slope = lerp(CAL_TABLE[idx].slope_mv_db,
                       CAL_TABLE[idx+1].slope_mv_db, frac);
    float k     = lerp(TEMP_COEFF_DB_PER_C[idx],
                       TEMP_COEFF_DB_PER_C[idx+1], frac);

    /* 3. Voltage → dBm */
    float p_raw  = (v_adc - v_int) / (-slope * 1e-3f);

    /* 4. Temperature correction */
    return p_raw - k * (ntc_temp_c - TEMP_CAL_T_REF_C);
}
```

### 5.5 Calibration Procedure Summary

Full step-by-step procedure is in `doc/Calibration_Procedure.md`.

#### Phase 1 — Generator characterisation (Generator → NRP sensor)

```bash
python cal/calibration.py --phase1-only
```

Records true generator output at every (freq, power) point to 0.01 dBm resolution.

#### Phase 2 — DUT linearity sweep (Generator → DUT, direct, no pad)

```bash
python cal/calibration.py --load-phase1 cal/results/phase1_raw.json
```

Sweeps 28 power steps (−54 to 0 dBm, 2 dB) at 19 frequencies.
Fits V_ADC = m×P + b per frequency; computes R², max INL, RMS INL.
Writes `calibration_table.h`, 6 figures, PDF report.

#### Phase 3 — Temperature drift (Vötsch chamber + tracking generator)

```bash
python cal/temp_calibration.py --chamber-ip 192.168.1.50 --soak 15
```

Sweeps 16, 20, 25, 30, 35, 40°C — 6 setpoints, 15 min soak each.
Uses `HW_Test/Phase1.2/Tools/VotschTechnikClimateChamber.py` for chamber control
(TCP port 2049, Vötsch SIMSERV protocol). Loads Phase 2 slope table; measures only
relative V_ADC drift. Fits linear k (dBm/°C) per frequency.
Writes `temp_cal_table.h`, 3 figures, PDF report.

---

## 6. SCPI Interface

The SCPI command `SENS:FREQ <val>` selects the active calibration index; firmware
interpolates the calibration table for frequencies between grid points.

| Command | Description |
| --- | --- |
| `SENS:FREQ <Hz>` | Set measurement frequency |
| `SENS:FREQ?` | Query current frequency |
| `READ?` | Trigger single measurement, return dBm |
| `MEAS:POW:RF?` | Alias for `READ?` |
| `CAL:FREQ <Hz>` | Enter calibration mode for frequency point |
| `CAL:SAVE` | Commit calibration table to flash |
| `CAL:DATE?` | Query calibration timestamp |
| `SENS:TEMP?` | Query NTC temperature reading (°C) |
| `SYST:RANGE?` | Query current measurement range (dBm min, max) |
| `*IDN?` | Identification |
| `*RST` | Reset to defaults |

---

## 7. Measurement Uncertainty Budget

### 7.1 At 1 GHz, 25°C, After Full Calibration (Phases 1–3)

| Source | Uncertainty (±) | Type | Notes |
| --- | --- | --- | --- |
| AD8317 log conformance residual | ±0.10 dB | Systematic | After 28-point INL characterisation |
| NRP sensor accuracy (Phase 1 ref) | ±0.05 dB | Systematic | NRP-Z21 spec |
| ADC quantization (16-bit, 64 avg) | <0.001 dB | Random | Negligible |
| AD8317 noise (1 kHz BW) | <0.001 dB | Random | Negligible |
| **RSS Total** | **±0.11 dB** | — | Root-sum-of-squares |

### 7.2 At 5.8 GHz, 25°C, After Full Calibration

| Source | Uncertainty (±) | Notes |
| --- | --- | --- |
| AD8317 log conformance residual | ±0.15 dB | Conformance degrades at high frequency |
| PCB input trace mismatch | ±0.10 dB | From input return loss ~10 dB at 5.8 GHz |
| NRP sensor accuracy (Phase 1 ref) | ±0.07 dB | NRP-Z21 spec at 5.8 GHz |
| **RSS Total** | **±0.19 dB** | |

### 7.3 Temperature Contribution (16–40°C After Phase 3 Correction)

| Source | Contribution |
| --- | --- |
| Phase 3 k correction applied | residual ≈ ±k × ΔT_NTC_error |
| NTC thermal gradient error (±3°C) | ±0.025–0.06 dB (steady-state) |
| Vref (REF3030, 50 ppm, 24°C span) | ±0.07 dB |
| **Total temperature contribution** | **±0.10–0.13 dB** |

### 7.4 Instrument Accuracy Summary

| Condition | Combined Uncertainty |
| --- | --- |
| 100 MHz – 2 GHz, 25°C | **±0.15–0.25 dB** (after Phases 1–3) |
| 2 GHz – 6 GHz, 25°C | **±0.20–0.35 dB** (after Phases 1–3) |
| 16–40°C, all freqs | add **±0.10–0.13 dB** temperature contribution |
| Uncalibrated | **±1.5–4 dB** (intercept shift + conformance) |

---

## 8. Hardware Summary for Lab Optimization

| Item | Value / Part |
| --- | --- |
| Input connection | Direct SMA — no external attenuator pad |
| RTADJ | 8 kΩ fixed (optimized 1.8–3.6 GHz) |
| Coupling caps C1, C2 | 47 nF 0402 C0G |
| Input shunt R1 | 52.3 Ω 0402 ±0.1% |
| RF trace SMA → INHI | 50 Ω microstrip, <8 mm, 3.0 mm wide on 1.6 mm FR-4 |
| NTC thermistor | Murata NCP15WF104, 0402, 1 mm from AD8317 |
| Ground via stitching | Every 3 mm along RF trace |

---

## 9. Firmware Checklist

- [ ] Load `CAL_TABLE[]` and `TEMP_COEFF_DB_PER_C[]` from flash on boot
- [ ] Implement log-frequency interpolation in `measure_power_dbm()`
- [ ] Implement NTC ADC read + Steinhart-Hart or B-parameter T conversion
- [ ] Apply temperature correction: `P_corr = P_raw − k × (T_ntc − 25)`
- [ ] Implement `SENS:FREQ` parser updating the active calibration index
- [ ] Implement `CAL:FREQ`, `CAL:SAVE` SCPI commands
- [ ] Implement `SENS:TEMP?` and `SYST:RANGE?` SCPI responses
- [ ] Display frequency on OLED
- [ ] Add frequency selection via MENU/NEXT buttons with log-step increments
- [ ] Validate calibration table CRC on boot, revert to factory defaults if corrupt
- [ ] Add out-of-range warning if V_ADC outside [0.30 V, 1.75 V]

---

## 10. References

- `doc/Calibration_Procedure.md` — step-by-step calibration instructions (all 3 phases)
- `doc/ADC_Signal_Path_Analysis.md` — noise and quantization analysis
- `HW_Test/Phase1.2/Tools/VotschTechnikClimateChamber.py` — Vötsch chamber driver
- AD8317 Datasheet Rev. D — Analog Devices (Table 1, Table 4, Figures 22–23)
- STM32F373 Reference Manual RM0313 — STMicroelectronics (SDADC, DMA)
- REF3030AIDBZR Datasheet — Texas Instruments

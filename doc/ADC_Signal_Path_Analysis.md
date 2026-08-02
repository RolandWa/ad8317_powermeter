# Signal Path Analysis: 16-bit vs 24-bit ADC for AD8317 RF Power Meter

**Project:** Handheld Micro-RF Power Meter (ad8317_powermeter)  
**Date:** 2026-07-24  
**Author:** Author  

---

## 1. System Overview

The RF power meter measures input power via the AD8317 log-amp detector, which converts RF power (dBm) to a DC voltage (VOUT). This DC voltage is then digitized by the MCU's on-chip ADC and converted back to dBm using a calibrated slope/intercept formula.

```text
RF Input (dBm)
    │
    ▼
[AD8317 Log Amp]  ──── VOUT ────►  [RC Filter]  ────►  [ADC]  ────►  [dBm readout]
                                   100 Ω / 10 nF        16-bit or
                                   fc = 159 kHz         24-bit SDADC
```

**MCU options compared:**

| MCU | ADC | Package |
|---|---|---|
| STM32F373RCT6 (current) | 16-bit SDADC (SDADC1), ENOB ≈ 15 bits | LQFP-64 |
| Renesas R7FA2A1AB3CNF (candidate) | 24-bit SDADC (SDADC24), ENOB ≈ 20 bits | HWQFN-40, 6×6 mm |

---

## 2. AD8317 Detector — Key Specifications

Source: AD8317 Datasheet Rev. D (Analog Devices)

| Parameter | Value | Condition |
|---|---|---|
| Dynamic range | 0 dBm to −55 dBm = **55 dB** | 900 MHz – 8 GHz |
| VOUT span | **0.35 V → 1.70 V = 1.35 V** | Full 55 dB range |
| Slope | **−22 mV/dB** (typ) | 900 MHz – 5.8 GHz |
| Slope min/max | −25 / −19.5 mV/dB | Process + temp variation |
| Output noise density | **90 nV/√Hz** | RFIN = −10 dBm, 2.2 GHz, f_noise = 100 kHz |
| Log conformance error | **±1.0 dB** | 50 dB range, 25°C |
| Log conformance over temp | **±1.0 dB** | −40°C to +85°C, 46–48 dB range |
| Temperature stability | **±0.5 dB** | Full temp range |
| Output series resistance | **10 Ω** | Internal buffer |
| Output pull-down | 1.6 kΩ to GND | Internal |

### Voltage-to-Power Conversion Formula (firmware)

```text
V_calc = (Σ 64 DMA samples / 64) × (3.00 V / 65535)
Power (dBm) = (V_calc − V_Intercept_freq) / Slope_freq + System_Offset_dB
```

**Slope used in analysis:** 22 mV/dB (magnitude, positive convention)

---

## 3. Signal Chain — Stage by Stage

### 3.1 Stage 1: AD8317 Output Noise

The VOUT noise spectral density is 90 nV/√Hz. The total noise reaching the ADC depends on the effective measurement bandwidth (BW), set by the SDADC decimation ratio and software averaging.

```text
σ_AD8317 (µV RMS) = 90 nV/√Hz × √(BW_Hz)
σ_AD8317 (mdB RMS) = σ_AD8317 (µV) / 22,000 (µV/dB) × 1000
```

| Measurement BW | σ AD8317 (µV RMS) | σ AD8317 (mdB RMS) |
|---|---|---|
| 1 Hz | 0.090 | 4.1 µdB |
| 10 Hz | 0.285 | 13 µdB |
| 100 Hz | 0.900 | 41 µdB |
| **1 kHz** | **2.85** | **0.13 mdB** |
| 10 kHz | 9.00 | 0.41 mdB |
| 159 kHz (RC cutoff) | 35.9 | 1.63 mdB |

### 3.2 Stage 2: RC Low-Pass Filter

**Components:** 100 Ω series + 10 nF to GND (C0G)  
**Cutoff frequency:** `fc = 1 / (2π × 100 Ω × 10 nF)` = **159 kHz**

This filter primarily suppresses RF rectification artifacts. It does not significantly limit the noise bandwidth for measurement rates below ~10 kHz. The effective noise BW is governed by the ADC decimation and software averaging, not by this filter.

### 3.3 Stage 3: Voltage Reference

**REF3030AIDBZR:** 3.00 V, 50 ppm/°C — feeds VREFSD+ on the STM32F373.  
At 3.00 V reference:
- A 1 mV reference error → 1 mV / 22 mV/dB = **0.045 dB error** (systematic, calibrates out)
- Temperature drift at 50 ppm: 3.00 V × 50e-6 × 85°C = **12.75 mV drift → 0.58 dB drift** (uncalibrated over full temp range)

---

## 4. ADC Quantization Analysis

### 4.1 Resolution in Voltage and dBm per LSB

| ADC | Full Scale | Bits | LSB (µV) | mdB per LSB |
|---|---|---|---|---|
| 16-bit SDADC (STM32F373) | 3.00 V | 16 | **45.8 µV** | **2.08 mdB** |
| 24-bit SDADC (RA2A1, PGA=1) | 3.00 V | 24 | **0.179 µV** | **0.008 mdB** |
| 24-bit SDADC (RA2A1, PGA=2 diff) | 1.50 V | 24 | **0.089 µV** | **0.004 mdB** |

> Note: With PGA=2 on the RA2A1 SDADC24, the 1.35 V AD8317 span fits within the 1.50 V differential input range, gaining one additional effective bit.  
> The PGA is only available in **differential input mode**. Single-ended input forces PGA=1.

### 4.2 Quantization Noise RMS

```text
σ_quant (RMS) = LSB / √12
```

| ADC | ENOB (realistic) | σ_quant (µV RMS) | σ_quant (mdB RMS) |
|---|---|---|---|
| 16-bit SDADC (STM32F373) | 15 bits | **26.4 µV** | **1.20 mdB** |
| 16-bit SDADC + 64-sample avg | 15 bits | **3.30 µV** | **0.15 mdB** |
| 24-bit SDADC (RA2A1, PGA=1) | 20 bits | **0.83 µV** | **0.038 mdB** |
| 24-bit SDADC (RA2A1, PGA=2) | 20 bits | **0.41 µV** | **0.019 mdB** |

Software averaging reduces noise by `√N`: 64 samples → ÷8 reduction.  
The RA2A1 SDADC24 internal oversampling already provides the decimation; additional software averaging applies the same benefit.

---

## 5. Combined System Noise (ADC + AD8317)

Total noise is root-sum-of-squares (uncorrelated sources):

```text
σ_total = √(σ_ADC² + σ_AD8317²)
```

### At 1 kHz effective measurement bandwidth (σ_AD8317 = 2.85 µV)

| ADC | σ_ADC (µV) | σ_AD8317 (µV) | σ_total (µV) | σ_total (mdB) | ADC contribution |
|---|---|---|---|---|---|
| 16-bit + 64 avg | 1.65 | 2.85 | **3.30** | **0.150 mdB** | 16% (limiting) |
| 24-bit PGA=1 | 0.83 | 2.85 | **2.97** | **0.135 mdB** | <1% (transparent) |
| 24-bit PGA=2 (diff) | 0.41 | 2.85 | **2.88** | **0.131 mdB** | <0.1% (invisible) |

### At 100 Hz effective measurement bandwidth (σ_AD8317 = 0.90 µV)

| ADC | σ_ADC (µV) | σ_AD8317 (µV) | σ_total (µV) | σ_total (mdB) | ADC contribution |
|---|---|---|---|---|---|
| 16-bit + 64 avg | 1.65 | 0.90 | **1.88** | **0.085 mdB** | 77% (**ADC dominates**) |
| 24-bit PGA=1 | 0.83 | 0.90 | **1.22** | **0.056 mdB** | 46% (significant) |
| 24-bit PGA=2 (diff) | 0.41 | 0.90 | **0.99** | **0.045 mdB** | 17% (minor) |

### At 10 kHz effective measurement bandwidth (σ_AD8317 = 9.00 µV)

| ADC | σ_ADC (µV) | σ_AD8317 (µV) | σ_total (µV) | σ_total (mdB) | ADC contribution |
|---|---|---|---|---|---|
| 16-bit + 64 avg | 1.65 | 9.00 | **9.15** | **0.416 mdB** | <4% (negligible) |
| 24-bit PGA=1 | 0.83 | 9.00 | **9.04** | **0.411 mdB** | <1% (negligible) |

> At 10 kHz BW the AD8317 noise dominates completely — ADC choice is irrelevant.

---

## 6. The Dominant Accuracy Limit: Log Conformance Error

The AD8317 log conformance error sets the fundamental measurement accuracy ceiling:

| Error Source | Magnitude | Calibratable? |
|---|---|---|
| AD8317 log conformance | **±1.0 dB** (50 dB range, 25°C) | Partially — multi-point cal → ~±0.2 dB residual |
| AD8317 temp drift | **±0.5 dB** (−40°C to +85°C) | Partially — TADJ resistor reduces significantly |
| Vref temp drift (REF3030, 50 ppm) | **~0.6 dB** (full temp range) | Partially — ratiometric cal at each temp |
| AD8317 noise at 1 kHz BW | **0.13 mdB RMS** | No (random) |
| 16-bit ADC noise (64 avg, 1 kHz) | **0.15 mdB RMS** | No (random) |
| 24-bit ADC noise (PGA=1, 1 kHz) | **0.038 mdB RMS** | No (random) |

**The ±1 dB conformance error is ~6,700× larger than the 16-bit ADC quantization noise and ~26,000× larger than the 24-bit ADC noise.**

Both ADCs can resolve the conformance error curve with far more than sufficient precision. After factory calibration with known power sources, the residual errors are dominated by temperature drift, not ADC resolution.

---

## 7. MCU Candidate Comparison

### Renesas RA2A1 (R7FA2A1AB3CNF)

| Feature | Specification |
|---|---|
| Package | HWQFN-40, **6×6 mm** |
| Core | ARM Cortex-M23, 48 MHz |
| 24-bit SDADC (SDADC24) | 4 differential channels, oversampling, PGA |
| 16-bit SAR ADC (ADC16) | Up to 17 channels, no PGA |
| PGA (SDADC24 only) | Stage 1: ×1/2/3/4/8 — Stage 2: ×1/2/4/8 — Max: **×64** |
| PGA restriction | Differential input only — single-ended forces PGA=1 |
| USB | Full-Speed USB 2.0 device |
| Debug | SWD/JTAG (ARM CoreSight) |
| J-Link support | Yes — explicitly listed in SEGGER device DB (R7FA2A1AB) |
| CMSIS-DAP support | Yes — standard ARM SWD port; any CMSIS-DAP probe works |
| Price (DigiKey, 1u) | ~$3.71 |
| Ecosystem | Renesas FSP (Flexible Software Package), e² studio |

### STM32F373RCT6 (current design)

| Feature | Specification |
|---|---|
| Package | LQFP-64, 10×10 mm |
| Core | ARM Cortex-M4F, 72 MHz |
| 16-bit SDADC | 3× independent, 21 SE / 11 diff channels, PGA ×1–128 |
| USB | Full-Speed USB 2.0 device |
| Debug | SWD/JTAG |
| Price (DigiKey, 1u) | ~$6.79 |
| Ecosystem | STM32CubeIDE, HAL, well-documented |

---

## 8. Results and Conclusions

### 8.1 ADC Resolution — Does 24-bit help?

**Yes, but only at slow update rates (<1 kHz).**

- At 1 kHz BW: 16-bit adds 16% noise on top of the AD8317 floor; 24-bit adds <1%.
- At 100 Hz BW: 16-bit **dominates** (77% of total noise); 24-bit still contributes 46%.
- At 10 kHz BW: both ADCs are irrelevant — AD8317 noise swamps both.

The 24-bit ADC is "transparent" to the detector noise at all practical update rates. The 16-bit ADC starts to limit at slow averaging speeds where high precision is most wanted.

### 8.2 Practical Power Resolution (post-calibration, 1 kHz BW)

| System | Noise floor (RMS) | 3σ noise (99.7%) | Dominant limit |
|---|---|---|---|
| 16-bit + 64-avg | 0.15 mdB | 0.45 mdB | ADC + AD8317 roughly equal |
| 24-bit PGA=1 | 0.135 mdB | 0.41 mdB | AD8317 noise |
| 24-bit PGA=2 (diff) | 0.131 mdB | 0.39 mdB | AD8317 noise |

All configurations achieve sub-0.5 mdB RMS readout noise — far better than the ±1 dB conformance error.

### 8.3 Recommendation

| Criterion | STM32F373 (16-bit) | RA2A1 (24-bit) |
|---|---|---|
| Board area | LQFP-64, 10×10 mm | **HWQFN-40, 6×6 mm** ✓ |
| ADC noise (1 kHz BW) | 0.15 mdB (ADC limits) | **0.135 mdB (AD8317 limits)** ✓ |
| ADC noise (100 Hz BW) | 0.085 mdB (ADC dominates) | **0.045–0.056 mdB** ✓ |
| Slow-averaging benefit | Limited by ADC floor | **Full benefit from averaging** ✓ |
| Log conformance ±1 dB | Both ADCs resolve it identically | = |
| J-Link / CMSIS-DAP | Yes | **Yes** ✓ |
| Firmware effort | Current design — done | Full port to Renesas FSP |
| Cost | $6.79 | **$3.71** ✓ |

**The RA2A1 24-bit SDADC gives a measurable advantage at measurement rates below 1 kHz, is smaller, and is cheaper. The practical measurement accuracy ceiling remains the AD8317 ±1 dB log conformance error, which calibration reduces to ~±0.2 dB — entirely independent of ADC resolution.**

---

## 9. Temperature Compensation — NTC Thermistor Error Budget

### 9.1 Why Temperature Compensation is Needed

The AD8317's primary temperature-dependent error is **intercept drift** — the slope is stable, but the output voltage at a fixed input power shifts with temperature. After calibration at 25°C, the residual error over the full −40°C to +85°C range reaches ±1.0 dB without compensation.

```text
Power (dBm) = (V_out − V_intercept) / Slope

Slope:     very stable with temperature  ← no correction needed
Intercept: primary source of temp error  ← shifts with T and frequency
```

Average drift rate derived from datasheet:

```text
±1.0 dB over 125°C  →  8 mdB/°C average
Worst case (high frequency, nonlinear region):  ~20 mdB/°C
```

Every degree of temperature measurement error → that many mdB of residual dBm error after compensation.

---

### 9.2 Two Compensation Approaches

#### Hardware: TADJ Pin (on-chip)

The AD8317 TADJ pin accepts a resistor to GND that creates a temperature-dependent bias current correcting the intercept in the analog domain. Values are frequency-specific:

| Frequency | R_TADJ |
|---|---|
| 50–900 MHz | 18 kΩ |
| 1.8–3.6 GHz | 8 kΩ |
| 5.3–5.8 GHz | 500 Ω |
| 8 GHz | Open |

**Limitation:** one fixed resistor optimises only one frequency band.

#### Software: NTC Thermistor + Correction Table

```c
dBm_corrected = dBm_raw + intercept_correction(T_degC, freq_band);

// Linear fit per band stored in flash:
float intercept_correction(float T, uint8_t band) {
    return slope_coeff[band] * (T - 25.0f) + offset_coeff[band];
}
```

Fits naturally into the existing firmware which already stores `V_Intercept_freq` and `Slope_freq` per frequency band — extend to `V_Intercept[freq][temp_index]`.

---

### 9.3 NTC Component — Reference Part

**Murata NCP15WF104E03RC** (or equivalent):

| Parameter | Value |
|---|---|
| Package | 0402 |
| Resistance at 25°C | 10 kΩ ±1% |
| B-constant (25/85°C) | 4050 K ±1% |
| Operating range | −40°C to +125°C |
| Dissipation factor (on PCB) | ~1.5 mW/°C |

Divider circuit: 10 kΩ fixed resistor from 3.3 V → NTC → GND, midpoint to ADC.

---

### 9.4 Error Source Analysis

#### Error Source 1 — NTC Resistance and B-Constant Tolerance

```text
dR/dT at 25°C (298 K) = −R × B / T²
                       = −10,000 × 4050 / 298²
                       = −456 Ω/°C

Temperature error (±1% R tolerance):
  ΔT = (1% × 10,000 Ω) / 456 Ω/°C = ±0.22°C

B-constant error (±1%, away from 25°C): ±0.3–0.5°C additional
```

**NTC tolerance subtotal: ±0.5–0.7°C**  
Reducible to ±0.05°C with ±0.1% grade NTC or individual calibration.

#### Error Source 2 — NTC Self-Heating

```text
Divider current: I = 3.3 V / (10 kΩ + 10 kΩ) = 165 µA
Power in NTC at 25°C: P = (165 µA)² × 10 kΩ = 0.27 mW
Self-heating = 0.27 mW / 1.5 mW/°C = +0.18°C  (systematic)
```

Reducible to <0.02°C by using a 100 kΩ divider or pulsed ADC excitation.

#### Error Source 3 — Thermal Gradient: NTC Pad vs AD8317 Die (dominant)

The NTC sits on the PCB surface; the AD8317 die is inside its package. These two temperatures are **never equal**.

**AD8317 self-heating:**

```text
Supply current (typ): 22 mA at 3.3 V
Power dissipated:     22 mA × 3.3 V = 72.6 mW
θ_JC (LFCSP-8, exposed pad): ~30–50 °C/W
Die rise above PCB:   72.6 mW × 40 °C/W ≈ +2.9°C
```

**NTC-to-die gradient vs placement distance:**

| NTC distance from AD8317 | Steady-state gradient |
|---|---|
| On AD8317 pad ring (0 mm) | ~1–2°C |
| 1 mm away on F.Cu | ~2–4°C |
| 2–3 mm away | ~4–6°C |

**Transient gradient** (device moved between environments — e.g. cold car → warm room):  
PCB and enclosure thermal time constant = several minutes. NTC and AD8317 can diverge by **5–10°C** during the settling period.

**Thermal gradient error: ±3°C steady-state, ±8°C transient**

#### Error Source 4 — Calibration Curve Fitting Residual

| Fit type | Residual |
|---|---|
| Linear (2-point) | ±0.2°C |
| Quadratic (3–5 points) | ±0.05°C |

#### Error Source 5 — ADC Resolution

With a 16-bit ADC and 10 kΩ divider the temperature resolution is <0.01°C — **negligible**.

---

### 9.5 Full Error Budget

| Source | Temperature Error | Reducible to |
|---|---|---|
| NTC resistance tolerance (±1%) | ±0.22°C | ±0.02°C (±0.1% grade) |
| NTC B-constant tolerance (±1%) | ±0.40°C | ±0.05°C (individual cal) |
| Self-heating (10 kΩ divider) | +0.18°C | +0.02°C (100 kΩ divider) |
| **Thermal gradient — steady-state** | **±3.0°C** | ~±1.5°C (optimal placement) |
| **Thermal gradient — transient** | **±8.0°C** | ±3–4°C (aluminium enclosure) |
| Calibration curve residual | ±0.20°C | ±0.05°C (quadratic fit) |
| ADC resolution | <0.01°C | — |
| **RSS total (steady-state)** | **±3.1°C** | — |
| **Worst-case (transient)** | **±9.0°C** | — |

---

### 9.6 Resulting dBm Error After NTC Compensation

`dBm_error = T_error (°C) × AD8317 drift rate (mdB/°C)`

| Scenario | T error | Drift rate | Residual dBm error |
|---|---|---|---|
| Steady-state, typical freq (8 mdB/°C) | ±3.1°C | 8 mdB/°C | **±25 mdB = ±0.025 dB** |
| Steady-state, high freq (20 mdB/°C) | ±3.1°C | 20 mdB/°C | **±62 mdB = ±0.062 dB** |
| Transient, typical freq | ±8°C | 8 mdB/°C | **±64 mdB = ±0.064 dB** |
| Transient, high freq | ±8°C | 20 mdB/°C | **±160 mdB = ±0.16 dB** |
| **Uncompensated (baseline)** | — | — | **±1.0 dB** |

---

### 9.7 Combined Strategy: TADJ + NTC Software Correction

Using both methods together:

1. **TADJ = 8 kΩ** — hardware correction, eliminates ~60% of drift in the 1–4 GHz band
2. **NTC 0402 placed 1 mm from AD8317** — software correction for residual
3. **Quadratic calibration** at −10 / 0 / 25 / 50 / 70°C per frequency band

| Strategy | Steady-state accuracy | Transient accuracy |
|---|---|---|
| No compensation | ±1.0 dB | ±1.0 dB |
| TADJ only (hardware) | ±0.4 dB (one band) | ±0.4 dB |
| NTC software only | ±0.03–0.06 dB | ±0.10–0.20 dB |
| **TADJ + NTC software** | **±0.02–0.04 dB** | **±0.05–0.10 dB** |

**The thermal gradient between NTC and AD8317 die is the irreducible physical limit** — it cannot be improved by a better ADC or tighter NTC tolerance. Optimal PCB placement (NTC pad touching or sharing a via with the AD8317 thermal pad) is the single most effective improvement.

---

## 10. References

- AD8317 Datasheet Rev. D — Analog Devices (analog.com)
- RA2A1 Group Datasheet R01DS0354EJ — Renesas Electronics
- RA2A1 Application Note: "24-Bit Sigma-Delta A/D Converter Performance" — Renesas
- REF3030AIDBZR Datasheet — Texas Instruments
- SEGGER Supported Devices: Renesas RA2A1 (segger.com)
- STM32F373 Reference Manual RM0313 — STMicroelectronics

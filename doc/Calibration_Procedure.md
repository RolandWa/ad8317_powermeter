# AD8317 Power Meter — Calibration Procedure

**Document:** Calibration Procedure and Instruction Manual
**Project:** Handheld Micro-RF Power Meter (ad8317_powermeter)
**Date:** 2026-07-24
**Author:** Author
**Scripts:** `cal/calibration.py` (RF linearity, Phases 1–2), `cal/temp_calibration.py` (Phase 3)

---

## 1. Purpose

This procedure calibrates the AD8317 power meter against a traceable reference
(R&S NRP USB power sensor) across the full operating band of 100 MHz to 6 GHz.
After calibration, the firmware stores a per-frequency slope and intercept table
that corrects the AD8317's frequency-dependent output variation.

**Achievable accuracy after calibration:** ±0.3–0.6 dB (100 MHz – 6 GHz, 25°C)

---

## 2. Required Equipment

### Phases 1 and 2 — RF linearity calibration

| Item | Example | Purpose |
| --- | --- | --- |
| R&S Signal Generator | SMB100A, SMF100A, SMBV100B | Swept RF source, 100 MHz – 6 GHz |
| R&S NRP USB Power Sensor | NRP-Z21, NRP18S, NRP33S | Calibrated reference (0.01 dBm resolution) |
| SMA cables (2×) | Phase-stable, 0–6 GHz rated | Signal routing |
| PC running Windows 10/11 | — | Calibration script host |
| USB cable | Type-A to Type-B or Type-C | DUT USB connection |
| R&S VISA library | R&S VISA or NI-VISA | VISA instrument control |

### Phase 3 — Temperature calibration

| Item | Example | Purpose |
| --- | --- | --- |
| Vötsch climatic chamber | VC / VT series with LAN port | Temperature sweep 16–40°C |
| Tracking generator | R&S ZVH, Keysight FieldFox, Siglent SSA/SVA | Fixed-level RF source at each frequency |
| SMA cable | Phase-stable, 0–6 GHz rated | Generator → DUT |
| DUT USB cable | — | DUT USBTMC connection |

> The NRP power sensor is **not needed** in Phase 3 — only temperature drift relative
> to 25°C is measured, so absolute power calibration is not required.

**Software prerequisites:**

```text
pip install pyvisa pyvisa-py numpy matplotlib
```

Optional for PDF generation:

```text
winget install pandoc
```

---

## 3. Physical Setup

### Phase 1 — Reference Sweep (Generator → NRP sensor)

```text
┌─────────────────┐    SMA cable    ┌──────────────────┐
│   R&S Signal    │─────────────────►  R&S NRP USB     │
│   Generator     │                 │  Power Sensor    │
│  RF OUT (50 Ω)  │                 │                  │
└─────────────────┘                 └────────┬─────────┘
                                             │ USB
                                    ┌────────▼─────────┐
                                    │   PC / Laptop    │
                                    │ calibration.py   │
                                    └──────────────────┘
```

> No attenuator. The NRP sensor measures generator output directly.

### Phase 2 — DUT Sweep (Generator → DUT, direct)

```text
┌─────────────────┐         SMA cable         ┌─────────────────┐
│   R&S Signal    │───────────────────────────►  AD8317 Power   │
│   Generator     │    (no attenuator pad)     │  Meter (DUT)    │
│  RF OUT (50 Ω)  │                            │  SMA Input      │
└─────────────────┘                            └────────┬────────┘
                                                        │ USB (USBTMC)
                                               ┌────────▼─────────┐
                                               │   PC / Laptop    │
                                               │ calibration.py   │
                                               └──────────────────┘
```

> The NRP sensor is disconnected in Phase 2. The Phase 1 generator correction
> table provides the true power at the DUT SMA input (0.01 dBm resolution).

---

## 4. Instrument Connection (VISA)

### 4.1 Identify VISA Addresses

Open NI-MAX, R&S VISA Interactive, or run:

```python
import pyvisa
rm = pyvisa.ResourceManager()
print(rm.list_resources())
```

Typical addresses:

| Instrument | Typical VISA address |
| --- | --- |
| R&S SMB100A via GPIB | `GPIB0::28::INSTR` |
| R&S SMB100A via USB | `USB0::0x0AAD::0x006E::xxxxxx::INSTR` |
| R&S SMB100A via LAN | `TCPIP0::192.168.1.100::inst0::INSTR` |
| R&S NRP-Z21 via USB | `USB0::0x0AAD::0x0095::xxxxxx::INSTR` |
| R&S NRP18S via USB | `USB0::0x0AAD::0x01BB::xxxxxx::INSTR` |
| DUT (Keysight emulation) | `USB0::0x2A8D::0x0100::xxxxxx::INSTR` |

### 4.2 Verify Connections

```python
import pyvisa
rm   = pyvisa.ResourceManager()
inst = rm.open_resource("GPIB0::28::INSTR")
print(inst.query("*IDN?"))
```

Expected generator response: `Rohde&Schwarz,SMB100A,...`

---

## 5. Calibration Script Usage

### 5.1 Quick Start (auto-discovery)

```bash
cd ad8317_powermeter
python cal/calibration.py
```

The script auto-discovers the generator, NRP sensor, and DUT from the VISA
resource list. It prompts for cable reconnection between phases.

### 5.2 Manual VISA address override

```bash
python cal/calibration.py \
  --gen    "GPIB0::28::INSTR" \
  --sensor "USB0::0x0AAD::0x0095::123456::INSTR" \
  --dut    "USB0::0x2A8D::0x0100::ABCDEF::INSTR"
```

### 5.3 Dry-run (no hardware — test and report only)

```bash
python cal/calibration.py --dry-run
```

Generates a complete report with synthetic data. Use this to verify the
installation and report format before connecting instruments.

### 5.4 Skip Phase 1 (re-use a previous reference sweep)

```bash
python cal/calibration.py --load-phase1 cal/results/phase1_raw.json
```

Useful when re-calibrating the same DUT on the same day without moving cables.

---

## 6. Step-by-Step Procedure

### Step 1 — Preparation

1. Allow the signal generator and DUT to **warm up for 30 minutes**.
2. Connect all USB cables (generator, NRP sensor, DUT) to the PC.
3. Connect generator RF OUT → SMA cable → NRP sensor for Phase 1.
4. Launch the script and follow its on-screen prompts.

### Step 2 — Run Phase 1 (Reference Sweep)

```text
[Script output]
PHASE 1 — Generator characterisation (Generator → NRP sensor)
Connect: Generator RF OUT ─── SMA cable ──────────────► NRP sensor input
         (DUT is NOT connected in this phase)
Press ENTER when ready …
```

The script sweeps:

- **19 frequencies:** 100 MHz, 200 MHz, 400 MHz, 700 MHz, 900 MHz, 1200 MHz,
  1575 MHz, 1800 MHz, 2100 MHz, 2400 MHz, 2700 MHz, 3000 MHz, 3600 MHz,
  4000 MHz, 4500 MHz, 5000 MHz, 5400 MHz, 5800 MHz, 6000 MHz
- **28 power steps per frequency:** −54, −52, −50 … −2, 0 dBm (2 dB increments)
- NRP readings reported at **0.01 dBm resolution** (two digits after decimal)

Duration: approximately **8–12 minutes**.

Output: `cal/results/phase1_raw.json`

### Step 3 — Reconnect for Phase 2

When the script prompts:

```text
PHASE 2 — DUT sweep (Generator RF OUT → DUT SMA, no attenuator)
Connect: Generator RF OUT ─── SMA cable ──────────────► DUT SMA input
         (no 30 dB pad — direct connection)
Press ENTER when ready …
```

1. Disconnect the SMA cable from the NRP sensor.
2. Connect: Generator RF OUT → SMA cable → DUT SMA input (direct, no pad).
3. Verify the DUT is powered and the USB cable is connected.
4. Press ENTER.

### Step 4 — Run Phase 2 (DUT Sweep)

The script sweeps the same 532 (19 freq × 28 power) points with the DUT in the
path. The AD8317 operates at power levels of −54 dBm to 0 dBm directly at INHI.

Duration: approximately **12–18 minutes**.

Output: `cal/results/phase2_raw.json`

### Step 5 — Review Results

The script fits slope and intercept per frequency and prints linearity metrics:

```text
   Freq     Slope        V_int     R²       MaxINL      RMS_INL
   100 MHz  21.853 mV/dB  0.32941 V  0.99998  +0.0031 dB  0.0018 dB
   200 MHz  22.012 mV/dB  0.30762 V  0.99999  +0.0024 dB  0.0014 dB
   ...
  6000 MHz  21.719 mV/dB  0.33012 V  0.99996  +0.0087 dB  0.0052 dB

  PASS  all 19 points within 0.1 dB INL target
```

If any point shows `MaxINL > 0.1 dB`:

- Verify the SMA connections are tight and properly torqued (finger-tight + ¼ turn).
- Repeat Phase 2 at that frequency band.
- If persistent, check for impedance mismatch or AD8317 non-linearity at that band.

---

## 7. Output Files

| File | Description |
| --- | --- |
| `cal/results/phase1_raw.json` | NRP sensor readings at each (freq, power) point |
| `cal/results/phase2_raw.json` | DUT ADC voltage readings at each (freq, power) point |
| `cal/results/calibration_table.json` | Fitted slope, V_intercept and INL per frequency |
| `cal/results/calibration_table.h` | C header for firmware — copy to `firmware/Inc/` |
| `cal/results/figures/fig1_linearity_grid.png` | V_ADC vs P_in scatter + fit, all frequencies |
| `cal/results/figures/fig2_inl_grid.png` | INL bar chart (residuals from fit), all frequencies |
| `cal/results/figures/fig3_slope_vs_freq.png` | Slope vs frequency bar chart |
| `cal/results/figures/fig4_intercept_vs_freq.png` | V_intercept and X-intercept vs frequency |
| `cal/results/figures/fig5_inl_summary.png` | Max and RMS INL summary per frequency |
| `cal/results/figures/fig6_inl_heatmap.png` | INL heat map (freq × power) |
| `cal/results/Calibration_Report.md` | Full calibration report (Markdown) |
| `cal/results/Calibration_Report.pdf` | Full calibration report (PDF) |

---

## 8. Phase 3 — Temperature Calibration (Vötsch Chamber)

Phase 3 characterises how the AD8317 VOUT drifts with temperature. It uses the
slope table produced in Phases 1–2 and needs only a tracking generator — no NRP sensor.

### 8.1 Setup

```text
Vötsch chamber interior:
  Tracking generator RF OUT ─── SMA cable ──────────────► DUT SMA input
  (DUT sits inside chamber or cable passes through the chamber port)
```

### 8.2 Run Phase 3

```bash
python cal/temp_calibration.py --chamber-ip 192.168.1.50 \
  --gen "GPIB0::26::INSTR" --dut "USB0::0x2A8D::0x0100::...::INSTR"

# Manual chamber control (operator sets temperature):
python cal/temp_calibration.py --chamber manual

# Dry-run (no hardware):
python cal/temp_calibration.py --dry-run
```

The script:

1. Connects to the Vötsch chamber via TCP port 2049 (Vötsch SIMSERV protocol).
2. Loads the Phase 1/2 slope table from `cal/results/calibration_table.json`.
3. Steps through temperatures: 16, 20, 25, 30, 35, 40°C — soaking 15 min each.
4. At each temperature: sweeps all 19 calibration frequencies, records V_ADC.
5. Fits a linear temperature coefficient k (dBm/°C) per frequency.
6. Writes `results/temp_cal_table.h` and a PDF report.

### 8.3 Phase 3 Output Files

| File | Description |
| --- | --- |
| `cal/results/temp_raw.json` | Raw V_ADC at every (temperature, frequency) point |
| `cal/results/temp_cal_table.json` | k coefficient per frequency (dBm/°C) |
| `cal/results/temp_cal_table.h` | C header — `TEMP_COEFF_DB_PER_C[]` array |
| `cal/results/figures/fig_t1_drift_grid.png` | ΔP vs temperature, per frequency |
| `cal/results/figures/fig_t2_k_vs_freq.png` | k (mdBm/°C) vs frequency |
| `cal/results/figures/fig_t3_drift_heatmap.png` | (temp × freq) drift heat map |
| `cal/results/Temperature_Calibration_Report.md` | Full report (Markdown) |
| `cal/results/Temperature_Calibration_Report.pdf` | Full report (PDF) |

---

## 9. Firmware Integration

After completing all three phases:

1. Copy `cal/results/calibration_table.h` to `firmware/Inc/calibration_table.h`.
2. Copy `cal/results/temp_cal_table.h` to `firmware/Inc/temp_cal_table.h`.
3. Rebuild the firmware project in STM32CubeIDE (or equivalent).
4. Flash the firmware with the new calibration tables to the DUT.
5. Verify by connecting the DUT to a known −20 dBm signal and checking the display.

The calibration table C structures:

```c
/* calibration_table.h — from calibration.py (Phases 1–2) */
#define CAL_FREQ_COUNT  19
#define CAL_ATT_DB       0.0f    /* no external attenuator */

static const FreqCalPoint CAL_TABLE[CAL_FREQ_COUNT] = {
    { .freq_hz=100000000, .v_intercept=0.329410f, .slope_mv_db=21.8530f, ... },
    /* ... */
};

/* temp_cal_table.h — from temp_calibration.py (Phase 3) */
#define TEMP_CAL_T_REF_C  25.0f
static const float TEMP_COEFF_DB_PER_C[CAL_FREQ_COUNT] = {
    0.008100f,  /* 100 MHz */
    0.009600f,  /* 200 MHz */
    /* ... */
};
```

Runtime correction in firmware:

```c
float p_raw    = (v_adc - CAL_TABLE[idx].v_intercept)
                 / (-CAL_TABLE[idx].slope_mv_db * 1e-3f);
float p_corr   = p_raw
                 - TEMP_COEFF_DB_PER_C[idx] * (ntc_temp_c - TEMP_CAL_T_REF_C);
```

---

## 11. Calibration Interval

| Usage | Recommended interval |
| --- | --- |
| Laboratory reference instrument | Every **6 months** |
| Frequent handling / field use | Every **12 months** |
| After any hardware repair | **Immediately after repair** |
| After firmware update | **Verify at 3 frequencies** — full cal if any error > 0.5 dB |

---

## 12. Troubleshooting

| Symptom | Likely cause | Action |
| --- | --- | --- |
| VISA resource not found | Driver not installed | Install R&S VISA or NI-VISA |
| NRP reads −60 dBm at all points | RF output of generator disabled | Check generator OUTP ON |
| DUT returns no response | USBTMC not enumerated | Check USB cable and DUT power |
| MaxINL > 0.5 dB at one freq | Loose SMA connection | Retighten; repeat that frequency |
| Slope < 18 mV/dB | DUT input overdriven | Reduce maximum power step |
| PDF not generated | pandoc not installed | `winget install pandoc` |
| Chamber not found on LAN | Wrong IP / firewall | Check `--chamber-ip`; use `--chamber manual` |
| k values all near zero | Generator not stable | Let generator warm up 30 min; check cable |
| Low R² in Phase 3 | High generator noise | Increase `--soak` to 20 min; shield cable |
| Chamber script uses mock | Driver not found | Ensure HW_Test repo is checked out next to ad8317 repo |

---

## 13. References

- `doc/ADC_Signal_Path_Analysis.md` — noise and quantization analysis
- `doc/Lab_Optimization_DC_to_6GHz.md` — frequency optimization, calibration framework
- `HW_Test/Phase1.2/Tools/VotschTechnikClimateChamber.py` — Vötsch chamber driver
- AD8317 Datasheet Rev. D — Analog Devices
- R&S NRP-Z21 Operating Manual — Rohde & Schwarz
- R&S SMB100A Operating Manual — Rohde & Schwarz
- Vötsch SIMSERV Manual — lampx.tugraz.at (TCP port 2049 protocol)
- [PyVISA Documentation](https://pyvisa.readthedocs.io)

# RF input targets: what the front end needs to be comparable to a lab power meter (R&S class)

Status: **design guidance, 2026-10-09**. It joins the S11 results of `S11_Analysis_J1_to_AD8317.md` with the
usual way lab power meters are specified. The R&S figures below are *typical orders of magnitude*; they were
**not** taken from a datasheet in this project. Before a requirement is written into the DRD, read the data
sheet of the sensor you compare with (R&S NRP family) and put its numbers in the table of section 2.

## 1. Where the meter stands today (circuit model C1, as-built geometry)

| Item | AD8317 meter now | Comment |
|---|---|---|
| Worst \|S11\|, 1 MHz – 10 GHz | −9 … −10 dB (VSWR ≈ 2); with the Figure-15 chip data −4.6 dB at 4–5 GHz | weak band 3–6 GHz, set by the chip input (300 Ω ∥ 0.33 pF at 3.6 GHz) |
| Share of the band below −10 dB | 92 – 95 % | model; no measurement yet |
| Detector | AD8317 log detector, ≈ ±1 dB, about −55 … 0 dBm | accuracy is a property of the chip, not of the input network |
| Input | SMD SMA (Würth 60312202114307) | limits the quality above a few GHz |

## 2. Targets (to confirm against the R&S data sheet)

| Parameter | Lab power-meter class (typical) | Proposed target for this meter |
|---|---|---|
| Input return loss | ≥ 20 dB (VSWR ≤ 1.2) over the sensor band | **≥ 15 dB over the whole use band, goal 20 dB**, measured with a VNA at J1 |
| Mismatch uncertainty | a few tenths of a percent to a few percent | ≤ ±3 % (≈ 0.13 dB) at the worst frequency |
| Absolute accuracy | tenths of a dB | ±0.5 dB after calibration, ±1 dB un-calibrated (AD8317 limit) |
| Dynamic range | tens of dB wider | −55 … 0 dBm without pad; shifted by the pad if one is added |
| Frequency response | factory correction table per sensor | per-frequency and per-temperature correction table in the firmware (`Calibration_Procedure.md`) |

Mismatch error (first order) = ±2 · |Γs| · |Γl|. For a source with return loss 20 dB (|Γs| = 0.1):

| Meter S11 | \|Γl\| | error | in dB |
|---|---|---|---|
| −10 dB | 0.316 | ±6.3 % | ±0.27 dB |
| −15 dB | 0.178 | ±3.6 % | ±0.15 dB |
| −20 dB | 0.100 | ±2.0 % | ±0.09 dB |
| −26 dB | 0.050 | ±1.0 % | ±0.04 dB |

## 3. How to get there

1. **Fix the chip-side mismatch, not only the traces.** The optimiser (`sim/qucs/optimize_geometry.py`) shows that the
   trace geometry is already within 1.5 – 3 dB of its best: the optimum is flat and sits on the manufacturing limits.
   The levers that matter are the *terminating network* and the *length of the lines*:
   * Put the 50 Ω termination as close to INHI as possible (0201 or thin-film resistor on the signal side of the DC block, as the AD8317 data sheet recommends with 52.3 Ω), and keep the stub between the termination and the pin short.
   * An input pad (π or T, 6 – 10 dB) improves the return loss by about twice its attenuation (6 dB pad: a −9 dB match becomes about −21 dB) at the price of the same loss of sensitivity. The DRD already assumes a 30 dB external attenuator on the high-power range; a pad for the low-power range trades range for match. This is a design decision for the DRD owner.
   * R1 ∥ R2 (now 100 Ω ∥ 100 Ω): the optimiser moves them to 109 Ω (geometry) or 185 Ω (with line lengths freed). Higher values help 3 – 6 GHz and hurt the low frequencies; choose by the requirement above.
2. **Launch and connector.** An SMD SMA is the weakest part above a few GHz. For comparability use a precision edge-launch SMA / 3.5 mm connector, a short 50 Ω (or deliberately ≈ 70 Ω first section, see the optimiser) line with the In1/In2 cut-out under the pad (keep it: a solid In1 made S11 4 – 10 dB worse in the model), stitching vias around the pad, and no stubs.
3. **Resolve the chip data.** The AD8317 data sheet table and the Figure-15 Smith chart disagree at 5.8 GHz (110 Ω ∥ 0.05 pF vs ≈ 210 Ω ∥ 0.5 pF). Until a VNA measurement of the real input exists, design for the worst of both (the optimiser does).
4. **Calibration.** Return loss alone does not make the meter comparable. Calibrate against a reference sensor at every frequency of use and over temperature, store the corrections, and report the measurement uncertainty with the mismatch term above.
5. **Verify with measurements.** VNA at J1 (S11 over 1 MHz – 10 GHz, reference plane at the connector), then power sweep against a reference sensor at 5 – 10 frequencies.

## 4. Checklist for the next hardware revision

- [ ] Write the return-loss target (≥ 15 dB, goal 20 dB) and the uncertainty budget into `Micro_RF_Power_Meter_DRD.md`.
- [ ] Decide on the input pad (none / 3 dB / 6 dB / 10 dB) with the optimiser and the dynamic-range requirement.
- [ ] Re-run `optimize_geometry.py --shunt` with the real R values and layout limits; re-check with the field solver (rfsim with the fixed runner).
- [ ] Order a precision connector option for the lab version.
- [ ] Measure S11 of the assembled board and replace the model data in `ad8317_input_model.py` if it differs.

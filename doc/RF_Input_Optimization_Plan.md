# RF Input Optimization Plan (J1 Würth 60312202114307, 1 MHz – 10 GHz)

Status: **proposal, not yet simulated or implemented.** Based on rfsim/openEMS runs of 2026-10-07
(`kicad/rfsim_results/run_20261007_*`) and an analytical impedance estimate.

## 1. Findings

| # | Finding | Evidence |
|---|---------|----------|
| 1 | J1 line is ~**90 Ω**, not 50 Ω | F.Cu trace 0.2032 mm, coplanar gap 0.20 mm, ref In1.Cu at h = 0.533 mm, εr 4.5 → CPWG formula ≈ 90 Ω |
| 2 | SMA pad (0.8 mm) → trace (0.2 mm) is an abrupt step | rfsim port data: pad width 0.8, track width 0.2032 |
| 3 | Input looks capacitive/open below ~5 GHz | \|S11\| −0.2 dB @ 1 GHz (55 − j484 Ω), −0.5 dB @ 2.4 GHz (22 − j184 Ω) |
| 4 | Best match only at 8–9.5 GHz | \|S11\| −12 to −17.6 dB (medium mesh) |
| 5 | New connector removes the 3–5.8 GHz resonance of the old one | Old run (1–6 GHz only): Z swings 94 + j70 Ω @ 4 GHz, 241 − j65 Ω @ 5 GHz |
| 6 | U1 pad 8 (INLO) coplanar gap 43 % asymmetric | rfsim warning |
| 7 | Coupling (S21/S31/S32) not converged | coarse vs medium differ by 15–25 dB |
| 8 | S11 at 8–10 GHz mesh-sensitive | coarse vs medium differ 1–2 dB |

> **Update 2026-10-09:** see `S11_Analysis_J1_to_AD8317.md`. The rfsim 3-port turned out to be decoupled from ports 2/3 (lumped R/C not effective), so finding 3 ("capacitive/open") is a solver artefact; a circuit model with the real AD8317 input gives S11 ≤ −10 dB except ~−9 dB near 3.6 GHz, and a 50 Ω re-draw (change 1) does not improve it.

> **Targets and next steps:** see `RF_Input_Targets_vs_Lab_Power_Meters.md` (return-loss targets, mismatch budget, input pad, calibration) and the optimiser results in `S11_Analysis_J1_to_AD8317.md`.

Caveats: ports 2/3 are 50 Ω terminations, not the AD8317 input; connector body is not modelled
(only the pad position); 1–100 MHz is not covered by FDTD; old connector was never simulated above 6 GHz.

## 2. Design changes to make (KiCad)

Priority order. Verify each one in rfsim before keeping it.

1. **Make the J1 → first-component line 50 Ω.** Widen the F.Cu trace (about 1.0 mm for plain
   microstrip, wider with 0.2 mm coplanar gaps; 0.8 mm CPWG ≈ 63 Ω). Match the SMA pad width so there is no step.
2. **Taper to the 0402 pad** (~0.5 mm) over a short length.
3. **J1 pad ground cutout on In1.Cu** (already done: In1/In2 are void under the whole RF corridor, GND only on F.Cu and B.Cu) to cut pad capacitance. Size to be found by sweep.
4. **Stitch SMA ground pads** to the ground pour with vias close to the pad; via fence pitch ≤ 3.2 mm.
5. **Broadband parts at the input.** Replace 47 nF 0402 DC blocks (C1, C2) with a broadband-rated
   DC block; use a low-ESL termination (0201 or thin-film) placed at the AD8317 pins.
6. **Even out INLO (U1 pad 8) coplanar gap** if the asymmetry is real layout, not a modelling artefact.

## 3. Simulation work

| Step | Setting |
|------|---------|
| Reference | re-run old-connector geometry, 0.1–10 GHz, same mesh as new |
| Mesh | fine mesh for final results (coarse/medium disagree at 8–10 GHz) |
| Sweeps (one parameter at a time) | trace width 0.2/0.4/0.8/1.0/1.2 mm; coplanar gap; In1 cutout size; taper length |
| Terminations | replace ports 2/3 50 Ω with measured AD8317 INHI impedance (`sim/AD8317_INHI_S11_*.s1p`) |
| Figure of merit | worst-case \|S11\| from 1 to 10 GHz (today: −0.5 dB @ 2.4 GHz, −17.6 dB @ 9 GHz) |
| Below 100 MHz | circuit model (R, C, measured AD8317 impedance), not FDTD |

## 4. Markdown files that need updating

| File | Update needed |
|------|---------------|
| `Micro_RF_Power_Meter_DRD.md` line 382 (BOM) | J1: Amphenol 132289 → **Würth Elektronik 60312202114307** (WR-SMA, SMD). Update Mouser/Digi-Key part number and notes |
| `Micro_RF_Power_Meter_DRD.md` §10, line 441 | Connector table row "SMA (Amphenol 132289)" → Würth 60312202114307; re-check the "edge-mount / edge-launch" wording, the new part is SMD |
| `Micro_RF_Power_Meter_DRD.md` §10 launch requirements | Req. 1 (GCPW 50 Ω) and Req. 2 (gap ≤ 0.15 mm) are **not met** today (≈ 90 Ω, 0.20 mm gap). Update after redesign |
| `Micro_RF_Power_Meter_DRD.md` §10 req. 5 | "No ground plane interruption under the SMA pad" conflicts with change 3 above. Reword to the result of the cutout sweep |
| `Micro_RF_Power_Meter_DRD.md` line 43 | Block diagram says "SMA Edge-Mount"; update to the SMD connector |
| `doc/KiCad.md` | Mention new symbol/footprint libs (`lib/symbols/Connector_Wurth_WR-SMA.kicad_sym`, `J_Wurth_WR-SMA_60312202114307`) and 3D model |
| `doc/EMerge_export.md` | Note port-focused subregion and that the connector body is not modelled |
| `doc/Lab_Optimization_DC_to_6GHz.md` | Extend/cross-reference for the 1 MHz–10 GHz target |
| `memory/project_ad8317.md`, `memory/kicad_simulation.md`, `memory/freecad_openems_export.md` | Replace 132372/132289 references with the Würth part; add today's rfsim findings |
| New: results note | Save the run comparison (this plan, §1) next to `kicad/rfsim_results/` once the sweeps are done |

Other stale references to 132372 (not md): `lib/132372.stp`, `doc/132372.pdf`,
`sim/emerge/tests/test_kicad_reader.py`, `kicad/ad8317_powermeter.kicad_sch_old`, `kicad/FEM/ad8317_powermeter.step`.

## 5. Open questions

- Is the 90 Ω estimate confirmed by simulation (extract Z0 of the J1 line)?
- Does the real AD8317 input impedance change the optimal trace width?
- Does the 30 dB external attenuator (DRD line 43) stay in the signal path for all bands?

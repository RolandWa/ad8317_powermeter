# S11 analysis: J1 (SMA pad) → AD8317 input, 1 MHz – 10 GHz

Date: 2026-10-09. Board: Würth 60312202114307, 4-layer FR-4 (εr 4.5, tanδ 0.02), In1.Cu GND reference at 0.533 mm.
Ports: **P1** = J1 pad, **P2** = U1:1 (INHI), **P3** = U1:8 (INLO).
Everything below is reproducible with `sim/qucs/run_s11_study.py` (qucsator 0.0.19, numpy).

## 1. AD8317 input model (replaces the 50 Ω terminations of P2/P3)

`sim/qucs/ad8317_input_model.py` → `AD8317_INHI_1MHz_10GHz.s1p` (1-port, INHI vs ground) and
`AD8317_INHI_INLO_series_1MHz_10GHz.s2p` (series element between P2 and P3, used in the Qucs files).

| f | Zin (datasheet Rev. D) |
|---|---|
| LF (p.10) | 500 Ω ‖ 0.7 pF |
| 0.9 / 1.9 / 2.2 GHz | 1500 ‖ 0.33 / 950 ‖ 0.38 / 810 ‖ 0.39 (Ω ‖ pF) |
| 3.6 / 5.8 / 8.0 GHz | 300 ‖ 0.33 / 110 ‖ 0.05 / 28 ‖ 0.79 (Ω ‖ pF) |

G = 1/R and C are interpolated vs log f between anchors; 8 GHz values are held to 10 GHz. The chip is **not** 50 Ω:
alone it reflects −0.6 … −3 dB over most of the band (|S11| ≈ −11 dB only around 6 GHz). INLO is an ac-coupled RF common, so
the model is a two-terminal load INHI–INLO (INLO returns to GND through C2 on the board).
Existing Figure-15-based files (`sim/AD8317_INHI_S11_*.s1p`, 0.05–10 GHz) were not used: they are chart readings, and the numeric table above is what the datasheet states.

## 2. Result 1 — the rfsim 3-port cannot answer the question

Qucs case **A1** = `rfsim_J1_U1_3port_1MHz_10GHz.s3p` (run_20261009_115836, fine mesh) + AD8317 load.
It is indistinguishable from **A0** (same 3-port with 50 Ω on P2/P3): 

| | 1 MHz–1 GHz | 3 GHz | 6 GHz | 10 GHz |
|---|---|---|---|---|
| A0 / A1 \|S11\| | −0.2 … −0.4 dB | −1.7 dB | −5.8 dB | −11.5 dB |

Because the load does not matter, P1 is electrically isolated from P2/P3 in the FDTD result: S21 = −103 dB, S22 = −0.05 dB at 1 GHz.
With R1/R2 = 100 Ω to GND and C1 = 47 nF in series, a real board gives S11 ≈ −10…−25 dB and S21 ≈ 0 dB at 1 GHz.
Evidence that this is a model/solver artefact and not the layout:

* S21 at 1 GHz gets *worse* with mesh refinement: coarse −48 dB → medium −77 dB → fine −103 dB (runs 20261007_163618 / 20261007_164359 / 20261009_*).
* The run with `lumped=False` (gap between pads left open) has a *higher* S21 (−31 dB) than any run with lumped C1/R1/R2 (−48 … −108 dB), so the lumped parts are not conducting.
* Layout plot of model.json shows R1/R2 bridging trace → F.Cu GND pour and C1/C2 bridging the pad gaps, i.e. the geometry itself is as in the schematic.
* Below ~100 MHz FDTD is not trustworthy anyway (not covered by the plan either); the s3p was extended flat from 10 MHz to 1 MHz only so Qucs can sweep from 1 MHz.

So the EM numbers (A0/A1) say only "open SMA pad, 0.3 pF" and must not be used to judge the match.

Follow-up tests on the same model (coarse mesh, port 1 only, 0.1–10 GHz; scratch runs, not stored):

| Run | S11 / S21 @ 1 GHz |
|---|---|
| T0 baseline (R1/R2/C1/C2 lumped) | −0.35 / −78 dB |
| T1 C1,C2 → metal shorts, R1/R2 lumped | −0.35 / −96 dB (same as T0) |
| T2 C1,C2 → shorts, R1/R2 removed | −0.22 / −51 dB |
| T4 R1/R2 removed, C1,C2 = 0.2 pF | −0.22 / −51 dB (same as T2) |
| T6 as T0, lumped boxes 0.2 mm tall in z (patched mesh) | −0.36 / −78 dB (same as T0) |
| T3 as T1 but R1/R2 = 100 kΩ | energy = NaN from the first time steps |

* Hypothesis "R1/R2 or C1/C2 not touching copper" is rejected: point-in-polygon check on model.json shows both box faces of every element inside the correct F.Cu polygon (signal island: P1, R1/R2 near ends, C1 left pad; GND pour with 64 vias: R1/R2 far ends, C2; C1 right pad = island with P2).
* Hypothesis "zero-thickness box in z" is not confirmed: T6 gives the same S-parameters.
* What the tests do show: the element *value* does not act as set (C = 0.2 pF behaves like a short, T4 = T2; R = 100 Ω like a low-impedance shunt, T0 vs T2). Even with C shorted and R removed S21 stays at −51 dB at 1 GHz, so the problem is not only in the elements.

### Root cause found: ports reference a plane that does not exist under them

Upstream validation (`run_lumped.py`, `run_rlc.py`, `run_shunt.py`, run here with the same openEMS rc2) **all pass** (series R −4.2/−3.5 dB, series R/L/C vs theory, shunt-C notch ESL within 3 %), so solver and element handling are fine. The difference is the board:

* In1.Cu and In2.Cu are **void under the whole RF input by design** (intentional, to reduce the SMA pad capacitance; GND only on F.Cu pour and B.Cu) (a ~4 mm wide corridor from the J1 pad to U1, plus the pad cut-out): point-in-polygon check gives 0 In1/In2 polygons at P1, P2, P3 and under the trace; only B.Cu (z = 0) is solid there.
* All three rfsim ports use `ref_layer = In1.Cu` (the tool's default guess, not a layout error). A lumped port is a resistor from the pad down to the reference layer, so it ends in air and nothing flows: S22 ≈ +1, S11 ≈ open, S21 ≈ −80…−100 dB, independent of R/C values. This is also why refining the mesh made it "worse".
* Test H7: same model, only `ref_layer = B.Cu` for the 3 ports (coarse): S11 at 1 / 3 / 6 GHz changes from −0.35 / −1.7 / −5.8 dB to −2.1 / −7.1 / −10.8 dB and S21 at 1 GHz from −78 to −29 dB. Medium mesh (H9) gives the same (−2.1 dB / −26 dB at 1 GHz), so the rest is not a mesh effect: with 100 Ω ∥ 100 Ω to GND the board should still give S11 ≈ −10 dB and S21 ≈ −6…−10 dB at 0.2–1 GHz, but S11 stays ≈ −0.2…−2 dB. The shunt/return path (R1/R2 → F.Cu pour → vias → B.Cu, and ports 2/3 into the 1.44 mm tall reference) is still not right; this is **not yet solved**.
* Second error: the run used the rfsim dialog stackup (three equal 0.533 mm layers, In1 at 0.533 mm below F.Cu). The saved KiCad stackup is F.Cu / 0.1 mm prepreg (εr 4.4) / In1 / 1.24 mm core / In2 / 0.1 mm prepreg / B.Cu. With the real stackup and the old `runner.py` ports snap to zero length at medium mesh ("Lumped Element with zero (snapped) length is invalid! skipping: port_resist_1..3"), so use fine mesh or CPW-type ports.
* Consequence for the plan: "J1 line is ~90 Ω (ref In1 at 0.533 mm)" is wrong in its reason. The true reference is the coplanar F.Cu pour plus B.Cu 1.44 mm below; the grounded-CPW estimate is still ≈ 88 Ω for the 0.2032 mm trace, so the conclusion (≈ 90 Ω line) survives by coincidence. The circuit model below now uses h = 1.44 mm, εr 4.5.

Upstream search (2026-10-09): kicad-rfsim has no open issue on this (only #5, slash-rated values; PR #6 closed). The installed plugin (runner.py, 1175 lines) is much older than upstream main (v1.2.0, 2316 lines), where a lone R or C is no longer put on the series `LEtype=1` path ("R ≥ 150 Ω diverges to NaN" is documented in `_le_topology`) – this explains the NaN of test T3 but not the main fault: upstream runner on the same model (R on the classic path) still gives S11 −0.23 dB / S21 −49 dB at 1 GHz. openEMS issue #136 ("Weird Behavior of Lumped Components", closed, redirected to Discussions) describes the same symptom (lumped R has no effect, `AddMetal` does), no fix recorded.

## 3. Result 2 — circuit-level model of the same board (cross-check, case B)

Sections read from the rfsim layout (mm): pad 0.8 wide × 1.1 (to pad edge), 0.2032 × 0.65 → R1 tap → 0.30 × 1.0 → R2 tap → 0.2032 × 1.1 → C1 → 0.1524 × 1.68 → U1:1.
INLO: 0.25 × 1.7 → C2 → GND. Lines = grounded CPW (Simons), 0.20 mm gap, ground plane B.Cu 1.44 mm below (In1/In2 void), εr 4.5: Z0 = 88 Ω (0.2032), 95 Ω (0.1524), 71 Ω (pad). R1, R2 = 100 Ω + 0.25 nH ESL, C1/C2 = 47 nF + 0.25 nH + 35 mΩ.
Not modelled: connector body/launch, vias, pad-to-ground fringe C, the step discontinuities.

| Band | worst \|S11\| (B, current layout) | worst (B2: 50 Ω traces, 1.21 mm wide) |
|---|---|---|
| 1–100 MHz | −26.5 dB | −26.4 dB |
| 0.1–1 GHz | −25.4 dB | −20.6 dB |
| 1–3 GHz | −11.2 dB @ 2.95 GHz | −9.5 dB |
| 3–6 GHz | **−8.7 dB @ 3.6 GHz** | −7.8 dB |
| 6–10 GHz | −9.2 dB @ 6.3 GHz | −9.2 dB |

\|S11\| < −10 dB on 93 % of the log sweep (B) vs 91 % (B2). Low-frequency value is set by R1‖R2 = 50 Ω in parallel with 500 Ω.
The 50 Ω shunt is what gives the match; the trace itself is secondary. The chip's capacitive input is partly compensated by the ~90 Ω (inductive) lines, so
**re-drawing the line as 50 Ω (plan item 1) makes the 3–4 GHz dip ≈ 1 dB worse in this model**, not better.

## 4. Verdict

* Is S11 optimal? **Not with certainty — the EM model as run cannot show it (ports floated over a void In1); the circuit model says "good, not optimal"**: ≤ −10 dB almost everywhere, weak regions 3–4 GHz (−8.7 dB) and ~6 GHz (−9.2 dB), set mainly by the chip's 300 Ω ‖ 0.33 pF at 3.6 GHz and the 50 Ω shunt.
* Cheap levers to test in Qucs (case B) before touching layout: R1‖R2 value, small series L/C tuning at INHI, shunt position; not trace width.
* Next steps: (1) rerun rfsim with ports referenced to B.Cu (or CPW ports) on fine mesh, then terminate with the `.s2p` series load; (2) re-evaluate plan items in `RF_Input_Optimization_Plan.md`; (3) measure real S11 with the VNA at J1 and compare with case B.

## 5. Files (`sim/qucs/`)

| File | Content |
|---|---|
| `ad8317_input_model.py` | AD8317 Zin model + Touchstone writers |
| `AD8317_INHI_1MHz_10GHz.s1p`, `AD8317_INHI_INLO_series_1MHz_10GHz.s2p` | chip S-parameters (1-port, and series 2-port for P2–P3) |
| `rfsim_J1_U1_3port_1MHz_10GHz.s3p` | rfsim result, Qucs-readable (wrapped rows), flat-extended to 1 MHz |
| `case_A0/A1/B/B0/B2_*.net` (+ `.dat`) | qucsator netlists and results; run: `qucsator -i case_A1_rfsim_ad8317.net -o out.dat` from this folder |
| `run_s11_study.py` | regenerates everything, also cross-checks A1 with an independent numpy Y-matrix reduction (max difference 4e-13) |
| `s11_results.csv`, `s11_1MHz_10GHz.png` | S11 [dB] per case, plot |

The Qucs GUI could not be driven headless here, so no `.sch` was produced; the `.net` files are the simulation inputs.

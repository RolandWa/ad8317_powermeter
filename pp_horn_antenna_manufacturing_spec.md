# Process Specification: High-Frequency Microwave Horn Antenna Manufacturing via 3D Printed Polypropylene (PP)

## Document Overview
This document outlines the end-to-end engineering specification for manufacturing precision K/Ka-band (10 GHz – 26 GHz) horn antennas using Polypropylene (PP) fused deposition modeling (FDM) additive manufacturing, thermal media annealing, chemical etching, and electrogalvanic copper plating.

---

## 1. Material & Printing Guidelines

### 1.1 Material Properties
* **Base Polymer:** Unfilled Polypropylene (PP)
* **Dielectric Constant ($arepsilon_r$):** $\sim 2.2 - 2.3$ @ 26 GHz
* **Loss Tangent ($	an \delta$):** $\sim 0.0003 - 0.001$ @ 26 GHz

### 1.2 FDM Slicer Settings (Bambu Studio / OrcaSlicer)
* **Orientation:** Vertical (Flange on print bed, flare expanding upward).
* **Layer Height:** 0.08 mm – 0.10 mm (Adaptive layer height in waveguide throat).
* **Wall Loops:** 5 to 8 solid perimeter loops.
* **Infill:** 100% Solid (Concentric pattern).
* **Flow Ratio:** 1.04 – 1.05 (Deliberate over-extrusion to eliminate internal air voids).
* **Seam Placement:** Rear / External surface (Zero Z-seam artifacts on internal waveguide walls).

---

## 2. Media-Assisted Salt Annealing Specification

### 2.1 Equipment & Tooling
* **Thermal Chamber:** Environmental / Drying chamber (Humidity control: OFF).
* **Enclosure:** Stainless Steel Gastronorm (GN 1/2 or GN 1/3, 100 mm depth) with fitted lid.
* **Media:** Fine-grained non-iodized sodium chloride ($\mathrm{NaCl}$), pre-dried at 120°C for 2 hours.

### 2.2 Thermal Profile Sequence
1. **Preparation:** Seal process holes and interfaces with High-Temp Kapton tape. Submerge print in pre-dried salt with a minimum 3 cm margin on all sides. Compact gently via mechanical vibration.
2. **Ramp-Up Phase:** Heat chamber from ambient to **128°C – 130°C** at a rate of **1.0°C – 1.5°C/min**.
3. **Soak / Annealing Phase:** Maintain **128°C – 130°C** for **2 to 3 hours** (thermal stabilization and stress-relief).
4. **Controlled Ramp-Down Phase:** Cool at a rate **$\le 0.5^\circ	ext{C/min}$** down to **$< 50^\circ	ext{C}$** before unsealing the chamber.

---

## 3. Surface Preparation & Chemical Etching

### 3.1 Pre-Etch Cleaning
1. Ultrasonic bath in 5% alkaline detergent solution at 55°C for 15 minutes.
2. Triple rinse in Deionized (DI) Water.
3. Isopropyl Alcohol (IPA 99.9%) flush (1–2 minutes) followed by dry nitrogen purge.

### 3.2 Sulfochromic Etching (Chromic Acid Bath)
> **Hazard Warning:** Chromic acid contains Hexavalent Chromium ($\mathrm{Cr^{VI}}$). Handle strictly inside a certified fume hood with proper PPE.

* **Bath Composition:**
  * $\mathrm{CrO_3}$ (Chromium Trioxide): $400 - 450	ext{ g/L}$
  * $\mathrm{H_2SO_4}$ (Sulfuric Acid 96–98%): $200 - 250	ext{ mL/L}$
  * Deionized Water: Balance to $1.0	ext{ L}$
* **Process Parameters:**
  * Temperature: **65°C – 75°C**
  * Duration: **10 – 15 minutes**
* **Post-Etch Neutralization:**
  * Submerge in $30	ext{ g/L}$ Sodium Bisulfite ($\mathrm{NaHSO_3}$) + $10	ext{ mL/L}$ $\mathrm{HCl}$ solution for 3 minutes to reduce residual $\mathrm{Cr^{VI}}$ to $\mathrm{Cr^{III}}$.
  * Final triple rinse in DI water.

---

## 4. Electrogalvanic Copper Metallization

### 4.1 Acid Sulfate Copper Plating Bath Receptacle
* **$\mathrm{CuSO_4 \cdot 5H_2O}$ (Copper Sulfate Pentahydrate):** $200 - 220	ext{ g/L}$
* **$\mathrm{H_2SO_4}$ (Concentrated Sulfuric Acid):** $50 - 60	ext{ g/L}$ ($\sim 30-35	ext{ mL/L}$)
* **$\mathrm{HCl}$ (Hydrochloric Acid 37%):** $0.1 - 0.15	ext{ mL/L}$ ($\sim 50-100	ext{ mg/L}$ $\mathrm{Cl^-}$)
* **Organic Brightener / Leveller:** $2 - 5	ext{ mL/L}$

### 4.2 Operating Parameters
* **Initial Strike Current Density:** $0.5	ext{ A/dm}^2$ for 5–10 minutes.
* **Main Plating Current Density:** $1.5 - 2.5	ext{ A/dm}^2$.
* **Anode Material:** Phosphorized Copper ($\mathrm{Cu-P}$, 0.04–0.06% P) in polypropylene anode bags.
* **Plating Time:** $\sim 70	ext{ minutes}$ for a target thickness of $20\,\mu	ext{m}$ (exceeding skin depth $\delta pprox 0.65\,\mu	ext{m}$ @ 26 GHz).
* **Auxiliary Anode:** Central copper rod along the horn axis to overcome Faraday cage shielding inside the waveguide throat.

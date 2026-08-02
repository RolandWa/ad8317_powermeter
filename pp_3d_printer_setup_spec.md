# 3D Printer Setup Specification: Polypropylene (PP) RF & Microwave Components

## Document Overview
This document details the precise 3D printer hardware setup, slicer configuration, and orientation parameters for printing Polypropylene (PP) high-frequency RF components (such as K/Ka-band Horn Antennas for 10 GHz – 26 GHz) on Bambu Lab and OrcaSlicer platforms.

---

## 1. Hardware & Physical Setup

### 1.1 Build Plate & Bed Adhesion
* **Surface Type:** Smooth PEI Sheet or Cool Plate.
* **Adhesive Layer:** Covered with wide **Oriented Polypropylene (BOPP)** packing tape or specialized PP adhesive (e.g., *Magigoo PP*).
* **Brim Configuration:** Outer Brim only (**10–15 mm width**) with a **0.00 mm Brim-to-Object Gap** to counter PP thermal warping.

### 1.2 Toolhead & Enclosure
* **Nozzle:** 0.4 mm Hardened Steel or Stainless Steel nozzle.
* **Enclosure Control:** **Fully Closed Chamber**.
* **Chamber Airflow:** All active enclosure exhaust/auxiliary fans **OFF** to maintain stable ambient temperatures ($\sim 35^\circ	ext{C} - 45^\circ	ext{C}$).

### 1.3 Material Conditioning
* **Drying Protocol:** Pre-dry PP spool at **70°C for 6–8 hours**.
* **Feed System:** Print directly from a sealed dry box or AMS with active/fresh desiccant.

---

## 2. Slicer Profile Configuration (Bambu Studio / OrcaSlicer)

### 2.1 Thermal & Motion Parameters
| Parameter | Setting | Engineering Justification |
| :--- | :--- | :--- |
| **Nozzle Temperature** | **220°C – 240°C** | Higher thermal energy ensures complete inter-layer fusion and eliminates micro-voids. |
| **Bed Temperature** | **85°C – 100°C** | Maintains bed adhesion above the glass transition phase. |
| **Print Speed** | **40 – 60 mm/s** | Slow extrusion rate guarantees dense volumetric deposition without shear stress. |
| **Part Cooling Fan** | **0% (OFF)** | Disabled after Layer 1 to prevent delamination and warping. |

### 2.2 Volumetric & RF Density Optimizations
* **Layer Height:** **0.08 mm – 0.10 mm** (Minimizes surface step roughness $R_a$ on internal walls).
* **Adaptive Layer Height:** **Enabled** (Reduces layer thickness down to 0.08 mm specifically at the narrow waveguide throat).
* **Flow Ratio (Extrusion Multiplier):** **1.04 – 1.05** (Intentional $4–5\%$ over-extrusion to force molten polymer into any internal voids).
* **Wall Loops:** **5 to 8 Solid Perimeter Loops** (Ensures full solid cross-section without void-heavy internal fill).
* **Infill Pattern:** **100% Solid Concentric** (Continuous circular toolpath prevents sharp directional changes).
* **Seam Placement:** **Aligned / Painted on External Face Only** (Guarantees zero Z-seam artifacts on internal RF waveguide walls).

---

## 3. Orientation & Support Guidelines

* **Print Orientation:** **Vertical** (Flange base mounted flat on the bed, flare opening upward).
  * *RF Alignment:* Current flow in $TE_{10}$ mode runs parallel to printed layer lines, minimizing RF attenuation.
  * *Self-Supporting:* Internal flare angles up to $30^\circ$ from vertical require zero internal support material.
* **Internal Supports:** **STRICTLY DISABLED**. Internal waveguide cavity must remain pristine for post-print chemical etching and electroplating.

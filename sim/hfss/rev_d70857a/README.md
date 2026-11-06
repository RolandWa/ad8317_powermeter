# HFSS verification of PCB revision d70857a

The PCB in commit d70857a (SHA-256 40B50F97...) is the same file that was simulated as the revision
`pcb_wip_40b50f97` in `D:\Ansys_proj\AD8317_RF_HFSS_KiCad\revisions\pcb_wip_40b50f97`
(copy on `K:\AD8317_RF_HFSS_KiCad\revisions\pcb_wip_40b50f97`).

- Solver: HFSS 2026 R1, 7-port model SMA -> PCB -> AD8317, HPC job 4251, 6 adaptive passes, final delta S 0.0033.
- Port order in `RF_7port.s7p`: P1_SMA, P2_INHI, P3_INLO, P4_R1, P5_R2, P6_C1, P7_C2 (50 ohm).
- `circuit_post.py` closes the component ports with R/C models and the AD8317 input model.
- Reports: `RAPORT_REWIZJI.pdf` (revision) and `RAPORT_SMA_PCB_AD8317.pdf` (SMA -> PCB -> AD8317).
- `component_options.json`: circuit study of resistor/capacitor options on the unchanged layout.

RF line: CPWG on F.Cu over In1 GND, 0.10 mm FR4 prepreg (er 4.4, tan d 0.02), 35 um Cu.
J1-In trace 0.15 mm, top GND gap 0.16-0.71 mm. INHI/INLO 0.25 mm.
Trace and GND are bare copper; the ENIG finish is not modelled.

The scripts expect the revision folder layout; they are archived here for traceability.

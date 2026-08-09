"""
Microstrip with a mid-line series lumped element using the same workflow as
bandpass_filter_30MHz.py.

Key points:
- PCBNew path + lumped_element() for series insertion
- Mesh sizing via set_boundary_size / set_face_size / set_domain_size
- Geometry and mesh rendering via m.view(...)
- Field animation via cutplane + m.display.animate()
"""

import math
import pathlib
import sys
import time

import emerge as em
import numpy as np
from emerge.plot import plot_sp

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def log(msg):
    print(msg, flush=True)


# CLI options
_RLC_MODE = "r"
_SHOW_GEOMETRY = "--show-geometry" in sys.argv
_SHOW_MESH = "--show-mesh" in sys.argv
_PLOTS_ENABLED = "--plots" in sys.argv
for _i, _a in enumerate(sys.argv[1:], 1):
    if _a == "--rlc" and _i < len(sys.argv):
        _RLC_MODE = sys.argv[_i + 1].lower()

_OUTDIR = pathlib.Path(__file__).parent / "results" / "microstrip_rlc"
_OUTDIR.mkdir(parents=True, exist_ok=True)

log(f"EMerge {em.__version__} OK")

# Units
mm = 1e-3
nF = 1e-9
pF = 1e-12
nH = 1e-9

# Board and trace geometry in mm (same style as bandpass_filter_30MHz.py)
BW = 10.0
BH = 5.0
TH = 1.6
HAIR = 4.0
TW = 3.0

PX1 = 1.0
PX2 = 9.0
PY = BH / 2.0
RLC_X = BW / 2.0
RLC_LEN = 0.6

# Component values
R_VAL = 50.0
C_VAL = 10.0 * pF
L_VAL = 5.0 * nH

# Use the same helper API used in the exporter flow.
from basicemergesolverhelperpackage.EMergeConstants import parallel_impedance, series_impedance


def _make_z_func():
    if _RLC_MODE == "r":
        log(f"  RLC mode: series_impedance(R={R_VAL} ohm)")
        return series_impedance(R=R_VAL)
    if _RLC_MODE == "rc":
        log(f"  RLC mode: parallel_impedance(R={R_VAL} ohm, C={C_VAL*1e12:.2g} pF)")
        return parallel_impedance(R=R_VAL, C=C_VAL)
    if _RLC_MODE == "rl":
        log(f"  RLC mode: series_impedance(R={R_VAL} ohm, L={L_VAL*1e9:.2g} nH)")
        return series_impedance(R=R_VAL, L=L_VAL)
    if _RLC_MODE == "rlc":
        log(
            "  RLC mode: series_impedance("
            f"R={R_VAL} ohm, L={L_VAL*1e9:.2g} nH, C={C_VAL*1e12:.2g} pF)"
        )
        return series_impedance(R=R_VAL, L=L_VAL, C=C_VAL)

    log(f"  Unknown --rlc mode '{_RLC_MODE}', defaulting to R only")
    return series_impedance(R=R_VAL)


Z_FUNC = _make_z_func()

log("  Z_component at selected frequencies:")
log("  freq(GHz)   |Z|(ohm)   arg(Z)(deg)")
for _f_chk in [0.1e9, 1e9, 3e9, 6e9]:
    _z = Z_FUNC(_f_chk)
    _ang = math.degrees(math.atan2(_z.imag, _z.real))
    log(f"    {_f_chk/1e9:5.1f}      {abs(_z):8.2f}   {_ang:+8.1f}")

t0 = time.monotonic()
log(f"\n=== Stage 1: Build geometry (RLC mode={_RLC_MODE}) ===")

pack = "0603"
m = em.Simulation(f"microstrip_rlc_{_RLC_MODE}", save_file=True, store_system="msgpack")
pcb = em.geo.PCBNew(thickness=TH, unit=mm, cs=em.GCS, material=em.lib.DIEL_FR4, layers=2)

line = pcb.new(0, PY, TW, (1, 0)).store("p1")
line = line.straight(PX1)
line = line.straight(RLC_X - RLC_LEN / 2 - PX1)
line = line.lumped_element(Z_FUNC, pack)
line = line.straight(PX2 - (RLC_X + RLC_LEN / 2)).store("p2")
line = line.straight(BW - PX2)

LEs = pcb.lumped_elements
traces = pcb.compile_paths(merge=True)
pcb.determine_bounds(leftmargin=4, topmargin=4, rightmargin=4, bottommargin=4)

mp1 = pcb.lumped_port("p1", name="port_in")
mp2 = pcb.lumped_port("p2", name="port_out")

diel = pcb.generate_pcb()
air = pcb.generate_air(HAIR)

m.commit_geometry()
log(f"Geometry built ({time.monotonic()-t0:.1f} s), lumped elements: {len(LEs)}")

# Same rendering calls as bandpass_filter_30MHz.py
if _SHOW_GEOMETRY:
    m.view()

log("\n=== Stage 2: Mesh ===")
m.mw.set_frequency_range(100e6, 6e9, 51)
m.mesher.set_boundary_size(traces, 0.5 * mm)
for le in LEs:
    m.mesher.set_face_size(le, 0.1 * mm)
m.mesher.set_domain_size(diel, 1.0 * mm)
m.mesher.set_domain_size(air, 3.0 * mm)

t1 = time.monotonic()
m.generate_mesh()
log(f"Mesh done ({time.monotonic()-t1:.1f} s)")

if _SHOW_MESH:
    m.view(plot_mesh=True, volume_mesh=False)

log("\n=== Stage 3: BCs + FEM sweep ===")
m.mw.bc.LumpedPort(mp1, 1)
m.mw.bc.LumpedPort(mp2, 2)
for le in LEs:
    m.mw.bc.LumpedElement(le)

t2 = time.monotonic()
data = m.mw.run_sweep()
log(f"Sweep done ({time.monotonic()-t2:.1f} s)")
m.save()

log("\n=== Stage 4: S-parameters ===")
grid = data.scalar.grid
fd = grid.dense_f(1001)
S11 = grid.model_S(1, 1, fd)
S21 = grid.model_S(2, 1, fd)
plot_sp(fd, [S11, S21], xunit="MHz", labels=["S11", "S21"], logx=False)

log("\n=== Stage 5: Field animation ===")
if _PLOTS_ENABLED:
    m.display.add_object(diel, opacity=0.1)
    m.display.add_object(traces, opacity=0.1)
    mid_f = 3.0e9
    cut = data.field.find(freq=mid_f).cutplane(0.1 * mm, z=-TH / 2 * mm)
    m.display.animate().add_field(cut.scalar("Ez", "complex"), symmetrize=True)
    m.display.show()
else:
    log("  Field animation skipped (pass --plots to enable).")

log(f"\nDone. Total: {time.monotonic()-t0:.1f} s")
log(f"Output: {_OUTDIR}")

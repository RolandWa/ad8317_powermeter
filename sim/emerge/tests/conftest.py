"""
Pytest session configuration for EMerge pipeline tests.

Installs mocks for both EMerge API paths before any test module is imported:

  emerge_pipeline.py  →  emerge.beta.gerber.FileBasedPCB
  emerge_runner.py    →  emerge.Simulation / PCBNew / PCBLayer /
                         open_pml_region / generate_touchstone / TouchstoneData

Tests run without an EMerge installation or a KiCad installation.
"""

import sys
import types
import pathlib
import math


def pytest_configure(config):

    # ── top-level emerge mock ─────────────────────────────────────────────────
    emerge_mock = types.ModuleType("emerge")
    emerge_mock.__version__ = "2.8.0-mock"

    # Constants shared by both pipeline and runner
    emerge_mock.ZAX = "ZAX"
    emerge_mock.XAX = "XAX"
    emerge_mock.YAX = "YAX"

    # emerge_pipeline.py: series/parallel impedance helpers
    emerge_mock.series_impedance   = lambda R=50.0:          {"type": "series",   "R": R}
    emerge_mock.parallel_impedance = lambda R=50.0, C=0.0:   {"type": "parallel", "R": R, "C": C}

    # ── emerge_runner.py: Simulation class ───────────────────────────────────
    class _FakeMWResult:
        """Minimal simulation result: 3-point sweep, low-loss network."""
        class _Scalar:
            def axis(self, name):
                return [1e9, 5e9, 10e9]
            def find(self, freq=0):
                class _SC:
                    def S(self, i, j):
                        # S11 ≈ -20 dB (0.1 magnitude), S21 ≈ -0.9 dB (0.9 magnitude)
                        return complex(0.1) if i == j else complex(0.9)
                return _SC()
        scalar = _Scalar()

    class _FakeSolverRoutine:
        def set_solver(self, *args): pass

    class _FakeMWBC:
        def LumpedPort(self, face, port_number, Z0=50.0, **kw): return object()

    class _FakeMW:
        nports = 2
        def __init__(self):
            self.bc = _FakeMWBC()
            self.solveroutine = _FakeSolverRoutine()
        def set_frequency_range(self, fmin, fmax, Npoints): pass
        def run_sweep(self): return _FakeMWResult()

    class FakeSimulation:
        modelname = "test_model"
        def __init__(self, name, loglevel="WARNING"):
            self.modelname = name
            self.mw = _FakeMW()
        def set_physics(self, **kw): pass
        def set_resolution(self, r): pass
        def generate_mesh(self): pass
        def commit_geometry(self, *args): pass
        def view(self, **kw): pass   # no-op — no display in tests

    class FakeMaterial:
        def __init__(self, **kw):
            self.name = kw.get("name", "")

    class FakeTouchstoneData:
        """
        Mock for emerge.TouchstoneData used in emerge_runner.EmergeReporter.

        Values represent a well-matched low-loss network:
          S11 magnitudes 0.10 / 0.12 / 0.15  →  -20 / -18 / -16.5 dB  (RL > 16 dB)
          S21 magnitudes 0.95 / 0.93 / 0.90  →  -0.5 / -0.6 / -0.9 dB (IL < 1 dB)
        Both comfortably within the default thresholds (IL<3 dB, RL>10 dB).
        """
        def __init__(self, path):
            self.f = [1e9, 5e9, 10e9]
        def S(self, i, j):
            if i == j:   # S11 — return loss ~16–20 dB
                return [complex(0.10), complex(0.12), complex(0.15)]
            else:        # S21 — insertion loss < 1 dB
                return [complex(0.95), complex(0.93), complex(0.90)]

    emerge_mock.Simulation     = FakeSimulation
    emerge_mock.Material       = FakeMaterial
    emerge_mock.TouchstoneData = FakeTouchstoneData

    def _fake_lumped_element_material(material_name, direction, length, Area,
                                      R=None, L=None, C=None, **kw):
        return FakeMaterial(name=material_name)

    emerge_mock.lumped_element_material = _fake_lumped_element_material

    # ── emerge_pipeline.py: FileBasedPCB ─────────────────────────────────────
    beta_mock   = types.ModuleType("emerge.beta")
    gerber_mock = types.ModuleType("emerge.beta.gerber")

    class FakeFileBasedPCB:
        # Used by emerge_pipeline.py (old API) and emerge_runner.py (new FileBasedPCB path)
        def __init__(self, path=None, **kw): self.path = path
        def set_dielectric(self, **kw): pass
        def set_copper_thickness_mm(self, t): pass
        def open_pml_region(self): pass
        def add_port(self, **kw): pass
        def set_frequency_sweep(self, **kw): pass
        def set_mesh_density(self, **kw): pass
        def solve(self): pass
        def export_touchstone(self, path): pathlib.Path(path).write_text("! mock\n# GHz S MA R 50\n1.0 0.1 0 0.9 0 0.9 0 0.1 0\n")
        # emerge_runner.py FileBasedPCB path (mirrors PCBNew API):
        def set_bounds(self, xmin, ymin, xmax, ymax): pass
        def layer_from_file(self, layer, filename, **kw): return object()
        def vias_from_file(self, filename, **kw): pass
        def lumped_port_pts(self, p1, p2, z, z_ground=None, name=None): return object()
        def generate_pcb(self): return object()
        def generate_air(self, height): return object()

    gerber_mock.FileBasedPCB = FakeFileBasedPCB
    beta_mock.gerber = gerber_mock
    emerge_mock.beta = beta_mock

    # ── emerge_runner.py: internal emerge._emerge.* sub-packages ─────────────
    _emerge     = types.ModuleType("emerge._emerge")
    _geo        = types.ModuleType("emerge._emerge.geo")
    _pcb        = types.ModuleType("emerge._emerge.geo.pcb")
    _open_reg   = types.ModuleType("emerge._emerge.geo.open_region")
    _cs         = types.ModuleType("emerge._emerge.cs")
    _physics    = types.ModuleType("emerge._emerge.physics")
    _mw         = types.ModuleType("emerge._emerge.physics.microwave")
    _ts         = types.ModuleType("emerge._emerge.physics.microwave.touchstone")

    class FakePCBLayer:
        def __init__(self, thickness=0.0, material=None, name=""):
            self.thickness = thickness
            self.material  = material
            self.name      = name

    class FakePCBNew:
        def __init__(self, **kw): pass
        def set_bounds(self, xmin, ymin, xmax, ymax): pass
        def lumped_port_pts(self, p1, p2, z, z_ground=None, name=None):
            return object()
        def generate_pcb(self): return object()
        def generate_air(self, height): return object()
        def add_poly(self, xs, ys, z=0, material=None, name=None): pass
        def layer_from_file(self, layer, filename, **kw): return object()
        def vias_from_file(self, filename, **kw): pass

    def _fake_open_pml_region(xmargin, ymargin, zmargin, **kw):
        return object()

    def _fake_generate_touchstone(filename, freq, Smat, data_format="RI", funit="GHz"):
        """Write a minimal stub .s2p so EmergeReporter can find the file."""
        import numpy as np
        n = len(freq)
        with open(filename, "w") as fh:
            fh.write("! generated by mock\n# GHz S MA R 50\n")
            for k in range(n):
                fh.write(f"{freq[k]/1e9:.4f}  0.10 0  0.90 0  0.90 0  0.10 0\n")

    _pcb.PCBNew         = FakePCBNew
    _pcb.PCBLayer       = FakePCBLayer
    _open_reg.open_pml_region = _fake_open_pml_region
    _cs.ZAX             = "ZAX"
    _ts.generate_touchstone = _fake_generate_touchstone

    # ── emerge._emerge.solver — solver engine classes ─────────────────────────
    _solver = types.ModuleType("emerge._emerge.solver")

    class _FakeSolver:
        pass

    _solver.SolverPardiso   = _FakeSolver
    _solver.SolverMUMPS     = _FakeSolver
    _solver.SolverCuDSS     = _FakeSolver
    _solver.SolverSuperLU   = _FakeSolver
    _solver.SolverUMFPACK   = _FakeSolver
    _solver._PARDISO_AVAILABLE = True
    _solver._MUMPS_AVAILABLE   = False
    _solver._CUDSS_AVAILABLE   = False
    _solver._UMFPACK_AVAILABLE = False

    # Link sub-module hierarchy
    _geo.pcb            = _pcb
    _geo.open_region    = _open_reg
    _emerge.geo         = _geo
    _emerge.cs          = _cs
    _emerge.physics     = _physics
    _emerge.solver      = _solver
    _physics.microwave  = _mw
    _mw.touchstone      = _ts
    emerge_mock._emerge = _emerge

    # Register all modules so `from emerge._emerge.geo.pcb import PCBNew` works
    sys.modules.update({
        "emerge":                                         emerge_mock,
        "emerge.beta":                                    beta_mock,
        "emerge.beta.gerber":                             gerber_mock,
        "emerge._emerge":                                 _emerge,
        "emerge._emerge.geo":                             _geo,
        "emerge._emerge.geo.pcb":                         _pcb,
        "emerge._emerge.geo.open_region":                 _open_reg,
        "emerge._emerge.cs":                              _cs,
        "emerge._emerge.physics":                         _physics,
        "emerge._emerge.physics.microwave":               _mw,
        "emerge._emerge.physics.microwave.touchstone":    _ts,
        "emerge._emerge.solver":                          _solver,
    })

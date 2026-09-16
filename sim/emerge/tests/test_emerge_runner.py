"""
Tests for emerge_runner.py — EmergeModelBuilder, EmergeSolver, EmergeReporter,
parse_component_value, and PassiveElementModeler.

All EMerge API calls are satisfied by the mocks installed in conftest.py.
No EMerge installation or KiCad installation required.

Run with:
    pytest sim/emerge/tests/test_emerge_runner.py -v

Author: Author
Version: 1.1.0
"""

import math
import pathlib
import sys
import pytest
import numpy as np
from unittest.mock import patch, MagicMock
from gerber_builder import crop_gerber_to_bbox, load_copper_layers, load_vias_from_drills


class TestGerberCrop:

    def test_keeps_region_crossing_simulation_bounds(self, tmp_path):
        source = tmp_path / "board-F_Cu.gbr"
        cropped = tmp_path / "cropped-F_Cu.gbr"
        source.write_text(
            "%FSLAX46Y46*%\n"
            "X0Y0D02*\n"
            "G36*\n"
            "X-010000Y-010000D02*\n"
            "X030000Y-010000D01*\n"
            "X030000Y030000D01*\n"
            "X-010000Y030000D01*\n"
            "X-010000Y-010000D01*\n"
            "G37*\nM02*\n",
            encoding="utf-8",
        )

        assert crop_gerber_to_bbox(
            source, cropped, 0.0, 0.0, 0.02, 0.02
        )
        result = cropped.read_text(encoding="utf-8")
        assert "G36*" in result
        assert "G37*" in result

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import emerge_runner as er


# ── PCB fixtures ─────────────────────────────────────────────────────────────

MINIMAL_PCB = """\
(kicad_pcb
  (version 20241229)
  (general (thickness 1.6))
  (setup
    (stackup
      (layer "F.Cu" (type "copper") (thickness 0.035))
      (layer "dielectric 1" (type "core") (thickness 1.53)
        (material "FR4") (epsilon_r 4.5) (loss_tangent 0.02))
      (layer "B.Cu" (type "copper") (thickness 0.035))
    )
  )
  (footprint "Lib:SMA"
    (at 50.0 40.0 0)
    (property "Reference" "J1")
    (pad "1" thru_hole circle (at 0 0 0) (size 1.6 1.6))
  )
  (footprint "Lib:SMA"
    (at 80.0 40.0 0)
    (property "Reference" "J2")
    (pad "1" thru_hole circle (at 0 0 0) (size 1.6 1.6))
  )
)
"""

PORT_DEFS = {
    "PORT1": {"pad": "J1:1", "R": 50.0,  "C": None,  "active": True,  "dir": "z"},
    "PORT2": {"pad": "J2:1", "R": 50.0,  "C": None,  "active": False, "dir": "z"},
}

# PCB with R/L/C passives for PassiveElementModeler tests
PASSIVE_PCB = """\
(kicad_pcb
  (version 20241229)
  (general (thickness 1.6))
  (setup
    (stackup
      (layer "F.Cu" (type "copper") (thickness 0.035))
      (layer "dielectric 1" (type "core") (thickness 1.53)
        (material "FR4") (epsilon_r 4.5) (loss_tangent 0.02))
      (layer "B.Cu" (type "copper") (thickness 0.035))
    )
  )
  (footprint "Lib:SMA" (layer "F.Cu")
    (at 50.0 40.0 0)
    (property "Reference" "J1")
    (pad "1" thru_hole circle (at 0 0 0) (size 1.6 1.6))
  )
  (footprint "Lib:SMA" (layer "F.Cu")
    (at 80.0 40.0 0)
    (property "Reference" "J2")
    (pad "1" thru_hole circle (at 0 0 0) (size 1.6 1.6))
  )
  (footprint "Resistor_SMD:R_0402" (layer "F.Cu")
    (at 55.0 40.0 0)
    (property "Reference" "R1")
    (property "Value" "100R")
    (pad "1" smd rect (at -0.5 0 0) (size 0.5 0.5))
    (pad "2" smd rect (at 0.5 0 0) (size 0.5 0.5))
  )
  (footprint "Capacitor_SMD:C_0402" (layer "F.Cu")
    (at 60.0 40.0 0)
    (property "Reference" "C1")
    (property "Value" "100n")
    (pad "1" smd rect (at -0.5 0 0) (size 0.5 0.5))
    (pad "2" smd rect (at 0.5 0 0) (size 0.5 0.5))
  )
  (footprint "Inductor_SMD:L_0402" (layer "B.Cu")
    (at 65.0 40.0 0)
    (property "Reference" "L1")
    (property "Value" "10n")
    (pad "1" smd rect (at -0.5 0 0) (size 0.5 0.5))
    (pad "2" smd rect (at 0.5 0 0) (size 0.5 0.5))
  )
  (footprint "Resistor_SMD:R_0402" (layer "F.Cu")
    (at 70.0 40.0 0)
    (property "Reference" "R2")
    (property "Value" "0R")
    (pad "1" smd rect (at -0.5 0 0) (size 0.5 0.5))
    (pad "2" smd rect (at 0.5 0 0) (size 0.5 0.5))
  )
  (footprint "Resistor_SMD:R_0402" (layer "F.Cu")
    (at 75.0 40.0 0)
    (property "Reference" "R3")
    (property "Value" "DNP")
    (pad "1" smd rect (at -0.5 0 0) (size 0.5 0.5))
    (pad "2" smd rect (at 0.5 0 0) (size 0.5 0.5))
  )
)
"""


@pytest.fixture
def pcb_file(tmp_path):
    f = tmp_path / "board.kicad_pcb"
    f.write_text(MINIMAL_PCB, encoding="utf-8")
    return f


@pytest.fixture
def passive_pcb_file(tmp_path):
    f = tmp_path / "passives.kicad_pcb"
    f.write_text(PASSIVE_PCB, encoding="utf-8")
    return f


# ── module-level state checks ─────────────────────────────────────────────────

class TestModuleState:

    def test_emerge_ok(self):
        assert er._EMERGE_OK is True, (
            f"emerge mock not loaded: {er._EMERGE_ERR}")


class TestGerberLayerIndexMapping:

    def test_filebasedpcb_layer_indices_follow_emerge_convention(self, tmp_path, pcb_file):
        class _FakePCB:
            def __init__(self):
                self.calls = []

            def layer_from_file(self, layer, filename, **kw):
                self.calls.append((layer, pathlib.Path(filename).name))
                return object()

        gerber_dir = tmp_path / "gerbers"
        gerber_dir.mkdir(parents=True, exist_ok=True)

        # Minimal valid Gerber body for loader path.
        gbr_text = "%FSLAX46Y46*%\nG01*\nX000000Y000000D02*\nM02*\n"
        for name in ("F_Cu", "In1_Cu", "In2_Cu", "B_Cu"):
            (gerber_dir / f"{pcb_file.stem}-{name}.gbr").write_text(gbr_text, encoding="utf-8")

        stackup = {
            "layers": [
                {"name": "F.Cu", "type": "copper", "thick": 0.035, "er": None, "tand": None},
                {"name": "In1.Cu", "type": "copper", "thick": 0.035, "er": None, "tand": None},
                {"name": "In2.Cu", "type": "copper", "thick": 0.035, "er": None, "tand": None},
                {"name": "B.Cu", "type": "copper", "thick": 0.035, "er": None, "tand": None},
            ]
        }

        fake = _FakePCB()
        loaded = load_copper_layers(
            pcb=fake,
            stackup=stackup,
            pcb_path=pcb_file,
            gerber_dir=gerber_dir,
            circ_segs=16,
            res_mm=0.2,
            min_seg_um=0.0,
            drop_zero_segments=False,
            simplify_regions=False,
            region_min_seg_um=0.0,
            sim_bounds=None,
            log=None,
        )

        assert loaded == 4
        # FileBasedPCB mapping convention:
        # F.Cu=-1, In1=1, In2=2, B.Cu=0
        assert [c[0] for c in fake.calls] == [-1, 1, 2, 0]

    def test_load_vias_from_drills_calls_vias_from_file(self, tmp_path, pcb_file):
        class _FakePCB:
            def __init__(self):
                self.via_calls = []

            def vias_from_file(self, filename, **kw):
                self.via_calls.append(pathlib.Path(filename).name)

        gerber_dir = tmp_path / "gerbers"
        gerber_dir.mkdir(parents=True, exist_ok=True)

        # Typical KiCad separate TH naming.
        (gerber_dir / f"{pcb_file.stem}-PTH.drl").write_text("M48\nM30\n", encoding="utf-8")
        (gerber_dir / f"{pcb_file.stem}-NPTH.drl").write_text("M48\nM30\n", encoding="utf-8")

        fake = _FakePCB()
        stats = load_vias_from_drills(
            pcb=fake,
            gerber_dir=gerber_dir,
            pcb_path=pcb_file,
            log=None,
        )

        assert stats["loaded_files"] == 2
        assert stats["drill_size_count"] == 0
        assert stats["total_holes"] == 0
        assert fake.via_calls == [f"{pcb_file.stem}-NPTH.drl", f"{pcb_file.stem}-PTH.drl"]

    def test_load_vias_from_drills_filters_to_sim_bounds(self, tmp_path, pcb_file):
        class _FakePCB:
            def __init__(self):
                self.via_calls = []
                self.added = []
                self.vias = []
                self.via_holes = []

            def vias_from_file(self, filename, **kw):
                self.via_calls.append(pathlib.Path(filename).name)

            def add_vias(self, *coords, radius, z1=None, z2=None, segments=6):
                self.added.append({"coords": list(coords), "radius": radius})
                self.vias.extend(coords)

        gerber_dir = tmp_path / "gerbers"
        gerber_dir.mkdir(parents=True, exist_ok=True)

        drl_text = """M48
; #@! TF.FileFunction,Plated,1,4,PTH
METRIC
T1C0.300
%
T1
X10.0Y-10.0
X100.0Y-100.0
M30
"""
        (gerber_dir / f"{pcb_file.stem}-PTH.drl").write_text(drl_text, encoding="utf-8")

        fake = _FakePCB()
        stats = load_vias_from_drills(
            pcb=fake,
            gerber_dir=gerber_dir,
            pcb_path=pcb_file,
            sim_bounds=(0.0, -0.02, 0.02, 0.0),
            log=None,
        )

        assert stats["loaded_files"] == 1
        assert stats["total_holes"] == 1
        assert stats["drill_size_count"] == 1
        assert stats["manual_fallback_holes"] == 1
        assert fake.via_calls == []
        assert len(fake.added) == 1
        assert fake.added[0]["coords"] == [(0.01, -0.01)]

    def test_emerge_version_set(self):
        assert er._EMERGE_VER != ""


# ── EmergeModelBuilder ────────────────────────────────────────────────────────

class TestEmergeModelBuilder:

    def test_run_returns_object_on_valid_pcb(self, pcb_file, tmp_path):
        report = []
        builder = er.EmergeModelBuilder(
            pcb_path=pcb_file,
            gerber_dir=tmp_path,
            port_defs=PORT_DEFS,
            report_lines=report,
            verbose=False,
        )
        result = builder.run()
        assert result is not None

    def test_run_logs_stackup_info(self, pcb_file, tmp_path):
        report = []
        er.EmergeModelBuilder(
            pcb_path=pcb_file, gerber_dir=tmp_path,
            port_defs=PORT_DEFS, report_lines=report, verbose=False,
        ).run()
        full = "\n".join(report)
        assert "er=" in full
        assert "tand=" in full

    def test_run_logs_port_positions(self, pcb_file, tmp_path):
        report = []
        er.EmergeModelBuilder(
            pcb_path=pcb_file, gerber_dir=tmp_path,
            port_defs=PORT_DEFS, report_lines=report, verbose=False,
        ).run()
        full = "\n".join(report)
        assert "PORT1" in full
        assert "PORT2" in full

    def test_run_returns_none_when_emerge_unavailable(self, pcb_file, tmp_path):
        report = []
        with patch.object(er, "_EMERGE_OK", False):
            result = er.EmergeModelBuilder(
                pcb_path=pcb_file, gerber_dir=tmp_path,
                port_defs=PORT_DEFS, report_lines=report, verbose=False,
            ).run()
        assert result is None
        assert any("ERROR" in line for line in report)

    def test_skips_port_with_unknown_pad(self, pcb_file, tmp_path):
        report = []
        bad_ports = {
            "PORT1": {"pad": "J1:1",   "R": 50.0, "active": True,  "dir": "z"},
            "BAD":   {"pad": "X99:1",  "R": 50.0, "active": False, "dir": "z"},
        }
        result = er.EmergeModelBuilder(
            pcb_path=pcb_file, gerber_dir=tmp_path,
            port_defs=bad_ports, report_lines=report, verbose=False,
        ).run()
        # Simulation still built with the one valid port
        assert result is not None
        assert any("WARNING" in line and "X99:1" in line for line in report)

    def test_returns_none_when_no_valid_ports(self, pcb_file, tmp_path):
        report = []
        no_ports = {
            "P": {"pad": "MISSING:1", "R": 50.0, "active": True, "dir": "z"}
        }
        result = er.EmergeModelBuilder(
            pcb_path=pcb_file, gerber_dir=tmp_path,
            port_defs=no_ports, report_lines=report, verbose=False,
        ).run()
        assert result is None

    def test_port_focus_only_takes_precedence_over_outline(self, pcb_file, tmp_path):
        report = []
        with patch.object(er, "read_board_outline", return_value=[(0.0, 0.0), (200.0, 0.0), (200.0, 100.0), (0.0, 100.0)]), \
             patch.object(er, "read_keepout_bbox", return_value=None), \
             patch.object(er, "build_and_commit", return_value=(object(), object())):
            er.EmergeModelBuilder(
                pcb_path=pcb_file,
                gerber_dir=tmp_path,
                port_defs=PORT_DEFS,
                port_focus_only=True,
                port_focus_margin_mm=2.0,
                report_lines=report,
                verbose=False,
            ).run()

        full = "\n".join(report)
        assert "port_focus_only=true" in full
        assert "board outline bbox" not in full


# ── EmergeSolver ──────────────────────────────────────────────────────────────

class TestEmergeSolver:

    def _make_fake_sim(self):
        """Return a fresh FakeSimulation via the conftest mock."""
        import emerge
        return emerge.Simulation("test_board")

    def test_returns_none_when_model_is_none(self, tmp_path):
        report = []
        solver = er.EmergeSolver(
            model=None, output_dir=tmp_path,
            report_lines=report, verbose=False,
        )
        assert solver.run() is None
        assert any("ERROR" in line for line in report)

    def test_calls_set_frequency_range(self, tmp_path):
        sim = self._make_fake_sim()
        calls = []
        sim.mw.set_frequency_range = lambda fmin, fmax, Npoints: calls.append((fmin, fmax, Npoints))
        er.EmergeSolver(
            model=sim, output_dir=tmp_path,
            freq_start=1e6, freq_stop=10e9, freq_steps=51,
            verbose=False,
        ).run()
        assert len(calls) == 1
        fmin, fmax, N = calls[0]
        assert abs(fmin - 1e6)  < 1
        assert abs(fmax - 10e9) < 1
        assert N == 51

    def test_writes_touchstone_file(self, tmp_path):
        sim = self._make_fake_sim()
        ts_dir = tmp_path / "ts"
        result = er.EmergeSolver(
            model=sim, output_dir=ts_dir,
            freq_start=1e6, freq_stop=5e9, freq_steps=3,
            verbose=False,
        ).run()
        assert result is not None
        assert pathlib.Path(result).exists()
        assert pathlib.Path(result).suffix == ".s2p"

    def test_logs_sweep_parameters(self, tmp_path):
        sim = self._make_fake_sim()
        report = []
        er.EmergeSolver(
            model=sim, output_dir=tmp_path,
            freq_start=100e6, freq_stop=6e9, freq_steps=21,
            report_lines=report, verbose=False,
        ).run()
        full = "\n".join(report)
        assert "100" in full    # 100 MHz start
        assert "6" in full      # 6 GHz stop


# ── EmergeReporter ────────────────────────────────────────────────────────────

class TestEmergeReporter:

    def test_missing_file_returns_negative(self, tmp_path):
        report = []
        r = er.EmergeReporter(
            touchstone_path=tmp_path / "missing.s2p",
            report_lines=report, verbose=False,
        ).run()
        assert r == -1
        assert any("ERROR" in line for line in report)

    def test_passes_with_low_loss_data(self, tmp_path):
        """TouchstoneData mock returns S11=-20dB, S21=-0.9dB — both within defaults."""
        ts = tmp_path / "result.s2p"
        ts.write_text("! stub")   # file just needs to exist; mock provides data
        report = []
        violations = er.EmergeReporter(
            touchstone_path=ts,
            il_threshold_db=3.0,
            rl_threshold_db=10.0,
            report_lines=report, verbose=False,
        ).run()
        assert violations == 0

    def test_detects_tight_il_threshold(self, tmp_path):
        ts = tmp_path / "result.s2p"
        ts.write_text("! stub")
        report = []
        violations = er.EmergeReporter(
            touchstone_path=ts,
            il_threshold_db=0.1,   # 0.1 dB limit — mock S21≈-0.9dB fails
            rl_threshold_db=100.0, # ignore RL
            report_lines=report, verbose=False,
        ).run()
        assert violations > 0

    def test_detects_tight_rl_threshold(self, tmp_path):
        ts = tmp_path / "result.s2p"
        ts.write_text("! stub")
        report = []
        violations = er.EmergeReporter(
            touchstone_path=ts,
            il_threshold_db=100.0,  # ignore IL
            rl_threshold_db=30.0,   # 30 dB min — mock S11≈-20dB fails
            report_lines=report, verbose=False,
        ).run()
        assert violations > 0

    def test_report_lines_include_summary(self, tmp_path):
        ts = tmp_path / "result.s2p"
        ts.write_text("! stub")
        report = []
        er.EmergeReporter(
            touchstone_path=ts, report_lines=report, verbose=False,
        ).run()
        full = "\n".join(report)
        assert "S21" in full or "S11" in full


# ── Full end-to-end (builder → solver → reporter) ────────────────────────────

class TestEndToEnd:

    def test_builder_solver_reporter_chain(self, pcb_file, tmp_path):
        report = []

        model = er.EmergeModelBuilder(
            pcb_path=pcb_file, gerber_dir=tmp_path,
            port_defs=PORT_DEFS, report_lines=report, verbose=False,
        ).run()
        assert model is not None

        ts_path = er.EmergeSolver(
            model=model, output_dir=tmp_path / "ts",
            freq_start=1e6, freq_stop=5e9, freq_steps=3,
            report_lines=report, verbose=False,
        ).run()
        assert ts_path is not None

        violations = er.EmergeReporter(
            touchstone_path=ts_path,
            il_threshold_db=3.0, rl_threshold_db=10.0,
            report_lines=report, verbose=False,
        ).run()
        assert violations == 0

        # Report log has content from all three stages
        full = "\n".join(report)
        assert "Stackup" in full
        assert "Sweep" in full
        assert "Touchstone" in full


# ── parse_component_value ─────────────────────────────────────────────────────

class TestParseComponentValue:
    """Covers EIA notation, edge cases, and skip conditions."""

    # -- resistors --

    def test_plain_integer_ohms(self):
        assert er.parse_component_value("R1", "100") == ("R", 100.0)

    def test_plain_float_ohms(self):
        assert er.parse_component_value("R1", "4.7") == ("R", pytest.approx(4.7))

    def test_trailing_r_unit(self):
        """100R → 100 Ω"""
        assert er.parse_component_value("R1", "100R") == ("R", 100.0)

    def test_r_as_decimal_separator(self):
        """4R7 → 4.7 Ω"""
        t, v = er.parse_component_value("R1", "4R7")
        assert t == "R"
        assert abs(v - 4.7) < 0.01

    def test_kilo_ohms_suffix(self):
        assert er.parse_component_value("R5", "4.7k") == ("R", pytest.approx(4700.0))

    def test_eia_split_kilo(self):
        """4k7 → 4700 Ω"""
        t, v = er.parse_component_value("R1", "4k7")
        assert t == "R"
        assert abs(v - 4700.0) < 1.0

    def test_mega_ohms(self):
        assert er.parse_component_value("R1", "1M") == ("R", pytest.approx(1e6))

    def test_omega_symbol_normalised(self):
        """Ω → R before parsing."""
        assert er.parse_component_value("R1", "100Ω") == ("R", 100.0)

    def test_zero_ohm_jumper_returns_none(self):
        assert er.parse_component_value("R5", "0R") is None

    def test_zero_ohm_0r0_returns_none(self):
        assert er.parse_component_value("R5", "0R0") is None

    def test_open_circuit_returns_none(self):
        """Resistors > 10 MΩ are treated as open."""
        assert er.parse_component_value("R1", "100M") is None

    # -- capacitors --

    def test_nanofarad(self):
        assert er.parse_component_value("C1", "100n") == ("C", pytest.approx(100e-9))

    def test_picofarad(self):
        assert er.parse_component_value("C2", "4.7p") == ("C", pytest.approx(4.7e-12))

    def test_microfarad(self):
        assert er.parse_component_value("C3", "10u") == ("C", pytest.approx(10e-6))

    def test_voltage_rating_stripped(self):
        """10uF/16V → 10 µF"""
        t, v = er.parse_component_value("C1", "10uF/16V")
        assert t == "C"
        assert abs(v - 10e-6) < 1e-9

    def test_eia_split_pico(self):
        """2p2 → 2.2 pF"""
        t, v = er.parse_component_value("C1", "2p2")
        assert t == "C"
        assert abs(v - 2.2e-12) < 1e-14

    # -- inductors --

    def test_nanohenry(self):
        assert er.parse_component_value("L1", "10n") == ("L", pytest.approx(10e-9))

    def test_microhenry(self):
        assert er.parse_component_value("L2", "4.7u") == ("L", pytest.approx(4.7e-6))

    def test_eia_split_nano(self):
        """4n7 → 4.7 nH"""
        t, v = er.parse_component_value("L1", "4n7")
        assert t == "L"
        assert abs(v - 4.7e-9) < 1e-12

    # -- skip / invalid --

    def test_dnp_returns_none(self):
        assert er.parse_component_value("R1", "DNP") is None

    def test_nc_returns_none(self):
        assert er.parse_component_value("C1", "NC") is None

    def test_empty_string_returns_none(self):
        assert er.parse_component_value("R1", "") is None

    def test_non_rlc_ref_returns_none(self):
        """Reference not starting with R/L/C is rejected."""
        assert er.parse_component_value("U1", "100") is None

    def test_type_char_matches_ref_prefix(self):
        t, _ = er.parse_component_value("C3", "10n")
        assert t == "C"

    def test_whitespace_stripped(self):
        assert er.parse_component_value("R1", "  47  ") == ("R", 47.0)


# ── PassiveElementModeler ─────────────────────────────────────────────────────

class TestPassiveElementModeler:

    def _make_pcb_obj(self):
        """Return a mock PCB object that records add_poly calls."""
        class MockPCB:
            def __init__(self):
                self.add_poly_calls = []
            def add_poly(self, xs, ys, z=0, material=None, name=None):
                self.add_poly_calls.append({
                    "name": name, "z": z,
                    "material": material, "xs": xs, "ys": ys,
                })
        return MockPCB()

    def _make_path_pcb_obj(self):
        """Return a mock PCB object that records stripline-style path calls."""
        class MockPath:
            def __init__(self, pcb, name):
                self.pcb = pcb
                self.name = name
                self.calls = []
            def straight(self, length):
                self.calls.append(("straight", length))
                return self
            def lumped_element(self, z_func, size=None):
                self.calls.append(("lumped_element", size))
                self.pcb.path_calls.append({"name": self.name, "size": size, "calls": self.calls})
                return self
            def prio_set(self, value):
                self.calls.append(("prio_set", value))
                return self

        class MockPCB:
            def __init__(self):
                self.add_poly_calls = []
                self.path_calls = []
            def new(self, x, y, width, direction, z=None):
                return MockPath(self, f"path@{x:.3f},{y:.3f}")
            def compile_paths(self, merge=True):
                self.path_calls.append({"compiled": merge})
                return [object()]
            def add_poly(self, xs, ys, z=0, material=None, name=None):
                self.add_poly_calls.append({
                    "name": name, "z": z,
                    "material": material, "xs": xs, "ys": ys,
                })
        return MockPCB()

    def _make_stackup(self):
        return {
            "board_thickness_mm":  1.6,
            "copper_thickness_mm": 0.035,
            "er": 4.5, "tand": 0.02,
            "copper_layers": 2, "layers": [],
        }

    def test_returns_count_of_modeled_elements(self, passive_pcb_file):
        pcb_obj  = self._make_pcb_obj()
        stackup  = self._make_stackup()
        count = er.PassiveElementModeler(
            pcb_obj=pcb_obj, pcb_path=passive_pcb_file, stackup=stackup,
        ).run()
        # R1(100R valid), C1(100n valid), L1(10n valid) — 3 modelled
        # R2(0R skip), R3(DNP skip)
        assert count == 3

    def test_add_poly_called_for_each_element(self, passive_pcb_file):
        pcb_obj  = self._make_pcb_obj()
        stackup  = self._make_stackup()
        er.PassiveElementModeler(
            pcb_obj=pcb_obj, pcb_path=passive_pcb_file, stackup=stackup,
        ).run()
        names = {c["name"] for c in pcb_obj.add_poly_calls}
        assert "R1" in names
        assert "C1" in names
        assert "L1" in names

    def test_zero_ohm_not_added(self, passive_pcb_file):
        pcb_obj  = self._make_pcb_obj()
        stackup  = self._make_stackup()
        er.PassiveElementModeler(
            pcb_obj=pcb_obj, pcb_path=passive_pcb_file, stackup=stackup,
        ).run()
        names = {c["name"] for c in pcb_obj.add_poly_calls}
        assert "R2" not in names

    def test_dnp_not_added(self, passive_pcb_file):
        pcb_obj  = self._make_pcb_obj()
        stackup  = self._make_stackup()
        er.PassiveElementModeler(
            pcb_obj=pcb_obj, pcb_path=passive_pcb_file, stackup=stackup,
        ).run()
        names = {c["name"] for c in pcb_obj.add_poly_calls}
        assert "R3" not in names

    def test_skip_list_respected(self, passive_pcb_file):
        pcb_obj  = self._make_pcb_obj()
        stackup  = self._make_stackup()
        count = er.PassiveElementModeler(
            pcb_obj=pcb_obj, pcb_path=passive_pcb_file, stackup=stackup,
            skip_refs=["R1", "C1"],
        ).run()
        names = {c["name"] for c in pcb_obj.add_poly_calls}
        assert "R1" not in names
        assert "C1" not in names
        assert "L1" in names
        assert count == 1

    def test_top_layer_z_equals_board_thickness(self, passive_pcb_file):
        """R1 is on F.Cu — z should equal board_thickness_mm * 1e-3."""
        pcb_obj  = self._make_pcb_obj()
        stackup  = self._make_stackup()
        er.PassiveElementModeler(
            pcb_obj=pcb_obj, pcb_path=passive_pcb_file, stackup=stackup,
        ).run()
        r1_call = next(c for c in pcb_obj.add_poly_calls if c["name"] == "R1")
        expected_z = stackup["board_thickness_mm"] * 1e-3
        assert abs(r1_call["z"] - expected_z) < 1e-9

    def test_bottom_layer_z_equals_copper_thickness(self, passive_pcb_file):
        """L1 is on B.Cu — z should equal copper_thickness_mm * 1e-3."""
        pcb_obj  = self._make_pcb_obj()
        stackup  = self._make_stackup()
        er.PassiveElementModeler(
            pcb_obj=pcb_obj, pcb_path=passive_pcb_file, stackup=stackup,
        ).run()
        l1_call = next(c for c in pcb_obj.add_poly_calls if c["name"] == "L1")
        expected_z = stackup["copper_thickness_mm"] * 1e-3
        assert abs(l1_call["z"] - expected_z) < 1e-9

    def test_rectangle_has_four_corners(self, passive_pcb_file):
        pcb_obj  = self._make_pcb_obj()
        stackup  = self._make_stackup()
        er.PassiveElementModeler(
            pcb_obj=pcb_obj, pcb_path=passive_pcb_file, stackup=stackup,
        ).run()
        r1_call = next(c for c in pcb_obj.add_poly_calls if c["name"] == "R1")
        assert len(r1_call["xs"]) == 4
        assert len(r1_call["ys"]) == 4

    def test_report_lines_populated(self, passive_pcb_file):
        pcb_obj  = self._make_pcb_obj()
        stackup  = self._make_stackup()
        report   = []
        er.PassiveElementModeler(
            pcb_obj=pcb_obj, pcb_path=passive_pcb_file, stackup=stackup,
            report_lines=report,
        ).run()
        full = "\n".join(report)
        assert "R1" in full
        assert "Passives:" in full

    def test_empty_pcb_returns_zero(self, pcb_file):
        """MINIMAL_PCB has no R/L/C components."""
        pcb_obj  = self._make_pcb_obj()
        stackup  = self._make_stackup()
        count = er.PassiveElementModeler(
            pcb_obj=pcb_obj, pcb_path=pcb_file, stackup=stackup,
        ).run()
        assert count == 0
        assert pcb_obj.add_poly_calls == []

    def test_outline_filters_outside_components(self, passive_pcb_file):
        """
        Board outline that only covers R1 area (54–56, 39–41) mm.
        C1(60,40), L1(65,40) are outside → only R1 should be modeled.
        R2(70,40) is outside too but would be skipped anyway (0R).
        R3(75,40) is outside and DNP.
        """
        pcb_obj = self._make_pcb_obj()
        stackup = self._make_stackup()
        # Tight outline: only contains R1 midpoint (55, 40)
        outline = [(53.0, 38.0), (57.0, 38.0), (57.0, 42.0), (53.0, 42.0)]
        count = er.PassiveElementModeler(
            pcb_obj=pcb_obj, pcb_path=passive_pcb_file, stackup=stackup,
            outline_pts=outline,
        ).run()
        names = {c["name"] for c in pcb_obj.add_poly_calls}
        assert "R1" in names
        assert "C1" not in names
        assert "L1" not in names
        assert count == 1

    def test_no_outline_includes_all(self, passive_pcb_file):
        """outline_pts=[] means no filtering — all valid components modeled."""
        pcb_obj = self._make_pcb_obj()
        stackup = self._make_stackup()
        count = er.PassiveElementModeler(
            pcb_obj=pcb_obj, pcb_path=passive_pcb_file, stackup=stackup,
            outline_pts=[],
        ).run()
        assert count == 3   # R1, C1, L1 (R2=0R and R3=DNP still skipped)

    def test_path_model_used_when_available(self, passive_pcb_file):
        """When the PCB API exposes path routing, use the stripline-style model."""
        pcb_obj = self._make_path_pcb_obj()
        stackup = self._make_stackup()
        count = er.PassiveElementModeler(
            pcb_obj=pcb_obj, pcb_path=passive_pcb_file, stackup=stackup,
        ).run()
        assert count == 3
        assert pcb_obj.add_poly_calls == []
        assert any(entry.get("compiled") for entry in pcb_obj.path_calls if isinstance(entry, dict))
        assert any(call.get("name", "").startswith("path@") for call in pcb_obj.path_calls if isinstance(call, dict) and "name" in call)
        r1_path = next(call for call in pcb_obj.path_calls if isinstance(call, dict) and call.get("name", "").startswith("path@"))
        straight_calls = [c for c in r1_path["calls"] if c[0] == "straight"]
        lumped_calls = [c for c in r1_path["calls"] if c[0] == "lumped_element"]
        assert len(straight_calls) == 2
        assert len(lumped_calls) == 1
        assert straight_calls[0][1] < 1.0e-3
        assert lumped_calls[0][1][0] < 1.0e-3

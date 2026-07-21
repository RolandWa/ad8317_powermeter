"""
Tests for emerge_pipeline.py — GerberExporter, EmergeModelBuilder, EmergeReporter.

Run with:
    pytest sim/emerge/tests/test_pipeline.py -v

EMerge and KiCad are mocked — tests do not require either to be installed.

Author: Author
Version: 1.0.0
Last Updated: 2026-07-20
"""

import math
import sys
import pathlib
import pytest
from unittest.mock import MagicMock, patch, mock_open

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import emerge_pipeline as ep


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

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
  (footprint "Lib:LFCSP8"
    (at 60.0 40.0 0)
    (property "Reference" "U2")
    (pad "1" smd rect (at 0.5 0 0) (size 0.6 0.5))
  )
)
"""

SAMPLE_S2P = """\
! 2-port Touchstone
# GHz S MA R 50
! Freq    S11_mag  S11_ang  S21_mag  S21_ang  S12_mag  S12_ang  S22_mag  S22_ang
1.0    0.1  -10.0  0.95   -5.0   0.95  -5.0   0.1  -10.0
5.0    0.2  -20.0  0.85  -10.0   0.85 -10.0   0.2  -20.0
10.0   0.4  -40.0  0.65  -20.0   0.65 -20.0   0.4  -40.0
"""

PORT_DEFS_TEST = {
    "PORT1": {"pad": "J1:1", "R": 50.0,  "C": None,  "active": True,  "dir": "z"},
    "PORT2": {"pad": "U2:1", "R": 200.0, "C": 2e-12, "active": False, "dir": "z"},
}


@pytest.fixture
def pcb_file(tmp_path):
    f = tmp_path / "board.kicad_pcb"
    f.write_text(MINIMAL_PCB, encoding="utf-8")
    return f


@pytest.fixture
def s2p_file(tmp_path):
    f = tmp_path / "result.s2p"
    f.write_text(SAMPLE_S2P, encoding="utf-8")
    return f


# --------------------------------------------------------------------------- #
# GerberExporter
# --------------------------------------------------------------------------- #

class TestGerberExporter:

    def test_missing_kicad_cli_logs_error(self, pcb_file, tmp_path):
        report = []
        exp = ep.GerberExporter(
            pcb_path=pcb_file,
            output_dir=tmp_path / "gerbers",
            kicad_cli=tmp_path / "nonexistent_cli.exe",
            report_lines=report,
            verbose=False,
        )
        result = exp.run()
        assert result == []
        assert any("ERROR" in line for line in report)

    def test_successful_export_returns_file_list(self, pcb_file, tmp_path):
        report = []
        gerber_dir = tmp_path / "gerbers"
        gerber_dir.mkdir()
        # Create fake output files to simulate CLI success
        (gerber_dir / "board-F_Cu.gbr").write_text("", encoding="utf-8")
        (gerber_dir / "board.drl").write_text("", encoding="utf-8")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            exp = ep.GerberExporter(
                pcb_path=pcb_file,
                output_dir=gerber_dir,
                kicad_cli=pathlib.Path(__file__),   # any existing file as fake CLI
                report_lines=report,
                verbose=False,
            )
            result = exp.run()

        assert len(result) > 0


# --------------------------------------------------------------------------- #
# EmergeModelBuilder
# --------------------------------------------------------------------------- #

class TestEmergeModelBuilder:

    def test_no_emerge_returns_none(self, pcb_file, tmp_path):
        report = []
        with patch.object(ep, "HAS_EMERGE", False):
            builder = ep.EmergeModelBuilder(
                pcb_path=pcb_file,
                gerber_dir=tmp_path,
                port_defs=PORT_DEFS_TEST,
                report_lines=report,
                verbose=False,
            )
            result = builder.run()
        assert result is None
        assert any("ERROR" in line for line in report)

    def test_resolve_port_position_known_pad(self, pcb_file, tmp_path):
        builder = ep.EmergeModelBuilder(
            pcb_path=pcb_file,
            gerber_dir=tmp_path,
            port_defs=PORT_DEFS_TEST,
            verbose=False,
        )
        builder.pad_map = {"J1:1": (50.0, 40.0)}
        x, y = builder._resolve_port_position("J1:1")
        assert abs(x - 50.0) < 0.001
        assert abs(y - 40.0) < 0.001

    def test_resolve_port_position_unknown_pad_raises(self, pcb_file, tmp_path):
        builder = ep.EmergeModelBuilder(
            pcb_path=pcb_file,
            gerber_dir=tmp_path,
            port_defs=PORT_DEFS_TEST,
            verbose=False,
        )
        builder.pad_map = {"J1:1": (50.0, 40.0)}
        with pytest.raises(KeyError):
            builder._resolve_port_position("J1:99")

    def test_stackup_read_from_pcb(self, pcb_file, tmp_path):
        builder = ep.EmergeModelBuilder(
            pcb_path=pcb_file,
            gerber_dir=tmp_path,
            port_defs=PORT_DEFS_TEST,
            verbose=False,
        )
        builder.stackup = ep._read_stackup(pcb_file)
        assert builder.stackup["er"] == 4.5
        assert builder.stackup["copper_layers"] == 2


# --------------------------------------------------------------------------- #
# EmergeReporter
# --------------------------------------------------------------------------- #

class TestEmergeReporter:

    def test_missing_file_returns_error(self, tmp_path):
        report = []
        reporter = ep.EmergeReporter(
            touchstone_path=tmp_path / "missing.s2p",
            report_lines=report,
            verbose=False,
        )
        result = reporter.run()
        assert result == 1
        assert any("ERROR" in line for line in report)

    def test_parses_s2p_and_passes_thresholds(self, s2p_file):
        report = []
        reporter = ep.EmergeReporter(
            touchstone_path=s2p_file,
            report_lines=report,
            verbose=False,
            il_threshold_db=6.0,     # generous — sample data has ~3.7 dB IL
            rl_threshold_db=5.0,     # generous — sample data has ~14 dB RL
        )
        violations = reporter.run()
        assert violations == 0

    def test_detects_insertion_loss_violation(self, s2p_file):
        report = []
        reporter = ep.EmergeReporter(
            touchstone_path=s2p_file,
            report_lines=report,
            verbose=False,
            il_threshold_db=1.0,    # tight — all points will fail
            rl_threshold_db=100.0,  # ignore RL
        )
        violations = reporter.run()
        assert violations > 0
        assert any("FAIL IL" in line for line in report)

    def test_detects_return_loss_violation(self, s2p_file):
        report = []
        reporter = ep.EmergeReporter(
            touchstone_path=s2p_file,
            report_lines=report,
            verbose=False,
            il_threshold_db=100.0,   # ignore IL
            rl_threshold_db=30.0,    # tight — all points will fail
        )
        violations = reporter.run()
        assert violations > 0
        assert any("FAIL RL" in line for line in report)

    def test_report_contains_frequency_range(self, s2p_file):
        report = []
        ep.EmergeReporter(
            touchstone_path=s2p_file, report_lines=report,
            verbose=False).run()
        full = "\n".join(report)
        assert "GHz" in full or "MHz" in full

    def test_s2p_parse_returns_correct_row_count(self, s2p_file):
        reporter = ep.EmergeReporter(touchstone_path=s2p_file, verbose=False)
        rows = reporter._parse_s2p()
        assert len(rows) == 3   # three data lines in SAMPLE_S2P

    def test_s2p_parse_first_row_frequency(self, s2p_file):
        reporter = ep.EmergeReporter(touchstone_path=s2p_file, verbose=False)
        rows = reporter._parse_s2p()
        f_hz, _, _ = rows[0]
        assert abs(f_hz - 1e9) < 1e6    # 1 GHz

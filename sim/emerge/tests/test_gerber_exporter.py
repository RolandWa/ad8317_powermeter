"""
Tests for gerber_exporter.py — GerberExporter class.

Direct tests for the canonical GerberExporter (the single source used by both
emerge_plugin.py and emerge_pipeline.py).

Run with:
    pytest sim/emerge/tests/test_gerber_exporter.py -v

KiCad installation not required — subprocess.run is mocked.

Author: Author
Version: 1.0.0
"""

import pathlib
import sys
import pytest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
from gerber_exporter import GerberExporter, _find_kicad_cli


# ── _find_kicad_cli ───────────────────────────────────────────────────────────

class TestFindKicadCli:

    def test_explicit_existing_path_returned(self, tmp_path):
        fake_cli = tmp_path / "kicad-cli.exe"
        fake_cli.write_text("")
        assert _find_kicad_cli(str(fake_cli)) == str(fake_cli)

    def test_explicit_missing_path_returns_none(self, tmp_path):
        result = _find_kicad_cli(str(tmp_path / "nonexistent.exe"))
        assert result is None

    def test_explicit_path_object_works(self, tmp_path):
        fake_cli = tmp_path / "kicad-cli.exe"
        fake_cli.write_text("")
        assert _find_kicad_cli(fake_cli) == str(fake_cli)

    def test_empty_override_falls_through_to_candidates(self):
        # With empty override, _find_kicad_cli returns something or None —
        # just verify it doesn't raise.
        result = _find_kicad_cli("")
        assert result is None or isinstance(result, str)


# ── GerberExporter constructor ────────────────────────────────────────────────

class TestGerberExporterInit:

    def test_explicit_missing_cli_stores_none(self, tmp_path):
        exp = GerberExporter(
            pcb_path=tmp_path / "board.kicad_pcb",
            output_dir=tmp_path / "out",
            kicad_cli=tmp_path / "no_cli.exe",
        )
        assert exp.kicad_cli is None

    def test_explicit_existing_cli_stored(self, tmp_path):
        fake_cli = tmp_path / "kicad-cli.exe"
        fake_cli.write_text("")
        exp = GerberExporter(
            pcb_path=tmp_path / "board.kicad_pcb",
            output_dir=tmp_path / "out",
            kicad_cli=fake_cli,
        )
        assert exp.kicad_cli == str(fake_cli)

    def test_report_lines_default_empty(self, tmp_path):
        exp = GerberExporter(
            pcb_path=tmp_path / "board.kicad_pcb",
            output_dir=tmp_path / "out",
        )
        assert exp.report_lines == []


# ── GerberExporter.run() ─────────────────────────────────────────────────────

class TestGerberExporterRun:

    def test_missing_pcb_returns_empty_list(self, tmp_path):
        report = []
        exp = GerberExporter(
            pcb_path=tmp_path / "missing_board.kicad_pcb",
            output_dir=tmp_path / "out",
            report_lines=report,
            verbose=False,
        )
        result = exp.run()
        assert result == []
        assert any("ERROR" in line for line in report)

    def test_missing_kicad_cli_returns_empty_list(self, tmp_path):
        pcb = tmp_path / "board.kicad_pcb"
        pcb.write_text("(kicad_pcb)")
        report = []
        exp = GerberExporter(
            pcb_path=pcb,
            output_dir=tmp_path / "out",
            kicad_cli=tmp_path / "no_cli.exe",
            report_lines=report,
            verbose=False,
        )
        result = exp.run()
        assert result == []
        assert any("ERROR" in line for line in report)

    def test_cli_error_returncode_returns_empty_list(self, tmp_path):
        pcb = tmp_path / "board.kicad_pcb"
        pcb.write_text("(kicad_pcb)")
        fake_cli = tmp_path / "kicad-cli.exe"
        fake_cli.write_text("")
        report = []
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1, stdout="", stderr="kicad-cli error")
            exp = GerberExporter(
                pcb_path=pcb,
                output_dir=tmp_path / "out",
                kicad_cli=fake_cli,
                report_lines=report,
                verbose=False,
            )
            result = exp.run()
        assert result == []
        assert any("ERROR" in line for line in report)

    def test_successful_run_returns_file_list(self, tmp_path):
        pcb = tmp_path / "board.kicad_pcb"
        pcb.write_text("(kicad_pcb)")
        fake_cli = tmp_path / "kicad-cli.exe"
        fake_cli.write_text("")
        gerber_dir = tmp_path / "gerbers"
        gerber_dir.mkdir()
        (gerber_dir / "board-F_Cu.gbr").write_text("")
        (gerber_dir / "board-B_Cu.gbr").write_text("")
        (gerber_dir / "board.drl").write_text("")
        report = []
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            exp = GerberExporter(
                pcb_path=pcb,
                output_dir=gerber_dir,
                kicad_cli=fake_cli,
                report_lines=report,
                verbose=False,
            )
            result = exp.run()
        assert len(result) >= 2
        assert all(isinstance(p, pathlib.Path) for p in result)

    def test_return_value_truthy_on_success(self, tmp_path):
        pcb = tmp_path / "board.kicad_pcb"
        pcb.write_text("(kicad_pcb)")
        fake_cli = tmp_path / "kicad-cli.exe"
        fake_cli.write_text("")
        gerber_dir = tmp_path / "gerbers"
        gerber_dir.mkdir()
        (gerber_dir / "board-F_Cu.gbr").write_text("")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            exp = GerberExporter(
                pcb_path=pcb, output_dir=gerber_dir, kicad_cli=fake_cli,
                verbose=False)
            assert exp.run()   # truthy — works as bool check in emerge_plugin.py

    def test_return_value_falsy_on_failure(self, tmp_path):
        pcb = tmp_path / "board.kicad_pcb"
        pcb.write_text("(kicad_pcb)")
        exp = GerberExporter(
            pcb_path=pcb,
            output_dir=tmp_path / "out",
            kicad_cli=tmp_path / "no_cli.exe",
            verbose=False,
        )
        assert not exp.run()   # falsy — if not exporter.run(): works

    def test_report_lines_populated_on_run(self, tmp_path):
        pcb = tmp_path / "board.kicad_pcb"
        pcb.write_text("(kicad_pcb)")
        fake_cli = tmp_path / "kicad-cli.exe"
        fake_cli.write_text("")
        gerber_dir = tmp_path / "gerbers"
        gerber_dir.mkdir()
        (gerber_dir / "board-F_Cu.gbr").write_text("")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            exp = GerberExporter(pcb_path=pcb, output_dir=gerber_dir,
                                 kicad_cli=fake_cli, verbose=False)
            exp.run()
        assert len(exp.report_lines) > 0

"""
Tests for python_bridge.py.
"""

import pathlib
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
from python_bridge import install_emerge_into_kicad, _iter_kicad_python_candidates


class TestKiCadPythonDiscovery:

    def test_candidate_order_prefers_10_before_9(self, tmp_path):
        root = tmp_path / "KiCad"
        py10 = root / "10.0" / "bin" / "python.exe"
        py9 = root / "9.0" / "bin" / "python.exe"
        py10.parent.mkdir(parents=True)
        py9.parent.mkdir(parents=True)
        py10.write_text("")
        py9.write_text("")

        candidates = list(_iter_kicad_python_candidates(root))

        assert candidates[:2] == [str(py10), str(py9)]

    def test_install_uses_10_when_present(self, tmp_path):
        root = tmp_path / "KiCad"
        py10 = root / "10.0" / "bin" / "python.exe"
        py9 = root / "9.0" / "bin" / "python.exe"
        py10.parent.mkdir(parents=True)
        py9.parent.mkdir(parents=True)
        py10.write_text("")
        py9.write_text("")

        with patch("subprocess.run") as mock_run, patch(
            "python_bridge._has_emerge",
            side_effect=lambda exe: "2.8.0" if str(py10) == exe else None,
        ):
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            ok, msg = install_emerge_into_kicad(root)

        assert ok is True
        assert "10.0" in mock_run.call_args[0][0][0]
        assert "installed" in msg

    def test_install_falls_back_to_9_when_10_missing(self, tmp_path):
        root = tmp_path / "KiCad"
        py9 = root / "9.0" / "bin" / "python.exe"
        py9.parent.mkdir(parents=True)
        py9.write_text("")

        with patch("subprocess.run") as mock_run, patch(
            "python_bridge._has_emerge",
            side_effect=lambda exe: "2.8.0" if str(py9) == exe else None,
        ):
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            ok, msg = install_emerge_into_kicad(root)

        assert ok is True
        assert "9.0" in mock_run.call_args[0][0][0]
        assert "installed" in msg

"""
python_bridge.py — Locate the system Python interpreter that has EMerge installed.

Called by emerge_plugin._find_emerge_python() (step 2 of the search order).
Returns the full path to a Python executable where `import emerge` succeeds,
or None if none is found.

Architecture note
-----------------
KiCad ships its own bundled Python (e.g. C:\\Program Files\\KiCad\\9.0\\bin\\python.exe).
EMerge is installed into the *system* Python (e.g. Python 3.12 in AppData).
The subprocess bridge in emerge_plugin.py spawns the system Python to run
emerge_runner.py, so KiCad never needs EMerge in its own interpreter.

Search order
------------
1. Well-known Windows user-install path  (AppData\\Local\\Programs\\Python\\*)
2. PATH entries (python, python3, python3.12, python3.11)
3. Common system-wide install paths      (C:\\Python3*, C:\\Program Files\\Python*)
"""

import os
import pathlib
import shutil
import subprocess
import sys


def _test(exe: str) -> bool:
    """Return True if *exe* can import emerge."""
    try:
        r = subprocess.run(
            [exe, "-c", "import emerge"],
            capture_output=True, timeout=10,
        )
        return r.returncode == 0
    except Exception:
        return False


def find_emerge_python() -> str | None:
    """
    Return the path of the Python interpreter that has emerge installed,
    or None if it cannot be found.
    """
    candidates: list[str] = []

    # ── 1. Windows AppData user installs (Python 3.12, 3.11, 3.10 …) ──────────
    appdata = os.environ.get("LOCALAPPDATA", "")
    if appdata:
        py_base = pathlib.Path(appdata) / "Programs" / "Python"
        if py_base.is_dir():
            for sub in sorted(py_base.iterdir(), reverse=True):  # newest first
                exe = sub / "python.exe"
                if exe.is_file():
                    candidates.append(str(exe))

    # ── 2. PATH: common names ──────────────────────────────────────────────────
    for name in ("python3.12", "python3.11", "python3.10", "python3", "python"):
        found = shutil.which(name)
        if found and found not in candidates:
            candidates.append(found)

    # ── 3. Common system-wide paths ────────────────────────────────────────────
    for pattern_root in (r"C:\Python3", r"C:\Program Files\Python"):
        root = pathlib.Path(pattern_root).parent
        glob_prefix = pathlib.Path(pattern_root).name
        if root.is_dir():
            for sub in sorted(root.glob(f"{glob_prefix}*"), reverse=True):
                exe = sub / "python.exe"
                if exe.is_file() and str(exe) not in candidates:
                    candidates.append(str(exe))

    # ── Skip KiCad's own Python — it never has EMerge ────────────────────────
    kicad_py = pathlib.Path(sys.executable).resolve()

    for exe in candidates:
        if pathlib.Path(exe).resolve() == kicad_py:
            continue
        if _test(exe):
            return exe

    return None

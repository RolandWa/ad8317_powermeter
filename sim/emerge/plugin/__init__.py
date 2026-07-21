"""
EMerge FEM Simulation Plugin for KiCad PCB Editor.

KiCad's LoadPlugins() imports this package via __init__.py.
The EmergePlugin().register() call at the bottom of
emerge_plugin.py registers the toolbar button automatically.
"""
import os
import sys
import traceback

# Ensure the package directory is on sys.path so sibling modules resolve.
_pkg_dir = os.path.dirname(os.path.abspath(__file__))
if _pkg_dir not in sys.path:
    sys.path.insert(0, _pkg_dir)

try:
    from emerge_plugin import EmergePlugin  # noqa: F401 (side-effect import)
except Exception as _e:
    # Print full traceback to KiCad's scripting console so the error is visible.
    print(f"[EMerge] Plugin load failed: {_e}")
    traceback.print_exc()

"""fix_kicad_xml_for_aedt.py — sanitize KiCad IPC-2581 XML exports for AEDT import.

Problem: KiCad's IPC-2581 exporter adds informational <Property text="Type : ..."/>
and <Property text="Color : ..."/> elements inside <General type="MATERIAL"> blocks.
AEDT (SIwave / HFSS 3D Layout) treats every <Property text> value as a material name
lookup, so the embedded ':' triggers "invalid character" errors and library lookup
failures for strings like "prepreg", "core", "Top Solder Mask", etc.

Fix: strip all Property elements whose text matches the pattern "Key : Value" (i.e.
contains ' : ') from inside MATERIAL General blocks. The first Property (e.g. "FR4",
"SOLDERMASK", "COPPER") is the material name and is left untouched.

Usage:
    python sim/fix_kicad_xml_for_aedt.py                # fixes both default paths
    python sim/fix_kicad_xml_for_aedt.py path/to/file.xml ...
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

_DEFAULT_TARGETS = [
    "kicad/ad8317_powermeter.xml",
    "kicad/FEM/ad8317_powermeter.xml",
]

# Matches a self-closing <Property .../> whose text attribute contains " : ".
_COLON_PROPERTY_RE = re.compile(
    r'[ \t]*<Property\s+text="[^"]*\s:\s[^"]*"\s*/>\n?',
)


def sanitize_xml(path: Path) -> int:
    """Remove colon-bearing Property elements from MATERIAL blocks. Returns count removed."""
    original = path.read_text(encoding="utf-8")

    # Only strip properties that are inside a <General type="MATERIAL"> block.
    # Strategy: split on General MATERIAL open/close tags and strip only inside those.
    chunks = re.split(r'(<General type="MATERIAL">|</General>)', original)
    inside = False
    removed = 0
    out_parts: list[str] = []

    for chunk in chunks:
        if chunk == '<General type="MATERIAL">':
            inside = True
            out_parts.append(chunk)
        elif chunk == "</General>":
            inside = False
            out_parts.append(chunk)
        elif inside:
            cleaned, n = _COLON_PROPERTY_RE.subn("", chunk)
            removed += n
            out_parts.append(cleaned)
        else:
            out_parts.append(chunk)

    result = "".join(out_parts)
    if removed:
        shutil.copy2(path, path.with_suffix(".xml.bak"))
        path.write_text(result, encoding="utf-8")

    return removed


def main(argv: list[str] | None = None) -> None:
    repo_root = Path(__file__).resolve().parent.parent.parent
    targets = [Path(t) if Path(t).is_absolute() else repo_root / t
               for t in (argv or _DEFAULT_TARGETS)]

    for path in targets:
        if not path.exists():
            print(f"  SKIP (not found): {path}")
            continue
        n = sanitize_xml(path)
        if n:
            print(f"  Fixed {n} property line(s) in {path.relative_to(repo_root)}")
            print(f"         Backup: {path.with_suffix('.xml.bak').name}")
        else:
            print(f"  Already clean:   {path.relative_to(repo_root)}")


if __name__ == "__main__":
    main(sys.argv[1:] or None)

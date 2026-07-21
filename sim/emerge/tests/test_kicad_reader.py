"""
Tests for kicad_reader.py — pad position, stackup, and passive component
extraction.

Run with:
    pytest sim/emerge/tests/test_kicad_reader.py -v

No KiCad installation required — tests use synthetic .kicad_pcb text.

Author: Author
Version: 1.2.0
Last Updated: 2026-07-21
"""

import math
import sys
import pathlib
import pytest

# Allow import from parent directory without installing the package
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
from kicad_reader import (
    read_pad_positions, read_stackup, list_pads_for_ref,
    read_passive_components, read_board_outline, point_in_board,
)


# --------------------------------------------------------------------------- #
# Fixtures — minimal synthetic .kicad_pcb content
# --------------------------------------------------------------------------- #

SIMPLE_PCB = """\
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
  (footprint "Connector_Coaxial:SMA_Amphenol_132372"
    (at 50.0 40.0 0)
    (property "Reference" "J1")
    (pad "1" thru_hole circle (at 0 0 0) (size 1.6 1.6))
    (pad "2" thru_hole circle (at 2.0 0 0) (size 1.6 1.6))
  )
  (footprint "Package_DFN_QFN:LFCSP-8"
    (at 60.0 40.0 90)
    (property "Reference" "U2")
    (pad "1" smd rect (at 0 1.0 0) (size 0.6 0.5))
    (pad "2" smd rect (at 0 -1.0 0) (size 0.6 0.5))
  )
)
"""

MISSING_STACKUP_PCB = """\
(kicad_pcb
  (version 20241229)
  (general (thickness 1.6))
  (footprint "Lib:Part"
    (at 10.0 10.0 0)
    (property "Reference" "R1")
    (pad "1" smd rect (at 0 0 0) (size 0.6 0.5))
  )
)
"""


@pytest.fixture
def pcb_file(tmp_path):
    f = tmp_path / "test.kicad_pcb"
    f.write_text(SIMPLE_PCB, encoding="utf-8")
    return f


@pytest.fixture
def pcb_no_stackup(tmp_path):
    f = tmp_path / "no_stackup.kicad_pcb"
    f.write_text(MISSING_STACKUP_PCB, encoding="utf-8")
    return f


# --------------------------------------------------------------------------- #
# read_pad_positions
# --------------------------------------------------------------------------- #

class TestReadPadPositions:

    def test_returns_dict(self, pcb_file):
        pads = read_pad_positions(pcb_file)
        assert isinstance(pads, dict)

    def test_j1_pad1_absolute_position(self, pcb_file):
        """J1 is at (50, 40) with no rotation — pad 1 at local (0,0) → absolute (50, 40)."""
        pads = read_pad_positions(pcb_file)
        assert "J1:1" in pads
        x, y = pads["J1:1"]
        assert abs(x - 50.0) < 0.001
        assert abs(y - 40.0) < 0.001

    def test_j1_pad2_offset(self, pcb_file):
        """J1 pad 2 is at local (2.0, 0) — absolute (52.0, 40.0)."""
        pads = read_pad_positions(pcb_file)
        assert "J1:2" in pads
        x, y = pads["J1:2"]
        assert abs(x - 52.0) < 0.001
        assert abs(y - 40.0) < 0.001

    def test_u2_pad1_rotated_90(self, pcb_file):
        """
        U2 is at (60, 40) rotated 90°.
        Pad 1 local (0, 1.0):
            ax = 60 + 0*cos(90°) - 1.0*sin(90°) = 60 - 1.0 = 59.0
            ay = 40 + 0*sin(90°) + 1.0*cos(90°) = 40 + 0.0 = 40.0
        """
        pads = read_pad_positions(pcb_file)
        assert "U2:1" in pads
        x, y = pads["U2:1"]
        assert abs(x - 59.0) < 0.001
        assert abs(y - 40.0) < 0.001

    def test_all_pads_found(self, pcb_file):
        pads = read_pad_positions(pcb_file)
        assert len(pads) == 4    # J1:1, J1:2, U2:1, U2:2

    def test_missing_file_raises(self):
        with pytest.raises(FileNotFoundError):
            read_pad_positions("/nonexistent/path/board.kicad_pcb")


# --------------------------------------------------------------------------- #
# list_pads_for_ref
# --------------------------------------------------------------------------- #

class TestListPadsForRef:

    def test_returns_only_matching_ref(self, pcb_file):
        pads = read_pad_positions(pcb_file)
        j1 = list_pads_for_ref(pads, "J1")
        assert set(j1.keys()) == {"1", "2"}

    def test_unknown_ref_returns_empty(self, pcb_file):
        pads = read_pad_positions(pcb_file)
        assert list_pads_for_ref(pads, "C99") == {}


# --------------------------------------------------------------------------- #
# read_stackup
# --------------------------------------------------------------------------- #

class TestReadStackup:

    def test_returns_dict(self, pcb_file):
        st = read_stackup(pcb_file)
        assert isinstance(st, dict)

    def test_copper_layers_count(self, pcb_file):
        st = read_stackup(pcb_file)
        assert st["copper_layers"] == 2

    def test_er_value(self, pcb_file):
        st = read_stackup(pcb_file)
        assert abs(st["er"] - 4.5) < 0.001

    def test_tand_value(self, pcb_file):
        st = read_stackup(pcb_file)
        assert abs(st["tand"] - 0.02) < 0.001

    def test_copper_thickness(self, pcb_file):
        """1 oz copper = 0.035 mm."""
        st = read_stackup(pcb_file)
        assert abs(st["copper_thickness_mm"] - 0.035) < 0.001

    def test_board_thickness(self, pcb_file):
        """0.035 + 1.53 + 0.035 = 1.6 mm."""
        st = read_stackup(pcb_file)
        assert abs(st["board_thickness_mm"] - 1.6) < 0.01

    def test_layers_list_present(self, pcb_file):
        st = read_stackup(pcb_file)
        assert "layers" in st
        assert len(st["layers"]) == 3

    def test_missing_stackup_returns_defaults(self, pcb_no_stackup):
        """Board without (stackup ...) block should return FR4 defaults."""
        st = read_stackup(pcb_no_stackup)
        assert st["er"] == 4.5
        assert st["tand"] == 0.02
        assert st["board_thickness_mm"] == 1.6
        assert st["copper_layers"] == 2


# --------------------------------------------------------------------------- #
# Passive component fixtures
# --------------------------------------------------------------------------- #

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
  (footprint "Connector_Coaxial:SMA" (layer "F.Cu")
    (at 10.0 10.0 0)
    (property "Reference" "J1")
    (pad "1" thru_hole circle (at 0 0 0) (size 1.6 1.6))
  )
  (footprint "Resistor_SMD:R_0402" (layer "F.Cu")
    (at 50.0 40.0 0)
    (property "Reference" "R1")
    (property "Value" "100R")
    (pad "1" smd rect (at -0.5 0 0) (size 0.5 0.5))
    (pad "2" smd rect (at 0.5 0 0) (size 0.5 0.5))
  )
  (footprint "Capacitor_SMD:C_0402" (layer "F.Cu")
    (at 55.0 45.0 0)
    (property "Reference" "C1")
    (property "Value" "100n")
    (pad "1" smd rect (at 0 -0.5 0) (size 0.5 0.5))
    (pad "2" smd rect (at 0 0.5 0) (size 0.5 0.5))
  )
  (footprint "Inductor_SMD:L_0402" (layer "B.Cu")
    (at 60.0 50.0 0)
    (property "Reference" "L1")
    (property "Value" "10n")
    (pad "1" smd rect (at -0.5 0 0) (size 0.6 0.4))
    (pad "2" smd rect (at 0.5 0 0) (size 0.6 0.4))
  )
  (footprint "Capacitor_SMD:C_0805" (layer "F.Cu")
    (at 65.0 45.0 0)
    (property "Reference" "C2")
    (property "Value" "10uF/16V")
    (pad "1" smd rect (at -1.0 0 0) (size 1.2 1.4))
    (pad "2" smd rect (at 1.0 0 0) (size 1.2 1.4))
  )
  (footprint "Resistor_SMD:R_0402" (layer "F.Cu")
    (at 70.0 40.0 0)
    (property "Reference" "R2")
    (property "Value" "0R")
    (pad "1" smd rect (at -0.5 0 0) (size 0.5 0.5))
    (pad "2" smd rect (at 0.5 0 0) (size 0.5 0.5))
  )
  (footprint "Resistor_SMD:R_0402" (layer "F.Cu")
    (at 75.0 40.0 90)
    (property "Reference" "R3")
    (property "Value" "4k7")
    (pad "1" smd rect (at -0.5 0 0) (size 0.5 0.5))
    (pad "2" smd rect (at 0.5 0 0) (size 0.5 0.5))
  )
)
"""


@pytest.fixture
def passive_pcb_file(tmp_path):
    f = tmp_path / "passives.kicad_pcb"
    f.write_text(PASSIVE_PCB, encoding="utf-8")
    return f


# --------------------------------------------------------------------------- #
# read_passive_components
# --------------------------------------------------------------------------- #

class TestReadPassiveComponents:

    def test_returns_list(self, passive_pcb_file):
        comps = read_passive_components(passive_pcb_file)
        assert isinstance(comps, list)

    def test_count_excludes_connector(self, passive_pcb_file):
        """J1 must not appear — only R/L/C refs are returned."""
        comps = read_passive_components(passive_pcb_file)
        refs = [c["ref"] for c in comps]
        assert "J1" not in refs
        assert len(comps) == 6   # R1, C1, L1, C2, R2, R3

    def test_r1_pad_positions_unrotated(self, passive_pcb_file):
        """R1 at (50,40), pads at local ±0.5 mm along X → absolute (49.5,40) and (50.5,40)."""
        comps = read_passive_components(passive_pcb_file)
        r1 = next(c for c in comps if c["ref"] == "R1")
        x1, y1 = r1["pad1_xy"]
        x2, y2 = r1["pad2_xy"]
        assert abs(x1 - 49.5) < 0.001
        assert abs(y1 - 40.0) < 0.001
        assert abs(x2 - 50.5) < 0.001
        assert abs(y2 - 40.0) < 0.001

    def test_c1_pad_positions_vertical(self, passive_pcb_file):
        """C1 at (55,45), pads along Y → (55,44.5) and (55,45.5)."""
        comps = read_passive_components(passive_pcb_file)
        c1 = next(c for c in comps if c["ref"] == "C1")
        x1, y1 = c1["pad1_xy"]
        x2, y2 = c1["pad2_xy"]
        assert abs(x1 - 55.0) < 0.001
        assert abs(y1 - 44.5) < 0.001
        assert abs(x2 - 55.0) < 0.001
        assert abs(y2 - 45.5) < 0.001

    def test_l1_on_bottom_layer(self, passive_pcb_file):
        comps = read_passive_components(passive_pcb_file)
        l1 = next(c for c in comps if c["ref"] == "L1")
        assert l1["layer"] == "B.Cu"

    def test_r1_on_top_layer(self, passive_pcb_file):
        comps = read_passive_components(passive_pcb_file)
        r1 = next(c for c in comps if c["ref"] == "R1")
        assert r1["layer"] == "F.Cu"

    def test_pad_size_extracted(self, passive_pcb_file):
        comps = read_passive_components(passive_pcb_file)
        r1 = next(c for c in comps if c["ref"] == "R1")
        w, h = r1["pad_size"]
        assert abs(w - 0.5) < 0.001
        assert abs(h - 0.5) < 0.001

    def test_c2_value_with_voltage_suffix(self, passive_pcb_file):
        """C2 has value '10uF/16V' — raw value string preserved as-is."""
        comps = read_passive_components(passive_pcb_file)
        c2 = next(c for c in comps if c["ref"] == "C2")
        assert c2["value"] == "10uF/16V"

    def test_r3_rotated_90_pad_positions(self, passive_pcb_file):
        """
        R3 at (75,40) rotated 90°.  Local pad1 at (-0.5, 0):
          ax = 75 + (-0.5)*cos(90°) - 0*sin(90°) = 75.0
          ay = 40 + (-0.5)*sin(90°) + 0*cos(90°) = 39.5
        """
        comps = read_passive_components(passive_pcb_file)
        r3 = next(c for c in comps if c["ref"] == "R3")
        x1, y1 = r3["pad1_xy"]
        x2, y2 = r3["pad2_xy"]
        assert abs(x1 - 75.0) < 0.001
        assert abs(y1 - 39.5) < 0.001
        assert abs(x2 - 75.0) < 0.001
        assert abs(y2 - 40.5) < 0.001

    def test_empty_pcb_returns_empty_list(self, tmp_path):
        """PCB with no R/L/C footprints returns []."""
        f = tmp_path / "empty.kicad_pcb"
        f.write_text(SIMPLE_PCB, encoding="utf-8")
        comps = read_passive_components(f)
        assert comps == []

    def test_l1_pad_size(self, passive_pcb_file):
        """L1 pads are 0.6×0.4 mm."""
        comps = read_passive_components(passive_pcb_file)
        l1 = next(c for c in comps if c["ref"] == "L1")
        w, h = l1["pad_size"]
        assert abs(w - 0.6) < 0.001
        assert abs(h - 0.4) < 0.001


# --------------------------------------------------------------------------- #
# Board outline fixtures
# --------------------------------------------------------------------------- #

# 100×50 mm rectangular board at (10, 20)
OUTLINE_PCB = """\
(kicad_pcb
  (version 20241229)
  (general (thickness 1.6))
  (gr_line (start 10.0 20.0) (end 110.0 20.0)
    (stroke (width 0.05) (type default)) (layer "Edge.Cuts"))
  (gr_line (start 110.0 20.0) (end 110.0 70.0)
    (stroke (width 0.05) (type default)) (layer "Edge.Cuts"))
  (gr_line (start 110.0 70.0) (end 10.0 70.0)
    (stroke (width 0.05) (type default)) (layer "Edge.Cuts"))
  (gr_line (start 10.0 70.0) (end 10.0 20.0)
    (stroke (width 0.05) (type default)) (layer "Edge.Cuts"))
)
"""

# Same board using gr_rect
RECT_PCB = """\
(kicad_pcb
  (version 20241229)
  (general (thickness 1.6))
  (gr_rect (start 10.0 20.0) (end 110.0 70.0)
    (stroke (width 0.05) (type default)) (layer "Edge.Cuts"))
)
"""

NO_OUTLINE_PCB = """\
(kicad_pcb
  (version 20241229)
  (general (thickness 1.6))
)
"""


@pytest.fixture
def outline_pcb_file(tmp_path):
    f = tmp_path / "outline.kicad_pcb"
    f.write_text(OUTLINE_PCB, encoding="utf-8")
    return f


@pytest.fixture
def rect_pcb_file(tmp_path):
    f = tmp_path / "rect.kicad_pcb"
    f.write_text(RECT_PCB, encoding="utf-8")
    return f


# --------------------------------------------------------------------------- #
# read_board_outline
# --------------------------------------------------------------------------- #

class TestReadBoardOutline:

    def test_returns_list(self, outline_pcb_file):
        pts = read_board_outline(outline_pcb_file)
        assert isinstance(pts, list)

    def test_four_gr_lines_give_polygon(self, outline_pcb_file):
        pts = read_board_outline(outline_pcb_file)
        assert len(pts) >= 4

    def test_bounding_box_correct(self, outline_pcb_file):
        pts = read_board_outline(outline_pcb_file)
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        assert abs(min(xs) - 10.0)  < 0.01
        assert abs(max(xs) - 110.0) < 0.01
        assert abs(min(ys) - 20.0)  < 0.01
        assert abs(max(ys) - 70.0)  < 0.01

    def test_gr_rect_equivalent_to_four_lines(self, outline_pcb_file, rect_pcb_file):
        pts_lines = read_board_outline(outline_pcb_file)
        pts_rect  = read_board_outline(rect_pcb_file)
        xs_l = sorted(set(round(p[0]) for p in pts_lines))
        xs_r = sorted(set(round(p[0]) for p in pts_rect))
        assert xs_l == xs_r

    def test_no_outline_returns_empty(self, tmp_path):
        f = tmp_path / "no_outline.kicad_pcb"
        f.write_text(NO_OUTLINE_PCB, encoding="utf-8")
        assert read_board_outline(f) == []


# --------------------------------------------------------------------------- #
# point_in_board
# --------------------------------------------------------------------------- #

class TestPointInBoard:

    # 100×50 mm rectangle: (10,20)–(110,70)
    RECT = [(10.0, 20.0), (110.0, 20.0), (110.0, 70.0), (10.0, 70.0)]

    def test_centre_is_inside(self):
        assert point_in_board(60.0, 45.0, self.RECT) is True

    def test_corner_vicinity_inside(self):
        assert point_in_board(15.0, 25.0, self.RECT) is True

    def test_outside_left(self):
        assert point_in_board(5.0, 45.0, self.RECT) is False

    def test_outside_right(self):
        assert point_in_board(120.0, 45.0, self.RECT) is False

    def test_outside_top(self):
        assert point_in_board(60.0, 10.0, self.RECT) is False

    def test_outside_bottom(self):
        assert point_in_board(60.0, 80.0, self.RECT) is False

    def test_empty_outline_always_true(self):
        """No outline → everything is inside (no filtering)."""
        assert point_in_board(999.0, 999.0, []) is True

"""בדיקות קורא ה-DXF — המספרים כאן אומתו ידנית מול חישוב אנליטי."""

from __future__ import annotations

import math

import pytest

from laser_pricing.cad import read_dxf


def test_rectangle_is_rebuilt_from_four_separate_lines(rect_with_hole_dxf):
    """הבדיקה החשובה ביותר: מלבן ב-DXF הוא ארבע ישויות LINE נפרדות."""
    x = read_dxf(rect_with_hole_dxf)
    geo = x.geometry
    assert geo.body_count == 1
    assert geo.hole_count == 1
    assert geo.pierce_count == 2
    assert geo.bbox.width == pytest.approx(400, abs=0.01)
    assert geo.bbox.height == pytest.approx(250, abs=0.01)
    assert geo.net_area == pytest.approx(400 * 250 - math.pi * 25**2, rel=1e-4)
    assert geo.cut_length == pytest.approx(2 * (400 + 250) + 2 * math.pi * 25, rel=1e-4)


def test_text_and_dimension_layers_are_excluded(rect_with_hole_dxf):
    """טקסט ושכבת מידות אינם גיאומטריית חיתוך ואסור שיתומחרו."""
    x = read_dxf(rect_with_hole_dxf)
    assert "TEXT" in x.skipped_entities
    assert any("DIM" in key for key in x.skipped_entities)


def test_inch_file_is_converted_to_mm(inch_dxf):
    """קובץ באינצ'ים שנקרא כמ"מ מתמחר בחסר פי 25 — זו נקודת כשל שקטה."""
    x = read_dxf(inch_dxf)
    assert x.scale_to_mm == pytest.approx(25.4)
    assert x.geometry.bbox.width == pytest.approx(254, abs=0.01)
    assert x.geometry.bbox.height == pytest.approx(127, abs=0.01)
    assert any("inches" in w for w in x.warnings)


def test_arcs_keep_their_curvature(rounded_dxf):
    """קשת שהופכת למיתר מקצרת את אורך החיתוך ומורידה את המחיר שלא בצדק."""
    geo = read_dxf(rounded_dxf).geometry
    expected_length = 2 * (200 - 40) + 2 * (100 - 40) + 2 * math.pi * 20
    expected_area = 200 * 100 - (4 * 20**2 - math.pi * 20**2)
    assert geo.bbox.width == pytest.approx(200, abs=0.01)
    assert geo.bbox.height == pytest.approx(100, abs=0.01)
    assert geo.cut_length == pytest.approx(expected_length, rel=1e-3)
    assert geo.net_area == pytest.approx(expected_area, rel=1e-3)

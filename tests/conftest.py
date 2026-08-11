"""פיקסצ'רים משותפים — קבצי DXF נבנים בזמן ריצה כדי שלא נחזיק בינאריים ב-git."""

from __future__ import annotations

import math

import ezdxf
import pytest


@pytest.fixture(scope="session")
def dxf_dir(tmp_path_factory) -> "object":
    return tmp_path_factory.mktemp("dxf")


@pytest.fixture(scope="session")
def rect_with_hole_dxf(dxf_dir) -> str:
    """מלבן 400x250 מארבעה קווי LINE נפרדים + חור עגול + רעש לסינון.

    זה המקרה שמייצג קובץ אמיתי: מלבן אינו ישות אחת, ויש בקובץ
    טקסט ושכבת מידות שאסור שייכנסו לאורך החיתוך.
    """
    doc = ezdxf.new("R2010", setup=True)
    doc.units = 4  # mm
    msp = doc.modelspace()
    corners = [(0, 0), (400, 0), (400, 250), (0, 250)]
    for i in range(4):
        msp.add_line(corners[i], corners[(i + 1) % 4])
    msp.add_circle((200, 125), 25)
    msp.add_text("PART A", height=10).set_placement((10, 260))
    msp.add_line((0, -50), (400, -50), dxfattribs={"layer": "DIM"})
    path = dxf_dir / "rect_with_hole.dxf"
    doc.saveas(path)
    return str(path)


@pytest.fixture(scope="session")
def inch_dxf(dxf_dir) -> str:
    """מלבן 10x5 אינץ' — חייב לצאת 254x127 מ"מ."""
    doc = ezdxf.new("R2010")
    doc.units = 1  # inches
    doc.modelspace().add_lwpolyline([(0, 0), (10, 0), (10, 5), (0, 5)], close=True)
    path = dxf_dir / "inch_part.dxf"
    doc.saveas(path)
    return str(path)


@pytest.fixture(scope="session")
def rounded_dxf(dxf_dir) -> str:
    """מלבן 200x100 עם פינות מעוגלות R20 — בודק שקשתות לא הופכות למיתר."""
    doc = ezdxf.new("R2010")
    doc.units = 4
    r, w, h = 20.0, 200.0, 100.0
    bulge = math.tan(math.pi / 8)  # קשת 90 מעלות
    doc.modelspace().add_lwpolyline(
        [
            (r, 0, 0, 0, 0),
            (w - r, 0, 0, 0, bulge),
            (w, r, 0, 0, 0),
            (w, h - r, 0, 0, bulge),
            (w - r, h, 0, 0, 0),
            (r, h, 0, 0, bulge),
            (0, h - r, 0, 0, 0),
            (0, r, 0, 0, bulge),
        ],
        format="xyseb",
        close=True,
    )
    path = dxf_dir / "rounded.dxf"
    doc.saveas(path)
    return str(path)


@pytest.fixture(scope="session")
def oversized_dxf(dxf_dir) -> str:
    """חלק 3200x1000 — חורג מהפלטה בשני הסיבובים."""
    doc = ezdxf.new("R2010")
    doc.units = 4
    doc.modelspace().add_lwpolyline([(0, 0), (3200, 0), (3200, 1000), (0, 1000)], close=True)
    path = dxf_dir / "oversized.dxf"
    doc.saveas(path)
    return str(path)

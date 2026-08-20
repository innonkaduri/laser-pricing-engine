"""חלק — גיאומטריה + חומר + עובי + כמות. היחידה שהמנוע מתמחר."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .geometry import BoundingBox, PartGeometry


class PartSource(str, Enum):
    """מאיפה הגיע החלק. משפיע על אמון במידות, לא על התמחור."""

    DXF = "dxf"
    STEP = "step"
    MANUAL = "manual"
    MODELED_3D = "modeled_3d"


@dataclass
class Part:
    """חלק בודד להצעת מחיר.

    material_key ו-thickness_mm חייבים להתאים לרשומה בטבלת התמחור של ינון.
    המנוע לא ממציא ערכים לצירוף שאינו בטבלה — הוא נכשל במפורש.
    """

    name: str
    geometry: PartGeometry
    material_key: str
    thickness_mm: float
    quantity: int = 1
    source: PartSource = PartSource.MANUAL
    notes: str = ""
    bend_count: int = 0
    """כמה כיפופים בחלק — **הצהרה של המזמין**, לא מסקנה מהגיאומטריה.

    קובץ DXF שטוח אינו יודע שהוא הולך להתכופף, ולכן אין שום דרך לחלץ
    את זה מהמידות. עד שתהיה פרישה אוטומטית ממודל תלת-ממד, מי שיודע
    כמה כיפופים יש הוא מי שמזמין — והוא מקליד את המספר.
    """
    bend_length_mm: float = 0.0
    """אורך קווי הכיפוף הכולל. 0 = לא הוצהר, ולכן לא מחויב לפי אורך."""
    cut_open_lines: bool = False
    """האם הקווים הפתוחים בשרטוט הם חיתוך.

    ברירת המחדל היא **לא**: קו פתוח בשרטוט פח הוא לרוב סימון — קו
    כיפוף, ציר או הערה. עד 20.8.2026 הם נספרו כחיתוך בשקט, וקו כיפוף
    אחד ניפח אורך חיתוך ב-31%. המנוע אינו יודע להבחין, ולכן הוא שואל
    במקום להחליט.
    """

    def __post_init__(self) -> None:
        if self.quantity < 1:
            raise ValueError("כמות חייבת להיות לפחות 1")
        if self.thickness_mm <= 0:
            raise ValueError("עובי חייב להיות חיובי")
        if not self.material_key:
            raise ValueError("חובה לציין סוג חומר")
        if self.bend_count < 0:
            raise ValueError("מספר כיפופים לא יכול להיות שלילי")
        if self.bend_length_mm < 0:
            raise ValueError("אורך כיפוף לא יכול להיות שלילי")

    @property
    def bbox(self) -> BoundingBox:
        return self.geometry.bbox

    @property
    def net_area_mm2(self) -> float:
        return self.geometry.net_area

    @property
    def cut_length_mm(self) -> float:
        """אורך החיתוך שמחויב בפועל, לפי הכרעת המזמין על הקווים הפתוחים."""
        length = self.geometry.cut_length
        if self.cut_open_lines:
            length += self.geometry.open_length
        return length

    @property
    def pierce_count(self) -> int:
        return self.geometry.pierce_count

    def summary(self) -> dict[str, float | int | str]:
        """תקציר קומפקטי — זה מה שנשלח ל-AI במקום הקובץ עצמו.

        חילוץ המידות נעשה כאן מקומית, כך שה-AI מקבל עשרות טוקנים
        במקום קובץ DXF שלם.
        """
        box = self.bbox
        return {
            "name": self.name,
            "material": self.material_key,
            "thickness_mm": self.thickness_mm,
            "quantity": self.quantity,
            "width_mm": round(box.width, 2),
            "height_mm": round(box.height, 2),
            "net_area_mm2": round(self.net_area_mm2, 2),
            "cut_length_mm": round(self.cut_length_mm, 2),
            "open_length_mm": round(self.geometry.open_length, 2),
            "pierces": self.pierce_count,
            "holes": self.geometry.hole_count,
            "bodies": self.geometry.body_count,
            "source": self.source.value,
        }

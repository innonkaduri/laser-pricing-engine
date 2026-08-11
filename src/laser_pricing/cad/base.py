"""חוזה משותף לכל קורא CAD.

DXF, STEP או מודל שנבנה בתוך המערכת — כולם מחזירים CadExtraction,
וכל מה שבמורד הזרם (תמחור, נסטינג, ממשק) לא יודע מאיפה זה הגיע.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..domain.geometry import PartGeometry


class CadReadError(Exception):
    """הקובץ לא נקרא, או שאין בו גיאומטריה שאפשר לתמחר."""


@dataclass
class CadExtraction:
    """התוצר של קריאת קובץ — גיאומטריה + מה שצריך לדעת עליה.

    warnings הוא חלק מהתוצר ולא רעש: מתאר פתוח או יחידות חסרות בקובץ
    משנים את המחיר, והמשתמש חייב לראות את זה לפני שהוא מאשר הצעה.
    """

    geometry: PartGeometry
    source_name: str
    units_detected: str = "unknown"
    scale_to_mm: float = 1.0
    warnings: list[str] = field(default_factory=list)
    skipped_entities: dict[str, int] = field(default_factory=dict)
    layers_seen: list[str] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        return not self.warnings and not self.skipped_entities

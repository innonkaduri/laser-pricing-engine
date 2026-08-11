"""אחסון זמני של גיאומטריות שהועלו.

למה בזיכרון ולא במסד: הגיאומטריה נחוצה רק בין ההעלאה לבין התמחור,
כלומר שניות בודדות. שמירה קבועה שלה תהיה איסוף מידע בלי צורך.

המחיר של הבחירה הזאת: אחרי הפעלה מחדש של השרת יש להעלות את הקובץ
שוב. הממשק מקבל 410 מפורש ומבקש העלאה מחדש, ולא נכשל בשקט.
"""

from __future__ import annotations

import itertools
from collections import OrderedDict
from dataclasses import dataclass

from ..domain.geometry import PartGeometry

MAX_ENTRIES = 200


class GeometryExpired(KeyError):
    """הגיאומטריה כבר לא בזיכרון — צריך להעלות מחדש."""


@dataclass
class StoredGeometry:
    geometry: PartGeometry
    name: str
    source: str
    units_detected: str = "unknown"
    warnings: tuple[str, ...] = ()


class GeometryStore:
    """מילון עם תקרה — הישן ביותר נזרק ראשון."""

    def __init__(self, max_entries: int = MAX_ENTRIES) -> None:
        self._items: OrderedDict[str, StoredGeometry] = OrderedDict()
        self._counter = itertools.count(1)
        self._max = max_entries

    def put(self, entry: StoredGeometry) -> str:
        key = f"g{next(self._counter)}"
        self._items[key] = entry
        while len(self._items) > self._max:
            self._items.popitem(last=False)
        return key

    def get(self, key: str) -> StoredGeometry:
        try:
            entry = self._items[key]
        except KeyError as exc:
            raise GeometryExpired(key) from exc
        self._items.move_to_end(key)
        return entry


STORE = GeometryStore()

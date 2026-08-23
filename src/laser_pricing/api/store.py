"""אחסון זמני של גיאומטריות שהועלו.

למה בזיכרון ולא במסד: הגיאומטריה נחוצה רק בין ההעלאה לבין התמחור,
כלומר שניות בודדות. שמירה קבועה שלה תהיה איסוף מידע בלי צורך.

המחיר של הבחירה הזאת: אחרי הפעלה מחדש של השרת יש להעלות את הקובץ
שוב. הממשק מקבל 410 מפורש ומבקש העלאה מחדש, ולא נכשל בשקט.

**המפתח אקראי והכניסה שייכת למי שהעלה אותה (23.8.2026).** קודם לכן
המפתחות היו `g1`, `g2`, `g3` — רצף — וכל מי שהזדהה יכול היה לתמחר
`g7` ולקבל בחזרה את המידות, החורים ואורך החיתוך של הקובץ של מישהו
אחר. עם ארבעה משתמשים פנימיים זה היה מטרד; עם הרשמה ציבורית זו קריאה
של שרטוט הלקוח של אבא בידי זר. הבעלות נאכפת ב-`get`, ולא רק המפתח
הוחלף: מפתח אקראי לבדו עדיין דולף למי שמקבל אותו בטעות.
"""

from __future__ import annotations

import secrets
from collections import OrderedDict
from dataclasses import dataclass

from ..domain.geometry import PartGeometry

MAX_ENTRIES = 400
MAX_ENTRIES_PER_OWNER = 40
"""תקרה לכל בעלים בנפרד, ולא רק תקרה גלובלית.

עם תקרה גלובלית בלבד, מבקר אחד שמעלה בלולאה היה דוחק מהזיכרון את
הקובץ שאבא באמצע לתמחר — כלומר מוחק את עבודתו של אחר בלי לגעת בה.
המגבלה הפרטנית עושה את ההצפה לבעיה של מי שיצר אותה.
"""


class GeometryExpired(KeyError):
    """הגיאומטריה כבר לא בזיכרון — צריך להעלות מחדש."""


@dataclass
class StoredGeometry:
    geometry: PartGeometry
    name: str
    source: str
    units_detected: str = "unknown"
    warnings: tuple[str, ...] = ()
    owner: str = ""


class GeometryStore:
    """מילון עם תקרה — הישן ביותר נזרק ראשון, בתוך כל בעלים בנפרד."""

    def __init__(
        self, max_entries: int = MAX_ENTRIES, max_per_owner: int = MAX_ENTRIES_PER_OWNER
    ) -> None:
        self._items: OrderedDict[str, StoredGeometry] = OrderedDict()
        self._max = max_entries
        self._max_per_owner = max_per_owner

    def put(self, entry: StoredGeometry, owner: str = "") -> str:
        entry.owner = owner
        # 12 בתים אקראיים ולא מונה: מפתח שאפשר לנחש הוא רשימת הקבצים
        # של כל השאר.
        key = secrets.token_urlsafe(12)
        self._items[key] = entry
        self._evict_owner(owner)
        while len(self._items) > self._max:
            self._items.popitem(last=False)
        return key

    def _evict_owner(self, owner: str) -> None:
        keys = [k for k, v in self._items.items() if v.owner == owner]
        for key in keys[: max(0, len(keys) - self._max_per_owner)]:
            del self._items[key]

    def get(self, key: str, owner: str = "") -> StoredGeometry:
        """מחזיר את הכניסה, או `GeometryExpired` — גם כשהיא של מישהו אחר.

        אותה שגיאה בדיוק לשני המקרים, ובכוונה: תשובה שמבדילה בין
        "אין כזה" לבין "יש, אבל לא שלך" מספרת לזר כמה קבצים יש במערכת
        ומתי הועלו.
        """
        entry = self._items.get(key)
        if entry is None or entry.owner != owner:
            raise GeometryExpired(key)
        self._items.move_to_end(key)
        return entry


STORE = GeometryStore()

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

MAX_ENTRIES = 500
"""תקרה גלובלית על כל הבעלים יחד."""

MAX_ENTRIES_PER_PUBLIC_OWNER = 40
"""תקרה למי שנרשם מהרחוב.

עם תקרה גלובלית בלבד, מבקר אחד שמעלה בלולאה היה דוחק מהזיכרון את
הקובץ שאבא באמצע לתמחר — כלומר מוחק את עבודתו של אחר בלי לגעת בה.
המגבלה הפרטנית עושה את ההצפה לבעיה של מי שיצר אותה.
"""

MAX_ENTRIES_PER_INTERNAL_OWNER = 200
"""תקרה לבעלים פנימי: אבא, ינון, ו-`crm` (טוקן השירות של ימיש).

**הכרעת האב, 25.8.2026:** "ימיש הוא עסק מתכת — הזמנה של 40 חלקים
אינה קצה, היא יום שלישי רגיל." התקרה הציבורית נשארת 40 כי היא הגנה
מפני זר, לא מפני איתי.

**המספר נגזר ממדידה ולא נבחר.** זיכרון לגיאומטריה שמורה, נמדד על
הקופסה ב-25.8.2026:

    מתאר אחד (מלבן פשוט)        1.0 KB
    11 מתארים (10 חורים)         72 KB
    201 מתארים                  1.4 MB
    401 מתארים                  2.9 MB

200 חלקים כבדים במיוחד הם ~580MB, מול `MemoryMax=1500M` ביחידה —
כלומר גם המקרה הגרוע ביותר נשאר בתוך התקציב, ו-200 הם פי חמישה
מ"יום שלישי רגיל". הזמנה טיפוסית של 200 חלקים היא ~14MB.

**שני המספרים כאן ואין להם עותק בשום מקום אחר בקוד.**
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

    def __init__(self, max_entries: int = MAX_ENTRIES) -> None:
        self._items: OrderedDict[str, StoredGeometry] = OrderedDict()
        self._max = max_entries

    def put(self, entry: StoredGeometry, owner: str = "", internal: bool = False) -> str:
        """שומר גיאומטריה ומחזיר מפתח.

        `internal` קובע איזו משתי התקרות חלה. הוא מגיע מהיכולות של
        הפונה ולא משם הבעלים, כדי שלא תיווצר רשימת שמות מיוחסים.
        """
        entry.owner = owner
        # 12 בתים אקראיים ולא מונה: מפתח שאפשר לנחש הוא רשימת הקבצים
        # של כל השאר.
        key = secrets.token_urlsafe(12)
        self._items[key] = entry
        cap = MAX_ENTRIES_PER_INTERNAL_OWNER if internal else MAX_ENTRIES_PER_PUBLIC_OWNER
        self._evict_owner(owner, cap)
        while len(self._items) > self._max:
            self._items.popitem(last=False)
        return key

    def _evict_owner(self, owner: str, cap: int) -> None:
        keys = [k for k, v in self._items.items() if v.owner == owner]
        for key in keys[: max(0, len(keys) - cap)]:
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

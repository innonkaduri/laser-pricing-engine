"""טעינת טבלת התמחור של ינון והחזקתה בזיכרון.

סדר העדיפות מכוון:
  1. משתנה הסביבה TARIFF_JSON — זה מה ששורד פריסה מחדש בענן.
  2. config/tariff.json — הקובץ שינון עורך מקומית.
  3. config/tariff.example.json — התבנית, שכל המחירים בה 0.

אם נטענה התבנית, is_ready יחזיר False והממשק חייב לומר את זה בפירוש.
מנוע שמציג 0 בלי אזהרה נראה בדיוק כמו מנוע שעובד.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from ..pricing.tariff import InvalidTariffError, Tariff, tariff_from_dict

ROOT = Path(__file__).resolve().parents[3]
CONFIG_DIR = ROOT / "config"
LIVE_PATH = Path(os.environ.get("TARIFF_PATH", CONFIG_DIR / "tariff.json"))
EXAMPLE_PATH = CONFIG_DIR / "tariff.example.json"


def _serialize(raw: dict) -> bytes:
    """הצורה המדויקת שבה הטבלה נכתבת לדיסק.

    חייבת להיות זהה לבית האחרון למה ש-`persist` כותב, אחרת השוואת
    הגיבובים תדווח על סטייה גם כשאין שום סטייה.
    """
    return json.dumps(raw, ensure_ascii=False, indent=2).encode("utf-8")


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class TariffState:
    """מחזיק את הטבלה הפעילה ואת ה-JSON הגולמי שלה לעריכה."""

    def __init__(self) -> None:
        self.raw: dict = {}
        self.tariff: Tariff | None = None
        self.origin: str = "none"
        self.error: str = ""
        self.memory_hash: str = ""
        # חתימת הקובץ שכבר גיבבנו: (גודל, זמן שינוי). כל עוד היא לא
        # השתנתה, אין שום סיבה לקרוא את הקובץ שוב.
        self._disk_stat: tuple[int, int] | None = None
        self._disk_hash: str = ""
        self.reload()

    # ---- טעינה ----

    def reload(self) -> None:
        raw, origin = _read_raw()
        self._apply(raw, origin)

    def _apply(self, raw: dict, origin: str) -> None:
        try:
            self.tariff = tariff_from_dict(raw, source=origin)
            self.raw = raw
            self.origin = origin
            self.error = ""
            self.memory_hash = _digest(_serialize(raw))
        except (InvalidTariffError, TypeError, ValueError) as exc:
            self.error = str(exc)
            if self.tariff is None:
                self.origin = origin
                self.raw = raw
                self.memory_hash = _digest(_serialize(raw))

    def replace(self, raw: dict) -> Tariff:
        """מחליף את הטבלה הפעילה. נכשל בלי לפגוע בקיימת אם היא לא תקינה."""
        tariff = tariff_from_dict(raw, source="עריכה בממשק")
        self.tariff = tariff
        self.raw = raw
        self.origin = "עריכה בממשק"
        self.error = ""
        self.memory_hash = _digest(_serialize(raw))
        return tariff

    def persist(self) -> Path | None:
        """שומר לדיסק. בענן זה זמני — ולכן הממשק מציע גם הורדת הקובץ."""
        data = _serialize(self.raw)
        try:
            LIVE_PATH.parent.mkdir(parents=True, exist_ok=True)
            LIVE_PATH.write_bytes(data)
            stat = LIVE_PATH.stat()
        except OSError:
            return None
        # רשמנו את מה שכתבנו, ולכן בדיקת הבריאות הבאה לא תיגע בקובץ בכלל.
        self._disk_hash = _digest(data)
        self._disk_stat = (stat.st_size, stat.st_mtime_ns)
        return LIVE_PATH

    # ---- מצב ----

    def disk_state(self) -> tuple[bool, bool]:
        """(האם יש קובץ חי על הדיסק, האם הוא זהה לטבלה שבזיכרון).

        הטבלה מוחזקת בזיכרון התהליך, והיא ממשיכה להיראות תקינה גם אחרי
        שהקובץ נמחק — עד ההפעלה מחדש, שמוחקת אותה. לכן צריך למדוד את
        הדיסק ולא להסתמך על מה שבזיכרון.

        ההשוואה עצמה עוברת דרך `stat` בלבד: גודל וזמן שינוי זהים למה
        שכבר גיבבנו פירושם אותו קובץ. הקריאה מהדיסק קורית רק כשהחתימה
        באמת השתנתה, ולא בכל בדיקת בריאות.
        """
        try:
            stat = LIVE_PATH.stat()
        except OSError:
            self._disk_stat = None
            self._disk_hash = ""
            return (False, False)

        signature = (stat.st_size, stat.st_mtime_ns)
        if signature != self._disk_stat:
            try:
                self._disk_hash = _digest(LIVE_PATH.read_bytes())
            except OSError:
                # הקובץ קיים אבל לא ניתן לקריאה — סטייה לכל דבר.
                self._disk_stat = None
                return (True, False)
            self._disk_stat = signature
        return (True, bool(self.memory_hash) and self._disk_hash == self.memory_hash)

    @property
    def is_ready(self) -> bool:
        """האם יש כאן מחירים אמיתיים, או רק שלד."""
        if self.tariff is None:
            return False
        rates = self.tariff.rates.values()
        return any(r.plate_price > 0 or r.cut_rate_per_m > 0 for r in rates)

    def require(self) -> Tariff:
        if self.tariff is None:
            raise InvalidTariffError(self.error or "לא נטענה טבלת תמחור.")
        return self.tariff


def _read_raw() -> tuple[dict, str]:
    env = os.environ.get("TARIFF_JSON", "").strip()
    if env:
        try:
            return json.loads(env), "TARIFF_JSON"
        except json.JSONDecodeError as exc:
            raise InvalidTariffError(f"TARIFF_JSON אינו JSON תקין: {exc}") from exc

    for path, label in ((LIVE_PATH, str(LIVE_PATH.name)), (EXAMPLE_PATH, EXAMPLE_PATH.name)):
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8")), label

    raise InvalidTariffError("לא נמצאה טבלת תמחור ולא תבנית.")


STATE = TariffState()

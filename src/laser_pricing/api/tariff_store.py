"""טעינת טבלת התמחור של ינון והחזקתה בזיכרון.

סדר העדיפות מכוון:
  1. משתנה הסביבה TARIFF_JSON — זה מה ששורד פריסה מחדש בענן.
  2. config/tariff.json — הקובץ שינון עורך מקומית.
  3. config/tariff.example.json — התבנית, שכל המחירים בה 0.

אם נטענה התבנית, is_ready יחזיר False והממשק חייב לומר את זה בפירוש.
מנוע שמציג 0 בלי אזהרה נראה בדיוק כמו מנוע שעובד.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from ..pricing.tariff import InvalidTariffError, Tariff, tariff_from_dict

ROOT = Path(__file__).resolve().parents[3]
CONFIG_DIR = ROOT / "config"
LIVE_PATH = Path(os.environ.get("TARIFF_PATH", CONFIG_DIR / "tariff.json"))
EXAMPLE_PATH = CONFIG_DIR / "tariff.example.json"


class TariffState:
    """מחזיק את הטבלה הפעילה ואת ה-JSON הגולמי שלה לעריכה."""

    def __init__(self) -> None:
        self.raw: dict = {}
        self.tariff: Tariff | None = None
        self.origin: str = "none"
        self.error: str = ""
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
        except (InvalidTariffError, TypeError, ValueError) as exc:
            self.error = str(exc)
            if self.tariff is None:
                self.origin = origin
                self.raw = raw

    def replace(self, raw: dict) -> Tariff:
        """מחליף את הטבלה הפעילה. נכשל בלי לפגוע בקיימת אם היא לא תקינה."""
        tariff = tariff_from_dict(raw, source="עריכה בממשק")
        self.tariff = tariff
        self.raw = raw
        self.origin = "עריכה בממשק"
        self.error = ""
        return tariff

    def persist(self) -> Path | None:
        """שומר לדיסק. בענן זה זמני — ולכן הממשק מציע גם הורדת הקובץ."""
        try:
            LIVE_PATH.parent.mkdir(parents=True, exist_ok=True)
            LIVE_PATH.write_text(
                json.dumps(self.raw, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            return LIVE_PATH
        except OSError:
            return None

    # ---- מצב ----

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

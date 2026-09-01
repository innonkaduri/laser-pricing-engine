"""המפתח שה-CRM משתמש בו. **נראה ומתחלף מהמסך, לא רק ב-SSH.**

עד 1.9.2026 השורש היחיד של הערך היה משתנה סביבה (`SERVICE_TOKEN`) —
נכון וחזק, אבל החלפה דרשה SSH ופריסה מלאה בשביל שורה אחת. הקובץ הזה
מוסיף שכבה: קובץ על הדיסק שהמסך `/admin` קורא וכותב, בדיוק כמו
`business.json`. **משתנה הסביבה נשאר תקף כברירת מחדל** — מי שלא סובב
עדיין דרך המסך ממשיך לעבוד עם הערך שכבר על הקופסה; ברגע שמסובבים,
הקובץ גובר.

**זה סוד, לא פרט עסק כמו `business.json`.** אסור שיגיע לריפו הציבורי
(`config/service_token.json` ב-`.gitignore`), ובפרודקשן `SERVICE_TOKEN_FILE`
מצביע על `/var/lib/laser/`, כמו כל מסד אחר שאי אפשר לשחזר.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path

CONFIG_DIR = Path(__file__).resolve().parents[3] / "config"
PATH = Path(os.environ.get("SERVICE_TOKEN_FILE", CONFIG_DIR / "service_token.json"))


def _load() -> dict:
    if not PATH.exists():
        return {}
    try:
        data = json.loads(PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def current() -> str:
    """הערך שתקף לאימות עכשיו. **הקובץ גובר על משתנה הסביבה** — מי
    שסובב דרך המסך רואה את זה מיד, בלי לגעת בקובץ הסביבה או להפעיל
    מחדש את השירות."""
    token = str(_load().get("token", "")).strip()
    return token or os.environ.get("SERVICE_TOKEN", "").strip()


def status() -> dict:
    """מה שמותר להראות במסך. **הערך עצמו נכלל, במפורש** — בניגוד
    לסיסמה, המפתח הזה חייב להיות קריא כדי שאפשר יהיה להעתיק אותו
    בדיוק לתצורה של הצד השני. אין כאן hash חד-כיווני כי אין כאן
    השוואה מול מי שמקליד — יש כאן שני צדדים שצריכים את אותו ערך."""
    data = _load()
    token = current()
    return {
        "configured": bool(token),
        "token": token,
        "source": "file" if data.get("token") else ("env" if token else "none"),
        "rotated_at": data.get("rotated_at"),
    }


def rotate() -> dict:
    """מפתח חדש, כתיבה אטומית. **הישן מפסיק לעבוד באותה נשימה** —
    מי שמסובב בלי לעדכן את הצד השני מנתק את החיבור, לא רק "מחדש" אותו."""
    token = f"laser_live_{secrets.token_urlsafe(32)}"
    data = {"token": token, "rotated_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    tmp = PATH.parent / f"{PATH.name}.tmp"
    PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(PATH)
    return status()

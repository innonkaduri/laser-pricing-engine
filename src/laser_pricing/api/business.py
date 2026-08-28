"""מי מוכר. **בלי זה אסור לקבל הזמנה, ולא ננחש.**

לקוח שמזמין צריך לדעת ממי הוא קונה, ובישראל זו גם חובה: שם העסק,
מספר ח"פ או עוסק מורשה, דרך ליצור קשר, ותקנון. חלק מזה עולה גם
בהצהרת הנגישות (ת"י 5568).

**הכלל של הפרויקט חל כאן בדיוק כמו על מחירים.** מנוע שממציא מחיר
גרוע ממנוע שמחזיר 0; מסך שממציא מספר ח"פ גרוע ממסך שאומר "אי אפשר
להזמין עדיין". לכן אין כאן ערכי ברירת מחדל שנראים אמיתיים — יש
`None`, ויש `blockers()` שאומר בדיוק מה חסר.

הקובץ עצמו יושב ב-`config/business.json`, נכנס ל-git (אין בו סוד —
אלה פרטים שממילא מודפסים על כל חשבונית), ומי שממלא אותו הוא ינון.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

CONFIG_DIR = Path(__file__).resolve().parents[3] / "config"
PATH = Path(os.environ.get("BUSINESS_FILE", CONFIG_DIR / "business.json"))

REQUIRED = {
    "legal_name": "השם המשפטי של העסק, כפי שהוא מופיע בחשבונית",
    "company_id": 'ח"פ או מספר עוסק מורשה',
    "phone": "טלפון שעונים בו — הלקוח מקבל אותו באישור ההזמנה",
    "email": "כתובת מייל לפניות",
    "address": "כתובת העסק",
}

OPTIONAL = {
    "trade_name": "השם המסחרי, אם שונה מהמשפטי",
    "vat_pct": "שיעור המע\"מ שמוצג ללקוח",
    "accessibility_contact": "איש הקשר להצהרת הנגישות",
}


def load() -> dict:
    if not PATH.exists():
        return {}
    try:
        data = json.loads(PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def blockers() -> list[str]:
    """מה חסר כדי שמותר יהיה לקבל הזמנה. **ריק = מותר.**"""
    data = load()
    return [
        f"{key} — {why}"
        for key, why in REQUIRED.items()
        if not str(data.get(key, "")).strip()
    ]


def ready() -> bool:
    return not blockers()


def public() -> dict:
    """מה שמותר להראות בכל מסך. **אין כאן שדה שאינו נועד לפרסום.**"""
    data = load()
    out = {key: data.get(key, "") for key in (*REQUIRED, *OPTIONAL)}
    out["ready"] = ready()
    out["missing"] = blockers()
    return out

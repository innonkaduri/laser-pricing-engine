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


class BusinessError(ValueError):
    """קלט לא תקין לטופס פרטי העסק. תמיד מפורש — לא ברירת מחדל שקטה."""


def save(data: dict) -> dict:
    """שומר פרטי עסק חדשים, אחרי בדיקה. מחזיר את `public()` העדכני.

    **אותו כלל כמו מחיר שממציאים.** שדה חובה ריק נדחה כאן במפורש
    ולא הופך בשקט למחרוזת ריקה בקובץ — אחרת ההזמנה הייתה נפתחת על
    בסיס נתון שאיש לא הזין.

    כתיבה אטומית (קובץ זמני + `replace`): קריאה שמתרחשת באותו רגע
    (checkout, /dashboard) לעולם לא רואה קובץ חצי-כתוב.
    """
    cleaned: dict = {}
    for key, why in REQUIRED.items():
        value = str(data.get(key) or "").strip()
        if not value:
            raise BusinessError(f"{key} הוא שדה חובה ({why}) — אי אפשר לשמור אותו ריק.")
        cleaned[key] = value

    vat_raw = data.get("vat_pct", 0)
    try:
        vat = float(vat_raw) if vat_raw not in ("", None) else 0.0
    except (TypeError, ValueError):
        raise BusinessError("vat_pct חייב להיות מספר.") from None
    if not 0 <= vat <= 100:
        raise BusinessError("vat_pct חייב להיות בין 0 ל-100.")
    cleaned["vat_pct"] = int(vat) if vat == int(vat) else vat

    for key in ("trade_name", "accessibility_contact"):
        cleaned[key] = str(data.get(key) or "").strip()

    tmp = PATH.parent / f"{PATH.name}.tmp"
    PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(cleaned, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(PATH)
    return public()

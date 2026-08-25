"""רישום שימוש — טלמטריה על המנוע, ולא מסמך הצעה.

**האישור וההגבלה, מהאב ב-25.8.2026:** "חותמת, חומר, עובי, כמות, סכום,
משתמש. בלי שם לקוח ובלי מזהה שאפשר לגזור ממנו לקוח — זו טלמטריה על
המנוע, לא מסמך הצעה, והגבול מול ה-CRM נשמר." הסיבה שזה נבנה עכשיו:
"'לא נאסף' הוא נכון היום, אבל בעוד חודש הוא עדיין יהיה 'לא נאסף' אם
לא נתחיל לכתוב עכשיו."

**מה שנרשם:** מתי · חומר · עובי · כמות · אורך חיתוך · סכום השורה ·
סכום ההצעה · מי ביקש · האם הופעל מינימום הזמנה.

**מה שלא נרשם, ואין על זה משא ומתן:** שם לקוח, מזהה לקוח, ו**שם
החלק**. האחרון אינו מובן מאליו והוא הסיבה שהרשימה הזאת כתובה כאן:
`part_name` נגזר משם הקובץ שהועלה, ואנשים קוראים לקבצים
"מדף-לברון.dxf". שם קובץ הוא שם לקוח בתחפושת.

**על הדיסק ולא בזיכרון.** גיאומטריות בזיכרון הן החלטה מודעת — הן
נחוצות לשניות. היסטוריה בזיכרון היא אובדן, ו**כל restart היה מאפס
אותה בשקט**. הקובץ יושב ב-`/var/lib/laser` יחד עם שאר מה שאי אפשר
לשחזר מהקוד, והוא נכנס לחבילת הגיבוי שיוצאת מהקופסה.

**JSONL ולא מסד:** הוספת שורה היא הכתיבה הבטוחה ביותר ללוג — כתיבה
שנקטעת מאבדת רשומה אחת ולא את הקובץ, ואפשר לקרוא אותו בעין על
הקופסה בלי כלי.
"""

from __future__ import annotations

import json
import os
import time
from collections import defaultdict
from pathlib import Path

CONFIG_DIR = Path(__file__).resolve().parents[3] / "config"
LOG_PATH = Path(os.environ.get("USAGE_LOG", CONFIG_DIR / "usage.jsonl"))

MAX_BYTES = 10 * 1024 * 1024
"""מעל זה הקובץ מתגלגל לקובץ חדש. **גלגול, לא מחיקה.**

הקופסה משותפת ל-ADI, ימיש, SIA ו-Arena, וצמיחה לא-חסומה שם היא
בעיה של כולם. המספר נגזר מ-`MAX_LINES_READ`: רשומה נמדדה ב-~270
בתים, ולכן 10MB הם ~37,000 שורות — **מתחת לתקרת הקריאה**, כך
שהקובץ החי תמיד נקרא במלואו ואין מצב שבו הצבירה מציגה חלק ממנו
בלי לדעת.

הקובץ הישן נשמר בשם `usage-<חותמת>.jsonl` לצידו, ונכנס לגיבוי.
שום שורה אינה נמחקת.
"""

MAX_LINES_READ = 50_000
"""כמה שורות נקראות לצורך הצבירה בדשבורד.

התקרה קיימת כדי שמסך שנפתח מהטלפון לא ייתקע על קובץ שגדל שנים.
כשהיא נחצית, הדשבורד מדווח על כך במפורש במקום להציג סיכום חלקי
שנראה שלם.
"""

WRITE_FAILURES: list[str] = []
"""כתיבות שנכשלו, בזיכרון התהליך.

לוג שנכשל בשקט גרוע מלוג שאינו קיים, כי הדשבורד ימשיך להראות מספר
שנראה מלא. הכישלונות מדווחים במסך.
"""


def record_quote(quote, user) -> None:
    """רושם תמחור שהצליח. **לעולם אינו מפיל את הבקשה.**

    ההצעה היא המוצר; הרישום הוא תצפית עליה. אם הכתיבה נכשלה — הלקוח
    מקבל את המחיר שלו, והכישלון מופיע בדשבורד.
    """
    try:
        row = {
            "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "by": user.username if user is not None else "",
            "source": user.source if user is not None else "open",
            "total": quote.total,
            "currency": quote.currency,
            "min_order_applied": bool(quote.min_order_applied),
            "lines": [
                {
                    # **בלי `part_name`** — הוא שם הקובץ שהועלה.
                    "material": line.material_name,
                    "thickness_mm": line.thickness_mm,
                    "quantity": line.quantity,
                    "cut_length_mm": round(line.cut_length_mm, 1),
                    "line_total": line.line_total,
                }
                for line in quote.lines
            ],
        }
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        _rotate_if_needed()
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001 — לוג לא מפיל תמחור
        WRITE_FAILURES.append(f"{time.strftime('%H:%M:%S')} {type(exc).__name__}: {exc}")
        del WRITE_FAILURES[:-20]


def _rotate_if_needed() -> None:
    """מגלגל את הקובץ כשהוא גדול מדי. **אינו מוחק דבר.**

    `rename` ולא העתקה: הוא אטומי, ולכן אין רגע שבו שורה נכתבת לקובץ
    שכבר אינו הקובץ החי.

    **החותמת לבדה אינה מספיקה, וזה נתפס בבדיקה:** היא ברזולוציית
    שנייה, ושני גלגולים באותה שנייה נתנו את אותו שם — ו-`rename` דרס
    את הקודם בשקט. כלומר "גלגול ולא מחיקה" היה מוחק. הסיומת המונה
    מבטיחה שם פנוי, ו-`os.link` היה חלופה מסובכת יותר לאותה בעיה.
    """
    try:
        if LOG_PATH.stat().st_size < MAX_BYTES:
            return
    except OSError:
        return
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = LOG_PATH.with_name(f"{LOG_PATH.stem}-{stamp}.jsonl")
    serial = 1
    while target.exists():
        target = LOG_PATH.with_name(f"{LOG_PATH.stem}-{stamp}-{serial}.jsonl")
        serial += 1
    LOG_PATH.rename(target)


def _rotated_files() -> list[Path]:
    try:
        return sorted(LOG_PATH.parent.glob(f"{LOG_PATH.stem}-*.jsonl"))
    except OSError:
        return []


def _read_rows() -> tuple[list[dict], bool]:
    """(שורות, האם נחתך בתקרה). שורה פגומה מדולגת ולא מפילה את המסך."""
    try:
        raw = LOG_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ([], False)
    truncated = len(raw) > MAX_LINES_READ
    rows = []
    for line in raw[-MAX_LINES_READ:]:
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue  # שורה שנקטעה באמצע כתיבה. אחת, לא הקובץ.
    return (rows, truncated)


def summary(recent: int = 10) -> dict:
    """סיכום לדשבורד.

    **אפס שורות מדווח כ`collected: false` ולא כאפס** — הוראת האב:
    "אפס שורות ממשיך לומר 'לא נאסף'. זה בדיוק ההבדל שדרשתי." אפס
    במסך נקרא כמדידה ("לא יצאו הצעות"), והוא אינו מדידה.
    """
    rows, truncated = _read_rows()
    if not rows:
        return {
            "collected": False,
            "reason": (
                "הרישום פעיל ועדיין לא נרשם אף תמחור."
                if LOG_PATH.exists()
                else "הרישום עדיין לא התחיל — לא נכתב אף תמחור."
            ),
            "write_failures": len(WRITE_FAILURES),
            "rotated_files": len(_rotated_files()),
        }

    by_material: dict[tuple[str, float], dict] = defaultdict(
        lambda: {"quotes": 0, "quantity": 0, "amount": 0.0, "cut_length_m": 0.0}
    )
    total_amount = 0.0
    for row in rows:
        total_amount += float(row.get("total") or 0.0)
        for line in row.get("lines", []):
            key = (str(line.get("material", "—")), float(line.get("thickness_mm") or 0))
            bucket = by_material[key]
            bucket["quotes"] += 1
            bucket["quantity"] += int(line.get("quantity") or 0)
            bucket["amount"] += float(line.get("line_total") or 0.0)
            bucket["cut_length_m"] += float(line.get("cut_length_mm") or 0.0) / 1000.0

    breakdown = sorted(
        (
            {
                "material": material,
                "thickness_mm": thickness,
                "quotes": v["quotes"],
                "quantity": v["quantity"],
                "amount": round(v["amount"], 2),
                "cut_length_m": round(v["cut_length_m"], 2),
            }
            for (material, thickness), v in by_material.items()
        ),
        key=lambda r: -r["amount"],
    )

    return {
        "collected": True,
        "count": len(rows),
        "total_amount": round(total_amount, 2),
        "currency": rows[-1].get("currency", "ILS"),
        "first_at": rows[0].get("at"),
        "last_at": rows[-1].get("at"),
        "min_order_count": sum(1 for r in rows if r.get("min_order_applied")),
        "by_material": breakdown,
        "recent": rows[-recent:][::-1],
        "truncated": truncated,
        "max_lines_read": MAX_LINES_READ,
        # קבצים שהתגלגלו אינם נקראים לצבירה — הם בגיבוי ובדיסק, והמסך
        # אומר כמה יש כדי שלא ייראה כאילו זו כל ההיסטוריה.
        "rotated_files": len(_rotated_files()),
        "write_failures": len(WRITE_FAILURES),
    }

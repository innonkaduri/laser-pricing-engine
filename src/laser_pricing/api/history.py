"""היסטוריית ההצעות של המשתמש — מה שהוא עצמו הגדיר, ותו לא.

**הגבול הזה נקבע ב-19.8 ומצוין כאן במפורש, כי הקובץ הזה הכי קרוב
אליו מכל מה שנכתב עד היום.** `usage.jsonl` הוא טלמטריה על המנוע:
מה נתמחר, בכמה, ובאיזה חומר — בלי שם לקוח, בלי שם קובץ, ובלי שום
שדה שאפשר לגזור ממנו לקוח. הוא **לא** מסמך.

מה שכאן הוא הדבר השני: **ההיסטוריה של המשתמש מול עצמו.** הוא הזיז
מחוונים, קיבל מחיר, ורוצה לחזור אליו מחר. זו אינה רשומת לקוח של
ימיש — היא רשומה של המשתמש על עצמו, והיא נראית רק לו.

מה שנשמר: מוצר, מידות, חומר, עובי, כמות וסכום. **מה שאינו נשמר,
ואין על זה משא ומתן:**

- **שם הקובץ שהועלה.** זה השדה שסגר האב ב-26.8: "רשימת שדות אסורים
  תמיד חלקית; השאלה הנכונה אינה איזה שדה נושא מידע אישי אלא **איזה
  שדה מחושב משדה כזה**." `part_name` נגזר מהקובץ, והקובץ נקרא
  לעיתים קרובות על שם הלקוח.
- **כל שדה חופשי.** אין הערות ואין תיאור. ברגע שיש שדה טקסט חופשי,
  מישהו יכתוב בו "לברון — לתאם משלוח", וההיסטוריה תהפוך ל-CRM.

אם יום אחד יידרש מסמך הצעה עם שם לקוח — הוא נכתב ב-CRM, לא כאן.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

CONFIG_DIR = Path(__file__).resolve().parents[3] / "config"
DB_PATH = Path(os.environ.get("QUOTES_DB", CONFIG_DIR / "quotes.db"))
"""מסד ההיסטוריה. **חייב לשבת מחוץ לעץ ה-git**, כמו `users.db`.

`QUOTES_DB` נמצא ב-`REQUIRED_ENV` מאותה סיבה בדיוק כמו השאר: בלעדיו
השירות עולה, נראה תקין, וכותב את ההיסטוריה של כל המשתמשים לתיקייה
ש-`git clean -fdx` מוחקת.
"""

KEEP_PER_USER = 200
"""כמה הצעות נשמרות למשתמש. **הישנה נופלת, לא הכול.**

תקרה נדרשת כי ההרשמה ציבורית וכל תמחור כותב שורה. 200 הצעות למשתמש
הן היסטוריה שאדם באמת גולל בה, ובקצב של 12 תמחורים לשעה זה יותר
מיום עבודה רצוף. גלגול ולא מחיקה — אותה החלטה כמו ברישום השימוש.
"""

MAX_PAGE = 100


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE IF NOT EXISTS quotes (
               id            INTEGER PRIMARY KEY AUTOINCREMENT,
               username      TEXT NOT NULL,
               at            TEXT NOT NULL,
               source        TEXT NOT NULL,
               product_id    TEXT NOT NULL DEFAULT '',
               product_name  TEXT NOT NULL DEFAULT '',
               dimensions    TEXT NOT NULL DEFAULT '{}',
               material_key  TEXT NOT NULL DEFAULT '',
               material_name TEXT NOT NULL DEFAULT '',
               thickness_mm  REAL NOT NULL DEFAULT 0,
               quantity      INTEGER NOT NULL DEFAULT 1,
               total         REAL NOT NULL DEFAULT 0,
               currency      TEXT NOT NULL DEFAULT 'ILS',
               min_order     INTEGER NOT NULL DEFAULT 0,
               cut_length_mm REAL NOT NULL DEFAULT 0,
               bend_count    INTEGER NOT NULL DEFAULT 0
           )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS quotes_by_user ON quotes (username, id DESC)")
    return conn


WRITE_FAILURES: list[str] = []
"""כתיבות שנכשלו, בזיכרון התהליך.

**היסטוריה שנכשלת בשקט גרועה מהיסטוריה שאין.** המשתמש יחשוב שההצעה
נשמרה ויחפש אותה מחר. המונה הזה מוצג בדשבורד.
"""


def record(
    *,
    username: str,
    source: str,
    quote,
    material_key: str,
    thickness_mm: float,
    quantity: int,
    product_id: str = "",
    product_name: str = "",
    dimensions: dict | None = None,
    bend_count: int = 0,
) -> None:
    """שומר הצעה אחת. **לעולם אינו מפיל את התמחור.**

    התמחור הצליח והמשתמש מחכה למספר. כישלון בכתיבת ההיסטוריה הוא
    תקלה שלנו, לא שלו, והוא נספר ומדווח בדשבורד במקום להיזרק אליו
    בפנים.
    """
    if not username:
        return  # קישור העריכה של אבא ואורח בפיתוח — אין למי לשייך

    line = quote.lines[0] if getattr(quote, "lines", None) else None
    try:
        with _connect() as conn:
            conn.execute(
                """INSERT INTO quotes (
                       username, at, source, product_id, product_name, dimensions,
                       material_key, material_name, thickness_mm, quantity,
                       total, currency, min_order, cut_length_mm, bend_count
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    username,
                    time.strftime("%Y-%m-%dT%H:%M:%S"),
                    source,
                    product_id,
                    product_name,
                    json.dumps(dimensions or {}, ensure_ascii=False),
                    # `material_key` אינו על `QuoteLine` — היא נושאת שם
                    # לתצוגה. המפתח מגיע מהקורא, שהוא זה שביקש אותו.
                    material_key,
                    getattr(line, "material_name", "") if line else "",
                    float(thickness_mm),
                    int(quantity),
                    float(quote.total),
                    quote.currency,
                    1 if getattr(quote, "min_order_applied", False) else 0,
                    float(getattr(line, "cut_length_mm", 0) or 0) if line else 0.0,
                    bend_count,
                ),
            )
            conn.execute(
                """DELETE FROM quotes WHERE username = ? AND id NOT IN (
                       SELECT id FROM quotes WHERE username = ? ORDER BY id DESC LIMIT ?
                   )""",
                (username, username, KEEP_PER_USER),
            )
    except (sqlite3.Error, OSError) as exc:
        WRITE_FAILURES.append(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {exc}")
        del WRITE_FAILURES[:-50]


def _row_to_json(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "at": row["at"],
        "source": row["source"],
        "product_id": row["product_id"],
        "product_name": row["product_name"],
        "dimensions": json.loads(row["dimensions"] or "{}"),
        "material_name": row["material_name"],
        "material_key": row["material_key"],
        "thickness_mm": row["thickness_mm"],
        "quantity": row["quantity"],
        "total": round(row["total"], 2),
        "currency": row["currency"],
        "min_order_applied": bool(row["min_order"]),
        "cut_length_mm": round(row["cut_length_mm"], 1),
        "bend_count": row["bend_count"],
    }


def for_user(username: str, limit: int = 50, before: int | None = None) -> dict:
    """הצעות המשתמש, החדשה ראשונה. **מסונן לפי שם המשתמש בשאילתה.**

    הסינון הוא ב-`WHERE` ולא בקוד שקורא הכול ואז בורר: שאילתה שמביאה
    את כל השורות ומסננת אחר כך היא באג הרשאה שממתין לשורה אחת שתשכח
    את הסינון.
    """
    limit = max(1, min(int(limit), MAX_PAGE))
    with _connect() as conn:
        if before:
            rows = conn.execute(
                "SELECT * FROM quotes WHERE username = ? AND id < ? ORDER BY id DESC LIMIT ?",
                (username, int(before), limit + 1),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM quotes WHERE username = ? ORDER BY id DESC LIMIT ?",
                (username, limit + 1),
            ).fetchall()
        totals = conn.execute(
            "SELECT COUNT(*) c, COALESCE(SUM(total),0) s FROM quotes WHERE username = ?",
            (username,),
        ).fetchone()

    more = len(rows) > limit
    items = [_row_to_json(r) for r in rows[:limit]]
    return {
        "items": items,
        "has_more": more,
        "next_before": items[-1]["id"] if more and items else None,
        "count": totals["c"],
        "total_amount": round(totals["s"], 2),
        "keep_per_user": KEEP_PER_USER,
    }


def one(username: str, quote_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM quotes WHERE id = ? AND username = ?", (int(quote_id), username)
        ).fetchone()
    return _row_to_json(row) if row else None


def delete(username: str, quote_id: int) -> bool:
    with _connect() as conn:
        return bool(
            conn.execute(
                "DELETE FROM quotes WHERE id = ? AND username = ?", (int(quote_id), username)
            ).rowcount
        )


def clear(username: str) -> int:
    with _connect() as conn:
        return conn.execute("DELETE FROM quotes WHERE username = ?", (username,)).rowcount


def stats() -> dict:
    """מספרים לדשבורד. **בלי לחשוף הצעה של אף אחד.**"""
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) c, COUNT(DISTINCT username) u, COALESCE(SUM(total),0) s FROM quotes"
            ).fetchone()
            recent = conn.execute(
                "SELECT COUNT(*) c FROM quotes WHERE at >= ?",
                (time.strftime("%Y-%m-%dT00:00:00"),),
            ).fetchone()
    except (sqlite3.Error, OSError) as exc:
        return {"available": False, "reason": str(exc), "write_failures": len(WRITE_FAILURES)}
    return {
        "available": True,
        "count": row["c"],
        "users_with_quotes": row["u"],
        "total_amount": round(row["s"], 2),
        "today": recent["c"],
        "keep_per_user": KEEP_PER_USER,
        "write_failures": len(WRITE_FAILURES),
    }


__all__ = ["DB_PATH", "KEEP_PER_USER", "WRITE_FAILURES", "clear", "delete", "for_user", "one", "record", "stats"]

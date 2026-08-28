"""עגלה והזמנות. **הדבר השלישי, והוא מסמך — לא טלמטריה.**

במערכת הזאת יש שלושה רישומים, וההבחנה ביניהם נקבעה לפני הקובץ הזה:

1. `usage.jsonl` — **טלמטריה על המנוע.** מה נתמחר ובכמה. בלי שם
   לקוח, בלי שם קובץ, ובלי שדה שאפשר לגזור ממנו לקוח. הוראת האב.
2. `quotes.db` — **המשתמש מול עצמו.** הוא הזיז מחוונים, קיבל מחיר,
   ורוצה לחזור אליו מחר. נראה רק לו.
3. `orders.db` — **כאן.** הזמנה היא מסמך מסחרי: מישהו ביקש שיחתכו
   לו, ומישהו צריך להתקשר אליו. לכן **כן** נשמר טלפון, ו**כן**
   נשמרת הערה. זה אינו סותר את הכלל של האב — הוא נאמר במפורש על
   הטלמטריה ("זו טלמטריה על המנוע, לא מסמך הצעה"), וזה הצד השני
   של אותו גבול.

**מה שעדיין אינו נשמר:** שם הקובץ שהועלה, ושום שדה שנגזר ממנו.
הזמנה נושאת את מה שהלקוח **הקליד**, לא את מה שהמערכת ניחשה עליו.

**המחיר אינו נשמר בעגלה, והוא מחושב מחדש בכל צפייה.** זו ההחלטה
המרכזית כאן. עגלה ששומרת מספר הופכת את עצמה למקור אמת שני, ואז
טבלה שהתעדכנה בבוקר אינה משנה עגלה שנפתחה אמש — הלקוח משלם מחיר
שאיש לא אישר. מה שנשמר הוא **המפרט**: הגיאומטריה, החומר, העובי
והכמות. המחיר מגיע מהטבלה, תמיד, כמו בכל מקום אחר.

**ולכן העגלה גם מוזילה.** כל הפריטים מתומחרים ב-`price_order`
אחד, כלומר מנוסטים על אותה פלטה. שני חלקים בהזמנה אחת עולים פחות
משני חלקים בשתי הזמנות, וזה נכון פיזית ולא שיווקית.
"""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
import time
from pathlib import Path

CONFIG_DIR = Path(__file__).resolve().parents[3] / "config"
DB_PATH = Path(os.environ.get("ORDERS_DB", CONFIG_DIR / "orders.db"))
"""מסד ההזמנות. **חייב לשבת מחוץ לעץ ה-git**, כמו כל השאר.

`ORDERS_DB` נמצא ב-`REQUIRED_ENV` מאותה סיבה בדיוק: בלעדיו השירות
עולה, נראה תקין, וכותב הזמנות של לקוחות לתיקייה ש-`git clean -fdx`
מוחקת. זה כבר קרה פעם לגיבוי, ופעם כמעט קרה להיסטוריה.
"""

MAX_ITEMS = 40
"""כמה פריטים בעגלה. תקרה נדרשת כי ההרשמה ציבורית, וכי כל צפייה
בעגלה מתמחרת את **כולם** יחד — עגלה בלי גבול היא נסטינג בלי גבול
על קופסה שהיא שתי ליבות לחמישה פרויקטים."""

MAX_NOTE = 500
STATUSES = ("new", "confirmed", "in_production", "done", "cancelled")

WRITE_FAILURES: list[str] = []
"""כישלון כתיבה **נרשם ונראה**, ואינו נבלע. הזמנה שנעלמה בשקט היא
לקוח שמחכה לטלפון שלא יגיע."""


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS cart (
               id           INTEGER PRIMARY KEY AUTOINCREMENT,
               username     TEXT NOT NULL,
               at           REAL NOT NULL,
               name         TEXT NOT NULL DEFAULT '',
               source       TEXT NOT NULL DEFAULT 'editor',
               product_id   TEXT NOT NULL DEFAULT '',
               material_key TEXT NOT NULL,
               thickness_mm REAL NOT NULL,
               quantity     INTEGER NOT NULL DEFAULT 1,
               spec         TEXT NOT NULL
           )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS cart_by_user ON cart(username, id)")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS orders (
               id         INTEGER PRIMARY KEY AUTOINCREMENT,
               ref        TEXT NOT NULL UNIQUE,
               username   TEXT NOT NULL,
               at         REAL NOT NULL,
               status     TEXT NOT NULL DEFAULT 'new',
               phone      TEXT NOT NULL DEFAULT '',
               note       TEXT NOT NULL DEFAULT '',
               total      REAL NOT NULL DEFAULT 0,
               currency   TEXT NOT NULL DEFAULT 'ILS',
               payment    TEXT NOT NULL DEFAULT 'phone',
               paid_at    REAL,
               items      TEXT NOT NULL DEFAULT '[]'
           )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS orders_by_user ON orders(username, id)")
    return conn


# ── עגלה ──────────────────────────────────────────────────────────


def add(
    username: str,
    *,
    name: str,
    source: str,
    product_id: str,
    material_key: str,
    thickness_mm: float,
    quantity: int,
    spec: dict,
) -> int:
    """מוסיף פריט. **המפרט נשמר, המחיר לא.**"""
    if not username:
        raise ValueError("עגלה שייכת למשתמש מחובר.")
    with _connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM cart WHERE username = ?", (username,)
        ).fetchone()[0]
        if count >= MAX_ITEMS:
            raise ValueError(f"בעגלה כבר {MAX_ITEMS} פריטים — המקסימום. הסר פריט כדי להוסיף.")
        cursor = conn.execute(
            """INSERT INTO cart (username, at, name, source, product_id,
                                 material_key, thickness_mm, quantity, spec)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                username,
                time.time(),
                name[:120],
                source,
                product_id[:80],
                material_key,
                float(thickness_mm),
                max(1, int(quantity)),
                json.dumps(spec, ensure_ascii=False),
            ),
        )
        return int(cursor.lastrowid or 0)


def items(username: str) -> list[dict]:
    """**הסינון ב-SQL ולא בפייתון.** עגלה של מישהו אחר לא נטענת
    לזיכרון כדי להיזרק אחר כך — זו הדרך שבה דליפה נולדת."""
    if not username:
        return []
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM cart WHERE username = ? ORDER BY id", (username,)
        ).fetchall()
    return [
        {
            "id": row["id"],
            "name": row["name"],
            "source": row["source"],
            "product_id": row["product_id"],
            "material_key": row["material_key"],
            "thickness_mm": row["thickness_mm"],
            "quantity": row["quantity"],
            "spec": json.loads(row["spec"]),
        }
        for row in rows
    ]


def remove(username: str, item_id: int) -> bool:
    with _connect() as conn:
        cursor = conn.execute(
            "DELETE FROM cart WHERE username = ? AND id = ?", (username, item_id)
        )
        return cursor.rowcount > 0


def set_quantity(username: str, item_id: int, quantity: int) -> bool:
    with _connect() as conn:
        cursor = conn.execute(
            "UPDATE cart SET quantity = ? WHERE username = ? AND id = ?",
            (max(1, int(quantity)), username, item_id),
        )
        return cursor.rowcount > 0


def clear(username: str) -> int:
    with _connect() as conn:
        return conn.execute("DELETE FROM cart WHERE username = ?", (username,)).rowcount


def count(username: str) -> int:
    if not username:
        return 0
    with _connect() as conn:
        return int(
            conn.execute(
                "SELECT COUNT(*) FROM cart WHERE username = ?", (username,)
            ).fetchone()[0]
        )


# ── הזמנות ────────────────────────────────────────────────────────


def _ref() -> str:
    """מזהה הזמנה שאפשר להקריא בטלפון.

    **לא מספר רץ.** מספר רץ מספר לכל לקוח כמה הזמנות היו לפניו,
    וגם מאפשר לנחש הזמנה של מישהו אחר. שנה + חמישה תווים.
    """
    alphabet = "ACDEFGHJKLMNPQRSTUVWXYZ34679"
    tail = "".join(secrets.choice(alphabet) for _ in range(5))
    return f"{time.strftime('%y')}-{tail}"


def place(
    username: str,
    *,
    phone: str,
    note: str,
    total: float,
    currency: str,
    payment: str,
    priced_items: list[dict],
) -> dict:
    """הופך עגלה להזמנה, ומרוקן אותה. **בטרנזקציה אחת.**

    אילו הריקון היה נפרד, נפילה באמצע הייתה משאירה לקוח עם הזמנה
    שנרשמה ועגלה שעדיין מלאה — והוא היה מזמין פעמיים.
    """
    if not priced_items:
        raise ValueError("אין מה להזמין — העגלה ריקה.")
    ref = _ref()
    with _connect() as conn:
        conn.execute(
            """INSERT INTO orders (ref, username, at, status, phone, note,
                                   total, currency, payment, items)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                ref,
                username,
                time.time(),
                "new",
                phone[:40],
                note[:MAX_NOTE],
                round(float(total), 2),
                currency,
                payment,
                json.dumps(priced_items, ensure_ascii=False),
            ),
        )
        conn.execute("DELETE FROM cart WHERE username = ?", (username,))
    return one(username, ref) or {}


def listing(username: str, limit: int = 30) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM orders WHERE username = ? ORDER BY id DESC LIMIT ?",
            (username, max(1, min(limit, 100))),
        ).fetchall()
    return [_row(row, with_items=False) for row in rows]


def one(username: str, ref: str) -> dict | None:
    """**הסינון לפי משתמש בשאילתה.** מזהה הזמנה אינו סיסמה."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM orders WHERE username = ? AND ref = ?", (username, ref)
        ).fetchone()
    return _row(row) if row else None


def all_orders(limit: int = 100) -> list[dict]:
    """כל ההזמנות — **לבית המלאכה בלבד.** נשמר מאחורי `quote:use`."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM orders ORDER BY id DESC LIMIT ?", (max(1, min(limit, 500)),)
        ).fetchall()
    return [_row(row) for row in rows]


def set_status(ref: str, status: str) -> bool:
    if status not in STATUSES:
        raise ValueError(f"מצב לא מוכר: {status}. המצבים: {', '.join(STATUSES)}")
    with _connect() as conn:
        return (
            conn.execute("UPDATE orders SET status = ? WHERE ref = ?", (status, ref)).rowcount
            > 0
        )


def _row(row: sqlite3.Row, with_items: bool = True) -> dict:
    out = {
        "ref": row["ref"],
        "at": row["at"],
        "status": row["status"],
        "phone": row["phone"],
        "note": row["note"],
        "total": row["total"],
        "currency": row["currency"],
        "payment": row["payment"],
        "paid_at": row["paid_at"],
    }
    if with_items:
        out["items"] = json.loads(row["items"])
    else:
        out["item_count"] = len(json.loads(row["items"]))
    return out

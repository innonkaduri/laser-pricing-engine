"""איך משלמים. **תפר אחד, ורשימה סגורה — כמו הזהות והבנאים.**

היום יש ספק אחד שעובד: `phone` — ההזמנה נרשמת, בית המלאכה מתקשר,
והתשלום נסגר בטלפון או בהעברה. זה לא "פתרון זמני עד שיהיה אמיתי";
זו הדרך שבה רוב עבודות החיתוך בישראל נסגרות בפועל, כי המחיר תלוי
בבדיקת הקובץ.

**סליקת אשראי אינה כתובה כאן, ובכוונה.** אי אפשר לכתוב אינטגרציה
לספק סליקה בלי מספר ספק, בלי מפתחות, ובלי החלטה עסקית מי הספק —
ומודול שמעמיד פנים שהוא סולק היה הגרסה התשלומית של "מנוע שממציא
מחיר". במקום זה יש כאן **תפר**: `PROVIDERS` היא רשימה סגורה,
`start()` מקבלת הזמנה ומחזירה מה לעשות הלאה, וספק שאין לו הגדרות
**נכשל במפורש** ואינו מופיע למשתמש מלכתחילה.

מה שיידרש כדי להדליק כרטיס אשראי — וזה מה שחסר, לא הקוד:
1. החלטה מי הספק (טרנזילה · קארדקום · משולם · PayPlus · Stripe).
2. מספר ספק ומפתחות, ב-`/etc/laser-pricing.env`.
3. ח"פ ותקנון — ראה `business.py`. בלעדיהם ממילא אסור לגבות.
"""

from __future__ import annotations

import os

PHONE = "phone"

PROVIDERS: dict[str, dict] = {
    PHONE: {
        "label": "הזמנה וסגירה בטלפון",
        "detail": "נרשמת ההזמנה, ובית המלאכה חוזר אליך לאישור ולתשלום.",
        "needs": (),
    },
    "card": {
        "label": "כרטיס אשראי",
        "detail": "סליקה מקוונת.",
        # **שמות המשתנים ולא הערכים.** הערכים חיים בקובץ הסביבה.
        "needs": ("PAYMENT_PROVIDER", "PAYMENT_TERMINAL", "PAYMENT_KEY"),
    },
}


class PaymentError(RuntimeError):
    """תשלום שאי אפשר לבצע. **תמיד מפורש.**"""


def configured(key: str) -> bool:
    spec = PROVIDERS.get(key)
    if spec is None:
        return False
    return all(os.environ.get(name, "").strip() for name in spec["needs"])


def available() -> list[dict]:
    """מה שמותר להציע. **מסך אינו מציע בחירה שנועדה להיכשל** —
    זה כלל שכבר נכתב כאן פעם, אחרי שהקטלוג הציע חומר בלי מחיר."""
    return [
        {"key": key, "label": spec["label"], "detail": spec["detail"]}
        for key, spec in PROVIDERS.items()
        if configured(key)
    ]


def start(key: str, order: dict) -> dict:
    """מתחיל תשלום. מחזיר מה המסך צריך לעשות הלאה."""
    if key not in PROVIDERS:
        raise PaymentError(f"אמצעי תשלום לא מוכר: {key}")
    if not configured(key):
        missing = ", ".join(
            name for name in PROVIDERS[key]["needs"] if not os.environ.get(name, "").strip()
        )
        raise PaymentError(
            f"אמצעי התשלום {PROVIDERS[key]['label']!r} אינו מוגדר בשרת. חסר: {missing}."
        )
    if key == PHONE:
        return {
            "kind": "none",
            "message": "ההזמנה נרשמה. נחזור אליך בטלפון לאישור ולתשלום.",
        }
    # אין כאן ענף שני, וזו הנקודה: ספק שנוסף ל-PROVIDERS בלי מימוש
    # ייפול כאן ברעש ולא יגבה כסף בשקט.
    raise PaymentError(
        f"אמצעי התשלום {key!r} מוגדר אך אינו ממומש. אין לגבות כסף דרך מסלול שלא נכתב."
    )

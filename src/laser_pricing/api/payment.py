"""איך משלמים. **תפר אחד, רשימה סגורה, וכסף שלא מסומן בשקט.**

שני ספקים:

- `phone` — ההזמנה נרשמת, בית המלאכה מתקשר, והתשלום נסגר בשיחה או
  בהעברה. זו הדרך שבה רוב עבודות החיתוך בישראל נסגרות בפועל, כי
  המחיר תלוי בבדיקת הקובץ. עובד היום, בלי שום הגדרה.
- `tranzila` — סליקת אשראי דרך **עמוד הסליקה המתארח** של טרנזילה.

**למה עמוד מתארח ולא API ישיר.** לטרנזילה יש גם ממשק שרת-לשרת
שמקבל מספר כרטיס. מי שמשתמש בו מכניס את העסק לתחולת **PCI-DSS**:
מספרי כרטיס עוברים בשרת, נכתבים ללוגים בטעות, ויושבים בגיבויים.
כאן הכרטיס **אינו נוגע בשרת שלנו אף פעם** — הלקוח מקליד אותו בעמוד
של טרנזילה, ואנחנו מקבלים תשובה. זו החלטה שלא כדאי להפוך.

**ומה שמסמן "שולם" הוא לא הדפדפן.**

זה הלב של הקובץ. אחרי תשלום הלקוח חוזר לאתר בהפניה, ואת ההפניה
הזאת אפשר לזייף: מי שיודע את הכתובת יכול לפתוח אותה ביד ולהיראות
כמי ששילם. לכן:

1. **ההפניה בדפדפן אינה משנה כלום.** היא מציגה "בודקים".
2. **רק `notify` — קריאה שרת-לשרת מטרנזילה — מסמנת שולם**, והיא
   מגיעה לכתובת שיש בה סוד משלנו (`TRANZILA_NOTIFY_SECRET`).
3. **וגם אז הסכום נבדק.** אם מה שדווח אינו שווה למה שחושב, ההזמנה
   **אינה** מסומנת כשולמה ונרשמת אי-התאמה. תשלום חלקי שמסומן כמלא
   הוא הפסד כסף שאיש לא רואה.

**מה שצריך לאמת מול טרנזילה כשיגיעו המפתחות**, ואינו ניחוש שאני
מסתיר: שמות שדות התשובה משתנים בין מסופים וגרסאות. הקוד מחפש את
`Response` (‎000 = אושר) ואת הסכום בכמה שמות מקובלים, ו**אם הוא
לא מצא — הוא אינו מסמן שולם ורושם את המטען הגולמי כפי שהוא**.
כישלון סגור, לא כישלון פתוח. בדיקת קצה אחת מול מסוף אמיתי סוגרת
את זה סופית.
"""

from __future__ import annotations

import os

PHONE = "phone"
TRANZILA = "tranzila"

TRANZILA_BASE = "https://direct.tranzila.com"
CURRENCY_ILS = "1"

PROVIDERS: dict[str, dict] = {
    PHONE: {
        "label": "הזמנה וסגירה בטלפון",
        "detail": "נרשמת ההזמנה, ובית המלאכה חוזר אליך לאישור ולתשלום.",
        "needs": (),
    },
    TRANZILA: {
        "label": "כרטיס אשראי",
        "detail": "תשלום מאובטח בעמוד של טרנזילה. פרטי הכרטיס אינם עוברים דרך האתר.",
        # **שמות המשתנים ולא הערכים.** הערכים חיים בקובץ הסביבה.
        "needs": ("TRANZILA_TERMINAL", "TRANZILA_NOTIFY_SECRET", "PUBLIC_BASE_URL"),
    },
}

APPROVED = "000"
"""קוד התשובה של טרנזילה לעסקה שאושרה. כל דבר אחר אינו תשלום."""

_SUM_FIELDS = ("sum", "Sum", "amount", "Amount")
_RESPONSE_FIELDS = ("Response", "response", "ResponseCode")
_REF_FIELDS = ("order_ref", "ORDER_REF", "orderref")
_CONFIRM_FIELDS = ("ConfirmationCode", "confirmationcode", "Tempref", "index")


class PaymentError(RuntimeError):
    """תשלום שאי אפשר לבצע. **תמיד מפורש.**"""


def configured(key: str) -> bool:
    spec = PROVIDERS.get(key)
    if spec is None:
        return False
    return all(os.environ.get(name, "").strip() for name in spec["needs"])


def missing(key: str) -> list[str]:
    spec = PROVIDERS.get(key)
    if spec is None:
        return []
    return [name for name in spec["needs"] if not os.environ.get(name, "").strip()]


def available() -> list[dict]:
    """מה שמותר להציע. **מסך אינו מציע בחירה שנועדה להיכשל** —
    זה כלל שכבר נכתב כאן פעם, אחרי שהקטלוג הציע חומר בלי מחיר."""
    return [
        {"key": key, "label": spec["label"], "detail": spec["detail"]}
        for key, spec in PROVIDERS.items()
        if configured(key)
    ]


def notify_secret() -> str:
    return os.environ.get("TRANZILA_NOTIFY_SECRET", "").strip()


def _base_url() -> str:
    return os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")


def start(key: str, order: dict) -> dict:
    """מתחיל תשלום. מחזיר מה המסך צריך לעשות הלאה."""
    if key not in PROVIDERS:
        raise PaymentError(f"אמצעי תשלום לא מוכר: {key}")
    if not configured(key):
        raise PaymentError(
            f"אמצעי התשלום {PROVIDERS[key]['label']!r} אינו מוגדר בשרת. "
            f"חסר: {', '.join(missing(key))}."
        )
    if key == PHONE:
        return {
            "kind": "none",
            "message": "ההזמנה נרשמה. נחזור אליך בטלפון לאישור ולתשלום.",
        }
    if key == TRANZILA:
        return _tranzila_redirect(order)
    # ספק שנוסף לרשימה בלי מימוש ייפול כאן ברעש ולא יגבה כסף בשקט.
    raise PaymentError(
        f"אמצעי התשלום {key!r} מוגדר אך אינו ממומש. אין לגבות כסף דרך מסלול שלא נכתב."
    )


def _tranzila_redirect(order: dict) -> dict:
    """בונה את הכתובת של עמוד הסליקה.

    **הסכום נשלח בשקלים עם שתי ספרות.** טרנזילה מקבלת `sum` כמספר
    עשרוני; שליחה באגורות הייתה מכפילה את החיוב פי מאה, וזו טעות
    שמתגלה אצל הלקוח ולא אצלנו.
    """
    from urllib.parse import urlencode

    terminal = os.environ["TRANZILA_TERMINAL"].strip()
    total = round(float(order["total"]), 2)
    if not total > 0:
        raise PaymentError("אין לשלוח לסליקה סכום שאינו חיובי.")
    base = _base_url()
    params = {
        "sum": f"{total:.2f}",
        "currency": CURRENCY_ILS,
        "cred_type": "1",
        "tranmode": "A",
        "lang": "il",
        # מזהה ההזמנה נוסע איתה וחוזר בתשובה. **זה מה שקושר תשלום
        # להזמנה**; בלעדיו קריאת ה-notify אינה יודעת מה לסמן.
        "order_ref": order["ref"],
        "contact": order.get("contact", ""),
        "phone": order.get("phone", ""),
        "success_url_address": f"{base}/payment/return?ref={order['ref']}",
        "fail_url_address": f"{base}/payment/return?ref={order['ref']}&failed=1",
        "notify_url_address": f"{base}/api/payment/tranzila/notify?s={notify_secret()}",
    }
    return {
        "kind": "redirect",
        "url": f"{TRANZILA_BASE}/{terminal}/iframenew.php?{urlencode(params)}",
        "message": "מעבירים אותך לעמוד התשלום המאובטח של טרנזילה.",
    }


def _pick(payload: dict, names: tuple[str, ...]) -> str:
    for name in names:
        value = payload.get(name)
        if value not in (None, ""):
            return str(value)
    return ""


def read_callback(payload: dict, expected_total: float) -> dict:
    """קורא תשובה מטרנזילה ומחליט אם זה תשלום. **כישלון סגור.**

    מוחזר תמיד `ok` בוליאני ו-`reason` בעברית. `ok=True` דורש
    **שלושה** דברים יחד: קוד אישור, מזהה הזמנה, וסכום שתואם. חוסר
    באחד מהם אינו "כנראה בסדר" — הוא אי-תשלום.
    """
    ref = _pick(payload, _REF_FIELDS)
    response = _pick(payload, _RESPONSE_FIELDS)
    raw_sum = _pick(payload, _SUM_FIELDS)

    if not ref:
        return {"ok": False, "ref": "", "reason": "אין מזהה הזמנה בתשובה."}
    if not response:
        return {"ok": False, "ref": ref, "reason": "אין קוד תשובה — לא מסמנים שולם."}
    if response != APPROVED:
        return {"ok": False, "ref": ref, "reason": f"העסקה לא אושרה (קוד {response})."}
    if not raw_sum:
        return {"ok": False, "ref": ref, "reason": "אין סכום בתשובה — לא מסמנים שולם."}
    try:
        paid = round(float(raw_sum), 2)
    except (TypeError, ValueError):
        return {"ok": False, "ref": ref, "reason": f"סכום לא קריא: {raw_sum!r}."}

    # **אגורה אחת של סטייה היא עדיין אי-התאמה.** הסכום שלנו מעוגל
    # לשתי ספרות לפני השליחה, ולכן אין כאן רעש צף לגיטימי.
    if abs(paid - round(float(expected_total), 2)) > 0.005:
        return {
            "ok": False,
            "ref": ref,
            "reason": f"הסכום ששולם ({paid}) אינו הסכום שחושב ({expected_total}).",
        }
    return {
        "ok": True,
        "ref": ref,
        "paid": paid,
        "confirmation": _pick(payload, _CONFIRM_FIELDS),
        "reason": "אושר.",
    }

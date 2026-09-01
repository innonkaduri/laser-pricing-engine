"""דחיפת הזמנה ל-CRM ברגע שהיא נוצרת. **בנוי ושקט עד שיגיעו המפתחות,
בדיוק כמו טרנזילה, גוגל ומייל** — בלי `CRM_WEBHOOK_URL` שום קריאה
לא יוצאת, וכל השאר בקובץ הזה ממשיך לעבוד בדיוק כמו קודם.

**החלטה מפורשת, לא סחיפה: קובץ הלקוח יוצא מהמכונה כאן.** עד
1.9.2026 הכלל היה "שום קובץ של לקוח לא יוצא החוצה" — נכון כשהיה
מדובר בזרים. כאן זה קובץ ה-DXF **של אותה הזמנה שהעסק עצמו קיבל**,
נשלח **רק** ליעד אחד שינון קובע (`CRM_WEBHOOK_URL`), לא לצד שלישי.
זו הרחבה של הגבול מול ה-CRM (ראה `docs/DECISIONS.md`, 19.8.2026),
לא סתירה שלו: הזמנה כבר הייתה "הדבר ההפוך" מטלמטריה אנונימית.

**`urllib` ולא `httpx`** — אותה סיבה בדיוק כמו ב-`google_auth.py`:
`deploy.sh` אינו מתקין תלויות, ותלות חדשה על הקופסה היא השבתה
שמחכה לקרות.

**כישלון כאן לעולם לא מפיל הזמנה.** ה-webhook רץ אחרי שההזמנה כבר
נשמרה ואושרה ללקוח (`BackgroundTasks`), ונכשל בשקט **מבחינת הלקוח**
— אבל לא מבחינתנו: `WEBHOOK_FAILURES` נרשם ונראה ב-`/admin`, כי
הזמנה שה-CRM לא שמע עליה היא עבודה שאובדת, בדיוק כמו הזמנה שנכתבה
ואיש לא ראה במסך (30.8.2026).
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request

from ..cad.dxf_writer import cut_dxf

TIMEOUT_SECONDS = 10.0
"""CRM תקוע לא אמור להחזיק עובד בקופסה עם שתי ליבות לחמישה פרויקטים —
אותו קו מחשבה בדיוק כמו מול גוגל ומול ספק המייל."""

MAX_TRACKED_FAILURES = 200

WEBHOOK_FAILURES: list[tuple[float, str]] = []
"""(מתי, למה). נבדק ומתאפס בבדיקות; ב-`/admin` מוצג רק "מאז ההפעלה"."""


class WebhookError(RuntimeError):
    """קריאה ל-CRM שנכשלה. תמיד מפורש בלוג — אף פעם לא נבלעת בשקט."""


def configured() -> bool:
    return bool(
        os.environ.get("CRM_WEBHOOK_URL", "").strip()
        and os.environ.get("CRM_WEBHOOK_SECRET", "").strip()
    )


def missing() -> list[str]:
    return [
        name
        for name in ("CRM_WEBHOOK_URL", "CRM_WEBHOOK_SECRET")
        if not os.environ.get(name, "").strip()
    ]


def _record_failure(reason: str) -> None:
    WEBHOOK_FAILURES.append((time.time(), reason))
    del WEBHOOK_FAILURES[: -MAX_TRACKED_FAILURES]


def send_order_created(order: dict, pricing: dict, files: list[dict]) -> None:
    """מודיע ל-CRM על הזמנה חדשה. **לא זורק** — קורא ל-`_record_failure`
    ומחזיר, כי זה רץ אחרי שהתשובה כבר נשלחה ללקוח.

    `files` הוא רשימת `{"filename": ..., "content_base64": ...}` —
    קובצי החיתוך, לא הקובץ שהלקוח העלה (זה כבר לא קיים בשלב הזה;
    כל קובץ שנוצר כאן נבנה מחדש מהמפרט השמור, כמו בכל ייצוא DXF
    אחר במערכת).
    """
    if not configured():
        return
    url = os.environ["CRM_WEBHOOK_URL"].strip()
    secret = os.environ["CRM_WEBHOOK_SECRET"].strip()
    payload = {
        "event": "order.created",
        "order": order,
        "pricing": pricing,
        "files": files,
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Laser-Webhook-Secret": secret,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            response.read()
    except urllib.error.HTTPError as exc:
        _record_failure(f"{order.get('ref', '?')}: CRM דחה ({exc.code})")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        _record_failure(f"{order.get('ref', '?')}: לא הגענו ל-CRM ({exc})")


def dxf_file(name: str, thickness_mm: float, spec: dict) -> dict:
    """עוטף `cut_dxf` לפורמט שהמטען לוקח — base64, כדי שהמטען כולו
    יהיה JSON אחד ולא בקשה מרובת חלקים שה-CRM צריך לפרסר אחרת.

    **קובץ החיתוך בלבד, לא דף הכיפוף.** מספיק כדי לחתוך; דף כיפוף
    לכל חלק מכופף הוא הרחבה עתידית, לא הושמט בטעות.
    """
    payload = cut_dxf(spec)
    stem = "".join(c for c in name if c.isalnum() or c in " -_")[:60].strip() or "חלק"
    filename = f"{stem}_{thickness_mm:g}mm.dxf"
    return {"filename": filename, "content_base64": base64.b64encode(payload).decode("ascii")}

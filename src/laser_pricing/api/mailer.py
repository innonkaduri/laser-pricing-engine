"""שליחת אימייל. תפר סגור, כמו התשלום וגוגל — ולא כדי לחכות.

בלי `SMTP_HOST`/`SMTP_USER`/`SMTP_PASSWORD`/`SMTP_FROM` שחזור הסיסמה
**קיים בקוד ושקט**: מסך "שכחתי סיסמה" מוצג תמיד (מי שביקש איפוס
לא צריך לדעת אם המערכת יודעת לשלוח — אותו כלל בדיוק כמו תשובת
"הכתובת קיימת/לא קיימת"), אבל השליחה בפועל נכשלת בבירור בלוג
ואינה קורסת את הבקשה.

**`smtplib` מהספרייה הסטנדרטית, לא ספריית שליחה חיצונית.** אותה
מלכודת בדיוק כמו `httpx` ב-`google_auth.py`: תלות שמותקנת מקומית
ולא על הקופסה נופלת בפריסה, כי `deploy.sh` אינו מתקין תלויות כלל.
"""

from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage

TIMEOUT_SECONDS = 10.0
"""ספק מייל תקוע לא אמור להחזיק עובד בקופסה עם שתי ליבות לחמישה
פרויקטים — אותו קו מחשבה בדיוק כמו ב-`google_auth.py`."""

REQUIRED_VARS = ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM")


class MailError(RuntimeError):
    """שליחה שנכשלה. תמיד מפורש — אף פעם לא נבלע בשקט."""


def configured() -> bool:
    return all(os.environ.get(name, "").strip() for name in REQUIRED_VARS)


def missing() -> list[str]:
    return [name for name in REQUIRED_VARS if not os.environ.get(name, "").strip()]


def send(to_addr: str, subject: str, text_body: str) -> None:
    """שולח מייל טקסט פשוט. **חוסם עד `TIMEOUT_SECONDS` ולא יותר.**

    כישלון (חיבור, אימות, ספק שדוחה) הופך ל-`MailError` עם הודעה
    שמכילה את הסיבה המקורית — כדי שמי שקורא לוג לא יראה רק "נכשל".
    """
    if not configured():
        raise MailError(f"שליחת מייל אינה מוגדרת: חסרים {', '.join(missing())}.")

    host = os.environ["SMTP_HOST"].strip()
    port = int((os.environ.get("SMTP_PORT", "") or "587").strip())
    user = os.environ["SMTP_USER"].strip()
    password = os.environ["SMTP_PASSWORD"]
    sender = os.environ["SMTP_FROM"].strip()

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = to_addr
    message.set_content(text_body)

    try:
        with smtplib.SMTP(host, port, timeout=TIMEOUT_SECONDS) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            smtp.login(user, password)
            smtp.send_message(message)
    except (smtplib.SMTPException, OSError) as exc:
        raise MailError(f"שליחת המייל אל {to_addr} נכשלה: {exc}") from exc


def send_password_reset(to_addr: str, reset_url: str) -> None:
    send(
        to_addr,
        "איפוס סיסמה — ימיש כדורי",
        "קיבלנו בקשה לאפס את הסיסמה בחשבון שלך במנוע התמחור.\n\n"
        f"לקביעת סיסמה חדשה: {reset_url}\n\n"
        "הקישור בתוקף לחצי שעה, ותקף לשימוש אחד בלבד.\n\n"
        "לא ביקשת איפוס? אפשר להתעלם מהמייל הזה — שום דבר לא ישתנה "
        "בחשבון שלך.",
    )

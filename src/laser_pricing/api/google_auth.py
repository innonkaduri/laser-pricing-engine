"""כניסה עם גוגל. **תפר, ורשימה סגורה — כמו התשלום והבנאים.**

בלי `GOOGLE_CLIENT_ID` ו-`GOOGLE_CLIENT_SECRET` הכפתור **אינו
מופיע במסך הכניסה כלל**. זה אותו כלל שכבר נכתב כאן פעמיים: מסך
אינו מציע בחירה שנועדה להיכשל.

**זרימת הקוד (Authorization Code), לא זרימת האסימון בדפדפן.**
הדפדפן מקבל **קוד** בלבד, והחלפתו באסימון קורית שרת-לשרת עם
`client_secret`. קוד שדולף מההיסטוריה או מהלוג של הדפדפן אינו שווה
כלום בלי הסוד, ולכן הוא הדבר היחיד שנוסע דרך הלקוח.

**ולכן גם אין כאן אימות חתימה של ה-`id_token`.** זו אינה השמטה:
כשמקבלים את האסימון **ישירות מגוגל** בערוץ שרת-לשרת מעל TLS, אין
צד שלישי שיכול היה לזייף אותו — וגוגל עצמה כותבת שבמקרה הזה מותר
לדלג על אימות החתימה. אסימון שמגיע מהדפדפן היה סיפור אחר לגמרי,
וזו בדיוק הסיבה שאנחנו לא מקבלים אסימונים מהדפדפן.

**מה שנבדק כן:** ש-`email_verified` אמיתי. חשבון גוגל עם כתובת
שלא אומתה יכול לטעון לכל כתובת שהיא, ולכן הוא אינו זהות.
"""

from __future__ import annotations

import os
import secrets
from urllib.parse import urlencode

import httpx

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
USERINFO_ENDPOINT = "https://openidconnect.googleapis.com/v1/userinfo"

SCOPES = "openid email profile"
STATE_COOKIE = "g_state"
STATE_TTL_SECONDS = 600
TIMEOUT_SECONDS = 10.0
"""גוגל אינה אמורה לקחת יותר. בלי תקרה, בקשה תקועה מחזיקה עובד
בקופסה שיש בה שתי ליבות לחמישה פרויקטים."""


class GoogleError(RuntimeError):
    """כניסה עם גוגל שנכשלה. **תמיד מפורש.**"""


def configured() -> bool:
    return bool(
        os.environ.get("GOOGLE_CLIENT_ID", "").strip()
        and os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
        and os.environ.get("PUBLIC_BASE_URL", "").strip()
    )


def missing() -> list[str]:
    return [
        name
        for name in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "PUBLIC_BASE_URL")
        if not os.environ.get(name, "").strip()
    ]


def redirect_uri() -> str:
    base = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")
    return f"{base}/api/auth/google/callback"


def new_state() -> str:
    return secrets.token_urlsafe(24)


def auth_url(state: str) -> str:
    """הכתובת שאליה שולחים את הדפדפן.

    `prompt=select_account` כדי שמי שיש לו כמה חשבונות יבחר, ולא
    ייכנס בשקט עם זה שגוגל זוכרת — זה בדיוק המקום שבו אנשים פותחים
    חשבון שני בטעות ומאבדים את ההזמנות שלהם.
    """
    if not configured():
        raise GoogleError(f"כניסה עם גוגל אינה מוגדרת בשרת. חסר: {', '.join(missing())}.")
    params = {
        "client_id": os.environ["GOOGLE_CLIENT_ID"].strip(),
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": SCOPES,
        "state": state,
        "prompt": "select_account",
        "access_type": "online",
    }
    return f"{AUTH_ENDPOINT}?{urlencode(params)}"


def exchange(code: str) -> dict:
    """קוד → זהות. **שרת-לשרת בלבד.**

    מוחזר `{sub, email, name, picture}`. כל כישלון הוא `GoogleError`
    עם סיבה בעברית — כולל המקרה שבו גוגל החזירה 200 בלי `sub`,
    שאינו אמור לקרות ואינו נבלע.
    """
    if not configured():
        raise GoogleError("כניסה עם גוגל אינה מוגדרת בשרת.")
    data = {
        "code": code,
        "client_id": os.environ["GOOGLE_CLIENT_ID"].strip(),
        "client_secret": os.environ["GOOGLE_CLIENT_SECRET"].strip(),
        "redirect_uri": redirect_uri(),
        "grant_type": "authorization_code",
    }
    try:
        with httpx.Client(timeout=TIMEOUT_SECONDS) as client:
            token_response = client.post(TOKEN_ENDPOINT, data=data)
            if token_response.status_code != 200:
                raise GoogleError(
                    f"גוגל דחתה את הקוד ({token_response.status_code}). נסה להיכנס שוב."
                )
            access_token = token_response.json().get("access_token", "")
            if not access_token:
                raise GoogleError("גוגל לא החזירה אסימון גישה.")
            info_response = client.get(
                USERINFO_ENDPOINT, headers={"Authorization": f"Bearer {access_token}"}
            )
            if info_response.status_code != 200:
                raise GoogleError("לא הצלחנו לקרוא את פרטי החשבון מגוגל.")
            info = info_response.json()
    except httpx.HTTPError as exc:
        raise GoogleError(f"לא הצלחנו להגיע לגוגל: {exc}") from exc

    sub = str(info.get("sub", "")).strip()
    email = str(info.get("email", "")).strip().lower()
    if not sub:
        raise GoogleError("תשובת גוגל בלי מזהה חשבון — לא נכניס על סמך זה.")
    # **כתובת שלא אומתה אינה זהות.** חשבון גוגל יכול לטעון לכל
    # כתובת; מה שהופך אותה לשלו הוא האימות.
    if not info.get("email_verified") or not email:
        raise GoogleError("כתובת המייל בחשבון גוגל אינה מאומתת, ולכן אי אפשר להיכנס איתה.")
    return {
        "sub": sub,
        "email": email,
        "name": str(info.get("name", "")).strip(),
        "picture": str(info.get("picture", "")).strip(),
    }

"""שכבת ה-API של מנוע התמחור.

שלושה סוגי קלט נשפכים לאותו טיפוס `PartGeometry`, ולכן לתמחור אין
מושג מאיפה הגיע החלק: העלאת DXF, קלט ידני, ובהמשך מודל שנבנה בממשק.

הערה על מצב: הגיאומטריות יושבות בזיכרון התהליך בין ההעלאה לתמחור.
זה מספיק לשרת יחיד; אם יומיום יעבור למספר מכונות, זה המקום שיצטרך
אחסון משותף — ולא שום דבר אחר.
"""

from __future__ import annotations

import base64
import hmac
import json
import math
import os
import sqlite3
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Literal
from urllib.parse import quote_plus

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..cad.base import CadReadError
from ..cad.dxf_reader import read_dxf
from ..domain.geometry import Contour, PartGeometry, circle, rectangle
from ..domain.part import Part, PartSource
from ..nesting.plate import check_manufacturability
from ..nesting.splitting import plan_split
from ..pricing.engine import PricingError, price_order
from ..pricing.tariff import InvalidTariffError, MissingTariffError
from . import catalog as catalog_mod
from . import identity, usage
from .serialize import geometry_to_json, public_quote_to_json, quote_to_json
from .simple_tariff import MONEY_FIELDS, apply_form, to_form
from .store import STORE, GeometryExpired, StoredGeometry
from .tariff_store import STATE

MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_MB", "25")) * 1024 * 1024
"""תקרת ההעלאה. נמדדה, לא נבחרה יפה.

זמן פענוח ושיא זיכרון על מק מהיר (מ-19.8.2026), קובץ עם מתארים סגורים:

    1.9MB  →   1.8 שניות,   82MB
    11.4MB →  11.9 שניות,  221MB
    56.4MB →  41.3 שניות,  823MB

הקופסה היא שתי ליבות משותפות לחמישה פרויקטים, כלומר איטית פי כמה
מהמדידה הזאת. 25MB נותנים מרווח אמיתי לתוכניות כבדות ועדיין משאירים
את הפענוח בסדר גודל של עשרות שניות ומאות מגה-בייט. קובץ של 60MB אינו
חלק — הוא תוכנית שלמה עם ריהוט, מידות וטקסט, ומה שהמנוע צריך הוא
המתאר. הגבול הזה אומר את זה במקום להיאבק בו.
"""
REQUIRED_ENV: dict[str, str] = {
    "SESSION_SECRET": "בלעדיו נוצר מפתח אקראי לכל הפעלה וכל הסשנים מתים בשקט",
    "TARIFF_PATH": "בלעדיו הטבלה נקראת מתוך עץ ה-git, ו-git clean מוחק אותה",
    "USERS_DB": "בלעדיו מסד המשתמשים נכתב לעץ ה-git ואיש לא קורא אותו",
    "BACKUP_STATUS_FILE": "בלעדיו דיווח הגיבוי נכתב לעץ ה-git",
    "USAGE_LOG": "בלעדיו רישום השימוש נכתב לעץ ה-git, ו-git clean -fdx מוחק אותו",
    "APP_USER": "שם המשתמש של סיסמת הסביבה",
    "APP_PASSWORD": "בלעדיו השער נשען על קיום משתמשים בלבד",
    "EDITOR_PASSWORD": "בלעדיו הקישור של אבא לטופס המחירים אינו עובד",
    "SIGNUP_MODE": "בלעדיו ההרשמה הציבורית פתוחה כברירת מחדל שקטה, ולא כהחלטה",
    "SERVICE_TOKEN": "בלעדיו כל קריאה מימיש חוזרת 401",
}
"""משתני הסביבה שפריסה אמיתית **חייבת** להגדיר, והסיבה לכל אחד.

**המכנה המשותף לכולם: היעדרם אינו מפיל את השירות.** הוא עולה, נראה
תקין, ועושה משהו אחר — קורא קובץ מהמקום הלא נכון, או פותח הרשמה
ציבורית שאיש לא החליט עליה. זה הזן שרדפנו אחריו כל החודש.

הרשימה כאן ולא בתיעוד, ו-`docs/RESTORE.md` **נבדק מולה**: נוהל
שחזור שאיש לא בודק הוא ספרות, ובדיוק הפער הזה כבר מחק את
`tariff.json` פעם אחת מהקופסה.

מה ש**אינו** כאן ובכוונה: `MAX_UPLOAD_MB`, `MAX_PUBLIC_UPLOAD_MB`,
`TARIFF_STALE_DAYS`, `TRUST_PROXY` ו-`TARIFF_JSON` — לכולם ברירת
מחדל שהיא ההתנהגות הרצויה, והיעדרם אינו שקט אלא פשוט נכון.
"""

WEB_DIR = Path(__file__).resolve().parents[3] / "web"
REPO_DIR = Path(__file__).resolve().parents[3]


def _running_commit() -> str | None:
    """איזה commit באמת טעון בתהליך הזה.

    נקרא פעם אחת, בזמן הייבוא, ובכוונה: זו הגרסה של הקוד **שרץ**, לא
    זו שיושבת על הדיסק ברגע ששואלים. ההבחנה אינה תיאורטית — פריסה
    שנכשלה כאן פעם אחת החזירה קבצים ישנים והשאירה את `git log` מדווח
    SHA חדש, ואי אפשר היה לראות את זה מבחוץ.

    קריאה ישירה מ-`.git` ולא `git rev-parse`: אין תהליך-בן, אין תלות
    ב-`safe.directory`, ואין מה להיכשל.
    """
    try:
        head = (REPO_DIR / ".git" / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not head.startswith("ref:"):
        return head[:40] or None

    ref = head.split(" ", 1)[1].strip()
    try:
        return (REPO_DIR / ".git" / ref).read_text(encoding="utf-8").strip()[:40]
    except OSError:
        pass
    try:  # ref ארוז (git gc)
        for line in (REPO_DIR / ".git" / "packed-refs").read_text(encoding="utf-8").splitlines():
            if line.endswith(" " + ref):
                return line.split(" ", 1)[0][:40]
    except OSError:
        pass
    return None


RUNNING_COMMIT = _running_commit()
"""ה-SHA שנטען עם התהליך. `None` כשאין `.git` (פריסה מתוך ארכיון)."""

CATALOG = catalog_mod.load()
"""הקטלוג נטען פעם אחת, בעליית התהליך, **ומפיל אותה אם הוא שבור.**

קטלוג שנטען בעצלתיים היה הופך שגיאת הקלדה ב-JSON ל-500 אצל המבקר
הראשון שלוחץ על "קטלוג", שעות אחרי הפריסה. כאן הוא נופל בשנייה
הראשונה, `deploy.sh` רואה שהיחידה לא עלתה, וההודעה מצביעה על השורה.
"""

app = FastAPI(
    title="מנוע תמחור לייזר",
    description="תמחור חיתוך לייזר לפי טבלת התמחור של ינון.",
    version="0.2.0",
)


# ---- נעילה ----

OPEN_PATHS = frozenset(
    {"/health", "/login", "/api/login", "/signup", "/api/signup", "/", "/api/offering"}
)
"""מה שנפתח בלי זהות — כולם מחזירים מסכים, בוליאנים ושמות, ולעולם
לא מחירים.

`/login` ו-`/signup` הם המסכים שבהם משיגים זהות. `/health` הוא בדיקת
הפלטפורמה. **`/` ו-`/api/offering` נפתחו 25.8.2026** עם דף הנחיתה:
מי שלא מכיר את המערכת צריך לנחות על משהו ולהחליט אם להירשם, ולא על
מסך כניסה. `/` מחזיר את דף הנחיתה לאנונימי ואת האפליקציה למי שנכנס —
ההחלטה בפונקציה עצמה, כי `OPEN_PATHS` עוקף את ה-middleware.

**מה ש-`/api/offering` מחזיר:** שמות חומרים ועוביים שאפשר **באמת**
לתמחר. אין בו מחיר, ואין בו מה שאין לו מחיר.
"""

EDITOR_PATHS = ("/prices", "/api/prices")
"""המסלולים היחידים ש-EDITOR_PASSWORD פותח.

הפרדת המפתחות היא העיקר כאן. אבא של ינון צריך סיסמה שאפשר להכתיב
בטלפון, אבל אותה סיסמה על *כל* המערכת הייתה נותנת לכל אחד גם לתמחר,
גם לראות הצעות וגם להחליף את הטבלה כולה דרך עורך ה-JSON. לכן מפתח
העריכה פותח אך ורק את טופס המחירים, ומפתח המערכת נשאר נפרד וחזק.
"""


def _is_font_path(path: str) -> bool:
    """גופנים פתוחים לכולם, כי דף הנחיתה ומסך הכניסה אנונימיים.

    **מתארחים אצלנו ולא נטענים מגוגל.** בקשה חיצונית מכל דף היא
    גם השהיה וגם דליפה: מי שפותח מסך שמציג מחירים לא צריך שגוגל
    תדע על כך. שני קבצים, 28.7KB יחד, נשמרים במטמון בין כל המסכים.
    """
    return path.startswith("/fonts/") and path.endswith(".woff2")


OPEN_ASSETS = ("/brand.css", "/part-draw.js")
"""נכסים שכל מסך ציבורי צריך, ולכן אינם יכולים לשבת מאחורי השער.

`brand.css` הוא מערכת העיצוב. `part-draw.js` הוא הקוד שהופך מתאר
שהשרת החזיר לנתיב SVG — **וקוד ציור אחד ולא שניים**: הקטלוג מצייר
תמונות ממוזערות והקונפיגורטור מצייר תצוגה חיה, ושני עותקים של אותה
המרה היו נפרדים בשקט בדיוק כמו שבעת קבצי ה-CSS שנפרדו לפני כן.
"""


def _is_open_asset(path: str) -> bool:
    """מערכת העיצוב, פתוחה מאותה סיבה בדיוק כמו הגופן.

    **קובץ אחד לשבעה מסכים.** קודם כל מסך נשא עותק של ה-CSS שלו,
    ולכן "מערכת עיצוב" הייתה שם לשבע גרסאות שנפרדו זו מזו בשקט —
    כפתור שקיבל רדיוס אחר כאן ושם, וצבע שתוקן במסך אחד ולא בשישה.
    אין בו שום נתון: טוקנים ורכיבים בלבד.
    """
    return path in OPEN_ASSETS


def _is_open_catalog(path: str) -> bool:
    """הקטלוג נגלל בלי חשבון. **המחיר לא.**

    זו אותה חלוקה שזאמיט עושים, ומאותה סיבה: מי שנוחת מגוגל צריך
    לראות מה מייצרים כאן לפני שיחליט לפתוח חשבון, ומסך הרשמה אינו
    תשובה לשאלה "מה אתם עושים". ההדמיה פתוחה גם היא — היא בנויה
    ממידות שהמבקר עצמו הקליד ואינה נוגעת בטבלה בכלל.

    `/api/catalog/<id>/quote` **אינו** ברשימה, ולכן הוא נופל לשער
    הרגיל ודורש `quote:total` לפחות. שם נמצא המספר.
    """
    if path in ("/catalog", "/api/catalog"):
        return True
    if path.startswith("/product/"):
        return True
    return path.startswith("/api/catalog/") and path.endswith("/preview")


def _is_editor_path(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") for p in EDITOR_PATHS)


def _editor_key(request: Request) -> str:
    """המפתח מגיע מהלינק עצמו או מכותרת, כדי שלינק בודד יספיק."""
    return request.headers.get("x-editor-key") or request.query_params.get("k", "")


EDITOR_ATTEMPTS_PER_IP = 20
EDITOR_WINDOW_SECONDS = 3600
_EDITOR_ATTEMPTS: dict[str, list[float]] = {}
"""הגבלת קצב על ניסיונות כושלים במפתח העריכה.

`EDITOR_PASSWORD` הוא בכוונה קוד קצר שאפשר להכתיב בטלפון, והוא עובר
כפרמטר בכתובת. עד היום מה שהגן עליו היה שהכתובת לא פורסמה —
**וההרשמה הציבורית מסירה בדיוק את ההגנה הזאת.** ניסיון כושל נספר
לפי כתובת; מפתח נכון אינו נספר, ולכן אבא יכול לרענן כמה שירצה.
זה מקטין את הנזק, **ואינו מחליף את החלפת הקוד למפתח ארוך.**
"""


PRICE_TABLE_PATHS = ("/prices", "/api/prices", "/api/tariff")
"""מה שדורש `prices:edit`: טופס אבא ועורך ה-JSON הגולמי.

עורך ה-JSON נכנס לרשימה הזאת ולא לרשימת המשתמשים הרגילים, כי `PUT`
עליו מחליף את הטבלה **כולה** — כולל הכיול שאף אחד לא רואה בטופס.
"""

INTERNAL_PATHS = ("/api/dashboard", "/dashboard")
"""פנימי: `quote:use` **או** `prices:edit`. לא הציבור.

הדשבורד מדווח אילו שדות בטבלה ריקים, מה מצב השער, מתי גובה לאחרונה
וכמה משתמשים יש: מפת המערכת מבפנים. כל עוד `quote:use` הייתה היכולת
היחידה שפותחת את המנוע, "כל מי שנכנס" ו"פנימי" היו אותו דבר והוא ישב
עם שאר המסלולים. מאז ההרשמה הציבורית הם נפרדו — ולכן הוא נזכר כאן
במפורש, ולא נשאר בברירת המחדל שנפתחה לרחוב.

**שתי היכולות ולא אחת (25.8.2026):** הדשבורד נבנה כדי שאבא יסתכל בו
מהטלפון ליד המכונה, ולאבא יש `prices:edit` בלבד. תנאי של `quote:use`
לבדו היה נועל אותו מחוץ למסך שנבנה בשבילו.
"""


ANY_USER_PATHS = ("/api/me", "/api/logout")
"""מה שכל מי שנכנס רשאי, ויהיו יכולותיו אשר יהיו.

"מי אני" ו"צא" אינם יכולת. אבא, שיש לו `prices:edit` בלבד, קיבל על
`/api/me` את "אין לך הרשאה ל-quote:use" — כלומר הודעת הרשאה על שאלה
שאין לה שום קשר להרשאות, בכל כניסה מחדש.
"""


def _required_capabilities(path: str) -> frozenset[str] | None:
    """אילו יכולות פותחות את המסלול. **אחת מהן מספיקה.**

    החזרת קבוצה ולא מחרוזת יחידה היא מה שאִפשר את ההרשמה הציבורית:
    המנוע נפתח גם ל-`quote:total` וגם ל-`quote:use`, וההבדל ביניהן
    אינו בשאלה *אם* מותר לתמחר אלא *מה חוזר* — וזה נקבע בשכבת
    התשובה, לא בשער.
    """
    if path in ANY_USER_PATHS or path.startswith("/static/"):
        return None
    if any(path == p or path.startswith(p + "/") for p in PRICE_TABLE_PATHS):
        return frozenset({identity.CAP_PRICES_EDIT})
    if any(path == p or path.startswith(p + "/") for p in INTERNAL_PATHS):
        return frozenset({identity.CAP_QUOTE_USE, identity.CAP_PRICES_EDIT})
    return identity.QUOTE_CAPABILITIES


def _gate_is_on() -> bool:
    """השער פעיל אם יש סיסמת מערכת או אם קיימים משתמשים.

    בפיתוח מקומי אין לא זה ולא זה, והשער כבוי. **בפרודקשן זה בדיוק
    המקום שדפק ב-18.8**: המשתנים לא היו ביחידה, ה-middleware ויתר על
    השער בשקט, והשרת היה פתוח לכתיבה לכל האינטרנט. לכן היום זה גם
    מדווח ב-`/health`, ולא רק מתרחש.
    """
    if os.environ.get("APP_PASSWORD", ""):
        return True
    try:
        return identity.user_count() > 0
    except sqlite3.Error:
        # מסד פגום לא יפתח את השער. כישלון סגור, לא כישלון פתוח.
        return True


def _wants_html(request: Request) -> bool:
    """האם להחזיר מסך כניסה במקום 401.

    **`/api/*` לעולם לא.** נתיב API שמחזיר 303 למסך כניסה נראה מבחוץ
    כמו נתיב פתוח: הסטטוס אינו 401, הגוף הוא HTML, וכלי שבודק "האם
    זה מוגן" רואה הפניה ולא סירוב. הדרישה של האב (25.8.2026) הייתה
    מפורשת — "401 בגוף הגולמי, לא הפניה למסך התחברות שמאחוריו ה-API
    פתוח" — ובבדיקה התברר ש-`/api/dashboard` עם `Accept: text/html`
    אכן החזיר 303. דפדפן שמבקש **דף** מקבל מסך; דפדפן שמבקש **API**
    מקבל 401 בדיוק כמו curl.
    """
    if request.url.path.startswith("/api/"):
        return False
    return request.method == "GET" and "text/html" in request.headers.get("accept", "")


CACHEABLE_ASSETS = ("/fonts/", "/static/") + OPEN_ASSETS
"""מה שזהה לכל אדם ולכן מותר לשמור. כל השאר — לא."""


def _no_shared_cache(path: str, response):
    """תשובה שתלויה בזהות אינה נשמרת במטמון. **נמצא בשטח, 26.8.2026.**

    ינון נרשם, החשבון נוצר, והדפדפן החזיר אותו לדף הנחיתה. הסיבה לא
    הייתה בשרת: `/` מחזיר **תוכן שונה לכל זהות** — נחיתה לאנונימי,
    מסך תמחור לנרשם, גיליון פנימי למי שרשאי — אבל `FileResponse` שלח
    `ETag` ו-`Last-Modified` **בלי `Cache-Control` ובלי `Vary`**.
    הדפדפן שמר את דף הנחיתה תחת הכתובת `/`, ואחרי ההרשמה הגיש את
    העותק השמור. **החשבון נוצר; המסך היה ישן.**

    ומעבר לבלבול: בלי `private`, מטמון משותף (proxy, CDN) היה רשאי
    להגיש את המסך של משתמש אחד לאחר.

    הכותרת נקבעת **כאן ולא בכל מסלול בנפרד**, כי מסלול חדש שיישכח
    הוא בדיוק הבאג הזה שוב.
    """
    if path.startswith(CACHEABLE_ASSETS):
        return response
    response.headers["Cache-Control"] = "private, no-store"
    existing = response.headers.get("Vary")
    response.headers["Vary"] = f"{existing}, Cookie" if existing else "Cookie"
    return response


@app.middleware("http")
async def auth_gate(request: Request, call_next):
    """מי אתה, ומה מותר לך.

    כל הזהות מגיעה מ-`identity.current_user` — סשן, Basic, או טוקן
    שירות של ה-CRM. הקובץ הזה מחליט רק *מה נדרש לאיזה מסלול*, ולא
    מאיפה מגיעה הזהות; החלפת מקור הזהות ל-CRM לא נוגעת כאן.

    **יציאה אחת.** ההחלטה מחושבת ב-`_decide`, והכותרות נקבעות כאן
    פעם אחת — כדי שיציאה מוקדמת חדשה לא תוכל לפספס אותן.
    """
    path = request.url.path
    return _no_shared_cache(path, await _decide(request, call_next, path))


async def _decide(request: Request, call_next, path: str):
    if (
        path in OPEN_PATHS
        or _is_font_path(path)
        or _is_open_asset(path)
        or _is_open_catalog(path)
        or not _gate_is_on()
    ):
        return await call_next(request)

    user = identity.current_user(request)

    # הקישור של אבא: מפתח מוגבל למסלול, לא אדם. נשאר בתוקף כדי שלא
    # ניקח ממנו את הטופס באמצע המילוי.
    if user is None and _is_editor_path(path):
        editor_password = os.environ.get("EDITOR_PASSWORD", "")
        if editor_password and hmac.compare_digest(_editor_key(request), editor_password):
            user = identity.EDITOR_LINK_USER
        elif not _rate_ok(
            _EDITOR_ATTEMPTS, _client_ip(request), EDITOR_ATTEMPTS_PER_IP, EDITOR_WINDOW_SECONDS
        ):
            return Response(
                status_code=429,
                content="יותר מדי ניסיונות. נסה שוב בעוד כמה דקות.",
                media_type="text/plain; charset=utf-8",
            )

    if user is None:
        if _is_editor_path(path):
            # לאבא אין שם משתמש, ומסך כניסה רק יבלבל אותו.
            return Response(
                status_code=401,
                content="הקישור אינו תקין או שפג תוקפו. בקש מינון קישור חדש.",
                media_type="text/plain; charset=utf-8",
            )
        if _wants_html(request):
            # דפדפן מקבל מסך כניסה; curl וכלים מקבלים 401 כמו תמיד.
            return RedirectResponse(f"/login?next={quote_plus(path)}", status_code=303)
        return Response(status_code=401, content="נדרשת התחברות")

    required = _required_capabilities(path)
    if required is not None and not user.can_any(required):
        return Response(
            status_code=403,
            content=f"אין לך הרשאה ל-{' או '.join(sorted(required))}. בקש מינון.",
            media_type="text/plain; charset=utf-8",
        )

    request.state.user = user
    return await call_next(request)


# ---- מה שמשתמש ציבורי מקבל, ומה שהוא לא ----

MAX_PUBLIC_UPLOAD_BYTES = int(os.environ.get("MAX_PUBLIC_UPLOAD_MB", "5")) * 1024 * 1024
"""תקרת העלאה נמוכה יותר למי שנרשם מהרחוב.

המספרים מ-`MAX_UPLOAD_BYTES` הם הסיבה: 56MB נמדדו ב-41 שניות ו-823MB
על מק מהיר, והקופסה היא **שתי ליבות המשותפות לחמישה פרויקטים**. 25MB
הם מרווח סביר למי שיושב במשרד ומעלה תוכנית כבדה פעם ביום; הם גם דרך
של שורה אחת בטרמינל להשבית את המנוע ואת ה-CRM יחד. ההפרדה נותנת
לפנימיים את המרווח ולציבור את מה שחלק אמיתי צריך.
"""

PROCESS_STARTED_AT = time.time()
"""מתי עלה התהליך. כל מונה בזיכרון נמדד מהרגע הזה, ולא "מאז ומתמיד"."""

_UPLOAD_FAILURES: list[tuple[float, str]] = []
"""(מתי, למה) לכל העלאה שנכשלה. בזיכרון התהליך, כמו שאר המונים.

**נמדד ולא מוערך**, וזו הנקודה: המונה מתאפס בכל הפעלה מחדש, ולכן
המסך אומר "מאז ההפעלה האחרונה" ולא מציג מספר שנראה כמו סך הכל
היסטורי. מונה שמעגל את גבולותיו הוא מונה שאפשר להחליט לפיו.
"""
MAX_TRACKED_FAILURES = 200


def _record_upload_failure(reason: str) -> None:
    _UPLOAD_FAILURES.append((time.time(), reason))
    del _UPLOAD_FAILURES[:-MAX_TRACKED_FAILURES]


PUBLIC_ENGINE_CALLS = 12
PUBLIC_ENGINE_WINDOW_SECONDS = 3600
_ENGINE_CALLS: dict[str, list[float]] = {}
"""הורד מ-40 ל-12 ב-23.8.2026, אחרי מדידה — ועם הסתייגות מפורשת.

**מה שהמספר הזה כן עושה:** מייקר מיפוי של הטבלה כולה. 22 שורות
תעריף, כל שורה דורשת חמש הצעות, ואותן ארבע גיאומטריות משמשות לכולן —
כלומר ~114 קריאות. ב-40 לשעה זה שלוש שעות; ב-12 זה עשר שעות, ועם
תקרת ההרשמה שירדה ל-3 לשעה גם דורש הרבה יותר חשבונות.

**מה שהוא לא עושה, ואסור להתבלבל:** הוא **אינו מונע** שחזור של שורה
אחת. ההתקפה שהצליחה דורשת ארבע גיאומטריות וחמש הצעות — תשע קריאות,
שנכנסות בנוחות ב-12. שום תקרה בטווח השמיש חוסמת אותה, כי המידע יושב
במחיר עצמו. ההגנה האמיתית היא שהמרווח אינו ניתן להפרדה.

**למה 12 ולא 10 ולא 40:** מסך התמחור הציבורי שולח קריאה אחת לכל
"הוסף חלק" ואחת לכל "חשב מחיר" (`web/quote.html`). ביקור נדיב —
שלושה חלקים ושמונה חישובים — הוא 11. 12 משאיר את זה עובר, ו-10 היה
חוסם משתמש אמיתי באמצע. התקרה נמדדת מול השימוש, לא מול ההתקפה,
כי היא ממילא לא עוצרת את ההתקפה.
"""


def _current(request: Request) -> identity.User | None:
    return getattr(request.state, "user", None)


def _sees_breakdown(request: Request) -> bool:
    """האם לפונה הזה מותר לראות את פירוק העלויות.

    בלי זהות (פיתוח מקומי, שער כבוי) — כן. זו אינה פרצה: כשהשער כבוי
    אין בכלל הפרדה לאכוף, ו-`/health` צועק על המצב הזה בקול.
    """
    user = _current(request)
    return user is None or user.sees_cost_breakdown


def _owner_key(request: Request) -> str:
    """למי שייכות הגיאומטריות שהועלו בבקשה הזאת."""
    user = _current(request)
    return user.username if user is not None else ""


def _guard_public_engine(request: Request) -> None:
    """שני התנאים שחלים על מי שאין לו `quote:use`, ועל אף אחד אחר.

    1. **טבלה ריקה = אין הצעה.** הוראת האב (23.8.2026): "הרשמה ציבורית
       למערכת שמחזירה אפס גרועה מאין הרשמה". פנימי שרואה 0 יודע בדיוק
       מה זה אומר, כי המסך אומר לו; מבקר מבחוץ פשוט למד שהמחיר אצל
       ינון הוא אפס. **החסימה היא על ההעלאה ולא רק על התמחור**, כדי
       שהמנוע לא ישרוף שתי ליבות על קובץ שממילא לא יחזיר מספר.
    2. **הגבלת קצב.** נקודה ציבורית שמפענחת DXF היא הדרך הזולה ביותר
       להפיל את הקופסה, ואיתה את ה-CRM.
    """
    user = _current(request)
    if user is None or user.sees_cost_breakdown:
        return

    if not STATE.is_ready:
        raise HTTPException(
            status_code=503,
            detail=(
                "המערכת בהרצה: טבלת המחירים עדיין נטענת, ולכן אי אפשר להוציא הצעה. "
                "החשבון שלך פעיל — נסה שוב בקרוב."
            ),
        )

    if not _rate_ok(
        _ENGINE_CALLS, user.username, PUBLIC_ENGINE_CALLS, PUBLIC_ENGINE_WINDOW_SECONDS
    ):
        raise HTTPException(
            status_code=429,
            detail="הגעת למכסת התמחורים לשעה. נסה שוב מאוחר יותר.",
        )


# ---- מודלים של הבקשות ----


class HoleSpec(BaseModel):
    diameter_mm: float = Field(gt=0)
    x_mm: float | None = None
    y_mm: float | None = None


class ManualPartSpec(BaseModel):
    name: str = "חלק ידני"
    shape: Literal["rect", "circle", "polygon"] = "rect"
    width_mm: float | None = Field(default=None, gt=0)
    height_mm: float | None = Field(default=None, gt=0)
    diameter_mm: float | None = Field(default=None, gt=0)
    points: list[tuple[float, float]] | None = None
    holes: list[HoleSpec] = Field(default_factory=list)


class PartRequest(BaseModel):
    geometry_id: str
    name: str = ""
    material_key: str
    thickness_mm: float = Field(gt=0)
    quantity: int = Field(default=1, ge=1)
    bend_count: int = Field(default=0, ge=0)
    """כמה כיפופים — הצהרה של המזמין. DXF שטוח לא יודע שהוא יתכופף."""
    bend_length_mm: float = Field(default=0.0, ge=0)
    cut_open_lines: bool = False
    """האם הקווים הפתוחים בשרטוט הם חיתוך. ברירת מחדל: לא — סימון."""


class QuoteRequest(BaseModel):
    parts: list[PartRequest] = Field(min_length=1)


# ---- מידע כללי ----


@app.get("/health")
def health() -> dict:
    """מצב הטבלה — כולל השאלה אם מה שבזיכרון קיים גם על הדיסק.

    שני כללים קובעים את הצורה של התשובה הזאת:

    1. **תמיד 200.** הנתיב הזה הוא `healthCheckPath` של הפלטפורמה.
       פלטפורמה שרואה "לא בריא" מפעילה מחדש — וההפעלה מחדש היא בדיוק
       מה שמוחק את הטבלה מהזיכרון, כלומר את העותק היחיד ששרד במקרה
       שהקובץ נעלם. בדיקת בריאות שמגיבה לאובדן בהריגת הניצול האחרון
       היא לא הגנה אלא הדק. הסטייה מדווחת ב-`warnings`, לא בקוד HTTP.

    2. **בלי אישור, ולכן בלי תוכן.** `/health` פתוח לכל האינטרנט
       (`OPEN_PATHS`), ולכן יוצאים מכאן בוליאנים, ספירות ומחרוזת
       המקור בלבד — לא שמות חומרים, לא עוביים ולא מחירים. גם טקסט
       שגיאת הטעינה נשאר בפנים, כי הוא מצטט שורות מהטבלה.
    """
    disk_present, disk_matches = STATE.disk_state()
    rates = list(STATE.tariff.rates.values()) if STATE.tariff else []
    ready = STATE.is_ready

    priced_rows, rate_rows = STATE.coverage
    price_origin = STATE.tariff.price_origin if STATE.tariff else ""
    low_confidence = list(STATE.tariff.price_low_confidence) if STATE.tariff else []
    age_days = STATE.age_days
    updated_at = (
        datetime.fromtimestamp(STATE.source_mtime).strftime("%Y-%m-%d")
        if STATE.source_mtime
        else None
    )

    warnings: list[str] = []
    if STATE.error:
        warnings.append("טעינת הטבלה נכשלה — ראה /api/config עם התחברות.")
    if not ready:
        warnings.append("הטבלה עדיין ריקה ממחירים — כל סכום ייצא 0.")

    # ---- מה שנטען, בן כמה הוא, וכמה ממנו מלא ----
    #
    # **`tariff_ready: true` לבדו הוא מדד גרוע, וזה נמצא בתרגיל שחזור
    # ב-23.8.2026.** החבילה שיוצאת מהקופסה נושאת את גיבוי הטבלה האחרון
    # שקיים; כל עוד אבא לא הזין מחירים, זה הניסוי של 18.8 עם שורה
    # מתומחרת אחת מתוך 22. שחזור עיוור היה מעלה מנוע שמכריז "מוכן"
    # ומתמחר לפי מחירי ניסוי ישנים — הצעה שגויה שיוצאת בשקט, שהוא
    # בדיוק הזן שהמערכת הזאת נבנתה למנוע. מי שמסתכל מבחוץ צריך לראות
    # "טבלה מ-18.8, שורה אחת מתוך 22" ולא "מוכן".
    if ready and age_days is not None and age_days > TARIFF_STALE_DAYS:
        warnings.append(
            f"הטבלה שנטענה היא מ-{updated_at} ({int(age_days)} ימים). "
            "אם זה שחזור מגיבוי — ודא שאלה המחירים הנכונים לפני שמוציאים הצעה."
        )
    # **מי קבע את המספרים.** הכרעת ינון 25.8.2026 מילאה את הטבלה
    # בהערכת שוק ולא במחירי אבא, וההבדל אינו נראה בשום מקום אחר:
    # הצעה שנשענת על הערכה נראית זהה להצעה שנשענת על מחיר אמיתי.
    if ready and not price_origin:
        warnings.append("לא הוצהר מי קבע את המחירים בטבלה הזאת.")
    elif ready and price_origin:
        warnings.append(f"מקור המחירים: {price_origin}")
    if ready and low_confidence:
        warnings.append(
            "שדות בוודאות נמוכה במיוחד: " + ", ".join(low_confidence) + "."
        )

    if ready and rate_rows and priced_rows / rate_rows < TARIFF_THIN_COVERAGE:
        warnings.append(
            f"רק {priced_rows} מתוך {rate_rows} שורות מתומחרות — הטבלה חלקית. "
            "צירוף חומר+עובי שאינו מתומחר יחזיר 422, ומה שכן מתומחר עשוי להיות ניסוי."
        )
    # TARIFF_JSON מגיע מהסביבה ושורד הפעלה מחדש בעצמו, ולכן מצב הדיסק
    # שם אינו מעיד על סכנה. האזהרה נכונה רק כשהדיסק הוא מה שאמור לשרוד.
    if ready and STATE.origin != "TARIFF_JSON":
        if not disk_present:
            warnings.append("הטבלה קיימת בזיכרון בלבד — הפעלה מחדש תמחק אותה.")
        elif not disk_matches:
            warnings.append("הטבלה בזיכרון שונה מזו שעל הדיסק — הפעלה מחדש תחזיר את גרסת הדיסק.")

    gate_on = _gate_is_on()
    if not gate_on:
        # זו התאונה של 18.8 בדיוק. אם היא תחזור — שתצעק, ולא בשקט.
        warnings.append("השער כבוי — הכתובת פתוחה לקריאה ולכתיבה לכל אחד.")
    if gate_on and identity.SESSION_SECRET_IS_EPHEMERAL:
        warnings.append("אין SESSION_SECRET — כל הפעלה מחדש תנתק את המשתמשים.")

    return {
        "status": "ok",
        # לא סוד: הריפו ציבורי, וה-SHA הוא בדיוק מה שמאפשר לאמת מבחוץ
        # *מה* פרוס — בלי SSH ובלי חשבון.
        "commit": RUNNING_COMMIT[:12] if RUNNING_COMMIT else None,
        "gate_on": gate_on,
        # אינו סוד, והוא בדיוק מה שמאפשר לאמת מבחוץ שההרשמה אכן פתוחה
        # (או שנסגרה) בלי להירשם ובלי SSH.
        "signup_mode": SIGNUP_MODE,
        "tariff_ready": ready,
        "tariff_source": STATE.origin,
        # תאריך בלבד, בלי שעה: זה מספיק כדי לזהות שחזור מגיבוי ישן,
        # ואינו מסגיר מתי בדיוק אבא יושב ועובד.
        "tariff_updated_at": updated_at,
        "price_origin": price_origin,
        "price_low_confidence": low_confidence,
        "tariff_age_days": round(age_days, 1) if age_days is not None else None,
        "tariff_error": bool(STATE.error),
        "rate_rows": len(rates),
        "priced_rows": sum(1 for r in rates if r.plate_price > 0 or r.cut_rate_per_m > 0),
        "materials_count": len({r.material_key for r in rates}),
        "disk_present": disk_present,
        "disk_matches_memory": disk_matches,
        "warnings": warnings,
    }


# ---- כניסה ----


class LoginRequest(BaseModel):
    username: str
    password: str
    next: str = "/"


_LOGIN_FAILURES: dict[str, list[float]] = {}
_MAX_FAILURES = 8
_FAILURE_WINDOW_SECONDS = 600


def _throttled(key: str) -> bool:
    """חסימה פשוטה בזיכרון על ניסיונות כושלים.

    מסך כניסה פתוח לאינטרנט בלי הגבלת קצב הוא הזמנה לניחוש סיסמאות.
    בזיכרון התהליך ולא במסד — ארבעה משתמשים ותהליך אחד, וזה מספיק.
    """
    now = time.time()
    recent = [t for t in _LOGIN_FAILURES.get(key, []) if now - t < _FAILURE_WINDOW_SECONDS]
    _LOGIN_FAILURES[key] = recent
    return len(recent) >= _MAX_FAILURES


def _record_failure(key: str) -> None:
    _LOGIN_FAILURES.setdefault(key, []).append(time.time())


@app.post("/api/login")
def login(body: LoginRequest, request: Request) -> JSONResponse:
    client = request.client.host if request.client else "?"
    key = f"{client}|{body.username.strip().lower()}"
    if _throttled(key):
        raise HTTPException(status_code=429, detail="יותר מדי ניסיונות. נסה שוב בעוד כמה דקות.")

    user = identity.authenticate(body.username, body.password)
    if user is None:
        _record_failure(key)
        # אותה הודעה לשם לא קיים ולסיסמה שגויה — אחרת הטופס מדליף
        # אילו שמות משתמש קיימים במערכת.
        raise HTTPException(status_code=401, detail="שם משתמש או סיסמה שגויים.")

    _LOGIN_FAILURES.pop(key, None)

    # מי שיש לו רק prices:edit יקבל 403 על המנוע, והמסך הראשי ייפתח
    # על שגיאה. אבא ממלא מחירים — שיגיע לטופס ולא לדף שבור.
    #
    # **התנאי הוא "יש לו prices:edit" ולא "אין לו quote:use".** כשהיו
    # שתי יכולות בלבד השניים היו זהים; מאז `quote:total` הם אינם, ומי
    # שנרשם מהרחוב נשלח לטופס המחירים של אבא וקיבל 403 מיד אחרי
    # הרשמה מוצלחת. נמצא בבדיקה בדפדפן, 23.8.2026.
    target = body.next if body.next.startswith("/") else "/"
    if target == "/" and user.can(identity.CAP_PRICES_EDIT) and not user.can(identity.CAP_QUOTE_USE):
        target = "/prices"

    response = JSONResponse(
        {
            "username": user.username,
            "display_name": user.display_name,
            "capabilities": sorted(user.capabilities),
            "next": target,
        }
    )
    response.set_cookie(
        identity.SESSION_COOKIE,
        identity.issue_session(user.username),
        max_age=identity.SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        # Secure רק כשבאמת ב-HTTPS: אחרת פיתוח מקומי על http לא יעבוד
        # והמפתח יכבה את הדגל לגמרי — וזה נגמר בפרודקשן בלי Secure.
        secure=request.url.scheme == "https",
        path="/",
    )
    return response


# ---- הרשמה עצמית ----

SIGNUP_MODE = os.environ.get("SIGNUP_MODE", "open").strip().lower()
"""`open` (ברירת מחדל) · `approval` · `closed`.

משתנה סביבה ולא קבוע בקוד, מסיבה אחת: ההרשמה נפתחה לאינטרנט כולו,
וסגירתה בשעת צרה חייבת להיות `systemctl restart` ולא פריסה. ב-
`approval` נוצר משתמש חסום, ואבא או ינון משחררים אותו ב-
`laser-user.py enable`; העמודה `disabled` הייתה קיימת בטבלה מהיום
הראשון, ולכן זה לא עלה כלום.
"""

SIGNUPS_PER_IP = 3
SIGNUP_WINDOW_SECONDS = 3600
"""הורד מ-5 ל-3 ב-23.8.2026.

**זה המכפיל האמיתי, לא תקרת התמחורים.** מי שרוצה למפות את הטבלה
עוקף תקרה אישית בפתיחת חשבונות, ולכן הורדת ההרשמות לשעה מכפילה את
עלות ההתקפה יותר מכל שינוי בתקרת התמחור. שלוש הרשמות לשעה מאותה
כתובת עדיין מכסות משפחה או משרד קטן שנרשמים יחד.
"""
_SIGNUPS: dict[str, list[float]] = {}


def _client_ip(request: Request) -> str:
    """הכתובת של הפונה, מאחורי nginx.

    `X-Forwarded-For` נלקח מהכותרת הראשונה, ורק כשמוגדר `TRUST_PROXY` —
    כותרת שאפשר לזייף היא הגבלת קצב שאפשר לעקוף.
    """
    if os.environ.get("TRUST_PROXY", "").strip():
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "?"


def _rate_ok(bucket: dict[str, list[float]], key: str, limit: int, window: int) -> bool:
    """דלי אסימונים פשוט בזיכרון התהליך.

    לא Redis ולא מסד: תהליך אחד על קופסה עם שתי ליבות. המחיר הוא
    שהמונה מתאפס בהפעלה מחדש, וזה מחיר מקובל מול תלות חדשה.
    """
    now = time.time()
    recent = [t for t in bucket.get(key, []) if now - t < window]
    if len(recent) >= limit:
        bucket[key] = recent
        return False
    recent.append(now)
    bucket[key] = recent
    return True


class SignupRequest(BaseModel):
    username: str
    password: str
    display_name: str = ""


@app.post("/api/signup")
def signup(body: SignupRequest, request: Request) -> JSONResponse:
    """הרשמה עצמית ציבורית.

    **מה שנשמר:** שם משתמש, שם לתצוגה וסיסמה. **מה שלא נשמר:** אימייל,
    טלפון וכל דרך אחרת ליצור קשר — לפי קו הגבול מול ה-CRM ("כל דבר
    שיש עליו שם של לקוח שייך ל-CRM"). המשמעות המעשית שצריך לומר בקול:
    **אין שחזור סיסמה.** מי ששוכח, אבא או ינון מאפסים לו ב-CLI.

    היכולת שמונפקת היא `quote:total` בלבד — מספר אחד, בלי פירוק.
    """
    if SIGNUP_MODE == "closed":
        raise HTTPException(status_code=403, detail="ההרשמה סגורה כרגע.")

    if not _rate_ok(_SIGNUPS, _client_ip(request), SIGNUPS_PER_IP, SIGNUP_WINDOW_SECONDS):
        raise HTTPException(
            status_code=429, detail="נפתחו יותר מדי חשבונות מהכתובת הזאת. נסה שוב בעוד שעה."
        )

    try:
        user = identity.create_user(
            body.username,
            body.password,
            body.display_name,
            set(identity.PUBLIC_SIGNUP_CAPABILITIES),
            disabled=SIGNUP_MODE == "approval",
        )
    except identity.UserError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if SIGNUP_MODE == "approval":
        return JSONResponse(
            {
                "username": user.username,
                "pending": True,
                "message": "החשבון נוצר וממתין לאישור. נעדכן כשייפתח.",
            },
            status_code=201,
        )

    response = JSONResponse(
        {
            "username": user.username,
            "display_name": user.display_name,
            "capabilities": sorted(user.capabilities),
            "pending": False,
            "tariff_ready": STATE.is_ready,
            "next": "/",
        },
        status_code=201,
    )
    # נכנס ישר פנימה. מסך הרשמה שמסתיים ב"עכשיו תתחבר" הוא טופס שנשלח
    # פעמיים על אותו מידע.
    response.set_cookie(
        identity.SESSION_COOKIE,
        identity.issue_session(user.username),
        max_age=identity.SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        path="/",
    )
    return response


@app.post("/api/logout")
def logout() -> JSONResponse:
    response = JSONResponse({"ok": True})
    response.delete_cookie(identity.SESSION_COOKIE, path="/")
    return response


@app.get("/api/me")
def me(request: Request) -> dict:
    """מי מחובר עכשיו. הממשק מציג את זה בכותרת."""
    user = getattr(request.state, "user", None)
    if user is None:
        return {"authenticated": False, "gate": _gate_is_on()}
    return {
        "authenticated": True,
        "username": user.username,
        "display_name": user.display_name,
        "capabilities": sorted(user.capabilities),
        "source": user.source,
    }


# ---- מצב המערכת ----

BACKUP_STATUS_PATH = Path(
    os.environ.get("BACKUP_STATUS_FILE", Path(__file__).resolve().parents[3] / "config" / "backup-status.json")
)
BACKUP_STALE_AFTER_MINUTES = 180
"""הטיימר שעתי. שלוש שעות בלי ריצה זו תקלה ולא איחור."""

TARIFF_STALE_DAYS = float(os.environ.get("TARIFF_STALE_DAYS", "14"))
"""מעל כמה ימים טבלה טעונה אישור לפני שמוציאים לפיה הצעה.

14 ולא 30: מחירי פח זזים, אבל הסכנה האמיתית אינה "מחיר שהתיישן" אלא
**שחזור מגיבוי** שמחזיר גרסה ישנה בשקט. שבועיים הם גבול שמבחין בין
"אבא עדכן לאחרונה" לבין "הקובץ הזה בא מאיפשהו אחר".
"""

TARIFF_THIN_COVERAGE = 0.2
"""מתחת לשיעור הזה של שורות מתומחרות, הטבלה מוכרזת חלקית.

הטבלה של 18.8 שיושבת בגיבוי היא שורה אחת מתוך 22 — 4.5%. `is_ready`
מחזיר True על שורה אחת מתומחרת בכוונה (אפשר לעבוד ככה, וזה בדיוק מה
שאיפשר לאבא להתחיל), ולכן הוא לבדו אינו מבחין בין "התחילו למלא" לבין
"שוחזר גיבוי ניסוי".
"""


def _backup_state() -> dict:
    """מתי גובה לאחרונה — מתוך קובץ המצב שהסקריפט מדווח.

    השירות אינו יכול לקרוא את `/var/backups/laser-tariff` (700 root),
    וזה מכוון: תהליך שמגיש דפים לאינטרנט לא צריך גישת קריאה לכל
    הגרסאות של טבלת המחירים. לכן הוא נשען על חותמות זמן בלבד.
    """
    try:
        raw = json.loads(BACKUP_STATUS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"reporting": False, "stale": True, "minutes_since_run": None}

    minutes = None
    try:
        last = datetime.strptime(raw["last_run"], "%Y-%m-%dT%H:%M:%S")
        minutes = max(0, int((datetime.now() - last).total_seconds() // 60))
    except (KeyError, TypeError, ValueError):
        pass

    return {
        "reporting": True,
        "last_run": raw.get("last_run"),
        "ok": bool(raw.get("ok", False)),
        "minutes_since_run": minutes,
        "stale": minutes is None or minutes > BACKUP_STALE_AFTER_MINUTES,
        "tariff": raw.get("tariff", {}),
        "users": raw.get("users", {}),
    }


def _table_coverage() -> dict:
    """כמה מהטבלה באמת מלא, ואיפה חסר.

    זה הלב של המסך התפעולי: "הטבלה ריקה" הוא מצב שאפשר לעבוד איתו,
    אבל רק אם רואים בדיוק **מה** ריק. הספירה היא על תאים כספיים —
    שורה × שדות מחיר — ולא על שורות, כי שורה עם מחיר פלטה בלבד עדיין
    תייצר הצעה חלקית.
    """
    fields = [f for f, _ in MONEY_FIELDS]
    materials: dict[str, dict] = {}
    filled_cells = 0
    total_cells = 0

    for row in STATE.raw.get("rates", []):
        key = row.get("material_key", "")
        entry = materials.setdefault(
            key,
            {
                "key": key,
                "name": row.get("material_name", key),
                "rows": 0,
                "priced_rows": 0,
                "filled_cells": 0,
                "total_cells": 0,
                "empty_fields": {f: 0 for f in fields},
            },
        )
        entry["rows"] += 1
        row_has_price = False
        for field in fields:
            total_cells += 1
            entry["total_cells"] += 1
            try:
                value = float(row.get(field, 0) or 0)
            except (TypeError, ValueError):
                value = 0.0
            if value > 0:
                filled_cells += 1
                entry["filled_cells"] += 1
                if field in ("plate_price", "cut_rate_per_m"):
                    row_has_price = True
            else:
                entry["empty_fields"][field] += 1
        if row_has_price:
            entry["priced_rows"] += 1

    return {
        "materials": sorted(materials.values(), key=lambda m: str(m["name"])),
        "filled_cells": filled_cells,
        "total_cells": total_cells,
        "field_labels": [{"field": f, "label": lbl} for f, lbl in MONEY_FIELDS],
    }


@app.get("/api/dashboard")
def dashboard() -> dict:
    """המסך התפעולי: מה מלא, מה בריא, ומתי גובה לאחרונה.

    **לא מסך מכירות.** היסטוריית ההצעות יושבת ב-CRM לפי קו הגבול
    שסוכם, והמנוע אינו שומר הצעות כלל.
    """
    disk_present, disk_matches = STATE.disk_state()
    coverage = _table_coverage()
    priced_rows, rate_rows = STATE.coverage
    now = time.time()

    # חומרים שאין בהם **אף** שורה מתומחרת. זו הפעולה שממתינה לאבא,
    # ולכן היא מחושבת כאן במפורש ולא מושארת לעין לגלות בטבלה.
    unpriced_materials = [
        {"key": m["key"], "name": m["name"], "rows": m["rows"]}
        for m in coverage["materials"]
        if m["priced_rows"] == 0
    ]

    users = identity.list_users()
    return {
        "tariff": {
            "ready": STATE.is_ready,
            "source": STATE.origin,
            "error": STATE.error,
            "priced_rows": priced_rows,
            "rate_rows": rate_rows,
            "updated_at": (
                datetime.fromtimestamp(STATE.source_mtime).strftime("%Y-%m-%d")
                if STATE.source_mtime
                else None
            ),
            "age_days": round(STATE.age_days, 1) if STATE.age_days is not None else None,
            "price_origin": STATE.tariff.price_origin if STATE.tariff else "",
            "price_low_confidence": (
                list(STATE.tariff.price_low_confidence) if STATE.tariff else []
            ),
            "unpriced_materials": unpriced_materials,
            **coverage,
        },
        "engine": {
            "disk_present": disk_present,
            "disk_matches_memory": disk_matches,
            "gate_on": _gate_is_on(),
            "session_secret_ephemeral": identity.SESSION_SECRET_IS_EPHEMERAL,
            "commit": RUNNING_COMMIT[:12] if RUNNING_COMMIT else None,
            "uptime_hours": round((now - PROCESS_STARTED_AT) / 3600.0, 1),
        },
        "backup": _backup_state(),
        "users": {
            "count": len(users),
            "internal": [u for u in users if identity.CAP_QUOTE_TOTAL not in u["capabilities"]],
            "public": [u for u in users if identity.CAP_QUOTE_TOTAL in u["capabilities"]],
        },
        "uploads": {
            # **נמדד מאז ההפעלה האחרונה בלבד**, ולכן החלון מדווח לצידו.
            "failures_since_start": len(_UPLOAD_FAILURES),
            "window_hours": round((now - PROCESS_STARTED_AT) / 3600.0, 1),
            "recent": [
                {"minutes_ago": int((now - at) / 60), "reason": why}
                for at, why in _UPLOAD_FAILURES[-8:][::-1]
            ],
        },
        # **טלמטריה על המנוע, לא היסטוריית הצעות.** אין ברשומות שם
        # לקוח, מזהה לקוח ולא שם החלק — האחרון נגזר משם הקובץ, ושם
        # קובץ הוא שם לקוח בתחפושת. הגבול מול ה-CRM נשמר.
        # **אפס שורות מדווח כ"לא נאסף" ולא כאפס**, כי אפס נקרא כמדידה.
        "quotes": usage.summary(),
    }


def _priced_materials(tariff) -> list[dict]:
    """חומרים ועוביים שיש להם **גם מחיר פלטה וגם מחיר חיתוך**.

    שורה עם אחד מהם בלבד מייצרת מחיר שחסר בו רכיב, ולכן היא אינה
    "נתמכת" גם אם היא נראית מלאה למחצה. מקור אחד לשאלה הזאת, וממנו
    ניזונים גם `/api/offering` (דף הנחיתה) וגם `/api/config` הציבורי
    (מסך התמחור) — כדי שהדף לא יבטיח משהו שהמסך אינו יודע לספק.
    """
    usable: dict[str, dict] = {}
    for rate in tariff.rates.values():
        if rate.plate_price <= 0 or rate.cut_rate_per_m <= 0:
            continue
        entry = usable.setdefault(
            rate.material_key,
            {"key": rate.material_key, "name": rate.material_name, "thicknesses": []},
        )
        entry["thicknesses"].append(rate.thickness_mm)
    return [
        {"key": m["key"], "name": m["name"], "thicknesses": sorted(m["thicknesses"])}
        for m in sorted(usable.values(), key=lambda m: str(m["name"]))
    ]


@app.get("/api/offering")
def offering() -> dict:
    """מה המערכת יודעת לתמחר **היום** — בלי זהות ובלי מחירים.

    **הסיבה שהנתיב הזה קיים:** דף הנחיתה חייב לומר אילו חומרים
    נתמכים, ואם הוא יאמר "כל החומרים" הוא ייצור לקוח שנוחת על 422
    בפנייה הראשונה שלו. נירוסטה, אלומיניום ופח מגולוון אינם מתומחרים
    היום — ולכן הם פשוט לא יופיעו, וברגע שיתומחרו הם יופיעו בלי
    שאיש יערוך את הדף.

    **"נתמך" פירושו גם מחיר פלטה וגם מחיר חיתוך.** שורה עם אחד מהם
    בלבד מייצרת מחיר שחסר בו רכיב, וזו הבטחה גרועה יותר משתיקה.
    """
    tariff = STATE.tariff
    if tariff is None:
        return {"ready": False, "materials": [], "plate": None, "max_upload_mb": None}

    materials = [{"name": m["name"], "thicknesses": m["thicknesses"]} for m in _priced_materials(tariff)]
    plate = tariff.plate
    return {
        "ready": STATE.is_ready and bool(materials),
        "materials": materials,
        "plate": {"width_mm": plate.width_mm, "height_mm": plate.height_mm},
        # מה שמותר להעלות בלי חשבון פנימי — הדף אומר את המספר האמיתי.
        "max_upload_mb": MAX_PUBLIC_UPLOAD_BYTES // (1024 * 1024),
        "signup_open": SIGNUP_MODE != "closed",
    }


@app.get("/api/config")
def config(request: Request) -> dict:
    """כל מה שהממשק צריך כדי להיפתח: פלטה, חומרים ומצב הטבלה.

    **למשתמש ציבורי חוזר פחות.** `margin_pct` הוא אחוז הרווח של ינון,
    ו-`waste_tiers` הם מכפילי החיוב על בזבוז — שני מספרים שאפשר לקרוא
    ישירות מהתשובה בלי לתמחר בכלל. מידות הפלטה, שמות החומרים והעוביים
    כן יוצאים: בלעדיהם אין מה לבחור במסך, והם ממילא מה שכל ספק פח
    מפרסם.
    """
    detailed = _sees_breakdown(request)
    tariff = STATE.tariff
    if tariff is None:
        return {
            "tariff_ready": False,
            "tariff_error": STATE.error if detailed else "",
            "tariff_source": STATE.origin if detailed else "",
            "materials": [],
            "detailed": detailed,
        }
    plate = tariff.plate
    body = {
        "tariff_ready": STATE.is_ready,
        "currency": tariff.currency,
        # מע"מ הוא שיעור חוקי ופומבי, ומי שרואה סכום חייב לדעת אם הוא כלול.
        "vat_pct": tariff.vat_pct,
        "plate": {
            "width_mm": plate.width_mm,
            "height_mm": plate.height_mm,
            "edge_margin_mm": plate.edge_margin_mm,
            "usable_width_mm": round(plate.usable_width, 2),
            "usable_height_mm": round(plate.usable_height, 2),
            "label": plate.label,
        },
        # **לציבור יוצאים רק חומרים שאפשר באמת לתמחר.** בלי הסינון
        # הזה מסך התמחור מציע אלומיניום כברירת מחדל, המשתמש לוחץ
        # "חשב מחיר" בפעם הראשונה בחייו — ומקבל 422 אדום. השגיאה
        # נכונה, אבל להציע בחירה שנועדה להיכשל היא באג במסך ולא
        # במנוע. אותו כלל בדיוק כמו ב-`/api/offering`.
        "materials": tariff.materials if detailed else _priced_materials(tariff),
        # יוצא **גם לציבור**: זו הדרישה של האב שההצעה תישא את מקורה
        # ולא באותיות קטנות. מקור המחירים אינו מחיר, ולכן אינו מדליף.
        "price_origin": tariff.price_origin,
        "detailed": detailed,
    }
    if not detailed:
        return body
    return body | {
        "tariff_error": STATE.error,
        "tariff_source": STATE.origin,
        "margin_pct": tariff.margin_pct,
        "part_gap_mm": tariff.part_gap_mm,
        "waste_tiers": [
            {"max_waste_pct": t.max_waste_pct, "multiplier": t.multiplier, "label": t.label}
            for t in tariff.waste_tiers
        ],
    }


# ---- קלט ----


@app.post("/api/upload")
async def upload(request: Request, file: UploadFile = File(...)) -> dict:
    """מעלה קובץ ייצור ומחלץ ממנו את הגיאומטריה.

    הקובץ עצמו לא נשמר ולא נשלח לשום מקום — הוא מפוענח מקומית ונמחק.

    הכישלונות נספרים כאן ולא בכל `raise` בנפרד: העטיפה תופסת גם את
    מה שייכשל בעתיד בלי שמישהו יזכור להוסיף מונה.
    """
    try:
        return await _upload(request, file)
    except HTTPException as exc:
        _record_upload_failure(f"{exc.status_code}: {str(exc.detail)[:120]}")
        raise


async def _upload(request: Request, file: UploadFile) -> dict:
    _guard_public_engine(request)
    name = file.filename or "upload.dxf"
    suffix = Path(name).suffix.lower()

    if suffix in {".step", ".stp"}:
        raise HTTPException(
            status_code=415,
            detail="קבצי STEP עדיין לא נתמכים. ייצא ל-DXF, או הזן את המידות ידנית.",
        )
    if suffix != ".dxf":
        raise HTTPException(status_code=415, detail=f"סוג קובץ לא נתמך: {suffix or 'ללא סיומת'}. נדרש DXF.")

    payload = await file.read()
    ceiling = MAX_UPLOAD_BYTES if _sees_breakdown(request) else MAX_PUBLIC_UPLOAD_BYTES
    if len(payload) > ceiling:
        raise HTTPException(
            status_code=413,
            detail=(
                f"הקובץ גדול מ-{ceiling // (1024 * 1024)}MB. "
                "תוכנית שלמה אינה חלק — ייצא מהתוכנית את המתאר של החלק בלבד "
                "(בלי ריהוט, מידות וטקסט) ושלח אותו."
            ),
        )
    if not payload:
        raise HTTPException(status_code=400, detail="הקובץ ריק.")

    with tempfile.NamedTemporaryFile(suffix=".dxf", delete=True) as tmp:
        tmp.write(payload)
        tmp.flush()
        try:
            extraction = read_dxf(tmp.name)
        except CadReadError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:  # קובץ פגום מגיע בצורות רבות ובלתי צפויות
            raise HTTPException(status_code=422, detail=f"קריאת ה-DXF נכשלה: {exc}") from exc

    stem = Path(name).stem
    key = STORE.put(
        StoredGeometry(
            geometry=extraction.geometry,
            name=stem,
            source=PartSource.DXF.value,
            units_detected=extraction.units_detected,
            warnings=tuple(extraction.warnings),
        ),
        owner=_owner_key(request),
        internal=_sees_breakdown(request),
    )
    return _geometry_response(key, stem, PartSource.DXF.value, extraction.geometry) | {
        "units_detected": extraction.units_detected,
        "scale_to_mm": extraction.scale_to_mm,
        "warnings": extraction.warnings,
        "skipped_entities": extraction.skipped_entities,
        "layers_seen": extraction.layers_seen,
    }


@app.post("/api/manual")
def manual(spec: ManualPartSpec, request: Request) -> dict:
    """בונה גיאומטריה ממידות שהוקלדו — אותו טיפוס בדיוק כמו מ-DXF."""
    _guard_public_engine(request)
    try:
        geometry = _build_manual_geometry(spec)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    key = STORE.put(
        StoredGeometry(geometry=geometry, name=spec.name, source=PartSource.MANUAL.value),
        owner=_owner_key(request),
        internal=_sees_breakdown(request),
    )
    return _geometry_response(key, spec.name, PartSource.MANUAL.value, geometry)


# ---- תמחור ----


def _expired_detail(request: Request, item: "PartRequest") -> str:
    """מה לעשות עכשיו — ולא רק מה קרה.

    **הכרעת האב, 25.8.2026:** "'העלה מחדש' נכונה לבן אדם בדפדפן
    ומטעה לחלוטין שירות." ימיש אינו מעלה קבצים ואינו יכול "להעלות
    מחדש"; מה שהוא צריך לעשות הוא לקרוא שוב ל-`/api/manual` ולקבל
    `geometry_id` חדש. אותה הודעה לשני הקהלים הייתה נכונה לאחד
    ומבלבלת את השני, ולכן היא נבחרת לפי מקור הזהות.
    """
    who = item.name or item.geometry_id
    user = _current(request)
    if user is not None and user.source == "service":
        return (
            f'geometry_id של "{who}" פג מהזיכרון. הגיאומטריות נשמרות בזיכרון התהליך '
            f"בלבד וכל הפעלה מחדש מאפסת אותן. שלח שוב /api/manual (או /api/upload) "
            f"לאותו חלק, קבל geometry_id חדש, וחזור על /api/quote."
        )
    return f'הגיאומטריה של "{who}" כבר לא בזיכרון. העלה את הקובץ מחדש.'


@app.post("/api/quote")
def quote(body: QuoteRequest, request: Request) -> dict:
    """מתמחר. **צורת התשובה תלויה במי ששאל** — ראה `serialize`."""
    _guard_public_engine(request)
    tariff = _require_tariff()
    owner = _owner_key(request)

    parts: list[Part] = []
    for item in body.parts:
        try:
            stored = STORE.get(item.geometry_id, owner=owner)
        except GeometryExpired as exc:
            raise HTTPException(status_code=410, detail=_expired_detail(request, item)) from exc
        try:
            parts.append(
                Part(
                    name=item.name or stored.name,
                    geometry=stored.geometry,
                    material_key=item.material_key,
                    thickness_mm=item.thickness_mm,
                    quantity=item.quantity,
                    source=PartSource(stored.source),
                    bend_count=item.bend_count,
                    bend_length_mm=item.bend_length_mm,
                    cut_open_lines=item.cut_open_lines,
                )
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        result = price_order(parts, tariff)
    except MissingTariffError as exc:
        # לא שגיאת שרת: הטבלה פשוט לא מכסה את הצירוף, וזה מכוון.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except PricingError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # הרישום קורה אחרי שהתמחור הצליח ולפני ההחזרה, ובאותו מקום לשתי
    # צורות התשובה — כדי שלא ייווצר מסלול שמתמחר ואינו נרשם.
    usage.record_quote(result, _current(request))
    return quote_to_json(result) if _sees_breakdown(request) else public_quote_to_json(result)


# ---- קטלוג ----

CATALOG_PREVIEW_PER_IP = 240
CATALOG_PREVIEW_WINDOW_SECONDS = 3600
_CATALOG_PREVIEWS: dict[str, list[float]] = {}
"""הדמיה אינה תמחור, ולכן היא מוגבלת אחרת.

ההדמיה אינה נוגעת בטבלה ואינה מחזירה שקל, אבל היא כן בונה עיגולים
ב-256 צלעות על **שתי ליבות המשותפות לחמישה פרויקטים**. 240 לשעה הם
בערך ארבע לדקה — יותר ממה שאדם שמזיז מחוונים מייצר, ורחוק ממה
שסקריפט צריך כדי להטריד את הקופסה.

**המספר הזה אינו קשור ל-`PUBLIC_ENGINE_CALLS`, ואסור לבלבל.** שם
12 לשעה מגנים על *טבלת המחירים*; כאן אין מה להגן עליו מלבד המעבד.
"""


class CatalogQuoteRequest(BaseModel):
    values: dict[str, float | int | str | None] = Field(default_factory=dict)
    material_key: str
    thickness_mm: float = Field(gt=0)
    quantity: int = Field(default=1, ge=1)


class CatalogPreviewRequest(BaseModel):
    values: dict[str, float | int | str | None] = Field(default_factory=dict)


def _catalog_materials(product: catalog_mod.Product) -> list[dict]:
    """אילו חומרים יכולים לתמחר **את המוצר הזה**, ולא בכלל.

    `_priced_materials` שואל אם יש מחיר פלטה ומחיר חיתוך. זה מספיק
    לחלק שטוח ואינו מספיק למוצר מכופף: המנוע דוחה כיפוף בשורה שאין
    בה `bend_price` ולא `bend_rate_per_m` — 422 נכון לחלוטין, ומסך
    שבור לגמרי. מוצר מכופף שאין לו חומר שיודע לתמחר כיפוף פשוט לא
    יופיע בקטלוג, מאותה סיבה שאלומיניום לא מופיע בדף הנחיתה.
    """
    tariff = STATE.tariff
    if tariff is None:
        return []

    usable: dict[str, dict] = {}
    for rate in tariff.rates.values():
        if rate.plate_price <= 0 or rate.cut_rate_per_m <= 0:
            continue
        if product.bends and rate.bend_price <= 0 and rate.bend_rate_per_m <= 0:
            continue
        entry = usable.setdefault(
            rate.material_key,
            {"key": rate.material_key, "name": rate.material_name, "thicknesses": []},
        )
        entry["thicknesses"].append(rate.thickness_mm)
    return [
        {"key": m["key"], "name": m["name"], "thicknesses": sorted(m["thicknesses"])}
        for m in sorted(usable.values(), key=lambda m: str(m["name"]))
    ]


THUMBNAIL_POINTS = 48
_THUMBNAILS: dict[str, dict] = {}


def _thumbnail(product: catalog_mod.Product) -> dict:
    """החלק בברירות המחדל שלו, מדולל לכרטיס בקטלוג.

    **הכרטיס מצייר את החלק ולא איור שלו.** לא צילמנו את המוצרים ואין
    לנו סטודיו, אבל גם אם היה — תצלום מתיישן ברגע שמישהו משנה בנאי,
    והמסך ימשיך להראות את החלק הישן לצד המחיר החדש. כאן אין מה
    שיתיישן: אלה אותם מתארים שמהם מחושב אורך החיתוך.

    נבנה פעם אחת ונשמר. ברירות המחדל אינן משתנות בזמן ריצה, ובנייה
    חוזרת בכל טעינת קטלוג הייתה שבעה חישובי גיאומטריה על **שתי
    ליבות המשותפות לחמישה פרויקטים**.
    """
    cached = _THUMBNAILS.get(product.id)
    if cached is not None:
        return cached

    blank = catalog_mod.build(product, product.defaults())
    geometry = _build_manual_geometry(ManualPartSpec(name=product.name, **blank.spec))
    data = geometry_to_json(geometry)
    thumb = {
        "bbox": data["bbox"],
        "fold_lines": blank.fold_lines,
        "contours": [
            {
                "points": c["points"][:: max(1, len(c["points"]) // THUMBNAIL_POINTS)],
                "hole": c["hole"],
                "closed": c["closed"],
            }
            for c in data["contours"]
        ],
    }
    _THUMBNAILS[product.id] = thumb
    return thumb


def _product_to_json(product: catalog_mod.Product, materials: list[dict]) -> dict:
    return {
        "thumb": _thumbnail(product),
        "id": product.id,
        "category": product.category,
        "name": product.name,
        "summary": product.summary,
        "description": product.description,
        "bends": product.bends,
        "params": [p.to_json() for p in product.params],
        "materials": materials,
    }


@app.get("/api/catalog")
def get_catalog() -> dict:
    """הקטלוג כפי שהוא ניתן לתמחור **היום**. בלי זהות ובלי מחירים.

    מוצר שאין לו אף חומר שיודע לתמחר אותו אינו נכנס לקטגוריות — אבל
    גם אינו נעלם בשקט: הוא נספר ב-`unavailable` עם הסיבה. **שקט זה
    מה שגרם לגיבוי לדווח `ok:true` על מסד ריק במשך ארבעה ימים**,
    ומסך שמראה חמישה מוצרים מתוך שבעה בלי לומר מילה הוא אותו זן.

    הסיבה שמופיעה שם אומרת ששדה בטבלה ריק, ולא מה ערכו. זה כבר
    פומבי ב-`/api/offering` ובמוני `/health`.
    """
    categories: dict[str, dict] = {
        c.key: {"key": c.key, "name": c.name, "summary": c.summary, "products": []}
        for c in CATALOG.categories
    }
    unavailable: list[dict] = []

    for product in CATALOG.products:
        materials = _catalog_materials(product)
        if not materials:
            unavailable.append(
                {
                    "id": product.id,
                    "name": product.name,
                    "reason": (
                        "אין בטבלה חומר עם מחיר כיפוף"
                        if product.bends
                        else "אין בטבלה חומר עם מחיר פלטה ומחיר חיתוך"
                    ),
                }
            )
            continue
        categories[product.category]["products"].append(_product_to_json(product, materials))

    live = [c for c in categories.values() if c["products"]]
    return {
        "ready": STATE.is_ready and bool(live),
        "version": CATALOG.version,
        "categories": live,
        "product_count": sum(len(c["products"]) for c in live),
        "unavailable": unavailable,
        "signup_open": SIGNUP_MODE != "closed",
    }


def _require_product(product_id: str) -> catalog_mod.Product:
    product = CATALOG.get(product_id)
    if product is None:
        raise HTTPException(status_code=404, detail=f'אין מוצר בשם "{product_id}" בקטלוג.')
    return product


def _blank(product: catalog_mod.Product, values: dict) -> catalog_mod.Blank:
    try:
        return catalog_mod.build(product, values)
    except catalog_mod.CatalogError as exc:
        # 400 ולא 422: המידות שהתקבלו אינן ניתנות לייצור, וזו טענה על
        # הקלט ולא על הטבלה. ההודעה אומרת מה הגבול ולא רק "לא חוקי".
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/catalog/{product_id}/preview")
def catalog_preview(product_id: str, body: CatalogPreviewRequest, request: Request) -> dict:
    """הצורה, בלי מחיר. **פתוח לכולם, ובכוונה.**

    זו הנקודה שבה הקטלוג מרגיש כמו קונפיגורטור ולא כמו טופס: המחוון
    זז והחלק משתנה מיד. הוא יכול להיות פתוח **מפני שאין בו טבלה** —
    הוא מחזיר גיאומטריה שנבנתה מהמידות שהמבקר עצמו הקליד, ואותו קוד
    בדיוק ירוץ אם יקליד אותן ב-/api/manual.

    **ובכוונה אין כאן מחיר גם לא למי שמחובר.** מחיר על כל תזוזת
    מחוון היה שורף את 12 התמחורים לשעה בעשר שניות, וגם היה הופך את
    שחזור הטבלה מ"תשע קריאות" למשהו שקורה מעצמו.
    """
    if not _rate_ok(
        _CATALOG_PREVIEWS,
        _client_ip(request),
        CATALOG_PREVIEW_PER_IP,
        CATALOG_PREVIEW_WINDOW_SECONDS,
    ):
        raise HTTPException(status_code=429, detail="יותר מדי הדמיות. נסה שוב בעוד כמה דקות.")

    product = _require_product(product_id)
    blank = _blank(product, body.values)
    geometry = _build_manual_geometry(ManualPartSpec(name=product.name, **blank.spec))
    plan = plan_split(geometry.bbox, _plate())
    return {
        "product_id": product.id,
        "name": product.name,
        "values": catalog_mod.resolve(product, body.values),
        "bend_count": blank.bend_count,
        "bend_length_mm": round(blank.bend_length_mm, 2),
        "fold_lines": blank.fold_lines,
        "fits_single_plate": plan.piece_count == 1,
        **geometry_to_json(geometry),
    }


@app.post("/api/catalog/{product_id}/quote")
def catalog_quote(product_id: str, body: CatalogQuoteRequest, request: Request) -> dict:
    """מידות → מחיר. **דרך אותו מנוע בדיוק כמו חלק שהוקלד ביד.**

    הגיאומטריה אינה נכנסת ל-`STORE`, ובכוונה: הקונפיגורטור מייצר
    חלק חדש בכל לחיצה, והחנות הייתה מתמלאת בעשרות גרסאות של אותה
    פלטה עד שהתקרה של הבעלים הייתה נגמרת מעצמה. ההצעה כאן היא בקשה
    אחת שלמה — מידות פנימה, מחיר החוצה.

    צורת התשובה נקבעת ב-`serialize` כמו בכל מקום אחר: ציבורי מקבל
    מספר אחד, פנימי מקבל פירוק.
    """
    _guard_public_engine(request)
    tariff = _require_tariff()
    product = _require_product(product_id)
    blank = _blank(product, body.values)
    geometry = _build_manual_geometry(ManualPartSpec(name=product.name, **blank.spec))

    try:
        part = Part(
            name=product.name,
            geometry=geometry,
            material_key=body.material_key,
            thickness_mm=body.thickness_mm,
            quantity=body.quantity,
            source=PartSource.MANUAL,
            bend_count=blank.bend_count,
            bend_length_mm=blank.bend_length_mm,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        result = price_order([part], tariff)
    except MissingTariffError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except PricingError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    usage.record_quote(result, _current(request))
    quote_json = quote_to_json(result) if _sees_breakdown(request) else public_quote_to_json(result)
    return {
        "product_id": product.id,
        "values": catalog_mod.resolve(product, body.values),
        "bend_count": blank.bend_count,
        "quote": quote_json,
    }


# ---- טבלת התמחור ----


@app.get("/api/tariff")
def get_tariff() -> dict:
    return {"raw": STATE.raw, "source": STATE.origin, "ready": STATE.is_ready, "error": STATE.error}


@app.put("/api/tariff")
async def put_tariff(raw: dict) -> dict:
    """מחליף את הטבלה הפעילה.

    בפריסת ענן הכתיבה לדיסק אינה שורדת הפעלה מחדש, ולכן התשובה אומרת
    במפורש אם הצליחה — והממשק מציע להוריד את הקובץ ולהזין אותו כמשתנה
    סביבה. עדיף לומר את זה מאשר שינון יגלה בעצמו שהמחירים נעלמו.
    """
    try:
        STATE.replace(raw)
    except (InvalidTariffError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    saved = STATE.persist()
    return {
        "ready": STATE.is_ready,
        "persisted": saved is not None,
        "persist_note": (
            "נשמר בדיסק השרת. בענן זה זמני — הורד את הקובץ והזן אותו כמשתנה הסביבה TARIFF_JSON כדי שישרוד פריסה מחדש."
            if saved
            else "לא ניתן היה לכתוב לדיסק. הטבלה פעילה בזיכרון בלבד — הורד אותה."
        ),
    }


# ---- טופס המחירים הפשוט ----


@app.get("/api/prices")
def get_prices() -> dict:
    """הטבלה בצורת טופס, בלי מבנה נתונים ובלי מונחים טכניים."""
    return {
        "form": to_form(STATE.raw or {}),
        "ready": STATE.is_ready,
        "source": STATE.origin,
        # אבא פותח את הטופס ורואה מספרים. **הוא חייב לדעת מיד שהם לא
        # שלו** — אחרת הוא יניח שמישהו כבר מילא במקומו, או גרוע מזה,
        # יאשר אותם בשתיקה. הכרעת ינון מילאה הערכת שוק, לא מחירים.
        "price_origin": STATE.tariff.price_origin if STATE.tariff else "",
    }


@app.put("/api/prices")
async def put_prices(form: dict) -> dict:
    """שומר את המחירים מהטופס לתוך הטבלה הקיימת.

    הטופס נוגע רק במחירים; מדרגות הבזבוז, מידות הפלטה ומדיניות
    השאריות עוברות דרכו ללא שינוי.
    """
    try:
        merged = apply_form(STATE.raw or {}, form)
        STATE.replace(merged)
    except (InvalidTariffError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    saved = STATE.persist()
    return {
        "ready": STATE.is_ready,
        "persisted": saved is not None,
        "message": (
            "המחירים נשמרו והמערכת מחשבת לפיהם."
            if STATE.is_ready
            else "נשמר, אבל כל המחירים עדיין 0 — המערכת תציג 0 בכל הצעה."
        ),
    }


@app.get("/api/tariff/download")
def download_tariff() -> JSONResponse:
    return JSONResponse(
        content=STATE.raw,
        headers={"Content-Disposition": 'attachment; filename="tariff.json"'},
    )


# ---- עזר ----


def _require_tariff():
    try:
        return STATE.require()
    except InvalidTariffError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _geometry_response(key: str, name: str, source: str, geometry: PartGeometry) -> dict:
    """תשובת השרת על חלק בודד, מיד אחרי חילוץ הגיאומטריה.

    `manufacturable` נשאר True גם לחלק שגדול מפלטה: מאז החלטת הפיצול
    כל חלק ניתן לייצור, והשאלה היא רק בכמה חתיכות ובכמה ריתוך.
    """
    plate = _plate()
    verdict = check_manufacturability(geometry.bbox, plate)
    plan = plan_split(geometry.bbox, plate)
    return {
        "geometry_id": key,
        "name": name,
        "source": source,
        "manufacturable": True,
        "fits_single_plate": verdict.ok,
        "manufacturability_reason": "" if verdict.ok else verdict.reason,
        "rotated_to_fit": verdict.rotated,
        "pieces": plan.piece_count,
        "split_columns": plan.columns,
        "split_rows": plan.rows,
        "weld_length_mm": round(plan.seam_length_mm, 2),
        **geometry_to_json(geometry),
    }


def _plate():
    tariff = STATE.tariff
    if tariff is not None:
        return tariff.plate
    from ..nesting.plate import STANDARD_PLATE

    return STANDARD_PLATE


def _build_manual_geometry(spec: ManualPartSpec) -> PartGeometry:
    if spec.shape == "rect":
        if not spec.width_mm or not spec.height_mm:
            raise ValueError("למלבן נדרשים רוחב וגובה.")
        outer = rectangle(spec.width_mm, spec.height_mm)
        span_w, span_h = spec.width_mm, spec.height_mm
    elif spec.shape == "circle":
        if not spec.diameter_mm:
            raise ValueError("לעיגול נדרש קוטר.")
        radius = spec.diameter_mm / 2.0
        outer = circle(radius, center=(radius, radius), segments=256)
        span_w = span_h = spec.diameter_mm
    else:
        if not spec.points or len(spec.points) < 3:
            raise ValueError("למצולע נדרשות לפחות 3 נקודות.")
        outer = Contour(points=[(float(x), float(y)) for x, y in spec.points], closed=True)
        box = outer.bbox
        span_w, span_h = box.width, box.height

    contours = [outer]
    box = outer.bbox
    for index, hole in enumerate(spec.holes):
        radius = hole.diameter_mm / 2.0
        if hole.x_mm is not None and hole.y_mm is not None:
            center = (hole.x_mm, hole.y_mm)
        else:
            # פריסה על ציר המרכז: המיקום אינו משנה את השטח או אורך החיתוך,
            # אבל חורים חופפים היו נספרים פעמיים ולכן מפזרים אותם.
            slot = (index + 1) / (len(spec.holes) + 1)
            center = (box.min_x + span_w * slot, box.min_y + span_h / 2.0)
        if radius * 2 > min(span_w, span_h):
            raise ValueError(f'קוטר החור {hole.diameter_mm} מ"מ גדול מהחלק עצמו.')
        contours.append(circle(radius, center=center, segments=_hole_segments(radius)))

    return PartGeometry(contours=contours)


def _hole_segments(radius: float) -> int:
    """מספיק צלעות כדי שהשגיאה בהיקף תהיה זניחה מול דיוק החיתוך."""
    return max(32, min(256, int(math.ceil(radius)) * 8))


# ---- הממשק הסטטי ----

if WEB_DIR.exists():

    @app.get("/")
    def index(request: Request) -> FileResponse:
        """שלושה מסכים שונים על אותה כתובת, לפי מי שנכנס.

        אנונימי מקבל את **דף הנחיתה** — הוא לא יודע מה זה, ומסך כניסה
        אינו תשובה לשאלה "מה זה". מי שנכנס מקבל את המנוע: הגיליון
        הפנימי למי שרשאי לראות פירוק עלויות, ומסך המחיר לכל השאר.

        זה לא "אותו מסך עם עמודות מוסתרות". הגיליון הפנימי מציג פירוק
        עלויות, פריסה על הפלטה, אחוזי בזבוז ולשוניות של הטבלה ומצב
        המערכת — שום דבר מזה אינו רלוונטי למי שרוצה לדעת כמה עולה
        החלק שלו, ורובו גם אסור לו.

        **הזהות נקראת כאן ולא מה-middleware:** `/` נמצא ב-`OPEN_PATHS`,
        ולכן `request.state.user` לא נקבע.
        """
        if not _gate_is_on():
            return FileResponse(WEB_DIR / "index.html")
        user = identity.current_user(request)
        if user is None:
            return FileResponse(WEB_DIR / "landing.html")
        return FileResponse(
            WEB_DIR / ("index.html" if user.sees_cost_breakdown else "quote.html")
        )

    @app.get("/login")
    def login_page() -> FileResponse:
        """מסך הכניסה. עצמאי לחלוטין, מאותה סיבה כמו טופס המחירים:
        דף שנטען לפני שיש זהות לא יכול להסתמך על /static שמאחורי השער."""
        return FileResponse(WEB_DIR / "login.html")

    @app.get("/brand.css")
    def brand_css() -> FileResponse:
        """מערכת העיצוב. פתוחה, כי המסכים הציבוריים נטענים בלי זהות."""
        return FileResponse(
            WEB_DIR / "brand.css",
            media_type="text/css; charset=utf-8",
            # קצר יותר מהגופן: העיצוב כן משתנה, ופריסה חייבת להיראות
            # מיד. שעה מספיקה כדי לחסוך את הבקשה בתוך אותו ביקור.
            headers={"Cache-Control": "public, max-age=3600"},
        )

    @app.get("/part-draw.js")
    def part_draw_js() -> FileResponse:
        """ציור המתאר. פתוח, כי הקטלוג נגלל בלי חשבון."""
        return FileResponse(
            WEB_DIR / "part-draw.js",
            media_type="application/javascript; charset=utf-8",
            headers={"Cache-Control": "public, max-age=3600"},
        )

    @app.get("/fonts/{name}")
    def font(name: str) -> FileResponse:
        """Assistant, מתארח אצלנו. רק woff2, ורק מהתיקייה הזאת."""
        if not name.endswith(".woff2") or "/" in name or ".." in name:
            raise HTTPException(status_code=404, detail="לא נמצא")
        path = WEB_DIR / "fonts" / name
        if not path.is_file():
            raise HTTPException(status_code=404, detail="לא נמצא")
        return FileResponse(
            path,
            media_type="font/woff2",
            # הגופן אינו משתנה. שנה של מטמון חוסכת את הבקשה לגמרי.
            headers={"Cache-Control": "public, max-age=31536000, immutable"},
        )

    @app.get("/catalog")
    def catalog_page() -> FileResponse:
        """הקטלוג. פתוח, כמו דף הנחיתה ומאותה סיבה."""
        return FileResponse(WEB_DIR / "catalog.html")

    @app.get("/product/{product_id}")
    def product_page(product_id: str) -> FileResponse:
        """הקונפיגורטור. **404 על מוצר שאינו קיים, ולא מסך ריק.**

        המסך נטען מאותו קובץ לכל המוצרים והוא קורא את המזהה מהכתובת,
        ולכן בלי הבדיקה הזאת כל שרשור אותיות היה מחזיר 200 עם שלד
        שנשאר ריק לנצח — כתובת שנראית תקינה ומסך ששבור בשקט.
        """
        if CATALOG.get(product_id) is None:
            raise HTTPException(status_code=404, detail=f'אין מוצר בשם "{product_id}".')
        return FileResponse(WEB_DIR / "product.html")

    @app.get("/dashboard")
    def dashboard_page() -> FileResponse:
        """המסך התפעולי לאבא ולינון. עצמאי, כמו שאר המסכים."""
        return FileResponse(WEB_DIR / "dashboard.html")

    @app.get("/signup")
    def signup_page() -> FileResponse:
        """מסך ההרשמה. עצמאי מאותה סיבה בדיוק כמו מסך הכניסה."""
        return FileResponse(WEB_DIR / "signup.html")

    @app.get("/prices")
    def prices_form() -> FileResponse:
        """מסך המחירים. עצמאי לחלוטין — CSS ו-JS בתוך הקובץ.

        הסיבה טכנית ולא סגנונית: מפתח העריכה מגיע בכתובת עצמה, והדפדפן
        לא יצרף אותו לבקשות ל-/static. דף שתלוי בקבצים חיצוניים היה
        נטען שבור אצל מי שנכנס עם הלינק.
        """
        return FileResponse(WEB_DIR / "prices.html")

    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


__all__ = ["app"]
